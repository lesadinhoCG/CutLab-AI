$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path (Join-Path $Here "main.py")) { $Project = $Here }
elseif (Test-Path (Join-Path (Split-Path $Here -Parent) "main.py")) { $Project = Split-Path $Here -Parent }
else { Write-Host "ERRO: main.py nao encontrado." -ForegroundColor Red; Read-Host "Enter para sair"; exit 1 }
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$Backup = Join-Path $Project ("backup_cutlab_idioma_duracao_" + $Stamp)
New-Item -ItemType Directory -Path $Backup -Force | Out-Null
$Targets = @("server.py","frontend\index.html","frontend\app.js","frontend\style.css")
foreach ($Rel in $Targets) {
  $Current = Join-Path $Project $Rel
  if (Test-Path $Current) { $B=Join-Path $Backup $Rel; New-Item -ItemType Directory -Path (Split-Path -Parent $B) -Force|Out-Null; Copy-Item $Current $B -Force }
  $Source=Join-Path $Here ("payload\"+$Rel); $Destination=Join-Path $Project $Rel
  New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force|Out-Null; Copy-Item $Source $Destination -Force
  Write-Host "OK: $Rel" -ForegroundColor Green
}
$Python=Join-Path $Project "venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "venv\Scripts\python.exe nao encontrado." }
& $Python (Join-Path $Here "patch_pipeline.py") $Project
if ($LASTEXITCODE -ne 0) { throw "Falha ao atualizar transcriber/highlights." }
& $Python -m py_compile (Join-Path $Project "server.py")
& $Python -m py_compile (Join-Path $Project "shorts_generator\local\transcriber.py")
& $Python -m py_compile (Join-Path $Project "shorts_generator\highlights.py")
if ($LASTEXITCODE -ne 0) { throw "Falha na validacao Python." }
Write-Host ""; Write-Host "ATUALIZACAO CONCLUIDA." -ForegroundColor Green
Write-Host "9:16 = minimo 30s | 16:9 = minimo 5min" -ForegroundColor Cyan
Write-Host "Whisper = idioma selecionavel + task=transcribe" -ForegroundColor Cyan
Write-Host "Backup: $Backup" -ForegroundColor Yellow
Write-Host "Reinicie: Ctrl+C, python .\server.py, Ctrl+F5" -ForegroundColor Cyan
Read-Host "Enter para finalizar"


