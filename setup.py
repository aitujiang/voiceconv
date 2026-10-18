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
import hashlib
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
    """跑命令并返回 (退出码, 输出)。

    所有缓存目录一律指到项目内：用户目录下的 pip / matplotlib / HF 缓存
    在没管理员权限的机器上常常不可写（WinError 5），会把正常安装搞失败。
    """
    e = dict(os.environ)
    e.update({
        "PIP_CACHE_DIR": str(LOGS / "pipcache"),
        "MPLCONFIGDIR": str(LOGS / "mplconfig"),
        "HF_HOME": str(ROOT / "models" / "hfcache"),
        "TORCH_HOME": str(ROOT / "models" / "torch"),
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    e.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if env:
        e.update(env)
    for d in ("PIP_CACHE_DIR", "MPLCONFIGDIR", "HF_HOME", "TORCH_HOME"):
        try:
            Path(e[d]).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    try:
        r = subprocess.run(cmd, cwd=str(cwd or ROOT), env=e,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=NO_WINDOW)
    except OSError as exc:
        return -1, str(exc)
    out = (r.stdout or "") + (r.stderr or "")
    if check and r.returncode != 0:
        say(f"  命令失败：{' '.join(cmd[:3])} …")
        say("  " + out.strip()[-800:])
    return r.returncode, out


def load_state() -> dict:
    """读安装状态。文件坏了就当空（顶多重跑一遍，不会漏装）。"""
    if MARKER.exists():
        try:
            data = json.loads(MARKER.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
        except Exception:                        # noqa: BLE001
            return {}
    return {}


def mark_done(step: str, fp: str | None = None) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    st = load_state()
    done = set(st.get("done", []))
    done.add(step)
    fps = dict(st.get("fp", {}))
    if fp:
        fps[step] = fp
    MARKER.write_text(json.dumps({"done": sorted(done), "fp": fps}, indent=1),
                      encoding="utf-8")


# ---------------------------------------------------------------- 指纹 --
# 纯代码更新（改界面、修 bug）不该让用户重装环境，所以哪些步骤能跳过，
# 由「依赖清单有没有变」决定，而不是「是不是做过」。
# 改动了 6.x 的修补逻辑就 PATCH_REV += 1，让老用户重跑一次。
PATCH_REV = "1"


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    if path.exists():
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def deps_fp() -> str:
    return _hash(ROOT / "requirements.txt")


def patches_fp() -> str:
    """三套依赖清单 + 修补逻辑版本。任何一个变了，第 6 步就要重跑。"""
    h = hashlib.sha256(PATCH_REV.encode())
    h.update(_hash(TTS_ENGINE / "requirements.txt").encode())
    for p in pick_rvc_req():
        h.update(_hash(p).encode())
    return h.hexdigest()[:16]


FP_STEPS = {"deps": deps_fp, "patches": patches_fp}


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
    # 只认 3.10 ~ 3.12。整套 AI 依赖（torch cu128 / funasr / pymss / opencc …）
    # 只在 3.12 上验证过；3.13 起 funasr、pymss 等还没有 Windows wheel，
    # 装上也是残的。宁可现在拦住，也别让用户装到一半才炸。
    ok = (3, 10) <= v < (3, 13)
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

    say(f"  Python      : {py_ver}  {'OK' if py_ok else '❌ 需要 3.10 ~ 3.12'}")
    if not py_ok:
        say("                  → 请装 Python 3.12（python.org，64 位），")
        say("                    3.13+ 上 funasr / pymss 等依赖还没有 Windows wheel")
    elif not py_ver.startswith("3.12"):
        say("                  ⚠️ 只在 3.12 上完整验证过，建议换 3.12")
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
            urllib.request.urlopen(url.rstrip("/") + "/", timeout=8).read(1)
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
#
# 为什么不用 git clone
# --------------------
# 实测（关掉代理）：
#     github.com 首页              ✅ 200
#     github.com/.../archive/zip   ✅ 200   ← 能下
#     api.github.com               ✅ 200
#     git clone github.com         ❌ "Failed to connect ... after 21047 ms"
#
# 也就是说 **HTTPS 下载能通，git 协议连不上**（大概是 git 的传输层被单独
# 拦了，或者走了不同的链路）。既然 zip 能下，就用 zip —— 还省了用户装 git。
# --------------------------------------------------------------------------- #
ENGINES = [
    # (显示名, owner/repo, 下载后的目标目录)
    ("Seed-VC", "Plachtaa/seed-vc", SRC_SEEDVC),
    ("GPT-SoVITS", "RVC-Boss/GPT-SoVITS", TTS_ENGINE),
    ("RVC", "RVC-Project/Retrieval-based-Voice-Conversion-WebUI", RVC_ENGINE),
]

# zip 下载的三条路，依次尝试
def engine_zip_urls(slug: str) -> list[str]:
    return [
        f"https://github.com/{slug}/archive/refs/heads/main.zip",
        f"https://github.com/{slug}/archive/refs/heads/master.zip",
        f"https://ghproxy.net/https://github.com/{slug}/archive/refs/heads/main.zip",
        f"https://gh-proxy.com/https://github.com/{slug}/archive/refs/heads/main.zip",
    ]


def _download(url: str, dst: Path, label: str = "") -> bool:
    """下载文件，带进度。返回是否成功。"""
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "voiceconv-setup"})
        with urllib.request.urlopen(req, timeout=180) as r, open(dst, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            last = 0
            while True:
                chunk = r.read(1024 * 512)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total and got - last > 20 * 1024 * 1024:
                    last = got
                    say(f"    {got*100//total:3d}%  {got/1e6:.0f}/{total/1e6:.0f} MB")
        if dst.stat().st_size < 1024:
            return False
        return True
    except Exception as exc:                     # noqa: BLE001
        say(f"    失败：{type(exc).__name__}: {str(exc)[:80]}")
        return False


def _extract_zip_to(zip_path: Path, dest: Path) -> bool:
    """把 GitHub 的 archive zip 解压到 dest。

    GitHub 的 archive 会多套一层目录（如 `seed-vc-main/`），
    这里把那一层去掉，直接把内容放到 dest 下。
    """
    import zipfile

    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            if not names:
                return False
            # 找出共同的顶层目录
            tops = {n.split("/")[0] for n in names if n.split("/")[0]}
            top = tops.pop() if len(tops) == 1 else ""
            dest.mkdir(parents=True, exist_ok=True)
            for info in z.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if top and name.startswith(top + "/"):
                    name = name[len(top) + 1:]
                if not name:
                    continue
                out = dest / name
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(out, "wb") as f:
                    shutil.copyfileobj(src, f)
        return any(dest.iterdir())
    except Exception as exc:                     # noqa: BLE001
        say(f"  解压失败：{type(exc).__name__}: {exc}")
        return False


def fetch_engine(name: str, slug: str, dest: Path) -> bool:
    """下载并解压一个引擎。已存在则跳过。"""
    if dest.exists() and any(dest.iterdir()):
        n = sum(1 for _ in dest.rglob("*"))
        say(f"  {name} 已存在（{n} 项），跳过")
        return True

    LOGS.mkdir(parents=True, exist_ok=True)
    zip_path = LOGS / f"engine-{slug.split('/')[-1]}.zip"
    zip_path.unlink(missing_ok=True)          # 上次被打断留下的半截包

    for url in engine_zip_urls(slug):
        say(f"  {name}：{url.split('/')[2]}…")
        if _download(url, zip_path):
            say(f"    下载完成 {zip_path.stat().st_size/1e6:.1f} MB，解压…")
            if _extract_zip_to(zip_path, dest):
                zip_path.unlink(missing_ok=True)
                say(f"  ✅ {name} → {dest.relative_to(ROOT)}")
                return True
            say("    解压出来的目录是空的，换下一个源")
        zip_path.unlink(missing_ok=True)

    say(f"  ❌ {name} 所有源都失败")
    say(f"     可以手动下载 https://github.com/{slug}/archive/refs/heads/main.zip")
    say(f"     解压后把里面的内容放到：{dest}")
    return False


def step_engines() -> bool:
    head("4", "三套引擎源码（约 700 MB）")
    say("  用 zip 下载（不需要 git，也不需要代理）")
    ok = True
    for name, slug, dest in ENGINES:
        ok &= fetch_engine(name, slug, dest)
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
    else:
        say(f"  创建 {name}（复用 .venv 的 torch）…")
        path.parent.mkdir(parents=True, exist_ok=True)
        code, out = run([str(VENV_PY), "-m", "venv", "--system-site-packages",
                         str(path)])
        if code != 0:
            say(f"  ❌ {name} 创建失败")
            return False
    # 用 .pth 把 .venv 的 site-packages 暴露给它。
    # 每次启动都补写：目录被误删/搬动后，这里能自愈。
    pth = path / "Lib" / "site-packages" / "_shared_from_voice.pth"
    target = str(VENV / "Lib" / "site-packages")
    if not pth.exists() or pth.read_text(encoding="utf-8", errors="ignore").strip() != target:
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(target + "\n", encoding="utf-8")
        say(f"  ✅ {name}: 指向 .venv 的 .pth")
    return True


# --------------------------------------------------------------------------- #
# 引擎依赖：以内核自带的 requirements 为准
#
# 以前这里是手抄的包名清单，漏了 GPT-SoVITS 的文本前端（g2p_en / opencc /
# split_lang / funasr / ToJyutping …）和 RVC 的 MSST 音高后端（pymss /
# torchfcpe）。本机因为当年是手工照引擎文件装的，所以看不出来；用户从零
# 装完，朗读和 RVC 会 ImportError。现在直接读引擎自己那份。
# --------------------------------------------------------------------------- #

# 这些不装：torch 由 .venv 共用；jieba_fast / pyopenjtalk 没有 Windows
# wheel，6.4 会用纯 Python 转发顶上。
# torch-directml 也要滤掉 —— RVC 的 requirements 在 Windows 上会拉它，
# 而它钉死 torch==2.4.1，跟 50 系必需的 torch 2.7+cu128 直接冲突
# （DirectML 是 AMD 用的，本工具箱不支持 AMD）。
SKIP_DEPS = {
    "torch", "torchaudio", "torchvision", "triton", "torch_directml",
    "onnxruntime_directml",
    "jieba_fast", "pyopenjtalk", "pyopenjtalk_plus",
}

# 装完做一次 import 验证 —— 这些都是引擎真正会 import 到的东西
# modelscope 不进去：引擎运行时不 import 它（权重走 HTTP 下），而它的
# 依赖 oss2 / crcmod 要本地编译，用户没装 VS 编译器的机器上装不动。
TTS_VERIFY = ["transformers", "torch", "torchaudio", "funasr", "ctranslate2",
              "sentencepiece", "g2p_en", "opencc", "split_lang",
              "fast_langdetect", "x_transformers", "torchmetrics", "jieba"]
RVC_VERIFY = ["transformers", "torch", "pymss", "pymss_core", "torchfcpe",
              "sounddevice", "sklearn", "coloredlogs", "local_attention",
              "faiss", "torchcrepe", "gradio"]

# import 名和包名不一样的那几个（重试时要用）
MODULE_ALIAS = {"yaml": "pyyaml", "faiss": "faiss_cpu", "sklearn": "scikit_learn",
                "cv2": "opencv_python_headless", "pil": "pillow", "attr": "attrs"}

# 这几个包的依赖声明会把共用的 torch 顶掉（pymss 要 torch>=2.7.1，而
# .venv 里是 2.7.0+cu128）。所以它们单独用 --no-deps 装：功能只要 torch
# 在就行，不能让 pip 去重装 torch（那是 5.4 GB，而且大概率换成 CPU 版）。
NO_DEPS_PKGS = {"pymss", "pymss_core"}


def torch_constraint() -> Path | None:
    """写一份 constraints，把 torch/torchaudio 钉在已装版本上。"""
    code = ("import torch, torchaudio\n"
            "print(torch.__version__.split('+')[0], "
            "torchaudio.__version__.split('+')[0])\n")
    _, out = run([str(VENV_PY), "-c", code], check=False)
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    if not lines or len(lines[-1].split()) != 2:
        return None
    tv, av = lines[-1].split()
    path = LOGS / "constraints.txt"
    path.write_text(f"torch=={tv}\ntorchaudio=={av}\n", encoding="utf-8")
    return path


def pkg_name(line: str) -> str:
    """从一行 requirement 里抽出规范化的包名。"""
    return (re.split(r"[\s\[<>=!~;]", line, 1)[0]
            .lower().replace("-", "_").replace(".", "_"))


def req_lines(path: Path) -> list[str]:
    """读 requirements 文件：去掉注释、pip 选项行（--index-url 等）和禁用包。"""
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        if pkg_name(line) in SKIP_DEPS:
            continue
        out.append(line)
    return out


def venv_import(venv: Path, mods: list[str]) -> list[str]:
    """在指定环境里逐个 import，返回失败的模块名。

    用一个哨兵前缀认自己的输出 —— 子进程会混进 pip / matplotlib 的
    警告，不能直接取最后一行。
    """
    code = ("import importlib, sys\n"
            "bad = []\n"
            "for m in sys.argv[1:]:\n"
            "    try:\n"
            "        importlib.import_module(m)\n"
            "    except Exception:\n"
            "        bad.append(m)\n"
            "print('__BAD__' + '|'.join(bad))\n")
    _, out = run([str(venv / "Scripts" / "python.exe"), "-c", code, *mods],
                 check=False)
    for line in reversed(out.splitlines()):
        if line.strip().startswith("__BAD__"):
            return [x for x in line.strip()[len("__BAD__"):].split("|") if x]
    return mods


def pick_rvc_req() -> list[Path]:
    """RVC 仓库按 (CUDA, Python) 分了多个文件，只取**最匹配的那一份**。

    取多了会把 CUDA 版和 CPU 版的包混着装，onnxruntime 之类会被互相覆盖。
    """
    gpu = probe_gpu()
    cuda = {"12.8": "cu128", "12.1": "cu118", "11.8": "cu118"}.get(
        gpu["cuda_max"], "cu118")
    v = sys.version_info
    py = f"py{v.major}{v.minor}"
    for p in (RVC_ENGINE / f"requirments_{cuda}_{py}.txt",
              RVC_ENGINE / f"requirments_{cuda}_py312.txt",
              RVC_ENGINE / f"requirments_cpu_{py}.txt",
              RVC_ENGINE / "requirements.txt"):
        if p.exists():
            return [p]
    return []


def install_engine_deps(venv: Path, label: str, req_files: list[Path],
                        verify: list[str], mirror: str) -> bool:
    """装一个子环境的依赖：整批装 → 验 import → 缺的单个重试 → 再验。"""
    pkgs: list[str] = []
    for req in req_files:
        if req.exists():
            got = req_lines(req)
            say(f"    {req.name}：{len(got)} 项")
            pkgs += got
        else:
            say(f"    ⬜ 没找到 {req.name}，跳过")
    if not pkgs:
        say(f"  ❌ {label}：引擎依赖清单是空的（引擎没下全？）")
        return False

    LOGS.mkdir(parents=True, exist_ok=True)
    tmp = LOGS / f"req-{label}.txt"
    tmp.write_text("\n".join(pkgs) + "\n", encoding="utf-8")

    py = str(venv / "Scripts" / "python.exe")
    say(f"  装 {label} 依赖（{len(pkgs)} 项，几分钟）…")

    # 会顶掉共用 torch 的那几个放到最后单独装（理由见 NO_DEPS_PKGS）。
    # 顺序很重要：先装它们，pip 解算时会拿它们声明的 torch>=2.7.1 去和
    # 约束里的 2.7.0 打架，整批就装不动了。
    solo = [p for p in pkgs if pkg_name(p) in NO_DEPS_PKGS]
    rest = [p for p in pkgs if pkg_name(p) not in NO_DEPS_PKGS]
    tmp.write_text("\n".join(rest) + "\n", encoding="utf-8")

    # constraints 钉住 torch —— 防止被引擎依赖顺带升级成别的版本
    con = torch_constraint()
    extra = ["--constraint", str(con)] if con else []

    code, _ = run([py, "-m", "pip", "install", "--progress-bar", "off",
                   "--no-input", "-r", str(tmp), "--index-url", mirror, *extra])
    if code != 0:
        # 常见两类失败：① 环境里躺着一个不该在的包（如 torch-directml）
        # 把依赖解算搞成 ResolutionImpossible；② 某个包没有现成 wheel。
        # 退一步：不让 pip 解依赖，先装进去，后面用 import 验证兜底。
        say("    整批装没过，改用 --no-deps 再试一次…")
        run([py, "-m", "pip", "install", "--progress-bar", "off",
             "--no-input", "--no-deps", "-r", str(tmp), "--index-url", mirror,
             *extra])

    for one in solo:
        run([py, "-m", "pip", "install", "--progress-bar", "off", "--no-input",
             "--no-deps", one, "--index-url", mirror], check=False)

    bad = venv_import(venv, verify)
    if bad:
        say(f"    缺 {len(bad)} 个：{', '.join(bad)}，按单个包重试")
        want = {MODULE_ALIAS.get(b, b) for b in bad}
        retry = [p for p in pkgs if pkg_name(p) in want]
        for one in retry:
            c, _ = run([py, "-m", "pip", "install", "--progress-bar", "off",
                        "--no-input", one, "--index-url", mirror, *extra],
                       check=False)
            if c != 0:
                run([py, "-m", "pip", "install", "--progress-bar", "off",
                     "--no-input", "--no-deps", one, "--index-url", mirror],
                    check=False)
        if "opencc" in bad:          # 没编译环境时退回纯 Python 实现
            run([py, "-m", "pip", "install", "--progress-bar", "off",
                 "--no-input", "opencc-python-reimplemented",
                 "--index-url", mirror], check=False)
        bad = venv_import(venv, verify)

    if bad:
        say(f"  ❌ {label} 依赖仍缺：{', '.join(bad)}")
        say(f"     手动补：{venv.name} 环境里 pip install <包名>")
        return False
    say(f"  ✅ {label} 依赖齐全（{len(verify)} 个关键 import 全过）")
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

    # --- 6.3 GPT-SoVITS 的依赖（吃引擎自带的 requirements.txt） ---
    say()
    mirror = pick_mirror()
    ok &= install_engine_deps(TTS_VENV, "朗读",
                              [TTS_ENGINE / "requirements.txt"],
                              TTS_VERIFY, mirror)

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

    # --- 6.5 RVC 环境依赖（吃引擎自带的 requirments_<cuda>_py<ver>.txt） ---
    say()
    ok &= install_engine_deps(RVC_VENV, "RVC", pick_rvc_req(),
                              RVC_VERIFY, mirror)

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

    state = load_state()
    done = set(state.get("done", []))
    fps = dict(state.get("fp", {}))
    t0 = time.time()
    results: dict[str, object] = {}

    for step in plan:
        if step != "check" and step in done:
            fp_fn = FP_STEPS.get(step)
            if fp_fn is None:
                say(f"\n  [{step}] 之前已完成，跳过")
                results[step] = "skip"
                continue
            now = fp_fn()
            if now == fps.get(step):
                say(f"\n  [{step}] 依赖没变，跳过")
                results[step] = "skip"
                continue
            say(f"\n  [{step}] 依赖有变化，重新执行")
        else:
            fp_fn = FP_STEPS.get(step)

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
            mark_done(step, fp_fn() if fp_fn else None)
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
    for step, r in results.items():
        if r == "skip":
            say(f"  ⏭  {STEPS[step][0]}（已是最新，跳过）")
        else:
            say(f"  {'✅' if r else '❌'} {STEPS[step][0]}")
    say()
    say(" 下一步：双击  启动.bat")
    say()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
