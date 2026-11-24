"""Multimodal gameplay signal scanner (Local Radar + Keyframe extraction).

Scans audio energy (dB windows, sudden onset spikes for screams/explosions),
visual motion & luminance flashes, dead-air vs. silent action, and facecam
regions so both the Gameplay cut selector and the TikTok Viral Director (Gemini)
have frame-accurate event markers.
"""

from __future__ import annotations

import base64
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_AUDIO_RATE = 8000
_WINDOW_SECONDS = 0.05


def _probe_duration(video_path: Path) -> float:
    if not video_path.is_file():
        return 0.0
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return 0.0
    return max(0.0, float(res.stdout.strip()))


def _probe_size(video_path: Path) -> tuple[int, int]:
    if not video_path.is_file():
        return 1920, 1080
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if res.returncode == 0 and "x" in res.stdout:
        parts = res.stdout.strip().splitlines()[0].split("x")
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    return 1920, 1080


def _levels_db(video_path: Path, start: float, end: float) -> np.ndarray:
    if not video_path.is_file():
        return np.zeros(0, dtype=np.float32)
    res = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{max(0.05, end - start):.3f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(_AUDIO_RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        timeout=max(180, int(max(0.0, end - start) / 25) + 60),
    )
    if res.returncode != 0 or not res.stdout:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(res.stdout, dtype=np.float32)
    size = int(_AUDIO_RATE * _WINDOW_SECONDS)
    count = len(samples) // size
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    blocks = samples[: count * size].reshape(count, size).astype(np.float64)
    return (20.0 * np.log10(np.sqrt((blocks**2).mean(axis=1)) + 1e-6)).astype(np.float32)


def _motion_score(video_path: Path, start: float, end: float) -> float:
    if not video_path.is_file():
        return 0.0
    span = min(8.0, max(0.25, end - start))
    res = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{span:.3f}",
            "-an",
            "-vf",
            "fps=4,scale=64:36:flags=fast_bilinear,format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        timeout=30,
    )
    frame_bytes = 64 * 36
    if res.returncode != 0 or len(res.stdout) < frame_bytes * 2:
        return 0.0
    count = len(res.stdout) // frame_bytes
    frames = np.frombuffer(res.stdout[: count * frame_bytes], dtype=np.uint8).reshape(count, 36, 64).astype(np.int16)
    diffs = np.abs(frames[1:] - frames[:-1]).mean(axis=(1, 2))
    return float(np.median(diffs))
_CONFIDENT_TRIGGER_WORDS = frozenset({
    "confia", "facil", "fácil", "olha", "certeza", "tranquilo", "ganhei",
    "ganhamo", "ganhamos", "impossivel", "impossível", "nunca", "safe",
    "watch", "easy", "trust", "clutch", "relaxa", "vem", "mata", "morreu",
})


def _norm_token(word: str) -> str:
    return "".join(ch for ch in (word or "").lower() if ch.isalnum())


