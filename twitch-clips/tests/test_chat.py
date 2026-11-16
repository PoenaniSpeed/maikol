import csv
import socket
import threading
import time

from twitchclips.chat import BucketCounter, ChatLogger, clean_text, parse_irc
from twitchclips.common import Session


def test_parse_privmsg_with_tags():
    line = ("@badge-info=;display-name=Foo;tmi-sent-ts=1700000000123;user-id=1 "
            ":foo!foo@foo.tmi.twitch.tv PRIVMSG #chan :hello :) world\r\n")
    msg = parse_irc(line)
    assert msg.command == "PRIVMSG"
    assert msg.nick == "foo"
    assert msg.params == ["#chan", "hello :) world"]
    assert msg.tags["tmi-sent-ts"] == "1700000000123"
    assert msg.tags["badge-info"] == ""


def test_parse_ping_and_numeric():
    assert parse_irc("PING :tmi.twitch.tv").text == "tmi.twitch.tv"
    welcome = parse_irc(":tmi.twitch.tv 001 justinfan123 :Welcome, GLHF!")
    assert welcome.command == "001" and welcome.params == ["justinfan123", "Welcome, GLHF!"]
    assert parse_irc("") is None


def test_clean_text_strips_action():
    assert clean_text("\x01ACTION dances\x01") == "dances"
    assert clean_text("a\nb") == "a b"


def test_bucket_counter_emits_empty_buckets():
    rows = []
    c = BucketCounter(10, start=100.0, sink=lambda *r: rows.append(r))
    c.add(101, "a")
    c.add(105, "a")
    c.add(108, "b")
    c.add(135, "c")      # 110 en 120 zijn leeg
    c.roll(140)
    assert rows == [(100, 3, 2), (110, 0, 0), (120, 0, 0), (130, 1, 1)]


def test_logger_against_fake_irc_server(tmp_path):
    """Volledige ronde: login, PING/PONG, berichten, RECONNECT, opnieuw verbinden."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    received: list[str] = []
    connections = []

    def serve():
        for n in range(2):
            conn, _ = server.accept()
            connections.append(conn)
            f = conn.makefile("rb")
            for _ in range(4):  # CAP, PASS, NICK, JOIN
                received.append(f.readline().decode().strip())
            conn.sendall(b":tmi.twitch.tv 001 justinfan1 :Welcome\r\n")
            if n == 0:
                conn.sendall(b"PING :tmi.twitch.tv\r\n")
                received.append(f.readline().decode().strip())
                conn.sendall(
                    b"@display-name=Alice;tmi-sent-ts=1700000000000 :alice!alice@x PRIVMSG #test :hoi\r\n"
                    b":bob!bob@x PRIVMSG #test :\x01ACTION lacht\x01\r\n"
                    b":nightbot!nightbot@x PRIVMSG #test :!discord\r\n"
                )
                time.sleep(0.2)
                conn.sendall(b":tmi.twitch.tv RECONNECT\r\n")
            else:
                conn.sendall(b":carol!carol@x PRIVMSG #test :na reconnect\r\n")

    threading.Thread(target=serve, daemon=True).start()
    session = Session(tmp_path / "s")
    stop = threading.Event()
    logger = ChatLogger("Test", session, host="127.0.0.1", port=port, tls=False, stop=stop, echo=False)
    t = threading.Thread(target=logger.run)
    t.start()
    deadline = time.time() + 10
    while logger.total < 4 and time.time() < deadline:
        time.sleep(0.05)
    stop.set()
    t.join(timeout=5)
    server.close()

    assert received[0] == "CAP REQ :twitch.tv/tags twitch.tv/commands"
    assert received[2].startswith("NICK justinfan")
    assert received[3] == "JOIN #test"
    assert received[4] == "PONG :tmi.twitch.tv"
    assert len(connections) == 2, "na RECONNECT moet er opnieuw verbonden worden"

    with session.chat_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["user"], r["text"]) for r in rows] == [
        ("alice", "hoi"), ("bob", "lacht"), ("nightbot", "!discord"), ("carol", "na reconnect"),
    ]
    assert rows[0]["server_ts"] == "1700000000.000"
    assert rows[1]["server_ts"] == ""
    with session.chat_counts_csv.open(newline="", encoding="utf-8") as f:
        counts = list(csv.DictReader(f))
    assert sum(int(c["messages"]) for c in counts) == 4
    meta = session.read_meta()
    assert meta["chat_start"] <= meta["chat_end"]
