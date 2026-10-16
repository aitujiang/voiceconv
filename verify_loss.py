"""在「丢掉的那些时刻」上，做一次干净的对比。

locate_loss.py 用人声频段能量判断有声/无声，但伴奏的鼓和贝斯会漏进这个频段，
所以「丢在 Demucs」那 24.3s 里大部分其实是鼓点被正确移除了，不是丢字。

这里换一个更严谨的做法：只在「人声轨明显活跃」的时刻上比较，
并且用相位/相干性来判断成品那一刻到底还有没有人声成分。
"""
from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np

SR = 22050
HOP = 256


def band_rms(path: Path, lo: int = 200, hi: int = 4000) -> tuple[np.ndarray, np.ndarray]:
    y, _ = librosa.load(str(path), sr=SR, mono=True)
    S = librosa.stft(y, n_fft=2048, hop_length=HOP)
    f = librosa.fft_frequencies(sr=SR, n_fft=2048)
    S = S[(f >= lo) & (f <= hi), :]
    yb = librosa.istft(S, hop_length=HOP, length=len(y))
    rms = librosa.feature.rms(y=yb, frame_length=1024, hop_length=HOP)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=SR, hop_length=HOP)
    return t, rms


def main() -> int:
    if len(sys.argv) < 5:
        print("用法: verify_loss.py <分离人声轨> <成品> <a> <b>  (检查 a~b 秒区间)")
        return 2
    voc, out = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = float(sys.argv[3]), float(sys.argv[4])

    t, e_v = band_rms(voc)
    _, e_o = band_rms(out)
    n = min(len(e_v), len(e_o))
    t, e_v, e_o = t[:n], e_v[:n], e_o[:n]

    sel = (t >= a) & (t <= b)
    if not sel.any():
        print("区间超出音频长度")
        return 2

    tv, to = e_v[sel], e_o[sel]
    print("=" * 70)
    print(f"检查区间 {a:.2f}s - {b:.2f}s（{sel.sum()} 帧）")
    print("=" * 70)
    print(f"人声轨该区间 RMS: 均值 {tv.mean():.4f}  峰值 {tv.max():.4f}")
    print(f"成品   该区间 RMS: 均值 {to.mean():.4f}  峰值 {to.max():.4f}")

    # 逐帧对比：人声轨活跃但成品静音的帧
    thr_v = tv.mean() * 0.8
    thr_o = tv.mean() * 0.25          # 成品比人声轨低 12dB 以上算「没了」
    lost = (tv > thr_v) & (to < thr_o)
    frames = np.flatnonzero(lost)
    print()
    print(f"判定为「人声轨有声、成品几乎无声」的帧: {lost.sum()}"
          f"  ({lost.sum()*HOP/SR:.2f}s)")

    if frames.size:
        groups = np.split(frames, np.flatnonzero(np.diff(frames) > 5) + 1)
        print(f"\n合并成 {len(groups)} 段短促丢失，逐段列出：")
        for g in groups:
            i0, i1 = g[0], g[-1]
            print(f"   {t[sel][i0]:6.2f}s - {t[sel][i1]:6.2f}s   "
                  f"时长 {(i1-i0+1)*HOP/SR:4.2f}s   "
                  f"人声轨 {tv[i0:i1+1].mean():.4f} -> 成品 {to[i0:i1+1].mean():.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
