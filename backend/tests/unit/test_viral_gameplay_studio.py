import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.editor_document import (
    CaptionStyle,
    EditDocument,
    Framing,
    Segment,
    ViralEditConfig,
    render_document,
    write_ass,
)
from src.media.gameplay_signals import analyze_clip_gameplay_signals
from src.media.viral_audio import (
    VIRAL_MUSIC_CATALOG,
    VIRAL_SFX_CATALOG,
    ensure_music_file,
    ensure_sfx_file,
)
from src.services.viral_director_service import build_viral_tiktok_edit


def _make_test_clip(out_path: Path, duration: float = 4.0) -> None:
    """Create a synthetic 1280x720 gameplay clip with a clear audio spike at ~2s."""
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1280x720:rate=24:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            (
                f"aevalsrc='0.03*sin(2*PI*220*t)*lt(t,1.8)"
                f"+0.85*(random(0)*2-1)*gte(t,1.8)*lt(t,2.6)"
                f"+0.04*sin(2*PI*220*t)*gte(t,2.6)':s=44100:d={duration}"
            ),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


def test_viral_audio_synthesizes_all_sfx_and_music(tmp_path):
    for sfx_id in VIRAL_SFX_CATALOG:
        path = ensure_sfx_file(sfx_id, str(tmp_path))
        assert path is not None and path.is_file() and path.stat().st_size > 1000
    for music_id in VIRAL_MUSIC_CATALOG:
        path = ensure_music_file(music_id, str(tmp_path))
        assert path is not None and path.is_file() and path.stat().st_size > 4000


def test_edit_document_accepts_viral_layers_and_writes_subtexts_to_ass(tmp_path):
    doc = EditDocument.model_validate(
        {
            "segments": [{"id": "s1", "start": 0.0, "end": 4.0}],
            "words": [
                {"id": "w1", "start": 0.2, "end": 0.8, "text": "Confia", "highlight": True},
                {"id": "w2", "start": 0.9, "end": 1.6, "text": "em", "highlight": False},
                {"id": "w3", "start": 1.7, "end": 2.3, "text": "mim", "highlight": False},
            ],
            "viral": {
                "mode": "hybrid",
                "vibe": "fail",
                "layoutMode": "split",
                "webcamBox": {"x": 0.02, "y": 0.03, "w": 0.26, "h": 0.28},
                "timeEffects": [
                    {
                        "id": "te1",
                        "start": 2.0,
                        "end": 3.5,
                        "kind": "freeze_bw",
                        "freezeDuration": 1.5,
                        "speed": 1.0,
                        "reason": "Preset Momento Fail",
                    }
                ],
                "cameraKeyframes": [
                    {
                        "id": "kf1",
                        "start": 0.2,
                        "end": 0.9,
                        "mode": "punch",
                        "zoom": 1.25,
                        "intensity": 0.5,
                        "target": "center",
                        "reason": "Zoom-in Punch",
                    }
                ],
                "subtexts": [
                    {
                        "id": "st1",
                        "start": 0.5,
                        "end": 2.5,
                        "text": "Ele não sabia...",
                        "x": 0.5,
                        "y": 0.28,
                        "color": "#FACC15",
                        "variant": "thought",
                    }
                ],
                "sfx": [{"id": "sfx1", "sfxId": "sad_violin", "start": 2.0, "volume": 0.85}],
                "music": {"trackId": "sad_drama", "volume": 0.25, "autoDuck": True},
            },
        }
    ).validate_duration(4.0)

    ass_path = tmp_path / "captions.ass"
    write_ass(ass_path, doc, 1080, 1920)
    ass_text = ass_path.read_text(encoding="utf-8")
    assert "ViralSubtext" in ass_text
    assert "Ele não sabia..." in ass_text


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_viral_director_auto_mode_and_ffmpeg_render(tmp_path):
    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(clean),
        ],
        check=True,
        capture_output=True,
    )

    initial = EditDocument.model_validate(
        {
            "segments": [{"id": "s1", "start": 0.0, "end": 4.0}],
            "words": [
                {"id": "w1", "start": 0.2, "end": 0.8, "text": "confia", "highlight": False},
                {"id": "w2", "start": 2.2, "end": 2.9, "text": "morri", "highlight": True},
            ],
        }
    )

    signals = analyze_clip_gameplay_signals(clean, 4.0, [w.model_dump() for w in initial.words])
    assert "webcam_box" in signals and "fail_candidates" in signals

    updated, report = await build_viral_tiktok_edit(
        clean,
        initial,
        4.0,
        mode="auto",
        vibe="fail",
        split_cam=True,
        cold_open=False,
        explosion_cut=False,
        use_gemini=False,
    )
    assert updated.viral is not None
    assert updated.viral.layoutMode == "split"
    assert updated.viral.music.trackId == "sad_drama"
    assert len(updated.viral.timeEffects) >= 1
    assert report["fail_moments_count"] >= 1

    render_document(tmp_path, "f" * 32, updated, "tiktok")
    exported = tmp_path / f"export-{'f' * 32}.mp4"
    assert exported.is_file() and exported.stat().st_size > 1000
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-of",
                "json",
                str(exported),
            ],
            text=True,
        )
    )
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video_stream["width"], video_stream["height"]) == (1080, 1920)


