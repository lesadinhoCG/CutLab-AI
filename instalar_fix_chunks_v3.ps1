$ErrorActionPreference = "Stop"
$Project = (Get-Location).Path
$Patch = Join-Path $PSScriptRoot "patch_chunks_v3.py"

Write-Host ""
Write-Host "============================================="
Write-Host " CutLab AI - FIX Chunks Robustos V3"
Write-Host "============================================="
Write-Host ""
Write-Host "Projeto: $Project"
Write-Host ""

$Python = Join-Path $Project "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python $Patch $Project
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao instalar o FIX V3."
}

Write-Host ""
Write-Host "Instalacao concluida."
Write-Host "Reinicie o CutLab com: python .\server.py"
Write-Host ""


