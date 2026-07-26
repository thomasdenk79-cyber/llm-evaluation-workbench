# Changelog

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
