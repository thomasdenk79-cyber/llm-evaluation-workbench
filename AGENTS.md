# LLM Evaluation Workbench - project router

- **AI-ACCESS:** allowed
- **INHERITS:** `C:\GIT\AGENTS.md` and `C:\GIT\standards\AGENTS.md`
- **OVERRIDES:** local benchmark safety, reporting, reproducibility, and lifecycle rules below
- **SCOPE:** this repository

> Mandatory before work: read `C:\GIT\user-memory\profile.md`,
> `C:\GIT\agent-memory\INDEX.md`, `C:\GIT\standards\AGENTS.md`, then this file.

## Aktueller Stand

### Stand: 2026-07-30

- Living-memory-Verstaendnisbenchmark implementiert:
  - Tool-Agent-Track navigiert den echten Workspace ueber OpenCode.
  - Pure-Model-Track nutzt fuer API-Modelle ausschliesslich ein synthetisches Fixture.
  - Restricted echte Workspace-Faelle sind auf lokale `ollama/*`-Modelle begrenzt.
  - Gewichtetes Scoring, Diagnose-Follow-up und track-getrennte Aggregation vorhanden.
  - Dry-runs und Unit-Tests bestanden; noch keine Modellkampagne gestartet.
  - Siemens-Pure-Model-Kampagne abgeschlossen: alle 5 chatfaehigen Modelle,
    70/70 Basisantworten ohne Fehler.
  - V1-Lexical-Score: Ministral 73.64, Qwen 73.56, GPT-OSS 70.75,
    DeepSeek 64.34, Mistral Small 51.95.
  - Ein fokussierter V2-Naming-Nachtest erreichte nach sechs exakten Regeln plus
    Beispiel bei allen Modellen 100 Prozent.
  - Kanonischer Laufbericht:
    `benchmark_results\living-memory-siemens-20260730-1418\evaluation.md`.
  - Hard-Agent-V2 abgeschlossen: echte Tool-Handlungen, 25% Antwort/75% Zustand.
  - Ergebnis ohne separat nachgetestete Sprache: Qwen 95.74, DeepSeek 94.80,
    Ministral 78.41, GPT-OSS 69.89, Mistral Small 0 (Tool-API inkompatibel).
  - Nur Ministral pflegte Agent-Memory proaktiv; Qwen/DeepSeek pflegten
    User-Memory. Ministral verletzte `AI-ACCESS: denied`.
  - Hard-Bericht:
    `benchmark_results\living-memory-hard-siemens-v2-20260730\evaluation.md`.

- Agent: opencode | llm: qwen-3.6-27b | role: benchmark orchestration (gestoppt)
- **Aktivierter P1-Fix (2026-07-29):**
  - `llama.cpp`-Server-Start repariert: `--threads` → `-t` (Build 132.x CLI-Bruch)
  - Debug-Logging für llama-server aktiviert (`stdout` in temporäre Log-Datei)
  - `qwen3.6:27b GGUF` ist inkompatibel zu llama.cpp Build 132.x (`qwen35.rope.dimension_sections` erwartet 4, hat 3) — Ollama-nativ nur lauffähig
  - `qwen3.6:35b-a3b-q4` via llama.cpp erfolgreich (94.68 Overall, 13.38 Tok/s, ngl=99, Balanced)
  - **Alle LLM-Testläufe vom User gestoppt** — zu hoher Ressourcenverbrauch, Wechsel zur manuellen Steuerung
  - **CRITICAL:** Niemals alle bestehenden Benchmark-Results löschen! Alte Ergebnisse sind Vergleichtsgrundlage.
  - **CRITICAL:** Maximal 1 lokales LLM gleichzeitig laufen lassen (12 GB VRAM-Limit). Kein Parallel-Run zweier lokaler Modelle.
  - **TODO:** Pro-Testlauf-Timeout einbauen, damit bei Hängern automatisch zum nächsten Test gesprungen wird

