"""预设的存取 —— 人物预设与任务预设。

两种类型（用户定的）
------------------
1. **现场变声**（voice_type="realtime"）
   拿一段参考音频当场克隆。即选即用，不用训练。
   存：素材路径 + 情绪模式 + 精细度 + 相似度

2. **模型训练**（voice_type="trained"）
   用精调出来的模型文件。效果更好，但要先训练。
   存：模型路径。相似度/精细度对它无意义。

为什么用「一个 JSON 一个预设」而不是一个大 JSON
--------------------------------------------
- 单个文件坏掉不会拖垮全部预设
- 以后要分享/备份某个预设，直接拷一个文件
- 手工编辑也方便
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from shared import paths

# 两种音色类型
VOICE_REALTIME = "realtime"   # 现场变声（参考音频）
VOICE_TRAINED = "trained"     # 模型训练

TYPE_LABEL = {
    VOICE_REALTIME: "现场变声",
    VOICE_TRAINED: "模型训练",
}
TYPE_ICON = {
    VOICE_REALTIME: "🔊",
    VOICE_TRAINED: "🧠",
}


@dataclass
class Preset:
    """一个音色预设。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "新音色"
    voice_type: str = VOICE_REALTIME

    # --- 现场变声用 ---
    ref_audio: str = ""            # 参考音频的绝对路径
    mode: str = "v1"               # v1 / v2 / singing
    similarity: float = 0.7        # 仅 v2 生效
    steps: int = 30                # 精细度

    # --- 模型训练用 ---
    model_path: str = ""           # RVC 的 .pth / GPT-SoVITS 的 .ckpt（主权重）
    index_path: str = ""           # RVC 专属：.index 特征检索文件（提升相似度）
    model_kind: str = ""           # "rvc" 或 "sovits"，说明这是哪种训练产物

    # --- 通用 ---
    note: str = ""
    created: float = field(default_factory=time.time)
    last_used: float = 0.0         # 用来把最近用过的排前面

    # ---------------------------------------------------------------- 校验 --
    def ref_exists(self) -> bool:
        return bool(self.ref_audio) and Path(self.ref_audio).exists()

    def model_exists(self) -> bool:
        return bool(self.model_path) and Path(self.model_path).exists()

    def index_exists(self) -> bool:
        return bool(self.index_path) and Path(self.index_path).exists()

    def supports(self, tool: str) -> bool:
        """这个预设能不能用在指定工具上。

        两种训练产物互不通用，所以必须分开判断：
          · 现场变声（参考音频）—— 变声和朗读都能用（零样本）
          · sovits 模型 —— **只能**用于朗读
          · rvc 模型   —— **只能**用于变声
        """
        if self.voice_type == VOICE_REALTIME:
            return True                      # 零样本两边都能用
        if tool == "tts":
            return self.model_kind == "sovits"
        return self.model_kind == "rvc"

    def is_usable(self) -> bool:
        """这个预设现在能不能真的用。"""
        if self.voice_type == VOICE_REALTIME:
            return self.ref_exists()
        return self.model_exists()

    def problem(self) -> str:
        """不可用时给出原因（界面上直接显示）。"""
        if self.voice_type == VOICE_REALTIME:
            if not self.ref_audio:
                return "还没指定素材"
            if not self.ref_exists():
                return f"素材找不到了：{Path(self.ref_audio).name}"
            return ""
        if not self.model_path:
            return "还没指定模型（训练功能尚未开发）"
        if not self.model_exists():
            return f"模型找不到了：{Path(self.model_path).name}"
        return ""

    def unsupported_reason(self, tool: str) -> str:
        """模型型预设用在不匹配的工具上时的原因说明。"""
        if self.supports(tool):
            return ""
        want = "朗读模型（GPT-SoVITS）" if tool == "tts" else "变声模型（RVC）"
        have = {"sovits": "朗读模型（GPT-SoVITS）",
                "rvc": "变声模型（RVC）"}.get(self.model_kind, "未知类型的模型")
        return (f"这是{have}，不能用在"
                f"{'朗读' if tool == 'tts' else '变声'}上。\n"
                f"这个位置需要{want}。\n\n"
                f"两种模型的训练方式和产出都不一样，互不通用。")

    def summary(self) -> str:
        """卡片上那行小字。"""
        if self.voice_type == VOICE_REALTIME:
            n = Path(self.ref_audio).name if self.ref_audio else "无素材"
            return f"{n} · {self.mode} · 精细度{self.steps}"
        n = Path(self.model_path).name if self.model_path else "无模型"
        kind = {"sovits": "朗读模型", "rvc": "变声模型"}.get(self.model_kind, "模型")
        return f"{n} · {kind}"

    def type_label(self) -> str:
        return TYPE_LABEL.get(self.voice_type, self.voice_type)

    def type_icon(self) -> str:
        return TYPE_ICON.get(self.voice_type, "🔊")


# --------------------------------------------------------------------------- #
# 存取
# --------------------------------------------------------------------------- #
def _preset_file(preset_id: str) -> Path:
    return paths.PRESETS / f"{preset_id}.json"


def load_all() -> list[Preset]:
    """读出全部预设。坏掉的文件跳过，不影响其它的。"""
    paths.PRESETS.mkdir(parents=True, exist_ok=True)
    out: list[Preset] = []
    for f in sorted(paths.PRESETS.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            known = {k: v for k, v in data.items() if k in Preset.__dataclass_fields__}
            out.append(Preset(**known))
        except Exception:                      # noqa: BLE001
            continue                           # 单个坏文件不该拖垮整个列表
    # 最近用过的排前面，其余按创建时间倒序
    out.sort(key=lambda p: (p.last_used, p.created), reverse=True)
    return out


def save(preset: Preset) -> Preset:
    paths.PRESETS.mkdir(parents=True, exist_ok=True)
    _preset_file(preset.id).write_text(
        json.dumps(asdict(preset), ensure_ascii=False, indent=2), encoding="utf-8")
    return preset


def delete(preset_id: str) -> None:
    f = _preset_file(preset_id)
    if f.exists():
        f.unlink()


def touch(preset_id: str) -> None:
    """记一次「最近用过」，让它在列表里冒头。"""
    for p in load_all():
        if p.id == preset_id:
            p.last_used = time.time()
            save(p)
            return


def duplicate(preset: Preset) -> Preset:
    copy = Preset(**{**asdict(preset), "id": uuid.uuid4().hex[:12],
                     "name": preset.name + " 副本",
                     "created": time.time(), "last_used": 0.0})
    return save(copy)
