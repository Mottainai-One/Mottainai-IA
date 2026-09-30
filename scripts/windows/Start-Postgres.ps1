param(
    [ValidateRange(10, 300)]
    [int]$TimeoutSeconds = 90
)

. (Join-Path $PSScriptRoot "Windows.Common.ps1")
Start-MottainaiDependency -Service "postgres" -ContainerName "mottainai-windows-postgres" -TimeoutSeconds $TimeoutSeconds
