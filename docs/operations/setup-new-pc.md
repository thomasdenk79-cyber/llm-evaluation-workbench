# Setup-Anleitung: Neue Windows-Installation (LLM Workbench)

> Dieses Dokument erklärt Schritt für Schritt wie die vollständige LLM-Arbeitsumgebung
> auf einem neuen oder frisch installierten Windows-PC eingerichtet wird.
> **Alles liegt in OneDrive gesichert** — nichts muss neu konfiguriert werden, nur wiederhergestellt.

---

## 0. Architektur: Wie alles zusammenhängt

```
OneDrive\GIT\          ← alle Repos + globale Agent-Regeln
    │
    ├── AGENTS.md      ← MASTER: Eine Datei für ALLE AI-Agents
    ├── llm-evaluation-workbench\
    ├── throne-liberty-eu-kalender\
    └── ...

C:\GIT\                ← Junction (mklink /J) → OneDrive\GIT\
    └── AGENTS.md      ← selbe Datei, automatisch via Junction

Agent-spezifische Symlinks (erstellt durch Autoinstaller):
    %USERPROFILE%\CLAUDE.md                    → C:\GIT\AGENTS.md  (Claude Code)
    %USERPROFILE%\.codex\instructions.md       → C:\GIT\AGENTS.md  (OpenAI Codex CLI)
    %USERPROFILE%\.cursorrules                 → C:\GIT\AGENTS.md  (Cursor)
    %APPDATA%\Code\User\prompts\global-agent-rules.instructions.md  → C:\GIT\AGENTS.md  (Copilot)

Env-Vars (User-Scope, persistent):
    AI_AGENT_INSTRUCTIONS = C:\GIT\AGENTS.md  (universeller Zeiger)
    CODEX_SYSTEM_PROMPT   = <Inhalt>           (für Agents ohne Datei-Lesen)
```

**Nur `C:\GIT\AGENTS.md` pflegen — alle Agents sehen es sofort.**

---

## 0b. Voraussetzungen

