"""下载 RVC 需要的预训练权重。

从 HF 镜像（hf-mirror.com）的官方仓库 `lj1995/VoiceConversionWebUI` 拉取 ——
这就是 RVC 作者发布权重的仓库。

三个权重的作用
--------------
  hubert_base.pt   语音内容特征提取（变声的输入）
  rmvpe.pt         音高提取（比老版 crepe 准，RVC 的推荐算法）
  f0G/f0D40k.pth   训练用的生成器/判别器底模（只在训练时需要）

不下的
------
  rmvpe.onnx（345MB）—— 那是 ONNX 版，跑 CUDA 用不上，省 345MB

用法
----
    python download_rvc_models.py              # 下推理需要的
    python download_rvc_models.py --with-train # 连训练底模一起下
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENGINE = ROOT / "tools" / "rvc" / "engine"
REPO = "lj1995/VoiceConversionWebUI"

# (仓库里的路径, 本地目标相对路径, 是否需要训练)
TARGETS: list[tuple[str, str, bool]] = [
    # RVC 2.3 的 HuBERT 走 transformers 格式（load_hubert_model 会检查目录里有
    # config.json + 权重），不再是老的单个 .pt 文件。
    ("hubert_base/config.json", "assets/hubert_base/config.json", False),
    ("hubert_base/preprocessor_config.json",
     "assets/hubert_base/preprocessor_config.json", False),
    ("hubert_base/pytorch_model.bin", "assets/hubert_base/pytorch_model.bin", False),
    ("rmvpe.pt", "assets/rmvpe/rmvpe.pt", False),
    ("pretrained_v2/f0G40k.pth", "assets/pretrained_v2/f0G40k.pth", True),
    ("pretrained_v2/f0D40k.pth", "assets/pretrained_v2/f0D40k.pth", True),
]


def url_for(rel: str) -> str:
    q = urllib.parse.urlencode({"download": "true"})
    return f"https://hf-mirror.com/{REPO}/resolve/main/{rel}?{q}"


def human(n: float) -> str:
    return f"{n/1e6:.1f} MB" if n < 1e9 else f"{n/1e9:.2f} GB"


def download(rel: str, dst: Path, retries: int = 4) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url_for(rel),
                                         headers={"User-Agent": "dsh-toolbox"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                t0 = time.time()
                last_print = 0.0
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 512)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last_print > 3:
                            last_print = now
                            pct = (done / total * 100) if total else 0
                            speed = done / max(now - t0, 1e-6)
                            print(f"      {pct:5.1f}%  {human(done)}"
                                  f"/{human(total) if total else '?'}  "
                                  f"{human(speed)}/s", flush=True)

            if tmp.stat().st_size == 0:
                raise RuntimeError("下载到 0 字节")
            if total and tmp.stat().st_size < total:
                raise RuntimeError(f"不完整：{tmp.stat().st_size}/{total}")
            tmp.replace(dst)
            return True
        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            print(f"      第 {attempt} 次失败：{type(exc).__name__}: {exc}", flush=True)
            if attempt < retries:
                time.sleep(3 * attempt)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="下载 RVC 预训练权重")
    ap.add_argument("--with-train", action="store_true",
                    help="连训练底模（f0G/f0D40k）一起下")
    ap.add_argument("--list", action="store_true", help="只检查现状")
    args = ap.parse_args()

    targets = [t for t in TARGETS if args.with_train or not t[2]]

    if args.list:
        print(f"目标目录：{ENGINE}")
        for rel, local, need_train in TARGETS:
            p = ENGINE / local
            tag = "（训练用）" if need_train else ""
            print(f"  {'OK ' if p.exists() else '缺 '} {local} {tag}")
        return 0

    print(f"仓库    : {REPO}")
    print(f"目标目录: {ENGINE}\n")

    ok = skipped = failed = 0
    for rel, local, _need in targets:
        dst = ENGINE / local
        if dst.exists() and dst.stat().st_size > 1024:
            print(f"[跳过] {local}  ({human(dst.stat().st_size)})")
            skipped += 1
            continue
        print(f"[下载] {rel}", flush=True)
        t0 = time.time()
        if download(rel, dst):
            print(f"[完成] {local}  ({human(dst.stat().st_size)}, {time.time()-t0:.0f}s)")
            ok += 1
        else:
            print(f"[失败] {local}")
            failed += 1

    print(f"\n==== 新增 {ok}，跳过 {skipped}，失败 {failed} ====")
    for rel, local, _need in TARGETS:
        p = ENGINE / local
        print(f"  {'OK ' if p.exists() else '缺 '} {local}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
