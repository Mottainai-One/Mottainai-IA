[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$SqlFile,
    [switch]$FreshDatabase,
    [ValidateRange(10, 300)]
    [int]$TimeoutSeconds = 90
)

. (Join-Path $PSScriptRoot "Windows.Common.ps1")

if (-not $FreshDatabase) {
    throw "Por segurança, informe -FreshDatabase. Este executor aceita schema somente para um banco local novo e descartável."
}

$resolvedSqlFile = (Resolve-Path -LiteralPath $SqlFile).Path
$sql = Get-Content -LiteralPath $resolvedSqlFile -Raw -Encoding utf8
if ([string]::IsNullOrWhiteSpace($sql)) {
    throw "O arquivo SQL informado está vazio."
}
if ($sql -notmatch "(?im)^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:SCHEMA|TABLE|EXTENSION)\b") {
    throw "O arquivo não parece ser um schema PostgreSQL. Revise a origem antes de aplicá-lo."
}

$postgresUser = Get-MottainaiWindowsEnvValue -Name "POSTGRES_USER"
$postgresDatabase = Get-MottainaiWindowsEnvValue -Name "POSTGRES_DB"
if (-not $PSCmdlet.ShouldProcess("$postgresDatabase no container mottainai-windows-postgres", "Aplicar schema de $resolvedSqlFile")) {
    return
}

Start-MottainaiDependency -Service "postgres" -ContainerName "mottainai-windows-postgres" -TimeoutSeconds $TimeoutSeconds

$inspectArguments = @(
    "exec", "--user", "postgres", "mottainai-windows-postgres", "psql",
    "--tuples-only", "--no-align",
    "--username", $postgresUser,
    "--dbname", $postgresDatabase,
    "--command", "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog', 'information_schema'));"
)
$hasUserTables = (& docker @inspectArguments | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Não foi possível verificar se o banco PostgreSQL está vazio."
}
if ($hasUserTables -ne "f") {
    throw "O banco informado já possui tabelas de usuário. O schema não será aplicado para evitar sobrescrever dados."
}

$applyArguments = @(
    "exec", "--interactive", "--user", "postgres", "mottainai-windows-postgres", "psql",
    "--single-transaction", "--set", "ON_ERROR_STOP=1",
    "--username", $postgresUser,
    "--dbname", $postgresDatabase
)
Get-Content -LiteralPath $resolvedSqlFile -Raw -Encoding utf8 | & docker @applyArguments
if ($LASTEXITCODE -ne 0) {
    throw "A aplicação do schema falhou. O PostgreSQL interrompeu no primeiro erro."
}

Write-Host "Schema aplicado. Agora valide com .\scripts\windows\Test-PostgresSchema.ps1"
