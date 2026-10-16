"""人声分离 —— 把歌/视频拆成人声和伴奏。

底层是 Demucs（项目里早就装好了，之前只在「整曲换主唱」里用），
这里把它单独做成一个工具，因为它自己就有很多用途：

  · 训练素材有 BGM  →  先分离，得到干净人声
  · 想做伴奏版      →  直接拿 no_vocals
  · 提取干净人声    →  拿 vocals 去做其他处理
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
        self.outdir = tk.StringVar(value=str(paths.OUTPUT / "分离"))

        root.title("人声分离")
        root.configure(bg=theme.BG)
        root.geometry("640x560")
        root.minsize(600, 520)

        self._build()
        self._after_id = None
        self._vram_after = None
        self.root.after(120, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 4))
        if self.on_back is not None:
            theme.button(head, "← 返回工具箱", self._back, small=True).pack(
                anchor="w", pady=(0, 6))
        tk.Label(head, text="🎤 人声分离", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="拆成人声 + 伴奏。用 Demucs，支持歌曲和视频。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(
            anchor="w", pady=(2, 0))

        card = tk.Frame(self.root, bg=theme.CARD)
        card.pack(fill="x", padx=theme.PAD, pady=(10, 0))
        inner = tk.Frame(card, bg=theme.CARD)
        inner.pack(fill="x", padx=14, pady=12)

        tk.Label(inner, text="要分离的文件", bg=theme.CARD, fg=theme.ACCENT,
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

        opt = tk.Frame(self.root, bg=theme.CARD)
        opt.pack(fill="x", padx=theme.PAD, pady=(8, 0))
        oi = tk.Frame(opt, bg=theme.CARD)
        oi.pack(fill="x", padx=14, pady=12)

        tk.Label(oi, text="输出目录", bg=theme.CARD, fg=theme.MUTED,
                 font=theme.F_BODY).pack(side="left")
        tk.Entry(oi, textvariable=self.outdir, bg=theme.PANEL, fg=theme.FG,
                 insertbackground=theme.FG, relief="flat", font=theme.F_SMALL,
                 width=42).pack(side="left", padx=(8, 6), ipady=3)
        theme.button(oi, "改", self.pick_outdir, small=True).pack(side="left")

        tk.Label(oi, text="（分离出的 vocals / no_vocals 都在这里）", bg=theme.CARD,
                 fg=theme.DIM, font=theme.F_SMALL).pack(anchor="w", pady=(6, 0))

        self.go = theme.button(self.root, "开始分离", self.start, primary=True)
        self.go.pack(fill="x", padx=theme.PAD, pady=(14, 6), ipady=6)

        self.bar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=theme.PAD)

        logbox = tk.Frame(self.root, bg=theme.BG)
        logbox.pack(fill="both", expand=True, padx=theme.PAD, pady=(8, 0))
        self.log = tk.Text(logbox, bg=theme.PANEL, fg=theme.MUTED, relief="flat",
                           bd=0, height=8, wrap="word", font=theme.F_SMALL,
                           highlightthickness=0)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

        run = tk.Frame(self.root, bg=theme.BG)
        run.pack(fill="x", padx=theme.PAD, pady=(8, 14))
        self.open_btn = theme.button(run, "打开输出目录", self.open_out, small=True)
        self.open_btn.pack(side="left")
        self.open_btn.config(state="disabled")

    # -------------------------------------------------------------- 选文件 --
    def pick_files(self) -> None:
        fs = filedialog.askopenfilenames(
            title="选要分离的音频或视频",
            filetypes=[("音频/视频", "*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.mp4 *.mkv *.mov *.avi *.webm"),
                       ("全部", "*.*")])
        if fs:
            self.files = [Path(f) for f in fs]
            self._refresh()

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title="选文件夹（含子目录里的音视频）")
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
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("没选文件", "先选要分离的音频或视频。")
            return
        self.busy = True
        self.go.config(state="disabled", text="分离中…")
        self.bar.config(value=0)
        self.outputs = []
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        out_root = Path(self.outdir.get())
        total = len(self.files)
        ok = fail = 0
        t0 = time.time()

        for i, f in enumerate(self.files, 1):
            self.q.put(("log", f"\n[{i}/{total}] {f.name}"))
            self.q.put(("progress", str(i - 1)))
            self.q.put(("total", str(total)))
            try:
                for line in pipeline.separate(f, out_root):
                    if line.startswith("__DONE__"):
                        parts = line[8:].split("|")
                        for p in parts:
                            if p:
                                self.outputs.append(Path(p))
                        ok += 1
                    elif line.startswith("__FAIL__"):
                        self.q.put(("log", "  " + line[8:]))
                        fail += 1
                    else:
                        if any(k in line for k in ("Separating", "Separated",
                                                   "%", "error", "Error")):
                            self.q.put(("log", "  " + line[:150]))
            except Exception as exc:                 # noqa: BLE001
                self.q.put(("log", f"  出错：{type(exc).__name__}: {exc}"))
                fail += 1
            self.q.put(("progress", str(i)))

        self.q.put(("done", f"完成：成功 {ok}，失败 {fail}，"
                            f"用时 {time.time()-t0:.0f} 秒"))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self._say(payload)
                elif kind == "progress":
                    self._done_n = int(payload)
                elif kind == "total":
                    self._total = max(int(payload), 1)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return
        # 进度条按"已完成个数"算
        if getattr(self, "_total", 0):
            self.bar.config(value=self._done_n / self._total * 100)
        try:
            self._after_id = self.root.after(120, self._drain)
        except tk.TclError:
            pass

    def _finish(self, msg: str) -> None:
        self.busy = False
        self.bar.config(value=100)
        self.go.config(state="normal", text="开始分离")
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
        if self.busy and not messagebox.askokcancel("正在分离", "还没跑完，确定退出？"):
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
    """无头自检：建界面 + 真跑一次分离（用项目里的测试音频）。"""
    from tools.audio.pipeline import selftest as pipe_test
    if pipe_test("separate") != 0:
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

    # 找一段测试音频
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

    out = paths.SANDBOX / "separate_test"
    print(f"[selftest] 真跑一次分离：{src.name}")
    got = None
    for line in pipeline.separate(src, out):
        if line.startswith("__DONE__"):
            got = line[8:].split("|")[0]
        elif line.startswith("__FAIL__"):
            print("  " + line)
    ok = bool(got) and Path(got).exists()
    print(f"[selftest] 分离测试：{'通过' if ok else '失败'}")
    if ok:
        import soundfile as sf
        info = sf.info(got)
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
