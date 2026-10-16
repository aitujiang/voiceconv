@echo off
rem Always run from the folder this file lives in, e.g. when double-clicked.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto NOENV
if not exist "oneclick.py" goto NOENV

start "" ".venv\Scripts\pythonw.exe" "oneclick.py"
exit /b 0

:NOENV
echo.
echo   [ERROR] Missing runtime. Expected:
echo     .venv\Scripts\pythonw.exe
echo     oneclick.py
echo   See README.md for the setup steps.
echo.
pause
exit /b 2
