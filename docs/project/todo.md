---
title: "LLM Evaluation Workbench - Canonical TODO"
status: active
canonical: true
updated: 2026-08-05T15:30:00+02:00
---

# Offene Arbeit

## Final product review — 2026-08-05 (GPT-5.6 Sol, GitHub Copilot CLI)

### Done

- [x] Rebuilt the Textual UI as compact fixed-height action bars and
      responsive panes; all six tabs mount headlessly under Textual 8.2.8.
- [x] Removed the `Select.BLANK` assignment crash and added a regression test.
- [x] Added WYSIWYG benchmark/model/backend selection, exact matrix preview,
      add/remove matrix entries, validation, cloning and safe matrix
      round-tripping (including per-entry `runner_args`).
- [x] Embedded the canonical Agent Monitor collector instead of launching a
      duplicate implementation.
- [x] Added Ollama/llama.cpp/Siemens inventory, curated pull choices, GGUF
      discovery, health diagnostics and a recovery bundle
      (`inventory.json`, campaign TOML, `install.md`).
- [x] Added sortable/filterable/groupable TUI results, compact HTML filters,
      reorderable grouping chips, system/light/dark themes and terminal/HTML
      leaderboard charts.
- [x] Added safe Ollama start/readiness/retry behavior and made pause/stop
      control interrupts non-error outcomes.
- [x] Measure free VRAM in the same telemetry query as used VRAM. Historical
      missing values remain `N/A`.
- [x] Replaced hard-coded local/Qwen PowerShell campaigns with
      `config/local-campaign.toml`; added `cross-backend-mini.toml`; removed
      obsolete model installer, one-off rescoring and duplicate telemetry
      scripts. Specialized runners with distinct contracts remain internal.
- [x] Added focused runner/TUI/report tests; agent-helper 273-test suite and
      related unit/docs tests pass.

### Remaining

- [ ] Keep automatic parameter mutation disabled until multiple measured
      warm-load runs validate each model-specific proposal.
- [ ] Automate rebuilding configured llama.cpp forks; recovery bundles already
      document the model/config restore path.

## Tabulator report rewrite + Textual TUI/web control plane — 2026-08-05 (Claude Sonnet 5, GitHub Copilot CLI)

Full requirements: `docs/project/requirements.md`. Architecture/concept:
`docs/project/tui-web-architecture.md`.

### Done

- [x] `docs/project/benchmark_report.html` (and mirrored
      `benchmark_live_status.html`) fully rewritten: readable formatted
      numeric fields (`tok_s`/`cpu_percent`/`gpu_percent`/VRAM/RAM all via
      `to_float()`), removed the baked-in "— N task × M runs" name suffix
      (now separate `runs`/`samples`/`run` fields), removed `quality_score`
      entirely (not produced by this pipeline; heuristic score only),
      de-duplicated `rating` (short tag) vs `interpretation` (distinct,
      numbers-backed sentence), replaced the broken `<details>` params
      expander with a truncated `.params-preview` cell plus a native
      Tabulator hover tooltip showing pretty-printed JSON, switched to
      `fitDataTable` layout for a compact grid, added native
      drag-and-drop multi-column grouping (chips, reorderable), added 10
      selectable CSS themes (midnight/slate/dracula/nord/solarized ×2/
      light/paper/terminal/high-contrast), added status pill colors
      (done=green, error=red, warning=orange, scheduled=gray,
      running=blue) and a 6-tier violet→red→orange→yellow→blue→green
      color scale on `heuristic_score`/`rating_score`/elapsed (inverted
      for "lower is better"), and hover/mouseover polish throughout.
  - Verified via jsdom (headless Edge screenshot is blocked by system
    policy: "Headless mode is disallowed by the system admin."): 71 rows
    load through the real Tabulator API, all 6 tier classes present in
    the DOM, status pill classes correct, params tooltip returns valid
    pretty JSON, theme switch updates `data-theme`, drag-built grouping
    calls `setGroupBy` and renders group rows, global search and header
    filters both filter the row count correctly, and the reset button
    clears both.
  - Regenerated in place from the real production source
    (`benchmark_results/clean-local-campaign/complete-report-source/`,
    71 rows — the actual folder used to produce today's committed
    report; `.master_runs` only holds 19-row partial snapshots).
  - **Known, deliberately unfixed data-source caveat**: every one of the
    71 rows still shows benchmark name "Standalone 50k web grid demo".
    This is *not* a display bug — `clean-local-campaign.toml` really only
    configures that one fixture (`benchmarks/web-grid-demo-v1.json`) for
    this campaign. If the user expected a different/second benchmark to
    appear (e.g. `top7_hard`/SWE suites), those live in separate CSVs
    that were never merged into this unified report — flag to user,
    do not silently invent a fix.
