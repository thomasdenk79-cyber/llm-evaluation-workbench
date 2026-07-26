# AGENTS.md — Lebendes Projektgedächtnis (SSOT)

> **Für jeden Agent der hier arbeitet:** Diese Datei ist das vollständige Gedächtnis des Projekts.
> Lies sie komplett bevor du irgend etwas tust. Aktualisiere sie nach jedem Meilenstein.
> Ohne dieses Dokument hat der nächste Agent keinen Kontext.

---

## 1. Projektziel

**LLM Evaluation Workbench** — Systematischer Vergleich lokaler LLMs für den Einsatz bei
Oracle-nach-PostgreSQL-Datenbankmigrationen und Übersetzungsaufgaben (DE→EN für eine Fan-Homepage).

**Warum lokal?** Datenschutz (Siemens-Umfeld), kein Cloud-API-Kosten-Overhead, echte Hardware-Charakteristik.

**Hardware-Kontext (wichtig!):**
- Windows 11, begrenztes VRAM (~8 GB effektiv nutzbar für Ollama, bis ~12 GB für llama.cpp)
- Ollama und llama.cpp parallel installiert
- llama-server bevorzugt vor llama-cli

---

## 2. Umgebung — Pfade und Binaries

```
llama-server:  C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe
llama-cli:     C:\Users\z000g9hu\llama.cpp\bin\llama.exe (Fallback)

GGUF-Modelle (lokal):
  C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf           (11.3 GB)
  C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf (20.8 GB)
  C:\Users\z000g9hu\llama.cpp\models\Qwen3-235B-A22B-Q3_K_M\           (gesharded, ~70 GB)

Benchmark-Skript: scripts\llm_migration_benchmark.py
Kampagnen-Wrapper: scripts\run_benchmark_campaign.ps1
Legacy-Ergebnisse: data\benchmark_results_legacy\
Neue Ergebnisse:   benchmark_results\  (wird bei Lauf angelegt)
```

**Siemens LLM (Cloud — OpenAI-kompatibel):**
```
Endpunkt:      https://api.siemens.com/llm/v1/chat/completions
Token-Datei:   C:\Users\z000g9hu\OneDrive - Siemens AG\tools\myconfigfiles\code.siemens.com_api_ai_token.txt
Env-Variable:  SIEMENS_LLM_TOKEN  (oder SIEMENS_LLM_API_KEY / OPENAI_API_KEY)
Setup-Script:  OneDrive\tools\scripts\configure_siemens_llm.ps1

Modelle:
  deepseek-v4-flash              (1M Context, kein Vision, Tool-Calling)
  qwen-3.6-27b                   (262K Context, Vision, Tool-Calling) ← Thinking deaktiviert!
  ministral-3-14b-instruct-2512  (256K Context, Vision, Tool-Calling)
```

**→ Vollständige Neuinstallationsanleitung: `docs/operations/setup-new-pc.md`**

**Power-Profil-GUIDs (Windows):**
```
Balanced:      381b4222-f694-41f0-9685-ff5bb260df2e
High-Perf:     8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```

---

## 3. Installierte Ollama-Modelle (vollständige Liste)

Stand: 2026-07-29 — `ollama list`

