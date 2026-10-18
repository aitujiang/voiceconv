"""工具箱的统一路径定义。

为什么要有这个文件
------------------
所有工具都从这里取路径，而不是各自硬编码 `D:\\dshworking01\\voiceconv\\...`。
好处有二：

1. **改结构只改一处**。以后要把 ffmpeg 挪到 shared\\bin、或者把 venv 换位置，
   只需改本文件，不用去 grep 五个脚本。
2. **共享资源集中声明**。ffmpeg 和模型仓库是全工具箱共用的（省磁盘），
   各自独立的只有 Python 环境。

关于「为什么有些目录是引用而不是真搬」
--------------------------------------
`tools\\voice\\venv` 和 `src\\seed-vc-main` 里的模型缓存**不能物理搬动**：
- `.venv\\pyvenv.cfg` 和 `.venv\\Scripts\\pip.exe` 内嵌了创建时的绝对路径
- HuggingFace 缓存目录里也记录了解压后的绝对路径
搬了就得重装 6GB 环境 + 重下 5GB 权重。

所以它们原地保留，由本文件指过去。对外表现上仍然是「共享的」。
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# 根目录
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent.parent          # ...\voiceconv

# --------------------------------------------------------------------------- #
# 共享资源（全工具箱共用，省磁盘）
# --------------------------------------------------------------------------- #
SHARED = ROOT / "shared"
BIN = ROOT / "bin"                                     # ffmpeg / ffprobe 实际所在
FFMPEG = BIN / "ffmpeg.exe"
FFPROBE = BIN / "ffprobe.exe"

MODEL_STORE = ROOT / "models"                          # demucs 等工具的权重缓存
SEEDVC_ROOT = ROOT / "src" / "seed-vc-main"            # Seed-VC 代码 + 模型仓库
SEEDVC_CHECKPOINTS = SEEDVC_ROOT / "checkpoints"

# --------------------------------------------------------------------------- #
# 各工具自己的环境（刻意独立，避免依赖冲突互相拖累）
# --------------------------------------------------------------------------- #
VOICE_VENV = ROOT / ".venv"                            # 变声工具的环境（torch 2.7 cu128）
VOICE_PY = VOICE_VENV / "Scripts" / "python.exe"
VOICE_PYW = VOICE_VENV / "Scripts" / "pythonw.exe"

TOOLS = ROOT / "tools"
AUDIO_TOOL = TOOLS / "audio"
DOWNLOAD_TOOL = TOOLS / "download"

# --------------------------------------------------------------------------- #
# 数据与产出
# --------------------------------------------------------------------------- #
DATA = ROOT / "data"
PRESETS = DATA / "presets"                             # 预设（人物 / 任务）
VOICES = DATA / "voices"                               # 音色素材
REFS = DATA / "refs"                                   # 挑好的参考音频
TRAINED = DATA / "models"                              # 训练出的模型（RVC / GPT-SoVITS）
TRAINED_RVC = TRAINED / "rvc"                          # .pth + .index
TRAINED_SOVITS = TRAINED / "sovits"                    # .ckpt + .pth
VOICE_DATASETS = DATA / "datasets"                     # 训练素材：data\datasets\<角色名>\*.wav
SITES_JSON = DATA / "sites.json"                       # 素材网站列表（出厂默认）
# 用户改过的列表存这里：升级时解压覆盖代码不会碰它（.gitignore 里排除）
SITES_LOCAL_JSON = DATA / "sites.local.json"
LINKS_JSON = DATA / "links.json"                       # 外接启动器（用户自己加的外部程序）

IO = ROOT / "io"
OUTPUT = IO / "output"                                 # 所有成品（用户的东西，别乱删）
DOWNLOADS = ROOT / "downloads"                         # 下载的视频
LOGS = ROOT / "logs"

# 自检 / 回归测试专用的目录。
#
# 为什么要单独开一个：我曾经在做回归测试时清空 io\output 来「保证测试干净」，
# 结果把用户的成品删掉了。测试**绝不允许**写进或清空 OUTPUT。
# 所有 --selftest / --auto 的产物一律落在 SANDBOX 里。
SANDBOX = ROOT / "io" / "_sandbox"

# --------------------------------------------------------------------------- #
# 品牌 / 界面用常量
# --------------------------------------------------------------------------- #
APP_NAME = "素材工具箱"
APP_VERSION = "0.0.2"


def ensure_dirs() -> None:
    """把工具箱需要的目录一次性建好（幂等，可反复调用）。

    顺便做一次子环境的路径自愈（见 fix_venv_paths）——
    每个入口都会调这个函数，所以放在这里最省事，
    不用去四个 ui.py 里各加一行。
    """
    for d in (SHARED, DATA, PRESETS, VOICES, REFS, TRAINED, TRAINED_RVC, TRAINED_SOVITS,
              VOICE_DATASETS, IO, OUTPUT, DOWNLOADS, LOGS, MODEL_STORE, SANDBOX):
        d.mkdir(parents=True, exist_ok=True)
    try:
        fix_venv_paths(verbose=os.environ.get("VOICECONV_DEBUG_PATHS") == "1")
    except Exception:                     # noqa: BLE001
        # 自愈失败不该拦住整个工具箱 —— 大不了那个工具报它自己的错
        pass


def sandbox(*parts: str) -> Path:
    """测试用的输出路径。

    自检和回归测试一律用这个，**不要用 OUTPUT** —— 见 SANDBOX 的注释。
    """
    p = SANDBOX.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- #
# 子环境的路径自愈
# --------------------------------------------------------------------------- #
def fix_venv_paths(verbose: bool = False) -> list[str]:
    """检查并修复子环境里写死的绝对路径。

    为什么需要
    ----------
    `tools\\tts\\venv` 和 `tools\\rvc\\venv` 是从 `.venv` 派生出来的
    （用 `--system-site-packages` + 一个 .pth 指向 .venv 的 site-packages，
    这样三个环境共用同一份 5.4GB 的 torch）。

    但这个做法有个副作用：**路径被写死了**。

        pyvenv.cfg:  executable = D:\\dshworking01\\voiceconv\\.venv\\Scripts\\python.exe
        _shared_from_voice.pth:  D:\\dshworking01\\voiceconv\\.venv\\Lib\\site-packages

    一旦改名、移动、或者复制到别的盘，这两处就对不上了 ——
    表现为「变声/朗读/RVC 全部报错」，而界面上看不出原因。

    做法
    ----
    不改 venv 的结构，只在**启动时**把这两处改写成"当前实际路径"。
    这样比把 venv 改成相对路径可靠得多（.pth 的相对路径是相对于
    site-packages 解析的，很容易算错）。

    返回改动了哪些文件（空列表 = 一切正常，不需要动）。
    """
    changed: list[str] = []
    if not VOICE_PY.exists():
        return changed

    want_site = str(VOICE_VENV / "Lib" / "site-packages")
    want_exe = str(VOICE_PY)

    for name, venv in (("tts", TOOLS / "tts" / "venv"),
                       ("rvc", TOOLS / "rvc" / "venv")):
        if not venv.is_dir():
            continue

        # ---- ① .pth：把 .venv 的 site-packages 暴露给它 ----
        pth = venv / "Lib" / "site-packages" / "_shared_from_voice.pth"
        try:
            cur = pth.read_text(encoding="utf-8").strip() if pth.exists() else ""
            if cur != want_site:
                pth.parent.mkdir(parents=True, exist_ok=True)
                # 用 utf-8 写；Python 读 .pth 时对编码不敏感，但别留 BOM
                pth.write_text(want_site + "\n", encoding="utf-8")
                changed.append(f"{name}/_shared_from_voice.pth")
        except OSError as exc:
            if verbose:
                print(f"[paths] 修 {name} 的 .pth 失败：{exc}")

        # ---- ② pyvenv.cfg：executable / command 两行 ----
        cfg = venv / "pyvenv.cfg"
        if not cfg.exists():
            continue
        try:
            lines = cfg.read_text(encoding="utf-8").splitlines()
            out: list[str] = []
            touched = False
            for line in lines:
                if line.startswith("executable "):
                    new = f"executable = {want_exe}"
                    touched |= (line.strip() != new)
                    out.append(new)
                elif line.startswith("command "):
                    new = (f"command = {want_exe} -m venv "
                           f"--system-site-packages {venv}")
                    touched |= (line.strip() != new)
                    out.append(new)
                else:
                    out.append(line)
            if touched:
                cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
                changed.append(f"{name}/pyvenv.cfg")
        except OSError as exc:
            if verbose:
                print(f"[paths] 修 {name} 的 pyvenv.cfg 失败：{exc}")

    if changed and verbose:
        print(f"[paths] 已自愈子环境路径：{', '.join(changed)}")
    return changed


def child_env(extra: dict | None = None) -> dict:
    """给子进程准备环境变量：镜像、可写缓存目录、ffmpeg 可见性。

    这些设置以前散落在 convert.py / song.py 里各写一遍，现在统一从这里出，
    避免出现「某个工具忘了设 TORCH_HOME 就往用户目录写」这类问题。
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # huggingface.co 在本机直连超时，必须走镜像
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    # 用户目录下的缓存不可写，全部挪进项目内
    env.setdefault("TORCH_HOME", str(MODEL_STORE / "torch"))
    env.setdefault("HF_HOME", str(MODEL_STORE / "hfcache"))
    env.setdefault("MPLCONFIGDIR", str(LOGS / "mplconfig"))

    # pydub 等库靠这两个变量找 ffmpeg，而我们的 ffmpeg 不在系统 PATH 上
    env["FFMPEG_BINARY"] = str(FFMPEG)
    env["FFPROBE_BINARY"] = str(FFPROBE)
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")

    if extra:
        env.update(extra)
    return env
