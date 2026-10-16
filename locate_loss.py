"""定位「丢字」发生在哪一步。

对比三份音频，用「人声频段短时能量」逐帧判断有声/无声：
    源文件  ->  分离出的人声轨  ->  变声后混回伴奏的成品

如果某个时间点在源文件里有声、在人声轨里也有声，但成品里没了
-> 丢在 Seed-VC 变声环节
如果源文件有声、人声轨里就没了
-> 丢在 Demucs 分离环节
"""
from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np

SR = 22050
HOP = 256
FRAME = 1024


def vocal_envelope(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """带通到人声频段后的短时 RMS，返回 (时间轴, 能量)。"""
    y, _ = librosa.load(str(path), sr=SR, mono=True)
    # 只保留 200Hz-4kHz —— 人声基频与清晰度所在，去掉鼓和贝斯
    S = librosa.stft(y, n_fft=2048, hop_length=HOP)
    freqs = librosa.fft_frequencies(sr=SR, n_fft=2048)
    mask = (freqs >= 200) & (freqs <= 4000)
    S = S[mask, :]
    y_band = librosa.istft(S, hop_length=HOP, length=len(y))
    rms = librosa.feature.rms(y=y_band, frame_length=FRAME, hop_length=HOP)[0]
    t = librosa.frames_to_time(np.arange(len(rms)), sr=SR, hop_length=HOP)
    return t, rms


def main() -> int:
    if len(sys.argv) < 4:
        print("用法: locate_loss.py <源文件> <人声轨> <成品>")
        return 2
    src, voc, out = (Path(p) for p in sys.argv[1:4])

    t_s, e_s = vocal_envelope(src)
    t_v, e_v = vocal_envelope(voc)
    t_o, e_o = vocal_envelope(out)

    n = min(len(e_s), len(e_v), len(e_o))
    t = t_s[:n]
    e_s, e_v, e_o = e_s[:n], e_v[:n], e_o[:n]

    def norm(x: np.ndarray) -> np.ndarray:
        # 各自按自身高分位归一化，比较「相对活跃度」而不是绝对音量
        ref = np.percentile(x, 95)
        return x / ref if ref > 0 else x

    ns, nv, no = norm(e_s), norm(e_v), norm(e_o)

    # 「有声」= 超过自身 95 分位的 25%
    act_s = ns > 0.25
    act_v = nv > 0.25
    act_o = no > 0.25

    print("=" * 72)
    print(f"帧数 {n}，每帧 {HOP/SR*1000:.1f} ms，覆盖 {t[-1]:.1f}s")
    print(f"源文件有声帧   : {act_s.sum():5d}  ({100*act_s.mean():.1f}%)")
    print(f"分离人声有声帧 : {act_v.sum():5d}  ({100*act_v.mean():.1f}%)")
    print(f"成品有声帧     : {act_o.sum():5d}  ({100*act_o.mean():.1f}%)")
    print("=" * 72)

    lost_in_demucs = act_s & ~act_v
    lost_in_seedvc = act_v & ~act_o
    print(f"\n源文件有声、但分离阶段就丢了 : {lost_in_demucs.sum()} 帧 "
          f"({lost_in_demucs.sum()*HOP/SR:.1f}s)")
    print(f"人声轨有声、但成品里丢了     : {lost_in_seedvc.sum()} 帧 "
          f"({lost_in_seedvc.sum()*HOP/SR:.1f}s)")

    def report(mask: np.ndarray, title: str, limit: int = 15) -> None:
        if not mask.any():
            print(f"\n{title}：无")
            return
        # 把连续帧合并成区间
        idx = np.flatnonzero(mask)
        groups = np.split(idx, np.flatnonzero(np.diff(idx) > 3) + 1)
        print(f"\n{title}（合并成 {len(groups)} 段，列前 {limit} 段）:")
        for g in groups[:limit]:
            a, b = t[g[0]], t[g[-1]]
            print(f"   {a:6.2f}s - {b:6.2f}s   持续 {b-a:5.2f}s")

    report(lost_in_demucs, "① 丢在 Demucs 分离环节")
    report(lost_in_seedvc, "② 丢在 Seed-VC 变声环节")

    # 成品相对人声轨的整体能量保留率
    keep = (e_o.sum() / e_v.sum()) if e_v.sum() > 0 else float("nan")
    print(f"\n成品的「人声频段总能量」占人声轨的比例: {keep*100:.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
