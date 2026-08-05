# Runbook: LLM Benchmark Campaign

## Verbindlicher CSV-Vertrag

Für **jeden** Benchmark-Run gilt ein universelles CSV-Format: Die Detail-CSV
enthält jede Probe mit `sample_id`/`sample_name`, `score`, `elapsed`,
`errors`, numerischer `rating`, qualitativer `agent_suitability` sowie
`io_read` und `io_write`. Die aggregierte Summary-CSV enthält genau eine Zeile
je Benchmark-Run (gruppiert nach Benchmark, Backend und Modell) und dieselben
Run-Metriken einschließlich der IO-Summen. IO wird über die vorhandene
Prozess-/Systemüberwachung erfasst; wenn der Backend keine belastbare Messung
liefert, bleibt das Feld explizit leer bzw. `N/A` (niemals schätzen).
Die Detail-CSV bleibt die exakte Prüfspur; HTML zeigt nur Run-Aggregate.

### ik_llama.cpp-Profile und MTP

ik_llama.cpp wird über denselben Benchmark-Einstiegspunkt wie alle anderen
lokalen Backends getestet. Das Profil `baseline` erzwingt den bisherigen
stabilen vollständigen CPU-MoE-Offload. Für Performance-Vergleiche stehen
zusätzlich `fit` (automatisches VRAM-Fitting) und `n-cpu-moe` (nur die ersten
N MoE-Layer im CPU-Speicher) zur Verfügung:

```powershell
python .\scripts\llm_migration_benchmark.py `
  --llama-server "C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v133-clean\bin\Release\llama-server.exe" `
  --ik-offload-profile fit --llama-fit-target-mib 1664 `
  --ik-spec-type "mtp:n_max=1,p_min=0.0"
```

Die kanonische ik-Syntax für speculative decoding ist `--spec-type`; die
alten Optionen `--multi-token-prediction`, `--draft-p-min` und `--draft-max`
gehören nicht zum geprüften Commit und dürfen nicht erfunden oder als
unterstützt dokumentiert werden. `mtp` funktioniert nur mit einem GGUF, das
einen MTP-Head enthält. Fehlt dieser Head, wird der Lauf als nicht
vergleichbar/fehlgeschlagen protokolliert; ein normales Q4-GGUF wird nicht
nachträglich als MTP-Modell behandelt.

### RTX 3500 Ada / 12-GB-VRAM: bekannte Fehlkonfigurationen

Auf dem ThinkPad P16 Gen 2 sind nominell 12282 MiB VRAM vorhanden, aber
Windows, der Desktop-Compositor, Treiber-Reserven und CUDA belegen davon
typischerweise 1--2 GiB. Das Ziel ist deshalb **nicht**, 12282 MiB zu
erzwingen, sondern unter Last ungefähr 10--10.5 GiB für das Modell und KV
Cache zu verwenden. Für lange OpenCode-Sitzungen bleiben mindestens etwa
1.5--2 GiB frei.

Die bisherigen Fehlkonfigurationen waren:

| Einstellung | Beobachtung | Konsequenz |
|---|---|---|
| `--ik-offload-profile baseline` / `--override-tensor exps=CPU` | nur ca. 3.8 GiB VRAM, ca. 19.3 Tok/s | stabil, aber die MoE-Experten werden unnötig aus dem GPU-Speicher herausgehalten |
| `--ik-offload-profile n-cpu-moe --ik-n-cpu-moe 16` | ca. 11.5 GiB VRAM, PCIe-Transfers und nur ca. 7.8 Tok/s | kein gutes Profil: Expertentransfers erzeugen Transfer-Thrashing |
| `--llama-ngl 0` oder fehlendes `--llama-ngl` | GPU-Offload ist deaktiviert bzw. nicht reproduzierbar | für jeden GPU-Vergleich `--llama-ngl 100` explizit setzen |
| `--fit-margin 2048` als Einzelentscheidung | ca. 9.9 GiB, aber ein Ausreißer mit ca. 13.7 Tok/s | nicht aus einem Einzelrun als Standard ableiten; mindestens drei Wiederholungen |
| volles `f16`-KV bei großem Kontext | unnötig hoher VRAM-Bedarf und spätere OOM-Gefahr | für 12 GiB `q8_0`-KV und zunächst 32768 Kontext verwenden |

`fit` ist das bevorzugte Startprofil. Auf dieser GPU ist
`--fit-margin 1664` der bisher beste reproduzierte Kompromiss (KAT:
ca. 10.26 GiB und ca. 25.3 Tok/s). Ein niedrigerer GPU-Auslastungswert als
90 % ist dabei kein Fehlerbeweis: Decode ist häufig speicherbandbreiten- oder
CPU-/PCIe-limitiert. Entscheidend sind Tok/s, VRAM-Stabilität, PCIe-Spitzen
und fehlende OOM-/Paging-Ereignisse. 90 % GPU-Auslastung darf als Diagnose-
signal aufgezeichnet werden, aber nicht als Zielwert erzwungen werden.

Empfohlenes reproduzierbares Startprofil für diesen Rechner:

```powershell
--ctx-size 32768 --llama-ngl 100 --threads 16 `
--llama-batch-size 512 --llama-ubatch-size 128 `
--ik-offload-profile fit --llama-fit-target-mib 1664
```

Für OpenCode mit wachsendem Kontext sind danach separate 8192/32768/65536-
und 131072-Tests nötig. Ein erfolgreicher Kurzlauf beweist keine
Langzeitstabilität. Modelle, die deutlich größer als der verfügbare VRAM
sind (z. B. Q8/BF16), werden nicht als VRAM-stabile Daily-Runner empfohlen:
sie können zwar in 200 GiB RAM laden, verursachen aber CPU-Offload und
langsames Paging/Transferverhalten.

### ik_llama.cpp unter Windows mit CUDA bauen

Den Fork immer in einem eigenen Build-Verzeichnis bauen; funktionierende
Builds wie `build-cuda-v133-clean` nicht überschreiben. Vor dem Build muss
`nvcc` aus derselben CUDA-Installation gefunden werden, die später auch im
DLL-Pfad des Servers liegt.

```powershell
cd C:\Users\z000g9hu\llama.cpp-ik
$env:PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin\x64;" + $env:PATH
$build = "build-cuda-v133-<name>"

