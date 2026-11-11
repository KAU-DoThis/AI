param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($env:FINTRIS_INTERNAL_TOKEN)) {
    throw "Set FINTRIS_INTERNAL_TOKEN in this shell. The server .env is not automatically loaded into PowerShell."
}
$body = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot "chat_transfer_request.json")
Invoke-RestMethod `
    -Method Post `
    -Uri ($BaseUrl.TrimEnd('/') + "/internal/ai/coach/chat") `
    -Headers @{ "X-Internal-Token" = $env:FINTRIS_INTERNAL_TOKEN } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) |
    ConvertTo-Json -Depth 20
