"""AI Viral Director service for TikTok / Reels / Shorts gameplay & charismatic edits.

Combines the local acoustic/visual radar (`gameplay_signals.py`) with Google
Gemini Multimodal (`GOOGLE_API_KEY`) to analyze a clip's audio chaos, webcam
reactions, and gameplay action, producing a `ViralEditConfig` and updated
`EditDocument` in either Automatic (1-Click) or Hybrid (Copilot) mode.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx

from ..config import get_config
from ..editor_document import (
    BackgroundMusic,
    CameraKeyframe,
    EditDocument,
    Segment,
    SfxPlacement,
    SubtextOverlay,
    TimeEffect,
    ViralEditConfig,
    ViralSuggestion,
    VoiceEffect,
    WebcamRegion,
)
from ..media.gameplay_signals import analyze_clip_gameplay_signals
from ..media.viral_audio import VIRAL_MUSIC_CATALOG, VIRAL_SFX_CATALOG

logger = logging.getLogger(__name__)

VibeKind = Literal["zoeira", "clutch", "fail", "hype"]
SPEAKER_PALETTE = ("#FACC15", "#38BDF8", "#4ADE80", "#FB7185", "#C084FC", "#FB923C")

VIBE_DEFAULT_MUSIC: dict[str, str] = {
    "zoeira": "wii_quirky",
    "clutch": "tension_suspense",
    "fail": "sad_drama",
    "hype": "hype_phonk",
}


async def _ask_gemini_multimodal(
    signals: Dict[str, Any],
    words: List[Dict[str, Any]],
    vibe: str,
) -> Optional[Dict[str, Any]]:
    """Call Google Gemini API with extracted keyframes + audio chaos radar + transcript."""
    cfg = get_config()
    api_key = (cfg.google_api_key or "").strip()
    if not api_key:
        return None

    transcript_snippet = " ".join(
        f"[{w['start']:.1f}s] {w['text']}" for w in words[:120]
    )
    radar_summary = {
        "duration": signals.get("duration"),
        "peaks": signals.get("peaks", [])[:8],
        "tension_windows": signals.get("tension_windows", [])[:4],
        "fail_candidates": signals.get("fail_candidates", [])[:4],
        "speed_ramp_candidates": signals.get("speed_ramp_candidates", [])[:3],
    }

    prompt = (
        "Você é um editor profissional de TikTok/Reels/Shorts especialista em vídeos virais e carismáticos de Gameplay com amigos.\n"
        f"Vibe escolhida: {vibe}.\n"
        f"Radar Acústico e Visual (JSON): {json.dumps(radar_summary, ensure_ascii=False)}\n"
        f"Transcrição com tempos: {transcript_snippet or '(sem fala detectada - focar nos barulhos e ação visual)'}\n\n"
        "Analise as imagens dos picos e os dados de áudio. Retorne APENAS um JSON válido com a estrutura:\n"
        "{\n"
        '  "hook_headline": "título curto e chamativo para os primeiros 3s (máx 45 chars)",\n'
        '  "subtexts": [{"start": 1.5, "end": 3.2, "text": "Ele não sabia...", "variant": "thought", "color": "#FACC15"}],\n'
        '  "fail_moment": {"time": 4.2, "reason": "motivo do momento fail P&B"},\n'
        '  "explosion_cut_time": null,\n'
        '  "music_track": "wii_quirky | sad_drama | tension_suspense | hype_phonk | lofi_chill"\n'
        "}"
    )

    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for kf in (signals.get("keyframes") or [])[:4]:
        parts.append({
            "inline_data": {
                "mime_type": kf.get("mime_type", "image/jpeg"),
                "data": kf["data_b64"],
            }
        })

    raw_llm = (cfg.llm or "").strip()
    configured_model = raw_llm.split(":", 1)[1].strip() if ":" in raw_llm else raw_llm
    candidate_models = []
    if configured_model and any(configured_model.startswith(p) for p in ("gemini-3", "gemini-flash", "gemini-pro")):
        candidate_models.append(configured_model)
    for m in (
        "gemini-3-flash-preview",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite-preview",
        "gemini-flash-latest",
        "gemini-3.1-pro-preview",
    ):
        if m not in candidate_models:
            candidate_models.append(m)

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            for model_name in candidate_models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                try:
                    resp = await client.post(url, json=payload)
                except httpx.HTTPError as exc:
                    logger.warning("Gemini multimodal (%s) request error: %s", model_name, exc)
                    continue
                if resp.status_code != 200:
                    logger.warning("Gemini multimodal (%s) returned status %s", model_name, resp.status_code)
                    continue
                data = resp.json()
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                if text:
                    return json.loads(text)
        return None
    except Exception as exc:
        logger.warning("Gemini multimodal enrichment skipped, using local radar: %s", exc)
        return None


def _assign_speaker_colors(words: List[Dict[str, Any]]) -> dict[str, str]:
    colors: dict[str, str] = {}
    idx = 0
    for w in words:
        spk = str(w.get("speaker") or "").strip()
        if spk and spk not in colors:
            colors[spk] = SPEAKER_PALETTE[idx % len(SPEAKER_PALETTE)]
            idx += 1
    if not colors:
        colors["Player 1"] = "#FACC15"
        colors["Player 2"] = "#38BDF8"
    return colors


def _apply_jcut_silence_cleanup(
    segments: List[Segment],
    dead_air_gaps: List[Dict[str, Any]],
    duration: float,
) -> List[Segment]:
    """Tighten dead-air pauses > 0.38s leaving a snappy 0.12s J-cut breath."""
    if not dead_air_gaps:
        return segments
    cuts: List[tuple[float, float]] = []
    for gap in dead_air_gaps:
        g_start = float(gap["start"]) + 0.06
        g_end = float(gap["end"]) - 0.06
        if g_end - g_start >= 0.25:
            cuts.append((g_start, g_end))
    if not cuts:
        return segments

    new_segments: List[Segment] = []
    for seg in segments:
        cursor = seg.start
        for c_start, c_end in sorted(cuts):
            if c_end <= cursor or c_start >= seg.end:
                continue
            trim_start = max(cursor, c_start)
            trim_end = min(seg.end, c_end)
            if trim_start - cursor >= 0.12:
                new_segments.append(Segment(id=uuid.uuid4().hex[:10], start=round(cursor, 3), end=round(trim_start, 3)))
            cursor = max(cursor, trim_end)
        if seg.end - cursor >= 0.12:
            new_segments.append(Segment(id=uuid.uuid4().hex[:10], start=round(cursor, 3), end=round(seg.end, 3)))

    total_kept = sum(s.end - s.start for s in new_segments)
    if not new_segments or total_kept < min(1.0, duration * 0.35):
        return segments
    return new_segments[:80]


def build_viral_tiktok_edit_sync(
    video_path: Path,
    document: EditDocument,
    duration: float,
    *,
    mode: Literal["auto", "hybrid"] = "auto",
    vibe: VibeKind = "zoeira",
    split_cam: Optional[bool] = None,
    cold_open: bool = False,
    explosion_cut: bool = False,
    gemini_plan: Optional[Dict[str, Any]] = None,
    signals: Optional[Dict[str, Any]] = None,
) -> tuple[EditDocument, Dict[str, Any]]:
    """Synchronous core of the Viral Director (deterministic, works without event loop)."""
    words_dump = [w.model_dump() for w in document.words]
    if signals is None:
        signals = analyze_clip_gameplay_signals(
            video_path,
            duration,
            words=words_dump,
            include_keyframes=False,
        )

    webcam_info = signals.get("webcam_box") or {}
    webcam_box = WebcamRegion(
        x=float(webcam_info.get("x", 0.02)),
        y=float(webcam_info.get("y", 0.03)),
        w=float(webcam_info.get("w", 0.26)),
        h=float(webcam_info.get("h", 0.28)),
    )

    if split_cam is True:
        layout_mode: Literal["off", "split", "fullscreen_cam", "fullscreen_game", "pip"] = "split"
    elif split_cam is False:
        layout_mode = "off"
    else:
        layout_mode = "split" if webcam_info.get("detected") and not webcam_info.get("already_vertical") else "off"

    camera_keyframes: List[CameraKeyframe] = []
    time_effects: List[TimeEffect] = []
    subtexts: List[SubtextOverlay] = []
    sfx_list: List[SfxPlacement] = []
    voice_effects: List[VoiceEffect] = []
    suggestions: List[ViralSuggestion] = []

    peaks = signals.get("peaks") or []
    tension_windows = signals.get("tension_windows") or []
    fail_candidates = signals.get("fail_candidates") or []
    speed_candidates = signals.get("speed_ramp_candidates") or []

    # 1. Slow Zoom-in de Tensão + Riser SFX on tension windows
    for tw in tension_windows[:2]:
        t_start = max(0.0, float(tw["start"]))
        t_end = min(duration, float(tw["end"]))
        if t_end - t_start >= 0.8:
            camera_keyframes.append(
                CameraKeyframe(
                    id=uuid.uuid4().hex[:10],
                    start=t_start,
                    end=t_end,
                    mode="slow_tension",
                    zoom=1.20,
                    intensity=0.5,
                    target="center",
                    reason="Silêncio tenso antes do pico de caos",
                )
            )
            sfx_list.append(
                SfxPlacement(
                    id=uuid.uuid4().hex[:10],
                    sfxId="tension_riser",
                    start=t_start,
                    volume=0.65,
                )
            )
            suggestions.append(
                ViralSuggestion(
                    id=uuid.uuid4().hex[:10],
                    enabled=True,
                    timestamp=t_start,
                    end=t_end,
                    category="camera",
                    title="Slow Zoom de Tensão + Riser Grave",
                    reason=f"Contraste de +{tw.get('contrast_db', 9)} dB detectado logo a seguir",
                )
            )

    # 2. Audio Peaks -> Zoom-in Punch + Screen Shake + SFX (Vine Boom / Whoosh)
    for idx, peak in enumerate(peaks[:5]):
        p_time = float(peak["time"])
        p_start = max(0.0, round(p_time - 0.05, 2))
        p_end = min(duration, round(p_time + 0.35, 2))
        if p_end <= p_start:
            continue
        is_explosion = peak.get("kind") == "explosion_or_scream"
        camera_keyframes.append(
            CameraKeyframe(
                id=uuid.uuid4().hex[:10],
                start=p_start,
                end=p_end,
                mode="shake" if is_explosion else "punch",
                zoom=1.26 if is_explosion else 1.18,
                intensity=0.75 if is_explosion else 0.45,
                target="cam" if layout_mode == "split" and idx % 2 == 1 else "center",
                reason=f"Pico de áudio ({peak['kind']}, +{peak['prominence_db']} dB)",
            )
        )
        sfx_list.append(
            SfxPlacement(
                id=uuid.uuid4().hex[:10],
                sfxId="vine_boom" if is_explosion else "whoosh",
                start=p_start,
                volume=0.82,
            )
        )
        suggestions.append(
            ViralSuggestion(
                id=uuid.uuid4().hex[:10],
                enabled=True,
                timestamp=p_start,
                end=p_end,
                category="camera",
                title="Screen Shake + Vine Boom" if is_explosion else "Zoom-in Punch Seco + Whoosh",
                reason=f"Pico acústico de +{peak['prominence_db']} dB acima da voz",
            )
        )

    # 3. Preset "Momento Fail" (Preto e Branco + Freeze / Drama + Sad Violin + Subtexto)
    chosen_fail = None
    if gemini_plan and isinstance(gemini_plan.get("fail_moment"), dict) and gemini_plan["fail_moment"].get("time") is not None:
        ft = float(gemini_plan["fail_moment"]["time"])
        if 0.2 <= ft <= duration - 0.2:
            chosen_fail = {
                "freeze_time": round(ft, 2),
                "word_time": max(0.0, round(ft - 1.2, 2)),
                "suggested_subtext": "Ele não sabia...",
                "reason": str(gemini_plan["fail_moment"].get("reason") or "Momento crítico detectado pela IA"),
            }
    if chosen_fail is None and fail_candidates and vibe in ("zoeira", "fail"):
        chosen_fail = fail_candidates[0]

    if chosen_fail is not None:
        f_start = max(0.0, min(duration - 0.4, float(chosen_fail["freeze_time"])))
        f_end = min(duration, round(f_start + 1.5, 2))
        if f_end > f_start + 0.1:
            time_effects.append(
                TimeEffect(
                    id=uuid.uuid4().hex[:10],
                    start=f_start,
                    end=f_end,
                    kind="freeze_bw",
                    freezeDuration=1.5,
                    speed=1.0,
                    reason=str(chosen_fail.get("reason") or "Preset Momento Fail (Preto & Branco + Drama)"),
                )
            )
            sfx_list.append(
                SfxPlacement(
                    id=uuid.uuid4().hex[:10],
                    sfxId="sad_violin" if vibe == "fail" else "record_scratch",
                    start=f_start,
                    volume=0.88,
                )
            )
            sub_start = max(0.0, float(chosen_fail.get("word_time", f_start)))
            sub_end = min(duration, max(sub_start + 1.4, f_end))
            subtexts.append(
                SubtextOverlay(
                    id=uuid.uuid4().hex[:10],
                    start=sub_start,
                    end=sub_end,
                    text=str(chosen_fail.get("suggested_subtext") or "Ele não sabia..."),
                    x=0.5,
                    y=0.26,
                    color="#FACC15",
                    variant="thought",
                )
            )
            suggestions.append(
                ViralSuggestion(
                    id=uuid.uuid4().hex[:10],
                    enabled=True,
                    timestamp=f_start,
                    end=f_end,
                    category="fail",
                    title="Preset Momento Fail (Preto & Branco + Subtexto + SFX)",
                    reason="Fala confiante / impacto seguido de caos",
                )
            )

    # Add any extra Gemini subtexts
    if gemini_plan and isinstance(gemini_plan.get("subtexts"), list):
        for raw_sub in gemini_plan["subtexts"][:3]:
            try:
                s_t = max(0.0, float(raw_sub.get("start", 0.5)))
                e_t = min(duration, float(raw_sub.get("end", s_t + 1.5)))
                txt = str(raw_sub.get("text") or "").strip()[:120]
                if txt and e_t > s_t + 0.2 and not any(abs(existing.start - s_t) < 0.8 for existing in subtexts):
                    subtexts.append(
                        SubtextOverlay(
                            id=uuid.uuid4().hex[:10],
                            start=round(s_t, 2),
                            end=round(e_t, 2),
                            text=txt,
                            x=0.5,
                            y=0.28,
                            color="#FACC15",
                            variant="thought",
                        )
                    )
            except Exception:
                continue

    # 4. Speed Ramp candidates (2x-4x on silent movement stretches)
    for sr in speed_candidates[:2]:
        sr_s = max(0.0, float(sr["start"]))
        sr_e = min(duration, float(sr["end"]))
        if sr_e - sr_s >= 1.5:
            time_effects.append(
                TimeEffect(
                    id=uuid.uuid4().hex[:10],
                    start=sr_s,
                    end=sr_e,
                    kind="speed_ramp",
                    freezeDuration=0.0,
                    speed=float(sr.get("suggested_speed", 2.5)),
                    reason="Trecho de deslocamento com movimento visual sem fala",
                )
            )
            suggestions.append(
                ViralSuggestion(
                    id=uuid.uuid4().hex[:10],
                    enabled=True,
                    timestamp=sr_s,
                    end=sr_e,
                    category="speed",
                    title=f"Speed Ramp ({sr.get('suggested_speed', 2.5)}x)",
                    reason="Acelera caminhada/ação silenciosa mantendo o contexto",
                )
            )

    # 5. Voice effects (Bass Boost on screamed/highlighted words near peaks)
    if peaks and document.words:
        loudest_time = float(peaks[0]["time"])
        near_word = min(document.words, key=lambda w: abs(w.start - loudest_time))
        if abs(near_word.start - loudest_time) <= 1.5:
            voice_effects.append(
                VoiceEffect(
                    id=uuid.uuid4().hex[:10],
                    start=near_word.start,
                    end=min(duration, max(near_word.end, near_word.start + 0.45)),
                    effect="bass_boost",
                )
            )

    # 6. Segment edits in Auto Mode: J-Cut silence cleanup, Explosion Cut, and Cold Open teaser
    next_segments = list(document.segments)
    if mode == "auto":
        next_segments = _apply_jcut_silence_cleanup(
            next_segments, signals.get("dead_air_gaps") or [], duration
        )

    if (explosion_cut or (mode == "auto" and vibe == "hype")) and peaks:
        last_peak = max(peaks, key=lambda p: p["time"])
        if last_peak["time"] >= duration * 0.55:
            cut_point = min(duration, round(float(last_peak["time"]) + 0.20, 2))
            trimmed = []
            for seg in next_segments:
                if seg.start >= cut_point - 0.05:
                    continue
                trimmed.append(seg.model_copy(update={"end": min(seg.end, cut_point)}))
            if trimmed:
                next_segments = trimmed
                suggestions.append(
                    ViralSuggestion(
                        id=uuid.uuid4().hex[:10],
                        enabled=True,
                        timestamp=max(0.0, cut_point - 0.3),
                        end=cut_point,
                        category="explosion",
                        title="Corte Seco na Explosão (Perfectly Cut Boom)",
                        reason=f"Corta exatamente 0.20s após o pico de +{last_peak['prominence_db']} dB",
                    )
                )

    if cold_open and peaks:
        best_peak = max(peaks, key=lambda p: p["prominence_db"])
        teaser_end = min(duration, round(float(best_peak["time"]) + 0.22, 2))
        teaser_start = max(0.0, round(teaser_end - 1.45, 2))
        if teaser_end - teaser_start >= 0.6 and teaser_start > 2.0:
            teaser_seg = Segment(
                id=f"teaser_{uuid.uuid4().hex[:6]}",
                start=teaser_start,
                end=teaser_end,
            )
            next_segments = [teaser_seg, *next_segments][:100]
            suggestions.append(
                ViralSuggestion(
                    id=uuid.uuid4().hex[:10],
                    enabled=True,
                    timestamp=teaser_start,
                    end=teaser_end,
                    category="explosion",
                    title="Cold Open de 1.5s (Teaser da Explosão no Início)",
                    reason="Puxa o auge do caos para os primeiros 1.5s para segurar retenção",
                )
            )

    # 7. Highlight punch words & configure TikTok dynamic captions
    peak_times = [float(p["time"]) for p in peaks]
    next_words = []
    for w in document.words:
        near_peak = any(abs(w.start - pt) <= 0.65 for pt in peak_times)
        next_words.append(
            w.model_copy(update={"highlight": bool(w.highlight or near_peak)})
        )

    next_captions = document.captions.model_copy(
        update={
            "enabled": True,
            "font": "Poppins-ExtraBold",
            "wordsPerLine": min(3, max(1, document.captions.wordsPerLine)),
            "karaoke": True,
            "uppercase": True,
            "accent": "#FACC15" if vibe != "fail" else "#FF4D4D",
        }
    )

    music_track = VIBE_DEFAULT_MUSIC.get(vibe, "wii_quirky")
    if gemini_plan and gemini_plan.get("music_track") in VIRAL_MUSIC_CATALOG:
        music_track = gemini_plan["music_track"]

    viral_config = ViralEditConfig(
        mode=mode,
        vibe=vibe,
        layoutMode=layout_mode,
        webcamBox=webcam_box,
        cameraKeyframes=camera_keyframes,
        timeEffects=time_effects,
        subtexts=subtexts,
        speakerColors=_assign_speaker_colors(words_dump),
        sfx=sfx_list,
        music=BackgroundMusic(
            trackId=music_track,  # type: ignore[arg-type]
            volume=0.28,
            autoDuck=True,
        ),
        voiceEffects=voice_effects,
        suggestions=suggestions,
    )

    next_hook = document.hookTitle
    if gemini_plan and isinstance(gemini_plan.get("hook_headline"), str) and gemini_plan["hook_headline"].strip():
        next_hook = gemini_plan["hook_headline"].strip()[:100]

    updated_doc = document.model_copy(
        update={
            "segments": next_segments,
            "words": next_words,
            "captions": next_captions,
            "framing": document.framing.model_copy(update={"aspect": "vertical"}),
            "hookTitle": next_hook,
            "viral": viral_config,
        }
    ).validate_duration(duration)

    report = {
        "used_gemini": gemini_plan is not None,
        "peaks_count": len(peaks),
        "tension_count": len(tension_windows),
        "fail_moments_count": len(fail_candidates),
        "speed_ramps_count": len(speed_candidates),
        "dead_air_trimmed_count": len(signals.get("dead_air_gaps") or []),
        "webcam_detected": bool(webcam_info.get("detected")),
        "webcam_box": webcam_box.model_dump(),
        "sfx_catalog": list(VIRAL_SFX_CATALOG.values()),
        "music_catalog": list(VIRAL_MUSIC_CATALOG.values()),
    }
    return updated_doc, report


async def build_viral_tiktok_edit(
    video_path: Path,
    document: EditDocument,
    duration: float,
    *,
    mode: Literal["auto", "hybrid"] = "auto",
    vibe: VibeKind = "zoeira",
    split_cam: Optional[bool] = None,
    cold_open: bool = False,
    explosion_cut: bool = False,
    use_gemini: bool = True,
) -> tuple[EditDocument, Dict[str, Any]]:
    """Analyze clip (with optional Gemini Multimodal) and produce a charismatic EditDocument."""
    words_dump = [w.model_dump() for w in document.words]
    signals = analyze_clip_gameplay_signals(
        video_path,
        duration,
        words=words_dump,
        include_keyframes=use_gemini,
    )
    gemini_plan = await _ask_gemini_multimodal(signals, words_dump, vibe) if use_gemini else None
    return build_viral_tiktok_edit_sync(
        video_path,
        document,
        duration,
        mode=mode,
        vibe=vibe,
        split_cam=split_cam,
        cold_open=cold_open,
        explosion_cut=explosion_cut,
        gemini_plan=gemini_plan,
        signals=signals,
    )
