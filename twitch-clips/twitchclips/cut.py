"""Stap 4: knip een clip rond elke piek.

Het venster is bewust asymmetrisch: [onset - pre, onset + post], standaard 20 s
ervoor en 10 s erna. Chat reageert ná het moment (reactietijd + stream-latency),
dus 15 s vóór en 15 s na de piek knipt vaak de aanloop weg.

Wall-clock -> positie in de opname gaat via segments.csv: zoek het segment waarin
het tijdstip valt, en seek vanaf het ijkpunt van dat segment.
"""

from __future__ import annotations

import bisect
import csv
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .common import Session, clock, hms, probe_duration, run_ffmpeg
from .spikes import read_spikes

log = logging.getLogger(__name__)

SEEK_MARGIN = 10.0  # ruim boven Twitch' keyframe-interval (max. 2-4 s)


@dataclass(frozen=True)
class Segment:
    index: int
    file: str
    run: int
    wall_start: float


@dataclass
class ClipPlan:
    segments: list[Segment]
    seek: float           # seconden vanaf het begin van het eerste segment
    duration: float
    start: float          # wall-clock, na inperken tot wat er opgenomen is
    end: float
    clamped: bool = False
    notes: list[str] = field(default_factory=list)


def load_segments(csv_path: Path) -> list[Segment]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        segs = [
            Segment(int(r["index"]), r["file"], int(r["run"]), float(r["wall_start"]))
            for r in csv.DictReader(f)
        ]
    return sorted(segs, key=lambda s: s.index)


def fix_run_starts(segments: list[Segment], duration_of) -> list[Segment]:
    """Corrigeer het ijkpunt van het eerste segment van elke run.

    Dat bestand ontstaat pas nadat ffmpeg de stream heeft geanalyseerd, en
    streamlink begint met een salvo oudere HLS-segmenten. Het eerste ijkpunt is
    dus te laat. Vanaf het tweede segment komt beeld in realtime binnen; reken
    het eerste daarom terug: start(0) = start(1) - duur(0).
    """
    out = list(segments)
    for k, seg in enumerate(out[:-1]):
        nxt = out[k + 1]
        is_run_start = k == 0 or out[k - 1].run != seg.run
        if is_run_start and nxt.run == seg.run and nxt.index == seg.index + 1:
            corrected = nxt.wall_start - duration_of(seg)
            if abs(corrected - seg.wall_start) > 0.25:
                log.debug("ijkpunt %s: %+.2fs gecorrigeerd", seg.file, corrected - seg.wall_start)
            out[k] = Segment(seg.index, seg.file, seg.run, corrected)
    return out


def plan_clip(segments: list[Segment], t0: float, t1: float, duration_of) -> ClipPlan | None:
    """Bepaal welke segmenten nodig zijn voor wall-clock-venster [t0, t1).

    `duration_of(segment)` geeft de lengte van een segment in seconden.
    Alleen aaneengesloten segmenten uit dezelfde run worden samengevoegd;
    over een gat heen (reconnect) wordt de clip ingekort.
    """
    if not segments or t1 <= t0:
        return None
    starts = [s.wall_start for s in segments]
    i = bisect.bisect_right(starts, t0) - 1
    if i < 0:
        i = 0                                   # venster begint vóór de opname
    elif t0 >= segments[i].wall_start + duration_of(segments[i]):
        i += 1                                  # t0 valt in een gat; begin bij het volgende segment
    if i >= len(segments) or segments[i].wall_start >= t1:
        return None

    chain = [segments[i]]
    while chain[-1].wall_start + duration_of(chain[-1]) < t1 and i + 1 < len(segments):
        nxt = segments[i + 1]
        if nxt.run != chain[-1].run or nxt.index != chain[-1].index + 1:
            break
        chain.append(nxt)
        i += 1

    first, last = chain[0], chain[-1]
    start = max(t0, first.wall_start)
    end = min(t1, last.wall_start + duration_of(last))
    if end <= start:
        return None
    plan = ClipPlan(chain, start - first.wall_start, end - start, start, end)
    if start > t0 + 0.5:
        plan.clamped = True
        plan.notes.append(f"begin {start - t0:.1f}s later (niet opgenomen)")
    if end < t1 - 0.5:
        plan.clamped = True
        plan.notes.append(f"einde {t1 - end:.1f}s eerder (niet opgenomen)")
    return plan


