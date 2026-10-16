"""音频处理三件套的共用逻辑：分离 / 响度 / 字幕。

为什么三个放一个文件
--------------------
它们做的是三件不同的事，但共用同一批底层能力：

    人声分离    Demucs 拆人声/伴奏
    响度标准化  ffmpeg 的 loudnorm 滤镜
    字幕识别    Whisper 转写

三者的界面几乎一样（选文件 → 跑 → 出结果），
各写一套 UI 要复制三遍。所以逻辑集中在这里，界面只负责收集参数。

三个工具各自也是独立窗口（在 tools/audio/ 下），互不影响。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import paths        # noqa: E402

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
AUDIO_EXT = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus", ".aiff"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".ts", ".m4v", ".wmv"}

# huggingface / torch 的缓存路径必须在**导入 transformers 之前**就设好 ——
# 它们是在 import 时读取环境变量并固化的，之后再改环境变量已经晚了。
# 不设的话会去写 C:\Users\<用户>\.cache\huggingface，那里没权限（WinError 5）。
_HF_CACHE = str(paths.SEEDVC_CHECKPOINTS)
os.environ.setdefault("HF_HOME", _HF_CACHE)
os.environ.setdefault("HF_HUB_CACHE", _HF_CACHE)
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", _HF_CACHE)
os.environ.setdefault("TRANSFORMERS_CACHE", _HF_CACHE)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("TORCH_HOME", str(paths.MODEL_STORE / "torch"))
os.environ.setdefault("MPLCONFIGDIR", str(paths.LOGS / "mplconfig"))


def find_inputs(paths_in: list[Path], *, want_video: bool = False) -> list[Path]:
    """把用户选的东西展开成真正的文件列表（支持选文件夹）。"""
    allow = VIDEO_EXT if want_video else (AUDIO_EXT | VIDEO_EXT)
    out: list[Path] = []
    for p in paths_in:
        if p.is_dir():
            out += [f for f in sorted(p.rglob("*"))
                    if f.is_file() and f.suffix.lower() in allow]
        elif p.is_file():
            out.append(p)
    # 去重并保持顺序
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def run_capture(cmd: list[str], cwd: Path | None = None,
                env: dict | None = None) -> tuple[int, str]:
    """跑子进程，返回 (退出码, 输出)。用于不需要实时进度的场合。"""
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except OSError as exc:
        return -1, str(exc)


def stream(cmd: list[str], cwd: Path | None = None, env: dict | None = None):
    """跑子进程并逐行产出输出。"""
    proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            bufsize=1, creationflags=NO_WINDOW)
    assert proc.stdout is not None
    for line in proc.stdout:
        yield line.rstrip()
    proc.wait()
    yield f"__EXIT__{proc.returncode}"


# =========================================================================== #
# 一、人声分离（Demucs）
# =========================================================================== #
DEMUCS_MODEL = "htdemucs"


def std_env() -> dict:
    """子进程要用的环境变量。

    踩过的坑：这些工具默认往 `C:\\Users\\<用户>\\.cache\\torch` 和
    `\\.cache\\huggingface` 写模型权重，而那两处**没有写权限**
    （会报 WinError 5 拒绝访问，表现为"分离失败"「Whisper 加载失败」）。
    所以必须显式把它们指到项目里可写的位置。
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    # 变声引擎的 checkpoints 目录里已经下好了 whisper 和一堆 HF 模型，
    # 复用它们省下载。注意 HF_HOME 会再往下拼一个 "hub" 子目录，
    # 而权重实际躺在 checkpoints\ 根下，所以这里指到 checkpoints 本身，
    # 同时显式把 HF_HUB_CACHE 也指过去。
    env["HF_HOME"] = str(paths.SEEDVC_CHECKPOINTS)
    env["HF_HUB_CACHE"] = str(paths.SEEDVC_CHECKPOINTS)
    env["HUGGINGFACE_HUB_CACHE"] = str(paths.SEEDVC_CHECKPOINTS)
    env["TRANSFORMERS_CACHE"] = str(paths.SEEDVC_CHECKPOINTS)
    # 关掉旧缓存迁移：它会去读/写 C:\Users\<用户>\.cache\huggingface，
    # 而那里没有写权限（WinError 5）。
    env["TRANSFORMERS_OFFLINE"] = env.get("TRANSFORMERS_OFFLINE", "0")
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["TORCH_HOME"] = str(paths.MODEL_STORE / "torch")
    env["MPLCONFIGDIR"] = str(paths.LOGS / "mplconfig")
    return env


