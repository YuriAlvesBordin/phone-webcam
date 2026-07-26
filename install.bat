@echo off
REM PhoneCam - Installer for Windows
REM
REM Runs install.py which detects everything automatically.

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

%PY% install.py %*
endlocal