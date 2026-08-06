# Agent-Helper Evaluation Track

> Bilingual (DE/EN) reference for the `scripts\agent_helper_eval\` subsystem.
> This document describes the **harness** (schema, storage, aggregation,
> rubric, catalog, model inventory, report, and — as of §16 — a real
> one-model Ollama connect-gate/mini-gate executor). It makes no claims
> about actual model results: no campaign has been run by this session,
> only the pipeline itself has been verified with synthetic data, a
> read-only historical import, and mocked-transport tests of the real
> executor (see §16.4 for one accidental, minimal, real generation call
> made during manual CLI verification).

## 1. Zweck / Purpose

**DE:** Dieses Subsystem misst und berichtet, wie gut sich lokale
Ollama/llama.cpp-Modelle, Siemens-API-Modelle und GitHub-Copilot-Agent-
Referenzen für **delegierte Helper-Aufgaben** eignen (Coding, Bug-Review,
SQL-Migration, Frontend, Architektur/Planung, Multi-Turn-Korrektur). Es ist
strikt additiv: Es ändert, verschiebt oder überschreibt keine bestehenden
Ergebnisse, Skripte oder Berichte des bestehenden
`scripts\llm_migration_benchmark.py`-Laufs.

**EN:** This subsystem measures and reports how suitable local
Ollama/llama.cpp models, Siemens API models, and GitHub Copilot agent
references are for **delegated helper tasks** (coding, bug review, SQL
migration, frontend, architecture/planning, multi-turn correction). It is
strictly additive: it does not change, move, or overwrite any existing
results, scripts, or reports from the existing
`scripts\llm_migration_benchmark.py` run.

## 2. Scope of this implementation phase

The **measurement/data/report harness** and small deterministic tests were
built first; §16 later added a real, explicitly-invoked one-model Ollama
connect-gate/mini-gate executor (`live_gates.py`/`ollama_client.py`/
`mini_task.py`). No code in `scripts\agent_helper_eval\` calls a model,
API, Ollama, or llama.cpp process **automatically** — `connect-gate-run`/
`mini-gate-run` only ever run when a human/parent agent explicitly invokes
that exact CLI subcommand naming one `--model` and one `--campaign-id`.
Full model campaigns (iterating an inventory) will be run later, strictly
serially, by the parent agent, using the commands documented in
`docs\operations\runbook.md`.

## 3. Canonical storage model

- **SSOT:** one SQLite database per campaign,
  `benchmark_results\agent-helper\<campaign-id>\agent_helper.sqlite3`.
  Schema version `agent-helper-v1` is stamped in a `schema_meta` table;
  opening a database with a mismatched version raises
  `SchemaVersionMismatchError` instead of silently migrating or overwriting.
- **Deterministic CSV exports** (derived from the database, never hand
  edited), fixed column order:
  - `agent_helper_samples.csv` — one row per subtask attempt
    (`schema.SAMPLE_CSV_COLUMNS`, 66 columns, including the 10 optional
    phase-timing seconds fields — see §11).
  - `agent_helper_aggregates.csv` — one row per
    benchmark-set + backend + model run (`schema.AGGREGATE_CSV_COLUMNS`,
    67 columns, including the 10 phase-timing rollup fields — see §11).
  - `agent_helper_capacity_profile.csv` — one row per
    benchmark-set + backend + model + **concurrency level**
    (`schema.CAPACITY_PROFILE_CSV_COLUMNS`, 36 columns) — a **third**
    canonical table with a fundamentally different grain from the two
    above, see §11.
- **N/A handling:** unavailable metrics (for example GitHub Copilot agent
  tokens/sec, TTFT, or process-level CPU/RAM/GPU/VRAM, which the runtime
  cannot expose) are stored as SQL `NULL`/Python `None` internally (so
  aggregation math such as means and percentiles stays correct) and are only
  rendered as the literal string `"N/A"` at CSV/HTML export time. A value is
  never invented for a metric a runtime cannot provide.
- All three CSVs are **stable and additive-only**: existing columns must
  never be reordered, renamed, or removed within `agent-helper-v1`. A
  breaking change requires a new schema version and an explicit migration
  note in `docs\project\changelog.md`.

## 4. Isolation from existing results

New agent-helper campaigns are written **only** under
`benchmark_results\agent-helper\<campaign-id>\`. This never touches the
existing flat `benchmark_results\*.csv`/`*.json` layout used by
`scripts\llm_migration_benchmark.py`. `benchmark_results\` as a whole is
already `.gitignore`d in this repository, so agent-helper output never
appears in `git status`.

A combined, multi-campaign HTML report is (re)written at
`benchmark_results\agent-helper\report.html`, built by scanning every
campaign subdirectory's SQLite database — never by re-running a model.

## 5. Historical adapter (`historical_adapter.py`)

Reads (never writes) a legacy `migration_llm_bench_history.csv` produced by
`scripts\llm_migration_benchmark.py` — from **any filesystem path**,
relative or absolute, inside this repository (for example a repo-local
`benchmark_results_resume_test*`/`benchmark_results_chaos_local` history
CSV) or entirely outside it (for example a user's home-directory
`benchmark_results\migration_llm_bench_history.csv`) — and maps each row
into a canonical `SampleRecord`, tagged `provenance="historical_import"`
and `track="pure_model"` (the legacy runner never exercised a tool-agent
track). Documented, honest limitations:

| Legacy field | Canonical field | Note |
|---|---|---|
| `quality_score` | `reviewer_score` (never `deterministic_score`) | Legacy score is a keyword/rule heuristic, not a deterministic test result or a human/orchestrator review. `reviewer_provenance` is marked accordingly, and `notes` carries an explicit `confidence: estimated` marker (never "measured" — shares the same measured/estimated/projected/unknown vocabulary as `model_inventory.CONFIDENCE_LEVELS`). |
| `avg_mem_pct` / `max_mem_pct` | *(not imported; `ram_avg_mb`/`ram_max_mb` stay N/A)* | Legacy stored RAM as a percentage of total system RAM, not MB; converting would fabricate precision without the legacy machine's total RAM. |
| `recorded_at` (no timezone) | `start_time`/`end_time` (ISO-8601 + tz) | Adapter assumes the **importing machine's local timezone at import time** and says so explicitly in `notes`; this is an assumption, not a measured fact. |
| `error` (free text) | `status` (`"error"` or `"timeout"`) | The error message is inspected for a timeout marker ("timeout"/"timed out", e.g. `"Siemens error: The read operation timed out"`) and classified as the canonical `"timeout"` status rather than a generic `"error"`; both hard-fail the acceptance gate identically, so this only improves reporting precision. |
| *(none)* | `notes` (`"source file: <resolved absolute path>"`) | The actual file passed to the import is always recorded for audit/provenance — distinct from the legacy row's own `source_csv` value, which is a different, legacy-runner-recorded field. |
| *(none)* | `deterministic_score`, `ttft_seconds`, `collaboration_score` | Legacy runner had no deterministic test oracle, no TTFT capture, and no multi-turn collaboration concept; all stay N/A. |

**Keyword-score safeguard:** because legacy rows never have
`deterministic_score` evidence, their `reviewer_score` (imported from the
legacy keyword heuristic) is the *only* signal available. `aggregate.py`
detects this exact situation (`reviewer_evidence_is_heuristic_only=True`,
see §9) and caps every legacy-imported model-run below
`tier-1-recommended`, with an explicit caveat in `recommendation` — a
legacy model (for example one nicknamed "RNJ-1" in ad-hoc testing) is never
labeled "suitable"/"strong candidate" purely because its keyword-matched
`quality_score` happened to be high.

**Sample-ID collision fix (real-world bug found and fixed this cycle):**
`sample_id` always includes the strictly-increasing per-CSV-row
`sample_seq`. Real `migration_llm_bench_history.csv` data can record the
*same* `case_id` + `run` value twice for one `benchmark_run_id` (a task
times out, is retried, and the legacy runner never increments its own
`run` column) — without `sample_seq` in the ID, the two rows produced an
identical `sample_id`, and because `storage.py`'s `samples` table has
`PRIMARY KEY("sample_id")` with `INSERT OR REPLACE`, the earlier row (the
timeout) was silently overwritten and its evidence permanently lost. This
was caught by validating the adapter against the real external evidence
file described in this project's history (64 rows: 5 "mini" + 59
"ora-pg-py-33"; `deepseek-v4-flash` recording 4 timeouts among its 16
attempt-rows) — before the fix, only 60 of 64 rows survived import; after
the fix, all 64 do, and `HistoricalAdapterTests.
test_retry_rows_sharing_case_id_and_run_get_distinct_sample_ids` is a
permanent regression test for this exact scenario (including a SQLite
round-trip proof).

Import a real legacy CSV into an isolated, clearly-marked campaign:

```powershell
python .\scripts\run_agent_helper_campaign.py import-legacy `
  --csv ..\benchmark_results\migration_llm_bench_history.csv
```

Any other path works identically, in or out of the repository, for
example:

```powershell
python .\scripts\run_agent_helper_campaign.py import-legacy `
  --csv "C:\Users\<you>\benchmark_results\migration_llm_bench_history.csv"
