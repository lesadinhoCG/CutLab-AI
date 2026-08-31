@echo off
title CutLab AI

cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo.
    echo [CutLab AI] Ambiente virtual nao encontrado.
    echo Coloque estes arquivos na raiz do projeto.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   CutLab AI
echo   http://127.0.0.1:8000
echo ============================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"

venv\Scripts\python.exe server.py

pause


