[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [ValidateRange(10, 300)]
    [int]$TimeoutSeconds = 90
)

. (Join-Path $PSScriptRoot "Windows.Common.ps1")

# Massa de desenvolvimento oficial (PR #11). O script dataLoad APAGA os dados
# transacionais existentes antes de recarregar. Por isso ele só roda com
# confirmação explícita e nunca deve apontar para um banco com dados reais.
$dataLoadFile = Join-Path $script:ProjectRoot "scripts\sql\mottainai-v6.dataload.sql"
if (-not (Test-Path -LiteralPath $dataLoadFile)) {
    throw "Arquivo de carga não encontrado: $dataLoadFile"
}

$postgresUser = Get-MottainaiWindowsEnvValue -Name "POSTGRES_USER"
$postgresDatabase = Get-MottainaiWindowsEnvValue -Name "POSTGRES_DB"

Write-Warning "Esta carga APAGA os dados transacionais do banco '$postgresDatabase' e insere a massa demo oficial."
if (-not $PSCmdlet.ShouldProcess("$postgresDatabase no container mottainai-windows-postgres", "Recarregar massa de desenvolvimento (dataLoad)")) {
    return
}

Start-MottainaiDependency -Service "postgres" -ContainerName "mottainai-windows-postgres" -TimeoutSeconds $TimeoutSeconds

# Exige que o schema v6 já esteja aplicado antes da carga.
$schemaCheckArguments = @(
    "exec", "--user", "postgres", "mottainai-windows-postgres", "psql",
    "--tuples-only", "--no-align",
    "--username", $postgresUser,
    "--dbname", $postgresDatabase,
    "--command", "SELECT to_regclass('mottainai.sale_item') IS NOT NULL AND to_regclass('mottainai.promotion') IS NOT NULL;"
)
$schemaReady = (& docker @schemaCheckArguments | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $schemaReady -ne "t") {
    throw "O schema v6 não está aplicado neste banco. Execute Apply-PostgresSchema.ps1 primeiro."
}

# O dataLoad controla a própria transação (BEGIN/COMMIT internos);
# não use --single-transaction aqui.
$applyArguments = @(
    "exec", "--interactive", "--user", "postgres", "mottainai-windows-postgres", "psql",
    "--set", "ON_ERROR_STOP=1",
    "--username", $postgresUser,
    "--dbname", $postgresDatabase
)
Get-Content -LiteralPath $dataLoadFile -Raw -Encoding utf8 | & docker @applyArguments
if ($LASTEXITCODE -ne 0) {
    throw "A carga de desenvolvimento falhou. O PostgreSQL interrompeu no primeiro erro."
}

Write-Host "Massa de desenvolvimento aplicada. Valide com .\scripts\windows\Test-PostgresSchema.ps1"
