"""Versioned, non-destructive clip drafts and deterministic render instructions.

Originals are never modified. Drafts, their revisions and render jobs are rows
(see ``editor_state``). A session's directory under ``TEMP_DIR/editor`` only
caches its media on the machine that is rendering it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
import hashlib
import json
import os
import subprocess
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

BROLL_MIN_SECONDS = 0.2
BROLL_CONTINUATION_MIN_SECONDS = 0.04
BROLL_FADE_SECONDS = 0.25
TIME_EPSILON = 1e-9


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Segment(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end - self.start < 0.04 - TIME_EPSILON:
            raise ValueError("Each segment must be at least one frame long")
        return self


class Word(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=120)
    highlight: bool = False

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Caption end must follow its start")
        return self


class CaptionStyle(Model):
    enabled: bool = True
    font: Literal[
        "TikTokSans-Regular", "TikTokSans-Bold", "Poppins-ExtraBold", "THEBOLDFONT"
    ] = "Poppins-ExtraBold"
    # 1080-wide units; 132 matches the generation renderer's largest caption.
    size: int = Field(default=54, ge=20, le=132)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    accent: str = Field(default="#FACC15", pattern=r"^#[0-9a-fA-F]{6}$")
    y: float = Field(default=0.78, ge=0.1, le=0.9)
    background: bool = True
    wordsPerLine: int = Field(default=4, ge=1, le=8)
    # Colour the word being spoken with the accent, as the generated clip does.
    karaoke: bool = False
    uppercase: bool = False
    # Caption template the clip was generated with; supplies the animation, outline,
    # word pill and emphasis styling that the fields above don't expose.
    template: str = Field(default="default", min_length=1, max_length=50)


class Framing(Model):
    aspect: Literal["vertical", "square", "original"] = "vertical"
    fit: Literal["contain", "cover", "auto"] = "cover"
    zoom: float = Field(default=1, ge=1, le=3)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class Effects(Model):
    brightness: float = Field(default=100, ge=40, le=180)
    contrast: float = Field(default=100, ge=40, le=180)
    saturation: float = Field(default=100, ge=0, le=220)
    blur: float = Field(default=0, ge=0, le=8)
    hue: float = Field(default=0, ge=-180, le=180)


class BRollFootage(Model):
    """A stock video rendition. Only Pexels' video CDN is ever downloaded."""

    provider: Literal["pexels"] = "pexels"
    id: int = Field(gt=0)
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    duration: float = Field(gt=0, le=3600)
    fileUrl: str = Field(pattern=r"^https://videos\.pexels\.com/[^\s]{1,500}$")
    pageUrl: str = Field(default="", max_length=500)
    thumbnail: str = Field(default="", max_length=500)
    credit: str = Field(default="", max_length=200)

    @property
    def key(self) -> str:
        return f"{self.id}/{self.width}x{self.height}"


class BRollClip(Model):
    """Stock footage shown over the clip from ``start`` to ``end`` (clean.mp4 time).

    Like caption words, it lives on the source timeline, so cuts, reorders and
    duplicated segments carry it along. ``offset`` is where in the footage it begins.
    """

    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    offset: float = Field(default=0, ge=0, le=3600)
    query: str = Field(default="", max_length=120)
    footage: BRollFootage
    # Combined cuts remain separate footage ranges, without adding dissolves at the join.
    fadeIn: bool = True
    fadeOut: bool = True
    fadeDuration: float | None = Field(default=None, gt=0, le=BROLL_FADE_SECONDS)

    @model_validator(mode="after")
    def ordered(self):
        minimum = BROLL_MIN_SECONDS if self.fadeIn and self.fadeOut else BROLL_CONTINUATION_MIN_SECONDS
        if self.end - self.start < minimum - TIME_EPSILON:
            raise ValueError(f"B-roll must be at least {minimum} seconds long")
        return self


class HookTiming(Model):
    start: float = Field(ge=0, le=86400)
    end: float = Field(gt=0, le=86400)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Title end must follow its start")
        return self


class TextTransform(Model):
    x: float = Field(default=0, ge=-1, le=1)
    y: float = Field(default=0, ge=-1, le=1)
    scale: float = Field(default=1, ge=0.25, le=4)


class CaptionTransform(TextTransform):
    size: float | None = Field(default=None, ge=5, le=528)


class WebcamRegion(Model):
    x: float = Field(default=0.02, ge=0.0, le=1.0)
    y: float = Field(default=0.03, ge=0.0, le=1.0)
    w: float = Field(default=0.26, ge=0.05, le=1.0)
    h: float = Field(default=0.28, ge=0.05, le=1.0)
    zoom: float = Field(default=1.0, ge=0.5, le=2.5)


