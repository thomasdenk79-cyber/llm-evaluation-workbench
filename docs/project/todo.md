---
title: "LLM Evaluation Workbench - Canonical TODO"
status: active
canonical: true
updated: 2026-08-04T02:30:00+02:00
---

# Offene Arbeit

## Clean benchmark restart — 2026-08-05

- [x] Generated benchmark artifacts reset; source runners and tests retained.
- [x] Persist launch parameters in detail/history and summary CSVs
      (`benchmark-v2.1`).
- [x] Document RTX 3500 Ada VRAM/offload rules and ik_llama.cpp CUDA Release
      build procedure in the operations runbook.
- [ ] Run the clean Qwen/KAT comparison over Ollama, upstream llama.cpp and
      ik_llama.cpp, then publish the grouped/filterable HTML report.

## Aktueller Zwischenstand — 2026-08-03 18:35

- Ollama und Siemens sind beendet; aktuell laufen keine Benchmarkprozesse.
- Der gemeinsame Coding-Betriebskontext ist für lokale Ollama-Tags sowie
  OpenCode-/llama.cpp-Presets auf 32.768 Tokens begrenzt.
- Der 27B-Nachtest unter 32k ist abgeschlossen: 3,23 Generate-Tok/s,
  17,81 Prompt-Tok/s, 5,25 s Wall-Time, 66,09 % CPU-Mittelwert und
  36,93 % GPU-Mittelwert. `ollama ps` meldete 48 % CPU / 52 % GPU.
- Die exportierte 27B-GGUF bleibt im ik_llama.cpp-Fork inkompatibel.
  Laguna Q4_K_M ist jetzt als vollständige GGUF installiert und im
  llama.cpp/OpenCode-Routing registriert; der CUDA-Smoke-Test ist gültig.
- Qwen 3.6 35B A3B Q4 wurde in Ollama und llama.cpp getestet. Der
  llama.cpp-Lauf war CPU-only und ist keine GPU-Evidenz.
- Der ursprüngliche Fork-Build war gegen einen sitzungsgebundenen
  CUDA-13.2.1-Pfad gelinkt. Konsistente Neubauten liegen unter
  `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v132-installed` und
  `C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v133-installed`.
- Der NVIDIA-Treiber wurde neu installiert und das vollständige CUDA
  Toolkit 13.3.1 ergänzt. Der saubere Fork-Build
  `build-cuda-v133-clean` meldet jetzt `cuda=true`, `gpu_blas=true` und
  die RTX 3500 Ada mit Compute Capability 8.9.
- Die erste gültige Qwen-3.6-35B-A3B-Q4-CUDA-Probe erreichte 98,27
  Prompt-Tok/s und 31,92 Generate-Tok/s. Die vollständige Matrix steht
  noch aus.
- Der Mini-Runner hat jetzt eine Stunde Timeout je Modell,
  `mini_progress.log` sowie einen Stall-Abbruch nach 300 Sekunden ohne
  neue Ollama-Tokens. Die vollständige llama.cpp-Matrix wartet auf einen
  gültigen CUDA-Preflight.

### Nächster Wiederaufsetzpunkt

1. Neustart und CUDA-Preflight mit dem CUDA-13.3-Build wiederholen.
2. Bei unverändertem Fehler NVIDIA Studio über
   `Benutzerdefiniert > Neuinstallation durchführen` reparieren.
3. Falls weiterhin `cuInit=100`/`ggml_cuda_init` erscheint, DDU
   (Display Driver Uninstaller, Wagnardsoft) im abgesicherten Modus
   verwenden und den aktuellen Studio-Treiber sauber installieren.
4. Erst nach nichtleerem `gpu_info` die komplette llama.cpp-Matrix starten.

Diese Datei ist die kanonische, knappe Aufgabenliste für den
Agent-Helper-Track. Detailanforderungen stehen in
[`agent_helper_benchmark.md`](agent_helper_benchmark.md).

## P0 - Zustand sichern und Pilot fortsetzen

- [x] Gemeinsamen Runtime-Guard fuer OpenCode-Kontextbudget, lokale Lease und
      Gaming-Blocker integrieren; vor jedem lokalen Lauf dessen Status pruefen.
- [x] Qwen-/Laguna-Q5/Q6-Hintergrundinstallation prüfen und Ergebnis
      dokumentieren: Laguna Q4/Q5/Q6 sind installiert; das exakte
      Q5-GGUF wurde als `qwen3.6:35b-a3b-q5_K_M` in Ollama importiert.