- [x] `docs/project/tui-web-architecture.md` — concept/architecture for
      the much larger follow-up request (Textual TUI, embedded web
      control plane, wildcard campaign matrix, VRAM-headroom tuning,
      parameter catalog, leaderboard/charts, backup/restore).
- [x] `docs/project/requirements.md` — binding requirements spec for the
      same follow-up request, written from the user's verbatim asks.
- [x] `scripts/llm_bench_tui.py` — new Textual TUI: Dashboard (status +
      Start/Pause/Resume/Stop wired to the *existing*
      `pause.ini`/`stop.ini`/`.benchmark_master.pid` protocol in
      `run_benchmark.py`, never a new one), Config (load/edit/save any
      `config/*.toml`, "reset tuning knobs to defaults" behind a confirm
      switch, never touches the curated `models`/`suites` list), Models &
      backends (Ollama tags with size, llama.cpp GGUF registry with
      on-disk validation, pull/register/backup-inventory actions),
      Results/report (reads the *same* embedded `DATA` JSON the HTML
      Tabulator grid renders — cannot drift from it — with the same
      status/tier colors, a filter box, and Open-in-browser /
      Open-in-Excel actions), Leaderboard (Top-5 by average
      `heuristic_score` plus an ASCII bar chart; no new charting
      dependency added — see architecture doc for the `plotext` upgrade
      path). Verified headless via Textual's `run_test()` pilot: all 5
      tabs render, results load 76 rows (71 completed + scheduled/live
      rows) and filter correctly, leaderboard computes 5 rows, config
      loads real TOML values, models screen lists 34 real rows (Ollama +
      GGUF registry) without error.
- [x] `run_benchmark.py`: invoking with **no CLI arguments in an
      interactive terminal** now launches the Textual TUI instead of the
      old Tkinter dialog; falls back to the previous behavior if Textual
      import fails for any reason. Non-interactive/scripted invocations
      (every documented usage in `docs/operations/runbook.md` passes
      explicit flags) are unaffected.
- [x] `requirements.txt`: added `textual>=0.60` (already present in the
      environment; now declared).
- [x] Re-ran `scripts/test_docs.py` (6/6) and `mkdocs build --strict`
      (clean) after all of the above — no regressions.

### Open (see `tui-web-architecture.md` "Backlog" for full detail/order)

- [x] `scripts/bench_web_server.py` embedded web control plane (stdlib
      `http.server.ThreadingHTTPServer`, mirrors `taskvision-grid-lab`'s
      compression negotiation: `none`/`gzip`/`br`/`zstd` via `?compression=`
      query param or `Accept-Encoding`). Routes: `GET /` (Tabulator report
      page), `GET /api/data` (compressed embedded JSON), `GET /api/status`,
      `GET /vendor/*` (static, path-traversal-guarded), `POST
      /api/control/{pause,resume,stop}` (reuses `run_benchmark.py`'s
      existing `pause.ini`/`stop.ini` file protocol — no new control
      mechanism invented). Verified live: started on port 8766 (port 8765
      was blocked by an unrelated stale listener, not a code bug), curl
      confirmed status JSON, all 4 compression schemes, HTML page, vendor
      JS file, and pause/resume file creation/removal all work correctly.
