"""Stap 1: neem een Twitch-kanaal op met streamlink.

Waarom niet één bestand met één starttijd?
Streamlink (8.x) filtert reclameblokken er altijd uit, en bij een reconnect mist er
ook een stuk. Na de eerste mid-roll loopt de video dan 30-180 s achter op de klok,
en elke chatpiek daarna wijst naar het verkeerde moment.

Daarom: streamlink -> ffmpeg -> segmenten van N seconden, en per segment noteren we
de wall-clock-tijd waarop het bestand ontstond (segments.csv). Elke segmentgrens is
een nieuw ijkpunt; een gat kan hooguit één segment verstoren.
"""

from __future__ import annotations

import csv
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Sequence

from .common import Session, detached_popen_kwargs, require

log = logging.getLogger(__name__)

SEGMENT_PREFIX = "seg_"
SEGMENT_PATTERN = SEGMENT_PREFIX + "%06d.ts"


class SegmentWatcher(threading.Thread):
    """Ziet nieuwe segmentbestanden verschijnen en schrijft hun starttijd weg.

    ffmpeg opent een nieuw segment op het moment dat het eerste pakket ervan
    binnenkomt, dus 'bestand verschenen' = 'beeld van dat moment ontvangen'
    (op de poll-interval van 0,2 s na).
    """

    def __init__(self, seg_dir: Path, csv_path: Path, interval: float = 0.2):
        super().__init__(daemon=True, name="segment-watcher")
        self.seg_dir = seg_dir
        self.csv_path = csv_path
        self.interval = interval
        self.run_id = 0
        self.count = 0
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._seen = {p.name for p in seg_dir.glob(SEGMENT_PREFIX + "*.ts")}
        if self.csv_path.exists():
            with self.csv_path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.count = len(rows)
            self.run_id = max((int(r["run"]) for r in rows), default=-1) + 1
        else:
            with self.csv_path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["index", "file", "run", "wall_start"])

    def scan(self) -> None:
        with self._lock:
            now = time.time()
            new = sorted(
                e.name for e in os.scandir(self.seg_dir)
                if e.name.startswith(SEGMENT_PREFIX) and e.name.endswith(".ts") and e.name not in self._seen
            )
            if not new:
                return
            with self.csv_path.open("a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                for name in new:
                    index = int(name[len(SEGMENT_PREFIX):-3])
                    writer.writerow([index, name, self.run_id, f"{now:.3f}"])
                    self._seen.add(name)
                    self.count += 1
            if len(new) > 1:
                log.warning("%d segmenten tegelijk gezien; de ijktijd van %s is onnauwkeurig", len(new), new[0])

    def next_run(self) -> None:
        with self._lock:
            self.run_id += 1

    def run(self) -> None:
        while not self._halt.wait(self.interval):
            self.scan()

    def stop(self) -> None:
        self._halt.set()
        self.join(timeout=2)
        self.scan()


def _next_index(seg_dir: Path) -> int:
    indices = [int(p.stem[len(SEGMENT_PREFIX):]) for p in seg_dir.glob(SEGMENT_PREFIX + "*.ts")]
    return max(indices, default=-1) + 1


def build_commands(
    channel: str,
    seg_dir: Path,
    *,
    quality: str = "best",
    segment_time: int = 60,
    start_number: int = 0,
    wait_for_live: bool = False,
    streamlink_args: Sequence[str] = (),
) -> tuple[list[str], list[str]]:
    streamlink = [require("streamlink", "pip install streamlink"), "--stdout", "--loglevel", "info"]  # info toont reclameblokken
    if wait_for_live:
        streamlink += ["--retry-streams", "30"]  # wacht (eindeloos) tot het kanaal live gaat
    streamlink += [*streamlink_args, f"https://www.twitch.tv/{channel}", quality]

    ffmpeg = [
        require("ffmpeg", "Installeer ffmpeg."),
        "-hide_banner", "-loglevel", "error",
        # Standaard analyseert ffmpeg 5 s stream vóór het eerste segment opengaat;
        # dan komt het eerste ijkpunt tot 5 s te laat. 1 s is genoeg voor H.264 + AAC.
        "-analyzeduration", "1000000",
        "-i", "pipe:0",
        # Alleen beeld en geluid; Twitch' ID3-metadata kan de TS-muxer laten struikelen.
        "-map", "0:v:0?", "-map", "0:a:0?",
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_time),
        "-segment_start_number", str(start_number),
        "-segment_format", "mpegts",
        # Geen -reset_timestamps: binnen één run lopen de tijdstempels door,
        # zodat cut.py aangrenzende segmenten met concat: aan elkaar kan plakken.
        str(seg_dir / SEGMENT_PATTERN),
    ]
    return streamlink, ffmpeg