- [x] Sicherstellen, dass kein Modell geladen ist und keine Downloads
      mehr laufen, bevor Performance gemessen wird.
- [x] Nach Treiber-/Toolkit-Neuinstallation `cuInit` gegen die RTX 3500 Ada
      validieren; der saubere Fork-Preflight meldet `cuda=true` und
      `gpu_blas=true`.
- [ ] `python -m unittest test_agent_helper_eval` erneut ausführen;
      Erwartung: mindestens 273 Tests erfolgreich.
- [ ] `agent-helper-serial-pilot-20260801` per Resume fortsetzen.
- [ ] Prüfen, dass das vorhandene Qwen-Connect-Gate nicht dupliziert wird.
- [ ] Vier Pilotmodelle anhand der ausführbaren Mini-Tests vergleichen;
      nur `gate-passed-provisional`, keine Daily-Runner-Empfehlung.
- [x] Zehn explizite Daily-Coder-Kandidaten seriell durch Connect- und
      Mini-Coding-Gate führen (`daily-coder-20260803`): neun akzeptiert,
      Devstral wegen `OLLAMA_GENERATE_FAILED` im Coding-Gate ausgeschlossen.

## P1 - Moderne Daily-Runner-Kandidaten

- [x] Qwen 3.6 35B A3B Q4 und verfügbare Varianten unter identischen
      Bedingungen testen. Q5 erst nach expliziter GGUF-Quelle ergänzen;
      der Q5-GGUF wurde importiert als `qwen3.6:35b-a3b-q5_K_M`.
- [x] Qwen 3.6 27B Dense Q4 testen, einschließlich Wiederholung unter dem
      32k-Coding-Cap; die neue Probe ist deutlich besser verteilt, aber
      mit 3,23 Generate-Tok/s weiterhin kein Daily-Runner-Kandidat.
- [x] Laguna XS 2.1 Q4, Q5 und Q6 testen.
- [ ] Qualität, Stabilität, Zeit bis zum akzeptierten Ergebnis,
      Korrekturschleifen, TPS und Ressourcen vergleichen.
- [ ] Nicht passende oder instabile Modelle sichtbar ausschließen; hohe
      Geschwindigkeit ist kein Ersatz für Qualität.
- [ ] Die Gate-Finalisten Qwen 3.6 35B A3B Q4 und DeepSeek Coder V2 16B
      gegen Bug Review, SQL-Migration, Frontend, Architektur und
      Multi-Turn-Korrektur evaluieren; erst dann Daily-Coder bestimmen.
- [ ] CUDA-Fork `ik_llama.cpp` mit Qwen 3.6 35B A3B Q4/Q5
      im dokumentierten Hybridprofil (GPU Attention/KV, CPU Experts)
      anschließend mit einem echten Coding-Gate prüfen. Gültige
      Durchsatzproben liegen jetzt vor: Q4 98,27/31,92 und Q5
      83,26/21,41 Prompt-/Generate-Tok/s; die Coding-Gates fehlen noch.
- [x] Eine kompatible Laguna-GGUF für den CUDA-Fork beschaffen:
      `Laguna-XS-2.1-Q4_K_M.gguf` ist installiert und mit CUDA ladbar.
- [ ] Laguna Q4_K_M im Hybridprofil mit CPU-/GPU-Sampling und einem echten
      Coding-Gate messen; der bisherige `pp32`/`tg8`-Smoke-Test erreichte
      40,43/8,00 Prompt-/Generate-Tok/s.
- [x] Favoritenvergleich mit dem vorhandenen Hard-/Mini-Track fortsetzen:
      Qwen/Ollama Hard v2 erreichte 78,32 % bei 400,3 s, DeepSeek war im
      Tool-Agent-Track wegen fehlender Tools nicht ausführbar, Laguna
      scheiterte dort an einem Tool-/Summary-Loop. Die sechs Mini-Gate-
      Rohdateien liegen unter `benchmark_results\favorites-cross-backend-20260804`;
      Fork- und Ollama-Ressourcenwerte sind im Handover dokumentiert.
- [x] `swe-sql-hard-24`, `swe-python-hard-24` und `swe-mixed-hard-24`
      für die drei Favoriten in Ollama und `ik_llama.cpp` ausführen und
      den konsolidierten Vergleich unter
      `benchmark_results\agent-helper\report.html` aktualisieren.