def cut_clip(plan: ClipPlan, seg_dir: Path, out: Path, reencode: bool = True) -> None:
    """Knip met ffmpeg. TS-segmenten mogen byte-voor-byte achter elkaar (concat-protocol).

    Seeken in MPEG-TS is onbetrouwbaar: ffmpeg landt soms op het keyframe ná het
    doel, dus een gewone input-seek begint tot een GOP (~2 s) te laat. Daarom:
    grof seeken tot SEEK_MARGIN s vóór het doel, de rest decoderen en op
    tijdstempel weggooien (output-seek). Dat is framenauwkeurig.
    --copy (zonder her-encoderen) kan dat niet en begint op een keyframe.
    """
    source = "concat:" + "|".join(s.file for s in plan.segments)
    coarse = max(0.0, plan.seek - SEEK_MARGIN)
    if reencode:
        args = ["-ss", f"{coarse:.3f}", "-i", source,
                "-ss", f"{plan.seek - coarse:.3f}", "-t", f"{plan.duration:.3f}",
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k"]
    else:
        args = ["-ss", f"{plan.seek:.3f}", "-i", source, "-t", f"{plan.duration:.3f}",
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c", "copy", "-avoid_negative_ts", "make_zero"]
    args += ["-movflags", "+faststart", str(out.resolve())]
    run_ffmpeg(args, cwd=seg_dir)  # relatieve namen: geen gedoe met ':' of '|' in paden


def cut_all(
    session: Session,
    *,
    pre: float = 20,
    post: float = 10,
    offset: float = 0.0,
    min_length: float = 8,
    reencode: bool = True,
) -> list[Path]:
    """Knip alle pieken uit spikes.csv. `offset` verschuift het venster (kalibratie)."""
    segments = load_segments(session.segments_csv)
    if not segments:
        raise RuntimeError(f"Geen segmenten in {session.segments_csv}")
    spikes = read_spikes(session.spikes_csv)

    @lru_cache(maxsize=None)
    def probed(file: str) -> float:
        return probe_duration(session.segments_dir / file)

    def duration_of(seg: Segment) -> float:
        return probed(seg.file)

    segments = fix_run_starts(segments, duration_of)
    rec_start = segments[0].wall_start

    session.clips_dir.mkdir(exist_ok=True)
    made: list[Path] = []
    rows = []
    for spike in spikes:
        t0 = spike.onset + offset - pre
        t1 = spike.onset + offset + post
        plan = plan_clip(segments, t0, t1, duration_of)
        label = f"#{spike.rank} {clock(spike.onset)}"
        if plan is None or plan.duration < min_length:
            log.warning("%s overgeslagen: venster valt (grotendeels) buiten de opname", label)
            continue
        out = session.clips_dir / f"clip_{spike.rank:02d}_{clock(spike.onset).replace(':', '')}.mp4"
        log.info("%s -> %s  (opname %s, %.0fs%s)", label, out.name, hms(plan.start - rec_start),
                 plan.duration, "; " + ", ".join(plan.notes) if plan.notes else "")
        cut_clip(plan, session.segments_dir, out, reencode=reencode)
        for stale in (out.with_suffix(".words.json"), out.with_suffix(".ass")):
            stale.unlink(missing_ok=True)  # nieuwe clip = oud transcript klopt niet meer
        made.append(out)
        rows.append([out.name, spike.rank, f"{spike.onset:.0f}", f"{plan.start:.3f}", f"{plan.end:.3f}",
                     f"{spike.strength:.2f}", "+".join(s.file for s in plan.segments)])

    with session.clips_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["clip", "rank", "onset", "start", "end", "strength", "segments"])
        w.writerows(rows)
    return made
