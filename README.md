# LLM Evaluation Workbench

Zentrale Sammlung und strukturierte Ausfuehrungsbasis fuer lokale LLM-Benchmarks
(Ollama + llama.cpp) mit Fokus auf Oracle->PostgreSQL-Migration, Coding und Translation.

## Ziele

- Einheitliche, reproduzierbare Benchmarks fuer lokale Modelle.
- Vergleich Ollama vs llama.cpp mit gleichen oder vergleichbaren Parametern.
- Bewertung mit Qualitaets-Score, Laufzeit, Tokens/s sowie CPU/GPU/VRAM-Metriken.
- Vergleich unter unterschiedlichen Windows-Energieprofilen.

## Struktur

- `scripts/llm_migration_benchmark.py`  
  Hauptbenchmark (aus bestehender Session konsolidiert)
- `scripts/metrics/`  
  Zusaetzliche Metrik-Sammler aus Altbestand
- `scripts/run_benchmark_campaign.ps1`  
  Wrapper fuer komplette Kampagnen inkl. Power-Profil-Steuerung
- `data/benchmark_results_legacy/`  
  Bereits vorhandene historische Benchmark-Ergebnisse
- `docs/operations/runbook.md`  
  Schritt-fuer-Schritt-Ausfuehrung

## Schnellstart

1) Python-Abhaengigkeit installieren:

```powershell
pip install -r requirements.txt
```

2) Einfache Kampagne (nur Ollama):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark_campaign.ps1 -Backend ollama
```

3) Gemischte Kampagne (Ollama + llama.cpp):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark_campaign.ps1 `
  -Backend both `
  -LlamaServer "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  -LlamaModel "qwen36_35b_q4=C:\MODELLE\Qwen3.6-35B-Q4.gguf"
```

## Hinweise

- Fuer konsistente Vergleiche immer gleiche Prompts, Seeds und Sampling-Settings nutzen.
- Bei starken Score-Abweichungen zwischen Backends zuerst Parametergleichheit pruefen.
- Aktive VSCode/Ollama/andere Last kann Messergebnisse verfälschen.

## License

Aktuell noch ohne finalen OSS-Lizenzentscheid.
Bis zur finalen Freigabe bitte als "all rights reserved" behandeln.
