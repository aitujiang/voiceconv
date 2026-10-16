"""AI 变声工具入口。

这个文件**不重新实现界面**，只是把已经验证过的 oneclick.py 包一层：

* 补上工具箱要求的 `--embedded` 参数
* 传入 `on_back`，让变声窗口出现「← 返回工具箱」按钮
* 作为独立进程被 launcher 启动，退出后 launcher 会自动重新出现

之所以不重写：oneclick.py 里的变声流程（三种模式、进度泵、错误提示、
--auto 回归测试）已经跑通并验证过，重写只会引入新 bug。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # ...\voiceconv
sys.path.insert(0, str(ROOT))

import oneclick                                    # noqa: E402
from shared import paths                           # noqa: E402


def launch(parent: tk.Tk | None = None, on_back=None) -> int:
    """打开变声窗口。

    parent / on_back 是为「被工具箱调用」准备的：以后要嵌进别的窗口，
    或者换一种返回方式，改这里就行，不用动 oneclick.py。
    """
    root = tk.Tk()

    def _back() -> None:
        if callable(on_back):
            on_back()
        root.destroy()

    oneclick.App(root, [], on_back=_back)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    embedded = "--embedded" in argv
    paths.ensure_dirs()
    # 被工具箱启动时（--embedded）给一个返回按钮；直接运行时不显示
    return launch(on_back=(lambda: None) if embedded else None)


if __name__ == "__main__":
    raise SystemExit(main())