def scan_audio_chaos(
    video_path: Path,
    start: float = 0.0,
    end: Optional[float] = None,
    max_peaks: int = 12,
) -> Dict[str, Any]:
    """Return audio energy timeline, sudden dB spikes (screams/explosions), and tension windows."""
    try:
        duration = _probe_duration(video_path)
    except Exception:
        duration = 0.0
    clip_end = min(duration, end) if end is not None and duration > 0 else (end or duration)
    if clip_end <= start + 0.15:
        return {"median_db": -35.0, "peaks": [], "tension_windows": []}

    try:
        levels = _levels_db(video_path, start, clip_end)
    except Exception as exc:
        logger.warning("Audio chaos scan failed on %s: %s", video_path, exc)
        return {"median_db": -35.0, "peaks": [], "tension_windows": []}

    if levels.size == 0:
        return {"median_db": -35.0, "peaks": [], "tension_windows": []}

    active = levels[levels > -48.0]
    median_db = float(np.median(active)) if active.size else float(np.median(levels))
    p90_db = float(np.percentile(levels, 90)) if levels.size >= 4 else median_db + 6.0
    threshold_db = max(median_db + 5.5, p90_db - 1.5, -28.0)

    peaks: List[Dict[str, Any]] = []
    min_gap_windows = int(1.2 / _WINDOW_SECONDS)
    last_idx = -min_gap_windows * 2

    for idx in range(1, len(levels)):
        db = float(levels[idx])
        if db < threshold_db:
            continue
        prev_slice = levels[max(0, idx - 8):idx]
        prev_db = float(np.median(prev_slice)) if prev_slice.size else median_db
        onset_jump = db - prev_db
        prominence = db - median_db
        if prominence < 5.0 and onset_jump < 6.5:
            continue

        if idx - last_idx < min_gap_windows and peaks:
            if db > peaks[-1]["peak_db"]:
                t_sec = round(start + idx * _WINDOW_SECONDS, 2)
                peaks[-1].update({
                    "time": t_sec,
                    "peak_db": round(db, 1),
                    "prominence_db": round(prominence, 1),
                    "onset_db": round(onset_jump, 1),
                    "kind": "explosion_or_scream" if (onset_jump >= 9.0 or prominence >= 9.5) else "laugh_or_shout",
                })
                last_idx = idx
            continue

        t_sec = round(start + idx * _WINDOW_SECONDS, 2)
        kind = "explosion_or_scream" if (onset_jump >= 9.0 or prominence >= 9.5) else "laugh_or_shout"
        peaks.append({
            "time": t_sec,
            "peak_db": round(db, 1),
            "prominence_db": round(prominence, 1),
            "onset_db": round(onset_jump, 1),
            "kind": kind,
        })
        last_idx = idx

    # Detect tension windows: 1.5s-4.0s before a strong peak where audio was noticeably quieter
    tension_windows: List[Dict[str, Any]] = []
    for peak in peaks:
        p_time = float(peak["time"])
        win_start = max(start, p_time - 3.2)
        win_end = max(start, p_time - 0.15)
        if win_end - win_start < 1.2:
            continue
        i0 = max(0, int((win_start - start) / _WINDOW_SECONDS))
        i1 = min(len(levels), int((win_end - start) / _WINDOW_SECONDS))
        if i1 <= i0:
            continue
        pre_median = float(np.median(levels[i0:i1]))
        if peak["peak_db"] - pre_median >= 8.0:
            tension_windows.append({
                "start": round(win_start, 2),
                "end": round(win_end, 2),
                "peak_time": p_time,
                "contrast_db": round(float(peak["peak_db"]) - pre_median, 1),
            })

    peaks.sort(key=lambda item: (item["prominence_db"] + item["onset_db"] * 0.5), reverse=True)
    top_peaks = sorted(peaks[:max(1, max_peaks)], key=lambda item: item["time"])
    return {
        "median_db": round(median_db, 1),
        "peaks": top_peaks,
        "tension_windows": tension_windows[:6],
    }


def detect_gameplay_webcam_box(video_path: Path, duration: float) -> Dict[str, Any]:
    """Detect or estimate a corner webcam region (normalized 0..1 coordinates).

    Tries `reaction_layout` face + inset edge detection first; if the player
    moved or covered their face, provides a clean corner webcam preset so
    the 'Split Cam + Gameplay' button always works.
    """
    try:
        width, height = _probe_size(video_path)
    except Exception:
        width, height = 1920, 1080

    if width <= height:
        # Already vertical: top third is reactor/cam, bottom two-thirds is game
        return {
            "detected": True,
            "already_vertical": True,
            "x": 0.0,
            "y": 0.0,
            "w": 1.0,
            "h": round(640 / 1920, 4),
        }

    if not video_path.is_file():
        return {
            "detected": False,
            "already_vertical": False,
            "x": 0.02,
            "y": 0.03,
            "w": 0.26,
            "h": 0.28,
        }

    try:
        from .face_detection import detect_faces_at_timestamps
        from .reaction_layout import find_reactor_face, measure_reaction_inset

        step = max(0.5, min(2.0, duration / 12.0)) if duration > 0 else 1.0
        times = [round(i * step, 2) for i in range(int(max(8, min(16, duration / step))))]
        samples = detect_faces_at_timestamps(video_path, times)
        face = find_reactor_face(width, height, samples)
        if face is not None:
            inset = measure_reaction_inset(video_path, width, height, face, times[:8])
            if inset is not None:
                ix, iy, iw, ih = inset
                return {
                    "detected": True,
                    "already_vertical": False,
                    "x": round(ix / width, 4),
                    "y": round(iy / height, 4),
                    "w": round(iw / width, 4),
                    "h": round(ih / height, 4),
                }
            fx, fy, fw, fh = face
            cam_w = min(width * 0.32, max(fw * 3.0, width * 0.22))
            cam_h = min(height * 0.34, max(fh * 2.6, height * 0.22))
            cam_x = max(0.0, min(width - cam_w, (fx + fw / 2) - cam_w / 2))
            cam_y = max(0.0, min(height - cam_h, (fy + fh / 2) - cam_h / 2))
            return {
                "detected": True,
                "already_vertical": False,
                "x": round(cam_x / width, 4),
                "y": round(cam_y / height, 4),
                "w": round(cam_w / width, 4),
                "h": round(cam_h / height, 4),
            }
    except Exception as exc:
        logger.debug("Automatic corner webcam check fell back to default preset: %s", exc)

    # Default top-left/right webcam box standard in OBS gameplay recordings (26%x28%)
    return {
        "detected": False,
        "already_vertical": False,
        "has_webcam": True,
        "method": "fallback",
        "x": 0.02,
        "y": 0.03,
        "w": 0.26,
        "h": 0.28,
    }


