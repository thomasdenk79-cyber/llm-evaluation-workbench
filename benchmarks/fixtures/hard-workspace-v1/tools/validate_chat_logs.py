from pathlib import Path
import json
import subprocess


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

assert RAW.exists()
assert SUMMARY.exists()
assert json.loads(RAW.read_text(encoding="utf-8"))["synthetic"] is True
tracked = subprocess.run(
    ["git", "ls-files", str(RAW.relative_to(ROOT))],
    cwd=ROOT,
    capture_output=True,
    text=True,
    check=True,
).stdout.strip()
assert not tracked
marker = ROOT / ".verification" / "chat-validated"
marker.parent.mkdir(parents=True, exist_ok=True)
marker.write_text("ok\n", encoding="utf-8")
print("chat archive valid")
