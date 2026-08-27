. (Join-Path $PSScriptRoot "Windows.Common.ps1")

$venvPython = Get-MottainaiVenvPython
$previousHubOffline = $env:HF_HUB_OFFLINE
$previousTransformersOffline = $env:TRANSFORMERS_OFFLINE
$pythonCode = @'
from sentence_transformers import SentenceTransformer

SentenceTransformer("all-MiniLM-L6-v2", local_files_only=False)
SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
print("Modelo de embeddings pronto para uso offline.")
'@

try {
    $env:HF_HUB_OFFLINE = "0"
    $env:TRANSFORMERS_OFFLINE = "0"
    Push-Location $script:ProjectRoot
    & $venvPython -c $pythonCode
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao preparar o modelo all-MiniLM-L6-v2."
    }
} finally {
    Pop-Location

    if ($null -eq $previousHubOffline) {
        Remove-Item Env:HF_HUB_OFFLINE -ErrorAction SilentlyContinue
    } else {
        $env:HF_HUB_OFFLINE = $previousHubOffline
    }

    if ($null -eq $previousTransformersOffline) {
        Remove-Item Env:TRANSFORMERS_OFFLINE -ErrorAction SilentlyContinue
    } else {
        $env:TRANSFORMERS_OFFLINE = $previousTransformersOffline
    }
}
