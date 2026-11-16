import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg niet geïnstalleerd")


def make_source(path: Path, seconds: int, fps: int = 10, size: str = "320x180") -> Path:
    """Testvideo waarvan de helderheid de seconde codeert: Y = (floor(t) * 16) mod 256.
    Zo kun je aan het eerste frame van een clip zien waar in de bron hij begint."""
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=black:s={size}:r={fps}:d={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-vf", "format=yuv420p,geq=lum='mod(floor(T)*16,256)':cb=128:cr=128",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", str(fps * 2), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        "-f", "mpegts", str(path),
    ], check=True)
    return path


def first_frame_luma(path: Path) -> float:
    raw = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", "extractplanes=y",
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],  # ruwe Y, geen range-omzetting
        check=True, capture_output=True,
    ).stdout
    return sum(raw) / len(raw)


def probe(path: Path, entries: str) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entries,
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


FAKE_STREAMLINK = """#!/bin/sh
# Doet alsof het streamlink is: realtime MPEG-TS naar stdout, daarna stoppen.
exec ffmpeg -hide_banner -loglevel error -re \\
  -f lavfi -i testsrc=size=160x90:rate=10 -f lavfi -i sine=frequency=440 \\
  -t "${FAKE_SECONDS:-5}" -c:v libx264 -preset ultrafast -g 10 -c:a aac \\
  -flush_packets 1 -f mpegts -
"""


@pytest.fixture
def fake_streamlink(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "streamlink"
    exe.write_text(FAKE_STREAMLINK)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return exe