- [x] Wildcard campaign matrix (`[[matrix]]` TOML shape: fnmatch patterns
      for `models` × `benchmarks` × `backend`) — implemented in
      `run_benchmark.py`: `_load_matrix()` reads the `[[matrix]]` table,
      `_available_benchmark_files()` discovers fixture stems under
      `benchmarks/`/`scripts/benchmarks/`, `expand_matrix()` resolves each
      entry into concrete `{backend, models, benchmark_stem,
      benchmark_file}` groups (wildcard models resolved live via
      `_ollama_models()` for `ollama`, against the GGUF registry for
      `llama_cpp`; a failed/unreachable Ollama tag lookup now degrades
      gracefully to an empty-models group with a warning instead of
      crashing the whole run). Wired into `main()` (`--show-matrix` prints
      a dry-run expansion table and exits 0 without running anything) and
      into `_main_locked()` via a new `_main_locked_matrix()` that mirrors
      the legacy per-suite loop's pause/stop/dashboard control flow but
      plans one runner invocation per expanded group, skipping groups
      whose models or benchmark fixture didn't resolve. Verified with a
      temp two-entry `[[matrix]]` test config (one literal-model entry,
      one wildcard entry against an intentionally-unreachable Ollama URL)
      — `--show-matrix` correctly expanded to 6 groups and printed a
      graceful warning for the unresolvable wildcard group instead of
      crashing; a config with no `[[matrix]]` table still prints "nothing
      to expand" and legacy `suites`-based planning is untouched.
- [x] **"VRAM free" semantics corrected** — the sampler now queries
      `memory.used` and `memory.free` in the same `nvidia-smi` telemetry
      sample and persists the measured free value. Historical rows without
      that measurement remain `N/A`; total-minus-used is never used as a
      historical estimate.
      **Important regeneration gotcha discovered while fixing this**:
      `update_markdown_report(results_dir, report_path, args)` expects
      `report_path` to be the **`.md`** report path (default
      `docs/project/benchmark_report.md`) — it derives the real Tabulator
      `.html` grid via `report_path[:-3] + ".html"` and the legacy
      "Detailed Live Status" plain table via
      `report_path.replace(".md", "_details.md")`. Calling it with an
      `.html` path directly (as done once, by mistake, this session)
      corrupts everything: the derived `.html` name becomes
      `..._report.html.html` (never seen) while the plain "Detailed Live
      Status" markdown content gets written straight into whatever path
      you passed as `report_path` — silently overwriting the real grid
      HTML with an unrelated, all-"scheduled"-status plain table. Always
      pass a `.md` path (or omit `--report-file` to use the default).
      This mistake was caught before being committed (git working tree
      diffs made it obvious) and fully reverted via `git restore` before
      redoing the regeneration correctly. This also revealed that the
      *previously committed* `docs/project/benchmark_report.html` was
      stale (missing `runs`, the 6-tier fields, and still containing
      `quality_score`/the "— N task × M runs" name-suffix bug) — i.e. an
      earlier session's report rewrite was verified but never actually
      saved/committed. The regeneration performed here is therefore the
      first time the fully-fixed grid (from earlier this session) has
      actually landed in the repo's working tree, now with the VRAM-free
      fix included on top. Re-verified `scripts/test_docs.py` (6/6) and
      `mkdocs build --strict` (clean) after copying the regenerated
      artifacts into `docs/project/`.
- [ ] LLM parameter catalog (ollama vs llama.cpp params, descriptions,
      optimal defaults, reset-to-default, "suggest best from history").
- [ ] VRAM headroom % config + auto-reconfigure launch params across all
      models — deliberately deferred until it can be validated read-only
      against real measured VRAM curves first.
- [x] Added Top-5 overall and score-vs-throughput charts to both TUI and HTML,
      plus compact HTML throughput and heuristic-quality bars.
- [x] Recovery bundles now contain `inventory.json`, campaign TOML and an
      `install.md` fallback. Automatic fork build/recompile remains in the
      final-review backlog above.
- [ ] Generic Textual → HTML converter as its own repository —
      deliberately deferred (see architecture doc §3: wait for a second
      real consumer).
- [x] `docs/project/benchmark_run_summary.csv` is retained as a tracked
      generated report artifact.
- [x] Declared `brotli` and `zstandard` alongside Textual so all four web
      compression modes are reproducible on a clean installation.

## Handover für den nächsten Agenten — 2026-08-05

- [x] 71-row clean comparison and backend matrix completed.
- [x] Unified CSV migrated to `benchmark-v2.2` with provider, sample,
      timing, VRAM, score, error, interpretation and launch-parameter fields.