_CORNER_WEBCAM_PRESETS: Dict[str, tuple[float, float, float, float]] = {
    "top_left": (0.015, 0.025, 0.27, 0.32),
    "top_right": (0.715, 0.025, 0.27, 0.32),
    "bottom_left": (0.015, 0.64, 0.27, 0.32),
    "bottom_right": (0.715, 0.64, 0.27, 0.32),
}


def _gemini_detect_webcam_box(video_path: Path, duration: float) -> Optional[Dict[str, Any]]:
    """Ask Gemini Vision once per video where the player's facecam overlay sits."""
    try:
        from ..config import get_config
        import httpx

        cfg = get_config()
        api_key = (cfg.google_api_key or "").strip()
        if not api_key:
            return None
        sample_times = [round(max(0.2, duration * f), 2) for f in (0.25, 0.68)] if duration > 1.5 else [0.2]
        frames = extract_peak_keyframes_b64(video_path, sample_times, max_frames=2)
        if not frames:
            return None

        parts: List[Dict[str, Any]] = [
            {
                "text": (
                    "Identify if this gameplay video has a player facecam/webcam overlay (a box or circle showing a streamer's face). "
                    "Return ONLY valid JSON: "
                    '{"has_webcam": true_or_false, "x": float_0_to_1, "y": float_0_to_1, "w": float_0_to_1, "h": float_0_to_1}. '
                    "x,y is the top-left of the webcam box and w,h are normalized width/height (e.g. w=0.25, h=0.30). "
                    "If there is NO facecam anywhere, set has_webcam=false."
                )
            }
        ]
        for fr in frames:
            parts.append({"inline_data": {"mime_type": fr.get("mime_type", "image/jpeg"), "data": fr["data_b64"]}})

        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        with httpx.Client(timeout=15.0) as client:
            for model_name in (
                "gemini-3-flash-preview",
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite-preview",
                "gemini-flash-latest",
            ):
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                try:
                    resp = client.post(url, json=payload)
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200:
                    continue
                raw = (
                    resp.json()
                    .get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                ).strip()
                if not raw:
                    continue
                parsed = json.loads(raw)
                has_cam = bool(parsed.get("has_webcam", True))
                if not has_cam:
                    return {
                        "detected": True,
                        "already_vertical": False,
                        "has_webcam": False,
                        "method": "gemini_vision",
                        "x": 0.02,
                        "y": 0.03,
                        "w": 0.26,
                        "h": 0.28,
                    }
                x = max(0.0, min(0.88, float(parsed.get("x", 0.02))))
                y = max(0.0, min(0.88, float(parsed.get("y", 0.03))))
                w = max(0.10, min(1.0 - x, float(parsed.get("w", 0.26))))
                h = max(0.12, min(1.0 - y, float(parsed.get("h", 0.28))))
                return {
                    "detected": True,
                    "already_vertical": False,
                    "has_webcam": True,
                    "method": "gemini_vision",
                    "x": round(x, 4),
                    "y": round(y, 4),
                    "w": round(w, 4),
                    "h": round(h, 4),
                }
    except Exception as exc:
        logger.debug("Gemini webcam locator skipped: %s", exc)
    return None


