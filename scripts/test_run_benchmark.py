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

    # -- VRAM headroom auto-tune tests ----------------------------------------

    def test_vram_tune_proposal(self) -> None:
        """VRAM headroom proposal returns consistent params for a known-sized model."""
        # 7B Q4_K_M model ≈ 8 800 MB VRAM footprint
        result = rb._derive_vram_params(
            model_name="llama3.1:8b",
            backend="llama_cpp",
            headroom_pct=10,
            available_vram_mb=24_000,
            model_size_bytes=int(8.5 * 1024 * 1024 * 1024),
        )
        self.assertIsNotNone(result)
        self.assertIn("ngl", result)
        self.assertIn("ctx_size", result)
        self.assertIn("batch", result)
        self.assertIsInstance(result["ngl"], int)
        self.assertIsInstance(result["ctx_size"], int)
        self.assertIsInstance(result["batch"], int)
        # With 24 GB and 10 % headroom the 8.5 GB model fits completely
        self.assertEqual(99, result["ngl"])
        self.assertEqual(32768, result["ctx_size"])
        self.assertEqual(512, result["batch"])

    def test_vram_tune_ollama_args(self) -> None:
        """Ollama backend returns ``num_gpu_layers`` and ``context_length``."""
        result = rb._derive_vram_params(
            model_name="qwen3:8b",
            backend="ollama",
            headroom_pct=10,
            available_vram_mb=12_000,
            model_params_billion=8.0,
        )
        self.assertIsNotNone(result)
        self.assertIn("num_gpu_layers", result)
        self.assertIn("context_length", result)
        self.assertIsInstance(result["num_gpu_layers"], int)
        self.assertGreater(result["num_gpu_layers"], 0)
        self.assertLessEqual(result["num_gpu_layers"], 99)

    def test_vram_tune_llama_args(self) -> None:
        """llama.cpp returns ``ngl``, ``ctx_size``, ``batch`` — partial offload when VRAM is tight."""
        # 30 B model needs ~36 GB VRAM; a 12 GB GPU can only partially offload
        result = rb._derive_vram_params(
            model_name="qwen3:30b",
            backend="llama_cpp",
            headroom_pct=10,
            available_vram_mb=12_000,
            model_params_billion=30.0,
        )
        self.assertIsNotNone(result)
        self.assertIn("ngl", result)
        self.assertIn("ctx_size", result)
        self.assertIn("batch", result)
        self.assertLess(result["ngl"], 99)
        self.assertGreater(result["ngl"], 0)
        self.assertEqual(8192, result["ctx_size"])
        self.assertEqual(128, result["batch"])

    def test_vram_headroom_out_of_range(self) -> None:
        """``_derive_vram_params`` returns ``None`` for headroom outside 1..80."""
        self.assertIsNone(
            rb._derive_vram_params("model", "ollama", headroom_pct=0, available_vram_mb=24_000)
        )
        self.assertIsNone(
            rb._derive_vram_params("model", "ollama", headroom_pct=81, available_vram_mb=24_000)
        )
        self.assertIsNone(
            rb._derive_vram_params("model", "ollama", headroom_pct=-5, available_vram_mb=24_000)
        )
        self.assertIsNone(
            rb._derive_vram_params("model", "ollama", headroom_pct=10, available_vram_mb=None)
        )
        # Boundary values (1 % and 80 %) must succeed
        self.assertIsNotNone(
            rb._derive_vram_params("model", "ollama", headroom_pct=1, available_vram_mb=24_000)
        )
        self.assertIsNotNone(
            rb._derive_vram_params("model", "ollama", headroom_pct=80, available_vram_mb=24_000)
        )
        # 75% free target (recommended default) must succeed
        self.assertIsNotNone(
            rb._derive_vram_params("model", "ollama", headroom_pct=75, available_vram_mb=24_000)
        )

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


class ConfigValidationTests(unittest.TestCase):
    """Block 5 — Config validation and schema generation."""

    def test_validate_valid_config(self) -> None:
        """A fully specified config returns no errors and no warnings."""
        config = {
            "schema_version": "campaign-v2",
            "backend": "ollama",
            "runs": 1,
            "timeout_sec": 900,
            "vram_headroom_pct": 5,
        }
        errors, warnings = rb.validate_config(config)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_validate_missing_schema(self) -> None:
        """Missing schema_version produces a warning (legacy-compat)."""
        config = {
            "backend": "ollama",
            "runs": 1,
        }
        errors, warnings = rb.validate_config(config)
        self.assertFalse(errors, f"unexpected errors: {errors}")
        self.assertTrue(
            any("schema_version" in w for w in warnings),
            f"expected schema_version warning, got: {warnings}",
        )

    def test_validate_invalid_backend(self) -> None:
        """An unknown backend value produces a schema-level error."""
        config = {
            "schema_version": "campaign-v2",
            "backend": "hypothetical-quantum",
            "runs": 1,
        }
        errors, warnings = rb.validate_config(config)
        self.assertTrue(
            any("backend" in e for e in errors),
            f"expected backend error, got: {errors}",
        )

    def test_vram_headroom_valid_range(self) -> None:
        """vram_headroom_pct values 1-80 are accepted without errors."""
        for value in (1, 5, 20, 75, 80):
            config = {
                "schema_version": "campaign-v2",
                "backend": "ollama",
                "runs": 1,
                "vram_headroom_pct": value,
            }
            errors, _ = rb.validate_config(config)
            self.assertFalse(errors, f"vram_headroom_pct={value} should be valid, got: {errors}")

    def test_vram_headroom_invalid_range(self) -> None:
        """vram_headroom_pct values outside 1-80 produce errors."""
        for value in (0, 81, -1):
            config = {
                "schema_version": "campaign-v2",
                "backend": "ollama",
                "runs": 1,
                "vram_headroom_pct": value,
            }
            errors, _ = rb.validate_config(config)
            self.assertTrue(
                any("vram_headroom_pct" in e for e in errors),
                f"vram_headroom_pct={value} should fail, got: {errors}",
            )

    def test_strict_config_aborts_on_warning(self) -> None:
        """--strict-config with a valid campaign that has warnings exits 1."""
        # benchmark.toml is fully valid (no warnings),
        # local-campaign.toml is missing schema_version → 1 warning
        exit_code = rb.main(["--config", str(rb.ROOT / "config" / "local-campaign.toml"),
                             "--validate-config", "--strict-config"])
        self.assertEqual(exit_code, 1,
                         "--strict-config should exit 1 when warnings are present")