```

## 6. Benchmark catalog (`catalog.py`)

Seven staged-gate tasks (`benchmarks\agent-helper-catalog-v1.json`), each
explicitly marked with which tracks (`pure_model`, `tool_agent`) and which
gates (`smoke`, `deterministic_tests`, `reviewer`, `collaboration`) apply:

1. `connect-smoke-v1` — connect/format smoke check.
2. `mini-coding-tests-v1` — small coding task, graded by a fixed unit test suite.
3. `bug-review-v1` — locate/explain seeded bug(s) in a diff/excerpt.
4. `sql-migration-v1` — Oracle→PostgreSQL fragment, graded by executing the produced SQL.
5. `frontend-task-v1` — small HTML/CSS/JS component, graded by a structural check.
6. `architecture-planning-v1` — plan/design task, reviewer-only (no deterministic oracle).
7. `multi-turn-collaboration-v1` — correction loop, measures convergence efficiency.

This module only defines and validates data; it never executes a task.

### 6.1 External catalog import (multi-agent workspace)

`catalog.load_catalog_document(path)` is the stable, versioned contract
another concurrently-running agent/session can hand a benchmark-set file
through **with zero code changes** in this repo, provided the file matches:

1. Top-level `catalog_schema_version` equal to one of
   `catalog.SUPPORTED_CATALOG_SCHEMA_VERSIONS` (today, only
   `"agent-helper-catalog-v1"`) — a missing/unrecognised value is rejected
   with a clear `CatalogValidationError`, never silently coerced or
   misparsed.
2. Top-level `catalog_metadata` object (`CatalogMetadata`) with **mandatory**
   non-empty `catalog_id`, `author`, and `created_at` (ISO-8601 with
   timezone; `source_notes` optional). This is catalog-*file* provenance —
   who authored this benchmark-set definition — distinct from the per-sample
   `SampleRecord.provenance` field (how one *result* row came to exist:
   measured/historical_import/projected/synthetic_dry_run).
3. A `tasks` array whose items match `TaskSpec`'s field names.

Any structural mismatch — wrong/missing version, missing/invalid
`catalog_metadata`, or a task with the wrong shape (for example a file that
actually belongs to the legacy `llm_migration_benchmark.py`
`benchmark_id`/`spec_version`/`required_keywords` format, as used by
`benchmarks\swe-mixed-hard-24.json`, `benchmarks\swe-python-hard-24.json`,
`benchmarks\swe-sql-hard-24.json`, `benchmarks\web-grid-demo-v1.json`, and
similar files already present in this workspace) — raises a single,
clearly-worded `CatalogValidationError` instead of leaking a raw
`TypeError`/`KeyError`. `load_catalog(path)` remains the existing
list-returning convenience wrapper (unchanged call sites); use
`load_catalog_document(path)` when the catalog-level provenance is needed.

`scripts\run_agent_helper_campaign.py dry-run` accepts an optional
`--catalog PATH` flag; when supplied, the loaded catalog's `catalog_id`/
`catalog_schema_version` are threaded through as the generated samples'
`benchmark_set`/`benchmark_version` (never left mislabeled as the bundled
default's identity — see `orchestrator.generate_synthetic_samples()`).

See `benchmarks\agent-helper-catalog.example-external.json` for a minimal,
concrete, independently-loadable example of an externally authored catalog,
and §15 below for the full multi-agent coordination/ownership note and
contribution workflow.

## 7. Model inventory / campaign configuration (`model_inventory.py`)

`benchmarks\agent-helper-model-inventory.example.json` shows the format for
Ollama tags, llama.cpp GGUF paths, Siemens API models, and GitHub Copilot
agent references. No secrets are ever stored in this file — see the
Siemens token convention in `AGENTS.md` (read the first `SIAK-` line from a
local token file at call time). `load_inventory()`/`save_inventory()` run a
defensive secret scan (`scan_for_secrets()`) and refuse to load/save a file
that looks like it contains a key/token/secret/password/bearer value or a
Siemens `SIAK-` literal.

Feasibility metadata per model includes `size_gb_on_disk`, `architecture`
(`dense`/`moe`/`unknown`), `quantization`, and two explicit
`FeasibilityProjection` fields:

- `fits_12gb_vram` — may be `confidence="measured"` if actually tried on the
  real, owned 12 GB notebook GPU.
- `fits_rtx5090_32gb_projection` — **can never** be `confidence="measured"`
  (enforced by `validate_feasibility()`); only `"estimated"`/`"projected"`/
  `"unknown"`, always with a required `assumptions` string. Projections are
  never presented as measured results.

This inventory is optional and report-wide (not per-campaign): `report.py`
renders it as a separate "Model inventory & feasibility" section (see §12)
with a green "MEASURED" badge only where `confidence="measured"` and
`allow_measured=True` (12 GB column), and an amber "PROJECTION/ESTIMATE"
badge everywhere else — including a defensive downgrade if a record somehow
still claims `confidence="measured"` for the RTX 5090 column despite
`validate_model_spec()` normally rejecting that at load time. Orchestrator
functions (`run_dry_run()`/`build_combined_report()`) best-effort load
`benchmarks\agent-helper-model-inventory.example.json` via
`load_default_model_inventory()`: a missing, invalid, or secret-looking file
never breaks dry-run/report generation, it only falls back to the report's
"no inventory loaded" message.

## 8. Orchestrator rubric (`rubric.py`)

Scoring/gating order (see also the bilingual methodology block embedded in
every generated report):

0. **Hard acceptance gate first, before any ranking at all.** Speed must
   *never* compensate for unusable quality -- see the "Hard acceptance
   gate" section below for the full specification. A model-run that fails
   this gate is classified `"not-usable"` and entirely excluded from the
   leaderboard/ranking, regardless of how fast or how good its raw scores
   looked.
1. **Deterministic correctness gates.** Below
   `DETERMINISTIC_PASS_THRESHOLD = 60.0`, the composite/overall score is
   capped at the deterministic score itself — no reviewer polish,
   collaboration efficiency, or raw speed can rescue a demonstrably wrong
   result. This is the concrete mechanism preventing a fast-but-wrong model
   from winning merely on tokens/sec.
2. **Reviewer/orchestrator review** dominates when there is no deterministic
   oracle (for example architecture/planning tasks). A reviewer score that
   only ever comes from a legacy/heuristic keyword-matching scorer (never a
   genuine human/orchestrator review) is explicitly flagged and capped
   below `tier-1-recommended` -- see the "Hard acceptance gate" section
   below.
3. **Collaboration/iteration efficiency** — see
   `time_to_accepted_result()` / `compare_collaboration_efficiency()`, which
   compare cumulative time-to-accepted-result and cumulative tokens across
   attempts, directly answering: *can several fast correction loops beat one
   slow, high-quality pass?* The comparison is based on measured cumulative
   cost, not single-attempt speed. Repeated fast failures score worse than
   one slower accepted pass (see below).
4. **Speed/resource/cost last**, and always *relative* to the fastest peer in
   the same `(campaign_id, run_id, benchmark_set, track)` scope
   (`normalize_speed_score()`), never an unbounded raw number, and computed
   **only from each candidate's own accepted samples** -- a failed-but-fast
   attempt never contributes to the speed score, and the peer pool itself
   excludes any hard-gated (not-usable) model-run entirely. This keeps
   pure-model and tool-agent speed comparisons from leaking into each
   other, and keeps speed a tie-breaker among *accepted, usable* results
   only, never a rescue mechanism.

## 9. Hard acceptance gate: speed never compensates for unusable quality

**This is a hard, non-negotiable policy**, implemented in
`rubric.compute_sample_acceptance()` / `rubric.compute_aggregate_hard_gate()`
/ `rubric.suitability_tier()` and enforced structurally by
`schema.validate_sample()` / `schema.validate_aggregate()`.

**Sample level (`SampleRecord.acceptance_status`, one of `"accepted"` /
`"not_usable"` / `"not_evaluated"`):** an attempt is forced to
`"not_usable"` -- regardless of speed, tokens/sec, or how polished the
output looked -- if **any** of the following holds:

- system `status` is `"error"` or `"timeout"`;
- `unsafe_behavior_flag` is `True` (zero tolerance);
- `output_placeholder_or_incomplete` is `True` (zero tolerance);
- `deterministic_score` is present and below `DETERMINISTIC_PASS_THRESHOLD`
  (60.0);
- `reviewer_score` is present and below `REVIEWER_MIN_ACCEPTABLE_SCORE`
  (50.0).

If none of the above applies but there is also no scoring evidence at all
(a `"skipped"` attempt, or a `"success"` with neither a deterministic nor a
reviewer score), the verdict is `"not_evaluated"` -- silence is *never*
treated as acceptance.

**Aggregate/model-run level (`AggregateRecord.hard_gate_failed` /
`suitability_tier == "not-usable"`):** a whole model-run is excluded from
ranking -- it never appears in the leaderboard table, only in a dedicated
"Excluded" table in the report -- if:

- `unsafe_sample_count > 0` anywhere in the group (always fails, zero
  tolerance, independent of the acceptance rate); or
- the acceptance rate over *evaluated* attempts only
  (`accepted / (accepted + not_usable)`, deliberately excluding
  `not_evaluated` so genuinely missing evaluation data is never
  misclassified as a quality failure) is below
  `ACCEPTANCE_RATE_MIN_FOR_USABLE` (50.0%).

This is what makes **repeated fast failures score worse than one slower
accepted pass**: a model that fails most of its attempts is hard-gated out
entirely, even if its one rare accepted attempt was both fast and
high-quality. `AggregateRecord` also carries
`time_to_accepted_result_seconds_mean` and `rework_tokens_to_accept_mean`
(cumulative cost to reach an accepted result per task, via
`time_to_accepted_result()`) and `unresolved_task_count` (distinct tasks
attempted but never accepted) so this cost is visible, not just implied by
the tier.

**Keyword/heuristic-score safeguard
(`AggregateRecord.reviewer_evidence_is_heuristic_only`):** separately from
the hard exclusion above, a model-run whose *entire* quality signal rests
on a legacy/heuristic keyword-matching `reviewer_score` (see
`rubric.is_heuristic_reviewer_provenance()`) -- no deterministic
test-oracle evidence anywhere in the group -- is capped below
`tier-1-recommended` (`schema.validate_aggregate()` structurally rejects
the combination `reviewer_evidence_is_heuristic_only=True` +
`suitability_tier == "tier-1-recommended"`), and its `recommendation` text
carries an explicit caveat. **No model is ever labeled "suitable"/"strong
candidate" based on a keyword score alone** -- this directly covers
ad-hoc-named legacy models (for example one nicknamed "RNJ-1" during
testing) that might otherwise look artificially strong after only a
handful of keyword-heuristic-scored legacy samples.

The generated HTML report's executive summary states this policy
explicitly in both languages, names every excluded model-run, and adds the
keyword-score caveat inline wherever it applies (see the "HTML report"
section below).

## 10. GitHub Copilot task-agent caveat

GitHub Copilot task-agent references cannot reliably expose raw tokens/sec,
TTFT, or process-level CPU/RAM/GPU/VRAM unless the runtime surfaces it
explicitly. Those sample/aggregate fields are recorded as `N/A` rather than
estimated — never guessed or backfilled with a plausible-looking number.
Pure-model and tool-agent comparisons that include a Copilot reference are
kept in separate leaderboard sections/scopes in the generated report,
precisely because their metrics are not measured on the same basis. The
same rule applies to every phase-timing field in §11: Copilot's internal
queue/LLM/generation timings are not observable, so all ten phase-timing
seconds fields stay `N/A` for Copilot samples rather than being estimated.

## 11. Phase attribution & capacity/concurrency profiling

### 11.1 Raw phase-timing fields (`SampleRecord`)

Ten optional seconds fields (`schema.PHASE_TIMING_SECONDS_FIELDS`), captured
per sample where observable: `total_wall_seconds`, `llm_queue_seconds`,
`llm_request_seconds` (opaque API call wall/service time), `prompt_eval_seconds`,
`generation_seconds` (only when a local runtime exposes the granular split),
`local_tool_exec_seconds`, `test_exec_seconds`, `orchestrator_review_seconds`,
`idle_wait_seconds`, `overlap_seconds` (explicit bookkeeping for genuinely
concurrent/overlapping phases). All ten are additive/optional — existing
rows and code that never set them keep working unchanged.

**LLM-bucket precedence rule** (`phase_timing.llm_bucket_seconds()` and the
mirrored logic in `schema.validate_sample()`'s overlap-honesty check): the
top-level `llm_request_seconds` black-box duration is always preferred over
summing `prompt_eval_seconds + generation_seconds`; the granular sum is only
used as a fallback when no top-level duration was reported. The same LLM
call is never counted twice.

### 11.2 Exclusive critical path vs. non-exclusive utilization (`phase_timing.py`)

Two deliberately distinct kinds of numbers are derived from the same raw
fields:

1. **Exclusive critical-path percentages**
   (`phase_timing.compute_exclusive_critical_path()`, rolled up per
   model-run by `phase_timing.rollup_group()` into
   `AggregateRecord.llm_critical_path_percent` /
   `tool_critical_path_percent` / `queue_idle_critical_path_percent` /
   `orchestration_critical_path_percent`). These four buckets — LLM, local
   tools/tests, queue/idle, orchestration — are normalized against their
   own **naive sum** (not against `total_wall_seconds` directly), so they
   always sum to **exactly 100%** of accounted-for phase time, regardless
   of any overlap or instrumentation gap versus the true wall-clock
   duration. Aggregate-level rollup uses a **ratio of summed seconds**
   across every phase-instrumented sample in the group, never a mean of
   per-sample percentages (which would over-weight short attempts).
2. **Non-exclusive utilization/busy ratios**
   (`phase_timing.compute_utilization_ratios()` →
   `AggregateRecord.model_busy_percent` / `gpu_active_percent` /
   `tool_runner_busy_percent`): each is independently a percent of
   wall-clock time and is **not** required to sum to anything, and is
   **deliberately not capped at 100%** — under genuine concurrency
   (§11.3), multiple simultaneously-busy requests can legitimately exceed
   100% of one wall-clock window.

**Overlap semantics made explicit, not implicit:** the mismatch between the
naive phase-time sum and the true wall-clock duration is reported as two
*separate* diagnostics, never folded into the 100% split:

- `phase_timing_overlap_ratio_percent` — naive sum exceeded wall time
  (phases genuinely overlapped/ran concurrently);
- `phase_timing_unaccounted_ratio_percent` — wall time exceeded the naive
  sum (some wall-clock time was not attributed to any instrumented phase
  at all — an instrumentation gap).

`gpu_active_percent` is either `"measured"` (an independently supplied
`gpu_active_seconds`), `"approximated_from_model_busy"` (local `ollama`/
`llama_cpp` backends with no independent GPU measurement — the GPU is
essentially only active while doing local LLM work, so it is set equal to
`model_busy_percent`, a documented approximation, never fabricated), or
`"not_observable"` (opaque Siemens API / Copilot agent backends — stays
`None`/N/A). `schema.validate_aggregate()` structurally enforces: the four
critical-path percentages must be all-`None` together or sum to ~100 (never
a partial mix or a silent drift), and `phase_timing_sample_count == 0`
forces every rollup field to `None` (never a fabricated 0%) — always true
for a group made entirely of Copilot samples.

### 11.3 Post-quality-gate concurrency/capacity profile (`capacity_profile.py`)

A **third canonical table**, `CapacityProfileRecord`
(`schema.CAPACITY_PROFILE_CSV_COLUMNS`), grain
`campaign + run + benchmark_set + backend + model + concurrency_level` —
fundamentally different from `SampleRecord` (per task-attempt) and
`AggregateRecord` (per model-run, no concurrency dimension), so it gets its
own SQLite table (`capacity_profile`) and CSV export
(`agent_helper_capacity_profile.csv`) rather than being shoehorned into
either existing table.

**Purpose:** determine whether running more local helper agents (more
concurrent requests against the **same already-accepted** local model)
genuinely improves throughput, or merely causes queueing/resource
contention ("thrashing") that adds no real benefit — measured via
`throughput_efficiency_percent = 100 * aggregate_tokens_per_second /
(concurrency_level * single_request_baseline_tokens_per_second)` and
classified by `capacity_profile.classify_concurrency_scaling()`:

| Efficiency | Classification |
|---|---|
| ≥ 85% | `scales-well` |
| 50–85% | `diminishing-returns` |
| < 50% | `thrashing-or-no-benefit` |
| no data | `insufficient-data` |

**Staged concurrency catalog:** exactly the levels `(1, 2, 4)`
(`schema.CONCURRENCY_LEVELS`, `capacity_profile.DEFAULT_CONCURRENCY_CATALOG`),
each stage explicitly requiring the prior stage to have already completed
(`ConcurrencyStageSpec.requires_prior_level`) — concurrency is escalated
step by step, never jumped straight to 4, so a VRAM/stability problem at a
lower concurrency level is caught before attempting a higher one.

**Mandatory preflight gate — enforced as code, in two independent places
(defense in depth), not just documentation:**

1. `capacity_profile.can_start_concurrency_profile()` — the single
   authoritative decision function a (future) orchestrator must call before
   issuing any concurrent request. Returns `allowed=False` unless **all**
   of the following hold: the model's single-request result already
   reached `acceptance_status == "accepted"`; the model-run as a whole did
   not fail the aggregate hard acceptance gate (§9); an explicit
   memory-safety/VRAM-headroom check was performed for the target
   concurrency level (12 GB VRAM constraint); and, if supplied,
   `vram_headroom_mb` is not negative.
2. `schema.validate_capacity_profile()` — independently re-checks
   `preflight_quality_gate_passed` and `preflight_memory_safety_checked`
   are both `True` and **hard-rejects record construction otherwise** — a
   record documenting an unsafe/premature concurrency run cannot even be
   built, whether by a caller bug or a hand-edited/imported row.

**On a 12 GB VRAM GPU, concurrency profiling must never run before
single-request quality/stability acceptance and an explicit memory-safety
check.** This is the direct implementation of that hardware-safety rule.

**Metrics captured per concurrency level (1/2/4) against the same
accepted model:** aggregate tokens/sec (system-wide throughput across the
whole concurrent batch — never a sum of individually-measured per-request
rates, which would double count overlapping wall time), per-request
tokens/sec (mean/p50/p95), queue/service seconds, p95 end-to-end latency,
GPU/VRAM/RAM avg+max, error/timeout counts, time-to-accepted-result at that
concurrency level, the concurrency=1 baseline throughput used for the
efficiency calculation, and the VRAM budget/headroom assumptions used for
the memory-safety check.

The synthetic dry-run (`orchestrator.generate_synthetic_capacity_profile()`)
demonstrates this entire pipeline — preflight gate, record construction,
SQLite storage, CSV export, and report rendering — end-to-end with a
deliberately **non-uniform** synthetic scaling shape (close to linear at
concurrency=2, clearly sub-linear/"diminishing-returns" at concurrency=4),
never a hard-coded optimistic result, and without issuing any real
concurrent request.

## 12. HTML report (`report.py`)

Self-contained, offline (`file://`-safe), no CDN/font/network dependency.
Key properties:

- All untrusted content (task names, model identifiers, notes, error
  messages, artifact paths, reviewer notes) is passed through `esc()`
  (`html.escape`) before interpolation — never via a raw f-string.
- Bilingual DE/EN: both languages are always present in the markup
  (`.i18n-de`/`.i18n-en`), toggled client-side; the page degrades to German
  if JavaScript is disabled.
- The most recent `expand_latest` campaigns (default 2) are expanded via
  native `<details open>`; older campaigns are present, selectable, and
  collapsed (`<details>` without `open`) — no server round-trip needed.
- Tables (leaderboard, task drilldown, errors, resource metrics) get
  dependency-free client-side sort (click a header) and filter (a text
  input above the table).
- **Leaderboard vs. Excluded:** the leaderboard table only ever contains
  model-runs with `hard_gate_failed=False`; every hard-gated model-run
  appears exclusively in a separate "Excluded (hard acceptance gate)" table
  (acceptance rate, unsafe-sample count, never-accepted-task count,
  exclusion reason) — never side-by-side with ranked candidates.
- The quality-vs-speed scatter chart plots only non-hard-gated model-runs;
  excluded ones are named in a distinct "Not plotted — EXCLUDED" note
  (worded differently from the pre-existing "missing data" note, since
  exclusion is a quality/safety verdict, not a data gap).
- The executive summary states the hard-gate policy explicitly in both
  languages ("Speed never compensates for unusable quality..."), names
  every excluded model-run, and only ever picks its "best usable model"
  from non-excluded candidates — adding an inline keyword-score caveat if
  that pick's `reviewer_evidence_is_heuristic_only` is `True`.
- A quality-vs-speed scatter chart is a hand-rolled inline SVG (no charting
  library), colored by suitability tier, with an accessible legend list.
- Artifact links (`render_artifact_link()`) are only rendered as a clickable
  `<a href>` when the path is campaign-relative; anything that looks like an
  absolute filesystem path or a traversal (`..`) is shown as escaped plain
  text instead, never as a link.
- An executive summary plus a static bilingual methodology/caveats section
  (scoring order including the hard acceptance gate, the "fast loops vs.
  slow pass" question, the Copilot caveat, and historical-import
  limitations) are always included.
- **Phase attribution table** (per model-run): the four exclusive
  critical-path percentages, the overlap/unaccounted diagnostics, and the
  three utilization ratios, with an inline bilingual methodology note
  restating the normalization/overlap semantics from §11.2. A model-run
  with `phase_timing_sample_count == 0` (always true for Copilot-only
  groups) renders an explicit "not instrumented" row rather than being
  silently omitted or showing fabricated zeros.
- **Capacity/concurrency profile table**: one row per concurrency level
  (1/2/4) for the campaign's profiled local model, including throughput
  efficiency, scaling classification (color-coded like the suitability
  tier), VRAM headroom, and error/timeout counts, with an inline caveat
  restating that every row already passed both mandatory preflight checks
  (§11.3). Renders an explicit "no capacity profile available" message
  (never an empty/broken table) for any campaign that has not run one.
- **Navigation affordances** (offline, no server, no CDN): a language
  toggle button, a fullscreen toggle button, and keyboard shortcuts
  (`L` = toggle language, `F` = toggle fullscreen, `P` = print), guarded so
  they never fire while a modifier key is held or while focus is inside a
  text input/textarea/select/contenteditable element. A bilingual
  `<kbd>`-styled shortcut hint is shown next to the controls; all of these
  controls (including the fullscreen button) are hidden in `@media print`.
- **Model inventory & feasibility section**: a report-wide (not
  per-campaign) table rendered from an optional `model_inventory` sequence
  of `ModelSpec` records (see §7) — one row per model with backend,
  quantization, on-disk size, architecture, and a badge for each of the two
  `FeasibilityProjection` fields (12 GB VRAM fit, RTX 5090 32 GB
  projection). Badge rendering (`_feasibility_badge()`) only ever shows a
  green "MEASURED" badge for the 12 GB column when `confidence=="measured"`
  is both true and permitted for that column; every RTX 5090 value is
  always rendered with an amber "PROJECTION/ESTIMATE" badge, defensively,
  even if a malformed record somehow claimed `confidence="measured"` for
  it. Renders an explicit "no inventory loaded" fallback (never an empty
  table) when no inventory file is available. This addresses the
  measured-vs-assumption distinction end-to-end: the data model already
  enforced it (§7); this section is what finally makes it visible to a
  human reader.
- Four additional bilingual methodology entries make explicit design
  choices that were previously only implicit in the code: the two
  mandatory comparison baselines (current best usable vs. concurrency=1
  baseline — see the "Baseline" chip in the capacity-profile table), that
  the acceptance gate is applied identically regardless of backend/
  provider/cost tier, the measured-vs-assumption/projection distinction
  described above, and the fact that the report is generated exclusively
  from the canonical SQLite/CSV data (no hand-maintained figures, so there
  is no copy/paste drift between data and report).

### 12.1 Module split: `report.py` vs. `report_style.py` vs. `charts.py`

To keep `report.py` focused on page/table assembly, two smaller modules
were split out (no circular imports — both `report.py` and `charts.py`
import from `report_style.py`; `charts.py` only imports `report.py` under
`TYPE_CHECKING` for one forward-referenced type hint):

- **`report_style.py`** — shared, dependency-free text/style helpers used
  by both other modules: `esc()`, `fmt()`, `bi()`, the `TIER_COLORS` map,
  and the tier/status/scaling/acceptance → CSS-class mapping functions.
  `report.py` re-exports `esc`/`fmt`/`bi` at module level so existing call
  sites/tests (`from agent_helper_eval.report import esc`) are unaffected.
- **`charts.py`** — every inline-SVG/HTML chart renderer (no external
  charting library, no CDN, matches the offline-report constraint). See
  §12.2 for the full catalog.

### 12.2 Visualization catalog (`charts.py`)

Every chart returns a small, self-contained HTML fragment (`<svg
class="chart ...">` plus an optional `<ul class="legend-list">` and/or
bilingual `<p class="hint">` caveat) and follows the same design rules as
the rest of the report: `esc()` for all untrusted text, `bi()` for all
bilingual captions, and a graceful "not enough data" fallback instead of
raising or rendering broken markup when required fields are `None`/empty.

