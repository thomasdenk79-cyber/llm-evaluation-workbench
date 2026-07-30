# Siemens Living-Memory Benchmark - Auswertung

**Lauf:** 2026-07-30
**Track:** Pure Model mit synthetischem Workspace-Fixture
**Modelle:** alle 5 chatfaehigen Siemens-Modelle
**Umfang:** 70 Basisantworten, 40 Diagnosetexte, 3 Diagnose-Timeouts,
2 Schwellenwertfaelle ohne Diagnoserunde, 5 fokussierte V2-Nachtests
**Datenschutz:** keine realen User-, Workspace- oder Secret-Daten im Prompt

## Ergebnis

Der Benchmark bestaetigt das Living-Memory-Konzept. Alle Modelle verstanden die
Grundarchitektur; die groessten Rohscore-Verluste entstanden durch zu woertliche
Scoringregeln und ein zu knappes synthetisches Chat-Naming-Fixture.

| Modell | V1 lexical | Intent nach Review + V2 | Laufzeit V1 | Bewertung |
|---|---:|---:|---:|---|
| `ministral-3-14b-instruct-2512` | 73.64 | 100.00 | 1.74 min | Beste Effizienz, vollstaendig nach V2 |
| `qwen-3.6-27b` | 73.56 | 100.00 | 9.80 min | Beste inhaltliche Tiefe |
| `gpt-oss-120b` | 70.75 | 94.74 | 2.66 min | Stark und schnell; ein READ-WHEN-Fehler |
| `deepseek-v4-flash` | 64.34 | 92.11 | 91.30 min | Inhaltlich gut, fuer diesen Zweck zu langsam |
| `Mistral-Small-24B-Instruct-2501-FP8-dynamic` | 51.95 | 92.11 | 2.03 min | Schnell, aber zu abstrakt bei Konzeptfragen |

`Intent nach Review + V2` ist eine transparente manuelle Adjudikation, kein direkt
mit anderen Benchmark-Suiten vergleichbarer Automatenscore.

## Was alle Modelle sicher verstanden

- Commit-Dokumentation mit zentralem `why-ref`
- Settings-Vererbung Standards -> User -> Repository
- Routing zwischen Projekt-, User- und Agent-Memory
- Owner-Policy und fehlende Selbstermächtigung
- private restricted Originale gegen neutrale zentrale Summaries
- User-kontrolliertes `preserve` bei weiterhin verbotenen Secrets in Commit/Summary
- Session-Ende mit Zustand, naechster Aktion, Validierung und Memory-Routing

## Wichtigster Befund: Doku statt Modellproblem

Im V1-Chat-Naming-Fall erreichten die Modelle nur 16.67 bis 33.33 Prozent. Das
Fixture nannte zwar die Grammatik, liess aber entscheidende Transformationen
implizit:

- Tool-Version als Teil des Tokens
- `/` und Leerzeichen -> `-`
- lowercase `.json`
- erster Snapshot `01`, zweiter `02`
- literal `public`
- forward slashes im `why-ref`

Nach sechs kurzen Regeln und genau einem fremden Beispiel erreichten alle Modelle
im identischen Transferfall **100 Prozent**. Das ist ein sehr starkes Signal:

> Eine knappe, vollstaendige Syntax plus ein Beispiel ist wirksamer als mehr
> allgemeine Erklaerprosa.

Die kanonische `standards\docs\shared\chat-logging.md` besitzt diese Details bereits.
Nachzuschaerfen war das synthetische API-Fixture, nicht der zentrale Standard.

## Echte verbleibende Modellfehler

- **GPT-OSS 120B:** Interpretierte READ-WHEN zu restriktiv. Die Frage selbst machte
  Learnings, Hypothesen, Beobachtungen und Konflikte relevant; das Modell verweigerte
  dennoch deren Inhalt.
- **DeepSeek V4 Flash:** Liess bei der Konzeptliste W-Fragen, knappe Kommunikation
  und explizite Owner-Kontrolle aus.
- **Mistral Small 24B:** Antwortete auf die Konzeptfrage mit allgemeinen
  Kategorienamen statt den konkreten Prinzipien.
- **Qwen 3.6 27B und Ministral 14B:** Nach der kleinen Naming-Klarstellung kein
  verbleibender Intent-Fehler in dieser Suite.

## Scoring-Learning

Der V1-Lexical-Score unterschlug korrekte Antworten durch:

- `Hello` statt `Hallo`
- `Wandern` statt `hiking`
- semantisch gleiche Formulierungen ohne erwartetes Schluesselwort
- Unicode-Bindestriche
- `standards\why` statt `standards/why`

Exakte deterministische Checks bleiben richtig fuer Dateinamen, Klassen und
Policy-Verbote. Semantische Fragen brauchen dagegen normalisierte Synonymgruppen
oder eine getrennte Adjudikation. Sonst misst der Benchmark Wortwahl statt
Verstaendnis.

## Laufzeit und Zuverlaessigkeit

| Modell | Median pro Fall | Maximum | Diagnosefehler |
|---|---:|---:|---:|
| `ministral-3-14b-instruct-2512` | 7.69 s | 13.16 s | 0 |
| `Mistral-Small-24B-Instruct-2501-FP8-dynamic` | 7.65 s | 15.86 s | 0 |
| `gpt-oss-120b` | 9.41 s | 42.45 s | 0 |
| `qwen-3.6-27b` | 41.51 s | 102.85 s | 0 |
| `deepseek-v4-flash` | 275.56 s | 1026.35 s | 3 |

Die Laufzeit je Fall umfasst Basisantwort und gegebenenfalls Diagnoserunde; sie ist
daher keine reine Single-Request-Latenz. Alle 70 Basisfaelle lieferten Antworten.
DeepSeek benoetigte jedoch 91.3 Minuten
gegenueber 1.74 bis 9.8 Minuten fuer die anderen Modelle. Fuer interaktive
Living-Memory-Arbeit ist dieser Mehrverbrauch nicht durch bessere Ergebnisse
gerechtfertigt.

## Meine Bewertung

1. **Standardmodell: Ministral 14B.** Es ist hier am schnellsten und nach praeziser
   Syntax vollstaendig. Das beste Preis-/Latenz-/Qualitaetsverhaeltnis.
2. **Komplexe Architekturfragen: Qwen 3.6 27B.** Inhaltlich am stabilsten und
   detailtreu, aber merklich langsamer.
3. **Breiter starker Fallback: GPT-OSS 120B.** Gute Qualitaet bei niedriger Latenz;
   READ-WHEN-Verhalten beobachten.
4. **Mistral Small 24B:** geeignet fuer klare operative Aufgaben, nicht erste Wahl
   fuer vollstaendige Konzeptrekonstruktion.
5. **DeepSeek V4 Flash:** fuer diesen Anwendungsfall nicht priorisieren. Seine
   semantische Qualitaet ist ordentlich, die Latenz aber unverhaeltnismaessig.

## Konsequenzen

- Keine zusaetzlichen Seiten allgemeiner Standards-Dokumentation erzeugen.
- Exakte Grammatik immer mit einem korrekten Transferbeispiel bereitstellen.
- V2-Fixture als kuenftige API-Basis verwenden.
- Lexical und semantisches Scoring getrennt ausweisen.
- Als naechsten Kalibrierungsschritt lokale Tool-Agenten testen; erst dieser Track
  misst echte Router-Navigation und Dateisystem-Retrieval.
