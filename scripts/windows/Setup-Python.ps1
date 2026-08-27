Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

function Invoke-HostPython {
    param(
        [Parameter(Mandatory)]
        [string[]]$PythonArguments
    )

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.13 @PythonArguments
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python @PythonArguments
    } else {
        throw "Python 3.13 não foi encontrado. Instale-o e adicione-o ao PATH."
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.13 não conseguiu executar: $($PythonArguments -join ' ')."
    }
}

$version = if (Get-Command py -ErrorAction SilentlyContinue) {
    (& py -3.13 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" | Out-String).Trim()
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" | Out-String).Trim()
} else {
    throw "Python 3.13 não foi encontrado. Instale-o e adicione-o ao PATH."
}

if ($LASTEXITCODE -ne 0 -or $version -ne "3.13") {
    throw "O projeto exige Python 3.13; versão encontrada: $version."
}

Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Invoke-HostPython -PythonArguments @("-m", "venv", ".venv")
    }

    $venvVersion = (& $venvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $venvVersion -ne "3.13") {
        throw ".venv existente não usa Python 3.13. Recrie-a manualmente somente se ela for descartável."
    }

    & $venvPython -m pip install --requirement requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao instalar as dependências Python."
    }
} finally {
    Pop-Location
}

Write-Host "Ambiente Python preparado em .venv."
