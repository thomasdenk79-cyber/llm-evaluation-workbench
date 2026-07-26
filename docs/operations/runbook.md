# Runbook: LLM Benchmark Campaign

## Voraussetzungen

- Windows mit PowerShell
- Python + `psutil`
- Optional:
  - Ollama lokal
  - llama.cpp (llama-cli oder llama-server)
  - GGUF-Modelle lokal

## 1) Umgebung

```powershell
pip install -r requirements.txt
```

## 2) Ollama-only

```powershell
python .\scripts\llm_migration_benchmark.py --backend ollama --runs 1
```

## 3) llama.cpp-only (Server-Modus empfohlen)

```powershell
python .\scripts\llm_migration_benchmark.py `
  --backend llama_cpp `
  --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" `
  --llama-model "qwen36_35b_q4=C:\MODELLE\Qwen3.6-35B-Q4.gguf" `
  --runs 1
```

## 4) Kampagnenmodus mit Power-Profilen

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_benchmark_campaign.ps1 -Backend both
```

## 5) Ergebnisse

- Neue Ergebnisse werden standardmaessig unter `benchmark_results\` abgelegt (relativ zum Aufrufpfad).
- Historische Ergebnisse sind unter `data/benchmark_results_legacy\`.
