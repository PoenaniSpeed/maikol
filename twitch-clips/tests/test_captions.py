import json
import subprocess

import pytest

from conftest import make_source, needs_ffmpeg, probe
from twitchclips.captions import (
    CaptionStyle, Word, _ass_time, build_ass, caption_all, group_words, render_vertical,
)
from twitchclips.common import Session


def _words(spec):
    return [Word(s, e, t) for s, e, t in spec]


def test_group_words_splits_on_count_gap_and_punctuation():
    words = _words([
        (0.0, 0.3, "dit"), (0.3, 0.6, "is"), (0.6, 0.9, "echt"), (0.9, 1.2, "niet"),   # max 3
        (1.2, 1.5, "normaal!"), (1.5, 1.8, "wat"),                                      # '!' breekt
        (3.0, 3.3, "later"),                                                            # gat > 0,6 s
    ])
    cues = group_words(words, max_words=3, max_chars=40)
    assert [[w.text for w in c.words] for c in cues] == [
        ["dit", "is", "echt"], ["niet", "normaal!"], ["wat"], ["later"],
    ]
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:]))
    assert all(c.end - c.start >= 0.3 for c in cues)


def test_ass_time_and_highlight():
    assert _ass_time(3723.456) == "1:02:03.46"
    cues = group_words(_words([(1.0, 1.4, "hallo"), (1.5, 2.0, "{chat}")]))
    ass = build_ass(cues, CaptionStyle())
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(events) == 2                       # één regel per woord (karaoke-markering)
    assert "{\\c&H00E5FF&}HALLO{\\r} (CHAT)" in events[0]
    assert events[0].split(",")[1:3] == ["0:00:01.00", "0:00:01.50"]
    plain = build_ass(cues, CaptionStyle(highlight="", uppercase=False))
    assert "hallo (chat)" in plain


@needs_ffmpeg
@pytest.mark.parametrize("layout", ["blur", "crop"])
def test_render_vertical_with_burned_captions(tmp_path, layout):
    clip = tmp_path / "clip_01.mp4"
    src = make_source(tmp_path / "src.ts", seconds=4, size="640x360")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-c", "copy", str(clip)], check=True)
    ass = tmp_path / "clip_01.ass"
    ass.write_text(build_ass(group_words(_words([(0.2, 1.0, "test"), (1.1, 2.0, "ondertitel")])), CaptionStyle()))
    out = tmp_path / f"out_{layout}.mp4"
    render_vertical(clip, out, ass_path=ass, layout=layout, preset="ultrafast")
    assert probe(out, "stream=width,height").split() == ["1080", "1920"]
    assert float(probe(out, "format=duration")) == pytest.approx(4, abs=0.2)


@needs_ffmpeg
def test_caption_all_uses_cached_transcript(tmp_path):
    """Met een .words.json naast de clip wordt Whisper niet geladen."""
    session = Session(tmp_path)
    session.clips_dir.mkdir()
    src = make_source(tmp_path / "src.ts", seconds=3, size="640x360")
    clip = session.clips_dir / "clip_01_210000.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-c", "copy", str(clip)], check=True)
    clip.with_suffix(".words.json").write_text(json.dumps([{"start": 0.1, "end": 0.8, "text": "hoi"}]))
    finals = caption_all(session, preset="ultrafast", model_name="bestaat-niet")
    assert [p.name for p in finals] == ["clip_01_210000_9x16.mp4"]
    assert "HOI" in clip.with_suffix(".ass").read_text()
