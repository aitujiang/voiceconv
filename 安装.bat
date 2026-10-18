@echo off
rem One-click install. ASCII-only content on purpose: this machine's console is
rem code page 936 (GBK), and Chinese inside a .bat gets mis-parsed by cmd.exe.
rem All Chinese text lives in setup.py.
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist "setup.py" goto NOFILE

rem Pick an interpreter, in order of preference:
rem   1. an existing .venv (re-run / repair after a partial install)
rem   2. the py launcher pinned to 3.12 (machines with several Pythons)
rem   3. whatever "python" is on PATH
if exist ".venv\Scripts\python.exe" goto USEVENV
py -3.12 -V >nul 2>&1
if not errorlevel 1 goto USEPY312
python -V >nul 2>&1
if not errorlevel 1 goto USEPY
goto NOPY

:USEVENV
".venv\Scripts\python.exe" "setup.py" --yes %*
goto DONE

:USEPY312
py -3.12 "setup.py" --yes %*
goto DONE

:USEPY
python "setup.py" --yes %*
goto DONE

:DONE
if errorlevel 1 goto FAILED
echo.
echo   ------------------------------------------------------------
echo   Done. Follow the "next step" line printed above to launch.
echo   ------------------------------------------------------------
echo.
pause
exit /b 0

:FAILED
echo.
echo   ------------------------------------------------------------
echo   Stopped. Read the message above, fix it, then run this again.
echo   ------------------------------------------------------------
echo.
pause
exit /b 1

:NOPY
echo.
echo   [ERROR] Python not found on PATH.
echo.
echo   Install Python 3.12 (64-bit) from:
echo       https://www.python.org/downloads/
echo   Tick "Add Python to PATH" during setup, then run this file again.
echo.
pause
exit /b 3

:NOFILE
echo.
echo   [ERROR] setup.py not found next to this file.
echo   Extract the whole zip first, then run this file from that folder.
echo.
pause
exit /b 2