cmake -S . -B $build -G "Visual Studio 17 2022" -A x64 `
  -DGGML_CUDA=ON `
  -DCMAKE_BUILD_TYPE=Release `
  -DCUDAToolkit_ROOT="C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3"
cmake --build $build --config Release --target llama-server llama-bench --parallel 8

$env:PATH = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin\x64;" + $env:PATH
& ".\$build\bin\Release\llama-server.exe" --version
& ".\$build\bin\Release\llama-server.exe" --help |
  Select-String "fit|fit-margin|n-cpu-moe|spec-type"
```

Abnahmebedingungen: `--version` muss den erwarteten Fork-Commit nennen,
`--help` muss die kanonischen ik-Optionen zeigen, und der Startlog muss
CUDA/GPU-BLAS bestätigen. Ein Build mit
`CUDAToolkit_NVCC_EXECUTABLE-NOTFOUND`, `cuda=false` oder `gpu_blas=false`
ist CPU-only und darf nicht in den GPU-Vergleich eingehen.

### Schema, Granularität und Spalten

Es gibt zwei verbindliche Körnungen, aber keinen dritten proprietären
Ergebnisexport:

- **Sample-/Detail-CSV:** genau eine Zeile je ausgeführtem
  `benchmark_run_id + backend + model + case_id + run`. Auch Fehler,
  Timeouts und übersprungene Samples werden als Zeile gespeichert.
- **Run-/Summary-CSV:** genau eine Zeile je abgeschlossenem oder aktuell
  laufendem `benchmark_run_id + backend + model + benchmark_name`. Diese
  Zeile wird aus der Detail-CSV aggregiert; Zahlen dürfen im HTML nicht
  unabhängig davon neu erfunden werden.

Die verbindliche Detail-/History-Reihenfolge des Migrationsformats ist
(History ergänzt vorne `source_csv`; alle übrigen Spalten sind identisch):

```text
benchmark_run_id, schema_version, benchmark_name, benchmark_spec_version,
benchmark_script_file, benchmark_task_count, benchmark_runs,
benchmark_task_ids, benchmark_task_hash, backend, model, llm_size_bytes, sample_id,
sample_name, case_id, case_title, run, wall_ms, elapsed_p50_ms,
elapsed_p95_ms, prompt_tokens,
output_tokens, output_tps, quality_score, keyword_hits, keyword_total,
forbidden_hits, avg_cpu_pct, max_cpu_pct, avg_mem_pct, max_mem_pct,
avg_gpu_pct, max_gpu_pct, avg_vram_used_mb, max_vram_used_mb,
avg_pcie_rx_mb_s, max_pcie_rx_mb_s, avg_pcie_tx_mb_s, max_pcie_tx_mb_s,
io_read, io_write, local_model_startup_sec, local_model_shutdown_sec,
cpu_time_sec, recorded_at, status, run_started_at, run_finished_at,
hardware_profile, provenance, launch_profile, server_executable, model_path,
launch_params, rating, agent_suitability, output_preview, error, provider,
benchmark_display, datetime_run_started, last_update, elapsed, wall_s, samples,
vram_free_gb, free_vram_gb, rating_score, interpretation
```

`migration_llm_bench_history.csv` beginnt zusätzlich mit `source_csv`.
`elapsed_p50_ms` und `elapsed_p95_ms` sind auf Sample-Zeilen leer und werden
in Run-Summaries aus erfolgreich gemessenen `wall_ms`-Werten berechnet.

Die verbindliche Run-/Summary-Reihenfolge ist:

```text
schema_version, status, run_started_at, run_finished_at, hardware_profile,
provenance, launch_profile, server_executable, model_path, launch_params,
llm_size_bytes, provider, benchmark_display, datetime_run_started, last_update,
wall_s, samples, vram_free_gb, interpretation, date_time, backend, model, benchmark_name, samples_x_n,
avg_gpu_percent, avg_cpu_percent, vram_gb,
pcie_rx_avg_mb_s, pcie_rx_max_mb_s, pcie_tx_avg_mb_s, pcie_tx_max_mb_s,
io_read, io_write, ram_gb, score, rating_score, errors,
tokens_per_second, elapsed, elapsed_p50_ms, elapsed_p95_ms, rating,
expected_success, agent_suitability
```

