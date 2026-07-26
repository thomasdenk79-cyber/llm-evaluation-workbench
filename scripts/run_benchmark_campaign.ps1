param(
    [ValidateSet("ollama", "llama_cpp", "both")]
    [string]$Backend = "both",
    [string]$LlamaServer = "",
    [string[]]$LlamaModel = @(),
    [int]$Runs = 1,
    [ValidateSet("balanced", "high", "both")]
    [string]$PowerProfile = "both",
    [int]$Threads = [Math]::Max([Environment]::ProcessorCount - 2, 1),
    [int]$CtxSize = 2048,
    [int]$MaxTokens = 220,
    [int]$Ngl = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Set-PowerProfile([string]$Profile) {
    if ($Profile -eq "balanced") {
        & powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e | Out-Null
        return
    }
    & powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c | Out-Null
}

$scriptRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$benchmark = Join-Path $scriptRoot "llm_migration_benchmark.py"
if (-not (Test-Path $benchmark)) {
    throw "Benchmark script not found: $benchmark"
}

$profiles = if ($PowerProfile -eq "both") { @("balanced", "high") } else { @($PowerProfile) }

foreach ($profile in $profiles) {
    Write-Host "=== Running profile: $profile ==="
    Set-PowerProfile -Profile $profile

    $args = @(
        $benchmark,
        "--backend", $Backend,
        "--runs", $Runs,
        "--threads", $Threads,
        "--ctx-size", $CtxSize,
        "--max-tokens", $MaxTokens,
        "--ngl", $Ngl,
        "--output-dir", (Join-Path $scriptRoot "..\benchmark_results\$profile")
    )

    if ($LlamaServer) {
        $args += @("--llama-server", $LlamaServer)
    }
    foreach ($modelSpec in $LlamaModel) {
        $args += @("--llama-model", $modelSpec)
    }

    & python @args
    if ($LASTEXITCODE -ne 0) {
        throw "Benchmark run failed for profile '$profile' with exit code $LASTEXITCODE"
    }
}

Write-Host "Campaign completed."
