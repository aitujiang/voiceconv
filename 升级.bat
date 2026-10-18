@echo off
rem One-click update. ASCII-only content on purpose: this machine's console is
rem code page 936 (GBK), and Chinese inside a .bat gets mis-parsed by cmd.exe.
rem All Chinese text lives in update.py.
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist "update.py" goto NOFILE

if exist ".venv\Scripts\python.exe" goto USEVENV
set "PY=python"
goto RUN

:USEVENV
set "PY=.venv\Scripts\python.exe"

:RUN
"%PY%" "update.py" %*
echo.
pause
exit /b 0

:NOFILE
echo.
echo   [ERROR] update.py not found next to this file.
echo.
pause
exit /b 2
