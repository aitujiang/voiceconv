@echo off
rem Toolbox launcher. Pure ASCII on purpose: this machine's console code page is
rem 936 (GBK), and a .bat containing Chinese gets mis-parsed by cmd.exe.
rem All Chinese text lives in launcher.py instead.
cd /d "%~dp0"

if not exist "launcher.py" goto NOFILE

rem Prefer the bundled interpreter that has tkinter; fall back to PATH python.
set "PY="
if exist "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" (
  set "PY=C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
)
if "%PY%"=="" if exist ".venv\Scripts\pythonw.exe" set "PY=.venv\Scripts\pythonw.exe"
if "%PY%"=="" set "PY=pythonw"

start "" "%PY%" "launcher.py"
exit /b 0

:NOFILE
echo.
echo   [ERROR] launcher.py not found next to this file.
echo.
pause
exit /b 2