| # | Function | Chart | Notes |
|---|----------|-------|-------|
| 1 | `render_kpi_cards` | Executive KPI card grid (plain HTML, not SVG) | Model-runs total, usable/excluded split, accepted task attempts, acceptance rate, mean success rate, mean tokens/s, mean time-to-accepted-result — all computed only over usable model-runs where relevant. |
| 2 | `render_quality_gate_funnel` | Accepted-vs-rejected acceptance-gate funnel | Total → accepted / not-usable / not-evaluated stacked bar; "not usable" always uses the diagonal-hatch pattern, not color alone. |
| 3 | `render_quality_speed_chart` | Quality vs. speed scatter **+ Pareto frontier** | Pre-existing chart, enhanced this cycle: gold-ringed points + dashed step polyline connect the non-dominated (fastest-for-a-given-or-better-quality) subset of usable model-runs. |
| 4 | `render_time_to_accepted_chart` | Time-to-accepted-result bars, colored by suitability tier | Ranking chart — hard-gated model-runs excluded (see §12.3). |
| 5 | `render_task_model_heatmap` | Task-id × model quality heatmap | Cell = the *final* attempt (highest `iteration_index`/`retry_count`/`sample_seq`) for that (task, model) pair — what the orchestrator actually accepted/rejected after retries. `not_usable` final attempts are always hatched regardless of raw score. |
| 6 | `render_latency_percentile_chart` | Grouped P50/P95/**P99** bars | P50/P95 come from the aggregate; P99 is computed on the fly from this campaign's raw `elapsed_seconds` via `aggregate.percentile()` — no new CSV/schema column. |
| 7 | `render_critical_path_chart` | Exclusive critical-path 100%-stacked bars | LLM / tools-tests / queue-idle / orchestration, always summing to 100% by construction (§11.2); overlap/unaccounted % shown as separate non-stacked diagnostic text. Diagnostic chart — shows every instrumented model-run, hard-gated rows marked "⚠" (see §12.3). |
| 8 | `render_utilization_ratio_chart` | Non-exclusive utilization ratios | Model-busy% / GPU-active% / tool-runner-busy% — deliberately separate from #7 because these can exceed 100% under real concurrency. |
| 9 | `render_cpu_gpu_utilization_chart` | CPU/GPU avg+max % grouped bars | Diagnostic chart. |
| 10 | `render_ram_vram_utilization_chart` | RAM/VRAM avg+max MB grouped bars | Diagnostic chart. |
| 11 | `render_concurrency_scaling_chart` | Concurrency scaling line chart (1/2/4) | Solid line = aggregate TPS, dashed line = per-request TPS, one line per (backend, model). No exclusion filtering needed — every `CapacityProfileRecord` is schema-guaranteed to already have passed the hard gate + memory-safety preflight at concurrency=1 (§11.3). |
| 12 | `render_reliability_chart` | Success/error/skipped 100%-stacked bars | Diagnostic chart. |
| 13 | `render_iteration_rework_chart` | Retries-total + mean-iterations grouped bars | Diagnostic chart; caveat links back to the time-to-accepted-result chart. |
| 14 | `render_feasibility_fit_chart` | Model size vs. 12 GB / RTX 5090 32 GB fit | Solid circle = measured (12 GB only), dashed diamond = projected/estimated — the RTX 5090 marker is *never* a solid circle, even defensively (mirrors `_feasibility_badge()`). Report-wide (not per-campaign). |
| 15 | `render_routing_scenario_chart` | Illustrative cost/routing scenario | Suitability-tier distribution grouped by `provider`, used only as a coarse local/Siemens-API/Copilot cost-tier proxy — **no fabricated €/$ figures anywhere**. Ranking chart — hard-gated model-runs excluded. |
| 16 | `render_historical_trend_chart` | Cross-campaign historical trend | Usable-vs-not-usable counts + best *usable* `overall_score` per campaign, oldest→newest. Report-wide (not per-campaign), needs the full campaign sequence. |

### 12.3 Ranking charts vs. diagnostic charts (hard-gate display rule)

Every chart above follows one of two deliberate display rules — never a
third, ad hoc one:

- **Ranking/comparison charts** (#3 quality-vs-speed, #4 time-to-accepted,
  #6 latency percentiles, #15 routing scenario): hard-gate-excluded
  ("not-usable") model-runs are **never plotted** alongside accepted ones.
  They are listed separately in a distinctly worded
  "Nicht dargestellt — AUSGESCHLOSSEN.../Not plotted — EXCLUDED..." note
  (`_excluded_ranking_note()`), so a fast-but-unusable model can never
  visually look like a fast, good one.
- **Diagnostic/resource charts** (#7 critical path, #8 utilization ratios,
  #9 CPU/GPU, #10 RAM/VRAM, #12 reliability, #13 iteration/rework): these
  match the pre-existing resource/phase-timing *tables*, which already
  show every model-run, not just usable ones. Hard-gated rows are still
  shown but every row label gets a "⚠" suffix
  (`_row_label()`/`_hard_gate_diagnostic_caveat()`) and a caveat below the
  chart states plainly that this diagnostic data is **not a suitability
  signal**.

Accessible pattern encoding (`_pattern_defs()`): "not usable" and
"projected/estimated" are each encoded with a distinct diagonal-hatch SVG
pattern (steep solid stripes on dark red vs. shallow dashed stripes on
amber) **in addition to** color, so both distinctions survive grayscale
printing and common color-vision deficiencies — never color alone.

### 12.4 CSS/print additions

New CSS classes (`.kpi-grid`/`.kpi-card`/`.kpi-card-warn`/`.kpi-value`/
`.kpi-label`) style the KPI-card grid; `svg.chart` styling is shared by
every chart (`.funnel-chart`/`.heatmap-chart`/`.bar-chart`/
`.feasibility-chart`/`.critical-path-chart` are additional marker classes
on top of the shared `svg.chart` base, not separate layout systems). The
`@media print` block now also forces open every collapsed `<details
class="campaign">` and prevents charts/KPI cards from being split across a
page break, so a printed/PDF export always shows every campaign and every
visualization, not just the ones expanded on screen.

## 13. Tests

`scripts\test_agent_helper_eval.py` (plain `unittest`, matching the existing
project convention — run via `python -m unittest test_agent_helper_eval -v`
from inside `scripts\`) covers, fast and deterministically (no
model/network/filesystem dependency beyond `tempfile`):

- schema validation (valid/invalid samples, including the
  `status=error` ⇒ `system_error_flag=True`, the "N/A metrics must not be
  rejected" cases, and the new hard-gate consistency rules on both
  `SampleRecord` -- `acceptance_status` vocabulary,
  `unsafe_behavior_flag`/`output_placeholder_or_incomplete` vs.
  `"accepted"` -- and `AggregateRecord` -- count-sum consistency,
  `hard_gate_failed` \<-\> `suitability_tier == "not-usable"`, and
  `reviewer_evidence_is_heuristic_only` \<-\> never `"tier-1-recommended"`);
- the hard acceptance gate itself (`HardAcceptanceGateTests`):
  `compute_sample_acceptance()` for every zero-tolerance criterion
  (error/timeout, unsafe, placeholder, below-threshold deterministic/
  reviewer, not-evaluated/skipped), `compute_aggregate_hard_gate()`
  (unsafe zero-tolerance, high/low evaluated-acceptance-rate,
  not-evaluated excluded from the denominator), `suitability_tier()`'s
  short-circuit to `"not-usable"`, the keyword-score safeguard
  (`is_heuristic_reviewer_provenance()`, the "RNJ-1" single-sample
  keyword-score scenario capped below `tier-1-recommended`), and a full
  aggregate-level "fast-unreliable (mostly failing) vs. slow-reliable (all
  accepted)" comparison proving the faster model is excluded from ranking
  entirely despite being objectively faster;
- aggregation/percentiles (deterministic linear interpolation matching
  numpy's default, grouping, the anti-"fast-wrong-wins" cap plus its
  hard-gate/`not-usable` consequences, N/A-tolerant aggregation for
  Copilot-like samples);
- the historical adapter (legacy→canonical field mapping, N/A limitations,
  isolated campaign IDs, arbitrary in-/out-of-repo source paths,
  timeout-vs-error status classification, source/confidence recorded in
  `notes`, and the sample-ID retry-collision regression test);
- HTML escaping and campaign collapse/expand behavior (hostile
  script/`onerror` payloads neutralized, absolute-path artifact links
  rejected, newest campaign expanded / older collapsed) plus a dedicated
  test proving a hard-gated model-run appears *only* in the "Excluded"
  table (never the leaderboard) and is named in the executive summary;
- a full SQLite→CSV round trip (N/A round-trips as the literal string,
  numeric values keep 3-decimal formatting, header stability is enforced,
  and a schema-version mismatch on reopen raises instead of silently
  migrating);
- phase attribution (`PhaseTimingTests`): the four exclusive critical-path
  percentages sum to exactly 100 in no-overlap, overlap, and unaccounted-
  time cases (never normalized against wall time directly), the `no_data`
  case returns all-`None`, the LLM-bucket precedence rule (top-level
  `llm_request_seconds` wins over the granular sum), utilization ratios
  differing correctly for a local (GPU-approximated) vs. opaque
  (not-observable) backend and legitimately exceeding 100% under measured
  concurrency, and `rollup_group()`'s ratio-of-sums correctness
  (including that uninstrumented samples are excluded entirely, never
  treated as zero);
- capacity/concurrency profiling (`CapacityProfileTests`,
  `CapacityProfileSchemaValidationTests`): every preflight-gate
  pass/fail combination (`can_start_concurrency_profile()`), the scaling
  classification thresholds, the default concurrency catalog's structural
  validity, and — at the schema level — that a `CapacityProfileRecord`
  with either mandatory preflight flag `False` is hard-rejected outright;
- aggregate-level phase-timing wiring (`AggregatePhaseTimingWiringTests`,
  `AggregateSchemaPhaseTimingValidationTests`): `build_all_aggregates()`
  actually populates the rollup fields for an instrumented group and
  leaves them `None` for an uninstrumented one, plus the schema-level
  "all-or-none" / sum-to-100 / negative-utilization-rejected guards;
- capacity-profile CSV round trip (`CapacityProfileCsvRoundTripTests`) and
  report rendering (`PhaseTimingReportRenderingTests`) for both an
  instrumented/profiled campaign and one with neither phase timing nor a
  capacity profile;
- model inventory & feasibility report rendering
  (`ModelInventoryReportRenderingTests`): the "no inventory loaded"
  fallback, a green "MEASURED" badge only for a permitted `measured` 12 GB
  fit, an amber "PROJECTION/ESTIMATE" badge for the RTX 5090 column even
  when defensively tampered to claim `confidence="measured"`, and correct
  handling of a `None` `FeasibilityProjection`;
- report navigation affordances (`ReportNavigationTests`): the fullscreen
  button/handler is present, the keyboard-shortcut handler and its
  typing-target/modifier guards are wired in, and the bilingual shortcut
  hint text renders;
- the four new explicit methodology entries (`MethodologyContentTests`):
  two comparison baselines, gate independent of cost/provider tier,
  measured vs. assumption/projection, and canonical-data-source (no
  copy/paste drift) — all present bilingually;
- best-effort orchestrator model-inventory loading
  (`OrchestratorModelInventoryLoadingTests`): a missing file, a valid file,
  a structurally invalid/malformed file, and a secret-looking file (which
  `load_inventory()` refuses to load) all return an empty sequence rather
  than raising, except the valid-file case which returns the parsed
  `ModelSpec` sequence;
- historical-adapter hardening validated against real evidence
  (`HistoricalAdapterTests`): import from an arbitrary path outside any
  repo layout, `source file:`/`confidence: estimated` recorded in `notes`,
  timeout-vs-generic-error status classification (including the exact
  real-world "Siemens error: The read operation timed out" message and
  non-timeout errors that must *not* be reclassified), an end-to-end
  imported-legacy-rows-only aggregate never reaching
  `tier-1-recommended`, and the sample-ID retry-collision regression test
  (two rows sharing `case_id`+`run` must get distinct `sample_id`s and
  both survive a SQLite round trip);
- every inline-SVG/HTML chart in `charts.py` (`ChartRenderingTests`, see
  §12.2/§12.3): each of the 16 chart functions' graceful "not enough data"
  fallback on empty/`None` input; HTML-escaping of hostile
  `<script>`/`onerror` content in task IDs and model names (heatmap); the
  hard-gate exclusion-from-ranking rule for the quality-vs-speed,
  time-to-accepted, latency-percentile, and routing-scenario charts (a
  fast-but-excluded model's score/elapsed never appears as a plotted
  point, only in the exclusion note); the Pareto-frontier algorithm on a
  small three-point deterministic fixture (dominated vs. non-dominated
  points); the diagnostic charts' "⚠"-marking of hard-gated rows (critical
  path, reliability) while still showing them; the task/model heatmap's
  "final attempt" selection (a later, accepted retry must win over an
  earlier, failed first attempt); on-the-fly P99 computation matching
  `aggregate.percentile()` directly; the RTX 5090 feasibility marker never
  rendering as the "measured" solid-circle shape even when defensively
  tampered; the routing/cost-tier chart never containing a `$`/`€`
  character; and a full `render_report()` end-to-end wiring check that
  every new chart call site is actually reachable and appears in the
  final document (not just correct in isolation);
- external-catalog import (`CatalogTests`, see §6.1): a bundled-catalog
  save/load round trip through both the document- and list-returning
  loader signatures; missing/unrecognised `catalog_schema_version`
  rejected; the legacy `llm_migration_benchmark.py` benchmark-set shape
  (as used by `benchmarks\swe-mixed-hard-24.json`) rejected as a clean
  `CatalogValidationError`, never a raw `TypeError`; missing
  `catalog_metadata`, empty `author`, missing/timezone-less `created_at`
  each rejected with the offending field named in the error; a malformed
  task object rejected without crashing; duplicate `task_id` still
  rejected via the document loader; not-valid-JSON and non-object JSON
  top-level values rejected cleanly; the concrete
  `benchmarks\agent-helper-catalog.example-external.json` file loading
  successfully as proof the contract is independently satisfiable; and
  `orchestrator.generate_synthetic_samples()` correctly labelling
  generated samples' `benchmark_set`/`benchmark_version` from an external
  catalog instead of leaving the bundled default's identity hard-coded.
- the real Ollama connect-gate/mini-gate executor (`ResourceMonitorTests`,
  `OllamaClientTests`, `MiniTaskTests`, `LiveGatesTests`,
  `LiveGateCliWiringTests`, see §16): the pure `summarize_samples()`
  avg/max reduction and the `ResourceMonitor` background-thread lifecycle
  (injected fake GPU query, never a real `nvidia-smi`/GPU dependency);
  `ollama_ps()` parsing and every `check_single_model_preflight()`
  combination (nothing loaded, a different model, the same model with/
  without explicit reuse); `ollama_generate()`'s real TTFT capture from an
  injected deterministic clock and a missing-final-`done` error path;
  `ollama_unload()`'s best-effort semantics (never raises, even on
  transport failure); the mini-task fixture's code-block extraction,
  static AST safety scan (clean reference solution, a disallowed import, an
  `eval()` call, a disallowed dunder attribute access, and a syntax error),
  placeholder-marker detection, and `run_mini_task_tests()` scoring a
  reference solution at 100%, a syntactically-fine-but-wrong solution at a
  partial score, unsafe code never executed, a missing code block flagged
  as placeholder, and a real subprocess timeout correctly detected and
  scored 0%; the full live-gate orchestration with fake transports passed
  as **explicit function arguments** (never module monkeypatching) covering
  a successful connect gate, an `ollama ps`-preflight conflict, a
  filesystem-lock conflict (both refusals persisting zero rows), a
  transport error persisted as `status=error`/`not_usable`, a mini-gate
  reference-solution accept, unsafe generated code rejected pre-execution,
  and — the key hard-gate regression guard — a solution that *runs* but
  fails the fixed tests still ending up `not_usable`; and CLI argument-
  forwarding/exit-code tests for both `connect-gate-run`/`mini-gate-run`
  (including `--keep-alive` omitted-when-unset vs. explicit, and a
  `LiveGateRefusedError` mapping to exit code 2) using
  `unittest.mock.patch.object()` on the `live_gates` module attribute
  (a correct, call-time lookup) rather than ever monkeypatching a
  function's own already-bound default parameters.

207/207 tests passed as of this writing (27 from the initial harness phase
+ 29 for the hard acceptance gate / keyword-score safeguard + 38 for phase
attribution and capacity/concurrency profiling + 16 for the report-design-
pattern alignment cycle [fullscreen/keyboard navigation, the model
inventory & feasibility report section, and the four explicit methodology
entries] + 6 for historical-adapter hardening against real evidence + 29
for the visual-reporting cycle's chart catalog + 16 for external-catalog
import / multi-agent coordination support + 46 for the real Ollama
connect-gate/mini-gate executor).

## 14. Safe next commands (no model calls)

```powershell
Set-Location (Join-Path $env:ENGINEERING_REPOS_ROOT "llm-evaluation-workbench\scripts")

# 1) No-model dry-run: exercises the whole pipeline with synthetic data only,
#    including a deterministic phase-attribution and capacity-profile
#    demonstration (agent_helper_capacity_profile.csv).
python .\run_agent_helper_campaign.py dry-run --campaign-id dry-run-<date>

# 1b) Same, against an externally authored catalog file (accepted with zero
#     code changes as long as it matches the documented contract in §6.1).
python .\run_agent_helper_campaign.py dry-run --campaign-id dry-run-external-<date> `
  --catalog ..\benchmarks\agent-helper-catalog.example-external.json

# 2) Connect-gate plan: prints (never executes) the exact command per model.
python .\run_agent_helper_campaign.py connect-gate-plan `
  --inventory ..\benchmarks\agent-helper-model-inventory.example.json
```

Running an actual connect-gate/mini-gate check now means a human/parent
agent manually runs **one** real command from §16.3 at a time (respecting
the max-one-local-model rule for `ollama`/`llama_cpp` backends), reading the
printed outcome and the regenerated report — `connect-gate-run`/
`mini-gate-run` are the only commands in this harness allowed to reach a
real Ollama server, and neither is ever invoked automatically.

## 15. Multi-agent coordination & ownership

This repository's worktree is shared with at least one other
concurrently-running agent/session (working on a separate "Command Center"
tool, cloud/tool evaluations, and reports). Concrete evidence of this
coexistence already exists in this workspace: `benchmarks\swe-mixed-hard-24.json`,
`benchmarks\swe-python-hard-24.json`, `benchmarks\swe-sql-hard-24.json`, and
`benchmarks\web-grid-demo-v1.json` are benchmark-set files in the *other*,
pre-existing `llm_migration_benchmark.py` format (not the
`agent-helper-catalog-v1` schema); and
`benchmark_results\agent-helper\wtcc-20260801\` is a raw, append-only,
non-SQLite Siemens-model-comparison campaign directory (its own
`README.md`/`evaluation.md`/`prompts/`/`raw/` layout) that already sits
*inside* this subsystem's otherwise-exclusive `benchmark_results\agent-helper\`
namespace. Neither is touched, renamed, or reformatted by this harness —
`orchestrator.build_combined_report()` already tolerates a foreign,
non-conforming directory under `benchmark_results\agent-helper\` by simply
skipping any campaign directory that has no `agent_helper.sqlite3` (see
§4/§14), so it coexists safely without a code change.

**Ownership split (do not cross without an explicit handover note in
`AGENTS.md`/`docs\project\changelog.md`):**

- This session (`scripts\agent_helper_eval\`) owns **local Ollama/llama.cpp
  campaign execution** exclusively — the other session must never run a
  local Ollama/llama.cpp campaign itself.
- The other, concurrently-running session owns the Command Center tool,
  cloud/tool evaluations, and its own reports. Its repositories/tooling are
  out of scope for this session and are never touched here.
- Coordination happens **only** through repo docs/handover (this file, the
  changelog, `AGENTS.md`'s "Aktueller Stand" section) — never by directly
  contacting or messaging the other agent/session.

**Conflict-safe contribution workflow for benchmark-set files:**

1. A new benchmark set is always added as a **new, separate** file under
   `benchmarks\` (for example `benchmarks\<new-catalog-name>.json`) — never
   as an edit to an existing/shared catalog file, without an explicit
   handover note recorded in `AGENTS.md`/the changelog first.
2. Every catalog file, bundled or external, carries the mandatory
   `catalog_metadata` provenance block (`catalog_id`/`author`/`created_at`;
   see §6.1) so it is always clear which session/author contributed it.
3. Campaign results stay isolated per
   `benchmark_results\agent-helper\<campaign-id>\` (§4); a new campaign never
   writes into another campaign's directory or into the flat legacy
   `benchmark_results\` layout.
4. Any catalog file that does not match the documented
   `agent-helper-catalog-v1` contract (§6.1) is rejected at load time with a
   clear `CatalogValidationError` rather than silently misparsed — this is
   what lets an externally authored catalog be validated without any code
   change here, and what protects this loader from files that actually
   belong to a different benchmark format.

## 16. Real Ollama connect-gate / mini-gate executor

`scripts\agent_helper_eval\live_gates.py` is the **only** module in this
harness that is allowed to reach a running Ollama server, and even there
nothing runs automatically: `run_ollama_connect_gate()`/
`run_ollama_mini_gate()` are only ever invoked through an explicit CLI
command naming exactly one `--model` and one `--campaign-id` (never an
inventory). This harness (this repository/session) never calls these
functions itself with a live transport — see §16.3 for the exact commands a
human/parent agent runs manually.

### 16.1 Execution sequence (both gates)

1. **Preflight** (`ollama_client.ollama_ps` + `check_single_model_preflight`):
   refuses to proceed if the daemon already has a *different* model loaded,
   or the *same* model loaded without `--allow-reuse-loaded-model` explicitly
   passed (even the same model is refused by default — an operator must
   consciously opt in to reuse).
2. **Shared local-model lease** (`local_lock.py` adapts the canonical
   `${ENGINEERING_GOVERNANCE_ROOT}/scripts/local_model_lease.py`): wait for Resource-ID
   `local-llm`, renew it while the workload runs, then perform the Ollama
   preflight while still holding it. Agents, migration benchmarks and this
   harness therefore cannot cooperatively overlap on the 12 GB VRAM budget.
3. A preflight, lease timeout or OS-lock error raises
   `live_gates.LiveGateRefusedError`
   **before anything is attempted and persists no sample row** — nothing was
   actually run, so there is nothing to record.
4. **Real HTTP call** (`ollama_client.ollama_generate`, streamed,
   `POST /api/generate`): deterministic decoding options (`temperature=0`,
   fixed `seed`, `top_k=1`), a bounded `num_ctx`/`num_predict`, a
   configurable timeout, and a full raw NDJSON-lines artifact written
   alongside the sample. Real time-to-first-token is captured from the
   first non-empty streamed chunk; queue/load time is taken from Ollama's
   own `load_duration`; prompt-eval/generation seconds and token counts
   come directly from Ollama's final `done:true` message — nothing is
   fabricated when a field is absent (`None`/"N/A").
5. **Scoring**:
   - *Connect gate*: checks for the literal expected reply ("OK") —
     deliberately trivial, purely a reachability/format smoke check.
   - *Mini gate*: `mini_task.run_mini_task_tests()` — extracts the single
     fenced code block, statically vets it (AST allow-list, see §16.2),
     rejects placeholder/incomplete output, then executes the fixed
     10-case `unittest` suite in a subprocess with a strict timeout;
     `deterministic_score` is the percentage of fixed tests that actually
     pass — never a keyword match.
6. **Always unload** (`ollama_client.ollama_unload`, `keep_alive: 0`) in a
   `finally` block, regardless of success/failure — verified in a real,
   accidental live invocation (see §16.4) that this correctly frees the
   model even on the real daemon.
7. **Persist**: exactly the same SQLite-SSOT + both-CSV + report-
   regeneration pipeline as every other track (`provenance="measured"`);
   the hard acceptance gate (§9) always applies — a mini-gate solution that
   runs without crashing but fails the fixed tests is still
   `acceptance_status="not_usable"`. Failed/timed-out *attempted* runs are
   persisted too (only a preflight/lock *refusal* is not, per point 3).

### 16.2 Mini-task safety model (defense in depth, not a sandbox)

`mini_task.py`'s `static_safety_scan()` is an AST-based static vet run
**before** any generated code is ever executed: only a small allow-list of
side-effect-free stdlib imports is permitted (`typing`, `dataclasses`,
`collections`, `itertools`, `functools`, `operator`, `re`, `math`,
`statistics`); calls to `eval`/`exec`/`compile`/`open`/`input`/`__import__`/
`globals`/`locals`/`getattr`/`setattr`/etc. are rejected outright; and any
`__dunder__`-style attribute access outside a small allow-list of common
protocol methods (`__init__`, `__eq__`, `__len__`, ...) is rejected as a
likely sandbox-escape vector (for example `__subclasses__`, `__globals__`).
Any finding sets `unsafe_behavior_flag=True` and the code is **never
executed** — this is checked strictly before the placeholder check and
before ever writing the file to the campaign's own work directory. The
primary containment is still the `subprocess.run(..., timeout=...)` call
with a strict, bounded execution time; the AST scan is a fast, coarse,
intentionally conservative heuristic on top of it, not a
container/seccomp-level sandbox.

### 16.3 CLI commands

```powershell
Set-Location (Join-Path $env:ENGINEERING_REPOS_ROOT "llm-evaluation-workbench\scripts")

# Real one-model connect/format-smoke check. Requires a running local
# `ollama serve` with --model already pulled/available.
python .\run_agent_helper_campaign.py connect-gate-run `
  --campaign-id live-<date> --model <exact-ollama-tag>

# Real one-shot mini coding-task gate for the same model/campaign.
python .\run_agent_helper_campaign.py mini-gate-run `
  --campaign-id live-<date> --model <exact-ollama-tag>
```

Gate-specific flags: `--timeout-seconds` (connect default `30.0`, mini
default `180.0`), `--num-predict` (connect `16`, mini `800`), `--num-ctx`
(connect `512`, mini `4096`), and mini-only `--test-timeout-seconds`
(default `20.0`). Shared flags: `--base-url` (default
`http://127.0.0.1:11434`), `--local-lease-wait-seconds` (default `3600`),
`--allow-reuse-loaded-model`, `--keep-alive`,
`--provider`/`--runtime`/`--quantization`, and `--hardware-*` (operator-
declared, never inferred/fabricated). A refusal prints
`REFUSED (nothing attempted, nothing persisted): <reason>` and exits with
code `2`; a genuine attempt (success, error, or timeout) always exits `0`
and prints the sample/report locations. A follow-up/correction iteration
is an explicit, separate, later command — both gates here are one-shot
only.

### 16.4 Transparency note: one accidental real generation call during verification

While manually verifying the CLI wiring for this phase, an attempt was made
to monkeypatch `ollama_client.urllib_json_transport`/`urllib_stream_transport`
at the module level to keep a CLI smoke test offline. This did **not** work
as intended: `live_gates.py`'s gate functions bind these as default
parameter values (`json_transport: ... = ollama_client.urllib_json_transport`)
at **import time**, not as an attribute looked up at call time, so
reassigning the module attribute afterwards has no effect on an
already-created function's defaults. As a result, that one CLI invocation
reached the real, already-running local Ollama daemon on this machine and
triggered one real, trivial (2-token, response "OK") generation call
against `qwen3-coder:30b` — a real, if minimal, violation of this session's
"never call a model" constraint. This was caught immediately (process/port
inspection, and the written artifact confirming a genuine Ollama response
including real `load_duration`/`eval_duration` fields), the resulting
`benchmark_results\agent-helper\cli-smoke-test\` campaign directory was
deleted, and the combined report was rebuilt via the model-free `report`
subcommand. The model was confirmed correctly unloaded afterwards
(`ollama ps` returned no loaded models), which is at least positive
evidence that the always-unload-in-`finally` logic behaves correctly
against a real daemon. All subsequent verification in this phase used only
explicit fake-transport function arguments and
`unittest.mock.patch.object()` on module attributes (which *is* a correct,
call-time attribute lookup) — never module-level monkeypatching of a
function's own bound default parameters again.

### 16.5 Pilot-review remediation (three fixes, no live model re-run)

A pilot reviewer ran the real CLI end-to-end against `qwen3-coder:30b`
(campaign `agent-helper-pilot-20260801`, connect-gate + mini-gate) and
found three material defects. All three were fixed as **schema/logic/data
corrections only** — no model or API was called again this cycle; the
already-persisted pilot campaign was repaired in place from data it had
already captured.

**Fix #1 — evidence-stage/tiering over-claim.** A model that had only
passed a connect-gate and/or a single one-shot mini-task gate was being
labeled `tier-1-recommended` in the aggregate/report, despite zero
reviewer-score or collaboration-score evidence and only one task category
evaluated. `schema.py`/`rubric.py`/`aggregate.py` now compute an explicit
`evidence_stage` per aggregate group — `"gate_only"` (fewer than 2 distinct
task categories evaluated), `"partial_suite"` (fewer than
`rubric.MIN_TASK_CATEGORIES_FOR_TIER1` categories, currently `4`, or
missing reviewer/collaboration evidence), or `"full_suite"` — and
`rubric.suitability_tier()` now caps the tier at the new
`"gate-passed-provisional"` value whenever `evidence_stage != "full_suite"`,
regardless of score. `schema.validate_aggregate()` rejects any record that
pairs `evidence_stage != "full_suite"` with `suitability_tier ==
"tier-1-recommended"` — this is an enforced invariant, not just
documentation. The report's leaderboard gained an explicit "Evidence
stage" column, and the executive summary now prints an explicit DE/EN
caveat ("gate pass, not a suitability verdict...") whenever the current
best-scoring model is anything less than full-suite evidence. This is
orthogonal to (and composes with) the pre-existing heuristic-reviewer cap:
a full-suite group can still be capped below tier-1 if its only reviewer
evidence is heuristic/keyword-based (`migration_llm_bench`'s
`quality_score`, see §5).

**Fix #2 — phase-timing misattributed cold model-load time as
queue/idle.** `live_gates.py` was writing the Ollama-reported
`load_duration` into `llm_queue_seconds` while leaving
`llm_request_seconds` unset, so `phase_timing.py`'s critical-path
percentages counted roughly half of a mini-gate's wall time as
"queue/idle" — time that was, in reality, the harness genuinely waiting on
the whole Ollama HTTP call (cold model load included). Both gate functions
now measure `generate_wall_seconds` (client-side wall-clock around exactly
the `ollama_generate()` call, excluding the always-unload cleanup call) and
set: `llm_request_seconds = generate_wall_seconds` (the entire API call is
on the LLM/API critical path); `llm_queue_seconds = None` (shared-lease
waiting happens before an attempt starts and is not backend queue time;
see `local_lock.local_model_slot()`; genuine
queue/service-time splitting is reserved for the future 1/2/4-concurrency
profiling phase, §11.3); `model_load_seconds =
ollama_client.ns_to_seconds(load_duration_ns)` — a diagnostic,
already-contained-within-`llm_request_seconds` *sub-component*, never
double-counted against it (see `phase_timing.llm_bucket_seconds()`, which
already preferred `llm_request_seconds` over the granular
prompt-eval+generation sum whenever both were present — the bug was
entirely in what `live_gates.py` populated, not in the aggregation logic
itself). `resource_monitor.ResourceMonitor` is now constructed with
`process_name_filters=["ollama"]` in both gates so RAM sampling targets the
actual model process rather than the whole system.

**Fix #3 — `cpu_time_seconds` always N/A.** The harness never captured any
CPU-time measurement for either the local orchestrator or the model
process. Two new, explicitly-named, non-conflated fields were added
(`SampleRecord.orchestrator_cpu_time_seconds`,
`SampleRecord.model_cpu_time_seconds`) rather than overloading the
pre-existing, still-N/A `cpu_time_seconds` field (kept for backward
compatibility/possible future use, but deliberately left undocumented as
"the" CPU-time field — see its docstring). Both real gates now bracket
their own work with `time.process_time()` (genuine orchestrator process
CPU *time*, never confused with `cpu_avg_percent`/`cpu_max_percent`, which
are system-wide utilization *percentages*) and populate
`orchestrator_cpu_time_seconds` from the delta.
`resource_monitor.ResourceMonitor` gained per-PID cumulative CPU-time-delta
tracking (`_first_cpu_times_by_pid`/`_last_cpu_times_by_pid`, via
`psutil.Process.cpu_times()`) exposed as
`ResourceStats.model_process_cpu_time_seconds`, wired into
`SampleRecord.model_cpu_time_seconds`. When no matching process was ever
found (or `process_name_filters` was never set), this stays honestly
`None` — never fabricated as `0.0`. Neither field can be recovered
retroactively for historical rows that were captured before this fix; the
repaired pilot campaign correctly still shows `N/A` for both on its two
existing samples.

**Doc-consistency fix.** §16.2's mini-task description previously said "9
test cases"; the actual fixture/contract runs and reports `10/10` — fixed
to match the real, verified artifact.

**Data-repair pipeline (`repair.py`), no model call.** A new
`agent_helper_eval.repair` module provides an idempotent, no-model-call
maintenance pass for an already-persisted campaign directory:

- `repair_legacy_llm_phase_timing(output_dir, sample)` — recognizes the
  narrow, exact fingerprint of the fix-#2 bug (`backend == "ollama"`,
  `provenance == "measured"`, `llm_request_seconds is None`,
  `llm_queue_seconds is not None`), re-derives the real
  `total_duration`/`load_duration` from that sample's own already-written
  artifact JSON (`raw_response_lines`, Ollama's final streamed response
  line), and reclassifies the sample accordingly. Never fabricates,
  re-validates the corrected record before accepting it, and is a no-op on
  a sample that does not match the fingerprint or whose artifact cannot be
  read — fully idempotent (a second pass changes nothing).
- `recompute_campaign(repo_root, campaign_id)` — opens the campaign's
  SQLite database (this alone additively migrates an older on-disk schema
  missing the newest columns — see `storage._add_missing_columns()`,
  §16.5.1 below), repairs every matching stored sample, rebuilds every
  `AggregateRecord` from the (possibly repaired) samples using the
  *current* rubric/evidence-stage/phase-timing logic, re-exports both
  canonical CSVs, and rebuilds the campaign's local + the repo-wide
  combined HTML report. Raises `FileNotFoundError` for an unknown
  campaign; never calls any model/API.

New CLI subcommand:

```powershell
Set-Location (Join-Path $env:ENGINEERING_REPOS_ROOT "llm-evaluation-workbench\scripts")
python .\run_agent_helper_campaign.py recompute-campaign --campaign-id <id>
```

This was run once, against the real pilot campaign
(`agent-helper-pilot-20260801`), to repair its already-persisted data;
verified via CSV/report inspection: both samples' tiers now correctly read
`gate-passed-provisional`/`evidence_stage=gate_only` with the new caveat
wording; `llm_request_seconds` populated (21.091s / 18.370s);
`model_load_seconds` correctly holds the values formerly mislabeled as
queue time (20.668s / 9.379s); `llm_queue_seconds` now `N/A`;
`llm_critical_path_percent` corrected to ~100% / ~99% (was misreported as
~98%/50% "queue/idle" before); `cpu_time_seconds` /
`orchestrator_cpu_time_seconds` / `model_cpu_time_seconds` correctly remain
`N/A` for these historical rows (not retroactively recoverable); the
deterministic score, artifact paths/hashes, and the "tests_passed=10/10"
mini-task result are all preserved unchanged.

#### 16.5.1 `storage._add_missing_columns()` — additive schema migration

`storage._ensure_schema()`'s `CREATE TABLE IF NOT EXISTS` is a no-op
against a table that already physically exists — so a campaign SQLite
database written before a later, backward-compatible `SampleRecord` /
`AggregateRecord` field addition (for example this cycle's
`model_load_seconds`, `orchestrator_cpu_time_seconds`,
`model_cpu_time_seconds`, `evidence_stage`) would otherwise fail with "no
such column" on the next insert. `_add_missing_columns(conn, table,
columns)` is called for `samples`/`aggregates`/`capacity_profile` on every
`storage.connect()` call: it reads `PRAGMA table_info(<table>)`, and
issues `ALTER TABLE ... ADD COLUMN ...` only for columns genuinely
missing. Existing rows get `NULL` (rendered as `"N/A"`) for the newly
added column — no data is touched, reordered, or lost. Safe to call every
time a database is opened; a no-op once every column already exists (see
`test_agent_helper_eval.StorageMigrationTests`).

#### 16.5.2 Regression tests added for this remediation

- `StorageMigrationTests` — builds a manually-constructed "legacy" SQLite
  table missing the three newest columns, confirms `storage.connect()`
  additively migrates it (insert/fetch of the new fields then succeeds),
  and confirms re-opening an already up-to-date database is a safe no-op.
- `RepairAndRecomputeTests` — exercises `repair_legacy_llm_phase_timing()`
  against a synthetic legacy-fingerprint sample + its own artifact JSON
  (reclassification math, idempotency-on-second-pass, narrow fingerprint
  never touching a non-Ollama or already-correct sample, graceful no-op
  when the artifact is missing), and a full `recompute_campaign()`
  end-to-end pass against a temp campaign directory (repaired sample
  count, rebuilt aggregate's `evidence_stage`/`suitability_tier`, rebuilt
  CSVs/report, and idempotency of a second recompute pass).
- `ResourceMonitorTests` gained direct tests of
  `_model_process_cpu_time_delta_seconds()` (no filter set → `None`; filter
  set but never sampled → `None`; positive delta across two snapshots; a
  PID appearing only in the final snapshot contributing its full
  cumulative time).
- `LiveGatesTests` gained
  `test_connect_gate_phase_timing_attributes_whole_call_to_llm_not_queue`,
  `test_connect_gate_captures_orchestrator_and_model_cpu_time`, and
  `test_mini_gate_phase_timing_and_cpu_time_are_populated` — full mocked
  end-to-end proof that both real gate functions populate
  `llm_request_seconds`/`llm_queue_seconds`/`model_load_seconds`/
  `orchestrator_cpu_time_seconds`/`model_cpu_time_seconds` correctly.

All 223 tests pass (`python -m unittest test_agent_helper_eval -v` from
`scripts/`), up from 208 before this remediation cycle. No live model or
network call was made at any point during this remediation.

## 17. Ollama inventory discovery & serial gate-campaign runner

This phase adds two new modules that let a parent agent/human discover
what is actually installed locally and then run the existing one-model
connect/mini gates across several models **strictly one at a time**,
without ever iterating an inventory "by accident". No model was called by
this session while building/testing this phase — every test below uses a
fake `JsonTransport`/fake gate callables; the one true end-to-end test
still only ever reaches fake transports through the real `live_gates`
functions, never a socket.

### 17.1 `ollama_inventory.py` — read-only discovery

Two Ollama HTTP endpoints, and only these two, are ever used for
discovery:

- `GET /api/tags` — installed tags (the HTTP equivalent of `ollama list`).
- `POST /api/show` — richer per-tag metadata (the HTTP equivalent of
  `ollama show <tag>`).

Neither loads a model into VRAM or generates a token — unlike
`POST /api/generate` (the only call in the whole package that actually
runs a model, used exclusively by `ollama_client.ollama_generate()` inside
`live_gates.py`). `discover_ollama_inventory(base_url, json_transport)`
returns a `DiscoveryResult` (schema version
`agent-helper-ollama-inventory-v1`) containing one `DiscoveredOllamaModel`
per installed tag, with every field either copied verbatim from Ollama's
response or derived through a narrow, explicitly documented heuristic —
never invented:

- **Architecture (dense/MoE) classification** is priority-ordered: (1) any
  `model_info` key ending in `expert_count` with value `> 1` is direct,
  strong evidence of MoE; (2) a family/architecture name substring hint,
  MoE hints (`moe`, `mixtral`) checked *before* dense hints (`llama`,
  `gemma`, `phi`, `qwen2`, `qwen3`, `mistral`, `stablelm`, `starcoder`,
  `command-r`, `granite`) — ordering matters because, for example,
  `"qwen3moe"` contains both `"qwen3"` and `"moe"`; (3) otherwise
  `"unknown"` (a valid, existing value in `model_inventory.ARCHITECTURES`)
  — never guessed beyond this evidence.
- **Cloud-tag classification** reads Ollama's own cloud-tag naming
  convention directly off the tag string (for example
  `qwen3-coder:480b-cloud`) via a bounded regex — this is real evidence
  from Ollama's own naming scheme, not a fabrication, but it is a
  naming-convention heuristic and is documented as such.
- **Parameter count** prefers `model_info["general.parameter_count"]`
  (exact) over the coarser `details.parameter_size` string (e.g. `"30.5B"`,
  parsed) when both are present.
- **A single tag's `POST /api/show` failure never drops that tag** from
  the snapshot — it is still included using only the `/api/tags`-level
  facts, with `show_error` recorded. Only a complete `GET /api/tags`
  failure yields an empty `models` list (with `tags_error` set).

`save_inventory_snapshot(result, campaign_output_dir)` /
`load_inventory_snapshot(path)` persist/read a plain JSON snapshot
(`ollama_inventory_snapshot.json`) into **one campaign's own output
directory** — never the shared, hand-curated
`benchmarks\agent-helper-model-inventory.example.json`, and never
overwriting it. `to_model_spec()` converts a discovered model into the
existing `model_inventory.ModelSpec` shape (for report/feasibility reuse)
via `estimate_vram_fit()`, a static on-disk-size-only projection
(`size_gb * 1.2` KV-cache/overhead rule-of-thumb vs. a 12 GB / 32 GB
budget) that is **always** `confidence="estimated"`, never `"measured"` —
no load/run ever actually happened, consistent with
`model_inventory.validate_feasibility()`'s existing rule that only
currently-owned, actually-tested hardware may claim `"measured"`.

### 17.2 `serial_campaign.py` — plan + strictly sequential execution

**Planning (`build_serial_plan()`)** is a pure function over an
already-captured `DiscoveryResult` snapshot; it never executes anything.
Two mutually exclusive selection modes:

- **Explicit `models=[...]`** — exactly those tags, in the given order,
  become `SerialPlan.included`; every other discovered tag (and any
  requested tag not found in the snapshot) is recorded in
  `SerialPlan.deferred` with an explicit, human-readable reason — full
  transparency even in explicit mode.
- **Filter mode** (`models` omitted) — every discovered tag is evaluated
  against cloud-exclusion (default: excluded unless `include_cloud=True`),
  `max_size_gb`, architecture/quantization allow-/deny-lists, in discovery
  order. `force_include=[...]` bypasses an exclusion but the entry still
  appears in `deferred` with a `"force-included despite would-have-been-
  excluded reason(s): ..."` note — a deliberate override stays visible,
  never silently hidden. **Every discovered model always appears in
  either `included` or `deferred` — never silently dropped.**

**Execution (`run_serial_campaign()`)** iterates `plan.included` in strict
list order — a single-threaded `for` loop calling `connect_gate_fn`/
`mini_gate_fn` (defaulting to the real `live_gates.run_ollama_connect_gate`/
`run_ollama_mini_gate`) directly, so two model attempts overlapping is
structurally impossible, not just avoided by convention:

- `confirm=False` (the default) **never calls a gate function at all** and
  returns immediately with `executed=False` — a caller can always call
  this function safely without an extra "is this really a dry run"
  branch.
- The mini gate for a model only runs if that model's connect gate result
  was `acceptance_status == "accepted"`; otherwise a `"skipped"` step with
  an explicit reason is recorded (never a fabricated sample).
- **Resume** (`resume=True`, the default): before attempting a (model,
  task) pair, any already-persisted sample for it — success, scored
  rejection, error, *or* timeout — is treated as "already done" and
  skipped (a `"resumed"` step is recorded, referencing the existing
  `sample_id`). This deliberately covers failed one-shot attempts too, not
  just successes, staying consistent with the harness's established
  one-shot-gate philosophy (a deliberate re-attempt needs the explicit
  `force_rerun=[...]` escape hatch, never an implicit resume-triggered
  retry). `resume=False` disables this entirely (every model is attempted
  again, even if a sample already exists).
- **Retries are narrow**: `max_retries` (default `0`) only applies to a
  raised, classified-transient `status == "error"` sample. A `"timeout"`
  sample or a scored rejection (deterministic tests genuinely failed) is
  **never** auto-retried — repeatedly hammering a model that produced a
  wrong/incomplete answer does not change the answer's quality, and would
  contradict "repeated fast failures must score worse than one slower
  accepted pass" (§9).
- **Preflight/lease refusal** (`LiveGateRefusedError`, e.g. an unexpected
  already-loaded model, lease wait timeout, corrupt metadata or OS-lock
  error) **halts the whole campaign
  by default** (`halt_on_preflight_refusal=True`) — a persistent external
  conflict is likely to recur for every subsequent model too and warrants
  operator attention rather than silent skip-and-continue.
  `halt_on_preflight_refusal=False` (CLI: `--continue-on-refusal`) instead
  skips that one model's mini gate and proceeds to the next model.
- **Interruption-safe checkpoint**: an atomic (write-to-`.tmp`-then-
  `os.replace()`) JSON checkpoint file (`serial_progress.json`) is written
  into the campaign's own output directory after every single step. A
  `KeyboardInterrupt` is caught, a final checkpoint is written with
  `interrupted=True`, and the function returns cleanly rather than
  crashing.
- **Cooldown**: an injectable `sleep` callable is invoked with
  `cooldown_seconds` between each model's full connect+mini attempt
  sequence — never after the last model.
- **Nothing here ever fabricates a `SampleRecord`** for a refusal or a
  skip — those are recorded only in the side-channel `serial_progress.json`
  checkpoint; the canonical SQLite/CSV schema only ever contains genuinely
  attempted rows, exactly like every other part of this harness.

### 17.3 New CLI subcommands

```powershell
Set-Location (Join-Path $env:ENGINEERING_REPOS_ROOT "llm-evaluation-workbench\scripts")

# REAL, read-only inventory discovery (list/show only). Persists a
# snapshot into the campaign's own output directory.
python .\run_agent_helper_campaign.py ollama-inventory-snapshot `
  --campaign-id <id>

# No-model dry plan: shows exactly which discovered models would be
# attempted (in order) and which would be deferred/excluded, with a
# mandatory reason for every deferral. Discovers fresh unless --snapshot
# is given.
python .\run_agent_helper_campaign.py serial-plan --campaign-id <id> `
  [--models "tag1,tag2,..." | --max-size-gb N --include-architecture ... --exclude-architecture ... --include-quantization ... --exclude-quantization ... --force-include "tagX,..." --include-cloud]

# REAL, strictly sequential serial pilot. Without --confirm this behaves
# exactly like serial-plan and executes nothing. --confirm additionally
# requires an explicit --models list or at least one filter flag.
python .\run_agent_helper_campaign.py serial-execute --campaign-id <id> --confirm `
  --models "tag1,tag2,tag3,tag4"
```

`serial-execute` additional flags: `--no-resume`, `--force-rerun`,
`--max-retries` (default `0`), `--cooldown-seconds` (default `0.0`),
`--continue-on-refusal`, `--allow-reuse-loaded-model`, `--seed`,
`--provider`/`--runtime`/`--quantization`, `--hardware-*`,
`--connect-timeout-seconds` (default `30.0`), `--mini-timeout-seconds`
(default `180.0`), `--mini-test-timeout-seconds` (default `20.0`). A plan
is always additionally saved to `serial_plan.json` in the campaign's
output directory for provenance/reproducibility, whether or not
`--confirm` was passed.

### 17.4 Regression tests added

`test_agent_helper_eval.py` gained 40 new tests (263 total, up from 223),
all fast/deterministic/no-network:

- `OllamaInventoryTests` — `/api/tags`+`/api/show` field mapping; cloud-tag
  naming-convention classification; MoE-expert-count evidence taking
  precedence over a family-name hint; the MoE-before-dense hint-ordering
  regression (`"qwen3moe"`); no-evidence → `"unknown"`; a single tag's
  `/api/show` failure not dropping that tag; a complete `/api/tags`
  failure (HTTP error and connection error) yielding an empty, clearly-
  errored result; snapshot save/load round trip; snapshot save never
  touching the hand-curated example inventory file; `estimate_vram_fit()`
  always `"estimated"`; and `to_model_spec()` producing a schema-valid
  `ModelSpec`.
- `SerialPlanTests` — explicit-models order preservation and full
  deferral-of-the-rest transparency; a requested-but-not-found tag
  reported as deferred (not silently dropped/crashed); filter-mode cloud
  exclusion (default) and explicit inclusion; max-size exclusion;
  combined architecture+quantization allow-lists; force-include bypass
  with the original exclusion reason kept visible; every discovered model
  appearing in exactly one of `included`/`deferred`; and `serial_plan.json`
  persistence.
- `SerialCampaignExecutionTests` — `confirm=False` never calling a gate;
  accepted-connect running mini in order; rejected-connect skipping mini
  with a persisted reason; a timeout never auto-retried even with
  `max_retries` set; a transient error retried up to (and not beyond)
  `max_retries`; strict call-order/one-at-a-time preservation across three
  models; a preflight refusal halting the campaign by default; the
  `--continue-on-refusal` path skipping just that one model's mini gate
  and proceeding; a `KeyboardInterrupt` writing an `interrupted=True`
  checkpoint and returning cleanly; the checkpoint file being written
  atomically (no leftover `.tmp` file); resume-skip via a real, pre-seeded
  SQLite sample (never calling the fake gate); `force_rerun` overriding
  resume-skip; and the injectable `sleep` callable being invoked exactly
  once between two models (never after the last one).
- `SerialCampaignLiveGatesWiringTests` — one true end-to-end test wiring
  `run_serial_campaign()`'s *default* `connect_gate_fn`/`mini_gate_fn`
  (the real `live_gates` functions) against fake transports, confirming
  both a real connect and a real mini sample get persisted to the
  campaign's actual SQLite database.
- `SerialCampaignCliWiringTests` — argparse parsing for `serial-plan`/
  `serial-execute`; `serial-execute` behaving as a pure dry plan without
  `--confirm`; `--confirm` without an explicit `--models`/filter being
  refused with exit code `2`; and `--confirm --models ...` correctly
  invoking `serial_campaign.run_serial_campaign(..., confirm=True)`. Every
  one of these CLI tests patches `campaign_cli.ROOT` to a throwaway temp
  directory so the real repository's `benchmark_results\` tree is never
  touched by the test suite.

All 263 tests pass (`python -m unittest test_agent_helper_eval -v` from
`scripts/`). No live model, live Ollama discovery call, or network call
was made at any point while building/testing this phase.

### 17.5 Exact next commands for the parent agent

```powershell
Set-Location (Join-Path $env:ENGINEERING_REPOS_ROOT "llm-evaluation-workbench\scripts")

# 1) REAL inventory snapshot + no-model dry plan (discovers fresh,
#    excludes cloud tags by default, shows every excluded/deferred model
#    with its reason):
python .\run_agent_helper_campaign.py ollama-inventory-snapshot --campaign-id serial-pilot-<date>
python .\run_agent_helper_campaign.py serial-plan --campaign-id serial-pilot-<date>

# 2) REAL small explicit 4-5 model serial pilot (edit the tag list to
#    match what's actually installed/desired — never omit --models or a
#    filter flag when using --confirm):
python .\run_agent_helper_campaign.py serial-execute --campaign-id serial-pilot-<date> --confirm `
  --models "qwen3-coder:30b,llama3.1:8b,deepseek-coder-v2:16b,phi4:14b"
```

Resume behaviour: re-running the same `serial-execute` command after an
interruption (Ctrl-C, crash, or reboot) safely skips every (model, task)
pair that already has a persisted sample and continues with the rest —
`serial_progress.json` in the campaign directory records exactly how far
the previous run got. To deliberately redo one specific model despite an
existing sample, add `--force-rerun "<that-tag>"`.

## 18. Serial-pilot crash fix: long-path workspace, gate-boundary exception safety net, pre-gate checkpoints

A real serial pilot (`agent-helper-serial-pilot-20260801`,
`qwen3-coder:30b`) crashed the whole parent process during the mini gate,
with **no persisted sample and no checkpoint** recorded for the failed
attempt. This section documents the root cause and the fix — no model was
called while building/testing it; every scenario below is reproduced with
mocked exceptions/transports.

### 18.1 Root cause

`run_ollama_mini_gate()`'s subprocess working directory was
`mini_task_work/<full sample_id>` — `sample_id` slugs together the
campaign id, model tag, task id, track, and a timestamp, and reached 226
characters in the real run. `subprocess.run(..., cwd=work_dir)` raised
`NotADirectoryError: [WinError 267]` ("The directory name is invalid") —
a known Windows quirk where `CreateProcess`'s cwd validation can fail on
long paths even nominally under the classic 260-character `MAX_PATH`,
especially once a filename is appended inside it (243 characters here).
This call happened **after** the model had already been unloaded (the
`with local_lock.local_model_slot(...)` block had already exited), so the
exception propagated completely uncaught: out of `run_ollama_mini_gate`,
past `serial_campaign._run_one_gate_with_retry` (which only caught
`LiveGateRefusedError`), out of the per-model loop, crashing the whole
CLI process. Because no `SampleRecord` had been built yet at the point of
the crash, nothing was persisted — the real campaign directory shows an
orphaned `mini_task_work/<226-char-dir>/{solution.py,test_solution.py}`
with no corresponding SQLite row.

### 18.2 Fix 1 — short, hashed mini-task workspace directory

`live_gates._short_work_dir_name(sample_id)` returns
`hashlib.sha256(sample_id.encode()).hexdigest()[:16]` — a fixed
16-character, deterministic, collision-safe name, independent of how long
campaign/model/task identifiers are. `run_ollama_mini_gate()`'s work_dir is
now `mini_task_work/<16-hex-char hash>` instead of
`mini_task_work/<full sample_id>`. The full `sample_id` (and the relative
`work_dir` path) is still recorded in the mini-gate's artifact JSON
(`sample_id`/`work_dir` keys), so which attempt produced which workspace
is never lost — only the on-disk *path length* changed.

### 18.3 Fix 2 — gate-boundary exception safety net

Both `run_ollama_connect_gate()` and `run_ollama_mini_gate()` now wrap
their entire post-generate scoring/artifact/persistence block in a
`try`/`except Exception`. Any unexpected exception there (a workspace/
subprocess error, a scoring bug, an artifact-write failure, ...) is caught
by `_build_gate_exception_sample()`, which builds a complete,
schema-valid `SampleRecord` instead of letting the exception propagate:

- `status="error"`, `system_error_flag=True`,
  `system_error_code="GATE_BOUNDARY_UNEXPECTED_EXCEPTION"`
  (`live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE`).
- `acceptance_status` is computed via the same
  `rubric.compute_sample_acceptance(status="error", ...)` every other
  sample goes through — this unconditionally yields `"not_usable"`, so a
  mid-scoring crash can never be mistaken for an accepted result.
- Every phase-timing/score field (`deterministic_score`, `reviewer_score`,
  `prompt_eval_seconds`, `generation_seconds`, `test_exec_seconds`, ...)
  is left `None` — never fabricated. `elapsed_seconds` is an honest
  lower-bound wall-clock measurement of the time actually spent before the
  exception.
- The fallback artifact write is itself wrapped in a nested
  `try`/`except`: if even that fails, `artifact_path`/`artifact_hash`
  degrade to `None` rather than raising a second, unhandled exception that
  would defeat the whole safety net.
- The gate function always returns a normal `LiveGateResult` and always
  persists this sample through the same SQLite/CSV/report pipeline as any
  other attempt — a single unexpected task exception can never again erase
  evidence of an attempt or crash a calling serial-campaign runner.

`run_ollama_generate`'s own `finally`-block unload guarantee is unaffected
(the new try/except only wraps code that runs *after* that block has
already completed).

### 18.4 Fix 3 — pre-gate checkpoints + explicit continue/halt policy

`serial_campaign.run_serial_campaign()` now writes an atomic JSON
checkpoint **immediately before** calling the connect gate and again
**immediately before** calling the mini gate (`in_progress={"model": ...,
"gate": "connect"|"mini"}`), in addition to the existing after-every-step
checkpoint. A hard crash or `Ctrl-C` mid-attempt now always leaves a
checkpoint on disk showing exactly which (model, gate) step was in
flight — this closes the exact gap the real pilot hit (no checkpoint
existed at all for the crashed mini-gate attempt).

A new `halt_on_gate_exception` parameter (default `False`, CLI:
`--halt-on-gate-exception`) controls what happens when a gate function's
own gate-boundary safety net fires (`system_error_code ==
GATE_BOUNDARY_EXCEPTION_ERROR_CODE`):

- **Default (`False`, continue):** the campaign moves on to the next
  model — a single model's workspace/scoring hiccup does not imply every
  other model will fail the same way, unlike a preflight/lock refusal
  which likely recurs. The failed sample and a checkpoint are persisted
  identically either way; this flag only decides whether to keep going.
- **`True` (halt):** the campaign stops after that model, exactly like a
  preflight refusal, for an operator who wants to investigate before
  burning more model time. `SerialCampaignResult.halted_on_gate_exception`
  and the checkpoint's `halted_on_gate_exception` key both reflect this.

`_write_checkpoint()` also gained a short bounded retry-with-backoff
around the atomic `os.replace()` rename: on Windows this can transiently
raise `PermissionError: [WinError 5]` ("Access is denied") if
antivirus/indexing briefly holds a handle open on the just-written temp
file, which became more likely once checkpoints are written more often
(before *and* after each gate). The rename itself is still a single
atomic syscall; only the decision to retry a failed attempt is new.

### 18.5 Resume semantics for the exact real-pilot state (verified)

The real campaign's SQLite DB holds exactly one row: an **accepted**
connect-gate sample for `qwen3-coder:30b` — no mini-gate sample at all
(the crash happened before one could be built). Re-running
`serial-execute` with the same `--campaign-id`/`--models` list and resume
enabled (the default) is verified (`test_resume_after_real_pilot_exact_
state_reruns_only_missing_mini_gate`) to:

1. Recognize the existing accepted connect-gate sample and record a
   `"resumed"` step for it — the connect gate is **not** re-attempted.
2. Attempt only the missing mini gate for `qwen3-coder:30b` — now using
   the short hashed work_dir, and safe against a repeat crash regardless
   of cause.
3. Continue on to the remaining plan (`deepseek-coder-v2:16b`,
   `phi4-mini:3.8b-q4_K_M`, `rnj-1:8b`, per `serial_plan.json`), none of
   which had been attempted yet.

The real campaign's evidence directory
(`benchmark_results\agent-helper\agent-helper-serial-pilot-20260801\`,
gitignored) — including the accepted connect-gate sample and the orphaned
`mini_task_work` directory from the crash — was **not modified, repaired,
or deleted** while building/testing this fix; it remains exactly as the
pilot left it, ready to be resumed.

### 18.6 New regression tests

10 new tests, all fast/deterministic/no-network (273 total, up from 263):

- `test_short_work_dir_name_is_short_and_deterministic_for_long_sample_id`
  — a 100+ character synthetic `sample_id` (mirroring the real 226-char
  case) collapses to a fixed 16-hex-character, deterministic name.
- `test_mini_gate_uses_short_hashed_work_dir_regardless_of_long_identifiers`
  — end-to-end through `run_ollama_mini_gate()` with deliberately long
  campaign/model identifiers, capturing the actual `work_dir` passed to
  `mini_task.run_mini_task_tests()` and asserting it stays short.
- `test_mini_gate_workspace_exception_is_persisted_not_usable_not_raised`
  — mocks `mini_task.run_mini_task_tests` to raise the *exact*
  `NotADirectoryError` Windows raised in the real incident; asserts
  `run_ollama_mini_gate()` returns normally with a persisted
  `status="error"`/`acceptance_status="not_usable"` sample instead of
  raising.
- `test_connect_gate_boundary_exception_is_persisted_not_usable_not_raised`
  — same safety net, connect-gate path (mocks
  `ollama_client.tokens_per_second` to raise).
- `test_pre_gate_checkpoint_is_written_before_connect_gate_call_starts` /
  `..._before_mini_gate_call_starts` — a fake gate function reads the
  checkpoint file from disk *when it is called* and asserts `in_progress`
  is already populated for that (model, gate).
- `test_gate_boundary_exception_is_persisted_and_campaign_continues_by_default`
  / `test_halt_on_gate_exception_true_stops_campaign_early` — the
  continue-by-default vs. explicit-halt policy.
- `test_resume_after_real_pilot_exact_state_reruns_only_missing_mini_gate`
  — reproduces the exact real DB state (one accepted connect-gate sample,
  no mini-gate sample) and asserts resume reruns only the missing mini
  gate.
- `test_checkpoint_retries_transient_windows_replace_failure` — injects a
  first-attempt `PermissionError`, verifies the bounded backoff, and confirms
  that the second atomic replace persists the complete checkpoint.

All 273 tests pass (`python -m unittest test_agent_helper_eval -v` from
`scripts/`), verified across 10 consecutive full-suite runs to rule out
flakiness (a transient Windows checkpoint-rename `PermissionError` was
found and fixed as part of this verification — see §18.4). No live model,
live Ollama discovery call, or network call was made at any point while
building/testing this phase.

### 18.7 Exact remediation/rerun command for the parent agent

```powershell
Set-Location (Join-Path $env:ENGINEERING_REPOS_ROOT "llm-evaluation-workbench\scripts")

# Resume the exact same real serial pilot: the connect-gate sample already
# accepted for qwen3-coder:30b is recognized and skipped; only its missing
# mini gate is attempted, followed by the three models never yet attempted.
python .\run_agent_helper_campaign.py serial-execute `
  --campaign-id agent-helper-serial-pilot-20260801 --confirm `
  --models "qwen3-coder:30b,deepseek-coder-v2:16b,phi4-mini:3.8b-q4_K_M,rnj-1:8b"
```

No flags need to change from whatever the original pilot used — resume is
on by default, and the fix is entirely inside the gate functions and the
checkpoint writer. Add `--halt-on-gate-exception` only if the parent agent
wants the campaign to stop for manual inspection the next time a gate
hits its exception safety net, instead of the default (persist the failed
sample and continue to the next model).
