"""RVC 变声的薄封装 —— 只包一层，推理逻辑全是 RVC 自己的。

为什么这么写
------------
用户的明确要求：**不要重写 RVC 的功能**，推理要用它原有的。

所以这个文件**不实现任何变声算法**，只做三件事：

    1. 摆好 RVC 需要的环境（工作目录、环境变量、sys.path）
    2. 调它的 `infer.vc.modules.VC` 类（这正是 WebUI 用的那套）
    3. 把结果落盘

一旦 RVC 内部改动，这里不用跟着改。

关键调用点（都是从 webui.py 抄的用法）
------------------------------------
    from infer.vc.modules import VC
    vc = VC(config)                                  # config 要有 device/is_half/路径
    vc.get_vc(pth_path)                              # 加载某个 .pth 模型
    vc.vc_single(sid, wav, f0_up_key, f0_method,
                 file_index, index_rate, resample_sr,
                 rms_mix_rate, protect)              # 整段变声

踩过的坑（都写在下面代码里了）
----------------------------
* RVC 的代码靠**相对路径**找 assets，所以必须 chdir 到它的根目录
* `tools/my_utils.py` 硬编码 `cmd=["ffmpeg"]`，要求 ffmpeg 在 PATH 上
* 环境变量 `rmvpe_root` / `weight_root` / `index_root` 必须在 import 之前设好
* HuBERT 输入必须是 **torch 张量**且精度和模型一致（half），否则
  `run_cuda_graph` 会因为读 numpy 的 `.device` 而崩
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # ...\voiceconv
ENGINE = ROOT / "tools" / "rvc" / "engine"
RVC_PY = ROOT / "tools" / "rvc" / "venv" / "Scripts" / "python.exe"

# 能 import 到 shared（项目根的包）。core.py 在 tools\rvc\ 下，
# 往上是 tools -> voiceconv，共两层。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
# 模型发现
# --------------------------------------------------------------------------- #
@dataclass
class RvcModel:
    """一个可用的 RVC 模型（.pth + 可选的 .index）。"""

    name: str
    pth: Path
    index: Path | None = None
    folder: Path | None = None

    def summary(self) -> str:
        idx = "有索引" if self.index else "无索引"
        return f"{self.pth.stem} · {idx}"


def list_models() -> list[RvcModel]:
    """扫描 RVC 的模型目录，列出所有可用模型。

    同时看两个地方：
      · RVC 自己的 assets\\weights（WebUI 直接用的）
      · 我们的 data\\models\\rvc（训练产出统一放这儿）
    """
    found: dict[str, RvcModel] = {}

    roots = [ENGINE / "assets" / "weights", ROOT / "data" / "models" / "rvc"]
    index_roots = [ENGINE / "assets" / "indices", ROOT / "data" / "models" / "rvc"]

    for root in roots:
        if not root.is_dir():
            continue
        for pth in sorted(root.rglob("*.pth")):
            # 同名的 .index 可能在同级、也可能在 indices 目录
            cands = [pth.with_suffix(".index")]
            for ir in index_roots:
                if ir.is_dir():
                    cands += list(ir.rglob(f"{pth.stem}.index"))
            index = next((c for c in cands if c.is_file()), None)
            # 重名时以先扫到的为准（assets 优先）
            found.setdefault(pth.stem, RvcModel(
                name=pth.stem, pth=pth, index=index, folder=pth.parent))

    return sorted(found.values(), key=lambda m: m.name)


# --------------------------------------------------------------------------- #
# 环境准备
# --------------------------------------------------------------------------- #
def _ffmpeg_shim_dir() -> Path:
    """RVC 的 tools/my_utils.py 把 ffmpeg 写死成 cmd=["ffmpeg"]，要求它在 PATH 上。
    我们的 ffmpeg 在 bin\\ 下，用 shim 转发，不改它的源码。"""
    shim = ROOT / "logs" / "bin-shim"
    shim.mkdir(parents=True, exist_ok=True)
    from shared import paths as _p
    for name, exe in (("ffmpeg", _p.FFMPEG), ("ffprobe", _p.FFPROBE)):
        (shim / f"{name}.cmd").write_text(
            f'@echo off\r\n"{exe}" %*\r\n', encoding="ascii")
    return shim


def prepare_env() -> dict:
    """设置 RVC 需要的环境变量。**必须在 import RVC 之前调用。**"""
    os.environ.setdefault("rmvpe_root", str(ENGINE / "assets" / "rmvpe"))
    os.environ.setdefault("weight_root", str(ENGINE / "assets" / "weights"))
    os.environ.setdefault("index_root", str(ENGINE / "assets" / "indices"))
    os.environ.setdefault("outside_index_root", str(ENGINE / "assets" / "indices"))
    os.environ.setdefault("weight_pymss_root", str(ENGINE / "assets" / "pymss_weights"))
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hfcache"))

    from shared import paths as _p
    shim = _ffmpeg_shim_dir()
    os.environ["PATH"] = os.pathsep.join(
        [str(shim), str(_p.BIN), os.environ.get("PATH", "")])

    if str(ENGINE) not in sys.path:
        sys.path.insert(0, str(ENGINE))
    return os.environ


class RvcConfig:
    """RVC 的 VC 类要求的配置对象。字段名照抄 webui.py 里的用法。"""

    def __init__(self, device: str = "cuda", is_half: bool = True) -> None:
        import torch
        self.device = device if torch.cuda.is_available() else "cpu"
        self.is_half = bool(is_half and self.device == "cuda")
        # 这些路径 RVC 内部会用到
        self.hubert_model = None
        self.weight_root = os.environ.get("weight_root", str(ENGINE / "assets" / "weights"))
        self.index_root = os.environ.get("index_root", str(ENGINE / "assets" / "indices"))
        self.rmvpe_root = os.environ.get("rmvpe_root", str(ENGINE / "assets" / "rmvpe"))


# --------------------------------------------------------------------------- #
# 变声
# --------------------------------------------------------------------------- #
class RvcConverter:
    """常驻的变声器：模型加载一次，之后反复变声。"""

    def __init__(self, device: str = "cuda", verbose: bool = True) -> None:
        self.verbose = verbose
        self.device = device
        self._vc = None
        self._loaded: str | None = None
        self._orig_cwd: Path | None = None

    # ------------------------------------------------------------------ 加载 --
    def _ensure_vc(self):
        if self._vc is not None:
            return self._vc

        prepare_env()
        # RVC 内部大量使用相对路径（assets/...），必须站在它的根目录
        if self._orig_cwd is None:
            self._orig_cwd = Path.cwd()
        os.chdir(ENGINE)

        from infer.vc.modules import VC        # noqa: PLC0415

        cfg = RvcConfig(device=self.device)
        self._vc = VC(cfg)
        if self.verbose:
            print(f"[rvc] VC 初始化完成（device={cfg.device}, half={cfg.is_half}）")
        return self._vc

    def load_model(self, model: RvcModel) -> None:
        """加载一个 .pth 模型。同一个模型重复调用不会重新加载。"""
        vc = self._ensure_vc()
        key = str(model.pth)
        if self._loaded == key:
            return
        t0 = time.time()
        res = vc.get_vc(key)
        self._loaded = key
        if self.verbose:
            # get_vc 返回 (状态信息, protect 值)
            info = res[0] if isinstance(res, (tuple, list)) and res else res
            print(f"[rvc] 模型已加载：{model.pth.name}  ({time.time()-t0:.1f}s)  {info}")

    # ------------------------------------------------------------------ 转换 --
    def convert(self, model: RvcModel, input_wav: str | Path, out_wav: str | Path, *,
                pitch: int = 0, f0_method: str = "rmvpe", index_rate: float = 0.75,
                resample_sr: int = 0, rms_mix_rate: float = 0.25,
                protect: float = 0.33, sid: int = 0) -> Path:
        """把 input_wav 换成 model 的音色，写出 out_wav。

        参数（默认值是 RVC WebUI 的常用值）
        ----------------------------------
        pitch       变调半音数。男→女约 +12，女→男约 -12，同性别 0 附近微调。
                    **这个参数最关键**，调错了会变怪声。
        f0_method   音高提取算法：rmvpe（推荐，最准）/ fcpe（更快）/ harvest / pm
        index_rate  特征检索比例 0~1，越高越像但要索引文件
        resample_sr 输出重采样；0 = 不重采样（用模型自身采样率）
        rms_mix_rate 响度包络混合 0~1，0 = 用输入音量，1 = 用模型音量
        protect     保护清音/呼吸声 0~0.5，越大越保留原音细节
        """
        vc = self._ensure_vc()
        self.load_model(model)

        # vc_single 要求 index 是字符串路径，没有就传空串
        index = str(model.index) if model.index else ""

        t0 = time.time()
        result = vc.vc_single(
            sid,
            str(input_wav),
            int(pitch),
            f0_method,
            index,
            float(index_rate),
            int(resample_sr),
            float(rms_mix_rate),
            float(protect),
        )
        if self.verbose:
            print(f"[rvc] 变声完成 {time.time()-t0:.1f}s")

        # vc_single 返回 (状态信息, (采样率, 音频))
        audio = None
        if isinstance(result, (tuple, list)) and len(result) >= 2:
            audio = result[1]
        if audio is None:
            raise RuntimeError(f"RVC 没有产出音频，返回：{result!r}")

        sr, data = audio[0], audio[1]
        out_path = Path(out_wav)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        import numpy as np
        import soundfile as sf
        if getattr(data, "dtype", None) != np.int16:
            data = (np.clip(np.asarray(data), -1.0, 1.0) * 32767).astype(np.int16)
        sf.write(str(out_path), data, int(sr))

        if self.verbose:
            print(f"[rvc] 已写出 {out_path.name}  "
                  f"{len(data)/int(sr):.2f}s/{int(sr)}Hz")
        return out_path


# --------------------------------------------------------------------------- #
def _selftest() -> int:
    """无头自检：列模型 + 报告环境状态。没有模型时也能跑。"""
    from shared import paths
    paths.ensure_dirs()

    print("=" * 62)
    print(" RVC 封装自检")
    print("=" * 62)

    prepare_env()
    import torch
    print(f" torch     : {torch.__version__}")
    print(f" cuda 可用 : {torch.cuda.is_available()}")
    print(f" 引擎目录  : {ENGINE}")
    print(f" 引擎存在  : {ENGINE.is_dir()}")

    for label, p in (("HuBERT", ENGINE / "assets" / "hubert_base" / "pytorch_model.bin"),
                     ("RMVPE", ENGINE / "assets" / "rmvpe" / "rmvpe.pt")):
        print(f" {label:9} : {'OK' if p.exists() else '缺失'}  {p.name}")

    models = list_models()
    print(f"\n 发现的模型：{len(models)} 个")
    for m in models:
        print(f"   · {m.name}   {m.summary()}")

    if not models:
        print("\n （还没有训练好的模型 —— 变声要等训练完成）")
        print(" 模型应放在：")
        print(f"   {ENGINE / 'assets' / 'weights'}   （RVC WebUI 直接用这里）")
        print(f"   {ROOT / 'data' / 'models' / 'rvc'}   （训练产出统一放这儿）")

    # 验证 VC 类能不能初始化（不需要模型）
    try:
        conv = RvcConverter(verbose=True)
        conv._ensure_vc()
        print("\n VC 类初始化：通过 ✅")
        ok = True
    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"\n VC 类初始化失败：{type(exc).__name__}: {exc}")
        ok = False

    print("=" * 62)
    print(" 自检" + ("通过" if ok else "失败"))
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
