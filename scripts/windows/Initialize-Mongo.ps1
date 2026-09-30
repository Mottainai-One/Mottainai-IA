param(
    [switch]$SeedDemo,
    [ValidateRange(10, 300)]
    [int]$TimeoutSeconds = 90
)

. (Join-Path $PSScriptRoot "Windows.Common.ps1")

Start-MottainaiDependency -Service "mongo" -ContainerName "mottainai-windows-mongo" -TimeoutSeconds $TimeoutSeconds

$venvPython = Get-MottainaiVenvPython
$appEnvFile = Join-Path $script:ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $appEnvFile)) {
    throw "Crie .env a partir de .env.example e configure MONGO_URI antes da inicialização."
}

$pythonArguments = @("scripts\setup_mongo.py")
if ($SeedDemo) {
    $pythonArguments += "--seed-demo"
}

Push-Location $script:ProjectRoot
try {
    & $venvPython @pythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "A inicialização do MongoDB falhou."
    }
} finally {
    Pop-Location
}
