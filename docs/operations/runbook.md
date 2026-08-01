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
# gepullt/verfügbar. Erzwingt Preflight (`ollama ps`) + Filesystem-Lock
# (max. ein lokales Modell); lädt danach IMMER mit keep_alive=0 wieder aus
# (finally-Block, auch bei Fehler/Timeout).
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
ein separater, später Befehl. Eine Preflight-/Lock-Verweigerung (z. B. ein
bereits geladenes Fremdmodell) druckt
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
Schritt atomar den Fortschritt. Eine Preflight-/Lock-Verweigerung stoppt
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