def test_gameplay_mode_cache_key_isolated_from_normal_mode():
    from src.services.task_service import TaskService

    base = "abc123"
    normal_key = TaskService._build_analysis_cache_key(base, "auto", gameplay_mode=False)
    gameplay_key = TaskService._build_analysis_cache_key(base, "auto", gameplay_mode=True)
    assert normal_key != gameplay_key
    assert normal_key == base


def test_gameplay_signals_survive_window_filter(tmp_path):
    from src.ai import _filter_clip_signals_for_window
    from src.media.gameplay_signals import format_gameplay_signals_for_prompt

    vid = tmp_path / "gp_long.mp4"
    _make_test_clip(vid)
    signals = format_gameplay_signals_for_prompt(vid)
    assert "GAMEPLAY AUDIO & CHAOS RADAR" in signals
    filtered = _filter_clip_signals_for_window(signals, 0, 60)
    assert filtered is not None
    assert "GAMEPLAY CHAOS PEAK" in filtered


def test_split_layout_uses_clean_even_if_layout_exists(tmp_path):
    clean = tmp_path / "clean.mp4"
    _make_test_clip(clean)
    # Create a tiny portrait dummy layout.mp4 to verify split prefers clean.mp4
    layout_dummy = tmp_path / "layout.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=540x960:d=1:r=24",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(layout_dummy),
        ],
        check=True,
        capture_output=True,
    )
    doc = EditDocument(
        segments=[Segment(id="seg_1", start=0.0, end=3.0)],
        words=[],
        captions=CaptionStyle(enabled=False),
        framing=Framing(aspect="vertical", fit="auto", zoom=1.0, x=0.5, y=0.5),
        viral=ViralEditConfig(mode="auto", vibe="zoeira", layoutMode="split"),
    )
    render_document(tmp_path, "e" * 32, doc, "tiktok")
    exported = tmp_path / f"export-{'e' * 32}.mp4"
    assert exported.is_file()
    probe = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(exported)],
            text=True,
        )
    )
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video_stream["width"], video_stream["height"]) == (1080, 1920)
    # Duration must match clean.mp4 segment (~3s), NOT 1s dummy layout.mp4
    assert float(video_stream["duration"]) >= 2.5


async def test_ai_fallback_advances_past_503_on_fallback_model(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from pydantic_ai.exceptions import ModelHTTPError
    from src import ai

    monkeypatch.setenv("LLM_FALLBACK_MODELS", "google-gla:fb1,google-gla:fb2")
    monkeypatch.setenv("LLM_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("LLM_MAX_RPM", "0")
    ai._llm_cooldown_until.clear()

    agent = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                ModelHTTPError(429, "primary"),
                ModelHTTPError(503, "fb1"),
                "ok-from-fb2",
            ]
        )
    )
    res = await ai._run_transcript_analysis(agent, "prompt")
    assert res == "ok-from-fb2"
    assert agent.run.await_count == 3


