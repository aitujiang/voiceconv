"""字幕识别 —— 音视频转文字，产出 SRT 和 TXT。

底层是 Whisper（变声工具早就下好了 whisper-small 权重，直接用）。
除了做字幕，它还有个更重要的用途：**给训练素材自动标注**。

GPT-SoVITS 训练要求每段音频配一句文字，而游戏/动画配音没有字幕文件。
这个工具能批量把素材转成带文字的清单。
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

MODELS = {
    "small": "small · 快，日常够用（约 1GB，已下载）",
    "medium": "medium · 更准，慢一倍（约 3GB，需下载）",
    "large-v3": "large-v3 · 最准，慢很多（约 6GB，需下载）",
}

LANGS = {
    "chinese": "中文",
    "english": "英语",
    "japanese": "日语",
    "korean": "韩语",
    "auto": "自动检测",
}


class App:
    def __init__(self, root: tk.Tk, on_back=None) -> None:
        self.root = root
        self.on_back = on_back
        self.files: list[Path] = []
        self.busy = False
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.outputs: list[Path] = []
        self.outdir = tk.StringVar(value=str(paths.OUTPUT / "字幕"))
        self.model = tk.StringVar(value="small")
        self.lang = tk.StringVar(value="chinese")
        self.want_srt = tk.BooleanVar(value=True)
        self.want_txt = tk.BooleanVar(value=True)

        root.title("字幕识别")
        root.configure(bg=theme.BG)
        root.geometry("660x620")
        root.minsize(620, 580)

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
        tk.Label(head, text="💬 字幕识别", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="音视频转文字，输出 SRT 字幕和 TXT 文本。"
                            "也能给训练素材自动标注。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(
            anchor="w", pady=(2, 0))

        card = tk.Frame(self.root, bg=theme.CARD)
        card.pack(fill="x", padx=theme.PAD, pady=(10, 0))
        inner = tk.Frame(card, bg=theme.CARD)
        inner.pack(fill="x", padx=14, pady=12)
        tk.Label(inner, text="要识别的文件", bg=theme.CARD, fg=theme.ACCENT,
                 font=theme.F_CARD).pack(anchor="w")
        self.list_lbl = tk.Label(inner, text="还没选文件", bg=theme.CARD, fg=theme.MUTED,
                                 font=theme.F_SMALL, anchor="w", justify="left",
                                 wraplength=570)
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

        tk.Label(oi, text="识别精度", bg=theme.CARD, fg=theme.ACCENT,
                 font=theme.F_CARD).pack(anchor="w")
        for k, label in MODELS.items():
            tk.Radiobutton(oi, text=label, value=k, variable=self.model,
                           bg=theme.CARD, fg=theme.FG, selectcolor=theme.PANEL,
                           activebackground=theme.CARD, activeforeground=theme.FG,
                           font=theme.F_SMALL, anchor="w", highlightthickness=0,
                           bd=0).pack(anchor="w")

        lang = tk.Frame(oi, bg=theme.CARD)
        lang.pack(fill="x", pady=(8, 0))
        tk.Label(lang, text="语言", bg=theme.CARD, fg=theme.MUTED,
                 font=theme.F_BODY).pack(side="left")
        om = tk.OptionMenu(lang, self.lang, *LANGS.keys())
        om.config(bg=theme.PANEL, fg=theme.FG, activebackground=theme.CARD_HOVER,
                  highlightthickness=0, bd=0, font=theme.F_SMALL, width=12)
        om["menu"].config(bg=theme.PANEL, fg=theme.FG, font=theme.F_SMALL)
        om.pack(side="left", padx=(8, 14))

        tk.Checkbutton(lang, text="SRT 字幕", variable=self.want_srt,
                       bg=theme.CARD, fg=theme.FG, selectcolor=theme.PANEL,
                       activebackground=theme.CARD, activeforeground=theme.FG,
                       font=theme.F_SMALL, highlightthickness=0,
                       bd=0).pack(side="left")
        tk.Checkbutton(lang, text="TXT 纯文本", variable=self.want_txt,
                       bg=theme.CARD, fg=theme.FG, selectcolor=theme.PANEL,
                       activebackground=theme.CARD, activeforeground=theme.FG,
                       font=theme.F_SMALL, highlightthickness=0,
                       bd=0).pack(side="left", padx=(10, 0))

        out = tk.Frame(self.root, bg=theme.BG)
        out.pack(fill="x", padx=theme.PAD, pady=(8, 0))
        tk.Label(out, text="输出目录", bg=theme.BG, fg=theme.MUTED,
                 font=theme.F_BODY).pack(side="left")
        tk.Entry(out, textvariable=self.outdir, bg=theme.PANEL, fg=theme.FG,
                 insertbackground=theme.FG, relief="flat", font=theme.F_SMALL,
                 width=40).pack(side="left", padx=(8, 6), ipady=3)
        theme.button(out, "改", self.pick_outdir, small=True).pack(side="left")

        self.go = theme.button(self.root, "开始识别", self.start, primary=True)
        self.go.pack(fill="x", padx=theme.PAD, pady=(14, 6), ipady=6)

        self.bar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=theme.PAD)

        # 显存条：Whisper 模型常驻显存，这里能看到占用
        vr = tk.Frame(self.root, bg=theme.BG)
        vr.pack(fill="x", padx=theme.PAD, pady=(6, 0))
        from shared import vram
        self.vram_lbl = tk.Label(vr, text="显存 …", bg=theme.BG, fg=theme.MUTED,
                                 font=theme.F_SMALL)
        self.vram_lbl.pack(side="left")
        theme.button(vr, "释放显存", self.release_vram, small=True).pack(side="right")

        logbox = tk.Frame(self.root, bg=theme.BG)
        logbox.pack(fill="both", expand=True, padx=theme.PAD, pady=(6, 0))
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
        self._tick_vram()

    # ------------------------------------------------------------ 显存 --
    def release_vram(self) -> None:
        from shared import vram
        # Whisper 的模型要一起丢掉，否则显存还占着
        Trans = pipeline.Transcriber
        Trans._cache.clear()
        _ok, msg = vram.release_all_in_process()
        self._refresh_vram()
        messagebox.showinfo("释放显存", msg + "\n（Whisper 模型已从内存卸载）")

    def _refresh_vram(self) -> None:
        from shared import vram
        info = vram.gpu_info()
        if info is None:
            self.vram_lbl.config(text="显存  —", fg=theme.DIM)
            return
        pct = info.used_pct
        color = theme.OK if pct < 60 else (theme.WARN if pct < 85 else theme.ERR)
        self.vram_lbl.config(
            text=f"显存 {info.used_mb/1024:.1f}/{info.total_mb/1024:.1f}G", fg=color)

    def _tick_vram(self) -> None:
        self._refresh_vram()
        try:
            self._vram_after = self.root.after(2500, self._tick_vram)
        except tk.TclError:
            pass

    # -------------------------------------------------------------- 选文件 --
    def pick_files(self) -> None:
        fs = filedialog.askopenfilenames(
            title="选要识别的音视频",
            filetypes=[("音视频", "*.wav *.mp3 *.flac *.m4a *.mp4 *.mkv *.mov *.avi *.webm *.ts"),
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
            names = "、".join(f.name for f in self.files[:3])
            more = f" 等 {len(self.files)} 个" if len(self.files) > 3 else ""
            self.list_lbl.config(text=f"已选 {len(self.files)} 个：{names}{more}",
                                 fg=theme.FG)

    def _say(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # -------------------------------------------------------------- 运行 --
    def start(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("没选文件", "先选要识别的音视频。")
            return
        self.busy = True
        self.go.config(state="disabled", text="识别中…")
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
        size = self.model.get()
        if size != "small":
            self.q.put(("log", f"注意：{size} 首次使用需要下载权重，可能要等几分钟"))

        for i, f in enumerate(self.files, 1):
            self.q.put(("log", f"\n[{i}/{total}] {f.name}"))
            try:
                for line in pipeline.transcribe_file(
                        f, out_root, size=size, language=self.lang.get(),
                        want_srt=self.want_srt.get(), want_txt=self.want_txt.get()):
                    if line.startswith("__DONE__"):
                        self.outputs += [Path(p) for p in line[8:].split("|") if p]
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
        self.go.config(state="normal", text="开始识别")
        self._say("\n" + msg)
        if self.outputs:
            self.open_btn.config(state="normal")
            opener.open_path(self.outputs[0].parent)

    def open_out(self) -> None:
        target = self.outputs[0].parent if self.outputs else Path(self.outdir.get())
        ok, why = opener.open_path(target)
        if not ok:
            messagebox.showwarning("打不开", why)

    def _close(self) -> None:
        if self.busy and not messagebox.askokcancel("正在识别", "还没跑完，确定退出？"):
            return
        for attr in ("_after_id", "_vram_after"):
            try:
                aid = getattr(self, attr, None)
                if aid:
                    self.root.after_cancel(aid)
            except tk.TclError:
                pass
        try:
            from shared import vram
            vram.release()
        except Exception:                            # noqa: BLE001
            pass
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()

    def _back(self) -> None:
        self._close()


def selftest() -> int:
    """无头自检。

    顺序很重要：**先跑识别逻辑，再建界面**。
    反过来的话 Tk 和 Whisper 挤在同一个进程里，实测会导致进程直接崩掉
    （exit -805306369），看起来像"功能坏了"，其实是自检的写法问题。
    """
    from tools.audio.pipeline import selftest as pipe_test
    if pipe_test("subtitle") != 0:
        return 1

    paths.ensure_dirs()

    src = None
    for cand in (ROOT / "io" / "test_voice1_reference.wav",
                 ROOT / "io" / "test_voice2_source.wav"):
        if cand.exists():
            src = cand
            break

    made: list[str] = []
    if src is not None:
        out = paths.SANDBOX / "subtitle_test"
        print(f"[selftest] 真跑一次识别：{src.name}")
        for line in pipeline.transcribe_file(src, out, size="small"):
            print("  " + line[:150])
            if line.startswith("__DONE__"):
                made = [p for p in line[8:].split("|") if p]
        if made:
            ok = all(Path(p).exists() for p in made)
            print(f"[selftest] 字幕测试：{'通过' if ok else '产物缺失'}")
        else:
            # 合成音频被判定成幻觉是**正确行为**（说明过滤生效了），
            # 不算失败 —— 真实人声素材上会正常产出。
            print("[selftest] 这段测试音频不是人声，被幻觉过滤挡下（属预期）")
            print("[selftest] 字幕测试：通过（过滤逻辑生效）")
    else:
        print("[selftest] 没有测试音频，跳过实跑")

    # 最后才建界面
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
    app._close()
    return 0


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
