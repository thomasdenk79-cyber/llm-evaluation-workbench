# Resume

Der Hard-Benchmark bestaetigt den Einwand gegen die fruehere zu freundliche
Bewertung. Reale Handlungen unterscheiden die Modelle deutlich.

- **Qwen:** 95.74, bester Gesamtagent.
- **DeepSeek:** 94.80, fast gleich gut, aber langsam.
- **Ministral:** 78.41, verletzt `AI-ACCESS: denied`.
- **GPT-OSS:** 69.89, Handlung deutlich schwaecher als Antwort.
- **Mistral Small:** 0, Tool-Agent technisch nicht lauffaehig.

Nur Ministral pflegte selbststaendig Agent-Memory; nur Qwen und DeepSeek pflegten
User-Memory korrekt. Alle toolfaehigen Modelle sicherten und validierten
Chat-Evidenz. Nur Qwen schloss Git vollstaendig sauber ab.

Nachgeschaerft wurde nur `user_chat_lang` pro Antwort und nach Settings-Aenderung.
Mehr Prosa ist nicht erforderlich.
