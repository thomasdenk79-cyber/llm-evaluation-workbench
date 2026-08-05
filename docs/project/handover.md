---
title: "LLM Evaluation Workbench - Session Handover"
status: active
canonical: true
updated: 2026-08-05T13:30:00+02:00
---

# Session handover

## Current handover — 2026-08-05

### Delivered

- Clean campaign data is complete under
  `benchmark_results\clean-local-campaign\`: 57 Ollama medium rows
  (19 models × 3), 7 upstream llama.cpp GGUF rows and 7 ik CUDA-fork rows.
- The authoritative migrated detail CSV is
  `benchmark_results\clean-local-campaign\unified_benchmark_detail.csv`
  with 71 rows and schema `benchmark-v2.2`.
- The report is
  `docs\project\benchmark_report.html`. It uses the locally vendored
  Tabulator 6.3.1 assets in `docs\project\vendor\tabulator\`, not a
  homemade table or CDN dependency.
- Report features: 71 run-level rows, movable columns, virtual DOM,
  per-column header filters, multi-status filtering, multi-column grouping,
  sorting, compact mode, expandable launch JSON and copyable error text.
- The report contract is documented in
  `docs\project\benchmark-report-requirements.md`.
- The Qwen 3.6 MTP fork is explicitly deferred: the downloaded artifact was
  only about 10.3 GB instead of the expected ~17.3 GiB and no verified MTP
  head exists. Normal Qwen rows must show no MTP.

### Commits

- `cee8d8b` — clean backend matrix documentation
- `4f588bd` — complete 71-run report
- `0c8870c` — unified report contract and schema
- `7c0b739` — real Tabulator grid and empty-grid fix

### Next agent: first checks

1. Read this handover, `docs\project\todo.md`,
   `docs\project\benchmark-report-requirements.md` and the current
   `AGENTS.md`.
2. Open `docs\project\benchmark_report.html` and verify that rows are
   visible, Tabulator assets load locally, header filters work, and grouping
   does not hide rows.
3. Run:
   `python -m py_compile scripts\llm_migration_benchmark.py`
   and the focused report smoke generation against the clean report source.
4. Do not reset or clean the dirty worktree. Several unrelated agent-helper
   changes are intentionally present and must not be reverted.

### Known limitations and open work

- `vram_free_gb` is `N/A` for historical rows because free VRAM was not
  persisted in the original telemetry; new runs must populate it.
- The web-grid fixture is a screening fixture, not a complete hard coding
  benchmark. Use the existing SWE hard suites for quality decisions.
- The current report is generated from a combined clean source directory;
  future master-runner changes must keep all source backends in the unified
  CSV instead of replacing it with only the latest backend.
- The seven GGUF matrix rows are one-repeat screens. Repeat the finalists
  before making a daily-runner recommendation.
- Do not treat GPU utilization above 90% as a target; throughput, quality,
  reliability, VRAM headroom and PCIe traffic are the decision signals.

## Auftrag und Scope

Diese Session baut aus der bisherigen LLM-Workbench eine belastbare
**Agent-Helper-Evaluation**. Ziel ist ein kostenbewusster Orchestrator:

- lokale Ollama-/llama.cpp-Modelle für geeignete, begrenzte Aufgaben;
- Siemens-API-Modelle, insbesondere Qwen 3.6 27B, für Coding und
  Dokumentation ohne lokale GPU-Last;
- Premium-Copilot-Modelle für Architektur, schwierige Fehler,
  Orchestrierung und abschließendes Review.

Qualität ist ein hartes Gate. Geschwindigkeit, Tokenverbrauch und Kosten
werden erst unter fachlich akzeptierten Ergebnissen verglichen.

## Aktueller technischer Stand

### Wiederaufsetzpunkt — 2026-08-03 18:35

- Der aktuelle Status ist in `docs\project\todo.md` im Abschnitt
  **Aktueller Zwischenstand** gespiegelt.
- Keine Benchmarkkampagne läuft derzeit. Ollama und Siemens sind beendet.
- Die Qwen-3.6-35B-A3B-Q4-Probe existiert für beide Backends; die
  llama.cpp-Probe ist wegen CPU-Fallback nicht als GPU-Ergebnis gültig.
- Der Runner verwendet 3600 Sekunden Modellfrist, schreibt
  `benchmark_results\cross-backend-mini-20260803\mini_progress.log` und
  beendet einen Ollama-Stream nach 300 Sekunden ohne neue Tokens.
- Konsistente Builds gegen CUDA 13.2 und 13.3 wurden erstellt; beide
  melden weiterhin `ggml_cuda_init: failed to initialize CUDA` und liefern
  kein `gpu_info`.
- Die vollständige llama.cpp-Matrix wurde deshalb noch nicht gestartet.
  CPU-only-Werte wären irreführend.

### Konkreter nächster Test

Nach Neustart:

```powershell
$cuda = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3'
$env:Path = "$cuda\bin\x64;$cuda\bin;$env:Path"
C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v133-installed\bin\Release\llama-bench.exe `
  -m C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf `
  -p 32 -n 16 -r 1 -ngl 999 -fa 1 -ctk q8_0 -ctv q8_0 -t 16 `
  -ot "blk\.[0-9]+\.ffn_(up|down|gate)_exps\.weight=CPU" -o json
```