| Modell-Tag | Größe | Getestet | Bemerkung |
|---|---|---|---|
| `qwen3.6:27b-q4_K_M` | 17 GB | ✅ Haupt-Benchmark | Beste Migration-Qualität (88.89%) |
| `qwen3.6:35b-a3b-q4_K_M` | 23 GB | ✅ Haupt-Benchmark | Gute Balance Speed/Qualität |
| `gpt-oss:20b` | 13 GB | ✅ Haupt-Benchmark | Schnellstes Modell, mittlere Qualität |
| `deepseek-coder-v2:16b` | 8.9 GB | ✅ Getestet | 55 TPS, 84% Qualität — bestes Speed/Qualitäts-Verhältnis |
| `qwen3-coder:30b` | 18 GB | ✅ Getestet | 88.89% Qualität, 28 TPS — bester Allrounder |
| `qwen3-coder-next:q4_K_M` | 51 GB | ✅ Getestet | 88.89% Qualität, 16 TPS |
| `codestral:latest` | 12 GB | ✅ Getestet | 72.22% Qualität, 7.9 TPS |
| `qwen2.5-coder:32b-instruct-q4_K_M` | 19 GB | ✅ Getestet | 79.17% Qualität, 3 TPS (langsam) |
| `devstral-small-2:24b` | 15 GB | ✅ Getestet | 72.22% Qualität, 5 TPS |
| `glm-4.7-flash:q4_K_M` | 19 GB | ✅ Getestet | 77.78% Qualität, 22 TPS |
| `qwen3.6:27b-bf16` | 55 GB | ❌ Nicht getestet | Zu groß für VRAM-Limit |
| `qwen3.6:27b-q8_0` | 29 GB | ❌ Nicht getestet | Quant-Vergleich ausstehend |
| `qwen3.6:35b-a3b-bf16` | 71 GB | ❌ Nicht getestet | Zu groß |
| `qwen3.6:35b-a3b-q8_0` | 38 GB | ❌ Nicht getestet | Quant-Vergleich ausstehend |
| `qwen3:235b-a22b-q4_K_M` | 142 GB | ❌ Nicht getestet | Gigantisch, RAM-limitiert |
| `llama3.3:70b` | 42 GB | ❌ Nicht getestet | Ausstehend |
| `qwen3:32b` | 20 GB | ❌ Nicht getestet | Ausstehend |
| `qwen2.5:32b` | 19 GB | ❌ Nicht getestet | Ausstehend |
| `phi4-mini:3.8b-q4_K_M` | 2.5 GB | ❌ Nicht getestet | Klein, interessant für Speed |
| `deepseek-r1:8b` | 5.2 GB | ❌ Nicht getestet | Reasoning-Modell, ausstehend |
| `llama3.1:8b` | 4.9 GB | ❌ Nicht getestet | Baseline ausstehend |
| `rnj-1:8b` | 5.1 GB | ❌ Nicht getestet | |
| `north-mini-code-1.0:q4_K_M` | 18 GB | ❌ Nicht getestet | |
| `laguna-xs-2.1:q4_K_M` | 20 GB | ❌ Nicht getestet | |
| `deepseek-v4-flash:cloud` | - | ❌ Nicht getestet | Cloud-Modell |
| `qwen36-1m:latest` | 23 GB | ❌ Nicht getestet | 1M Context, ausstehend |
| `qwen2.5-coder:1.5b-base` | 986 MB | ❌ Nicht getestet | Winzig, Speed-Test interessant |
| `deepseek-coder-v2:16b` (llama.cpp) | — | ✅ Getestet | 12-15 TPS, 72.22% Qualität |
| `qwen3-coder-30b` (llama.cpp) | — | ✅ Getestet | 9 TPS, 72.22% Qualität |

## 3b. Siemens Cloud-Modelle (api.siemens.com)

| Modell-ID | Name | Context | Vision | Benchmark-Status |
|---|---|---|---|---|
| `deepseek-v4-flash` | Siemens DeepSeek V4 Flash | 1M Token | ❌ | ✅ Getestet 2026-07-26 |
| `qwen-3.6-27b` | Siemens Qwen 3.6 27B | 262K Token | ✅ | ✅ Getestet 2026-07-26 |
| `ministral-3-14b-instruct-2512` | Siemens Ministral 3-14B | 256K Token | ✅ | ✅ Getestet 2026-07-26 |

**Erstes Benchmark-Ergebnis (2026-07-26, 1 Run, Migration-Tasks):**

| Modell | Overall | Qualität% | TPS | Wall-ms | CPU% |
|---|---|---|---|---|---|
| `qwen-3.6-27b` | **83.33** | **88.89** | 99 | 1 346 | 54 |
| `ministral-3-14b-instruct-2512` | 70.00 | 83.33 | 38 | 3 606 | 40 |
| `deepseek-v4-flash` | 36.50 | 18.17 | 98 | 2 548 | 54 |

> **Hinweis:** DeepSeek V4 Flash hat schlechte Migrations-Qualität (18%) — vermutlich
> antwortet es ohne Markdown-Wrapper anders als erwartet. Mehr Runs und Prompt-Anpassung nötig.
> Qwen 3.6 27B ist der klare Siemens-Favorit: 89% Qualität bei 99 TPS!

**Wichtig für `qwen-3.6-27b`:** Das Modell hat standardmäßig Thinking-Mode aktiv.
Das Skript deaktiviert ihn automatisch via `chat_template_kwargs: {enable_thinking: false}`.

**Token-Datei-Format:** Datei enthält 2 Zeilen — Skript liest automatisch nur die `SIAK-`-Zeile.

---

## 4. Benchmark-Ergebnisse — Zusammenfassung aller Läufe

### 4.1 Oracle→PostgreSQL Migration (Hauptbenchmark)

Benchmark-Fälle: DDL-Konvertierung, PL/SQL→PL/pgSQL, Validierungs-SQL (3 Runs je Konfiguration)
Scoring: Keyword-Matching (required_keywords je Task), 0–100 %, Durchschnitt über alle Tasks.

**Beste Konfigurationen (final, highperf-Modus):**