def _stop_pipeline(streamlink: subprocess.Popen, ffmpeg: subprocess.Popen) -> None:
    # Stop alleen de bron: ffmpeg krijgt EOF en sluit het laatste segment netjes af.
    if streamlink.poll() is None:
        streamlink.terminate()
    try:
        ffmpeg.wait(timeout=20)
    except subprocess.TimeoutExpired:
        ffmpeg.terminate()
        try:
            ffmpeg.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ffmpeg.kill()
    try:
        streamlink.wait(timeout=5)
    except subprocess.TimeoutExpired:
        streamlink.kill()


def _run_once(cmds: tuple[list[str], list[str]], stop: threading.Event, deadline: float | None) -> int:
    sl_cmd, ff_cmd = cmds
    log.debug("streamlink: %s", " ".join(sl_cmd))
    sl = subprocess.Popen(sl_cmd, stdout=subprocess.PIPE, **detached_popen_kwargs())
    try:
        ff = subprocess.Popen(ff_cmd, stdin=sl.stdout, **detached_popen_kwargs())
    except BaseException:
        sl.kill()
        raise
    sl.stdout.close()  # alleen ffmpeg houdt de pijp open, dus EOF komt aan
    try:
        while ff.poll() is None:
            if stop.wait(0.5) or (deadline is not None and time.time() >= deadline):
                break
    finally:
        _stop_pipeline(sl, ff)
    return sl.returncode if sl.returncode not in (None, 0) else (ff.returncode or 0)


def record(
    channel: str,
    session: Session,
    *,
    quality: str = "best",
    segment_time: int = 60,
    wait_for_live: bool = False,
    max_duration: float | None = None,
    retries: int = 3,
    retry_delay: float = 15,
    streamlink_args: Sequence[str] = (),
    stop: threading.Event | None = None,
) -> int:
    """Neem op tot de stream stopt, `max_duration` verstrijkt of `stop` gezet wordt.

    Een Ctrl+C stopt de opname netjes (geen exception naar buiten).
    Geeft het aantal opgenomen segmenten terug.
    """
    stop = stop or threading.Event()
    session.segments_dir.mkdir(parents=True, exist_ok=True)
    session.write_meta({"channel": channel.lower(), "quality": quality, "segment_time": segment_time})

    watcher = SegmentWatcher(session.segments_dir, session.segments_csv)
    watcher.start()
    deadline = time.time() + max_duration if max_duration else None
    failures = 0
    first_attempt = True
    try:
        while not stop.is_set():
            before = watcher.count
            cmds = build_commands(
                channel, session.segments_dir,
                quality=quality,
                segment_time=segment_time,
                start_number=_next_index(session.segments_dir),
                wait_for_live=wait_for_live and first_attempt,
                streamlink_args=streamlink_args,
            )
            log.info("Opname van twitch.tv/%s gestart (%s, segmenten van %ds)", channel, quality, segment_time)
            code = _run_once(cmds, stop, deadline)
            watcher.scan()
            produced = watcher.count - before
            first_attempt = False

            if stop.is_set() or (deadline is not None and time.time() >= deadline):
                break
            if watcher.count == 0 and not wait_for_live:
                log.error("Geen beeld ontvangen (code %s). Is het kanaal live? Gebruik --wait om te wachten.", code)
                break
            failures = 0 if produced else failures + 1
            if failures > retries:
                log.info("Geen beeld meer na %d pogingen; stream lijkt afgelopen.", retries)
                break
            log.warning("Streamlink stopte (code %s). Nieuwe poging over %.0fs.", code, retry_delay)
            watcher.next_run()  # nieuwe run = tijdstempels beginnen opnieuw, niet aan elkaar plakken
            if stop.wait(retry_delay):
                break
    except KeyboardInterrupt:
        log.info("Opname gestopt (Ctrl+C).")
        stop.set()
    finally:
        watcher.stop()
    log.info("%d segmenten opgenomen in %s", watcher.count, session.segments_dir)
    return watcher.count
