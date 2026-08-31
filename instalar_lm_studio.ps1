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
$Backup = Join-Path $Project ("backup_cutlab_lmstudio_" + $Stamp)
New-Item -ItemType Directory -Path $Backup -Force | Out-Null

$Targets = @(
    "server.py",
    "frontend\index.html",
    "frontend\app.js",
    "frontend\style.css"
)

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " CutLab AI - Adicionar LM Studio Local" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

foreach ($Rel in $Targets) {
    $Current = Join-Path $Project $Rel

    if (Test-Path $Current) {
        $BackupPath = Join-Path $Backup $Rel
        New-Item -ItemType Directory -Path (Split-Path -Parent $BackupPath) -Force | Out-Null
        Copy-Item $Current $BackupPath -Force
    }

    $Source = Join-Path $Here ("payload\" + $Rel)
    $Destination = Join-Path $Project $Rel
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item $Source $Destination -Force
    Write-Host "OK: $Rel" -ForegroundColor Green
}

$Python = Join-Path $Project "venv\Scripts\python.exe"
if (Test-Path $Python) {
    & $Python -m py_compile (Join-Path $Project "server.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Falha na validacao Python."
    }
}

Write-Host ""
Write-Host "LM STUDIO LOCAL ADICIONADO." -ForegroundColor Green
Write-Host "Backup: $Backup" -ForegroundColor Yellow
Write-Host ""
Write-Host "Nao foram removidos NVIDIA, Gemini, Groq ou OpenAI." -ForegroundColor Cyan
Write-Host ""
Write-Host "No LM Studio, inicie o servidor local OpenAI-compatible." -ForegroundColor Cyan
Write-Host "Padrao esperado: http://127.0.0.1:1234/v1" -ForegroundColor Cyan
Write-Host ""
Write-Host "Depois reinicie o CutLab:" -ForegroundColor Cyan
Write-Host "  Ctrl+C"
Write-Host "  python .\server.py"
Write-Host "  Ctrl+F5 no navegador"
Write-Host ""
Read-Host "Pressione Enter para finalizar"


