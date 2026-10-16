"""训练素材要求 —— 全工具箱唯一的说明来源。

界面上的提示、弹窗里的说明、文档里的表格，都从这里取，
避免出现「界面上说 5 分钟、文档里说 10 分钟」这种自相矛盾。

写这份东西的依据
----------------
两种训练的输入形式不同，所以要求也不同：

* **GPT-SoVITS**（产出给「AI 朗读」用）
  它要提取**音素级的语义特征**，所以素材必须是**干净人声**：
  有伴奏、有音效、有别人说话，特征就被污染，训出来会「像但不是本人」。

* **RVC**（产出给「AI 变声」用）
  它学的是**梅尔频谱 + 音高**，对背景噪声容忍度高一些。
  但干净素材依然明显更好。

用户手里的游戏配音往往**带 BGM 和音效**，所以 GPT-SoVITS 那条路
基本都要先过一遍 Demucs（本工具箱已装）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrainSpec:
    """一种训练路线的素材要求。"""

    key: str                      # "sovits" / "rvc"
    title: str                    # 显示名
    icon: str
    serves: str                   # 训练出来能用在哪个工具
    min_minutes: float            # 最低可用时长
    good_minutes: float           # 效果好的时长
    best_minutes: float           # 效果最好的时长
    clean_required: bool          # 是否必须有干净人声
    env_ready: bool               # 运行环境是否已就绪
    env_note: str                 # 环境说明
    time_note: str                # 训练耗时
    outputs: list[str] = field(default_factory=list)      # 产出文件
    steps: list[str] = field(default_factory=list)        # 操作步骤
    cautions: list[str] = field(default_factory=list)     # 注意事项

    def length_verdict(self, minutes: float) -> tuple[str, str]:
        """给定时长，返回 (结论, 颜色语义)。颜色语义: ok/good/best/low。"""
        if minutes < 1:
            return "远远不够，先别训", "low"
        if minutes < self.min_minutes:
            return "偏少，训出来可能不如零样本", "low"
        if minutes < self.good_minutes:
            return "够用，能训出可用模型", "ok"
        if minutes < self.best_minutes:
            return "很好，效果会明显提升", "good"
        return "充足，效果接近本人", "best"


SOVITS = TrainSpec(
    key="sovits",
    title="GPT-SoVITS 训练",
    icon="📖",
    serves="AI 朗读（打字 → 用这个音色念出来）",
    min_minutes=5.0,
    good_minutes=10.0,
    best_minutes=20.0,
    clean_required=True,
    env_ready=True,
    env_note="环境已装好（GPT-SoVITS 就在 tools\\tts\\engine），不需要额外安装",
    time_note="约 20~40 分钟（8GB 显存够用）",
    outputs=["GPT 权重（.ckpt）", "SoVITS 权重（.pth）"],
    steps=[
        "把同一个角色的几十条配音放进 data\\voices\\<角色名>\\",
        "（如果素材带 BGM/音效）先用「音频提取 + Demucs」弄成干净人声",
        "程序自动切片、转文本、提特征",
        "训练 GPT 部分（学语气走向）",
        "训练 SoVITS 部分（学音色）",
        "导出两个权重文件，自动登记进音色库",
    ],
    cautions=[
        "素材必须是干净人声：有 BGM、有音效、有别人插话都会让音色被「平均」掉",
        "多条短句比一条长音频更好（切分更准）",
        "同一角色的素材不要混进别的角色，混了就学成四不像",
        "素材质量比数量重要：10 分钟干净人声 > 30 分钟嘈杂人声",
    ],
)

RVC = TrainSpec(
    key="rvc",
    title="RVC 训练",
    icon="🎙",
    serves="AI 变声（把你的录音换成这个音色）",
    min_minutes=5.0,
    good_minutes=10.0,
    best_minutes=20.0,
    clean_required=False,
    env_ready=False,
    env_note="需要另装一套 RVC 环境（约 +6GB，且要解决新显卡兼容），目前未安装",
    time_note="约 10~30 分钟",
    outputs=["模型文件（.pth）", "特征检索文件（.index）"],
    steps=[
        "把同一个角色的配音放进 data\\voices\\<角色名>\\",
        "程序自动切片、去静音、提取音高与特征",
        "训练模型（可以调轮数，轮数越多越像但越容易过拟合）",
        "生成特征检索文件（提升相似度）",
        "导出两个文件，自动登记进音色库",
    ],
    cautions=[
        "可以容忍一些背景音，但干净素材依然明显更好",
        "素材太杂（多个角色混在一起）会训不出目标音色",
        "轮数不是越多越好：过多会把素材里的噪声也学进去",
    ],
)

SPECS: dict[str, TrainSpec] = {SOVITS.key: SOVITS, RVC.key: RVC}


def for_tool(tool: str) -> TrainSpec:
    """按工具取对应的训练路线：'tts' -> sovits，'voice' -> rvc。"""
    return SOVITS if tool == "tts" else RVC


def requirements_text(spec: TrainSpec, minutes: float | None = None) -> str:
    """生成给弹窗用的完整说明文本。"""
    lines = [
        f"{spec.icon} {spec.title}",
        "",
        f"训练出来后用在：{spec.serves}",
        "",
        "── 素材要求 ──",
        f"· 最低可用：同角色 {spec.min_minutes:.0f} 分钟",
        f"· 效果不错：{spec.good_minutes:.0f} 分钟以上",
        f"· 效果最好：{spec.best_minutes:.0f} 分钟以上",
        f"· 干净人声：{'必须' if spec.clean_required else '建议（可容忍少量背景音）'}",
    ]
    if spec.clean_required:
        lines.append("  （游戏配音常带 BGM/音效，需要先用 Demucs 分离干净）")

    if minutes is not None:
        verdict, _ = spec.length_verdict(minutes)
        lines += ["", f"── 你当前的素材：{minutes:.1f} 分钟 ──", f"· {verdict}"]

    lines += [
        "",
        "── 运行环境 ──",
        f"· {spec.env_note}",
        f"· 训练耗时：{spec.time_note}",
        "",
        "── 产出 ──",
    ]
    lines += [f"· {o}" for o in spec.outputs]

    lines += ["", "── 流程 ──"]
    lines += [f"{i}. {s}" for i, s in enumerate(spec.steps, 1)]

    lines += ["", "── 注意事项 ──"]
    lines += [f"· {c}" for c in spec.cautions]
    return "\n".join(lines)