`sample_id` ist stabil und eindeutig; `sample_name` ist der lesbare
Benchmark-/Taskname. `score`/`quality_score` beschreibt die fachliche
Qualität, `rating_score` ist die normalisierte Gesamtbewertung des Runs,
`rating` die lesbare Einstufung und `agent_suitability` die separate
Agenten-Einschätzung. `elapsed`/`wall_ms` sind End-to-End-Wandzeit,
`errors` zählt technische Fehler, `io_read`/`io_write` sind gemessene Bytes
des lokalen Modellprozesses. `free_vram_gb`/`vram_free_gb` werden im selben `nvidia-smi`-Sample wie
`avg_vram_used_mb` gemessen. Historische Werte werden niemals aus
`Gesamt-VRAM - benutzt` abgeleitet. Nicht messbare Werte bleiben `N/A` bzw. leer;
`0` ist nur ein tatsächlich gemessener Nullwert. PCIe-Werte sind
Momentanraten in MB/s und tragen deshalb ihre Einheit im Spaltennamen.
`launch_params` ist ein sortiertes JSON-Objekt mit Kontextgröße, Threads,
Batch-/Ubatch-Größe, GPU-Layern, KV-Cache-Typen, Flash-Attention,
Continuous-Batching, Offload-Profil, Fit-Margin, `n-cpu-moe`, MTP-
`spec_type` und Speculative-Autotuning. Damit bleibt nachvollziehbar, ob
ein Modell über Ollama, Upstream-llama.cpp oder ik_llama.cpp mit MTP oder
ohne MTP gestartet wurde.

Alle neuen Spalten werden ausschließlich additiv ergänzt. Reihenfolge,
Bedeutung und Einheiten bleiben stabil; eine inkompatible Änderung erhält
eine neue `benchmark_spec_version`. Historische CSVs werden nur lesbar
importiert und nicht stillschweigend umgedeutet.

Diese Spalten sind additive Erweiterungen. Beim ersten Schreiben werden alte
History-Dateien migriert, indem fehlende Spalten leer ergänzt werden; unbekannte
historische Spalten bleiben erhalten. Historische Messwerte werden nicht
rückwirkend erfunden.

### Top-level dispatcher

Der kanonische Einstieg ist beispielsweise:

```powershell
python .\scripts\run_benchmark.py migration --backend ollama --runs 1
python .\scripts\run_benchmark.py agent-helper dry-run --campaign-id dry-run-<date>
```

`run_benchmark.py` wählt nur Suite/Runner und delegiert alle weiteren
Argumente. Die spezialisierten Skripte bleiben absichtlich bestehen: Sie haben
unterschiedliche Fixtures, Sicherheitsgates, CLI-Verträge und Ergebnisformate
und werden weiterhin direkt für Tests, Wiederaufnahme und fokussierte Läufe
benötigt.

### Unified campaign entrypoint (kanonisch)

Für neue Benutzer ist `scripts\run_benchmark.py` der einzige Einstieg. Er
akzeptiert mehrere Suite-IDs (Komma-getrennt), `ollama`, `llama_cpp` oder
`both`, explizite Modellnamen und fnmatch-Muster wie `*qwen*`. Ollama-Muster
werden ausschließlich über `GET /api/tags` aufgelöst; bei Nichterreichbarkeit
gibt es keinen stillen Fallback. llama.cpp wird als `NAME=PFAD` angegeben.
Nach jeder Suite werden dieselbe Detail-CSV, Summary-CSV und das dynamische
HTML-Dashboard aktualisiert.

```powershell
python .\scripts\run_benchmark.py `
  --suites migration,ora-pg-py-33 `
  --backend both `
  --models "*qwen*,deepseek-coder-v2:16b" `
  --llama-model "qwen35=C:\MODELLE\qwen35.gguf" `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --output "C:\GIT\llm-evaluation-workbench\benchmark_results\unified_benchmark_detail.csv"