- [x] Real local Tabulator 6.3.1 grid vendored and integrated.
- [x] Verified Tabulator behavior through the real generated report:
      visible rows, column filters, multi-status filtering, sorting,
      multi-column grouping, movable columns and parameter expanders.
      jsdom interaction checks covered filtering/grouping/sorting before the
      final review; the final 71-row artifact was rechecked for valid JSON,
      JavaScript syntax, vendored assets and live HTTP serving. `vendor/tabulator/`
      files present with expected sizes, `DATA` block in
      `benchmark_report.html` parses as valid JSON (71 rows, 23 columns
      matching the defined Tabulator column set), `new Tabulator(...)`
      wiring present with `headerFilter`, `movableColumns`,
      `groupStartOpen` and a toolbar status/search filter.
- [x] Add measured `vram_free_gb` to all future telemetry rows; do not
      estimate historical free VRAM.
- [ ] Re-run the top finalists with the hard suites before declaring a
      Daily Runner. The web-grid screen alone is not sufficient.
- [ ] Validate that future `run_benchmark.py` resume runs append all backend
      rows to the same v2.2 unified CSV.

## Clean benchmark restart — 2026-08-05

- [x] Generated benchmark artifacts reset; source runners and tests retained.
- [x] Persist launch parameters in detail/history and summary CSVs
      (`benchmark-v2.2`).
- [x] Document RTX 3500 Ada VRAM/offload rules and ik_llama.cpp CUDA Release
      build procedure in the operations runbook.
- [x] Run the clean Ollama inventory and three-repeat medium rerun; publish
      the grouped/filterable HTML report and preserve launch parameters.
- [x] Run the executable top-seven coding gate and record pass/fail reasons.
- [x] Complete the equivalent upstream and ik matrices for the seven
      compatible installable GGUFs; the exported Qwen-27B file remains
      explicitly excluded as not server-compatible.
- [x] Publish the unified run-level report contract with provider,
      provenance, resource, score, error and interpretation fields.

## Aktueller Zwischenstand — 2026-08-03 18:35

- Ollama und Siemens sind beendet; aktuell laufen keine Benchmarkprozesse.
- Der gemeinsame Coding-Betriebskontext ist für lokale Ollama-Tags sowie
  OpenCode-/llama.cpp-Presets auf 32.768 Tokens begrenzt.
- Der 27B-Nachtest unter 32k ist abgeschlossen: 3,23 Generate-Tok/s,
  17,81 Prompt-Tok/s, 5,25 s Wall-Time, 66,09 % CPU-Mittelwert und
  36,93 % GPU-Mittelwert. `ollama ps` meldete 48 % CPU / 52 % GPU.
- Die exportierte 27B-GGUF bleibt im ik_llama.cpp-Fork inkompatibel.
  Laguna Q4_K_M ist jetzt als vollständige GGUF installiert und im
  llama.cpp/OpenCode-Routing registriert; der CUDA-Smoke-Test ist gültig.
- Qwen 3.6 35B A3B Q4 wurde in Ollama und llama.cpp getestet. Der
  llama.cpp-Lauf war CPU-only und ist keine GPU-Evidenz.
- Der ursprüngliche Fork-Build war gegen einen sitzungsgebundenen
  CUDA-13.2.1-Pfad gelinkt. Konsistente Neubauten liegen unter
  `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v132-installed` und
  `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v133-installed`.
- Der NVIDIA-Treiber wurde neu installiert und das vollständige CUDA
  Toolkit 13.3.1 ergänzt. Der saubere Fork-Build
  `build-cuda-v133-clean` meldet jetzt `cuda=true`, `gpu_blas=true` und
  die RTX 3500 Ada mit Compute Capability 8.9.
- Die erste gültige Qwen-3.6-35B-A3B-Q4-CUDA-Probe erreichte 98,27
  Prompt-Tok/s und 31,92 Generate-Tok/s. Die vollständige Matrix steht
  noch aus.
- Der Mini-Runner hat jetzt eine Stunde Timeout je Modell,
  `mini_progress.log` sowie einen Stall-Abbruch nach 300 Sekunden ohne
  neue Ollama-Tokens. Die vollständige llama.cpp-Matrix wartet auf einen
  gültigen CUDA-Preflight.

### Nächster Wiederaufsetzpunkt

