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
    [switch]$Reset
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT        = Split-Path -Parent $PSScriptRoot  # repo root
$BENCH       = Join-Path $PSScriptRoot "llm_migration_benchmark.py"
$STATE_FILE  = Join-Path $ROOT "benchmark_results\campaign_local_state.json"
$LLAMA_SRV   = "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe"
$GPT_OSS     = "C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf"
$QWEN35      = "C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf"
$RUNS        = 3

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
    # ── Ollama (highperf, seriell) ──────────────────────────────────────────
    @{
        id      = "ollama-qwen3-coder-30b-highperf"
        label   = "Ollama | qwen3-coder:30b | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "qwen3-coder:30b", "--runs", $RUNS)
    },
    @{
        id      = "ollama-deepseek-coder-v2-16b-highperf"
        label   = "Ollama | deepseek-coder-v2:16b | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "deepseek-coder-v2:16b", "--runs", $RUNS)
    },
    @{
        id      = "ollama-gpt-oss-20b-highperf"
        label   = "Ollama | gpt-oss:20b | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "gpt-oss:20b", "--runs", $RUNS)
    },
    @{
        id      = "ollama-qwen3.6-27b-q4-highperf"
        label   = "Ollama | qwen3.6:27b-q4_K_M | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "qwen3.6:27b-q4_K_M", "--runs", $RUNS)
    },
    @{
        id      = "ollama-qwen3.6-35b-q4-highperf"
        label   = "Ollama | qwen3.6:35b-a3b-q4_K_M | highperf"
        power   = "highperf"
        args    = @("--backend", "ollama", "--ollama-model", "qwen3.6:35b-a3b-q4_K_M", "--runs", $RUNS)
    },
    # ── llama.cpp balanced ngl99 (balanced schlägt highperf bei llama.cpp!) ─
    @{
        id      = "llamacpp-gpt-oss-20b-balanced-ngl99"
        label   = "llama.cpp | gpt-oss:20b | balanced | ngl99"
        power   = "balanced"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "gpt-oss:20b=$GPT_OSS",
                    "--llama-ngl", "99", "--runs", $RUNS)
    },
    @{
        id      = "llamacpp-qwen3.6-35b-balanced-ngl99"
        label   = "llama.cpp | qwen3.6:35b-a3b | balanced | ngl99"
        power   = "balanced"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "qwen3.6:35b-a3b=$QWEN35",
                    "--llama-ngl", "99", "--runs", $RUNS)
    },
    # ── llama.cpp highperf ngl99 (Vergleich) ────────────────────────────────
    @{
        id      = "llamacpp-gpt-oss-20b-highperf-ngl99"
        label   = "llama.cpp | gpt-oss:20b | highperf | ngl99"
        power   = "highperf"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "gpt-oss:20b=$GPT_OSS",
                    "--llama-ngl", "99", "--runs", $RUNS)
    },
    @{
        id      = "llamacpp-qwen3.6-35b-highperf-ngl99"
        label   = "llama.cpp | qwen3.6:35b-a3b | highperf | ngl99"
        power   = "highperf"
        args    = @("--backend", "llama_cpp",
                    "--llama-server", $LLAMA_SRV,
                    "--llama-model", "qwen3.6:35b-a3b=$QWEN35",
                    "--llama-ngl", "99", "--runs", $RUNS)
    },
    # ── Ollama balanced (Vergleich zu highperf) ─────────────────────────────
    @{
        id      = "ollama-qwen3-coder-30b-balanced"
        label   = "Ollama | qwen3-coder:30b | balanced"
        power   = "balanced"
        args    = @("--backend", "ollama", "--ollama-model", "qwen3-coder:30b", "--runs", $RUNS)
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
Write-Host "╔══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║        Lokale Benchmark-Kampagne — 11 Tasks, 3 Runs      ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

$pending = 0
foreach ($run in $ALL_RUNS) {
    if ($done -contains $run.id) {
        Write-Host "  ✅ DONE   $($run.label)" -ForegroundColor Green
    } else {
        Write-Host "  ⏳ OFFEN  $($run.label)" -ForegroundColor Yellow
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
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  ▶ $($run.label)" -ForegroundColor White
    $remaining = $pending - $completed
    Write-Host "  Noch $remaining Run(s) nach diesem | Start: $(Get-Date -Format 'HH:mm')" -ForegroundColor DarkGray
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan

    Set-Power $run.power
    Set-Location $ROOT

    $exitCode = 0
    try {
        & python $BENCH @($run.args)
        $exitCode = $LASTEXITCODE
    } catch {
        Write-Host "  FEHLER: $_" -ForegroundColor Red
        $exitCode = 1
    }

    if ($exitCode -eq 0) {
        $done += $run.id
        Save-State $done
        $completed++
        Write-Host "  ✅ Abgeschlossen und gespeichert." -ForegroundColor Green
    } else {
        Write-Host "  ❌ Run fehlgeschlagen (exit $exitCode) — wird beim nächsten Start wiederholt." -ForegroundColor Red
        Write-Host "  Kampagne wird fortgesetzt mit nächstem Run..." -ForegroundColor Yellow
    }
}

# Balanced wieder herstellen nach dem letzten Run
powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e | Out-Null

$elapsed = [math]::Round(((Get-Date) - $startTime).TotalMinutes, 1)
Write-Host ""
Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Cyan
if ($pending -eq $completed) {
    Write-Host "  🎉 Alle $completed Runs abgeschlossen in $elapsed Minuten!" -ForegroundColor Green
} else {
    $leftover = $pending - $completed
    Write-Host "  $completed/$pending Runs abgeschlossen ($leftover fehlgeschlagen) | $elapsed min" -ForegroundColor Yellow
}
Write-Host "  State: $STATE_FILE" -ForegroundColor DarkGray
Write-Host "══════════════════════════════════════════════════════════" -ForegroundColor Cyan