```

Alle Optionen können in TOML unter `[benchmark]` stehen. Ohne CLI-Parameter
wird `config/benchmark.toml` verwendet (Beispieldatei im Repository):

```toml
[benchmark]
suites = "migration,ora-pg-py-33"
backend = "ollama"
models = ["*qwen*", "deepseek-coder-v2:16b"]
ollama_url = "http://127.0.0.1:11434"
runs = 1
detail_csv = "benchmark_results/unified_benchmark_detail.csv"
run = "resume" # resume | force
resume = "auto"
pause_file = "pause.ini"
stop_file = "stop.ini"
lock_file = ".benchmark_master.pid"
```

Ohne Parameter in einem interaktiven Terminal öffnet sich die Textual-
Workbench. Ein expliziter automatisierter Lauf verwendet `--config`, zum
Beispiel:

```powershell
python .\scripts\run_benchmark.py --config .\config\benchmark.toml
```

`--interactive` öffnet weiterhin den dependency-freien ANSI-/Nummern-Dialog.
Die vorhandenen spezialisierten Runner bleiben interne Implementierungen und
werden vom Master delegiert.

#### Pause, Gaming und Resume

`pause.ini` ist der Pause-Schalter. `python .\scripts\run_benchmark.py --pause`
legt ihn an; ein aktiver Runner wird beendet, Ollama wird entladen und der
Master wartet. Nach dem Löschen wird aus der Detail-CSV und den
`*_inprogress.csv` anhand der geplanten `sample_id`s fortgesetzt.
`stop.ini` ist dagegen endgültig: `--stop` beendet nur den aktiven Master samt
seiner eigenen Runner-Prozesshierarchie, entlädt Ollama und beendet die Suite.
Ein PID-/JSON-Lock verhindert doppelte Master; verwaiste Steuerdateien werden
beim Start gemeldet und sicher bereinigt.

`run = "force"` startet ohne Löschen bestehender Nutzerdaten mit einer neuen,
zeitgestempelten Detail-CSV. `--list-models` zeigt installierte Ollama-Tags und
`--pull-ollama NAME` zieht nur den ausdrücklich genannten Tag. Für llama.cpp
akzeptiert `--llama-install NAME=PFAD` ausschließlich eine vorhandene GGUF;
es gibt keine erfundenen URLs oder stillen Downloads. Fehlt ein Programm,
werden Installationshinweise ausgegeben.

Ein Rechnerabsturz hat denselben vorgesehenen Wiederanlauf: den gleichen
Master-Befehl erneut starten, dieselbe Detail-CSV und dasselbe
`.master_runs`-Verzeichnis beibehalten. Ein explizites `--resume off` beginnt
bewusst einen neuen Lauf.

#### HWiNFO (bewusst opt-in)

Es werden keine HWiNFO-Schalter oder undokumentierten Parameter erfunden.
Optional können exakt die vom Benutzer getesteten Start-/Stop-Kommandos
konfiguriert werden. `{executable}` wird durch `hwinfo_executable` ersetzt:

```toml
[benchmark]
hwinfo_executable = "C:\\Tools\\HWiNFO64.exe"
hwinfo_start_command = '"{executable}" <documented-start-arguments>'
hwinfo_stop_command = '"{executable}" <documented-stop-arguments>'
```

Die Kommandos werden vor bzw. nach jeder Suite ausgeführt; ohne beide
Kommandos findet keine HWiNFO-Aktion statt. Die konkreten Argumente müssen aus
der installierten HWiNFO-Version bzw. deren eigener Dokumentation stammen.
Exit-Codes des Stop-Kommandos werden bewusst nur protokolliert, weil HWiNFO
je nach Version/Privilege-Level unterschiedliche CLI-Unterstützung hat.

### HTML-Erzeugung

`scripts\llm_migration_benchmark.py` ist der kanonische Exporter. Nach jedem
persistierten Sample schreibt er Detail-CSV, JSON, History-CSV,
Summary-CSV sowie `report.html` und `benchmark_live_status.html`. Das HTML
zeigt oben die geplante/laufende Serie mit Status und Fortschritt, darunter
die aggregierte Run-Historie. Samples gehören ausschließlich in die
Detail-CSV; die Run-Tabelle gruppiert und sortiert nach Benchmark, Backend und
Modell. Der Agent-Helper verwendet dieselben Körnungen über
`scripts\agent_helper_eval\schema.py`; seine zusätzlichen Prüffelder sind
additive, schema-versionierte Erweiterungen und werden ebenfalls aus dem
Sample-Export aggregiert.

## Voraussetzungen

- Windows mit PowerShell
- Python + `psutil`
- Optional:
  - Ollama lokal
  - llama.cpp (llama-cli oder llama-server)
  - GGUF-Modelle lokal

## 1) Umgebung

```powershell
pip install -r requirements.txt
```

## 2) Lokale Modelle in OpenCode

Einmalig synchronisieren, Startup-Verknüpfung anlegen und den llama.cpp-Router starten:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_llama_router_startup.ps1 -StartNow
```

Danach erscheinen zwei getrennte Provider:

- `ollama/<tag>` für alle lokal installierten Ollama-Tags;
- `llama-cpp/<alias>` für alle vollständigen GGUF-Modellsätze.

Ollama lädt Modelle nativ bei der ersten Anfrage. `llama-server` läuft auf `127.0.0.1:8080`
im Routermodus, lädt das angeforderte GGUF-Modell automatisch und begrenzt gleichzeitig
geladene Modelle mit `--models-max 1`. Cloud-Tags und unvollständige GGUF-Shards werden nicht
registriert. Nach Änderungen am Modellbestand erneut synchronisieren:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_llama_router_startup.ps1 `
  -StartNow -Restart
```

Das aktualisiert die OpenCode-Konfiguration und startet den llama.cpp-Router mit dem neuen
Preset neu. Eine bereits laufende OpenCode-Sitzung anschließend ebenfalls neu starten.

Bei jeder Synchronisation erhalten lokale Ollama-Tags persistent mindestens den vom Modell
deklarierten Maximalkontext. Bereits konfigurierte größere Sonderwerte bleiben erhalten.
GGUF-Presets verwenden ebenfalls ihren Metadaten-Maximalwert sowie Flash Attention und einen
Q4-KV-Cache. Große Kontexte erhöhen RAM-Bedarf und Prompt-Latenz und können den automatisch
ermittelten GPU-Anteil reduzieren.
Der Installer hinterlegt Flash Attention und Q4-KV-Cache auch für künftige Ollama-Starts;
bei einer erstmaligen Einrichtung dafür Ollama anschließend neu starten.

Nur die Ollama-Kontexte lassen sich unabhängig erneut anwenden:

```powershell
python .\scripts\configure_ollama_max_context.py
```

Für interaktives Coding auf einer GPU mit 12 GB VRAM wird ein 32k-
Betriebskontext für alle lokalen Ollama-Tags empfohlen. Das reduziert den
KV-Cache, ohne den nativen Modellkontext zu verändern; die Original-
Modelfiles werden vor der Änderung gesichert:

```powershell
python .\scripts\configure_ollama_max_context.py --max-context 32768
```

Die Synchronisation verwaltet die kompletten Modelllisten der Provider `ollama` und
`llama-cpp`; manuelle Einträge innerhalb dieser beiden Listen werden ersetzt. Andere Provider,
Optionen und Zugangsdaten bleiben unverändert.

