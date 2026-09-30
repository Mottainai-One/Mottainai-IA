. (Join-Path $PSScriptRoot "Windows.Common.ps1")

$venvPython = Get-MottainaiVenvPython
$appEnvFile = Join-Path $script:ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $appEnvFile)) {
    throw "Crie .env a partir de .env.example e configure REDIS_URL/REDIS_PASSWORD antes do teste."
}

$pythonCode = @'
import asyncio

from app.database.redis_client import close_redis_pool, get_redis


async def main() -> None:
    try:
        await get_redis().ping()
    finally:
        await close_redis_pool()


asyncio.run(main())
print("Redis acessível pela configuração da API.")
'@

Push-Location $script:ProjectRoot
try {
    & $venvPython -c $pythonCode
    if ($LASTEXITCODE -ne 0) {
        throw "O teste de conexão com Redis falhou."
    }
} finally {
    Pop-Location
}
