# LLM Benchmark Vergleich: Lokal vs. Siemens Cloud

> Oracle→PostgreSQL Migration — 3 Tasks (DDL, PL/SQL, Validation), 3 Runs, Keyword-Scoring
> Stand: 2026-07-26

---

## Gesamtvergleich (sortiert nach Qualität)

| Modell | Backend | Qual% | TPS | Wall-ms | Rel% | Fehler |
|---|---|---|---|---|---|---|
| **ministral-3-14b-instruct-2512** | ☁️ Siemens | **95.83** | 36 | 6 521 | 88.9 | 1× 502 |
| **deepseek-v4-flash** | ☁️ Siemens | **92.59** | 115 | 21 906 | 100 | — |
| **qwen-3.6-27b** | ☁️ Siemens | **92.86** | 93 | 3 066 | 100 | — |
| **gpt-oss-120b** | ☁️ Siemens | **88.89** | **163** | 5 459 | 100 | — |
| qwen3-coder:30b | 🖥️ Ollama | 88.89 | 28 | 10 817 | 100 | — |
| qwen3.6:27b-q4_K_M | 🖥️ Ollama | 88.89 | 4 | 46 202 | 100 | — |
| qwen3-coder-next:q4_K_M | 🖥️ Ollama | 88.89 | 16 | 30 628 | 100 | — |
| Mistral-Small-24B-FP8 | ☁️ Siemens | 83.33 | 24 | 8 321 | 100 | — |
| deepseek-coder-v2:16b | 🖥️ Ollama | 84.13 | 55 | 7 705 | 100 | — |
| qwen2.5-coder:32b | 🖥️ Ollama | 79.17 | 3 | 50 528 | 100 | — |
| glm-4.7-flash:q4_K_M | 🖥️ Ollama | 77.78 | 22 | 17 098 | 100 | — |
| qwen3.6:35b-a3b-q4_K_M | 🖥️ Ollama | 72.22 | 29 | 16 065 | 100 | — |
| codestral:latest | 🖥️ Ollama | 72.22 | 8 | 26 447 | 100 | — |
| devstral-small-2:24b | 🖥️ Ollama | 72.22 | 5 | 34 023 | 100 | — |
| gpt-oss:20b (ngl99) | 🖥️ llama.cpp | 66.07 | 84 | 2 646 | 100 | — |
| deepseek-coder-v2-16b | 🖥️ llama.cpp | 72.22 | 14 | 9 351 | 100 | — |

---

## Siemens Cloud — Detail (3 Runs, ohne Token-Limits)

| Modell | Overall | Qual% | TPS | Wall-ms | CPU% | Rel% |
|---|---|---|---|---|---|---|
| gpt-oss-120b | 84.93 | **88.89** | **163** | 5 459 | 66 | 100 |
| deepseek-v4-flash | 83.36 | 92.59 | 115 | 21 906 | 55 | 100 |
| qwen-3.6-27b | 79.67 | **92.86** | 93 | **3 066** | 70 | 100 |
| ministral-3-14b-instruct-2512 | 75.96 | **95.83** | 36 | 6 521 | 67 | 88.9 |
| Mistral-Small-24B-FP8-dynamic | 69.21 | 83.33 | 24 | 8 321 | 65 | 100 |

> **Hinweis:** `deepseek-v4-flash` hatte zuvor 18% Qualität durch ein Token-Limit von 220.
> Nach Entfernung aller Token-Limits: **92.59%** — drittbestes Modell im Gesamtfeld.

---

## Lokale Modelle — Beste Konfigurationen (Ollama + llama.cpp)

| Modell | Backend | Qual% | TPS | Wall-ms |
|---|---|---|---|---|
| qwen3-coder:30b | Ollama | **88.89** | 28 | 10 817 |
| qwen3.6:27b-q4_K_M | Ollama | **88.89** | 4 | 46 202 |
| qwen3-coder-next:q4_K_M | Ollama | **88.89** | 16 | 30 628 |
| deepseek-coder-v2:16b | Ollama | 84.13 | **55** | 7 705 |
| qwen2.5-coder:32b | Ollama | 79.17 | 3 | 50 528 |
| glm-4.7-flash:q4_K_M | Ollama | 77.78 | 22 | 17 098 |
| qwen3.6:35b-a3b-q4_K_M | Ollama (highperf) | 72.22 | 29 | 16 065 |
| gpt-oss:20b ngl99 balanced | llama.cpp | 66.07 | **84** | 2 646 |

---

## Wichtigste Erkenntnisse

### Siemens Cloud schlägt lokal bei Qualität
- **Beste Qualität gesamt:** `ministral-3-14b-instruct-2512` mit **95.83%** (Cloud)
- **Schnellstes Cloud-Modell:** `gpt-oss-120b` mit **163 TPS** — schneller als jedes lokale Modell
- `qwen-3.6-27b` und `deepseek-v4-flash` erzielen beide ~92% Qualität

### Token-Limit war kritischer Bug
- `deepseek-v4-flash` mit Limit 220 Tokens: **18% Qualität**
- `deepseek-v4-flash` ohne Limit: **92.59% Qualität** → Unterschied durch Truncation

### Bestes Preis-Leistungs-Verhältnis lokal
- `deepseek-coder-v2:16b`: 84% Qualität bei 55 TPS — schnellstes lokales Modell mit guter Qualität
- `qwen3-coder:30b`: 89% Qualität bei 28 TPS — bester lokaler Allrounder

---

## Empfehlungen

| Szenario | Empfehlung | Grund |
|---|---|---|
| Beste Qualität (Cloud) | `ministral-3-14b-instruct-2512` | 95.83% — höchste Qualität überhaupt |
| Beste Balance (Cloud) | `qwen-3.6-27b` | 92.86% + 93 TPS + schnellste Antwort (3s) |
| Schnellstes (Cloud) | `gpt-oss-120b` | 163 TPS + 88.89% Qualität |
| Beste Qualität (lokal) | `qwen3-coder:30b` | 88.89%, 28 TPS, zuverlässig |
| Schnellstes (lokal) | `deepseek-coder-v2:16b` | 55 TPS, 84% Qualität |
| Kein Internet / Datenschutz | `qwen3-coder:30b` (Ollama) | Beste lokale Option |
