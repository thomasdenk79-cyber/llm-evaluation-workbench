"""Focused tests for living-memory scoring and OpenCode event parsing."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_agent_understanding_benchmark import (
    parse_opencode_events,
    validate_execution_scope,
)
from run_hard_agent_benchmark import evaluate_state_checks
from run_model_understanding_benchmark import completed_cases, generate, load_catalog
from understanding_scoring import (
    aggregate_records,
    diagnostic_prompt,
    evaluate_checks,
    validate_catalog,
)


class UnderstandingScoringTests(unittest.TestCase):
    def test_weighted_checks_and_dimensions(self) -> None:
        checks = [
            {
                "id": "required",
                "kind": "contains_all",
                "values": ["alpha", "beta"],
                "weight": 3,
                "dimension": "retrieval",
            },
            {
                "id": "forbidden",
                "kind": "not_contains",
                "values": ["secret"],
                "weight": 1,
                "dimension": "privacy",
            },
        ]
        result = evaluate_checks("Alpha and beta are present.", checks)
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["dimensions"]["retrieval"], 100.0)

    def test_weighted_dimension_score(self) -> None:
        result = evaluate_checks(
            "alpha",
            [
                {
                    "id": "heavy",
                    "kind": "contains_all",
                    "values": ["alpha"],
                    "weight": 3,
                    "dimension": "d",
                },
                {
                    "id": "light",
                    "kind": "contains_all",
                    "values": ["beta"],
                    "weight": 1,
                    "dimension": "d",
                },
            ],
        )
        self.assertEqual(result["dimensions"]["d"], 75.0)

    def test_max_chars_check(self) -> None:
        result = evaluate_checks(
            "short",
            [
                {
                    "id": "brief",
                    "kind": "max_chars",
                    "limit": 5,
                    "dimension": "restraint",
                }
            ],
        )
        self.assertEqual(result["score"], 100.0)

    def test_failed_check_keeps_actionable_feedback(self) -> None:
        result = evaluate_checks(
            "wrong",
            [
                {
                    "id": "path",
                    "kind": "contains_any",
                    "values": ["standards/why"],
                    "dimension": "routing",
                    "feedback": "Use the central WHY path.",
                }
            ],
        )
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["failed"][0]["feedback"], "Use the central WHY path.")

    def test_diagnostic_prompt_requests_cause_without_chain_of_thought(self) -> None:
        evaluation = {
            "failed": [{"dimension": "routing", "id": "path", "feedback": "Wrong path"}]
        }
        prompt = diagnostic_prompt("Where?", "Here.", evaluation)
        self.assertIn('"cause"', prompt)
        self.assertIn("keine versteckte Gedankenkette", prompt)

    def test_pure_model_catalog_uses_synthetic_profile(self) -> None:
        catalog, fixture = load_catalog(
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "living-memory-model-v1.json"
        )
        self.assertEqual(catalog["track"], "pure_model")
        self.assertIn("Alex Example (synthetic)", fixture)
        self.assertNotIn("Thomas", fixture)

    def test_v2_catalog_adds_chat_naming_clarification(self) -> None:
        catalog, fixture = load_catalog(
            Path(__file__).resolve().parents[1]
            / "benchmarks"
            / "living-memory-model-v2.json"
        )
        self.assertEqual(catalog["benchmark_id"], "living-memory-model-v2")
        self.assertIn("042-01__original-restricted__opencode-1.18.9", fixture)

    def test_catalog_validation_rejects_duplicate_case_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_catalog(
                {
                    "benchmark_id": "x",
                    "track": "tool_agent",
                    "initial_prompt": "x",
                    "initial_checks": [
                        {"id": "x", "kind": "contains_all", "values": ["x"]}
                    ],
                    "tasks": [
                        {
                            "id": "startup",
                            "checks": [
                                {"id": "x", "kind": "contains_all", "values": ["x"]}
                            ],
                        }
                    ],
                }
            )

    def test_aggregate_prioritizes_core_cases(self) -> None:
        summary = aggregate_records(
            [
                {
                    "model": "m",
                    "case_id": "core",
                    "priority": "core",
                    "evaluation": {"score": 100, "failed": [], "dimensions": {}},
                },
                {
                    "model": "m",
                    "case_id": "edge",
                    "priority": "edge",
                    "evaluation": {
                        "score": 0,
                        "failed": [{"id": "x"}],
                        "dimensions": {},
                    },
                },
            ]
        )
        self.assertEqual(summary["models"][0]["weighted_score"], 75.0)

    def test_opencode_json_event_parsing(self) -> None:
        lines = [
            {
                "type": "step_start",
                "sessionID": "ses_test",
            },
            {
                "type": "text",
                "sessionID": "ses_test",
                "part": {"type": "text", "text": "Hallo Tommy"},
            },
        ]
        session, response = parse_opencode_events(
            "\n".join(json.dumps(line) for line in lines)
        )
        self.assertEqual(session, "ses_test")
        self.assertEqual(response, "Hallo Tommy")

    def test_opencode_parser_preserves_repeated_text(self) -> None:
        event = json.dumps(
            {
                "type": "text",
                "sessionID": "ses_test",
                "part": {"type": "text", "text": "same"},
            }
        )
        _, response = parse_opencode_events(f"{event}\n{event}")
        self.assertEqual(response, "same\nsame")

    def test_live_track_rejects_cloud_models_for_any_workspace(self) -> None:
        catalog = {"workspace_data_classification": "restricted"}
        with self.assertRaisesRegex(ValueError, "pure-model"):
            validate_execution_scope(catalog, ["siemens/deepseek-v4-flash"])
        validate_execution_scope(catalog, ["ollama/deepseek-v4-flash"])

    def test_siemens_request_uses_loaded_token(self) -> None:
        args = SimpleNamespace(
            ollama_url="",
            siemens_url="https://example.invalid/v1",
            timeout_sec=1,
            request_attempts=3,
        )
        response = {"choices": [{"message": {"content": "ok"}}]}
        with patch(
            "run_model_understanding_benchmark.request_json",
            return_value=response,
        ) as request_json:
            self.assertEqual(
                generate("siemens", "model", "prompt", args, "test-token"),
                "ok",
            )
        request = request_json.call_args.args[0]
        self.assertIn("test-token", request.get_header("Authorization"))

    def test_resume_uses_only_successful_records(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = Path(directory) / "results.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "benchmark_id": "b",
                                "backend": "siemens",
                                "model": "m",
                                "case_id": "ok",
                                "evaluation": {"score": 100},
                            }
                        ),
                        json.dumps(
                            {
                                "benchmark_id": "b",
                                "backend": "siemens",
                                "model": "m",
                                "case_id": "retry",
                                "error": "timeout",
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                completed_cases(path, "b"), {("siemens", "m", "ok")}
            )

    def test_hard_state_checks_file_content_and_length(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "state.md").write_text("# State\n\n- concise\n", encoding="utf-8")
            result = evaluate_state_checks(
                workspace,
                [
                    {
                        "id": "contains",
                        "kind": "file_contains_all",
                        "path": "state.md",
                        "values": ["concise"],
                    },
                    {
                        "id": "short",
                        "kind": "file_max_lines",
                        "path": "state.md",
                        "limit": 3,
                    },
                ],
                {},
            )
            self.assertEqual(result["score"], 100.0)


if __name__ == "__main__":
    unittest.main()
