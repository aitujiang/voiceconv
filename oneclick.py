"""一键变声 —— 双击运行，全部操作都是弹窗。

用户视角：
    1. 选「要变声的音频」（第二段，保留它的说话内容）
    2. 选「音色参考音频」（第一段，借用它的音色）
    3. 等进度条跑完，成品自动打开所在文件夹

不需要命令行、不需要记参数。想调音色像不像的程度就点「高级选项」。
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
CONVERT = ROOT / "convert.py"
SONG = ROOT / "song.py"

# 成品目录。selftest / --auto 这类自动化运行会把 DSH_SANDBOX 置为 1，
# 于是产物落到 io\_sandbox 而不是用户真正的成品目录 —— 测试绝不许动用户的文件。
OUTDIR = ROOT / "io" / ("_sandbox" if os.environ.get("DSH_SANDBOX") == "1" else "output")

# 预设的存取在 shared 里，工具箱和本界面共用同一份数据
sys.path.insert(0, str(ROOT))
from shared import presets as preset_store            # noqa: E402
from shared import opener                             # noqa: E402
from shared import vram                               # noqa: E402
from shared import training_spec                      # noqa: E402
from shared.presets import Preset                     # noqa: E402

# GUI mode key -> (script, label, needs whole-song pipeline?)
MODE_SPEC = {
    "v1": (CONVERT, "说话（推荐）", False),
    "v2": (CONVERT, "说话·更像参考", False),
    "singing": (CONVERT, "唱歌（仅人声）", False),
    "song": (SONG, "整曲换人声（保留伴奏）", True),
}

# 音色的两个来源 —— 用户要求的「一个用音频、一个用训练模型」
VOICE_SOURCES = [
    (preset_store.VOICE_REALTIME, "🔊 用音频", "给一段参考音频，当场克隆（即选即用）"),
    (preset_store.VOICE_TRAINED, "🧠 用训练模型", "用训练好的模型，效果更准（需要先训练）"),
]

AUDIO_TYPES = [
    ("音频文件", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.mp4 *.opus"),
    ("所有文件", "*.*"),
]

BG = "#0f1720"
CARD = "#16212c"
FG = "#e8eef5"
MUTED = "#8fa3b8"
DIM = "#5f7385"          # 比 MUTED 更弱，用于更次要的说明文字
ACCENT = "#3ea6ff"
OK = "#38d39f"
WARN = "#ffb454"
ERR = "#ff7b72"          # 错误色（原本散在几处写成字面量，统一到这里）


def human_time(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 60} 分 {sec % 60} 秒" if sec >= 60 else f"{sec} 秒"


class App:
    def __init__(self, root: tk.Tk, preselect: list[str],
                 on_back=None) -> None:
        self.root = root
        self.on_back = on_back        # 从工具箱进来时才有值，独立运行是 None
        self.source: Path | None = None
        self.target: Path | None = None
        self.mode = tk.StringVar(value="v1")
        self.similarity = tk.DoubleVar(value=0.7)
        self.steps = tk.IntVar(value=30)
        self.busy = False
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.last_out: Path | None = None
        self.t0 = 0.0
        self.presets: list[Preset] = preset_store.load_all()
        self.active_preset: Preset | None = None
        # 音色来源：用音频（零样本）/ 用训练模型
        self.voice_source = tk.StringVar(value=preset_store.VOICE_REALTIME)

        root.title("AI 变声 · 用 A 的音色说 B 的话")
        root.configure(bg=BG)
        root.geometry("660x640")
        root.minsize(620, 600)
        try:
            root.iconbitmap(default="")
        except Exception:
            pass

        self._build_ui()
        if len(preselect) >= 2:          # 拖拽进 .bat 时自动填好
            self.source = Path(preselect[0])
            self.target = Path(preselect[1])
            self._refresh_labels()
        elif len(preselect) == 1:
            self.source = Path(preselect[0])
            self._refresh_labels()

        self.root.after(120, self._drain)
        self._after_id = None
        self._vram_after = None
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick_vram()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        pad = dict(padx=18)

        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", pady=(16, 6), **pad)

        # 从工具箱进来时才显示返回按钮；独立双击运行时 on_back 是 None，
        # 这一行不占位置也不显示任何东西。
        if self.on_back is not None:
            bar = tk.Frame(head, bg=BG)
            bar.pack(fill="x", pady=(0, 6))
            tk.Button(bar, text="← 返回工具箱", command=self._go_back,
                      bg="#22303d", fg=FG, activebackground="#2c3d4d",
                      font=("Microsoft YaHei UI", 9), relief="flat", bd=0,
                      cursor="hand2", padx=10, pady=3).pack(side="left")

        tk.Label(head, text="AI 变声", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        tk.Label(head, text="把「要变声的音频」换成「音色参考音频」的声音",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 0))

        # --- 两个文件选择卡片 ---
        self.src_lbl, self.src_btn = self._file_card(
            1, "① 要变声的音频", "内容保留这段（替换的是音色）", "#7fd1ff")
        self.tgt_lbl, self.tgt_btn = self._file_card(
            2, "② 音色参考音频", "借用这段的音色（建议 5~25 秒干净人声）", OK,
            extra=("存为预设", self.save_current_as_preset))

        # --- 音色预设：点一下把素材和参数全部填好 ---
        self._build_preset_row()

        # --- 高级选项 ---
        adv = tk.Frame(self.root, bg=CARD, highlightthickness=0)
        adv.pack(fill="x", pady=(10, 0), **pad)

        row = tk.Frame(adv, bg=CARD)
        row.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(row, text="类型", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        for val in ("v1", "v2", "singing", "song"):
            tk.Radiobutton(row, text=MODE_SPEC[val][1], value=val, variable=self.mode,
                           bg=CARD, fg=FG, selectcolor=BG, activebackground=CARD,
                           activeforeground=ACCENT, font=("Microsoft YaHei UI", 9),
                           highlightthickness=0, bd=0,
                           command=self._on_mode).pack(side="left", padx=(10, 0))

        tk.Label(adv, text="「整曲换人声」= 自动分离伴奏再混回，输出的还是完整歌曲；"
                           "其他类型输出的只有一条干声",
                 bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8),
                 wraplength=580, justify="left").pack(anchor="w", padx=14, pady=(0, 2))

        sim = tk.Frame(adv, bg=CARD)
        sim.pack(fill="x", padx=14, pady=(4, 2))
        tk.Label(sim, text="音色相似度", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.sim_scale = tk.Scale(sim, from_=0.3, to=1.0, resolution=0.05, orient="horizontal",
                                  variable=self.similarity, bg=CARD, fg=FG, troughcolor=BG,
                                  highlightthickness=0, bd=0, length=210,
                                  font=("Microsoft YaHei UI", 8), showvalue=True)
        self.sim_scale.pack(side="left", padx=10)
        tk.Label(sim, text="（v2 / 唱歌模式生效，越高越像）", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        st = tk.Frame(adv, bg=CARD)
        st.pack(fill="x", padx=14, pady=(2, 12))
        tk.Label(st, text="精细度", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Scale(st, from_=10, to=50, resolution=5, orient="horizontal", variable=self.steps,
                 bg=CARD, fg=FG, troughcolor=BG, highlightthickness=0, bd=0, length=210,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=10)
        tk.Label(st, text="（越高越慢越细腻，30 够用）", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        # --- 开始按钮 ---
        self.go = tk.Button(self.root, text="开始变声", command=self.start,
                            bg=ACCENT, fg="#04121f", activebackground="#6cc0ff",
                            font=("Microsoft YaHei UI", 12, "bold"),
                            relief="flat", bd=0, cursor="hand2")
        self.go.pack(fill="x", pady=(14, 6), ipady=9, **pad)

        self.bar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.bar.pack(fill="x", **pad)

        # --- 显存条 + 释放按钮 ---
        # 变声模型加载后会常驻显存（Seed-VC 约 2~3GB）。想开别的工具又怕不够时，
        # 点「释放」把本进程占的还回去。显示的是整卡占用（能看到别的进程）。
        vr = tk.Frame(self.root, bg=BG)
        vr.pack(fill="x", pady=(6, 0), **pad)
        self.vram_lbl = tk.Label(vr, text="显存 …", bg=BG, fg=MUTED,
                                 font=("Microsoft YaHei UI", 8))
        self.vram_lbl.pack(side="left")
        tk.Button(vr, text="释放显存", command=self.release_vram,
                  bg=CARD, fg=FG, activebackground="#1e2c3a",
                  font=("Microsoft YaHei UI", 8), relief="flat", bd=0,
                  cursor="hand2", padx=8, pady=2).pack(side="right")

        self.status = tk.Label(self.root, text="选好两个文件后点「开始变声」", bg=BG, fg=MUTED,
                               font=("Microsoft YaHei UI", 9), anchor="w", justify="left",
                               wraplength=590)
        self.status.pack(fill="x", pady=(8, 4), **pad)

        self.open_btn = tk.Button(self.root, text="打开成品文件夹", command=self.open_out,
                                  bg=CARD, fg=FG, activebackground="#1e2c3a",
                                  font=("Microsoft YaHei UI", 9), relief="flat", bd=0,
                                  state="disabled", cursor="hand2")
        self.open_btn.pack(anchor="w", **pad, ipady=4, ipadx=8)

        self._on_mode()
        # 初始没有任何文件时必须禁用「开始变声」，否则点了会毫无反应。
        # 放在最后调用，让它在 preselect 填充之后再决定按钮状态。
        self._refresh_labels()

    # ------------------------------------------------------- 音色预设 UI --
    def _build_preset_row(self) -> None:
        """一行预设卡片。点一下 = 把参考音频 + 情绪模式 + 参数全部填好。

        这就是「省着每次变声还得重新选择素材」那个需求的落点。
        """
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="x", padx=18, pady=(12, 0))

        head = tk.Frame(wrap, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="③ 音色来源", bg=BG, fg=FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self.preset_hint = tk.Label(head, text="点一下自动填好素材和参数",
                                    bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8))
        self.preset_hint.pack(side="left", padx=(8, 0))

        for label, cmd in (("刷新", self.reload_presets),
                           ("音色库", self.manage_presets),
                           ("＋ 新建音色", self.new_preset_from_file)):
            tk.Button(head, text=label, command=cmd,
                      bg="#22303d" if label == "刷新" else "#1c3a52",
                      fg=FG if label == "刷新" else "#7fd1ff",
                      activebackground="#2c3d4d",
                      font=("Microsoft YaHei UI", 8), relief="flat", bd=0,
                      cursor="hand2", padx=8, pady=1).pack(side="right", padx=(4, 0))

        # ---- 两个来源的选择（用户要的「一个用音频、一个用训练模型」）----
        pick = tk.Frame(wrap, bg=BG)
        pick.pack(fill="x", pady=(6, 0))
        for val, label, tip in VOICE_SOURCES:
            tk.Radiobutton(pick, text=label, value=val, variable=self.voice_source,
                           bg=BG, fg=FG, selectcolor=CARD, activebackground=BG,
                           activeforeground=ACCENT, font=("Microsoft YaHei UI", 9),
                           highlightthickness=0, bd=0,
                           command=self._on_source_change).pack(side="left")
            tk.Label(pick, text=tip, bg=BG, fg=DIM,
                     font=("Microsoft YaHei UI", 7)).pack(side="left", padx=(2, 14))

        tk.Button(pick, text="素材要求？", command=self.show_training_requirements,
                  bg=BG, fg="#7fd1ff", activebackground=BG, font=("Microsoft YaHei UI", 8),
                  relief="flat", bd=0, cursor="hand2").pack(side="right")

        self.preset_box = tk.Frame(wrap, bg=CARD)
        self.preset_box.pack(fill="x", pady=(6, 0))
        self._render_presets()

    def _on_source_change(self) -> None:
        """切换来源：清掉当前选择，并按新类型重画卡片。"""
        src = self.voice_source.get()
        if src == preset_store.VOICE_TRAINED:
            self.target = None
            self._refresh_labels()
        self._render_presets()

    def _presets_of_source(self) -> list[Preset]:
        want = self.voice_source.get()
        return [p for p in self.presets if p.voice_type == want]

    def _render_presets(self) -> None:
        for w in self.preset_box.winfo_children():
            w.destroy()

        src = self.voice_source.get()
        usable = [p for p in self.presets if p.voice_type == src]

        # ---- 「用训练模型」这一档：还没训练功能，给清楚的要求说明 ----
        if src == preset_store.VOICE_TRAINED:
            spec = training_spec.RVC
            box = tk.Frame(self.preset_box, bg=CARD)
            box.pack(fill="x", padx=14, pady=12)

            tk.Label(box, text="🧠 用训练模型 —— 训练功能尚未开发",
                     bg=CARD, fg="#ffb454",
                     font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            tk.Label(box,
                     text="用训练好的模型变声，比每次给参考音频更准、更稳。\n"
                          "但需要先用你的素材训练一个模型。",
                     bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 8),
                     justify="left").pack(anchor="w", pady=(4, 8))

            reqs = tk.Frame(box, bg=CARD)
            reqs.pack(fill="x")
            for text, colour in (
                (f"素材时长　最低 {spec.min_minutes:.0f} 分钟，"
                 f"{spec.good_minutes:.0f} 分钟以上效果明显更好", FG),
                ("素材质量　" + ("必须是干净人声（无人声外杂音）" if spec.clean_required
                                 else "建议干净；可容忍少量背景音"), FG),
                (f"训练耗时　{spec.time_note}", MUTED),
                (f"运行环境　{spec.env_note}", "#ffb454" if not spec.env_ready else MUTED),
                (f"产出文件　{' + '.join(spec.outputs)}", MUTED),
            ):
                tk.Label(reqs, text=text, bg=CARD, fg=colour,
                         font=("Microsoft YaHei UI", 8),
                         justify="left").pack(anchor="w", pady=1)

            tk.Button(box, text="看完整素材要求", command=self.show_training_requirements,
                      bg="#1c3a52", fg="#7fd1ff", activebackground="#244a68",
                      font=("Microsoft YaHei UI", 8), relief="flat", bd=0,
                      cursor="hand2", padx=10, pady=4).pack(anchor="w", pady=(8, 0))
            return

        # ---- 「用音频」这一档 ----
        if not usable:
            tk.Label(self.preset_box,
                     text="还没有音色预设。两种建法：\n"
                          "· 点右上「＋ 新建音色」直接选一段素材\n"
                          "· 或先在下面「② 音色参考音频」选好，再点「存为预设」",
                     bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9),
                     wraplength=560, justify="left").pack(anchor="w", padx=14, pady=12)
            return

        grid = tk.Frame(self.preset_box, bg=CARD)
        grid.pack(fill="x", padx=10, pady=8)
        for i, preset in enumerate(usable[:8]):
            self._preset_chip(grid, preset).grid(row=i // 4, column=i % 4,
                                                 padx=(0, 6), pady=(0, 6), sticky="w")
        if len(usable) > 8:
            tk.Label(self.preset_box,
                     text=f"（只显示前 8 个，共 {len(usable)} 个 —— 其余的到「音色库」里管理）",
                     bg=CARD, fg=DIM, font=("Microsoft YaHei UI", 7)
                     ).pack(anchor="w", padx=14, pady=(0, 8))

    def show_training_requirements(self) -> None:
        """弹窗展示素材要求（内容来自 shared/training_spec.py，全工具箱统一）。"""
        spec = training_spec.RVC
        minutes = self._dataset_minutes()
        messagebox.showinfo(f"{spec.title} · 素材要求",
                            training_spec.requirements_text(spec, minutes))

    def _dataset_minutes(self) -> float | None:
        """扫一下 data\\datasets 里攒了多少分钟的素材，用于给「够不够」的判断。"""
        root = ROOT / "data" / "datasets"
        if not root.is_dir():
            return None
        total = 0.0
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
                try:
                    import wave
                    if p.suffix.lower() == ".wav":
                        with wave.open(str(p), "rb") as fh:
                            total += fh.getnframes() / max(fh.getframerate(), 1)
                    else:
                        total += p.stat().st_size / 16000      # 粗略估算
                except Exception:                              # noqa: BLE001
                    pass
        return total / 60 if total else 0.0

    def _preset_chip(self, parent, preset: Preset):
        """一个预设小卡片。素材丢失的用红边标出来，点了让你重新指定。"""
        ok = preset.is_usable()
        active = self.active_preset is not None and self.active_preset.id == preset.id
        bg = "#1d3a52" if active else ("#1c2a36" if ok else "#33232a")
        fg = FG if ok else "#ff9e97"

        chip = tk.Frame(parent, bg=bg, highlightthickness=1,
                        highlightbackground=ACCENT if active else bg, cursor="hand2")
        inner = tk.Frame(chip, bg=bg)
        inner.pack(padx=10, pady=7)

        row = tk.Frame(inner, bg=bg)
        row.pack(anchor="w")
        tk.Label(row, text=preset.type_icon(), bg=bg,
                 font=("Segoe UI Emoji", 11)).pack(side="left")
        tk.Label(row, text=preset.name, bg=bg, fg=fg,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=(5, 0))

        sub = preset.summary() if ok else preset.problem()
        tk.Label(inner, text=sub[:34], bg=bg, fg=MUTED if ok else "#ff9e97",
                 font=("Microsoft YaHei UI", 7), anchor="w").pack(anchor="w")
        tk.Label(inner, text=preset.type_label(), bg=bg, fg=MUTED,
                 font=("Microsoft YaHei UI", 7)).pack(anchor="w")

        for w in [chip, inner, row, *inner.winfo_children(), *row.winfo_children()]:
            w.bind("<Button-1>", lambda _e, p=preset: self.apply_preset(p))
        return chip

    def reload_presets(self) -> None:
        self.presets = preset_store.load_all()
        self._render_presets()

    def apply_preset(self, preset: Preset) -> None:
        """把预设套用到界面上 —— 这就是「一键填好」的全部内容。"""
        # 模型型预设：现在还没有训练功能，说清楚而不是假装能用
        if preset.voice_type == preset_store.VOICE_TRAINED:
            if preset.is_usable():
                messagebox.showinfo(
                    f"「{preset.name}」",
                    f"这是训练模型型预设：\n{preset.model_path}\n\n"
                    "模型变声的推理链路还没开发（需要先装 RVC 并完成训练）。\n"
                    "现在请用「🔊 用音频」那一档。")
            else:
                messagebox.showinfo(
                    f"「{preset.name}」还不可用",
                    f"{preset.problem()}\n\n"
                    "训练功能尚未开发，所以这个预设暂时只是占位。")
            return

        if not preset.is_usable():
            if not messagebox.askyesno(
                    f"「{preset.name}」不可用",
                    f"{preset.problem()}\n\n现在给它重新指定一个素材/模型？"):
                return
            new = self._pick_for_preset(preset)
            if new is None:
                return
            preset_store.save(preset)
            self.reload_presets()

        self.active_preset = preset

        # 现场变声型：把参考音频和参数都填上
        if preset.ref_audio:
            self.target = Path(preset.ref_audio)
            self.mode.set(preset.mode if preset.mode in MODE_SPEC else "v1")
            self.similarity.set(preset.similarity)
            self.steps.set(preset.steps)
            self._on_mode()

        self._refresh_labels()
        self._render_presets()
        self.preset_hint.config(text=f"已套用「{preset.name}」", fg=OK)

    def save_current_as_preset(self) -> None:
        """把当前选好的素材和参数存成一个预设。"""
        if not self.target:
            messagebox.showinfo("先选素材", "请先在「② 音色参考音频」里选一个文件，"
                                            "再存为预设。")
            return
        name = simpledialog.askstring(
            "存为预设", "给这个音色起个名字（比如角色名）：",
            initialvalue=self.target.stem[:20] or "我的音色", parent=self.root)
        if not name:
            return
        preset = Preset(
            name=name.strip(),
            voice_type=preset_store.VOICE_REALTIME,
            ref_audio=str(self.target),
            mode=self.mode.get(),
            similarity=float(self.similarity.get()),
            steps=int(self.steps.get()),
        )
        preset_store.save(preset)
        self.active_preset = preset
        self.reload_presets()
        self.preset_hint.config(text=f"已保存「{preset.name}」，以后点它就能一键调用", fg=OK)

    def new_preset_from_file(self) -> None:
        """从素材文件直接建一个新预设（不用先选成参考音频）。

        场景：你手上有几十条角色配音，想快速把其中几条存成音色，
        不必每次都先「选成参考音频」再「存为预设」。
        """
        src = filedialog.askopenfilename(
            title="选一段配音当素材（建议 5~25 秒干净人声）",
            filetypes=AUDIO_TYPES, initialdir=str(ROOT / "io"))
        if not src:
            return
        default = Path(src).stem[:20] or "新音色"
        name = simpledialog.askstring("新建音色", "给这个音色起个名字：",
                                      initialvalue=default, parent=self.root)
        if not name:
            return
        preset = Preset(name=name.strip(), voice_type=preset_store.VOICE_REALTIME,
                        ref_audio=src, mode=self.mode.get(),
                        similarity=float(self.similarity.get()),
                        steps=int(self.steps.get()))
        preset_store.save(preset)
        # 建完直接套用，省得再点一次
        self.active_preset = preset
        self.target = Path(src)
        self.reload_presets()
        self._refresh_labels()
        self.preset_hint.config(
            text=f"已新建并套用「{preset.name}」", fg=OK)

    def manage_presets(self) -> None:
        """打开音色库窗口管理预设（独立进程，用完回来刷新）。"""
        entry = ROOT / "tools" / "voice" / "voices_ui.py"
        if not entry.exists():
            messagebox.showerror("模块缺失", f"找不到：\n{entry}")
            return
        try:
            subprocess.Popen([str(VENV_PY), str(entry)], cwd=str(ROOT))
        except OSError as exc:
            messagebox.showerror("启动失败", str(exc))
            return
        self.preset_hint.config(text="已在另一个窗口打开音色库，改完点「刷新」", fg=MUTED)

    def _pick_for_preset(self, preset: Preset) -> bool | None:
        """预设失效时，让用户重新指定素材/模型。"""
        if preset.voice_type == preset_store.VOICE_REALTIME:
            p = filedialog.askopenfilename(title=f"给「{preset.name}」重新指定素材",
                                           filetypes=AUDIO_TYPES,
                                           initialdir=str(ROOT / "io"))
            if not p:
                return None
            preset.ref_audio = p
        else:
            p = filedialog.askopenfilename(title=f"给「{preset.name}」重新指定模型")
            if not p:
                return None
            preset.model_path = p
        return True

    def _file_card(self, idx: int, title: str, hint: str, color: str,
                   extra: tuple[str, object] | None = None):
        card = tk.Frame(self.root, bg=CARD)
        card.pack(fill="x", pady=(10 if idx == 1 else 8, 0), padx=18)
        left = tk.Frame(card, bg=CARD)
        left.pack(side="left", fill="x", expand=True, padx=14, pady=11)
        tk.Label(left, text=title, bg=CARD, fg=color,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        name = tk.Label(left, text="未选择", bg=CARD, fg=MUTED,
                        font=("Microsoft YaHei UI", 9), anchor="w",
                        wraplength=430, justify="left")
        name.pack(anchor="w", pady=(2, 0))
        tk.Label(left, text=hint, bg=CARD, fg="#5f7385",
                 font=("Microsoft YaHei UI", 8)).pack(anchor="w")
        btn = tk.Button(card, text="选择文件", command=lambda i=idx: self.pick(i),
                        bg="#22303d", fg=FG, activebackground="#2c3d4d",
                        font=("Microsoft YaHei UI", 9), relief="flat", bd=0,
                        cursor="hand2", padx=12, pady=6)
        btn.pack(side="right", padx=(0, 14))
        if extra is not None:                     # 可选的小按钮，比如「存为预设」
            label, command = extra
            tk.Button(card, text=label, command=command,
                      bg="#1c2a36", fg="#7fd1ff", activebackground="#24384a",
                      font=("Microsoft YaHei UI", 8), relief="flat", bd=0,
                      cursor="hand2", padx=9, pady=4).pack(side="right", padx=(8, 0))
        return name, btn

    # --------------------------------------------------------------- actions --
    def pick(self, which: int) -> None:
        if self.busy:
            return
        title = "① 选择要变声的音频（保留内容）" if which == 1 else "② 选择音色参考音频（借用音色）"
        path = filedialog.askopenfilename(title=title, filetypes=AUDIO_TYPES,
                                          initialdir=str(ROOT / "io"))
        if not path:
            return
        if which == 1:
            self.source = Path(path)
        else:
            self.target = Path(path)
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        for lbl, p in ((self.src_lbl, self.source), (self.tgt_lbl, self.target)):
            if p:
                try:
                    mb = p.stat().st_size / 1e6
                    lbl.config(text=f"{p.name}   ({mb:.1f} MB)", fg=FG)
                except OSError:
                    lbl.config(text=p.name, fg=FG)
        ready = self.source and self.target and self.source.exists() and self.target.exists()
        self.go.config(state="normal" if ready and not self.busy else "disabled")
        if ready and not self.busy:
            self.status.config(text="已就绪，点「开始变声」", fg=MUTED)

    def _on_mode(self) -> None:
        # The similarity slider is only wired into the v2 and whole-song paths.
        active = self.mode.get() in ("v2", "song")
        self.sim_scale.config(state="normal" if active else "disabled")

    def start(self) -> None:
        if self.busy or not (self.source and self.target):
            return
        self.busy = True
        self.go.config(state="disabled", text="正在变声…")
        self.open_btn.config(state="disabled")
        self.bar.config(value=2)
        self.status.config(text="准备中…", fg=MUTED)
        self.t0 = time.time()

        # Snapshot every Tk variable HERE, on the main thread. Reading a Tk
        # variable from the worker thread raises "main thread is not in main
        # loop" and is undefined behaviour even when it happens to work.
        job = {
            "source": str(self.source),
            "target": str(self.target),
            "mode": self.mode.get(),
            "similarity": float(self.similarity.get()),
            "steps": int(self.steps.get()),
        }
        threading.Thread(target=self._worker, args=(job,), daemon=True).start()

    def _worker(self, job: dict) -> None:
        script, _label, whole_song = MODE_SPEC[job["mode"]]

        if whole_song:
            # Whole-song pipeline: song.py separates the vocal with Demucs,
            # converts it, then mixes the accompaniment back in. It takes the
            # song through --song and always runs Seed-VC in singing mode.
            cmd = [str(VENV_PY), str(script),
                   "--song", job["source"], "--voice", job["target"],
                   "--mode", "singing", "--steps", str(job["steps"])]
        else:
            cmd = [str(VENV_PY), str(script),
                   "--source", job["source"], "--target", job["target"],
                   "--mode", job["mode"]]
            # NOTE: these must be the child script's OWN flag names. They are
            # argparse programs and exit with code 2 on an unknown flag -- which
            # is exactly how singing mode used to fail: it passed
            # --inference-cfg-rate (an internal name) instead of --cfg-rate.
            if job["mode"] == "v2":
                cmd += ["--similarity-cfg-rate", f"{job['similarity']:.2f}"]
            cmd += ["--steps", str(job["steps"])]

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["HF_ENDPOINT"] = "https://hf-mirror.com"

        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace",
                                    bufsize=1, creationflags=creation)
        except OSError as exc:
            self.q.put(("fail", f"无法启动转换程序：{exc}"))
            return

        assert proc.stdout is not None
        recent: list[str] = []
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                recent.append(line)
                if len(recent) > 40:
                    recent.pop(0)
                self.q.put(("log", line))
        code = proc.wait()
        if code == 0:
            outs = sorted(OUTDIR.glob("*.wav"), key=lambda p: p.stat().st_mtime)
            self.q.put(("done", str(outs[-1]) if outs else ""))
        else:
            # Surface what actually went wrong instead of guessing. convert.py
            # prints a "[ERROR] ..." line before it exits, so prefer the last
            # such line; otherwise show the tail of the log.
            err = next((ln for ln in reversed(recent) if ln.startswith("[ERROR]")), "")
            if not err:
                err = " | ".join(recent[-4:]) or "（没有任何输出）"
            self.q.put(("fail", f"转换失败（错误码 {code}）：\n{err}"))

    # ------------------------------------------------------------ ui pump --
    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._show_log(payload)
                elif kind == "done":
                    self._finish(Path(payload) if payload else None)
                elif kind == "fail":
                    self._fail(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return            # 窗口已销毁，不再排下一轮
        try:
            self._after_id = self.root.after(120, self._drain)
        except tk.TclError:
            pass

    def _show_log(self, line: str) -> None:
        low = line.lower()
        elapsed = human_time(time.time() - self.t0)
        if "分离人声" in line or "separating track" in low:
            self.bar.config(value=10)
            self.status.config(text=f"正在分离人声与伴奏（Demucs）…（{elapsed}）", fg=WARN)
        elif "分离结果已存在" in line:
            self.status.config(text="已有人声/伴奏分离结果，直接复用", fg=MUTED)
        elif "给分离出的人声变声" in line:
            self.bar.config(value=45)
            self.status.config(text=f"正在给人声变声（{elapsed}）…", fg=MUTED)
        elif "混回伴奏" in line:
            self.bar.config(value=92)
            self.status.config(text="正在把新人声混回伴奏…", fg=MUTED)
        elif "decoding" in low:
            self.bar.config(value=6)
            self.status.config(text=f"正在解码音频…（{elapsed}）", fg=MUTED)
        elif "running inference" in low:
            self.bar.config(value=15)
            self.status.config(text="正在加载模型（首次运行较慢，要 1~3 分钟）…", fg=WARN)
        elif "rtf" in low:
            self.bar.config(value=85)
            self.status.config(text="正在合成声音…接近完成", fg=MUTED)
        elif "collecting output" in low:
            self.bar.config(value=95)
            self.status.config(text="正在导出成品…", fg=MUTED)
        elif "download" in low and ("%" in line or "it/s" in low):
            self.status.config(text="正在补下载模型文件（仅首次）…", fg=WARN)
        # 让进度条缓慢自走，避免看起来卡死
        else:
            v = self.bar["value"]
            if 15 <= v < 85:
                self.bar.config(value=min(v + 0.6, 84))

    def _finish(self, out: Path | None) -> None:
        self.busy = False
        self.bar.config(value=100)
        self.go.config(state="normal", text="开始变声")
        if out and out.exists():
            self.last_out = out
            self.open_btn.config(state="normal")
            self.status.config(text=f"完成！用时 {human_time(time.time() - self.t0)}\n{out}",
                               fg=OK)
            self.open_out(select=out)
        else:
            self.status.config(text="完成，但没有找到输出文件", fg=WARN)
        self._refresh_labels()

    def _fail(self, msg: str) -> None:
        self.busy = False
        self.go.config(state="normal", text="开始变声")
        self.bar.config(value=0)
        self.status.config(text=msg, fg="#ff7b72", wraplength=590)
        self._refresh_labels()

        # Lead with the real error, then add hints that actually match it.
        # Guessing the cause is how the singing-mode flag bug stayed hidden.
        hints = []
        if "错误码 2" in msg:
            hints.append("· 这是参数错误，通常意味着程序内部版本不一致，请重启本程序再试")
        if "参考" in msg or "太短" in msg:
            hints.append("· 音色参考音频太短或几乎没声音（建议 5~25 秒干净人声）")
        low = msg.lower()
        if "memory" in low or "cuda" in low or "显存" in msg:
            hints.append("· 显存不足：先试「说话（推荐）」类型，或把精细度调低")
        if "download" in low or "connectionerror" in low or "网络" in msg:
            hints.append("· 需要联网下载模型文件，检查网络后重试")
        if not hints:
            hints.append("· 完整日志见 io\\_stage 目录")

        messagebox.showerror("变声失败", msg + "\n\n" + "\n".join(hints))

    def open_out(self, select: Path | None = None) -> None:
        """打开成品文件夹；如果指定了文件就在里面选中它。

        用 shared.opener 而不是直接 os.startfile —— 实测 venv 的 pythonw
        调 startfile 会报 WinError 5，换 explorer.exe 就正常。
        """
        if select is not None and select.exists():
            ok, why = opener.open_path(select, select=True)
        else:
            OUTDIR.mkdir(parents=True, exist_ok=True)
            ok, why = opener.open_path(OUTDIR)
        if not ok:
            messagebox.showwarning("打不开文件夹", why)

    def _go_back(self) -> None:
        """点了「← 返回工具箱」：先确认没在跑任务，然后关掉自己，
        由工具箱检测到进程退出后重新显示主界面。"""
        if self.busy and not messagebox.askokcancel("正在变声", "任务还没跑完，确定要返回吗？"):
            return
        if callable(self.on_back):
            self.on_back()
        else:
            self.root.destroy()

    def _on_close(self) -> None:
        if self.busy and not messagebox.askokcancel("正在变声", "任务还没跑完，确定要退出吗？"):
            return
        # 先取消定时器：否则窗口销毁后它还会触发一次，去更新已经不存在的控件
        try:
            if self._after_id:
                self.root.after_cancel(self._after_id)
        except tk.TclError:
            pass
        try:
            if self._vram_after:
                self.root.after_cancel(self._vram_after)
        except tk.TclError:
            pass
        # 退出前把本进程占的显存还回去。变声模型很占（Seed-VC 常驻 2~3GB），
        # 不还的话下次开别的工具容易 OOM。
        try:
            vram.release()
        except Exception:                            # noqa: BLE001
            pass
        self.root.destroy()

    def release_vram(self) -> None:
        """手动释放显存：模型加载后一直占着，想腾出来给别的工具时点这个。"""
        ok, msg = vram.release_all_in_process()
        self._refresh_vram()
        if ok:
            messagebox.showinfo("释放显存", msg)
        else:
            messagebox.showinfo("释放显存", msg)

    def _refresh_vram(self) -> None:
        info = vram.gpu_info()
        if info is None:
            self.vram_lbl.config(text="显存  —", fg=DIM)
            return
        pct = info.used_pct
        color = OK if pct < 60 else (WARN if pct < 85 else ERR)
        self.vram_lbl.config(
            text=f"显存 {info.used_mb/1024:.1f}/{info.total_mb/1024:.1f}G",
            fg=color)

    def _tick_vram(self) -> None:
        self._refresh_vram()
        try:
            self._vram_after = self.root.after(2500, self._tick_vram)
        except tk.TclError:
            pass


def main() -> int:
    missing = [str(p) for p in (VENV_PY, CONVERT) if not p.exists()]
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("环境不完整", "缺少运行环境：\n" + "\n".join(missing) +
                             "\n\n请先完成安装（见 README.md）。")
        return 2

    # 自动化运行（自检 / --auto）一律切到沙箱目录，
    # 避免测试产物混进用户真正的成品里，也避免测试去清理成品目录。
    if "--selftest" in sys.argv or "--auto" in sys.argv:
        os.environ["DSH_SANDBOX"] = "1"
        globals()["OUTDIR"] = ROOT / "io" / "_sandbox"
        OUTDIR.mkdir(parents=True, exist_ok=True)
        print(f"[sandbox] 自动化运行，产物目录：{OUTDIR}")

    # --selftest: build the whole UI, render it once, report, and exit.
    # Used to verify the GUI headlessly (no human needed to look at a window).
    if "--selftest" in sys.argv:
        root = tk.Tk()
        app = App(root, [])
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
        print(f"[selftest] 开始按钮初始状态：{app.go['state']}")
        # pretend a file was chosen, confirm the button unlocks
        dummy = ROOT / "io" / "test_voice2_source.wav"
        dummy2 = ROOT / "io" / "test_voice1_reference.wav"
        if dummy.exists() and dummy2.exists():
            app.source, app.target = dummy, dummy2
            app._refresh_labels()
            root.update_idletasks()
            print(f"[selftest] 选好文件后按钮状态：{app.go['state']}")
            print(f"[selftest] 就绪提示：{app.status['text']}")

        # ---- 预设流程验证 ----
        ok = True
        # 注意 similarity 用 0.85：界面滑块有 resolution=0.05，会把值吸附到网格，
        # 存 0.88 再套用会变成 0.9 —— 那是滑块的正常行为，不是 bug。
        probe = Preset(name="_自检音色", voice_type=preset_store.VOICE_REALTIME,
                       ref_audio=str(dummy2), mode="v2",
                       similarity=0.85, steps=42)
        try:
            preset_store.save(probe)
            app.reload_presets()
            root.update_idletasks()
            found = [p for p in app.presets if p.id == probe.id]
            print(f"[selftest] 保存预设：{'通过' if found else '失败'}")
            ok &= bool(found)

            # 点它应当把参考音频和参数全部填上
            app.mode.set("v1")
            app.steps.set(30)
            app.similarity.set(0.7)
            app.apply_preset(found[0])
            root.update_idletasks()
            applied = (app.target == dummy2
                       and app.mode.get() == "v2"
                       and abs(app.similarity.get() - 0.85) < 1e-6
                       and app.steps.get() == 42)
            print(f"[selftest] 套用预设自动填参：{'通过' if applied else '失败'}"
                  f"  (mode={app.mode.get()} 相似度={app.similarity.get()}"
                  f" 精细度={app.steps.get()})")
            ok &= applied

            # 素材丢失检测：指向一个不存在的文件，应当报不可用并给出原因
            lost = Preset(name="_自检丢失", voice_type=preset_store.VOICE_REALTIME,
                          ref_audio=str(ROOT / "io" / "不存在的文件.wav"))
            usable = not lost.is_usable()
            print(f"[selftest] 素材丢失检测：{'通过' if usable else '失败'}"
                  f"  原因={lost.problem()!r}")
            ok &= usable

            # 渲染含失效预设的卡片行，确认不会崩
            preset_store.save(lost)
            app.reload_presets()
            root.update_idletasks()
            print("[selftest] 含失效预设的卡片渲染：通过")
        finally:
            # 清掉自检产生的预设，别污染用户的列表
            for p in preset_store.load_all():
                if p.name.startswith("_自检"):
                    preset_store.delete(p.id)
            probe_id = probe.id
            preset_store.delete(probe_id)
            app.reload_presets()

        print(f"[selftest] 剩余预设数量：{len(preset_store.load_all())}")
        print("[selftest] " + ("全部通过" if ok else "有问题，见上面失败行"))
        root.destroy()
        return 0 if ok else 1

    # --auto <source> <target> [mode]: drive the real conversion flow without a
    # human, exercising the same worker thread, progress pump and completion
    # handler that a button click uses. Pass a mode to cover every branch of the
    # argument building -- a wrong flag name there exits with code 2 and is
    # invisible unless each mode is actually run.
    if "--auto" in sys.argv:
        i = sys.argv.index("--auto")
        rest = [a for a in sys.argv[i + 1:] if not a.startswith("--")]
        if len(rest) < 2:
            print("[auto] 用法: oneclick.py --auto <要变声的音频> <音色参考音频> "
                  "[v1|v2|singing|song]")
            return 2
        src, tgt = Path(rest[0]), Path(rest[1])
        mode = rest[2] if len(rest) > 2 else "v1"
        if mode not in MODE_SPEC:
            print(f"[auto] 未知模式：{mode}（可选 {', '.join(MODE_SPEC)}）")
            return 2

        root = tk.Tk()
        app = App(root, [])
        app.source, app.target = src, tgt
        app.mode.set(mode)          # exactly what clicking the radio button does
        app._on_mode()
        app._refresh_labels()
        root.update_idletasks()
        print(f"[auto] mode={mode}")
        print(f"[auto] source={src.name}")
        print(f"[auto] target={tgt.name}")
        print(f"[auto] 按钮状态={app.go['state']}")
        app.start()

        deadline = time.time() + 900
        while app.busy and time.time() < deadline:
            root.update()          # let the real _drain pump run
            time.sleep(0.05)

        ok = (not app.busy) and app.last_out is not None and app.last_out.exists()
        print(f"[auto] 状态栏: {app.status['text']}")
        print(f"[auto] 进度条: {app.bar['value']}")
        print(f"[auto] 输出: {app.last_out}")
        if ok:
            print(f"[auto] 输出大小: {app.last_out.stat().st_size/1e6:.2f} MB")
        root.destroy()
        print("[auto] " + ("成功" if ok else "失败"))
        return 0 if ok else 1

    preselect = [a for a in sys.argv[1:] if Path(a).exists()]
    root = tk.Tk()
    App(root, preselect)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
