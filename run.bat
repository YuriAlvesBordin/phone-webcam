@echo off
REM PhoneCam - Launcher para Windows
REM
REM Uso:
REM   run.bat                  # padrao: 1280x720 @ 30fps, porta 8765
REM   run.bat --audio          # video + microfone
REM   run.bat --width 1920 --height 1080
REM   run.bat --port 9000 --pin 123456

setlocal
cd /d "%~dp0"

REM Detecta python (python, python3, py)
where python >nul 2>&1 && set "PY=python" || (
  where python3 >nul 2>&1 && set "PY=python3" || (
    where py >nul 2>&1 && set "PY=py -3" || (
      echo [ERRO] Python 3 nao encontrado. Instale de https://python.org
      exit /b 1
    )
  )
)

%PY% run.py %*
endlocal
