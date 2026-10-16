"""响度标准化 —— 把音量统一到平台要求，避免被压。

为什么需要
----------
各平台对上传音频有响度要求（B站/抖音约 -14 LUFS）。
不达标的成品会被平台**二次压缩**，动态和音量都变差。

之前做「整曲换主唱」时就遇到过：变声后的人声比伴奏响了 +9 dB，
只能手动调小 —— 那就是缺少响度统一的表现。

底层是 ffmpeg 的 loudnorm 滤镜，两遍处理（先测量再处理），
比单遍模式保真度高。
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import opener, paths, theme               # noqa: E402
from tools.audio import pipeline                      # noqa: E402


class App:
    def __init__(self, root: tk.Tk, on_back=None) -> None:
        self.root = root
        self.on_back = on_back
        self.files: list[Path] = []
        self.busy = False
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.outputs: list[Path] = []
        self.outdir = tk.StringVar(value=str(paths.OUTPUT / "标准化"))
        self.target_name = tk.StringVar(value="B站 / 抖音")
        self.custom = tk.DoubleVar(value=-14.0)
        self.use_custom = tk.BooleanVar(value=False)

        root.title("响度标准化")
        root.configure(bg=theme.BG)
        root.geometry("640x580")
        root.minsize(600, 540)

        self._build()
        self._after_id = None
        self.root.after(120, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 4))
        if self.on_back is not None:
            theme.button(head, "← 返回工具箱", self._back, small=True).pack(
                anchor="w", pady=(0, 6))
        tk.Label(head, text="📊 响度标准化", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="把音量统一到平台标准，避免上传后被压。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(
            anchor="w", pady=(2, 0))

        card = tk.Frame(self.root, bg=theme.CARD)
        card.pack(fill="x", padx=theme.PAD, pady=(10, 0))
        inner = tk.Frame(card, bg=theme.CARD)
        inner.pack(fill="x", padx=14, pady=12)

        tk.Label(inner, text="要处理的文件", bg=theme.CARD, fg=theme.ACCENT,
                 font=theme.F_CARD).pack(anchor="w")
        self.list_lbl = tk.Label(inner, text="还没选文件", bg=theme.CARD, fg=theme.MUTED,
                                 font=theme.F_SMALL, anchor="w", justify="left",
                                 wraplength=560)
        self.list_lbl.pack(anchor="w", pady=(4, 6))
        row = tk.Frame(inner, bg=theme.CARD)
        row.pack(fill="x")
        theme.button(row, "选文件", self.pick_files, primary=True).pack(side="left")
        theme.button(row, "选文件夹", self.pick_folder, small=True).pack(
            side="left", padx=(8, 0))
        theme.button(row, "清空", self.clear, small=True).pack(side="left", padx=(8, 0))

        tgt = tk.Frame(self.root, bg=theme.CARD)
        tgt.pack(fill="x", padx=theme.PAD, pady=(8, 0))
        ti = tk.Frame(tgt, bg=theme.CARD)
        ti.pack(fill="x", padx=14, pady=12)

        tk.Label(ti, text="目标响度", bg=theme.CARD, fg=theme.ACCENT,
                 font=theme.F_CARD).pack(anchor="w")

        grid = tk.Frame(ti, bg=theme.CARD)
        grid.pack(fill="x", pady=(6, 0))
        for i, (name, lufs) in enumerate(pipeline.LOUDNESS_TARGETS.items()):
            tk.Radiobutton(
                grid, text=f"{name}   {lufs:g} LUFS", value=name,
                variable=self.target_name, command=self._on_target,
                bg=theme.CARD, fg=theme.FG, selectcolor=theme.PANEL,
                activebackground=theme.CARD, activeforeground=theme.FG,
                font=theme.F_SMALL, anchor="w", highlightthickness=0,
                bd=0).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 18), pady=1)

        cust = tk.Frame(ti, bg=theme.CARD)
        cust.pack(fill="x", pady=(8, 0))
        tk.Checkbutton(cust, text="自定义", variable=self.use_custom,
                       command=self._on_target, bg=theme.CARD, fg=theme.FG,
                       selectcolor=theme.PANEL, activebackground=theme.CARD,
                       activeforeground=theme.FG, font=theme.F_SMALL,
                       highlightthickness=0, bd=0).pack(side="left")
        tk.Scale(cust, from_=-30, to=-8, resolution=0.5, orient="horizontal",
                 variable=self.custom, bg=theme.CARD, fg=theme.FG,
                 troughcolor=theme.BG, highlightthickness=0, bd=0, length=230,
                 font=theme.F_SMALL).pack(side="left", padx=(6, 6))
        tk.Label(cust, text="LUFS", bg=theme.CARD, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left")

        out = tk.Frame(self.root, bg=theme.BG)
        out.pack(fill="x", padx=theme.PAD, pady=(8, 0))
        tk.Label(out, text="输出目录", bg=theme.BG, fg=theme.MUTED,
                 font=theme.F_BODY).pack(side="left")
        tk.Entry(out, textvariable=self.outdir, bg=theme.PANEL, fg=theme.FG,
                 insertbackground=theme.FG, relief="flat", font=theme.F_SMALL,
                 width=40).pack(side="left", padx=(8, 6), ipady=3)
        theme.button(out, "改", self.pick_outdir, small=True).pack(side="left")

        self.go = theme.button(self.root, "开始标准化", self.start, primary=True)
        self.go.pack(fill="x", padx=theme.PAD, pady=(14, 6), ipady=6)

        self.bar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=theme.PAD)

        logbox = tk.Frame(self.root, bg=theme.BG)
        logbox.pack(fill="both", expand=True, padx=theme.PAD, pady=(8, 0))
        self.log = tk.Text(logbox, bg=theme.PANEL, fg=theme.MUTED, relief="flat",
                           bd=0, height=7, wrap="word", font=theme.F_SMALL,
                           highlightthickness=0)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        run = tk.Frame(self.root, bg=theme.BG)
        run.pack(fill="x", padx=theme.PAD, pady=(8, 14))
        self.open_btn = theme.button(run, "打开输出目录", self.open_out, small=True)
        self.open_btn.pack(side="left")
        self.open_btn.config(state="disabled")

    def _on_target(self) -> None:
        pass

    def target_lufs(self) -> float:
        if self.use_custom.get():
            return float(self.custom.get())
        return pipeline.LOUDNESS_TARGETS.get(self.target_name.get(), -14.0)

    # -------------------------------------------------------------- 选文件 --
    def pick_files(self) -> None:
        fs = filedialog.askopenfilenames(
            title="选要标准化的音频",
            filetypes=[("音频", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus"),
                       ("全部", "*.*")])
        if fs:
            self.files = [Path(f) for f in fs]
            self._refresh()

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title="选文件夹")
        if d:
            self.files = pipeline.find_inputs([Path(d)])
            self._refresh()

    def clear(self) -> None:
        self.files = []
        self._refresh()

    def pick_outdir(self) -> None:
        d = filedialog.askdirectory(title="选输出目录")
        if d:
            self.outdir.set(d)

    def _refresh(self) -> None:
        if not self.files:
            self.list_lbl.config(text="还没选文件", fg=theme.MUTED)
        else:
            names = "、".join(f.name for f in self.files[:4])
            more = f" 等 {len(self.files)} 个" if len(self.files) > 4 else ""
            self.list_lbl.config(text=f"已选 {len(self.files)} 个：{names}{more}",
                                 fg=theme.FG)

    def _say(self, text: str, colour=None) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        if colour:
            self.status.config(fg=colour)

    # -------------------------------------------------------------- 运行 --
    def start(self) -> None:
        if self.busy or not self.files:
            if not self.files:
                messagebox.showinfo("没选文件", "先选要标准化的音频。")
            return
        self.busy = True
        self.go.config(state="disabled", text="处理中…")
        self.bar.config(value=0)
        self.outputs = []
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        out_root = Path(self.outdir.get())
        lufs = self.target_lufs()
        total = len(self.files)
        ok = fail = 0
        t0 = time.time()
        self.q.put(("log", f"目标响度：{lufs:g} LUFS"))

        for i, f in enumerate(self.files, 1):
            self.q.put(("log", f"\n[{i}/{total}] {f.name}"))
            dst = out_root / f"{f.stem}_标准化{f.suffix}"
            try:
                for line in pipeline.normalize_loudness(f, dst, lufs):
                    if line.startswith("__DONE__"):
                        self.outputs.append(Path(line[8:]))
                        ok += 1
                    elif line.startswith("__FAIL__"):
                        self.q.put(("log", "  " + line[8:]))
                        fail += 1
                    elif line.strip():
                        self.q.put(("log", "  " + line[:150]))
            except Exception as exc:                 # noqa: BLE001
                self.q.put(("log", f"  出错：{type(exc).__name__}: {exc}"))
                fail += 1
            self.q.put(("progress", f"{i}/{total}"))

        self.q.put(("done", f"完成：成功 {ok}，失败 {fail}，"
                            f"用时 {time.time()-t0:.0f} 秒"))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._say(payload)
                elif kind == "progress":
                    a, b = payload.split("/")
                    self.bar.config(value=float(a) / max(float(b), 1) * 100)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            self._after_id = self.root.after(120, self._drain)
        except tk.TclError:
            pass

    def _finish(self, msg: str) -> None:
        self.busy = False
        self.bar.config(value=100)
        self.go.config(state="normal", text="开始标准化")
        self._say("\n" + msg, theme.OK)
        if self.outputs:
            self.open_btn.config(state="normal")
            opener.open_path(self.outputs[0].parent)

    def open_out(self) -> None:
        target = self.outputs[0].parent if self.outputs else Path(self.outdir.get())
        ok, why = opener.open_path(target)
        if not ok:
            messagebox.showwarning("打不开", why)

    def _close(self) -> None:
        if self.busy and not messagebox.askokcancel("正在处理", "还没跑完，确定退出？"):
            return
        try:
            if self._after_id:
                self.root.after_cancel(self._after_id)
        except tk.TclError:
            pass
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()

    def _back(self) -> None:
        self._close()


def selftest() -> int:
    """无头自检：建界面 + 真跑一次标准化。"""
    from tools.audio.pipeline import selftest as pipe_test
    if pipe_test("loudness") != 0:
        return 1

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

    src = None
    for cand in (ROOT / "io" / "test_voice1_reference.wav",
                 ROOT / "io" / "test_voice2_source.wav"):
        if cand.exists():
            src = cand
            break
    if src is None:
        print("[selftest] 没有测试音频，跳过实跑")
        app._close()
        return 0

    dst = paths.SANDBOX / "loudnorm_test.wav"
    print(f"[selftest] 真跑一次标准化：{src.name}")
    for line in pipeline.normalize_loudness(src, dst, -14.0):
        if line.strip():
            print("  " + line[:140])

    ok = dst.exists() and dst.stat().st_size > 1000
    print(f"[selftest] 标准化测试：{'通过' if ok else '失败'}")
    if ok:
        import soundfile as sf
        info = sf.info(str(dst))
        print(f"[selftest] 产物 {info.frames/info.samplerate:.2f}s / {info.samplerate}Hz")
    app._close()
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
