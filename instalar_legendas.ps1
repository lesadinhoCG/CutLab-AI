$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " CutLab AI - Instalador de Legendas" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

if (Test-Path (Join-Path $Here "main.py")) {
    $Project = $Here
}
elseif (Test-Path (Join-Path (Split-Path $Here -Parent) "main.py")) {
    $Project = Split-Path $Here -Parent
}
else {
    Write-Host "ERRO: main.py nao encontrado." -ForegroundColor Red
    Write-Host "Extraia esta pasta dentro de D:\CutLab-AI e execute novamente."
    Read-Host "Pressione Enter para sair"
    exit 1
}

$Payload = Join-Path $Here "payload"

Write-Host "Projeto detectado:" -ForegroundColor Green
Write-Host "  $Project"
Write-Host ""

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupDir = Join-Path $Project ("backup_legendas_" + $Timestamp)
New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null

$Targets = @(
    "server.py",
    "frontend\index.html",
    "frontend\style.css",
    "frontend\app.js",
    "shorts_generator\local\clipper.py"
)

Write-Host "Criando backup..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Existing = Join-Path $Project $Rel

    if (Test-Path $Existing) {
        $BackupPath = Join-Path $BackupDir $Rel
        $BackupParent = Split-Path -Parent $BackupPath
        New-Item -ItemType Directory -Path $BackupParent -Force | Out-Null
        Copy-Item $Existing $BackupPath -Force
    }
}

Write-Host "Instalando arquivos atualizados..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Source = Join-Path $Payload $Rel
    $Destination = Join-Path $Project $Rel
    $DestinationParent = Split-Path -Parent $Destination

    New-Item -ItemType Directory -Path $DestinationParent -Force | Out-Null
    Copy-Item $Source $Destination -Force

    Write-Host "  OK  $Rel" -ForegroundColor Green
}

$Python = Join-Path $Project "venv\Scripts\python.exe"

if (Test-Path $Python) {
    Write-Host ""
    Write-Host "Validando Python..." -ForegroundColor Yellow

    & $Python -m py_compile (Join-Path $Project "server.py")
    & $Python -m py_compile (Join-Path $Project "shorts_generator\local\clipper.py")

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Falha na validacao Python." -ForegroundColor Red
        Read-Host "Pressione Enter para sair"
        exit 1
    }

    Write-Host "  server.py OK" -ForegroundColor Green
    Write-Host "  clipper.py OK" -ForegroundColor Green
}
else {
    Write-Host ""
    Write-Host "Aviso: venv nao encontrado. Arquivos foram copiados, mas a validacao foi ignorada." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "INSTALACAO CONCLUIDA." -ForegroundColor Green
Write-Host ""
Write-Host "Backup salvo em:"
Write-Host "  $BackupDir"
Write-Host ""
Write-Host "Agora:"
Write-Host "  1. Feche o servidor antigo com Ctrl+C."
Write-Host "  2. Rode: python .\server.py"
Write-Host "  3. Abra: http://127.0.0.1:8000"
Write-Host "  4. No navegador pressione Ctrl+F5 para limpar o cache."
Write-Host ""
Write-Host "Voce deve ver a secao 'LEGENDAS' abaixo das configuracoes do projeto."
Write-Host ""

Read-Host "Pressione Enter para finalizar"


