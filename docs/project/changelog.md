# Changelog

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
