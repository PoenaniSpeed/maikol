"""Stap 5: transcribeer met Whisper (faster-whisper), maak 9:16 en brand ondertitels in.

- faster-whisper i.p.v. openai-whisper: zelfde modellen, ~4x sneller op CPU, geen PyTorch.
- Woord-tijdstempels -> korte ondertitels van max. 3 woorden, actief woord geel.
- Transcript wordt per clip gecachet (.words.json), dus opnieuw stylen kost geen
  nieuwe transcriptie.
- Layouts:
    blur  volledige 16:9-beeld in het midden, vervaagde kopie als achtergrond (standaard)
    crop  9:16-uitsnede uit het beeld (positie met --crop-x, 0 = links, 1 = rechts)
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from .common import Session, run_ffmpeg

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1920


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Cue:
    start: float
    end: float
    words: list[Word]


@dataclass
class CaptionStyle:
    font: str = "Arial"
    size: int = 84
    outline: int = 6
    margin_v: int = 560        # afstand vanaf onderkant; TikTok-UI bedekt de onderste ~20%
    uppercase: bool = True
    highlight: str = "&H00E5FF"  # ASS-kleur in BBGGRR: geel; leeg = geen markering
    max_words: int = 3
    max_chars: int = 20


# -- transcriptie --------------------------------------------------------------

def load_model(name: str = "small", device: str = "auto"):
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper ontbreekt: pip install faster-whisper  (of gebruik --no-captions)"
        ) from exc
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"  # int8 is op CPU ~2x sneller dan float32
    log.info("Whisper-model '%s' laden op %s/%s (eerste keer wordt het gedownload)...", name, device, compute_type)
    return WhisperModel(name, device=device, compute_type=compute_type)


def transcribe(model, media: Path, language: str | None = None) -> list[Word]:
    segments, info = model.transcribe(
        str(media),
        language=language,
        word_timestamps=True,
        vad_filter=True,                 # minder gehallucineerde tekst tijdens game-geluid/stilte
        condition_on_previous_text=False,
    )
    words = [
        Word(float(w.start), float(w.end), w.word.strip())
        for seg in segments for w in (seg.words or []) if w.word.strip()
    ]
    log.info("%s: %d woorden (taal %s)", media.name, len(words), getattr(info, "language", "?"))
    return words


def cached_words(model_factory, media: Path, language: str | None) -> list[Word]:
    cache = media.with_suffix(".words.json")
    if cache.exists():
        return [Word(**w) for w in json.loads(cache.read_text(encoding="utf-8"))]
    words = transcribe(model_factory(), media, language)
    cache.write_text(json.dumps([asdict(w) for w in words], ensure_ascii=False, indent=1), encoding="utf-8")
    return words


# -- ondertitels ---------------------------------------------------------------

def group_words(words: list[Word], max_words: int = 3, max_chars: int = 20, max_gap: float = 0.6) -> list[Cue]:
    """Knip het transcript in korte stukjes die snel leesbaar zijn."""
    cues: list[Cue] = []
    current: list[Word] = []
    for w in words:
        if current:
            chars = sum(len(x.text) + 1 for x in current) + len(w.text)
            breaks = (
                len(current) >= max_words
                or chars > max_chars
                or w.start - current[-1].end > max_gap
                or current[-1].text[-1:] in ".!?"
            )
            if breaks:
                cues.append(Cue(current[0].start, current[-1].end, current))
                current = []
        current.append(w)
    if current:
        cues.append(Cue(current[0].start, current[-1].end, current))

    # Kleine gaten dichten zodat de tekst niet flikkert; minimaal 0,4 s in beeld.
    for a, b in zip(cues, cues[1:]):
        if b.start - a.end < 0.35:
            a.end = b.start
    for c in cues:
        c.end = max(c.end, c.start + 0.4)
    for a, b in zip(cues, cues[1:]):
        a.end = min(a.end, b.start)
    return cues


def _ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000:d}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(cues: list[Cue], style: CaptionStyle) -> str:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{style.font},{style.size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{style.outline},2,2,60,60,{style.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for cue in cues:
        texts = [_ass_escape(w.text.upper() if style.uppercase else w.text) for w in cue.words]
        if not style.highlight:
            lines.append(f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Default,,0,0,0,,{' '.join(texts)}")
            continue
        # Eén regel per woord: hetzelfde stukje tekst, telkens een ander woord gekleurd.
        for k, w in enumerate(cue.words):
            start = cue.start if k == 0 else w.start
            end = cue.words[k + 1].start if k + 1 < len(cue.words) else cue.end
            if end <= start:
                continue
            parts = [f"{{\\c{style.highlight}&}}{t}{{\\r}}" if j == k else t for j, t in enumerate(texts)]
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"


# -- renderen ------------------------------------------------------------------

def vertical_filter(layout: str = "blur", crop_x: float = 0.5, ass_name: str | None = None) -> str:
    if layout == "blur":
        graph = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,"
            f"boxblur=8:2,eq=brightness=-0.12,scale={WIDTH}:{HEIGHT},setsar=1[bgb];"
            f"[fg]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,setsar=1[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2"
        )
    elif layout == "crop":
        x = min(max(crop_x, 0.0), 1.0)
        graph = (
            f"[0:v]crop=w='min(iw,trunc(ih*9/32)*2)':h=ih:x='(iw-ow)*{x}':y=0,"
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
    else:
        raise ValueError(f"Onbekende layout: {layout}")
    if ass_name:
        graph += f",ass={ass_name}"
    return graph + "[v]"


def render_vertical(
    clip: Path,
    out: Path,
    *,
    ass_path: Path | None = None,
    layout: str = "blur",
    crop_x: float = 0.5,
    crf: int = 20,
    preset: str = "medium",
) -> None:
    # ffmpeg draait in de map van het .ass-bestand, zodat de filter alleen een
    # kale bestandsnaam ziet (Windows-paden met ':' breken de filtersyntax).
    cwd = ass_path.parent if ass_path else clip.parent
    graph = vertical_filter(layout, crop_x, ass_path.name if ass_path else None)
    run_ffmpeg([
        "-i", str(clip.resolve()),
        "-filter_complex", graph,
        "-map", "[v]", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=cwd)


def current_clips(session: Session) -> list[Path]:
    """De clips van de laatste `cut`-run (clips.csv), niet oude bestanden van eerdere runs."""
    if session.clips_csv.exists():
        with session.clips_csv.open(newline="", encoding="utf-8") as f:
            names = [row["clip"] for row in csv.DictReader(f)]
        return [session.clips_dir / n for n in names if (session.clips_dir / n).exists()]
    return sorted(session.clips_dir.glob("clip_*.mp4"))


def caption_all(
    session: Session,
    *,
    model_name: str = "small",
    language: str | None = None,
    device: str = "auto",
    captions: bool = True,
    layout: str = "blur",
    crop_x: float = 0.5,
    style: CaptionStyle | None = None,
    crf: int = 20,
    preset: str = "medium",
) -> list[Path]:
    style = style or CaptionStyle()
    clips = current_clips(session)
    if not clips:
        log.warning("Geen clips in %s", session.clips_dir)
        return []
    session.final_dir.mkdir(exist_ok=True)

    model = None

    def model_factory():
        nonlocal model
        if model is None:  # alleen laden als er echt iets te transcriberen valt
            model = load_model(model_name, device=device)
        return model

    made = []
    for clip in clips:
        ass_path = None
        if captions:
            words = cached_words(model_factory, clip, language)
            cues = group_words(words, style.max_words, style.max_chars)
            ass_path = clip.with_suffix(".ass")
            ass_path.write_text(build_ass(cues, style), encoding="utf-8")
        out = session.final_dir / f"{clip.stem}_9x16.mp4"
        log.info("Renderen: %s", out.name)
        render_vertical(clip, out, ass_path=ass_path, layout=layout, crop_x=crop_x, crf=crf, preset=preset)
        made.append(out)
    return made
