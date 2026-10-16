@echo off
rem Drag two audio files onto this file to convert without picking them by hand.
rem Order matters: first = audio to convert, second = timbre reference.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto NOENV
if not exist "oneclick.py" goto NOENV

if "%~1"=="" goto USAGE

start "" ".venv\Scripts\pythonw.exe" "oneclick.py" %*
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
echo     .venv\Scripts\pythonw.exe
echo     oneclick.py
echo   See README.md for the setup steps.
echo.
pause
exit /b 2
