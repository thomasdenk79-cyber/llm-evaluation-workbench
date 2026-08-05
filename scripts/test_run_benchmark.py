from __future__ import annotations

import argparse
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_benchmark as rb


class CampaignPlanningTests(unittest.TestCase):
    def test_relative_control_paths_resolve_against_repository_root(self) -> None:
        self.assertEqual(rb.ROOT / "pause.ini", rb._repo_path("pause.ini"))
        absolute = rb.ROOT / "control" / "stop.ini"
        self.assertEqual(absolute, rb._repo_path(absolute))

    def test_only_migration_fixtures_enter_migration_matrix(self) -> None:
        fixtures = rb._available_benchmark_files()
        self.assertIn("ora-pg-py-33", fixtures)
        self.assertNotIn("living-memory-agent-v1", fixtures)
        self.assertTrue(all(rb._is_migration_benchmark(path) for path in fixtures.values()))

    def test_matrix_keeps_per_entry_runner_arguments(self) -> None:
        cfg = {
            "backend": "llama_cpp",
            "llama_models": ["demo=C:\\models\\demo.gguf"],
        }
        args = rb._parser().parse_args([])
        matrix = [{
            "backend": "llama_cpp",
            "models": ["demo"],
            "benchmarks": ["ora-pg-py-33"],
            "runner_args": ["--llama-ngl", "99"],
        }]
        groups = rb.expand_matrix(matrix, args, cfg)
        self.assertEqual(1, len(groups))
        self.assertEqual(["--llama-ngl", "99"], groups[0]["runner_args"])

    def test_preview_can_expand_wildcards_without_network(self) -> None:
        args = rb._parser().parse_args([])
        matrix = [{
            "backend": "ollama",
            "models": ["*qwen*"],
            "benchmarks": ["ora-pg-py-33"],
        }]
        with patch.object(rb, "_ollama_models") as discovery:
            groups = rb.expand_matrix(
                matrix,
                args,
                {"models": ["qwen3:8b"]},
                resolve_wildcards=False,
            )
        discovery.assert_not_called()
        self.assertEqual(["qwen3:8b"], groups[0]["models"])

    def test_cross_backend_adapter_derives_required_arguments(self) -> None:
        args = rb._parser().parse_args([])
        cfg = {
            "models": ["demo:latest"],
            "llama_models": ["demo=C:\\models\\demo.gguf"],
            "llama_server": "C:\\llama\\llama-server.exe",
            "ollama_url": "http://127.0.0.1:11434/api/generate",
            "timeout_sec": 123,
        }
        command = rb._runner_command("cross-backend", args, cfg, Path("C:\\work"))
        self.assertIn("--output", command)
        self.assertIn("--ollama-model", command)
        self.assertIn("--llama-model", command)
        self.assertIn("--server", command)
        self.assertEqual("http://127.0.0.1:11434", command[command.index("--ollama-url") + 1])

    def test_cross_backend_rows_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cross_backend_mini.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "timestamp", "backend", "model", "configuration",
                        "quality_percent", "elapsed_seconds", "tokens_per_second",
                        "status", "error", "sample_id", "score", "agent_suitability",
                    ],
                )
                writer.writeheader()
                writer.writerow({
                    "timestamp": "2026-08-05T12:00:00Z",
                    "backend": "ollama",
                    "model": "demo",
                    "configuration": "ctx4096",
                    "quality_percent": "100",
                    "elapsed_seconds": "2.5",
                    "tokens_per_second": "20",
                    "status": "done",
                    "error": "",
                    "sample_id": "demo-1",
                    "score": "100",
                    "agent_suitability": "accepted",
                })
            rows = rb._read_runner_csvs(Path(temp))
        self.assertEqual("cross-backend-mini", rows[0]["benchmark_name"])
        self.assertEqual("Local/Ollama", rows[0]["provider"])
        self.assertIn("launch_params", rows[0])

    def test_ollama_recovery_starts_service_once(self) -> None:
        with patch.object(
            rb,
            "_ollama_inventory",
            side_effect=[OSError("offline"), ["demo"]],
        ), patch.object(rb.shutil, "which", return_value="ollama.exe"), patch.object(
            rb.subprocess,
            "Popen",
        ) as popen, patch.object(rb.time, "sleep"):
            ready, detail = rb._ensure_ollama("http://127.0.0.1:11434", timeout_sec=1)
        self.assertTrue(ready)
        self.assertIn("started", detail)
        popen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
