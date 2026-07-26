# AGENTS.md — Lebendes Projektgedächtnis (SSOT)

> **Für jeden Agent der hier arbeitet — PFLICHTLEKTÜRE vor jeder Aktion:**
>
> Diese Datei ist das vollständige Gedächtnis des Projekts. Sie ersetzt jeden Onboarding-Chat.
> Lies sie vollständig → verstehe den Aktuellen Stand → handle. Keine Rückfragen vorher.
> **Jeder Agent MUSS sie nach jedem bedeutenden Schritt aktualisieren.**
> Ein Agent der diese Datei nicht aktuell hält, macht seine Arbeit unbrauchbar für alle Nachfolger.

---

## 0. 🔴 PFLICHT: Gedächtnis-Protokoll (jeder Agent, jede Session)

**Das ist keine Empfehlung — das ist Voraussetzung für das Arbeiten in diesem Repo.**

### Was MUSS nach jeder Session aktualisiert werden:

| Was passiert ist | Wo eintragen |
|---|---|
| Benchmark-Lauf abgeschlossen | Abschnitt 4 (Ergebnistabellen) |
| Neues Modell getestet | Abschnitt 3 (Modell-Tabelle, Status auf ✅) |
| Code geändert (Skript, Args, Format) | Abschnitt 6 (Schnellstart) + `docs/project/changelog.md` |
| Aufgabe erledigt | Abschnitt 5 (Offene Aufgaben, Checkbox abhaken) |
| Neuer Befund / Erkenntnis | Abschnitt 4.3 (Gesamtempfehlung) |
| Session endet ohne Fertigstellung | Abschnitt 5.1 (Aktueller Stand) |

### Wie der "Aktueller Stand"-Block aussehen muss (Abschnitt 5.1):
```
### Stand: YYYY-MM-DD HH:MM
- ✅ Was wurde fertig
- 🔄 Was läuft gerade / ist halb fertig (inkl. konkreten nächsten Befehl)
- ❌ Was ist blockiert und warum
- 👉 Nächster Schritt für den nächsten Agent (copy-paste-ready)
```

---

## 1. Projektziel

**LLM Evaluation Workbench** — Systematischer Vergleich lokaler LLMs und Siemens-Cloud-LLMs
für Oracle→PostgreSQL-Migrationen und DE→EN-Übersetzung (Fan-Homepage).

**Hardware-Kontext:**
- Windows 11, VRAM ~8 GB (Ollama), bis ~12 GB (llama.cpp)
- Ollama + llama.cpp + Siemens-API alle parallel verfügbar
- llama-server bevorzugt vor llama-cli (Warmstart-Vorteil)

---

## 2. Umgebung — Pfade und Binaries

```
llama-server:  C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe
llama-cli:     C:\Users\z000g9hu\llama.cpp\bin\llama.exe (Fallback)

GGUF-Modelle (lokal):
  C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf           (11.3 GB)
  C:\Users\z000g9hu\llama.cpp\models\Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf (20.8 GB)

Siemens LLM (Cloud — OpenAI-kompatibel):
  Endpunkt:    https://api.siemens.com/llm/v1/chat/completions
  Token-Datei: C:\Users\z000g9hu\OneDrive - Siemens AG\tools\myconfigfiles\code.siemens.com_api_ai_token.txt
               (Datei hat 2 Zeilen: SIAK-... Token + URL — Skript liest automatisch nur SIAK-Zeile)
  Env-Var:     SIEMENS_LLM_TOKEN
  Setup:       OneDrive\tools\scripts\configure_siemens_llm.ps1

Benchmark-Skript: scripts\llm_migration_benchmark.py
Kampagnen-Wrapper: scripts\run_benchmark_campaign.ps1
Legacy-Ergebnisse: data\benchmark_results_legacy\
Neue Ergebnisse:   benchmark_results\  (wird bei Lauf angelegt)
Docs-Test:         python standards\scripts\test_docs.py  (aus Standards-Submodule)
MkDocs-Build:      python -m mkdocs build --strict
```

**→ Vollständige Neuinstallationsanleitung: `docs/operations/setup-new-pc.md`**

**Power-Profil-GUIDs:**
```
Balanced:   381b4222-f694-41f0-9685-ff5bb260df2e
High-Perf:  8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```

---

## 3. Modell-Inventar

### 3a. Ollama (lokal, 632 GB Gesamt-Store)