def apply_std_env() -> None:
    """把上面那套写进**当前进程**的环境。

    whisper 是通过 transformers 在当前进程里加载的（不像 demucs 是子进程），
    所以在调它之前必须先把环境变量设好。
    """
    os.environ.update(std_env())


def separate(input_audio: Path, out_root: Path, model: str = DEMUCS_MODEL,
             two_stems: bool = True):
    """把音频拆成人声 + 伴奏。

    产出（Demucs 自己决定目录结构）：
        out_root\\<model>\\<文件名>\\vocals.wav
        out_root\\<model>\\<文件名>\\no_vocals.wav
    """
    out_root.mkdir(parents=True, exist_ok=True)
    # 不要传 --filename：那个模板必须带扩展名，写错会报
    # "Invalid suffix for path"。用 Demucs 的默认布局
    # （<out>/<model>/<音频名>/vocals.wav），再用 rglob 找结果，最稳。
    cmd = [str(paths.VOICE_PY), "-m", "demucs.separate",
           "-n", model, "-o", str(out_root)]
    if two_stems:
        cmd.append("--two-stems=vocals")
    cmd.append(str(input_audio))

    env = std_env()

    tag = input_audio.stem
    vocals = out_root / model / tag / "vocals.wav"
    accomp = out_root / model / tag / "no_vocals.wav"

    if vocals.exists() and (accomp.exists() or not two_stems):
        yield "分离结果已存在，跳过"
        yield f"__DONE__{vocals}|{accomp if accomp.exists() else ''}"
        return

    tail: list[str] = []
    for line in stream(cmd, cwd=ROOT, env=env):
        tail.append(line)
        if len(tail) > 60:
            tail.pop(0)
        yield line

    if not vocals.exists():
        # 文件名可能被 Demucs 改过（比如含中文/空格），全局找一遍
        found = list(out_root.rglob("vocals.wav"))
        if found:
            vocals = found[0]
            accomp = vocals.parent / "no_vocals.wav"

    if vocals.exists():
        yield f"__DONE__{vocals}|{accomp if accomp.exists() else ''}"
    else:
        useful = [ln for ln in tail
                  if any(k in ln for k in ("Error", "error", "Traceback",
                                           "Permission", "No such", "Failed"))]
        hint = useful[-1][:200] if useful else " ".join(tail[-2:])[:200]
        yield f"__FAIL__分离失败：{hint}"


# =========================================================================== #
# 二、响度标准化（ffmpeg loudnorm）
# =========================================================================== #
# 各平台的常见目标响度（LUFS）。数值参考各平台的公开规范。
LOUDNESS_TARGETS = {
    "B站 / 抖音": -14.0,
    "YouTube": -14.0,
    "播客（单声道）": -16.0,
    "广播（EBU R128）": -23.0,
    "音乐流媒体（Spotify）": -14.0,
    "保守（不压动态）": -18.0,
}


