"""最干净的一次对比：分离人声轨  vs  变声后的人声（都不含伴奏）。

伴奏会污染人声频段，所以只有在「两边都不含伴奏」时，能量对比才有意义。
判断丢失的标准：人声轨明显活跃、而变声后的人声几乎无声 / 或明显短于原时长。
"""
from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np

SR = 22050
HOP = 256


def env(path: Path) -> tuple[np.ndarray, np.ndarray, float]:
    y, _ = librosa.load(str(path), sr=SR, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=HOP)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=SR, hop_length=HOP)
    return t, rms, len(y) / SR


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: compare_vocals.py <分离人声轨> <变声后的人声>")
        return 2
    a_path, b_path = Path(sys.argv[1]), Path(sys.argv[2])

    ta, ea, da = env(a_path)
    tb, eb, db = env(b_path)
    print("=" * 74)
    print(f"分离人声轨 : {da:7.3f}s   平均 RMS {ea.mean():.4f}   总能量 {ea.sum():.1f}")
    print(f"变声后人声 : {db:7.3f}s   平均 RMS {eb.mean():.4f}   总能量 {eb.sum():.1f}")
    print("=" * 74)

    n = min(len(ea), len(eb))
    t, ea, eb = ta[:n], ea[:n], eb[:n]

    # 归一化后比较「活跃时刻的分布」是否一致 —— 这直接反映字有没有少
    na = ea / max(np.percentile(ea, 95), 1e-9)
    nb = eb / max(np.percentile(eb, 95), 1e-9)
    corr = float(np.corrcoef(na, nb)[0, 1])
    print(f"\n两者能量包络的相关系数: {corr:.3f}")
    print("  （接近 1 = 断句、停顿、轻重音都保留；明显偏低 = 有字被吃掉或时间错位）")

    act_a = na > 0.25
    act_b = nb > 0.25
    print(f"\n人声轨活跃帧   : {act_a.sum():5d} ({100*act_a.mean():.1f}%)")
    print(f"变声后活跃帧   : {act_b.sum():5d} ({100*act_b.mean():.1f}%)")
    print(f"两者都有声     : {(act_a & act_b).sum():5d}")
    print(f"人声轨有声→变声后没了 : {(act_a & ~act_b).sum():5d} "
          f"({(act_a & ~act_b).sum()*HOP/SR:.2f}s)")

    lost = act_a & ~act_b
    if lost.any():
        idx = np.flatnonzero(lost)
        groups = np.split(idx, np.flatnonzero(np.diff(idx) > 6) + 1)
        print(f"\n丢失片段（合并后 {len(groups)} 段），全部列出:")
        for g in groups:
            i0, i1 = g[0], g[-1]
            dur = (i1 - i0 + 1) * HOP / SR
            print(f"   {t[i0]:6.2f}s - {t[i1]:6.2f}s  时长 {dur:4.2f}s  "
                  f"原能量 {ea[i0:i1+1].mean():.4f} -> 变声后 {eb[i0:i1+1].mean():.4f}")
    else:
        print("\n没有发现「原有人声、变声后消失」的片段。")

    # 反过来：变声后多出来的（Seed-VC 幻觉/拖音）
    extra = act_b & ~act_a
    print(f"\n反向：人声轨静音→变声后有声音 : {extra.sum()} 帧 "
          f"({extra.sum()*HOP/SR:.2f}s)  ← 这部分是模型自己加的（幻觉）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