Bei erneutem `cuInit=100`/`ggml_cuda_init`: NVIDIA Studio zuerst über
**Benutzerdefiniert > Neuinstallation durchführen** reparieren; erst
danach DDU im abgesicherten Modus einsetzen.

### CUDA-Fork-Neuaufbau nach Treiberinstallation (2026-08-03 20:13)

- Der NVIDIA-Treiber `610.88` erkennt die RTX 3500 Ada wieder korrekt.
- Das vollständige CUDA Toolkit 13.3.1 wurde installiert; `nvcc` liegt unter
  `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin\nvcc.exe`.
- Ein sauberer Visual-Studio-2022-Build des Forks auf Commit `cb9147f` liegt
  unter `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v133-clean`.
  `llama-bench.exe` und `llama-server.exe` wurden erfolgreich gebaut.
- Der CUDA-Preflight ist jetzt gültig: `cuda=true`, `gpu_blas=true`,
  `gpu_info=NVIDIA RTX 3500 Ada Generation Laptop GPU`,
  Compute Capability 8.9 und 12.281 MiB VRAM.
- Qwen 3.6 35B A3B Q4 mit GPU-Offload und CPU-Experts erreichte bei diesem
  Einzeltest 98,27 Prompt-Tok/s und 31,92 Generate-Tok/s; Q5 erreichte
  83,26 Prompt-Tok/s und 21,41 Generate-Tok/s. Beide sind gültige CUDA-
  Proben, aber noch keine vollständige Matrix.
- Für die Laufzeit muss zusätzlich
  `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin\x64`
  im `PATH` stehen; dort liegen die CUDA-DLLs.

### Kontext-Cap und 27B-Nachtest (2026-08-03)

- Die lokalen Ollama-Tags sowie die OpenCode-/llama.cpp-Presets sind für
  interaktives Coding auf 32.768 Kontexttokens begrenzt. Der native
  Modellkontext bleibt unverändert; begrenzt wird nur der Betriebswert.
- `qwen3.6:27b-q4_K_M` wurde nach der Umstellung erneut über Ollama
  gemessen. Bei 8 erzeugten Tokens und 28 Prompttokens ergaben sich
  3,23 Generate-Tok/s, 17,81 Prompt-Tok/s und 5,25 s Wall-Time.
  System-Sampling: 66,09 % CPU im Mittel (80,4 % max.) und 36,93 % GPU
  im Mittel (98 % max.). `ollama ps` bestätigte 48 % CPU / 52 % GPU bei
  32.768 Kontexttokens.
- Der Lauf bestätigt eine bessere GPU-Verteilung gegenüber dem alten
  262k-Betrieb, bleibt aber für interaktives Coding deutlich langsamer als
  der Qwen-3.6-35B-A3B-Q4-Fork. Die aus Ollama exportierte 27B-GGUF ist
  mit dem ik_llama.cpp-Fork weiterhin nicht ladbar; hierfür liegt keine
  gültige llama.cpp-Messung vor.
