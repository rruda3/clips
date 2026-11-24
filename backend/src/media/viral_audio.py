"""Built-in SFX and background music catalog for charismatic/viral gameplay edits.

Synthesizes clean, deterministic royalty-free WAV assets on demand using NumPy
and the standard-library ``wave`` module so both FFmpeg exports and browser
previews work offline without external network dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
import math
import tempfile
import wave

import numpy as np

SAMPLE_RATE = 44100

SfxId = Literal[
    "vine_boom",
    "record_scratch",
    "windows_error",
    "crickets",
    "censor_beep",
    "sad_violin",
    "tension_riser",
    "whoosh",
]

MusicId = Literal[
    "sad_drama",
    "wii_quirky",
    "lofi_chill",
    "tension_suspense",
    "hype_phonk",
]

VIRAL_SFX_CATALOG: dict[str, dict[str, str | float]] = {
    "vine_boom": {
        "id": "vine_boom",
        "label": "Vine Boom (Impacto)",
        "category": "impact",
        "duration": 1.35,
    },
    "record_scratch": {
        "id": "record_scratch",
        "label": "Record Scratch (Quebra)",
        "category": "comedy",
        "duration": 0.65,
    },
    "windows_error": {
        "id": "windows_error",
        "label": "Erro do Windows (Fail)",
        "category": "fail",
        "duration": 0.75,
    },
    "crickets": {
        "id": "crickets",
        "label": "Grilo (Silêncio Constrangedor)",
        "category": "comedy",
        "duration": 1.8,
    },
    "censor_beep": {
        "id": "censor_beep",
        "label": "Bipe de Censura",
        "category": "censor",
        "duration": 0.45,
    },
    "sad_violin": {
        "id": "sad_violin",
        "label": "Violino Triste (Momento Fail)",
        "category": "fail",
        "duration": 2.2,
    },
    "tension_riser": {
        "id": "tension_riser",
        "label": "Riser Grave (Tensão)",
        "category": "tension",
        "duration": 2.5,
    },
    "whoosh": {
        "id": "whoosh",
        "label": "Whoosh (Zoom / Transição)",
        "category": "transition",
        "duration": 0.35,
    },
}

VIRAL_MUSIC_CATALOG: dict[str, dict[str, str | float]] = {
    "sad_drama": {
        "id": "sad_drama",
        "label": "Triste / Dramática (Preto & Branco)",
        "mood": "fail",
        "duration": 16.0,
    },
    "wii_quirky": {
        "id": "wii_quirky",
        "label": "Estilo Nintendo / Wii (Zoeira)",
        "mood": "comedy",
        "duration": 16.0,
    },
    "lofi_chill": {
        "id": "lofi_chill",
        "label": "Lo-Fi / Elevador (Fundo Suave)",
        "mood": "chill",
        "duration": 16.0,
    },
    "tension_suspense": {
        "id": "tension_suspense",
        "label": "Suspense / Clutch (Tensão)",
        "mood": "tension",
        "duration": 16.0,
    },
    "hype_phonk": {
        "id": "hype_phonk",
        "label": "Phonk / Hype (Ação / Explosão)",
        "mood": "hype",
        "duration": 16.0,
    },
}


def _write_wav(path: Path, signal: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(signal))) if signal.size else 1.0
    if peak > 1e-6:
        signal = signal / max(1.0, peak)
    pcm = np.clip(signal * 32767.0, -32768, 32767).astype(np.int16)
    stereo = np.column_stack((pcm, pcm)).ravel()
    tmp = path.with_suffix(".tmp.wav")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(stereo.tobytes())
    tmp.replace(path)
    return path


def _synth_sfx(sfx_id: str) -> np.ndarray:
    info = VIRAL_SFX_CATALOG.get(sfx_id) or VIRAL_SFX_CATALOG["vine_boom"]
    duration = float(info["duration"])
    t = np.linspace(0.0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    if sfx_id == "vine_boom":
        freq = 34.0 + 75.0 * np.exp(-t * 6.5)
        phase = 2.0 * math.pi * np.cumsum(freq) / SAMPLE_RATE
        env = np.exp(-t * 2.4) * np.minimum(1.0, t / 0.008)
        sub = np.sin(phase) + 0.45 * np.sin(2.0 * phase) + 0.2 * np.sin(3.0 * phase)
        return np.tanh(sub * 1.8) * env * 0.95

    if sfx_id == "record_scratch":
        rng = np.random.default_rng(42)
        noise = rng.normal(0.0, 0.35, size=t.shape)
        sweep = np.sin(2.0 * math.pi * (180.0 + 950.0 * np.sin(t * 22.0)) * t)
        env = np.sin(math.pi * t / duration) ** 0.6
        return (0.65 * sweep + 0.35 * noise) * env * 0.85

    if sfx_id == "windows_error":
        part1 = t < 0.22
        part2 = t >= 0.22
        tone1 = (np.sin(2 * math.pi * 440 * t) + 0.6 * np.sin(2 * math.pi * 554.37 * t)) * np.exp(-t * 8.0)
        t2 = np.maximum(0.0, t - 0.22)
        tone2 = (np.sin(2 * math.pi * 330 * t2) + 0.7 * np.sin(2 * math.pi * 415.3 * t2)) * np.exp(-t2 * 4.5)
        return (tone1 * part1 + tone2 * part2) * 0.8

    if sfx_id == "crickets":
        carrier = np.sin(2 * math.pi * 4200 * t) + 0.4 * np.sin(2 * math.pi * 8400 * t)
        pulse = (np.sin(2 * math.pi * 28 * t) > 0.2).astype(np.float64)
        burst = ((t % 0.6) < 0.28).astype(np.float64)
        return carrier * pulse * burst * 0.45

    if sfx_id == "censor_beep":
        env = np.ones_like(t)
        fade = min(400, len(t) // 8)
        if fade > 0:
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
        return np.sin(2 * math.pi * 1000.0 * t) * env * 0.85

    if sfx_id == "sad_violin":
        vibrato = 5.5 * np.sin(2 * math.pi * 5.8 * t)
        f1 = np.where(t < 1.0, 440.0 + vibrato, 415.3 + vibrato)
        phase = 2 * math.pi * np.cumsum(f1) / SAMPLE_RATE
        bow = (
            np.sin(phase)
            + 0.5 * np.sin(2 * phase)
            + 0.25 * np.sin(3 * phase)
            + 0.12 * np.sin(4 * phase)
        )
        env = np.minimum(1.0, t / 0.18) * np.exp(-np.maximum(0.0, t - 1.2) * 1.4)
        return bow * env * 0.75

    if sfx_id == "tension_riser":
        freq = 55.0 + 380.0 * ((t / duration) ** 2.2)
        phase = 2 * math.pi * np.cumsum(freq) / SAMPLE_RATE
        env = (t / duration) ** 1.6
        return (np.sin(phase) + 0.4 * np.sin(1.5 * phase)) * env * 0.8

    # Default: whoosh
    rng = np.random.default_rng(7)
    noise = rng.normal(0.0, 0.3, size=t.shape)
    freq = 160.0 + 620.0 * np.sin(math.pi * t / duration)
    phase = 2 * math.pi * np.cumsum(freq) / SAMPLE_RATE
    env = np.sin(math.pi * t / duration) ** 1.4
    return (0.65 * np.sin(phase) + 0.35 * noise) * env * 0.8


def _synth_music(music_id: str) -> np.ndarray:
    info = VIRAL_MUSIC_CATALOG.get(music_id) or VIRAL_MUSIC_CATALOG["sad_drama"]
    duration = float(info["duration"])
    t = np.linspace(0.0, duration, int(SAMPLE_RATE * duration), endpoint=False)

    if music_id == "sad_drama":
        # Slow emotional minor progression (Am -> F -> Dm -> E)
        chords = [
            (220.0, 261.63, 329.63),
            (174.61, 220.0, 261.63),
            (146.83, 174.61, 220.0),
            (164.81, 207.65, 246.94),
        ]
        out = np.zeros_like(t)
        bar_len = 4.0
        for idx in range(int(duration // bar_len)):
            c = chords[idx % len(chords)]
            mask = (t >= idx * bar_len) & (t < (idx + 1) * bar_len)
            local_t = t[mask] - idx * bar_len
            vibrato = 1.0 + 0.004 * np.sin(2 * math.pi * 5.0 * local_t)
            env = np.minimum(1.0, local_t / 0.25) * np.exp(-local_t * 0.35)
            wave_sum = sum(
                np.sin(2 * math.pi * f * vibrato * local_t)
                + 0.35 * np.sin(4 * math.pi * f * vibrato * local_t)
                for f in c
            )
            out[mask] = wave_sum * env
        return out * 0.45

    if music_id == "wii_quirky":
        # Plucky lighthearted melody
        notes = [261.63, 329.63, 392.0, 329.63, 440.0, 392.0, 329.63, 293.66]
        step = 0.5
        out = np.zeros_like(t)
        for idx in range(int(duration / step)):
            f = notes[idx % len(notes)]
            mask = (t >= idx * step) & (t < (idx + 1) * step)
            local_t = t[mask] - idx * step
            env = np.exp(-local_t * 8.5)
            out[mask] = (np.sin(2 * math.pi * f * local_t) + 0.4 * np.sin(4 * math.pi * f * local_t)) * env
        return out * 0.45

    if music_id == "tension_suspense":
        # Low pulsing suspense drone + heartbeat tick
        pulse = 0.55 + 0.45 * np.sin(2 * math.pi * 2.0 * t)
        drone = (np.sin(2 * math.pi * 55.0 * t) + 0.5 * np.sin(2 * math.pi * 58.27 * t)) * pulse
        tick = np.exp(-((t % 0.5) * 35.0)) * np.sin(2 * math.pi * 1200.0 * t) * 0.25
        return (drone + tick) * 0.5

    if music_id == "hype_phonk":
        # Driving bass + cowbell pattern
        notes = [110.0, 110.0, 130.81, 123.47, 110.0, 146.83, 130.81, 123.47]
        step = 0.25
        out = np.zeros_like(t)
        for idx in range(int(duration / step)):
            f = notes[idx % len(notes)]
            mask = (t >= idx * step) & (t < (idx + 1) * step)
            local_t = t[mask] - idx * step
            env = np.exp(-local_t * 9.0)
            cowbell = (
                np.sin(2 * math.pi * (f * 4) * local_t)
                + 0.7 * np.sin(2 * math.pi * (f * 4 * 1.48) * local_t)
            ) * env
            bass = np.tanh(np.sin(2 * math.pi * (f / 2) * local_t) * 2.2) * np.exp(-local_t * 5.0)
            out[mask] = 0.55 * bass + 0.45 * cowbell
        return out * 0.55

    # Default: lofi_chill
    chords = [
        (261.63, 329.63, 392.0, 493.88),
        (220.0, 261.63, 329.63, 392.0),
        (174.61, 220.0, 261.63, 349.23),
        (196.0, 246.94, 293.66, 392.0),
    ]
    out = np.zeros_like(t)
    bar_len = 4.0
    for idx in range(int(duration // bar_len)):
        c = chords[idx % len(chords)]
        mask = (t >= idx * bar_len) & (t < (idx + 1) * bar_len)
        local_t = t[mask] - idx * bar_len
        env = np.minimum(1.0, local_t / 0.12) * np.exp(-local_t * 0.45)
        out[mask] = sum(np.sin(2 * math.pi * f * local_t) for f in c) * env
    return out * 0.38


def audio_cache_dir(temp_dir: str | None = None) -> Path:
    base = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
    return base / "supoclip_viral_audio"


def ensure_sfx_file(sfx_id: str, temp_dir: str | None = None) -> Path | None:
    if sfx_id not in VIRAL_SFX_CATALOG:
        return None
    target = audio_cache_dir(temp_dir) / "sfx" / f"{sfx_id}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target
    return _write_wav(target, _synth_sfx(sfx_id))


def ensure_music_file(music_id: str, temp_dir: str | None = None) -> Path | None:
    if music_id not in VIRAL_MUSIC_CATALOG:
        return None
    target = audio_cache_dir(temp_dir) / "music" / f"{music_id}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target
    return _write_wav(target, _synth_music(music_id))
