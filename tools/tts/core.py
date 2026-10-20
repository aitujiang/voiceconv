"""GPT-SoVITS 合成器封装。

给工具箱提供干净的一层：给「参考音频 + 文字」→ 出「wav 路径」。

为什么要这一层
--------------
GPT-SoVITS 自带的入口（webui.py / api_v2.py）都绑着 gradio / fastapi 整套 Web 依赖，
为了它多装几百 MB 不值得 —— 工具箱有自己的 tkinter 界面。
所以这里直接用它真正干活的类：`GPT_SoVITS.TTS_infer_pack.TTS.TTS`。

两个必须注意的约束（都是 GPT-SoVITS 自身的设计决定的）
-----------------------------------------------------
1. **必须在它的仓库根目录下运行**：它内部大量使用
   `GPT_SoVITS/pretrained_models/...` 这样的相对路径。所以 load() 里会临时
   切换到仓库根，加载完再切回来。
2. **日语不可用**：日语要 pyopenjtalk，而它没有 Windows 预编译轮子。
   中文链路（chinese2 + G2PW）已验证可用。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# core.py 在 ...\voiceconv\tools\tts\ 下：
#   parents[0]=tools\tts  parents[1]=tools  parents[2]=voiceconv
ROOT = Path(__file__).resolve().parents[2]              # ...\voiceconv
ENGINE = ROOT / "tools" / "tts" / "engine"              # GPT-SoVITS 仓库根
PRETRAINED = ENGINE / "GPT_SoVITS" / "pretrained_models"

# v2final（GPT-SoVITS 推荐的默认版本）
T2S_CKPT = PRETRAINED / "gsv-v2final-pretrained" / "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
VITS_PTH = PRETRAINED / "gsv-v2final-pretrained" / "s2G2333k.pth"
BERT_DIR = PRETRAINED / "chinese-roberta-wwm-ext-large"
HUBERT_DIR = PRETRAINED / "chinese-hubert-base"


def prepare_env() -> None:
    """设置镜像与可写缓存目录（用户目录在本机不可写）。"""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hfcache"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(ROOT / "models" / "hfcache"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "logs" / "mplconfig"))


def preflight(t2s: Path | None = None, vits: Path | None = None) -> list[str]:
    """检查模型文件是否齐全，返回缺失项列表（空 = 可以跑）。"""
    t2s = Path(t2s) if t2s else T2S_CKPT
    vits = Path(vits) if vits else VITS_PTH
    missing = []
    for p in (ENGINE, t2s, vits):
        if not p.exists():
            missing.append(str(p))
    for d in (BERT_DIR, HUBERT_DIR):
        if not d.is_dir():
            missing.append(str(d))
    return missing


class TTSEngine:
    """常驻合成器：模型只加载一次，之后反复合成。"""

    def __init__(self, device: str = "cuda", version: str = "v2",
                 verbose: bool = True,
                 t2s: Path | str | None = None,
                 vits: Path | str | None = None) -> None:
        prepare_env()
        self.device = device
        self.version = version
        self.verbose = verbose
        # 自定义角色模型（.ckpt = GPT 阶段，.pth = SoVITS 阶段）；
        # 不传就用 v2final 预训练底模。
        self.t2s = Path(t2s) if t2s else T2S_CKPT
        self.vits = Path(vits) if vits else VITS_PTH
        self._tts = None
        self._sample_rate = 32000

    # ------------------------------------------------------------------ 加载 --
    def load(self) -> None:
        """加载模型。第一次要十几秒，之后走缓存。

        注意：这里会**永久改变进程的工作目录**到 GPT-SoVITS 仓库根，且不切回。
        原因是它的相对路径写法散布在「加载时」和「首次合成时」两个阶段：

            chinese2.py : model_dir="GPT_SoVITS/text/G2PWModel"
            onnx_api.py : if not os.path.exists(model_dir) -> 去下载

        G2PW 和 BERT 是在**第一次合成**时才真正加载的，如果那时工作目录已经被
        切回调用方，这些相对路径就会解析失败（表现为「模型文件明明在，却要重新下载」）。

        本模块是独立进程、且所有路径都写成绝对路径，所以常驻切换是安全的。
        """
        if self._tts is not None:
            return

        missing = preflight()
        if missing:
            raise FileNotFoundError(
                "模型文件不齐，先运行 download_tts_models.py：\n  " + "\n  ".join(missing))

        os.chdir(ENGINE)                       # 常驻，不切回 —— 见上面的说明
        for p in (str(ENGINE), str(ENGINE / "GPT_SoVITS")):
            if p not in sys.path:
                sys.path.insert(0, p)

        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

        config = TTS_Config({
            "custom": {
                "device": self.device,
                "is_half": self.device != "cpu",
                "version": self.version,
                "t2s_weights_path": str(self.t2s),
                "vits_weights_path": str(self.vits),
                "bert_base_path": str(BERT_DIR),
                "cnhuhbert_base_path": str(HUBERT_DIR),
            }
        })
        self._tts = TTS(config)

        if self.verbose:
            print(f"[tts] 模型加载完成（{self.version}, {self.device}），工作目录 {Path.cwd()}")

    # ------------------------------------------------------------------ 合成 --
    def synthesize(self, text: str, ref_audio: str | Path, out_path: str | Path,
                   prompt_text: str = "", *, lang: str = "zh",
                   top_k: int = 15, top_p: float = 1.0, temperature: float = 1.0,
                   speed: float = 1.0, split: str = "cut5",
                   seed: int = 42) -> Path:
        """用 ref_audio 的音色把 text 念出来，写到 out_path。

        prompt_text 是参考音频里说的内容。**留空也能用**（GPT-SoVITS 会自己
        从参考音频里识别），但填上它相似度会更稳。
        """
        self.load()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        inputs = {
            "text": text,
            "text_lang": lang,
            "ref_audio_path": str(ref_audio),
            "prompt_text": prompt_text,
            "prompt_lang": lang,
            "top_k": top_k,
            "top_p": top_p,
            "temperature": temperature,
            "text_split_method": split,
            "batch_size": 1,
            "speed_factor": speed,
            "return_fragment": False,
            "seed": seed,
        }

        t0 = time.time()
        result = None
        for item in self._tts.run(inputs):
            result = item                    # 非流式时最后一项即完整结果
        if result is None:
            raise RuntimeError("合成没有产出音频")

        sample_rate, audio = int(result[0]), result[1]
        self._sample_rate = sample_rate

        import numpy as np
        import soundfile as sf
        if audio.dtype != np.int16:
            audio = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        sf.write(str(out_path), audio, sample_rate)

        if self.verbose:
            print(f"[tts] 合成完成 {time.time()-t0:.1f}s  "
                  f"{len(audio)/sample_rate:.2f}s/{sample_rate}Hz  -> {out_path.name}")
        return out_path

    # ------------------------------------------------------------- 便捷方法 --
    def synthesize_text(self, text: str, ref_audio: str | Path,
                        out_path: str | Path, **kw) -> Path:
        return self.synthesize(text, ref_audio, out_path, **kw)


# --------------------------------------------------------------------------- #
# 命令行自检：不需要界面，直接验证合成链路
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    print("=" * 62)
    print(" GPT-SoVITS 链路自检")
    print("=" * 62)

    missing = preflight()
    if missing:
        print("模型缺失：")
        for m in missing:
            print("  " + m)
        return 1
    print("模型文件齐全")

    ref = ROOT / "io" / "test_voice1_reference.wav"
    if not ref.exists():
        print(f"缺少参考音频：{ref}")
        return 2

    eng = TTSEngine()
    out = ROOT / "io" / "_sandbox" / "tts_selftest.wav"
    eng.synthesize_text("你好，这是一段语音合成的测试。", ref, out)

    import soundfile as sf
    info = sf.info(str(out))
    ok = info.frames > 0 and info.samplerate > 8000
    print(f"产物：{out}")
    print(f"  {info.frames} 帧 / {info.frames/info.samplerate:.2f}s / {info.samplerate}Hz")
    print("自检" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
