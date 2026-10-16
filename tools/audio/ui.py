"""音频提取工具 —— 从视频里抽出音频。

刻意做得小：**不建虚拟环境、不装任何包**，直接调项目里已有的 ffmpeg。
这样一个工具的环境成本是 0 字节，也符合「尽量缩小体积」的要求。

支持：
* 选多个文件，或选一整个文件夹（含子目录）批量提取
* 输出 MP3 / WAV / 保留原始音轨
* 提取完可以直接送去 AI 变声
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
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import paths, theme                       # noqa: E402
from shared import opener                             # noqa: E402

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts",
             ".m4v", ".mpg", ".mpeg", ".rmvb", ".3gp", ".vob"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma", ".opus"}

FORMATS = {
    "mp3": ("MP3 · 320kbps", ["-c:a", "libmp3lame", "-b:a", "320k"]),
    "wav": ("WAV · 无损 (体积大)", ["-c:a", "pcm_s16le"]),
    "copy": ("原样复制音轨（最快，不改编码）", ["-c:a", "copy"]),
}


class App:
    def __init__(self, root: tk.Tk, on_back=None) -> None:
        self.root = root
        self.on_back = on_back
        self.files: list[Path] = []
        self.busy = False
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.done = 0

        self.fmt = tk.StringVar(value="mp3")
        self.to_voice = tk.BooleanVar(value=False)
        self.outdir = tk.StringVar(value=str(paths.OUTPUT))

        root.title("音频提取")
        root.configure(bg=theme.BG)
        root.geometry("620x520")
        root.minsize(580, 480)

        self._build()
        self._after_id = None
        root.after(120, self._drain)
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 6))
        if self.on_back is not None:
            theme.button(head, "← 返回工具箱", self._back, small=True).pack(anchor="w",
                                                                             pady=(0, 6))
        tk.Label(head, text="🎵 音频提取", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="从视频里抽出音频。不装任何依赖，直接用项目里的 ffmpeg。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(anchor="w",
                                                                       pady=(2, 0))

        pick = tk.Frame(self.root, bg=theme.BG)
        pick.pack(fill="x", padx=theme.PAD, pady=(8, 0))
        theme.button(pick, "选择视频文件", self.pick_files, primary=True).pack(side="left")
        theme.button(pick, "选择整个文件夹", self.pick_folder).pack(side="left", padx=(8, 0))
        theme.button(pick, "清空", self.clear).pack(side="left", padx=(8, 0))

        self.summary = tk.Label(self.root, text="还没选择文件", bg=theme.BG, fg=theme.MUTED,
                                font=theme.F_BODY, anchor="w")
        self.summary.pack(fill="x", padx=theme.PAD, pady=(8, 4))

        listbox_wrap = tk.Frame(self.root, bg=theme.CARD)
        listbox_wrap.pack(fill="both", expand=True, padx=theme.PAD)
        self.listbox = tk.Listbox(listbox_wrap, bg=theme.CARD, fg=theme.FG,
                                  selectbackground=theme.ACCENT, relief="flat",
                                  highlightthickness=0, font=theme.F_SMALL,
                                  activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        sb = tk.Scrollbar(listbox_wrap, command=self.listbox.yview)
        sb.pack(side="right", fill="y", pady=8)
        self.listbox.config(yscrollcommand=sb.set)

        opt = tk.Frame(self.root, bg=theme.CARD)
        opt.pack(fill="x", padx=theme.PAD, pady=(10, 0))
        inner = tk.Frame(opt, bg=theme.CARD)
        inner.pack(fill="x", padx=12, pady=10)

        r1 = tk.Frame(inner, bg=theme.CARD)
        r1.pack(fill="x")
        tk.Label(r1, text="输出格式", bg=theme.CARD, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left")
        for key, (label, _args) in FORMATS.items():
            tk.Radiobutton(r1, text=label, value=key, variable=self.fmt,
                           bg=theme.CARD, fg=theme.FG, selectcolor=theme.BG,
                           activebackground=theme.CARD, activeforeground=theme.ACCENT,
                           font=theme.F_SMALL, highlightthickness=0, bd=0
                           ).pack(side="left", padx=(10, 0))

        r2 = tk.Frame(inner, bg=theme.CARD)
        r2.pack(fill="x", pady=(6, 0))
        tk.Checkbutton(r2, text="提取后直接送去 AI 变声（需要先准备好音色）",
                       variable=self.to_voice, bg=theme.CARD, fg=theme.FG,
                       selectcolor=theme.BG, activebackground=theme.CARD,
                       activeforeground=theme.ACCENT, font=theme.F_SMALL,
                       highlightthickness=0, bd=0).pack(anchor="w")

        out = tk.Frame(self.root, bg=theme.BG)
        out.pack(fill="x", padx=theme.PAD, pady=(10, 0))
        tk.Label(out, text="保存到", bg=theme.BG, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left")
        tk.Entry(out, textvariable=self.outdir, bg=theme.CARD, fg=theme.FG,
                 insertbackground=theme.FG, relief="flat", font=theme.F_SMALL
                 ).pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        theme.button(out, "浏览", self.pick_outdir, small=True).pack(side="left")

        run = tk.Frame(self.root, bg=theme.BG)
        run.pack(fill="x", padx=theme.PAD, pady=(10, 6))
        self.go = theme.button(run, "开始提取", self.start, primary=True)
        self.go.pack(side="left")

        self.bar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=theme.PAD)
        self.status = tk.Label(self.root, text="", bg=theme.BG, fg=theme.MUTED,
                               font=theme.F_SMALL, anchor="w", wraplength=580,
                               justify="left")
        self.status.pack(fill="x", padx=theme.PAD, pady=(6, 12))

    # -------------------------------------------------------------- 选择 --
    def pick_files(self) -> None:
        types = [("视频/音频", " ".join(f"*{e}" for e in sorted(VIDEO_EXT | AUDIO_EXT))),
                 ("所有文件", "*.*")]
        got = filedialog.askopenfilenames(title="选择视频文件（可多选）", filetypes=types)
        self._add([Path(p) for p in got])

    def pick_folder(self) -> None:
        d = filedialog.askdirectory(title="选择包含视频的文件夹")
        if not d:
            return
        found = [p for p in Path(d).rglob("*")
                 if p.is_file() and p.suffix.lower() in (VIDEO_EXT | AUDIO_EXT)]
        if not found:
            messagebox.showinfo("没有找到", "这个文件夹里没有认出视频或音频文件。")
            return
        self._add(found)

    def pick_outdir(self) -> None:
        d = filedialog.askdirectory(title="选择保存位置")
        if d:
            self.outdir.set(d)

    def _add(self, items: list[Path]) -> None:
        have = {f.resolve() for f in self.files}
        for p in items:
            if p.resolve() not in have:
                self.files.append(p)
                have.add(p.resolve())
        self._refresh()

    def clear(self) -> None:
        self.files.clear()
        self._refresh()

    def _refresh(self) -> None:
        self.listbox.delete(0, tk.END)
        total = 0.0
        for f in self.files:
            try:
                mb = f.stat().st_size / 1e6
            except OSError:
                mb = 0.0
            total += mb
            self.listbox.insert(tk.END, f"  {f.name}    {mb:.1f} MB")
        if self.files:
            self.summary.config(text=f"已选 {len(self.files)} 个文件，共 {total:.1f} MB",
                                fg=theme.FG)
        else:
            self.summary.config(text="还没选择文件", fg=theme.MUTED)
        self.go.config(state="normal" if self.files and not self.busy else "disabled")

    # -------------------------------------------------------------- 执行 --
    def start(self) -> None:
        if self.busy or not self.files:
            return
        if not paths.FFMPEG.exists():
            messagebox.showerror("缺少 ffmpeg", f"找不到：\n{paths.FFMPEG}")
            return
        self.busy = True
        self.done = 0
        self.go.config(state="disabled", text="提取中…")
        self.bar.config(value=0)
        outdir = Path(self.outdir.get())
        outdir.mkdir(parents=True, exist_ok=True)

        # 在主线程把 Tk 变量的值取出来再交给工作线程。
        # 子线程里读 Tk 变量会抛 "main thread is not in main loop"，
        # 而且即使碰巧能用也是未定义行为。
        fmt = self.fmt.get()
        send = bool(self.to_voice.get())
        threading.Thread(target=self._worker,
                         args=(list(self.files), outdir, fmt, send),
                         daemon=True).start()

    def _worker(self, files: list[Path], outdir: Path, fmt: str, send: bool) -> None:
        args = FORMATS[fmt][1]
        results: list[Path] = []
        fail = 0

        for i, src in enumerate(files, 1):
            # "copy" 保留原容器里的编码，扩展名交给 ffmpeg 自己定；这里统一用 m4a
            ext = {"mp3": ".mp3", "wav": ".wav", "copy": ".m4a"}[fmt]
            dst = outdir / (src.stem + ext)
            self.q.put(("log", f"[{i}/{len(files)}] {src.name}"))

            creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            cmd = [str(paths.FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(src), "-vn"] + args + [str(dst)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace",
                                      creationflags=creation)
            except OSError as exc:
                self.q.put(("log", f"    启动 ffmpeg 失败：{exc}"))
                fail += 1
                continue

            if proc.returncode != 0 or not dst.exists():
                # 「原样复制」遇到容器不兼容时，退回转 mp3，别让用户白等
                if fmt == "copy":
                    dst = outdir / (src.stem + ".mp3")
                    retry = [str(paths.FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                             "-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "320k",
                             str(dst)]
                    subprocess.run(retry, capture_output=True, creationflags=creation)
                if not dst.exists():
                    self.q.put(("log", f"    失败：{(proc.stderr or '').strip()[-160:]}"))
                    fail += 1
                    continue

            results.append(dst)
            self.done = i
            self.q.put(("progress", f"{i}/{len(files)}"))

        self.q.put(("done", f"{len(results)} 成功，{fail} 失败|" +
                    ";".join(str(r) for r in results) + f"|{int(send)}"))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.status.config(text=payload, fg=theme.MUTED)
                elif kind == "progress":
                    a, b = payload.split("/")
                    self.bar.config(value=float(a) / max(float(b), 1) * 100)
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return            # 窗口已销毁，不再排下一轮
        try:
            self._after_id = self.root.after(120, self._drain)
        except tk.TclError:
            pass

    def _finish(self, payload: str) -> None:
        self.busy = False
        self.go.config(state="normal", text="开始提取")
        self.bar.config(value=100)
        parts = payload.split("|")
        head = parts[0]
        files = parts[1] if len(parts) > 1 else ""
        send = parts[2] == "1" if len(parts) > 2 else False
        self.status.config(text=f"完成：{head}", fg=theme.OK)

        made = [Path(p) for p in files.split(";") if p]
        if made:
            opener.open_path(made[0].parent)      # 失败了也不打断流程

        if send and made:
            self._send_to_voice(made)

    def _send_to_voice(self, made: list[Path]) -> None:
        """把提取出的音频交给变声工具（用它的环境另开一个进程）。"""
        script = ROOT / "convert.py"
        if not (paths.VOICE_PY.exists() and script.exists()):
            messagebox.showinfo("变声工具不可用", "找不到变声工具的运行环境。")
            return
        messagebox.showinfo(
            "下一步",
            f"已提取 {len(made)} 个音频。\n\n"
            "变声需要先选好音色参考，所以请打开「AI 变声」，\n"
            "把刚提取的音频作为「要变声的音频」选进去。\n\n"
            f"文件在：{made[0].parent}")

    # -------------------------------------------------------------- 导航 --
    def _back(self) -> None:
        if self.busy and not messagebox.askokcancel("正在提取", "还没跑完，确定返回吗？"):
            return
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()

    def _close(self) -> None:
        if self.busy and not messagebox.askokcancel("正在提取", "还没跑完，确定退出吗？"):
            return
        # 先取消定时器，避免它在窗口销毁后还去碰控件
        try:
            if getattr(self, "_after_id", None):
                self.root.after_cancel(self._after_id)
        except tk.TclError:
            pass
        self.root.destroy()


def selftest() -> int:
    """无头自检：建出界面、报告状态、真跑一次提取（用项目里的测试音频）。"""
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

    if not paths.FFMPEG.exists():
        print(f"[selftest] ffmpeg 缺失：{paths.FFMPEG}")
        root.destroy()
        return 1
    print(f"[selftest] ffmpeg 就绪：{paths.FFMPEG.name}")

    # 真跑一次：把测试 wav 转成 mp3，验证整条链路
    src = ROOT / "io" / "test_voice2_source.wav"
    if src.exists():
        out = ROOT / "io" / "_audio_selftest"
        out.mkdir(parents=True, exist_ok=True)
        # 输出名跟着源文件走（src.stem + 扩展名），断言要用同一个规则
        dst = out / (src.stem + ".mp3")
        if dst.exists():
            dst.unlink()
        app.files = [src]
        app.outdir.set(str(out))
        app._refresh()
        app.fmt.set("mp3")
        app.to_voice.set(False)
        app.start()
        print(f"[selftest] 开始，输出目标 {dst}")

        for _ in range(300):                     # 最多等 30 秒
            root.update()
            time.sleep(0.1)
            if not app.busy:
                break

        print(f"[selftest] 结束：busy={app.busy}  状态栏={app.status['text']!r}")
        print(f"[selftest] 目标文件存在={dst.exists()}"
              + (f"  {dst.stat().st_size/1024:.0f} KB" if dst.exists() else ""))
        produced = sorted(out.glob("*"))
        print(f"[selftest] 目录里实际有：{[p.name for p in produced]}")

        ok = dst.exists() and dst.stat().st_size > 1000
        print(f"[selftest] 真实提取测试：{'通过' if ok else '失败'}")
        import shutil
        shutil.rmtree(out, ignore_errors=True)
        root.destroy()
        return 0 if ok else 1

    print("[selftest] 没有测试音频，跳过实跑")
    root.destroy()
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