def test_gameplay_split_webcam_position_and_render(tmp_path):
    from src.clip_cleanup import normalize_clip_cleanup_settings
    from src.media.gameplay_signals import detect_gameplay_webcam_box_smart
    from src.media.reframing import build_vertical_filter_plan
    from src.video_utils import create_optimized_clip

    cleanup = normalize_clip_cleanup_settings(
        gameplay_mode=True,
        webcam_position="top_right",
    )
    assert cleanup["gameplay_mode"] is True
    assert cleanup["webcam_position"] == "top_right"

    vid = tmp_path / "gp_corner.mp4"
    _make_test_clip(vid)
    wb = detect_gameplay_webcam_box_smart(vid, 4.0, hint="top_right", use_gemini=False)
    assert wb["x"] > 0.5 and wb["y"] < 0.2

    report = {}
    vf, mode, cap_y = build_vertical_filter_plan(
        vid,
        1280,
        720,
        report=report,
        gameplay_mode=True,
        gameplay_webcam_box=wb,
    )
    assert mode == "complex"
    assert report["kind"] == "gameplay_split"
    assert "vstack=inputs=2" in vf
    assert cap_y == pytest.approx(0.78)

    out_clip = tmp_path / "gameplay_split_out.mp4"
    cleanup["resolved_webcam_box"] = wb
    ok = create_optimized_clip(
        vid,
        0.0,
        3.0,
        out_clip,
        add_subtitles=False,
        output_format="vertical",
        keep_ranges=[(0.0, 3.0)],
        extend_to_sentence=False,
        cleanup_settings=cleanup,
    )
    assert ok and out_clip.is_file()
    probe = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(out_clip)],
            text=True,
        )
    )
    vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (vs["width"], vs["height"]) == (1080, 1920)


async def test_load_task_source_settings_preserves_gameplay_and_webcam():
    from unittest.mock import AsyncMock, MagicMock
    from src.config import get_config
    from src.services.clip_service import ClipEditingMixin

    class _Dummy(ClipEditingMixin):
        def __init__(self):
            self.db = AsyncMock()
            self.config = get_config()
            self.task_repo = MagicMock()
            self.task_repo.load_source_metadata = AsyncMock(
                return_value={
                    "output_format": "vertical",
                    "add_subtitles": True,
                    "clip_length": "auto",
                    "gameplay_mode": True,
                    "webcam_position": "top_right",
                    "resolved_webcam_box": {
                        "x": 0.83,
                        "y": 0.34,
                        "w": 0.16,
                        "h": 0.30,
                        "has_webcam": True,
                        "already_vertical": False,
                    },
                }
            )

    svc = _Dummy()
    loaded = await svc._load_task_source_settings("task-gp")
    assert loaded["gameplay_mode"] is True
    assert loaded["webcam_position"] == "top_right"
    assert loaded["resolved_webcam_box"]["x"] == pytest.approx(0.83)


def test_bake_editor_layout_keeps_landscape_clean_and_renders_gameplay_split_layout(tmp_path):
    from src.media.reframing import ffprobe_video_size
    from src.services.editor_service import (
        bake_editor_layout,
        needs_gameplay_landscape_restore,
        upgrade_document,
    )

    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(clean),
        ],
        check=True,
        capture_output=True,
    )
    wb = {
        "x": 0.8,
        "y": 0.25,
        "w": 0.18,
        "h": 0.30,
        "has_webcam": True,
        "already_vertical": False,
    }
    baked = bake_editor_layout(
        clean,
        "vertical",
        clean,
        [(0.0, 2.0)],
        preserve_content=True,
        gameplay_mode=True,
        gameplay_webcam_box=wb,
    )
    assert baked is True
    assert ffprobe_video_size(clean) == (1280, 720)
    layout = clean.with_name("layout.mp4")
    assert layout.is_file()
    assert ffprobe_video_size(layout) == (1080, 1920)

    upgraded = upgrade_document(
        {"framing": {"aspect": "vertical", "fit": "auto"}, "captions": {"template": "default", "karaoke": True, "uppercase": False}},
        {"hook_title": ""},
        {"gameplay_mode": True, "resolved_webcam_box": wb},
        [],
        2.0,
        source_width=1280,
        source_height=720,
    )
    assert upgraded["viral"]["layoutMode"] == "split"
    assert upgraded["viral"]["webcamBox"]["x"] == pytest.approx(0.8)
    assert upgraded["viral"]["cameraKeyframes"] == []
    assert upgraded["viral"]["music"]["trackId"] == "none"

    # Explicit "off" in viral is preserved and never overwritten on upgrade.
    kept_off = upgrade_document(
        {"framing": {"aspect": "vertical", "fit": "auto"}, "viral": {"layoutMode": "off"}},
        {"hook_title": ""},
        {"gameplay_mode": True, "resolved_webcam_box": wb},
        [],
        2.0,
        source_width=1280,
        source_height=720,
    )
    assert kept_off["viral"]["layoutMode"] == "off"

    assert needs_gameplay_landscape_restore(
        {"source_metadata": {"gameplay_mode": True, "resolved_webcam_box": wb, "output_format": "vertical"}},
        {"status": "ready", "width": 1080, "height": 1920},
    ) is True
    assert needs_gameplay_landscape_restore(
        {"source_metadata": {"gameplay_mode": True, "resolved_webcam_box": wb, "output_format": "vertical"}},
        {"status": "ready", "width": 1920, "height": 1080},
    ) is False


