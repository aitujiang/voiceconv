"""GPT-SoVITS 训练流水线调度器。

把七个环节串起来，每个环节都调 GPT-SoVITS 自带的脚本（不重写它的训练逻辑）：

    0. 数据集准备      prepare_dataset.py        切片 + Whisper 自动标注
    1. 文本特征        1-get-text.py              音素 + BERT 特征
    2. 语音特征        2-get-hubert-wav32k.py     HuBERT 特征
    3. 语义 token      3-get-semantic.py          语义 token
    4a. 训练 GPT       s1_train.py               学语气走向
    4b. 训练 SoVITS    s2_train.py               学音色
    5. 导出            把权重整理到 data\\models\\sovits\\

为什么用子进程
--------------
这些脚本靠**环境变量**传参（inp_text / opt_dir / exp_name ...），
而且各自会改 sys.path 和工作目录。用子进程跑最干净，互不干扰。

用法
----
    python train_model.py --character 梦前辈                  # 全流程
    python train_model.py --character 梦前辈 --stage features  # 跑到特征提取为止
    python train_model.py --character 梦前辈 --dry-run         # 只打印命令不执行
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from shared import paths, vram        # noqa: E402

ENGINE = ROOT / "tools" / "tts" / "engine"
GPT_SOVITS = ENGINE / "GPT_SoVITS"
PREPARED = paths.VOICE_DATASETS / "_prepared"
EXP_ROOT = ENGINE / "logs"
TTS_PY = ROOT / "tools" / "tts" / "venv" / "Scripts" / "python.exe"

PRETRAINED = GPT_SOVITS / "pretrained_models"
V2 = PRETRAINED / "gsv-v2final-pretrained"
S1_CKPT = V2 / "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
S2_G = V2 / "s2G2333k.pth"
S2_D = V2 / "s2D2333k.pth"
BERT_DIR = PRETRAINED / "chinese-roberta-wwm-ext-large"
HUBERT_DIR = PRETRAINED / "chinese-hubert-base"


def die(msg: str) -> None:
    print(f"\n[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def say(stage: str, msg: str) -> None:
    print(f"\n{'=' * 66}\n [{stage}] {msg}\n{'=' * 66}", flush=True)


# --------------------------------------------------------------------------- #
# 环境准备
# --------------------------------------------------------------------------- #
def _ffmpeg_shim_dir() -> Path:
    """生成只含 ffmpeg/ffprobe 转发的 shim 目录。

    GPT-SoVITS 的 tools/my_utils.py 把 ffmpeg 写死成 cmd=["ffmpeg", ...]，
    要求它在 PATH 上；而本工具箱的 ffmpeg 在 bin 下、不在 PATH。
    用 shim 顶一下，不用改它的源码。
    """
    shim = paths.LOGS / "bin-shim"
    shim.mkdir(parents=True, exist_ok=True)
    for name, exe in (("ffmpeg", paths.FFMPEG), ("ffprobe", paths.FFPROBE)):
        (shim / f"{name}.cmd").write_text(
            f'@echo off\r\n"{exe}" %*\r\n', encoding="ascii")
    return shim


def base_env() -> dict:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HF_ENDPOINT"] = "https://hf-mirror.com"
    env["HF_HOME"] = str(paths.MODEL_STORE / "hfcache")
    env["MPLCONFIGDIR"] = str(paths.LOGS / "mplconfig")
    env["CUDA_VISIBLE_DEVICES"] = "0"
    env["_CUDA_VISIBLE_DEVICES"] = "0"

    # 这些脚本里写的是 from text.cleaner import ...，而 text 在 GPT_SoVITS 下。
    # 用绝对路径调脚本时 Python 只把脚本所在目录放进 sys.path，
    # 所以必须显式把仓库根和 GPT_SoVITS 塞进 PYTHONPATH。
    extra = [str(ENGINE), str(GPT_SOVITS)]
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(extra + ([old] if old else []))

    shim = _ffmpeg_shim_dir()
    env["PATH"] = os.pathsep.join([str(shim), str(paths.BIN), env.get("PATH", "")])
    return env


def run(cmd: list[str], env: dict, *, cwd: Path = ENGINE, title: str = "",
        tail: int = 30, dry: bool = False) -> int:
    """跑一个子进程并回显尾部输出；dry=True 时只打印将执行的命令。"""
    if dry:
        print("   [演练] " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        print(f"   [演练] cwd = {cwd}")
        for k in ("inp_text", "inp_wav_dir", "opt_dir", "exp_name",
                  "bert_pretrained_dir", "cnhubert_base_dir",
                  "pretrained_s2G", "s2config_path"):
            if k in env:
                print(f"   [演练] {k} = {env[k]}")
        return 0

    creation = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace", bufsize=1, creationflags=creation)
    assert proc.stdout is not None
    buf: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        buf.append(line)
        if len(buf) > 400:
            del buf[:200]
        low = line.lower()
        if any(k in low for k in ("error", "traceback", "epoch", "it/s", "%|",
                                  "完成", "失败", "saving", "warning: skip")):
            print("   " + line[:150], flush=True)
    code = proc.wait()
    if code != 0:
        print(f"\n   --- {title} 最后的输出 ---")
        for ln in buf[-tail:]:
            print("   " + ln[:200])
    return code


def check_prereqs() -> None:
    need = (TTS_PY, GPT_SOVITS, S1_CKPT, S2_G, S2_D, BERT_DIR, HUBERT_DIR)
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        die("训练需要的文件不齐：\n  " + "\n  ".join(missing)
            + "\n\n先运行 download_tts_models.py")


# --------------------------------------------------------------------------- #
# 第 0 步：数据集准备
# --------------------------------------------------------------------------- #
def stage_prepare(character: str, asr: str, force: bool, dry: bool = False):
    say("0/5", "数据集准备：切片 + Whisper 自动标注")
    py = paths.VOICE_PY if paths.VOICE_PY.exists() else Path(sys.executable)
    cmd = [str(py), str(ROOT / "prepare_dataset.py"),
           "--character", character, "--asr", asr]
    if force:
        cmd.append("--force")

    env = base_env()
    # prepare_dataset.py 在变声环境跑（whisper 权重已缓存在那儿）
    env["HF_HOME"] = str(paths.SEEDVC_CHECKPOINTS)

    if run(cmd, env, cwd=ROOT, title="数据集准备", dry=dry) != 0:
        die("数据集准备失败")

    list_path = PREPARED / f"{character}.list"
    wav_dir = PREPARED / character
    if not dry:
        if not list_path.exists() or not wav_dir.is_dir():
            die(f"数据集准备没产出预期文件：\n  {list_path}\n  {wav_dir}")
        lines = [ln for ln in list_path.read_text(encoding="utf-8").strip().split("\n") if ln]
        print(f"\n   标注条数：{len(lines)}")
    return list_path, wav_dir


# --------------------------------------------------------------------------- #
# 第 1~3 步：特征提取
# --------------------------------------------------------------------------- #
def _features_env(list_path: Path, wav_dir: Path, character: str, exp_dir: Path) -> dict:
    env = base_env()
    env.update({
        "inp_text": str(list_path),
        "inp_wav_dir": str(wav_dir),
        "exp_name": character,
        "opt_dir": str(exp_dir),
        "i_part": "0",
        "all_parts": "1",
        "is_half": "True",
    })
    return env


def _merge_parts(exp_dir: Path, stem: str, ext: str, sep: str = "\n") -> None:
    """把分片输出合并成最终文件（官方 webui 也是这么做的）。"""
    final = exp_dir / f"{stem}{ext}"
    parts = sorted(exp_dir.glob(f"{stem}-*{ext}"))
    if not parts:
        return
    chunks = []
    for p in parts:
        txt = p.read_text(encoding="utf-8").strip("\n")
        if txt:
            chunks.append(txt)
        p.unlink()
    final.write_text(sep.join(chunks) + "\n", encoding="utf-8")
    print(f"   合并 -> {final.name}（{len(chunks)} 片，{final.stat().st_size} 字节）")


def stage_features(character: str, list_path: Path, wav_dir: Path,
                   exp_dir: Path, dry: bool = False) -> None:
    say("1~3/5", "提取文本 / 语音 / 语义特征")
    if not dry:
        exp_dir.mkdir(parents=True, exist_ok=True)

    # 1. 文本音素 + BERT
    env = _features_env(list_path, wav_dir, character, exp_dir)
    env["bert_pretrained_dir"] = str(BERT_DIR)
    if run([str(TTS_PY), "-s", "GPT_SoVITS/prepare_datasets/1-get-text.py"],
           env, title="文本特征", dry=dry) != 0:
        die("文本特征提取失败")
    if not dry:
        _merge_parts(exp_dir, "2-name2text", ".txt")

    # 2. HuBERT 语音特征
    env = _features_env(list_path, wav_dir, character, exp_dir)
    env["cnhubert_base_dir"] = str(HUBERT_DIR)
    if run([str(TTS_PY), "-s", "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py"],
           env, title="HuBERT 特征", dry=dry) != 0:
        die("HuBERT 特征提取失败")

    # 3. 语义 token
    env = _features_env(list_path, wav_dir, character, exp_dir)
    env["pretrained_s2G"] = str(S2_G)
    env["s2config_path"] = "GPT_SoVITS/configs/s2.json"
    if run([str(TTS_PY), "-s", "GPT_SoVITS/prepare_datasets/3-get-semantic.py"],
           env, title="语义 token", dry=dry) != 0:
        die("语义 token 提取失败")
    if not dry:
        _merge_parts(exp_dir, "6-name2semantic", ".tsv", sep="\n")


# --------------------------------------------------------------------------- #
# 第 4a 步：训练 GPT
# --------------------------------------------------------------------------- #
def stage_train_gpt(character: str, exp_dir: Path, epochs: int,
                    batch_size: int, dry: bool = False) -> None:
    say("4/5a", f"训练 GPT 部分（{epochs} 轮）")

    import yaml

    cfg = yaml.safe_load((GPT_SOVITS / "configs" / "s1longer-v2.yaml")
                         .read_text(encoding="utf-8"))
    cfg["train"]["epochs"] = epochs
    cfg["train"]["batch_size"] = batch_size
    cfg["train"]["save_every_n_epoch"] = max(1, epochs // 2)
    cfg["train"]["if_save_every_weights"] = True
    cfg["train"]["half_weights_save_dir"] = "GPT_weights_v2"
    cfg["output_dir"] = str(exp_dir / "logs_s1_v2")
    cfg["pretrained_s1"] = str(S1_CKPT)

    cfg_path = exp_dir / "s1_train.yaml"
    if not dry:
        exp_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    cmd = [str(TTS_PY), "GPT_SoVITS/s1_train.py",
           "--config_file", str(cfg_path),
           "--train_semantic_path", str(exp_dir / "6-name2semantic.tsv"),
           "--train_phoneme_path", str(exp_dir / "2-name2text.txt"),
           "--output_dir", str(exp_dir / "logs_s1_v2")]
    if run(cmd, base_env(), title="GPT 训练", dry=dry) != 0:
        die("GPT 训练失败")


# --------------------------------------------------------------------------- #
# 第 4b 步：训练 SoVITS
# --------------------------------------------------------------------------- #
def stage_train_sovits(character: str, exp_dir: Path, epochs: int,
                       batch_size: int, dry: bool = False) -> None:
    say("4/5b", f"训练 SoVITS 部分（{epochs} 轮）")

    tpl = json.loads((GPT_SOVITS / "configs" / "s2.json").read_text(encoding="utf-8"))
    tpl["train"].update({
        "batch_size": batch_size,
        "epochs": epochs,
        "text_low_lr_rate": 0.4,
        "pretrained_s2G": str(S2_G),
        "pretrained_s2D": str(S2_D),
        "if_save_latest": True,
        "if_save_every_weights": True,
        "save_every_epoch": max(1, epochs // 2),
        "gpu_numbers": "0",
        "grad_ckpt": False,
        "fp16_run": True,
    })
    tpl["model"]["version"] = "v2"
    tpl["data"]["exp_dir"] = tpl["s2_ckpt_dir"] = str(exp_dir)
    tpl["save_weight_dir"] = "SoVITS_weights_v2"
    tpl["name"] = character
    tpl["version"] = "v2"

    cfg_path = exp_dir / "s2_train.json"
    if not dry:
        exp_dir.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(tpl, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # s2_train.py 的 get_hparams 接受 -c/--config（默认 ./configs/s2.json）
    cmd = [str(TTS_PY), "-s", "GPT_SoVITS/s2_train.py", "--config", str(cfg_path)]
    if run(cmd, base_env(), title="SoVITS 训练", dry=dry) != 0:
        die("SoVITS 训练失败")


# --------------------------------------------------------------------------- #
# 第 5 步：导出
# --------------------------------------------------------------------------- #
def stage_export(character: str, exp_dir: Path) -> dict:
    say("5/5", "导出模型")
    dest = paths.TRAINED_SOVITS / character
    dest.mkdir(parents=True, exist_ok=True)

    def newest(cands: list[Path]) -> Path | None:
        cands = [c for c in cands if c.is_file()]
        return max(cands, key=lambda p: p.stat().st_mtime) if cands else None

    gpt = newest(list(exp_dir.rglob("*.ckpt"))
                 + list((ENGINE / "GPT_weights_v2").glob("*.ckpt")))
    sovits = newest(list(exp_dir.rglob("*.pth"))
                    + list((ENGINE / "SoVITS_weights_v2").glob("*.pth")))

    result = {"character": character, "gpt": "", "sovits": "", "kind": "sovits"}
    for key, src in (("gpt", gpt), ("sovits", sovits)):
        if src:
            target = dest / src.name
            shutil.copy2(src, target)
            result[key] = str(target)
            print(f"   {key:7} -> {target.name}  ({target.stat().st_size / 1e6:.0f} MB)")
        else:
            print(f"   {key:7} -> 没找到")

    (dest / "info.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="GPT-SoVITS 训练流水线")
    ap.add_argument("--character", required=True, help="角色名（data\\datasets 下的文件夹）")
    ap.add_argument("--stage", default="all",
                    choices=["all", "prepare", "features", "gpt", "sovits", "export"],
                    help="跑到这一步为止（前面的会自动补上）")
    ap.add_argument("--asr", default="small", help="Whisper 规格")
    ap.add_argument("--epochs-gpt", type=int, default=15)
    ap.add_argument("--epochs-sovits", type=int, default=8)
    ap.add_argument("--batch-gpt", type=int, default=8)
    ap.add_argument("--batch-sovits", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="重做已完成的步骤")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将执行的命令和环境变量，不真正运行")
    args = ap.parse_args()

    paths.ensure_dirs()
    check_prereqs()

    exp_dir = EXP_ROOT / args.character
    t0 = time.time()

    print("=" * 66)
    print(f" GPT-SoVITS 训练：{args.character}")
    print("=" * 66)
    print(f" 实验目录：{exp_dir}")
    print(f" 训练轮数：GPT {args.epochs_gpt} 轮 / SoVITS {args.epochs_sovits} 轮")

    # ---- 显存检查：批大小填大了会在训练中途爆显存，白等半小时 ----
    # 这里只警告 + 给建议值，不硬改用户的选择（改了会让人困惑）。
    info = vram.gpu_info()
    if info is None:
        print(" 显存    ：读不到 GPU 状态")
    else:
        print(f" 显存    ：{info.used_mb}/{info.total_mb} MB "
              f"(空闲 {info.free_mb} MB)")
        tight = False
        for label, key, val in (("GPT", "batch_size_gpt", args.batch_gpt),
                                ("SoVITS", "batch_size", args.batch_sovits)):
            suggest = vram.suggest_safe(key, info.total_mb)
            if suggest and val > suggest:
                print(f" ⚠ {label} 批大小 {val} 偏大 —— "
                      f"按 {info.total_mb/1024:.1f}GB 显存建议 ≤ {suggest}，"
                      f"否则可能爆显存")
                tight = True
        if info.used_pct > 80:
            print(f" ⚠ 显存已占用 {info.used_pct:.0f}% —— "
                  f"建议先关掉其他工具再训练")
            tight = True
        if tight and not args.dry_run:
            print("\n 提示：训练中途爆显存的话，用 --batch-gpt 4 --batch-sovits 2 重跑")
            print("      （已完成的步骤会跳过，不用从头来）")

    # --stage X = 跑到 X 为止，前面缺的自动补
    order = ["prepare", "features", "gpt", "sovits", "export"]
    upto = len(order) if args.stage == "all" else order.index(args.stage) + 1
    todo = order[:upto]

    done: set[str] = set()
    marker = exp_dir / ".stages.json"
    if marker.exists() and not args.force:
        try:
            done = set(json.loads(marker.read_text(encoding="utf-8")))
        except Exception:                        # noqa: BLE001
            done = set()

    def mark(name: str) -> None:
        done.add(name)
        if not args.dry_run:
            exp_dir.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(sorted(done)), encoding="utf-8")

    print(f" 本次执行：{' -> '.join(todo)}")
    if done:
        print(f" 已完成（跳过）：{', '.join(sorted(done))}")

    list_path = PREPARED / f"{args.character}.list"
    wav_dir = PREPARED / args.character

    if "prepare" in todo and ("prepare" not in done or args.force):
        list_path, wav_dir = stage_prepare(args.character, args.asr, args.force,
                                           dry=args.dry_run)
        mark("prepare")
    elif not args.dry_run and not list_path.exists():
        die(f"找不到标注文件：{list_path}\n先跑 --stage prepare")

    if "features" in todo and ("features" not in done or args.force):
        stage_features(args.character, list_path, wav_dir, exp_dir, dry=args.dry_run)
        mark("features")

    if "gpt" in todo and ("gpt" not in done or args.force):
        stage_train_gpt(args.character, exp_dir, args.epochs_gpt,
                        args.batch_gpt, dry=args.dry_run)
        mark("gpt")

    if "sovits" in todo and ("sovits" not in done or args.force):
        stage_train_sovits(args.character, exp_dir, args.epochs_sovits,
                           args.batch_sovits, dry=args.dry_run)
        mark("sovits")

    result = {}
    if "export" in todo:
        if args.dry_run:
            print("\n   [演练] 跳过导出")
        else:
            result = stage_export(args.character, exp_dir)
            mark("export")

    print("\n" + "=" * 66)
    print(f" 完成，总耗时 {(time.time() - t0) / 60:.1f} 分钟")
    if result.get("gpt") or result.get("sovits"):
        print(f" 产出目录：{paths.TRAINED_SOVITS / args.character}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
