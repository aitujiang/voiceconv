"""RVC 组件冒烟测试：验证核心模型能在本机 GPU 上加载并运行。

为什么先做这个
--------------
还没有训练好的变声模型，不能跑完整的变声。但变声依赖两个核心组件：

    HuBERT    把音频变成内容特征
    RMVPE     提取音高（RVC 推荐的算法）

这两个是 GPU 上最容易出问题的部分（自定义算子、CUDA 内核）。
先把它们跑通，等模型训练好，整条链路基本就稳了。

用法
----
    python tools/rvc/smoke_test.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "tools" / "rvc" / "engine"
sys.path.insert(0, str(ENGINE))
os.chdir(ENGINE)

# RVC 的代码靠这些环境变量找文件，必须在 import 之前设好
os.environ.setdefault("rmvpe_root", str(ENGINE / "assets" / "rmvpe"))
os.environ.setdefault("weight_root", str(ENGINE / "assets" / "weights"))
os.environ.setdefault("index_root", str(ENGINE / "assets" / "indices"))
os.environ.setdefault("outside_index_root", str(ENGINE / "assets" / "indices"))


def main() -> int:
    import numpy as np
    import soundfile as sf
    import torch

    print("=" * 62)
    print(" RVC 组件冒烟测试")
    print("=" * 62)
    print(f" torch      : {torch.__version__}")
    print(f" cuda 可用  : {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f" 显卡       : {torch.cuda.get_device_name(0)}")
        print(f" sm_120     : {'sm_120' in torch.cuda.get_arch_list()}")

    ok = True

    # ---- 1. HuBERT 特征提取 ----
    print("\n[1/2] HuBERT 特征提取")
    t0 = time.time()
    try:
        # 正确用法来自 infer/vc/pipeline.py：
        #   model = load_hubert_model(device, is_half)
        #   feats = extract_hubert_features(model, audio, version)
        from infer.hubert import extract_hubert_features, load_hubert_model

        wav = ROOT / "io" / "test_voice1_reference.wav"
        if not wav.exists():
            print(f"   缺少测试音频 {wav}")
            return 2
        audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        # RVC 期望 16kHz 单声道
        import librosa
        a16 = librosa.resample(audio, orig_sr=sr, target_sr=16000)

        # 设备用字符串（load_hubert_model 内部按 "cuda"/"cpu" 判断）
        device = "cuda" if torch.cuda.is_available() else "cpu"
        model = load_hubert_model(device, torch.cuda.is_available())
        print(f"   HuBERT 模型已加载到 {device}")

        # 关键 1：必须传 torch 张量，不能传 numpy 数组。
        #   RVC 的 run_cuda_graph 会读 inputs[0].device，numpy 数组没这个属性。
        # 关键 2：精度要和模型一致。load_hubert_model(device, is_half=True) 加载的是
        #   half 权重，输入若是 float32 会报 "Input type and weight type should be the same"。
        use_half = torch.cuda.is_available()
        device = "cuda" if use_half else "cpu"
        src = torch.from_numpy(a16).unsqueeze(0).to(device)
        src = src.half() if use_half else src.float()

        feats = extract_hubert_features(model, src, "v2")
        # 返回的是 numpy 数组（不是 tensor），所以不能问它 .device
        shape = tuple(feats.shape) if hasattr(feats, "shape") else type(feats).__name__
        print(f"   输入 {len(a16)/16000:.2f}s -> 特征 {shape} "
              f"({type(feats).__name__})  ({time.time()-t0:.1f}s)")
    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"   HuBERT 失败：{type(exc).__name__}: {exc}")
        ok = False

    # ---- 2. RMVPE 音高提取 ----
    print("\n[2/2] RMVPE 音高提取")
    t0 = time.time()
    try:
        from infer.rmvpe import RMVPE

        model_path = ENGINE / "assets" / "rmvpe" / "rmvpe.pt"
        device = "cuda" if torch.cuda.is_available() else "cpu"
        rmvpe = RMVPE(str(model_path), is_half=torch.cuda.is_available(),
                      device=device)

        wav = ROOT / "io" / "test_voice1_reference.wav"
        audio, sr = sf.read(str(wav), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        import librosa
        a16 = librosa.resample(audio, orig_sr=sr, target_sr=16000)

        f0 = rmvpe.infer_from_audio(a16, thred=0.03)
        voiced = f0[f0 > 1]
        print(f"   帧数 {len(f0)}  有声帧 {len(voiced)}  "
              f"基频中位数 {float(np.median(voiced)) if len(voiced) else 0:.1f} Hz  "
              f"({time.time()-t0:.1f}s)")
        if len(voiced) == 0:
            print("   ! 没有检测到有声段（测试音频可能不是人声）")
    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        print(f"   RMVPE 失败：{type(exc).__name__}: {exc}")
        ok = False

    print("\n" + "=" * 62)
    print(" 结论：" + ("两个核心组件都可用 ✅" if ok
                      else "有组件失败，见上面的报错"))
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
