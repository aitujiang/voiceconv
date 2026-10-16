"""跨工具数据一致性检查。

「音色库」和「变声」是**两个独立进程**，但它们必须读写同一份预设数据。
这个脚本模拟真实流程：在音色库建音色 -> 在变声界面看它能不能被套用。

如果哪天有人把数据路径改岔了，这个检查会立刻报出来。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from shared import paths, presets as preset_store      # noqa: E402
from shared.presets import Preset                      # noqa: E402


def main() -> int:
    paths.ensure_dirs()
    ok = True
    ref = ROOT / "io" / "test_voice1_reference.wav"
    if not ref.exists():
        print(f"缺少测试素材：{ref}")
        return 2

    print("=" * 66)
    print(" 跨工具数据一致性检查")
    print("=" * 66)
    print(f" 预设目录：{paths.PRESETS}")

    before = len(preset_store.load_all())
    print(f" 检查前已有 {before} 个音色")

    made: list[str] = []
    try:
        # ---- 第一步：模拟「在音色库新建一个音色」 ----
        p = preset_store.save(Preset(
            name="_一致性检查", ref_audio=str(ref), mode="v2",
            similarity=0.85, steps=40))
        made.append(p.id)
        print("\n[1] 在音色库保存音色 …")
        disk = [x for x in preset_store.load_all() if x.id == p.id]
        print(f"    落盘成功：{bool(disk)}")
        ok &= bool(disk)

        # ---- 第二步：模拟「变声界面读到它并套用」 ----
        print("\n[2] 变声界面读取同一份数据 …")
        import oneclick
        root = tk.Tk()
        app = oneclick.App(root, [])
        root.update_idletasks()

        found = [x for x in app.presets if x.id == p.id]
        print(f"    变声界面看到这个音色：{bool(found)}")
        ok &= bool(found)

        if found:
            app.mode.set("v1")
            app.steps.set(30)
            app.apply_preset(found[0])
            root.update_idletasks()
            applied = (app.target == ref and app.mode.get() == "v2"
                       and app.steps.get() == 40
                       and abs(app.similarity.get() - 0.85) < 1e-6)
            print(f"    套用后参数：mode={app.mode.get()} "
                  f"相似度={app.similarity.get()} 精细度={app.steps.get()}")
            print(f"    参考音频已填入：{app.target == ref}")
            print(f"    套用结果：{'通过' if applied else '失败'}")
            ok &= applied

        root.destroy()

        # ---- 第三步：在音色库删掉，变声界面应当看不到 ----
        print("\n[3] 在音色库删除后，变声界面不该再看到它 …")
        preset_store.delete(p.id)
        made.remove(p.id)
        importlib_check = preset_store.load_all()
        gone = not any(x.id == p.id for x in importlib_check)
        print(f"    已从磁盘移除：{gone}")
        ok &= gone
    finally:
        for pid in made:
            preset_store.delete(pid)

    after = len(preset_store.load_all())
    clean = after == before
    print(f"\n 清理：{before} -> {after}  {'通过' if clean else '失败'}")
    ok &= clean

    print("=" * 66)
    print(" 结论：" + ("两个工具共用同一份数据，一致 ✅" if ok else "不一致 ❌"))
    print("=" * 66)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
