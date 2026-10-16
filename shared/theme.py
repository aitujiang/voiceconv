"""界面主题 —— 颜色和字体的单一来源。

以前 oneclick.py 里散着一堆 "#0f1720" 这样的字面量，加第二个工具就得复制一遍。
集中到这里以后，改配色只改这一个文件，所有工具跟着变。
"""
from __future__ import annotations

# ---- 背景层次（由深到浅）----
BG = "#0f1720"          # 窗口底色
CARD = "#16212c"        # 卡片
CARD_HOVER = "#1d2b39"  # 卡片悬停
CARD_OFF = "#131a22"    # 禁用卡片（比 CARD 更暗，一眼看出不可点）
PANEL = "#1a2836"       # 内嵌面板

# ---- 文字 ----
FG = "#e8eef5"          # 主文字
MUTED = "#8fa3b8"       # 次要文字
DIM = "#5f7385"         # 更弱的说明文字
OFF_TEXT = "#4a5a6a"    # 禁用卡片的文字

# ---- 强调色 ----
ACCENT = "#3ea6ff"      # 主按钮 / 选中
ACCENT_HOVER = "#6cc0ff"
OK = "#38d39f"          # 成功 / 可用
WARN = "#ffb454"        # 警告 / 处理中
ERR = "#ff7b72"         # 错误
GREY = "#5f7385"        # 未开发（灰显）
RED = "#ff7b72"         # 环境缺失（和 ERR 同色，留个别名方便语义化使用）

# ---- 字体 ----
FONT = "Microsoft YaHei UI"
F_TITLE = (FONT, 17, "bold")
F_H2 = (FONT, 12, "bold")
F_CARD = (FONT, 10, "bold")
F_BODY = (FONT, 9)
F_SMALL = (FONT, 8)

# ---- 尺寸 ----
PAD = 16                # 通用外边距
CARD_W = 145            # 工具卡片宽
CARD_H = 116            # 工具卡片高（固定，避免文字行数不同导致参差）


def button(parent, text, command, *, primary=False, small=False):
    """统一风格的按钮，免得每个界面各写一套参数。"""
    import tkinter as tk
    if primary:
        bg, fg, active = ACCENT, "#04121f", ACCENT_HOVER
    else:
        bg, fg, active = "#22303d", FG, "#2c3d4d"
    return tk.Button(
        parent, text=text, command=command,
        bg=bg, fg=fg, activebackground=active, activeforeground=fg,
        font=(FONT, 9 if small else 10, "bold" if primary else "normal"),
        relief="flat", bd=0, cursor="hand2",
        padx=10 if small else 16, pady=4 if small else 7,
    )
