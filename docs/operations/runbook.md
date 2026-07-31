# Runbook: LLM Benchmark Campaign

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

## 5) Kampagnenmodus mit Power-Profilen

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark_campaign.ps1 -Backend both
```

## 6) Ergebnisse

- Neue Ergebnisse werden standardmaessig unter `benchmark_results\` abgelegt (relativ zum Aufrufpfad).
- Historische Ergebnisse sind unter `data/benchmark_results_legacy\`.

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