def detect_gameplay_webcam_box_smart(
    video_path: Path,
    duration: float,
    hint: str = "auto",
    *,
    use_gemini: bool = True,
) -> Dict[str, Any]:
    """Locate the facecam region once per video with corner hint + Gemini fallback."""
    clean_hint = (hint or "auto").strip().lower()
    try:
        width, height = _probe_size(video_path)
    except Exception:
        width, height = 1920, 1080

    if width <= height:
        return {
            "detected": True,
            "already_vertical": True,
            "has_webcam": True,
            "method": "already_vertical",
            "x": 0.0,
            "y": 0.0,
            "w": 1.0,
            "h": round(640 / 1920, 4),
        }

    local = detect_gameplay_webcam_box(video_path, duration)
    if clean_hint in _CORNER_WEBCAM_PRESETS:
        px, py, pw, ph = _CORNER_WEBCAM_PRESETS[clean_hint]
        # If local face detector landed in the same quadrant, keep its tighter crop.
        if local.get("detected"):
            cx = float(local["x"]) + float(local["w"]) / 2
            cy = float(local["y"]) + float(local["h"]) / 2
            in_left = cx < 0.5
            in_top = cy < 0.5
            if (
                (("left" in clean_hint) == in_left)
                and (("top" in clean_hint) == in_top)
            ):
                return {**local, "has_webcam": True, "method": f"hint:{clean_hint}:refined"}
        return {
            "detected": True,
            "already_vertical": False,
            "has_webcam": True,
            "method": f"hint:{clean_hint}",
            "x": px,
            "y": py,
            "w": pw,
            "h": ph,
        }

    if local.get("detected"):
        return {**local, "has_webcam": True, "method": "local_face"}

    if use_gemini and video_path.is_file():
        gem = _gemini_detect_webcam_box(video_path, duration)
        if gem is not None:
            return gem

    return local


def extract_peak_keyframes_b64(
    video_path: Path,
    timestamps: Sequence[float],
    max_frames: int = 6,
) -> List[Dict[str, Any]]:
    """Extract small 384px-wide JPEG keyframes at chaos timestamps for Gemini Vision."""
    frames: List[Dict[str, Any]] = []
    for ts in list(timestamps)[:max_frames]:
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    f"{max(0.0, float(ts)):.2f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=384:-2",
                    "-q:v",
                    "6",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "mjpeg",
                    "-",
                ],
                capture_output=True,
                timeout=15,
            )
            if proc.returncode == 0 and proc.stdout:
                frames.append({
                    "time": round(float(ts), 2),
                    "mime_type": "image/jpeg",
                    "data_b64": base64.b64encode(proc.stdout).decode("ascii"),
                })
        except Exception as exc:
            logger.debug("Keyframe extraction at %.2fs skipped: %s", ts, exc)
    return frames


