import csv
import random

from twitchclips.common import Session
from twitchclips.spikes import Bucket, bucketize, detect, find_spikes, load_messages

T0 = 1_700_000_000.0  # deelbaar door 10


def _buckets(values, size=10):
    return [Bucket(T0 + i * size, messages=v, chatters=v) for i, v in enumerate(values)]


def test_bucketize_fills_gaps():
    msgs = [(T0 + 1, "a"), (T0 + 2, "a"), (T0 + 31, "b")]
    b = bucketize(msgs, 10, start=T0, end=T0 + 45)
    assert [x.messages for x in b] == [2, 0, 0, 1, 0]
    assert [x.chatters for x in b] == [1, 0, 0, 1, 0]


def test_spike_detected_with_onset_and_peak():
    values = [3] * 40 + [12, 30, 20] + [3] * 20
    series, spikes = detect(_buckets(values))
    assert len(spikes) == 1
    s = spikes[0]
    assert s.onset == T0 + 400          # eerste hete bucket, niet de top
    assert s.peak == T0 + 410 and s.peak_value == 30
    assert s.baseline == 3 and s.threshold == 9
    assert series[41]["hot"] and not series[39]["hot"]


def test_quiet_chat_needs_min_count():
    # mediaan 0: zonder ondergrens zou elk bericht een 'piek' zijn
    values = [0, 1, 0, 0, 2, 0, 0, 0] * 10 + [5] + [0] * 10
    _, spikes = detect(_buckets(values), min_count=8)
    assert spikes == []
    _, spikes = detect(_buckets(values), min_count=4)
    assert len(spikes) == 1


def test_single_cold_bucket_does_not_split_event():
    values = [2] * 40 + [20, 2, 25] + [2] * 20
    _, spikes = detect(_buckets(values))
    assert len(spikes) == 1 and spikes[0].peak_value == 25


def test_min_gap_keeps_strongest_and_top_limits():
    values = [2] * 40 + [20] + [2] * 2 + [40] + [2] * 40 + [15] + [2] * 30
    _, spikes = detect(_buckets(values), min_gap=60)
    assert [s.peak_value for s in spikes] == [40, 15]   # 20 ligt 30 s van 40 af
    assert [s.rank for s in spikes] == [1, 2]
    _, spikes = detect(_buckets(values), min_gap=60, top=1)
    assert [s.peak_value for s in spikes] == [40]


def test_warmup_suppresses_start():
    values = [2, 2, 30] + [2] * 50
    _, spikes = detect(_buckets(values), warmup=60)
    assert spikes == []
    _, spikes = detect(_buckets(values), warmup=10)
    assert len(spikes) == 1


def test_load_messages_filters_bots_and_commands(tmp_path):
    p = tmp_path / "chat.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "server_ts", "user", "text"])
        w.writerow([T0 + 1, "", "Nightbot", "Volg op twitter!"])
        w.writerow([T0 + 2, "", "viewer", "!giveaway"])
        w.writerow([T0 + 3, "", "viewer", "KEKW"])
    assert load_messages(p) == [(T0 + 3, "viewer")]
    assert len(load_messages(p, frozenset(), skip_commands=False)) == 3


def test_find_spikes_writes_outputs(tmp_path):
    rng = random.Random(1)
    session = Session(tmp_path)
    session.write_meta({"chat_start": T0, "chat_end": T0 + 900})
    with session.chat_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "server_ts", "user", "text"])
        for i in range(250):                       # ~3 per 10 s
            w.writerow([f"{T0 + rng.uniform(0, 900):.3f}", "", f"u{rng.randrange(40)}", "hi"])
        for i in range(40):                        # golf op 600-620 s
            w.writerow([f"{T0 + 600 + rng.uniform(0, 20):.3f}", "", f"u{i}", "LUL"])
    spikes = find_spikes(session)
    best = min(spikes, key=lambda s: s.rank)
    assert 600 <= best.onset - T0 <= 610 and best.strength > 2
    # Ruis rond de drempel mag meekomen, maar alleen zwak (sterkte ~1).
    assert all(s.strength < 1.5 for s in spikes if s is not best)
    assert session.series_csv.exists() and session.spikes_csv.exists()