| Modell-Tag | Größe | Status | Ergebnis |
|---|---|---|---|
| `qwen3-coder:30b` | 18 GB | ✅ | **88.89% Qual, 28 TPS** — bester Allrounder |
| `qwen3.6:27b-q4_K_M` | 17 GB | ✅ | **88.89% Qual, 3.5 TPS** — beste Qualität |
| `qwen3-coder-next:q4_K_M` | 51 GB | ✅ | 88.89% Qual, 16 TPS |
| `deepseek-coder-v2:16b` | 8.9 GB | ✅ | 84.13% Qual, **55 TPS** — schnellstes |
| `qwen2.5-coder:32b-instruct-q4_K_M` | 19 GB | ✅ | 79.17% Qual, 3 TPS |
| `glm-4.7-flash:q4_K_M` | 19 GB | ✅ | 77.78% Qual, 22 TPS |
| `qwen3.6:35b-a3b-q4_K_M` | 23 GB | ✅ | 72.22% Qual, 9–29 TPS |
| `codestral:latest` | 12 GB | ✅ | 72.22% Qual, 7.9 TPS |
| `devstral-small-2:24b` | 15 GB | ✅ | 72.22% Qual, 5 TPS |
| `gpt-oss:20b` | 13 GB | ✅ | 54–66% Qual, 40–84 TPS |
| `qwen3:32b` | 20 GB | ❌ ausstehend | — |
| `qwen3.6:27b-q8_0` | 29 GB | ❌ ausstehend | Quant-Vergleich |
| `phi4-mini:3.8b-q4_K_M` | 2.5 GB | ❌ ausstehend | Baseline |
| `deepseek-r1:8b` | 5.2 GB | ❌ ausstehend | Reasoning-Baseline |
| `llama3.1:8b` | 4.9 GB | ❌ ausstehend | Baseline |
| `llama3.3:70b` | 42 GB | ❌ ausstehend | Großes Referenzmodell |
| `qwen3:235b-a22b-q4_K_M` | 142 GB | ❌ zu groß | RAM-limitiert |

### 3b. llama.cpp (lokal, GGUF)

| Modell | GGUF-Datei | Status | Bestes Ergebnis |
|---|---|---|---|
| `gpt-oss:20b` | `gpt-oss-20b-MXFP4.gguf` | ✅ | 84 TPS (balanced+ngl99), 66% Qual |
| `qwen3.6:35b-a3b` | `Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf` | ✅ | 77.78% Qual (ngl99) |
| `deepseek-coder-v2-16b` | (Ollama-Modell) | ✅ | 13 TPS, 72.22% Qual |
| `qwen3-coder-30b` | (Ollama-Modell) | ✅ | 9 TPS, 72.22% Qual |

**Key Finding llama.cpp:** `ngl` (GPU-Layer-Offload) ist kritisch. ngl=0 → 7 TPS, ngl=99 → 50+ TPS.
Balanced-Profil schlägt High-Perf bei llama.cpp (84 vs 50 TPS bei gpt-oss).

### 3c. Siemens Cloud (api.siemens.com, OpenAI-kompatibel)

| Modell-ID | Kontext | Vision | Typ | Smoke-Test | Benchmark |
|---|---|---|---|---|---|
| `deepseek-v4-flash` | 1M | ❌ | Reasoning (intern) | ✅ OK | 🔄 läuft |
| `gpt-oss-120b` | — | — | Standard | ✅ OK | 🔄 läuft |
| `qwen-3.6-27b` | 262K | ✅ | Standard (Thinking OFF) | ✅ OK | 🔄 läuft |
| `Mistral-Small-24B-Instruct-2501-FP8-dynamic` | — | — | Standard | ✅ OK | 🔄 läuft |
| `ministral-3-14b-instruct-2512` | 256K | ✅ | Standard | ✅ OK | 🔄 läuft |

**Nicht-LLM Siemens-Modelle (kein Benchmark sinnvoll):**
`bge-m3`, `qwen3-embedding-0.6b/8b`, `qwen3-reranker-0.6b` (Embeddings/Reranking),
`whisper-large-v3-turbo` (Speech-to-Text)

**Siemens-spezifische Eigenheiten:**
- `deepseek-v4-flash`: Reasoning-Modell, nutzt `reasoning`-Feld intern → braucht 1500 Token Budget (in `SIEMENS_MODEL_MAX_TOKENS`)
- `qwen-3.6-27b`: Thinking-Mode wird automatisch via `chat_template_kwargs: {enable_thinking: false}` deaktiviert
- Alle Siemens-Modelle: **kein max_tokens-Limit** gesetzt — Cloud hat kein VRAM-Problem
- API-Calls werden **parallel** ausgeführt (ThreadPoolExecutor, `--siemens-workers 5`)

---

## 4. Benchmark-Ergebnisse

### 4.1 Oracle→PostgreSQL Migration — Lokale Modelle (abgeschlossen 2026-07-25)

