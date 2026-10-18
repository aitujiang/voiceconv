"""一键安装 —— 把 README 里那一串手工步骤自动化。

设计原则
--------
1. **每一步单独可重跑**。20 GB 下载中途断掉是常态，不能一失败就从头来。
2. **先检测再动手**。显卡什么型号、驱动多新、already装了什么都先问清楚，
   不同配置装的东西不一样（尤其 PyTorch 的 CUDA 版本）。
3. **失败要能定位**。每一步都有明确的名字和错误输出，不是一句"失败"。
4. **不覆盖已有成果**。已经装好的环境、已经下好的权重，跳过。

用法
----
    python setup.py                # 完整安装（交互式，会问要不要装哪些）
    python setup.py --check        # 只体检，不改任何东西
    python setup.py --step env     # 只跑某一步
    python setup.py --core         # 只装核心（音频工具，不装 AI）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = ROOT / "bin"
LOGS = ROOT / "logs"
VENV = ROOT / ".venv"
VENV_PY = VENV / "Scripts" / "python.exe"
VENV_PYW = VENV / "Scripts" / "pythonw.exe"
TTS_VENV = ROOT / "tools" / "tts" / "venv"
RVC_VENV = ROOT / "tools" / "rvc" / "venv"
SRC_SEEDVC = ROOT / "src" / "seed-vc-main"
TTS_ENGINE = ROOT / "tools" / "tts" / "engine"
RVC_ENGINE = ROOT / "tools" / "rvc" / "engine"
MARKER = ROOT / "logs" / "setup-state.json"

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# 国内 pip 源（实测：北大 4 秒响应，官方源 160 秒）
PIP_MIRRORS = [
    ("北大", "https://mirrors.pku.edu.cn/pypi/simple"),
    ("腾讯", "https://mirrors.cloud.tencent.com/pypi/simple"),
    ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
]

SEEDVC_REPO = "https://github.com/Plachtaa/seed-vc.git"
TTS_REPO = "https://github.com/RVC-Boss/GPT-SoVITS.git"
RVC_REPO = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def say(msg: str = "") -> None:
    print(msg, flush=True)


def head(step: str, title: str) -> None:
    say()
    say("=" * 64)
    say(f" [{step}] {title}")
    say("=" * 64)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None,
        check: bool = True) -> tuple[int, str]:
    """跑命令并返回 (退出码, 输出)。"""
    try:
        r = subprocess.run(cmd, cwd=str(cwd or ROOT), env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW)
    except OSError as exc:
        return -1, str(exc)
    out = (r.stdout or "") + (r.stderr or "")
    if check and r.returncode != 0:
        say(f"  命令失败：{' '.join(cmd[:3])} …")
        say("  " + out.strip()[-800:])
    return r.returncode, out


def load_state() -> set[str]:
    if MARKER.exists():
        try:
            return set(json.loads(MARKER.read_text(encoding="utf-8")).get("done", []))
        except Exception:                        # noqa: BLE001
            return set()
    return set()


def mark_done(step: str) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    done = load_state()
    done.add(step)
    MARKER.write_text(json.dumps({"done": sorted(done)}, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 第 0 步：体检
# --------------------------------------------------------------------------- #
def probe_gpu() -> dict:
    """读显卡信息。返回 dict，读不到就给空值。"""
    info = {"name": "", "driver": "", "cuda_max": "", "vram_mb": 0,
            "sm": "", "ok": False}
    code, out = run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                     "--format=csv,noheader,nounits"], check=False)
    if code != 0 or not out.strip():
        return info

    line = out.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 3:
        info["name"] = parts[0]
        info["driver"] = parts[1]
        try:
            info["vram_mb"] = int(float(parts[2]))
        except ValueError:
            pass
        info["ok"] = True

    # 驱动版本 → 能用的最高 CUDA
    m = re.match(r"(\d+)\.", info["driver"])
    major = int(m.group(1)) if m else 0
    if major >= 570:
        info["cuda_max"] = "12.8"
    elif major >= 530:
        info["cuda_max"] = "12.1"
    elif major >= 520:
        info["cuda_max"] = "11.8"
    else:
        info["cuda_max"] = ""

    # 算力（决定能不能用新架构的 kernel）
    code, out = run(["nvidia-smi", "--query-gpu=compute_cap",
                     "--format=csv,noheader"], check=False)
    if code == 0 and out.strip():
        info["sm"] = out.strip().splitlines()[0].replace(".", "")
    return info


def probe_python() -> tuple[bool, str]:
    v = sys.version_info
    ok = v >= (3, 10)
    return ok, f"{v.major}.{v.minor}.{v.micro}"


def probe_disk() -> tuple[float, float]:
    try:
        u = shutil.disk_usage(ROOT)
        return u.free / 1e9, u.total / 1e9
    except OSError:
        return 0.0, 0.0


def step_check() -> bool:
    head("0", "体检")
    gpu = probe_gpu()
    py_ok, py_ver = probe_python()
    free_gb, total_gb = probe_disk()

    say(f"  Python      : {py_ver}  {'OK' if py_ok else '❌ 需要 3.10+'}")
    if gpu["ok"]:
        say(f"  显卡        : {gpu['name']}")
        say(f"  显存        : {gpu['vram_mb']} MB  ({gpu['vram_mb']/1024:.1f} GB)")
        say(f"  驱动        : {gpu['driver']}  → 可用最高 CUDA {gpu['cuda_max'] or '未知'}")
        say(f"  算力        : sm_{gpu['sm'] or '?'}")
        if gpu["sm"] == "120" and gpu["cuda_max"] != "12.8":
            say("  ⚠️ 50 系显卡需要驱动 ≥ 570（CUDA 12.8），请先更新驱动")
    else:
        say("  显卡        : ❌ 读不到 nvidia-smi")
        say("                  → 只能装核心音频工具，AI 功能用不了")
    say(f"  磁盘        : 可用 {free_gb:.1f} GB / 共 {total_gb:.1f} GB")
    if free_gb < 20:
        say("  ⚠️ 剩余空间不足 20 GB，AI 功能可能装不下")

    say()
    say("  已有组件：")
    checks = [
        ("Python 环境", VENV_PY),
        ("Seed-VC 引擎", SRC_SEEDVC),
        ("GPT-SoVITS 引擎", TTS_ENGINE),
        ("RVC 引擎", RVC_ENGINE),
        ("ffmpeg", BIN / "ffmpeg.exe"),
    ]
    for label, p in checks:
        say(f"    {'✅' if p.exists() else '⬜'} {label}")

    return py_ok and (gpu["ok"] or True)


# --------------------------------------------------------------------------- #
# 第 1 步：ffmpeg
# --------------------------------------------------------------------------- #
FFMPEG_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
]


def step_ffmpeg() -> bool:
    head("1", "ffmpeg")
    if (BIN / "ffmpeg.exe").exists() and (BIN / "ffprobe.exe").exists():
        say("  已存在，跳过")
        return True

    BIN.mkdir(parents=True, exist_ok=True)
    say("  下载 ffmpeg（约 100 MB）…")

    import urllib.request
    zip_path = LOGS / "ffmpeg.zip"
    LOGS.mkdir(parents=True, exist_ok=True)

    for url in FFMPEG_URLS:
        try:
            say(f"    来源：{url[:60]}…")
            with urllib.request.urlopen(url, timeout=180) as r, \
                    open(zip_path, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    chunk = r.read(1024 * 512)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total and got % (10 * 1024 * 1024) < 1024 * 512:
                        say(f"    {got*100//total}%  {got/1e6:.0f}/{total/1e6:.0f} MB")
            break
        except Exception as exc:                 # noqa: BLE001
            say(f"    失败：{type(exc).__name__}")
            continue
    else:
        say("  ❌ 所有来源都失败。请手动下载 ffmpeg 放进 bin\\")
        return False

    say("  解压…")
    import zipfile
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                base = Path(name).name
                if base in ("ffmpeg.exe", "ffprobe.exe"):
                    with z.open(name) as src, open(BIN / base, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        zip_path.unlink(missing_ok=True)
    except Exception as exc:                     # noqa: BLE001
        say(f"  解压失败：{exc}")
        return False

    ok = (BIN / "ffmpeg.exe").exists()
    say("  ✅ 完成" if ok else "  ❌ ffmpeg.exe 没找到")
    return ok


# --------------------------------------------------------------------------- #
# 第 2 步：Python 环境 + PyTorch
# --------------------------------------------------------------------------- #
def step_env(cuda: str = "") -> bool:
    head("2", "Python 环境 + PyTorch")
    if VENV_PY.exists():
        say("  .venv 已存在，检查 PyTorch…")
        code, out = run([str(VENV_PY), "-c",
                         "import torch;print(torch.__version__, torch.cuda.is_available())"],
                        check=False)
        if code == 0:
            say(f"  {out.strip()}")
            if "True" in out:
                say("  ✅ 可以用，跳过")
                return True
        say("  重装 PyTorch")

    if not VENV_PY.exists():
        say("  创建虚拟环境…")
        code, out = run([sys.executable, "-m", "venv", str(VENV)])
        if code != 0 or not VENV_PY.exists():
            say("  ❌ 创建 venv 失败")
            return False

    # 装 pip 工具链
    say("  升级 pip…")
    run([str(VENV_PY), "-m", "pip", "install", "-q", "--upgrade",
         "pip", "setuptools", "wheel"], check=False)

    # 装 PyTorch
    gpu = probe_gpu()
    cuda = cuda or gpu.get("cuda_max") or "12.1"
    say(f"  装 PyTorch（CUDA {cuda}，约 2.5 GB，要等一会）…")

    if cuda == "12.8":
        index = "https://download.pytorch.org/whl/cu128"
        pkgs = ["torch==2.7.0", "torchaudio==2.7.0"]
    elif cuda == "12.1":
        index = "https://download.pytorch.org/whl/cu121"
        pkgs = ["torch", "torchaudio"]
    else:
        index = "https://download.pytorch.org/whl/cu118"
        pkgs = ["torch", "torchaudio"]

    code, out = run([str(VENV_PY), "-m", "pip", "install",
                     "--progress-bar", "off", *pkgs, "--index-url", index])
    if code != 0:
        say("  ❌ PyTorch 安装失败")
        return False

    code, out = run([str(VENV_PY), "-c",
                     "import torch;print(torch.__version__, torch.cuda.is_available())"],
                    check=False)
    say(f"  结果：{out.strip()}")
    if "False" in out:
        say("  ⚠️ CUDA 不可用 —— 可能装成了 CPU 版，或驱动太旧")
    return True


def pick_mirror() -> str:
    """测速挑最快 pip 源。"""
    import urllib.request
    best, best_ms = PIP_MIRRORS[0][1], 1e9
    for name, url in PIP_MIRRORS:
        t0 = time.time()
        try:
            urllib.request.urlopen(url, timeout=8).read(1)
            ms = (time.time() - t0) * 1000
            say(f"    {name:5} {ms:6.0f} ms")
            if ms < best_ms:
                best, best_ms = url, ms
        except Exception:                        # noqa: BLE001
            say(f"    {name:5} 不可用")
    return best


def step_deps() -> bool:
    head("3", "依赖包")
    req = ROOT / "requirements.txt"
    if not req.exists():
        say("  ❌ 找不到 requirements.txt")
        return False

    say("  测速选源：")
    mirror = pick_mirror()
    say(f"  用：{mirror}")

    code, out = run([str(VENV_PY), "-m", "pip", "install",
                     "--progress-bar", "off", "-r", str(req),
                     "--index-url", mirror])
    if code != 0:
        say("  ⚠️ 第一次失败，试试 --only-binary（避免源码编译）")
        code, out = run([str(VENV_PY), "-m", "pip", "install",
                         "--progress-bar", "off", "--only-binary=:all:",
                         "-r", str(req), "--index-url", mirror])
    say("  ✅ 完成" if code == 0 else "  ❌ 依赖安装失败，见上面的输出")
    return code == 0


# --------------------------------------------------------------------------- #
# 第 4 步：引擎
# --------------------------------------------------------------------------- #
def clone(url: str, dest: Path, name: str) -> bool:
    if dest.exists() and any(dest.iterdir()):
        say(f"  {name} 已存在，跳过")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"  clone {name}…")
    code, out = run(["git", "clone", "--depth", "1", url, str(dest)])
    if code != 0:
        say(f"  ❌ {name} 下载失败。可能原因：没装 git / 网络不通")
        say("     如果 GitHub 连不上，先开代理，或手动下载 zip 解压到该目录")
        return False
    say(f"  ✅ {name}")
    return True


def step_engines() -> bool:
    head("4", "三套引擎（约 700 MB）")
    ok = True
    ok &= clone(SEEDVC_REPO, SRC_SEEDVC, "Seed-VC")
    ok &= clone(TTS_REPO, TTS_ENGINE, "GPT-SoVITS")
    ok &= clone(RVC_REPO, RVC_ENGINE, "RVC")
    return ok


# --------------------------------------------------------------------------- #
# 第 5 步：权重
# --------------------------------------------------------------------------- #
def step_weights(which: str = "all") -> bool:
    head("5", "模型权重（约 9 GB）")
    scripts = []
    if which in ("all", "seedvc"):
        scripts.append(("Seed-VC", ROOT / "download_models.py"))
    if which in ("all", "rvc"):
        scripts.append(("RVC", ROOT / "download_rvc_models.py"))
    if which in ("all", "tts"):
        scripts.append(("GPT-SoVITS", ROOT / "download_tts_models.py"))

    ok = True
    for name, script in scripts:
        if not script.exists():
            say(f"  ⬜ {name}：没有下载脚本，跳过")
            continue
        say(f"  --- {name} ---")
        code, out = run([str(VENV_PY), str(script)])
        # 下载脚本自己有进度输出，这里只在失败时给提示
        if code != 0:
            tail = "\n".join(out.strip().splitlines()[-6:])
            say(f"  ❌ {name} 权重下载失败：\n{tail}")
            ok = False
        else:
            say(f"  ✅ {name}")
    return ok


# --------------------------------------------------------------------------- #
# 第 6 步：子环境 + 修补
# --------------------------------------------------------------------------- #
def make_subenv(path: Path, name: str) -> bool:
    """从 .venv 派生子环境：共用 torch，但自己装的包不污染回去。"""
    if (path / "Scripts" / "python.exe").exists():
        say(f"  {name} 已存在")
        return True
    say(f"  创建 {name}（复用 .venv 的 torch）…")
    path.parent.mkdir(parents=True, exist_ok=True)
    code, out = run([str(VENV_PY), "-m", "venv", "--system-site-packages",
                     str(path)])
    if code != 0:
        say(f"  ❌ {name} 创建失败")
        return False
    # 用 .pth 把 .venv 的 site-packages 暴露给它
    pth = path / "Lib" / "site-packages" / "_shared_from_voice.pth"
    pth.parent.mkdir(parents=True, exist_ok=True)
    pth.write_text(str(VENV / "Lib" / "site-packages") + "\n", encoding="utf-8")
    return True


def step_patches(quick: bool = False) -> bool:
    head("6", "子环境 + 兼容修补")
    ok = True

    # --- 6.1 两个子环境 ---
    ok &= make_subenv(TTS_VENV, "朗读环境")
    ok &= make_subenv(RVC_VENV, "RVC 环境")

    # --- 6.2 torch.load 兼容层（torch>=2.6 默认 weights_only=True） ---
    for venv in (TTS_VENV, RVC_VENV):
        sc = venv / "Lib" / "site-packages" / "sitecustomize.py"
        if not sc.exists():
            sc.parent.mkdir(parents=True, exist_ok=True)
            sc.write_text(
                '"""恢复 torch.load 的旧默认行为（torch>=2.6 改成 weights_only=True，\n'
                '很多老代码会因此报 UnpicklingError）。"""\n'
                "try:\n"
                "    import torch\n"
                "except Exception:\n"
                "    torch = None\n"
                "if torch is not None and not getattr(torch, '_legacy_load_patched', False):\n"
                "    _orig = torch.load\n"
                "    def load(*a, **kw):\n"
                "        kw.setdefault('weights_only', False)\n"
                "        return _orig(*a, **kw)\n"
                "    torch.load = load\n"
                "    torch._legacy_load_patched = True\n",
                encoding="utf-8")
            say(f"  ✅ {venv.name}: torch.load 兼容层")

    if quick:
        return ok

    # --- 6.3 GPT-SoVITS 的依赖 ---
    say("  装 GPT-SoVITS 依赖（transformers 4.57 等）…")
    code, out = run([str(TTS_VENV / "Scripts" / "python.exe"), "-m", "pip", "install",
                     "--progress-bar", "off", "-q",
                     "transformers==4.57.0", "gradio>=4,<5", "onnxruntime",
                     "jieba", "pypinyin", "cn2an", "wordsegment",
                     "pyopenjtalk-plus", "ffmpeg-python", "tensorboard",
                     "pytorch-lightning", "peft", "numpy==1.26.4"])
    if code != 0:
        say("  ⚠️ 部分包失败，继续（有些是可选的）")

    # --- 6.4 jieba_fast shim（没有 Windows wheel，用 jieba 顶上） ---
    for venv in (TTS_VENV,):
        sp = venv / "Lib" / "site-packages"
        jf = sp / "jieba_fast"
        if not jf.exists():
            jf.mkdir(parents=True, exist_ok=True)
            (jf / "__init__.py").write_text(
                '"""jieba_fast 没有 Windows wheel，转发给 jieba。"""\n'
                "from jieba import *          # noqa: F401,F403\n"
                "import jieba as _j\n"
                "lcut       = _j.lcut\n"
                "lcut_for_search = _j.lcut_for_search\n"
                "cut        = _j.cut\n"
                "cut_for_search = _j.cut_for_search\n"
                "Tokenizer  = _j.Tokenizer\n"
                "initialize = _j.initialize\n"
                "setLogLevel = _j.setLogLevel\n",
                encoding="utf-8")
            (jf / "posseg.py").write_text(
                '"""转发 jieba.posseg。"""\n'
                "from jieba.posseg import *     # noqa: F401,F403\n"
                "import jieba.posseg as _p\n"
                "cut  = _p.cut\n"
                "lcut = _p.lcut\n"
                "pair = _p.pair\n",
                encoding="utf-8")
            say("  ✅ jieba_fast → jieba 转发")

        # pyopenjtalk：日语音素，Windows 上难装，做个 stub（中文用不到）
        pj = sp / "pyopenjtalk"
        if not pj.exists():
            pj.mkdir(parents=True, exist_ok=True)
            (pj / "__init__.py").write_text(
                '"""Windows 上没有 pyopenjtalk 的 wheel。\n'
                '只有日语文本会用到它，中文/英文不需要。"""\n'
                "def _missing(*a, **kw):\n"
                "    raise ImportError(\n"
                "        '日语功能需要 pyopenjtalk，当前环境没有安装。\\n'\n"
                "        '中文和英文不受影响。')\n"
                "g2p = _missing\n"
                "extract_fullcontext = _missing\n"
                "run_frontend = _missing\n",
                encoding="utf-8")
            say("  ✅ pyopenjtalk stub（日语不可用，中英文正常）")

    # --- 6.5 RVC 环境依赖 ---
    say("  装 RVC 依赖…")
    code, out = run([str(RVC_VENV / "Scripts" / "python.exe"), "-m", "pip", "install",
                     "--progress-bar", "off", "-q",
                     "transformers>=4.49,<4.50", "gradio>=3.14,<3.15",
                     "pydantic>=1.10,<2", "fastapi>=0.88,<0.100",
                     "starlette>=0.22,<0.28", "uvicorn>=0.20,<0.23",
                     "faiss-cpu", "praat-parselmouth", "torchcrepe",
                     "local-attention", "einops", "onnxruntime-gpu>=1.19,<1.20",
                     "opencv-python-headless", "av", "FreeSimpleGUI",
                     "librosa>=0.10.2,<0.11", "numpy==1.26.4"])
    if code != 0:
        say("  ⚠️ 部分包失败，继续")

    # --- 6.6 ffmpeg shim（RVC 的 my_utils.py 硬编码 cmd=[\"ffmpeg\"]） ---
    shim = LOGS / "bin-shim"
    shim.mkdir(parents=True, exist_ok=True)
    for nm, exe in (("ffmpeg", BIN / "ffmpeg.exe"), ("ffprobe", BIN / "ffprobe.exe")):
        (shim / f"{nm}.cmd").write_text(
            f'@echo off\r\n"{exe}" %*\r\n', encoding="ascii")
    say(f"  ✅ ffmpeg shim（{shim}）")

    return ok


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
STEPS = {
    "check": ("体检", None),
    "ffmpeg": ("ffmpeg", None),
    "env": ("Python 环境 + PyTorch", None),
    "deps": ("依赖包", None),
    "engines": ("三套引擎", None),
    "weights": ("模型权重", None),
    "patches": ("子环境 + 修补", None),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="素材工具箱 一键安装")
    ap.add_argument("--check", action="store_true", help="只体检")
    ap.add_argument("--core", action="store_true",
                    help="只装核心（音频工具，不装 AI 引擎和权重）")
    ap.add_argument("--step", choices=list(STEPS), help="只跑某一步")
    ap.add_argument("--no-mirror", action="store_true", help="pip 用官方源")
    ap.add_argument("--yes", "-y", action="store_true", help="不交互，全自动")
    args = ap.parse_args()

    LOGS.mkdir(parents=True, exist_ok=True)

    say("=" * 64)
    say(" 素材工具箱 · 安装程序")
    say("=" * 64)

    if not step_check():
        say()
        say("体检查出问题，先解决再继续。")
        return 1
    if args.check:
        return 0

    gpu = probe_gpu()
    if not gpu["ok"]:
        say()
        say("⚠️ 没有可用的 NVIDIA 显卡，只能装核心音频工具。")
        if not args.yes:
            ans = input("继续安装核心功能？[Y/n] ").strip().lower()
            if ans in ("n", "no"):
                return 1
        args.core = True

    if args.core:
        plan = ["ffmpeg", "env", "deps"]
    else:
        plan = ["ffmpeg", "env", "deps", "engines", "weights", "patches"]

    if args.step:
        plan = [args.step]

    say()
    say("将执行：" + " → ".join(plan))
    if not args.yes:
        ans = input("开始？[Y/n] ").strip().lower()
        if ans in ("n", "no"):
            say("已取消")
            return 0

    done = load_state()
    t0 = time.time()
    results: dict[str, bool] = {}

    for step in plan:
        if step in done and step != "check":
            say(f"\n  [{step}] 之前已完成，跳过（删 logs/setup-state.json 可重做）")
            results[step] = True
            continue

        if step == "ffmpeg":
            ok = step_ffmpeg()
        elif step == "env":
            ok = step_env(gpu.get("cuda_max", ""))
        elif step == "deps":
            ok = step_deps()
        elif step == "engines":
            ok = step_engines()
        elif step == "weights":
            ok = step_weights()
        elif step == "patches":
            ok = step_patches()
        else:
            ok = False

        results[step] = ok
        if ok:
            mark_done(step)
        else:
            say()
            say(f"❌ [{step}] 失败。修好后可以只重跑这一步：")
            say(f"     python setup.py --step {step}")
            return 1

    elapsed = (time.time() - t0) / 60
    say()
    say("=" * 64)
    say(f" 完成，用时 {elapsed:.1f} 分钟")
    say("=" * 64)
    for step, ok in results.items():
        say(f"  {'✅' if ok else '❌'} {STEPS[step][0]}")
    say()
    say(" 下一步：双击  启动.bat")
    say()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
