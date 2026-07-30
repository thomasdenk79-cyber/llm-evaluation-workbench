from pathlib import Path
import json


ROOT = Path(__file__).resolve().parents[1]
RAW = (
    ROOT
    / "user-memory"
    / "why"
    / "conversations"
    / "002-01__original-restricted__opencode-1.18.9__siemens-hard-agent__hard-benchmark.json"
)
SUMMARY = (
    ROOT
    / "standards"
    / "why"
    / "conversations"
    / "002__ai-summary-public__opencode-1.18.9__siemens-hard-agent__hard-benchmark.md"
)

RAW.parent.mkdir(parents=True, exist_ok=True)
SUMMARY.parent.mkdir(parents=True, exist_ok=True)
RAW.write_text(
    json.dumps({"synthetic": True, "chat": 2}, indent=2) + "\n",
    encoding="utf-8",
)
SUMMARY.write_text(
    "# Hard benchmark\n\n- Synthetic milestone evidence.\n",
    encoding="utf-8",
)
print(RAW)
print(SUMMARY)
