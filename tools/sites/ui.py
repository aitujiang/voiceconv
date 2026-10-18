"""素材网站导航 —— 常用素材站的快捷入口。

同样刻意做小：**不装任何依赖**，数据就是一个 JSON 文件。

* 左侧按分类列出网站，点一下用默认浏览器打开
* 可以自己增删（"+" 添加、"−" 删除选中）
* 数据存在 data/sites.local.json（首次保存时生成），可以直接编辑
"""
from __future__ import annotations

import json
import os
import sys
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, simpledialog

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared import paths, theme                       # noqa: E402
from shared import opener                             # noqa: E402

# 首次运行时的默认列表。你可以随便改，改完会存进 sites.json。
DEFAULT_SITES: dict[str, list[list[str]]] = {
    "视频素材": [
        ["B站", "https://www.bilibili.com"],
        ["YouTube", "https://www.youtube.com"],
        ["抖音", "https://www.douyin.com"],
        ["小红书", "https://www.xiaohongshu.com"],
    ],
    "音效素材": [
        ["爱给网", "https://www.aigei.com"],
        ["Freesound", "https://freesound.org"],
        ["淘声网", "https://www.ear0.com"],
    ],
    "字体素材": [
        ["字体天下", "https://www.fonts.net.cn"],
        ["100font", "https://www.100font.com"],
    ],
    "配音 / 语音": [
        ["GPT-SoVITS", "https://github.com/RVC-Boss/GPT-SoVITS"],
        ["Seed-VC", "https://github.com/Plachtaa/Seed-VC"],
        ["ModelScope（模型下载）", "https://www.modelscope.cn"],
    ],
}


def load_sites() -> dict[str, list[list[str]]]:
    """读网站列表。用户改过的 sites.local.json 优先，其次出厂默认 sites.json。"""
    for p in (paths.SITES_LOCAL_JSON, paths.SITES_JSON):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
        except Exception:                              # noqa: BLE001
            continue
    save_sites(DEFAULT_SITES)
    return json.loads(json.dumps(DEFAULT_SITES))


