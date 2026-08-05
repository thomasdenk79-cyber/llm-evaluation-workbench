# Clean local benchmark campaign

## Purpose

This is the restart point after removing generated legacy benchmark output.
The authoritative campaign configuration is
`config/clean-local-campaign.toml`; the durable detail CSV, summary CSV and
HTML dashboard are written below
`benchmark_results/clean-local-campaign/`.

The campaign is deliberately serial: one local model slot, one server and one
GPU workload at a time. This avoids comparing a model against leftover Ollama
VRAM allocations or another `llama-server` process.

## First clean comparison snapshot

The first controlled snapshot used the web-grid fixture and the aggressive
512-MiB margin for llama.cpp/ik. It is a performance screen, not the final
coding ranking:

| Backend/profile | Model | Tok/s | GPU avg | VRAM avg | Errors |
|---|---|---:|---:|---:|---:|
| Ollama | KAT Coder Q4 | 37.44 | 39% | 10.79 GiB | 0 |
| Ollama | Qwen 35B Q4 | 31.10 | 28% | 9.91 GiB | 0 |
| Ollama | Qwen 35B Q5 | 29.56 | 30% | 9.72 GiB | 0 |
| Ollama | Qwen 27B Q4 | 3.38 | 26% | 10.73 GiB | 0 |
| Upstream fit 512 | KAT Coder Q4 | 5.75 | 97% | 11.40 GiB | 0 |
| Upstream fit 512 | Qwen 35B Q4 | 4.75 | 99% | 11.33 GiB | 0 |
| Upstream fit 512 | Qwen 35B Q5 | 3.09 | 99% | 11.29 GiB | 0 |
| ik fit 512 | KAT Coder Q4 | 44.82 | 39% | 11.19 GiB | 0 |
| ik fit 512 | Qwen 35B Q4 | 45.50 | 39% | 11.19 GiB | 0 |
| ik fit 512 | Qwen 35B Q5 | 34.31 | 39% | 11.21 GiB | 0 |

The Qwen 27B exported GGUF failed to become ready in both llama.cpp
implementations and is therefore excluded from the fork ranking until the
original compatible GGUF is used. The low ik GPU percentage is not a reason
to replace this faster profile with `n-cpu-moe 16`; the earlier transfer-heavy
test was only about 7.8 Tok/s despite 84% GPU utilization.

## Phases

1. **Baseline inventory:** one web-grid run for every controlled local Qwen,
   KAT and coding model in the TOML. Record speed, heuristic quality, GPU,
   VRAM, RAM and PCIe telemetry.
2. **Top-20 medium run:** repeat the 20 best reliable candidates three times
   with the same 32768-context profile.
3. **Top-7 hard run:** run the hard SQL/Python/coding suites for the seven best
   candidates, then compare correctness, reliability and speed.
4. **Final report:** rebuild the grouped/filterable HTML from the unified
   detail CSV. Never rank from a live HTML snapshot alone.

The same model list and benchmark fixture must be used for Ollama,
upstream-llama.cpp and ik_llama.cpp. Only the launch backend/profile changes.
For llama.cpp runs, pass repeated `--llama-model NAME=GGUF` options and the
corresponding server executable.

## Clean inventory and medium rerun — 2026-08-05

The Ollama endpoint was corrected to `/api/generate`; the complete controlled
inventory then finished without transport errors. The 19 installed candidates
were repeated three times with the same 32k/16-thread profile. The strongest
medium results were:

| Model | Overall | Quality | Tok/s | GPU avg | VRAM avg |
|---|---:|---:|---:|---:|---:|
| deepseek-r1:8b | 48.01 | 34.53 | 64.78 | 74.2% | 7.30 GiB |
| gpt-oss:20b | 47.61 | 33.60 | 41.85 | 31.4% | 9.18 GiB |
| phi4-mini:3.8b-q4_K_M | 38.69 | 7.47 | 108.51 | 51.3% | 4.82 GiB |
| llama3.1:8b | 35.54 | 16.80 | 68.79 | 62.0% | 6.66 GiB |
| qwen3-coder:30b | 33.87 | 5.60 | 31.65 | 21.9% | 9.38 GiB |
| kat-coder-v2.5:dev | 32.83 | 8.40 | 43.25 | 29.0% | 9.45 GiB |
| rnj-1:8b | 32.32 | 13.07 | 61.71 | 73.4% | 7.17 GiB |

