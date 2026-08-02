# Changelog

## 2026-08-02 — Shared local-model lease integration (kein Live-Modell)

- Die repo-private PID-Lock-Implementierung ist jetzt ein dünner Adapter auf
  `standards\scripts\local_model_lease.py`; Agent-Helper, Migration-Benchmark
  und andere Workspace-Agenten verwenden dieselbe Resource-ID `local-llm`.
- Lokale Entrypoints warten standardmäßig bis zu 3600 Sekunden, halten eine
  per Heartbeat erneuerte Lease pro Modell und geben sie explizit frei.
  Preflight läuft innerhalb der Lease; fremde Lease-IDs werden nie gelöscht.
- `--local-lease-wait-seconds` erlaubt einen begrenzten operator-spezifischen
  Timeout. Siemens-Backends bleiben leasefrei und semantisch unverändert.
- Verifiziert ausschließlich mit fokussierten Lease-/Fake-Transport-Tests
  und CLI-Help; kein Modell-, Ollama-Generate- oder Netzwerkaufruf.

## 2026-08-01 — Agent-Helper-Evaluation-Track: Serial-Pilot-Crash-Fix (kein Live-Modell)

- **Auftrag:** einen realen seriellen Pilot-Absturz reproduzieren/beheben,
  ohne selbst ein Modell aufzurufen. Evidenz: Kampagne
  `agent-helper-serial-pilot-20260801`, Modell `qwen3-coder:30b` — das
  Mini-Gate-Arbeitsverzeichnis (`mini_task_work/<voller sample_id>`) war
  226 Zeichen lang, `subprocess.run(cwd=...)` warf unter Windows
  `NotADirectoryError: [WinError 267]` ("Der Verzeichnisname ist
  ungültig"). Der Fehler geschah **nach** dem bereits abgeschlossenen
  Entladen des Modells und propagierte komplett ungefangen bis zum
  Absturz des ganzen Parent-Prozesses — **keine** Probe und **kein**
  Checkpoint wurden für diesen Versuch persistiert.
- **Fix 1 — kurzes, gehashtes Arbeitsverzeichnis:** neue
  `live_gates._short_work_dir_name(sample_id)` liefert
  `sha256(sample_id)[:16]` — ein fester 16-Hex-Zeichen-Name, unabhängig
  von der Länge von Kampagne-/Modell-/Task-IDs.
  `run_ollama_mini_gate()`'s `work_dir` ist jetzt
  `mini_task_work/<Hash>` statt `mini_task_work/<voller sample_id>`; der
  volle `sample_id` bleibt im Artefakt-JSON erhalten (`sample_id`/
  `work_dir`-Felder) — nur die Pfadlänge auf der Platte ändert sich.
- **Fix 2 — Gate-Boundary-Exception-Sicherheitsnetz:** beide echten Gates
  (`run_ollama_connect_gate`/`run_ollama_mini_gate`) fangen jetzt jede
  unerwartete Exception nach dem echten HTTP-Aufruf in einem
  `try`/`except` ab. `_build_gate_exception_sample()` baut daraus immer
  eine vollständige, schema-valide `SampleRecord`
  (`status="error"`, `system_error_code=
  "GATE_BOUNDARY_UNEXPECTED_EXCEPTION"`, `acceptance_status` über
  dieselbe `rubric.compute_sample_acceptance(status="error", ...)` wie
  jede andere Probe — erzwingt immer `"not_usable"`; alle Phasen-/Score-
  Felder bleiben ehrlich `None`) statt die Exception weiterzureichen. Die
  Gate-Funktion gibt immer ein normales `LiveGateResult` zurück und
  persistiert diese Probe über dieselbe SQLite/CSV/Report-Pipeline wie
  jeder andere Versuch.
- **Fix 3 — Pre-Gate-Checkpoints + explizite Continue-/Halt-Policy:**
  `serial_campaign.run_serial_campaign()` schreibt jetzt einen atomaren
  Checkpoint (`in_progress={"model": ..., "gate": "connect"|"mini"}`)
  **unmittelbar vor** jedem Gate-Aufruf, zusätzlich zum bestehenden
  Checkpoint danach — schließt genau die Lücke, die der reale Pilot traf
  (kein Checkpoint existierte für den abgestürzten Mini-Gate-Versuch).
  Neuer Parameter `halt_on_gate_exception` (Default `False`, CLI:
  `--halt-on-gate-exception`): standardmäßig läuft die Kampagne nach
  einem Gate-Boundary-Exception-Vorfall zum nächsten Modell weiter (ein
  Workspace-/Scoring-Problem eines Modells impliziert nicht, dass jedes
  andere Modell genauso scheitert); `True` stoppt die Kampagne stattdessen
  wie bei einer Preflight-Verweigerung. `_write_checkpoint()` erhielt
  zusätzlich einen kurzen begrenzten Retry-mit-Backoff um das atomare
  `os.replace()` — unter Windows kann dieses transient
  `PermissionError: [WinError 5]` werfen, wenn Antivirus/Indexierung kurz
  einen Handle auf der frisch geschriebenen Temp-Datei hält (in der
  eigenen Testsuite beobachtet, nachdem Pre-Gate-Checkpoints die
  Schreibfrequenz erhöht hatten).
- **Resume für den exakten realen Zustand verifiziert:** die reale
  Kampagnen-Datenbank enthält genau eine Zeile — ein akzeptiertes
  Connect-Gate für `qwen3-coder:30b`, kein Mini-Gate. Ein neuer Test
  (`test_resume_after_real_pilot_exact_state_reruns_only_missing_mini_gate`)
  reproduziert exakt diesen Zustand und bestätigt: Connect-Gate wird als
  `"resumed"` erkannt (nicht erneut aufgerufen), nur das fehlende Mini-Gate
  wird versucht, danach laufen die drei noch nie versuchten Modelle
  (`deepseek-coder-v2:16b`, `phi4-mini:3.8b-q4_K_M`, `rnj-1:8b`) weiter.
- **Reale Kampagnen-Evidenz unverändert:**
  `benchmark_results\agent-helper\agent-helper-serial-pilot-20260801\`
  (gitignored) — inklusive der akzeptierten Connect-Gate-Probe und des
  verwaisten `mini_task_work`-Verzeichnisses aus dem Absturz — wurde beim
  Bauen/Testen dieses Fixes nicht verändert, repariert oder gelöscht.
- **10 neue, schnelle, deterministische Regressionstests** (273/273
  bestehen, vorher 263): Hash-Kürzung des Arbeitsverzeichnisses (isoliert
  + end-to-end mit langen Identifiern); Gate-Boundary-Exception-
  Sicherheitsnetz für beide Gates (gemockte `NotADirectoryError` bzw.
  `RuntimeError` nach dem echten HTTP-Aufruf); Pre-Gate-Checkpoints vor
  Connect- und vor Mini-Gate; Continue-by-Default- und expliziter-Halt-
  Pfad bei einer Gate-Boundary-Exception; und der reale Resume-Zustand
  oben. Über 10 aufeinanderfolgende volle Testläufe verifiziert, um
  Flakiness auszuschließen (dabei die transiente Windows-Checkpoint-
  Rename-`PermissionError` gefunden und mit Fix 3 behoben). Kein
  Live-Modell- oder Netzwerkaufruf in diesem Zyklus. Der zusätzliche
  Checkpoint-Test verifiziert den transienten `PermissionError`, den
  Backoff und den danach erfolgreichen atomaren Replace.
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  §18, `docs\operations\runbook.md` §8.

## 2026-08-03 — Agent-Helper-Evaluation-Track: Ollama-Inventar-Discovery + serieller Gate-Runner (kein Live-Modell)

- **Auftrag:** robuste lokale Ollama-Inventar-Discovery plus ein strikt
  serieller Gate-Kampagnen-Runner für die Ausführung durch den
  Parent-Agent — kein Modell-/Ollama-Aufruf durch diese Session selbst.
- **Neu: `scripts\agent_helper_eval\ollama_inventory.py`.** Discovery
  ausschließlich über `GET /api/tags` + `POST /api/show` (nie
  `/api/generate` — lädt/generiert nie). `discover_ollama_inventory()`
  liefert ein `DiscoveryResult` (Schema-Version
  `agent-helper-ollama-inventory-v1`) mit einem `DiscoveredOllamaModel` je
  installiertem Tag: Architektur-Klassifikation (MoE/dense) nur bei echter
  Evidenz (`expert_count`-Feld zuerst, dann Familie-Namens-Hinweise,
  MoE-Hinweise vor Dense-Hinweisen geprüft — sonst `"unknown"`);
  Cloud-Tag-Erkennung über Ollamas eigene Tag-Namenskonvention (z. B.
  `qwen3-coder:480b-cloud`); Parameterzahl bevorzugt
  `model_info["general.parameter_count"]` vor der gröberen
  `details.parameter_size`-Zeichenkette; ein einzelner fehlgeschlagener
  `/api/show`-Aufruf wirft das Tag nie aus dem Snapshot (nur
  `show_error` markiert). `save_inventory_snapshot()`/
  `load_inventory_snapshot()` schreiben/lesen einen generierten JSON-
  Snapshot **in das jeweilige Kampagnen-Ausgabeverzeichnis** — die
  handkuratierte `benchmarks\agent-helper-model-inventory.example.json`
  wird nie überschrieben. `to_model_spec()`/`estimate_vram_fit()`
  konvertieren einen entdeckten Tag in die bestehende `ModelSpec`-Form
  für die Bericht-Wiederverwendung; die VRAM-Passungs-Projektion ist
  **immer** `confidence="estimated"`, nie `"measured"` (reine
  Dateigrößen-Schätzung, kein Ladetest).
- **Neu: `scripts\agent_helper_eval\serial_campaign.py`.**
  `build_serial_plan()` (rein, immer trocken): explizite `--models`-Liste
  (Reihenfolge erhalten, jedes andere entdeckte Tag transparent als
  "deferred" markiert) oder Filter-Modus (Cloud-Ausschluss standardmäßig,
  Max-Größe, Architektur-/Quantisierungs-Allow-/Deny-Listen,
  `force_include` mit sichtbar erhaltenem Ausschlussgrund) — jedes
  entdeckte Modell erscheint immer in `included` oder `deferred`, nie
  stillschweigend übergangen. `run_serial_campaign()`: strikt
  sequenzielle Ein-Thread-`for`-Schleife über `plan.included`, ruft
  Connect- dann (nur bei Akzeptanz) Mini-Gate über injizierbare
  `connect_gate_fn`/`mini_gate_fn` (Default: die echten
  `live_gates`-Funktionen) — Überlappung zweier Modell-Versuche ist durch
  den Aufbau strukturell unmöglich. `confirm=False` (Default) ruft nie
  ein Gate auf. Resume (Default an) überspringt jedes (Modell, Task)-Paar
  mit bereits persistierter Probe — Erfolg, Fehlschlag *oder* Timeout —,
  außer `force_rerun` nennt es explizit. Retries (`max_retries`, Default
  `0`) gelten ausschließlich für einen transienten `status=error`, nie für
  Timeout oder eine inhaltlich abgelehnte Probe. Eine
  Preflight-/Lock-Verweigerung (`LiveGateRefusedError`) stoppt
  standardmäßig die gesamte Serie (`halt_on_preflight_refusal=True`,
  `--continue-on-refusal` zum bewussten Fortsetzen). Atomarer
  (`.tmp`+`os.replace()`) JSON-Checkpoint (`serial_progress.json`) nach
  jedem Schritt; ein `KeyboardInterrupt` schreibt einen finalen
  `interrupted=True`-Checkpoint und kehrt sauber zurück statt
  abzustürzen. Ein injizierbares `sleep` realisiert `cooldown_seconds`
  zwischen den Modellen (nie nach dem letzten). Weder eine Verweigerung
  noch ein Skip erzeugt je eine fingierte `SampleRecord` — beides landet
  ausschließlich im Seitenkanal-Checkpoint.
- **Neue CLI-Subcommands** in `scripts\run_agent_helper_campaign.py`:
  `ollama-inventory-snapshot` (real, read-only), `serial-plan` (immer
  trocken), `serial-execute` (`--confirm` **und** eine explizite
  `--models`-Liste oder mindestens ein Filter-Flag zwingend erforderlich,
  um tatsächlich auszuführen — alle entdeckten Modelle aus Versehen laufen
  zu lassen ist nicht möglich).
- **40 neue, schnelle, deterministische Regressionstests** (263/263
  bestehen, vorher 223): `OllamaInventoryTests`, `SerialPlanTests`,
  `SerialCampaignExecutionTests`, `SerialCampaignLiveGatesWiringTests`
  (ein echter End-to-End-Test durch die reale `live_gates`-Verdrahtung mit
  Fake-Transports), `SerialCampaignCliWiringTests` (patcht
  `campaign_cli.ROOT` konsequent auf ein Temp-Verzeichnis, damit das
  reale Repository nie berührt wird). Kein Commit/Push; keine
  Abhängigkeiten installiert; kein Live-Modell-, Live-Ollama-Discovery-
  oder Netzwerkaufruf in diesem Zyklus; keine Command-Center-Repositories
  berührt.
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  §17, `docs\operations\runbook.md` §8.

## 2026-08-02 — Agent-Helper-Evaluation-Track: Pilot-Review-Remediation (3 Fixes, kein Live-Modell)

- **Auftrag:** ein Pilot-Reviewer führte den echten Ollama-Connect-/
  Mini-Gate gegen `qwen3-coder:30b` aus (Kampagne
  `agent-helper-pilot-20260801`) und meldete drei materielle Mängel plus
  eine Doku-Inkonsistenz. Diese Session ruft dabei erneut **kein** Modell/
  keine API auf — alle Korrekturen sind Schema-/Logik-/Datenreparaturen,
  verifiziert über die bestehende Test-Suite und einen sicheren,
  modellfreien "Recompute"-Durchlauf gegen die bereits persistierte
  Pilot-Kampagne.
- **Fix 1 — Evidenz-Stufen/Tiering-Überschätzung:** `qwen3-coder` wurde
  nach nur Connect-/Mini-Smoke-Evidenz (eine Task-Kategorie, keine
  Reviewer-/Kollaborations-Abdeckung) fälschlich als `tier-1-recommended`
  gelabelt. `schema.py`: neues `EVIDENCE_STAGES = ("gate_only",
  "partial_suite", "full_suite")`, neue Tier-Stufe
  `"gate-passed-provisional"`, neues `AggregateRecord.evidence_stage`-Feld
  + drei neue Mittelwert-Rollup-Felder
  (`model_load_seconds_mean`/`orchestrator_cpu_time_seconds_mean`/
  `model_cpu_time_seconds_mean`), erweiterte `validate_aggregate()`-
  Invarianten (verbietet `evidence_stage != full_suite` zusammen mit
  `tier-1-recommended`). `rubric.py`: neue `evidence_stage()`-Funktion +
  `MIN_TASK_CATEGORIES_FOR_TIER1 = 4`; `suitability_tier()` erzwingt die
  neue Deckelung. `aggregate.py`: `_evidence_stage_inputs()`-Helper,
  Verdrahtung, neue Empfehlungstexte für `gate_only`/`partial_suite`.
  `report.py`/`report_style.py`: neue Leaderboard-Spalte "Evidence stage",
  neue Executive-Summary-Caveats (DE/EN) wenn das beste Modell nur
  Gate-/Teil-Evidenz hat.
- **Fix 2 — Phasenzuordnung zählte kalte Modell-Ladezeit als Queue/Idle:**
  `live_gates.py` schrieb `load_duration` (Ollamas Kalt-Ladezeit)
  fälschlich in `llm_queue_seconds`, während `llm_request_seconds`
  unbesetzt blieb — dadurch wurden ~50 % der Mini-Gate-Wandzeit
  fälschlich als "Queue/Idle" statt als LLM/API-Critical-Path ausgewiesen.
  Beide echten Gates messen jetzt `generate_wall_seconds` (Client-
  Wandzeit exakt um den `ollama_generate()`-Call, ohne den
  Unload-Call) und setzen: `llm_request_seconds = generate_wall_seconds`
  (der gesamte API-Call zählt als LLM/API-Critical-Path);
  `llm_queue_seconds = None` (ein Fail-fast-Preflight-Lock wartet nie
  real — echte Queue-/Service-Zeit-Trennung bleibt der zukünftigen
  1/2/4-Concurrency-Profiling-Phase vorbehalten); neues additives Feld
  `model_load_seconds = ollama_client.ns_to_seconds(load_duration_ns)`
  als reines Diagnose-Subfeld, bereits in `llm_request_seconds`
  enthalten, nie doppelt gezählt (`phase_timing.llm_bucket_seconds()`
  bevorzugte `llm_request_seconds` bereits korrekt — der Bug lag
  ausschließlich in der Feldbefüllung in `live_gates.py`).
  `resource_monitor.ResourceMonitor` wird in beiden Gates jetzt mit
  `process_name_filters=["ollama"]` konstruiert.
- **Fix 3 — `cpu_time_seconds` immer N/A:** neue, explizit benannte,
  nicht-vermengte Felder `SampleRecord.orchestrator_cpu_time_seconds`
  (via `time.process_time()`-Klammerung in beiden echten Gates — echte
  Prozess-CPU-*Zeit*, nie mit `cpu_avg_percent`/`cpu_max_percent`
  [System-CPU-*Auslastung*-Prozentsatz] verwechselt) und
  `SampleRecord.model_cpu_time_seconds` (neues Per-PID-CPU-Zeit-Delta-
  Tracking in `resource_monitor.ResourceMonitor` via
  `psutil.Process.cpu_times()`, ehrlich `None` wenn kein passender
  Prozess je gefunden wurde — nie fabriziert als `0.0`).
- **Doku-Konsistenz-Fix:** "9-Test-Suite" → korrekt "10-Test-Suite"
  (`AGENTS.md`, `docs\operations\runbook.md`,
  `docs\project\agent_helper_benchmark.md` §16.2) — der reale
  `mini_task.py`-Fixture-Code hat exakt 10 `unittest`-Testmethoden.
- **Neu: `scripts\agent_helper_eval\repair.py`** — idempotente,
  modellfreie Wartungs-Pipeline für bereits persistierte Kampagnen-
  Verzeichnisse: `repair_legacy_llm_phase_timing()` (erkennt den engen
  Fingerprint des Fix-2-Bugs, leitet die echten
  `total_duration`/`load_duration`-Werte aus dem bereits geschriebenen
  Artefakt-JSON einer Probe neu ab, re-validiert vor dem Übernehmen, ist
  vollständig idempotent) und `recompute_campaign()` (öffnet die
  Kampagnen-SQLite-DB — migriert dabei additiv ein älteres Schema, siehe
  unten —, repariert alle passenden Proben, baut alle Aggregate mit der
  *aktuellen* Rubrik-/Evidenz-Stufen-/Phasenzuordnungs-Logik neu, exportiert
  beide kanonischen CSVs neu, baut den lokalen + den kombinierten
  HTML-Report neu). Neuer CLI-Subcommand `recompute-campaign
  --campaign-id <id>` in `scripts\run_agent_helper_campaign.py`. Ruft nie
  ein Modell/eine API auf; sicher wiederholt ausführbar (vollständig
  idempotent).
- **`storage.py`:** neue `_add_missing_columns()`-Migration (additive
  `PRAGMA table_info` + `ALTER TABLE ... ADD COLUMN`, aufgerufen für
  `samples`/`aggregates`/`capacity_profile` bei jedem `storage.connect()`)
  — notwendig, damit eine vor dieser Änderung geschriebene Kampagnen-
  SQLite-DB (z. B. die reale Pilot-DB) die neuen Spalten ohne
  Datenverlust aufnehmen kann; bestehende Zeilen erhalten `NULL`
  ("N/A") für die neue Spalte.
- **Angewendet auf die reale Pilot-Kampagne:** `python
  .\scripts\run_agent_helper_campaign.py recompute-campaign --campaign-id
  agent-helper-pilot-20260801` einmal ausgeführt (kein Modellaufruf) und
  per CSV-/Report-Inspektion verifiziert: Tiers jetzt korrekt
  `gate-passed-provisional`/`evidence_stage=gate_only` mit den neuen
  Caveat-Texten; `llm_request_seconds` populiert (21,091 s / 18,370 s);
  `model_load_seconds` enthält korrekt die vormals fälschlich als
  Queue-Zeit gelabelten Werte (20,668 s / 9,379 s); `llm_queue_seconds`
  jetzt `N/A`; `llm_critical_path_percent` korrigiert auf ~100 %/~99 %
  (vorher fälschlich ~98 %/50 % "Queue/Idle"); `cpu_time_seconds`-Familie
  bleibt für diese historischen Zeilen ehrlich `N/A` (nicht rückwirkend
  rekonstruierbar); deterministischer Score, Artefaktpfade/-Hashes und das
  "tests_passed=10/10"-Ergebnis bleiben unverändert erhalten.
- **15 neue, schnelle, deterministische Regressionstests:**
  `StorageMigrationTests` (additive Spaltenmigration einer manuell
  gebauten "alten" Tabelle, No-op auf einer bereits aktuellen DB),
  `RepairAndRecomputeTests` (Fingerprint-/Reklassifizierungs-Mathematik,
  Idempotenz, enger Fingerprint berührt nie eine bereits korrekte oder
  Nicht-Ollama-Probe, End-to-End-`recompute_campaign()` gegen ein
  temporäres Kampagnen-Verzeichnis), neue `ResourceMonitorTests` für
  `_model_process_cpu_time_delta_seconds()` (kein Filter ⇒ `None`, Filter
  ohne Treffer ⇒ `None`, positives Delta über zwei Snapshots, ein nur im
  letzten Snapshot gesehener PID zählt seine volle kumulative Zeit), sowie
  neue `LiveGatesTests` (`test_connect_gate_phase_timing_attributes_
  whole_call_to_llm_not_queue`, `test_connect_gate_captures_orchestrator_
  and_model_cpu_time`, `test_mini_gate_phase_timing_and_cpu_time_are_
  populated`) als vollständig gemockte End-to-End-Beweise für beide echten
  Gate-Funktionen. **223/223 Tests bestehen** (vorher 208). Kein Commit/
  Push; keine Abhängigkeiten installiert; kein Live-Modellaufruf in
  diesem Zyklus; keine Command-Center-Repositories berührt.
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  §16.5, `docs\operations\runbook.md` §8.

## 2026-08-02 — Agent-Helper-Evaluation-Track: Realer Ollama-Connect-/Mini-Gate-Executor

- **Auftrag:** ein *echter* One-Model-Ollama-Connect-Gate- und
  Mini-Coding-Task-Executor, vollständig in den agent-helper-Harness
  integriert. Diese Session selbst ruft dabei **kein** Modell auf — der
  neue Code stellt nur das Werkzeug bereit; das tatsächliche Ausführen
  eines echten Gates gegen ein echtes Modell bleibt dem Parent-Agent/Human
  vorbehalten (siehe exakter Befehl am Ende dieses Eintrags).
- **Neu: `scripts\agent_helper_eval\resource_monitor.py`** — leichtgewichtiger
  CPU/RAM/GPU/VRAM-Sampler für echte Attempts: reine `summarize_samples()`-
  Funktion (avg/max-Reduktion, leere Serie ⇒ `None`, nie fabrizierte `0.0`)
  plus `ResourceMonitor` (Hintergrund-Thread, psutil CPU/RAM +
  injizierbare `nvidia-smi`-Abfrage für GPU/VRAM). Bewusst eine neue,
  eigenständige Implementierung statt eines Imports aus
  `llm_migration_benchmark.SystemMonitor`, um dieses Package
  dependency-isoliert zu halten.
- **Neu: `scripts\agent_helper_eval\ollama_client.py`** — echter Ollama-
  HTTP-Client (nur `urllib`, keine neue `requests`-Abhängigkeit):
  `ollama_ps()`/`check_single_model_preflight()` (verweigert strikt jedes
  bereits geladene Fremdmodell; verweigert sogar dasselbe Modell ohne
  explizites `allow_reuse=True`), `ollama_generate()` (gestreamt,
  deterministische Optionen, echte Time-to-First-Token aus dem ersten
  nicht-leeren Chunk, bounded Context/Output, konfigurierbares Timeout),
  `ollama_unload()` (`keep_alive: 0`, best-effort, wirft nie). Produktions-
  Transports sind austauschbar (`JsonTransport`/`StreamTransport`); Tests
  injizieren immer Fakes.
- **Neu: `scripts\agent_helper_eval\mini_task.py`** — die deterministische
  Mini-Coding-Aufgabe (keine Keyword-Bewertung): fester Prompt mit
  maschinenlesbarem Contract (genau ein `normalize(records)`-Funktions-Fence),
  AST-basierter `static_safety_scan()` (Allow-List-Imports, verbotene
  Aufrufe wie `eval`/`exec`/`open`, verbotene Dunder-Attributzugriffe —
  *bevor* generierter Code je ausgeführt wird), `detect_placeholder_markers()`,
  und `run_mini_task_tests()` (führt eine feste 10-Test-`unittest`-Suite in
  einem Subprozess mit striktem Timeout aus; `deterministic_score` ist der
  Prozentsatz bestandener fester Tests — nie ein Keyword-Treffer).
  Sicherheitsmodell explizit als "Defense in Depth, kein vollständiges
  Sandboxing" dokumentiert; primärer Schutz ist der Subprozess + striktes
  Timeout, der AST-Scan verhindert nur die offensichtlichsten Escape-/IO-
  Vektoren vorab.
- **Neu: `scripts\agent_helper_eval\live_gates.py`** — die Orchestrierung:
  `run_ollama_connect_gate()`/`run_ollama_mini_gate()` (Preflight → lokaler
  Filesystem-Lock (`local_lock.py`, wiederverwendet) → echter, gestreamter
  Generate-Call → Scoring → **immer** Unload in `finally` → Persistenz durch
  SQLite-SSOT + beide CSVs → Report-Neuaufbau). Eine Preflight-/Lock-
  Verweigerung (`LiveGateRefusedError`) persistiert **keine** Zeile (nichts
  wurde versucht); ein echter, fehlgeschlagener/timeout-behafteter Versuch
  wird dagegen persistiert (`status=error`/`timeout`,
  `acceptance_status=not_usable` durch den bestehenden Hard-Gate). Beide
  Gates sind explizit **One-Shot**: eine Korrektur-/Folge-Iteration ist ein
  separater, später Befehl. Bugfix während Implementierung: `total_wall_seconds`
  musste die *gesamte* Attempt-Spanne (Generate + Unload + Scoring/Test-Exec)
  abdecken, inklusive eines Floor-Werts aus Ollamas eigenem
  `total_duration`, damit die Phase-Timing-Bucket-Summen-Invariante in
  `schema.validate_sample()` nie verletzt wird (siehe
  `_reconcile_total_wall_seconds()`).
- **CLI (`scripts\run_agent_helper_campaign.py`):** zwei neue Subcommands,
  `connect-gate-run` und `mini-gate-run`, beide mit Pflicht-`--model` und
  Pflicht-`--campaign-id` (niemals ein Inventar iterierend). Gate-
  spezifische Flags (`--timeout-seconds`, `--num-predict`, `--num-ctx`,
  zusätzlich `--test-timeout-seconds` bei `mini-gate-run`) sowie gemeinsame
  Flags (`--base-url`, `--allow-reuse-loaded-model`, `--keep-alive`,
  `--provider`, `--runtime`, `--quantization`, `--hardware-*`). Eine
  `LiveGateRefusedError` wird als klare "REFUSED (nothing attempted,
  nothing persisted)"-Meldung mit Exit-Code 2 ausgegeben statt eines
  rohen Tracebacks.
- **Wichtige Selbstkorrektur/Transparenz:** Beim manuellen Verifizieren der
  CLI-Verdrahtung wurde versehentlich per Modul-Monkeypatch versucht,
  `ollama_client.urllib_json_transport`/`urllib_stream_transport`
  auszutauschen — das griff **nicht**, weil `live_gates.py`s Funktionen
  diese als Default-Parameterwerte bereits *beim Import* gebunden hatten
  (`json_transport: ... = ollama_client.urllib_json_transport`), nicht als
  Attribut-Lookup zur Aufrufzeit. Dadurch hat dieser eine CLI-Testlauf
  tatsächlich den lokal laufenden echten Ollama-Server erreicht und einen
  echten, aber trivialen (2 Tokens, Antwort "OK") Generate-Call gegen
  `qwen3-coder:30b` ausgelöst — ein Verstoß gegen die explizite Vorgabe,
  in dieser Session kein Modell aufzurufen. Sofort erkannt (Prozess-/Port-
  Check auf `127.0.0.1:11434`, Artefakt-Inhalt bestätigt echten
  Ollama-Response), das versehentliche Kampagnenverzeichnis
  (`benchmark_results\agent-helper\cli-smoke-test\`) gelöscht und der
  kombinierte Report per `report`-Subcommand (liest nur vorhandene
  SQLite-DBs, ruft kein Modell auf) neu aufgebaut. Positive Nebenerkenntnis:
  Unload-in-`finally` funktionierte auch im echten Fall korrekt (`ollama ps`
  danach leer). Für alle weiteren Verifikationen wurden ausschließlich
  explizit als Funktionsargumente übergebene Fakes bzw.
  `unittest.mock.patch.object()` auf Modul-Attribute (korrekt, da
  Attribut-Lookup zur Aufrufzeit) verwendet — nie wieder
  Modul-Monkeypatching auf Funktionen mit bereits gebundenen
  Default-Parametern.
- **Tests:** 46 neue, schnelle, deterministische Tests
  (`ResourceMonitorTests`, `OllamaClientTests`, `MiniTaskTests`,
  `LiveGatesTests`, `LiveGateCliWiringTests` in
  `scripts\test_agent_helper_eval.py`) — ausschließlich mit injizierten
  Fake-Transports/-Monitoren bzw. gemockten `live_gates`-Funktionen; kein
  Test öffnet einen echten Socket oder ruft ein Modell auf (die
  Mini-Task-Tests starten lediglich den lokalen Python-Interpreter selbst
  als Subprozess für die feste Testsuite — kein Modell). Deckt ab:
  Preflight-Konflikt via `ollama ps` *und* via Filesystem-Lock (beide
  persistieren nichts), erfolgreicher Connect-/Mini-Gate, Generate-Fehler
  (persistiert als `error`/`not_usable`), unsicherer generierter Code
  (nie ausgeführt, `not_usable`), eine syntaktisch lauffähige aber
  *falsche* Lösung (Hard-Gate greift trotzdem: `not_usable`), Timeout-
  Erkennung, sowie CLI-Argument-Forwarding inkl. Refusal→Exit-Code-2.
  **207/207 Tests bestehen** (vorher 161). Keine CSV-/Report-Schema-
  Änderung; keine bestehende Kampagne/Historie berührt.
- Keine Command-Center-Repositories berührt; kein Commit/Push; keine
  Abhängigkeiten installiert.
- **Exakter Parent-Befehl für den qwen3-coder:30b-Connect-Gate**
  (benötigt einen laufenden lokalen `ollama serve` mit geladenem/verfügbarem
  Modell):

  ```powershell
  python .\scripts\run_agent_helper_campaign.py connect-gate-run `
      --campaign-id live-2026-08-01 --model qwen3-coder:30b
  ```

  **Exakter Parent-Befehl für den anschließenden Mini-Gate** (gleiche
  Kampagne, gleiches Modell):

  ```powershell
  python .\scripts\run_agent_helper_campaign.py mini-gate-run `
      --campaign-id live-2026-08-01 --model qwen3-coder:30b
  ```

## 2026-08-02 — Agent-Helper-Evaluation-Track: Multi-Agent-Koordination & externer Katalog-Import

- **Auftrag (Koordinations-Update):** Ein weiterer, gleichzeitig laufender
  Agent/Session arbeitet in diesem Workspace am separaten "Command Center"-
  Werkzeug, Cloud-/Tool-Evaluationen und eigenen Berichten und darf
  zusätzliche offline Benchmark-Set-Dateien beisteuern; er führt **niemals**
  lokale Ollama/llama.cpp-Kampagnen aus — das bleibt ausschließlich Aufgabe
  dieser Session. Gefordert: (1) externe, dokumentiert-versionierte
  Benchmark-Kataloge müssen ohne Code-Änderung ladbar/validierbar sein,
  (2) eine knappe Ownership-/Koordinationsnotiz, (3) ein konfliktsicherer
  Beitrags-Workflow (neue Benchmark-Sets nur in separaten Dateien, keine
  Shared-File-Edits ohne Übergabenotiz, Ergebnisisolation pro Kampagne,
  Provenienzfelder verpflichtend), (4) keine direkte Kontaktaufnahme mit dem
  fremden Agenten — Koordination nur über Repo-Doku/Handover.
- **Reale Evidenz der Koexistenz bestätigt (git status vor Änderung
  geprüft):** `benchmarks\swe-mixed-hard-24.json`,
  `benchmarks\swe-python-hard-24.json`, `benchmarks\swe-sql-hard-24.json`
  und `benchmarks\web-grid-demo-v1.json` sind Benchmark-Set-Dateien im
  *anderen*, bereits existierenden `llm_migration_benchmark.py`-Format
  (`benchmark_id`/`spec_version`/`required_keywords`), nicht im
  `agent-helper-catalog-v1`-Schema. Zusätzlich existiert bereits
  `benchmark_results\agent-helper\wtcc-20260801\` — ein rohes,
  Append-only-Siemens-Modellvergleichsverzeichnis (eigenes
  `README.md`/`evaluation.md`/`prompts\`/`raw\`-Layout, keine SQLite-DB) —
  **innerhalb** des sonst exklusiven `benchmark_results\agent-helper\`-
  Namensraums dieser Subsystem-Schicht. Keines von beidem wurde angerührt,
  umbenannt oder umformatiert; `orchestrator.build_combined_report()`
  überspringt bereits jedes Kampagnenverzeichnis ohne
  `agent_helper.sqlite3` und toleriert damit dieses fremde Verzeichnis ohne
  Code-Änderung (bestätigt durch Testlauf).
- **`catalog.py` — externer Katalog-Import-Vertrag (`load_catalog_document()`):**
  neue `SUPPORTED_CATALOG_SCHEMA_VERSIONS`-Prüfung (bisher wurde
  `catalog_schema_version` im JSON überhaupt nicht geprüft — ein
  Fremdformat-File wie `swe-mixed-hard-24.json` hätte zuvor eine rohe,
  unkontrollierte `TypeError` ausgelöst statt eines klaren Fehlers); neue
  Pflicht-Klasse `CatalogMetadata` (`catalog_id`/`author`/`created_at`
  [ISO-8601 mit Zeitzone, per `schema.is_timestamp_with_tz()` geprüft]/
  optional `source_notes`) als Katalog-*Datei*-Provenienz, getrennt von
  `SampleRecord.provenance` (Ergebnis-Provenienz pro Sample); neue
  `CatalogDocument`-Rückgabe (`catalog_schema_version`/`metadata`/`tasks`).
  `load_catalog()` bleibt als abwärtskompatibler, listenrückgebender
  Wrapper bestehen (kein bestehender Aufrufer geändert). `save_catalog()`
  verlangt jetzt zwingend ein `metadata`-Argument (einziger Aufrufer
  `build_example_configs.py` angepasst; `benchmarks\agent-helper-catalog-v1.json`
  neu generiert, jetzt mit `catalog_metadata`-Block).
- **Orchestrator-Verdrahtung:** `generate_synthetic_samples()`/`run_dry_run()`
  akzeptieren jetzt `benchmark_set`/`benchmark_version` (Default weiterhin
  das gebündelte `agent-helper-catalog-v1`) statt der zuvor hart codierten
  Literale — ein per `--catalog` geladener externer Katalog beschriftet
  erzeugte Samples jetzt korrekt mit seiner eigenen Identität statt
  fälschlich mit der des Standardkatalogs.
- **CLI:** `run_agent_helper_campaign.py dry-run` hat ein neues optionales
  `--catalog PATH`-Flag; beim Laden werden `catalog_id`/`author`/
  `created_at`/Task-Anzahl zur Transparenz auf stdout ausgegeben, bevor der
  Dry-Run läuft.
- **Neue Beispieldatei:** `benchmarks\agent-helper-catalog.example-external.json`
  — minimaler, eigenständig ladbarer Beleg für einen extern verfassten
  Katalog nach dem dokumentierten Schema (ein Task, klar als Platzhalter
  markiert).
- **Neuer Abschnitt §15 "Multi-agent coordination & ownership"** sowie
  §6.1 "External catalog import" in `docs\project\agent_helper_benchmark.md`:
  Ownership-Split, konfliktsicherer Beitrags-Workflow, Verweis auf die
  reale Koexistenz-Evidenz oben.
- **Tests:** 16 neue, schnelle deterministische Tests
  (`CatalogTests` in `scripts\test_agent_helper_eval.py`): Round-Trip über
  Save/Load (Dokument- und Listen-Signatur), fehlende/unbekannte
  `catalog_schema_version`, das reale Fremdformat (`swe-mixed-hard-24.json`-
  Form) sauber als `CatalogValidationError` statt roher `TypeError`
  abgelehnt, fehlende `catalog_metadata`/leerer `author`/fehlendes oder
  zeitzonenloses `created_at` je mit benanntem Feld im Fehlertext, doppelte
  `task_id` weiterhin abgelehnt, ungültiges JSON und Nicht-Objekt-JSON
  sauber abgelehnt, das reale externe Beispiel lädt erfolgreich, und
  `generate_synthetic_samples()` beschriftet Samples korrekt mit der
  externen Katalog-Identität statt der Standardidentität.
  **161/161 Tests bestehen** (vorher 145; siehe §13 der Methodik-Doku für
  die volle Aufschlüsselung). Keine CSV-/Report-Schema-Änderung; keine
  bestehende Kampagne/Historie berührt; keine Modell-/API-/Ollama-/
  llama.cpp-Aufrufe.
- Keine Command-Center-Repositories berührt; keine direkte Kontaktaufnahme
  mit dem fremden Agenten versucht — Koordination ausschließlich über diese
  Doku/den Changelog/`AGENTS.md`.

## 2026-08-02 — Agent-Helper-Evaluation-Track: Erweiterte visuelle Berichterstattung (16 neue Diagramme)

- **Auftrag:** "Strengthen visual reporting requirements" — ein
  hochwertiger, farbenfroher, aber evidenzbasierter Offline-HTML-Bericht
  mit vielen aussagekräftigen Visualisierungen statt dekorativem
  Diagramm-Wildwuchs; explizit gefordert: Executive-KPI-Karten,
  Akzeptanz-Gate-Trichter, Qualität-vs-Geschwindigkeit mit Pareto-Front,
  Zeit-bis-akzeptiert-Balken, Task-Modell-Heatmap, P50/P95/P99-Latenz,
  kritischer Pfad + Auslastungsquoten, CPU/GPU-, RAM/VRAM-Auslastung,
  Nebenläufigkeitsskalierung (1/2/4), Zuverlässigkeits-/Fehler-Diagramm,
  Iterations-/Rework-Diagramm, gemessen-vs-projizierte RTX-5090-Passung,
  Routing-/Kosten-Szenario und historischer Verlauf — alle offline (kein
  CDN/externe JS-Bibliothek), barrierearm (Farbe + Muster, nie Farbe
  allein), zweisprachig, druckbar, mit escaptem Modellinhalt.
- **Modul-Aufteilung zur Vermeidung eines Zirkelimports:** neues
  `report_style.py` (gemeinsame `esc()`/`fmt()`/`bi()`/`TIER_COLORS`/
  Tier-CSS-Klassen-Helfer, aus `report.py` herausgezogen) und neues
  `charts.py` (alle 16 Diagramm-Renderer). `report.py` importiert
  `charts` als Modul (`from . import charts`); `charts.py` importiert
  `report.py` nur unter `TYPE_CHECKING` für einen Typ-Hinweis — kein
  echter Zirkelimport zur Laufzeit.
- **Echter Bug gefunden und behoben (beim Verschieben):**
  `scaling_css_class()` (vormals `_scaling_class()` in `report.py`)
  bildete `scaling_classification`-Werte (z. B. `"scales-well"`) auf
  nicht existente CSS-Klassennamen ab (z. B. `"tier-1-recommended"`
  statt der tatsächlich in `_CSS` definierten `"tier-1"`) — das
  Skalierungs-Badge in der Kapazitätsprofil-Tabelle war dadurch
  stillschweigend ungestylt. Behoben, da direkt an dem beim Verschieben
  betroffenen Code gekoppelt; kein Test prüfte den fehlerhaften
  CSS-Klassennamen, nur den rohen Klassifikationstext, daher unschädlich.
- **Harte Trennung Ranking- vs. Diagnose-Diagramme (Akzeptanz-Gate-Regel):**
  Ranking-/Vergleichsdiagramme (Qualität-vs-Geschwindigkeit,
  Zeit-bis-akzeptiert, Latenz-Perzentile, Routing-/Kosten-Szenario) zeigen
  ausgeschlossene ("nicht nutzbare") Modell-Läufe **nie** als geplotteten
  Punkt — nur in einer eigenen, deutlich formulierten
  "Nicht dargestellt — AUSGESCHLOSSEN"-Notiz. Diagnose-/Ressourcen-
  Diagramme (kritischer Pfad, Auslastungsquoten, CPU/GPU, RAM/VRAM,
  Zuverlässigkeit, Iteration/Rework) zeigen dagegen — wie die bereits
  bestehenden Ressourcen-/Phasen-Tabellen — jeden Modell-Lauf, markieren
  ausgeschlossene Zeilen aber sichtbar mit "⚠" plus erklärendem Hinweis,
  dass diese Daten kein Eignungsnachweis sind.
- **Barrierearme Musterkodierung:** "nicht nutzbar" und
  "projiziert/geschätzt" werden je über ein eigenes diagonales
  SVG-Schraffurmuster (steile durchgezogene Streifen auf Dunkelrot bzw.
  flache gestrichelte Streifen auf Amber) **zusätzlich zur Farbe**
  kodiert — überlebt Graustufendruck und übliche Farbfehlsichtigkeit.
- **Pareto-Front im bestehenden Qualität-vs-Geschwindigkeit-Diagramm
  ergänzt:** gold umrandete Punkte + gestrichelte Treppenlinie verbinden
  die nicht-dominierte Teilmenge nutzbarer Modell-Läufe (kein anderes
  nutzbares Modell ist sowohl schneller ALS AUCH mindestens gleich gut).
- **RTX-5090-Passungs-Diagramm:** Marker kodiert gemessen (ausgefüllter
  Kreis) vs. projiziert/geschätzt (gestrichelte Raute) über Form UND
  Farbe; die hypothetische RTX 5090 32 GB kann laut Schema nie als
  "gemessen" auftreten und wird defensiv selbst dann als Raute gerendert,
  wenn ein Datensatz fälschlich `confidence="measured"` trüge.
- **Routing-/Kosten-Szenario-Diagramm:** ausschließlich illustrativ
  (Eignungsstufen-Verteilung je Anbieter als grobe Kosten-Proxy-Kategorie)
  — **keine erfundenen Euro-/Dollar-Beträge**, nur gemessene
  Eignungsstufen-Zähler nutzbarer Modell-Läufe.
- **P99-Latenz** wird für das Latenz-Perzentil-Diagramm on-the-fly aus den
  Roh-`elapsed_seconds`-Werten der Kampagne via `aggregate.percentile()`
  berechnet — keine neue CSV-/Schema-Spalte, da nur für diese Anzeige
  benötigt (Anforderung: Spalten dürfen sich nie stillschweigend ändern).
- **Neue Executive-KPI-Kartenreihe** (`render_kpi_cards`, reines HTML,
  nicht SVG): Modell-Läufe gesamt, nutzbar/ausgeschlossen, akzeptierte
  Task-Versuche, Akzeptanzquote, Ø Erfolgsquote/Tokens-pro-Sekunde/
  Zeit-bis-akzeptiert — alle Mittelwerte ausschließlich über nutzbare
  Modell-Läufe, sodass ein schnelles, aber ausgeschlossenes Modell den
  angezeigten Durchschnitt nie verzerrt.
- Alle 16 neuen Diagramme in `report.py`'s `_render_campaign()`/
  `render_report()` verdrahtet (Qualität-Trichter, Heatmap,
  Zeit-bis-akzeptiert und Latenz-Perzentile pro Kampagne; RTX-5090-
  Passungsdiagramm und historischer Verlauf berichtsweit/kampagnen-
  übergreifend). Neue CSS-Regeln für `.kpi-grid`/`.kpi-card`/
  `.kpi-card-warn`/`.kpi-value`/`.kpi-label`; `@media print` klappt jetzt
  zusätzlich jede eingeklappte Kampagne für den Druck/PDF-Export auf und
  verhindert Seitenumbrüche mitten in einem Diagramm/der KPI-Kartenreihe.
- Testsuite von 116 auf **145/145 bestandene** deterministische Tests
  erweitert (29 neu in `ChartRenderingTests`: Leerdaten-Fallbacks für
  alle 16 Diagramme, HTML-Escaping feindlichen Task-/Modellinhalts,
  Akzeptanz-Gate-Ausschluss aus Ranking-Diagrammen, Pareto-Front-Korrektheit
  an einem deterministischen Drei-Punkte-Fixture, "finaler Versuch" in der
  Heatmap, On-the-fly-P99-Berechnung, RTX-5090-Marker-Verhalten,
  keine $/€-Zeichen im Routing-Diagramm, sowie ein End-to-End-
  `render_report()`-Verdrahtungstest). Schema-Version weiterhin
  `agent-helper-v1` (rein additive Bericht-/Darstellungsänderung; kein
  CSV-/SQLite-Schemafeld geändert).
- Details/volle Spezifikation: `docs\project\agent_helper_benchmark.md`
  Abschnitt 12.1–12.4.
- Weiterhin keine Modellkampagne gestartet; ausschließlich Harness-/
  Report-Arbeit. Ein manueller, synthetischer Dry-Run-Report wurde in
  dieser Session lokal generiert, visuell/strukturell geprüft (u. a.
  Escaping feindlicher Titel/Fehlermeldungen, Pareto-Front, Hatch-Muster)
  und anschließend vollständig aus `benchmark_results\agent-helper\`
  entfernt — kein Artefakt verbleibt im Repo.

## 2026-08-01 — Agent-Helper-Evaluation-Track: Historical-Adapter-Härtung anhand realer Evidenz

- **Kontext:** korrigierte historische Evidenz vom Nutzer bestätigt: Repo
  hat 19 Commits (2 vor `origin/main`); eine primäre historische Ausgabe
  liegt außerhalb des Repos unter
  `C:\Users\z000g9hu\benchmark_results\migration_llm_bench_history.csv`
  (64 Zeilen: 5 "mini" + 59 "ora-pg-py-33"); repo-lokale
  Resume-Historien enthalten 11 Siemens-`gpt-oss`- und 11
  Ollama-`deepseek-coder-v2`-Zeilen. `ora-pg-py-33` hat 11 Aufgaben;
  `deepseek-v4-flash` schloss 7 ab und lief bei 4 in einen Timeout
  (36,36 %). Der bestehende `quality_score` ist strikt ein
  Keyword-/Regel-Heuristikwert, keine ausführbare Korrektheits- oder
  Eignungsprüfung.
- **Echter Bug gefunden und behoben (Sample-ID-Kollision):** die reale
  Historien-CSV enthält Fälle, in denen dieselbe `case_id` + `run`
  innerhalb desselben `benchmark_run_id` zweimal auftritt (ein Task läuft
  in einen Timeout, wird retried, der Legacy-Runner erhöht dabei aber nie
  seine eigene `run`-Spalte). Der zuvor daraus abgeleitete `sample_id` war
  in diesem Fall für beide Zeilen identisch — da `storage.py`s
  `samples`-Tabelle `PRIMARY KEY("sample_id")` mit `INSERT OR REPLACE`
  verwendet, wurde die frühere Zeile (der Timeout) beim Import stillschweigend
  überschrieben und ihre Evidenz verloren. Verifiziert anhand der echten
  externen Datei: **vor** dem Fix überlebten nur 60 von 64 Zeilen den
  Import, **nach** dem Fix alle 64 (inkl. aller 4 `deepseek-v4-flash`-
  Timeouts). Behoben durch Aufnahme des strikt monoton steigenden
  `sample_seq` in jede `sample_id`; permanenter Regressionstest
  (`test_retry_rows_sharing_case_id_and_run_get_distinct_sample_ids`,
  inkl. SQLite-Roundtrip-Beweis).
- **Konfigurierbare externe Pfade bestätigt/dokumentiert:** `--csv`
  akzeptiert bereits jeden Dateisystempfad (`pathlib.Path`), relativ,
  repo-intern oder vollständig extern — verifiziert live gegen die reale
  externe 64-Zeilen-Datei sowie gegen zwei repo-lokale
  `benchmark_results_resume_test*`-Historien (11 Zeilen je Datei, exakt
  wie vom Nutzer beschrieben). Neuer expliziter Regressionstest
  (`test_import_accepts_arbitrary_external_path_outside_any_repo_layout`).
- **Source/Provenance/Confidence jetzt explizit festgehalten:** jede
  importierte Zeile trägt jetzt in `notes` sowohl den aufgelösten
  absoluten Pfad der tatsächlich importierten Datei
  (`"source file: <path>"`) als auch einen expliziten
  `"confidence: estimated"`-Marker (nie "measured") — unter Wiederverwendung
  desselben measured/estimated/projected/unknown-Vokabulars wie
  `model_inventory.CONFIDENCE_LEVELS`. `provenance="historical_import"`
  und `reviewer_provenance=LEGACY_REVIEWER_PROVENANCE` bestehen bereits
  unverändert seit einem früheren Zyklus.
- **Timeout- vs. generischer Fehlerstatus:** die `error`-Spalte der
  Legacy-CSV wird jetzt auf Timeout-Marker ("timeout"/"timed out")
  untersucht und entsprechend als kanonischer `status="timeout"` statt
  eines generischen `"error"` importiert (`schema.STATUSES` unterscheidet
  beide bereits). Beide Status lösen im Hard-Acceptance-Gate identisch
  `"not_usable"` aus (`rubric.compute_sample_acceptance`) — dies verbessert
  ausschließlich die Berichtspräzision, nie das Ranking-Ergebnis.
  `system_error_code` wird bei Timeout auf `"timeout"` gesetzt.
- **Bereits bestehende Sicherung bestätigt (keine Codeänderung nötig):**
  `rubric.is_heuristic_reviewer_provenance()` erkennt den
  `LEGACY_REVIEWER_PROVENANCE`-Marker bereits zuverlässig (enthält sowohl
  "heuristic" als auch "keyword"), wodurch `AggregateRecord.
  reviewer_evidence_is_heuristic_only=True` für jede rein aus historischem
  Import bestehende Modell-Lauf-Gruppe gesetzt wird und
  `rubric.suitability_tier()` diese unabhängig vom rohen `quality_score`
  strikt unterhalb von `tier-1-recommended` deckelt — verifiziert
  end-to-end sowohl mit einem neuen dedizierten Test
  (`test_imported_heuristic_only_model_run_never_reaches_recommended_tier`)
  als auch live gegen die echten Siemens-`deepseek-v4-flash`-Daten (Tier
  `tier-2-conditional`/`tier-3-not-recommended`, nie `tier-1-recommended`,
  trotz eines `quality_score` von teils 100,0).
- Testsuite von 110 auf **116/116 bestandene** deterministische Tests
  erweitert (6 neu, alle in `HistoricalAdapterTests`). Schema-Version
  weiterhin `agent-helper-v1` (rein additive Adapter-Korrektur; keine
  CSV-Spalte geändert oder entfernt).
- Details: `docs\project\agent_helper_benchmark.md` Abschnitt 5
  (Historical adapter).
- Weiterhin keine Modellkampagne gestartet; ausschließlich Harness-
  Korrektur- und Verifikationsarbeit gegen bereits vorhandene, unveränderte
  historische CSV-Dateien (nur gelesen, nie geschrieben).

## 2026-08-01 — Agent-Helper-Evaluation-Track: Report-Design-Pattern-Abgleich (Navigation, Feasibility-Sichtbarkeit, Methodik)

- **Kontext:** gezielte Übernahme von fünf wiederverwendbaren
  Report-Design-Mustern, die in einem separaten, unverbundenen internen
  Repo (`ai-engineering-investment-case`, selbst ohne Commits — daher
  bewusst nicht als starke Evidenz behandelt) beobachtet wurden. Es wurden
  ausschließlich Muster übernommen, keine Inhalte — insbesondere keine
  finanziellen/ROI-Zahlen aus jenem Repo wurden übernommen oder referenziert.
- **Navigation (Muster 1 — Keyboard/Fullscreen/Print):** neuer
  Vollbild-Umschalter-Button sowie Tastaturkürzel `L` (Sprache), `F`
  (Vollbild), `P` (Drucken) in `report.py`, mit Schutz gegen versehentliches
  Auslösen bei gehaltener Modifikator-Taste oder während der Fokus in
  einem Eingabefeld liegt (`isTypingTarget()`); ein zweisprachiger
  `<kbd>`-Hinweis zeigt die Kürzel an. Alle Steuerelemente (inkl.
  Vollbild-Button) sind in `@media print` ausgeblendet.
- **Model-Inventory- & Feasibility-Sichtbarkeit (Muster 5 — Measured vs.
  Assumption/Projection):** die bereits seit einem früheren Zyklus
  bestehenden `ModelSpec`/`FeasibilityProjection`-Datenstrukturen
  (`model_inventory.py`, striktes `confidence`-Feld: measured/estimated/
  projected/unknown, RTX-5090-32GB kann strukturell nie "measured" sein)
  wurden bislang **nie im HTML-Bericht dargestellt** — dies war die größte
  reale Lücke. Neuer berichtweiter Abschnitt "Model inventory &
  feasibility" (`_render_model_inventory_table()`, `_feasibility_badge()`)
  zeigt pro Modell eine grüne "MEASURED"-Badge nur dort, wo tatsächlich
  gemessen und für diese Spalte zulässig, sonst immer eine amberfarbene
  "PROJECTION/ESTIMATE"-Badge — mit defensiver Rückstufung selbst bei einem
  manipulierten/direkt konstruierten Datensatz, der fälschlich
  `confidence="measured"` für die RTX-5090-Spalte behauptet.
  `orchestrator.py` lädt die Inventardatei jetzt best-effort
  (`load_default_model_inventory()`, nie werfend — fehlende, ungültige
  oder geheimnisverdächtige Dateien führen nur zum "kein Inventar
  geladen"-Fallback im Bericht) und reicht sie an `run_dry_run()` und
  `build_combined_report()` durch.
- **Doppel-Baseline-Vergleiche (Muster 3):** bereits strukturell vorhanden
  (`compute_throughput_efficiency_percent()` normiert immer gegen die
  Concurrency=1-Baseline; das Leaderboard vergleicht ausschließlich
  akzeptierte Kandidaten) — jetzt zusätzlich sichtbar gemacht durch einen
  "Baseline"-Chip auf der Concurrency=1-Zeile der
  Concurrency-Profil-Tabelle und einen expliziten Methodik-Eintrag.
- **Gate unabhängig von Kosten-/Provider-Stufe (Muster 4):** bereits
  strukturell vorhanden (`rubric.py` verzweigt nirgends nach Backend/
  Provider/Kostenklasse) — jetzt zusätzlich als expliziter
  zweisprachiger Methodik-Eintrag dokumentiert.
- **Kanonische Datenquelle ohne Copy/Paste-Drift (Muster 2):** bereits
  strukturell vorhanden (der Bericht liest ausschließlich aus SQLite/
  CSV-abgeleiteten Datenklassen, keine fest codierten Zahlen) — jetzt
  zusätzlich als expliziter Methodik-Eintrag dokumentiert.
- Vier neue zweisprachige `<dt>/<dd>`-Einträge im Methodik-Abschnitt von
  `report.py`: "Two comparison baselines", "Gate independent of cost/
  provider tier", "Measured vs. assumption/projection", "Canonical data
  source".
- `render_report()` erhält einen neuen optionalen Keyword-Parameter
  `model_inventory: Sequence[ModelSpec] = ()` — vollständig
  abwärtskompatibel; bestehende Aufrufer ohne dieses Argument rendern
  weiterhin unverändert den "kein Inventar geladen"-Fallback.
- Testsuite auf 110/110 bestandene deterministische Tests erweitert
  (16 neu: `ModelInventoryReportRenderingTests`,
  `OrchestratorModelInventoryLoadingTests`, `MethodologyContentTests`,
  `ReportNavigationTests`); `agent-helper-v1`-Schema-Version unverändert
  (rein additive Bericht-/Orchestrierungsänderung, kein CSV-/
  SQLite-Schemafeld geändert).

## 2026-08-01 — Agent-Helper-Evaluation-Track: Phasenzuordnung & Concurrency-Capacity-Profiling

- **Phasenzuordnung (per Sample, wo beobachtbar):** zehn neue rohe
  Zeit-in-Sekunden-Felder auf `SampleRecord` — `total_wall_seconds`,
  `llm_queue_seconds`, `llm_request_seconds`, `prompt_eval_seconds`,
  `generation_seconds`, `local_tool_exec_seconds`, `test_exec_seconds`,
  `orchestrator_review_seconds`, `idle_wait_seconds`, `overlap_seconds` —
  alle einzeln optional (`None` = nicht instrumentiert, nie geschätzt).
  Für GitHub-Copilot-Task-Agent-Samples bleiben alle Felder `None`
  (interne Modell-Timings sind dort strukturell nicht exponierbar).
- Neues reines Funktionsmodul `scripts\agent_helper_eval\phase_timing.py`:
  berechnet vier **exklusive** Critical-Path-Prozentsätze (LLM / lokale
  Tools+Tests / Queue+Idle / Orchestrierung), die stets exakt auf 100 %
  normiert sind (Normierung gegen die eigene Rohsumme, nicht gegen
  `total_wall_seconds` — Overlap/unerklärte Zeit bleiben separate
  Diagnosewerte, nie in die 100 %-Aufteilung eingerechnet), plus drei
  **nicht-exklusive** Auslastungsquoten (Modell-Busy-%, GPU-Active-%,
  Tool-Runner-Busy-%; bewusst nicht auf 100 % gedeckelt — unter echter
  Nebenläufigkeit legitim über 100 %). Eine LLM-Bucket-Präzedenzregel
  bevorzugt den API-seitigen `llm_request_seconds`-Wert (falls vorhanden)
  gegenüber der granularen `prompt_eval_seconds + generation_seconds`-Summe.
  `aggregate.build_all_aggregates()` rollt diese Werte pro Modell-Lauf
  per **Ratio-of-Sums** (nicht Mean-of-Percentages) in zehn neue
  `AggregateRecord`-Felder auf; nicht instrumentierte Gruppen bleiben
  vollständig `None` statt erfundener Nullen.
- **Concurrency-/Capacity-Profiling (nur nach bestandenem Akzeptanz-Gate):**
  neue dritte kanonische Tabelle `CapacityProfileRecord`
  (`agent_helper_capacity_profile.csv`, eigenes Grain
  Campaign+Run+Benchmark-Set+Backend+Modell+Concurrency-Level) plus neues
  Modul `scripts\agent_helper_eval\capacity_profile.py` mit Katalog für
  1/2/4 gleichzeitige Anfragen an **dasselbe** akzeptierte lokale Modell
  (Aggregat-/Pro-Request-TPS, Queue-/Service-Zeit, p95-Latenz,
  GPU/VRAM/RAM, Fehler, Time-to-Accepted-Result) und einer
  Skalierungsklassifikation (`scales-well`/`diminishing-returns`/
  `thrashing`/etc.). **Hartes, zweifach abgesichertes Preflight-Gate**
  (Entscheidungsfunktion `can_start_concurrency_profile()` **und**
  `schema.validate_capacity_profile()` verweigern die Erzeugung, wenn
  `preflight_quality_gate_passed` oder `preflight_memory_safety_checked`
  nicht beide `True` sind) — Concurrency-Profiling darf laut Spezifikation
  bei 12 GB VRAM niemals vor bestandener Einzel-Request-Qualitäts-/
  Stabilitätsprüfung und Speicher-Sicherheitscheck laufen; Stufen laufen
  gestaffelt (`requires_prior_level`).
- `orchestrator.py`: deterministischer synthetischer Demo-Generator für
  Phasenzuordnung (`_synthetic_phase_timing()`, unterschiedlich je Backend:
  Copilot=alles `None`, Siemens-API=nur opaker `llm_request_seconds`, lokal
  `ollama`=granulare Prompt-Eval-/Generation-Aufteilung mit einer bewusst
  demonstrierten Overlap-Situation) sowie für ein komplettes
  1/2/4-Concurrency-Profil (`generate_synthetic_capacity_profile()`,
  "diminishing-returns"-Skalierungsform) — beide vollständig in `dry-run`
  und den kombinierten Bericht verdrahtet, ohne echten Modell-/
  Ressourcenzugriff.
- Neuer HTML-Bericht-Abschnitt je Modell-Lauf: Phasenzuordnungstabelle
  (vier exklusive Prozentsätze, Overlap-/Unaccounted-Diagnose, drei
  Auslastungsquoten, zweisprachige Methodik-Notiz) und
  Concurrency-Profil-Tabelle (1/2/4, Effizienz, Skalierungsklasse,
  VRAM-Headroom, Preflight-Bestätigungshinweis); beide zeigen einen
  expliziten "nicht instrumentiert"/"kein Profil vorhanden"-Hinweis statt
  leerer oder erfundener Zeilen.
- `local_lock.py`: nur Dokumentationsklarstellung (keine Codeänderung) —
  das Lock ist Backend/Modell-Identitäts-basiert, nicht Request-Zahl-
  basiert, unterstützt "ein geladenes Modell, N gleichzeitige Anfragen"
  also bereits strukturell.
- Testsuite auf 94/94 bestandene deterministische Tests erweitert (38 neu:
  `PhaseTimingTests`, `CapacityProfileTests`,
  `CapacityProfileSchemaValidationTests`, `AggregatePhaseTimingWiringTests`,
  `AggregateSchemaPhaseTimingValidationTests`,
  `CapacityProfileCsvRoundTripTests`, `PhaseTimingReportRenderingTests`);
  `agent-helper-v1`-Schema-Version unverändert (rein additiv, bestehende
  66/67-Spalten-CSVs und Historie unangetastet; neue dritte CSV-Datei ist
  additiv und beeinflusst keine bestehende Datei).
- Dokumentation aktualisiert: `docs\project\agent_helper_benchmark.md`
  (§3 Storage-Modell, neuer §11 "Phase attribution & capacity/concurrency
  profiling", §12 HTML-Bericht, §13 Tests, §14 Safe-Commands),
  `docs\operations\runbook.md` (Dry-Run-Abschnitt erwähnt jetzt alle drei
  CSVs + Phasen-/Concurrency-Demo), `AGENTS.md` (Aktueller Stand).
- Keine Modellkampagne gestartet; weiterhin ausschließlich Harness-Arbeit.

## 2026-08-01 — Agent-Helper-Evaluation-Track: Hartes Akzeptanz-Gate ("Speed never compensates for unusable quality")

- Hartes Akzeptanz-Gate implementiert, das **vor** jeder Performance-/
  Composite-Rangfolge greift: ein Modell-Lauf mit fehlgeschlagenem
  deterministischem Test, Platzhalter-/unvollständiger Ausgabe, unsicherem
  Verhalten oder zu niedrigem Reviewer-Score wird als `"not-usable"`
  eingestuft und unabhängig von Tokens/Sekunde aus dem Ranking
  ausgeschlossen; Performance ist nur noch Tie-Breaker unter bereits
  akzeptierten, nutzbaren Ergebnissen.
- Neue Sample-Ebene (`SampleRecord`): `unsafe_behavior_flag`,
  `output_placeholder_or_incomplete`, `acceptance_status`
  (`accepted`/`not_usable`/`not_evaluated`), `acceptance_reasons` — via
  `rubric.compute_sample_acceptance()` einzig-autoritativ berechnet.
- Neue Aggregat-Ebene (`AggregateRecord`): `accepted_sample_count`,
  `not_usable_sample_count`, `not_evaluated_sample_count`,
  `acceptance_rate_percent`, `unsafe_sample_count`,
  `unresolved_task_count`, `time_to_accepted_result_seconds_mean`,
  `rework_tokens_to_accept_mean`, `hard_gate_failed`, `hard_gate_reasons`,
  `reviewer_evidence_is_heuristic_only` — via
  `rubric.compute_aggregate_hard_gate()` bzw. der neuen zweistufigen
  `aggregate.build_all_aggregates()`-Logik berechnet (Peer-Pool für den
  Speed-Score enthält nur nicht-ausgeschlossene Gruppen und nur deren
  akzeptierte Samples).
- **Keyword-/Heuristik-Score-Sicherung:** ein Modell-Lauf, dessen gesamte
  Qualitätsevidenz allein auf einem Legacy-/Heuristik-Keyword-Reviewer-Score
  beruht (keine deterministische Testevidenz), wird strukturell unterhalb
  von `tier-1-recommended` gedeckelt (`reviewer_evidence_is_heuristic_only`,
  von `schema.validate_aggregate()` erzwungen) — kein Modell wird je allein
  aufgrund eines Keyword-Scores als "geeignet" bezeichnet (deckt insbesondere
  ad-hoc benannte Legacy-Modelle wie ein testweise "RNJ-1" genanntes ab).
  `historical_adapter.py` markiert importierte Legacy-Zeilen entsprechend.
- Neuer HTML-Bericht: eigene "Excluded (hartes Akzeptanz-Gate)"-Tabelle,
  Leaderboard/Quality-vs-Speed-Chart zeigen ausgeschlossene Modell-Läufe
  nie, Executive Summary formuliert die Hart-Regel zweisprachig explizit
  und nennt jeden ausgeschlossenen Modell-Lauf sowie ggf. den
  Keyword-Score-Vorbehalt beim "besten nutzbaren Modell".
- Testsuite auf 56/56 bestandene deterministische Tests erweitert (29 neu:
  `HardAcceptanceGateTests`, `AggregateSchemaValidationTests`, neue
  `SchemaValidationTests`-Fälle, ein `ReportEscapingTests`-Fall für die
  Leaderboard-/Excluded-Trennung); `agent-helper-v1`-Schema-Version
  unverändert (rein additiv, bestehende Spalten/Historie unangetastet).
- Dokumentation aktualisiert: `docs\project\agent_helper_benchmark.md`
  (neuer §9 "Hard acceptance gate"), `docs\operations\runbook.md`
  (Hart-Gate-Hinweis + Fixture-Modell-Erklärung im Dry-Run-Abschnitt),
  `AGENTS.md` (Aktueller Stand).
- Keine Modellkampagne gestartet; weiterhin ausschließlich Harness-Arbeit.

## 2026-08-01 — Agent-Helper-Evaluation-Track: Harness-Fundament

- Neues additives Subsystem `scripts\agent_helper_eval\` implementiert
  (Schema, SQLite-Speicher, CSV-Export, Aggregation/Perzentile,
  Orchestrator-Rubrik, Legacy-Adapter, Benchmark-Katalog, Modell-Inventory,
  Ein-lokales-Modell-Lock, zweisprachiger HTML-Bericht, Orchestrator-Skelett).
- CLI `scripts\run_agent_helper_campaign.py` mit Subcommands `dry-run`,
  `connect-gate-plan`, `report`, `import-legacy` ergänzt.
- Kanonisches Schema `agent-helper-v1`: `agent_helper_samples.csv`
  (Task-Attempt-Ebene) und `agent_helper_aggregates.csv` (Modell-Lauf-Ebene),
  feste Spaltenreihenfolge, explizite Validierung, N/A statt erfundener Werte.
- Neue Ergebnisse strikt isoliert unter
  `benchmark_results\agent-helper\<campaign-id>\`; bestehende
  `llm_migration_benchmark.py`-Ausgaben, -Skripte und -Historie unverändert.
- Read-only-Adapter für `migration_llm_bench_history.csv` mit explizit
  dokumentierten Limitationen (RAM-Prozent statt MB, fehlende Zeitzone,
  Heuristik-Score → `reviewer_score`, nie `deterministic_score`).
- 27 deterministische Unit-Tests (`scripts\test_agent_helper_eval.py`)
  bestanden; kein Modell-/API-/Ollama-/llama.cpp-Aufruf in dieser Phase.
- Dokumentation ergänzt: `docs\project\agent_helper_benchmark.md`
  (Methodik/Schema/Rubrik), `docs\operations\runbook.md` (sichere Befehle),
  `AGENTS.md` (Aktueller Stand, Quellenverzeichnis, Quick Commands, Validation).
- Keine Modellkampagne gestartet; dies ist ausschließlich das Mess-/
  Daten-/Report-Harness-Fundament für spätere, seriell vom Parent-Agent
  durchgeführte Kampagnen.

## 2026-07-31 — Alle lokalen Modelle in OpenCode

- OpenCode-Modellkonfiguration aus der laufenden Ollama-Registry und vollständigen
  llama.cpp-GGUF-Sätzen reproduzierbar erzeugt.
- Ollama und llama.cpp als getrennte Provider mit eindeutigen Modell-IDs registriert.
- Nicht-lokale Ollama-Cloud-Tags und unvollständige GGUF-Shards ausgeschlossen.
- llama.cpp-Router mit nativem Auto-Load, maximal einem gleichzeitig geladenen Modell und
  Benutzer-Autostart eingerichtet.
- Native Maximalkontexte für alle lokalen Ollama- und GGUF-Modelle persistent konfiguriert;
  vorhandene größere Sonderkontexte bleiben erhalten.
- GGUF-Presets auf Flash Attention und Q4-KV-Cache eingestellt.
- Vorhandene Provider und Zugangsdaten beim Konfigurationsupdate unverändert bewahrt.

## 2026-07-30 — Living-memory-Verstaendnisbenchmark

- Tool-Agent-Katalog mit Startup-Navigation, Chatablage, WHY-Referenzen,
  Memory-Routing, Settings-Vererbung, Owner-Policy und Session-Ende ergaenzt.
- Deterministisches gewichtetes Scoring und diagnostische Fehlerrunde hinzugefuegt.
- Getrennten Pure-Model-Track mit synthetischem Workspace-Fixture fuer Siemens und
  Ollama gebaut; echte Userdaten werden nicht an Cloud-Modelle uebertragen.
- Restricted Live-Workspace-Faelle technisch auf lokale `ollama/*`-Agenten begrenzt.
- JSONL-Aggregation nach Track, Backend, Modell, Prioritaet und Dimension ergaenzt.
- Alle fuenf chatfaehigen Siemens-Modelle mit 70 Basisfaellen getestet.
- API-Fixture-Luecke im exakten Chat-Naming isoliert: Nach sechs Syntaxregeln und
  einem Beispiel stiegen alle fuenf Modelle im fokussierten Nachtest auf 100 Prozent.
- Vollstaendige Auswertung unter
  `benchmark_results/living-memory-siemens-20260730-1418/` abgelegt.
- Hard-Agent-V2 mit isolierten realen Tool-Workspaces ergaenzt: Inline-Warum,
  User-/Agent-Memory, Owner-Denial, Autonomie, Chat-Evidenz und Git-Abschluss.
- Windows-`opencode.cmd`, absolute/kurze Workspaces, fallweises Resume und
  Capability-Fehler als persistente Resultate implementiert.
- Kanonischen Sprachwechsel mit `user_chat_lang` separat nachgetestet.
- Hard-Auswertung unter
  `benchmark_results/living-memory-hard-siemens-v2-20260730/` abgelegt.

## 2026-07-27 — Agent-Hierarchie und Projekt-Router konsolidiert

- Doppelte Abschnitte aus `AGENTS.md` entfernt und die Datei als kompakten
  Projekt-Router mit aktuellem Stand, kanonischen Quellen, Befehlen, Guardrails
  und Memory-Routing neu strukturiert.
- Parallele `.github/copilot-instructions.md` entfernt; `AGENTS.md` ist nun die
  eindeutige globale Instruktionsquelle dieses Repositories.
- Vererbung, lokale Overrides und `AI-ACCESS` gemäß der zentralen
  `C:\GIT\AGENTS.md`-Hierarchie dokumentiert.

## 2026-07-27 — Terminologie: Heuristik-Score + Sandbox-TODO im Report

- `scripts/llm_migration_benchmark.py` angepasst:
  - Tabellen-/Chart-Begriff `Score` auf `Heuristik-Score` umbenannt
  - Detailreport-Spalte `Model score` auf `Model heuristik-score` umbenannt
  - Report-Hinweis ergänzt, dass der Heuristik-Score keyword/rule-basiert ist
  - TODO-Block ergänzt: späterer PostgreSQL+Python-Sandbox-Eval mit separatem Pass/Fail-Score
- `README.md` und `AGENTS.md` Terminologie auf `Heuristik-Score` nachgezogen.

## 2026-07-26 — Live-Status/ETA-Overhaul + Historien-CSV

- `scripts/llm_migration_benchmark.py` erweitert:
  - Live-Hauptansicht auf kompakte Modell-Status-Tabelle umgestellt
    (15 geplante Zeilen: 5x Siemens, 5x Ollama, 5x llama.cpp)
  - Zeit-/Planungsfelder ergänzt:
    `Planned start`, `Run started`, `Last update`,
    `Elapsed time`, `Elapsed left (est.)`, `ETA end`
  - Gesamtblock für Kampagnenlauf ergänzt:
    `Bulk/campaign started`, `Elapsed total`,
    `Elapsed left (estimated)`, `Estimated total duration`, `Estimated finish`
  - ETA-Logik auf modell-/slotbasierte Projektion umgestellt
    (realistischere Gesamtschätzung bei gemischten laufenden/geplanten Slots)
  - Live-Tabelle um AVG-Auslastungsmetriken erweitert:
    `Tok/s`, `CPU%(avg)`, `GPU%(avg)`, `RAM GB(avg)`, `VRAM GB(avg)`
  - RAM-Messung auf prozessbezogene Ermittlung umgestellt
    (Ollama-/llama-Prozesse statt System-Gesamtlast); VRAM-Messung unverändert
  - Bewertungsfelder in Live-Tabelle ergänzt: `Score`, `Rating`
  - Visualisierung ergänzt:
    `docs/project/benchmark_models_overview.svg`
  - Detailansicht ausgelagert:
    `docs/project/benchmark_report_details.md`
  - Persistente Historie ergänzt:
    finale Läufe werden append-only in
    `benchmark_results/migration_llm_bench_history.csv` mit allen Detailspalten gespeichert
  - Auto-Resume ergänzt:
    `--resume auto` setzt nach Crash/Kill/Neustart auf dem letzten passenden
    `*_inprogress.csv` auf und führt nur fehlende Samples erneut aus

## 2026-07-26 — Reporting-Standard verschärft (Pflichtmetriken + Settings)

- `scripts/llm_migration_benchmark.py` erweitert:
  - automatischer HTML-embedded Markdown-Report (`docs/project/benchmark_report.md`)
  - Model-Summary mit `Overall`, `Suitability`, `Wall-s(total)`
  - zusätzliche Min/Max-Metriken: `RAM%(min/max)`, `GPU%(min/max)`, `VRAM MB(min/max)`
  - vollständige Raw-Metriken pro Testfall inkl. `forbidden_hits`
  - Ausgabe der effektiven llama.cpp-Kampagnen-Settings aus `scripts/run_local_campaign.ps1`
- `AGENTS.md` Abschnitt 7.1 ergänzt: verbindliche Liste aller Pflichtmetriken und Reproduzierbarkeits-Settings.

## 2026-07-29

- `AGENTS.md` komplett neu geschrieben als lebendes Projektgedächtnis:
  vollständige Modell-Tabelle (28 Ollama-Modelle), alle Benchmark-Ergebnisse,
  ngl-Einfluss-Tabelle, Empfehlungen, offene Aufgaben, Schnellstart-Befehle.
- `.github/copilot-instructions.md` erneuert: sofort lauffähige Befehle,
  bekannte Fallstricke, nächste konkrete Tasks.

## 2026-07-25 — Erste Benchmark-Runde (abgeschlossen)

### Durchgeführte Läufe

| Lauf-ID | Konfiguration | Modelle |
|---|---|---|
| 095653 | Ollama-only, früh | qwen3.6:27b, qwen3.6:35b, gpt-oss:20b |
| 100138 | Ollama-only | qwen3.6:27b, qwen3.6:35b, gpt-oss:20b |
| 100607 | Ollama-only | qwen3.6:27b, qwen3.6:35b, gpt-oss:20b |
| 124336 | llama.cpp (früh, schlechte Scores) | gpt-oss, qwen3.6-27b, qwen3.6-35b |
| 125910 | llama.cpp | gpt-oss, qwen3.6-27b, qwen3.6-35b |
| 130942 | Ollama | qwen3.6:27b, qwen3.6:35b, gpt-oss:20b |
| 131636 | llama.cpp | gpt-oss, qwen3.6-27b, qwen3.6-35b |
| 132136 | Ollama, erweiterte Modelle | codestral, deepseek-coder-v2:16b, qwen2.5-coder:32b, qwen3-coder:30b |
| 132600 | Ollama, neue Modelle | devstral-small-2:24b, glm-4.7-flash, qwen3-coder-next |
| 132747 | llama.cpp, neue Modelle | deepseek-coder-v2-16b, qwen3-coder-30b |
| 132937 | llama.cpp, neue Modelle | deepseek-coder-v2-16b, qwen3-coder-30b |
| 140632 | llama.cpp, Neutest | gpt-oss, qwen3.6-27b, qwen3.6-35b |
| balanced_ngl99/162158 | Beide Backends, ngl=99, Balanced-Profil | gpt-oss, qwen3.6:27b, qwen3.6:35b |
| highperf_ngl99/163505 | Beide Backends, ngl=99, HighPerf-Profil | gpt-oss, qwen3.6:27b, qwen3.6:35b |
| highperf_llama_ngl20/163630 | llama.cpp only, ngl=20, HighPerf | gpt-oss:20b, qwen3.6:35b |
| highperf_llama_ngl0/163917 | llama.cpp only, ngl=0, HighPerf | gpt-oss:20b, qwen3.6:35b |
| translation_highperf | Übersetzung DE→EN, beide Backends | qwen3.6:27b, qwen3.6:35b, gpt-oss:20b |

### Wichtigste Erkenntnisse

- `qwen3-coder:30b` und `qwen3.6:27b-q4_K_M` sind die besten Migrations-Modelle (88.89%)
- `deepseek-coder-v2:16b` bietet bestes Speed/Qualitäts-Verhältnis (55 TPS, 84%)
- ngl-Offload kritisch: ngl=0 → 7 TPS, ngl=99 → 50+ TPS bei llama.cpp
- Balanced-Profil übertrifft High-Perf bei llama.cpp (84 vs 50 TPS bei gpt-oss)
- Alle qwen3.6-Modelle erreichen 100% bei Übersetzung

## 2026-07-26 — Siemens Cloud Benchmark (abgeschlossen)

### Durchgeführte Läufe
- `migration_llm_bench_20260726_142651` — 5 Siemens-Modelle, 3 Runs, **ohne Token-Limits**

### Wichtigste Erkenntnisse
- Token-Limit-Bug bei `deepseek-v4-flash` behoben: 18% → **92.59% Qualität**
- `ministral-3-14b-instruct-2512` höchste Qualität überhaupt: **95.83%**
- `gpt-oss-120b` schnellstes Modell gesamt: **163 TPS**
- `qwen-3.6-27b` beste Balance: 92.86% Qualität bei 93 TPS und nur 3s Wall-time
- `SIEMENS_MODEL_MAX_TOKENS` vollständig entfernt (war falsch dokumentiert als bereits gefixt)
- Vergleichstabelle erstellt: `docs/project/comparison_table.md`

### Code-Änderungen
- `scripts/llm_migration_benchmark.py`: `SIEMENS_MODEL_MAX_TOKENS` auf leeres Dict gesetzt (kein Limit für Cloud-Modelle)

## 2026-07-26 — Repository-Initialisierung

- Neues Repository `D:\git\llm-evaluation-workbench` angelegt.
- Alle Legacy-Skripte und Ergebnisse konsolidiert.
- GitHub-Remote: `https://github.com/thomasdenk79-cyber/llm-evaluation-workbench.git`
- CI/CodeQL/Dependabot aktiviert.
- Commits: `df7b446`, `acc0db2`, `84d2a44`.
