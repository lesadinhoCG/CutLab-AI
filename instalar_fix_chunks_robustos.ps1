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

$Python = Join-Path $Project "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " CutLab AI - FIX chunks robustos V2" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

& $Python (Join-Path $Here "patch_robust_chunks.py") $Project
if ($LASTEXITCODE -ne 0) { throw "Falha ao aplicar patch em highlights.py." }

& $Python -m py_compile (Join-Path $Project "shorts_generator\highlights.py")
if ($LASTEXITCODE -ne 0) { throw "highlights.py falhou na validacao Python." }

Write-Host ""
Write-Host "FIX INSTALADO E VALIDADO." -ForegroundColor Green
Write-Host "Backup criado ao lado do highlights.py." -ForegroundColor Yellow
Write-Host ""
Write-Host "Agora reinicie:" -ForegroundColor Cyan
Write-Host "  Ctrl+C"
Write-Host "  python .\server.py"
Write-Host "  Ctrl+F5 no navegador"
Write-Host ""
Read-Host "Pressione Enter para finalizar"


