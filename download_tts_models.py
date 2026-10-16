"""从 ModelScope 直接下载 GPT-SoVITS 预训练模型（不走 modelscope SDK）。

为什么不用 modelscope 的 snapshot_download
------------------------------------------
它要先在 `C:\\Users\\Administrator\\.modelscope` 建缓存目录，而那个位置
在本机**不可写**（WinError 5）。设 MODELSCOPE_CACHE 也未必全绕得过去。

所以这里直接走 ModelScope 的 HTTP 文件接口，下到项目目录里：

    https://www.modelscope.cn/api/v1/models/<repo>/repo?Revision=master&FilePath=<path>

好处：不依赖任何 SDK 缓存、支持断点续传式重试、下到哪完全可控。

只下 v2final 那一套（GPT-SoVITS 推荐的默认版本），
判别器 s2D2333k.pth 推理用不到，跳过。
"""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "tools" / "tts" / "engine" / "GPT_SoVITS" / "pretrained_models"
REPO = "AI-ModelScope/GPT-SoVITS"
REVISION = "master"

FILES = [
    # roberta 在这个仓库里只有 config/tokenizer/pytorch_model 三个文件，
    # 没有 vocab.txt 和 tokenizer_config.json —— 不要照抄 HF 上的文件清单。
    "chinese-roberta-wwm-ext-large/config.json",
    "chinese-roberta-wwm-ext-large/tokenizer.json",
    "chinese-roberta-wwm-ext-large/pytorch_model.bin",
    "chinese-hubert-base/config.json",
    "chinese-hubert-base/preprocessor_config.json",
    "chinese-hubert-base/pytorch_model.bin",
    "gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
    "gsv-v2final-pretrained/s2G2333k.pth",
    # 判别器：**推理不需要**（一开始跳过了），但训练必须有它，
    # 因为 SoVITS 训练要和判别器对抗。
    "gsv-v2final-pretrained/s2D2333k.pth",
]


def url_for(rel: str) -> str:
    q = urllib.parse.urlencode({"Revision": REVISION, "FilePath": rel})
    return f"https://www.modelscope.cn/api/v1/models/{REPO}/repo?{q}"


def human(n: float) -> str:
    return f"{n/1e6:.1f} MB" if n < 1e9 else f"{n/1e9:.2f} GB"


def download(rel: str, dst: Path, retries: int = 4) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url_for(rel), headers={"User-Agent": "dsh-toolbox"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                t0 = time.time()
                with open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        fh.write(chunk)
                        done += len(chunk)
                        if total and done % (16 * 1024 * 1024) < 256 * 1024:
                            pct = done / total * 100
                            speed = done / max(time.time() - t0, 1e-6)
                            print(f"      {pct:5.1f}%  {human(done)}/{human(total)}  "
                                  f"{human(speed)}/s", flush=True)

            # 只校验「有没有内容」，不设最小体积门槛 ——
            # config.json 这类文件本来就只有几百字节，用 1KB 去卡会误判成失败。
            if tmp.stat().st_size == 0:
                raise RuntimeError("下载到 0 字节")
            if total and tmp.stat().st_size < total:
                raise RuntimeError(f"不完整：{tmp.stat().st_size}/{total} 字节")
            tmp.replace(dst)
            return True
        except (urllib.error.URLError, TimeoutError, RuntimeError, OSError) as exc:
            print(f"      第 {attempt} 次失败：{type(exc).__name__}: {exc}", flush=True)
            if attempt < retries:
                time.sleep(3 * attempt)
    return False


def main() -> int:
    print(f"仓库    : {REPO} @ {REVISION}")
    print(f"目标目录: {DEST}\n")

    ok = skipped = failed = 0
    for rel in FILES:
        dst = DEST / rel
        if dst.exists() and dst.stat().st_size > 1024:
            print(f"[跳过] {rel}  ({human(dst.stat().st_size)})")
            skipped += 1
            continue

        print(f"[下载] {rel}", flush=True)
        t0 = time.time()
        if download(rel, dst):
            print(f"[完成] {rel}  ({human(dst.stat().st_size)}, {time.time()-t0:.0f}s)")
            ok += 1
        else:
            print(f"[失败] {rel}")
            failed += 1

    print(f"\n==== 新增 {ok}，跳过 {skipped}，失败 {failed} ====")
    total = sum((DEST / r).stat().st_size for r in FILES if (DEST / r).exists())
    print(f"pretrained_models 总计 {human(total)}")
    for rel in FILES:
        p = DEST / rel
        print(f"  {'OK ' if p.exists() else '缺 '} {rel}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
