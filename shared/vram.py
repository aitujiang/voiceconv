"""显存监视与释放 —— 全工具箱共用。

为什么单独一个文件
------------------
用户要三件事，都围绕显存：

  1. 实时显示占用
  2. 一键释放
  3. 参数防护，别把显存撑爆

三件事都要读同一份信息（nvidia-smi）和调同一套 torch API，
所以集中在这里，各工具只管调用。

关于「释放显存」的一个重要事实
------------------------------
**显存是按进程隔离的。**

工具箱里每个工具是**独立进程**（变声、朗读、RVC 各一个）。
在 A 进程里调 `torch.cuda.empty_cache()` 只能释放 **A 自己的**显存，
碰不到 B 进程的。

所以：
  · 工具内的「释放显存」按钮  -> 真的有效（释放它自己）
  · 启动器上的「释放」        -> 只释放启动器自己（通常它是 0）
  · 想看/清掉别人的显存        -> 只能看进程列表，然后结束进程

`empty_cache()` 也有个前提：只有当**没有张量还引用着那块显存**时才有效。
所以前面要先 `gc.collect()` 把没人引用的对象回收掉。
"""
from __future__ import annotations

import gc
import subprocess
import sys
from dataclasses import dataclass

# 查显存时不要弹出黑窗口（Windows）
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
@dataclass
class GpuInfo:
    """一次 nvidia-smi 查询的结果。"""

    index: int = 0
    name: str = ""
    total_mb: int = 0
    used_mb: int = 0
    free_mb: int = 0
    temp_c: int = 0
    util_pct: int = 0

    @property
    def used_pct(self) -> float:
        return (self.used_mb / self.total_mb * 100) if self.total_mb else 0.0

    def bar(self, width: int = 20) -> str:
        """给界面用的文本进度条。"""
        filled = int(round(self.used_pct / 100 * width))
        return "█" * filled + "░" * (width - filled)


def gpu_info(index: int = 0) -> GpuInfo | None:
    """读 GPU 状态。没显卡 / 没 nvidia-smi 时返回 None。

    用 nvidia-smi 而不是 torch：它能看到**整卡**占用（含别的进程），
    而 torch 只知道自己这个进程用了多少 —— 我们要的是前者。
    """
    fields = ("index,name,memory.total,memory.used,memory.free,"
              "temperature.gpu,utilization.gpu")
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--id={index}", f"--query-gpu={fields}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
            creationflags=_NO_WINDOW,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None

    line = out.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 7:
        return None

    def num(v: str) -> int:
        try:
            return int(float(v))
        except ValueError:
            return 0

    return GpuInfo(
        index=num(parts[0]), name=parts[1],
        total_mb=num(parts[2]), used_mb=num(parts[3]), free_mb=num(parts[4]),
        temp_c=num(parts[5]), util_pct=num(parts[6]),
    )


def gpu_count() -> int:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index",
                              "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=6,
                             creationflags=_NO_WINDOW)
        return len([x for x in out.stdout.strip().splitlines() if x.strip()])
    except Exception:                                # noqa: BLE001
        return 0


@dataclass
class GpuProc:
    """一张卡上的一个占用进程。"""

    pid: int
    name: str
    used_mb: int
    is_ours: bool = False

    def display(self) -> str:
        mark = "  ← 工具箱" if self.is_ours else ""
        return f"{self.name}  (pid {self.pid})  占用 {self.used_mb} MB{mark}"


