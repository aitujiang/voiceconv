@echo off
rem Toolbox launcher. Pure ASCII on purpose: this machine's console code page is
rem 936 (GBK), and a .bat containing Chinese gets mis-parsed by cmd.exe.
rem All Chinese text lives in launcher.py instead.
cd /d "%~dp0"

if not exist "launcher.py" goto NOFILE

rem Pick an interpreter, in order of preference:
rem   1. the bundled venv (has tkinter + all deps)
rem   2. pythonw on PATH
if exist ".venv\Scripts\pythonw.exe" goto USEVENV
set "PY=pythonw"
goto RUN

:USEVENV
set "PY=.venv\Scripts\pythonw.exe"

:RUN
start "" "%PY%" "launcher.py"
exit /b 0

:NOFILE
echo.
echo   [ERROR] launcher.py not found next to this file.
echo.
pause
exit /b 2
