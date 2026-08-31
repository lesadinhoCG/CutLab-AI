$ErrorActionPreference = "Stop"
$Project = (Get-Location).Path
$Patch = Join-Path $PSScriptRoot "patch_boundary_editor_v1.py"

Write-Host ""
Write-Host "======================================================"
Write-Host " CutLab AI - Semantic Boundary Editor V1"
Write-Host "======================================================"
Write-Host ""
Write-Host "Projeto: $Project"
Write-Host ""

$Python = Join-Path $Project "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python $Patch $Project
if ($LASTEXITCODE -ne 0) {
    throw "Falha ao instalar o Semantic Boundary Editor."
}

Write-Host ""
Write-Host "Instalacao concluida."
Write-Host "Reinicie o CutLab com: python .\server.py"
Write-Host ""