def gpu_processes(index: int = 0) -> list[GpuProc]:
    """列出正在占用这张卡的进程（按占用从大到小）。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--id={index}",
             "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
            creationflags=_NO_WINDOW,
        )
    except Exception:                                # noqa: BLE001
        return []
    if out.returncode != 0:
        return []

    procs: list[GpuProc] = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid = int(float(parts[0]))
            mb = int(float(parts[2]))
        except ValueError:
            continue
        procs.append(GpuProc(pid=pid, name=parts[1], used_mb=mb))

    procs.sort(key=lambda p: p.used_mb, reverse=True)
    return procs


# --------------------------------------------------------------------------- #
# 释放
# --------------------------------------------------------------------------- #
def torch_allocated_mb() -> tuple[float, float] | None:
    """返回 (本进程已分配 MB, 本进程峰值 MB)。torch 没装或没 CUDA 时 None。

    注意：这**只是本进程**的，和 nvidia-smi 看到的整卡占用不是一回事。
    """
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    try:
        return (torch.cuda.memory_allocated() / 1e6,
                torch.cuda.max_memory_allocated() / 1e6)
    except Exception:                                # noqa: BLE001
        return None


def release(verbose: bool = False) -> tuple[bool, str]:
    """释放**本进程**的显存缓存。

    返回 (是否做了事, 说明文字)。

    两步缺一不可：
      1. gc.collect()            把没人引用的张量回收掉（释放真正的占用）
      2. torch.cuda.empty_cache() 把缓存还给驱动（降低 nvidia-smi 显示的数字）

    只做第 2 步是没用的 —— 张量还被引用着，缓存就还不了。
    """
    before = torch_allocated_mb()
    collected = gc.collect()

    try:
        import torch
    except ImportError:
        return False, "这个工具没用到 PyTorch，不需要释放"
    if not torch.cuda.is_available():
        return False, "没有可用的 CUDA 设备"

    try:
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()
    except Exception as exc:                         # noqa: BLE001
        return False, f"释放失败：{type(exc).__name__}: {exc}"

    after = torch_allocated_mb()
    if before and after:
        freed = before[0] - after[0]
        # 峰值也要清，否则下次统计会带着旧峰值
        try:
            torch.cuda.reset_peak_memory_stats()
        except Exception:                            # noqa: BLE001
            pass
        msg = (f"已释放（回收 {collected} 个对象，"
               f"本进程显存 {before[0]:.0f} → {after[0]:.0f} MB，"
               f"腾出 {max(freed, 0):.0f} MB）")
    else:
        msg = f"已释放缓存（回收 {collected} 个对象）"
    if verbose:
        print(f"[vram] {msg}")
    return True, msg


def release_all_in_process() -> str:
    """尽量在**本进程**里多腾一点：清缓存 + 清各种框架的缓存池。

    给「一键释放」按钮用。仍然只影响本进程（显存按进程隔离）。
    """
    notes: list[str] = []

    ok, msg = release()
    notes.append(msg)

    # 有些框架会自己留一块缓存池，顺手也清掉
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.synchronize()
    except Exception:                                # noqa: BLE001
        pass

    # 垃圾回收再多跑一轮：上一轮可能又释放出新的可回收对象
    gc.collect()

    info = gpu_info()
    if info:
        notes.append(f"整卡现在 {info.used_mb}/{info.total_mb} MB")
    return "；".join(notes)


# --------------------------------------------------------------------------- #
# 参数防护
# --------------------------------------------------------------------------- #
@dataclass
class Bound:
    """一个数值参数的合法范围 + 显存相关的建议上限。"""

    key: str
    label: str
    lo: float
    hi: float
    default: float
    # 每单位这个参数大约吃多少 MB 显存（粗估，用于给建议）
    mb_per_unit: float = 0.0
    # 绝对不得超过的显存安全比例
    safety_ratio: float = 0.85

    def clamp(self, value: float) -> tuple[float, bool, bool]:
        """收敛到合法范围。

        返回 (收敛后的值, 是否被抬过, 是否被压过)。界面据此给出不同的提示：
        低于下限通常是手滑，高于上限可能是有意为之（但会 OOM）。
        """
        if value < self.lo:
            return self.lo, True, False
        if value > self.hi:
            return self.hi, False, True
        return value, False, False

    def vram_limit(self, total_mb: int, reserve_mb: int = 700) -> float | None:
        """按显存算这个参数的上限。不知道占用模型时返回 None。"""
        if not self.mb_per_unit or not total_mb:
            return None
        usable = total_mb * self.safety_ratio - reserve_mb
        if usable <= self.mb_per_unit:
            return self.lo
        return usable / self.mb_per_unit


def check_number(bound: Bound, raw: str, total_mb: int = 0,
                 reserve_mb: int = 700) -> tuple[float | None, str, str]:
    """校验一个输入框里的数值。

    返回 (值 或 None, 提示文字, 级别)。级别是 "ok" / "warn" / "err"。

    设计意图：**不阻止用户输入**，只告诉他风险。
    硬夹取值会让人困惑（"我明明填了 32 怎么变 6 了"），
    明确警告 + 自动降级更符合直觉。
    """
    text = (raw or "").strip()
    if not text:
        return None, "不能为空", "err"
    try:
        value = float(text)
    except ValueError:
        return None, "要填数字", "err"

    if value < bound.lo:
        return bound.lo, f"太小了，最小 {bound.lo:g}（已按最小值算）", "warn"
    if value > bound.hi:
        return bound.hi, f"太大了，最大 {bound.hi:g}（已按最大值算）", "warn"

    limit = bound.vram_limit(total_mb, reserve_mb)
    if limit is not None and value > limit:
        return value, (f"⚠ 按你的显存（{total_mb/1024:.1f} GB）"
                       f"建议不超过 {limit:.0f}，否则可能爆显存"), "warn"

    return value, "", "ok"


def suggest_safe(key: str, total_mb: int, reserve_mb: int = 700) -> int | None:
    """给某个参数一个「显存安全」的建议值。"""
    bounds = {b.key: b for b in COMMON_BOUNDS}
    b = bounds.get(key)
    if not b:
        return None
    limit = b.vram_limit(total_mb, reserve_mb)
    if limit is None:
        return int(b.default)
    # 取 2 的幂或整数档，更符合实际使用的取值习惯
    candidates = [1, 2, 4, 6, 8, 12, 16, 24, 32, 48, 64]
    usable = [c for c in candidates if c <= limit]
    return max(usable) if usable else 1


# 我们自己的工具里那些「填大了就 OOM」的参数
COMMON_BOUNDS: list[Bound] = [
    # GPT-SoVITS 训练：batch 8 在 8GB 卡上会紧，4 很稳
    Bound("batch_size", "批大小", 1, 32, 4, mb_per_unit=520),
    # GPT-SoVITS 的 GPT 部分用 batch 8 起步
    Bound("batch_size_gpt", "GPT 批大小", 1, 16, 8, mb_per_unit=800),
    # 变声的推理步数：越多越慢，显存影响小
    Bound("steps", "推理步数", 4, 100, 30, mb_per_unit=6),
    # 训练轮数不吃显存，只吃时间
    Bound("epochs", "训练轮数", 1, 1000, 100, mb_per_unit=0),
]


def warn_if_tight(total_mb: int, need_mb: int, what: str) -> str:
    """运行前粗略判断够不够，返回提示（空串表示没问题）。"""
    if not total_mb or not need_mb:
        return ""
    info = gpu_info()
    free = info.free_mb if info else total_mb
    if need_mb > free * 0.9:
        return (f"{what}大约需要 {need_mb} MB，当前空闲 {free} MB —— "
                f"可能爆显存。建议先关掉其他工具。")
    return ""


# --------------------------------------------------------------------------- #
def _selftest() -> int:
    print("=" * 58)
    print(" 显存模块自检")
    print("=" * 58)

    n = gpu_count()
    print(f" 显卡数量 : {n}")
    info = gpu_info()
    if info is None:
        print(" 读不到 GPU 状态（没显卡或没装驱动）")
        return 1

    print(f" 型号     : {info.name}")
    print(f" 显存     : {info.used_mb} / {info.total_mb} MB  ({info.used_pct:.1f}%)")
    print(f" 进度条   : {info.bar()}")
    print(f" 温度     : {info.temp_c}°C    利用率: {info.util_pct}%")

    procs = gpu_processes()
    print(f"\n 占用进程 : {len(procs)} 个")
    for p in procs[:6]:
        print(f"   · {p.display()}")

    print(f"\n 本进程 torch 显存 : {torch_allocated_mb()}")
    ok, msg = release()
    print(f" 释放测试 : {msg}")

    print("\n 参数防护示例（当前显存）:")
    for key in ("batch_size", "batch_size_gpt"):
        s = suggest_safe(key, info.total_mb)
        print(f"   {key:16} 建议值 {s}")

    b = COMMON_BOUNDS[0]
    for raw in ("0", "999", "4", "abc"):
        val, note, lvl = check_number(b, raw, info.total_mb)
        print(f"   输入 {raw!r:6} -> {val}  [{lvl}] {note}")

    print("=" * 58)
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
