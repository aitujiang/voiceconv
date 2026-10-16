"""RVC 变声器入口 —— 启动**原版 WebUI**。

设计原则（用户明确要求）
----------------------
**不重写 RVC 的任何功能。** 这个入口只负责：

    1. 摆好 RVC 需要的环境变量和 PATH
    2. 启动它自己的 webui.py
    3. 浏览器自动打开

所以你在 WebUI 里看到的一切 —— 训练、推理、变调、模型管理 ——
都是 RVC 原版的完整功能，网上所有教程都能直接套用。

和「AI 变声」的区别
------------------
  🎙 AI 变声      日常用：选音色 → 选音频 → 点开始（简单）
  🎛 RVC 变声器   训练和细调：原版 WebUI（功能全）

两者共用同一批模型文件（tools\\rvc\\engine\\assets\\weights）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import opener, paths, theme, vram            # noqa: E402

ENGINE = ROOT / "tools" / "rvc" / "engine"
RVC_PY = ROOT / "tools" / "rvc" / "venv" / "Scripts" / "python.exe"
DEFAULT_PORT = 7865


def rvc_env() -> dict:
    """RVC 需要的一整套环境变量。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["rmvpe_root"] = str(ENGINE / "assets" / "rmvpe")
    env["weight_root"] = str(ENGINE / "assets" / "weights")
    env["index_root"] = str(ENGINE / "assets" / "indices")
    env["outside_index_root"] = str(ENGINE / "assets" / "indices")
    env["weight_pymss_root"] = str(ENGINE / "assets" / "pymss_weights")
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    env["MPLCONFIGDIR"] = str(paths.LOGS / "mplconfig")
    # tools/my_utils.py 硬编码 cmd=["ffmpeg"]，需要它在 PATH 上
    shim = paths.LOGS / "bin-shim"
    shim.mkdir(parents=True, exist_ok=True)
    for name, exe in (("ffmpeg", paths.FFMPEG), ("ffprobe", paths.FFPROBE)):
        (shim / f"{name}.cmd").write_text(
            f'@echo off\r\n"{exe}" %*\r\n', encoding="ascii")
    env["PATH"] = os.pathsep.join([str(shim), str(paths.BIN), env.get("PATH", "")])
    return env


