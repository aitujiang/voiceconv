"""外部软件链接 —— 用户在启动器里自己添加的程序/文件夹。

为什么需要这个
--------------
工具箱内置的工具是我们写的，但用户还会用别的软件（ComfyUI、剪辑软件…）。
与其让他们去桌面找图标，不如把入口放进启动器。

**不预填任何东西** —— 每个人的软件都不一样，预填只会留下一堆
"找不到文件"的坏卡片。空列表 + 明确的添加入口才是对的。

数据存在 data\\links.json（不进版本库，因为里面是用户自己的路径）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 支持两种调用方式：
#   ① 作为包被导入（from shared import links）—— 用相对导入
#   ② 直接跑这个文件做自检（python shared\links.py）—— 走下面的兜底
try:
    from . import paths
except ImportError:                                   # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from shared import paths                          # type: ignore[no-redef]

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# 目标类型
KIND_AUTO = "auto"          # 按后缀/存在性自动判断
KIND_FOLDER = "folder"      # 文件夹


@dataclass
class Link:
    """一条外部软件链接。"""

    id: str
    name: str                       # 显示名
    target: str                     # 文件路径
    kind: str = KIND_AUTO
    created: float = field(default_factory=time.time)

    @property
    def path(self) -> Path:
        return Path(self.target)

    def is_folder(self) -> bool:
        return self.kind == KIND_FOLDER or self.path.is_dir()

    def exists(self) -> bool:
        return bool(self.target) and self.path.exists()

    def problem(self) -> str:
        """返回不可用的原因，可用时返回空串。"""
        if not (self.target or "").strip():
            return "没有设置路径"
        if not self.exists():
            return f"找不到：{self.target}"
        return ""

    def icon(self) -> str:
        """按类型/名字猜一个 emoji。

        没有图标选择器（用户说了不要），所以这里猜：
        先按名字里的关键词，再按文件类型兜底。
        """
        if self.is_folder():
            return "📁"
        low = (self.name + " " + self.target).lower()
        guess = (
            (("comfy", "绘图", "stable", "diffus", "sd"), "🎨"),
            (("剪", "video", "剪辑", "premiere", "剪映"), "🎬"),
            (("game", "游戏", "steam"), "🎮"),
            (("music", "音乐", "audio", "音频", "daw", "宿主"), "🎵"),
            (("chat", "gpt", "ai", "llm"), "🤖"),
            (("code", "vscode", "编辑器", "ide"), "💻"),
            (("browser", "chrome", "edge", "firefox", "浏览器"), "🌐"),
            (("record", "录屏", "obs", "capture"), "🎥"),
            (("photo", "image", "图片", "ps", "修图"), "🖼"),
        )
        for keys, emoji in guess:
            if any(k in low for k in keys):
                return emoji
        # 按后缀兜底
        suf = self.path.suffix.lower()
        if suf in (".bat", ".cmd"):
            return "▶"
        if suf == ".exe":
            return "🔧"
        return "🔗"

    def summary(self) -> str:
        return "文件夹" if self.is_folder() else self.path.name


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #
def _read_raw() -> list[dict]:
    p = paths.LINKS_JSON
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        data = data.get("links", [])
    return [d for d in data if isinstance(d, dict)]


def load_all() -> list[Link]:
    """读出全部链接。单条坏了只跳过它。"""
    out: list[Link] = []
    for d in _read_raw():
        try:
            out.append(Link(
                id=str(d.get("id") or uuid.uuid4().hex[:12]),
                name=str(d.get("name") or "未命名"),
                target=str(d.get("target") or ""),
                kind=str(d.get("kind") or KIND_AUTO),
                created=float(d.get("created") or time.time()),
            ))
        except (TypeError, ValueError):
            continue
    return out


def save_all(items: list[Link]) -> None:
    paths.DATA.mkdir(parents=True, exist_ok=True)
    tmp = paths.LINKS_JSON.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=1),
        encoding="utf-8")
    tmp.replace(paths.LINKS_JSON)


def new_link(name: str, target: str, kind: str = KIND_AUTO) -> Link:
    return Link(id=uuid.uuid4().hex[:12], name=name, target=target, kind=kind)


def add(item: Link) -> None:
    items = load_all()
    items.append(item)
    save_all(items)


def update(item: Link) -> None:
    items = load_all()
    for i, x in enumerate(items):
        if x.id == item.id:
            items[i] = item
            break
    else:
        items.append(item)
    save_all(items)


def delete(link_id: str) -> None:
    save_all([x for x in load_all() if x.id != link_id])


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #
def launch(item: Link) -> tuple[bool, str]:
    """打开这条链接。返回 (成功, 说明)。"""
    why = item.problem()
    if why:
        return False, why

    target = item.path

    # ---- 文件夹：交给资源管理器 ----
    if item.is_folder():
        from . import opener
        return opener.open_path(target)

    suffix = target.suffix.lower()

    try:
        if suffix == ".lnk":
            os.startfile(str(target))                # noqa: S606
            return True, f"已启动 {item.name}"

        if suffix in (".bat", ".cmd"):
            # 批处理要独立控制台窗口，否则跑完就没了、也看不到输出
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(target)],
                cwd=str(target.parent),
                creationflags=subprocess.CREATE_NEW_CONSOLE, shell=False)
            return True, f"已启动 {item.name}"

        # exe 及其他
        subprocess.Popen([str(target)], cwd=str(target.parent),
                         creationflags=_NO_WINDOW, shell=False)
        return True, f"已启动 {item.name}"

    except FileNotFoundError as exc:
        return False, f"找不到文件：{exc}"
    except OSError as exc:
        # 没有执行权限之类：退回系统关联（等于双击）
        try:
            os.startfile(str(target))                # noqa: S606
            return True, f"已交给系统打开 {item.name}"
        except OSError:
            return False, f"启动失败：{exc}"


def _selftest() -> int:
    print("=" * 58)
    print(" 外部链接模块自检")
    print("=" * 58)
    paths.ensure_dirs()

    items = load_all()
    print(f" 现有链接: {len(items)} 条")
    for x in items:
        print(f"   {x.icon()} {x.name:20} {x.problem() or '可用'}")

    # 测图标猜测
    print("\n 图标识别:")
    for name, tgt in (("ComfyUI", r"D:\x\启动器.exe"),
                      ("剪映", r"C:\x\JianyingPro.exe"),
                      ("我的素材", r"D:\素材")):
        l = Link(id="t", name=name, target=tgt)
        print(f"   {name:12} -> {l.icon()}")

    print("=" * 58)
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
