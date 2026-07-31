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
- `scripts/sync_opencode_local_models.py`
  Synchronisiert installierte Ollama- und llama.cpp-Modelle als getrennte OpenCode-Provider
- `scripts/configure_ollama_max_context.py`
  Setzt für jedes lokale Ollama-Modell persistent den höchsten deklarierten Kontext
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

3) Alle lokalen Modelle in OpenCode bereitstellen:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_llama_router_startup.ps1 -StartNow
```

Ollama lädt das ausgewählte Modell bei der ersten Anfrage. Der llama.cpp-Router arbeitet
genauso und hält höchstens ein GGUF-Modell gleichzeitig geladen. Eine laufende OpenCode-Sitzung
nach der Synchronisation neu starten, damit der Model-Picker die neuen Einträge übernimmt.
Nach neu hinzugefügten GGUF-Dateien den Installer mit `-StartNow -Restart` erneut ausführen.
Der Installer setzt außerdem jedes erreichbare Ollama-Modell und jedes GGUF-Preset auf dessen
jeweiligen maximal deklarierten Kontext.

4) Gemischte Kampagne (Ollama + llama.cpp):

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

## Automation

- GitHub Actions CI:
  - Python Compile Checks
  - Benchmark CLI Smoke Test (`--help`)
- CodeQL Security Scan (Push/PR + Weekly Schedule)
- Dependabot fuer Python-Dependencies und GitHub Actions

## License

Aktuell noch ohne finalen OSS-Lizenzentscheid.
Bis zur finalen Freigabe bitte als "all rights reserved" behandeln.
