@echo off
rem Launch the ORIGINAL RVC WebUI. Pure ASCII on purpose -- this machine's console
rem code page is 936 (GBK) and a .bat containing Chinese gets mis-parsed by cmd.exe.
rem All Chinese text lives in the Python files.
setlocal
cd /d "%~dp0..\engine"

rem RVC looks for its assets through these paths.
set "rmvpe_root=%CD%\assets\rmvpe"
set "weight_root=%CD%\assets\weights"
set "index_root=%CD%\assets\indices"
set "outside_index_root=%CD%\assets\indices"
set "weight_pymss_root=%CD%\assets\pymss_weights"
set "HF_ENDPOINT=https://hf-mirror.com"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem tools\my_utils.py hardcodes cmd=["ffmpeg"], so ffmpeg must be on PATH.
rem The shim folder contains ffmpeg.cmd / ffprobe.cmd that forward to our binaries.
set "PATH=%~dp0..\..\..\logs\bin-shim;%~dp0..\..\..\bin;%PATH%"

set "PY=%~dp0..\venv\Scripts\python.exe"
if not exist "%PY%" goto NOPY

echo.
echo   Starting RVC WebUI ...
echo   It opens in your browser. Keep this window open.
echo.
"%PY%" webui.py
goto END

:NOPY
echo.
echo   [ERROR] RVC environment not found:
echo     %PY%
echo.
pause

:END
endlocal
