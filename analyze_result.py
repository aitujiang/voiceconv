"""Objectively check whether the timbre actually moved from A to B.

Reads three wavs (reference timbre, source content, converted output) and
reports pitch + spectral features, so we can prove the conversion did more
than exit with code 0.

Expectation for v1 (no f0 conditioning):
  * pitch of OUT should track SOURCE  (content / prosody preserved)
  * spectrum of OUT should shift toward REFERENCE (timbre adopted)
"""
from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np


def features(path: Path) -> dict:
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    if y.size == 0:
        return {}
    # pitch via pyin (robust for both speech and tonal input)
    f0, vflag, vprob = librosa.pyin(y, fmin=60, fmax=500, sr=sr,
                                    frame_length=2048, hop_length=256)
    voiced = f0[~np.isnan(f0)]
    # spectral shape
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=256))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    centroid = librosa.feature.spectral_centroid(S=S, sr=sr).mean()
    rolloff = librosa.feature.spectral_rolloff(S=S, sr=sr, roll_percent=0.85).mean()
    flatness = librosa.feature.spectral_flatness(S=S).mean()
    # energy distribution in 3 coarse bands
    power = (S ** 2).mean(axis=1)
    bands = {}
    for lo, hi, name in ((0, 800, "low"), (800, 2500, "mid"), (2500, 11025, "high")):
        m = (freqs >= lo) & (freqs < hi)
        bands[name] = float(power[m].sum() / max(power.sum(), 1e-12))
    return {
        "file": path.name,
        "dur": y.size / sr,
        "rms": float(np.sqrt((y ** 2).mean())),
        "peak": float(np.abs(y).max()),
        "f0_median": float(np.median(voiced)) if voiced.size else float("nan"),
        "voiced_ratio": float(voiced.size / max(f0.size, 1)),
        "centroid": float(centroid),
        "rolloff": float(rolloff),
        "flatness": float(flatness),
        **{f"band_{k}": v for k, v in bands.items()},
    }


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    ref, src, out = (Path(p) for p in sys.argv[1:4])
    fr, fs, fo = features(ref), features(src), features(out)

    w = 30
    keys = [("dur", "时长 s", "{:.2f}"), ("rms", "整体响度 RMS", "{:.4f}"),
            ("peak", "峰值", "{:.3f}"), ("f0_median", "基频中位数 Hz", "{:.1f}"),
            ("voiced_ratio", "有声帧占比", "{:.2f}"),
            ("centroid", "谱质心 Hz", "{:.0f}"), ("rolloff", "85%滚降 Hz", "{:.0f}"),
            ("flatness", "谱平坦度", "{:.5f}"),
            ("band_low", "低频能量占比", "{:.3f}"),
            ("band_mid", "中频能量占比", "{:.3f}"),
            ("band_high", "高频能量占比", "{:.3f}")]

    print("=" * 78)
    print(f"{'特征':<14}{'音色参考(A)':>20}{'待变声(B)':>20}{'成品(OUT)':>20}")
    print("-" * 78)
    for k, label, fmt in keys:
        print(f"{label:<14}{fmt.format(fr[k]):>20}{fmt.format(fs[k]):>20}{fmt.format(fo[k]):>20}")

    print("-" * 78)
    # which speaker is OUT closer to, per feature family?
    def closer(key, scale=1.0):
        d_a = abs(fo[key] - fr[key]) / scale
        d_b = abs(fo[key] - fs[key]) / scale
        return "A(参考)" if d_a < d_b else "B(原声)"

    print(f"{'谱质心归属':<14}{'':>20}{'':>20}{closer('centroid'):>20}")
    print(f"{'基频归属':<14}{'':>20}{'':>20}{closer('f0_median', 50):>20}")
    print(f"{'高频能量归属':<14}{'':>20}{'':>20}{closer('band_high'):>20}")
    print("=" * 78)
    print("解读：v1 不做音高变换，所以「基频归属」通常仍偏 B（保留原说话节奏）；")
    print("     「谱质心/高频能量归属」应偏向 A，说明音色（声道特征）已迁移。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