- Laguna Q4/Q5/Q6 sind im Ollama-Inventar verfügbar und auf 32k begrenzt.
- Die vollständige `Laguna-XS-2.1-Q4_K_M.gguf` wurde aus
  `bartowski/Laguna-XS-2.1-GGUF` nach
  `C:\Users\z000g9hu\llama.cpp\models\` geladen. Der CUDA-Fork erkennt
  sie als `laguna 33B.A3B Q4_K - Medium`; der Smoke-Test lief mit
  `cuda=true` und `gpu_blas=true` bei 40,43 Prompt-Tok/s und
  8,00 Generate-Tok/s (`pp32`/`tg8`). CPU-/GPU-Sampling und echte
  Coding-Gates stehen für Laguna noch aus.

### Favoritenvergleich Hard-/Mini-Gates (2026-08-04)

- Der vorhandene Hard Tool-Agent Benchmark v2 mit acht synthetischen
  Workspace-/Tool-/Memory-/Git-Fällen wurde für Qwen 3.6 35B Q4 über
  Ollama vollständig ausgeführt: 78,32 % Score, 400,3 s Wall-Time,
  53,06 % CPU- und 41,58 % GPU-Mittelwert. Die Rohdaten liegen unter
  `benchmark_results\favorites-hard-20260803\ollama-qwen35`.
- DeepSeek Coder V2 16B ist im Hard Tool-Agent Track nicht ausführbar,
  weil das Ollama-Modell keine Tools anbietet. Laguna Q4 wurde wegen eines
  wiederholten Tool-/Summary-Loops nicht abgeschlossen; der Qwen-CUDA-
  Fork-Hardlauf überschritt 25 Minuten ohne abgeschlossenen Fall.
- Für den Backendvergleich wurde deshalb zusätzlich der vorhandene
  Cross-Backend-Mini-Coding-Gate mit identischem Prompt und Score-Logik
  ausgeführt. Die sechs Rohdateien liegen unter
  `benchmark_results\favorites-cross-backend-20260804`.
- Mini-Gate-Messwerte (System-Sampling, `ik_llama.cpp` mit `ngl999`,
  Flash Attention, Q8-KV, 16 Threads, CPU-Experts):
  - Ollama DeepSeek 16B: 80 % (8/10), 59,86 Tok/s, 16,10 s,
    25,24 % CPU / 25,30 % GPU.
  - Ollama Laguna Q4: Format-Gate 0 %, 53,36 Tok/s, 27,06 s,
    26,37 % CPU / 19,27 % GPU.
  - Ollama Qwen 35B Q4: Format-Gate 0 %, 30,78 Tok/s, 46,47 s,
    39,55 % CPU / 31,61 % GPU.
  - CUDA-Fork DeepSeek 16B: 80 % (8/10), 32,43 Tok/s, 11,07 s,
    36,13 % CPU / 13,34 % GPU.
  - CUDA-Fork Laguna Q4: Format-Gate 0 %, 26,83 Tok/s, 27,38 s,
    42,70 % CPU / 27,31 % GPU.
  - CUDA-Fork Qwen 35B Q4: Format-Gate 0 %, 24,52 Tok/s, 29,84 s,
    44,86 % CPU / 27,93 % GPU.
- Ein Mini-Gate mit Formatfehler ist kein Qualitätsurteil; die 0 %-Zeilen
  zeigen, dass die Antwort nicht in das erwartete ausführbare Python-
  Format normalisiert werden konnte. Für eine Daily-Coder-Entscheidung
  bleibt Qwen/Ollama mit dem Hard-Score der stärkste belastbare Kandidat;
  DeepSeek ist der schnellste robuste Mini-Gate-Lauf.

### Vollständige SWE-Hard-Suites (2026-08-04)

- `swe-sql-hard-24`, `swe-python-hard-24` und `swe-mixed-hard-24` wurden
  mit je acht Fällen, 32k Kontext, 16 Threads, `ngl=999`, Flash Attention,
  Q8-KV und CPU-Experts für Qwen 35B Q4, DeepSeek Coder V2 16B und Laguna
  Q4 in Ollama sowie im CUDA-Fork ausgeführt.
- Der kanonische Vergleich steht in
  `benchmark_results\agent-helper\report.html` und `report.md`. Die
  Übersicht enthält Datum/Uhrzeit, Backend, Modell, Suite, Samples,
  GPU-/CPU-Mittelwert, VRAM/RAM, Score, Fehler, Tok/s, Elapsed und Rating.
- Ollama aggregiert: Qwen SQL/Python/Mixed `79,78/69,69/62,81`,
  DeepSeek `66,11/87,50/48,75`, Laguna `71,95/72,81/65,05`.
  Ollama-Durchsatz liegt typischerweise bei Qwen `33,94–36,71`,
  DeepSeek `38,05–41,76` und Laguna `45,89–51,81 Tok/s`.
- CUDA-Fork aggregiert: Qwen `57,67/40,16/42,66`, DeepSeek
  `67,59/79,84/47,66`, Laguna `70,87/78,91/77,88`. Fork-Durchsatz liegt
  bei Qwen `22,98–24,22`, DeepSeek `20,63–24,51` und Laguna
  `21,88–28,66 Tok/s`.
- Der Workbench-Runner wurde fork-spezifisch korrigiert: CUDA-DLL-Pfad
  wird an `llama-server` vererbt, inkompatible Upstream-Flags werden beim
  `ik_llama.cpp`-Fork nicht verwendet, und ein HTTP-500-Fallback nutzt
  dessen funktionierenden `/completion`-Endpoint. Dadurch sind jetzt
  echte Laguna-Forkwerte statt leerer Fehlerzeilen im Report enthalten.
- Die Fork-Parameterprüfung für Qwen3-Coder zeigte einen wichtigen
  Tuning-Punkt: `--cpu-moe` legt bei der 30B-A3B-GGUF rund 16,9 GiB Experten
  in den Host-Speicher und ließ im Smoke-Test nur etwa 1,6 Tok/s zu.
  `--n-cpu-moe 24` erreichte nur etwa 2,3 Tok/s. Beide Profile sind daher
  nicht als Optimierung übernommen; Expert-Platzierung bleibt automatisch.
  Explizit gesetzt werden nur `--flash-attn on`, Q8-K/V-Cache,
  `--threads-batch` und `--no-cont-batching`.
- Die aktuelle Fork-Dokumentation empfiehlt für Low-VRAM-MoE stattdessen
  `-ngl 99 --override-tensor exps=CPU`; fused MoE ist in diesem Build
  standardmäßig aktiv. `--attention-max-batch` wird begrenzt; `-rtr` soll
  bei hybriden K-Quants vermieden
  werden. Dieses dokumentierte Profil ist nun im Runner hinterlegt und
  wird nach der Gaming-Pause mit einem kontrollierten A/B-Smoke-Test gegen
  den automatischen Fit gemessen:
  <https://github.com/ikawrakow/ik_llama.cpp/blob/master/docs/parameters.md>.
- Für jeden lokalen Benchmark-Case erfasst `SystemMonitor` jetzt zusätzlich
  `avg/max_pcie_rx_mb_s` und `avg/max_pcie_tx_mb_s`. Die Werte kommen aus
  `nvidia-smi dmon -s t` und sind Momentanraten in MB/s, keine kumulierten
  Bytes. Damit sind Prompt-/Generierungsverkehr und Offload-Belastung
  backendübergreifend vergleichbar; Modell-Laden bleibt separat über
  `local_model_startup_sec` ausgewiesen.
- Erste valide PCIe-Messung auf `swe-sql-hard-24` (8 Cases, max. 128
  Ausgabetokens):
  - Upstream Qwen3.6-35B Q4: 97,5 % GPU, 11,8 GiB VRAM, ca. 12,65 GB/s
    RX und 3,44 GB/s TX, 5,34 Tok/s; damit wäre TB4 für diesen
    Voll-GPU-/K-Quant-Lauf klar zu langsam.
  - ik-Fork Qwen3.6-35B Q4 mit `exps=CPU`: 35,0 % GPU, 4,30 GiB VRAM,
    ca. 1,29 GB/s RX und 0,20 GB/s TX, 22,83 Tok/s. Das ist schneller,
    aber der niedrige GPU-Anteil zeigt, dass das Profil viel Rechenarbeit
    auf CPU verlagert und nicht automatisch die beste Gesamtkonfiguration
    ist.
  - ik-Fork Laguna Q4: 35,2 % GPU, 6,29 GiB VRAM, ca. 1,41 GB/s RX und
    0,18 GB/s TX, 26,40 Tok/s. Upstream-Laguna wurde separat als
    `llama-server not ready` erfasst.
  Die RX/TX-Werte sind beobachtete `dmon`-Momentanraten des internen
  Laptop-PCIe-Pfads, keine direkte TB4-Simulation; sie dienen als
  Workload-Obergrenze für die eGPU-Kaufprojektion.
- Die Bewertung trennt jetzt `Rating` und `Agent suitability`: `Rating`
  kombiniert Qualität, Elapsed-Performance und Zuverlässigkeit geometrisch
  (`Q^0,60 * P^0,25 * R^0,15`). `Agent suitability` nutzt zusätzlich die
  erwartete Zeit bis zum erfolgreichen Ergebnis (`elapsed / (Q * R)`), damit
  schnelle Korrekturschleifen gegen langsamere, stärkere Einzelantworten
  vergleichbar werden. Ein kritischer Fehler bleibt ein Hard-Gate.
- KAT-Coder-V2.5-Dev ist als dieselbe offizielle `bartowski`-Q4_K_M-GGUF
  in allen drei lokalen Backends geprüft (`ora-pg-py-33`, 11 Fälle,
  max. 128 Tokens). Ollama erreichte 73,38 % Qualität, 28,65 Tok/s und
  01:35 Laufzeit. Upstream-llama.cpp erreichte 74,51 %, 1,18 Tok/s,
  17:39, 99,2 % GPU und 11,52 GiB VRAM; der gemessene PCIe-Verkehr lag bei
  etwa 4,4 GB/s RX und 1,1 GB/s TX. Der ik-Fork mit dem 12-GB-Hybridprofil
  (`-ngl 99`, `exps=CPU`, Q8-K/V, Flash Attention, Batch-Limit,
  `--no-cont-batching`) erreichte 70,62 %, 16,88 Tok/s, 01:16, 34,9 %
  GPU, 3,72 GiB VRAM sowie etwa 0,16 GB/s RX und 0,13 GB/s TX. Damit ist
  der Fork in diesem Lauf deutlich schneller und eGPU-freundlicher, aber
  die geringere Qualitätswertung muss durch Coding-Gates weiter abgesichert
  werden. Rohdaten und Einzelreports liegen unter
  `benchmark_results\kat-coder-{ollama,upstream,fork}-20260804\`.

### Cross-backend mini-coder run (2026-08-03)

- The append-only result file is
  `benchmark_results\cross-backend-mini-20260803\mini_results.csv`;
  the readable table is `mini_results.md`.
- Qwen 3.6 35B A3B Q5 was imported into Ollama as
  `qwen3.6:35b-a3b-q5_K_M` and tested in both Ollama and the ik_llama
  runner. Laguna Q4/Q5/Q6 and the Daily-Coder candidates were also
  included in the Ollama pass.
- Siemens repeat testing completed successfully under
  `benchmark_results\siemens-repeat-20260803\`.
- The llama.cpp server logs for Q4/Q5 report
  `ggml_cuda_init: failed to initialize CUDA`; those rows are CPU fallback
  diagnostics and are not valid GPU evidence. A valid CUDA preflight is
  still required before ranking the fork.
- The fork was rebuilt in
  `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v132-installed` with the
  installed CUDA 13.2 toolset. The previous build had linked to a
  session-local `cuda-13.2.1-local` tree. The clean rebuild still reports
  `cuInit`/`ggml_cuda_init` failure, so the remaining blocker is runtime
  driver/device initialization, not the CMake toolkit path.
- A second fully consistent CUDA 13.3 build is now available at
  `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v133-installed`; its preflight
  produces the same `no CUDA-capable device` result. The issue is therefore
  independent of the 13.2/13.3 toolkit mismatch.
- The cross-backend runner now allows one hour per model, writes
  `mini_progress.log`, and aborts a streaming model after 300 seconds
  without a new token. The full llama.cpp matrix is intentionally waiting
  for a valid CUDA preflight; CPU fallback rows would be misleading.

### Daily-Coder-Mini-Gates: zehn lokale Kandidaten (2026-08-03)

- Kampagne `daily-coder-20260803` ist vollständig und seriell unter der
  gemeinsamen `local-llm`-Lease gelaufen. Persistierte Evidenz:
  `benchmark_results\agent-helper\daily-coder-20260803\` (SQLite-SSOT,
  Samples, Aggregate, Plan, Checkpoint und lokaler HTML-Bericht); der
  zusammengeführte Bericht liegt unter `benchmark_results\agent-helper\report.html`.
- Connect- und ausführbares Mini-Coding-Gate bestanden: `deepseek-coder-v2:16b`,
  `qwen3-coder:30b`, `qwen3.6:27b-q4_K_M`,
  `qwen3.6:35b-a3b-q4_K_M`, Laguna XS 2.1 Q4/Q5/Q6, `qwen3:32b` und
  `qwen2.5-coder:32b-instruct-q4_K_M`. Alle akzeptierten Kandidaten sind
  strukturell nur `gate-passed-provisional` mit `evidence_stage=gate_only`.
- Schnellste akzeptierte Mini-Coding-Proben (Zeit bis akzeptiert):
  Qwen 3.6 35B A3B Q4 `35,176 s` / `28,73 Tok/s`, DeepSeek Coder V2 16B
  `36,530 s` / `20,91 Tok/s`, Qwen3 Coder 30B `78,892 s` / `5,75 Tok/s`.
  Die weiteren akzeptierten Kandidaten lagen bei `100,236 s` (Qwen 3.6
  27B Q4), `132,770–195,307 s` (Laguna) oder langsamer.
- `devstral-small-2:24b` bestand zwar den Connect-Smoke, scheiterte aber
  im Mini-Coding-Gate mit `OLLAMA_GENERATE_FAILED` und ist deshalb
  `not-usable`/hart aus jeder Rangfolge ausgeschlossen.
- **Kein Daily-Coder ist entschieden.** Der Mini-Gate-Sieg von Qwen 3.6
  35B Q4 ist ein guter Finalistenhinweis, aber kein Eignungsnachweis:
  Bug Review, SQL-Migration, Frontend, Architektur, Multi-Turn-Korrektur
  sowie echte Reviewer-/Kollaborations-Evidenz fehlen noch.

### llama.cpp-Fork, CUDA und Reporting (2026-08-03)

- Der performance-orientierte Fork
  [`ikawrakow/ik_llama.cpp`](https://github.com/ikawrakow/ik_llama.cpp) ist
  unter `C:\Users\z000g9hu\llama.cpp-ik` auf Commit `cb9147f` geklont.
  Gebaute Ziele sind `llama-bench.exe` und `llama-server.exe`.
- Der erste Build war trotz `GGML_CUDA=ON` faktisch CPU-only: sein
  CMake-Cache enthielt `CUDAToolkit_NVCC_EXECUTABLE-NOTFOUND`; die
  Bench-JSON meldete deshalb `cuda=false` und `gpu_blas=false`. Die darin
  ermittelten 93,14 Prompt-Tok/s und 10,15 Generate-Tok/s sind **ungültig**
  und duerfen nie verglichen oder berichtet werden.
- CUDA 13.2 ist jetzt im Standardpfad
  `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2` installiert.
  Zusätzlich existiert fuer diese Session ein isolierter, erfolgreich
  konfigurierter CUDA-13.2.1-Fallback. Der CUDA-13.2-Fork-Build liegt unter
  `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v132-local`.
- Aktueller Blocker: Der NVIDIA Studio Driver `596.86` meldet per
  `nvidia-smi` zwar die RTX 3500 Ada Laptop GPU und CUDA 13.2, aber die
  direkte CUDA-Driver-API liefert `cuInit=100` und `deviceCount=0`.
  Das ist ein Treiber-/Neustartzustand, kein Fork- oder Modellfehler.
  Nach Abschluss der CUDA/Nsight-Installation Windows neu starten und
  zuerst diese Prüfung ausführen:

  ```powershell
  nvidia-smi
  ```

  Anschließend nur bei sichtbarer CUDA-GPU den reproduzierbaren Hybridlauf
  starten:

  ```powershell
  $cuda = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2'
  $env:Path = "$cuda\bin\x64;$cuda\bin;$env:Path"
  C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v132-local\bin\Release\llama-bench.exe `
    -m C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf `
    -p 512 -n 128 -r 3 -ngl 999 -fa 1 -ctk q8_0 -ctv q8_0 -t 16 `
    -ot "blk\.[0-9]+\.ffn_(up|down|gate)_exps\.weight=CPU" -o json
  ```

  Der echte Lauf erfolgt ausschliesslich unter der kanonischen
  `local-llm`-Lease und schreibt sein JSON in
  `benchmark_results\ik-llama-qwen35-q4-20260803\`.
- Die Berichtssichten sind getrennt: `benchmark_report.html`/`.md` und
  `benchmark_run_summary.csv` enthalten exakt eine kompakte Zeile je
  abgeschlossenem Lauf; `benchmark_live_status.html`/`.md` zeigt die breite
  operative Planung mit Fortschritt und ETA. Die Live-Sicht wird nach jeder
  gespeicherten Probe aktualisiert.
- Der Fork misst zunaechst nur Prompt-/Generate-Durchsatz. Er ist kein
  Daily-Coder-Nachweis: dafür sind anschließend dieselben Coding-Gates wie
  beim Ollama-Track nötig. Eine Daily-Coder-Empfehlung existiert noch nicht.

### Runtime-Guard und Neustart (2026-08-02)

- `C:\GIT\standards\scripts\ai_runtime.py` ist der gemeinsame Guard fuer
  Kontextbudget, Nutzungsmetadaten und lokale Blocker. OpenCode ist auf 98.304
  Kontext- und 8.192 Output-Tokens begrenzt; ab 65.536 Tokens ist ein
  Phasenabschluss faellig, ab 98.304 Tokens eine neue Session.
- Lokale OpenCode-Worker starten nur ueber
  `C:\GIT\standards\scripts\start_ai_worker.ps1`; der Wrapper nutzt die
  kanonische `local-llm`-Lease und blockiert bei manuellem Blocker oder `TL.exe`.
  Siemens-Worker bleiben ohne lokale GPU-Last verfuegbar.
- `agent_helper_eval\local_lock.py` prueft vor lokalen Benchmark-Leases dieselbe
  Runtime-Policy. Die schnelle Gesamtsuite lief nach der Integration mit
  279/279 Tests erfolgreich.
- Nach dem NVIDIA-Neustart zuerst
  `python C:\GIT\standards\scripts\ai_runtime.py status` ausfuehren. Erst wenn
  keine Blocker und keine Fremdlease sichtbar sind, lokale Modellarbeit planen.
- Fuer manuelles Gaming `C:\GIT\standards\scripts\ai-runtime-control.cmd`
  doppelklicken und im Menue pausieren oder fortsetzen; keine Parameter merken.

### Laufbedingung: Energieprofil (2026-08-02)

- Alle bisherigen lokalen Messungen dieser Phase liefen unter dem von Windows
  gemeldeten Profil `Ausbalanciert`
  (`381b4222-f694-41f0-9685-ff5bb260df2e`).
- Diese Werte bleiben als Ausbalanciert-Evidenz erhalten, duerfen aber nicht
  mit einer spaeteren Finalrunde unter einem anderen Leistungsprofil vermischt
  werden.
- Vor dem naechsten Finalistenlauf das aktive Windows- und gegebenenfalls
  Lenovo-Leistungsprofil erfassen und fuer die gesamte Vergleichsrunde
  unveraendert lassen.

### Laufbedingung: NVIDIA-Treiberwechsel (2026-08-02)

- Bisherige lokale Smoke- und Vergleichsläufe dieser Phase verwendeten den
  NVIDIA-Treiber `610.82`.
- Der Nutzer installiert anschliessend einen konservativeren NVIDIA
  Studio-Treiber. Messungen vor und nach dem Wechsel sind getrennte
  Baselines; keine Durchsatz-, Stabilitaets- oder Thermikwerte vermischen.
- Erst nach Abschluss der Installation und einem vollstaendigen Neustart
  Hardware-, Treiber- und Leistungsprofil erneut erfassen. Bis dahin keine
  weiteren lokalen Modellläufe starten.

### Agent-Helper-Harness

Implementiert unter `scripts\agent_helper_eval\`:

- SQLite je Kampagne als SSOT;
- `agent_helper_samples.csv`, `agent_helper_aggregates.csv` und
  `agent_helper_capacity_profile.csv`;
- harte Akzeptanzgates gegen fehlerhafte, unsichere, unvollständige oder
  placeholderhaltige Ausgaben;
- Messung von LLM/API-, Tool-/Test-, Orchestrator- und Wartezeit;
- CPU/RAM/GPU/VRAM, Tokens, TPS, TTFT und Time-to-accepted-result;
- historische CSV-Adapter mit expliziter Kennzeichnung alter
  Keyword-Scores als nicht deterministische Heuristik;
- vollständig offlinefähiger DE/EN-HTML-Report mit KPI-Karten und
  datengetriebenen Diagrammen;
- Ollama-Inventar, Max-ein-lokales-Modell-Lock, Connect-/Mini-Gates,
  serieller Runner, Checkpoints und Resume.

Letzte Validierung:

```text
python -m unittest test_agent_helper_eval
Ran 273 tests
OK
```

### Reale Pilot-Evidenz

`agent-helper-pilot-20260801`:

- Qwen3 Coder 30B Connect-Gate: bestanden.
- Qwen3 Coder 30B Mini-Coding-Gate: 10/10 ausführbare Tests bestanden.
- Einstufung bewusst nur `gate-passed-provisional`, nicht Daily Runner.

`agent-helper-serial-pilot-20260801`:

- Connect-Gate für Qwen3 Coder 30B bereits erfolgreich persistiert.
- Mini-Gate stürzte ursprünglich wegen eines 226 Zeichen langen
  Windows-Workspacepfads ab.
- Behoben durch kurze Hash-Verzeichnisse, Gate-Exception-Persistenz und
  atomare Checkpoints vor/nach jedem Gate.
- Vorhandene Kampagnenevidenz blieb erhalten.

### Laufende Q5/Q6-Installation

Ein losgelöster Hintergrundprozess installiert seriell:

1. `qwen3.6:35b-a3b-q5_K_M`
2. `qwen3.6:35b-a3b-q6_K`
3. `laguna-xs-2.1:q5_K_M`
4. `laguna-xs-2.1:q6_K`

Quellen:

- Qwen: `unsloth/Qwen3.6-35B-A3B-GGUF`
- Laguna: `bartowski/Laguna-XS-2.1-GGUF`

Lokale Quantisierung aus Qwen-BF16 ist mit dem vorhandenen llama.cpp
nicht möglich:

```text
qwen35moe.rope.dimension_sections has wrong array length; expected 4, got 3
```

Deshalb werden fertige GGUFs geladen. Der erste Download wurde nach
einem Verbindungsreset mit HTTP-Range fortgesetzt. Stand beim Handover:
Qwen Q5 ist vollständig heruntergeladen und wird noch importiert oder
steht unmittelbar vor dem Import. Keine höhere Quantisierung war zu
diesem Zeitpunkt bereits sicher in `ollama list` sichtbar.

**Keine lokalen Performance-Messungen während Download/Import**, weil
I/O-Last die Ergebnisse verfälscht.

Prüfung in der neuen Session:

```powershell
ollama ps
ollama list | Select-String -Pattern 'qwen3.6.*q[56]|laguna.*q[56]'
Get-ChildItem "$env:USERPROFILE\.ollama\imports\higher-quants" -File |
  Select-Object Name, Length, LastWriteTime
