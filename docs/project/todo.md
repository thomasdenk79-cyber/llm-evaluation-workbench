---
title: "LLM Evaluation Workbench - Canonical TODO"
status: active
canonical: true
updated: 2026-08-01T23:45:00+02:00
---

# Offene Arbeit

Diese Datei ist die kanonische, knappe Aufgabenliste für den
Agent-Helper-Track. Detailanforderungen stehen in
[`agent_helper_benchmark.md`](agent_helper_benchmark.md).

## P0 - Zustand sichern und Pilot fortsetzen

- [x] Gemeinsamen Runtime-Guard fuer OpenCode-Kontextbudget, lokale Lease und
      Gaming-Blocker integrieren; vor jedem lokalen Lauf dessen Status pruefen.
- [ ] Qwen-/Laguna-Q5/Q6-Hintergrundinstallation prüfen und Ergebnis
      dokumentieren.
- [ ] Sicherstellen, dass kein Modell geladen ist und keine Downloads
      mehr laufen, bevor Performance gemessen wird.
- [ ] `python -m unittest test_agent_helper_eval` erneut ausführen;
      Erwartung: mindestens 273 Tests erfolgreich.
- [ ] `agent-helper-serial-pilot-20260801` per Resume fortsetzen.
- [ ] Prüfen, dass das vorhandene Qwen-Connect-Gate nicht dupliziert wird.
- [ ] Vier Pilotmodelle anhand der ausführbaren Mini-Tests vergleichen;
      nur `gate-passed-provisional`, keine Daily-Runner-Empfehlung.

## P1 - Moderne Daily-Runner-Kandidaten

- [ ] Qwen 3.6 35B A3B Q4, Q5 und Q6 unter identischen Bedingungen testen.
- [ ] Qwen 3.6 27B Dense Q4 testen.
- [ ] Laguna XS 2.1 Q4, Q5 und Q6 testen.
- [ ] Qualität, Stabilität, Zeit bis zum akzeptierten Ergebnis,
      Korrekturschleifen, TPS und Ressourcen vergleichen.
- [ ] Nicht passende oder instabile Modelle sichtbar ausschließen; hohe
      Geschwindigkeit ist kein Ersatz für Qualität.

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