def test_webcam_region_full_range_and_zoom_crop():
    from src.editor_document import WebcamRegion, resolve_webcam_crop_rect

    # Full 0..1 range (0% to 100%) validates without error and never collapses to a thin sliver.
    edge = WebcamRegion(x=1.0, y=1.0, w=0.25, h=0.25, zoom=1.0)
    cx, cy, cw, ch = resolve_webcam_crop_rect(edge, 1920, 1080)
    assert cw == 480 and ch == 270
    assert cx + cw <= 1920 and cy + ch <= 1080

    # Zoom in shrinks the crop window around the webcam center; zoom out expands it.
    base = WebcamRegion(x=0.4, y=0.4, w=0.2, h=0.2, zoom=1.0)
    zoomed_in = WebcamRegion(x=0.4, y=0.4, w=0.2, h=0.2, zoom=2.0)
    zoomed_out = WebcamRegion(x=0.4, y=0.4, w=0.2, h=0.2, zoom=0.5)
    _, _, bw, bh = resolve_webcam_crop_rect(base, 1920, 1080)
    _, _, iw, ih = resolve_webcam_crop_rect(zoomed_in, 1920, 1080)
    _, _, ow, oh = resolve_webcam_crop_rect(zoomed_out, 1920, 1080)
    assert iw < bw < ow
    assert ih < bh < oh


def test_tiktok_export_renders_1080p_60fps(tmp_path):
    from src.editor_document import EditDocument, render_document

    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(clean),
        ],
        check=True,
        capture_output=True,
    )
    doc = EditDocument.model_validate(
        {
            "segments": [{"id": "s", "start": 0, "end": 2}],
            "framing": {"aspect": "vertical", "fit": "cover"},
            "captions": {"enabled": False},
            "viral": {
                "mode": "auto",
                "vibe": "zoeira",
                "layoutMode": "split",
                "webcamBox": {"x": 0.7, "y": 0.05, "w": 0.25, "h": 0.3, "zoom": 1.0},
            },
        }
    )
    render_document(tmp_path, "tiktok60", doc, "tiktok")
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                "-of", "json", str(tmp_path / "export-tiktok60.mp4"),
            ],
            text=True,
        )
    )["streams"][0]
    assert (probe["width"], probe["height"]) == (1080, 1920)
    assert probe["r_frame_rate"] == "60/1"
    assert probe["avg_frame_rate"] == "60/1"


