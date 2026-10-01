. (Join-Path $PSScriptRoot "Windows.Common.ps1")

Assert-MottainaiDocker
Assert-MottainaiWindowsEnv
Invoke-MottainaiCompose -ComposeArguments @("ps")
