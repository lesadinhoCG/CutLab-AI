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
    Write-Host "Extraia esta pasta dentro da raiz do projeto e execute novamente." -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}

$Payload = Join-Path $Here "payload"
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupDir = Join-Path $Project ("backup_reframe_front_" + $Timestamp)
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null

$Targets = @(
    "server.py",
    "frontend\index.html",
    "frontend\app.js",
    "frontend\style.css",
    "shorts_generator\local\clipper.py"
)

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " CutLab AI - Update de Enquadramento" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Criando backup..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Current = Join-Path $Project $Rel
    if (Test-Path $Current) {
        $BackupPath = Join-Path $BackupDir $Rel
        New-Item -ItemType Directory -Path (Split-Path -Parent $BackupPath) -Force | Out-Null
        Copy-Item $Current $BackupPath -Force
    }
}

Write-Host "Instalando arquivos..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Source = Join-Path $Payload $Rel
    $Destination = Join-Path $Project $Rel
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item $Source $Destination -Force
    Write-Host "  OK  $Rel" -ForegroundColor Green
}

$Python = Join-Path $Project "venv\Scripts\python.exe"
if (Test-Path $Python) {
    & $Python -m py_compile (Join-Path $Project "server.py")
    & $Python -m py_compile (Join-Path $Project "shorts_generator\local\clipper.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Falha na validacao Python." -ForegroundColor Red
        Read-Host "Pressione Enter para sair"
        exit 1
    }
    Write-Host ""
    Write-Host "Validacao OK." -ForegroundColor Green
}

Write-Host ""
Write-Host "Instalacao concluida com sucesso." -ForegroundColor Green
Write-Host "Backup salvo em:" -ForegroundColor Yellow
Write-Host "  $BackupDir"
Write-Host ""
Write-Host "Agora:" -ForegroundColor Cyan
Write-Host "  1. Feche o servidor antigo com Ctrl+C"
Write-Host "  2. Rode: python .\server.py"
Write-Host "  3. Abra: http://127.0.0.1:8000"
Write-Host "  4. Pressione Ctrl+F5 no navegador"
Write-Host ""
Write-Host "No frontend aparecerÃ¡ a seÃ§Ã£o ENQUADRAMENTO com:" -ForegroundColor Cyan
Write-Host "  - AutomÃ¡tico"
Write-Host "  - Pessoa / Rosto"
Write-Host "  - ConteÃºdo / Tela"
Write-Host ""
Read-Host "Pressione Enter para finalizar"


