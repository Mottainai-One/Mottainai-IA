. (Join-Path $PSScriptRoot "Windows.Common.ps1")

$venvPython = Get-MottainaiVenvPython
$appEnvFile = Join-Path $script:ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $appEnvFile)) {
    throw "Crie .env a partir de .env.example e configure MONGO_URI antes do teste."
}

$pythonCode = @'
import asyncio

from app.database.mongo import get_mongo_client, get_mongo_db


async def main() -> None:
    client = get_mongo_client()
    try:
        await get_mongo_db().command("ping")
    finally:
        client.close()


asyncio.run(main())
print("MongoDB acessível pela configuração da API.")
'@

Push-Location $script:ProjectRoot
try {
    & $venvPython -c $pythonCode
    if ($LASTEXITCODE -ne 0) {
        throw "O teste de conexão com MongoDB falhou."
    }
} finally {
    Pop-Location
}