async def test_extend_clip_uses_source_and_expands_duration(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from src.clip_source_map import load_clip_source_ranges, save_clip_source_ranges
    from src.config import get_config
    from src.services.clip_service import ClipEditingMixin
    from src.services.video_service import VideoService

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    src_video = uploads_dir / "full_source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=12",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=12",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k",
            str(src_video),
        ],
        check=True,
        capture_output=True,
    )

    initial_clip = tmp_path / "initial.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "3", "-t", "4", "-i", str(src_video), "-c", "copy", str(initial_clip)],
        check=True,
        capture_output=True,
    )
    save_clip_source_ranges(initial_clip, [(3.0, 7.0)], [0.0])

    clip_row = {
        "id": "clip-1",
        "task_id": "task-1",
        "filename": initial_clip.name,
        "file_path": str(initial_clip),
        "start_time": "00:03",
        "end_time": "00:07",
        "duration": 4.0,
        "text": "initial cut",
        "relevance_score": 0.9,
        "reasoning": "test",
        "virality_score": 90,
        "hook_score": 85,
        "engagement_score": 88,
        "value_score": 80,
        "shareability_score": 82,
    }
    task_row = {
        "id": "task-1",
        "user_id": "user-1",
        "source_url": "upload://full_source.mp4",
        "source_type": "video_url",
        "source_duration_seconds": 12,
        "font_family": None,
        "font_size": 28,
        "font_color": None,
        "caption_template": "default",
    }

    class _DummyService(ClipEditingMixin):
        def __init__(self):
            self.db = AsyncMock()
            self.config = get_config()
            self.video_service = VideoService()
            self.clip_repo = MagicMock()
            self.task_repo = MagicMock()
            self.updated_payload = None

            async def _get_clip(_db, _cid):
                return self.updated_payload or clip_row

            async def _update_clip(_db, cid, fn, fp, st, en, dur, txt, **_kw):
                self.updated_payload = {
                    **clip_row,
                    "filename": fn,
                    "file_path": fp,
                    "start_time": st,
                    "end_time": en,
                    "duration": dur,
                    "text": txt,
                }

            self.clip_repo.get_clip_by_id = AsyncMock(side_effect=_get_clip)
            self.clip_repo.update_clip = AsyncMock(side_effect=_update_clip)
            self.task_repo.get_task_by_id = AsyncMock(return_value=task_row)
            self.task_repo.load_source_metadata = AsyncMock(
                return_value={
                    "output_format": "vertical",
                    "add_subtitles": False,
                    "gameplay_mode": True,
                    "webcam_position": "top_left",
                }
            )

        async def _media_limits(self, _uid):
            return None, None

        async def _load_task_source_settings(self, tid):
            return await self.task_repo.load_source_metadata(self.db, tid)

        @staticmethod
        def _get_clip_source_ranges(c):
            return load_clip_source_ranges(Path(c["file_path"])) or [(3.0, 7.0)]

        @staticmethod
        def _seconds_to_mmss(sec):
            s = max(0, int(round(sec)))
            return f"{s // 60:02d}:{s % 60:02d}"

        @staticmethod
        def _cleanup_source_video(*_a, **_kw):
            return None

    from src.services import video_service as vs_mod

    svc = _DummyService()
    monkeypatch.setattr(svc.config, "temp_dir", str(tmp_path))
    monkeypatch.setattr(vs_mod, "get_config", lambda: svc.config)
    res = await svc.extend_clip.__wrapped__(svc, "task-1", "clip-1", before_seconds=2.0, after_seconds=3.0)

    assert res["applied_before"] == pytest.approx(2.0)
    assert res["applied_after"] == pytest.approx(3.0)
    ext_clip = res["clip"]
    assert ext_clip["start_time"] == "00:01"
    assert ext_clip["end_time"] == "00:10"
    assert ext_clip["duration"] >= 8.5
    new_ranges = load_clip_source_ranges(Path(ext_clip["file_path"]))
    assert new_ranges == [(1.0, 10.0)]
    probe = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", ext_clip["file_path"]],
            text=True,
        )
    )
    vs = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (vs["width"], vs["height"]) == (1080, 1920)


async def test_load_task_source_settings_preserves_gameplay_and_webcam():
    from unittest.mock import AsyncMock, MagicMock
    from src.config import get_config
    from src.services.clip_service import ClipEditingMixin

    class _Dummy(ClipEditingMixin):
        def __init__(self):
            self.db = AsyncMock()
            self.config = get_config()
            self.task_repo = MagicMock()
            self.task_repo.load_source_metadata = AsyncMock(
                return_value={
                    "output_format": "vertical",
                    "add_subtitles": True,
                    "clip_length": "auto",
                    "gameplay_mode": True,
                    "webcam_position": "top_right",
                    "resolved_webcam_box": {
                        "x": 0.83,
                        "y": 0.34,
                        "w": 0.16,
                        "h": 0.30,
                        "has_webcam": True,
                        "already_vertical": False,
                    },
                }
            )

    svc = _Dummy()
    loaded = await svc._load_task_source_settings("task-gp")
    assert loaded["gameplay_mode"] is True
    assert loaded["webcam_position"] == "top_right"
    assert loaded["resolved_webcam_box"]["x"] == pytest.approx(0.83)


