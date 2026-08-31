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
    Write-Host "Extraia esta atualizacao dentro da raiz do CutLab." -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = Join-Path $Project ("backup_cutlab_cortes_persistentes_" + $Stamp)
New-Item -ItemType Directory -Path $Backup -Force | Out-Null

$Targets = @(
    "frontend\index.html",
    "frontend\style.css",
    "frontend\app.js"
)

foreach ($Rel in $Targets) {
    $Current = Join-Path $Project $Rel
    if (Test-Path $Current) {
        $BackupPath = Join-Path $Backup $Rel
        New-Item -ItemType Directory -Path (Split-Path -Parent $BackupPath) -Force | Out-Null
        Copy-Item $Current $BackupPath -Force
    }

    $Source = Join-Path $Here ("payload\" + $Rel)
    $Destination = Join-Path $Project $Rel
    Copy-Item $Source $Destination -Force
    Write-Host "OK: $Rel" -ForegroundColor Green
}

Write-Host ""
Write-Host "FIX instalado." -ForegroundColor Green
Write-Host "Backup: $Backup" -ForegroundColor Yellow
Write-Host ""
Write-Host "Reinicie o servidor e pressione Ctrl+F5 no navegador." -ForegroundColor Cyan
Read-Host "Pressione Enter para finalizar"


