Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:ComposeFile = Join-Path $script:ProjectRoot "docker-compose.windows.yml"
$script:WindowsEnvFile = Join-Path $script:ProjectRoot ".env.windows"

function Get-MottainaiWindowsEnvValue {
    param(
        [Parameter(Mandatory)]
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $script:WindowsEnvFile)) {
        throw "Crie .env.windows a partir de .env.windows.example antes de usar os serviços."
    }

    $content = Get-Content -LiteralPath $script:WindowsEnvFile -Raw
    $match = [regex]::Match(
        $content,
        "(?m)^\s*$([regex]::Escape($Name))\s*=\s*(?<value>[^\r\n#]+)"
    )
    if (-not $match.Success) {
        throw "Defina $Name em .env.windows antes de usar os serviços."
    }

    return $match.Groups["value"].Value.Trim()
}

function Assert-MottainaiDocker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker Desktop não foi encontrado. Instale-o, inicie-o e tente novamente."
    }

    & docker version --format "{{.Server.Version}}" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "O Docker Desktop não está pronto. Abra o Docker Desktop e aguarde o engine iniciar."
    }
}

function Assert-MottainaiWindowsEnv {
    if (-not (Test-Path -LiteralPath $script:ComposeFile)) {
        throw "docker-compose.windows.yml não foi encontrado no projeto."
    }

    if (-not (Test-Path -LiteralPath $script:WindowsEnvFile)) {
        throw "Crie .env.windows a partir de .env.windows.example antes de iniciar os serviços."
    }

    foreach ($name in @(
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "MONGO_INITDB_ROOT_USERNAME",
        "MONGO_INITDB_ROOT_PASSWORD",
        "REDIS_PASSWORD"
    )) {
        $value = Get-MottainaiWindowsEnvValue -Name $name
        if ([string]::IsNullOrWhiteSpace($value) -or $value -like "CHANGE_ME*") {
            throw "Defina $name em .env.windows antes de iniciar os serviços."
        }

        if ($name -like "*PASSWORD" -and ($value.Length -lt 16 -or $value -notmatch "^[A-Za-z0-9_-]+$")) {
            throw "$name deve ter ao menos 16 caracteres e usar somente letras, números, underscore e hífen."
        }
    }
}

function Invoke-MottainaiCompose {
    param(
        [Parameter(Mandatory)]
        [string[]]$ComposeArguments
    )

    & docker compose --env-file $script:WindowsEnvFile --file $script:ComposeFile @ComposeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "O Docker Compose falhou ao executar: $($ComposeArguments -join ' ')."
    }
}

function Wait-MottainaiContainerHealthy {
    param(
        [Parameter(Mandatory)]
        [string]$ContainerName,
        [ValidateRange(10, 300)]
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $format = "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}"

    while ((Get-Date) -lt $deadline) {
        $status = (& docker inspect --format $format $ContainerName 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -eq 0) {
            if ($status -eq "healthy") {
                return
            }

            if ($status -eq "unhealthy") {
                throw "$ContainerName ficou unhealthy. Consulte: docker logs $ContainerName"
            }
        }

        Start-Sleep -Seconds 2
    }

    throw "$ContainerName não ficou saudável em $TimeoutSeconds segundos. Consulte: docker logs $ContainerName"
}

function Start-MottainaiDependency {
    param(
        [Parameter(Mandatory)]
        [ValidateSet("postgres", "mongo", "redis")]
        [string]$Service,
        [Parameter(Mandatory)]
        [string]$ContainerName,
        [ValidateRange(10, 300)]
        [int]$TimeoutSeconds = 90
    )

    Assert-MottainaiDocker
    Assert-MottainaiWindowsEnv
    Invoke-MottainaiCompose -ComposeArguments @("up", "--detach", $Service)
    Wait-MottainaiContainerHealthy -ContainerName $ContainerName -TimeoutSeconds $TimeoutSeconds
    Write-Host "$Service está saudável."
}

function Get-MottainaiVenvPython {
    $python = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Ambiente virtual não encontrado. Execute .\scripts\windows\Setup-Python.ps1 primeiro."
    }

    return $python
}