def test_bake_editor_layout_keeps_landscape_clean_and_renders_gameplay_split_layout(tmp_path):
    from src.media.reframing import ffprobe_video_size
    from src.services.editor_service import (
        bake_editor_layout,
        needs_gameplay_landscape_restore,
        upgrade_document,
    )

    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(clean),
        ],
        check=True,
        capture_output=True,
    )
    wb = {
        "x": 0.8,
        "y": 0.25,
        "w": 0.18,
        "h": 0.30,
        "has_webcam": True,
        "already_vertical": False,
    }
    baked = bake_editor_layout(
        clean,
        "vertical",
        clean,
        [(0.0, 2.0)],
        preserve_content=True,
        gameplay_mode=True,
        gameplay_webcam_box=wb,
    )
    assert baked is True
    assert ffprobe_video_size(clean) == (1280, 720)
    layout = clean.with_name("layout.mp4")
    assert layout.is_file()
    assert ffprobe_video_size(layout) == (1080, 1920)

    upgraded = upgrade_document(
        {"framing": {"aspect": "vertical", "fit": "auto"}, "captions": {"template": "default", "karaoke": True, "uppercase": False}},
        {"hook_title": ""},
        {"gameplay_mode": True, "resolved_webcam_box": wb},
        [],
        2.0,
        source_width=1280,
        source_height=720,
    )
    assert upgraded["viral"]["layoutMode"] == "split"
    assert upgraded["viral"]["webcamBox"]["x"] == pytest.approx(0.8)
    assert upgraded["viral"]["cameraKeyframes"] == []
    assert upgraded["viral"]["music"]["trackId"] == "none"

    # Explicit "off" in viral is preserved and never overwritten on upgrade.
    kept_off = upgrade_document(
        {"framing": {"aspect": "vertical", "fit": "auto"}, "viral": {"layoutMode": "off"}},
        {"hook_title": ""},
        {"gameplay_mode": True, "resolved_webcam_box": wb},
        [],
        2.0,
        source_width=1280,
        source_height=720,
    )
    assert kept_off["viral"]["layoutMode"] == "off"

    assert needs_gameplay_landscape_restore(
        {"source_metadata": {"gameplay_mode": True, "resolved_webcam_box": wb, "output_format": "vertical"}},
        {"status": "ready", "width": 1080, "height": 1920},
    ) is True
    assert needs_gameplay_landscape_restore(
        {"source_metadata": {"gameplay_mode": True, "resolved_webcam_box": wb, "output_format": "vertical"}},
        {"status": "ready", "width": 1920, "height": 1080},
    ) is False


def test_webcam_region_full_range_and_zoom_crop():
    from src.editor_document import WebcamRegion, resolve_webcam_crop_rect

    # Full 0..1 range (0% to 100%) validates without error and never collapses to a thin sliver.
    edge = WebcamRegion(x=1.0, y=1.0, w=0.25, h=0.25, zoom=1.0)
    cx, cy, cw, ch = resolve_webcam_crop_rect(edge, 1920, 1080)
    assert cw == 480 and ch == 270
    assert cx + cw <= 1920 and cy + ch <= 1080

    # Zoom in shrinks the crop window around the webcam center; zoom out expands it.
    base = WebcamRegion(x=0.4, y=0.4, w=0.2, h=0.2, zoom=1.0)
    zoomed_in = WebcamRegion(x=0.4, y=0.4, w=0.2, h=0.2, zoom=2.0)
    zoomed_out = WebcamRegion(x=0.4, y=0.4, w=0.2, h=0.2, zoom=0.5)
    _, _, bw, bh = resolve_webcam_crop_rect(base, 1920, 1080)
    _, _, iw, ih = resolve_webcam_crop_rect(zoomed_in, 1920, 1080)
    _, _, ow, oh = resolve_webcam_crop_rect(zoomed_out, 1920, 1080)
    assert iw < bw < ow
    assert ih < bh < oh