| Modell | Backend | Config | avg TPS | Quality% | avg Wall-ms | avg CPU% |
|---|---|---|---|---|---|---|
| qwen3.6:27b-q4_K_M | ollama | highperf | **3.5** | **88.89** | 46 202 | 86 |
| qwen3-coder:30b | ollama | highperf | 28.5 | **88.89** | 10 817 | 40 |
| qwen3-coder-next:q4_K_M | ollama | highperf | 15.9 | **88.89** | 30 628 | 49 |
| deepseek-coder-v2:16b | ollama | highperf | **55.2** | 84.13 | 7 705 | 42 |
| glm-4.7-flash:q4_K_M | ollama | — | 21.8 | 77.78 | 17 098 | 47 |
| qwen2.5-coder:32b-instruct-q4_K_M | ollama | — | 3.0 | 79.17 | 50 528 | 52 |
| qwen3.6:35b-a3b-q4_K_M | ollama | balanced | 9.0 | 72.22 | 32 665 | 70 |
| qwen3.6:35b-a3b-q4_K_M | ollama | highperf | 28.7 | 72.22 | 16 065 | 66 |
| codestral:latest | ollama | — | 7.9 | 72.22 | 26 447 | 55 |
| devstral-small-2:24b | ollama | — | 5.0 | 72.22 | 34 023 | 50 |
| gpt-oss:20b | ollama | highperf | 42.1 | 54.37 | 11 862 | 68 |
| gpt-oss:20b | llama.cpp | highperf ngl99 | 50.2 | **66.07** | 4 401 | 13 |
| gpt-oss:20b | llama.cpp | balanced ngl99 | **84.1** | **66.07** | 2 646 | 20 |
| qwen3.6:35b-a3b-q4_K_M | llama.cpp | highperf ngl99 | 3.0 | 77.78 | 171 558 | 13 |
| deepseek-coder-v2-16b | llama.cpp | — | 13.6 | 72.22 | 9 351 | 60 |
| qwen3-coder-30b | llama.cpp | — | 9.2 | 72.22 | 12 317 | 61 |

**ngl-Einfluss auf llama.cpp (gpt-oss:20b, highperf):**

| ngl | TPS | Qualität | Wall-ms |
|---|---|---|---|
| 0 (CPU-only) | 7.2 | 41.67% | 30 639 |
| 20 | 35.5 | 58.33% | 6 209 |
| 99 | 50.2 | 66.07% | 4 401 |

→ **GPU-Offload (ngl) ist kritisch** — immer so hoch wie VRAM erlaubt setzen.
→ **Überraschung**: Balanced-Modus schlägt High-Perf für llama.cpp (84 vs 50 TPS bei gpt-oss).

### 4.2 Übersetzung DE→EN (Fan-Homepage-Texte)

| Modell | Backend | TPS | Qualität | VRAM MB |
|---|---|---|---|---|
| qwen3.6:27b-q4_K_M | ollama | 4.0 | **100%** | 6 903 |
| qwen3.6:35b-a3b-q4_K_M | ollama | 36.9 | **100%** | 7 386 |
| gpt-oss:20b | llama.cpp | 33.8 | **100%** | 10 794 |
| qwen3.6:35b-a3b-q4_K_M | llama.cpp | 3.7 | **100%** | 11 886 |
| gpt-oss:20b | ollama | 45.1 | 83.33% | 7 312 |

→ qwen3.6-Modelle übertreffen bei Übersetzung. gpt-oss verpasste 1 von 6 Kriterien.

### 4.3 Gesamtempfehlung (aktueller Stand)

| Verwendungszweck | Empfehlung | Begründung |
|---|---|---|
| Beste Qualität Migration | `qwen3.6:27b-q4_K_M` (Ollama) | 88.89%, konsistent |
| Bester Allrounder | `qwen3-coder:30b` (Ollama) | 88.89% + 28 TPS |
| Schnellstes mit guter Qualität | `deepseek-coder-v2:16b` (Ollama) | 55 TPS + 84% |
| Übersetzung | `qwen3.6:35b-a3b-q4_K_M` (Ollama) | 100% + 37 TPS |
| llama.cpp-Empfehlung | `gpt-oss:20b` ngl=99 balanced | 84 TPS + 66% |

---

## 5. Offene Aufgaben (Nächste Schritte)

**Priorität 1 — Fehlende Modell-Tests:**
- [ ] `qwen3:32b` (Ollama) — direkte Konkurrenz zu qwen3-coder:30b, noch nicht getestet
- [ ] `qwen3.6:27b-q8_0` vs `qwen3.6:27b-q4_K_M` — Quant-Vergleich (Speed vs Qualität)
- [ ] `phi4-mini:3.8b` + `llama3.1:8b` + `deepseek-r1:8b` — kleine/schnelle Modelle als Baseline
- [ ] `llama3.3:70b` — großes Referenzmodell (falls RAM reicht)

**Priorität 2 — Analyse und Reporting:**
- [ ] Auswertungs-Notebook oder HTML-Report aus legacy CSV-Daten generieren
- [ ] Empfehlungsdokument `docs/project/recommendation.md` erstellen
- [ ] GPU-Temperatur und Takt-Daten in Metriken einbauen (aktuell minimal erfasst)

