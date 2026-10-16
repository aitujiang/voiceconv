# 搭建记录与踩坑

目标：在这台机器上实现「提取音频 1 的音色 → 应用到音频 2」的本地变声。

## 机器现状（开工前探测）

| 项目 | 结果 | 影响 |
|---|---|---|
| OS | Windows 10 19045 | — |
| CPU / RAM | i5-14600KF / 31.8 GB | 充足 |
| GPU | RTX 5060 Ti **8GB**，驱动 576.88 | Blackwell = `sm_120`，必须 CUDA 12.8+ 的 PyTorch |
| Python | 随附 3.12.14 + pip 26.2.1 | PATH 上的 `python.exe` 是应用商店占位符，不可用 |
| ffmpeg | **未安装** | 音频解码/重采样必需 |
| git | **未安装** | 改用 zip 归档下载源码 |
| 网络 | PyPI ✅ / download.pytorch.org ✅ / **huggingface.co ❌ 超时** / hf-mirror.com ✅ / modelscope ✅ | 模型权重必须走镜像 |
| D 盘剩余 | 623 GB | 够用 |

## 技术选型

选了 [Seed-VC](https://github.com/Plachtaa/Seed-VC)：零样本音色克隆，1~30 秒参考音频即可，不用训练。它的 CLI 语义正好对应需求：

- `--source` = 被转换的音频（用户的**音频 2**）
- `--target` = 提供音色的参考音频（用户的**音频 1**）

对比过的其它路线：RVC 需要为每个音色训练模型（重）；纯 DSP 的 WORLD/谱包络替换速度快但音质有明显电子味、无法保留音素细节。Seed-VC 是效果/成本比最好的。

## 踩坑记录

### 1. 沙箱把所有命令都堵死了（开工第一坑）
初始 `workspace-write` 策略下，连 `echo hello` 都返回 `0xC0000142`（STATUS_DLL_INIT_FAILED）。这不是 ACL 权限问题，而是进程在沙箱受限令牌下根本起不来。会话切到完全权限后一切正常。

### 2. `requirements.txt` 自相矛盾
文件里先写 `torch --pre --index-url .../nightly/cu126`，后面又钉 `torch==2.4.0` —— pip 会以后者为准，而 **torch 2.4 没有 `sm_120` 内核，在 5060 Ti 上只能退回 CPU 或直接报错**。所以不能直接 `pip install -r requirements.txt`。

### 3. CUDA 12.8 频道最低只有 torch 2.7.0
实测 `download.pytorch.org/whl/cu128` 对 cp312/win 只提供 2.7.0 及以上。而 2.6 起 `torch.load` 默认变成 `weights_only=True`，Seed-VC 全程用裸 `torch.load`（且检查点里含 dict/自定义对象，不只是张量），会直接抛 `UnpicklingError`。

解决：装 2.7.0 + 在 venv 的 `site-packages` 放一个 `sitecustomize.py`，把 `torch.load` 的 `weights_only` 默认值恢复为 `False`。**不改 Seed-VC 一行源码**，解释器启动时自动生效。

### 4. 可以砍掉一堆重依赖
`funasr`、`modelscope`、`gradio`、`resemblyzer`、`jiwer`、`sounddevice`、`FreeSimpleGUI` 只出现在 `eval.py` 和几个 Web UI 里，命令行推理完全用不到。只装：torch、torchaudio、numpy、scipy、librosa、soundfile、pydub、einops、munch、pyyaml、omegaconf、hydra-core、transformers、huggingface-hub、accelerate。

`descript-audio-codec` 看起来也需要（`modules/encodec.py`），但实际打开发现该文件是自带的 encodec 实现，只依赖 torch/einops —— 又一个可省的包。

### 5. `skip_ssl: true` 并不等于不用 hubert
v2 配置里 `content_extractor_narrow/wide` 写的是 `facebook/hubert-large-ll60k`。虽然 `skip_ssl: true` 跳过了模型本体，但 `AstralQuantizer.__init__` 仍然会执行
`Wav2Vec2FeatureExtractor.from_pretrained(ssl_model_name)` —— **只下配置，不下权重**（省了 ~1.2 GB），但必须能联网取到。

### 6. BigVGAN 不编译 CUDA 扩展
`use_cuda_kernel=False` 明确走纯 PyTorch 实现，不需要 nvcc / 不需要编译 `alias_free_activation` 的 CUDA 算子。

### 7. 网络抖动导致下载源反复切换
- `hf-mirror.com` 稳定，实测 440 MB 权重 10 秒下完；偶尔超时会自动 resume。
- ffmpeg：gyan.dev 直连慢但不掉线；GitHub Releases 的 `objects.githubusercontent.com` 一度 404、`github.com` 超时，不可靠。最终用 `curl -C -` 从 gyan.dev 断点续传。

### 8. 非 ASCII 文件名的处理
`convert.py` 会先把输入用 ffmpeg 解码成 `io\_stage\src_<ascii>.wav` / `ref_<ascii>.wav` 的纯 ASCII 中间文件再喂给 Seed-VC，避免中文路径在子进程里的编码问题；输出文件名仍保留原意。

## 最终环境

### 又踩到的坑（第二轮）

**9. pip 缓存里有个 2.6GB 的截断残file，导致安装反复死锁**
现象：`pip install torch` 每次都是 CPU 满载空转、网络 0.1MB/s、磁盘零写入。实测 151 秒里烧掉 148 秒 CPU。
根因：`%LOCALAPPDATA%\pip\cache\http-v2` 里有个 2625.7MB 的 `.body` 文件（缺 zip 的 EOCD 结尾标记，是被中断的半截下载）。pip 每次都去校验它，而它既清不掉（`pip cache purge` 报 `WinError 5`，句柄占用）也不会自我修复。
解法：`--no-cache-dir` 完全绕开缓存，速度立刻从 0.1MB/s 变成 **77MB/s**，3.3GB 的 torch 100 秒装完。
后遗症：这个坏缓存仍然占着 2.6GB，建议关掉所有 python 进程后手动删掉 `C:\Users\Administrator\AppData\Local\pip\cache`。

**10. 依赖解析器回溯**
`pip install torch torchaudio --index-url .../cu128` 会卡在解析阶段（同样是 CPU 满载无进展）。加 `--no-deps` 后秒过 —— CUDA 版 torch 的轮子自带所需运行时组件，本来也不需要解析依赖。

**11. 漏了 `descript-audio-codec`（我自己的疏忽）**
第一轮我只 grep 了 `modules/encodec.py` 就断定 `dac` 不需要，但真正 import 它的是 `modules/length_regulator.py` 第 7 行 `from dac.nn.quantize import VectorQuantize`。
教训：判断依赖要扫**整个**代码树的 import，不能只看名字最像的那个文件。
副作用：装它会顺手把 numpy 升到 2.5.3，必须再降回 1.26.4，否则 numba/librosa 崩。

**12. GUI 的跨线程 Tk 访问（真实 bug，已修）**
`oneclick.py` 原本在工作线程里直接读 `self.mode.get()` / `self.similarity.get()`。Tkinter 变量不是线程安全的，会抛 `RuntimeError: main thread is not in main loop`。在 `mainloop()` 运行时碰巧不出错，但属于未定义行为。
修法：在 `start()`（主线程）里把所有 Tk 变量取成普通 Python 值打包成 dict，再传给工作线程。**不在子线程碰任何 Tk 对象。**

**13. 「开始变声」按钮初始可点（真实 bug，已修）**
`_build_ui()` 末尾调用的 `_refresh_labels()` 在 `go` 按钮创建之前执行，`AttributeError` 被吞掉，按钮保持了默认的 `normal` 状态。用户未选文件就能点，点了静默无反应。
修法：把 `_refresh_labels()` 挪到 `_build_ui()` 最后、所有控件创建完之后。

### 为避免这类问题加的自检工具

- `oneclick.py --selftest`：真实构建界面 + 走动按钮状态，无需人眼盯着窗口。
- `oneclick.py --auto <源> <参考>`：无人值守跑完整转换流程，复用 GUI 的真实线程、进度泵和完成回调 —— 就是它抓出了上面第 12 条。
- `analyze_result.py`：对比基频/谱质心/频段能量，客观判断音色有没有迁移。

### 验收结果

| 项目 | 证据 |
|---|---|
| GPU | RTX 5060 Ti，2335 GFLOPS 实测，8.55GB 显存 |
| Blackwell 内核 | `torch.cuda.get_arch_list()` 含 `sm_120` |
| 权重 | 3.96GB 基础 + 1.18GB hubert-large，全部走 hf-mirror |
| **v1 模式** | ✅ 4.24s 音频，RTF 0.36（20 步）/ RTF 0.23（8 步），输出 22.05kHz |
| **v2 模式** | ✅ RTF ~0.5（30 步），输出 22.05kHz，走 ASTRAL+CFM+AR 全套 |
| **singing 模式** | ✅ 输出 **44.1kHz**，RMVPE 音高提取 + 44k BigVGAN |
| 中文名 + mp3 | ✅ 通过，文件名带 6 位哈希保证唯一 |
| 一键 GUI | ✅ 34 控件；`--selftest` 与 `--auto` 全流程均通过 |

**唯一未验证项**：音色克隆的音质。因为只有合成脉冲音频可用，不是人声。需要真实音频才能判断。

### 又修的两个问题（第三轮）

**14. 模型缓存存了两份（1.78GB 冗余）**
`inference.py` 第 5 行硬编码 `os.environ['HF_HUB_CACHE'] = './checkpoints/hf_cache'`，而 `hf_utils.py` 用 `cache_dir="./checkpoints"` —— 同一个根下两个不同路径，导致 whisper（922MB）和两个 BigVGAN（428MB + 466MB）各存两份。
修法：删掉 `hf_cache`，用 `mklink /J` 建立目录联接指向 `checkpoints`。两种路径都能命中同一份文件，不重复下载。
**验证过联接是安全的**：递归遍历 20 秒内正常结束（65 个文件），推理实测 15 秒完成且无任何重新下载。

**15. pydub 找不到 ffmpeg**
启动时报 `Couldn't find ffmpeg or avconv`。因为我们的 ffmpeg 在 `.\bin` 下，不在系统 PATH 上，而 v2 的 `vc_wrapper.py` 用 pydub 读音频。
修法：`convert.py` 给子进程注入 `FFMPEG_BINARY` / `FFPROBE_BINARY`，并把 `bin` 前置到 `PATH`。同时把 `HF_HOME` / `TRANSFORMERS_CACHE` / `MPLCONFIGDIR` 都指到可写目录，消除那两个权限警告。

### 又修的三个问题（第四轮：用户实测才发现）

**16. GUI 唱歌模式传了不存在的参数（用户实际踩到的 bug）**
现象：用户选「唱歌」模式点开始，弹「转换失败（错误码 2）」。
根因：`oneclick.py` 的 `_worker` 里写的是
```python
elif job["mode"] == "singing":
    cmd += ["--inference-cfg-rate", "0.7"]   # convert.py 没这个参数
```
`--inference-cfg-rate` 是 `convert.py` **内部**转发给 Seed-VC 时才用的名字；它对外暴露的参数叫 `--cfg-rate`。argparse 遇到未知参数直接退出，**退出码正是 2**。
为什么没提前发现：命令行测试没传这个参数；GUI 自动测试默认走 v1 分支，**永远进不了 singing 那条 elif**。测试覆盖不全。
修法：改成 `--steps`；并给 `--auto` 加了模式参数，现在四种模式都会过 GUI 路径回归。
教训：**参数名拼错这类错误，只有把每个分支都真跑一遍才能发现。**

**17. 报错框只显示猜测、藏起真实错误**
原来 `_fail()` 只弹「常见原因：显存不足…」这类猜测文案，把 `convert.py` 真实打印的 `[ERROR]` 行吞掉了 —— 所以用户看到的是完全无关的「显存」提示，而真实原因是参数错误。
修法：`_worker` 收集子进程最后 40 行输出，失败时优先提取 `[ERROR]` 行原样显示；提示语改成按错误内容匹配，不再瞎猜。

**18. .bat 入口在中文系统上完全打不开（用户实际踩到的 bug）**
现象：双击 `一键变声.bat` 毫无反应。
根因：系统代码页是 **936（GBK）**，而我把 bat 存成了 **UTF-8 无 BOM**。cmd 用 GBK 去解析 UTF-8 字节，脚本被拆得面目全非：
```
'exist' 不是内部或外部命令      ← "if not exist" 被切坏
'完成安装?' 不是内部或外部命令   ← echo 的中文变成乱码命令
```
修法：两个 bat 重写成**纯 ASCII**（0 个非 ASCII 字节），中文提示全部由 Python 界面负责。`make_launchers.py` 可重新生成。
教训：**bat 文件里不要出现中文**，或者必须存成 GBK。我测过 `oneclick.py` 却没测过 bat 这个入口。

### 新增能力：整曲换声（三段流程）

用户要求「歌曲也要，不能只有人声」。这暴露了一个原理性问题：

> **Seed-VC 只在人声上训练，看不懂伴奏。** 把带伴奏的歌喂进去，伴奏要么被丢掉要么变杂音，输出永远只有一条干声。

所以新增 `song.py`，实现完整三段流程：

| 步骤 | 工具 | 87 秒歌曲实测耗时 |
|---|---|---|
| ① 分离人声/伴奏 | Demucs 4.1.0（htdemucs） | 6.6 秒 |
| ② 只给人声变声 | Seed-VC（singing 模式） | 37 秒 |
| ③ 混回伴奏 + 峰值归一化 | numpy + soundfile | <1 秒 |
| | **总计** | **46 秒** |

分离质量客观验证（各频段能量占比）：

| | 低频 <150Hz | 人声带 150Hz-3k |
|---|---|---|
| 人声轨 | **0.0%** | **90.5%** |
| 伴奏轨 | **90.9%** | 7.2% |
| 整曲成品 | 50.3% | 45.6% |

混音后成品 87.2 秒 / 44.1kHz / 立体声 / 14.67MB，人声 RMS 0.169 与伴奏 RMS 0.154 响度平衡，峰值 0.97 无削波。

### Demucs 踩的小坑

- `--ext wav` 参数在 4.1.0 已移除（默认就是 wav），传了会报 `unrecognized arguments`
- 权重格式变了：不再是 `.th`，而是 `adefossez/HTDemucs` 仓库里的 `.yaml` + `.safetensors`，通过 `hf_hub_download` 拉取，所以 `HF_ENDPOINT` 镜像变量同样有效
- 必须设 `TORCH_HOME`，否则 demucs 会去用户目录缓存（那个目录在本机不可写）

### 最终回归测试（四模式全过 GUI 路径）

用 15 秒短样本跑 `oneclick.py --auto <源> <参考> <模式>`：

| 模式 | 结果 |
|---|---|
| v1 | ✅ 1.26 MB |
| v2 | ✅ 0.63 MB |
| singing | ✅ 2.52 MB |
| song（整曲） | ✅ 2.53 MB |

## 环境总占用

约 11.8 GB（6.3GB venv 含 3.3GB torch、5.1GB Seed-VC 权重、0.2GB ffmpeg、80MB demucs 权重），D 盘剩余 611GB。

## 交付清单

| 文件 | 用途 |
|---|---|
| `一键变声.bat` | 双击启动图形界面（推荐给用户） |
| `一键变声-拖拽两个文件到这里.bat` | 拖拽两个音频文件 |
| `oneclick.py` | GUI 本体；`--selftest` 自检、`--auto` 无人值守（四模式） |
| `convert.py` | 单段音频变声，三种模式 |
| **`song.py`** | **整曲换声：Demucs 分离 + 变声 + 混回伴奏** |
| `analyze_result.py` | 客观对比音色是否迁移 |
| `download_models.py` | 权重下载器（走 hf-mirror） |
| `make_test_audio.py` | 合成测试音频 |
| `make_launchers.py` | 重新生成两个 .bat（纯 ASCII） |
| `README.md` | 使用说明 |
| `setup_log.md` | 本文档 |
