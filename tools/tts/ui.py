"""AI 朗读 —— 输入文字，用指定音色念出来。

界面只做三件事：选音色、填文字、点生成。
真正的合成交给 `synth_worker.py` 子进程（原因见那个文件的说明：
GPT-SoVITS 会改工作目录，跑在界面进程里会把文件对话框搞坏）。
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import opener, paths, presets as preset_store, theme   # noqa: E402
from shared import vram                                            # noqa: E402
from shared import training_spec                                   # noqa: E402
from shared.presets import Preset                                  # noqa: E402

AUDIO_TYPES = [
    ("音频文件", "*.wav *.mp3 *.m4a *.flac *.ogg *.aac"),
    ("所有文件", "*.*"),
]

# 音色的两个来源 —— 和「AI 变声」保持一致的说法
VOICE_SOURCES = [
    (preset_store.VOICE_REALTIME, "🔊 用音频", "一段参考音频，当场克隆（即选即用）"),
    (preset_store.VOICE_TRAINED, "🧠 用训练模型", "训练出的 GPT-SoVITS 模型（需要先训练）"),
]

# 朗读固定用它的环境（里面装了 GPT-SoVITS 依赖，且共用变声那边的 torch）
TTS_PY = ROOT / "tools" / "tts" / "venv" / "Scripts" / "python.exe"
WORKER = ROOT / "tools" / "tts" / "synth_worker.py"


class App:
    def __init__(self, root: tk.Tk, on_back=None) -> None:
        self.root = root
        self.on_back = on_back
        self.ref: Path | None = None
        self.busy = False
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.last_out: Path | None = None
        self.t0 = 0.0
        # 自检时置 True：合成完不弹资源管理器（无头测试里弹窗会抢焦点）
        self.quiet = False

        self.speed = tk.DoubleVar(value=1.0)
        self.lang = tk.StringVar(value="zh")
        self.presets: list[Preset] = preset_store.load_all()
        self.voice_source = tk.StringVar(value=preset_store.VOICE_REALTIME)

        root.title("AI 朗读")
        root.configure(bg=theme.BG)
        root.geometry("680x660")
        root.minsize(640, 620)

        self._build()
        # 记住定时器 id：窗口销毁后它可能还会触发一次，那时控件已经没了，
        # 会抛 TclError: invalid command name "..."。退出时要能取消它。
        self._after_id = root.after(120, self._drain)
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 6))
        if self.on_back is not None:
            theme.button(head, "← 返回工具箱", self._back, small=True).pack(anchor="w",
                                                                             pady=(0, 6))
        tk.Label(head, text="📖 AI 朗读", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="打字，用你选的音色念出来。音色来自一段参考音频（零样本克隆）。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(anchor="w",
                                                                       pady=(2, 0))

        # ---- ① 音色 ----
        head2 = tk.Frame(self.root, bg=theme.BG)
        head2.pack(fill="x", padx=theme.PAD, pady=(10, 4))
        tk.Label(head2, text="① 用谁的声音", bg=theme.BG, fg=theme.FG,
                 font=theme.F_H2).pack(side="left")
        tk.Button(head2, text="素材要求？", command=self.show_training_requirements,
                  bg=theme.BG, fg="#7fd1ff", activebackground=theme.BG,
                  font=theme.F_SMALL, relief="flat", bd=0,
                  cursor="hand2").pack(side="right")

        pick = tk.Frame(self.root, bg=theme.BG)
        pick.pack(fill="x", padx=theme.PAD, pady=(0, 4))
        for val, label, tip in VOICE_SOURCES:
            tk.Radiobutton(pick, text=label, value=val, variable=self.voice_source,
                           bg=theme.BG, fg=theme.FG, selectcolor=theme.CARD,
                           activebackground=theme.BG, activeforeground=theme.ACCENT,
                           font=theme.F_BODY, highlightthickness=0, bd=0,
                           command=self._on_source_change).pack(side="left")
            tk.Label(pick, text=tip, bg=theme.BG, fg=theme.DIM,
                     font=(theme.FONT, 7)).pack(side="left", padx=(2, 12))

        self.preset_box = tk.Frame(self.root, bg=theme.CARD)
        self.preset_box.pack(fill="x", padx=theme.PAD)

        row = tk.Frame(self.root, bg=theme.BG)
        row.pack(fill="x", padx=theme.PAD, pady=(6, 0))
        tk.Label(row, text="当前参考音频：", bg=theme.BG, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left")
        self.ref_lbl = tk.Label(row, text="未选择", bg=theme.BG, fg=theme.MUTED,
                                font=theme.F_SMALL, anchor="w")
        self.ref_lbl.pack(side="left", fill="x", expand=True)
        theme.button(row, "选文件", self.pick_ref, small=True).pack(side="right")

        # ---- ② 文字 ----
        tk.Label(self.root, text="② 要念什么", bg=theme.BG, fg=theme.FG,
                 font=theme.F_H2).pack(anchor="w", padx=theme.PAD, pady=(12, 4))

        wrap = tk.Frame(self.root, bg=theme.CARD)
        wrap.pack(fill="both", expand=True, padx=theme.PAD)
        self.text = tk.Text(wrap, bg=theme.CARD, fg=theme.FG, insertbackground=theme.FG,
                            relief="flat", font=(theme.FONT, 10), wrap="word",
                            highlightthickness=0, padx=10, pady=8)
        self.text.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(wrap, command=self.text.yview)
        sb.pack(side="right", fill="y")
        self.text.config(yscrollcommand=sb.set)
        self.text.insert("1.0", "你好，这是一段用我的音色朗读的测试文字。")
        self.text.bind("<KeyRelease>", lambda _e: self._update_count())

        self.count = tk.Label(self.root, text="", bg=theme.BG, fg=theme.DIM,
                              font=theme.F_SMALL, anchor="w")
        self.count.pack(fill="x", padx=theme.PAD, pady=(4, 0))

        # ---- ③ 参数 ----
        opt = tk.Frame(self.root, bg=theme.BG)
        opt.pack(fill="x", padx=theme.PAD, pady=(8, 0))
        tk.Label(opt, text="语速", bg=theme.BG, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left")
        tk.Scale(opt, from_=0.6, to=1.6, resolution=0.05, orient="horizontal",
                 variable=self.speed, bg=theme.BG, fg=theme.FG, troughcolor=theme.CARD,
                 highlightthickness=0, bd=0, length=170,
                 font=theme.F_SMALL).pack(side="left", padx=(6, 16))
        tk.Label(opt, text="语言", bg=theme.BG, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left")
        for val, label in (("zh", "中文"), ("en", "英文"), ("auto", "自动")):
            tk.Radiobutton(opt, text=label, value=val, variable=self.lang,
                           bg=theme.BG, fg=theme.FG, selectcolor=theme.CARD,
                           activebackground=theme.BG, activeforeground=theme.ACCENT,
                           font=theme.F_SMALL, highlightthickness=0,
                           bd=0).pack(side="left", padx=(6, 0))

        # ---- 按钮 ----
        run = tk.Frame(self.root, bg=theme.BG)
        run.pack(fill="x", padx=theme.PAD, pady=(12, 6))
        self.go = theme.button(run, "生成语音", self.start, primary=True)
        self.go.pack(side="left")
        self.open_btn = theme.button(run, "打开成品目录", self.open_out, small=True)
        self.open_btn.pack(side="left", padx=(8, 0))
        self.open_btn.config(state="disabled")

        self.bar = ttk.Progressbar(self.root, mode="determinate", maximum=100)
        self.bar.pack(fill="x", padx=theme.PAD)

        # 显存条 + 释放按钮：朗读模型常驻显存，想腾给别的工具时点释放
        vr = tk.Frame(self.root, bg=theme.BG)
        vr.pack(fill="x", padx=theme.PAD, pady=(6, 0))
        self.vram_lbl = tk.Label(vr, text="显存 …", bg=theme.BG, fg=theme.MUTED,
                                 font=theme.F_SMALL)
        self.vram_lbl.pack(side="left")
        theme.button(vr, "释放显存", self.release_vram, small=True).pack(side="right")

        self.status = tk.Label(self.root, text="选好音色、写好文字，点「生成语音」",
                               bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL,
                               anchor="w", wraplength=630, justify="left")
        self.status.pack(fill="x", padx=theme.PAD, pady=(6, 12))

        self._render_presets()
        self._update_count()
        self._vram_after = None
        self._tick_vram()

    def _on_source_change(self) -> None:
        # 训练模型同样需要一段参考音频（模型定音色，参考定语气），所以不清 ref
        self._render_presets()

    def _scan_trained(self) -> list[tuple[Path, Path]]:
        """扫 data\\models\\sovits 下的 .ckpt，给每个找同目录的 .pth 配对。"""
        root = paths.TRAINED_SOVITS
        found: list[tuple[Path, Path]] = []
        if root.is_dir():
            for ck in sorted(root.rglob("*.ckpt")):
                pths = sorted(ck.parent.glob("*.pth"))
                if not pths:
                    continue
                key = ck.stem.split("-")[0].split("_")[0]
                pick = pths[0]
                for p in pths:
                    if p.stem.startswith(key):
                        pick = p
                        break
                found.append((ck, pick))
        return found

    def _use_trained(self, ck: Path, pth: Path) -> None:
        self.trained_model = (ck, pth)
        self.status.config(text=f"已选模型：{ck.name} + {pth.name}", fg=theme.OK)
        self._render_presets()

    def _pick_trained(self) -> None:
        f = filedialog.askopenfilename(
            title="选 GPT 阶段权重（.ckpt）", initialdir=str(paths.TRAINED_SOVITS),
            filetypes=[("GPT-SoVITS GPT 权重", "*.ckpt"), ("所有文件", "*.*")])
        if not f:
            return
        ck = Path(f)
        pths = sorted(ck.parent.glob("*.pth"))
        if not pths:
            messagebox.showwarning(
                "缺少 SoVITS 权重",
                "同目录下没有 .pth 文件。\nGPT-SoVITS 需要 .ckpt 和 .pth 成对才能用。")
            return
        self._use_trained(ck, pths[0])

    def _render_presets(self) -> None:
        for w in self.preset_box.winfo_children():
            w.destroy()

        src = self.voice_source.get()

        # ---- 「用训练模型」这一档：扫描 data\models\sovits 下的角色模型 ----
        if src == preset_store.VOICE_TRAINED:
            box = tk.Frame(self.preset_box, bg=theme.CARD)
            box.pack(fill="x", padx=12, pady=10)

            tk.Label(box, text="🧠 用训练模型 —— 选一个角色的 .ckpt + .pth",
                     bg=theme.CARD, fg=theme.ACCENT, font=theme.F_CARD).pack(anchor="w")
            tk.Label(box,
                     text="把别人训练好的 GPT-SoVITS 模型放进 "
                          "data\\models\\sovits\\<角色名>\\ 就会出现在下面。\n"
                          "仍然需要一段 5~10 秒参考音频（模型决定音色，参考决定语气）。",
                     bg=theme.CARD, fg=theme.MUTED, font=theme.F_SMALL,
                     justify="left").pack(anchor="w", pady=(4, 8))

            models = self._scan_trained()
            if not models:
                tk.Label(box, text="（还没找到模型）", bg=theme.CARD,
                         fg=theme.DIM, font=theme.F_SMALL).pack(anchor="w")
            for ck, pth in models[:8]:
                name = f"{ck.parent.name} · {ck.stem}"
                tk.Button(box, text=f"🎭 {name}",
                          command=lambda a=ck, b=pth: self._use_trained(a, b),
                          bg="#1c3a52", fg="#7fd1ff", activebackground="#244a68",
                          font=theme.F_SMALL, relief="flat", bd=0, cursor="hand2",
                          padx=10, pady=4).pack(anchor="w", pady=(0, 4))

            theme.button(box, "浏览…（手动选 .ckpt）", self._pick_trained,
                         small=True).pack(anchor="w", pady=(8, 0))
            cur = getattr(self, "trained_model", None)
            if cur:
                tk.Label(box, text=f"当前：{Path(cur[0]).name} + {Path(cur[1]).name}",
                         bg=theme.CARD, fg=theme.OK, font=theme.F_SMALL).pack(
                    anchor="w", pady=(6, 0))
            return

        # ---- 「用音频」这一档 ----
        usable = [p for p in self.presets
                  if p.voice_type == preset_store.VOICE_REALTIME and p.is_usable()]
        if not usable:
            tk.Label(self.preset_box,
                     text="还没有可用的音色。可以点下面的「选文件」直接选一段参考音频，\n"
                          "或者去「AI 变声 → 音色库 → ＋新建音色」建一个能反复用的。",
                     bg=theme.CARD, fg=theme.MUTED, font=theme.F_SMALL,
                     wraplength=600, justify="left").pack(anchor="w", padx=12, pady=10)
            return
        grid = tk.Frame(self.preset_box, bg=theme.CARD)
        grid.pack(fill="x", padx=10, pady=8)
        for i, p in enumerate(usable[:6]):
            tk.Button(grid, text=f"{p.type_icon()} {p.name}",
                      command=lambda x=p: self.use_preset(x),
                      bg="#1c3a52", fg="#7fd1ff", activebackground="#244a68",
                      font=theme.F_SMALL, relief="flat", bd=0, cursor="hand2",
                      padx=10, pady=4).grid(row=i // 4, column=i % 4,
                                            padx=(0, 6), pady=(0, 6), sticky="w")

    def show_training_requirements(self) -> None:
        """朗读对应的训练是 GPT-SoVITS，要求和变声那边的 RVC 不一样。"""
        spec = training_spec.SOVITS
        messagebox.showinfo(f"{spec.title} · 素材要求",
                            training_spec.requirements_text(spec, self._dataset_minutes()))

    def _dataset_minutes(self) -> float | None:
        """扫一下 data\\datasets，看攒了多少分钟素材，用于「够不够」的判断。"""
        root = ROOT / "data" / "datasets"
        if not root.is_dir():
            return None
        import wave
        total = 0.0
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() == ".wav":
                try:
                    with wave.open(str(p), "rb") as fh:
                        total += fh.getnframes() / max(fh.getframerate(), 1)
                except Exception:                              # noqa: BLE001
                    pass
            elif p.suffix.lower() in (".mp3", ".m4a", ".flac", ".ogg"):
                total += p.stat().st_size / 16000              # 粗略估算
        return total / 60 if total else 0.0

    def _update_count(self) -> None:
        n = len(self.text.get("1.0", "end").strip())
        self.count.config(text=f"{n} 字   （建议一次不超过 500 字，太长会慢）")

    # -------------------------------------------------------------- 动作 --
    def use_preset(self, p: Preset) -> None:
        if not p.is_usable():
            messagebox.showwarning("音色不可用", p.problem())
            return
        target = p.ref_audio if p.voice_type == preset_store.VOICE_REALTIME \
            else p.model_path
        self.ref = Path(target)
        self._refresh_ref()
        self.status.config(text=f"已选音色「{p.name}」", fg=theme.OK)

    def pick_ref(self) -> None:
        f = filedialog.askopenfilename(
            title="选一段参考音频（建议 5~10 秒干净人声）", filetypes=AUDIO_TYPES,
            initialdir=str(ROOT / "io"))
        if not f:
            return
        self.ref = Path(f)
        self._refresh_ref()

    def _refresh_ref(self) -> None:
        if self.ref and self.ref.exists():
            self.ref_lbl.config(text=f"{self.ref.name}   ({self.ref.stat().st_size/1e6:.1f} MB)",
                                fg=theme.FG)
        else:
            self.ref_lbl.config(text="未选择", fg=theme.MUTED)

    def start(self) -> None:
        if self.busy:
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("还没写文字", "请在上面的框里输入要朗读的内容。")
            return
        if not self.ref or not self.ref.exists():
            messagebox.showinfo("还没选音色", "请先选一个参考音频，或点上面的音色卡片。")
            return
        if not (TTS_PY.exists() and WORKER.exists()):
            messagebox.showerror(
                "朗读引擎缺失",
                f"找不到：\n{TTS_PY if not TTS_PY.exists() else WORKER}\n\n"
                "AI 朗读需要 GPT-SoVITS 环境，请先完成安装。")
            return

        self.busy = True
        self.t0 = time.time()
        self.go.config(state="disabled", text="生成中…")
        self.bar.config(value=8)
        self.status.config(text="启动合成进程（首次要加载模型，约 15~60 秒）…", fg=theme.WARN)

        out = paths.OUTPUT / f"朗读_{text[:12].replace(chr(10),'')}_{int(time.time())}.wav"
        if os.environ.get("DSH_SANDBOX") == "1":
            # 自检 / 自动化运行时写沙箱，绝不往用户成品目录里塞测试文件
            out = paths.SANDBOX / out.name
        # 所有参数在主线程取好再交给子进程
        job = {
            "text": text,
            "ref": str(self.ref.resolve()),
            "out": str(out),
            "speed": float(self.speed.get()),
            "lang": self.lang.get(),
            "t2s": str(self.trained_model[0]) if getattr(self, "trained_model", None) else "",
            "vits": str(self.trained_model[1]) if getattr(self, "trained_model", None) else "",
        }
        threading.Thread(target=self._worker, args=(job,), daemon=True).start()

    def _worker(self, job: dict) -> None:
        cmd = [str(TTS_PY), str(WORKER),
               "--text", job["text"], "--ref", job["ref"], "--out", job["out"],
               "--speed", str(job["speed"]), "--lang", job["lang"]]
        if job.get("t2s") and job.get("vits"):
            cmd += ["--t2s", job["t2s"], "--vits", job["vits"]]
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["HF_ENDPOINT"] = "https://hf-mirror.com"
        env["HF_HOME"] = str(ROOT / "models" / "hfcache")
        env["MPLCONFIGDIR"] = str(ROOT / "logs" / "mplconfig")

        creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace",
                                    bufsize=1, creationflags=creation)
        except OSError as exc:
            self.q.put(("fail", f"无法启动合成进程：{exc}"))
            return

        recent: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                recent.append(line)
                if len(recent) > 40:
                    recent.pop(0)
                self.q.put(("log", line))
        code = proc.wait()
        if code == 0:
            self.q.put(("done", job["out"]))
        else:
            err = next((ln for ln in reversed(recent) if ln.startswith("[ERROR]")), "")
            self.q.put(("fail", err or " | ".join(recent[-4:]) or "（无输出）"))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    low = payload.lower()
                    if "加载完成" in payload:
                        self.bar.config(value=45)
                        self.status.config(text="模型已加载，正在合成…", fg=theme.MUTED)
                    elif "预测语义token" in low or "推理" in payload:
                        self.bar.config(value=70)
                        self.status.config(text="正在合成语音…", fg=theme.MUTED)
                    elif "合成音频" in payload:
                        self.bar.config(value=88)
                    elif payload.startswith("[ERROR]"):
                        self.status.config(text=payload, fg=theme.ERR)
                elif kind == "done":
                    self._finish(Path(payload))
                elif kind == "fail":
                    self._fail(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return          # 窗口已经销毁，停止排下一轮
        try:
            self._after_id = self.root.after(120, self._drain)
        except tk.TclError:
            pass

    def _finish(self, out: Path) -> None:
        self.busy = False
        self.go.config(state="normal", text="生成语音")
        self.bar.config(value=100)
        if out.exists():
            self.last_out = out
            self.open_btn.config(state="normal")
            import soundfile as sf
            info = sf.info(str(out))
            self.status.config(
                text=f"完成！{info.frames/info.samplerate:.2f} 秒语音，"
                     f"用时 {time.time()-self.t0:.0f} 秒\n{out}", fg=theme.OK)
            # 自检时不要弹资源管理器 —— 无头测试里它会抢焦点，
            # 实测还会连带把 Tk 的控件弄失效（自检读到 status 时抛 TclError）。
            if not self.quiet:
                opener.open_path(out, select=True)
        else:
            self.status.config(text="合成结束但没有找到输出文件", fg=theme.WARN)

    def _fail(self, msg: str) -> None:
        self.busy = False
        self.go.config(state="normal", text="生成语音")
        self.bar.config(value=0)
        self.status.config(text=msg, fg=theme.ERR)
        messagebox.showerror("朗读失败", msg)

    def open_out(self) -> None:
        if self.last_out and self.last_out.exists():
            ok, why = opener.open_path(self.last_out, select=True)
        else:
            ok, why = opener.open_path(paths.OUTPUT)
        if not ok:
            messagebox.showwarning("打不开", why)

    def _back(self) -> None:
        if self.busy and not messagebox.askokcancel("正在合成", "还没完成，确定返回吗？"):
            return
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()

    def _close(self) -> None:
        """正常关闭：先取消定时器，避免它在窗口销毁后还去碰控件。"""
        try:
            if getattr(self, "_after_id", None):
                self.root.after_cancel(self._after_id)
        except tk.TclError:
            pass
        try:
            if getattr(self, "_vram_after", None):
                self.root.after_cancel(self._vram_after)
        except tk.TclError:
            pass
        # 朗读模型常驻显存（GPT-SoVITS 约 2~3GB），退出时还回去，
        # 否则接着开变声或 RVC 容易爆显存。
        try:
            vram.release()
        except Exception:                            # noqa: BLE001
            pass
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()

    def release_vram(self) -> None:
        """手动释放显存，腾给别的工具用。"""
        _ok, msg = vram.release_all_in_process()
        self._refresh_vram()
        messagebox.showinfo("释放显存", msg)

    def _refresh_vram(self) -> None:
        if not hasattr(self, "vram_lbl"):
            return
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


def selftest() -> int:
    """无头自检：建界面 + 真跑一次合成（需要参考音频）。"""
    paths.ensure_dirs()
    root = tk.Tk()
    app = App(root, on_back=None)
    app.quiet = True                  # 自检不弹资源管理器
    # 自检产物必须进 _sandbox。以前靠外部设 DSH_SANDBOX=1，
    # 忘了设就会写进用户真正的 io\output —— 这是造成过实际损失的事故类型，
    # 所以改成自检内部自己设，不依赖调用方。
    os.environ["DSH_SANDBOX"] = "1"
    root.update_idletasks()
    root.update()

    widgets = 0
    stack = [root]
    while stack:
        w = stack.pop()
        widgets += 1
        stack.extend(w.winfo_children())
    print(f"[selftest] 界面构建成功：{widgets} 个控件")

    if not (TTS_PY.exists() and WORKER.exists()):
        print(f"[selftest] 引擎缺失：TTS_PY={TTS_PY.exists()} WORKER={WORKER.exists()}")
        root.destroy()
        return 1
    print(f"[selftest] 朗读引擎就绪")

    ref = ROOT / "io" / "test_voice1_reference.wav"
    if not ref.exists():
        print(f"[selftest] 没有测试参考音频，跳过实跑")
        root.destroy()
        return 0

    app.ref = ref
    app._refresh_ref()
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "朗读功能自检。")
    out = paths.SANDBOX / "tts_ui_selftest.wav"
    if out.exists():
        out.unlink()
    app.start()
    print("[selftest] 已启动合成，等待…")
    for _ in range(600):                      # 最多等 60 秒
        root.update()
        time.sleep(0.1)
        if not app.busy:
            break

    ok = out.exists() and out.stat().st_size > 1000 if False else (
        app.last_out is not None and app.last_out.exists())
    print(f"[selftest] 状态：{app.status['text'][:80]!r}")
    print(f"[selftest] 合成测试：{'通过' if ok else '失败'}")
    if ok:
        import soundfile as sf
        info = sf.info(str(app.last_out))
        print(f"[selftest] 产物 {info.frames/info.samplerate:.2f}s / {info.samplerate}Hz")
    # 走 _close 而不是直接 destroy：它会先取消定时器，
    # 否则销毁后定时器还会触发一次、去碰已经不存在的控件并抛 TclError。
    try:
        app._close()
    except tk.TclError:
        pass
    return 0 if ok else 1


def launch(parent=None, on_back=None) -> int:
    root = tk.Tk()
    App(root, on_back=on_back)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        os.environ["DSH_SANDBOX"] = "1"
        return selftest()
    embedded = "--embedded" in argv
    paths.ensure_dirs()
    return launch(on_back=(lambda: None) if embedded else None)


if __name__ == "__main__":
    raise SystemExit(main())
