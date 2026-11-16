"""Stap 2: log Twitch-chat anoniem (justinfan, geen token) en tel per 10 s.

Schrijft twee bestanden:
  chat.csv         elk bericht: ontvangsttijd (lokale klok), servertijd, gebruiker, tekst
  chat_counts.csv  live tellingen per bucket: berichten en unieke chatters

We tellen op ontvangsttijd van de lokale klok, niet op Twitch' tmi-sent-ts:
de opname-ijkpunten (record.py) gebruiken dezelfde klok, dus er is geen klokverschil.
"""

from __future__ import annotations

import csv
import logging
import random
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field

from .common import Session, clock

log = logging.getLogger(__name__)

HOST = "irc.chat.twitch.tv"
TLS_PORT = 6697
IDLE_PING = 60       # stuur zelf een PING na zoveel seconden stilte
DEAD_AFTER = 150     # geen enkele data zo lang = verbinding dood, opnieuw verbinden


@dataclass
class IrcMessage:
    command: str
    params: list[str] = field(default_factory=list)
    prefix: str = ""
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def nick(self) -> str:
        return self.prefix.split("!", 1)[0]

    @property
    def text(self) -> str:
        return self.params[-1] if self.params else ""


def parse_irc(line: str) -> IrcMessage | None:
    """Parse één IRC-regel inclusief IRCv3-tags (@a=b;c=d :prefix CMD p1 :trailing)."""
    line = line.rstrip("\r\n")
    if not line:
        return None
    tags: dict[str, str] = {}
    prefix = ""
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        for item in raw_tags.split(";"):
            key, _, value = item.partition("=")
            tags[key] = value
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    elif line.startswith(":"):
        trailing, line = line[1:], ""
    parts = line.split()
    if not parts:
        return None
    params = parts[1:] + ([trailing] if trailing is not None else [])
    return IrcMessage(command=parts[0].upper(), params=params, prefix=prefix, tags=tags)


def clean_text(text: str) -> str:
    """Haal /me-markering (\\x01ACTION ...\\x01) en regeleinden weg."""
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    return text.replace("\r", " ").replace("\n", " ")


class _Reconnect(Exception):
    pass