- [x] Rating und Agententauglichkeit im Legacy-SWE-Report trennen:
      Rating = Qualität × Elapsed-Performance × Zuverlässigkeit;
      Agententauglichkeit = erwartete Zeit bis zum erfolgreichen Ergebnis,
      damit schnelle Korrekturschleifen berücksichtigt werden.
- [ ] Ik-Fork-Qwen-MoE-Tensorprofil weiter tunen: automatischen Fit gegen
      tensor-spezifische `--override-tensor`-/partielle Expert-Offloads
      messen; `--cpu-moe` und `--n-cpu-moe 24` waren im Smoke-Test langsamer.
- [ ] Qwen 3.6 35B, Qwen Coder 30B und Laguna im Fork mit PCIe-RX/TX-
      Monitoring messen; GPU-Auslastung/VRAM und PCIe-Durchsatz pro Case
      gegen Ollama und Upstream-llama.cpp vergleichen, anschließend
- [x] KAT-Coder-V2.5-Dev als identische offizielle Q4_K_M-GGUF in Ollama,
      Upstream-llama.cpp und ik-Fork mit 12-GB-Hybridprofil messen; Rohdaten
      und PCIe-Werte liegen unter den drei `kat-coder-*-20260804`-Verzeichnissen.
      TB4/TB5/PCIe-x16-Szenarien aus den Dauerwerten ableiten.

## P1 - Vollständige Benchmarktracks

- [ ] Reale Executor-Gates für Bug Review, SQL-Migration, Frontend,
      Architektur und Multi-Turn-Korrektur implementieren.
- [ ] Tool-Agent-Track in isolierten Fixtures ergänzen.
- [ ] Siemens-Qwen-3.6-27B-Adapter für abgegrenztes Coding integrieren.
- [ ] Weitere Siemens-Modelle unter demselben Vertrag testen.
- [ ] Copilot-Referenzläufe getrennt ausweisen; nicht verfügbare interne
      Token-/TPS-Metriken als `N/A` belassen.
- [ ] llama.cpp-Adapter und Lifecycle unter denselben Qualitätsgates
      integrieren.

## P2 - Kapazität und Wirtschaftlichkeit

- [ ] Nur für vollständig akzeptierte lokale Modelle Parallelität 1/2/4
      testen.
- [ ] LLM/API-, Tool-/Test-, Orchestrator-, Queue- und Idle-Anteile am
      kritischen Pfad validieren.
- [ ] Gesamt-TPS, TPS je Anfrage, p95, VRAM/RAM und Fehler unter
      Parallelität auswerten.
- [ ] RTX-5090-32-GB-Fit als Projektion mit Annahmen und Konfidenz
      darstellen, niemals als Messung.
- [ ] Premium-first, Siemens-/Local-Routing und Hardware-Investition mit
      getrennten Baselines vergleichen.

## P2 - Berichte und Managementdarstellung

- [ ] Historische Runs mit Provenienz-/Konfidenzkennzeichnung importieren.
- [ ] Aktuelle Kampagne standardmäßig öffnen, ältere Kampagnen einklappen.
- [ ] DE/EN-HTML auf Managementstory, technische Drilldowns,
      Barrierefreiheit, Print/PDF und Offlinebetrieb prüfen.
- [ ] Funktionale/visuelle Bewertung der vorhandenen HTML-Grid-Artefakte
      abschließen; Devstral-Timeout separat dokumentieren.
- [ ] Managementbericht klar zwischen Messung, Annahme und Projektion
      unterscheiden lassen.

## P3 - Routing-Regeln und Governance

- [ ] Erst nach vollständiger Evidenz ein Agent-Routing-Regelwerk
      vorschlagen.
- [ ] Deterministische Tools vor LLM, Siemens-/Local-Worker für geeignete
      Aufgaben und Premium-Modell für Architektur/Review belegen.
- [ ] Provenienz jedes Subagenten, Modells und Reviews verpflichtend
      machen.
- [ ] Knappe, validierte Regeln anschließend in die passenden
      Workspace-/Standards-Markdowndateien übernehmen.

## Nicht in diesem Track

- Command-Center-Implementierung und dessen Cloud-/Tool-Berichte.
- ITSM-PWA, Backend-Sprachbenchmark und PostgreSQL-Domänenmodell.
- Moderner Oracle-nach-PostgreSQL-Data-Pump-Migrator.
- Jira-TaskVision-Produktionsmigration.
