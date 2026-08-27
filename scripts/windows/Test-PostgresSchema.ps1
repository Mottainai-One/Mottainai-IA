. (Join-Path $PSScriptRoot "Windows.Common.ps1")

$venvPython = Get-MottainaiVenvPython
$appEnvFile = Join-Path $script:ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $appEnvFile)) {
    throw "Crie .env a partir de .env.example e configure POSTGRES_DSN antes do preflight."
}

Push-Location $script:ProjectRoot
try {
    & $venvPython "scripts\preflight_postgres.py"
    if ($LASTEXITCODE -ne 0) {
        throw "O preflight do schema PostgreSQL falhou."
    }
} finally {
    Pop-Location
}
