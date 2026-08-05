# LLM Evaluation Workbench - project router

- **AI-ACCESS:** allowed
- **INHERITS:** `C:\GIT\AGENTS.md` and `C:\GIT\standards\AGENTS.md`
- **OVERRIDES:** local benchmark safety, reporting, reproducibility, and lifecycle rules below
- **SCOPE:** this repository

### Stand: 2026-08-05 — Clean benchmark baseline and launch provenance

- The unified report contract is defined in
  `docs\project\benchmark-report-requirements.md`. The HTML report is one
  offline grid with per-column filters, multi-status filtering, multi-column
  grouping, run-level rows, readable role interpretation, and expandable
  launch parameters.
- The detail schema is now `benchmark-v2.2`; required additive fields include
  provider, benchmark display/sample metadata, elapsed/wall time, VRAM free,
  heuristic/error aliases, rating score and interpretation.

- The Ollama campaign endpoint is `http://127.0.0.1:11434/api/generate`;
  using the base URL for generation causes HTTP 405 and invalidates the run.
- The 19-model clean Ollama inventory and three-repeat medium rerun completed
  on 2026-08-05. The authoritative medium artifact is
  `benchmark_results\clean-local-campaign\top20_medium_detail_run_20260805_031105.csv`.
- The top-seven executable coding gate is separate evidence at
  `benchmark_results\clean-local-campaign\top7_hard\top7_mini_gate.csv`.
- The seven-model compatible GGUF matrix completed for upstream and ik:
  `upstream_matrix_detail_run_20260805_035226.csv` and
  `ik_matrix_detail_run_20260805_040436.csv`. The exported Qwen-27B GGUF
  remains excluded because both servers failed readiness.

- Generated benchmark output was reset so the next campaign starts with an
  empty `benchmark_results` directory; source runners and tests were retained.
- The migration runner now persists `launch_profile`, `server_executable`,
  `model_path` and deterministic `launch_params` JSON in detail and summary
  CSVs. Schema version is `benchmark-v2.1`.
- For the RTX 3500 Ada 12-GB profile, `fit` with a 1664 MiB margin, Q8 K/V
  cache, Flash Attention, 16 threads, 512/128 batch sizes and 32768 context
  is the controlled starting point. `exps=CPU` and blind `n-cpu-moe` are
  diagnostic profiles, not defaults.
- The next clean comparison must use one local model at a time and record
  GPU/VRAM/PCIe telemetry; GPU utilization above 90% is a diagnostic signal,
  not a correctness or throughput target.

### Stand: 2026-08-04 — Rating und Agententauglichkeit

- Der Legacy-SWE-Report bewertet Läufe nicht mehr nur nach Qualität:
  `Rating` kombiniert normalisierte Qualität, Elapsed-Performance und
  Zuverlässigkeit; `Agent suitability` bewertet zusätzlich die erwartete
  Zeit bis zum erfolgreichen Ergebnis und bleibt eine getrennte
  Routing-Einschätzung.

> Mandatory before work: read `C:\GIT\user-memory\profile.md`,
> `C:\GIT\agent-memory\INDEX.md`, `C:\GIT\standards\AGENTS.md`, then this file.

## Aktueller Stand

### Stand: 2026-08-03 — Daily-Coder-Mini-Gates abgeschlossen