**Priorität 3 — Benchmark-Erweiterung:**
- [ ] Weitere SQL-Migrationsfälle: Sequences, Triggers, Views, Package-Bodies
- [ ] Multi-Run-Stabilitätsanalyse (Varianz über 5+ Runs)
- [ ] Quantisierungs-Studie: q4 vs q8 vs bf16 für qwen3.6:27b

---

## 6. Schnellstart-Befehle (sofort lauffähig)

```powershell
cd D:\git\llm-evaluation-workbench

# Einzelner Ollama-Run (ein Modell):
python .\scripts\llm_migration_benchmark.py --backend ollama --ollama-models "qwen3-coder:30b" --runs 3

# Siemens-Backend (Token wird automatisch aus OneDrive-Datei geladen):
python .\scripts\llm_migration_benchmark.py --backend siemens --runs 2

# Nur ein Siemens-Modell:
python .\scripts\llm_migration_benchmark.py --backend siemens --siemens-model "deepseek-v4-flash" --runs 3

# Alle Backends inkl. Siemens:
python .\scripts\llm_migration_benchmark.py --backend all --runs 1

# Einzelner llama.cpp-Run (gpt-oss, GPU-optimiert):
python .\scripts\llm_migration_benchmark.py `
  --backend llama_cpp `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --llama-model "gpt-oss:20b=C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf" `
  --llama-ngl 99 --runs 3

# Vollständige Kampagne (beide Backends, beide Power-Profile):
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark_campaign.ps1 -Backend both -Runs 3
```

**Token für Siemens-Backend setzen (falls nicht automatisch gefunden):**
```powershell
$env:SIEMENS_LLM_TOKEN = (Get-Content "C:\Users\z000g9hu\OneDrive - Siemens AG\tools\myconfigfiles\code.siemens.com_api_ai_token.txt" -Raw).Trim()
```

---

## 7. Bewertungsmethodik (Scoring)

- **quality_score**: Keyword-Matching pro Benchmark-Task
  - Jeder Task hat `required_keywords` (teils OR-Gruppen)
  - Score = (Treffer / Total) × 100
  - Durchschnitt über alle Tasks = Gesamt-Score
- **output_tps**: Output-Tokens pro Sekunde (Wall-time basiert)
- **wall_ms**: Gesamtlaufzeit in Millisekunden (inkl. Kaltstart bei llama.cpp)
- **avg_cpu_pct / avg_gpu_pct / avg_vram_mb**: Durchschnitt über Laufzeit via psutil + GPU-Counter

---

## 8. Agent-Regeln

1. **Kein stiller Fallback bei Fehlern** — Fehler explizit loggen und im Ergebnis kennzeichnen.
2. **Reproduzierbarkeit** — Seeds, Prompt-Sets und Parameter immer transparent halten.
3. **Vergleichbarkeit** — Ollama und llama.cpp nur mit äquivalenten Einstellungen vergleichen.
4. **Ergebnisstruktur stabil** — CSV/JSON-Format nicht brechen ohne Doku-Update.
5. **Immer diese Datei aktualisieren** — nach jedem Benchmark-Lauf Ergebnisse hier eintragen.
6. **Changelog pflegen** — `docs/project/changelog.md` bei Änderungen aktualisieren.
7. **Runbook aktuell halten** — neue Befehle/Pfade sofort in `docs/operations/runbook.md`.

---

## 9. Repository-Struktur

```
llm-evaluation-workbench/
├── scripts/
│   ├── llm_migration_benchmark.py   # Haupt-Benchmark-Engine (1170 Zeilen)
│   ├── run_benchmark_campaign.ps1   # Kampagnen-Wrapper mit Power-Profil-Switching
│   └── metrics/
│       ├── os_stats_perf_mon.py     # OS-Metriken via Perf-Mon
│       └── system_metrics_collctor.py  # Alternativ-Collector
├── data/
│   └── benchmark_results_legacy/   # Alle historischen Ergebnisse (CSV + JSON)
├── benchmark_results/              # Neue Läufe (wird bei Lauf angelegt)
├── docs/
│   ├── operations/runbook.md       # Schritt-für-Schritt-Anleitung
│   ├── project/changelog.md        # Änderungshistorie
│   └── engineering/documentation-guidelines.md
├── .github/
│   ├── copilot-instructions.md     # Copilot-spezifische Regeln
│   ├── workflows/ci.yml
│   ├── workflows/codeql.yml
│   └── dependabot.yml
├── AGENTS.md                       # ← Diese Datei (SSOT)
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
└── requirements.txt                # psutil>=5.9
```

---

*Zuletzt aktualisiert: 2026-07-29 — alle Benchmark-Läufe vom 2026-07-25 eingetragen.*