def _probe_loudness(src: Path) -> dict | None:
    """先用一次扫描测出当前响度，供 loudnorm 的第二遍使用。

    ffmpeg 的 loudnorm 是两遍式的：第一遍测量、第二遍按测量结果处理。
    只跑一遍（单遍模式）也能用，但动态范围会处理得粗糙些。
    """
    code, out = run_capture([
        str(paths.FFMPEG), "-hide_banner", "-i", str(src),
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-",
    ])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def normalize_loudness(src: Path, dst: Path, target_lufs: float = -14.0,
                       true_peak: float = -1.0, two_pass: bool = True):
    """把响度标准化到目标值。产出 dst。

    踩过的坑
    --------
    ffmpeg 的 loudnorm 有个容易踩的陷阱：

      · `linear=true`（线性模式，最保真）**只有目标 LRA ≥ 素材实际 LRA 时才生效**
      · 否则它会**静默降级成 Dynamic 模式** —— 结果就是"跑完了但没对齐"
        （实测：目标 -14，出来 -17.8）

    所以这里**先测一次拿到实际 LRA**，再取 `max(LRA目标, 实际LRA)` 传回去。
    这样 linear 模式才能真正启用，响度也才对得上。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    # 目标 LRA：默认 11，但绝不能小于素材本身的动态范围，否则 linear 会失效
    target_lra = 11.0
    meas: dict | None = None

    if two_pass:
        yield f"测量当前响度（目标 {target_lufs:g} LUFS）…"
        meas = _probe_loudness(src)
        if meas:
            try:
                cur_i = float(meas["input_i"])
                cur_lra = float(meas["input_lra"])
            except (KeyError, ValueError):
                meas = None
            else:
                yield (f"  当前 {cur_i:.1f} LUFS，"
                       f"动态范围 {cur_lra:.1f} LU，峰值 {float(meas['input_tp']):.1f} dBTP")
                # 关键：目标 LRA 不能低于实际 LRA
                target_lra = max(target_lra, cur_lra)
                if target_lra > 11.0:
                    yield f"  素材动态较大，LRA 目标放宽到 {target_lra:.1f} 以保证线性处理"
        if meas is None:
            yield "  测量失败，改用单遍模式"

    if meas:
        af = (
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={target_lra}"
            f":measured_I={meas['input_i']}"
            f":measured_TP={meas['input_tp']}"
            f":measured_LRA={meas['input_lra']}"
            f":measured_thresh={meas['input_thresh']}"
            f":offset={meas.get('target_offset', 0)}"
            f":linear=true:print_format=summary"
        )
    else:
        af = (f"loudnorm=I={target_lufs}:TP={true_peak}:LRA={target_lra}"
              f":print_format=summary")

    cmd = [str(paths.FFMPEG), "-y", "-hide_banner", "-i", str(src),
           "-af", af, "-ar", "48000", str(dst)]
    for line in stream(cmd, cwd=ROOT):
        yield line

    if not (dst.exists() and dst.stat().st_size > 1000):
        yield "__FAIL__没有产出文件"
        return

    # 复查一次，确认真的到位了（差得远就明确告诉用户，而不是假装成功）
    check = _probe_loudness(dst)
    if check:
        try:
            got = float(check["input_i"])
        except (KeyError, ValueError):
            got = float("nan")
        diff = abs(got - target_lufs)
        yield f"  复查：{got:.1f} LUFS（差 {diff:.1f}）"
        if diff > 1.0:
            yield (f"  注意：和目标差 {diff:.1f} LU。loudnorm 需要足够长的音频"
                   f"才能测准（建议 30 秒以上），太短的片段偏差会比较大。")
    yield f"__DONE__{dst}"


# =========================================================================== #
# 三、字幕识别（Whisper）
# =========================================================================== #
class Transcriber:
    """Whisper 常驻转写器（模型只加载一次）。"""

    _cache: dict[str, "Transcriber"] = {}

    def __init__(self, size: str = "small", device: str = "cuda") -> None:
        self.size = size
        self.device = device
        self._pipe = None

    @classmethod
    def get(cls, size: str = "small") -> "Transcriber":
        if size not in cls._cache:
            cls._cache[size] = cls(size)
        return cls._cache[size]

    def load(self) -> tuple[bool, str]:
        if self._pipe is not None:
            return True, "已加载"
        try:
            # 必须在 import/加载之前设好，否则 transformers 会去写
            # C:\Users\<用户>\.cache\huggingface（那里没权限，报 WinError 5）
            apply_std_env()
            import torch
            from transformers import pipeline
            self._pipe = pipeline(
                "automatic-speech-recognition",
                model=f"openai/whisper-{self.size}",
                device=0 if (torch.cuda.is_available() and self.device == "cuda") else -1,
                chunk_length_s=30,
                return_timestamps=True,          # 要时间戳才能生成 SRT
            )
            return True, "加载完成"
        except Exception as exc:                 # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    def transcribe(self, wav: Path, language: str = "chinese"):
        """转写一个已经解码成 16k 单声道的 numpy 数组。"""
        import numpy as np
        import soundfile as sf
        audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != 16000:
            import librosa
            audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
        return self._pipe({"array": np.asarray(audio, dtype=np.float32),
                           "sampling_rate": 16000},
                          generate_kwargs={"language": language, "task": "transcribe"})


def _srt_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms, s = 0, s + 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# Whisper 遇到噪声/非语音时会「幻觉」出训练数据里的字幕水印（实测过：
# 一段合成音频被转成「字幕by索兰娅」）。这类结果必须挡掉，
# 否则字幕是错的、训练标注是脏的。
HALLUCINATION_PATTERNS = [
    "字幕", "subtitle", "sub by", "subtitle by", "transcript by",
    "请不吝点赞", "订阅", "转发", "打赏", "明镜与点点",
    "thanks for watching", "thank you for watching",
    "please subscribe", "amara.org", "www.", ".com", "http",
]


def looks_hallucinated(text: str) -> tuple[bool, str]:
    """判断一段转写是不是 Whisper 的幻觉。返回 (是否幻觉, 原因)。"""
    if not text:
        return True, "空"
    low = text.lower()
    for pat in HALLUCINATION_PATTERNS:
        if pat in low:
            return True, f"含字幕水印词「{pat}」"
    if re.search(r"[A-Za-z]{3,}", text) and re.search(r"[\u4e00-\u9fff]", text):
        if len(text) < 25:
            return True, "中英混杂的短句，疑似水印"
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", text):
        return True, "没有实际文字"
    return False, ""


def to_srt(chunks: list[dict]) -> str:
    """把 Whisper 的 chunks 转成 SRT 文本。"""
    out = []
    for i, c in enumerate(chunks, 1):
        ts = c.get("timestamp") or (0, 0)
        start = ts[0] if ts[0] is not None else 0
        end = ts[1] if ts[1] is not None else start + 2
        text = (c.get("text") or "").strip()
        if not text:
            continue
        out.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    return "\n".join(out)


def transcribe_file(src: Path, out_dir: Path, size: str = "small",
                    language: str = "chinese", want_srt: bool = True,
                    want_txt: bool = True):
    """转写单个音频/视频，产出 .srt 和 .txt。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    # 先统一转成 16k 单声道 wav（Whisper 要的格式，也顺便支持视频输入）
    tmp = out_dir / "_tmp_16k.wav"
    yield "转成 16kHz 单声道…"
    code, out = run_capture([
        str(paths.FFMPEG), "-y", "-hide_banner", "-i", str(src),
        "-ac", "1", "-ar", "16000", str(tmp),
    ])
    if code != 0 or not tmp.exists():
        yield f"__FAIL__音频解码失败：{out[-300:]}"
        return

    tr = Transcriber.get(size)
    ok, msg = tr.load()
    if not ok:
        tmp.unlink(missing_ok=True)
        yield f"__FAIL__Whisper 加载失败：{msg}"
        return
    yield "Whisper 已就绪，开始识别…"

    try:
        res = tr.transcribe(tmp, language=language)
    except Exception as exc:                     # noqa: BLE001
        tmp.unlink(missing_ok=True)
        yield f"__FAIL__识别失败：{type(exc).__name__}: {exc}"
        return
    finally:
        tmp.unlink(missing_ok=True)

    chunks = res.get("chunks") or []
    text = (res.get("text") or "").strip()

    # 挡掉 Whisper 的幻觉（噪声/纯音乐会被转成字幕水印）
    bad, reason = looks_hallucinated(text)
    if bad:
        yield f"识别结果疑似幻觉，已丢弃（{reason}）"
        yield f"  内容：{text[:60]!r}"
        yield ("__FAIL__这段音频可能是纯音乐、环境音或静音，"
               "Whisper 没有识别出真实语音")
        return

    made = []
    if want_srt and chunks:
        p = out_dir / f"{src.stem}.srt"
        p.write_text(to_srt(chunks), encoding="utf-8")
        made.append(p)
        yield f"已写 {p.name}（{len(chunks)} 条字幕）"
    if want_txt and text:
        p = out_dir / f"{src.stem}.txt"
        p.write_text(text, encoding="utf-8")
        made.append(p)
        yield f"已写 {p.name}（{len(text)} 字）"

    if made:
        yield f"__DONE__{'|'.join(str(p) for p in made)}"
    else:
        yield "__FAIL__没有识别出内容（可能是纯音乐或静音）"


