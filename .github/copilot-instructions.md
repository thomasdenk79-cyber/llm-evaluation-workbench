# GitHub Copilot Instructions — LLM Evaluation Workbench

> Lies zuerst `AGENTS.md` — das ist das vollständige lebende Gedächtnis des Projekts.
> Diese Datei ergänzt es mit Copilot-spezifischen Arbeitsregeln.

---

## Kontext auf einen Blick

**Was ist das?** Benchmark-Workbench für lokale LLMs. Ziel: beste Modelle für
Oracle→PostgreSQL-Migration und DE→EN-Übersetzung finden.

**Wo sind wir?** Erste Benchmark-Runde abgeschlossen (2026-07-25).
Ergebnisse in `data/benchmark_results_legacy/`. Analyse und fehlende Modelle stehen noch aus.

**Aktueller Champion Migration:** `qwen3-coder:30b` (Ollama) — 88.89% Qualität, 28 TPS.
**Aktueller Champion Übersetzung:** `qwen3.6:35b-a3b-q4_K_M` (Ollama) — 100% Qualität, 37 TPS.

---

## Sofort lauffähige Befehle

```powershell
# Schnelltest (qwen3-coder:30b, Ollama):
cd D:\git\llm-evaluation-workbench
python .\scripts\llm_migration_benchmark.py --backend ollama --ollama-models "qwen3-coder:30b" --runs 3

# Alle Ollama-Modelle automatisch testen:
python .\scripts\llm_migration_benchmark.py --backend ollama --runs 1

# llama.cpp (gpt-oss, GPU-optimiert, ngl=99):
python .\scripts\llm_migration_benchmark.py `
  --backend llama_cpp `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --llama-model "gpt-oss:20b=C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b-MXFP4.gguf" `
  --llama-ngl 99 --runs 3

# Kampagne (beide Backends, beide Power-Profile, strukturierte Ausgabe):
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark_campaign.ps1 -Backend both -Runs 3
```

---

## Copilot-Arbeitsregeln

### Das Grundprinzip: "Was" ist kostenlos — "Warum" muss geschrieben werden

```
git diff / git log   → speichert automatisch WAS sich geändert hat
Code selbst          → zeigt WAS er tut

Commit-Message       → erklärt WARUM diese Änderung jetzt
Code-Kommentar       → erklärt WARUM genau diese Zeile so (höchster Detailgrad)
AGENTS.md            → erklärt WARUM diese Richtung/dieses Feature
```

**Kommentier-Regel:** Nur WARUM kommentieren, nie WAS. Wenn der Kommentar
beschreibt was der Code tut — löschen. Git weiß das schon.

```python
# ❌ WERTLOS
options["num_predict"] = -1  # setzt num_predict auf -1

# ✅ WERTVOLL — das steht nirgendwo sonst
# Ollama bricht bei num_predict=0 mit Timeout ab; -1 = kein Output-Limit.
# Nicht weglassen: Default wäre 128 Token, zu kurz für SQL-Ausgaben.
options["num_predict"] = -1
```

### Bei Code-Änderungen
1. CLI-Argumente in `llm_migration_benchmark.py` nicht brechen (--backend, --runs, --llama-model, --llama-ngl).
2. CSV/JSON-Ausgabeformat stabil halten — Legacy-Ergebnisse müssen kompatibel bleiben.
3. Nach Änderungen: `README.md` und `docs/operations/runbook.md` prüfen/anpassen.
4. Changelog-Eintrag in `docs/project/changelog.md` ergänzen.

### Bei neuen Benchmark-Läufen
1. Ergebnisse in `AGENTS.md` Abschnitt 4 nachtragen (Tabellen aktuell halten).
2. Interessante Befunde in Abschnitt 5 (Offene Aufgaben) abhaken oder ergänzen.
3. Neue Modell-Tags in die Modell-Tabelle (Abschnitt 3) eintragen.

### Was NICHT tun
- Keine `--no-verify` Commits.
- Kein Umbenennen/Löschen von Legacy-Ergebnisdaten ohne Rückfrage.
- Keine Silent-Fallbacks in Benchmark-Logik einbauen.

---

## Bekannte Fallstricke

| Problem | Ursache | Lösung |
|---|---|---|
| llama.cpp sehr langsam | ngl=0 (kein GPU-Offload) | `--llama-ngl 20` bis `99` |
| Ollama-Modell-Tags inkonsistent | Ollama vs llama.cpp-Name | Immer `ollama list` prüfen |
| VRAM-OOM bei großen Modellen | > 8 GB Modell | Kleinere Quant oder Ollama-Swapping |
| Balanced schlägt High-Perf (llama.cpp) | OS-Scheduler-Verhalten | Immer beide Profile testen |
| quality_score = 0 | Prompt-Format oder Modell denkt auf Englisch | `/no_think` testen oder Prompt anpassen |

---

## Nächste konkrete Aufgaben

1. **Quant-Vergleich qwen3.6:27b**: `q4_K_M` vs `q8_0` — Speed/Qualitäts-Trade-off messen
2. **Kleine Modelle testen**: `phi4-mini:3.8b`, `deepseek-r1:8b`, `llama3.1:8b` als untere Baseline
3. **qwen3:32b testen**: direkte Alternative zu qwen3-coder:30b, ähnliche Größe
4. **HTML-Ergebnisreport**: Tabelle aller Runs aus CSVs aggregieren und als `docs/results/summary.html` ausgeben
5. **Empfehlungsdokument**: `docs/project/recommendation.md` mit finaler Modellwahl + Begründung