### Open Handover-Tasks (Next Session — Priority)

**A. Modelle installieren (nur Installation, NOCH KEINE Testläufe):**

| Modell | Quantisierung | Status |
|---|---|---|
| Mixtral 8x22B (MoE) | Q4_K_M, Q8_0 | todo |
| Deepseek-v4-flash (MoE) | Q4_K_M, Q8_0 | todo |
| GLM-5.x (MoE) | Q4_K_M, Q8_0 | todo |
| Llama 3 8B (Baseline) | Q4_K_M, Q8_0 | todo |
| Qwen 1.8B (Baseline) | Q4_K_M, Q8_0 | todo |

**Wichtig:** Erst Installation/Konfiguration vorbereiten. Benchmark-Tests erst interaktiv mit User starten.

**B. Bestehende Next-Actions (vorherige Session):**

1. Retry `ministral-3-14b-instruct-2512` (eine Validierung lieferte HTTP 502)
2. Test missing local baselines: `qwen3:32b`, `phi4-mini:3.8b-q4_K_M`, `deepseek-r1:8b`, `llama3.1:8b`
3. Implement the sandbox harness only when explicitly requested
4. **NEU:** Benchmark-Script pro-Testlauf-Timeout implementieren (auto-skip bei Hängern)
5. **NEU:** Benchmark-Script Warnung einbauen: "Max 1 lokales LLM gleichzeitig (12 GB VRAM)"

## Nächster Schritt

- Keine Modellkampagne ohne interaktive Freigabe starten.
- Nach Freigabe zuerst je ein starkes, mittleres und lokales Modell seriell mit
  beiden Verstaendnistracks kalibrieren und die Track-Differenz auswerten.
- Hard-Agent-Findings in technische Gates fuer Owner-Scope, Memory-Checkpoint,
  Sprache pro Turn, Chat-Validierung und Git-Abschluss ueberfuehren.

## Project purpose

Compare local Ollama/llama.cpp models and Siemens cloud models for:

- Oracle-to-PostgreSQL migration tasks;
- translation tasks;
- quality, reliability, throughput, efficiency, and hardware behavior.

## Sources of truth

| Information | Canonical source |
|---|---|
| Current handoff and mandatory rules | this `AGENTS.md` |
| Benchmark implementation | `scripts\llm_migration_benchmark.py` |
| Campaign orchestration | `scripts\run_benchmark_campaign.ps1`, `scripts\run_local_campaign.ps1` |
| Human-readable current report | `docs\project\benchmark_report.md` |
| Detailed live plan/status | `docs\project\benchmark_report_details.md` |
| Comparison table | `docs\project\comparison_table.md` |
| Operations | `docs\operations\runbook.md` |
| Setup | `docs\operations\setup-new-pc.md` |
| Change history | `docs\project\changelog.md` |
| Raw/final results | `benchmark_results\` and `data\benchmark_results_legacy\` |

Do not duplicate large result tables in this router. Update the canonical report and
keep only decision-relevant summaries here.

## Environment

```text
llama-server: C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe
llama-cli:    C:\Users\z000g9hu\llama.cpp\bin\llama.exe
benchmark:    scripts\llm_migration_benchmark.py
results:      benchmark_results\
legacy:       data\benchmark_results_legacy\
```

Siemens endpoint: `https://api.siemens.com/llm/v1/chat/completions`.
The token file contains multiple lines; read the first line beginning with `SIAK-`.
Never write the token value into code, logs, documentation, or memory.

Power plans:

```text
Balanced:  381b4222-f694-41f0-9685-ff5bb260df2e
High-Perf: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```

## Decision-relevant benchmark summary

### Siemens cloud final (2026-07-26, three runs)