class BucketCounter:
    """Telt berichten en unieke chatters per bucket, inclusief lege buckets."""

    def __init__(self, size: int, start: float, sink):
        self.size = size
        self.current = int(start // size) * size
        self.messages = 0
        self.users: set[str] = set()
        self.sink = sink  # callable(bucket_start, messages, chatters)

    def add(self, ts: float, user: str) -> None:
        self.roll(ts)
        self.messages += 1
        self.users.add(user)

    def roll(self, now: float) -> None:
        while now >= self.current + self.size:
            self.flush()
            self.current += self.size

    def flush(self) -> None:
        self.sink(self.current, self.messages, len(self.users))
        self.messages = 0
        self.users = set()


class ChatLogger:
    def __init__(
        self,
        channel: str,
        session: Session,
        *,
        bucket: int = 10,
        host: str = HOST,
        port: int = TLS_PORT,
        tls: bool = True,
        stop: threading.Event | None = None,
        echo: bool = True,
    ):
        self.channel = channel.lower().lstrip("#")
        self.session = session
        self.bucket = bucket
        self.host, self.port, self.tls = host, port, tls
        self.stop = stop or threading.Event()
        self.echo = echo
        self.total = 0

    # -- verbinding ---------------------------------------------------------
    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=15)
        if self.tls:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
        sock.settimeout(1.0)
        nick = f"justinfan{random.randint(10000, 99999)}"
        self._send(sock, "CAP REQ :twitch.tv/tags twitch.tv/commands")
        self._send(sock, "PASS SCHMOOPIIE")  # wordt genegeerd voor justinfan-nicks
        self._send(sock, f"NICK {nick}")
        self._send(sock, f"JOIN #{self.channel}")
        log.info("Verbonden met chat van #%s als %s", self.channel, nick)
        return sock

    @staticmethod
    def _send(sock: socket.socket, line: str) -> None:
        sock.sendall((line + "\r\n").encode("utf-8"))

    # -- hoofdlus -------------------------------------------------------------
    def run(self) -> None:
        """Blokkeert tot `stop` gezet wordt. Verbindt zelf opnieuw bij storingen."""
        self.session.root.mkdir(parents=True, exist_ok=True)
        new_chat = not self.session.chat_csv.exists()
        new_counts = not self.session.chat_counts_csv.exists()
        with self.session.chat_csv.open("a", newline="", encoding="utf-8") as chat_f, \
                self.session.chat_counts_csv.open("a", newline="", encoding="utf-8") as count_f:
            chat_w, count_w = csv.writer(chat_f), csv.writer(count_f)
            if new_chat:
                chat_w.writerow(["ts", "server_ts", "user", "text"])
            if new_counts:
                count_w.writerow(["bucket_start", "time", "messages", "chatters"])

            def sink(start: float, messages: int, chatters: int) -> None:
                count_w.writerow([f"{start:.0f}", clock(start), messages, chatters])
                count_f.flush()
                if self.echo:
                    log.info("%s  %4d berichten  %4d chatters", clock(start), messages, chatters)

            counter = BucketCounter(self.bucket, time.time(), sink)
            self.session.write_meta({"chat_start": time.time()})
            try:
                self._connection_loop(chat_w, chat_f, counter)
            except KeyboardInterrupt:
                self.stop.set()
            counter.roll(time.time())
            counter.flush()  # laatste, onvolledige bucket
            self.session.write_meta({"chat_end": time.time()})
        log.info("Chat gestopt; %d berichten gelogd in %s", self.total, self.session.chat_csv)

    def _connection_loop(self, chat_w, chat_f, counter: BucketCounter) -> None:
        backoff = 1.0
        while not self.stop.is_set():
            try:
                sock = self._connect()
                backoff = 1.0
                try:
                    self._read_loop(sock, chat_w, chat_f, counter)
                finally:
                    sock.close()
            except _Reconnect as exc:
                log.info("Chat: opnieuw verbinden (%s)", exc)
            except OSError as exc:
                log.warning("Chat-verbinding weg: %s; opnieuw over %.0fs", exc, backoff)
                if self.stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 30)

    def _read_loop(self, sock, chat_w, chat_f, counter: BucketCounter) -> None:
        buf = b""
        last_rx = last_tx = time.time()
        while not self.stop.is_set():
            try:
                data = sock.recv(65536)
                if not data:
                    raise _Reconnect("server sloot de verbinding")
                last_rx = time.time()
            except (socket.timeout, ssl.SSLWantReadError):
                data = b""
            now = time.time()
            counter.roll(now)
            if now - last_rx > DEAD_AFTER:
                raise _Reconnect(f"{DEAD_AFTER}s niets ontvangen")
            if now - max(last_rx, last_tx) > IDLE_PING:
                self._send(sock, "PING :keepalive")
                last_tx = now
            if not data:
                continue
            buf += data
            *lines, buf = buf.split(b"\r\n")
            wrote = False
            for raw in lines:
                msg = parse_irc(raw.decode("utf-8", errors="replace"))
                if msg is None:
                    continue
                if msg.command == "PING":
                    self._send(sock, f"PONG :{msg.text or 'tmi.twitch.tv'}")
                elif msg.command == "RECONNECT":
                    raise _Reconnect("Twitch vroeg om RECONNECT")
                elif msg.command == "NOTICE":
                    log.warning("Twitch NOTICE: %s", msg.text)
                elif msg.command == "ROOMSTATE":
                    log.debug("Kanaal #%s gejoind", self.channel)
                elif msg.command == "PRIVMSG":
                    user = (msg.nick or msg.tags.get("display-name", "?")).lower()
                    sent = msg.tags.get("tmi-sent-ts", "")
                    server_ts = f"{int(sent) / 1000:.3f}" if sent.isdigit() else ""
                    chat_w.writerow([f"{now:.3f}", server_ts, user, clean_text(msg.text)])
                    counter.add(now, user)
                    self.total += 1
                    wrote = True
            if wrote:
                chat_f.flush()


def log_chat(channel: str, session: Session, *, stop: threading.Event | None = None, **kwargs) -> int:
    """Blokkerend chat loggen tot `stop` of Ctrl+C. Geeft het aantal berichten terug."""
    logger = ChatLogger(channel, session, stop=stop, **kwargs)
    logger.run()
    return logger.total
