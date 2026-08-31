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
    Write-Host "Extraia esta pasta dentro da raiz do projeto." -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}

$Payload = Join-Path $Here "payload"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = Join-Path $Project ("backup_cutlab_v09_" + $Stamp)

New-Item -ItemType Directory -Path $Backup -Force | Out-Null

$Targets = @(
    "server.py",
    "youtube_publisher.py",
    "requirements.txt",
    "frontend\index.html",
    "frontend\style.css",
    "frontend\app.js",
    "shorts_generator\local\clipper.py",
    "shorts_generator\local\downloader.py"
)

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host " CutLab AI v0.9 - Atualizacao Consolidada" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
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

        Copy-Item `
            $Current `
            $BackupPath `
            -Force
    }
}

Write-Host "Instalando arquivos..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {

    $Source = Join-Path $Payload $Rel
    $Destination = Join-Path $Project $Rel

    New-Item `
        -ItemType Directory `
        -Path (Split-Path -Parent $Destination) `
        -Force | Out-Null

    Copy-Item `
        $Source `
        $Destination `
        -Force

    Write-Host "  OK  $Rel" -ForegroundColor Green
}

$Python = Join-Path $Project "venv\Scripts\python.exe"

if (Test-Path $Python) {

    Write-Host ""
    Write-Host "Atualizando dependencias..." -ForegroundColor Yellow

    & $Python -m pip install -r (Join-Path $Project "requirements.txt")

    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao instalar dependencias."
    }

    Write-Host ""
    Write-Host "Validando Python..." -ForegroundColor Yellow

    & $Python -m py_compile (Join-Path $Project "server.py")
    & $Python -m py_compile (Join-Path $Project "youtube_publisher.py")
    & $Python -m py_compile (Join-Path $Project "shorts_generator\local\clipper.py")
    & $Python -m py_compile (Join-Path $Project "shorts_generator\local\downloader.py")

    if ($LASTEXITCODE -ne 0) {
        throw "Falha na validacao Python."
    }
}

Write-Host ""
Write-Host "ATUALIZACAO CONCLUIDA." -ForegroundColor Green
Write-Host ""
Write-Host "Backup salvo em:" -ForegroundColor Yellow
Write-Host "  $Backup"
Write-Host ""
Write-Host "Agora:" -ForegroundColor Cyan
Write-Host "  1. Feche o servidor antigo com Ctrl+C"
Write-Host "  2. Rode: python .\server.py"
Write-Host "  3. Abra: http://127.0.0.1:8000"
Write-Host "  4. Pressione Ctrl+F5"
Write-Host ""
Read-Host "Pressione Enter para finalizar"