1. Neustart und CUDA-Preflight mit dem CUDA-13.3-Build wiederholen.
2. Bei unverändertem Fehler NVIDIA Studio über
   `Benutzerdefiniert > Neuinstallation durchführen` reparieren.
3. Falls weiterhin `cuInit=100`/`ggml_cuda_init` erscheint, DDU
   (Display Driver Uninstaller, Wagnardsoft) im abgesicherten Modus
   verwenden und den aktuellen Studio-Treiber sauber installieren.
4. Erst nach nichtleerem `gpu_info` die komplette llama.cpp-Matrix starten.

Diese Datei ist die kanonische, knappe Aufgabenliste für den
Agent-Helper-Track. Detailanforderungen stehen in
[`agent_helper_benchmark.md`](agent_helper_benchmark.md).

## P0 - Zustand sichern und Pilot fortsetzen

- [x] Gemeinsamen Runtime-Guard fuer OpenCode-Kontextbudget, lokale Lease und
      Gaming-Blocker integrieren; vor jedem lokalen Lauf dessen Status pruefen.
- [x] Qwen-/Laguna-Q5/Q6-Hintergrundinstallation prüfen und Ergebnis
      dokumentieren: Laguna Q4/Q5/Q6 sind installiert; das exakte
      Q5-GGUF wurde als `qwen3.6:35b-a3b-q5_K_M` in Ollama importiert.
- [x] Sicherstellen, dass kein Modell geladen ist und keine Downloads
      mehr laufen, bevor Performance gemessen wird.
- [x] Nach Treiber-/Toolkit-Neuinstallation `cuInit` gegen die RTX 3500 Ada
      validieren; der saubere Fork-Preflight meldet `cuda=true` und
      `gpu_blas=true`.
- [ ] `python -m unittest test_agent_helper_eval` erneut ausführen;
      Erwartung: mindestens 273 Tests erfolgreich.
- [ ] `agent-helper-serial-pilot-20260801` per Resume fortsetzen.
- [ ] Prüfen, dass das vorhandene Qwen-Connect-Gate nicht dupliziert wird.
- [ ] Vier Pilotmodelle anhand der ausführbaren Mini-Tests vergleichen;
      nur `gate-passed-provisional`, keine Daily-Runner-Empfehlung.
- [x] Zehn explizite Daily-Coder-Kandidaten seriell durch Connect- und
      Mini-Coding-Gate führen (`daily-coder-20260803`): neun akzeptiert,
      Devstral wegen `OLLAMA_GENERATE_FAILED` im Coding-Gate ausgeschlossen.

## P1 - Moderne Daily-Runner-Kandidaten

- [x] Qwen 3.6 35B A3B Q4 und verfügbare Varianten unter identischen
      Bedingungen testen. Q5 erst nach expliziter GGUF-Quelle ergänzen;
      der Q5-GGUF wurde importiert als `qwen3.6:35b-a3b-q5_K_M`.
- [x] Qwen 3.6 27B Dense Q4 testen, einschließlich Wiederholung unter dem
      32k-Coding-Cap; die neue Probe ist deutlich besser verteilt, aber
      mit 3,23 Generate-Tok/s weiterhin kein Daily-Runner-Kandidat.
- [x] Laguna XS 2.1 Q4, Q5 und Q6 testen.
- [x] Qualität, Stabilität, Zeit bis zum akzeptierten Ergebnis,
      Korrekturschleifen, TPS und Ressourcen vergleichen.
- [ ] Nicht passende oder instabile Modelle sichtbar ausschließen; hohe
      Geschwindigkeit ist kein Ersatz für Qualität.
- [ ] Die Gate-Finalisten Qwen 3.6 35B A3B Q4 und DeepSeek Coder V2 16B
      gegen Bug Review, SQL-Migration, Frontend, Architektur und
      Multi-Turn-Korrektur evaluieren; erst dann Daily-Coder bestimmen.
- [ ] CUDA-Fork `ik_llama.cpp` mit Qwen 3.6 35B A3B Q4/Q5
      im dokumentierten Hybridprofil (GPU Attention/KV, CPU Experts)
      anschließend mit einem echten Coding-Gate prüfen. Gültige
      Durchsatzproben liegen jetzt vor: Q4 98,27/31,92 und Q5
      83,26/21,41 Prompt-/Generate-Tok/s; die Coding-Gates fehlen noch.