def analyze_clip_gameplay_signals(
    video_path: Path,
    duration: float,
    words: Optional[Sequence[Dict[str, Any]]] = None,
    include_keyframes: bool = False,
) -> Dict[str, Any]:
    """Comprehensive gameplay & viral edit radar for a clip."""
    chaos = scan_audio_chaos(video_path, 0.0, duration)
    peaks = chaos["peaks"]
    tension_windows = chaos["tension_windows"]

    word_list = sorted(
        [
            {
                "text": str(w.get("text", "")),
                "start": float(w.get("start", 0.0)),
                "end": float(w.get("end", 0.0)),
                "speaker": w.get("speaker"),
            }
            for w in (words or [])
        ],
        key=lambda item: item["start"],
    )

    # Scan gaps between words for:
    # 1) Speed-ramp candidates (silent movement >= 2.2s)
    # 2) Aggressive J-cut/L-cut dead-air trims (> 0.35s quiet & low motion)
    speed_ramp_candidates: List[Dict[str, Any]] = []
    dead_air_gaps: List[Dict[str, Any]] = []

    if word_list:
        gaps = []
        if word_list[0]["start"] > 0.4:
            gaps.append((0.0, word_list[0]["start"]))
        for curr, nxt in zip(word_list, word_list[1:]):
            if nxt["start"] - curr["end"] >= 0.35:
                gaps.append((curr["end"], nxt["start"]))
        if duration - word_list[-1]["end"] > 0.45:
            gaps.append((word_list[-1]["end"], duration))

        for g_start, g_end in gaps[:24]:
            span = g_end - g_start
            has_peak = any(g_start - 0.15 <= p["time"] <= g_end + 0.15 for p in peaks)
            if span >= 2.2 and not has_peak:
                try:
                    motion = _motion_score(video_path, g_start, g_end)
                except Exception:
                    motion = 0.0
                if motion >= 8.0:
                    speed_ramp_candidates.append({
                        "start": round(g_start, 2),
                        "end": round(g_end, 2),
                        "motion_score": round(motion, 1),
                        "suggested_speed": 2.5 if span < 4.5 else 3.0,
                    })
                else:
                    dead_air_gaps.append({
                        "start": round(g_start, 2),
                        "end": round(g_end, 2),
                    })
            elif span >= 0.38 and not has_peak:
                dead_air_gaps.append({
                    "start": round(g_start, 2),
                    "end": round(g_end, 2),
                })

    # Identify "Momento Fail" and ironic subtext candidates ("Ele não sabia")
    fail_candidates: List[Dict[str, Any]] = []
    for w in word_list:
        token = _norm_token(w["text"])
        if token in _CONFIDENT_TRIGGER_WORDS:
            # Check if a loud peak happens within 1.0s - 6.0s after this confident word
            matching_peak = next(
                (p for p in peaks if w["end"] + 0.3 <= p["time"] <= w["end"] + 6.5),
                None,
            )
            target_time = round(matching_peak["time"] - 0.08, 2) if matching_peak else round(min(duration - 0.2, w["end"] + 1.2), 2)
            fail_candidates.append({
                "trigger_word": w["text"],
                "word_time": round(w["start"], 2),
                "freeze_time": max(0.2, target_time),
                "suggested_subtext": "Ele não sabia...",
            })

    if not fail_candidates and peaks:
        strongest = max(peaks, key=lambda p: p["prominence_db"])
        fail_candidates.append({
            "trigger_word": None,
            "word_time": round(max(0.0, strongest["time"] - 1.2), 2),
            "freeze_time": round(max(0.2, strongest["time"] - 0.05), 2),
            "suggested_subtext": "Foi nesse momento...",
        })

    webcam_box = detect_gameplay_webcam_box(video_path, duration)
    keyframes = []
    if include_keyframes:
        sample_times = [p["time"] for p in peaks[:5]]
        if not sample_times and duration > 1.0:
            sample_times = [round(duration * frac, 2) for frac in (0.2, 0.5, 0.8)]
        keyframes = extract_peak_keyframes_b64(video_path, sample_times)

    return {
        "duration": round(duration, 2),
        "median_db": chaos["median_db"],
        "peaks": peaks,
        "tension_windows": tension_windows,
        "speed_ramp_candidates": speed_ramp_candidates[:4],
        "dead_air_gaps": dead_air_gaps[:12],
        "fail_candidates": fail_candidates[:4],
        "webcam_box": webcam_box,
        "keyframes": keyframes,
    }


def _fmt_signal_ts(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    mm = total // 60
    ss = total % 60
    return f"{mm:02d}:{ss:02d}"


def format_gameplay_signals_for_prompt(video_path: Path) -> str:
    """Build a compact text summary of audio chaos & visual action across a full source video
    so the Gameplay workflow cut selector picks high-energy setups + payoffs.

    Lines follow ``- [MM:SS - MM:SS] ...`` so ``ai._filter_clip_signals_for_window``
    keeps them inside each analysis window on long videos.
    """
    try:
        duration = _probe_duration(video_path)
    except Exception:
        return ""
    target_peaks = max(32, min(96, int(duration // 120) + 16))
    chaos = scan_audio_chaos(video_path, 0.0, duration, max_peaks=target_peaks)
    peaks = chaos.get("peaks") or []
    if not peaks:
        return ""
    lines = [
        "GAMEPLAY AUDIO & CHAOS RADAR (prioritize these high-energy moments, screams, explosions, and laughs):"
    ]
    for p in peaks[:target_peaks]:
        peak_t = float(p["time"])
        setup_t = max(0.0, peak_t - 5.0)
        end_t = min(duration, peak_t + 8.0) if duration > 0 else peak_t + 8.0
        lines.append(
            f"- [{_fmt_signal_ts(setup_t)} - {_fmt_signal_ts(end_t)}] "
            f"[GAMEPLAY CHAOS PEAK @ {_fmt_signal_ts(peak_t)}] {p['kind'].upper()} "
            f"(+{p['prominence_db']} dB above speech, onset +{p['onset_db']} dB) "
            f"-> ideal setup starts around [{_fmt_signal_ts(setup_t)}]"
        )
    return "\n".join(lines)