class CameraKeyframe(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    mode: Literal["punch", "slow_tension", "shake", "bw_dramatic"] = "punch"
    zoom: float = Field(default=1.22, ge=1.0, le=2.5)
    intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    target: Literal["center", "cam", "game"] = "center"
    reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Camera keyframe end must follow its start")
        return self


class LayoutSwitch(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    layout: Literal["split", "fullscreen_cam", "fullscreen_game", "pip"] = "split"

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Layout switch end must follow its start")
        return self


class TimeEffect(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    kind: Literal["freeze_bw", "explosion_cut", "speed_ramp", "cold_open"] = "freeze_bw"
    freezeDuration: float = Field(default=1.5, ge=0.2, le=5.0)
    speed: float = Field(default=2.5, ge=0.5, le=4.0)
    reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Time effect end must follow its start")
        return self


class SubtextOverlay(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=160)
    x: float = Field(default=0.5, ge=0.05, le=0.95)
    y: float = Field(default=0.28, ge=0.05, le=0.95)
    color: str = Field(default="#FACC15", pattern=r"^#[0-9a-fA-F]{6}$")
    variant: Literal["thought", "tracker", "censor_bar"] = "thought"

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Subtext end must follow its start")
        return self


class SfxPlacement(Model):
    id: str = Field(min_length=1, max_length=100)
    sfxId: Literal[
        "vine_boom",
        "record_scratch",
        "windows_error",
        "crickets",
        "censor_beep",
        "sad_violin",
        "tension_riser",
        "whoosh",
    ] = "vine_boom"
    start: float = Field(ge=0)
    volume: float = Field(default=0.85, ge=0.0, le=2.0)


class BackgroundMusic(Model):
    trackId: Literal[
        "none",
        "sad_drama",
        "wii_quirky",
        "lofi_chill",
        "tension_suspense",
        "hype_phonk",
    ] = "none"
    volume: float = Field(default=0.28, ge=0.0, le=1.5)
    autoDuck: bool = True


class VoiceEffect(Model):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    effect: Literal["bass_boost", "reverb"] = "bass_boost"

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Voice effect end must follow its start")
        return self


class ViralSuggestion(Model):
    id: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    timestamp: float = Field(ge=0)
    end: float = Field(gt=0)
    category: Literal["camera", "fail", "explosion", "speed", "subtext", "sfx", "layout"] = "camera"
    title: str = Field(min_length=1, max_length=140)
    reason: str = Field(default="", max_length=260)


class ViralEditConfig(Model):
    mode: Literal["auto", "hybrid"] = "hybrid"
    vibe: Literal["zoeira", "clutch", "fail", "hype"] = "zoeira"
    layoutMode: Literal["off", "split", "fullscreen_cam", "fullscreen_game", "pip"] = "off"
    webcamBox: WebcamRegion = Field(default_factory=WebcamRegion)
    layoutSwitches: list[LayoutSwitch] = Field(default_factory=list, max_length=60)
    cameraKeyframes: list[CameraKeyframe] = Field(default_factory=list, max_length=80)
    timeEffects: list[TimeEffect] = Field(default_factory=list, max_length=40)
    subtexts: list[SubtextOverlay] = Field(default_factory=list, max_length=60)
    speakerColors: dict[Annotated[str, Field(max_length=40)], Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]] = Field(default_factory=dict)
    sfx: list[SfxPlacement] = Field(default_factory=list, max_length=80)
    music: BackgroundMusic = Field(default_factory=BackgroundMusic)
    voiceEffects: list[VoiceEffect] = Field(default_factory=list, max_length=40)
    suggestions: list[ViralSuggestion] = Field(default_factory=list, max_length=80)


class EditDocument(Model):
    version: Literal[1] = 1
    segments: list[Segment] = Field(min_length=1, max_length=100)
    words: list[Word] = Field(default_factory=list, max_length=5000)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    framing: Framing = Field(default_factory=Framing)
    effects: Effects = Field(default_factory=Effects)
    volume: float = Field(default=1, ge=0, le=2)
    muted: bool = False
    # AI headline burned into the top safe area for the first seconds, as generated.
    hookTitle: str = Field(default="", max_length=200)
    hookTiming: HookTiming | None = None
    textTransforms: dict[Literal["hook", "captions"], TextTransform] = Field(default_factory=dict)
    captionTransforms: dict[Annotated[str, Field(max_length=300)], CaptionTransform] = Field(default_factory=dict, max_length=5000)
    broll: list[BRollClip] = Field(default_factory=list, max_length=60)
    viral: ViralEditConfig | None = None

    def validate_duration(self, duration: float):
        if any(s.end > duration + 0.05 for s in self.segments):
            raise ValueError("An edit extends past the original clip")
        if any(w.end > duration + 0.05 for w in self.words):
            raise ValueError("A caption extends past the original clip")
        if any(b.end > duration + 0.05 for b in self.broll):
            raise ValueError("B-roll extends past the original clip")
        if len({s.id for s in self.segments}) != len(self.segments) or len(
            {w.id for w in self.words}
        ) != len(self.words) or len({b.id for b in self.broll}) != len(self.broll):
            raise ValueError("Editor item IDs must be unique")
        ordered = sorted(self.broll, key=lambda b: b.start)
        if any(later.start < earlier.end - 0.01 for earlier, later in zip(ordered, ordered[1:])):
            raise ValueError("B-roll clips can't overlap")
        return self


def editor_dir(temp_dir: str, task_id: str, clip_id: str, filename: str) -> Path:
    # No request-controlled string is ever used as a path component.
    key = hashlib.sha256(f"{task_id}:{clip_id}:{filename}".encode()).hexdigest()
    return Path(temp_dir) / "editor" / key


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        temporary.write_text(json.dumps(value, allow_nan=False))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RevisionConflict(ValueError):
    pass


def output_size(document: EditDocument, width: int, height: int):
    if document.framing.aspect == "square":
        return 1080, 1080
    if document.framing.aspect == "vertical":
        return 1080, 1920
    scale = min(1, 1920 / max(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def mapped_words(document: EditDocument):
    result = []
    cursor = 0.0
    previous_end = None
    previous_words = {}
    ordered_words = sorted(document.words, key=lambda w: w.start)
    for segment in document.segments:
        continuous = previous_end is not None and abs(segment.start - previous_end) < 1e-6
        current_words = {}
        for word in ordered_words:
            start, end = max(segment.start, word.start), min(segment.end, word.end)
            if end > start and word.text.strip():
                piece = {
                    **word.model_dump(),
                    "start": cursor + start - segment.start,
                    "end": cursor + end - segment.start,
                }
                previous = previous_words.get(word.id) if continuous else None
                if previous is not None and abs(previous["end"] - piece["start"]) < 1e-6:
                    previous["end"] = piece["end"]
                    current_words[word.id] = previous
                else:
                    result.append(piece)
                    current_words[word.id] = piece
        cursor += segment.end - segment.start
        previous_end = segment.end
        previous_words = current_words
    return result


def continuous_segments(segments: list[Segment]) -> list[Segment]:
    result = []
    for segment in segments:
        if result and abs(result[-1].end - segment.start) < TIME_EPSILON:
            result[-1] = result[-1].model_copy(update={"end": segment.end})
        else:
            result.append(segment)
    return result


def caption_key(document: EditDocument, words: list[dict]) -> str:
    cursor = 0.0
    segment = document.segments[-1]
    for segment in continuous_segments(document.segments):
        cursor += segment.end - segment.start
        if words[0]["start"] < cursor - TIME_EPSILON:
            break
    return json.dumps([segment.id, words[0]["id"]], separators=(",", ":"), ensure_ascii=False)


def mapped_broll(document: EditDocument) -> list[dict]:
    """B-roll pieces on the output timeline, following segments like captions do.

    A clip cut by a segment boundary becomes several pieces; only a piece that
    starts (or ends) where the clip does fades in (or out), so a cutaway keeps
    playing across a cut instead of dissolving at it.
    """
    pieces = []
    cursor = 0.0
    ordered = sorted(document.broll, key=lambda b: b.start)
    for segment in document.segments:
        for clip in ordered:
            start, end = max(segment.start, clip.start), min(segment.end, clip.end)
            if end - start >= BROLL_CONTINUATION_MIN_SECONDS - TIME_EPSILON:
                pieces.append(
                    {
                        "id": clip.id,
                        "footage": clip.footage,
                        "query": clip.query,
                        "start": cursor + start - segment.start,
                        "duration": end - start,
                        "offset": clip.offset + start - clip.start,
                        "fade_in": clip.fadeIn and start - clip.start < 0.02,
                        "fade_out": clip.fadeOut and clip.end - end < 0.02,
                        "fade_duration": clip.fadeDuration or min(BROLL_FADE_SECONDS, (clip.end - clip.start) / 3),
                    }
                )
        cursor += segment.end - segment.start
    return pieces


def editor_caption_template(style: CaptionStyle) -> dict:
    """The generation template with the editor's own style choices applied on top."""
    from .caption_templates import get_template

    template = dict(get_template(style.template))
    # Templates that emphasise words in their highlight colour follow the editor's choice.
    if (template.get("emphasis_color") or template.get("highlight_color")) == template.get("highlight_color"):
        template["emphasis_color"] = style.accent
    template.update(
        font_color=style.color,
        highlight_color=style.accent,
        uppercase=style.uppercase,
        max_words_per_line=style.wordsPerLine,
    )
    if style.background:
        template["background"] = True
        template["background_color"] = template.get("background_color") or "#00000080"
    else:
        template["background"] = False
        template["background_color"] = None
    if style.karaoke:
        template["animation"] = "karaoke"
    elif template["animation"] == "karaoke":
        template["animation"] = "none"
    return template


def write_ass(path: Path, document: EditDocument, width: int, height: int):
    """Write captions with the generation renderer so edits keep the clip's animation."""
    from .media.captions import build_assemblyai_ass_subtitles

    style = document.captions
    words = mapped_words(document) if style.enabled else []
    caption_transform = document.textTransforms.get("captions", TextTransform())
    hook_transform = document.textTransforms.get("hook", TextTransform())
    original_font_px = max(1, round(style.size * width / 1080))
    font_px = max(1, round(style.size * width * caption_transform.scale / 1080))
    placements = []
    for index in range(0, len(words), style.wordsPerLine):
        group = words[index:index + style.wordsPerLine]
        placement = document.captionTransforms.get(caption_key(document, group))
        placements.append(None if placement is None else {
            "font_px": max(1, round((placement.size if placement.size is not None else style.size) * width * placement.scale / 1080)),
            "x": round((0.5 + placement.x) * width),
            "y": round((style.y + placement.y) * height),
        })
    written = build_assemblyai_ass_subtitles(
        path,
        0,
        sum(s.end - s.start for s in document.segments),
        width,
        height,
        path,
        font_family=style.font,
        caption_words=words,
        include_captions=style.enabled,
        template_override=editor_caption_template(style),
        font_px_override=font_px,
        # The editor's position is the caption centre, already kept in range.
        center_y_override=round((style.y + caption_transform.y) * height),
        center_x_override=round((0.5 + caption_transform.x) * width),
        hook_editor_style={
            **hook_transform.model_dump(),
            "base_px": max(34, min(66, int(original_font_px * 0.82))),
            **(document.hookTiming.model_dump() if document.hookTiming else {}),
        },
        # Highlights are per occurrence and already include the generated emphasis.
        emphasis_indices=[i for i, w in enumerate(words) if w["highlight"]],
        wrap_lines=True,
        hook_title=document.hookTitle.strip() or None,
        caption_placements=placements,
    )
    if not written:
        # Nothing to show (no captions or hook title): keep ffmpeg's ass filter valid.
        path.write_text(
            f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
    if document.viral and document.viral.subtexts:
        _append_viral_subtexts_ass(path, document, width, height)


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_bgr(hex_color: str) -> str:
    c = (hex_color or "#FACC15").lstrip("#")
    if len(c) != 6:
        c = "FACC15"
    return f"&H00{c[4:6]}{c[2:4]}{c[0:2]}&"


def map_source_span(document: EditDocument, start: float, end: float) -> list[tuple[float, float]]:
    """Map a source-time interval onto the output timeline across kept segments."""
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for seg in document.segments:
        s, e = max(seg.start, start), min(seg.end, end)
        if e - s > 0.02:
            spans.append((cursor + (s - seg.start), cursor + (e - seg.start)))
        cursor += seg.end - seg.start
    return spans


def _append_viral_subtexts_ass(path: Path, document: EditDocument, width: int, height: int):
    viral = document.viral
    if not viral or not viral.subtexts:
        return
    content = path.read_text(encoding="utf-8")
    font_px = max(28, round(46 * width / 1080))
    style_line = (
        f"Style: ViralSubtext,{document.captions.font},{font_px},&H0015CCFA&,&H000000FF&,"
        f"&H00000000&,&H90000000&,-1,1,0,0,100,100,0,0,1,4,2,5,20,20,20,1\n"
        f"Style: ViralCensor,{document.captions.font},{font_px},&H00FFFFFF&,&H000000FF&,"
        f"&H00000000&,&H00000000&,-1,0,0,0,100,100,0,0,3,18,0,5,20,20,20,1\n"
    )
    if "[V4+ Styles]" in content:
        content = content.replace("[Events]", style_line + "\n[Events]", 1)
    else:
        content = content.replace(
            "[Events]",
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, "
            "Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            + style_line
            + "\n[Events]",
            1,
        )
    dialogue_lines = []
    for sub in viral.subtexts:
        for out_s, out_e in map_source_span(document, sub.start, sub.end):
            px = round(sub.x * width)
            py = round(sub.y * height)
            safe_text = sub.text.replace("{", "(").replace("}", ")").replace("\n", "\\N")
            color_tag = _ass_bgr(sub.color)
            style_name = "ViralCensor" if sub.variant == "censor_bar" else "ViralSubtext"
            anim = "\\fscx85\\fscy85\\t(0,110,\\fscx110\\fscy110)\\t(110,200,\\fscx100\\fscy100)"
            dialogue_lines.append(
                f"Dialogue: 2,{_ass_time(out_s)},{_ass_time(out_e)},{style_name},,0,0,0,,"
                f"{{\\an5\\pos({px},{py})\\c{color_tag}{anim}}}{safe_text}"
            )
    if dialogue_lines:
        if not content.endswith("\n"):
            content += "\n"
        content += "\n".join(dialogue_lines) + "\n"
        path.write_text(content, encoding="utf-8")


def run_render(
    command: list[str],
    directory: Path,
    job_id: str,
    duration: float,
    *,
    should_cancel=None,
    on_progress=None,
):
    """Poll a cancellable child without buffering unbounded ffmpeg logs.

    ``should_cancel`` and ``on_progress`` belong to the job's owner, which
    keeps the job row. The files written here are this process's scratch.
    """
    progress_path = directory / f"progress-{job_id}.txt"
    log_path = directory / f"log-{job_id}.txt"
    with log_path.open("w+") as log:
        # -progress/-nostats are ffmpeg flags. A non-ffmpeg command (tests use
        # sleep) must be started unchanged or GNU sleep rejects the extra args
        # and the cancel path never runs.
        launched = list(command)
        if command and Path(command[0]).name == "ffmpeg":
            launched = command[:1] + ["-progress", str(progress_path), "-nostats"] + command[1:]
        process = subprocess.Popen(
            launched,
            stdout=subprocess.DEVNULL,
            stderr=log,
        )
        try:
            while process.poll() is None:
                if should_cancel is not None and should_cancel():
                    raise InterruptedError("Export cancelled")
                percent = 0
                if progress_path.exists():
                    for line in progress_path.read_text().splitlines():
                        if line.startswith("out_time_us="):
                            try:
                                percent = min(
                                    99,
                                    int(
                                        float(line.split("=", 1)[1])
                                        / 1_000_000
                                        / duration
                                        * 100
                                    ),
                                )
                            except ValueError:
                                pass
                if on_progress is not None:
                    on_progress(percent)
                time.sleep(0.25)
            if process.returncode:
                log.seek(0)
                raise RuntimeError("Video rendering failed: " + log.read()[-1500:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            progress_path.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)


def resolve_webcam_crop_rect(wb: WebcamRegion, sw: int, sh: int) -> tuple[int, int, int, int]:
    """Compute even-aligned webcam crop (x, y, w, h) honoring 0..1 position and zoom."""
    cam_zoom = max(0.5, min(2.5, float(wb.zoom or 1.0)))
    base_w = max(4.0, min(float(sw), wb.w * sw))
    base_h = max(4.0, min(float(sh), wb.h * sh))
    raw_x = max(0.0, min(float(sw) - base_w, wb.x * sw))
    raw_y = max(0.0, min(float(sh) - base_h, wb.y * sh))
    mid_x = raw_x + base_w / 2.0
    mid_y = raw_y + base_h / 2.0
    cw = max(4, min(sw, (round(base_w / cam_zoom) // 2) * 2 or 4))
    ch = max(4, min(sh, (round(base_h / cam_zoom) // 2) * 2 or 4))
    cx = max(0, min(sw - cw, (round(mid_x - cw / 2.0) // 2) * 2))
    cy = max(0, min(sh - ch, (round(mid_y - ch / 2.0) // 2) * 2))
    return cx, cy, cw, ch


def _build_viral_video_chain(
    document: EditDocument,
    sw: int,
    sh: int,
    width: int,
    height: int,
    geometry: str,
    effects: str,
) -> str:
    viral = document.viral
    if viral is None:
        return f"{geometry},{effects}"

    cx, cy, cw, ch = resolve_webcam_crop_rect(viral.webcamBox, sw, sh)

    if viral.layoutMode == "split" and sw > sh:
        if height > width:
            top_h = (round(height / 3) // 2) * 2
            bot_h = height - top_h
            base_geom = (
                f"split=2[vcam_in][vgame_in];"
                f"[vcam_in]crop={cw}:{ch}:{cx}:{cy},"
                f"scale={width}:{top_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{top_h},unsharp=5:5:0.45:5:5:0.0,setsar=1[vcam_top];"
                f"[vgame_in]scale={width}:{bot_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{bot_h},setsar=1[vgame_bot];"
                f"[vcam_top][vgame_bot]vstack=inputs=2,setsar=1"
            )
        elif height == width:
            top_h = (round(height * 0.38) // 2) * 2
            bot_h = height - top_h
            base_geom = (
                f"split=2[vcam_in][vgame_in];"
                f"[vcam_in]crop={cw}:{ch}:{cx}:{cy},"
                f"scale={width}:{top_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{top_h},unsharp=5:5:0.45:5:5:0.0,setsar=1[vcam_top];"
                f"[vgame_in]scale={width}:{bot_h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{bot_h},setsar=1[vgame_bot];"
                f"[vcam_top][vgame_bot]vstack=inputs=2,setsar=1"
            )
        else:
            left_w = (round(width * 0.34) // 2) * 2
            right_w = width - left_w
            base_geom = (
                f"split=2[vcam_in][vgame_in];"
                f"[vcam_in]crop={cw}:{ch}:{cx}:{cy},"
                f"scale={left_w}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={left_w}:{height},unsharp=5:5:0.45:5:5:0.0,setsar=1[vcam_left];"
                f"[vgame_in]scale={right_w}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={right_w}:{height},setsar=1[vgame_right];"
                f"[vcam_left][vgame_right]hstack=inputs=2,setsar=1"
            )
    elif viral.layoutMode == "fullscreen_cam" and sw > sh:
        base_geom = (
            f"crop={cw}:{ch}:{cx}:{cy},"
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},unsharp=5:5:0.45:5:5:0.0,setsar=1"
        )
    elif viral.layoutMode == "pip" and sw > sh:
        pip_w = max(120, (round(width * 0.30) // 2) * 2)
        pip_h = max(90, (round(pip_w * 0.78) // 2) * 2)
        pip_x = max(24, round(width * 0.04))
        pip_y_margin = max(24, round(height * 0.11))
        base_geom = (
            f"split=2[vgame_bg][vcam_pip];"
            f"[vgame_bg]{geometry}[vbg];"
            f"[vcam_pip]crop={cw}:{ch}:{cx}:{cy},"
            f"scale={pip_w}:{pip_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={pip_w}:{pip_h},unsharp=5:5:0.4:5:5:0.0[vpip];"
            f"[vbg][vpip]overlay=x={pip_x}:y=H-h-{pip_y_margin},setsar=1"
        )
    else:
        base_geom = geometry

    chain = f"{base_geom},{effects}"

    # Collect Black & White (Preto e Branco) intervals from Momento Fail & bw_dramatic keyframes
    bw_spans: list[tuple[float, float]] = []
    for te in viral.timeEffects:
        if te.kind == "freeze_bw":
            bw_spans.extend(map_source_span(document, te.start, te.end))
    for kf in viral.cameraKeyframes:
        if kf.mode == "bw_dramatic":
            bw_spans.extend(map_source_span(document, kf.start, kf.end))
    if bw_spans:
        expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in bw_spans[:24])
        chain += f",hue=s=0:enable='{expr}',eq=contrast=1.22:brightness=-0.03:enable='{expr}'"

    # Collect Screen Shake, Zoom Punch & Slow Zoom-in de Tensão intervals
    shake_spans: list[tuple[float, float]] = []
    zoom_terms: list[str] = []
    for kf in viral.cameraKeyframes[:24]:
        for s, e in map_source_span(document, kf.start, kf.end):
            z = max(1.05, min(2.0, float(kf.zoom)))
            if kf.mode == "shake":
                shake_spans.append((s, e))
                z_shake = max(1.14, z)
                zoom_terms.append(f"if(between(t,{s:.3f},{e:.3f}),{z_shake:.3f},1)")
            elif kf.mode == "slow_tension":
                span_dur = max(0.05, e - s)
                zoom_terms.append(
                    f"if(between(t,{s:.3f},{e:.3f}),1+{(z - 1.0):.3f}*(t-{s:.3f})/{span_dur:.3f},1)"
                )
            elif kf.mode == "punch":
                zoom_terms.append(f"if(between(t,{s:.3f},{e:.3f}),{z:.3f},1)")

    # Also apply subtle zoom during freeze_bw dramatic moments
    for te in viral.timeEffects[:12]:
        if te.kind == "freeze_bw":
            for s, e in map_source_span(document, te.start, te.end):
                zoom_terms.append(f"if(between(t,{s:.3f},{e:.3f}),1.12,1)")

    if zoom_terms or shake_spans:
        zoom_expr = "1"
        for term in zoom_terms[:16]:
            zoom_expr = f"max({zoom_expr},{term})"
        shake_expr_x = "0"
        shake_expr_y = "0"
        if shake_spans:
            sh_cond = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in shake_spans[:16])
            amp_x = max(4, round(width * 0.018))
            amp_y = max(4, round(height * 0.014))
            shake_expr_x = f"if({sh_cond},{amp_x}*sin(t*75),0)"
            shake_expr_y = f"if({sh_cond},{amp_y}*cos(t*65),0)"
        chain += (
            f",crop=w='iw/({zoom_expr})':h='ih/({zoom_expr})':"
            f"x='clip((iw-ow)/2+{shake_expr_x},0,iw-ow)':"
            f"y='clip((ih-oh)/2+{shake_expr_y},0,ih-oh)',"
            f"scale={width}:{height}:flags=lanczos,setsar=1"
        )

    return chain


def _build_viral_audio_chain(
    document: EditDocument,
    directory: Path,
    total_duration: float,
    start_input_index: int,
) -> tuple[str, list[str]]:
    from .media.viral_audio import ensure_music_file, ensure_sfx_file

    base_vol = 0 if document.muted else document.volume
    viral = document.viral
    if viral is None or document.muted:
        return f"[audio]volume={base_vol}[outa]", []

    voice_chain = f"[audio]volume={base_vol}"
    # Apply voice distortion effects (bass_boost / reverb) on selected windows
    bass_spans: list[tuple[float, float]] = []
    for ve in viral.voiceEffects:
        if ve.effect == "bass_boost":
            bass_spans.extend(map_source_span(document, ve.start, ve.end))
    if bass_spans:
        b_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in bass_spans[:16])
        voice_chain += f",bass=g=14:f=110:w=0.6:enable='{b_expr}'"

    extra_inputs: list[str] = []
    mix_labels: list[str] = ["[voice_main]"]
    sub_filters: list[str] = [f"{voice_chain}[voice_main]"]
    input_idx = start_input_index

    # Background music with auto-ducking
    if viral.music.trackId != "none" and viral.music.volume > 0.01:
        music_path = ensure_music_file(viral.music.trackId, str(directory.parent))
        if music_path and music_path.is_file():
            extra_inputs.extend(["-stream_loop", "-1", "-i", str(music_path)])
            m_vol = viral.music.volume
            # Boost music during freeze_bw / bw_dramatic spans, duck during speech
            bw_spans: list[tuple[float, float]] = []
            for te in viral.timeEffects:
                if te.kind == "freeze_bw":
                    bw_spans.extend(map_source_span(document, te.start, te.end))
            if viral.music.autoDuck and bw_spans:
                boost_expr = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in bw_spans[:12])
                vol_expr = f"if({boost_expr},{min(1.2, m_vol * 2.2):.3f},{m_vol * 0.55:.3f})"
                sub_filters.append(
                    f"[{input_idx}:a]atrim=start=0:end={total_duration:.3f},asetpts=PTS-STARTPTS,"
                    f"volume='{vol_expr}':eval=frame[bg_music]"
                )
            else:
                ducked = m_vol * 0.65 if viral.music.autoDuck else m_vol
                sub_filters.append(
                    f"[{input_idx}:a]atrim=start=0:end={total_duration:.3f},asetpts=PTS-STARTPTS,"
                    f"volume={ducked:.3f}[bg_music]"
                )
            mix_labels.append("[bg_music]")
            input_idx += 1

    # SFX placements mapped onto output timeline
    for sfx_i, sfx in enumerate(viral.sfx[:16]):
        if sfx.volume <= 0.01:
            continue
        spans = map_source_span(document, sfx.start, sfx.start + 0.1)
        if not spans:
            continue
        out_start = spans[0][0]
        if out_start >= total_duration:
            continue
        sfx_path = ensure_sfx_file(sfx.sfxId, str(directory.parent))
        if not sfx_path or not sfx_path.is_file():
            continue
        extra_inputs.extend(["-i", str(sfx_path)])
        delay_ms = max(0, int(round(out_start * 1000)))
        lbl = f"[sfx_{sfx_i}]"
        sub_filters.append(
            f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume={sfx.volume:.3f}{lbl}"
        )
        mix_labels.append(lbl)
        input_idx += 1

    if len(mix_labels) == 1:
        return f"{voice_chain}[outa]", []

    sub_filters.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0[outa]"
    )
    return ";".join(sub_filters), extra_inputs


def render_document(
    directory: Path,
    job_id: str,
    document: EditDocument,
    preset: str,
    broll_files: dict[str, Path] | None = None,
    *,
    should_cancel=None,
    on_progress=None,
):
    """Render a draft. ``broll_files`` maps footage keys to local files; B-roll
    whose footage is missing is left out rather than failing the export."""
    from .clip_editor import _ffprobe_size, _escape_filter_path
    from .media.broll_overlay import broll_input_args, broll_overlay_graph, normalize_broll_overlays
    from .video_utils import ass_fonts_dir

    # A split without a source-time jump is one continuous picture. Render it
    # as such so a split inside a dissolve cannot reset its opacity or discard
    # a sub-frame B-roll fragment. Keep the editable draft and its IDs intact.
    document = document.model_copy(update={"segments": continuous_segments(document.segments)})

    needs_raw_landscape = bool(
        document.viral and document.viral.layoutMode in ("split", "fullscreen_cam", "pip")
    )
    source = directory / (
        "layout.mp4"
        if not needs_raw_landscape
        and document.framing.fit == "auto"
        and document.framing.aspect == "vertical"
        and (directory / "layout.mp4").exists()
        else "clean.mp4"
    )
    sw, sh = _ffprobe_size(source)
    width, height = output_size(document, sw, sh)
    framing, fx = document.framing, document.effects
    scale = (max if framing.fit == "cover" else min)(
        width / sw, height / sh
    ) * framing.zoom
    rw, rh = max(2, round(sw * scale / 2) * 2), max(2, round(sh * scale / 2) * 2)
    # Crop overflow and position letterboxing using the same normalized anchor.
    cw, ch = min(width, rw), min(height, rh)
    geometry = f"scale={rw}:{rh}:flags=lanczos,crop={cw}:{ch}:{(rw - cw) * framing.x}:{(rh - ch) * framing.y},pad={width}:{height}:{(width - cw) * framing.x}:{(height - ch) * framing.y}:black,setsar=1"
    # CSS brightness/contrast use RGB multiplication followed by a midpoint shift.
    gain = fx.brightness / 100 * fx.contrast / 100
    offset = 128 * (1 - fx.contrast / 100)
    effects = f"lutrgb=r='clip(val*{gain}+{offset},0,255)':g='clip(val*{gain}+{offset},0,255)':b='clip(val*{gain}+{offset},0,255)',hue=h={fx.hue}:s={fx.saturation / 100}"
    if fx.blur:
        effects += f",gblur=sigma={fx.blur * width / 1080}"
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    audio = bool(probe.stdout.strip())
    filters, joins = [], []
    for index, segment in enumerate(document.segments):
        filters.append(
            f"[0:v]trim=start={segment.start}:end={segment.end},setpts=PTS-STARTPTS[v{index}]"
        )
        joins.append(f"[v{index}]")
        if audio:
            filters.append(
                f"[0:a]atrim=start={segment.start}:end={segment.end},asetpts=PTS-STARTPTS[a{index}]"
            )
            joins.append(f"[a{index}]")
    filters.append(
        "".join(joins)
        + f"concat=n={len(document.segments)}:v=1:a={int(audio)}[video]"
        + ("[audio]" if audio else "")
    )
    ass = directory / f"captions-{job_id}.ass"
    write_ass(ass, document, width, height)
    fonts = ass_fonts_dir(document.captions.font)
    subtitles = f"ass={_escape_filter_path(ass)}:fontsdir={_escape_filter_path(fonts or Path('fonts'))}"
    total = sum(s.end - s.start for s in document.segments)
    overlays = normalize_broll_overlays(
        [
            {**piece, "path": broll_files[piece["footage"].key]}
            for piece in mapped_broll(document)
            if broll_files and piece["footage"].key in broll_files
        ],
        total,
        min_duration=BROLL_MIN_SECONDS,
    )
    viral = document.viral
    viral_video_chain = _build_viral_video_chain(document, sw, sh, width, height, geometry, effects)
    if overlays:
        # Footage fills the output frame over the framed, graded picture and
        # under the captions, as in the preview.
        filters.append(f"[video]{viral_video_chain}[framed]")
        graph, label = broll_overlay_graph("[framed]", overlays, width, height)
        filters.append(graph)
        filters.append(f"{label}{subtitles}[outv]")
    else:
        filters.append(f"[video]{viral_video_chain},{subtitles}[outv]")
    # Mixed source/stock frame rates must not make ffmpeg duplicate tail frames.
    filters.append("[outv]fps=60[finalv]")
    extra_audio_inputs: list[str] = []
    if audio:
        audio_chain, extra_audio_inputs = _build_viral_audio_chain(
            document, directory, total, start_input_index=1 + len(overlays)
        )
        filters.append(audio_chain)
    output = directory / f"export-{job_id}.mp4"
    temporary = directory / f".export-{job_id}.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        *broll_input_args(overlays),
        *extra_audio_inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[finalv]",
    ]
    if audio:
        command += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "16",
        "-profile:v",
        "high",
        "-level",
        "4.2",
        "-r",
        "60",
        "-maxrate",
        "22M" if preset == "reels" else "20M",
        "-bufsize",
        "40M",
        "-x264-params",
        "keyint=120:min-keyint=30:scenecut=40",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-t",
        f"{total:.6f}",
        str(temporary),
    ]
    try:
        run_render(
            command,
            directory,
            job_id,
            sum(s.end - s.start for s in document.segments),
            should_cancel=should_cancel,
            on_progress=on_progress,
        )
        if should_cancel is not None and should_cancel():
            raise InterruptedError("Export cancelled")
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        ass.unlink(missing_ok=True)
