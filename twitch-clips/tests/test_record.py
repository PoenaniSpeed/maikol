import csv
import sys

import pytest

from conftest import needs_ffmpeg
from twitchclips.common import Session
from twitchclips.record import record


@needs_ffmpeg
@pytest.mark.skipif(sys.platform == "win32", reason="nep-streamlink is een shellscript")
def test_record_anchors_segments_and_reconnects(tmp_path, fake_streamlink, monkeypatch):
    monkeypatch.setenv("FAKE_SECONDS", "5")
    session = Session(tmp_path / "s")
    # Elke 'stream' duurt 5 s en stopt dan; record moet opnieuw starten (nieuwe run).
    count = record("kanaal", session, segment_time=2, max_duration=13, retry_delay=1, retries=1)
    with session.segments_csv.open(newline="") as f:
        rows = list(csv.DictReader(f))

    assert count == len(rows) >= 4
    runs = sorted({int(r["run"]) for r in rows})
    assert runs[:2] == [0, 1], "na het wegvallen hoort een nieuwe run te beginnen"
    for r in rows:
        assert (session.segments_dir / r["file"]).exists()

    # Binnen een run liggen de ijkpunten ~segment_time uit elkaar (realtime binnenkomst).
    # Het eerste segment ontstaat pas na ffmpeg's analyse (~1 s); cut.fix_run_starts
    # corrigeert dat, dus hier alleen: niet later dan een segment.
    run0 = [float(r["wall_start"]) for r in rows if r["run"] == "0"]
    gaps = [b - a for a, b in zip(run0, run0[1:])]
    assert len(gaps) >= 2 and 0.5 <= gaps[0] <= 2.2, gaps
    assert all(1.7 <= g <= 2.3 for g in gaps[1:]), gaps
    # Tussen runs zit minstens de retry-pauze.
    first_run1 = min(float(r["wall_start"]) for r in rows if r["run"] == "1")
    assert first_run1 - run0[-1] >= 1.0
    assert [int(r["index"]) for r in rows] == list(range(len(rows))), "doorlopende nummering"
