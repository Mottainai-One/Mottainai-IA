param(
    [switch]$Reload
)

. (Join-Path $PSScriptRoot "Windows.Common.ps1")

$venvPython = Get-MottainaiVenvPython
$appEnvFile = Join-Path $script:ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $appEnvFile)) {
    throw "Crie .env a partir de .env.example antes de iniciar a API."
}

$secretCheck = @'
from app.security.auth import is_configured_jwt_secret
from config.settings import get_settings

if not is_configured_jwt_secret(get_settings().jwt_secret):
    raise SystemExit("Defina JWT_SECRET forte e diferente dos placeholders antes de iniciar a API.")
'@
Push-Location $script:ProjectRoot
try {
    & $venvPython -c $secretCheck
    if ($LASTEXITCODE -ne 0) {
        throw "A validação de JWT_SECRET falhou."
    }
} finally {
    Pop-Location
}

$uvicornArguments = @(
    "-m", "uvicorn",
    "interfaces.api.main:app",
    "--host", "127.0.0.1",
    "--port", "8000"
)
if ($Reload) {
    $uvicornArguments += "--reload"
}

Push-Location $script:ProjectRoot
try {
    & $venvPython @uvicornArguments
    if ($LASTEXITCODE -ne 0) {
        throw "A API FastAPI terminou com erro."
    }
} finally {
    Pop-Location
}
