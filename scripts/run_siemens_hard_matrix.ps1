param(
    [int]$Runs = 2
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
Set-Location $ROOT

$benchmarks = @(
    "swe-sql-hard-24",
    "swe-python-hard-24",
    "swe-mixed-hard-24"
)

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  Siemens hard benchmark matrix ($Runs runs)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

foreach ($bid in $benchmarks) {
    Write-Host ">>> Benchmark: $bid" -ForegroundColor Yellow
    & python .\scripts\llm_migration_benchmark.py `
        --backend siemens `
        --runs $Runs `
        --siemens-workers 5 `
        --benchmark-id $bid `
        --output-dir benchmark_results `
        --report-file docs\project\benchmark_report.md

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Benchmark failed: $bid (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "Matrix complete." -ForegroundColor Green
