import csv
import subprocess

import pytest

from conftest import first_frame_luma, make_source, needs_ffmpeg, probe
from twitchclips.common import Session, probe_duration
from twitchclips.cut import Segment, cut_all, fix_run_starts, plan_clip
from twitchclips.record import SEGMENT_PATTERN
from twitchclips.spikes import Spike, write_spikes

BASE = 1_700_000_000.0


def _segs(starts, runs=None, length=10.0):
    runs = runs or [0] * len(starts)
    segs = [Segment(i, f"seg_{i:06d}.ts", r, BASE + s) for i, (s, r) in enumerate(zip(starts, runs))]
    return segs, (lambda seg: length)


def test_plan_inside_one_segment():
    segs, dur = _segs([0, 10, 20, 30])
    plan = plan_clip(segs, BASE + 12, BASE + 18, dur)
    assert [s.index for s in plan.segments] == [1]
    assert plan.seek == pytest.approx(2) and plan.duration == pytest.approx(6)


def test_plan_spans_segments():
    segs, dur = _segs([0, 10, 20, 30])
    plan = plan_clip(segs, BASE + 5, BASE + 35, dur)
    assert [s.index for s in plan.segments] == [0, 1, 2, 3]
    assert plan.seek == pytest.approx(5) and plan.duration == pytest.approx(30)
    assert not plan.clamped


def test_plan_uses_per_segment_anchor_after_gap():
    # 90 s reclame tussen segment 1 en 2: het ijkpunt van segment 2 vangt dat op.
    segs, dur = _segs([0, 10, 110, 120])
    plan = plan_clip(segs, BASE + 113, BASE + 125, dur)
    assert plan.segments[0].index == 2 and plan.seek == pytest.approx(3)


def test_plan_clamps_at_gap_and_run_boundary():
    segs, dur = _segs([0, 10, 110, 120], runs=[0, 0, 1, 1])
    plan = plan_clip(segs, BASE + 15, BASE + 115, dur)       # loopt het gat in
    assert [s.index for s in plan.segments] == [1]
    assert plan.end == BASE + 20 and plan.clamped
    plan = plan_clip(segs, BASE + 50, BASE + 115, dur)       # begint in het gat
    assert plan.segments[0].index == 2 and plan.start == BASE + 110
    assert plan_clip(segs, BASE + 40, BASE + 60, dur) is None  # helemaal in het gat
    assert plan_clip(segs, BASE + 500, BASE + 530, dur) is None


def test_fix_run_starts_back_calculates_first_anchor():
    # Eerste segment van elke run werd 1,2 s te laat gezien (analyse/salvo).
    segs, dur = _segs([1.2, 10, 20, 101.2, 110], runs=[0, 0, 0, 1, 1])
    fixed = fix_run_starts(segs, dur)
    assert [s.wall_start - BASE for s in fixed] == pytest.approx([0, 10, 20, 100, 110])
    # Een run van één segment heeft geen opvolger: ongemoeid laten.
    segs, dur = _segs([1.2, 50.5], runs=[0, 1])
    assert [s.wall_start - BASE for s in fix_run_starts(segs, dur)] == pytest.approx([1.2, 50.5])


@needs_ffmpeg
def test_cut_lands_on_right_moment(tmp_path):
    """Echte segmenten (zelfde ffmpeg-instellingen als record.py) + een gat van 90 s."""
    session = Session(tmp_path)
    session.segments_dir.mkdir()
    src = make_source(tmp_path / "src.ts", seconds=60)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-f", "segment", "-segment_time", "10",
        "-segment_format", "mpegts", str(session.segments_dir / SEGMENT_PATTERN),
    ], check=True)
    files = sorted(session.segments_dir.glob("seg_*.ts"))
    assert len(files) == 6
    # Segmenten 0-2 op hun 'echte' tijd; 3-5 kwamen 90 s later binnen (reclameblok).
    with session.segments_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "file", "run", "wall_start"])
        for i, p in enumerate(files):
            w.writerow([i, p.name, 0, BASE + i * 10 + (90 if i >= 3 else 0)])
    for p in files:
        assert probe_duration(p) == pytest.approx(10, abs=0.2)

    # Piek 1 rond bron-seconde 25 -> venster 15..35 (over segmentgrenzen heen).
    # Piek 2 op wall-clock BASE+90+45 -> bron-seconde 45 dankzij het ijkpunt van segment 3.
    spikes = [
        Spike(onset=BASE + 25, peak=BASE + 25, peak_value=30, baseline=3, threshold=9, strength=3.3, rank=1),
        Spike(onset=BASE + 135, peak=BASE + 135, peak_value=20, baseline=3, threshold=9, strength=2.2, rank=2),
    ]
    write_spikes(spikes, session.spikes_csv)
    clips = cut_all(session, pre=10, post=10)
    assert len(clips) == 2
    luma = lambda sec: (sec * 16) % 256
    assert first_frame_luma(clips[0]) == pytest.approx(luma(15), abs=4)
    assert first_frame_luma(clips[1]) == pytest.approx(luma(35), abs=4)
    assert float(probe(clips[0], "format=duration")) == pytest.approx(20, abs=0.3)
    with session.clips_csv.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["segments"] == "seg_000001.ts+seg_000002.ts+seg_000003.ts"