class App:
    def __init__(self, root: tk.Tk, on_back=None) -> None:
        self.root = root
        self.on_back = on_back
        self.proc: subprocess.Popen | None = None
        self.port = DEFAULT_PORT

        root.title("RVC 变声器")
        root.configure(bg=theme.BG)
        root.geometry("600x420")
        root.minsize(560, 380)

        self._build()

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 6))
        if self.on_back is not None:
            theme.button(head, "← 返回工具箱", self._back, small=True).pack(
                anchor="w", pady=(0, 6))
        tk.Label(head, text="🎛 RVC 变声器", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="原版 WebUI —— 训练模型、细调参数都在这里",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(
            anchor="w", pady=(2, 0))

        body = tk.Frame(self.root, bg=theme.CARD)
        body.pack(fill="x", padx=theme.PAD, pady=(12, 0))
        inner = tk.Frame(body, bg=theme.CARD)
        inner.pack(fill="x", padx=14, pady=12)

        tk.Label(inner, text="它是什么", bg=theme.CARD, fg=theme.ACCENT,
                 font=theme.F_CARD).pack(anchor="w")
        for line in (
            "RVC 官方的完整界面。训练模型、变调、特征检索、模型管理全都在里面。",
            "网上所有 RVC 教程都能直接套用（因为它就是原版）。",
            "",
            "和「🎙 AI 变声」的关系：",
            "  · AI 变声   = 日常用，选音色 → 选音频 → 点开始（简单）",
            "  · 这个界面  = 训练模型、需要细调时用",
            "两者共用同一批模型文件，训练完在 AI 变声里就能选到。",
        ):
            tk.Label(inner, text=line, bg=theme.CARD, fg=theme.MUTED,
                     font=theme.F_SMALL, justify="left", anchor="w",
                     wraplength=520).pack(anchor="w")

        info = tk.Frame(self.root, bg=theme.BG)
        info.pack(fill="x", padx=theme.PAD, pady=(12, 0))
        tk.Label(info, text="界面地址", bg=theme.BG, fg=theme.MUTED,
                 font=theme.F_SMALL).pack(side="left")
        self.url_lbl = tk.Label(info, text=f"http://127.0.0.1:{self.port}",
                                bg=theme.BG, fg=theme.FG, font=theme.F_BODY)
        self.url_lbl.pack(side="left", padx=(8, 0))

        run = tk.Frame(self.root, bg=theme.BG)
        run.pack(fill="x", padx=theme.PAD, pady=(12, 6))
        self.go = theme.button(run, "启动 RVC", self.start, primary=True)
        self.go.pack(side="left")
        self.open_btn = theme.button(run, "在浏览器打开", self.open_browser, small=True)
        self.open_btn.pack(side="left", padx=(8, 0))
        self.stop_btn = theme.button(run, "停止", self.stop, small=True)
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.stop_btn.config(state="disabled")

        self.bar = ttk.Progressbar(self.root, mode="indeterminate")
        self.bar.pack(fill="x", padx=theme.PAD, pady=(8, 0))

        # 显存条：RVC 训练最常翻车的就是爆显存，训练时盯这个数字
        vr = tk.Frame(self.root, bg=theme.BG)
        vr.pack(fill="x", padx=theme.PAD, pady=(6, 0))
        self.vram_lbl = tk.Label(vr, text="显存 …", bg=theme.BG, fg=theme.MUTED,
                                 font=theme.F_SMALL)
        self.vram_lbl.pack(side="left")
        theme.button(vr, "显存详情", self.show_vram, small=True).pack(side="right")

        self.status = tk.Label(self.root, text="点「启动 RVC」，首次加载模型要 1~2 分钟",
                               bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL,
                               anchor="w", wraplength=550, justify="left")
        self.status.pack(fill="x", padx=theme.PAD, pady=(8, 12))

        self._vram_after = None
        self._tick_vram()

    # ---------------------------------------------------------- 显存 --
    def _tick_vram(self) -> None:
        info = vram.gpu_info()
        if info is None:
            self.vram_lbl.config(text="显存  读不到 GPU 状态", fg=theme.DIM)
        else:
            pct = info.used_pct
            color = theme.OK if pct < 60 else (theme.WARN if pct < 85 else theme.ERR)
            self.vram_lbl.config(
                text=(f"显存 {info.bar(12)} {info.used_mb/1024:.1f}/"
                      f"{info.total_mb/1024:.1f} GB   {info.temp_c}°C"),
                fg=color)
        try:
            self._vram_after = self.root.after(2500, self._tick_vram)
        except tk.TclError:
            pass

    def show_vram(self) -> None:
        """显存详情。训练爆显存是最常见的问题，这里能看到谁在占。"""
        info = vram.gpu_info()
        if info is None:
            messagebox.showinfo("显存详情", "读不到 GPU 状态。")
            return
        procs = vram.gpu_processes()
        lines = [f"{info.name}",
                 f"占用 {info.used_mb} / {info.total_mb} MB（{info.used_pct:.0f}%）",
                 f"温度 {info.temp_c}°C   利用率 {info.util_pct}%",
                 "", "占用进程："]
        lines += [f"  · {p.display()}" for p in procs] if procs else ["  （没有进程在占用）"]
        lines += ["",
                  "训练时如果爆显存，在 RVC 训练页把 batch size 调到 4 再试。",
                  "（本机 8GB 显存，batch 4~6 稳）"]
        messagebox.showinfo("显存详情", "\n".join(lines))

    # -------------------------------------------------------------- 动作 --
    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            messagebox.showinfo("已在运行", "RVC 已经启动了。")
            return
        if not (RVC_PY.exists() and (ENGINE / "webui.py").exists()):
            messagebox.showerror(
                "环境不完整",
                f"找不到：\n{RVC_PY if not RVC_PY.exists() else ENGINE / 'webui.py'}")
            return

        self.go.config(state="disabled", text="启动中…")
        self.stop_btn.config(state="normal")
        self.bar.start(12)
        self.status.config(
            text="正在启动 RVC（首次要加载 HuBERT/RMVPE，约 1~2 分钟）…", fg=theme.WARN)

        try:
            creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                [str(RVC_PY), "webui.py"], cwd=str(ENGINE), env=rvc_env(),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=creation)
        except OSError as exc:
            self._fail(f"启动失败：{exc}")
            return

        threading.Thread(target=self._watch, daemon=True).start()
        self.root.after(120, self._poll_ready)

    def _watch(self) -> None:
        """读输出，进程退出时报告。"""
        assert self.proc and self.proc.stdout
        tail: list[str] = []
        for line in self.proc.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                if len(tail) > 40:
                    tail.pop(0)
        code = self.proc.wait()
        if code != 0 and not self._stopping:
            msg = "\n".join(tail[-8:]) or "（没有输出）"
            self.root.after(0, lambda: self._fail(f"RVC 异常退出（{code}）：\n{msg}"))

    _stopping = False

    def _poll_ready(self) -> None:
        """轮询端口，起来了就提示并开浏览器。"""
        if self.proc is None or self.proc.poll() is not None:
            return
        if self._port_open():
            self.bar.stop()
            self.status.config(
                text=f"RVC 已就绪：http://127.0.0.1:{self.port}\n"
                     f"浏览器应该已经打开了。没开就点「在浏览器打开」。", fg=theme.OK)
            self.go.config(state="normal", text="启动 RVC")
            self.open_browser()
            return
        self.root.after(1500, self._poll_ready)

    def _port_open(self) -> bool:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.4)
            return s.connect_ex(("127.0.0.1", self.port)) == 0

    def open_browser(self) -> None:
        url = f"http://127.0.0.1:{self.port}"
        if not self._port_open():
            messagebox.showinfo("还没就绪", "RVC 还没启动完，等状态栏显示「已就绪」再点。")
            return
        ok, why = opener.open_url(url)
        if not ok:
            messagebox.showwarning("打不开", f"{url}\n\n{why}")

    def stop(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            self.status.config(text="没有在运行的 RVC", fg=theme.MUTED)
            return
        if not messagebox.askyesno("停止 RVC", "确定关掉 RVC 服务？\n\n"
                                               "正在训练的话会中断，训练进度可能丢失。"):
            return
        self._stopping = True
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.bar.stop()
        self.stop_btn.config(state="disabled")
        self.go.config(state="normal", text="启动 RVC")
        self.status.config(text="已停止", fg=theme.MUTED)

    def _fail(self, msg: str) -> None:
        self.bar.stop()
        self.go.config(state="normal", text="启动 RVC")
        self.stop_btn.config(state="disabled")
        self.status.config(text=msg, fg=theme.ERR)
        messagebox.showerror("RVC 启动失败", msg)

    def _back(self) -> None:
        if self.proc and self.proc.poll() is None:
            if not messagebox.askokcancel(
                    "RVC 还在运行", "返回工具箱会关掉 RVC 服务。\n"
                                    "正在训练的话会中断。确定吗？"):
                return
            self.proc.terminate()
        # 取消显存刷新定时器，否则窗口销毁后它还会触发并抛 TclError
        try:
            if self._vram_after:
                self.root.after_cancel(self._vram_after)
        except tk.TclError:
            pass
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()


def selftest() -> int:
    """无头自检：不真的启动 WebUI，只检查环境。"""
    paths.ensure_dirs()
    print("=" * 62)
    print(" RVC 入口自检")
    print("=" * 62)

    checks = [
        ("RVC 环境", RVC_PY),
        ("webui.py", ENGINE / "webui.py"),
        ("HuBERT", ENGINE / "assets" / "hubert_base" / "pytorch_model.bin"),
        ("RMVPE", ENGINE / "assets" / "rmvpe" / "rmvpe.pt"),
        ("训练底模 G", ENGINE / "assets" / "pretrained_v2" / "f0G40k.pth"),
        ("训练底模 D", ENGINE / "assets" / "pretrained_v2" / "f0D40k.pth"),
    ]
    ok = True
    for label, p in checks:
        exists = p.exists()
        ok &= exists
        print(f"  {'OK ' if exists else '缺 '} {label:12} {p.name}")

    env = rvc_env()
    print(f"\n  环境变量已设：rmvpe_root / weight_root / index_root")
    print(f"  ffmpeg 在 PATH：{any('bin-shim' in p for p in env['PATH'].split(os.pathsep))}")

    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        busy = s.connect_ex(("127.0.0.1", DEFAULT_PORT)) == 0
    print(f"  端口 {DEFAULT_PORT}：{'已被占用（RVC 可能已在运行）' if busy else '空闲'}")

    print("=" * 62)
    print(" 自检" + ("通过" if ok else "失败"))
    print("=" * 62)
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
