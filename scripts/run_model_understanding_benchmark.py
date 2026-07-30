"""Run the synthetic living-memory benchmark against Ollama or Siemens APIs."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from understanding_scoring import diagnostic_prompt, evaluate_checks, validate_catalog


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "benchmarks" / "living-memory-model-v1.json"
DEFAULT_OUTPUT = ROOT / "benchmark_results" / "living_memory_model_results.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["ollama", "siemens"], required=True)
    parser.add_argument("--model", action="append", default=[], required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        dest="case_ids",
        help="Run only the selected case ID; repeatable.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434/api/generate")
    parser.add_argument("--siemens-url", default="https://api.siemens.com/llm/v1")
    parser.add_argument("--siemens-token-file", type=Path)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--request-attempts", type=int, default=3)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--no-feedback", action="store_true")
    return parser.parse_args()


def load_catalog(path: Path) -> tuple[dict[str, Any], str]:
    model_catalog = json.loads(path.read_text(encoding="utf-8"))
    if model_catalog.get("track") != "pure_model":
        raise ValueError("Catalog is not a pure_model benchmark")
    base_path = path.parent / model_catalog["base_catalog"]
    catalog = json.loads(base_path.read_text(encoding="utf-8"))
    catalog.update(
        {
            key: value
            for key, value in model_catalog.items()
            if key not in {"base_catalog", "case_overrides", "fixture"}
        }
    )
    overrides = model_catalog.get("case_overrides", {})
    for index, task in enumerate(catalog["tasks"]):
        if task["id"] in overrides:
            merged = copy.deepcopy(task)
            merged.update(overrides[task["id"]])
            catalog["tasks"][index] = merged
    validate_catalog(catalog)
    fixture = (path.parent / model_catalog["fixture"]).read_text(encoding="utf-8")
    for appendix in model_catalog.get("fixture_append", []):
        fixture += "\n\n" + (path.parent / appendix).read_text(encoding="utf-8")
    return catalog, fixture


def load_siemens_token(path: Path | None) -> str:
    token = os.environ.get("SIEMENS_LLM_TOKEN", "").strip()
    candidates = [path] if path else []
    candidates.extend(
        [
            Path.home() / ".siemens_llm_token",
            Path.home()
            / "OneDrive - Siemens AG"
            / "tools"
            / "myconfigfiles"
            / "code.siemens.com_api_ai_token.txt",
        ]
    )
    if token:
        return token
    for candidate in candidates:
        if not candidate:
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                value = line.strip()
                if value.startswith("SIAK-"):
                    return value
        except OSError:
            continue
    raise ValueError("Siemens token not found")


def request_json(
    request: urllib.request.Request, timeout: int, attempts: int = 3
) -> dict[str, Any]:
    if attempts < 1:
        raise ValueError("request attempts must be at least 1")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(attempt)
                continue
            raise RuntimeError(f"API request failed after retries: {error}") from error
    raise RuntimeError(f"API request failed: {last_error}")


def generate(
    backend: str,
    model: str,
    prompt: str,
    args: argparse.Namespace,
    token: str = "",
) -> str:
    if backend == "ollama":
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "seed": 42},
        }
        url = args.ollama_url
        headers = {"Content-Type": "application/json"}
    else:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "Answer only from the supplied synthetic fixture. Be concise.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "stream": False,
        }
        url = args.siemens_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    result = request_json(request, args.timeout_sec, args.request_attempts)
    if backend == "ollama":
        return str(result.get("response") or result.get("thinking") or "")
    message = result.get("choices", [{}])[0].get("message", {})
    return str(message.get("content") or message.get("reasoning") or "")


def benchmark_prompt(fixture: str, prompt: str) -> str:
    return (
        f"{fixture}\n\n"
        "BENCHMARK TASK\n"
        "Use only the synthetic fixture above. Never use or infer real user data.\n"
        f"User input: {prompt}"
    )


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def completed_cases(path: Path, benchmark_id: str) -> set[tuple[str, str, str]]:
    completed: set[tuple[str, str, str]] = set()
    if not path.exists():
        return completed
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
        if record.get("benchmark_id") == benchmark_id and not record.get("error"):
            completed.add(
                (
                    str(record.get("backend", "")),
                    str(record.get("model", "")),
                    str(record.get("case_id", "")),
                )
            )
    return completed


def run_model(
    catalog: dict[str, Any],
    fixture: str,
    backend: str,
    model: str,
    args: argparse.Namespace,
    token: str,
    completed: set[tuple[str, str, str]],
) -> None:
    cases = [
        {
            "id": "startup",
            "prompt": catalog["initial_prompt"],
            "checks": catalog["initial_checks"],
            "threshold": catalog.get("initial_threshold", 80),
            "priority": "core",
        },
        *catalog["tasks"],
    ]
    if args.case_ids:
        unknown = sorted(set(args.case_ids) - {case["id"] for case in cases})
        if unknown:
            raise ValueError(f"Unknown benchmark cases: {', '.join(unknown)}")
        cases = [case for case in cases if case["id"] in args.case_ids]
    for case in cases:
        key = (backend, model, case["id"])
        if key in completed:
            print(f"  skip {case['id']} (already complete)")
            continue
        print(f"  case {case['id']} ...", flush=True)
        full_prompt = benchmark_prompt(fixture, case["prompt"])
        started = time.perf_counter()
        record: dict[str, Any] = {
            "recorded_at": datetime.now().isoformat(),
            "benchmark_id": catalog["benchmark_id"],
            "track": "pure_model",
            "backend": backend,
            "model": model,
            "case_id": case["id"],
            "priority": case.get("priority", "common"),
        }
        try:
            answer = generate(backend, model, full_prompt, args, token)
            evaluation = evaluate_checks(answer, case["checks"])
            record["response"] = answer
            record["evaluation"] = evaluation
            if not args.no_feedback and evaluation["score"] < float(
                case.get("threshold", 80)
            ):
                follow_up = benchmark_prompt(
                    fixture, diagnostic_prompt(case["prompt"], answer, evaluation)
                )
                try:
                    record["diagnostic_response"] = generate(
                        backend, model, follow_up, args, token
                    )
                except RuntimeError as error:
                    record["diagnostic_error"] = str(error)
        except RuntimeError as error:
            record["error"] = str(error)
        record["wall_ms"] = round((time.perf_counter() - started) * 1000, 2)
        append_record(args.output, record)
        if not record.get("error"):
            completed.add(key)


def main() -> int:
    args = parse_args()
    catalog, fixture = load_catalog(args.catalog)
    token = load_siemens_token(args.siemens_token_file) if args.backend == "siemens" and args.execute else ""
    print(f"Catalog: {catalog['benchmark_id']} ({len(catalog['tasks'])} questions + startup)")
    print("Track: pure_model; data: synthetic fixture only")
    print(f"Backend: {args.backend}; models run serially: {', '.join(args.model)}")
    print(f"Output: {args.output}")
    if not args.execute:
        print("DRY-RUN: add --execute to call the selected API.")
        return 0
    completed = completed_cases(args.output, catalog["benchmark_id"])
    for model in args.model:
        print(f"Running {model} ...")
        run_model(catalog, fixture, args.backend, model, args, token, completed)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
