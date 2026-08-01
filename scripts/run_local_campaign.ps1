<#
.SYNOPSIS
    Wiederaufsetzbare lokale Benchmark-Kampagne (10 Runs).

.DESCRIPTION
    Führt alle lokalen Modell-Runs seriell aus.
    Fortschritt wird in benchmark_results\campaign_local_state.json gespeichert.
    Beim Neustart werden abgeschlossene Runs automatisch übersprungen.

.EXAMPLE
    # Starten / fortsetzen:
    .\scripts\run_local_campaign.ps1

    # Status anzeigen ohne auszuführen:
    .\scripts\run_local_campaign.ps1 -StatusOnly

    # Bestimmten Run überspringen (manuell als done markieren):
    .\scripts\run_local_campaign.ps1 -MarkDone "ollama-gpt-oss-20b-highperf"

    # Kampagne komplett zurücksetzen:
    .\scripts\run_local_campaign.ps1 -Reset
#>
param(
    [switch]$StatusOnly,
    [string]$MarkDone = "",
    [switch]$Reset,
    [int]$WatchdogCheckSec = 60,
    [int]$WatchdogStallMin = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT        = Split-Path -Parent $PSScriptRoot  # repo root
$BENCH       = Join-Path $PSScriptRoot "llm_migration_benchmark.py"
$STATE_FILE  = Join-Path $ROOT "benchmark_results\campaign_local_state.json"
$LLAMA_SRV   = "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe"
$GPT_OSS     = "C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf"
$QWEN35      = "C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf"
$QWEN30      = "C:\Users\z000g9hu\llama.cpp\models\qwen3-coder-30b-from-ollama.gguf"
$DEEPSEEK16  = "C:\Users\z000g9hu\llama.cpp\models\deepseek-coder-v2-16b-from-ollama.gguf"
$QWEN27      = "C:\Users\z000g9hu\llama.cpp\models\qwen3.6-27b-q4_K_M-from-ollama.gguf"
$RUNS        = 3
$BENCHMARK_ID = "ora-pg-py-33"

# ── Power-Profil ────────────────────────────────────────────────────────────
function Set-Power([string]$Profile) {
    if ($Profile -eq "balanced") {
        powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e | Out-Null
    } else {
        powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c | Out-Null
    }
    Write-Host "  Power: $Profile" -ForegroundColor DarkGray
}

# ── Run-Definitionen ────────────────────────────────────────────────────────
# id muss eindeutig und stabil sein (wird als Done-Key gespeichert)
$ALL_RUNS = @(
    # ── Ollama: MoE-Modelle + deepseek (alle relevant für diesen PC) ─────────
    @{
        id      = "ollama-qwen3-coder-30b-highperf"
        label   = "Ollama | qwen3-coder:30b (MoE 3.3B aktiv) | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "qwen3-coder:30b", "--runs", $RUNS)
    },
    @{
        id      = "ollama-deepseek-coder-v2-16b-highperf"
        label   = "Ollama | deepseek-coder-v2:16b (MoE, passt in VRAM) | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "deepseek-coder-v2:16b", "--runs", $RUNS)
    },
    @{
        id      = "ollama-qwen3.6-35b-q4-highperf"
        label   = "Ollama | qwen3.6:35b-a3b-q4_K_M (MoE 3B aktiv) | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "qwen3.6:35b-a3b-q4_K_M", "--runs", $RUNS)
    },
    # ── llama.cpp: gleiche 5 Modelle wie Ollama (optimierte lokale Parameter) ─
    @{
        id      = "llamacpp-qwen3-coder-30b-highperf"
        label   = "llama.cpp | qwen3-coder:30b | highperf | ngl99"
        power   = "highperf"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "qwen3-coder:30b=$QWEN30",
                    "--llama-ngl", "99",
                    "--llama-extra-args", "--mlock --threads 20",
                    "--runs", $RUNS)
    },
    @{
        id      = "llamacpp-deepseek-coder-v2-16b-highperf"
        label   = "llama.cpp | deepseek-coder-v2:16b | highperf | ngl99"
        power   = "highperf"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "deepseek-coder-v2:16b=$DEEPSEEK16",
                    "--llama-ngl", "99",
                    "--llama-extra-args", "--mlock --threads 20",
                    "--runs", $RUNS)
    },
    @{
        id      = "llamacpp-qwen3.6-35b-moe-highperf"
        label   = "llama.cpp | qwen3.6:35b-a3b | Expert-Offload | highperf | ngl99"
        power   = "highperf"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "qwen3.6:35b-a3b=$QWEN35",
                    "--llama-ngl", "99",
                    "--llama-extra-args", "--override-tensor blk\.\.*\.ffn_(up|down|gate)_exps=CPU --mlock --threads 20",
                    "--runs", $RUNS)
    },
    # ── Dense-Modelle ───────────────────────────────────────────────────────────
    @{
        id      = "ollama-gpt-oss-20b-highperf"
        label   = "Ollama | gpt-oss:20b (Dense, Referenz) | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "gpt-oss:20b", "--runs", $RUNS)
    },
    @{
        id      = "llamacpp-gpt-oss-20b-highperf"
        label   = "llama.cpp | gpt-oss:20b (Dense, Referenz) | highperf | ngl99"
        power   = "highperf"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "gpt-oss:20b=$GPT_OSS",
                    "--llama-ngl", "99",
                    "--llama-extra-args", "--mlock --threads 20",
                    "--runs", $RUNS)
    },
    @{
        id      = "llamacpp-qwen3.6-27b-q4-highperf"
        label   = "llama.cpp | qwen3.6:27b-q4_K_M (Dense, Referenz) | highperf | ngl99"
        power   = "highperf"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "qwen3.6:27b-q4_K_M=$QWEN27",
                    "--llama-ngl", "99",
                    "--llama-extra-args", "--mlock --threads 20",
                    "--runs", $RUNS)
    },
    @{
        id      = "ollama-qwen3.6-27b-q4-highperf"
        label   = "Ollama | qwen3.6:27b-q4_K_M (Dense, Referenz) | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "qwen3.6:27b-q4_K_M", "--runs", $RUNS)
    }
)

