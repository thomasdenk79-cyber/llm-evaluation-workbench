[CmdletBinding()]
param(
    [switch]$StartNow,
    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$contextScript = Join-Path $PSScriptRoot "configure_ollama_max_context.py"
$syncScript = Join-Path $PSScriptRoot "sync_opencode_local_models.py"
$routerScript = Join-Path $PSScriptRoot "start_llama_router.ps1"
$server = "$env:USERPROFILE\llama.cpp\bin\llama-server.exe"

[Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KV_CACHE_TYPE", "q4_0", "User")

$ollamaListeners = @(Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue)
if ($ollamaListeners) {
    & python $contextScript
    if ($LASTEXITCODE -ne 0) {
        throw "Ollama maximum-context configuration failed with exit code $LASTEXITCODE."
    }
} else {
    Write-Warning "Ollama is unavailable; maximum contexts will be configured on the next run."
}

& python $syncScript
if ($LASTEXITCODE -ne 0) {
    throw "Local model synchronization failed with exit code $LASTEXITCODE."
}

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "llama.cpp Router.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$powerShell = (Get-Process -Id $PID).Path
$shortcut.TargetPath = $powerShell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$routerScript`""
$shortcut.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$shortcut.Description = "llama.cpp multi-model router for OpenCode"
$shortcut.Save()

Write-Host "Startup shortcut: $shortcutPath"
if ($Restart) {
    $StartNow = $true
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $process = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
        if ([IO.Path]::GetFullPath($process.Path) -ne [IO.Path]::GetFullPath($server)) {
            throw "Port 8080 belongs to $($process.Path), not the configured llama-server."
        }
        Stop-Process -Id $process.Id
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue)) {
            break
        }
        Start-Sleep -Milliseconds 250
    }
}

if ($StartNow) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue)
    if (-not $listeners) {
        Start-Process -FilePath $powerShell -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden",
            "-File", "`"$routerScript`""
        ) -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
    }
}