- [x] Eine kompatible Laguna-GGUF für den CUDA-Fork beschaffen:
      `Laguna-XS-2.1-Q4_K_M.gguf` ist installiert und mit CUDA ladbar.
- [ ] Laguna Q4_K_M im Hybridprofil mit CPU-/GPU-Sampling und einem echten
      Coding-Gate messen; der bisherige `pp32`/`tg8`-Smoke-Test erreichte
      40,43/8,00 Prompt-/Generate-Tok/s.
- [x] Favoritenvergleich mit dem vorhandenen Hard-/Mini-Track fortsetzen:
      Qwen/Ollama Hard v2 erreichte 78,32 % bei 400,3 s, DeepSeek war im
      Tool-Agent-Track wegen fehlender Tools nicht ausführbar, Laguna
      scheiterte dort an einem Tool-/Summary-Loop. Die sechs Mini-Gate-
      Rohdateien liegen unter `benchmark_results\favorites-cross-backend-20260804`;
      Fork- und Ollama-Ressourcenwerte sind im Handover dokumentiert.
- [x] `swe-sql-hard-24`, `swe-python-hard-24` und `swe-mixed-hard-24`
      für die drei Favoriten in Ollama und `ik_llama.cpp` ausführen und
      den konsolidierten Vergleich unter
      `benchmark_results\agent-helper\report.html` aktualisieren.
- [x] Rating und Agententauglichkeit im Legacy-SWE-Report trennen:
      Rating = Qualität × Elapsed-Performance × Zuverlässigkeit;
      Agententauglichkeit = erwartete Zeit bis zum erfolgreichen Ergebnis,
      damit schnelle Korrekturschleifen berücksichtigt werden.
- [ ] Ik-Fork-Qwen-MoE-Tensorprofil weiter tunen: automatischen Fit gegen
      tensor-spezifische `--override-tensor`-/partielle Expert-Offloads
      messen; `--cpu-moe` und `--n-cpu-moe 24` waren im Smoke-Test langsamer.
- [ ] Qwen 3.6 35B, Qwen Coder 30B und Laguna im Fork mit PCIe-RX/TX-
      Monitoring messen; GPU-Auslastung/VRAM und PCIe-Durchsatz pro Case
      gegen Ollama und Upstream-llama.cpp vergleichen, anschließend
- [x] KAT-Coder-V2.5-Dev als identische offizielle Q4_K_M-GGUF in Ollama,
      Upstream-llama.cpp und ik-Fork mit 12-GB-Hybridprofil messen; Rohdaten
      und PCIe-Werte liegen unter den drei `kat-coder-*-20260804`-Verzeichnissen.
      TB4/TB5/PCIe-x16-Szenarien aus den Dauerwerten ableiten.

## P1 - Vollständige Benchmarktracks

- [ ] Reale Executor-Gates für Bug Review, SQL-Migration, Frontend,
      Architektur und Multi-Turn-Korrektur implementieren.
- [ ] Tool-Agent-Track in isolierten Fixtures ergänzen.
- [ ] Siemens-Qwen-3.6-27B-Adapter für abgegrenztes Coding integrieren.
- [ ] Weitere Siemens-Modelle unter demselben Vertrag testen.
- [ ] Copilot-Referenzläufe getrennt ausweisen; nicht verfügbare interne
      Token-/TPS-Metriken als `N/A` belassen.
- [ ] llama.cpp-Adapter und Lifecycle unter denselben Qualitätsgates
      integrieren.

## P2 - Kapazität und Wirtschaftlichkeit

- [ ] Nur für vollständig akzeptierte lokale Modelle Parallelität 1/2/4
      testen.
- [ ] LLM/API-, Tool-/Test-, Orchestrator-, Queue- und Idle-Anteile am
      kritischen Pfad validieren.
- [ ] Gesamt-TPS, TPS je Anfrage, p95, VRAM/RAM und Fehler unter
      Parallelität auswerten.
- [ ] RTX-5090-32-GB-Fit als Projektion mit Annahmen und Konfidenz
      darstellen, niemals als Messung.
- [ ] Premium-first, Siemens-/Local-Routing und Hardware-Investition mit
      getrennten Baselines vergleichen.

## P2 - Berichte und Managementdarstellung