# ── State laden / initialisieren ─────────────────────────────────────────────
function Load-State {
    if (Test-Path $STATE_FILE) {
        return (Get-Content $STATE_FILE -Raw | ConvertFrom-Json).done
    }
    return @()
}

function Save-State([string[]]$done) {
    $null = New-Item -ItemType Directory -Force -Path (Split-Path $STATE_FILE)
    @{ done = $done; updated = (Get-Date -Format "yyyy-MM-dd HH:mm") } | ConvertTo-Json | Set-Content $STATE_FILE
}

function Get-LatestInProgressSnapshot([string]$resultsDir) {
    $f = Get-ChildItem -Path $resultsDir -Filter "migration_llm_bench_*_inprogress.csv" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if (-not $f) { return $null }
    return [pscustomobject]@{
        Path          = $f.FullName
        LastWriteUtc  = $f.LastWriteTimeUtc
        Length        = [int64]$f.Length
        Signature     = "{0}|{1}|{2}" -f $f.FullName, $f.LastWriteTimeUtc.Ticks, $f.Length
    }
}

function Stop-ProcessTree([int]$RootPid) {
    $all = New-Object System.Collections.Generic.List[int]
    $q = New-Object System.Collections.Generic.Queue[int]
    $q.Enqueue($RootPid)
    while ($q.Count -gt 0) {
        $pid = $q.Dequeue()
        if ($all -contains $pid) { continue }
        [void]$all.Add($pid)
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $pid" -ErrorAction SilentlyContinue
        foreach ($c in $children) {
            $q.Enqueue([int]$c.ProcessId)
        }
    }
    foreach ($pid in ($all | Sort-Object -Descending)) {
        try {
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host "  Watchdog: stopped PID $pid" -ForegroundColor Yellow
        } catch {
            # process may already be gone
        }
    }
}

function Invoke-BenchmarkRunWithWatchdog(
    [string[]]$RunArgs,
    [string]$BenchmarkId,
    [int]$CheckSec,
    [int]$StallMin
) {
    $argsList = @($BENCH) + @($RunArgs) + @("--benchmark-id", $BenchmarkId)
    $proc = Start-Process -FilePath "python" -ArgumentList $argsList -WorkingDirectory $ROOT -NoNewWindow -PassThru
    Write-Host "  Watchdog: monitoring PID $($proc.Id) (check ${CheckSec}s, stall ${StallMin}m)" -ForegroundColor DarkGray

    $resultsDir = Join-Path $ROOT "benchmark_results"
    $lastSignature = ""
    $lastProgressAt = Get-Date
    $nextCheck = (Get-Date).AddSeconds([Math]::Max($CheckSec, 10))

    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 2
        if ((Get-Date) -lt $nextCheck) { continue }
        $nextCheck = (Get-Date).AddSeconds([Math]::Max($CheckSec, 10))

        $snap = Get-LatestInProgressSnapshot -resultsDir $resultsDir
        if ($snap -and $snap.Signature -ne $lastSignature) {
            $lastSignature = $snap.Signature
            $lastProgressAt = Get-Date
            Write-Host ("  Watchdog: progress at {0:HH:mm:ss} ({1})" -f (Get-Date), (Split-Path $snap.Path -Leaf)) -ForegroundColor DarkGray
        }

        $idle = ((Get-Date) - $lastProgressAt).TotalMinutes
        if ($idle -ge [Math]::Max($StallMin, 1)) {
            Write-Host ("  Watchdog: no progress for {0:N1} min -> aborting stuck run." -f $idle) -ForegroundColor Red
            Stop-ProcessTree -RootPid $proc.Id
            return 124
        }
    }

    $proc.WaitForExit()
    return $proc.ExitCode
}

