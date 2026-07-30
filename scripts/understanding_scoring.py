"""Deterministic scoring for living-memory understanding responses."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def evaluate_checks(text: str, checks: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize(text)
    earned = 0.0
    total = 0.0
    passed: list[str] = []
    failed: list[dict[str, str]] = []
    dimensions: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for check in checks:
        check_id = str(check["id"])
        kind = str(check["kind"])
        raw_values = [str(value) for value in check.get("values", [])]
        values = [normalize(value) for value in raw_values]
        weight = float(check.get("weight", 1.0))
        dimension = str(check.get("dimension", "general"))
        total += weight

        if kind == "contains_all":
            ok = all(value in normalized for value in values)
        elif kind == "contains_any":
            ok = any(value in normalized for value in values)
        elif kind == "contains_at_least":
            ok = sum(value in normalized for value in values) >= int(check["min"])
        elif kind == "not_contains":
            ok = all(value not in normalized for value in values)
        elif kind == "regex":
            ok = any(re.search(value, text, re.IGNORECASE) for value in raw_values)
        elif kind == "max_chars":
            ok = len(text) <= int(check["limit"])
        else:
            raise ValueError(f"Unsupported check kind: {kind}")

        dimensions[dimension].append((1.0 if ok else 0.0, weight))
        if ok:
            earned += weight
            passed.append(check_id)
        else:
            failed.append(
                {
                    "id": check_id,
                    "dimension": dimension,
                    "feedback": str(check.get("feedback", f"Failed check: {check_id}")),
                }
            )

    score = (earned / total * 100.0) if total else 0.0
    return {
        "score": round(score, 2),
        "passed": passed,
        "failed": failed,
        "dimensions": {
            name: round(
                sum(value * weight for value, weight in values)
                / sum(weight for _, weight in values)
                * 100.0,
                2,
            )
            for name, values in dimensions.items()
        },
    }


def validate_catalog(catalog: dict[str, Any]) -> None:
    required = {"benchmark_id", "track", "initial_prompt", "initial_checks", "tasks"}
    missing = sorted(required - catalog.keys())
    if missing:
        raise ValueError(f"Catalog missing fields: {', '.join(missing)}")
    case_ids = ["startup", *(str(task.get("id", "")) for task in catalog["tasks"])]
    if any(not case_id for case_id in case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("Catalog case IDs must be non-empty and unique")
    for owner, checks in [
        ("startup", catalog["initial_checks"]),
        *((str(task["id"]), task.get("checks", [])) for task in catalog["tasks"]),
    ]:
        if not checks:
            raise ValueError(f"Catalog case {owner} has no checks")
        for check in checks:
            if check.get("kind") not in {
                "contains_all",
                "contains_any",
                "contains_at_least",
                "not_contains",
                "regex",
                "max_chars",
            }:
                raise ValueError(f"Catalog case {owner} has unsupported check kind")
            if check.get("kind") != "max_chars" and not check.get("values"):
                raise ValueError(f"Catalog case {owner} has a check without values")


def diagnostic_prompt(question: str, answer: str, evaluation: dict[str, Any]) -> str:
    failures = "\n".join(
        f"- {item['dimension']}/{item['id']}: {item['feedback']}"
        for item in evaluation["failed"]
    )
    return f"""Deine vorherige Antwort auf diese Benchmarkfrage war ungenuegend.

FRAGE:
{question}

DEINE ANTWORT:
{answer}

NICHT ERFUELLTE KRITERIEN:
{failures}

Antworte als kompaktes JSON:
{{
  "cause": "retrieval|precedence|scope|format|privacy|hallucination|verbosity|capability|ambiguous-doc",
  "why_failed": "kurze sachliche Erklaerung, keine versteckte Gedankenkette",
  "missed_source": "Datei oder Regel, falls zutreffend",
  "minimal_improvement": "kleinste sinnvolle Verbesserung an Agent, Prompt oder Doku",
  "model_limitation": true|false,
  "confidence": 0.0
}}

Schlage keine zusaetzliche Dokumentation vor, wenn die vorhandene Regel bereits eindeutig war."""


def aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate scores without discarding failed-case diagnostics."""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = (
            str(record.get("track", "tool_agent")),
            str(record.get("backend", "opencode")),
            str(record["model"]),
        )
        groups[key].append(record)

    models = []
    priority_weight = {"core": 3.0, "common": 2.0, "edge": 1.0}
    for (track, backend, model), items in sorted(groups.items()):
        weighted = 0.0
        total_weight = 0.0
        failed_cases = []
        dimensions: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for item in items:
            weight = priority_weight.get(str(item.get("priority", "common")), 2.0)
            score = float(item["evaluation"]["score"])
            weighted += score * weight
            total_weight += weight
            if item["evaluation"].get("failed"):
                failed_cases.append(item["case_id"])
            for name, value in item["evaluation"].get("dimensions", {}).items():
                dimensions[name].append((float(value), weight))
        models.append(
            {
                "track": track,
                "backend": backend,
                "model": model,
                "weighted_score": round(weighted / total_weight, 2),
                "case_count": len(items),
                "failed_cases": failed_cases,
                "dimensions": {
                    name: round(
                        sum(value * weight for value, weight in values)
                        / sum(weight for _, weight in values),
                        2,
                    )
                    for name, values in sorted(dimensions.items())
                },
            }
        )
    return {"models": models}
