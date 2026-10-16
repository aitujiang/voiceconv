"""Write the two launcher .bat files as pure ASCII.

Why: this machine's console code page is 936 (GBK). A .bat saved as UTF-8 and
containing Chinese gets mis-parsed by cmd.exe -- `if not exist` became `xist`,
`echo ...` text became bogus commands, and the launcher silently died. Keeping
the batch files 100% ASCII removes the whole class of problem, whatever the
code page is. All Chinese text lives in oneclick.py instead.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent

LAUNCH = """@echo off
rem Always run from the folder this file lives in, e.g. when double-clicked.
cd /d "%~dp0"

if not exist ".venv\\Scripts\\pythonw.exe" goto NOENV
if not exist "oneclick.py" goto NOENV

start "" ".venv\\Scripts\\pythonw.exe" "oneclick.py"
exit /b 0

:NOENV
echo.
echo   [ERROR] Missing runtime. Expected:
echo     .venv\\Scripts\\pythonw.exe
echo     oneclick.py
echo   See README.md for the setup steps.
echo.
pause
exit /b 2
"""

DRAG = """@echo off
rem Drag two audio files onto this file to convert without picking them by hand.
rem Order matters: first = audio to convert, second = timbre reference.
cd /d "%~dp0"

if not exist ".venv\\Scripts\\pythonw.exe" goto NOENV
if not exist "oneclick.py" goto NOENV

if "%~1"=="" goto USAGE

start "" ".venv\\Scripts\\pythonw.exe" "oneclick.py" %*
exit /b 0

:USAGE
echo.
echo   Drag TWO audio files onto this file at once.
echo     first  = the audio to convert   (its words are kept)
echo     second = the timbre reference   (its voice is borrowed)
echo.
echo   Order matters. If unsure, double-click the plain launcher
echo   instead and pick the files in the window.
echo.
pause
exit /b 1

:NOENV
echo.
echo   [ERROR] Missing runtime. Expected:
echo     .venv\\Scripts\\pythonw.exe
echo     oneclick.py
echo   See README.md for the setup steps.
echo.
pause
exit /b 2
"""


def main() -> int:
    # ASCII only, CRLF line endings -- what cmd.exe expects.
    targets = {
        "一键变声.bat": LAUNCH,
        "一键变声-拖拽两个文件到这里.bat": DRAG,
    }
    for name, body in targets.items():
        data = body.replace("\n", "\r\n").encode("ascii")  # raises if non-ASCII slipped in
        path = ROOT / name
        path.write_bytes(data)
        print(f"wrote {path.name}  ({len(data)} bytes, pure ASCII)")

    # Verify: every byte must be < 0x80.
    for name in targets:
        raw = (ROOT / name).read_bytes()
        bad = [b for b in raw if b > 0x7F]
        print(f"check {name}: non-ascii bytes = {len(bad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
