"""训练数据集准备：扫描素材 → 检查质量 → 自动标注文字 → 生成 .list 文件。

为什么需要这个
--------------
GPT-SoVITS 的训练要求每段音频都配一句它说的**文字**（用于提取音素特征）。
而游戏解包的配音只有音频文件，没有文字。

所以这里用 Whisper 自动转写。方法：
    Whisper 是**一句话一个结果**的模型，不适合一次性吃几十个文件，
    所以逐个文件转录，但**模型只加载一次**常驻显存。

素材放哪
--------
    data\\datasets\\<角色名>\\          ← 直接放音频
    data\\datasets\\<角色名>\\raw\\     ← 也可以（会被递归扫描）

输出
----
    data\\datasets\\_prepared\\<角色名>\\切片后的 wav
    data\\datasets\\_prepared\\<角色名>.list     ← 训练用的标注文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from shared import paths, training_spec          # noqa: E402

DATASETS = paths.VOICE_DATASETS
PREPARED = DATASETS / "_prepared"

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wma", ".opus"}

# Whisper 用的采样率
TARGET_SR = 32000
SLICE_MIN = 2.0        # 太短的片段训练价值低
SLICE_MAX = 12.0       # 太长的片段不好对齐
SILENCE_DB = -40.0     # 低于这个视为静音


# --------------------------------------------------------------------------- #
# 音频读取（统一转成 32kHz 单声道 —— GPT-SoVITS 训练要求）
# --------------------------------------------------------------------------- #
def load_audio_32k(path: Path) -> np.ndarray:
    """用 ffmpeg 解码成 32kHz 单声道 float32。比 librosa 稳（支持更多格式）。"""
    import subprocess
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    cmd = [str(paths.FFMPEG), "-v", "error", "-i", str(path),
           "-ac", "1", "-ar", str(TARGET_SR), "-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=creation)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"ffmpeg 解码失败: {(r.stderr or b'')[:200]!r}")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def write_wav(path: Path, audio: np.ndarray, sr: int = TARGET_SR) -> None:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(pcm.tobytes())


def trim_silence(audio: np.ndarray, sr: int = TARGET_SR,
                 top_db: float = SILENCE_DB) -> np.ndarray:
    """去掉首尾静音 —— 训练数据里留着静音是浪费。"""
    if audio.size == 0:
        return audio
    frame = max(int(sr * 0.02), 1)
    n = audio.size // frame
    if n < 2:
        return audio
    rms = np.sqrt((audio[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    thresh = 10 ** (top_db / 20.0)
    voiced = np.flatnonzero(rms > thresh)
    if voiced.size == 0:
        return audio
    return audio[voiced[0] * frame: (voiced[-1] + 1) * frame]


def slice_long(audio: np.ndarray, sr: int = TARGET_SR,
               max_len: float = SLICE_MAX, min_len: float = SLICE_MIN) -> list[np.ndarray]:
    """把过长的音频切成 <= max_len 的片段（在能量最低处切，避免切断字）。

    短于 min_len 的直接丢弃 —— 训练价值低还会拖慢对齐。
    """
    total = audio.size / sr
    if total < min_len:
        return []
    if total <= max_len:
        return [audio]

    out: list[np.ndarray] = []
    pos = 0
    step = int(max_len * sr)
    while pos < audio.size:
        end = min(pos + step, audio.size)
        if audio.size - end < min_len * sr:      # 尾巴太短就并进上一段
            end = audio.size
        seg = audio[pos:end]
        if seg.size / sr >= min_len:
            out.append(seg)
        pos = end
    return out


# --------------------------------------------------------------------------- #
# 自动标注
# --------------------------------------------------------------------------- #
class Transcriber:
    """Whisper 转写器：模型只加载一次，逐个文件用。"""

    def __init__(self, model_name: str = "small", device: str = "cuda") -> None:
        self.model_name = model_name
        self.device = device
        self._model = None
        self._load_error = ""

    def load(self) -> bool:
        """优先用本地已缓存的 Whisper（变声工具已经下过 whisper-small）。"""
        if self._model is not None:
            return True
        try:
            import os
            os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
            # 复用变声工具下载好的 whisper 权重，避免重复下载 1GB
            os.environ.setdefault("HF_HOME", str(paths.SEEDVC_CHECKPOINTS))
            os.environ.setdefault("TRANSFORMERS_CACHE", str(paths.SEEDVC_CHECKPOINTS))

            import torch
            from transformers import pipeline
            self._model = pipeline(
                "automatic-speech-recognition",
                model=f"openai/whisper-{self.model_name}",
                device=0 if (self.device == "cuda" and torch.cuda.is_available()) else -1,
                chunk_length_s=30,
            )
            return True
        except Exception as exc:                 # noqa: BLE001
            self._load_error = f"{type(exc).__name__}: {exc}"
            return False

    def transcribe(self, audio: np.ndarray, sr: int = TARGET_SR) -> str:
        if self._model is None and not self.load():
            raise RuntimeError(f"Whisper 加载失败：{self._load_error}")
        res = self._model({"array": audio.astype(np.float32), "sampling_rate": sr},
                          generate_kwargs={"language": "chinese", "task": "transcribe"})
        text = (res.get("text") or "").strip()
        return clean_transcript(text)


def clean_transcript(text: str) -> str:
    """清理转写结果：去掉括号注释、多余空白、纯标点。"""
    text = re.sub(r"[\(（\[【].*?[\)）\]】]", "", text)      # 去 (笑声) 之类
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip("，。、！？,.!?;；:：")
    return text


# Whisper 遇到噪声/非语音时会「幻觉」出训练数据里的字幕水印。
# 这些词一旦进了标注，就会把垃圾学进模型，所以必须挡掉。
HALLUCINATION_PATTERNS = [
    "字幕", "subtitle", "sub by", "subtitle by",
    "请不吝点赞", "订阅", "转发", "打赏", "明镜与点点",
    "thanks for watching", "thank you for watching",
    "please subscribe", "amara.org", "transcription by",
    "www.", ".com", "http",
]


def looks_hallucinated(text: str) -> tuple[bool, str]:
    """判断一段转写是不是 Whisper 的幻觉。返回 (是否幻觉, 原因)。"""
    if not text:
        return True, "空"
    low = text.lower()
    for pat in HALLUCINATION_PATTERNS:
        if pat in low:
            return True, f"含字幕水印词「{pat}」"
    # 有水印特征：中英混杂的固定短语
    if re.search(r"[A-Za-z]{3,}", text) and re.search(r"[\u4e00-\u9fff]", text):
        if len(text) < 25:
            return True, "中英混杂的短句，疑似水印"
    # 纯符号/纯数字
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
        return True, "没有实际文字"
    # 太短：一两个字很难构成有效训练样本
    if len(text) < 4:
        return True, f"太短（{len(text)} 字）"
    return False, ""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def prepare(character: str, *, asr_model: str = "small", force: bool = False,
            verbose: bool = True) -> dict:
    """准备一个角色的训练数据。返回统计信息。"""
    src_dir = DATASETS / character
    if not src_dir.is_dir():
        raise FileNotFoundError(
            f"找不到素材目录：{src_dir}\n"
            f"请把同一个角色的配音放进这个文件夹（可以放子目录里）。")

    files = sorted(p for p in src_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in AUDIO_EXT
                   and PREPARED not in p.parents)
    if not files:
        raise FileNotFoundError(f"{src_dir} 里没找到音频文件")

    if verbose:
        print(f"素材目录：{src_dir}")
        print(f"找到 {len(files)} 个音频文件\n")

    out_dir = PREPARED / character
    if force and out_dir.exists():
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    transcriber = Transcriber(model_name=asr_model)
    if not transcriber.load():
        raise RuntimeError(f"Whisper 加载失败：{transcriber._load_error}")

    entries: list[tuple[str, str]] = []
    stats = {"input_files": len(files), "slices": 0, "skipped_short": 0,
             "failed": 0, "hallucinated": 0, "seconds": 0.0, "chars": 0}
    t0 = time.time()

    for i, f in enumerate(files, 1):
        try:
            audio = load_audio_32k(f)
        except Exception as exc:                 # noqa: BLE001
            if verbose:
                print(f"  [{i}/{len(files)}] 跳过 {f.name}：解码失败 {exc}")
            stats["failed"] += 1
            continue

        audio = trim_silence(audio)
        segs = slice_long(audio)
        if not segs:
            stats["skipped_short"] += 1
            if verbose:
                print(f"  [{i}/{len(files)}] 跳过 {f.name}：有效时长不足")
            continue

        for j, seg in enumerate(segs):
            name = f"{f.stem}" + (f"_{j:02d}" if len(segs) > 1 else "")
            wav_path = out_dir / f"{name}.wav"
            # 同一批重跑时，已有切片且不强制就复用，跳过耗时的转写
            if not wav_path.exists() or force:
                write_wav(wav_path, seg)
                try:
                    text = transcriber.transcribe(seg)
                except Exception as exc:          # noqa: BLE001
                    if verbose:
                        print(f"      转写失败 {name}：{exc}")
                    text = ""
            else:
                text = ""

            if not text:
                # 复用模式下去读上次的结果
                cached = _cached_text(character, name)
                text = cached
            if not text:
                if verbose:
                    print(f"      {name}：没有文字，跳过（空白/纯音乐？）")
                stats["failed"] += 1
                continue

            # 挡掉 Whisper 的幻觉水印 —— 放进去就是往训练数据里掺垃圾
            bad, reason = looks_hallucinated(text)
            if bad:
                stats["hallucinated"] += 1
                if verbose:
                    print(f"      {name}：疑似幻觉，已丢弃（{reason}）")
                # 把可疑结果也删掉，免得下次复用模式又捡回来
                try:
                    wav_path.unlink(missing_ok=True)
                except Exception:                # noqa: BLE001
                    pass
                continue

            entries.append((f"{name}.wav", text))
            stats["slices"] += 1
            stats["seconds"] += len(seg) / TARGET_SR
            stats["chars"] += len(text)

        if verbose and i % 10 == 0:
            print(f"  ...已处理 {i}/{len(files)} 个文件")

    # 写 .list（GPT-SoVITS 要求的格式：wav名|说话人|语言|文字）
    list_path = PREPARED / f"{character}.list"
    lines = [f"{n}|{character}|ZH|{t}" for n, t in entries]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _save_text_cache(character, dict(entries))

    stats["list_path"] = str(list_path)
    stats["wav_dir"] = str(out_dir)
    stats["elapsed"] = time.time() - t0
    stats["minutes"] = stats["seconds"] / 60

    if verbose:
        spec = training_spec.SOVITS
        verdict, _ = spec.length_verdict(stats["minutes"])
        print("\n" + "=" * 62)
        print(f" 角色     ：{character}")
        print(f" 输入文件 ：{stats['input_files']} 个")
        print(f" 有效切片 ：{stats['slices']} 段")
        print(f" 总时长   ：{stats['minutes']:.1f} 分钟")
        print(f" 总字数   ：{stats['chars']} 字")
        print(f" 跳过     ：{stats['skipped_short']} 太短 / {stats['failed']} 失败"
              f" / {stats['hallucinated']} 疑似幻觉水印")
        print(f" 耗时     ：{stats['elapsed']:.0f} 秒")
        print(f" 标注文件 ：{list_path}")
        if stats["hallucinated"]:
            print("\n ⚠ 有片段被判为 Whisper 幻觉（通常是噪声/纯音乐/多人说话），已丢弃。")
            print("   如果丢得很多，说明素材里语音太少或太吵，训练效果会差。")
        print(f"\n 素材量判断：{verdict}")
        print(f"（GPT-SoVITS 要求：最低 {spec.min_minutes:.0f} 分钟，"
              f"{spec.good_minutes:.0f} 分钟以上效果明显更好）")
        print("=" * 62)
    return stats


def _cache_path(character: str) -> Path:
    return PREPARED / f"{character}.texts.json"


def _save_text_cache(character: str, mapping: dict[str, str]) -> None:
    _cache_path(character).write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")


def _cached_text(character: str, name: str) -> str:
    p = _cache_path(character)
    if not p.exists():
        return ""
    try:
        return json.loads(p.read_text(encoding="utf-8")).get(f"{name}.wav", "")
    except Exception:                            # noqa: BLE001
        return ""


def list_characters() -> list[tuple[str, int, float]]:
    """列出 data\\datasets 下有哪些角色、各有多少文件。用于界面显示。"""
    out: list[tuple[str, int, float]] = []
    if not DATASETS.is_dir():
        return out
    for d in sorted(DATASETS.iterdir()):
        if not d.is_dir() or d.name == "_prepared":
            continue
        files = [p for p in d.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXT]
        if not files:
            continue
        total = sum(p.stat().st_size for p in files)
        out.append((d.name, len(files), total / 16000 / 60))   # 粗略分钟数
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="训练数据集准备 + 自动标注")
    ap.add_argument("--character", help="角色名（data\\datasets 下的文件夹名）")
    ap.add_argument("--asr", default="small", help="Whisper 模型规格 small/medium/large")
    ap.add_argument("--force", action="store_true", help="强制重做（忽略已有切片）")
    ap.add_argument("--list", action="store_true", help="只列出有哪些角色")
    args = ap.parse_args()

    paths.ensure_dirs()

    if args.list or not args.character:
        chars = list_characters()
        if not chars:
            print(f"还没有任何素材。\n请把同一个角色的配音放进：\n  {DATASETS}\\<角色名>\\")
            return 0
        print(f"素材目录：{DATASETS}\n")
        for name, n, approx in chars:
            print(f"  {name:20} {n:4} 个文件  约 {approx:.1f} 分钟（粗略）")
        return 0

    try:
        prepare(args.character, asr_model=args.asr, force=args.force)
    except Exception as exc:                     # noqa: BLE001
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
