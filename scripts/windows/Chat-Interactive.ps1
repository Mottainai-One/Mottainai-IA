param(
    [ValidateSet("CLIENTE", "ESTOQUISTA", "GERENTE", "DONO")]
    [string]$Role = "CLIENTE",
    [int]$UsuarioId = 101,
    [int]$EmpresaId = 1,
    [int]$ApiPort = 8001
)

chcp 65001 | Out-Null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

Write-Host "Gerando token de desenvolvimento (role=$Role)..."
$token = & $venvPython (Join-Path $projectRoot "scripts\generate_dev_token.py") --usuario-id $UsuarioId --empresa-id $EmpresaId --role $Role
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($token)) {
    throw "Não foi possível gerar o token. A API está rodando e o .env está configurado?"
}

$sessionId = [guid]::NewGuid().ToString()
$headers = @{ Authorization = "Bearer $token" }

Write-Host ""
Write-Host "Conectado como $Role (sessao $sessionId)."
Write-Host "Digite sua mensagem e aperte Enter. Digite 'sair' para encerrar."
Write-Host ""

while ($true) {
    $msg = Read-Host "Voce"
    if ([string]::IsNullOrWhiteSpace($msg) -or $msg -eq "sair") { break }

    $body = @{ message = $msg; session_id = $sessionId } | ConvertTo-Json

    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/chat" -Method Post -Headers $headers -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
        Write-Host "IA ($($resp.agent), score $($resp.judge_score)): $($resp.response)"
    } catch {
        Write-Host "Erro: $($_.Exception.Message)"
    }
    Write-Host ""
}

Write-Host "Sessao encerrada."
