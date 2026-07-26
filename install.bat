@echo off
REM PhoneCam - Instalador para Windows
REM
REM Roda install.py que detecta tudo automaticamente.

setlocal
cd /d "%~dp0"

where python >nul 2>&1 && set "PY=python" || (
  where python3 >nul 2>&1 && set "PY=python3" || (
    where py >nul 2>&1 && set "PY=py -3" || (
      echo [ERRO] Python 3 nao encontrado. Instale de https://python.org
      exit /b 1
    )
  )
)

%PY% install.py %*
endlocal
