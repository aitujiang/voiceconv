"""打开文件 / 文件夹 / 网址 —— 多级兜底。

为什么需要这个模块
------------------
`os.startfile()` 在 Windows 上走 ShellExecuteW。实测发现存在这种组合：

    普通 python.exe        -> 成功
    venv 的 pythonw.exe    -> **WinError 5 拒绝访问**
    普通 pythonw.exe       -> 成功

也就是说「用哪个解释器」会影响 ShellExecuteW 能否拿到桌面会话。
为了不让用户看到「拒绝访问」这种没法处理的报错，这里做多级兜底：
一种方式失败就换下一种，全失败才报错，并且**至少把路径塞进剪贴板**，
让用户能自己粘到资源管理器里。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _try_explorer(target: Path, select: bool) -> bool:
    """直接用 explorer.exe 打开（或定位到某个文件）。"""
    try:
        if select:
            subprocess.Popen(["explorer", f"/select,{target}"], close_fds=True)
        else:
            subprocess.Popen(["explorer", str(target)], close_fds=True)
        return True
    except Exception:                       # noqa: BLE001
        return False


def _try_cmd_start(target: Path) -> bool:
    """退一步：交给 cmd 的 start 命令。"""
    try:
        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(["cmd", "/c", "start", "", str(target)],
                         creationflags=creation, close_fds=True)
        return True
    except Exception:                       # noqa: BLE001
        return False


def _copy_to_clipboard(text: str) -> bool:
    """最后一招：把路径放进剪贴板，用户手动粘到地址栏就能到。"""
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return True
    except Exception:                       # noqa: BLE001
        return False


def open_path(path: str | Path, select: bool = False) -> tuple[bool, str]:
    """打开一个文件或文件夹。

    返回 (成功与否, 说明)。调用方可以据此决定要不要提示用户。
    `select=True` 时尽量在资源管理器里**选中**该文件而不是打开它。
    """
    target = Path(path)
    if not target.exists():
        return False, f"路径不存在：{target}"

    # 1) 首选系统默认方式（文件夹会开资源管理器，文件会用默认程序打开）
    if not select:
        try:
            os.startfile(str(target))       # noqa: S606
            return True, "ok"
        except Exception:                   # noqa: BLE001
            pass

    # 2) 直接调 explorer.exe
    if _try_explorer(target, select):
        return True, "explorer"

    # 3) 交给 cmd start
    if _try_cmd_start(target):
        return True, "cmd-start"

    # 4) 全失败：至少给出路径，并尝试复制到剪贴板
    copied = _copy_to_clipboard(str(target))
    hint = "路径已复制到剪贴板，可以粘到资源管理器地址栏。" if copied \
        else "请手动打开下面的路径。"
    return False, f"打不开这个位置。\n\n{target}\n\n{hint}"


def open_url(url: str) -> tuple[bool, str]:
    """用默认浏览器打开网址（这个不涉及资源管理器，一般不会失败）。"""
    import webbrowser
    try:
        ok = webbrowser.open(url)
        return bool(ok), "ok" if ok else "浏览器没有响应"
    except Exception as exc:                # noqa: BLE001
        return False, str(exc)
