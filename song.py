"""整曲换声：歌曲 -> 人声+伴奏 -> 变声 -> 混回完整歌曲。

为什么需要这个脚本
------------------
Seed-VC 只在人声上训练，把带伴奏的歌喂进去，伴奏会被当成噪声（丢掉或变杂音）。
所以「换掉一首歌的主唱」必须分三步：

    1. 分离   Demucs 把歌曲拆成 vocals.wav + no_vocals.wav
    2. 变声   Seed-VC 只处理 vocals.wav，换成参考音频的音色
    3. 混音   变声后的人声 + 原伴奏 -> 完整歌曲

用法
----
    python song.py --song "我的歌.mp3" --voice "音色参考.wav"
    python song.py --song 歌.mp3 --voice 参考.wav --mode v2 --vocal-gain 1.0
    python song.py --song 歌.mp3 --voice 参考.wav --keep-stems   # 保留分离出的音轨

输出仍在 io\\output\\，文件名带 song_ 前缀。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
FFMPEG = ROOT / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "bin" / "ffprobe.exe"
CONVERT = ROOT / "convert.py"
STAGE = ROOT / "io" / "_stage"
SEP_ROOT = ROOT / "io" / "_sep"
# 自动化运行（DSH_SANDBOX=1）时产物落到沙箱，绝不碰用户真正的成品目录
OUTDIR = ROOT / "io" / ("_sandbox" if os.environ.get("DSH_SANDBOX") == "1" else "output")
MODELS = ROOT / "models"

DEMUCS_MODEL = "htdemucs"


def die(msg: str) -> "None":
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(2)


def run(cmd: list[str], env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, cwd=str(cwd) if cwd else None)


def slug(text: str, fallback: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^A-Za-z0-9._-]+", "_", norm).strip("._-")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    return f"{norm[:40]}_{digest}" if norm else f"{fallback}_{digest}"


def probe(path: Path) -> dict:
    if not FFPROBE.exists():
        return {}
    r = run([str(FFPROBE), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    try:
        meta = json.loads(r.stdout)
    except Exception:
        return {}
    a = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {"sample_rate": a.get("sample_rate"), "channels": a.get("channels"),
            "duration": float(meta.get("format", {}).get("duration") or 0.0)}


def child_env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    # Keep every model cache inside the project -- the user profile cache is not
    # writable here, and demucs would otherwise try to use it.
    env["TORCH_HOME"] = str(MODELS / "torch")
    env["HF_HOME"] = str(MODELS / "hfcache")
    env["MPLCONFIGDIR"] = str(ROOT / "logs" / "mplconfig")
    return env


# --------------------------------------------------------------------------- #
# step 1: separate
# --------------------------------------------------------------------------- #
def separate(song: Path, tag: str, env: dict) -> tuple[Path, Path]:
    """Run demucs; return (vocals_path, accompaniment_path)."""
    out = SEP_ROOT / tag
    vocals = out / DEMUCS_MODEL / tag / "vocals.wav"
    accomp = out / DEMUCS_MODEL / tag / "no_vocals.wav"
    if vocals.exists() and accomp.exists():
        print("  分离结果已存在，跳过 demucs")
        return vocals, accomp

    out.mkdir(parents=True, exist_ok=True)
    cmd = [str(VENV_PY), "-m", "demucs.separate",
           "-n", DEMUCS_MODEL,
           "--two-stems=vocals",
           "-o", str(out), str(song)]
    print("  $ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        if len(tail) > 30:
            tail.pop(0)
        if "%" in line or "Separating" in line or "Separated" in line:
            print("    " + line.replace("\r", " ").strip()[-110:], flush=True)
    if proc.wait() != 0:
        print("\n".join(tail), file=sys.stderr)
        die("demucs 分离失败")

    if not vocals.exists():
        found = list(out.rglob("vocals.wav"))
        if not found:
            die(f"demucs 没产出 vocals.wav，看 {out}")
        vocals = found[0]
        accomp = vocals.parent / "no_vocals.wav"
    return vocals, accomp


# --------------------------------------------------------------------------- #
# step 3: mix
# --------------------------------------------------------------------------- #
def read_audio(path: Path) -> tuple[np.ndarray, int]:
    """Return (samples as float32 [frames, channels], sample_rate)."""
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data, sr


def resample_to(data: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to:
        return data
    import librosa
    # resample per channel (librosa is mono-oriented)
    chans = [librosa.resample(data[:, c], orig_sr=sr_from, target_sr=sr_to)
             for c in range(data.shape[1])]
    n = min(len(c) for c in chans)
    return np.stack([c[:n] for c in chans], axis=1).astype(np.float32)


def mix(vocals: Path, accomp: Path, out: Path, vocal_gain: float,
        inst_gain: float, target_peak: float) -> dict:
    v, sr_v = read_audio(vocals)
    a, sr_a = read_audio(accomp)

    # Work at the accompaniment's rate (usually 44.1k) so nothing is upsampled.
    v = resample_to(v, sr_v, sr_a)
    sr = sr_a

    # Force matching shape: V is often mono (Seed-VC outputs mono), A is stereo.
    if a.shape[1] == 1 and v.shape[1] > 1:
        a = np.repeat(a, v.shape[1], axis=1)
    if v.shape[1] == 1 and a.shape[1] > 1:
        v = np.repeat(v, a.shape[1], axis=1)

    n = min(len(v), len(a))
    if abs(len(v) - len(a)) > sr * 0.05:
        print(f"  ! 长度不一致（人声 {len(v)/sr:.3f}s vs 伴奏 {len(a)/sr:.3f}s），"
              f"按较短的 {n/sr:.3f}s 对齐")
    v, a = v[:n], a[:n]

    mixed = v * vocal_gain + a * inst_gain

    peak = float(np.abs(mixed).max())
    if peak > 0:
        mixed = mixed * (target_peak / peak)

    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out), mixed, sr, subtype="PCM_16")

    def rms(x: np.ndarray) -> float:
        return float(np.sqrt((x.astype(np.float64) ** 2).mean()))

    return {"sample_rate": sr, "seconds": n / sr, "channels": mixed.shape[1],
            "vocal_rms": rms(v), "inst_rms": rms(a), "out_rms": rms(mixed),
            "out_peak": float(np.abs(mixed).max())}


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="整曲换声：分离人声 -> 变声 -> 混回完整歌曲",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--song", required=True, help="完整歌曲文件（含伴奏）")
    ap.add_argument("--voice", required=True, help="音色参考音频")
    ap.add_argument("--mode", choices=["v1", "v2", "singing"], default="singing",
                    help="变声模式，整曲换声默认 singing（44.1kHz，音质最高）")
    ap.add_argument("--steps", type=int, default=None, help="扩散步数，默认 30")
    ap.add_argument("--cfg-rate", type=float, default=None, help="跟随参考音色的强度 0~1")
    ap.add_argument("--similarity-cfg-rate", type=float, default=None, help="仅 v2")
    ap.add_argument("--vocal-gain", type=float, default=1.0, help="人声在混音里的音量")
    ap.add_argument("--inst-gain", type=float, default=1.0, help="伴奏在混音里的音量")
    ap.add_argument("--target-peak", type=float, default=0.97, help="混音后峰值归一化目标")
    ap.add_argument("--out", default=None, help="输出路径")
    ap.add_argument("--keep-stems", action="store_true", help="保留分离出的人声/伴奏")
    args = ap.parse_args()

    song = Path(args.song).expanduser().resolve()
    voice = Path(args.voice).expanduser().resolve()
    for label, p in (("--song", song), ("--voice", voice)):
        if not p.exists():
            die(f"{label} 不存在：{p}")

    py = VENV_PY if VENV_PY.exists() else Path(sys.executable)
    env = child_env()
    STAGE.mkdir(parents=True, exist_ok=True)
    OUTDIR.mkdir(parents=True, exist_ok=True)

    tag = slug(song.stem, "song")
    voice_tag = slug(voice.stem, "voice")
    t_all = time.time()

    print("=" * 70)
    print(" 整曲换声：三步流程")
    print("=" * 70)
    info = probe(song)
    print(f" 歌曲    : {song.name}  ({info.get('duration', 0):.1f}s / "
          f"{info.get('sample_rate')}Hz / {info.get('channels')}ch)")
    print(f" 音色参考: {voice.name}")
    print(f" 模式    : {args.mode}")
    print(f" 歌曲哈希: {tag}")

    # ---- 1. separate -------------------------------------------------------
    print(f"\n[1/3] 分离人声与伴奏（Demucs {DEMUCS_MODEL}）...")
    t0 = time.time()
    vocals, accomp = separate(song, tag, env)
    print(f"  人声: {vocals.name}  伴奏: {accomp.name}   ({time.time()-t0:.1f}s)")

    # ---- 2. convert the vocal stem ----------------------------------------
    print(f"\n[2/3] 给分离出的人声变声（Seed-VC {args.mode}）...")
    conv_dir = STAGE / f"song_conv_{tag}"
    cmd = [str(py), str(CONVERT),
           "--source", str(vocals), "--target", str(voice),
           "--mode", args.mode, "--out", str(conv_dir / "converted_vocals.wav")]
    if args.steps is not None:
        cmd += ["--steps", str(args.steps)]
    if args.cfg_rate is not None:
        cmd += ["--cfg-rate", str(args.cfg_rate)]
    if args.similarity_cfg_rate is not None:
        cmd += ["--similarity-cfg-rate", str(args.similarity_cfg_rate)]
    cmd += ["--keep-stage"]

    t0 = time.time()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)
        if line.startswith(("[1/3]", "[2/3]", "[3/3]", " DONE", "[ERROR]")):
            print("  " + line.strip(), flush=True)
    if proc.wait() != 0:
        print("\n".join(tail[-25:]), file=sys.stderr)
        die("变声步骤失败")
    converted = conv_dir / "converted_vocals.wav"
    if not converted.exists():
        die(f"没有找到变声后的人声：{converted}")
    print(f"  变声完成 ({time.time()-t0:.1f}s)")

    # ---- 3. mix back -------------------------------------------------------
    print(f"\n[3/3] 把变声后的人声混回伴奏...")
    final = Path(args.out).expanduser().resolve() if args.out else \
        OUTDIR / f"song_{tag}_voice-{voice_tag}_{args.mode}.wav"
    stats = mix(converted, accomp, final, args.vocal_gain, args.inst_gain, args.target_peak)

    print("\n" + "=" * 70)
    print(f" 完成！总耗时 {time.time()-t_all:.1f}s")
    print(f" 成品: {final}")
    print(f"   {stats['seconds']:.1f}s / {stats['sample_rate']}Hz / {stats['channels']}ch / "
          f"{final.stat().st_size/1e6:.1f}MB")
    print(f"   人声 RMS {stats['vocal_rms']:.4f} | 伴奏 RMS {stats['inst_rms']:.4f} | "
          f"混合 RMS {stats['out_rms']:.4f} | 峰值 {stats['out_peak']:.3f}")
    print("=" * 70)

    if not args.keep_stems:
        shutil.rmtree(SEP_ROOT / tag, ignore_errors=True)
        shutil.rmtree(conv_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