def save_sites(data: dict[str, list[list[str]]]) -> None:
    """改动一律写 sites.local.json —— 升级覆盖代码时这个文件不会被动。"""
    paths.SITES_LOCAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    paths.SITES_LOCAL_JSON.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class App:
    def __init__(self, root: tk.Tk, on_back=None) -> None:
        self.root = root
        self.on_back = on_back
        self.sites = load_sites()

        root.title("素材网站")
        root.configure(bg=theme.BG)
        root.geometry("600x520")
        root.minsize(560, 460)

        self._build()

    # ------------------------------------------------------------------ UI --
    def _build(self) -> None:
        head = tk.Frame(self.root, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(14, 6))
        if self.on_back is not None:
            theme.button(head, "← 返回工具箱", self._back, small=True).pack(anchor="w",
                                                                             pady=(0, 6))
        tk.Label(head, text="🔗 素材网站", bg=theme.BG, fg=theme.FG,
                 font=theme.F_TITLE).pack(anchor="w")
        tk.Label(head, text="点一下用浏览器打开。列表可以自己增删。",
                 bg=theme.BG, fg=theme.MUTED, font=theme.F_SMALL).pack(anchor="w",
                                                                       pady=(2, 0))

        self.body = tk.Frame(self.root, bg=theme.BG)
        self.body.pack(fill="both", expand=True, padx=theme.PAD, pady=(10, 0))

        bar = tk.Frame(self.root, bg=theme.BG)
        bar.pack(fill="x", padx=theme.PAD, pady=(10, 0))
        theme.button(bar, "＋ 添加网站", self.add_site, primary=True).pack(side="left")
        theme.button(bar, "－ 删除选中", self.del_site).pack(side="left", padx=(8, 0))
        theme.button(bar, "打开配置文件", self.open_json, small=True).pack(side="right")

        tk.Label(self.root, text=f"数据文件：{paths.SITES_JSON}",
                 bg=theme.BG, fg=theme.DIM, font=theme.F_SMALL,
                 anchor="w").pack(fill="x", padx=theme.PAD, pady=(8, 12))

        self.selected: tuple[str, str] | None = None
        self._render()

    def _render(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()

        for group, items in self.sites.items():
            box = tk.Frame(self.body, bg=theme.CARD)
            box.pack(fill="x", pady=(0, 8))
            tk.Label(box, text=group, bg=theme.CARD, fg=theme.ACCENT,
                     font=theme.F_CARD, anchor="w").pack(fill="x", padx=12, pady=(8, 4))

            grid = tk.Frame(box, bg=theme.CARD)
            grid.pack(fill="x", padx=12, pady=(0, 10))
            for i, (name, url) in enumerate(items):
                btn = tk.Button(grid, text=name, bg="#22303d", fg=theme.FG,
                                activebackground=theme.ACCENT, activeforeground="#04121f",
                                font=theme.F_SMALL, relief="flat", bd=0, cursor="hand2",
                                padx=10, pady=5,
                                command=lambda n=name, u=url, g=group: self.open_site(n, u, g))
                btn.grid(row=i // 4, column=i % 4, padx=(0, 6), pady=(0, 6), sticky="w")
                btn.bind("<Button-3>", lambda e, n=name, u=url, g=group:
                         self._context(e, n, u, g))

    # -------------------------------------------------------------- 动作 --
    def open_site(self, name: str, url: str, group: str) -> None:
        self.selected = (group, name)
        ok, why = opener.open_url(url)
        if not ok:
            messagebox.showerror("打不开", f"{name}\n{url}\n\n{why}")

    def _context(self, event, name: str, url: str, group: str) -> None:
        """右键：修改地址 / 删除。省得为了改一个链接去编辑 JSON。"""
        self.selected = (group, name)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"打开 {name}", command=lambda: webbrowser.open(url))
        menu.add_command(label="修改名字或地址…",
                         command=lambda: self.edit_site(group, name))
        menu.add_separator()
        menu.add_command(label="删除", command=self.del_site)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def add_site(self) -> None:
        group = simpledialog.askstring(
            "添加网站", "放在哪个分类？\n（填已有的分类名，或写一个新分类）",
            initialvalue=next(iter(self.sites), "其他"), parent=self.root)
        if not group:
            return
        name = simpledialog.askstring("添加网站", "网站名字：", parent=self.root)
        if not name:
            return
        url = simpledialog.askstring("添加网站", "网址（http:// 或 https:// 开头）：",
                                     parent=self.root)
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self.sites.setdefault(group, []).append([name, url])
        save_sites(self.sites)
        self._render()

    def edit_site(self, group: str, name: str) -> None:
        items = self.sites.get(group, [])
        for item in items:
            if item[0] == name:
                new_name = simpledialog.askstring("修改", "名字：", initialvalue=item[0],
                                                  parent=self.root)
                if new_name is None:
                    return
                new_url = simpledialog.askstring("修改", "网址：", initialvalue=item[1],
                                                 parent=self.root)
                if new_url is None:
                    return
                item[0], item[1] = new_name, new_url
                save_sites(self.sites)
                self._render()
                return

    def del_site(self) -> None:
        if not self.selected:
            messagebox.showinfo("先选一个", "请先左键点一下要删的网站（或右键点它）。")
            return
        group, name = self.selected
        if not messagebox.askyesno("删除", f"确定从「{group}」里删掉「{name}」？"):
            return
        self.sites[group] = [i for i in self.sites.get(group, []) if i[0] != name]
        if not self.sites[group]:
            del self.sites[group]
        save_sites(self.sites)
        self.selected = None
        self._render()

    def open_json(self) -> None:
        save_sites(self.sites)
        ok, why = opener.open_path(paths.SITES_JSON)
        if not ok:
            messagebox.showinfo("配置文件位置", why)

    def _back(self) -> None:
        if callable(self.on_back):
            self.on_back()
        self.root.destroy()


def selftest() -> int:
    """无头自检：建界面、验证网址数据合法、不真的开浏览器。"""
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

    total = sum(len(v) for v in app.sites.values())
    print(f"[selftest] 网站数据：{len(app.sites)} 个分类，共 {total} 个网址")

    bad = [(g, n, u) for g, items in app.sites.items() for n, u in items
           if not u.startswith(("http://", "https://"))]
    if bad:
        print(f"[selftest] 有 {len(bad)} 个网址格式不对：{bad[:3]}")
    else:
        print("[selftest] 所有网址格式合法")

    # 验证增删能落盘（用一份假数据，不动用户的真实列表）
    import json
    backup = json.dumps(app.sites, ensure_ascii=False)
    app.sites.setdefault("_selftest", []).append(["_测试", "https://example.com"])
    save_sites(app.sites)
    reloaded = json.loads(paths.SITES_JSON.read_text(encoding="utf-8"))
    write_ok = "_selftest" in reloaded
    # 还原
    app.sites = json.loads(backup)
    save_sites(app.sites)
    print(f"[selftest] 增删能落盘：{'通过' if write_ok else '失败'}")

    root.destroy()
    return 0 if (not bad and write_ok) else 1


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