Benchmark: 3 Tasks (DDL, PL/SQL, Validation), 3 Runs, Keyword-Scoring.

| Modell | Backend | Konfig | Qual% | TPS | Wall-s | CPU% |
|---|---|---|---|---|---|---|
| qwen3-coder:30b | ollama | — | **88.89** | 28.5 | 10.8 | 40 |
| qwen3.6:27b-q4_K_M | ollama | highperf | **88.89** | 3.5 | 46.2 | 86 |
| qwen3-coder-next:q4_K_M | ollama | — | **88.89** | 15.9 | 30.6 | 49 |
| deepseek-coder-v2:16b | ollama | — | 84.13 | **55.2** | 7.7 | 42 |
| gpt-oss:20b | llama_cpp | balanced ngl99 | 66.07 | **84.1** | 2.6 | 20 |
| gpt-oss:20b | llama_cpp | highperf ngl99 | 66.07 | 50.2 | 4.4 | 13 |
| qwen3.6:35b-a3b | ollama | highperf | 72.22 | 28.7 | 16.1 | 66 |
| deepseek-coder-v2-16b | llama_cpp | — | 72.22 | 13.6 | 9.4 | 60 |

### 4.2 Siemens Cloud — Final (2026-07-26, 3 Runs, ohne Token-Limits)

Datei: `benchmark_results/migration_llm_bench_20260726_142651.csv`

| Modell | Overall | Qual% | TPS | Wall-ms | Rel% |
|---|---|---|---|---|---|
| `gpt-oss-120b` | 84.93 | 88.89 | **163** | 5 459 | 100 |
| `deepseek-v4-flash` | 83.36 | 92.59 | 115 | 21 906 | 100 |
| `qwen-3.6-27b` | 79.67 | **92.86** | 93 | **3 066** | 100 |
| `ministral-3-14b-instruct-2512` | 75.96 | **95.83** | 36 | 6 521 | 88.9 |
| `Mistral-Small-24B-Instruct-2501-FP8-dynamic` | 69.21 | 83.33 | 24 | 8 321 | 100 |

> **⚠️ Token-Limit-Bug:** `deepseek-v4-flash` hatte mit Limit 220 Tokens nur 18% Qualität.
> Nach Entfernung aller Token-Limits (`SIEMENS_MODEL_MAX_TOKENS = {}`): **92.59%**.

### 4.3 Übersetzung DE→EN

| Modell | Backend | TPS | Qual% |
|---|---|---|---|
| qwen3.6:35b-a3b-q4_K_M | ollama | 36.9 | **100** |
| qwen3.6:27b-q4_K_M | ollama | 4.0 | **100** |
| gpt-oss:20b | llama_cpp | 33.8 | **100** |
| gpt-oss:20b | ollama | 45.1 | 83.3 |

### 4.4 Gesamtempfehlung (Stand 2026-07-26)

| Zweck | Empfehlung | Grund |
|---|---|---|
| Beste Qualität gesamt | `ministral-3-14b-instruct-2512` (Cloud) | 95.83% — Spitzenreiter |
| Beste Balance Cloud | `qwen-3.6-27b` (Cloud) | 92.86%, 93 TPS, nur 3s Wall |
| Schnellstes Modell | `gpt-oss-120b` (Cloud) | 163 TPS, 88.89% |
| Bester lokaler Allrounder | `qwen3-coder:30b` (Ollama) | 88.89%, 28 TPS |
| Schnellstes lokal | `deepseek-coder-v2:16b` (Ollama) | 55 TPS, 84% |
| llama.cpp | `gpt-oss:20b` balanced+ngl99 | 84 TPS, 66% |

---

## 5. Aktueller Arbeitsstand

### Stand: 2026-07-26 14:27

- ✅ Siemens-Backend mit 5 Modellen eingebaut
- ✅ Token-Limits komplett entfernt (`SIEMENS_MODEL_MAX_TOKENS = {}`) — war der Kern-Bug
- ✅ Voller Siemens-Benchmark abgeschlossen: 5 Modelle × 3 Runs (`migration_llm_bench_20260726_142651`)
- ✅ Vergleichstabelle erstellt: `docs/project/comparison_table.md`
- ✅ Changelog aktualisiert
- ❌ `ministral-3-14b-instruct-2512` hatte 1× 502 Bad Gateway bei validation_query run 3 — Retry sinnvoll

### 👉 Nächster Schritt für neuen Agent:

```powershell
cd D:\git\llm-evaluation-workbench

# Optional: ministral nochmal solo wegen 502-Fehler:
python .\scripts\llm_migration_benchmark.py --backend siemens --siemens-model ministral-3-14b-instruct-2512 --runs 3

# Dann: fehlende lokale Modelle testen (Priorität 1 aus Abschnitt 5):
python .\scripts\llm_migration_benchmark.py --backend ollama --ollama-models "qwen3:32b" --runs 3
python .\scripts\llm_migration_benchmark.py --backend ollama --ollama-models "phi4-mini:3.8b-q4_K_M" --runs 3
```

---

## 6. Schnellstart-Befehle

```powershell
cd D:\git\llm-evaluation-workbench

# Siemens (alle 5, parallel, 3 Runs — empfohlen):
python .\scripts\llm_migration_benchmark.py --backend siemens --runs 3

# Ollama (ein Modell):
python .\scripts\llm_migration_benchmark.py --backend ollama --ollama-models "qwen3-coder:30b" --runs 3

# llama.cpp (gpt-oss, ngl=99):
python .\scripts\llm_migration_benchmark.py `
  --backend llama_cpp `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --llama-model "gpt-oss:20b=C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf" `
  --llama-ngl 99 --runs 3

# Alle Backends:
python .\scripts\llm_migration_benchmark.py --backend all --runs 1

# Token manuell setzen (falls nicht automatisch gefunden):
$env:SIEMENS_LLM_TOKEN = (Get-Content "C:\Users\z000g9hu\OneDrive - Siemens AG\tools\myconfigfiles\code.siemens.com_api_ai_token.txt" | Where-Object { $_ -match '^SIAK-' }).Trim()
```

---

## 7. Bewertungsmethodik (Scoring)

- **quality_score**: Keyword-Matching pro Task (OR-Gruppen möglich), Durchschnitt über alle Tasks
- **output_tps**: Output-Tokens / Wall-time (Sekunden)
- **wall_ms**: Gesamtlaufzeit inkl. Modell-Kaltstart (bei llama.cpp relevant)
- **Composite Overall-Score**: 60% Qualität + 20% Speed + 10% Reliability + 10% Effizienz

---

## 8. 🔴 Agent-Regeln (verbindlich)

**Regel 0 — Gedächtnis-Pflicht (WICHTIGSTE REGEL):**
> Jeder Agent aktualisiert AGENTS.md nach jedem bedeutenden Schritt.
> Abschnitt 5 ("Aktueller Arbeitsstand") MUSS am Ende jeder Session den genauen Stand zeigen.
> Wer das nicht tut, macht seine Arbeit für alle Nachfolger unbrauchbar.

1. **Keine stillen Fallbacks** — Fehler explizit loggen und im Ergebnis kennzeichnen.
2. **Reproduzierbarkeit** — Seeds, Prompt-Sets, Parameter transparent halten.
3. **Vergleichbarkeit** — Ollama, llama.cpp, Siemens nur mit äquivalenten Settings vergleichen.
4. **Ergebnisstruktur stabil** — CSV/JSON-Format nicht brechen ohne Changelog-Eintrag.
5. **Changelog führen** — `docs/project/changelog.md` bei Code- und Ergebnis-Änderungen.
6. **Runbook aktuell** — neue Befehle/Pfade sofort in `docs/operations/runbook.md`.
7. **Commit nach jeder Arbeitseinheit** — nie uncommitted enden.

---

## 9. Repository-Struktur

```
llm-evaluation-workbench/
├── scripts/
│   ├── llm_migration_benchmark.py   # Haupt-Engine: ollama|llama_cpp|siemens|both|all
│   ├── run_benchmark_campaign.ps1   # Kampagnen-Wrapper mit Power-Profil-Switching
│   └── metrics/
│       ├── os_stats_perf_mon.py
│       └── system_metrics_collctor.py
├── data/
│   └── benchmark_results_legacy/   # Historische Ergebnisse (CSV + JSON, 2026-07-25)
├── benchmark_results/              # Neue Läufe (auto-angelegt)
├── docs/
│   ├── operations/
│   │   ├── runbook.md
│   │   └── setup-new-pc.md         # ← Neuinstallation Windows komplett
│   ├── project/
│   │   ├── changelog.md
│   │   └── comparison_table.md     # ← TODO: noch zu erstellen
│   └── engineering/documentation-guidelines.md
├── .github/copilot-instructions.md  # Copilot-Regeln (verweist auf AGENTS.md)
├── AGENTS.md                        # ← Diese Datei — IMMER ZUERST LESEN
└── requirements.txt                 # psutil>=5.9
```

---

*Zuletzt aktualisiert: 2026-07-26 14:27 — Siemens-Benchmark abgeschlossen, Token-Limits entfernt, Vergleichstabelle erstellt.*

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
