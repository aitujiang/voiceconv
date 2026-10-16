"""Synthesise two clearly different 'speakers' for a pipeline smoke test.

This is NOT a voice-quality test -- it only proves that the whole chain
(ffmpeg staging -> tokenizer -> style encoder -> DiT/CFM -> vocoder -> wav)
runs end to end and produces a file whose timbre moved toward the reference.

Speaker A: low pitch (~105 Hz), dark spectrum (strong low formants)
Speaker B: high pitch (~210 Hz), bright spectrum (strong high formants)

Both use the same syllable rhythm so we can hear/measure that B's timing is
preserved while A's timbre is imposed.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

SR = 22050
DUR = 4.0


def write_wav(path: Path, wav: np.ndarray, sr: int) -> None:
    """16-bit PCM mono wav via the standard library (no third-party deps)."""
    pcm = np.clip(wav, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(pcm.tobytes())


def syllable(t: np.ndarray, f0: float, formants: list[tuple[float, float, float]],
             seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # glottal-ish pulse train with slight vibrato and jitter
    vib = 1.0 + 0.02 * np.sin(2 * np.pi * 5.0 * t)
    jitter = 1.0 + 0.004 * rng.standard_normal(t.size).cumsum() / max(t.size, 1) ** 0.5
    phase = 2 * np.pi * np.cumsum(f0 * vib * jitter) / SR
    pulse = np.zeros_like(t)
    pulse[np.mod(phase, 2 * np.pi) < 0.35] = 1.0
    sig = pulse

    # formant filtering: sum of resonant modes
    out = np.zeros_like(t)
    for freq, bw, gain in formants:
        w = 2 * np.pi * freq / SR
        r = np.exp(-np.pi * bw / SR)
        # simple 2-pole resonator, applied in the frequency domain for stability
        n = np.arange(t.size)
        out += gain * np.exp(-np.pi * bw * t) * np.sin(w * n)
    sig = 0.6 * sig + 0.9 * out
    # per-syllable envelope
    env = np.hanning(t.size) ** 0.7
    return sig * env


def build(f0: float, formants, n_syl: int, seed: int) -> np.ndarray:
    syl_len = int(SR * DUR / n_syl)
    parts = []
    for i in range(n_syl):
        t = np.arange(syl_len) / SR
        f = f0 * (1.0 + 0.06 * np.sin(i))  # small intonation drift
        parts.append(syllable(t, f, formants, seed + i))
        parts.append(np.zeros(int(SR * 0.04)))  # short gap
    wav = np.concatenate(parts)
    wav /= np.max(np.abs(wav)) + 1e-9
    return 0.85 * wav


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else r"D:\dshworking01\voiceconv\io")
    out.mkdir(parents=True, exist_ok=True)

    # Speaker A -- to be used as the TIMBRE REFERENCE (audio 1)
    a = build(105.0, [(520, 90, 1.0), (1100, 120, 0.5), (2400, 200, 0.15)], 6, 11)
    # Speaker B -- to be CONVERTED (audio 2)
    b = build(210.0, [(760, 110, 1.0), (1900, 150, 0.7), (3300, 250, 0.35)], 6, 77)

    pa, pb = out / "test_voice1_reference.wav", out / "test_voice2_source.wav"
    write_wav(pa, a, SR)
    write_wav(pb, b, SR)
    print(f"wrote {pa}  ({a.size/SR:.2f}s)")
    print(f"wrote {pb}  ({b.size/SR:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
