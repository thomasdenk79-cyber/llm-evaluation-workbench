"""Run the living-memory benchmark through real OpenCode tool-agent sessions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from understanding_scoring import diagnostic_prompt, evaluate_checks, validate_catalog


DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "benchmarks" / "living-memory-agent-v1.json"
DEFAULT_PRIVATE_OUTPUT = (
    Path(r"C:\GIT\user-memory\why\conversations\benchmarks")
    / "living-memory-agent-results.jsonl"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[], help="OpenCode provider/model")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--workspace", type=Path, default=Path(r"C:\GIT"))
    parser.add_argument("--output", type=Path, default=DEFAULT_PRIVATE_OUTPUT)
    parser.add_argument("--execute", action="store_true", help="Run models; default is dry-run")
    parser.add_argument("--no-feedback", action="store_true")
    return parser.parse_args()


def walk_values(value: Any, key_names: set[str]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_names and isinstance(item, str):
                found.append(item)
            found.extend(walk_values(item, key_names))
    elif isinstance(value, list):
        for item in value:
            found.extend(walk_values(item, key_names))
    return found


def parse_opencode_events(stdout: str) -> tuple[str, str]:
    session_ids: list[str] = []
    text_parts: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_ids.extend(walk_values(event, {"sessionID", "sessionId", "session_id"}))
        if isinstance(event, dict):
            part = event.get("part")
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            elif event.get("type") == "text" and isinstance(event.get("text"), str):
                text_parts.append(event["text"])
    response = "\n".join(part for part in text_parts if part.strip()).strip()
    return (session_ids[-1] if session_ids else ""), response


def run_turn(
    workspace: Path,
    model: str,
    prompt: str,
    session_id: str = "",
    title: str = "",
    require_response: bool = True,
) -> tuple[str, str, str]:
    executable = shutil.which("opencode")
    if not executable:
        raise RuntimeError("OpenCode executable not found on PATH")
    arguments = [
        "run",
        "--format",
        "json",
        "--model",
        model,
        "--dir",
        str(workspace),
    ]
    if session_id:
        arguments.extend(["--session", session_id])
    elif title:
        arguments.extend(["--title", title])
    arguments.append(prompt)
    if Path(executable).suffix.casefold() in {".cmd", ".bat"}:
        command = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/c", executable, *arguments]
    else:
        command = [executable, *arguments]
    result = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    parsed_session, response = parse_opencode_events(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(
            f"OpenCode failed for {model}: {(result.stderr or result.stdout).strip()}"
        )
    if require_response and not response:
        raise RuntimeError(
            f"OpenCode returned no parseable text event for {model}; "
            "the event format may have changed"
        )
    return parsed_session or session_id, response, result.stdout


def validate_execution_scope(catalog: dict[str, Any], models: list[str]) -> None:
    if catalog.get("workspace_data_classification") != "restricted":
        return
    non_local = [
        model for model in models if not model.casefold().startswith("ollama/")
    ]
    if non_local:
        raise ValueError(
            "The restricted live-workspace track may run only on local ollama/* "
            "models. Use the synthetic pure-model track for cloud models: "
            + ", ".join(non_local)
        )


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_model(
    catalog: dict[str, Any],
    model: str,
    workspace: Path,
    output: Path,
    feedback: bool,
) -> None:
    title = f"living-memory-{catalog['spec_version']}-{model.replace('/', '-')}"
    session_id, initial_answer, _ = run_turn(
        workspace, model, str(catalog["initial_prompt"]), title=title
    )
    if not session_id:
        raise RuntimeError(f"OpenCode returned no session ID for {model}")
    initial_eval = evaluate_checks(initial_answer, catalog["initial_checks"])
    append_record(
        output,
        {
            "recorded_at": datetime.now().isoformat(),
            "benchmark_id": catalog["benchmark_id"],
            "track": "tool_agent",
            "backend": "opencode",
            "model": model,
            "session_id": session_id,
            "case_id": "startup",
            "prompt": catalog["initial_prompt"],
            "response": initial_answer,
            "evaluation": initial_eval,
        },
    )

    for task in catalog["tasks"]:
        session_id, answer, _ = run_turn(workspace, model, task["prompt"], session_id=session_id)
        evaluation = evaluate_checks(answer, task["checks"])
        record: dict[str, Any] = {
            "recorded_at": datetime.now().isoformat(),
            "benchmark_id": catalog["benchmark_id"],
            "track": "tool_agent",
            "backend": "opencode",
            "model": model,
            "session_id": session_id,
            "case_id": task["id"],
            "priority": task.get("priority", "common"),
            "sensitivity": task.get("sensitivity", "internal"),
            "prompt": task["prompt"],
            "response": answer,
            "evaluation": evaluation,
        }
        if feedback and evaluation["score"] < float(task.get("threshold", 80)):
            follow_up = diagnostic_prompt(task["prompt"], answer, evaluation)
            session_id, diagnosis, _ = run_turn(
                workspace, model, follow_up, session_id=session_id
            )
            record["diagnostic_prompt"] = follow_up
            record["diagnostic_response"] = diagnosis
        append_record(output, record)


def main() -> int:
    args = parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    validate_catalog(catalog)
    if catalog.get("track") != "tool_agent":
        raise ValueError("Catalog is not a tool_agent benchmark")
    if not args.model:
        print("No models selected. Use --model provider/model.")
        return 2
    validate_execution_scope(catalog, args.model)

    print(f"Catalog: {catalog['benchmark_id']} ({len(catalog['tasks'])} questions + startup)")
    print(f"Workspace: {args.workspace}")
    print(f"Private output: {args.output}")
    print("Models run serially:")
    for model in args.model:
        print(f" - {model}")
    if not args.execute:
        print("DRY-RUN: add --execute to start OpenCode sessions.")
        return 0

    for model in args.model:
        print(f"Running {model} ...")
        run_model(catalog, model, args.workspace, args.output, not args.no_feedback)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
