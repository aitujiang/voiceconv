"""Pre-download every Seed-VC checkpoint through the hf-mirror endpoint.

Run with any Python that has huggingface_hub installed:
    HF_ENDPOINT=https://hf-mirror.com python download_models.py
"""
import os
import sys
import time

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from huggingface_hub import hf_hub_download  # noqa: E402

from pathlib import Path as _Path           # noqa: E402

# 基于本文件位置推导，不写死绝对路径 —— 项目放哪都能跑
ROOT = _Path(__file__).resolve().parent
SRC = str(ROOT / "src" / "seed-vc-main")
CACHE = os.path.join(SRC, "checkpoints")

# (repo_id, filename) -- one entry per distinct blob Seed-VC may ask for.
TARGETS = [
    # ---- v1 voice conversion (f0_condition=False) ----
    ("Plachta/Seed-VC", "DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth"),
    ("Plachta/Seed-VC", "config_dit_mel_seed_uvit_whisper_small_wavenet.yml"),
    # ---- v1 singing voice conversion (f0_condition=True) ----
    ("Plachta/Seed-VC", "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"),
    ("Plachta/Seed-VC", "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml"),
    ("lj1995/VoiceConversionWebUI", "rmvpe.pt"),
    # ---- v2 voice / accent conversion ----
    ("Plachta/Seed-VC", "v2/cfm_small.pth"),
    ("Plachta/Seed-VC", "v2/ar_base.pth"),
    ("Plachta/ASTRAL-quantization", "bsq32/bsq32_light.pth"),
    ("Plachta/ASTRAL-quantization", "bsq2048/bsq2048_light.pth"),
    # ---- shared style encoder ----
    ("funasr/campplus", "campplus_cn_common.bin"),
    # ---- vocoders / content tokenizers used by either config ----
    ("nvidia/bigvgan_v2_22khz_80band_256x", "config.json"),
    ("nvidia/bigvgan_v2_22khz_80band_256x", "bigvgan_generator.pt"),
    ("nvidia/bigvgan_v2_44khz_128band_512x", "config.json"),
    ("nvidia/bigvgan_v2_44khz_128band_512x", "bigvgan_generator.pt"),
    ("openai/whisper-small", "config.json"),
    ("openai/whisper-small", "model.safetensors"),
    ("openai/whisper-small", "preprocessor_config.json"),
    ("openai/whisper-small", "generation_config.json"),
    ("openai/whisper-small", "tokenizer.json"),
    ("openai/whisper-small", "tokenizer_config.json"),
    ("openai/whisper-small", "vocab.json"),
    ("openai/whisper-small", "merges.txt"),
    ("openai/whisper-small", "normalizer.json"),
    ("openai/whisper-small", "added_tokens.json"),
    ("openai/whisper-small", "special_tokens_map.json"),
]

os.makedirs(CACHE, exist_ok=True)
print("HF_ENDPOINT =", os.environ["HF_ENDPOINT"], flush=True)
print("cache_dir  =", CACHE, flush=True)

failed = []
for i, (repo, fn) in enumerate(TARGETS, 1):
    t0 = time.time()
    try:
        p = hf_hub_download(repo_id=repo, filename=fn, cache_dir=CACHE)
        try:
            size = os.path.getsize(p) / 1e6
        except OSError:
            size = float("nan")
        print(f"[{i}/{len(TARGETS)}] OK   {repo}/{fn}  {size:.1f}MB  {time.time()-t0:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        failed.append((repo, fn, repr(exc)))
        print(f"[{i}/{len(TARGETS)}] FAIL {repo}/{fn}  {exc!r}", flush=True)

print("\n==== SUMMARY ====", flush=True)
print(f"ok={len(TARGETS)-len(failed)} failed={len(failed)}", flush=True)
for repo, fn, err in failed:
    print(f"  FAILED {repo}/{fn}: {err}", flush=True)
sys.exit(1 if failed else 0)
