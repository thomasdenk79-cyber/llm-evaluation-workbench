# AGENTS.md (SSOT)

## Scope

Diese Datei ist die Single Source of Truth fuer Agent-/Copilot-Arbeitsregeln in diesem Repository.

## Grundregeln

1. Keine stillen Fallbacks fuer Benchmark-Fehler:
   - Fehler explizit loggen und im Ergebnis ausweisen.
2. Reproduzierbarkeit vor Geschwindigkeit:
   - Seeds, Prompt-Sets und Parameter transparent halten.
3. Vergleichbarkeit sichern:
   - Ollama und llama.cpp nur mit aequivalenten Settings vergleichen.
4. Ergebnisstruktur stabil halten:
   - CSV/JSON-Ausgabeformat nicht brechen, ohne Doku/Changelog-Update.
5. Dokumentation als Vertrag:
   - Bei Struktur- oder CLI-Aenderungen immer Runbook + README aktualisieren.

## Benchmark-Policy

- Pflichtmetriken:
  - wall_ms
  - output_tps
  - quality_score
  - avg/max CPU
  - avg/max GPU (falls verfuegbar)
  - avg/max VRAM (falls verfuegbar)
- Pflichtvergleich:
  - mindestens ein Run pro Backend je Modell/Konfiguration
- Optional:
  - Multi-Run-Stabilitaetsanalyse

## Change Management

- Changelog unter `docs/project/changelog.md` pflegen.
- Keine grossen Refactors ohne Rueckwaertskompatibilitaet bei Ergebnisdateien.