## 3) Ollama-only

```powershell
python .\scripts\llm_migration_benchmark.py --backend ollama --runs 1
```

## 4) llama.cpp-only (Server-Modus empfohlen)

```powershell
python .\scripts\llm_migration_benchmark.py `
  --backend llama_cpp `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --llama-model "qwen36_35b_q4=C:\MODELLE\Qwen3.6-35B-Q4.gguf" `
  --runs 1
```

### 4.1) ik_llama.cpp CUDA-Hybridprofil fuer Qwen 3.6 35B A3B Q4

Der Fork `ikawrakow/ik_llama.cpp` liegt unter
`C:\Users\z000g9hu\llama.cpp-ik`. Fuer 12-GB-VRAM wird die Attention/KV
auf die GPU ausgelagert, die MoE-Expertentensoren bleiben im CPU-RAM. Vor
jeder Ausführung die gemeinsame `local-llm`-Lease nutzen und nach einem
Treiber-/Toolkitwechsel Windows neu starten.

```powershell
$cuda = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2'
$env:Path = "$cuda\bin\x64;$cuda\bin;$env:Path"

C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v132-local\bin\Release\llama-bench.exe `
  -m C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf `
  -p 512 -n 128 -r 3 -ngl 999 -fa 1 -ctk q8_0 -ctv q8_0 -t 16 `
  -ot "blk\.[0-9]+\.ffn_(up|down|gate)_exps\.weight=CPU" -o json
```

Ein Ergebnis ist nur gültig, wenn die JSON-Metadaten `cuda=true` und
`gpu_blas=true` enthalten. Ein CPU-Fallback oder `cuInit=100` wird als
blockierter Lauf dokumentiert, niemals als GPU-Benchmark ausgewertet.
`llama-bench` misst ausschliesslich Durchsatz; die Daily-Coder-Eignung
erfordert danach einen separaten Coding-Gate-Lauf.