# =========================================================================== #
def selftest(which: str) -> int:
    """三个工具共用自检：检查底层能力在不在。"""
    print("=" * 60)
    print(f" 自检：{which}")
    print("=" * 60)
    ok = True

    print(f"  ffmpeg      : {'OK' if paths.FFMPEG.exists() else '缺失'}")
    ok &= paths.FFMPEG.exists()
    print(f"  变声环境    : {'OK' if paths.VOICE_PY.exists() else '缺失'}")

    if which == "separate":
        try:
            import demucs                                     # noqa: F401
            from demucs.pretrained import get_model           # noqa: F401
            print("  demucs      : OK")
        except ImportError as exc:
            print(f"  demucs      : 缺失 ({exc})")
            ok = False
    elif which == "loudness":
        code, out = run_capture([str(paths.FFMPEG), "-hide_banner", "-filters"])
        has = "loudnorm" in out
        print(f"  loudnorm    : {'OK' if has else '这个 ffmpeg 不带 loudnorm'}")
        ok &= has
    elif which == "subtitle":
        try:
            import transformers                              # noqa: F401
            print(f"  transformers: OK ({transformers.__version__})")
        except ImportError as exc:
            print(f"  transformers: 缺失 ({exc})")
            ok = False

    print("=" * 60)
    print(" 自检" + ("通过" if ok else "失败"))
    print("=" * 60)
    return 0 if ok else 1
