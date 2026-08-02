---
title: "LLM Evaluation Workbench - Session Handover"
status: active
canonical: true
updated: 2026-08-01T23:45:00+02:00
---

# Session handover

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
