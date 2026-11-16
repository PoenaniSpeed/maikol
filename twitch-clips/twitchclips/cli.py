"""Eén command voor de hele pijplijn, plus losse subcommands per stap.

  twitch-clips run KANAAL            opnemen + chat loggen; na afloop (of Ctrl+C) clips maken
  twitch-clips process SESSIEMAP     pieken -> knippen -> ondertitelen op een bestaande opname
  twitch-clips record|chat KANAAL    alleen opnemen / alleen chat loggen
  twitch-clips spikes|cut|captions SESSIEMAP
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from . import __version__
from .captions import CaptionStyle, caption_all
from .chat import log_chat
from .common import Session, ToolMissing, setup_logging
from .cut import cut_all
from .record import record
from .spikes import DEFAULT_IGNORE_USERS, find_spikes

log = logging.getLogger("twitchclips.cli")


# -- argumentgroepen -------------------------------------------------------------

def _add_session_target(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default="sessions", help="map waarin een nieuwe sessie komt (standaard: sessions)")
    p.add_argument("--session", help="bestaande sessiemap gebruiken (bv. record en chat in twee terminals)")


def _add_record_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("opname")
    g.add_argument("--quality", default="best", help="streamlink-kwaliteit (best, 1080p60, 720p60, ...)")
    g.add_argument("--segment-time", type=int, default=60, help="seconden per segment = ijkinterval (60)")
    g.add_argument("--wait", action="store_true", help="wacht tot het kanaal live gaat")
    g.add_argument("--duration", type=float, help="stop na zoveel seconden")
    g.add_argument("--retries", type=int, default=3, help="herstartpogingen na wegvallen stream (3)")
    g.add_argument("--streamlink-arg", action="append", default=[], metavar="ARG",
                   help="extra streamlink-argument, herhaalbaar (bv. --streamlink-arg=--twitch-low-latency)")


def _add_chat_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("chat")
    g.add_argument("--quiet-chat", action="store_true", help="geen telling per bucket in de terminal")


def _add_spike_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("pieken")
    g.add_argument("--ratio", type=float, default=3.0, help="piek = waarde > ratio x rollende mediaan (3.0)")
    g.add_argument("--window", type=int, default=300, help="venster rollende mediaan in seconden (300)")
    g.add_argument("--min-count", type=int, default=8, help="absolute ondergrens per bucket (8)")
    g.add_argument("--warmup", type=int, default=60, help="geen pieken in de eerste N seconden (60)")
    g.add_argument("--min-gap", type=float, default=30, help="min. seconden tussen twee clips (30)")
    g.add_argument("--top", type=int, default=10, help="maximaal aantal clips (10, 0 = alles)")
    g.add_argument("--metric", choices=["messages", "chatters"], default="messages",
                   help="tel berichten of unieke chatters (chatters is robuuster tegen spam)")
    g.add_argument("--keep-bots", action="store_true", help="bots (Nightbot e.d.) meetellen")
    g.add_argument("--keep-commands", action="store_true", help="!commands meetellen")


def _add_bucket_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bucket", type=int, default=10, help="bucketgrootte in seconden (10)")


def _add_cut_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("knippen")
    g.add_argument("--pre", type=float, default=20, help="seconden vóór de piek (20)")
    g.add_argument("--post", type=float, default=10, help="seconden na de piek (10)")
    g.add_argument("--offset", type=float, default=0.0,
                   help="verschuif alle vensters (s); negatief als clips te laat beginnen")
    g.add_argument("--copy", action="store_true", help="knippen zonder her-encoderen (sneller, op keyframes)")


def _add_caption_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("ondertitels en 9:16")
    g.add_argument("--model", default="small", help="Whisper-model: tiny/base/small/medium/large-v3 (small)")
    g.add_argument("--language", help="taalcode, bv. nl of en (standaard: automatisch per clip)")
    g.add_argument("--device", default="auto", help="auto, cpu of cuda")
    g.add_argument("--no-captions", action="store_true", help="alleen 9:16 maken, zonder Whisper")
    g.add_argument("--layout", choices=["blur", "crop"], default="blur", help="blur (hele beeld) of crop")
    g.add_argument("--crop-x", type=float, default=0.5, help="horizontale uitsnede bij crop, 0..1 (0.5)")
    g.add_argument("--font", default="Arial")
    g.add_argument("--font-size", type=int, default=84)
    g.add_argument("--margin-v", type=int, default=560, help="ondertitels zoveel px boven de onderrand (560)")
    g.add_argument("--no-uppercase", action="store_true")
    g.add_argument("--no-highlight", action="store_true", help="actieve woord niet geel kleuren")
    g.add_argument("--crf", type=int, default=20)
    g.add_argument("--preset", default="medium", help="x264-preset (medium)")


# -- uitvoering ----------------------------------------------------------------

def _session_for(args, channel: str) -> Session:
    if args.session:
        session = Session(args.session)
        session.root.mkdir(parents=True, exist_ok=True)
        if not session.read_meta():
            session.write_meta({"channel": channel.lower()})
        return session
    return Session.create(args.out, channel)


def _existing_session(path: str) -> Session:
    session = Session(path)
    if not session.root.is_dir():
        raise FileNotFoundError(f"Sessiemap bestaat niet: {path}")
    return session


def _do_record(args, session: Session, stop: threading.Event | None = None) -> int:
    return record(
        args.channel, session,
        quality=args.quality,
        segment_time=args.segment_time,
        wait_for_live=args.wait,
        max_duration=args.duration,
        retries=args.retries,
        streamlink_args=args.streamlink_arg,
        stop=stop,
    )


def _do_spikes(args, session: Session):
    return find_spikes(
        session,
        bucket=args.bucket,
        ignore_users=frozenset() if args.keep_bots else DEFAULT_IGNORE_USERS,
        skip_commands=not args.keep_commands,
        metric=args.metric,
        ratio=args.ratio,
        window=args.window,
        min_count=args.min_count,
        warmup=args.warmup,
        min_gap=args.min_gap,
        top=args.top or None,
    )


def _do_cut(args, session: Session):
    return cut_all(session, pre=args.pre, post=args.post, offset=args.offset, reencode=not args.copy)


def _do_captions(args, session: Session):
    style = CaptionStyle(
        font=args.font,
        size=args.font_size,
        margin_v=args.margin_v,
        uppercase=not args.no_uppercase,
        highlight="" if args.no_highlight else CaptionStyle.highlight,
    )
    return caption_all(
        session,
        model_name=args.model,
        language=args.language,
        device=args.device,
        captions=not args.no_captions,
        layout=args.layout,
        crop_x=args.crop_x,
        style=style,
        crf=args.crf,
        preset=args.preset,
    )


def _do_process(args, session: Session) -> int:
    spikes = _do_spikes(args, session)
    if not spikes:
        log.warning("Geen pieken gevonden. Probeer --min-count lager of --ratio lager; zie series.csv.")
        return 0
    clips = _do_cut(args, session)
    finals = _do_captions(args, session)
    log.info("Klaar: %d clips in %s", len(finals), session.final_dir)
    for path in finals:
        print(path)
    return 0 if clips else 1


def cmd_run(args) -> int:
    session = Session.create(args.out, args.channel)
    log.info("Sessie: %s  (Ctrl+C stopt de opname en maakt de clips)", session.root)
    stop = threading.Event()
    chat = threading.Thread(
        target=log_chat, args=(args.channel, session),
        kwargs={"stop": stop, "bucket": args.bucket, "echo": not args.quiet_chat},
        name="chat", daemon=True,
    )
    chat.start()
    try:
        count = _do_record(args, session, stop)
    finally:
        stop.set()
        chat.join(timeout=10)
    if count == 0:
        log.error("Niets opgenomen. Is het kanaal live? (gebruik --wait om te wachten)")
        return 1
    return _do_process(args, session)


def cmd_record(args) -> int:
    session = _session_for(args, args.channel)
    log.info("Sessie: %s", session.root)
    return 0 if _do_record(args, session) else 1


def cmd_chat(args) -> int:
    session = _session_for(args, args.channel)
    log.info("Sessie: %s  (Ctrl+C om te stoppen)", session.root)
    log_chat(args.channel, session, bucket=args.bucket, echo=not args.quiet_chat)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twitch-clips",
        description="Twitch opnemen, chatpieken vinden en 9:16-clips met ondertitels maken.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("run", help="alles: opnemen + chat, daarna pieken/knippen/ondertitelen")
    p.add_argument("channel")
    p.add_argument("--out", default="sessions", help="map voor sessies (standaard: sessions)")
    _add_bucket_arg(p)
    _add_record_args(p)
    _add_chat_args(p)
    _add_spike_args(p)
    _add_cut_args(p)
    _add_caption_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("process", help="pieken + knippen + ondertitelen op een bestaande sessie")
    p.add_argument("session_dir")
    _add_bucket_arg(p)
    _add_spike_args(p)
    _add_cut_args(p)
    _add_caption_args(p)
    p.set_defaults(func=lambda a: _do_process(a, _existing_session(a.session_dir)))

    p = sub.add_parser("record", help="stap 1: alleen opnemen")
    p.add_argument("channel")
    _add_session_target(p)
    _add_record_args(p)
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("chat", help="stap 2: alleen chat loggen")
    p.add_argument("channel")
    _add_session_target(p)
    _add_bucket_arg(p)
    _add_chat_args(p)
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("spikes", help="stap 3: pieken zoeken")
    p.add_argument("session_dir")
    _add_bucket_arg(p)
    _add_spike_args(p)
    p.set_defaults(func=lambda a: (_do_spikes(a, _existing_session(a.session_dir)), 0)[1])

    p = sub.add_parser("cut", help="stap 4: clips knippen uit spikes.csv")
    p.add_argument("session_dir")
    _add_cut_args(p)
    p.set_defaults(func=lambda a: 0 if _do_cut(a, _existing_session(a.session_dir)) else 1)

    p = sub.add_parser("captions", help="stap 5: transcriberen, 9:16, ondertitels inbranden")
    p.add_argument("session_dir")
    _add_caption_args(p)
    p.set_defaults(func=lambda a: 0 if _do_captions(a, _existing_session(a.session_dir)) else 1)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        return args.func(args)
    except (ToolMissing, FileNotFoundError, RuntimeError, ValueError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.error("Afgebroken.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
