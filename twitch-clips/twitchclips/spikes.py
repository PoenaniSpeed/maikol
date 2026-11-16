"""Stap 3: vind chatpieken.

Een bucket is 'heet' als
    waarde > ratio x mediaan(voorgaande `window` seconden)   EN   waarde >= min_count

De ondergrens is nodig: in stille chat is de mediaan 0 of 1, en dan is
'3x de mediaan' al bij 3 berichten gehaald. De mediaan komt uit de buckets
*vóór* de huidige, zodat een piek zijn eigen drempel niet optilt.

Aaneengesloten hete buckets vormen één gebeurtenis. Het ankerpunt is het
*begin* (onset) van de golf, niet de top: de chat reageert ná het moment, en de
top ligt nog later dan het begin.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from .common import Session, clock

log = logging.getLogger(__name__)

# Bots posten in salvo's (alerts, !commands) en maken nep-pieken.
DEFAULT_IGNORE_USERS = frozenset({
    "nightbot", "streamelements", "streamlabs", "moobot", "fossabot", "wizebot",
    "soundalerts", "sery_bot", "botrixoficial", "kofistreambot", "blerp",
    "pokemoncommunitygame", "streamstickers", "commanderroot",
})


@dataclass
class Bucket:
    start: float
    messages: int = 0
    chatters: int = 0


@dataclass
class Spike:
    onset: float          # begin eerste hete bucket (epoch)
    peak: float           # begin hoogste bucket (epoch)
    peak_value: int
    baseline: float       # mediaan vlak voor de onset
    threshold: float      # drempel vlak voor de onset
    strength: float       # piek / drempel; > 1 betekent boven de drempel
    rank: int = 0


def load_messages(
    chat_csv: Path,
    ignore_users: frozenset[str] = DEFAULT_IGNORE_USERS,
    skip_commands: bool = True,
) -> list[tuple[float, str]]:
    out = []
    with chat_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            user = row["user"].lower()
            if user in ignore_users:
                continue
            if skip_commands and row["text"].lstrip().startswith("!"):
                continue  # '!drop', '!giveaway' enz. zijn geen reacties op beeld
            out.append((float(row["ts"]), user))
    out.sort()
    return out


def bucketize(
    messages: list[tuple[float, str]],
    size: int = 10,
    start: float | None = None,
    end: float | None = None,
) -> list[Bucket]:
    """Tel per bucket, met lege buckets erbij (anders klopt de mediaan niet)."""
    if not messages and (start is None or end is None):
        return []
    lo = start if start is not None else messages[0][0]
    hi = end if end is not None else messages[-1][0]
    first = int(lo // size) * size
    count = int((hi - first) // size) + 1
    buckets = [Bucket(first + i * size) for i in range(count)]
    users: list[set[str]] = [set() for _ in range(count)]
    for ts, user in messages:
        i = int((ts - first) // size)
        if 0 <= i < count:
            buckets[i].messages += 1
            users[i].add(user)
    for b, u in zip(buckets, users):
        b.chatters = len(u)
    return buckets


def detect(
    buckets: list[Bucket],
    *,
    metric: str = "messages",
    ratio: float = 3.0,
    window: int = 300,
    min_count: int = 8,
    warmup: int = 60,
    min_gap: float = 30,
    top: int | None = 10,
) -> tuple[list[dict], list[Spike]]:
    """Geeft (tijdreeks met baseline/drempel per bucket, gekozen pieken)."""
    if not buckets:
        return [], []
    size = int(buckets[1].start - buckets[0].start) if len(buckets) > 1 else 10
    values = [getattr(b, metric) for b in buckets]
    n_window = max(1, window // size)
    n_warmup = max(1, warmup // size)

    series: list[dict] = []
    for i, b in enumerate(buckets):
        history = values[max(0, i - n_window):i]
        baseline = threshold = None
        hot = False
        if len(history) >= n_warmup:
            baseline = median(history)
            threshold = max(ratio * baseline, float(min_count))
            hot = values[i] > ratio * baseline and values[i] >= min_count
        series.append({
            "bucket_start": b.start, "messages": b.messages, "chatters": b.chatters,
            "baseline": baseline, "threshold": threshold, "hot": hot,
        })

    # Groepeer hete buckets; één koude bucket ertussen breekt de golf niet.
    events: list[Spike] = []
    i = 0
    while i < len(series):
        if not series[i]["hot"]:
            i += 1
            continue
        j = i
        while j + 1 < len(series) and (series[j + 1]["hot"] or (j + 2 < len(series) and series[j + 2]["hot"])):
            j += 1
        peak_i = max(range(i, j + 1), key=lambda k: values[k])
        onset = series[i]
        events.append(Spike(
            onset=onset["bucket_start"],
            peak=series[peak_i]["bucket_start"],
            peak_value=values[peak_i],
            baseline=onset["baseline"],
            threshold=onset["threshold"],
            strength=values[peak_i] / onset["threshold"],
        ))
        i = j + 1

    # Sterkste eerst; laat pieken vallen die te dicht bij een sterkere liggen.
    chosen: list[Spike] = []
    for ev in sorted(events, key=lambda e: (e.strength, e.peak_value), reverse=True):
        if all(abs(ev.onset - c.onset) >= min_gap for c in chosen):
            chosen.append(ev)
        if top and len(chosen) >= top:
            break
    for rank, ev in enumerate(chosen, 1):
        ev.rank = rank
    chosen.sort(key=lambda e: e.onset)
    return series, chosen


def write_series(series: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bucket_start", "time", "messages", "chatters", "baseline", "threshold", "hot"])
        for r in series:
            w.writerow([
                f"{r['bucket_start']:.0f}", clock(r["bucket_start"]), r["messages"], r["chatters"],
                "" if r["baseline"] is None else f"{r['baseline']:.1f}",
                "" if r["threshold"] is None else f"{r['threshold']:.1f}",
                int(r["hot"]),
            ])


def write_spikes(spikes: list[Spike], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "onset", "onset_time", "peak", "peak_value", "baseline", "threshold", "strength"])
        for s in spikes:
            w.writerow([
                s.rank, f"{s.onset:.0f}", clock(s.onset), f"{s.peak:.0f}", s.peak_value,
                f"{s.baseline:.1f}", f"{s.threshold:.1f}", f"{s.strength:.2f}",
            ])


def read_spikes(path: Path) -> list[Spike]:
    with path.open(newline="", encoding="utf-8") as f:
        return [
            Spike(
                onset=float(r["onset"]), peak=float(r["peak"]), peak_value=int(r["peak_value"]),
                baseline=float(r["baseline"]), threshold=float(r["threshold"]),
                strength=float(r["strength"]), rank=int(r["rank"]),
            )
            for r in csv.DictReader(f)
        ]


def find_spikes(
    session: Session,
    *,
    bucket: int = 10,
    ignore_users: frozenset[str] = DEFAULT_IGNORE_USERS,
    skip_commands: bool = True,
    **detect_kwargs,
) -> list[Spike]:
    if not session.chat_csv.exists():
        raise FileNotFoundError(f"Geen chatlog: {session.chat_csv}")
    meta = session.read_meta()
    messages = load_messages(session.chat_csv, ignore_users, skip_commands)
    buckets = bucketize(messages, bucket, meta.get("chat_start"), meta.get("chat_end"))
    series, spikes = detect(buckets, **detect_kwargs)
    write_series(series, session.series_csv)
    write_spikes(spikes, session.spikes_csv)

    log.info("%d berichten in %d buckets van %ds; %d pieken gekozen",
             len(messages), len(buckets), bucket, len(spikes))
    for s in spikes:
        log.info("  #%-2d %s  piek %3d  (mediaan %.1f, drempel %.0f, sterkte %.2f)",
                 s.rank, clock(s.onset), s.peak_value, s.baseline, s.threshold, s.strength)
    return spikes