## 5) Kampagnenmodus mit Power-Profilen

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark_campaign.ps1 -Backend both
```

## 6) Ergebnisse

- Neue Ergebnisse werden standardmaessig unter `benchmark_results\` abgelegt (relativ zum Aufrufpfad).
- Historische Ergebnisse sind unter `data/benchmark_results_legacy\`.
- Jeder Benchmark-Update erzeugt drei zusammengehörige Artefakte:
  `benchmark_report.html`/`.md` und `benchmark_run_summary.csv` enthalten die kompakte
  Übersicht mit einer Zeile je Benchlauf (Datum/Uhrzeit, Backend, Modell,
  Benchmarkname, Samples, GPU/CPU, VRAM/RAM, Score, Fehler, Tokens/s, Laufzeit,
  Bewertung). `benchmark_live_status.html`/`.md` enthält die breite operative
  Tabelle mit Planung, Fortschritt und ETA.

## 7) Living-memory-Verstaendnisbenchmark

Zwei getrennte Tracks verhindern falsche Vergleiche und Datenabfluss:

- **Tool-Agent:** OpenCode arbeitet im echten `C:\GIT`. Der gesamte Live-Workspace-
  Katalog ist `restricted` und nur fuer lokale `ollama/*`-Modelle erlaubt. Ergebnisse mit realem User-Kontext
  liegen privat unter `C:\GIT\user-memory\why\conversations\benchmarks`.
- **Pure Model:** Ollama- oder Siemens-API erhaelt bei jedem Fall ausschliesslich das
  synthetische Fixture. Dieser Track misst Regelverstaendnis, nicht Dateisystem-Retrieval.

Dry-runs:

```powershell
python .\scripts\run_agent_understanding_benchmark.py `
  --model ollama/deepseek-v4-flash

python .\scripts\run_model_understanding_benchmark.py `
  --backend siemens --model deepseek-v4-flash
```

Erst nach expliziter Freigabe `--execute` ergaenzen. Modelle laufen seriell; lokal
nie mehr als ein Modell gleichzeitig. Fehlende Kriterien loesen eine kompakte
Diagnoserunde zu Ursache, verpasster Quelle und kleinster Verbesserung aus.

Ergebnisse aggregieren:

```powershell
python .\scripts\summarize_understanding_results.py `
  .\benchmark_results\living_memory_model_results.jsonl `
  --json-output .\benchmark_results\living_memory_summary.json `
  --markdown-output .\benchmark_results\living_memory_summary.md
```

Core/Common/Edge werden 3/2/1 gewichtet. Tool-Agent- und Pure-Model-Scores immer
getrennt interpretieren: Besteht das Pure Model, aber der Tool-Agent nicht, liegt
das Problem wahrscheinlich bei Navigation, Retrieval oder Tool-Integration.

Einzelfaelle koennen mit `--case <case-id>` gezielt nachgetestet werden. Laengere
Modelle erhalten `--timeout-sec`; `--request-attempts 1` verhindert bei sehr langen
Timeouts eine unkontrollierte Vervielfachung der Wartezeit. Erfolgreiche
`backend+model+case`-Kombinationen werden beim Resume uebersprungen.

### Hard Tool-Agent

Der Hard-Track nutzt pro Modell einen isolierten synthetischen Git-Workspace und
bewertet zu 75 Prozent Dateien, Tests, Memory, Chat-Evidenz und Git:

```powershell
python .\scripts\run_hard_agent_benchmark.py `
  --catalog .\benchmarks\living-memory-hard-agent-v2.json `
  --model siemens/qwen-3.6-27b `
  --output-dir .\benchmark_results\<lauf> `
  --workspace-root C:\Users\<user>\.copilot\hard-bench\<lauf> `
  --execute
```

Nach Abbruch denselben Befehl mit `--resume` fortsetzen. Kurze physische
Workspace-Pfade verhindern Windows-Long-Path-Verzerrungen.

## 7.1) Sustained CPU/GPU thermal-load comparison

Use this only after recording a stable driver, Lenovo Vantage mode, Windows
power mode, AC power, room conditions, and notebook-cooler setting. It holds
one local Qwen coding workload for ten minutes, makes serial requests only,
and writes raw 1 Hz CPU/GPU/VRAM/power/temperature/clock telemetry plus
per-request tok/s to an isolated result directory.

```powershell
cd C:\GIT\llm-evaluation-workbench\scripts

# Select Q4 or Q5 and cooler off/on interactively. Ollama starts only when
# necessary, and a server started by this script stops after the run.
python .\run_sustained_ollama_load.py
```

The default is `qwen3.6:35b`, Q4, 16K context, 512 output tokens per
request, and 600 seconds of sustained work. Q5 is selectable only as a
controlled follow-up when Q4 does not impose sufficient CPU-offload load. Do
not compare runs with different models, drivers, power modes, or cooler states. `summary.json`,
`telemetry.csv`, and `requests.jsonl` are the raw evidence; do not infer an
energy value that the system did not measure.

## 8) Agent-Helper-Evaluation-Track (Harness + expliziter Echt-Gate-Executor)

Vollständige Methodik/Schema/Rubrik-Dokumentation:
`docs\project\agent_helper_benchmark.md`. Die meisten Befehle hier rufen
**kein** Modell, keine API, kein Ollama/llama.cpp auf — sie validieren nur
die Speicher-/Aggregations-/Report-Pipeline oder importieren bestehende
Legacy-CSV-Daten read-only. Einzige Ausnahme:
`connect-gate-run`/`mini-gate-run` (§8, weiter unten) rufen **wirklich**
einen laufenden lokalen Ollama-Server auf — aber ausschließlich, wenn ein
Mensch/Parent-Agent das explizit mit genau einem `--model` aufruft; diese
Session selbst tut das nie automatisch.

**Hartes Akzeptanz-Gate (wichtig für die Interpretation jedes Berichts):**
Geschwindigkeit gleicht niemals mangelnde Nutzbarkeit aus. Ein Modell-Lauf
mit fehlgeschlagenen deterministischen Tests, unsicherem Verhalten,
Platzhalter-/unvollständiger Ausgabe oder zu niedrigem Reviewer-Score wird
unabhängig von Tokens/Sekunde als `"not-usable"` eingestuft und komplett aus
dem Ranking ausgeschlossen (eigene "Excluded"-Tabelle im Bericht, nie im
Leaderboard). Ein Modell wird außerdem **nie** allein aufgrund eines
Keyword-/Heuristik-Reviewer-Scores (z. B. importierte Legacy-Daten) als
`tier-1-recommended` gelabelt — siehe `agent_helper_benchmark.md` §9 für die
volle Spezifikation.

**No-model dry-run** (empfohlen vor jeder echten Kampagne, beliebig oft wiederholbar):

```powershell
cd C:\GIT\llm-evaluation-workbench\scripts
python .\run_agent_helper_campaign.py dry-run --campaign-id dry-run-<datum>
```

Schreibt eine isolierte Testkampagne unter
`benchmark_results\agent-helper\dry-run-<datum>\` (SQLite, alle drei CSVs
`agent_helper_samples.csv`/`agent_helper_aggregates.csv`/
`agent_helper_capacity_profile.csv`, Artefakte, HTML-Bericht) mit rein
synthetischen, seeded Zufallsdaten. Ein Fixture-Modell
(`fixture-fast-unreliable`) demonstriert absichtlich alle vier harten
Ausschlusskriterien (fehlgeschlagener deterministischer Test, unsicheres
Verhalten, Platzhalter-Ausgabe, niedriger Reviewer-Score) und erscheint
dadurch reproduzierbar in der "Excluded"-Sektion des Berichts — so lässt
sich das Gate ohne echten Modellaufruf visuell prüfen. Der Dry-Run erzeugt
außerdem eine deterministische Phasenaufschlüsselung (LLM/Tool/Queue-Idle/
Orchestrierung, exklusiv auf 100 % normiert, siehe `agent_helper_benchmark.md`
§11) sowie ein Demo-Concurrency-Profil (1/2/4 gleichzeitige Anfragen an
dasselbe akzeptierte lokale Modell, §11.3) — beide nur zur Vorschau der
Report-Darstellung, ohne echten Modell-/Ressourcenzugriff. Sofern
`benchmarks\agent-helper-model-inventory.example.json` vorhanden ist, wird
sie außerdem best-effort geladen und im berichtweiten Abschnitt "Model
inventory & feasibility" gerendert (measured/projection-Badges, §12); fehlt
die Datei oder ist sie ungültig, zeigt der Bericht lediglich einen
"kein Inventar geladen"-Hinweis, der Dry-Run schlägt dadurch nie fehl. Der
Bericht bietet außerdem einen Vollbild-Umschalter und die Tastaturkürzel
`L`/`F`/`P` (Sprache/Vollbild/Drucken).

**Externer Benchmark-Katalog** (optional, für Mehr-Agenten-Workspaces):
`dry-run` akzeptiert `--catalog PATH`, um statt des gebündelten
Standardkatalogs eine andere, dem dokumentierten
`agent-helper-catalog-v1`-Schema entsprechende JSON-Datei zu laden — ohne
jede Code-Änderung, solange `catalog_schema_version` und ein
Pflicht-`catalog_metadata`-Block (`catalog_id`/`author`/`created_at`)
vorhanden sind (siehe `agent_helper_benchmark.md` §6.1). Beispiel:

```powershell
python .\run_agent_helper_campaign.py dry-run --campaign-id dry-run-external-<datum> `
  --catalog ..\benchmarks\agent-helper-catalog.example-external.json
```

**Koordination mit der parallel laufenden Command-Center-Session:** neue
Benchmark-Sets immer als **neue, separate** Datei unter `benchmarks\`
anlegen (nie eine bestehende Katalogdatei ohne Übergabenotiz in `AGENTS.md`/
dem Changelog editieren); Kampagnenergebnisse bleiben pro
`benchmark_results\agent-helper\<campaign-id>\` isoliert; jede Katalogdatei
braucht den Pflicht-`catalog_metadata`-Block. Die andere Session führt nie
selbst eine lokale Ollama/llama.cpp-Kampagne aus; Koordination läuft
ausschließlich über diese Doku/den Changelog, nie über direkte
Agent-Kontaktaufnahme — siehe `agent_helper_benchmark.md` §15.

**Connect-Gate-Plan** (druckt nur die exakten Befehle, führt nichts aus):

```powershell
python .\run_agent_helper_campaign.py connect-gate-plan `
  --inventory ..\benchmarks\agent-helper-model-inventory.example.json
```

Für einen echten Connect-Gate-Check anschließend **manuell genau einen**
ausgedruckten Befehl ausführen (bei `ollama`/`llama_cpp` die
Ein-lokales-Modell-Regel beachten), Ergebnis dokumentieren. Dieses Script
führt Connect-Checks nie selbst automatisiert aus.

**Echter Connect-Gate / Mini-Gate für genau ein Modell** (ruft **wirklich**
einen laufenden lokalen Ollama-Server auf — volle Methodik in
`agent_helper_benchmark.md` §16; niemals von dieser Session selbst
ausgeführt, nur vom Parent-Agent/Menschen manuell):

```powershell
cd C:\GIT\llm-evaluation-workbench\scripts

# Voraussetzung: `ollama serve` läuft lokal und --model ist bereits
# gepullt/verfügbar. Wartet standardmäßig bis zu 3600 Sekunden auf die
# gemeinsame Workspace-Lease `local-llm`, prüft danach `ollama ps` und lädt
# IMMER mit keep_alive=0 wieder aus (finally-Block, auch bei Fehler/Timeout).
python .\run_agent_helper_campaign.py connect-gate-run `
  --campaign-id live-<datum> --model qwen3-coder:30b

# Echte, einmalige Mini-Coding-Aufgabe für dasselbe Modell/dieselbe
# Kampagne. Bewertet über eine feste 10-Test-unittest-Suite in einem
# Subprozess mit striktem Timeout -- nie über Keywords. Das harte
# Akzeptanz-Gate gilt immer: eine lauffähige, aber falsche Lösung bleibt
# "not_usable".
python .\run_agent_helper_campaign.py mini-gate-run `
  --campaign-id live-<datum> --model qwen3-coder:30b
```

Beide Befehle sind bewusst **One-Shot**: eine Korrektur-/Folge-Iteration ist
ein separater, später Befehl. `--local-lease-wait-seconds` steuert das
begrenzte Warten auf die kanonische Lease in
`C:\GIT\standards\scripts\local_model_lease.py`. Erst ein Wait-Timeout,
Lockfehler oder eine Preflight-Verweigerung (z. B. ein bereits geladenes
Fremdmodell) druckt
`REFUSED (nothing attempted, nothing persisted): <Grund>`, gibt Exit-Code
`2` zurück und persistiert **keine** Zeile — nichts wurde versucht. Ein
echter, fehlgeschlagener oder Timeout-behafteter Versuch wird dagegen
persistiert (`status=error`/`timeout`, `acceptance_status=not_usable`) und
erscheint korrekt in der "Excluded"-Sektion des Berichts. Ergebnisse landen
isoliert unter `benchmark_results\agent-helper\live-<datum>\` (SQLite, beide
CSVs, JSON-Artefakt pro Sample, HTML-Bericht) — exakt dieselbe Pipeline wie
`dry-run`.

**Echte, read-only Ollama-Inventar-Discovery + serieller Mehr-Modell-Gate-Runner**
(volle Methodik in `agent_helper_benchmark.md` §17; niemals von dieser
Session selbst ausgeführt, nur vom Parent-Agent/Menschen manuell):

```powershell
cd C:\GIT\llm-evaluation-workbench\scripts

# 1) REAL, read-only Discovery (nur GET /api/tags + POST /api/show -- lädt
#    und generiert nie). Speichert einen Snapshot in die Kampagne, ohne die
#    handkuratierte Beispiel-Inventardatei zu überschreiben.
python .\run_agent_helper_campaign.py ollama-inventory-snapshot --campaign-id serial-pilot-<datum>

# 2) Modellfreier, immer trockener Plan: zeigt genau, welche entdeckten
#    Modelle in welcher Reihenfolge versucht würden ("included") und
#    welche zurückgestellt/ausgeschlossen würden ("deferred", jeweils mit
#    Begründung).
python .\run_agent_helper_campaign.py serial-plan --campaign-id serial-pilot-<datum>

# 3) REAL, strikt sequenzieller kleiner Pilot (Connect- dann Mini-Gate pro
#    Modell, nie parallel). --confirm erfordert zusätzlich eine explizite
#    --models-Liste oder mindestens einen Filter-Flag -- alle entdeckten
#    Modelle "aus Versehen" laufen zu lassen ist nicht möglich.
python .\run_agent_helper_campaign.py serial-execute --campaign-id serial-pilot-<datum> --confirm `
  --models "qwen3-coder:30b,llama3.1:8b,deepseek-coder-v2:16b,phi4:14b"
```

Resume ist Standard (`--no-resume` deaktiviert es explizit): ein
unterbrochener/abgestürzter Lauf überspringt beim erneuten Ausführen
desselben Befehls jedes (Modell, Task)-Paar mit bereits persistierter
Probe (Erfolg, Fehlschlag oder Timeout) und macht mit dem Rest weiter --
`serial_progress.json` in der Kampagne protokolliert nach jedem einzelnen
Schritt atomar den Fortschritt. Eine Preflight-/Lease-Verweigerung stoppt
standardmäßig die gesamte Serie (`--continue-on-refusal` für bewusstes
Fortsetzen); Retries (`--max-retries`, Default `0`) gelten ausschließlich
für einen transienten `status=error`, nie für Timeout oder eine
inhaltlich abgelehnte Probe.

**Serial-Pilot-Crash-Fix (§18 in `agent_helper_benchmark.md`):** ein
realer Pilot (`agent-helper-serial-pilot-20260801`, `qwen3-coder:30b`)
stürzte während des Mini-Gates ab, weil dessen Arbeitsverzeichnis
(`mini_task_work/<voller sample_id>`) 226 Zeichen lang war und
`subprocess.run(cwd=...)` unter Windows `NotADirectoryError: [WinError
267]` warf — **ohne** persistierte Probe oder Checkpoint. Behoben durch:
ein kurzes, gehashtes Arbeitsverzeichnis (`_short_work_dir_name()`, 16 Hex-
Zeichen, unabhängig von der Länge von Kampagne/Modell/Task-IDs); ein
Gate-Boundary-Exception-Sicherheitsnetz in beiden echten Gates (jede
unerwartete Exception nach dem echten HTTP-Aufruf wird jetzt immer als
`status=error`/`acceptance_status=not_usable`-Probe persistiert statt die
Session abstürzen zu lassen — neues `--halt-on-gate-exception`-Flag
steuert, ob die Kampagne danach trotzdem weiterläuft [Standard] oder
stoppt); und ein Checkpoint **vor** jedem Gate-Aufruf (zusätzlich zum
bestehenden Checkpoint danach), damit ein Absturz/Ctrl-C immer einen
wiederaufnehmbaren Zustand hinterlässt. Genauer Wiederaufnahme-Befehl für
exakt diesen realen Piloten (Connect-Gate für `qwen3-coder:30b` bereits
akzeptiert und wird übersprungen; nur dessen fehlendes Mini-Gate plus die
drei noch nie versuchten Modelle laufen):

```powershell
python .\run_agent_helper_campaign.py serial-execute `
  --campaign-id agent-helper-serial-pilot-20260801 --confirm `
  --models "qwen3-coder:30b,deepseek-coder-v2:16b,phi4-mini:3.8b-q4_K_M,rnj-1:8b"
```

**Kombinierten Bericht neu bauen** (liest nur bereits vorhandene Kampagnen-Datenbanken):

```powershell
python .\run_agent_helper_campaign.py report
```

**Kampagne reparieren/neu berechnen** (modellfrei, idempotent — behebt
rückwirkend die Pilot-Review-Remediation vom 2026-08-02, siehe
`agent_helper_benchmark.md` §16.5: Phasenzuordnungs-Mislabel aus dem
bereits geschriebenen Artefakt-JSON reklassifizieren, alle Aggregate mit
der aktuellen Evidenz-Stufen-/Rubrik-Logik neu berechnen, beide CSVs +
Report neu bauen; ruft nie ein Modell/eine API auf, sicher wiederholt
ausführbar):

```powershell
python .\run_agent_helper_campaign.py recompute-campaign `
  --campaign-id agent-helper-pilot-20260801
```

**Legacy-Migrationsdaten importieren** (liest die bestehende CSV nur, schreibt nichts zurück):

```powershell
python .\run_agent_helper_campaign.py import-legacy `
  --csv ..\benchmark_results\migration_llm_bench_history.csv
```

**Tests** (schnell, deterministisch, ohne Modell-/Netzwerkzugriff — der
echte Connect-/Mini-Gate-Code sowie die neue Discovery/Serial-Runner-Logik
werden ausschließlich über injizierte Fake-Transports/Fake-Gate-Funktionen
getestet, 263/263 bestehen aktuell; siehe `agent_helper_benchmark.md` §16.4
für eine Transparenznotiz zu einem versehentlichen echten Aufruf während
der manuellen CLI-Verifikation einer früheren Phase, und §17.4 für die
neuen Discovery-/Serial-Runner-Tests):

```powershell
cd C:\GIT\llm-evaluation-workbench\scripts
python -m unittest test_agent_helper_eval -v
```
