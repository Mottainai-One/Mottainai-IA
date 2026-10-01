param(
    [ValidateRange(16, 128)]
    [int]$Length = 32
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-LocalSecret {
    param(
        [Parameter(Mandatory)]
        [int]$SecretLength
    )

    $alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    $bytes = New-Object byte[] $SecretLength
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($bytes)
        $builder = New-Object -TypeName System.Text.StringBuilder -ArgumentList $SecretLength
        foreach ($byte in $bytes) {
            $index = ([int]$byte) -band 63
            [void]$builder.Append($alphabet[$index])
        }
        return $builder.ToString()
    } finally {
        $random.Dispose()
    }
}

Write-Output "JWT_SECRET=$(New-LocalSecret -SecretLength $Length)"
Write-Output "POSTGRES_PASSWORD=$(New-LocalSecret -SecretLength $Length)"
Write-Output "MONGO_INITDB_ROOT_PASSWORD=$(New-LocalSecret -SecretLength $Length)"
Write-Output "REDIS_PASSWORD=$(New-LocalSecret -SecretLength $Length)"
Write-Output "MCP_SHARED_TOKEN=$(New-LocalSecret -SecretLength $Length)"
Write-Output "A2A_SHARED_TOKEN=$(New-LocalSecret -SecretLength $Length)"
