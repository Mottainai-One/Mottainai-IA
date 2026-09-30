param(
    [Parameter(Mandatory)]
    [ValidateSet("CLIENTE", "ESTOQUISTA", "GERENTE", "DONO")]
    [string]$Role,
    [Parameter(Mandatory)]
    [ValidateRange(1, 2147483647)]
    [int]$UsuarioId,
    [Parameter(Mandatory)]
    [ValidateRange(1, 2147483647)]
    [int]$EmpresaId,
    [Parameter(Mandatory)]
    [ValidateLength(1, 2000)]
    [string]$Message,
    [string]$SessionId
)

. (Join-Path $PSScriptRoot "Windows.Common.ps1")

$venvPython = Get-MottainaiVenvPython
$appEnvFile = Join-Path $script:ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $appEnvFile)) {
    throw "Crie .env a partir de .env.example antes do smoke."
}

if ([string]::IsNullOrWhiteSpace($SessionId)) {
    $SessionId = "windows-$($Role.ToLowerInvariant())-$UsuarioId-$([guid]::NewGuid().ToString('N'))"
}

Push-Location $script:ProjectRoot
try {
    $token = (& $venvPython "scripts\generate_dev_token.py" "--usuario-id" $UsuarioId "--empresa-id" $EmpresaId "--role" $Role | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
        throw "Não foi possível gerar o JWT local para o smoke."
    }

    $headers = @{ Authorization = "Bearer $token" }
    $payload = @{ message = $Message; session_id = $SessionId } | ConvertTo-Json -Compress
    $response = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/chat" -Headers $headers -ContentType "application/json" -Body $payload -TimeoutSec 120 -ErrorAction Stop
    $response | ConvertTo-Json -Depth 8
} finally {
    Pop-Location
}