```

## Exakte Wiederaufnahme

### 1. Kontext laden

```powershell
Set-Location C:\GIT\llm-evaluation-workbench
Get-Content C:\GIT\AGENTS.md
Get-Content .\AGENTS.md
Get-Content .\docs\project\handover.md
Get-Content .\docs\project\todo.md
```

### 2. Zustand und Tests prüfen

```powershell
ollama ps
git status --short
Set-Location .\scripts
python -m unittest test_agent_helper_eval
```

### 3. Erst nach abgeschlossener Quantisierungsinstallation Pilot fortsetzen

```powershell
Set-Location C:\GIT\llm-evaluation-workbench\scripts
python .\run_agent_helper_campaign.py serial-execute `
  --campaign-id agent-helper-serial-pilot-20260801 --confirm `
  --models "qwen3-coder:30b,deepseek-coder-v2:16b,phi4-mini:3.8b-q4_K_M,rnj-1:8b"
```

Der Runner muss das bereits erfolgreiche Qwen-Connect-Gate überspringen,
nur dessen fehlendes Mini-Gate nachholen und danach die drei Baselines
seriell ausführen.

### 4. Moderne Daily-Runner-Kandidaten priorisieren

Nach dem Baseline-Pilot zuerst:

- Qwen 3.6 35B A3B Q4/Q5/Q6;
- Qwen 3.6 27B Dense Q4;
- Laguna XS 2.1 Q4/Q5/Q6.

Weitere ältere/kleine Modelle dienen primär als Baselines. Kein Modell
wird allein wegen hoher TPS empfohlen.

## Getrennte parallele Arbeitsbereiche

- Ein anderer Agent arbeitet am Command Center, Cloud-/Tool-Evaluationen
  und zusätzlichen Offline-Benchmarksets.
- Diese Session besitzt die lokalen Ollama-/llama.cpp-Läufe.
- Austausch erfolgt über versionierte Benchmarkkataloge mit Provenienz,
  nicht durch parallele Änderungen derselben Dateien.
- Command-Center-Repositories hier nicht bearbeiten.
- Das ITSM-PoC liegt separat unter `C:\GIT\itsm-platform-poc`; dessen
  eigener Wiederaufsetzpunkt ist
  `C:\GIT\itsm-platform-poc\docs\project\handover.md`.

## Bekannte Risiken

- Der Worktree war bereits vor dieser Arbeit umfangreich dirty. Keine
  fremden Änderungen verwerfen.
- `.github\copilot-instructions.md` ist als gelöscht markiert; diese
  Session hat die Löschung nicht verursacht.
- Viele Benchmarkdefinitionen und Reports sind untracked. Vor einem
  Commit Herkunft und Scope einzeln prüfen.
- Alte `quality_score`-Werte sind überwiegend Keyword-/Regex-Heuristik,
  keine ausführbare Korrektheit.
- GitHub-Copilot-Agenten liefern nicht zwingend Tokens/s oder interne
  Modellzeiten; solche Werte bleiben `N/A`.
- Der generierte HTML-Report ist Evidenzdarstellung, kein Ersatz für
  deterministische Tests und Orchestrator-Review.

## Kopierfertiger Prompt für die neue Terra-Session

```text
Arbeite ausschließlich am LLM-Agent-Helper- und Benchmark-Track in
C:\GIT\llm-evaluation-workbench. Lies zuerst C:\GIT\AGENTS.md,
C:\GIT\llm-evaluation-workbench\AGENTS.md,
docs\project\handover.md und docs\project\todo.md vollständig.

