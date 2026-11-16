"""End-to-end: `twitch-clips run` met nep-streamlink en nep-chat, daarna `process` opnieuw."""

import csv
import sys
import time

import pytest

from conftest import needs_ffmpeg, probe
from twitchclips import cli


def fake_log_chat(channel, session, *, stop=None, bucket=10, echo=True):
    """Schrijft een chatlog met een duidelijke golf op t0+8 s en wacht dan op stop."""
    t0 = time.time()
    session.write_meta({"chat_start": t0})
    rows = [(t0 + i * 0.5, f"u{i % 3}", "hi") for i in range(28)]          # 2 per seconde
    rows += [(t0 + 8 + k * 0.05, f"v{k}", "KEKW") for k in range(25)]      # golf
    rows.append((t0 + 8.5, "nightbot", "Volg ons!"))                        # bot: genegeerd
    with session.chat_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "server_ts", "user", "text"])
        for ts, user, text in sorted(rows):
            w.writerow([f"{ts:.3f}", "", user, text])
    stop.wait(60)
    session.write_meta({"chat_end": time.time()})
    return len(rows)


@needs_ffmpeg
@pytest.mark.skipif(sys.platform == "win32", reason="nep-streamlink is een shellscript")
def test_run_end_to_end(tmp_path, fake_streamlink, monkeypatch):
    monkeypatch.setenv("FAKE_SECONDS", "30")
    monkeypatch.setattr(cli, "log_chat", fake_log_chat)
    tuning = ["--bucket", "2", "--window", "6", "--warmup", "4", "--min-count", "5",
              "--pre", "5", "--post", "5", "--no-captions", "--preset", "ultrafast"]
    code = cli.main(["run", "kanaal", "--out", str(tmp_path / "sessions"),
                     "--segment-time", "2", "--duration", "15", "--quiet-chat", *tuning])
    assert code == 0

    (session_dir,) = (tmp_path / "sessions").iterdir()
    assert session_dir.name.startswith("kanaal-")
    finals = sorted((session_dir / "final").glob("*.mp4"))
    assert len(finals) == 1
    assert probe(finals[0], "stream=width,height").split() == ["1080", "1920"]
    assert float(probe(finals[0], "format=duration")) == pytest.approx(10, abs=0.5)

    with (session_dir / "spikes.csv").open(newline="") as f:
        spikes = list(csv.DictReader(f))
    # De golf kan over een bucketgrens vallen; dan is de piek kleiner dan 25, maar nog
    # steeds ruim boven de drempel (3 x mediaan 4 = 12).
    assert len(spikes) == 1 and int(spikes[0]["peak_value"]) > 12

    # Opnieuw verwerken met een ander venster, zonder opnieuw op te nemen. Een oud
    # transcript van de vorige knip mag dan niet hergebruikt worden.
    clip = next((session_dir / "clips").glob("clip_*.mp4"))
    stale = clip.with_suffix(".words.json")
    stale.write_text("[]")
    stray = session_dir / "clips" / "clip_09_000000.mp4"   # rest van een eerdere run
    stray.write_bytes(b"")
    code = cli.main(["process", str(session_dir), *tuning, "--layout", "crop", "--pre", "6", "--post", "4"])
    assert code == 0
    assert not stale.exists()
    assert [p.name for p in (session_dir / "final").glob("*.mp4")] == [finals[0].name]


def test_meta_writes_from_threads_do_not_collide(tmp_path):
    import threading
    from twitchclips.common import Session
    session = Session(tmp_path)
    errors = []

    def hammer(key):
        try:
            for i in range(300):
                session.write_meta({key: i})
        except Exception as exc:  # pragma: no cover - zou niet mogen gebeuren
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(k,)) for k in "abcd"]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errors
    assert session.read_meta() == {"a": 299, "b": 299, "c": 299, "d": 299}


def test_missing_session_is_a_clean_error(tmp_path, capsys):
    assert cli.main(["spikes", str(tmp_path / "bestaat-niet")]) == 1
