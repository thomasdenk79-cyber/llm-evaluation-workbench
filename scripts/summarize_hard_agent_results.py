"""Summarize state-based hard-agent benchmark JSONL results."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def summarize(
    records: list[dict[str, Any]],
    catalog: dict[str, Any],
    models: list[str],
    excluded_cases: set[str],
) -> dict[str, Any]:
    case_ids = [
        case_id
        for case_id in ["startup", *(task["id"] for task in catalog["tasks"])]
        if case_id not in excluded_cases
    ]
    stateful_cases = {
        "startup",
        *(
            task["id"]
            for task in catalog["tasks"]
            if task.get("state_checks")
        ),
    }
    by_model: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    reliability_errors: dict[str, int] = defaultdict(int)
    for record in records:
        model = str(record["model"])
        if record.get("error"):
            reliability_errors[model] += 1
            continue
        if record.get("case_id") in case_ids:
            by_model[model][str(record["case_id"])] = record

    model_rows = []
    for model in models:
        cases = by_model.get(model, {})
        scores = [float(cases[case]["score"]) if case in cases else 0.0 for case in case_ids]
        state_scores = [
            float(cases[case]["state_evaluation"]["score"])
            for case in case_ids
            if case in cases and case in stateful_cases
        ]
        response_scores = [
            float(cases[case]["response_evaluation"]["score"]) if case in cases else 0.0
            for case in case_ids
        ]
        model_rows.append(
            {
                "model": model,
                "overall_score": round(sum(scores) / len(case_ids), 2),
                "state_score": (
                    round(sum(state_scores) / len(state_scores), 2)
                    if state_scores
                    else 0.0
                ),
                "response_score": round(sum(response_scores) / len(case_ids), 2),
                "completed_cases": len(cases),
                "case_count": len(case_ids),
                "reliability_errors": reliability_errors.get(model, 0),
                "cases": {
                    case: {
                        "score": float(cases[case]["score"]) if case in cases else 0.0,
                        "state_score": (
                            float(cases[case]["state_evaluation"]["score"])
                            if case in cases
                            else 0.0
                        ),
                        "state_failures": (
                            [
                                failed["id"]
                                for failed in cases[case]["state_evaluation"]["failed"]
                            ]
                            if case in cases
                            else ["missing"]
                        ),
                    }
                    for case in case_ids
                },
            }
        )
    model_rows.sort(key=lambda item: item["overall_score"], reverse=True)
    return {
        "benchmark_id": catalog["benchmark_id"],
        "excluded_cases": sorted(excluded_cases),
        "models": model_rows,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Hard-agent benchmark summary",
        "",
        "| Model | Overall | State | Response | Cases | Reliability errors |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in summary["models"]:
        lines.append(
            f"| `{item['model']}` | {item['overall_score']:.2f} | "
            f"{item['state_score']:.2f} | {item['response_score']:.2f} | "
            f"{item['completed_cases']}/{item['case_count']} | "
            f"{item['reliability_errors']} |"
        )
    lines.append("")
    lines.append("Overall = 25% response checks + 75% observable workspace state.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--model", action="append", default=[], required=True)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    parser.add_argument("--exclude-case", action="append", default=[])
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    summary = summarize(
        load_jsonl(args.results), catalog, args.model, set(args.exclude_case)
    )
    args.json_output.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(render_markdown(summary), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
