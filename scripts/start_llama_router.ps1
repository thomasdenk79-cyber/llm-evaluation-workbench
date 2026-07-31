[CmdletBinding()]
param(
    [string]$Server = "$env:USERPROFILE\llama.cpp\bin\llama-server.exe",
    [string]$Preset = "$env:USERPROFILE\llama.cpp\models\router-models.ini",
    [int]$Port = 8080,
    [int]$ModelsMax = 1
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Server -PathType Leaf)) {
    throw "llama-server.exe not found: $Server"
}
if (-not (Test-Path -LiteralPath $Preset -PathType Leaf)) {
    throw "llama.cpp router preset not found: $Preset"
}

$listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if ($listeners) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/models" -TimeoutSec 5 | Out-Null
        exit 0
    } catch {
        throw "Port $Port is already used by a non-compatible process."
    }
}

$logDirectory = Join-Path $env:LOCALAPPDATA "llama.cpp"
$logPath = Join-Path $logDirectory "router.log"
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

$arguments = @(
    "--models-preset", $Preset,
    "--models-max", $ModelsMax,
    "--host", "127.0.0.1",
    "--port", $Port
)

"[$(Get-Date -Format o)] Starting llama.cpp router" | Add-Content -LiteralPath $logPath
& $Server @arguments *>> $logPath
exit $LASTEXITCODE
