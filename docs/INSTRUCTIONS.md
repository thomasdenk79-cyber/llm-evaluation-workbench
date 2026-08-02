# Project Instructions – LLM Evaluation Workbench

## Purpose
Benchmarks local LLMs (Ollama, llama.cpp) against Siemens cloud models for migration and translation tasks.

## Setup
1. Install Ollama and ensure the `ollama` CLI is in PATH.
2. Install llama.cpp and place `llama-server.exe` and `llama.exe` in `C:\Users\z000g9hu\llama.cpp\bin`.
3. Run the model‑installation script (background):
   ```powershell
   cd llm-evaluation-workbench\scripts
   .\install_models.ps1
   ```
4. Verify models:
   ```powershell
   ollama list
   ```
   For llama.cpp, check `C:\Users\z000g9hu\llama.cpp\models`.

## Benchmark execution
- Ollama backend:
  ```powershell
  python .\scripts\llm_migration_benchmark.py --backend ollama --ollama-models "mixtral:8x22b-q4_K_M" --runs 3
  ```
- llama.cpp backend (adjust model path as needed):
  ```powershell
  python .\scripts\llm_migration_benchmark.py \
    --backend llama_cpp \
    --llama-server "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" \
    --llama-model "gpt-oss:20b=C:\Users\z000g9hu\llama.cpp\models\gpt-oss-20b.gguf" \
    --llama-ngl 99 --runs 3
  ```

## Important rules (from AGENTS.md)
- Only one local LLM at a time (max 12 GB VRAM). Script warns if another `llama-server` runs.
- Per‑test timeout is enforced (see install script).
- Never delete existing benchmark results.
- Record all changes in `docs/PROJECT_STATE.md` and `docs/TODO.md`.

## Recovery
If the session crashes, run:
```powershell
git pull origin main
git status
```