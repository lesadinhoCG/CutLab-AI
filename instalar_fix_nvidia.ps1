$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (Test-Path (Join-Path $Here "main.py")) {
    $Project = $Here
}
elseif (Test-Path (Join-Path (Split-Path $Here -Parent) "main.py")) {
    $Project = Split-Path $Here -Parent
}
else {
    Write-Host "ERRO: main.py nao encontrado." -ForegroundColor Red
    Write-Host "Extraia esta pasta dentro de D:\CutLab-AI." -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}

$Source = Join-Path $Here "payload\shorts_generator\local\llm.py"
$Destination = Join-Path $Project "shorts_generator\local\llm.py"

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = Join-Path $Project ("shorts_generator\local\llm_BACKUP_" + $Stamp + ".py")

if (Test-Path $Destination) {
    Copy-Item $Destination $Backup -Force
}

Copy-Item $Source $Destination -Force

$Python = Join-Path $Project "venv\Scripts\python.exe"

if (Test-Path $Python) {
    & $Python -m py_compile $Destination

    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao validar llm.py."
    }
}

Write-Host ""
Write-Host "FIX NVIDIA instalado." -ForegroundColor Green
Write-Host ""
Write-Host "Timeout por modelo: 240 segundos" -ForegroundColor Cyan
Write-Host "Fallback padrao:" -ForegroundColor Cyan
Write-Host "  1. modelo escolhido no frontend"
Write-Host "  2. nvidia/llama-3.3-nemotron-super-49b-v1.5"
Write-Host "  3. deepseek-ai/deepseek-v4-flash-0731"
Write-Host ""
Write-Host "Backup:" -ForegroundColor Yellow
Write-Host "  $Backup"
Write-Host ""
Write-Host "Agora feche o servidor antigo com Ctrl+C e rode:"
Write-Host "  python .\server.py"
Write-Host ""

Read-Host "Pressione Enter para finalizar"


