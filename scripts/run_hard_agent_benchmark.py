"""Run the state-based hard living-memory benchmark through OpenCode."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from run_agent_understanding_benchmark import run_turn
from understanding_scoring import evaluate_checks, validate_catalog


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "benchmarks" / "living-memory-hard-agent-v1.json"
DEFAULT_OUTPUT_DIR = ROOT / "benchmark_results" / "living-memory-hard-agent"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[], required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        help="Short physical root for isolated model workspaces.",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue incomplete cases in existing model workspaces.",
    )
    return parser.parse_args()


def run_command(
    command: list[str], workspace: Path, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def initialize_workspace(template: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError(f"Workspace already exists: {destination}")
    shutil.copytree(template, destination)
    commands = [
        ["git", "init", "--quiet"],
        ["git", "config", "user.name", "Synthetic Benchmark Agent"],
        ["git", "config", "user.email", "benchmark@example.invalid"],
        ["git", "add", "."],
        ["git", "commit", "--quiet", "-m", "chore(fixture): initialize hard benchmark"],
    ]
    for command in commands:
        run_command(command, destination, check=True)


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply_pre_actions(workspace: Path, actions: list[dict[str, Any]]) -> None:
    for action in actions:
        if action["kind"] != "replace_text":
            raise ValueError(f"Unsupported pre-action: {action['kind']}")
        path = workspace / action["path"]
        content = path.read_text(encoding="utf-8")
        old = str(action["old"])
        if old not in content:
            raise ValueError(f"Pre-action text not found in {path}: {old}")
        path.write_text(content.replace(old, str(action["new"])), encoding="utf-8")


def git_commit_count(workspace: Path) -> int:
    result = run_command(["git", "rev-list", "--count", "HEAD"], workspace, check=True)
    return int(result.stdout.strip())


def evaluate_state_checks(
    workspace: Path,
    checks: list[dict[str, Any]],
    initial_hashes: dict[str, str],
) -> dict[str, Any]:
    earned = 0.0
    total = 0.0
    passed: list[str] = []
    failed: list[dict[str, str]] = []
    details: dict[str, str] = {}

    for check in checks:
        check_id = str(check["id"])
        kind = str(check["kind"])
        weight = float(check.get("weight", 1))
        total += weight
        path = workspace / str(check.get("path", ""))
        values = [str(value) for value in check.get("values", [])]
        ok = False
        detail = ""

        if kind == "file_exists":
            ok = path.is_file()
        elif kind == "file_contains_all":
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            ok = all(value.casefold() in content.casefold() for value in values)
        elif kind == "file_not_contains":
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            ok = path.is_file() and all(
                value.casefold() not in content.casefold() for value in values
            )
        elif kind == "file_not_regex":
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            ok = path.is_file() and all(
                not re.search(value, content, re.IGNORECASE) for value in values
            )
        elif kind == "file_regex":
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            ok = all(re.search(value, content, re.IGNORECASE) for value in values)
        elif kind == "file_regex_count":
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
            count = sum(len(re.findall(value, content, re.IGNORECASE)) for value in values)
            ok = int(check["min"]) <= count <= int(check["max"])
            detail = f"count={count}"
        elif kind == "file_max_lines":
            line_count = len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0
            ok = path.is_file() and line_count <= int(check["limit"])
            detail = f"lines={line_count}"
        elif kind == "file_unchanged":
            relative = str(check["path"]).replace("\\", "/")
            ok = path.is_file() and hash_file(path) == initial_hashes.get(relative)
        elif kind == "file_changed":
            relative = str(check["path"]).replace("\\", "/")
            ok = path.is_file() and hash_file(path) != initial_hashes.get(relative)
        elif kind == "command_success":
            result = run_command([str(item) for item in check["command"]], workspace)
            ok = result.returncode == 0
            detail = (result.stdout + result.stderr)[-1000:]
        elif kind == "git_commit_count":
            count = git_commit_count(workspace)
            ok = int(check["min"]) <= count <= int(check["max"])
            detail = f"count={count}"
        elif kind == "git_log_contains_all":
            result = run_command(["git", "log", "-1", "--pretty=%B"], workspace, check=True)
            ok = all(value.casefold() in result.stdout.casefold() for value in values)
            detail = result.stdout.strip()
        elif kind == "git_clean":
            result = run_command(["git", "status", "--porcelain"], workspace, check=True)
            ok = not result.stdout.strip()
            detail = result.stdout.strip()
        else:
            raise ValueError(f"Unsupported state check kind: {kind}")

        if detail:
            details[check_id] = detail
        if ok:
            earned += weight
            passed.append(check_id)
        else:
            failed.append(
                {
                    "id": check_id,
                    "feedback": str(check.get("feedback", check_id)),
                }
            )

    return {
        "score": round(earned / total * 100, 2) if total else 100.0,
        "passed": passed,
        "failed": failed,
        "details": details,
    }


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9.-]+", "-", model.casefold()).strip("-")


def model_records(path: Path, model: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("model") == model
    ]


def run_model(
    catalog: dict[str, Any],
    model: str,
    template: Path,
    output_dir: Path,
    workspace_root: Path,
    resume: bool,
) -> None:
    workspace = workspace_root / model_slug(model)
    records_path = output_dir / "raw-results.jsonl"
    prior = model_records(records_path, model) if resume else []
    completed = {
        str(record["case_id"])
        for record in prior
        if record.get("case_id") != "capability-error" and not record.get("error")
    }
    session_id = next(
        (
            str(record["session_id"])
            for record in reversed(prior)
            if record.get("session_id")
        ),
        "",
    )
    if workspace.exists():
        if not resume:
            raise ValueError(f"Workspace already exists: {workspace}")
    else:
        initialize_workspace(template, workspace)
    initial_hashes = {
        str(path.relative_to(template)).replace("\\", "/"): hash_file(path)
        for path in template.rglob("*")
        if path.is_file()
    }
    if "startup" not in completed:
        prompt = str(catalog["initial_prompt"]).replace("<WORKSPACE>", str(workspace))
        session_id, response, _ = run_turn(
            workspace,
            model,
            prompt,
            title=f"{catalog['benchmark_id']}-{model_slug(model)}",
            require_response=False,
        )
        response_eval = evaluate_checks(response, catalog["initial_checks"])
        state_eval = evaluate_state_checks(
            workspace, catalog.get("initial_state_checks", []), initial_hashes
        )
        startup_score = round(
            response_eval["score"] * 0.25 + state_eval["score"] * 0.75, 2
        )
        append_record(
            records_path,
            {
                "recorded_at": datetime.now().isoformat(),
                "benchmark_id": catalog["benchmark_id"],
                "track": catalog["track"],
                "model": model,
                "session_id": session_id,
                "case_id": "startup",
                "response": response,
                "response_evaluation": response_eval,
                "state_evaluation": state_eval,
                "score": startup_score,
            },
        )

    for task in catalog["tasks"]:
        if task["id"] in completed:
            print(f"  skip {task['id']} (already complete)")
            continue
        apply_pre_actions(workspace, task.get("pre_actions", []))
        session_id, response, _ = run_turn(
            workspace,
            model,
            str(task["prompt"]),
            session_id=session_id,
            require_response=False,
        )
        response_eval = evaluate_checks(response, task.get("response_checks", []))
        state_eval = evaluate_state_checks(
            workspace, task.get("state_checks", []), initial_hashes
        )
        score = round(response_eval["score"] * 0.25 + state_eval["score"] * 0.75, 2)
        append_record(
            records_path,
            {
                "recorded_at": datetime.now().isoformat(),
                "benchmark_id": catalog["benchmark_id"],
                "track": catalog["track"],
                "model": model,
                "session_id": session_id,
                "case_id": task["id"],
                "response": response,
                "response_evaluation": response_eval,
                "state_evaluation": state_eval,
                "score": score,
            },
        )


def main() -> int:
    args = parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    if catalog.get("track") != "synthetic_tool_agent":
        raise ValueError("Catalog is not a synthetic_tool_agent benchmark")
    validate_catalog(
        {
            **catalog,
            "tasks": [
                {
                    "id": task["id"],
                    "checks": task.get("response_checks")
                    or [
                        {
                            "id": "state-only",
                            "kind": "max_chars",
                            "limit": 100000,
                        }
                    ],
                }
                for task in catalog["tasks"]
            ],
        }
    )
    template = args.catalog.parent / catalog["template"]
    if not template.is_dir():
        raise ValueError(f"Fixture template not found: {template}")
    print(f"Catalog: {catalog['benchmark_id']}")
    print(f"Synthetic template: {template}")
    output_dir = args.output_dir.resolve()
    workspace_root = (
        args.workspace_root.resolve()
        if args.workspace_root
        else (output_dir / "workspaces").resolve()
    )
    print(f"Output: {output_dir}")
    print(f"Workspace root: {workspace_root}")
    print("Models run serially:")
    for model in args.model:
        print(f" - {model}")
    if not args.execute:
        print("DRY-RUN: add --execute to run isolated OpenCode workspaces.")
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    for model in args.model:
        print(f"Running {model} ...", flush=True)
        try:
            run_model(
                catalog,
                model,
                template,
                output_dir,
                workspace_root,
                args.resume,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
            append_record(
                output_dir / "raw-results.jsonl",
                {
                    "recorded_at": datetime.now().isoformat(),
                    "benchmark_id": catalog["benchmark_id"],
                    "track": catalog["track"],
                    "model": model,
                    "case_id": "capability-error",
                    "error": str(error),
                    "response_evaluation": {
                        "score": 0.0,
                        "passed": [],
                        "failed": [{"id": "tool-capability"}],
                    },
                    "state_evaluation": {
                        "score": 0.0,
                        "passed": [],
                        "failed": [{"id": "tool-capability"}],
                    },
                    "score": 0.0,
                },
            )
            print(f"  capability error: {error}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
