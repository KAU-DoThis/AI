param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$InternalToken = $env:FINTRIS_INTERNAL_TOKEN
)
$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($InternalToken)) {
    throw "FINTRIS_INTERNAL_TOKEN 환경변수 또는 -InternalToken 인자가 필요합니다."
}
# 재시도는 원래 JSON 그대로 사용. 새 메시지마다 새 requestId를 만듭니다.
$payload = Get-Content -Raw -Encoding UTF8 (Join-Path $PSScriptRoot "coach_chat_request.json") | ConvertFrom-Json
$payload.requestId = [guid]::NewGuid().ToString()
$body = [System.Text.Encoding]::UTF8.GetBytes(($payload | ConvertTo-Json -Depth 10))
Invoke-RestMethod -Method Post -Uri ($BaseUrl.TrimEnd('/') + "/internal/ai/coach/chat") `
    -Headers @{"X-Internal-Token" = $InternalToken} `
    -ContentType "application/json; charset=utf-8" -Body $body | ConvertTo-Json -Depth 10
