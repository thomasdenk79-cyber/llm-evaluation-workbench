---
title: "TUI + Web Control Plane - Requirements"
status: active
canonical: true
updated: 2026-08-05T15:30:00+02:00
---

# TUI + web control plane requirements

This is the binding requirements specification for the interactive control
layer requested on top of the existing benchmark report/orchestrator. It
captures the user's request (2026-08-05, German) as concrete, checkable
requirements. See `docs/project/tui-web-architecture.md` for the
architecture/design that implements these, and `todo.md`/`handover.md` for
current implementation status.

## 1. Textual TUI launched on parameterless invocation

- Running the benchmark suite entrypoint with **no CLI parameters** must
  open a Textual-based terminal GUI (not the previous bare Tkinter dialog),
  usable with keyboard **and mouse**.
- The TUI must offer, at minimum: Start, Stop, Pause, Resume of a benchmark
  campaign; editing the active campaign configuration; a "reset to
  defaults" action; browsing installed LLMs/backends and installing more;
  automatic format conversion where only one backend supports a given model
  file format (documented as a backlog item where automatic conversion is
  not yet implemented -- see architecture doc).
- Non-interactive/scripted invocations (CI, existing `.ps1` wrappers) must
  keep working exactly as before -- the TUI must not become a blocking
  requirement for automation.

## 2. LLM/backend lifecycle management

- View all installed local LLMs (Ollama tags, llama.cpp GGUF registry) and
  which backend(s) they are compatible with.
- Install additional models without requiring a human to ask an AI agent to
  do it manually every time.
- Back up the LLM/backend configuration (what's installed, with what
  launch parameters) so a full system crash can be recovered from: re-
  install the same models/backends including configuration, and, where a
  backend is a source fork, rebuild/recompile it. Where automatic
  rebuild is not possible in a given environment, produce a durable
  `install.md` an autonomous agent can follow instead.

## 3. Feature parity between the HTML report and the TUI

- The Textual "Results" view must expose the same features as the HTML
  Tabulator report: filter, multi-column grouping, and the same color
  scheme (status colors, 6-tier violet→green quality/score/elapsed
  coloring).
- "Open in Excel" and "Open in browser" actions from the TUI.
- A generic "Textual → HTML" bridge is a desirable idea for reuse across
  future terminal tools in this environment, but is explicitly **not**
  started as its own repository yet -- see architecture doc §3 for the
  reasoning (wait for a second real consumer before generalizing).

## 4. Web control plane

- The same report/data should also be reachable from a browser via a small
  embedded web server, so pause/stop/resume and richer inspection are also
  possible from a browser, without needing OS-level agent mediation for
  every action.
- Reference basis: `${ENGINEERING_REPOS_ROOT}/taskvision-grid-lab/server.py` -- a
  stdlib-only `http.server` implementation with configurable response
  compression (`none`/`gzip`/`br`/`zstd`), which this project should reuse
  the *pattern* of (not the TaskVision-specific schema/routes).
- Compression algorithm must be configurable (the same four options).

## 5. Wildcard campaign matrix

- Campaign configuration (TOML) must support wildcard/pattern matching of
  models, benchmarks, and backends so combinations like "model A + B run
  benchmark `ora2pg`; every `qwen*` model runs every `*hard*` benchmark"
  can be expressed declaratively, without hand-enumerating every
  combination.
- Backends currently in scope: `ollama`, `llama_cpp`, and at least one
  llama.cpp source fork (already present: the ik_llama.cpp CUDA fork).

## 6. VRAM headroom and parameter tuning

- A configurable "target free VRAM after model load" percentage (default
  5%) must exist. Too little headroom causes RAM/host swapping (slow); too
  much wastes GPU offload (also slow).
- Changing this value must be able to re-derive per-model launch
  parameters (`ngl`/`n_gpu_layers`, context size, batch size, offload
  profile) across **all** configured models, per backend (ollama and
  llama.cpp differ in which parameters exist and how they are named).
- Every tunable parameter needs: a short human-readable description of
  what it does, a documented optimal default, a reset-to-default action,
  and (data permitting) a "suggest best parameters from benchmark
  history" action.

## 7. Leaderboards and management-facing charts

- A "Top 5 LLMs overall" view/table.
- A management-friendly chart plotting score vs. performance per model
  (the familiar "quality vs. speed/cost" scatter chart pattern), in both
  the HTML report and, to the extent feasible in a terminal, the TUI.

## 8. General polish

- Continue and extend the already-delivered HTML report polish (readable
  formatted values, drag-to-group columns, compact autofit layout, 10
  selectable themes, 6-tier violet→green color coding on
  rating/quality/elapsed, hover/mouseover effects, working per-column and
  global filters) -- this requirements set is additive to, not a
  replacement for, that earlier work.
- "20 weitere sinnvolle Features" (20 further useful features) was an
  open-ended invitation, not a fixed checklist; concrete candidate
  features are tracked as they are identified in `todo.md` rather than
  invented speculatively here.

## Explicit non-goals (for this round)

- A generic remote-shell/OS-command endpoint on the web control plane.
  Browser-triggered actions are limited to the same pause/stop/resume file
  protocol the TUI uses -- an unrestricted remote-exec endpoint on a
  network-reachable listener would be a security regression this
  repository's `AGENTS.md` posture does not allow.
- A full, generic Textual→HTML conversion library/repo (deferred, see §3).
- Automatic VRAM-headroom-driven parameter rewriting without a human
  reviewing the proposed change first (start read-only, see architecture
  doc backlog item 3).
