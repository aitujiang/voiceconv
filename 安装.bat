@echo off
rem One-click install. ASCII-only content on purpose: this machine's console is
rem code page 936 (GBK), and Chinese inside a .bat gets mis-parsed by cmd.exe.
rem All Chinese text lives in setup.py.
rem
rem If Python 3.10-3.12 is missing, this script downloads and installs
rem Python 3.12 for the CURRENT USER ONLY (no administrator rights), then
rem carries on. Users do not have to install Python themselves.
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

if not exist "setup.py" goto NOFILE

rem 1) an existing .venv (re-run / repair after a partial install)
if exist ".venv\Scripts\python.exe" goto USEVENV

rem 2) the py launcher pinned to 3.12
py -3.12 -V >nul 2>&1
if not errorlevel 1 goto USEPY312

rem 3) plain python on PATH, but only if the version is acceptable
python -c "import sys;sys.exit(0 if (3,10)<=sys.version_info<(3,13) else 1)" >nul 2>&1
if not errorlevel 1 goto USEPY

rem 4) the usual per-user install location
set "PYLOCAL=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%PYLOCAL%" goto USELOCAL
if exist "C:\Python312\python.exe" (
    set "PYLOCAL=C:\Python312\python.exe"
    goto USELOCAL
)
if exist "C:\Program Files\Python312\python.exe" (
    set "PYLOCAL=C:\Program Files\Python312\python.exe"
    goto USELOCAL
)

goto BOOTSTRAP

:USEVENV
".venv\Scripts\python.exe" "setup.py" --yes %*
goto DONE

:USEPY312
py -3.12 "setup.py" --yes %*
goto DONE

:USEPY
python "setup.py" --yes %*
goto DONE

:USELOCAL
"%PYLOCAL%" "setup.py" --yes %*
goto DONE

:BOOTSTRAP
echo.
echo   Python 3.10-3.12 was not found on this PC.
echo   Downloading Python 3.12 and installing it for this user only
echo   (about 26 MB, no administrator rights needed)...
echo.
set "PYVER=3.12.10"
set "NAME=python-%PYVER%-amd64.exe"
set "INST=%TEMP%\%NAME%"
del "%INST%" >nul 2>&1

call :FETCH "https://mirrors.huaweicloud.com/python/%PYVER%/%NAME%"
if exist "%INST%" goto GOTINST
call :FETCH "https://registry.npmmirror.com/-/binary/python/%PYVER%/%NAME%"
if exist "%INST%" goto GOTINST
call :FETCH "https://www.python.org/ftp/python/%PYVER%/%NAME%"
if exist "%INST%" goto GOTINST
goto BOOTFAIL

:FETCH
echo   Downloading from %~1
where curl >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try{Invoke-WebRequest -Uri '%~1' -OutFile '%INST%' -UseBasicParsing}catch{exit 1}" >nul 2>&1
) else (
    curl -L --fail --retry 2 --connect-timeout 15 -o "%INST%" "%~1" >nul 2>&1
)
if not exist "%INST%" exit /b 0
set "SZ=0"
for %%A in ("%INST%") do set "SZ=%%~zA"
if "%SZ%"=="" set "SZ=0"
if %SZ% LSS 20000000 del "%INST%" >nul 2>&1
exit /b 0

:GOTINST
echo   Installer is ready. Running it silently (this takes a few minutes)...
start /wait "" "%INST%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
del "%INST%" >nul 2>&1

set "PYLOCAL=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
set /a WAIT=0
:WAITPY
if exist "%PYLOCAL%" goto USELOCAL
if exist "C:\Python312\python.exe" (
    set "PYLOCAL=C:\Python312\python.exe"
    goto USELOCAL
)
set /a WAIT+=1
if %WAIT% GTR 90 goto BOOTFAIL2
timeout /t 2 /nobreak >nul
goto WAITPY

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

:BOOTFAIL
echo.
echo   [ERROR] Could not download the Python installer (network).
echo   Install Python 3.12 manually from https://www.python.org/downloads/
echo   tick "Add Python to PATH", then run this file again.
echo.
pause
exit /b 4

:BOOTFAIL2
echo.
echo   [ERROR] Python was installed but the interpreter was not found yet.
echo   Close this window and double-click this file again.
echo.
pause
exit /b 5

:NOFILE
echo.
echo   [ERROR] setup.py not found next to this file.
echo   Extract the whole zip first, then run this file from that folder.
echo.
pause
exit /b 2
