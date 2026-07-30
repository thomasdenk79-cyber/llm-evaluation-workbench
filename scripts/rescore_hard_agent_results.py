"""Re-score hard-agent results after deterministic rubric corrections."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from run_hard_agent_benchmark import evaluate_state_checks
from understanding_scoring import evaluate_checks


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", model.casefold()).strip("-")


def load_latest(path: Path) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("error"):
            errors.append(record)
        else:
            latest[(str(record["model"]), str(record["case_id"]))] = record
    return [*latest.values(), *errors]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    template = args.catalog.parent / catalog["template"]
    initial_hashes = {
        str(path.relative_to(template)).replace("\\", "/"): hash_file(path)
        for path in template.rglob("*")
        if path.is_file()
    }
    tasks = {task["id"]: task for task in catalog["tasks"]}
    rescored = []
    for record in load_latest(args.results):
        case_id = str(record.get("case_id"))
        if record.get("error") or case_id in {"startup", "trivial-no-commit"}:
            rescored.append(record)
            continue
        task = tasks[case_id]
        workspace = args.workspace_root / model_slug(str(record["model"]))
        response_eval = evaluate_checks(
            str(record.get("response", "")), task.get("response_checks", [])
        )
        state_checks = task.get("state_checks", [])
        state_eval = evaluate_state_checks(workspace, state_checks, initial_hashes)
        score = (
            round(response_eval["score"] * 0.25 + state_eval["score"] * 0.75, 2)
            if state_checks
            else response_eval["score"]
        )
        rescored.append(
            {
                **record,
                "original_score": record["score"],
                "response_evaluation": response_eval,
                "state_evaluation": state_eval,
                "score": score,
                "adjudication": "deterministic-rubric-v2.1",
            }
        )
    args.output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in rescored),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
