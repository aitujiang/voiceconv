"""音色库 —— 管理所有音色预设。

和「变声」界面共用同一份数据（`shared/presets.py` → `data/presets/*.json`），
所以在哪边改都一样。

这里管的是「音色本身」：叫什么、用哪段素材、什么参数。
变声界面那边管的是「这次要处理哪个文件」。

素材被移动或删除时，卡片会标红并给出「重新指定」按钮 —— 不静默失败。
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import paths, presets as preset_store, theme      # noqa: E402
from shared import opener                                     # noqa: E402
from shared.presets import Preset                             # noqa: E402

AUDIO_TYPES = [
    ("音频文件", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.opus"),
    ("所有文件", "*.*"),
]


class App:
    def __init__(self, root: tk.Tk, on_back=None) -> None:
        self.root = root
        self.on_back = on_back
        self.items: list[Preset] = preset_store.load_all()

        root.title("音色库")
        root.configure(bg=theme.BG)
        root.geometry("700x560")
        root.minsize(660, 520)

        self._build()

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 6))
        if self.on_back is not None:
            theme.button(head, "← 返回工具箱", self._back, small=True).pack(anchor="w",
                                                                             pady=(0, 6))
        tk.Label(head, text="📚 音色库", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="一个「音色」= 一段素材 + 一套参数。存一次，变声时一键调用。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(anchor="w",
                                                                       pady=(2, 0))

        bar = tk.Frame(self.root, bg=theme.BG)
        bar.pack(fill="x", padx=theme.PAD, pady=(10, 6))
        theme.button(bar, "＋ 新建音色", self.create, primary=True).pack(side="left")
        theme.button(bar, "打开音色文件夹", self.open_folder, small=True).pack(side="right")

        self.listbox_wrap = tk.Frame(self.root, bg=theme.CARD)
        self.listbox_wrap.pack(fill="both", expand=True, padx=theme.PAD)

        self.canvas = tk.Canvas(self.listbox_wrap, bg=theme.CARD, highlightthickness=0)
        sb = tk.Scrollbar(self.listbox_wrap, command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=theme.CARD)
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        tk.Label(self.root, text=f"数据目录：{paths.PRESETS}",
                 bg=theme.BG, fg=theme.DIM, font=theme.F_SMALL, anchor="w"
                 ).pack(fill="x", padx=theme.PAD, pady=(8, 12))

        self._render()

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tk.TclError:
            pass

    def _render(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()

        if not self.items:
            tk.Label(self.inner,
                     text="还没有音色。\n\n点「＋ 新建音色」，选一段角色配音当素材，\n"
                          "以后变声时点一下它就能自动填好参数。",
                     bg=theme.CARD, fg=theme.MUTED, font=theme.F_BODY,
                     justify="left").pack(anchor="w", padx=16, pady=20)
            return

        for p in self.items:
            self._row(p)

    def _row(self, p: Preset) -> None:
        ok = p.is_usable()
        bg = theme.PANEL if ok else "#33232a"
        row = tk.Frame(self.inner, bg=bg)
        row.pack(fill="x", padx=10, pady=(0, 8))

        left = tk.Frame(row, bg=bg)
        left.pack(side="left", fill="x", expand=True, padx=12, pady=10)

        title = tk.Frame(left, bg=bg)
        title.pack(anchor="w")
        tk.Label(title, text=p.type_icon(), bg=bg,
                 font=("Segoe UI Emoji", 12)).pack(side="left")
        tk.Label(title, text=p.name, bg=bg, fg=theme.FG if ok else "#ff9e97",
                 font=theme.F_CARD).pack(side="left", padx=(6, 0))
        tk.Label(title, text=p.type_label(), bg=bg, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left", padx=(8, 0))

        detail = p.summary() if ok else f"⚠ {p.problem()}"
        tk.Label(left, text=detail, bg=bg,
                 fg=theme.MUTED if ok else "#ff9e97",
                 font=theme.F_SMALL, anchor="w", justify="left",
                 wraplength=430).pack(anchor="w", pady=(3, 0))

        acts = tk.Frame(row, bg=bg)
        acts.pack(side="right", padx=12)
        if not ok:
            theme.button(acts, "重新指定", lambda: self.respecify(p), primary=True,
                         small=True).pack(side="left", padx=(0, 6))
        else:
            theme.button(acts, "试听", lambda: self.preview(p), small=True).pack(
                side="left", padx=(0, 6))
        theme.button(acts, "编辑", lambda: self.edit(p), small=True).pack(
            side="left", padx=(0, 6))
        theme.button(acts, "复制", lambda: self.duplicate(p), small=True).pack(
            side="left", padx=(0, 6))
        theme.button(acts, "删除", lambda: self.delete(p), small=True).pack(side="left")

    # -------------------------------------------------------------- 动作 --
    def create(self) -> None:
        name = simpledialog.askstring("新建音色", "给这个音色起个名字（比如角色名）：",
                                      parent=self.root)
        if not name:
            return
        src = filedialog.askopenfilename(title="选一段配音当素材（建议 5~25 秒干净人声）",
                                         filetypes=AUDIO_TYPES,
                                         initialdir=str(paths.VOICES))
        if not src:
            return
        p = Preset(name=name.strip(), voice_type=preset_store.VOICE_REALTIME,
                   ref_audio=src)
        preset_store.save(p)
        self.items = preset_store.load_all()
        self._render()

    def edit(self, p: Preset) -> None:
        name = simpledialog.askstring("编辑", "音色名字：", initialvalue=p.name,
                                      parent=self.root)
        if name is None:
            return
        p.name = name.strip() or p.name

        if p.voice_type == preset_store.VOICE_REALTIME:
            mode = simpledialog.askstring(
                "编辑", "模式：填 v1（保留你的表演）/ v2（更贴近参考）/ singing（唱歌）",
                initialvalue=p.mode, parent=self.root)
            if mode in ("v1", "v2", "singing"):
                p.mode = mode

            steps = simpledialog.askinteger("编辑", "精细度（10~50，默认 30）：",
                                            initialvalue=p.steps, minvalue=5,
                                            maxvalue=80, parent=self.root)
            if steps:
                p.steps = steps

            if p.mode == "v2":
                sim = simpledialog.askfloat("编辑", "音色相似度（0.3~1.0，越高越像）：",
                                            initialvalue=p.similarity,
                                            minvalue=0.1, maxvalue=1.0,
                                            parent=self.root)
                if sim is not None:
                    p.similarity = round(sim, 2)

        preset_store.save(p)
        self.items = preset_store.load_all()
        self._render()

    def duplicate(self, p: Preset) -> None:
        copy = preset_store.duplicate(p)
        self.items = preset_store.load_all()
        self._render()

    def delete(self, p: Preset) -> None:
        if not messagebox.askyesno("删除", f"确定删除音色「{p.name}」？\n\n"
                                           "只删这个预设，不会删你的素材文件。"):
            return
        preset_store.delete(p.id)
        self.items = preset_store.load_all()
        self._render()

    def respecify(self, p: Preset) -> None:
        """素材/模型丢了，重新指定一个。其他参数保持不变。"""
        if p.voice_type == preset_store.VOICE_REALTIME:
            new = filedialog.askopenfilename(
                title=f"给「{p.name}」重新指定素材", filetypes=AUDIO_TYPES,
                initialdir=str(paths.VOICES))
            if not new:
                return
            p.ref_audio = new
        else:
            new = filedialog.askopenfilename(title=f"给「{p.name}」重新指定模型")
            if not new:
                return
            p.model_path = new
        preset_store.save(p)
        self.items = preset_store.load_all()
        self._render()
        messagebox.showinfo("已修复", f"「{p.name}」现在可以用了。")

    def preview(self, p: Preset) -> None:
        """用系统默认播放器放一下素材，确认是不是想要的那个音色。"""
        target = p.ref_audio if p.voice_type == preset_store.VOICE_REALTIME \
            else p.model_path
        if not target:
            messagebox.showinfo("没有文件", "这个音色还没指定素材。")
            return
        ok, why = opener.open_path(target)
        if not ok:
            messagebox.showwarning("打不开", why)

    def open_folder(self) -> None:
        paths.PRESETS.mkdir(parents=True, exist_ok=True)
        ok, why = opener.open_path(paths.PRESETS)
        if not ok:
            messagebox.showwarning("打不开", why)

    def _back(self) -> None:
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()


def selftest() -> int:
    """无头自检：建界面 + 验证增删改查与素材丢失提示。"""
    paths.ensure_dirs()
    root = tk.Tk()
    app = App(root, on_back=None)
    root.update_idletasks()
    root.update()

    widgets = 0
    stack = [root]
    while stack:
        w = stack.pop()
        widgets += 1
        stack.extend(w.winfo_children())
    print(f"[selftest] 界面构建成功：{widgets} 个控件")

    before = len(preset_store.load_all())
    print(f"[selftest] 现有音色：{before} 个")

    made: list[str] = []
    ok = True
    try:
        good = preset_store.save(Preset(
            name="_自检正常", ref_audio=str(ROOT / "io" / "test_voice1_reference.wav")))
        bad = preset_store.save(Preset(
            name="_自检缺失", ref_audio=str(ROOT / "io" / "不存在.wav")))
        made = [good.id, bad.id]

        app.items = preset_store.load_all()
        app._render()
        root.update_idletasks()
        print(f"[selftest] 渲染含失效音色的列表：通过（{len(app.items)} 行）")

        usable_ok = good.is_usable() and not bad.is_usable()
        print(f"[selftest] 可用性判断：{'通过' if usable_ok else '失败'}")
        ok &= usable_ok

        dup = preset_store.duplicate(good)
        made.append(dup.id)
        dup_ok = dup.id != good.id and dup.name.endswith("副本")
        print(f"[selftest] 复制音色：{'通过' if dup_ok else '失败'}")
        ok &= dup_ok

        # 编辑后落盘
        good.steps = 44
        preset_store.save(good)
        reloaded = [p for p in preset_store.load_all() if p.id == good.id]
        edit_ok = bool(reloaded) and reloaded[0].steps == 44
        print(f"[selftest] 编辑并落盘：{'通过' if edit_ok else '失败'}")
        ok &= edit_ok
    finally:
        for pid in made:
            preset_store.delete(pid)
        app.items = preset_store.load_all()
        app._render()

    after = len(preset_store.load_all())
    clean = after == before
    print(f"[selftest] 清理自检数据：{'通过' if clean else '失败'}"
          f"（{before} -> {after}）")
    ok &= clean

    print("[selftest] " + ("全部通过" if ok else "有问题"))
    root.destroy()
    return 0 if ok else 1


def launch(parent=None, on_back=None) -> int:
    root = tk.Tk()
    App(root, on_back=on_back)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    embedded = "--embedded" in argv
    paths.ensure_dirs()
    return launch(on_back=(lambda: None) if embedded else None)


if __name__ == "__main__":
    raise SystemExit(main())