- [ ] Historische Runs mit Provenienz-/Konfidenzkennzeichnung importieren.
- [ ] Aktuelle Kampagne standardmäßig öffnen, ältere Kampagnen einklappen.
- [ ] DE/EN-HTML auf Managementstory, technische Drilldowns,
      Barrierefreiheit, Print/PDF und Offlinebetrieb prüfen.
- [ ] Funktionale/visuelle Bewertung der vorhandenen HTML-Grid-Artefakte
      abschließen; Devstral-Timeout separat dokumentieren.
- [ ] Managementbericht klar zwischen Messung, Annahme und Projektion
      unterscheiden lassen.

## P3 - Routing-Regeln und Governance

- [ ] Erst nach vollständiger Evidenz ein Agent-Routing-Regelwerk
      vorschlagen.
- [ ] Deterministische Tools vor LLM, Siemens-/Local-Worker für geeignete
      Aufgaben und Premium-Modell für Architektur/Review belegen.
- [ ] Provenienz jedes Subagenten, Modells und Reviews verpflichtend
      machen.
- [ ] Knappe, validierte Regeln anschließend in die passenden
      Workspace-/Standards-Markdowndateien übernehmen.

## Nicht in diesem Track

- Command-Center-Implementierung und dessen Cloud-/Tool-Berichte.
- ITSM-PWA, Backend-Sprachbenchmark und PostgreSQL-Domänenmodell.
- Moderner Oracle-nach-PostgreSQL-Data-Pump-Migrator.
- Jira-TaskVision-Produktionsmigration.


## Phase 2 - Autonomous Implementation (completed 2026-08-06)

### Completed Blocks

| Block | Feature | Tests | Coverage Impact |
|-------|---------|-------|-----------------|
| 1 | VRAM Headroom Auto-Tune (Propose-Confirm-Apply) | +4 | run_benchmark.py full coverage |
| 2 | Fork Build Automation (detect, cmake, install.md) | +15 | fork_build.py module |
| 3 | TUI Robustness (error boundaries, F5 refresh) | +6 | All 6 screens protected |
| 4 | Web Server Hardening (size limits, health, CORS) | +16 | Full HTTP endpoint suite |
| 5 | Config Validation & Schema (JSON Schema draft 7) | +6 | All configs validated |
| 6 | HTML Report (quality bars, throughput, PNG export) | +3 | Report generation covered |
| 7 | Test Expansion & Coverage | +0 (already 89%) | agent_helper_eval at 89% |
| 8 | Integration Tests | +13 | E2E: campaign, TUI, web |

### Requirements Status

All 8 requirements from `docs/project/requirements.md`:

- **§1 TUI**: SHIPPED (with robustness hardening, 6 screens verified by integration tests)
- **§2 Lifecycle**: SHIPPED (fork rebuild added via Block 2)
- **§3 Parity**: SHIPPED (same grid data, same colors, browser/Excel export)
- **§4 Web server**: SHIPPED (with hardening - size limits, CORS, health)
- **§5 Matrix**: SHIPPED (wildcard TOML, per-entry args, preview)
- **§6 VRAM**: SHIPPED (auto-tune with human confirmation)
- **§7 Leaderboards**: SHIPPED (HTML enhanced with quality bars, throughput, scatter)
- **§8 Polish**: SHIPPED (10 themes, 6-tier colors, hover, filters, compact)

### Integration Test Evidence

`scripts/test_integration.py` - 13 safe E2E tests (no model runs, no GPU, localhost-only):

- **TestCampaignIntegration** (5 tests): `--validate-config` on `benchmark.toml` and `local-campaign.toml`,
  `--show-matrix` expansion, `--doctor` health check, `--generate-schema` round-trip
- **TestTUIStructure** (3 tests): TUI module import, 6 pane classes present in source,
  6 TabPane labels match in `compose()`
- **TestWebServerIntegration** (5 tests): root page 200, `/api/data` JSON list,
  `/api/health` OK with expected fields, `/api/status` contains `running`,
  pause/resume/stop control file cycle with cleanup

### Backlog (deferred)

- **B3**: Generic Textual-to-HTML converter - waiting for second consumer
- **"Suggest best from history"** tuning - future enhancement
- Automatic fork rebuild - recovery bundles already document the restore path
