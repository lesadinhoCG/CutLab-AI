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
    Read-Host "Pressione Enter para sair"
    exit 1
}

$Source = Join-Path $Here "server.py"
$Destination = Join-Path $Project "server.py"
$Backup = Join-Path $Project "server_BACKUP_ANTES_FIX_ASS.py"

if (Test-Path $Destination) {
    Copy-Item $Destination $Backup -Force
}

Copy-Item $Source $Destination -Force

$Python = Join-Path $Project "venv\Scripts\python.exe"

if (Test-Path $Python) {
    & $Python -m py_compile $Destination

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Falha ao validar server.py." -ForegroundColor Red
        Read-Host "Pressione Enter para sair"
        exit 1
    }
}

Write-Host ""
Write-Host "FIX ASS instalado com sucesso." -ForegroundColor Green
Write-Host "Backup: $Backup"
Write-Host ""
Write-Host "Agora feche o servidor com Ctrl+C, rode python .\server.py e pressione Ctrl+F5 no navegador."
Write-Host ""
Read-Host "Pressione Enter para finalizar"


