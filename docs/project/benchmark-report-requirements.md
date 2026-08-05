# Unified Benchmark Report Requirements

This is the binding report and data-contract specification for the local,
Siemens and GitHub model benchmark views.

## One unified grid

- The report has one data grid only. Current, scheduled, running, completed,
  warning and error rows are all records in that grid.
- The grid is offline-capable and uses compact/autofit columns with horizontal
  scrolling for technical fields.
- Every column supports filtering and sorting. Status filtering supports
  multiple selected values.
- Users can group by multiple columns in order, for example `benchmark`,
  then `provider`, then `model`.
- The grid exposes a global search as well as per-column filters.
- The free-text interpretation is the rightmost column and may be wide.

## Required columns

The unified CSV and the HTML grid must carry these concepts as explicit,
stable fields:

`benchmark_display`, `benchmark_name`, `benchmark_task_count`,
`benchmark_runs`, `samples`, `status`, `provider`, `backend`, `model`,
`run_started_at`, `recorded_at`, `last_update`, `elapsed_seconds`, `wall_s`,
`output_tps`, `avg_cpu_pct`, `avg_gpu_pct`, `avg_vram_used_gb`,
`free_vram_gb`, `avg_mem_gb`, `system_errors`, `error_text`,
`quality_score`, `heuristic_score`, `rating_score`, `rating`,
`interpretation`, and `launch_params`.

`launch_params` remains deterministic JSON in the CSV. The HTML renders it as
an expandable, human-readable parameter panel so differences between runs are
visible without opening the raw CSV.

## Semantics

- `benchmark_display` includes the fixture and sample count, for example
  `Clean multi-backend model screening — 1 task × 3 runs`. The internal
  fixture name remains available separately in `benchmark_name`.
- `provider` identifies the origin: `Siemens`, `GitHub`, `Local/Ollama`,
  `Local/upstream`, or `Local/ik`.
- `quality_score` is the task-quality result. `heuristic_score` is the
  keyword/rule screening result. They must not be displayed as identical
  values under two unexplained labels. If no independent quality evaluator
  exists, quality is `N/A` and the report says why.
- `rating_score` is the numeric routing score:
  `quality × normalized performance × reliability`, where reliability is the
  successful-run ratio after system errors. The report shows the components
  and the formula.
- `rating` is a readable class, not a cryptic failure label. It must explain
  the practical role, for example `good coder, slow`, `good architect,
  limited coder`, or `not reliable for unattended use`.
- `error_text` is visible in a tooltip/popover and has a copy-to-clipboard
  action. Errors remain represented in history; they are never silently
  dropped.
- `free_vram_gb` is measured from the same telemetry window as used VRAM.
  If it was not measured, the value is `N/A`, never an invented estimate.

## Header and status

The header contains the current series status and one ETA. ETA is calculated
from the mean elapsed time of completed benchmark rows multiplied by the
remaining planned rows. There is no per-row ETA, planned-start column, or
LLM-start/LLM-stop telemetry column in the main grid.

## Provenance and compatibility

The report must preserve backend, executable, model path, context, threads,
batch/ubatch, GPU layers, KV cache, Flash Attention, offload profile, fit
margin, CPU-MoE and MTP/speculative-decoding settings. Normal Qwen GGUF rows
must explicitly show no MTP when no verified MTP head is present.

