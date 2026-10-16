"""语音合成的独立工作进程。

为什么要单独开一个进程
----------------------
`tools/tts/core.py` 里的 TTSEngine 会把**进程的工作目录永久切到 GPT-SoVITS 仓库根**
（它的相对路径写法要求如此）。如果直接在 tkinter 界面进程里跑，界面的文件对话框、
相对路径保存全都会受影响。

所以拆成：界面进程负责收集输入 → 启动本脚本做合成 → 读回结果。
副作用是合成期间界面完全不卡（模型加载十几秒也不影响）。

用法
----
    python synth_worker.py --text "要念的内容" --ref "参考音频.wav" \
        --out "输出.wav" [--prompt "参考音频里说了什么"] [--speed 1.0]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> int:
    ap = argparse.ArgumentParser(description="GPT-SoVITS 合成工作进程")
    ap.add_argument("--text", required=True, help="要合成的文字")
    ap.add_argument("--ref", required=True, help="参考音频（音色来源）")
    ap.add_argument("--out", required=True, help="输出 wav 路径")
    ap.add_argument("--prompt", default="", help="参考音频里说的内容（可留空）")
    ap.add_argument("--lang", default="zh", help="语言：zh / en / auto")
    ap.add_argument("--speed", type=float, default=1.0, help="语速")
    ap.add_argument("--top-k", type=int, default=15)
    ap.add_argument("--top-p", type=float, default=1.0)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    # 路径一律先转绝对 —— 后面工作目录会被引擎改掉，相对路径就失效了
    ref = Path(args.ref).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if not ref.exists():
        print(f"[ERROR] 参考音频不存在：{ref}")
        return 2

    t0 = time.time()
    try:
        from core import TTSEngine

        engine = TTSEngine(device="cuda")
        engine.synthesize(
            text=args.text,
            ref_audio=ref,
            out_path=out,
            prompt_text=args.prompt,
            lang=args.lang,
            speed=args.speed,
            top_k=args.top_k,
            top_p=args.top_p,
            temperature=args.temperature,
            seed=args.seed,
        )
    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"[ERROR] 合成失败：{type(exc).__name__}: {exc}")
        return 1

    elapsed = time.time() - t0
    if not out.exists():
        print("[ERROR] 合成结束但没有产出文件")
        return 1

    import soundfile as sf
    info = sf.info(str(out))
    result = {
        "ok": True,
        "out": str(out),
        "seconds": info.frames / info.samplerate,
        "sample_rate": info.samplerate,
        "elapsed": elapsed,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"[DONE] {result['out']}  "
              f"{result['seconds']:.2f}s/{result['sample_rate']}Hz  {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