- `daily-coder-20260803` testete zehn explizite lokale Kandidaten seriell
  mit Connect- und ausführbarem Mini-Coding-Gate. Die Daten liegen unter
  `benchmark_results\agent-helper\daily-coder-20260803\`; lokale und
  zusammengeführte HTML-Berichte wurden nach jedem Gate aktualisiert.
- Neun Kandidaten bestanden beide Gates. Schnellste akzeptierte
  Mini-Coding-Proben: `qwen3.6:35b-a3b-q4_K_M` (`35,176 s`, `28,73 Tok/s`)
  und `deepseek-coder-v2:16b` (`36,530 s`, `20,91 Tok/s`), danach
  `qwen3-coder:30b` (`78,892 s`, `5,75 Tok/s`).
- `devstral-small-2:24b` ist `not-usable`: Connect bestanden, aber
  Mini-Coding-Gate `OLLAMA_GENERATE_FAILED`; das harte Gate schließt es
  aus der Rangfolge aus.
- Alle akzeptierten Modelle bleiben `gate-passed-provisional`/
  `evidence_stage=gate_only`. Keine Daily-Coder-Empfehlung vor breiter
  Coding-, Reviewer- und Multi-Turn-Evidenz.

### Stand: 2026-08-03 — Fork-/Report-Integration und CUDA-Blocker

- Der echte `ikawrakow/ik_llama.cpp`-Fork ist unter
  `C:\Users\z000g9hu\llama.cpp-ik` auf Commit `cb9147f` vorhanden. Ein
  CUDA-13.2-Build mit `llama-bench.exe` und `llama-server.exe` liegt unter
  `build-cuda-v132-local\bin\Release`.
- Einen ersten scheinbar erfolgreichen Fork-Lauf nicht verwenden: Er lief
  wegen `CUDAToolkit_NVCC_EXECUTABLE-NOTFOUND` CPU-only (`cuda=false`,
  `gpu_blas=false`). CPU- oder Fallback-Werte dürfen nie als GPU-Evidenz
  in Leaderboard, Empfehlung oder Bericht erscheinen.
- Aktuell meldet die RTX 3500 Ada per `nvidia-smi` den Studio-Treiber
  `596.86`/CUDA 13.2, doch die direkte CUDA-Driver-API liefert
  `cuInit=100`, `deviceCount=0`. Nach Abschluss von CUDA/Nsight ist ein
  Windows-Neustart Pflicht, dann zuerst CUDA sichtbar machen und erst
  danach den Fork-Hybridlauf starten.
- Reporting-Vertrag: `benchmark_report.html`/`.md` plus
  `benchmark_run_summary.csv` ist die kompakte Historie (eine Zeile je
  abgeschlossenem Lauf). `benchmark_live_status.html`/`.md` ist die breite
  Live-Sicht mit geplanter Reihenfolge, Fortschritt und ETA und wird nach
  jeder gespeicherten Probe aktualisiert.
- Der Fork misst zunächst nur PP/TG-Durchsatz. Ein Daily-Coder darf erst
  nach denselben Coding-/Korrektheitsgates wie die Ollama-Kandidaten
  empfohlen werden.

### Stand: 2026-08-02 — Gemeinsame lokale Modell-Lease (kein Live-Modell)

- `scripts\agent_helper_eval\local_lock.py` adaptiert jetzt die kanonische,
  dependency-freie Lease unter
  `standards\scripts\local_model_lease.py`; Resource-ID `local-llm` gilt
  workspaceübergreifend für Ollama/llama.cpp.
- Agent-Helper-Gates und `llm_migration_benchmark.py` warten begrenzt auf
  Freigabe, erneuern die Lease während des Workloads und geben nur die
  eigene zufällige Lease-ID frei. Siemens-Läufe benötigen keine lokale
  Lease.
- Fokussierte Shared-Lease- und Fake-Transport-Tests bestanden; kein
  Modell-, Ollama-Generate- oder Netzwerkaufruf.
- Nächster Schritt bleibt der bestehende serielle Pilot nach expliziter
  interaktiver Freigabe; alle lokalen Entrypoints verwenden dabei dieselbe
  Lease.

### Stand: 2026-08-01 23:45 — Session-Handover für Modellwechsel

- Kanonischer Wiederaufsetzpunkt:
  `docs\project\handover.md`.
- Kanonische offene Arbeit:
  `docs\project\todo.md`.
- Agent-Helper-Harness implementiert und zuletzt mit **273/273** schnellen
  Tests validiert. Er umfasst SQLite-SSOT, stabile CSV-Exporte,
  Qualitätsgates, Ollama-Lifecycle, seriellen Resume-Runner, Phasen- und
  Ressourcenmessung sowie einen offlinefähigen DE/EN-HTML-Report.
- Der serielle Pilot `agent-helper-serial-pilot-20260801` ist nach einem
  behobenen Windows-Langpfadfehler resumierbar. Ein erfolgreiches
  Connect-Gate für `qwen3-coder:30b` ist bereits gespeichert; das
  fehlende Mini-Gate und drei Baselines stehen noch aus.
- Ein losgelöster Download-/Importprozess installiert Q5/Q6 für Qwen 3.6
  35B A3B und Laguna XS 2.1. Solange er läuft, keine lokalen
  Performance-Benchmarks starten.
- Keine Commits oder Pushes in dieser Session. Der Worktree enthält
  umfangreiche ältere und parallele Änderungen; niemals pauschal
  bereinigen oder zurücksetzen.

### Stand: 2026-08-01 — Agent-Helper-Evaluation-Track: Serial-Pilot-Crash-Fix (kein Live-Modell)

- Ein realer serieller Pilot (`agent-helper-serial-pilot-20260801`,
  `qwen3-coder:30b`) stürzte während des Mini-Gates ab: das
  Arbeitsverzeichnis (`mini_task_work/<voller sample_id>`) war 226 Zeichen
  lang, `subprocess.run(cwd=...)` warf unter Windows
  `NotADirectoryError: [WinError 267]`, die Exception propagierte
  ungefangen bis zum Absturz — **ohne** persistierte Probe oder
  Checkpoint für diesen Versuch.
- **Fix 1:** neues `live_gates._short_work_dir_name(sample_id)` →
  `sha256(sample_id)[:16]`, ein fester 16-Hex-Zeichen-Name unabhängig von
  Kampagne-/Modell-/Task-ID-Länge. Der volle `sample_id` bleibt im
  Artefakt-JSON erhalten.
- **Fix 2:** ein Gate-Boundary-Exception-Sicherheitsnetz in beiden echten
  Gates (`run_ollama_connect_gate`/`run_ollama_mini_gate`) — jede
  unerwartete Exception nach dem echten HTTP-Aufruf wird jetzt immer als
  schema-valide `status="error"`/`acceptance_status="not_usable"`-Probe
  (`system_error_code="GATE_BOUNDARY_UNEXPECTED_EXCEPTION"`) persistiert
  statt die Session abstürzen zu lassen.
- **Fix 3:** ein atomarer Checkpoint **vor** jedem Gate-Aufruf
  (zusätzlich zum bestehenden danach), neuer Parameter/Flag
  `halt_on_gate_exception`/`--halt-on-gate-exception` (Default: Kampagne
  läuft nach einem Gate-Boundary-Vorfall weiter), sowie ein Retry-mit-
  Backoff um den atomaren Checkpoint-`os.replace()` (transiente Windows-
  `PermissionError` unter erhöhter Schreibfrequenz behoben).
- Resume für den exakten realen DB-Zustand (ein akzeptiertes Connect-Gate,
  kein Mini-Gate) verifiziert: Connect-Gate wird übersprungen
  (`"resumed"`), nur das fehlende Mini-Gate plus die drei noch nie
  versuchten Modelle laufen. Die reale Kampagnen-Evidenz (gitignored)
  wurde nicht verändert/gelöscht.
- **10 neue Regressionstests, 273/273 bestehen** (vorher 263), über 10
  volle Testläufe gegen Flakiness verifiziert. Keine Abhängigkeiten
  installiert; kein Live-Modell-/Netzwerkaufruf.
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  §18, `docs\operations\runbook.md` §8, `docs\project\changelog.md`
  (Eintrag "Serial-Pilot-Crash-Fix").
- **Exakter Wiederaufnahme-Befehl für den Parent-Agent:**
  ```powershell
  cd C:\GIT\llm-evaluation-workbench\scripts
  python .\run_agent_helper_campaign.py serial-execute `
    --campaign-id agent-helper-serial-pilot-20260801 --confirm `
    --models "qwen3-coder:30b,deepseek-coder-v2:16b,phi4-mini:3.8b-q4_K_M,rnj-1:8b"
  ```

### Stand: 2026-08-03 — Agent-Helper-Evaluation-Track: Ollama-Inventar-Discovery + serieller Gate-Runner (kein Live-Modell)

- Zwei neue Module, beide reine Discovery-/Orchestrierungslogik ohne
  jeden Modell-/API-Aufruf durch diese Session:
  `scripts\agent_helper_eval\ollama_inventory.py` (Discovery installierter
  Ollama-Tags ausschließlich über `GET /api/tags` + `POST /api/show` — nie
  `/api/generate`, lädt/generiert also nie ein Modell; Architektur-
  Klassifikation MoE/dense nur bei echter Evidenz, sonst `"unknown"`;
  Cloud-Tag-Erkennung über Ollamas eigene Namenskonvention; generierter
  JSON-Snapshot wird **in das jeweilige Kampagnen-Ausgabeverzeichnis**
  geschrieben, die handkuratierte
  `benchmarks\agent-helper-model-inventory.example.json` bleibt
  unverändert; VRAM-Passungs-Projektion immer `confidence="estimated"`)
  und `scripts\agent_helper_eval\serial_campaign.py` (`build_serial_plan()`
  — explizite Modellliste oder Filter-Modus, jedes entdeckte Modell
  erscheint immer transparent in `included` oder `deferred` mit Grund,
  nie stillschweigend übergangen; `run_serial_campaign()` — strikt
  sequenzielle Connect→Mini-Gate-Ausführung pro Modell mit Resume
  [überspringt jedes bereits persistierte (Modell, Task)-Paar, auch
  Fehlschlag/Timeout], engem Retry [nur transientes `status=error`],
  Cooldown, atomarem JSON-Checkpoint nach jedem Schritt, Preflight-Lock-
  Verweigerung stoppt standardmäßig die ganze Serie).
- Drei neue CLI-Subcommands in `run_agent_helper_campaign.py`:
  `ollama-inventory-snapshot` (real, read-only), `serial-plan` (immer
  trocken), `serial-execute` (erfordert `--confirm` **und** eine explizite
  `--models`-Liste oder mindestens ein Filter-Flag — ein versehentliches
  Ausführen aller entdeckten Modelle ist durch das CLI-Design
  ausgeschlossen).
- 40 neue, schnelle, deterministische Regressionstests (gemockte
  Discovery-Transports, gemockte Gate-Funktionen, ein echter End-to-End-
  Test durch die reale `live_gates`-Verdrahtung mit Fake-Transports, CLI-
  Wiring-Tests mit auf ein Temp-Verzeichnis gepatchtem `campaign_cli.ROOT`
  damit das reale Repository nie berührt wird). **263/263 Tests bestehen**
  (vorher 223). Kein Commit/Push; keine Abhängigkeiten installiert; kein
  Live-Modell-, Live-Ollama-Discovery- oder Netzwerkaufruf in diesem
  Zyklus; keine Command-Center-Repositories berührt.
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  §17, `docs\operations\runbook.md` §8, `docs\project\changelog.md`
  (Eintrag "Ollama-Inventar-Discovery + serieller Gate-Runner").

### Stand: 2026-08-02 — Agent-Helper-Evaluation-Track: Pilot-Review-Remediation (3 Fixes, kein Live-Modell)

- Ein Pilot-Reviewer führte den echten CLI-Connect-/Mini-Gate gegen
  `qwen3-coder:30b` aus (Kampagne `agent-helper-pilot-20260801`) und meldete
  drei materielle Mängel. Alle drei wurden **ausschließlich als Schema-/
  Logik-/Datenkorrektur** behoben — in diesem Zyklus wurde kein Modell/keine
  API erneut aufgerufen; die bereits persistierte Pilot-Kampagne wurde aus
  bereits erfassten Daten repariert.
- **Fix 1 (Evidenz-Stufen/Tiering-Überschätzung):** `qwen3-coder` wurde nach
  nur Smoke-/Mini-Evidenz ohne Reviewer-/Kollaborations-Abdeckung fälschlich
  als `tier-1-recommended` gelabelt. Neu: explizites `evidence_stage`
  (`gate_only`/`partial_suite`/`full_suite`) pro Aggregat-Gruppe
  (`rubric.evidence_stage()`), neue Tier-Stufe `gate-passed-provisional`,
  die `rubric.suitability_tier()` erzwingt sobald `evidence_stage !=
  full_suite` — unabhängig vom Score. `schema.validate_aggregate()`
  verbietet die Kombination `evidence_stage != full_suite` +
  `tier-1-recommended` als hartes Schema-Invariant. Report-Leaderboard und
  Executive Summary (DE/EN) machen "Gate bestanden ≠ Eignungsurteil"
  jetzt explizit.
- **Fix 2 (Phasenzuordnung):** `live_gates.py` schrieb den kalten
  Modell-Ladezeit-Wert (`load_duration`) fälschlich in `llm_queue_seconds`,
  wodurch ~50 % der Mini-Gate-Wandzeit als "Queue/Idle" statt als LLM/API
  ausgewiesen wurde. Jetzt: `llm_request_seconds` = gemessene
  Client-Wandzeit des ganzen Ollama-Calls (gesamter Call = LLM/API-
  Critical-Path), `llm_queue_seconds = None` (Fail-fast-Preflight-Lock
  wartet nie real), neues additive Feld `model_load_seconds` als reines
  Diagnose-Subfeld (bereits in `llm_request_seconds` enthalten, nie
  doppelt gezählt).
- **Fix 3 (`cpu_time_seconds` immer N/A):** Neue, explizit benannte Felder
  `orchestrator_cpu_time_seconds` (via `time.process_time()`-Klammerung in
  beiden echten Gates) und `model_cpu_time_seconds` (neues Per-PID-
  CPU-Zeit-Delta-Tracking in `resource_monitor.ResourceMonitor`, ehrlich
  `None`, wenn kein passender Prozess gefunden wurde).
- **Doku-Konsistenz:** "9-Test-Suite" → korrekt "10-Test-Suite" (AGENTS.md,
  runbook.md, changelog.md, agent_helper_benchmark.md) — der reale Fixture-
  Code hat exakt 10 `unittest`-Testmethoden, das war ein reiner Tippfehler.
- **Neues Reparatur-Modul `scripts\agent_helper_eval\repair.py`:**
  `repair_legacy_llm_phase_timing()` (leitet die echten Ollama-Dauern aus
  dem bereits geschriebenen Artefakt-JSON einer Probe neu ab, eng
  gefingerprintet, idempotent, fabriziert nie) und `recompute_campaign()`
  (repariert alle passenden Proben, baut alle Aggregate mit der aktuellen
  Rubrik neu, exportiert beide CSVs neu, baut Report neu). Neuer CLI-
  Subcommand `recompute-campaign --campaign-id <id>` in
  `run_agent_helper_campaign.py`. Einmal gegen die reale Pilot-Kampagne
  ausgeführt und verifiziert (Tiers jetzt korrekt
  `gate-passed-provisional`, `llm_request_seconds` populiert,
  `llm_queue_seconds` jetzt `N/A`, `cpu_time_seconds`-Familie ehrlich
  weiterhin `N/A` für historische Zeilen — nicht rückwirkend rekonstruierbar).
- `storage.py`: neue `_add_missing_columns()`-Migration (additive
  `ALTER TABLE`), notwendig damit die bereits bestehende Pilot-SQLite-DB
  die neuen Spalten ohne Datenverlust aufnehmen kann.
- 15 neue, schnelle, deterministische Regressionstests (Storage-Migration,
  Repair-/Recompute-Pipeline End-to-End, Resource-Monitor-CPU-Zeit-Delta,
  Live-Gate-Phasenzuordnung/CPU-Zeit gemockt). **223/223 Tests bestehen**
  (vorher 208). Kein Commit/Push; kein Live-Modellaufruf in diesem Zyklus;
  keine Command-Center-Repositories berührt.
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  §16.5, `docs\operations\runbook.md` §8, `docs\project\changelog.md`
  (Eintrag "Pilot-Review-Remediation").

### Stand: 2026-08-02 — Agent-Helper-Evaluation-Track: Realer Ollama-Connect-/Mini-Gate-Executor

- Neuer, echter One-Model-Ollama-Connect-Gate- und Mini-Coding-Task-Executor:
  `scripts\agent_helper_eval\resource_monitor.py` (CPU/RAM/GPU/VRAM-Sampler,
  eigenständig, kein Import aus `llm_migration_benchmark.py`),
  `ollama_client.py` (nur `urllib`, injectable Transport, echte
  Time-to-First-Token aus dem Stream, strikte Max-ein-lokales-Modell-
  Preflight, best-effort Unload), `mini_task.py` (fester, maschinenlesbarer
  Coding-Task-Contract; AST-basierter Safety-Scan *vor* jeder Ausführung;
  Bewertung über eine feste 10-Test-`unittest`-Suite in einem Subprozess —
  nie Keywords), und `live_gates.py` (Orchestrierung:
  Preflight→Lock→Generate→Scoring→immer-Unload→Persistenz durch bestehende
  SQLite-SSOT + beide CSVs + Report-Neuaufbau; hartes Akzeptanz-Gate gilt
  immer).
- Neue CLI-Subcommands `connect-gate-run`/`mini-gate-run` in
  `scripts\run_agent_helper_campaign.py` — beide erfordern explizites
  `--model` + `--campaign-id`, iterieren nie ein Inventar, sind bewusst
  One-Shot (Korrektur-/Folge-Iteration = separater, späterer Befehl). Eine
  Preflight-/Lock-Verweigerung persistiert nichts; ein echter Fehler/Timeout
  wird dagegen persistiert.
- **Transparenznotiz:** Bei der manuellen CLI-Verifikation griff ein
  Modul-Monkeypatch-Versuch auf `ollama_client.urllib_json_transport`/
  `urllib_stream_transport` **nicht** (Python bindet Default-
  Parameterwerte beim Funktionsimport, nicht als Attribut-Lookup zur
  Aufrufzeit) — dadurch erreichte ein CLI-Testlauf versehentlich den
  echten, lokal laufenden Ollama-Server und löste einen echten, aber
  trivialen (2 Tokens, "OK") Generate-Call gegen `qwen3-coder:30b` aus. Ein
  Verstoß gegen die "kein Modellaufruf in dieser Session"-Vorgabe, sofort
  erkannt, das versehentliche Kampagnenverzeichnis gelöscht, kombinierter
  Bericht über die modellfreie `report`-Subcommand neu gebaut. Volle Details:
  `docs\project\agent_helper_benchmark.md` §16.4.
- 46 neue, schnelle, deterministische Tests (ausschließlich mit injizierten
  Fake-Transports/gemockten `live_gates`-Funktionen; kein Test ruft ein
  Modell auf). **207/207 Tests bestehen** (vorher 161). Keine CSV-/Report-
  Schema-Änderung; keine bestehende Kampagne/Historie berührt; kein
  Commit/Push; keine Abhängigkeiten installiert; keine Command-Center-
  Repositories berührt.
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  §16, `docs\operations\runbook.md` §8, `docs\project\changelog.md`
  (Eintrag "Realer Ollama-Connect-/Mini-Gate-Executor").

### Stand: 2026-08-02 — Agent-Helper-Evaluation-Track: Multi-Agent-Koordination & externer Katalog-Import

- **Auftrag (Koordinations-Update):** Ein weiterer, gleichzeitig laufender
  Agent/Session arbeitet in diesem Workspace am separaten "Command
  Center"-Werkzeug/Cloud-Evaluationen und darf zusätzliche offline
  Benchmark-Set-Dateien beisteuern, führt aber **nie** lokale
  Ollama/llama.cpp-Kampagnen aus (bleibt exklusiv diese Session). Gefordert:
  externe, versionierte Benchmark-Kataloge ohne Code-Änderung ladbar/
  validierbar; Ownership-/Koordinationsnotiz; konfliktsicherer
  Beitrags-Workflow (separate Dateien, keine Shared-Edits ohne
  Übergabenotiz, Ergebnisisolation, Pflicht-Provenienz); Koordination nur
  über Repo-Doku/Handover, nie direkte Kontaktaufnahme.
- **Reale Koexistenz-Evidenz bestätigt (git status geprüft):**
  `benchmarks\swe-mixed-hard-24.json`/`swe-python-hard-24.json`/
  `swe-sql-hard-24.json`/`web-grid-demo-v1.json` sind Benchmark-Sets im
  *anderen* `llm_migration_benchmark.py`-Format (nicht
  `agent-helper-catalog-v1`); `benchmark_results\agent-helper\
  wtcc-20260801\` ist ein fremdes, nicht-SQLite Rohdaten-Kampagnen-
  verzeichnis **innerhalb** unseres sonst exklusiven Namensraums. Beides
  unangetastet gelassen; `build_combined_report()` überspringt bereits
  jedes Verzeichnis ohne `agent_helper.sqlite3` (verifiziert).
- `catalog.py`: neuer `load_catalog_document()`-Vertrag mit
  `SUPPORTED_CATALOG_SCHEMA_VERSIONS`-Prüfung (zuvor ungeprüft — ein
  Fremdformat-File hätte eine rohe `TypeError` statt eines klaren Fehlers
  ausgelöst) und neuer Pflicht-`CatalogMetadata`
  (`catalog_id`/`author`/`created_at`/`source_notes`) als Katalog-*Datei*-
  Provenienz (getrennt von `SampleRecord.provenance`).
  `load_catalog()` bleibt abwärtskompatibel; `save_catalog()` verlangt
  jetzt `metadata` (einziger Aufrufer `build_example_configs.py`
  angepasst; `agent-helper-catalog-v1.json` neu generiert).
- Orchestrator (`generate_synthetic_samples`/`run_dry_run`) und CLI
  (`run_agent_helper_campaign.py dry-run --catalog PATH`) verdrahtet, damit
  ein extern geladener Katalog Samples korrekt mit seiner eigenen
  `benchmark_set`-Identität beschriftet statt der Standardidentität.
- Neue Beispieldatei `benchmarks\agent-helper-catalog.example-external.json`
  demonstriert den Vertrag konkret und eigenständig ladbar.
- Neuer Abschnitt §15 + §6.1 in `docs\project\agent_helper_benchmark.md`.
- Testsuite von 145 auf **161/161 bestandene** Tests erweitert (16 neu in
  `CatalogTests`). Schema-Version/CSV-Spalten unverändert; keine
  bestehende Kampagne/Historie berührt; keine Command-Center-Repos
  berührt; keine Modell-/API-Aufrufe.
- Details: `docs\project\agent_helper_benchmark.md` §6.1/§15,
  `docs\project\changelog.md`.

### Stand: 2026-08-02 — Agent-Helper-Evaluation-Track: Erweiterte visuelle Berichterstattung

- **Auftrag:** Nutzer forderte einen hochwertigen, farbenfrohen, aber
  evidenzbasierten Offline-HTML-Bericht mit vielen aussagekräftigen
  Visualisierungen (keine dekorative Diagramm-Überladung): Executive-KPI-
  Karten, Akzeptanz-Gate-Trichter, Qualität-vs-Geschwindigkeit mit
  Pareto-Front, Zeit-bis-akzeptiert-Balken, Task-Modell-Heatmap,
  P50/P95/P99-Latenz, kritischer Pfad + Auslastungsquoten, CPU/GPU- und
  RAM/VRAM-Auslastung, Nebenläufigkeitsskalierung (1/2/4), Zuverlässig-
  keits-/Fehler-, Iterations-/Rework-Diagramm, gemessen-vs-projizierte
  RTX-5090-Passung, Routing-/Kosten-Szenario, historischer Verlauf.
- 16 neue Diagramm-/KPI-Renderer in neuem Modul `scripts\agent_helper_eval\
  charts.py` implementiert; `report.py`s gemeinsame Text-/Style-Helfer
  (`esc`/`fmt`/`bi`/`TIER_COLORS`/Tier-CSS-Klassen) in neues
  `report_style.py` ausgelagert, um einen Zirkelimport zwischen `report.py`
  und `charts.py` zu vermeiden.
- **Echter Bug beim Verschieben gefunden und behoben:** `scaling_css_class()`
  bildete Skalierungsklassifikationen auf nicht existente CSS-Klassennamen
  ab (Kapazitätsprofil-Skalierungs-Badge war stillschweigend ungestylt).
- Harte Trennung durchgehalten: Ranking-Diagramme zeigen ausgeschlossene
  ("nicht nutzbare") Modell-Läufe nie als geplotteten Punkt, nur in einer
  eigenen Ausschluss-Notiz; Diagnose-/Ressourcen-Diagramme zeigen jeden
  Modell-Lauf, markieren ausgeschlossene Zeilen aber sichtbar mit "⚠".
  Barrierearme Muster (Schraffur, nicht nur Farbe) für "nicht nutzbar" und
  "projiziert/geschätzt"; RTX-5090-Marker kann laut Schema nie als
  "gemessen" gerendert werden, selbst defensiv nicht.
- Alle 16 Diagramme in `_render_campaign()`/`render_report()` verdrahtet;
  neue CSS für KPI-Kartenraster; `@media print` klappt jetzt zusätzlich
  jede eingeklappte Kampagne für den Druck/PDF-Export auf.
- Testsuite von 116 auf **145/145 bestandene** deterministische Tests
  erweitert (29 neu in `ChartRenderingTests`). Schema-Version weiterhin
  `agent-helper-v1` (rein additive Bericht-/Darstellungsänderung; kein
  CSV-/SQLite-Schemafeld geändert).
- Details: `docs\project\agent_helper_benchmark.md` Abschnitt 12.1–12.4,
  `docs\project\changelog.md`.
- Weiterhin keine Modellkampagne gestartet; ausschließlich Harness-/
  Report-Arbeit. Manueller synthetischer Dry-Run-Report lokal generiert,
  geprüft und wieder vollständig entfernt — kein Artefakt im Repo.

### Stand: 2026-08-01 — Agent-Helper-Evaluation-Track: Historical-Adapter-Härtung anhand realer Evidenz

- **Kontext:** Nutzer bestätigte korrigierte historische Evidenz: Repo hat
  19 Commits (2 vor `origin/main`); eine primäre historische Ausgabe liegt
  außerhalb des Repos (`C:\Users\z000g9hu\benchmark_results\
  migration_llm_bench_history.csv`, 64 Zeilen); repo-lokale
  Resume-Historien (11 Siemens-`gpt-oss` + 11 Ollama-`deepseek-coder-v2`
  Zeilen) bestätigt. `ora-pg-py-33` hat 11 Aufgaben; `deepseek-v4-flash`
  schloss 7 ab, 4 liefen in einen Timeout (36,36 %).
- **Echter Bug gefunden und behoben:** eine Sample-ID-Kollision im
  Historical-Adapter führte dazu, dass ein Task, der zunächst in einen
  Timeout lief und dann (ohne dass der Legacy-Runner seine `run`-Spalte
  erhöht hätte) erfolgreich retried wurde, in der SQLite-`samples`-Tabelle
  (`PRIMARY KEY("sample_id")` + `INSERT OR REPLACE`) stillschweigend
  überschrieben wurde — die Timeout-Evidenz ging verloren. Verifiziert
  anhand der echten externen Datei: vor dem Fix 60/64, nach dem Fix 64/64
  Zeilen erhalten (inkl. aller 4 `deepseek-v4-flash`-Timeouts). Fix: jede
  `sample_id` enthält jetzt zusätzlich den strikt monoton steigenden
  `sample_seq`.
- Konfigurierbare externe Importpfade (bereits vorhanden, jetzt live
  gegen die echte externe Datei sowie zwei repo-lokale Resume-Historien
  verifiziert), explizite `"source file: ..."`/`"confidence: estimated"`-
  Vermerke in `notes`, und Timeout- vs. generischer Fehlerstatus (neue
  Unterscheidung, betrifft nur Berichtspräzision, nie das
  Akzeptanz-Gate-Ergebnis) ergänzt in `historical_adapter.py`.
- Bereits bestehende Sicherung bestätigt (kein Codeänderungsbedarf):
  `rubric.is_heuristic_reviewer_provenance()` deckelt jede rein aus
  historischem Import bestehende Modell-Lauf-Gruppe strikt unterhalb
  `tier-1-recommended`, verifiziert live gegen die echten
  `deepseek-v4-flash`-Daten trotz `quality_score`-Werten von teils 100,0.
- Testsuite von 110 auf **116/116 bestandene** deterministische Tests
  erweitert (6 neu in `HistoricalAdapterTests`). Schema-Version weiterhin
  `agent-helper-v1` (rein additive Adapter-Korrektur).
- Details: `docs\project\agent_helper_benchmark.md` Abschnitt 5,
  `docs\project\changelog.md`.
- Weiterhin keine Modellkampagne gestartet; ausschließlich Harness-
  Korrektur-/Verifikationsarbeit gegen bereits vorhandene, unveränderte
  historische CSV-Dateien (nur gelesen, nie geschrieben).

### Stand: 2026-08-01 — Agent-Helper-Evaluation-Track: Report-Design-Pattern-Abgleich

- **Kontext:** gezielte Übernahme von fünf wiederverwendbaren
  Report-Design-Mustern aus einer Sichtung von `ai-engineering-investment-
  case` (separates, unverbundenes internes Repo, selbst ohne Commits —
  daher bewusst nicht als starke Evidenz behandelt). Übernommen wurden
  ausschließlich Muster, keine Inhalte — keine ROI-/Finanzzahlen aus jenem
  Repo wurden übernommen oder referenziert.
- Neue Navigation in `report.py`: Vollbild-Umschalter-Button plus
  Tastaturkürzel `L`/`F`/`P` (Sprache/Vollbild/Drucken), geschützt gegen
  Modifikator-Tasten und Texteingabefokus; zweisprachiger `<kbd>`-Hinweis;
  alle Steuerelemente in `@media print` ausgeblendet.
- **Größte reale Lücke geschlossen:** das seit einem früheren Zyklus
  bestehende `model_inventory.py`-Datenmodell (`ModelSpec`/
  `FeasibilityProjection`, striktes measured/estimated/projected/unknown-
  Feld, RTX-5090-32GB strukturell nie "measured") wurde bislang **nie im
  HTML-Bericht angezeigt**. Neuer berichtweiter Abschnitt "Model inventory
  & feasibility" mit measured/projection-Badges (defensiv abgesichert
  gegen manipulierte "measured"-Behauptungen bei der RTX-5090-Spalte);
  `orchestrator.py` lädt die Inventardatei jetzt best-effort
  (`load_default_model_inventory()`, wirft nie).
- Doppel-Baseline-Vergleich (Concurrency=1 als Baseline), Gate-Unabhängigkeit
  von Kosten-/Provider-Stufe, und kanonische Datenquelle ohne Copy/Paste-
  Drift waren strukturell bereits vorhanden — jetzt zusätzlich als
  explizite zweisprachige Methodik-Einträge sichtbar gemacht, plus ein
  "Baseline"-Chip auf der Concurrency=1-Zeile der Profil-Tabelle.
- Testsuite von 94 auf **110/110 bestandene** deterministische Tests
  erweitert (16 neu: `ModelInventoryReportRenderingTests`,
  `OrchestratorModelInventoryLoadingTests`, `MethodologyContentTests`,
  `ReportNavigationTests`). Schema-Version weiterhin `agent-helper-v1`
  (rein additive Bericht-/Orchestrierungsänderung; kein CSV-/
  SQLite-Schemafeld geändert; `render_report()`'s neuer
  `model_inventory`-Parameter ist optional mit leerem Default).
- Details: `docs\project\agent_helper_benchmark.md` Abschnitt 12
  (HTML-Bericht) und `docs\project\changelog.md`.
- Weiterhin keine Modellkampagne gestartet; ausschließlich Harness-/
  Report-Arbeit.

### Stand: 2026-08-01 — Agent-Helper-Evaluation-Track: Phasenzuordnung & Concurrency-Capacity-Profiling

- **Phasenzuordnung pro Sample (wo beobachtbar):** zehn neue rohe
  Zeit-Felder auf `SampleRecord` (`total_wall_seconds`, `llm_queue_seconds`,
  `llm_request_seconds`, `prompt_eval_seconds`, `generation_seconds`,
  `local_tool_exec_seconds`, `test_exec_seconds`,
  `orchestrator_review_seconds`, `idle_wait_seconds`, `overlap_seconds`) —
  einzeln optional, für Copilot-Task-Agent-Samples strukturell immer `None`
  statt geschätzt. Neues reines Modul `phase_timing.py` berechnet vier
  **exklusive** Critical-Path-Prozentsätze (LLM/Tools+Tests/Queue+Idle/
  Orchestrierung), normiert gegen die eigene Rohsumme (nie gegen
  `total_wall_seconds`) und daher immer exakt 100 % — Overlap/unerklärte
  Zeit bleiben separate Diagnosewerte. Zusätzlich drei **nicht-exklusive**
  Auslastungsquoten (Modell-Busy-%, GPU-Active-%, Tool-Runner-Busy-%,
  bewusst nicht auf 100 % gedeckelt). `aggregate.py` rollt dies per
  Ratio-of-Sums in zehn neue `AggregateRecord`-Felder auf.
- **Concurrency-/Capacity-Profiling (nur nach bestandenem Akzeptanz-Gate):**
  neue dritte kanonische Tabelle `CapacityProfileRecord`
  (`agent_helper_capacity_profile.csv`) plus neues Modul
  `capacity_profile.py` mit Katalog für 1/2/4 gleichzeitige Anfragen an
  **dasselbe** akzeptierte lokale Modell und Skalierungsklassifikation.
  **Hartes, zweifach abgesichertes Preflight-Gate** verweigert jede
  Profilerzeugung, solange `preflight_quality_gate_passed` und
  `preflight_memory_safety_checked` nicht beide `True` sind — bei 12 GB
  VRAM darf Concurrency-Profiling nie vor bestandener Einzel-Request-
  Qualitäts-/Speicher-Sicherheitsprüfung laufen.
- `orchestrator.py`-Dry-Run und kombinierter Bericht erzeugen/rendern
  deterministische synthetische Demos beider Features (kein echter
  Modell-/Ressourcenzugriff); neue HTML-Bericht-Abschnitte je Modell-Lauf
  (Phasenzuordnungstabelle, Concurrency-Profil-Tabelle) mit explizitem
  "nicht instrumentiert"/"kein Profil vorhanden"-Hinweis statt erfundener
  Werte.
- Testsuite von 56 auf **94/94 bestandene** deterministische Tests erweitert
  (38 neu: `PhaseTimingTests`, `CapacityProfileTests`,
  `CapacityProfileSchemaValidationTests`, `AggregatePhaseTimingWiringTests`,
  `AggregateSchemaPhaseTimingValidationTests`,
  `CapacityProfileCsvRoundTripTests`, `PhaseTimingReportRenderingTests`).
  Schema-Version weiterhin `agent-helper-v1` (rein additiv; bestehende
  66/67-Spalten-CSVs und Historie unangetastet; neue dritte CSV additiv).
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  Abschnitt 11 ("Phase attribution & capacity/concurrency profiling").
- Weiterhin keine Modellkampagne gestartet; ausschließlich Harness-Arbeit.

### Stand: 2026-08-01 — Agent-Helper-Evaluation-Track: Hartes Akzeptanz-Gate

- **Kritische Nutzeranforderung umgesetzt:** Geschwindigkeit darf NIE
  mangelnde Nutzbarkeit ausgleichen. Ein hartes Akzeptanz-Gate läuft jetzt
  **vor** jeder Performance-/Composite-Rangfolge; Performance ist nur noch
  Tie-Breaker unter bereits akzeptierten, nutzbaren Ergebnissen.
- Sample-Ebene: `rubric.compute_sample_acceptance()` klassifiziert jeden
  Task-Attempt als `accepted` / `not_usable` / `not_evaluated` — zwingend
  `not_usable` bei fehlgeschlagenem deterministischem Test, unsicherem
  Verhalten (`unsafe_behavior_flag`), Platzhalter-/unvollständiger Ausgabe
  oder zu niedrigem Reviewer-Score. Neue `SampleRecord`-Felder:
  `unsafe_behavior_flag`, `output_placeholder_or_incomplete`,
  `acceptance_status`, `acceptance_reasons`.
- Aggregat-Ebene: `rubric.compute_aggregate_hard_gate()` markiert einen
  gesamten Modell-Lauf als `hard_gate_failed` (Suitability-Tier
  `"not-usable"`) bei unsicherem Verhalten (Zero-Tolerance) oder
  Akzeptanzrate < 50 % der bewerteten Attempts; `aggregate.py` berechnet
  Peer-Pool-Speed-Scores zweistufig — ausgeschlossene Gruppen und deren
  nicht-akzeptierte Samples fließen nie in den Vergleichspool ein. Neue
  `AggregateRecord`-Felder u. a. `accepted_sample_count`,
  `not_usable_sample_count`, `acceptance_rate_percent`,
  `unresolved_task_count`, `time_to_accepted_result_seconds_mean`,
  `rework_tokens_to_accept_mean`, `hard_gate_failed`, `hard_gate_reasons`.
- **Keyword-/Heuristik-Score-Sicherung ("RNJ-1"-Fix):** ein Modell-Lauf,
  dessen gesamte Qualitätsevidenz allein auf einem Legacy-Heuristik-/
  Keyword-Reviewer-Score beruht (keine deterministische Testevidenz), wird
  strukturell unterhalb von `tier-1-recommended` gedeckelt
  (`reviewer_evidence_is_heuristic_only`, von `schema.validate_aggregate()`
  erzwungen). Vor diesem Fix hätte ein einzelner, per Legacy-Import
  übernommener Keyword-Score ein Modell fälschlich als "starker Kandidat"
  ausgewiesen — genau das Szenario, vor dem der Nutzer gewarnt hat.
  `historical_adapter.py` markiert importierte Legacy-Reviewer-Scores
  entsprechend.
- HTML-Bericht: eigene "Excluded"-Tabelle für hart-gated Modell-Läufe
  (nie im Leaderboard/Chart), Executive Summary formuliert die Hart-Regel
  zweisprachig explizit und nennt jeden Ausschluss sowie ggf. den
  Keyword-Score-Vorbehalt beim "besten nutzbaren Modell".
- Testsuite von 27 auf **56/56 bestandene** deterministische Tests erweitert
  (`HardAcceptanceGateTests`, `AggregateSchemaValidationTests`, neue
  `SchemaValidationTests`-Fälle, ein `ReportEscapingTests`-Fall für die
  Leaderboard-/Excluded-Trennung). Schema-Version weiterhin `agent-helper-v1`
  (rein additiv, bestehende Spalten/Historie unangetastet).
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  Abschnitt 9 ("Hard acceptance gate").
- Weiterhin keine Modellkampagne gestartet; ausschließlich Harness-Arbeit.

### Stand: 2026-08-01 — Agent-Helper-Evaluation-Track (Harness-Fundament)

- Neues, additives Subsystem `scripts\agent_helper_eval\` implementiert (reines
  Mess-/Daten-/Report-Harness; **keine Modell-/API-/Ollama-/llama.cpp-Aufrufe**
  in diesem Schritt, wie beauftragt).
- Kanonisches Speichermodell: SQLite als Single Source of Truth
  (`agent_helper.sqlite3`) je Kampagne, plus deterministische CSV-Exporte:
  `agent_helper_samples.csv` (Task-Attempt-Ebene) und
  `agent_helper_aggregates.csv` (Modell-Lauf-Ebene). Schema-Version
  `agent-helper-v1`, feste Spaltenreihenfolge, `validate_sample`/
  `validate_aggregate` erzwingen Kontrakt; nie stillschweigend geänderte Spalten.
- Module: `schema.py` (Datenmodell/Validierung), `storage.py` (SQLite+CSV),
  `aggregate.py` (Perzentile/Gruppierung/Scoring), `rubric.py`
  (Orchestrator-Rubrik: deterministisch → Reviewer → Kollaboration →
  Geschwindigkeit; deckelt Gesamt-Score bei fehlgeschlagenem
  deterministischem Gate — ein schnelles, falsches Modell kann nicht allein
  über Tokens/s gewinnen), `historical_adapter.py` (liest **nur**, importiert
  `migration_llm_bench_history.csv` verlustfrei mit expliziten N/A-Markierungen
  für nicht konvertierbare Legacy-Felder), `catalog.py` (7 Staged-Gate-Aufgaben:
  Connect-Smoke, Mini-Coding-Tests, Bug-Review, SQL-Migration, Frontend,
  Architektur/Planung, Multi-Turn-Kollaboration — Pure-Model- vs.
  Tool-Agent-Track explizit getrennt), `local_lock.py` (Dateisystem-Lock:
  maximal 1 lokales Modell gleichzeitig), `model_inventory.py`
  (Modell-/Kampagnen-Konfigurationsformat für Ollama/llama.cpp/Siemens/Copilot,
  keine Secrets, RTX-5090-32-GB-Projektionen dürfen nie als "measured" markiert
  werden), `report.py` (eigenständiger, offline-fähiger HTML-Bericht,
  DE/EN zweisprachig, aktuelle Kampagne aufgeklappt/ältere eingeklappt,
  sortier-/filterbare Tabellen, escaped jeden untrusted Modellinhalt),
  `orchestrator.py` (synthetischer Dry-Run, Connect-Gate-Planung als reine
  Textausgabe, Connect-Gate-Ausführung nur mit injiziertem Executor —
  `subprocess_executor` wird in diesem Schritt nirgends aufgerufen).
- CLI-Einstieg: `scripts\run_agent_helper_campaign.py` mit Subcommands
  `dry-run`, `connect-gate-plan`, `report`, `import-legacy`.
- Neue Ergebnisse isoliert unter `benchmark_results\agent-helper\<campaign-id>\`
  (dieser Pfad ist vollständig `.gitignore`t, wie der Rest von
  `benchmark_results\`); bestehende Ordner/Historie unverändert.
- Verifiziert (in dieser Session, dann wieder aufgeräumt): synthetischer
  Dry-Run End-to-End (42 Samples, 6 Aggregate, HTML-Report), Connect-Gate-Plan
  für die Beispiel-Inventory, sowie ein realer Legacy-Import (683/683 Zeilen aus
  `migration_llm_bench_history.csv`, Original-CSV unverändert).
- 27 deterministische Unit-Tests in `scripts\test_agent_helper_eval.py`
  bestanden (`python -m unittest test_agent_helper_eval -v`), keine bestehenden
  Tests/Skripte verändert.
- Vollständige Methodik/Schema/Rubrik-Doku:
  `docs\project\agent_helper_benchmark.md`.
- **Nächster Schritt (Parent-Agent, seriell, ein Modell nach dem anderen):**
  zuerst `dry-run` erneut zur Bestätigung, dann `connect-gate-plan` prüfen und
  **manuell** je einen Connect-Check ausführen (nie automatisiert durch dieses
  Script) — siehe `docs\operations\runbook.md` für exakte Befehle.

### Stand: 2026-07-31

- Living-memory-Verstaendnisbenchmark implementiert:
  - Tool-Agent-Track navigiert den echten Workspace ueber OpenCode.
  - Pure-Model-Track nutzt fuer API-Modelle ausschliesslich ein synthetisches Fixture.
  - Restricted echte Workspace-Faelle sind auf lokale `ollama/*`-Modelle begrenzt.
  - Gewichtetes Scoring, Diagnose-Follow-up und track-getrennte Aggregation vorhanden.
  - Dry-runs und Unit-Tests bestanden; noch keine Modellkampagne gestartet.
  - Siemens-Pure-Model-Kampagne abgeschlossen: alle 5 chatfaehigen Modelle,
    70/70 Basisantworten ohne Fehler.
  - V1-Lexical-Score: Ministral 73.64, Qwen 73.56, GPT-OSS 70.75,
    DeepSeek 64.34, Mistral Small 51.95.
  - Ein fokussierter V2-Naming-Nachtest erreichte nach sechs exakten Regeln plus
    Beispiel bei allen Modellen 100 Prozent.
  - Kanonischer Laufbericht:
    `benchmark_results\living-memory-siemens-20260730-1418\evaluation.md`.
  - Hard-Agent-V2 abgeschlossen: echte Tool-Handlungen, 25% Antwort/75% Zustand.
  - Ergebnis ohne separat nachgetestete Sprache: Qwen 95.74, DeepSeek 94.80,
    Ministral 78.41, GPT-OSS 69.89, Mistral Small 0 (Tool-API inkompatibel).
  - Nur Ministral pflegte Agent-Memory proaktiv; Qwen/DeepSeek pflegten
    User-Memory. Ministral verletzte `AI-ACCESS: denied`.
  - Hard-Bericht:
    `benchmark_results\living-memory-hard-siemens-v2-20260730\evaluation.md`.
  - Synthetische Hard-Workspace-Fixtures verwenden die gemeinsame hierarchische
    `settings.yml`-Benennung; Scoring-Tests und JSON-Validierung bestanden.
  - OpenCode listet alle lokalen Modelle getrennt nach Backend: 29 Ollama-Tags und sechs
    vollständige llama.cpp-GGUF-Sätze. Ollama und der persistente llama.cpp-Router laden das
    ausgewählte Modell nativ bei der ersten Anfrage; unvollständige/Cloud-Modelle bleiben aus.
  - Alle lokalen Modellkonfigurationen verwenden ihren höchsten deklarierten Kontext; explizit
    größere Sonderwerte bleiben erhalten. Flash Attention plus Q4-KV-Cache begrenzen den
    Speicherbedarf. Qwen 3.6 35B A3B Q4 erreicht bei 262144 Kontext rund 29,7 Token/s.

- Agent: opencode | llm: qwen-3.6-27b | role: benchmark orchestration (gestoppt)
- **Aktivierter P1-Fix (2026-07-29):**
  - `llama.cpp`-Server-Start repariert: `--threads` → `-t` (Build 132.x CLI-Bruch)
  - Debug-Logging für llama-server aktiviert (`stdout` in temporäre Log-Datei)
  - `qwen3.6:27b GGUF` ist inkompatibel zu llama.cpp Build 132.x (`qwen35.rope.dimension_sections` erwartet 4, hat 3) — Ollama-nativ nur lauffähig
  - `qwen3.6:35b-a3b-q4` via llama.cpp erfolgreich (94.68 Overall, 13.38 Tok/s, ngl=99, Balanced)
  - **Alle LLM-Testläufe vom User gestoppt** — zu hoher Ressourcenverbrauch, Wechsel zur manuellen Steuerung
  - **CRITICAL:** Niemals alle bestehenden Benchmark-Results löschen! Alte Ergebnisse sind Vergleichtsgrundlage.
  - **CRITICAL:** Maximal 1 lokales LLM gleichzeitig laufen lassen (12 GB VRAM-Limit). Kein Parallel-Run zweier lokaler Modelle.
  - **TODO:** Pro-Testlauf-Timeout einbauen, damit bei Hängern automatisch zum nächsten Test gesprungen wird

### Open Handover-Tasks (Next Session — Priority)

**A. Modelle installieren (nur Installation, NOCH KEINE Testläufe):**

| Modell | Quantisierung | Status |
|---|---|---|
| Mixtral 8x22B (MoE) | Q4_K_M, Q8_0 | todo |
| Deepseek-v4-flash (MoE) | Q4_K_M, Q8_0 | todo |
| GLM-5.x (MoE) | Q4_K_M, Q8_0 | todo |
| Llama 3 8B (Baseline) | Q4_K_M, Q8_0 | todo |
| Qwen 1.8B (Baseline) | Q4_K_M, Q8_0 | todo |

**Wichtig:** Erst Installation/Konfiguration vorbereiten. Benchmark-Tests erst interaktiv mit User starten.

**B. Bestehende Next-Actions (vorherige Session):**

1. Retry `ministral-3-14b-instruct-2512` (eine Validierung lieferte HTTP 502)
2. Test missing local baselines: `qwen3:32b`, `phi4-mini:3.8b-q4_K_M`, `deepseek-r1:8b`, `llama3.1:8b`
3. Implement the sandbox harness only when explicitly requested
4. **NEU:** Benchmark-Script pro-Testlauf-Timeout implementieren (auto-skip bei Hängern)
5. **NEU:** Benchmark-Script Warnung einbauen: "Max 1 lokales LLM gleichzeitig (12 GB VRAM)"

## Nächster Schritt

- Keine Modellkampagne ohne interaktive Freigabe starten.
- Nach Freigabe zuerst je ein starkes, mittleres und lokales Modell seriell mit
  beiden Verstaendnistracks kalibrieren und die Track-Differenz auswerten.
- Hard-Agent-Findings in technische Gates fuer Owner-Scope, Memory-Checkpoint,
  Sprache pro Turn, Chat-Validierung und Git-Abschluss ueberfuehren.

## Project purpose

Compare local Ollama/llama.cpp models and Siemens cloud models for:

- Oracle-to-PostgreSQL migration tasks;
- translation tasks;
- quality, reliability, throughput, efficiency, and hardware behavior.

## Sources of truth

| Information | Canonical source |
|---|---|
| Current handoff and mandatory rules | this `AGENTS.md` |
| Benchmark implementation | `scripts\llm_migration_benchmark.py` |
| Campaign orchestration | `scripts\run_benchmark_campaign.ps1`, `scripts\run_local_campaign.ps1` |
| Human-readable current report | `docs\project\benchmark_report.md` |
| Detailed live plan/status | `docs\project\benchmark_report_details.md` |
| Comparison table | `docs\project\comparison_table.md` |
| Agent-helper evaluation harness (methodology, schema, rubric) | `docs\project\agent_helper_benchmark.md` |
| Agent-helper harness implementation | `scripts\agent_helper_eval\`, `scripts\run_agent_helper_campaign.py` |
| Control-plane architecture raw evidence and review | `benchmark_results\agent-helper\wtcc-20260801\evaluation.md` |
| Operations | `docs\operations\runbook.md` |
| Setup | `docs\operations\setup-new-pc.md` |
| Change history | `docs\project\changelog.md` |
| Raw/final results | `benchmark_results\` and `data\benchmark_results_legacy\` |

Do not duplicate large result tables in this router. Update the canonical report and
keep only decision-relevant summaries here.

## Environment

```text
llama-server: C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe
llama-cli:    C:\Users\z000g9hu\llama.cpp\bin\llama.exe
benchmark:    scripts\llm_migration_benchmark.py
results:      benchmark_results\
legacy:       data\benchmark_results_legacy\
```

Siemens endpoint: `https://api.siemens.com/llm/v1/chat/completions`.
The token file contains multiple lines; read the first line beginning with `SIAK-`.
Never write the token value into code, logs, documentation, or memory.

Power plans:

```text
Balanced:  381b4222-f694-41f0-9685-ff5bb260df2e
High-Perf: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```

## Decision-relevant benchmark summary

### Siemens cloud final (2026-07-26, three runs)

| Model | Overall | Quality | TPS | Reliability |
|---|---:|---:|---:|---:|
| `gpt-oss-120b` | 84.93 | 88.89% | 163 | 100% |
| `deepseek-v4-flash` | 83.36 | 92.59% | 115 | 100% |
| `qwen-3.6-27b` | 79.67 | 92.86% | 93 | 100% |
| `ministral-3-14b-instruct-2512` | 75.96 | 95.83% | 36 | 88.9% |
| `Mistral-Small-24B-Instruct-2501-FP8-dynamic` | 69.21 | 83.33% | 24 | 100% |

### Local recommendations

| Purpose | Model/configuration | Reason |
|---|---|---|
| Best local all-rounder | `qwen3-coder:30b` via Ollama | 88.89% quality, about 28 TPS |
| Fast local model | `deepseek-coder-v2:16b` via Ollama | about 55 TPS, 84% quality |
| llama.cpp | `gpt-oss:20b`, Balanced, `ngl=99` | about 84 TPS |
| Translation | `qwen3.6:35b-a3b-q4_K_M` | 100% heuristic quality, about 37 TPS |

`ngl` is critical for llama.cpp. Set it as high as available VRAM safely permits.
Balanced has outperformed High-Perf in measured llama.cpp runs; test both rather
than assuming the profile name predicts performance.

## Quick commands

```powershell
cd D:\git\llm-evaluation-workbench

# Siemens, all configured models
python .\scripts\llm_migration_benchmark.py --backend siemens --runs 3

# Retry the model with one HTTP 502 sample
python .\scripts\llm_migration_benchmark.py --backend siemens `
  --siemens-model ministral-3-14b-instruct-2512 --runs 3

# One Ollama model
python .\scripts\llm_migration_benchmark.py --backend ollama `
  --ollama-models "qwen3-coder:30b" --runs 3

# llama.cpp with GPU offload
python .\scripts\llm_migration_benchmark.py `
  --backend llama_cpp `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --llama-model "gpt-oss:20b=C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf" `
  --llama-ngl 99 --runs 3
```

### Agent-helper evaluation harness (no model calls; see `docs\operations\runbook.md`)

```powershell
cd C:\GIT\llm-evaluation-workbench\scripts

# No-model dry-run: validates storage/aggregation/report pipeline end-to-end.
python .\run_agent_helper_campaign.py dry-run --campaign-id dry-run-<date>

# Same, but against an externally authored catalog file (no code change
# needed; see docs\project\agent_helper_benchmark.md §6.1/§15).
python .\run_agent_helper_campaign.py dry-run --campaign-id dry-run-external-<date> `
  --catalog ..\benchmarks\agent-helper-catalog.example-external.json

# Print (never execute) the connect-gate command for every model in an inventory.
python .\run_agent_helper_campaign.py connect-gate-plan `
  --inventory ..\benchmarks\agent-helper-model-inventory.example.json

# Rebuild the combined multi-campaign report from existing campaign data.
python .\run_agent_helper_campaign.py report

# Import the legacy migration_llm_bench CSV into an isolated campaign (read-only source).
python .\run_agent_helper_campaign.py import-legacy `
  --csv ..\benchmark_results\migration_llm_bench_history.csv
```

### Agent-helper real connect-gate / mini-gate (DOES call a real local Ollama server)

The only two subcommands allowed to reach a real Ollama server; never run
automatically by any code in this repository — see
`docs\project\agent_helper_benchmark.md` §16 for full methodology and §16.4
for a transparency note on one accidental real call made during this
feature's own manual CLI verification.

```powershell
cd C:\GIT\llm-evaluation-workbench\scripts

# Requires a running local `ollama serve` with the model already available.
# Enforces preflight (ollama ps) + filesystem max-one-local-model lock;
# always unloads (keep_alive=0) in a finally block.
python .\run_agent_helper_campaign.py connect-gate-run `
  --campaign-id live-<date> --model qwen3-coder:30b

# Real, one-shot mini coding-task gate for the same model/campaign. Scored
# by a fixed unittest suite in a subprocess, never keywords. Hard
# acceptance gate always applies.
python .\run_agent_helper_campaign.py mini-gate-run `
  --campaign-id live-<date> --model qwen3-coder:30b
```

## Reporting contract

- **Heuristik-Score** is keyword-based benchmark guidance, not an official correctness score.
- Composite overall score: 60% quality, 20% speed, 10% reliability, 10% efficiency.
- Raw rows preserve timing, token, score, CPU, RAM, GPU, VRAM, lifecycle, preview, and error fields.
- Final runs append to `benchmark_results\migration_llm_bench_history.csv`.
- Resume must skip already successful `backend+model+case+run` samples and retry missing/error samples.
- Every benchmark update generates three linked summary artefacts from the same aggregated rows:
  `benchmark_report.html`/`.md` and `benchmark_run_summary.csv` contain the compact
  one-row-per-run overview; `benchmark_live_status.html`/`.md` contains the wider
  operational view with planning, progress, and ETA.
  The compact overview fields are:
  `Date/time`, `Backend`, `Model`, `Benchmark name`, `Samples x/n`, average GPU/CPU,
  VRAM/RAM, score, errors, tokens/s, elapsed time, and rating.
- The compact live report contains exactly the planned model rows; case/run detail belongs in the details report.
- Changing output schemas or visible metrics requires report, runbook, and changelog updates in the same change.

## Non-negotiable project rules

1. No silent fallbacks. Surface errors in logs and result records.
2. Do not use `--no-verify`.
3. Do not rename or delete legacy result data without explicit approval.
4. Preserve CLI compatibility unless the task explicitly authorizes a breaking change.
5. Preserve CSV/JSON compatibility or document and migrate the schema deliberately.
6. Keep seeds, prompts, settings, model lifecycle, and effective campaign parameters reproducible.
7. Compare backends only under equivalent semantics; document unavoidable differences.
8. Run existing smoke/tests/docs checks after related changes.
9. Update this current-state block after every significant benchmark or implementation phase.
10. Never end a stable work unit with uncommitted project changes when session-end policy requires commit/push.

## Memory routing

At every significant finding, completed phase, topic/project switch, pre-compaction,
or completion response:

| Information | Destination |
|---|---|
| Technical state and next command | this file and the relevant report/runbook |
| Detailed benchmark result | canonical report plus result files |
| Personal/professional Thomas context | `C:\GIT\user-memory\profile.md` |
| Cross-project handoff | `C:\GIT\user-memory\session-log.md` |
| Reusable agent learning | route through `C:\GIT\agent-memory\INDEX.md` |

Do not wait for Thomas to ask for persistence.

## Validation

```powershell
python .\scripts\llm_migration_benchmark.py --help
python .\standards\scripts\test_docs.py
python -m mkdocs build --strict

# Agent-helper harness (fast, deterministic, no model/network calls):
cd scripts; python -m unittest test_agent_helper_eval -v
```

Run only existing checks relevant to the changed surface.
