"""Test dynamic user_chat_lang reload in isolated OpenCode sessions."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from run_agent_understanding_benchmark import run_turn
from run_hard_agent_benchmark import initialize_workspace, model_slug
from understanding_scoring import evaluate_checks


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "benchmarks" / "fixtures" / "hard-workspace-v3"


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print("DRY-RUN: add --execute to run the language policy retest.")
        return 0

    startup_checks = [
        {
            "id": "german",
            "kind": "contains_any",
            "values": ["hallo", "guten", "willkommen", "servus"],
            "weight": 1,
        }
    ]
    english_checks = [
        {
            "id": "english",
            "kind": "contains_at_least",
            "min": 2,
            "values": ["current", "status", "completed", "pending", "ready", "workspace"],
            "weight": 2,
        },
        {
            "id": "not-german",
            "kind": "not_contains",
            "values": ["aktueller", "erledigt", "offen", "bereit", "abgeschlossen"],
            "weight": 1,
        },
    ]
    for model in args.model:
        workspace = args.workspace_root.resolve() / model_slug(model)
        if workspace.exists():
            shutil.rmtree(workspace)
        initialize_workspace(args.template.resolve(), workspace)
        try:
            session_id, startup, _ = run_turn(
                workspace,
                model,
                str(workspace),
                title=f"language-v3-{model_slug(model)}",
                require_response=False,
            )
            settings = workspace / "user-memory" / "alex" / "settings.yml"
            content = settings.read_text(encoding="utf-8")
            settings.write_text(
                content.replace("user_chat_lang: de", "user_chat_lang: en"),
                encoding="utf-8",
            )
            session_id, status, _ = run_turn(
                workspace,
                model,
                "STATUS",
                session_id=session_id,
                require_response=False,
            )
            append_record(
                args.output,
                {
                    "recorded_at": datetime.now().isoformat(),
                    "model": model,
                    "startup_response": startup,
                    "startup_evaluation": evaluate_checks(startup, startup_checks),
                    "status_response": status,
                    "status_evaluation": evaluate_checks(status, english_checks),
                },
            )
        except RuntimeError as error:
            append_record(
                args.output,
                {
                    "recorded_at": datetime.now().isoformat(),
                    "model": model,
                    "error": str(error),
                },
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