def test_tiktok_export_renders_1080p_60fps(tmp_path):
    from src.editor_document import EditDocument, render_document

    clean = tmp_path / "clean.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(clean),
        ],
        check=True,
        capture_output=True,
    )
    doc = EditDocument.model_validate(
        {
            "segments": [{"id": "s", "start": 0, "end": 2}],
            "framing": {"aspect": "vertical", "fit": "cover"},
            "captions": {"enabled": False},
            "viral": {
                "mode": "auto",
                "vibe": "zoeira",
                "layoutMode": "split",
                "webcamBox": {"x": 0.7, "y": 0.05, "w": 0.25, "h": 0.3, "zoom": 1.0},
            },
        }
    )
    render_document(tmp_path, "tiktok60", doc, "tiktok")
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
                "-of", "json", str(tmp_path / "export-tiktok60.mp4"),
            ],
            text=True,
        )
    )["streams"][0]
    assert (probe["width"], probe["height"]) == (1080, 1920)
    assert probe["r_frame_rate"] == "60/1"
    assert probe["avg_frame_rate"] == "60/1"


@pytest.mark.parametrize(
    "aspect,layout,expected_size",
    [
        ("vertical", "split", (1080, 1920)),
        ("square", "split", (1080, 1080)),
        ("original", "split", (1280, 720)),
        ("square", "pip", (1080, 1080)),
        ("original", "pip", (1280, 720)),
        ("original", "fullscreen_cam", (1280, 720)),
        ("vertical", "off", (1080, 1920)),
    ],
)
def test_render_all_viral_effects_across_all_formats(tmp_path, aspect, layout, expected_size):
    clean = tmp_path / "clean.mp4"
    _make_test_clip(clean, duration=2.5)
    doc = EditDocument.model_validate(
        {
            "segments": [{"id": "s1", "start": 0.0, "end": 2.5}],
            "words": [
                {"id": "w1", "start": 0.2, "end": 0.9, "text": "Olha", "highlight": True},
                {"id": "w2", "start": 1.0, "end": 1.8, "text": "isso", "highlight": False},
            ],
            "framing": {"aspect": aspect, "fit": "cover"},
            "captions": {"enabled": True},
            "viral": {
                "mode": "hybrid",
                "vibe": "fail",
                "layoutMode": layout,
                "webcamBox": {"x": 0.05, "y": 0.05, "w": 0.25, "h": 0.28, "zoom": 1.2},
                "timeEffects": [
                    {
                        "id": "te1",
                        "start": 1.4,
                        "end": 2.2,
                        "kind": "freeze_bw",
                        "freezeDuration": 0.8,
                        "speed": 1.0,
                        "reason": "Momento Fail",
                    }
                ],
                "cameraKeyframes": [
                    {"id": "kf1", "start": 0.1, "end": 0.5, "mode": "punch", "zoom": 1.25, "intensity": 0.5, "target": "center"},
                    {"id": "kf2", "start": 0.6, "end": 1.1, "mode": "slow_tension", "zoom": 1.2, "intensity": 0.5, "target": "center"},
                    {"id": "kf3", "start": 1.1, "end": 1.4, "mode": "shake", "zoom": 1.2, "intensity": 0.8, "target": "center"},
                    {"id": "kf4", "start": 2.2, "end": 2.5, "mode": "bw_dramatic", "zoom": 1.0, "intensity": 0.5, "target": "center"},
                ],
                "subtexts": [
                    {
                        "id": "st1",
                        "start": 1.4,
                        "end": 2.2,
                        "text": "Ele não sabia...",
                        "x": 0.5,
                        "y": 0.28,
                        "color": "#FACC15",
                        "variant": "thought",
                    }
                ],
                "sfx": [{"id": "sfx1", "sfxId": "sad_violin", "start": 1.4, "volume": 0.85}],
                "music": {"trackId": "sad_drama", "volume": 0.3, "autoDuck": True},
                "voiceEffects": [{"id": "ve1", "start": 1.0, "end": 1.4, "effect": "bass_boost"}],
            },
        }
    )
    job_id = f"fmt-{aspect}-{layout}"
    render_document(tmp_path, job_id, doc, "tiktok" if aspect == "vertical" else "reels")
    out_file = tmp_path / f"export-{job_id}.mp4"
    assert out_file.is_file() and out_file.stat().st_size > 10_000
    probe = json.loads(
        subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type,width,height,r_frame_rate",
                "-of", "json", str(out_file),
            ],
            text=True,
        )
    )["streams"]
    video_stream = next(s for s in probe if s["codec_type"] == "video")
    audio_stream = next(s for s in probe if s["codec_type"] == "audio")
    assert (video_stream["width"], video_stream["height"]) == expected_size
    assert video_stream["r_frame_rate"] == "60/1"
    assert audio_stream is not None
