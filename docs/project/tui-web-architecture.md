---
title: "TUI + Web Control Plane - Concept & Architecture"
status: draft
canonical: true
updated: 2026-08-05T15:00:00+02:00
---

# Textual TUI + Web control plane: concept and architecture

This document is the answer to the user's request for "ein gutes Konzept und
Architektur" behind a much larger interactive-control feature set for this
workbench: a Textual TUI launched when the benchmark suite is run without
parameters, an embedded web control plane that mirrors the same data/actions
in a browser, LLM/backend lifecycle management (install, backup, restore,
fork build), a wildcard campaign matrix, VRAM-headroom-aware parameter
tuning, and management-facing leaderboards/charts.

The full request is large (roughly 20+ distinct features). This document
prioritizes and phases the work; the "Implemented" section below reflects
what actually shipped in this round, the "Backlog" section is the honest
remainder, ordered by value/effort, for the next agent or session to pick up.

## Guiding principles (carried over from `AGENTS.md`)

- Reuse, don't duplicate: the pause/stop/resume file protocol
  (`pause.ini`/`stop.ini`/`.benchmark_master.pid`), the Ollama/llama.cpp
  discovery code in `scripts/agent_helper_eval/ollama_inventory.py` /
  `model_inventory.py`, and the Tabulator grid-row builder
  (`grid_rows()`/`_apply_tiers()` in `scripts/llm_migration_benchmark.py`)
  are the single sources of truth. The TUI and the web control plane are
  new *views* over this existing state, not a parallel implementation.
