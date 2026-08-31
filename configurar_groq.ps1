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

$EnvFile = Join-Path $Project ".env"

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " CutLab AI - Configurar Groq" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Cole sua GROQ_API_KEY." -ForegroundColor Yellow
Write-Host "A chave sera salva apenas no arquivo .env local." -ForegroundColor DarkGray
Write-Host ""

$Secure = Read-Host "GROQ_API_KEY" -AsSecureString
$Ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)

try {
    $Key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)
}

if ([string]::IsNullOrWhiteSpace($Key)) {
    throw "A chave nao pode ficar vazia."
}

if (Test-Path $EnvFile) {
    $Text = Get-Content $EnvFile -Raw
}
else {
    $Text = ""
}

function Set-EnvValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $script:Text = $script:Text -replace (
        "(?m)^" + [regex]::Escape($Name) + "=.*$"
    ), (
        $Name + "=" + $Value
    )

    if ($script:Text -notmatch (
        "(?m)^" + [regex]::Escape($Name) + "="
    )) {
        if ($script:Text -and -not $script:Text.EndsWith("`n")) {
            $script:Text += "`r`n"
        }

        $script:Text += (
            $Name + "=" + $Value + "`r`n"
        )
    }
}

Set-EnvValue "GROQ_API_KEY" $Key
Set-EnvValue "GROQ_BASE_URL" "https://api.groq.com/openai/v1"
Set-EnvValue "GROQ_MODEL" "qwen/qwen3.6-27b"

Set-Content `
    -Path $EnvFile `
    -Value $Text `
    -Encoding UTF8

Write-Host ""
Write-Host "Groq configurada." -ForegroundColor Green
Write-Host "Modelo inicial: qwen/qwen3.6-27b" -ForegroundColor Green
Write-Host ""
Write-Host "Reinicie o servidor do CutLab para carregar a chave." -ForegroundColor Cyan
Write-Host ""
Read-Host "Pressione Enter para finalizar"


