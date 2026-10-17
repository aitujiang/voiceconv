"""素材工具箱 —— 总启动器。

双击「启动.bat」看到的就是这个窗口。

设计要点
--------
* **工具是独立进程**：某个工具崩了不会带崩工具箱，而且各工具可以用自己的
  Python 环境（变声要 torch，下载只要个小环境）。
* **没开发的功能也有卡片**：点它会弹一个「这个工具是干什么的、打算怎么做、
  什么时候做」的说明窗，而不是死按钮。这样界面布局一次定好，以后加工具
  只是把状态从「未开发」改成「可用」，不用重排。
* **刻意做小**：只用标准库 + tkinter，新代码几十 KB，不引入任何依赖。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared import paths, theme                                  # noqa: E402
from shared import opener                                        # noqa: E402
from shared import vram                                          # noqa: E402
from shared.tools_registry import (GROUPS, TOOLS, Tool, all_tools,  # noqa: E402
                                   module_available)


class ToolCard(tk.Frame):
    """一个工具卡片。

    可用 -> 点击启动
    未开发 -> 点击弹出说明窗口（讲清楚这是什么、为什么要做、大概怎么做）
    环境缺失 -> 点击列出缺什么
    """

    def __init__(self, parent, tool: Tool, on_click, on_right_click=None):
        self.tool = tool
        self.on_click = on_click
        self.on_right_click = on_right_click
        # 外部软件没有 Python 模块，只看文件在不在
        if tool.is_external:
            self.enabled = tool.ready and tool.external.exists()
        else:
            self.enabled = tool.ready and not tool.missing_requirements() \
                and module_available(tool.module)

        bg = theme.CARD if self.enabled else theme.CARD_OFF
        super().__init__(parent, bg=bg, highlightthickness=1,
                         highlightbackground=theme.CARD_HOVER if self.enabled else bg,
                         cursor="hand2" if self.enabled else "arrow")
        self._bg = bg
        # 固定高度：说明文字有一行的也有两行的，不锁高就会参差不齐
        self.configure(height=theme.CARD_H)
        self.grid_propagate(False)
        self.pack_propagate(False)

        box = tk.Frame(self, bg=bg)
        box.pack(fill="both", expand=True, padx=10, pady=8)

        top = tk.Frame(box, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=tool.icon, bg=bg, font=("Segoe UI Emoji", 15)).pack(side="left")
        tk.Label(top, text=tool.title, bg=bg,
                 fg=theme.FG if self.enabled else theme.OFF_TEXT,
                 font=theme.F_CARD).pack(side="left", padx=(6, 0))

        tk.Label(box, text=tool.subtitle, bg=bg,
                 fg=theme.MUTED if self.enabled else theme.DIM,
                 font=theme.F_SMALL, wraplength=150, justify="left",
                 anchor="w").pack(fill="x", pady=(5, 0))

        if self.enabled:
            label, colour = "可用", theme.OK
        elif not tool.ready:
            label, colour = "未开发", theme.GREY
        else:
            label, colour = "环境缺失", theme.ERR

        tk.Label(box, text=label, bg=bg, fg=colour,
                 font=theme.F_SMALL).pack(anchor="w", pady=(4, 0))

        # 整张卡片（含所有子控件）都要能点
        for w in self._walk():
            w.bind("<Button-1>", self._clicked)
            # 右键：只有外部软件有菜单（修改/删除）
            w.bind("<Button-3>", self._right_clicked)
            if self.enabled:
                w.bind("<Enter>", lambda _e: self._hover(True))
                w.bind("<Leave>", lambda _e: self._hover(False))

    def _right_clicked(self, event):
        if self.on_right_click is not None:
            self.on_right_click(self.tool, event)

    def _walk(self):
        stack = [self]
        while stack:
            w = stack.pop()
            yield w
            stack.extend(w.winfo_children())

    def _hover(self, on: bool) -> None:
        colour = theme.CARD_HOVER if on else self._bg
        for w in self._walk():
            try:
                w.configure(bg=colour)
            except tk.TclError:
                pass

    def _clicked(self, _event=None) -> None:
        if self.enabled:
            self.on_click(self.tool)
        else:
            self._explain()

    def _explain(self) -> None:
        """给未开发的功能弹一个正经的说明窗，而不是死链接。"""
        t = self.tool
        missing = t.missing_requirements()
        lines = [f"{t.icon}  {t.title}", "", t.subtitle, ""]

        if not t.ready:
            lines += ["── 状态 ──", "还没开发，位置已预留。", ""]
            detail = TOOL_DETAIL.get(t.key)
            if detail:
                lines += ["── 这个工具会做什么 ──"]
                lines += [f"· {d}" for d in detail]
            if t.notes:
                lines += ["", "── 说明 ──", t.notes]
        else:
            lines += ["── 状态 ──", "已开发，但运行环境不完整。", "",
                      "── 缺少 ──"]
            lines += [f"· {m}" for m in missing]

        messagebox.showinfo(f"{t.title}（{t.status()[0]}）", "\n".join(lines))


# 每个未开发功能「将会做什么」的说明。放在这里而不是注册表里，
# 因为注册表要保持精简，这些文案只在弹窗时用。
TOOL_DETAIL: dict[str, list[str]] = {
    "download": [
        "粘贴 B站链接（支持一次贴多个）批量下载",
        "配置 cookie 后可以下 1080P 及以上",
        "可以只下音频，不存视频，省磁盘",
        "失败自动重试、支持断点续传",
        "以后再加 YouTube / 抖音 / 小红书（那几个反爬更严）",
    ],
    "sites": [
        "把常用素材站的链接收在一个窗口里",
        "点一下就打开浏览器",
        "可以自己增删，列表存在 data\\sites.json",
    ],
    "voices": [
        "第一层 素材库：你下载的原始配音文件",
        "第二层 参考音频：挑一条给零样本变声用",
        "第三层 训练模型：精调后的模型，变声和朗读各自一套",
        "素材被移动或删除时会明确提示，让你重新指定",
    ],
    "presets": [
        "把「处理类型 + 输入文件夹 + 输出文件夹」也存下来",
        "点一下就跑完整个流程，不用每次重选文件夹",
        "例如「每周二创」：音色=梦前辈 · 文件夹批量 · 响度统一开",
    ],
    "tts": [
        "打字或粘贴台词，用角色声音念出来",
        "两种模式：零样本（用参考音频）/ 精调（用训练模型）",
        "打算用 GPT-SoVITS（中文效果最好，而且能用你的素材精调）",
        "风险最高：需要再装一套 PyTorch，可能和现有环境冲突",
        "打算优先尝试和变声共用一套 torch，能省约 6GB",
    ],
    "edit": ["裁剪、拼接视频片段"],
    "subtitle": ["从视频提取字幕 / 自动生成字幕 / 翻译字幕"],
    "image": ["图片缩放、格式转换、批量处理"],
    "record": ["录制屏幕操作"],
    "convert": ["视频与音频的格式转换、码率调整"],
}


class Launcher:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.proc: subprocess.Popen | None = None
        self._gpu_procs: list[vram.GpuProc] = []
        self._gpu_after: str | None = None

        root.title(paths.APP_NAME)
        root.configure(bg=theme.BG)
        root.geometry("710x640")
        root.minsize(680, 600)

        self._build()
        root.after(300, self._watch_child)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self) -> None:
        # 先取消显存刷新定时器：窗口销毁后它还会触发，去碰已不存在的控件
        # 会抛 TclError: invalid command name
        try:
            if self._gpu_after:
                self.root.after_cancel(self._gpu_after)
        except tk.TclError:
            pass
        self.root.destroy()

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 0))
        tk.Label(head, text=f"🧰 {paths.APP_NAME}", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(side="left")
        tk.Label(head, text=f"v{paths.APP_VERSION}", bg=theme.BG, fg=theme.DIM,
                 font=theme.F_SMALL).pack(side="left", padx=(6, 0), pady=(6, 0))
        theme.button(head, "成品目录", self.open_output, small=True).pack(side="right")

        tk.Label(self.root, text="点卡片打开工具。灰色的还没开发，点了可以看它打算做什么。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(
            anchor="w", padx=theme.PAD, pady=(6, 0))

        body = tk.Frame(self.root, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(10, 0))
        self._body = body

        self._render_groups()


        # ---- 底部：显存状态条 ----
        # 放在成品目录上面一行，常驻显示，每 2 秒刷新。
        # 用 nvidia-smi 读整卡占用（能看到别的进程），不是 torch 的自报数。
        gpu = tk.Frame(self.root, bg=theme.PANEL)
        gpu.pack(fill="x", side="bottom")
        tk.Frame(gpu, bg="#243444", height=1).pack(fill="x")

        row = tk.Frame(gpu, bg=theme.PANEL)
        row.pack(fill="x", padx=10, pady=6)
        self.gpu_lbl = tk.Label(row, text="显存  读取中…", bg=theme.PANEL,
                                fg=theme.MUTED, font=theme.F_SMALL, anchor="w")
        self.gpu_lbl.pack(side="left")
        self.gpu_btn = theme.button(row, "显存详情", self.show_gpu_detail, small=True)
        self.gpu_btn.pack(side="right")

        foot = tk.Frame(self.root, bg=theme.PANEL)
        foot.pack(fill="x", side="bottom")
        tk.Label(foot, text=f"  成品都放在  {paths.OUTPUT}",
                 bg=theme.PANEL, fg=theme.DIM, font=theme.F_SMALL,
                 anchor="w").pack(fill="x", pady=7)

        self._tick_gpu()

    # ---------------------------------------------------------- 卡片渲染 --
    def _render_groups(self) -> None:
        """重新渲染所有分组。添加/删除外部软件后调用。"""
        for w in self._body.winfo_children():
            w.destroy()

        tools = all_tools()
        for group in GROUPS:
            items = [t for t in tools if t.group == group]

            row = tk.Frame(self._body, bg=theme.BG)
            row.pack(fill="x", pady=(0, 8))

            head = tk.Frame(row, bg=theme.BG)
            head.pack(side="left", anchor="n", pady=(8, 0))
            tk.Label(head, text=group, bg=theme.BG, fg=theme.ACCENT,
                     font=theme.F_H2, width=6, anchor="w").pack(anchor="w")
            # 外部软件组给一个添加入口
            if group == "外部软件":
                theme.button(head, "＋ 添加", self.add_link, small=True).pack(
                    anchor="w", pady=(4, 0))

            grid = tk.Frame(row, bg=theme.BG)
            grid.pack(side="left", fill="x", expand=True)

            if not items:
                # 空组：给一句说明，别让人以为是坏了
                hint = ("还没有添加软件。点左边「＋ 添加」，"
                        "选一个 .exe / .bat，或者一个文件夹。"
                        if group == "外部软件" else "（暂无）")
                tk.Label(grid, text=hint, bg=theme.BG, fg=theme.DIM,
                         font=theme.F_SMALL, anchor="w", justify="left",
                         wraplength=520).pack(anchor="w", pady=(10, 0), padx=4)
                continue

            for i, tool in enumerate(items):
                card = ToolCard(grid, tool, self.open_tool,
                                on_right_click=self._card_menu)
                card.grid(row=i // 4, column=i % 4, padx=4, pady=(0, 8),
                          sticky="nsew")
            for c in range(4):
                grid.columnconfigure(c, weight=1, uniform="toolcol", minsize=138)

    def _card_menu(self, tool: Tool, event) -> None:
        """外部软件卡片上的右键菜单：修改 / 删除。内置工具没有菜单。"""
        if not tool.link_id:
            return
        from shared import links as link_store

        m = tk.Menu(self.root, tearoff=0, bg=theme.CARD, fg=theme.FG,
                    activebackground=theme.ACCENT, activeforeground="#04121f",
                    font=theme.F_SMALL, bd=0)
        m.add_command(label="修改", command=lambda: self.edit_link(tool.link_id))
        m.add_command(label="删除", command=lambda: self.delete_link(tool.link_id))
        m.add_separator()
        item = next((x for x in link_store.load_all() if x.id == tool.link_id), None)
        if item and item.exists() and not item.is_folder():
            m.add_command(label="打开所在文件夹",
                          command=lambda: opener.open_path(item.path.parent))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    # ------------------------------------------------------ 外部软件管理 --
    def add_link(self) -> None:
        LinkDialog(self.root, on_done=self._render_groups)

    def edit_link(self, link_id: str) -> None:
        from shared import links as link_store
        item = next((x for x in link_store.load_all() if x.id == link_id), None)
        if item:
            LinkDialog(self.root, item=item, on_done=self._render_groups)

    def delete_link(self, link_id: str) -> None:
        from shared import links as link_store
        item = next((x for x in link_store.load_all() if x.id == link_id), None)
        if not item:
            return
        if messagebox.askyesno("删除", f"确定删除「{item.name}」？\n\n"
                                       f"只是从列表里移除，不会删掉程序本身。"):
            link_store.delete(link_id)
            self._render_groups()

    # ---------------------------------------------------------- 显存状态 --
    def _tick_gpu(self) -> None:
        """每 2 秒刷新一次显存显示。"""
        info = vram.gpu_info()
        if info is None:
            self.gpu_lbl.config(text="显存  读不到 GPU 状态", fg=theme.DIM)
        else:
            # 占用高时变黄、很高变红，一眼能看出该关工具了
            pct = info.used_pct
            color = theme.OK if pct < 60 else (theme.WARN if pct < 85 else theme.ERR)
            self.gpu_lbl.config(
                text=(f"显存  {info.bar(16)}  "
                      f"{info.used_mb/1024:.1f}/{info.total_mb/1024:.1f} GB"
                      f"   {info.temp_c}°C"),
                fg=color)
        self._gpu_after = self.root.after(2000, self._tick_gpu)

    def show_gpu_detail(self) -> None:
        """显存详情窗：谁在占、占用多少、能不能结束。"""
        info = vram.gpu_info()
        if info is None:
            messagebox.showinfo("显存详情", "读不到 GPU 状态。\n"
                                            "可能没有 NVIDIA 显卡，或者没装驱动。")
            return

        win = tk.Toplevel(self.root)
        win.title("显存详情")
        win.configure(bg=theme.BG)
        win.geometry("520x400")
        win.transient(self.root)

        head = tk.Frame(win, bg=theme.BG)
        head.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(head, text=f"🎮 {info.name}", bg=theme.BG, fg=theme.FG,
                 font=theme.F_H2).pack(anchor="w")
        tk.Label(head,
                 text=(f"占用 {info.used_mb} / {info.total_mb} MB"
                       f"（{info.used_pct:.0f}%）   空闲 {info.free_mb} MB\n"
                       f"温度 {info.temp_c}°C   利用率 {info.util_pct}%"),
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL,
                 justify="left").pack(anchor="w", pady=(3, 0))

        tk.Label(win, text=info.bar(40), bg=theme.BG,
                 fg=theme.OK if info.used_pct < 60 else theme.WARN,
                 font=(theme.FONT, 10)).pack(anchor="w", padx=14)

        # ---- 进程列表 ----
        box = tk.Frame(win, bg=theme.BG)
        box.pack(fill="both", expand=True, padx=14, pady=(10, 0))
        tk.Label(box, text="占用显存的进程", bg=theme.BG, fg=theme.ACCENT,
                 font=theme.F_CARD).pack(anchor="w")

        listbox = tk.Listbox(box, bg=theme.CARD, fg=theme.FG, height=8,
                             selectbackground=theme.ACCENT, relief="flat",
                             bd=0, highlightthickness=0,
                             font=theme.F_SMALL, activestyle="none")
        listbox.pack(fill="both", expand=True, pady=(4, 0))

        procs = vram.gpu_processes()
        self._gpu_procs = procs
        if not procs:
            listbox.insert("end", "  （没有进程在占用显存）")
        else:
            for p in procs:
                listbox.insert("end", "  " + p.display())

        tk.Label(win, text=("说明：显存按进程隔离。这里的「结束进程」是强制关闭那个程序，\n"
                            "正在跑的任务会中断。想温和释放，请在那个工具窗口里点「释放显存」。"),
                 bg=theme.BG, fg=theme.DIM, font=theme.F_SMALL,
                 justify="left").pack(anchor="w", padx=14, pady=(8, 0))

        btns = tk.Frame(win, bg=theme.BG)
        btns.pack(fill="x", padx=14, pady=(8, 14))
        theme.button(btns, "结束选中进程", lambda: self._kill_gpu_proc(win, listbox),
                     primary=True).pack(side="left")
        theme.button(btns, "刷新", lambda: (win.destroy(), self.show_gpu_detail()),
                     small=True).pack(side="left", padx=(8, 0))
        theme.button(btns, "关闭", win.destroy, small=True).pack(side="right")

    def _kill_gpu_proc(self, win: tk.Toplevel, listbox: tk.Listbox) -> None:
        idx = listbox.curselection()
        if not idx or idx[0] >= len(self._gpu_procs):
            messagebox.showinfo("没选", "先在列表里选一个进程。", parent=win)
            return
        p = self._gpu_procs[idx[0]]
        if not messagebox.askyesno(
                "结束进程",
                f"确定强制结束这个进程？\n\n{p.display()}\n\n"
                f"它正在跑的任务会中断，未保存的进度会丢失。", parent=win):
            return
        try:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/F"],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW
                           if sys.platform == "win32" else 0)
        except Exception as exc:                     # noqa: BLE001
            messagebox.showerror("失败", str(exc), parent=win)
            return
        win.destroy()
        self.show_gpu_detail()

    # -------------------------------------------------------------- 动作 --
    def open_output(self) -> None:
        paths.OUTPUT.mkdir(parents=True, exist_ok=True)
        ok, why = opener.open_path(paths.OUTPUT)
        if not ok:
            messagebox.showwarning("打不开", why)

    def open_tool(self, tool: Tool) -> None:
        # ---- 外部软件：直接交给系统打开（等同双击它的启动器）----
        # 不占工具箱的「子进程」槽位，也不隐藏工具箱窗口 ——
        # 因为要同时开好几个外部程序是常事。
        if tool.is_external:
            target = tool.external
            assert target is not None
            if not target.exists():
                messagebox.showerror(
                    "找不到程序",
                    f"{tool.title}\n\n{target}\n\n"
                    f"可能被移动或删除了。要改路径请编辑\n"
                    f"shared\\tools_registry.py 里 {tool.key} 那条。")
                return
            try:
                # 用 cwd 指向程序所在目录：很多启动器（含绘世）靠相对路径
                # 找自己的 python 和配置，工作目录不对会直接闪退。
                if sys.platform == "win32":
                    os.startfile(str(target), cwd=str(target.parent))  # noqa: S606
                else:
                    subprocess.Popen([str(target)], cwd=str(target.parent))
            except OSError as exc:
                messagebox.showerror("启动失败", f"{tool.title}\n\n{exc}")
            return

        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("已有工具在运行", "请先关掉当前打开的工具窗口。")
            return

        entry = tool.entry_file()          # 注册表知道每个工具的入口在哪
        if not entry.exists():
            messagebox.showerror("模块缺失", f"找不到入口文件：\n{entry}")
            return

        # 变声要用 torch 环境；其它工具用启动器自己的解释器即可
        python = paths.VOICE_PY if tool.key in ("voice", "voices", "presets") \
            and paths.VOICE_PY.exists() else Path(sys.executable)

        try:
            self.proc = subprocess.Popen(
                [str(python), str(entry), "--embedded"], cwd=str(paths.ROOT))
        except OSError as exc:
            messagebox.showerror("启动失败", f"{tool.title}\n\n{exc}")
            return
        self.root.withdraw()

    def _watch_child(self) -> None:
        if self.proc is not None and self.proc.poll() is not None:
            code = self.proc.returncode
            self.proc = None
            self.root.deiconify()
            self.root.lift()
            if code not in (0, 1):     # 1 = 用户点了「返回工具箱」，正常
                messagebox.showwarning(
                    "工具异常退出",
                    f"退出码 {code}。\n该工具可能报错了，详情可看它的日志。")
        self.root.after(300, self._watch_child)


class LinkDialog(tk.Toplevel):
    """添加 / 修改一条外部软件链接。

    只有两个字段：名称 + 路径。没有图标选择器（图标按类型和名字自动猜），
    也没有"描述"（用户自己加的软件，名字就够了）。
    """

    def __init__(self, parent, item=None, on_done=None) -> None:
        super().__init__(parent)
        self.item = item
        self.on_done = on_done

        self.title("修改软件" if item else "添加软件")
        self.configure(bg=theme.BG)
        self.geometry("540x260")
        self.resizable(False, False)
        self.transient(parent)

        self.name_var = tk.StringVar(value=item.name if item else "")
        self.path_var = tk.StringVar(value=item.target if item else "")

        pad = {"padx": 18}
        tk.Label(self, text=("修改「" + item.name + "」" if item else "添加外部软件"),
                 bg=theme.BG, fg=theme.FG, font=theme.F_H2).pack(
            anchor="w", pady=(16, 2), **pad)
        tk.Label(self, text="可以是 .exe / .bat，也可以选一个文件夹。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(
            anchor="w", **pad)

        card = tk.Frame(self, bg=theme.CARD)
        card.pack(fill="x", pady=(12, 0), **pad)
        inner = tk.Frame(card, bg=theme.CARD)
        inner.pack(fill="x", padx=14, pady=12)

        r1 = tk.Frame(inner, bg=theme.CARD)
        r1.pack(fill="x")
        tk.Label(r1, text="名称", bg=theme.CARD, fg=theme.MUTED,
                 font=theme.F_BODY, width=6, anchor="w").pack(side="left")
        tk.Entry(r1, textvariable=self.name_var, bg=theme.PANEL, fg=theme.FG,
                 insertbackground=theme.FG, relief="flat",
                 font=theme.F_BODY).pack(side="left", fill="x", expand=True, ipady=4)

        r2 = tk.Frame(inner, bg=theme.CARD)
        r2.pack(fill="x", pady=(10, 0))
        tk.Label(r2, text="路径", bg=theme.CARD, fg=theme.MUTED,
                 font=theme.F_BODY, width=6, anchor="w").pack(side="left")
        tk.Entry(r2, textvariable=self.path_var, bg=theme.PANEL, fg=theme.FG,
                 insertbackground=theme.FG, relief="flat",
                 font=theme.F_SMALL).pack(side="left", fill="x", expand=True, ipady=4)
        theme.button(r2, "浏览…", self.browse, small=True).pack(side="left", padx=(6, 0))

        self.hint = tk.Label(self, text="", bg=theme.BG, fg=theme.WARN,
                             font=theme.F_SMALL, anchor="w")
        self.hint.pack(fill="x", pady=(8, 0), **pad)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", pady=(10, 16), **pad)
        theme.button(btns, "保存" if item else "添加", self.save,
                     primary=True).pack(side="left")
        theme.button(btns, "取消", self.destroy, small=True).pack(side="left", padx=(8, 0))

    def browse(self) -> None:
        """先让用户选程序；想加文件夹就点"取消"再点文件夹按钮。"""
        choice = messagebox.askyesnocancel(
            "选择类型",
            "要添加**程序**（.exe / .bat）吗？\n\n"
            "是 = 选程序文件\n否 = 选文件夹\n取消 = 什么都不做")
        if choice is None:
            return
        if choice:
            f = filedialog.askopenfilename(
                title="选程序",
                filetypes=[("程序", "*.exe *.bat *.cmd *.lnk"), ("全部", "*.*")])
            if not f:
                return
            p = Path(f)
            self.path_var.set(str(p))
            if not self.name_var.get().strip():
                self.name_var.set(p.stem)
        else:
            d = filedialog.askdirectory(title="选文件夹")
            if not d:
                return
            p = Path(d)
            self.path_var.set(str(p))
            if not self.name_var.get().strip():
                self.name_var.set(p.name)

    def save(self) -> None:
        from shared import links as link_store

        name = self.name_var.get().strip()
        target = self.path_var.get().strip()

        if not target:
            self.hint.config(text="要填一个路径")
            return
        p = Path(target)
        if not p.exists():
            self.hint.config(text=f"这个路径不存在：{target}")
            return
        if not name:
            name = p.stem if p.is_file() else p.name

        if self.item is None:
            link_store.add(link_store.new_link(name, target))
        else:
            self.item.name = name
            self.item.target = target
            # 目标类型重算（用户可能从文件改成了文件夹）
            self.item.kind = "folder" if p.is_dir() else "auto"
            link_store.update(self.item)

        if callable(self.on_done):
            self.on_done()
        self.destroy()


def launch() -> int:
    paths.ensure_dirs()
    root = tk.Tk()
    Launcher(root)
    root.mainloop()
    return 0


def selftest() -> int:
    """无头自检：把界面真正建出来，报告卡片状态，然后退出。

    没有这个就只能靠人眼看窗口，而「靠人眼看」正是之前那些 bug 溜过去的原因。
    """
    paths.ensure_dirs()
    root = tk.Tk()
    app = Launcher(root)
    root.update_idletasks()
    root.update()

    widgets = 0
    stack = [root]
    while stack:
        w = stack.pop()
        widgets += 1
        stack.extend(w.winfo_children())

    print(f"[selftest] 界面构建成功：{widgets} 个控件")
    print(f"[selftest] 窗口尺寸：{root.winfo_width()}x{root.winfo_height()}")

    ok = miss = planned = 0
    for tool in TOOLS:
        # 外部软件没有 Python 模块，判定标准是文件在不在（和 ToolCard 保持一致）
        if tool.is_external:
            ready = tool.ready and tool.external.exists()
        else:
            ready = tool.ready and not tool.missing_requirements() \
                and module_available(tool.module)
        if ready:
            ok += 1
        elif tool.ready:
            miss += 1
            why = ([str(m) for m in tool.missing_requirements()]
                   if not tool.is_external
                   else [f"找不到 {tool.external}"])
            print(f"[selftest]   ! 环境缺失: {tool.title} -> {why}")
        else:
            planned += 1
    print(f"[selftest] 工具总数 {len(TOOLS)}：可用 {ok}，环境缺失 {miss}，未开发 {planned}")

    # 验证未开发功能的说明文案都在（点了弹窗要用的）
    from launcher import TOOL_DETAIL
    no_detail = [t.title for t in TOOLS if not t.ready and t.key not in TOOL_DETAIL]
    if no_detail:
        print(f"[selftest]   ! 这些未开发工具没有说明文案: {no_detail}")
    else:
        print(f"[selftest] 未开发工具的说明文案齐全")

    # 验证可用工具的入口文件真的存在
    bad: list[str] = []
    for tool in TOOLS:
        if tool.ready and not tool.missing_requirements():
            if not tool.entry_file().exists():
                bad.append(f"{tool.title} -> {tool.entry_file()}")
    if bad:
        print("[selftest]   ! 注册表标为可用但入口文件不存在：")
        for b in bad:
            print(f"[selftest]     {b}")
    else:
        print("[selftest] 所有可用工具的入口文件都存在")

    verdict = not no_detail and not bad and miss == 0
    print("[selftest] " + ("全部通过" if verdict else "有问题，见上面 ! 行"))
    root.destroy()
    return 0 if verdict else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    raise SystemExit(launch())
