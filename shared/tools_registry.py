"""工具箱的工具注册表 —— 单一事实来源。

每个工具的「叫什么、归哪一组、做没做、需要什么环境」都在这里声明，
launcher 和各工具界面都从这里读，不再各自硬编码。

想加一个新工具？在 TOOLS 里加一条，再把 ready 改成 True。
界面会自动点亮它，不用动 launcher 的布局代码。
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

from shared import paths


@dataclass
class Tool:
    key: str                      # 稳定标识，用于命令行参数和配置
    title: str                    # 卡片上的名字
    subtitle: str                 # 一句话说明
    icon: str                     # 一个 emoji
    group: str                    # 分组：素材获取 / 声音处理 / 后续规划
    module: str | None = None     # 提供 launch(parent, on_back) 的模块
    entry: str | None = None      # 入口文件；默认是 tools\<key>\ui.py
    ready: bool = False           # 开发完了吗（没完就是灰显占位）
    notes: str = ""               # 灰显时显示的原因
    requires: list[Path] = field(default_factory=list)   # 缺了就不能跑
    # 外部程序：不是我们写的工具，而是用户电脑上已有的软件（ComfyUI 之类）。
    # 有值时点卡片不去跑 Python，而是直接把这个文件交给系统打开
    # （等同双击），并且用它所在目录当工作目录 —— 很多启动器依赖相对路径。
    external: Path | None = None

    @property
    def is_external(self) -> bool:
        return self.external is not None

    def entry_file(self) -> Path:
        """入口 .py 的实际路径。外部程序没有 .py，返回它自己的路径。"""
        if self.external is not None:
            return self.external
        if self.entry:
            return paths.ROOT / self.entry
        return paths.ROOT / "tools" / self.key / "ui.py"

    def missing_requirements(self) -> list[Path]:
        return [p for p in self.requires if not p.exists()]

    def status(self) -> tuple[str, str]:
        """返回 (状态标签, 颜色)。颜色用十六进制，界面直接吃。"""
        if not self.ready:
            return "未开发", GREY
        if self.missing_requirements():
            return "环境缺失", RED
        if self.is_external:
            # 外部软件：文件在就是可用，不检查 Python 模块
            return ("可用", GREEN) if self.external.exists() else ("未安装", GREY)
        if not self.entry_file().exists():
            return "模块缺失", RED
        return "可用", GREEN


# 状态颜色（和界面主题一致）
GREEN = "#38d39f"
GREY = "#5f7385"
RED = "#ff7b72"
AMBER = "#ffb454"


TOOLS: list[Tool] = [
    # ---------------- 素材获取 ----------------
    Tool(
        key="download",
        title="视频下载",
        subtitle="B站视频下载，支持高清与 cookie",
        icon="📥",
        group="素材获取",
        module="tools.download.ui",
        ready=False,
        notes="规划中：先做 B站，YouTube/抖音/小红书后续再加",
    ),
    Tool(
        key="audio",
        title="音频提取",
        subtitle="从视频里抽出音频，支持批量",
        icon="🎵",
        group="素材获取",
        module="tools.audio.ui",
        ready=True,
        requires=[paths.FFMPEG],
    ),
    Tool(
        key="separate",
        title="人声分离",
        subtitle="拆成人声 + 伴奏，训练素材去 BGM",
        icon="🎤",
        group="素材获取",
        module="tools.audio.separate_ui",
        entry="tools/audio/separate_ui.py",
        ready=True,
        requires=[paths.VOICE_PY],
    ),
    Tool(
        key="loudness",
        title="响度标准化",
        subtitle="音量统一到平台标准，避免被压",
        icon="📊",
        group="素材获取",
        module="tools.audio.loudness_ui",
        entry="tools/audio/loudness_ui.py",
        ready=True,
        requires=[paths.FFMPEG],
    ),
    Tool(
        key="subtitle",
        title="字幕识别",
        subtitle="音视频转文字，也能给素材自动标注",
        icon="💬",
        group="素材获取",
        module="tools.audio.subtitle_ui",
        entry="tools/audio/subtitle_ui.py",
        ready=True,
        requires=[paths.VOICE_PY],
    ),
    Tool(
        key="sites",
        title="素材网站",
        subtitle="常用素材站快捷入口，可自己增删",
        icon="🔗",
        group="素材获取",
        module="tools.sites.ui",
        entry="tools/sites/ui.py",
        ready=True,
    ),

    # ---------------- 声音处理 ----------------
    Tool(
        key="voice",
        title="AI 变声",
        subtitle="换音色 · 换主唱 · 批量处理",
        icon="🎙",
        group="声音处理",
        module="tools.voice.ui",
        ready=True,
        requires=[paths.VOICE_PY, paths.FFMPEG, paths.SEEDVC_ROOT],
    ),
    Tool(
        key="voices",
        title="音色库",
        subtitle="音色预设在里面：素材 + 参数，一键调用",
        icon="📚",
        group="声音处理",
        module="tools.voice.voices_ui",
        entry="tools/voice/voices_ui.py",
        ready=True,
    ),
    Tool(
        key="presets",
        title="任务预设",
        subtitle="连处理类型和文件夹一起存，一键跑完",
        icon="⚙",
        group="声音处理",
        module="tools.voice.presets_ui",
        entry="tools/voice/presets_ui.py",
        ready=False,
        notes="这两种是分开的：\n"
              "· 音色预设（已经有了）—— 存「用谁的声音 + 参数」，"
              "在「AI 变声」和「音色库」里用\n"
              "· 任务预设（就是这个）—— 把「处理类型 + 输入文件夹 + 输出文件夹」"
              "也一起存下来，点一下跑完整个流程，不用每次重选文件夹",
    ),
    Tool(
        key="rvc",
        title="RVC 变声器",
        subtitle="原版 WebUI：训练模型 / 细调参数",
        icon="🎛",
        group="声音处理",
        module="tools.rvc.ui",
        entry="tools/rvc/ui.py",
        ready=True,
        requires=[
            paths.ROOT / "tools" / "rvc" / "venv" / "Scripts" / "python.exe",
            paths.ROOT / "tools" / "rvc" / "engine" / "webui.py",
        ],
    ),
    Tool(
        key="tts",
        title="AI 朗读",
        subtitle="打字 → 用角色声音念出来",
        icon="📖",
        group="声音处理",
        module="tools.tts.ui",
        entry="tools/tts/ui.py",
        ready=True,
        requires=[
            paths.ROOT / "tools" / "tts" / "venv" / "Scripts" / "python.exe",
            paths.ROOT / "tools" / "tts" / "engine" / "GPT_SoVITS" / "pretrained_models"
            / "gsv-v2final-pretrained" / "s2G2333k.pth",
        ],
    ),

    # ---------------- 外部软件（用户电脑上已有的程序） ----------------
    # 这些不是我们写的，点了只是把它自己的启动器打开（等同双击）。
    # 想加新的：复制一条，把 external 换成那个 .exe / .bat 的路径。
    Tool(key="comfyui", title="ComfyUI", subtitle="AI 绘图 · 绘世启动器",
         icon="🎨", group="外部软件",
         external=Path(r"D:\confluui\ComfyUI-aki-v3.2\绘世启动器.exe"),
         ready=True,
         notes="你电脑上已有的 ComfyUI，点了打开它的启动器"),

    # ---------------- 后续规划（占位） ----------------
    Tool(key="edit", title="视频剪辑", subtitle="裁剪 / 拼接", icon="✂",
         group="后续规划", ready=False, notes="未开发"),
    Tool(key="subtitle", title="字幕", subtitle="提取 / 生成 / 翻译", icon="💬",
         group="后续规划", ready=False, notes="未开发"),
    Tool(key="image", title="图片处理", subtitle="缩放 / 格式转换", icon="🖼",
         group="后续规划", ready=False, notes="未开发"),
    Tool(key="record", title="录屏", subtitle="屏幕录制", icon="🎬",
         group="后续规划", ready=False, notes="未开发"),
    Tool(key="convert", title="格式转换", subtitle="视频 / 音频转码", icon="🔄",
         group="后续规划", ready=False, notes="未开发"),
]

GROUPS = ["素材获取", "声音处理", "外部软件", "后续规划"]


def by_key(key: str) -> Tool | None:
    return next((t for t in TOOLS if t.key == key), None)


def launchable() -> list[Tool]:
    """能真正启动的工具（已开发 + 环境齐备）。"""
    return [t for t in TOOLS if t.ready and not t.missing_requirements()]


def module_available(module: str | None) -> bool:
    """模块文件在不在 —— 用于在界面上一眼看出「话说了但没做」。"""
    if not module:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False
