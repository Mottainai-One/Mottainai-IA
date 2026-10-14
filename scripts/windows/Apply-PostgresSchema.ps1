[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Container })]
    [string]$DatabaseRepository,
    [switch]$FreshDatabase,
    [ValidateRange(10, 300)]
    [int]$TimeoutSeconds = 90
)

. (Join-Path $PSScriptRoot "Windows.Common.ps1")

if (-not $FreshDatabase) {
    throw "Por segurança, informe -FreshDatabase. Este executor aceita schema somente para um banco local novo e descartável."
}

$repositoryRoot = (Resolve-Path -LiteralPath $DatabaseRepository).Path
$source = Get-Content (Join-Path $script:ProjectRoot "scripts/sql/operational-source.json") -Raw | ConvertFrom-Json
$referenceCommit = (& git -C $repositoryRoot rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $referenceCommit -ne $source.commit) {
    throw "O checkout do banco deve corresponder ao commit oficial registrado em operational-source.json."
}
$officialDirectory = Join-Path $repositoryRoot "database/operational"
$officialInstaller = Join-Path $officialDirectory "install.sql"
if (-not (Test-Path -LiteralPath $officialInstaller -PathType Leaf)) {
    throw "Instalador oficial database/operational/install.sql não encontrado."
}
if ((& git -C $repositoryRoot status --porcelain -- database/operational | Out-String).Trim()) {
    throw "Os scripts oficiais têm alterações locais; instalação recusada."
}

$postgresUser = Get-MottainaiWindowsEnvValue -Name "POSTGRES_USER"
$postgresDatabase = Get-MottainaiWindowsEnvValue -Name "POSTGRES_DB"
if (-not $PSCmdlet.ShouldProcess("$postgresDatabase no container mottainai-windows-postgres", "Aplicar schema de $officialInstaller")) {
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

# psql resolves the official installer's relative includes inside this directory.
& docker cp $officialDirectory "mottainai-windows-postgres:/tmp/mottainai-official-contract"
if ($LASTEXITCODE -ne 0) { throw "Não foi possível copiar o instalador oficial." }
& docker exec --user postgres mottainai-windows-postgres psql --set ON_ERROR_STOP=1 --username $postgresUser --dbname $postgresDatabase --file /tmp/mottainai-official-contract/install.sql
if ($LASTEXITCODE -ne 0) {
    throw "A instalação oficial falhou. O banco interrompeu a transação."
}
Write-Host "Instalador oficial concluído. Valide com Test-PostgresSchema.ps1."
