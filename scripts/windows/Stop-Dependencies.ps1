. (Join-Path $PSScriptRoot "Windows.Common.ps1")

Assert-MottainaiDocker
Assert-MottainaiWindowsEnv
Invoke-MottainaiCompose -ComposeArguments @("stop")
Write-Host "Serviços parados. Containers e volumes foram preservados."