Setze den dokumentierten Zustand fort, ohne fremde dirty Änderungen zu
verwerfen, ohne Commit/Push und ohne Command-Center-Repositories zu
bearbeiten. Prüfe zuerst, ob der losgelöste Q5/Q6-Download/Import für
Qwen 3.6 35B A3B und Laguna XS 2.1 abgeschlossen ist. Solange Download
oder Import läuft, keine lokalen Performance-Benchmarks starten.

Validiere danach die Agent-Helper-Tests. Setze
agent-helper-serial-pilot-20260801 mit dem im Handover dokumentierten
Resume-Befehl fort. Bewerte Ergebnisse ausschließlich über harte
Korrektheits-/Sicherheits-/Vollständigkeitsgates; Geschwindigkeit darf
schlechte Qualität niemals kompensieren.

Priorisiere anschließend als Daily-Runner-Kandidaten Qwen 3.6 35B
A3B Q4/Q5/Q6, Qwen 3.6 27B Dense Q4 und Laguna XS 2.1 Q4/Q5/Q6.
Lokale Modelle immer strikt seriell laden und entladen. Nutze für
abgegrenztes Coding bevorzugt das Siemens-API-Modell Qwen 3.6 27B, um
Copilot-Quota zu sparen; GPT-5.6/Terra bleibt für Architektur,
Orchestrierung und Review.

Halte SQLite, Sample-/Aggregate-CSV und den offlinefähigen DE/EN-HTML-
Report konsistent. Dokumentiere gemessene Werte, Annahmen und
5090-Projektionen getrennt. Aktualisiere Handover und Todo nach jedem
stabilen Meilenstein.
```
# Benchmark reporting contract (binding)

All benchmark runners use one universal CSV contract. Detail CSVs retain every
sample (`sample_id`, `sample_name`) and exact `score`, `elapsed`, `errors`,
numeric `rating`, qualitative `agent_suitability`, `io_read`, and `io_write`.
Summary CSVs contain one aggregate row per benchmark run, grouped by benchmark,
backend, and model, with the same run-level metrics. IO values come only from
observable system/process monitoring; unavailable values are empty/`N/A`, never
fabricated. HTML is an aggregate dashboard (current planned/status series first,
completed-run history second); detailed CSV remains authoritative for inspection.