# ── Sondermodi ──────────────────────────────────────────────────────────────
if ($Reset) {
    if (Test-Path $STATE_FILE) { Remove-Item $STATE_FILE }
    Write-Host "State zurückgesetzt. Nächster Start beginnt von vorne." -ForegroundColor Yellow
    exit 0
}

$done = @(Load-State)

if ($MarkDone) {
    if ($ALL_RUNS.id -notcontains $MarkDone) {
        Write-Host "Unbekannte Run-ID: $MarkDone" -ForegroundColor Red
        Write-Host "Gültige IDs:" ; $ALL_RUNS | ForEach-Object { Write-Host "  $($_.id)" }
        exit 1
    }
    if ($done -notcontains $MarkDone) { $done += $MarkDone }
    Save-State $done
    Write-Host "Markiert als done: $MarkDone" -ForegroundColor Green
    exit 0
}

# ── Status-Anzeige ──────────────────────────────────────────────────────────
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  Local benchmark campaign - benchmark: $BENCHMARK_ID | 3 runs" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host ""

$pending = 0
foreach ($run in $ALL_RUNS) {
    if ($done -contains $run.id) {
        Write-Host "  DONE   $($run.label)" -ForegroundColor Green
    } else {
        Write-Host "  OPEN   $($run.label)" -ForegroundColor Yellow
        $pending++
    }
}
Write-Host ""
Write-Host "  $($ALL_RUNS.Count - $pending)/$($ALL_RUNS.Count) abgeschlossen, $pending noch offen." -ForegroundColor Cyan
Write-Host ""

if ($StatusOnly) { exit 0 }
if ($pending -eq 0) {
    Write-Host "Alle Runs abgeschlossen!" -ForegroundColor Green
    exit 0
}

Write-Host "Starte in 3 Sekunden... (Ctrl+C zum Abbrechen)" -ForegroundColor DarkGray
Start-Sleep 3

# ── Haupt-Schleife ───────────────────────────────────────────────────────────
$startTime = Get-Date
$completed = 0

foreach ($run in $ALL_RUNS) {
    if ($done -contains $run.id) { continue }

    Write-Host ""
    Write-Host "----------------------------------------------------------" -ForegroundColor Cyan
    Write-Host "  ▶ $($run.label)" -ForegroundColor White
    $remaining = $pending - $completed
    Write-Host "  Noch $remaining Run(s) nach diesem | Start: $(Get-Date -Format 'HH:mm')" -ForegroundColor DarkGray
    Write-Host "----------------------------------------------------------" -ForegroundColor Cyan

    Set-Power $run.power
    Set-Location $ROOT

    $exitCode = 0
    try {
        $exitCode = Invoke-BenchmarkRunWithWatchdog -RunArgs @($run.args) -BenchmarkId $BENCHMARK_ID -CheckSec $WatchdogCheckSec -StallMin $WatchdogStallMin
    } catch {
        Write-Host "  FEHLER: $_" -ForegroundColor Red
        $exitCode = 1
    }

    if ($exitCode -eq 0) {
        $done += $run.id
        Save-State $done
        $completed++
        Write-Host "  Completed and saved." -ForegroundColor Green
    } else {
        Write-Host "  Run failed (exit $exitCode) - will retry on next start." -ForegroundColor Red
        Write-Host "  Campaign continues with next run..." -ForegroundColor Yellow
    }
}

# Balanced wieder herstellen nach dem letzten Run
powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e | Out-Null

$elapsed = [math]::Round(((Get-Date) - $startTime).TotalMinutes, 1)
Write-Host ""
Write-Host "==========================================================" -ForegroundColor Cyan
if ($pending -eq $completed) {
    Write-Host "  All $completed runs completed in $elapsed minutes!" -ForegroundColor Green
} else {
    $leftover = $pending - $completed
    Write-Host ("  {0}/{1} runs completed ({2} failed) | {3} min" -f $completed, $pending, $leftover, $elapsed) -ForegroundColor Yellow
}
Write-Host "  State: $STATE_FILE" -ForegroundColor DarkGray
Write-Host "==========================================================" -ForegroundColor Cyan
