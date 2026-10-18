"""一键更新 —— 拉最新代码、覆盖、再跑一遍 setup.py 的增量。

为什么要有这个
--------------
手工升级要"解压到临时目录 → 只复制里边的内容 → 选替换 → 不能多一层目录"，
四步里任何一步错都是"以为升级了其实没有"。这个脚本把四步合成一次双击。

它**只覆盖 zip 里的代码文件**（45 个），不会碰：
    .venv\\  models\\  bin\\  src\\  tools\\*\\engine\\  tools\\*\\venv\\
    data\\presets\\  data\\links.json  data\\sites.local.json  io\\output\\
这些目录和文件本来就不在发布包里，覆盖时也就无从删起。

用法
----
    双击 升级.bat                       # 从 GitHub 拉最新 + 覆盖 + 跑 setup.py
    python update.py --check            # 只看有没有新版，不动任何文件
    python update.py --zip D:\\x.zip     # 用自己下好的 zip（离线 / GitHub 连不上时）
    python update.py --no-setup         # 只更新代码，不跑 setup.py
    python update.py --branch main      # 换分支（默认 main）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"

REPO = "aitujiang/voiceconv"
REQUIRED = ("setup.py", "launcher.py", "shared/paths.py", "启动.bat")


def say(msg: str = "") -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- 版本 --
def read_version(base: Path) -> str:
    """从 <base>/shared/paths.py 里抠 APP_VERSION。"""
    p = base / "shared" / "paths.py"
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ""


# ------------------------------------------------------------ 下载/解压 --
def zip_urls(branch: str) -> list[str]:
    base = f"https://github.com/{REPO}/archive/refs/heads/{branch}.zip"
    return [base,
            f"https://ghproxy.net/{base}",
            f"https://gh-proxy.com/{base}"]


def download(branch: str, dst: Path) -> bool:
    for url in zip_urls(branch):
        host = url.split("/")[2]
        say(f"  下载中（{host}）…")
        try:
            with urllib.request.urlopen(url, timeout=90) as r, \
                    open(dst, "wb") as f:
                shutil.copyfileobj(r, f)
            size = dst.stat().st_size
            if size < 100_000:
                say(f"    文件太小（{size} 字节），不像源码包，换下一个源")
                dst.unlink(missing_ok=True)
                continue
            say(f"    {size/1e6:.2f} MB")
            return True
        except Exception as exc:                     # noqa: BLE001
            say(f"    失败：{type(exc).__name__}: {exc}")
            dst.unlink(missing_ok=True)
    return False


def extract(zip_path: Path, dest: Path) -> Path | None:
    """解压并找到真正的项目根（GitHub 的 zip 会多包一层 repo-main\\）。"""
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)
    except Exception as exc:                         # noqa: BLE001
        say(f"  解压失败：{type(exc).__name__}: {exc}")
        return None

    for cand in [dest, *[p for p in dest.iterdir() if p.is_dir()]]:
        if all((cand / rel).exists() for rel in REQUIRED):
            return cand
    return None


# -------------------------------------------------------------- 覆盖 --
def apply_code(new_root: Path) -> tuple[int, int]:
    """把 new_root 下的文件覆盖到 ROOT。返回 (改动数, 总数)。"""
    changed = total = 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = LOGS / "code-backup" / stamp

    for src in new_root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(new_root)
        if rel.parts[0] in (".git", "logs"):
            continue
        dst = ROOT / rel
        total += 1
        try:
            same = dst.exists() and dst.read_bytes() == src.read_bytes()
        except OSError:
            same = False
        if same:
            continue
        if dst.exists():                             # 覆盖前留一份，方便回退
            b = backup / rel
            b.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(dst, b)
            except OSError:
                pass
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        changed += 1
    return changed, total


# -------------------------------------------------------------- 主流程 --
def main() -> int:
    ap = argparse.ArgumentParser(description="素材工具箱 一键更新")
    ap.add_argument("--zip", help="用本地 zip，不去 GitHub 下载")
    ap.add_argument("--branch", default="main", help="分支（默认 main）")
    ap.add_argument("--check", action="store_true", help="只检查版本，不动文件")
    ap.add_argument("--no-setup", action="store_true", help="更新后不跑 setup.py")
    args, extra = ap.parse_known_args()
    if not args.zip:
        # 允许把 zip 直接拖到 升级.bat 上：多出来的那个参数就是 zip 路径
        for item in extra:
            if item.lower().endswith(".zip"):
                args.zip = item
                break
        else:
            if extra:
                say(f"❓ 看不懂这个参数：{extra[0]}")
                say("   用法：双击 升级.bat，或把下载好的 zip 拖到 升级.bat 上")
                return 2

    cur = read_version(ROOT)
    say("=" * 62)
    say(f" 素材工具箱 · 一键更新   当前 v{cur or '?'}")
    say("=" * 62)

    tmp = Path(tempfile.mkdtemp(prefix="vc-update-"))
    try:
        if args.zip:
            zip_path = Path(args.zip).expanduser().resolve()
            if not zip_path.exists():
                say(f"❌ 找不到 {zip_path}")
                return 1
            say(f"  用本地包：{zip_path.name}")
        else:
            zip_path = tmp / "update.zip"
            say(f"  从 GitHub 取最新（{args.branch}）…")
            if not download(args.branch, zip_path):
                say("❌ 三个源都失败。可以把 zip 下到本地，再跑：")
                say("     python update.py --zip 路径\\voiceconv-main.zip")
                return 1

        say("  解压…")
        new_root = extract(zip_path, tmp / "unpacked")
        if not new_root:
            say("❌ 这不是本项目的源码包（缺 setup.py / launcher.py / 启动.bat）")
            return 1

        new = read_version(new_root)
        say(f"  版本：本地 v{cur or '?'} → 新的 v{new or '?'}")
        if args.check:
            if cur and new and cur == new:
                say("  ✅ 已是最新")
            else:
                say(f"  ⬆ 有新版本可更新：v{new}")
            return 0

        changed, total = apply_code(new_root)
        say()
        say(f"  覆盖完成：{total} 个文件里改了 {changed} 个")
        if changed == 0:
            say("  （内容完全一致，你已经是最新版）")
        else:
            say(f"  旧文件已备份到 logs\\code-backup\\（要回退就从那儿拷回来）")

        if not args.no_setup:
            py = ROOT / ".venv" / "Scripts" / "python.exe"
            exe = str(py) if py.exists() else sys.executable
            say()
            say("  跑 setup.py 检查环境（只装变化的部分）…")
            say("-" * 62)
            code = subprocess.call([exe, str(ROOT / "setup.py"), "--yes"], cwd=str(ROOT))
            say("-" * 62)
            if code != 0:
                say("  ⚠️ setup.py 没跑通，按上面的提示处理后重跑：python setup.py")

        say()
        say("=" * 62)
        say("  更新完成，双击  启动.bat  使用")
        say("=" * 62)
        return 0
    except PermissionError as exc:
        say(f"❌ 文件被占用：{exc.filename}")
        say("   请先退出工具箱（含后台的引擎窗口），再运行一次")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
