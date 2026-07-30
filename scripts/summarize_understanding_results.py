"""Aggregate living-memory JSONL results into compact JSON and Markdown."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from understanding_scoring import aggregate_records


def load_records(paths: list[Path]) -> list[dict[str, Any]]:
    records = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{number}: {error}") from error
    return records


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Living-memory benchmark summary",
        "",
        "| Track | Backend | Model | Weighted score | Cases | Failed |",
        "|---|---|---|---:|---:|---:|",
    ]
    for item in summary["models"]:
        lines.append(
            f"| {item['track']} | {item['backend']} | {item['model']} | "
            f"{item['weighted_score']:.2f} | {item['case_count']} | "
            f"{len(item['failed_cases'])} |"
        )
    lines.extend(
        [
            "",
            "Scores weight core/common/edge cases 3/2/1. Compare pure-model and "
            "tool-agent tracks separately: only the latter measures filesystem retrieval.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    summary = aggregate_records(load_records(args.inputs))
    text = render_markdown(summary)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(text, encoding="utf-8")
    if not args.json_output and not args.markdown_output:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
