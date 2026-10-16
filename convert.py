"""One-click timbre-transfer / voice-conversion driver for Seed-VC.

The user story this solves:
    audio 1 = the voice whose TIMBRE we want to borrow  -> --target (reference)
    audio 2 = the performance we want to keep           -> --source
    output  = audio 2's words/timing, delivered in audio 1's voice

Seed-VC's own CLI already names things that way (--source is the audio being
converted, --target is the voice reference), so this script mainly adds:
  * automatic decoding of any input format to 16-bit WAV via bundled ffmpeg
  * voice-activity trimming so a long reference does not waste its 25s budget
  * a single `--mode` switch across the three released model families
  * UTF-8 / non-ASCII file name safety by staging inputs as ASCII names
  * a readable report of what was produced

Examples
--------
    python convert.py --source "D:\\audio\\speech2.mp3" --target "D:\\audio\\voice1.wav"
    python convert.py --source b.mp3 --target a.wav --mode v2 --steps 40
    python convert.py --source song.wav --target singer.wav --mode singing
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEEDVC = ROOT / "src" / "seed-vc-main"
FFMPEG = ROOT / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "bin" / "ffprobe.exe"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
STAGE = ROOT / "io" / "_stage"
# 自动化运行（DSH_SANDBOX=1）时产物落到沙箱，绝不碰用户真正的成品目录
OUTDIR = ROOT / "io" / ("_sandbox" if os.environ.get("DSH_SANDBOX") == "1" else "output")

MODES = {
    # name -> (script, extra args)
    "v1": ("inference.py", []),
    "v2": ("inference_v2.py", []),
    "singing": ("inference.py", ["--f0-condition", "true", "--auto-f0-adjust", "true"]),
}

TARGET_SR = {"v1": 22050, "v2": 22050, "singing": 44100}
MAX_REF_SECONDS = 25.0  # Seed-VC only reads the first 25s of the reference


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def die(msg: str) -> "None":
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(2)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def ascii_slug(text: str, fallback: str) -> str:
    """Reduce an arbitrary file stem to a safe, *unique* ASCII token.

    Chinese/Japanese/Korean names transliterate to nothing, so we append a short
    digest of the original stem. Without this, every CJK-named input would
    collapse to the same staged filename and the output name would lose all
    trace of which file it came from.
    """
    norm = unicodedata.normalize("NFKD", text)
    norm = norm.encode("ascii", "ignore").decode("ascii")
    norm = re.sub(r"[^A-Za-z0-9._-]+", "_", norm).strip("._-")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    if not norm:
        return f"{fallback}_{digest}"
    return f"{norm[:40]}_{digest}"


def probe(path: Path) -> dict:
    if not FFPROBE.exists():
        return {}
    r = run([str(FFPROBE), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)])
    if r.returncode != 0:
        return {}
    try:
        meta = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}
    audio = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "codec": audio.get("codec_name"),
        "sample_rate": audio.get("sample_rate"),
        "channels": audio.get("channels"),
        "duration": float(meta.get("format", {}).get("duration") or 0.0),
    }


def decode(src: Path, dst: Path, sr: int, *, mono: bool = True,
           trim_silence: bool = False, max_seconds: float | None = None) -> None:
    """Decode/convert src into a 16-bit PCM WAV at dst."""
    if not FFMPEG.exists():
        die(f"ffmpeg not found at {FFMPEG}. Run setup first.")
    filters = []
    if trim_silence:
        # Trim leading/trailing near-silence; harmless on already-tight clips.
        filters.append(
            "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-45dB:"
            "stop_periods=-1:stop_duration=0.20:stop_threshold=-45dB"
        )
    if mono:
        filters.append("aformat=channel_layouts=mono")
    if max_seconds:
        filters.append(f"atrim=0:{max_seconds},asetpts=N/SR/TB")

    cmd = [str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += ["-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    r = run(cmd)
    if r.returncode != 0 or not dst.exists():
        die(f"ffmpeg failed decoding {src.name}:\n{r.stderr.strip()[-1200:]}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Apply the timbre of one audio to the performance of another (Seed-VC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--source", required=True,
                    help="audio to convert (keeps its words/timing) = your audio 2")
    ap.add_argument("--target", required=True,
                    help="reference audio supplying the timbre = your audio 1")
    ap.add_argument("--mode", choices=sorted(MODES), default="v1",
                    help="v1=speech (default), v2=speech w/ stronger accent+trait removal, "
                         "singing=singing voice conversion at 44.1kHz")
    ap.add_argument("--out", default=None, help="output wav path (default: io/output/...)")
    ap.add_argument("--steps", type=int, default=None, help="diffusion steps (default 30)")
    ap.add_argument("--cfg-rate", type=float, default=None,
                    help="inference cfg rate, 0-1 (default 0.7; higher = follow reference more)")
    ap.add_argument("--similarity-cfg-rate", type=float, default=None,
                    help="v2 only: how strongly to imitate the reference timbre (default 0.7)")
    ap.add_argument("--length-adjust", type=float, default=None,
                    help=">1 slower/longer, <1 faster/shorter (default 1.0)")
    ap.add_argument("--semi-tone-shift", type=int, default=None,
                    help="shift pitch by N semitones (singing mode mainly)")
    ap.add_argument("--fp16", default=None, help="true/false, halves VRAM (default true)")
    ap.add_argument("--keep-stage", action="store_true", help="keep intermediate wav files")
    args = ap.parse_args()

    src = Path(args.source).expanduser().resolve()
    ref = Path(args.target).expanduser().resolve()
    for label, p in (("--source", src), ("--target", ref)):
        if not p.exists():
            die(f"{label} not found: {p}")

    if not SEEDVC.exists():
        die(f"Seed-VC source missing at {SEEDVC}")
    py = VENV_PY if VENV_PY.exists() else Path(sys.executable)

    sr = TARGET_SR[args.mode]
    STAGE.mkdir(parents=True, exist_ok=True)
    OUTDIR.mkdir(parents=True, exist_ok=True)

    src_stem = ascii_slug(src.stem, "source")
    ref_stem = ascii_slug(ref.stem, "reference")
    stg_src = STAGE / f"src_{src_stem}.wav"
    stg_ref = STAGE / f"ref_{ref_stem}.wav"

    print("=" * 68)
    print(" Seed-VC timbre transfer")
    print("=" * 68)
    for label, p in (("audio 2 (source, keeps words)", src), ("audio 1 (reference timbre)", ref)):
        info = probe(p)
        if info:
            print(f" {label}:\n   {p.name}\n   {info['codec']} / {info['sample_rate']}Hz / "
                  f"{info['channels']}ch / {info['duration']:.2f}s")
        else:
            print(f" {label}: {p.name}")
    print(f" mode={args.mode}  working_sr={sr}Hz  device=auto")

    # ---- 1. stage inputs as clean ASCII WAVs --------------------------------
    print("\n[1/3] decoding inputs ...")
    decode(src, stg_src, sr)
    decode(ref, stg_ref, sr, trim_silence=True, max_seconds=MAX_REF_SECONDS)
    ref_info = probe(stg_ref)
    if ref_info.get("duration", 0) < 1.0:
        print(f"  ! reference is only {ref_info.get('duration', 0):.2f}s after trimming; "
              f"1-30s of clean speech gives the best timbre match.")
    print(f"  staged: {stg_src.name} ({probe(stg_src).get('duration', 0):.2f}s) + "
          f"{stg_ref.name} ({ref_info.get('duration', 0):.2f}s)")

    # ---- 2. build and run the Seed-VC command ------------------------------
    script, extra = MODES[args.mode]
    run_out = STAGE / "run_output"
    cmd = [str(py), script,
           "--source", str(stg_src),
           "--target", str(stg_ref),
           "--output", str(run_out)]
    if args.mode == "v2":
        cmd += ["--intelligibility-cfg-rate", str(args.cfg_rate if args.cfg_rate is not None else 0.7),
                "--similarity-cfg-rate", str(args.similarity_cfg_rate if args.similarity_cfg_rate is not None else 0.7)]
    else:
        cmd += ["--diffusion-steps", str(args.steps if args.steps is not None else 30),
                "--inference-cfg-rate", str(args.cfg_rate if args.cfg_rate is not None else 0.7),
                "--fp16", str(args.fp16 if args.fp16 is not None else "true")]
    if args.length_adjust is not None:
        cmd += ["--length-adjust", str(args.length_adjust)]
    if args.semi_tone_shift and args.mode == "singing":
        cmd += ["--semi-tone-shift", str(args.semi_tone_shift)]
    cmd += extra

    env = os.environ.copy()
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # huggingface.co is blocked here
    env.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    # pydub (used by the v2 wrapper) locates ffmpeg via PATH / FFMPEG_BINARY.
    # Our ffmpeg lives in .\bin and is not on the system PATH, so without this
    # it warns "Couldn't find ffmpeg or avconv" and v2 audio loading can fail.
    env["FFMPEG_BINARY"] = str(FFMPEG)
    env["FFPROBE_BINARY"] = str(FFPROBE)
    env["PATH"] = str(ROOT / "bin") + os.pathsep + env.get("PATH", "")

    # Keep every HF download inside the Seed-VC checkpoint tree instead of
    # C:\Users\...\.cache (which is not writable here). inference.py itself
    # forces HF_HUB_CACHE to ./checkpoints/hf_cache relative to its CWD, so we
    # do not override that -- we only move the shared caches.
    env.setdefault("HF_HOME", str(SEEDVC / "checkpoints"))
    env.setdefault("TRANSFORMERS_CACHE", str(SEEDVC / "checkpoints"))
    env.setdefault("HF_HUB_CACHE", str(SEEDVC / "checkpoints"))
    env.setdefault("MPLCONFIGDIR", str(ROOT / "logs" / "mplconfig"))
    (ROOT / "logs" / "mplconfig").mkdir(parents=True, exist_ok=True)

    print(f"\n[2/3] running {script} on {'GPU' if True else ''} (first run loads/下载 weights) ...")
    print("  $ " + " ".join(cmd))
    t0 = time.time()
    proc = subprocess.Popen(cmd, cwd=str(SEEDVC), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        if len(tail) > 60:
            tail.pop(0)
        if line:
            print("  | " + line, flush=True)
    code = proc.wait()
    elapsed = time.time() - t0
    if code != 0:
        print("\n".join(tail[-40:]), file=sys.stderr)
        die(f"Seed-VC exited with code {code}")

    # ---- 3. collect the produced wav ---------------------------------------
    print(f"\n[3/3] collecting output ({elapsed:.1f}s wall) ...")
    produced = sorted(run_out.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    if not produced:
        die(f"no wav produced under {run_out}")
    newest = produced[-1]
    suffix = f"{args.mode}_{src_stem}_timbre-of_{ref_stem}.wav"
    final = Path(args.out).expanduser().resolve() if args.out else OUTDIR / suffix
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(newest, final)

    info = probe(final)
    print("\n" + "=" * 68)
    print(f" DONE  ->  {final}")
    if info:
        print(f" {info['codec']} / {info['sample_rate']}Hz / {info['duration']:.2f}s / "
              f"{final.stat().st_size/1e6:.2f}MB")
    print("=" * 68)

    if not args.keep_stage:
        shutil.rmtree(run_out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