| Model | Overall | Quality | TPS | Reliability |
|---|---:|---:|---:|---:|
| `gpt-oss-120b` | 84.93 | 88.89% | 163 | 100% |
| `deepseek-v4-flash` | 83.36 | 92.59% | 115 | 100% |
| `qwen-3.6-27b` | 79.67 | 92.86% | 93 | 100% |
| `ministral-3-14b-instruct-2512` | 75.96 | 95.83% | 36 | 88.9% |
| `Mistral-Small-24B-Instruct-2501-FP8-dynamic` | 69.21 | 83.33% | 24 | 100% |

### Local recommendations

| Purpose | Model/configuration | Reason |
|---|---|---|
| Best local all-rounder | `qwen3-coder:30b` via Ollama | 88.89% quality, about 28 TPS |
| Fast local model | `deepseek-coder-v2:16b` via Ollama | about 55 TPS, 84% quality |
| llama.cpp | `gpt-oss:20b`, Balanced, `ngl=99` | about 84 TPS |
| Translation | `qwen3.6:35b-a3b-q4_K_M` | 100% heuristic quality, about 37 TPS |

`ngl` is critical for llama.cpp. Set it as high as available VRAM safely permits.
Balanced has outperformed High-Perf in measured llama.cpp runs; test both rather
than assuming the profile name predicts performance.

## Quick commands

```powershell
cd D:\git\llm-evaluation-workbench

# Siemens, all configured models
python .\scripts\llm_migration_benchmark.py --backend siemens --runs 3

# Retry the model with one HTTP 502 sample
python .\scripts\llm_migration_benchmark.py --backend siemens `
  --siemens-model ministral-3-14b-instruct-2512 --runs 3

# One Ollama model
python .\scripts\llm_migration_benchmark.py --backend ollama `
  --ollama-models "qwen3-coder:30b" --runs 3

# llama.cpp with GPU offload
python .\scripts\llm_migration_benchmark.py `
  --backend llama_cpp `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --llama-model "gpt-oss:20b=C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf" `
  --llama-ngl 99 --runs 3
```

## Reporting contract

- **Heuristik-Score** is keyword-based benchmark guidance, not an official correctness score.
- Composite overall score: 60% quality, 20% speed, 10% reliability, 10% efficiency.
- Raw rows preserve timing, token, score, CPU, RAM, GPU, VRAM, lifecycle, preview, and error fields.
- Final runs append to `benchmark_results\migration_llm_bench_history.csv`.
- Resume must skip already successful `backend+model+case+run` samples and retry missing/error samples.
- The compact live report contains exactly the planned model rows; case/run detail belongs in the details report.
- Changing output schemas or visible metrics requires report, runbook, and changelog updates in the same change.

## Non-negotiable project rules

1. No silent fallbacks. Surface errors in logs and result records.
2. Do not use `--no-verify`.
3. Do not rename or delete legacy result data without explicit approval.
4. Preserve CLI compatibility unless the task explicitly authorizes a breaking change.
5. Preserve CSV/JSON compatibility or document and migrate the schema deliberately.
6. Keep seeds, prompts, settings, model lifecycle, and effective campaign parameters reproducible.
7. Compare backends only under equivalent semantics; document unavoidable differences.
8. Run existing smoke/tests/docs checks after related changes.
9. Update this current-state block after every significant benchmark or implementation phase.
10. Never end a stable work unit with uncommitted project changes when session-end policy requires commit/push.

## Memory routing

At every significant finding, completed phase, topic/project switch, pre-compaction,
or completion response:

| Information | Destination |
|---|---|
| Technical state and next command | this file and the relevant report/runbook |
| Detailed benchmark result | canonical report plus result files |
| Personal/professional Thomas context | `C:\GIT\user-memory\profile.md` |
| Cross-project handoff | `C:\GIT\user-memory\session-log.md` |
| Reusable agent learning | route through `C:\GIT\agent-memory\INDEX.md` |

Do not wait for Thomas to ask for persistence.

## Validation

```powershell
python .\scripts\llm_migration_benchmark.py --help
python .\standards\scripts\test_docs.py
python -m mkdocs build --strict
```

Run only existing checks relevant to the changed surface.
