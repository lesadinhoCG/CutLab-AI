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
$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupDir = Join-Path $Project ("backup_acabamento_visual_" + $Timestamp)

$Targets = @(
    "server.py",
    "youtube_publisher.py",
    "requirements-youtube.txt",
    "frontend\index.html",
    "frontend\app.js",
    "frontend\style.css",
    "shorts_generator\local\clipper.py"
)

New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null

Write-Host ""
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host " CutLab AI - Acabamento Visual + YouTube" -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Criando backup..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Existing = Join-Path $Project $Rel

    if (Test-Path $Existing) {
        $BackupPath = Join-Path $BackupDir $Rel
        New-Item -ItemType Directory -Path (Split-Path -Parent $BackupPath) -Force | Out-Null
        Copy-Item $Existing $BackupPath -Force
    }
}

Write-Host "Instalando arquivos corrigidos..." -ForegroundColor Yellow

foreach ($Rel in $Targets) {
    $Source = Join-Path $Payload $Rel
    $Destination = Join-Path $Project $Rel

    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item $Source $Destination -Force

    Write-Host "  OK  $Rel" -ForegroundColor Green
}

$Python = $null
$PythonCandidates = @(
    (Join-Path $Project "venv\Scripts\python.exe"),
    (Join-Path $Project ".venv\Scripts\python.exe")
)

foreach ($Candidate in $PythonCandidates) {
    if (Test-Path $Candidate) {
        $Python = $Candidate
        break
    }
}

if (-not $Python) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue

    if ($PythonCommand) {
        $Python = $PythonCommand.Source
    }
}

if ($Python) {
    Write-Host ""
    Write-Host "Instalando bibliotecas oficiais do Google..." -ForegroundColor Yellow

    & $Python -m pip install --disable-pip-version-check -r (Join-Path $Project "requirements-youtube.txt")
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar as dependencias do YouTube" }

    Write-Host "  Dependencias do YouTube OK" -ForegroundColor Green

    Write-Host ""
    Write-Host "Validando arquivos..." -ForegroundColor Yellow

    & $Python -m py_compile (Join-Path $Project "server.py")
    if ($LASTEXITCODE -ne 0) { throw "server.py invalido" }

    & $Python -m py_compile (Join-Path $Project "youtube_publisher.py")
    if ($LASTEXITCODE -ne 0) { throw "youtube_publisher.py invalido" }

    & $Python -m py_compile (Join-Path $Project "shorts_generator\local\clipper.py")
    if ($LASTEXITCODE -ne 0) { throw "clipper.py invalido" }

    Write-Host "  Python OK" -ForegroundColor Green
}
else {
    throw "Python nao encontrado. Ative o ambiente virtual do projeto e rode o instalador novamente."
}

$GitIgnore = Join-Path $Project ".gitignore"
$SensitiveEntries = @(
    ".cutlab/",
    "output/*.cutlab.json"
)

if (Test-Path $GitIgnore) {
    $GitIgnoreBackup = Join-Path $BackupDir ".gitignore"
    Copy-Item $GitIgnore $GitIgnoreBackup -Force
    $GitIgnoreLines = Get-Content $GitIgnore
}
else {
    $GitIgnoreLines = @()
}

foreach ($Entry in $SensitiveEntries) {
    if ($GitIgnoreLines -notcontains $Entry) {
        Add-Content -Path $GitIgnore -Value $Entry
        $GitIgnoreLines += $Entry
    }
}

Write-Host "  Credenciais locais protegidas no .gitignore" -ForegroundColor Green

Write-Host ""
Write-Host "Acabamento visual e integracao do YouTube instalados." -ForegroundColor Green
Write-Host ""
Write-Host "Backup:" -ForegroundColor Yellow
Write-Host "  $BackupDir"
Write-Host ""
Write-Host "Agora:" -ForegroundColor Cyan
Write-Host "  1. Feche o server atual com Ctrl+C"
Write-Host "  2. Rode: python .\server.py"
Write-Host "  3. No navegador pressione Ctrl+F5"
Write-Host "  4. Siga o README_FIX.txt para configurar o OAuth do Google"
Write-Host ""
Write-Host "Primeiro teste recomendado:" -ForegroundColor Cyan
Write-Host "  Use o look Natural+ em 55% e gere 1 Short"
Write-Host "  Envie ao YouTube com privacidade: Privado"
Write-Host "  Revise titulo, descricao e tags antes do upload"
Write-Host ""
Read-Host "Pressione Enter para finalizar"


