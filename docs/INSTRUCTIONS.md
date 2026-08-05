# Project Instructions – LLM Evaluation Workbench

## Purpose
Benchmarks local LLMs (Ollama, llama.cpp) against Siemens cloud models for migration and translation tasks.

## Setup
1. Install Ollama and ensure the `ollama` CLI is in PATH.
2. Install llama.cpp and place `llama-server.exe` and `llama.exe` in `C:\Users\z000g9hu\llama.cpp\bin`.
3. Open the workbench and use **Models & backends → Pull** for Ollama
   models or **Register GGUF** for an existing llama.cpp file:
   ```powershell
   python .\scripts\run_benchmark.py
   ```
4. Verify models:
   ```powershell
   ollama list
   ```
   For llama.cpp, check `C:\Users\z000g9hu\llama.cpp\models`.

## Benchmark execution
- Ollama backend:
  ```powershell
  python .\scripts\run_benchmark.py --config .\config\benchmark.toml
  ```
- llama.cpp backend (adjust model path as needed):
  ```powershell
  python .\scripts\run_benchmark.py --config .\config\local-campaign.toml
  ```

## Important rules (from AGENTS.md)
- Only one local LLM at a time (max 12 GB VRAM). Script warns if another `llama-server` runs.
- Per-test timeout is enforced by the selected campaign.
- Never delete existing benchmark results.
- Record all changes in `docs/PROJECT_STATE.md` and `docs/TODO.md`.

## Recovery
If the session crashes, run:
```powershell
git pull origin main
git status
```