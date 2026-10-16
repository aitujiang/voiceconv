"""Check the argument contract between the GUI and the scripts it launches.

Written as a file because inline quoting through PowerShell mangles regexes.

The bug this guards against: oneclick.py used to pass `--inference-cfg-rate` to
convert.py, which is not a convert.py flag. argparse exits with code 2 on an
unknown flag, so singing mode failed with "错误码 2" for the user. Every flag the
GUI builds must exist in the target script's parser.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def flags(script: str) -> set[str]:
    src = (ROOT / script).read_text(encoding="utf-8")
    return set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', src))


def main() -> int:
    conv = flags("convert.py")
    song = flags("song.py")
    print("convert.py 接受的参数:")
    print("   ", " ".join(sorted(conv)))
    print("song.py 接受的参数:")
    print("   ", " ".join(sorted(song)))

    gui = (ROOT / "oneclick.py").read_text(encoding="utf-8")
    # every --flag literal that appears in oneclick.py
    used = set(re.findall(r'"( --[a-z0-9-]+)"'.replace(" ", ""), gui))
    internal = {"--selftest", "--auto"}          # handled by oneclick.py itself
    external = used - internal

    unknown = sorted(f for f in external if f not in conv and f not in song)
    print()
    if unknown:
        print("❌ GUI 用到了子脚本不认识的参数：", unknown)
        return 1

    # report which external flags map to which script
    print("✅ 参数契约一致，GUI 传出的每个参数都能被对应脚本识别：")
    for f in sorted(external):
        where = []
        if f in conv:
            where.append("convert.py")
        if f in song:
            where.append("song.py")
        print(f"    {f:<26} -> {', '.join(where)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
