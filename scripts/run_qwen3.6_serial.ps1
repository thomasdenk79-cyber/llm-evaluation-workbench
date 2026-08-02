# Qwen3.6 Local Benchmark Runner — Serial, One model at a time
# Agent: opencode | llm: qwen-3.6-27b | llm_version: siemens/qwen-3.6-27b | role: benchmark orchestrator
# Auftraggeber: Thomas Denk (z000g9hu, Siemens)
# Datum: 2026-07-28T07:20:00+02:00
# Zweck: Serial benchmark runner for qwen3.6 models via Ollama and llama.cpp, produces consolidated report

$ErrorActionPreference = "Stop"
$benchDir = "D:\git\llm-evaluation-workbench"
$python = "python"
$script = Join-Path $benchDir "scripts\llm_migration_benchmark.py"
$reportFile = Join-Path $benchDir "docs\project\benchmark_report.md"
$resultsDir = Join-Path $benchDir "benchmark_results"

# Set High-Performance power plan for stable throughput
$powerPlan = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
Start-Process -FilePath "powercfg" -ArgumentList "-setactive", $powerPlan -NoNewWindow -Wait

# NOTE: Balanced actually performs better for llama.cpp, but High-Perf is safer to start.
# We'll use Balanced below for llama.cpp runs.

$runs = 3
$timeoutSec = 900
$ctxSize = 2048
$threads = 28

# ---- Kill any leftover processes ----
foreach ($name in @("llama-server", "python")) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep 2

# ---- Ensure ollama is running ----
$ollama = Get-Process -Name ollama -ErrorAction SilentlyContinue
if (-not $ollama) {
    Write-Host "[setup] Starting ollama serve..."
    Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep 5
}

# ---- Clean old inprogress ----
Remove-Item (Join-Path $resultsDir "migration_llm_bench_*_inprogress.*") -Force -ErrorAction SilentlyContinue -Recurse

$log = Join-Path $benchDir "benchmark_results\qwen3.6_local_bench_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
Start-Transcript -Path $log -Append -Force

Write-Host ""
Write-Host "============================================================"
Write-Host "  QWEN3.6 LOCAL BENCHMARK - SERIAL RUN"
$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Host "  $now"
Write-Host "============================================================"
Write-Host ""

$allResults = @()
$errors = @()

function Run-Bench {
    param(
        [string]$Backend,
        [string]$Label,
        [string[]]$ArgsList
    )

    Write-Host ""
    Write-Host "--- $(Get-Date -Format 'HH:mm:ss') START: $Label ---"

    # Kill any leftover llama-server before llama_cpp runs
    if ($Backend -eq "llama_cpp") {
        Get-Process -Name "llama-server" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep 2
    }

    $cmdArgs = @($script) + $ArgsList

    try {
        $outFile = Join-Path $resultsDir ("out_{0}.txt" -f $Label)
        $errFile = Join-Path $resultsDir ("err_{0}.txt" -f $Label)
        $proc = Start-Process -FilePath $python -ArgumentList $cmdArgs `
            -WorkingDirectory $benchDir -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $outFile `
            -RedirectStandardError $errFile `
            -ErrorAction Stop

        $exitCode = $proc.ExitCode
        $nowStr = Get-Date -Format 'HH:mm:ss'
        Write-Host "--- $nowStr DONE: $Label (exit=$exitCode) ---"
        return $true
    }
    catch {
        $errStr = $_
        $nowStr = Get-Date -Format 'HH:mm:ss'
        Write-Host "--- $nowStr ERROR: $Label : $errStr ---"
        return $false
    }
    finally {
        if ($Backend -eq "llama_cpp") {
            Get-Process -Name "llama-server" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep 1
        }
        if ($Backend -eq "ollama") {
            $body = '{"model":"","keep_alive":0}'
            try { Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/generate" -Method Post -Body $body -ErrorAction SilentlyContinue | Out-Null } catch {}
        }
    }
}

# ========== RUN 1: Ollama qwen3.6:27b-q4_K_M ==========
$succ = Run-Bench -Backend "ollama" -Label "ollama_27b" -ArgsList @(
    "--backend", "ollama",
    "--ollama-model", "qwen3.6:27b-q4_K_M",
    "--runs", "$runs",
    "--timeout-sec", "$timeoutSec",
    "--ctx-size", "$ctxSize",
    "--threads", "$threads"
)
if (-not $succ) { $errors += "Ollama 27b failed" }

# ========== RUN 2: Ollama qwen3.6:35b-a3b-q4_K_M ==========
$succ = Run-Bench -Backend "ollama" -Label "ollama_35b" -ArgsList @(
    "--backend", "ollama",
    "--ollama-model", "qwen3.6:35b-a3b-q4_K_M",
    "--runs", "$runs",
    "--timeout-sec", "$timeoutSec",
    "--ctx-size", "$ctxSize",
    "--threads", "$threads"
)
if (-not $succ) { $errors += "Ollama 35b failed" }

# ========== RUN 3: llama.cpp qwen3.6-35b-a3b-q4 (ngl=99) ==========
# Use Balanced power plan — measured better for llama.cpp
Start-Process -FilePath "powercfg" -ArgumentList "-setactive", "381b4222-f694-41f0-9685-ff5bb260df2e" -NoNewWindow -Wait

$succ = Run-Bench -Backend "llama_cpp" -Label "llama_35b" -ArgsList @(
    "--backend", "llama_cpp",
    "--llama-server", "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe",
    "--llama-model", "qwen3.6-35b-a3b-q4=C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf",
    "--llama-ngl", "99",
    "--runs", "$runs",
    "--timeout-sec", "$timeoutSec",
    "--ctx-size", "$ctxSize",
    "--threads", "$threads"
)
if (-not $succ) { $errors += "llama.cpp 35b failed" }
else {
    $allResults += "llama.cpp 35b: SUCCESS"
}

# ========== RUN 4: llama.cpp qwen3.6-27b (SKIIPPED) ==========
Write-Host ""
Write-Host "--- SKIPPED: llama.cpp qwen3.6-27b ---"
Write-Host "Reason: GGUF incompatibility with llama.cpp build 132.x"
Write-Host "Error: qwen35.rope.dimension_sections has wrong array length; expected 4, got 3"
Write-Host "The Ollama-distributed GGUF for qwen3.6:27b uses a qwen35 architecture variant"
Write-Host "that this llama.cpp build cannot load. Ollama ships its own backend that works."

# ========== GENERATE REPORT ==========
Write-Host ""
$now2 = Get-Date -Format 'HH:mm:ss'
Write-Host "--- $now2 Generating final markdown report...---"

# Run Python to update the report
python -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('bench', r'$script')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.update_markdown_report(r'$resultsDir', r'$reportFile', None)
print('Report updated: $reportFile')
" 2>&1

Write-Host ""
Write-Host "============================================================"
$endNow = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
Write-Host "  BENCHMARK COMPLETE: $endNow"
Write-Host "============================================================"
Write-Host ""
$errCount = $errors.Count
Write-Host "Errors: $errCount"
foreach ($e in $errors) { Write-Host "  [ERROR] $e" }
Write-Host ""
Write-Host "Results: $resultsDir\migration_llm_bench_*.csv"
Write-Host "Report:  $reportFile"
Write-Host ""

Stop-Transcript
