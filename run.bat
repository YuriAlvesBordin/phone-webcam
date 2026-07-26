@echo off
REM PhoneCam - Launcher for Windows
REM
REM Usage:
REM   run.bat                  # default: 1280x720 @ 30fps, port 8765
REM   run.bat --audio          # video + microphone
REM   run.bat --width 1920 --height 1080
REM   run.bat --port 9000 --pin 123456

setlocal
cd /d "%~dp0"

where python >nul 2>&1 && set "PY=python" || (
  where python3 >nul 2>&1 && set "PY=python3" || (
    where py >nul 2>&1 && set "PY=py -3" || (
      echo [ERROR] Python 3 not found. Install from https://python.org
      exit /b 1
    )
  )
)

%PY% run.py %*
endlocal