| Was | Warum | Installieren via |
|---|---|---|
| Windows 11 | Plattform | — |
| Python 3.11+ | Benchmark-Skript | `winget install Python.Python.3.13` |
| Git | Repo-Verwaltung | `winget install Git.Git` |
| VS Code | Copilot-Agent, IDE | `winget install Microsoft.VisualStudioCode` |
| NVIDIA-Treiber | GPU-Offload für llama.cpp | [nvidia.com/drivers](https://www.nvidia.com/drivers) |

**Schnellinstall via Autoinstaller (empfohlen — läuft als Admin, macht alles automatisch):**
```
C:\Users\z000g9hu\OneDrive - Siemens AG\tools\scripts\TommysWin11Autoinstaller.bat
```
Kategorien wählen:
- **"System"** → erstellt `C:\GIT` Junction + setzt Env-Vars
- **"Development"** → VS Code Extensions, Copilot, Siemens LLM, `restore_agent_rules.ps1` (setzt alle Agent-Symlinks)
- **"AI Tools"** → Siemens LLM konfigurieren

---

## 1. Ollama (lokale LLMs)

### 1a. Installieren
```powershell
winget install Ollama.Ollama
# oder direkt: https://ollama.com/download
```

Ollama läuft als Windows-Dienst auf Port `11434`. Nach Installation automatisch gestartet.

**Installationspfad:** `C:\Users\z000g9hu\AppData\Local\Programs\Ollama\ollama.exe`
**Modell-Store:** `C:\Users\z000g9hu\.ollama\models\` (aktuell ~632 GB!)

> ⚠️ **Hinweis:** Der Modell-Store ist NICHT in OneDrive — Modelle müssen nach Neuinstallation
> neu heruntergeladen werden. Bei einem PC-Wechsel ggf. Ordner extern sichern.

### 1b. Modelle herunterladen (Benchmark-relevante, nach Priorität)

```powershell
# Haupt-Benchmark-Modelle:
ollama pull qwen3.6:27b-q4_K_M        # 17 GB — beste Migration-Qualität
ollama pull qwen3.6:35b-a3b-q4_K_M    # 23 GB — beste Übersetzungsqualität
ollama pull gpt-oss:20b               # 13 GB — schnellstes Modell
ollama pull qwen3-coder:30b           # 18 GB — bester Allrounder (empfohlen!)
ollama pull deepseek-coder-v2:16b     # 8.9 GB — bestes Speed/Qualitäts-Verhältnis

# Erweiterte Modelle (alle getesteten):
ollama pull codestral:latest
ollama pull qwen2.5-coder:32b-instruct-q4_K_M
ollama pull devstral-small-2:24b
ollama pull glm-4.7-flash:q4_K_M
ollama pull qwen3-coder-next:q4_K_M   # 51 GB — sehr groß

# Quantisierungs-Varianten (Studie ausstehend):
ollama pull qwen3.6:27b-q8_0          # 29 GB
ollama pull qwen3:32b                 # 20 GB — noch nicht getestet
```

### 1c. Ollama-Konfiguration prüfen
```powershell
ollama list       # Alle installierten Modelle
ollama ps         # Aktuell geladene Modelle
curl http://localhost:11434/api/version  # API erreichbar?
```

---

## 2. llama.cpp (lokale LLMs, GPU-optimiert)

### 2a. Binaries wiederherstellen
llama.cpp ist **nicht via winget installiert** — die Binaries liegen im Benutzerordner:

```
C:\Users\z000g9hu\llama.cpp\
├── bin\
│   ├── llama-server.exe    ← Haupt-Binary (Server-Modus, empfohlen)
│   └── llama.exe           ← CLI-Fallback
└── models\                 ← GGUF-Modelle (lokal, nicht OneDrive)
    ├── gpt-oss-20b-MXFP4.gguf           (11.3 GB)
    └── Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf (20.8 GB)
```

**Wenn `llama-server.exe` fehlt:** Aktuellen Release von GitHub holen:
```powershell
# Neuesten Release von github.com/ggml-org/llama.cpp herunterladen
# Windows CUDA-Build wählen: llama-b*-bin-win-cuda-cu12.*.zip
# Entpacken nach C:\Users\z000g9hu\llama.cpp\bin\
```

### 2b. GGUF-Modelle
Die GGUF-Dateien müssen nach `C:\Users\z000g9hu\llama.cpp\models\` gelegt werden.
Sie sind NICHT in OneDrive gesichert (zu groß). Bei Neuinstallation von HuggingFace herunterladen:

| Modell | HuggingFace | Größe |
|---|---|---|
| gpt-oss-20b-MXFP4.gguf | Microsoft/gpt-oss-20b-MXFP4-GGUF | 11.3 GB |
| Qwen_Qwen3.6-35B-A3B-Q4_K_M.gguf | Qwen/Qwen3.6-35B-A3B-GGUF | 20.8 GB |

### 2c. Test
```powershell
& "C:\Users\z000g9hu\llama.cpp\bin\llama-server.exe" --version
```

### 2d. OpenCode-Modellpicker und Auto-Load

```powershell
cd C:\GIT\llm-evaluation-workbench
powershell -ExecutionPolicy Bypass -File .\scripts\install_llama_router_startup.ps1 -StartNow
```

Das Script übernimmt alle lokal installierten Ollama-Tags und alle vollständigen GGUF-Sätze
in getrennte OpenCode-Provider. Ollama und der llama.cpp-Router laden das im Picker gewählte
Modell erst bei der ersten Anfrage. Der Router startet künftig über den Benutzer-Autostart und
hält maximal ein GGUF-Modell gleichzeitig geladen. Für jedes Modell wird dessen höchster
deklarierter Kontext persistent gesetzt; Flash Attention und Q4-KV-Cache begrenzen den
zusätzlichen Speicherbedarf.

---

## 3. Siemens LLM (Cloud, über api.siemens.com)

### 3a. Token erstellen
1. Browser: [my.siemens.com](https://my.siemens.com) → **"LLM API"** → Token erstellen
2. Scope: `llm`
3. Token beginnt mit `SIAK-...`

### 3b. Token speichern (ONE-TIME)
```
C:\Users\z000g9hu\OneDrive - Siemens AG\tools\myconfigfiles\code.siemens.com_api_ai_token.txt
```
Nur den Token reinschreiben, nichts anderes. Die Datei liegt in OneDrive — sie überlebt jede Neuinstallation.

> ⚠️ Token hat eine Laufzeit. Wenn die Siemens-Modelle 401-Fehler zurückgeben, Token erneuern
> und Datei aktualisieren, dann `configure_siemens_llm.ps1` erneut ausführen.

### 3c. Konfiguration einrichten (Einzeiler)
```powershell
powershell -ExecutionPolicy Bypass -File `
  "C:\Users\z000g9hu\OneDrive - Siemens AG\tools\scripts\configure_siemens_llm.ps1"
```

Das Skript richtet automatisch ein:
- ✅ **VS Code** (`chatLanguageModels.json`) → Modelle im Model-Picker
- ✅ **VS Code Portable** (`C:\tools\PortableApps\VSCode\...`)
- ✅ **Env-Vars** `SIEMENS_LLM_API_KEY` und `OPENAI_API_KEY` (User-Level, persistent)
- ✅ **OpenCode** (`~/.config/opencode/opencode.json`) für TUI-Betrieb
- ✅ **Continue Extension** (`~/.continue/config.yaml`) wenn installiert

**Siemens-Modelle nach Einrichtung:**

| Modell-ID | Name in VS Code | Context | Vision |
|---|---|---|---|
| `deepseek-v4-flash` | Siemens DeepSeek V4 Flash | 1M Token | ❌ |
| `qwen-3.6-27b` | Siemens Qwen 3.6 27B | 262K Token | ✅ |
| `ministral-3-14b-instruct-2512` | Siemens Ministral 3-14B | 256K Token | ✅ |

**API-Endpunkt:** `https://api.siemens.com/llm/v1/chat/completions`

### 3d. Verifizieren
```powershell
# Token aus Datei lesen und Verbindung testen:
$token = Get-Content "C:\Users\z000g9hu\OneDrive - Siemens AG\tools\myconfigfiles\code.siemens.com_api_ai_token.txt" -Raw | ForEach-Object { $_.Trim() }
$headers = @{ Authorization = "Bearer $token"; "Content-Type" = "application/json" }
$body = '{"model":"ministral-3-14b-instruct-2512","messages":[{"role":"user","content":"Hi"}],"max_tokens":5}'
Invoke-RestMethod -Uri "https://api.siemens.com/llm/v1/chat/completions" -Method Post -Headers $headers -Body $body
```

---

## 4. Benchmark-Workbench einrichten

```powershell
# Repo klonen (falls nicht vorhanden):
git clone https://github.com/thomasdenk79-cyber/llm-evaluation-workbench.git D:\git\llm-evaluation-workbench

# Oder via OneDrive (falls synchronisiert):
# D:\git ist oft mit "C:\Users\z000g9hu\OneDrive - Siemens AG\GIT\" verknüpft

cd D:\git\llm-evaluation-workbench

# Dependencies installieren:
pip install -r requirements.txt  # psutil>=5.9
```

### 4b. Schnelltest nach Setup
```powershell
cd D:\git\llm-evaluation-workbench

# Ollama-Test (qwen3-coder:30b, 3 Runs):
python .\scripts\llm_migration_benchmark.py --backend ollama --ollama-models "qwen3-coder:30b" --runs 1

# Siemens-Test (Token aus Env-Var oder OneDrive-Datei automatisch gefunden):
python .\scripts\llm_migration_benchmark.py --backend siemens --runs 1

# Alle Backends:
python .\scripts\llm_migration_benchmark.py --backend all --runs 1
```

---

## 5. Windows Power-Profile (für Benchmark-Genauigkeit)

```powershell
# Ultimatives Power-Profil aktivieren (liegt im Autoinstaller):
powershell -ExecutionPolicy Bypass -File `
  "C:\Users\z000g9hu\OneDrive - Siemens AG\tools\scripts\activate_ultimative_powerplan.ps1"

# Oder manuell:
# Balanced:   powercfg /setactive 381b4222-f694-41f0-9685-ff5bb260df2e
# High-Perf:  powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
```

> Benchmark-Erfahrung: **Balanced schlägt High-Perf bei llama.cpp** (84 vs 50 TPS bei gpt-oss).
> Immer beide Profile testen und vergleichen!

---

## 6. VS Code Chat-Verlauf wiederherstellen

Der Chat-Verlauf (Copilot-Sessions, Agent-Memories) wird von `backup_vscode_chat.ps1` gesichert.

```powershell
# Backup liegt in OneDrive:
# C:\Users\z000g9hu\OneDrive - Siemens AG\ai_agent\vscode-chat-backup\

# Wiederherstellen:
powershell -ExecutionPolicy Bypass -File `
  "C:\Users\z000g9hu\OneDrive - Siemens AG\tools\scripts\restore_vscode_chat.ps1"
```

---

## 7. Datei-Überblick: Was wo liegt

| Was | Pfad | In OneDrive? |
|---|---|---|
| Siemens API Token | `OneDrive\tools\myconfigfiles\code.siemens.com_api_ai_token.txt` | ✅ |
| VS Code chatLanguageModels.json | `%APPDATA%\Code\User\chatLanguageModels.json` | ❌ (via Script) |
| Ollama Modelle | `C:\Users\z000g9hu\.ollama\models\` | ❌ (re-download) |
| GGUF Modelle | `C:\Users\z000g9hu\llama.cpp\models\` | ❌ (re-download) |
| llama.cpp Binaries | `C:\Users\z000g9hu\llama.cpp\bin\` | ❌ (re-download) |
| Benchmark-Repo | `D:\git\llm-evaluation-workbench\` | ✅ (via git+OneDrive) |
| Setup-Scripts | `OneDrive\tools\scripts\` | ✅ |
| VS Code Chat-Backup | `OneDrive\ai_agent\vscode-chat-backup\` | ✅ |
| Autoinstaller-Config | `OneDrive\tools\scripts\autoinstaller_config.ini` | ✅ |

---

## 8. Autoinstaller-Integration

Der **Tommy Win11 Autoinstaller** (`TommysWin11Autoinstaller.bat`) kennt die Kategorie `ai_tools`:
```ini
[actions.ai_tools]
powershell = backup_vscode_chat.ps1|configure_siemens_llm.ps1
```

Das bedeutet: Nach Windows-Neuinstallation einfach den Autoinstaller starten,
Kategorie **"AI Tools"** anklicken → Siemens LLM wird automatisch konfiguriert.

Ollama und llama.cpp müssen manuell installiert werden (zu groß für Autoinstaller).

---

## 9. Checkliste Neuinstallation

```
[ ] Python 3.13 installiert (winget install Python.Python.3.13)
[ ] Git installiert (winget install Git.Git)
[ ] VS Code installiert (winget install Microsoft.VisualStudioCode)
[ ] GitHub Copilot Extension installiert (ms-vscode.copilot)
[ ] Ollama installiert (winget install Ollama.Ollama)
[ ] Ollama Modelle heruntergeladen (mindestens qwen3-coder:30b + gpt-oss:20b)
[ ] llama.cpp Binaries nach C:\Users\...\llama.cpp\bin\ kopiert/entpackt
[ ] GGUF-Modelle nach C:\Users\...\llama.cpp\models\ kopiert/heruntergeladen
[ ] OpenCode-Modelle synchronisiert und llama.cpp-Router-Autostart eingerichtet
[ ] Siemens Token in code.siemens.com_api_ai_token.txt vorhanden (OneDrive)
[ ] configure_siemens_llm.ps1 ausgeführt
[ ] pip install -r requirements.txt (im Benchmark-Repo)
[ ] Schnelltest: python llm_migration_benchmark.py --backend ollama --runs 1
[ ] Schnelltest: python llm_migration_benchmark.py --backend siemens --runs 1
[ ] VS Code neu laden (Ctrl+Shift+P → Reload Window)
[ ] Siemens-Modelle im Model-Picker sichtbar
```
