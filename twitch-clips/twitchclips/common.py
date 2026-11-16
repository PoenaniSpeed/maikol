"""Gedeelde helpers: sessie-indeling, ffmpeg/ffprobe-aanroepen, tijdnotatie."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("twitchclips")


_META_LOCK = threading.Lock()


class ToolMissing(RuntimeError):
    pass


def require(binary: str, hint: str = "") -> str:
    path = shutil.which(binary)
    if not path:
        raise ToolMissing(f"'{binary}' niet gevonden in PATH. {hint}".strip())
    return path


class Session:
    """Eén map per opname. Alle stappen lezen en schrijven hier."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    @classmethod
    def create(cls, out_dir: Path | str, channel: str) -> "Session":
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        session = cls(Path(out_dir) / f"{channel.lower()}-{stamp}")
        session.root.mkdir(parents=True, exist_ok=False)
        session.write_meta({"channel": channel.lower(), "created": time.time()})
        return session

    @property
    def segments_dir(self) -> Path:
        return self.root / "segments"

    @property
    def segments_csv(self) -> Path:
        return self.root / "segments.csv"

    @property
    def chat_csv(self) -> Path:
        return self.root / "chat.csv"

    @property
    def chat_counts_csv(self) -> Path:
        return self.root / "chat_counts.csv"

    @property
    def series_csv(self) -> Path:
        return self.root / "series.csv"

    @property
    def spikes_csv(self) -> Path:
        return self.root / "spikes.csv"

    @property
    def clips_dir(self) -> Path:
        return self.root / "clips"

    @property
    def clips_csv(self) -> Path:
        return self.root / "clips.csv"

    @property
    def final_dir(self) -> Path:
        return self.root / "final"

    @property
    def meta_json(self) -> Path:
        return self.root / "session.json"

    def read_meta(self) -> dict:
        try:
            return json.loads(self.meta_json.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def write_meta(self, data: dict) -> None:
        """Voeg sleutels toe aan session.json. Chat-thread en recorder schrijven
        tegelijk; daarom een lock en een atomische vervanging (nooit een half bestand)."""
        with _META_LOCK:
            merged = {**self.read_meta(), **data}
            tmp = self.meta_json.with_name(f".{self.meta_json.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            os.replace(tmp, self.meta_json)


def run_ffmpeg(args: list[str], cwd: Path | None = None) -> None:
    """Draai ffmpeg; bij een fout komt de stderr in de exception."""
    cmd = [require("ffmpeg", "Installeer ffmpeg."), "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg faalde ({proc.returncode}):\n{proc.stderr.strip()[-2000:]}")


def probe_duration(path: Path) -> float:
    cmd = [
        require("ffprobe", "ffprobe hoort bij ffmpeg."),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def clock(ts: float) -> str:
    """Epoch -> lokale kloktijd, bv. 21:04:37."""
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def detached_popen_kwargs() -> dict:
    """Kindprocessen in een eigen procesgroep: Ctrl+C gaat dan alleen naar Python,
    dat de opname netjes afsluit (streamlink stoppen, ffmpeg laten afronden)."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class _ShortNameFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.short = record.name.rsplit(".", 1)[-1]
        return super().format(record)


def setup_logging(verbose: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_ShortNameFormatter("%(asctime)s %(short)-8s %(message)s", "%H:%M:%S"))
    root = logging.getLogger("twitchclips")
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False