Source: `benchmark_results/clean-local-campaign/top20_medium_detail_run_20260805_031105.csv`.
The score is a screening heuristic, not a complete coding-quality judgment.

The top-seven executable coding gate is stored at
`benchmark_results/clean-local-campaign/top7_hard/top7_mini_gate.csv`.
Codestral, RNJ-1 and Qwen2.5 passed all ten deterministic checks; DeepSeek-R1,
GPT-OSS and KAT did not return the required fenced Python response; Llama 3.1
passed 9/10. This gate is intentionally reported separately from the
performance ranking.

## GGUF backend matrix — 2026-08-05

Seven compatible GGUFs were run once through upstream llama.cpp and the clean
ik CUDA build. The exported Qwen-27B file was excluded because neither server
could become ready with it. The web-grid screen shows the backend tradeoff:

| Backend | Best model | Overall | Tok/s | GPU avg | Result |
|---|---|---:|---:|---:|---|
| Upstream llama.cpp | gpt-oss-20b | 57.57 | 77.65 | 84.8% | fastest valid upstream row |
| Upstream llama.cpp | deepseek-coder-v2-16b | 31.32 | 36.07 | 69.9% | valid |
| ik_llama.cpp | deepseek-coder-v2-16b | 42.33 | 21.43 | 31.1% | best ik screen row |
| ik_llama.cpp | qwen3.6-35b-q4 | 34.80 | 19.05 | 35.5% | valid |

Upstream KAT/Qwen rows used nearly full VRAM and reached about 97–99% GPU
utilization but were only 3–4 Tok/s. The ik hybrid profile used about 31–36%
GPU and delivered 13–22 Tok/s on the same class of models. This confirms that
GPU utilization is diagnostic telemetry, not an optimization target.

Artifacts:
`upstream_matrix_detail_run_20260805_035226.csv` and
`ik_matrix_detail_run_20260805_040436.csv`.

## Controlled local profile

For this P16 Gen 2 / RTX 3500 Ada, use:

```text
context       32768
threads       16
batch/ubatch  512/128
GPU layers    100
KV cache      q8_0/q8_0
Flash Attn    on
batching      no continuous batching
ik profile    fit, margin 1664 MiB
MTP           empty unless the GGUF contains a verified MTP head
```

The old baseline forced `exps=CPU` and used only about 3.8 GiB VRAM. The
`n-cpu-moe 16` experiment filled about 11.5 GiB but became slower because of
PCIe expert transfers. Neither is the default. GPU utilization above 90% is
useful evidence, not a target that justifies exhausting VRAM; throughput,
VRAM headroom and absence of OOM/paging decide.

For a deliberate maximum-performance boundary test, use a separate ik run
with only 512 MiB fit margin:

```powershell
--ik-offload-profile fit --llama-fit-target-mib 512
```

This is an **aggressive stress profile**, not the Daily-Runner default.
Accept it only if three repetitions plus a 32768-context soak show no OOM,
no sustained paging and no runaway PCIe transfer. Windows' current desktop
allocation is measured separately; the 512 MiB value is not a guarantee that
the full physical VRAM is available.

## Resume and crash recovery

Use the master entrypoint and do not delete its in-progress files:

```powershell
python .\scripts\run_benchmark.py `
  --config .\config\clean-local-campaign.toml `
  --run resume --resume auto
```

If a run is interrupted, restart the same command. The runner skips durable
successful samples and retries missing/error samples. `pause.ini` pauses
between samples; `stop.ini` ends the campaign cleanly. Remove only a stale
control file after confirming no master process is alive.

Before a restart:

```powershell
Get-Process llama-server,ollama -ErrorAction SilentlyContinue
ollama ps
nvidia-smi
```

No benchmark is valid while another model occupies the GPU. Stop only the
identified stale PID or unload the named Ollama model; do not kill by process
name when a user workload may be active.

## Deferred models

BF16, Q8 and multi-hundred-gigabyte split models are not silently mixed into
the VRAM-stable ranking. They require a separate CPU-offload/RAM-throughput
campaign because they cannot fit this 12-GB GPU profile without substantial
host transfers. Their results may be useful, but they are not comparable to
the controlled daily-runner profile.
