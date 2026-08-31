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

$Payload = Join-Path $Here "payload"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = Join-Path $Project ("backup_cutlab_youtube_completo_" + $Stamp)

New-Item -ItemType Directory -Path $Backup -Force | Out-Null

$Targets = @(
    "server.py",
    "youtube_publisher.py",
    "frontend\index.html",
    "frontend\style.css",
    "frontend\app.js"
)

Write-Host ""
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host " CutLab AI - Pacote Completo do YouTube" -ForegroundColor Cyan
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Criando backup..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Current = Join-Path $Project $Rel

    if (Test-Path $Current) {
        $BackupPath = Join-Path $Backup $Rel

        New-Item `
            -ItemType Directory `
            -Path (Split-Path -Parent $BackupPath) `
            -Force | Out-Null

        Copy-Item $Current $BackupPath -Force
    }
}

Write-Host "Instalando modulo completo do YouTube..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Source = Join-Path $Payload $Rel
    $Destination = Join-Path $Project $Rel

    New-Item `
        -ItemType Directory `
        -Path (Split-Path -Parent $Destination) `
        -Force | Out-Null

    Copy-Item $Source $Destination -Force
    Write-Host "  OK  $Rel" -ForegroundColor Green
}

$Python = Join-Path $Project "venv\Scripts\python.exe"

if (Test-Path $Python) {
    Write-Host ""
    Write-Host "Validando Python..." -ForegroundColor Yellow

    & $Python -m py_compile (Join-Path $Project "server.py")
    & $Python -m py_compile (Join-Path $Project "youtube_publisher.py")

    if ($LASTEXITCODE -ne 0) {
        throw "Falha na validacao Python."
    }
}

Write-Host ""
Write-Host "PACOTE YOUTUBE INSTALADO." -ForegroundColor Green
Write-Host ""
Write-Host "Backup:" -ForegroundColor Yellow
Write-Host "  $Backup"
Write-Host ""
Write-Host "Agora:" -ForegroundColor Cyan
Write-Host "  1. Ctrl+C no servidor antigo"
Write-Host "  2. python .\server.py"
Write-Host "  3. Abra http://127.0.0.1:8000"
Write-Host "  4. Ctrl+F5"
Write-Host ""
Read-Host "Pressione Enter para finalizar"


