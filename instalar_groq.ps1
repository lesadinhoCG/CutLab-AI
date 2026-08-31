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
    Write-Host "Extraia este pacote dentro da raiz do CutLab." -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = Join-Path $Project ("backup_cutlab_groq_" + $Stamp)

New-Item -ItemType Directory -Path $Backup -Force | Out-Null

$Targets = @(
    "server.py",
    "youtube_publisher.py",
    "frontend\index.html",
    "frontend\app.js",
    "frontend\style.css"
)

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " CutLab AI - Integracao Groq" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

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

    $Source = Join-Path $Here ("payload\" + $Rel)
    $Destination = Join-Path $Project $Rel

    New-Item `
        -ItemType Directory `
        -Path (Split-Path -Parent $Destination) `
        -Force | Out-Null

    Copy-Item $Source $Destination -Force
    Write-Host "OK: $Rel" -ForegroundColor Green
}

$Python = Join-Path $Project "venv\Scripts\python.exe"

if (Test-Path $Python) {
    & $Python -m py_compile (Join-Path $Project "server.py")
    & $Python -m py_compile (Join-Path $Project "youtube_publisher.py")

    if ($LASTEXITCODE -ne 0) {
        throw "Falha na validacao Python."
    }
}

Write-Host ""
Write-Host "INTEGRACAO GROQ INSTALADA." -ForegroundColor Green
Write-Host "Backup: $Backup" -ForegroundColor Yellow
Write-Host ""
Write-Host "Proximo passo:" -ForegroundColor Cyan
Write-Host "  Execute CONFIGURAR_GROQ.bat e cole sua chave Groq."
Write-Host ""
Read-Host "Pressione Enter para finalizar"