- No secrets, no silent network calls: model install/pull/uninstall always
  requires explicit user confirmation (already the existing convention in
  `run_benchmark.py`'s `--install-ollama` prompt); the TUI keeps that.
- Everything must degrade gracefully offline: no CDN dependency (mirrors
  the already-vendored Tabulator 6.3.1 approach), stdlib-first web server
  (mirrors `C:\GIT\taskvision-grid-lab\server.py`, which uses
  `http.server.ThreadingHTTPServer` with **no** external web framework and
  a query-param-negotiated `none|gzip|br|zstd` compression scheme -- reused
  here rather than reinvented).

## Architecture overview

```
                       ┌───────────────────────────┐
                       │   config/*.toml campaigns  │
                       │ (wildcard matrix, params,  │
                       │  vram_headroom_pct, ...)   │
                       └─────────────┬─────────────┘
                                     │ read/write
                 ┌───────────────────┴────────────────────┐
                 │                                          │
        ┌────────▼─────────┐                     ┌─────────▼─────────┐
        │ scripts/          │  pause.ini/stop.ini │ scripts/           │
        │ run_benchmark.py  │◄────────────────────┤ llm_bench_tui.py   │
        │ (orchestrator,    │  (existing file       │ (Textual app)      │
        │  unchanged control│   protocol, reused)   │  - Dashboard        │
        │  protocol)        │                       │  - Config editor    │
        └────────┬──────────┘                       │  - Models/backends  │
                 │ writes                            │  - Results/report   │
                 │ unified_benchmark_detail.csv       │  - Leaderboard/chart│
                 │ + benchmark_report.html (Tabulator)└─────────┬──────────┘
                 │                                              │ launches
        ┌────────▼─────────────────────┐             ┌──────────▼─────────┐
        │ docs/project/benchmark_report │             │ scripts/            │
        │ .html (Tabulator grid, already│◄───same JSON┤ bench_web_server.py │
        │ shipped this session)         │  payload    │ (stdlib http.server,│
        └────────────────────────────────┘             │  compression like   │
                                                        │  taskvision-grid-lab)│
                                                        └─────────────────────┘
```

### 1. Textual TUI (`scripts/llm_bench_tui.py`)

Launched automatically when `run_benchmark.py` is invoked with **no CLI
arguments in an interactive TTY** (non-interactive/CI invocations, and any
invocation with an explicit `--config`/flags, are unaffected -- this
preserves existing automation such as `run_local_campaign.ps1`). It can
also be started directly: `python scripts\llm_bench_tui.py`.

Screens (all navigable by keyboard *and* mouse, per the user's request):

- **Dashboard** -- live status table (reads the same
  `unified_benchmark_detail.csv` / `.master_runs` state `run_benchmark.py`
  already maintains), a progress bar, and Start / Pause / Resume / Stop
  buttons that just create/remove `pause.ini` / `stop.ini` -- i.e. the TUI
  is a thin client over the *existing*, already-tested control protocol,
  not a new one.
- **Config** -- loads the active campaign TOML (`config/benchmark.toml` or
  `config/clean-local-campaign.toml`), renders every key as a typed widget
  (`Input`, `Switch`, `Select`), "Save", and "Reset to defaults" (defaults
  sourced from a small `DEFAULT_CAMPAIGN` dict shipped with the TUI so a
  reset never depends on the file it is resetting).
- **Models & backends** -- lists installed Ollama tags (via
  `agent_helper_eval.ollama_inventory`) and configured llama.cpp GGUF
  paths side by side with a fit/VRAM column; actions to pull a new Ollama
  model, register a GGUF path, or run the existing `--install-ollama`
  flow -- all delegate to `run_benchmark.py`'s existing, already-guarded
  functions instead of re-implementing installation.
- **Results / report** -- a `DataTable` populated from the *same*
  `grid_rows()` + `_apply_tiers()` output that feeds the HTML Tabulator
  grid, so the two views can never drift: identical column set, identical
  6-tier violet→green color scale (rendered via Rich `Text` styles),
  identical status colors. Supports a filter input row and column-click
  sort; "Open in browser" (`os.startfile`) and "Open in Excel"
  (writes a `.xlsx`-compatible CSV and opens it) actions.
- **Leaderboard** -- Top-5-overall table plus a score-vs-elapsed scatter
  (via the `plotext`-backed Textual widget if available, otherwise a
  degraded bar-chart fallback so the screen never crashes without the
  optional dependency).

### 2. Web control plane (`scripts/bench_web_server.py`)

A second, independent view of the same state, deliberately built on
`http.server.ThreadingHTTPServer` (stdlib-only, matching
`taskvision-grid-lab/server.py`) rather than a framework, so it stays a
zero-install, offline-friendly tool consistent with this repo's existing
posture:

- `GET /` -- serves the *existing* `docs/project/benchmark_report.html`
  Tabulator grid (no duplicate frontend).
- `GET /api/data` -- the same JSON payload the HTML report embeds inline,
  served with negotiated `none|gzip|br|zstd` compression (query param
  `?compression=` **or** `Accept-Encoding`, exactly like
  `taskvision-grid-lab`'s `send_json()`), so very large campaigns don't
  have to inline multi-MB JSON into the HTML file any more.
- `POST /api/control/{pause,resume,stop}` -- touches/removes
  `pause.ini`/`stop.ini`, i.e. the browser gets the same "OS rights" the
  user asked for, but scoped to the one action the file protocol already
  exposes safely (no generic remote-shell endpoint -- that would be a
  security regression this repo's `AGENTS.md` posture does not allow for
  an internal, potentially-networked listener).
- Binds to `127.0.0.1` by default; a `--host` override is opt-in and logs
  a loud warning, mirroring the "no secrets, no silent exposure" posture.
- Uses port `8766` by default; port `8765` is reserved by the separate
  AutoInstaller/taskvision local service on this workstation.

### 3. Generic "Textual → HTML" converter

The user floated this as possibly worth its own repository. Assessment:
genuinely useful (this workbench alone would benefit twice -- the Results
screen above *is* a hand-written duplicate of the HTML grid's rendering
logic today), but building a *good*, general version (one that handles
arbitrary Textual widgets, not just `DataTable`, with layout fidelity) is
a multi-week effort in itself and shouldn't be started speculatively
inside this workbench's repo. Recommendation, not yet started:

1. Ship the narrow version used here first (a `grid_rows()`-shaped JSON
   contract consumed by *both* the Textual `DataTable` and the Tabulator
   HTML grid -- already effectively true after this round's work).
2. If a second internal project needs the same bridge, extract the shared
   "typed rows + tier colors + status colors → Rich renderable *or*
   Tabulator column defs" mapping into a small, dependency-free module
   first (e.g. `scripts/grid_contract.py`), and only spin it into its own
   repository once a *third* consumer appears. Speculative generalization
   before a second real consumer tends to guess the wrong abstraction.

## Wildcard campaign matrix

`config/*.toml` already supports `fnmatch`-style wildcards for
`--models`/`ollama_models` (see `_ollama_models()` in `run_benchmark.py`).
Extending this to a full matrix -- e.g. "models 1+2 run `ora2pg`, `qwen*`
runs `*hard*`" -- needs one addition: an optional `[[matrix]]` array of
tables, each with `models`, `benchmarks`, `backend` (all fnmatch patterns),
expanded into concrete `(model, benchmark, backend)` triples before
planning. Documented here as the target shape; not yet implemented (see
Backlog).

## VRAM headroom %

Target: one `vram_headroom_pct` (default `5`) config value that, when
changed, re-derives per-model launch parameters (`ngl`, `ctx_size`,
`batch_size`, offload profile) for *every* configured model so free VRAM
after load stays close to the target -- too little headroom causes CPU/RAM
swapping (slow), too much wastes GPU offload (also slow). This needs a
per-backend formula (ollama's `num_gpu`/`main_gpu` heuristics differ from
llama.cpp's explicit `--n-gpu-layers`), which should be derived from the
`unified_benchmark_detail.csv` history already being collected
(`vram_used_gb`/`vram_free_gb` are already measured fields). Not yet
implemented (see Backlog) -- this is a data-driven tuning feature and
deserves its own validation pass against real measured runs before it
starts silently rewriting launch parameters.

## Implemented this round

- [x] This concept/architecture document.
- [x] `scripts/llm_bench_tui.py` -- Textual TUI with Dashboard, Config,
      Models & backends, and Results/report screens (see file for the
      exact widget tree); Leaderboard screen included with a graceful
      fallback when `plotext` is unavailable. The dashboard reads child
      output on a background thread so a quiet benchmark cannot freeze the
      TUI; Config offers benchmark-file and model selection; local GGUFs are
      discovered from the standard llama.cpp model directories; and the
      Agent monitor tab launches the existing `wt-command-center` monitor
      instead of duplicating its collector.
- [x] Results use the benchmark field from the shared report payload, expose
      Tasks instead of a duplicate Run column, and sort when a column header
      is selected.
- [x] `run_benchmark.py` now launches the Textual TUI (instead of the
      Tkinter `_gui()`) when invoked with no arguments in an interactive
      terminal; unchanged behavior otherwise.
- [x] `scripts/bench_web_server.py` -- stdlib web control plane serving the
      existing Tabulator report plus a compressed JSON API and
      pause/resume/stop control endpoints.

## Backlog (not implemented this round, prioritized)

1. **Wildcard campaign matrix** (`[[matrix]]` TOML shape above) -- medium
   effort, high value, safe (pure planning-time expansion).
2. **LLM parameter catalog with descriptions + reset-to-default +
   best-run suggestion** -- needs a small static catalog (ollama vs
   llama.cpp params, defaults, one-line descriptions) plus a "suggest
   from history" query over `unified_benchmark_detail.csv` grouped by
   model, ranked by `heuristic_score`/`wall_seconds`.
3. **VRAM headroom % auto-reconfig** -- data-tuning feature, needs
   validation against real measured VRAM curves before it is allowed to
   rewrite launch parameters automatically; start read-only ("here is
   what I would change") before making it mutate configs.
4. **Top-5 leaderboard + score-vs-performance chart in the HTML report**
   -- straightforward addition to the already-shipped Tabulator page (an
   SVG/Canvas scatter of `heuristic_score` vs `wall_seconds` per model,
   grouped/colored by backend).
5. **Backup/restore of LLM + backend configuration** -- snapshot
   `ollama list`/`ollama show` output and the llama.cpp GGUF registry
   (`config/*.toml` `llama_models`) into a versioned JSON, with a restore
   path that re-pulls/re-registers on a fresh machine.
6. **Fork build automation** -- attempt `cmake --build` for a configured
   llama.cpp fork checkout when its source is present; when it is not (or
   the toolchain is missing), emit a durable `docs/operations/install.md`
   with the exact steps so an agent without build tools can still follow
   them autonomously (this half already exists as a fallback pattern the
   user explicitly asked for).
7. **Generic Textual → HTML converter as its own repo** -- deferred by
   design until a second real consumer exists (see above).

Each backlog item is independently shippable; none of them block the
"Implemented this round" pieces above from being useful today.
