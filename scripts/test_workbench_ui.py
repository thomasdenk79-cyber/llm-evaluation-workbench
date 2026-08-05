from __future__ import annotations

import asyncio
import csv
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm_bench_tui as tui
import llm_migration_benchmark as migration


class WorkbenchStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_tabs_mount_without_select_sentinel_crash(self) -> None:
        def no_collector(pane: tui.AgentMonitorPane) -> None:
            pane.query_one("#monitor_status", tui.Static).update("collector disabled for test")

        with patch.object(tui.AgentMonitorPane, "_start_collector", no_collector), patch.object(
            tui,
            "fetch_ollama_tags",
            return_value=[],
        ):
            app = tui.BenchmarkTUI()
            async with app.run_test(size=(160, 50)) as pilot:
                await pilot.pause(0.5)
                tabs = app.query_one(tui.TabbedContent)
                for name in (
                    "dashboard",
                    "config",
                    "models",
                    "results",
                    "leaderboard",
                    "agent_monitor",
                ):
                    tabs.active = name
                    await pilot.pause(0.05)
                self.assertGreaterEqual(app.query_one("#plan_table", tui.DataTable).row_count, 1)

    async def test_config_save_preserves_advanced_matrix_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "campaign.toml"
            config.write_text(
                "[benchmark]\n"
                'backend = "llama_cpp"\n'
                'benchmarks = ["ora-pg-py-33"]\n'
                'llama_models = ["demo=C:\\\\models\\\\demo.gguf"]\n'
                "runs = 1\n"
                "timeout_sec = 60\n"
                'ollama_url = "http://127.0.0.1:11434"\n'
                "\n[[matrix]]\n"
                'backend = "llama_cpp"\n'
                'models = ["demo"]\n'
                'benchmarks = ["ora-pg-py-33"]\n'
                'runner_args = ["--llama-ngl", "99"]\n',
                encoding="utf-8",
            )

            def no_collector(pane: tui.AgentMonitorPane) -> None:
                pane.query_one("#monitor_status", tui.Static).update("collector disabled for test")

            with patch.object(tui, "list_campaign_configs", return_value=[config]), patch.object(
                tui.AgentMonitorPane,
                "_start_collector",
                no_collector,
            ), patch.object(tui, "fetch_ollama_tags", return_value=[]):
                app = tui.BenchmarkTUI()
                async with app.run_test(size=(160, 50)) as pilot:
                    await pilot.pause(0.3)
                    errors = app.query_one(tui.ConfigPane)._save()
                    self.assertEqual([], errors)
            _header, _table, matrix = tui.load_campaign_toml(config)
        self.assertEqual(["--llama-ngl", "99"], matrix[0]["runner_args"])


class CampaignDocumentTests(unittest.TestCase):
    def test_toml_writer_preserves_matrix_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "campaign.toml"
            tui.write_campaign_toml(
                path,
                "# test",
                {"backend": "both", "models": ["demo"]},
                [{"backend": "ollama", "models": ["demo"], "benchmarks": ["ora-*"]}],
            )
            header, table, matrix = tui.load_campaign_toml(path)
        self.assertEqual("# test", header)
        self.assertEqual("both", table["backend"])
        self.assertEqual("ora-*", matrix[0]["benchmarks"][0])


class TelemetryContractTests(unittest.TestCase):
    def test_gpu_sampler_measures_free_vram_in_same_query(self) -> None:
        result = SimpleNamespace(returncode=0, stdout="50, 4096, 8192\n")
        with patch.object(migration.subprocess, "run", return_value=result):
            gpu, used, free = migration.SystemMonitor._query_gpu()
        self.assertEqual((50.0, 4096.0, 8192.0), (gpu, used, free))

    def test_detail_csv_carries_measured_free_vram_alias(self) -> None:
        row = migration.BenchResult(
            backend="ollama",
            model="demo",
            case_id="case",
            case_title="Case",
            run=1,
            wall_ms=1000,
            prompt_tokens=10,
            output_tokens=20,
            output_tps=20,
            quality_score=80,
            keyword_hits=1,
            keyword_total=1,
            forbidden_hits=0,
            avg_cpu_pct=10,
            max_cpu_pct=20,
            avg_mem_pct=5,
            max_mem_pct=6,
            avg_gpu_pct=40,
            max_gpu_pct=50,
            avg_vram_used_mb=4096,
            max_vram_used_mb=4200,
            cpu_time_sec=0.5,
            output_preview="ok",
            error="",
            benchmark_task_count=1,
            benchmark_runs=1,
            vram_free_gb=8.0,
        )
        with tempfile.TemporaryDirectory() as temp:
            csv_path, _json_path = migration.save_results([row], temp, run_tag="test")
            with open(csv_path, newline="", encoding="utf-8") as handle:
                saved = next(csv.DictReader(handle))
        self.assertEqual("8.0", saved["vram_free_gb"])
        self.assertEqual("8.0", saved["free_vram_gb"])
        self.assertEqual("Local/Ollama", saved["provider"])

    def test_generated_report_has_compact_interactive_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            detail = Path(temp) / "migration_llm_bench_20260805_120000.csv"
            with detail.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=migration.DETAIL_CSV_COLUMNS)
                writer.writeheader()
                writer.writerow({
                    "benchmark_name": "test",
                    "benchmark_display": "Test - 1 task",
                    "benchmark_task_count": "1",
                    "benchmark_runs": "1",
                    "backend": "ollama",
                    "model": "demo",
                    "case_id": "case",
                    "case_title": "Case",
                    "run": "1",
                    "wall_ms": "1000",
                    "output_tps": "10",
                    "quality_score": "80",
                    "status": "measured",
                    "recorded_at": "2026-08-05 12:00:00",
                })
            report = Path(temp) / "report.md"
            migration.update_markdown_report(temp, str(report), None)
            html = report.with_suffix(".html").read_text(encoding="utf-8")
        self.assertIn("headerFilter:'input'", html)
        self.assertIn("id=\"groupbar\"", html)
        self.assertIn('value="system"', html)
        self.assertIn('id="chart-quality"', html)
        self.assertIn('id="chart-speed"', html)
        self.assertIn('id="chart-overall"', html)
        self.assertIn('id="chart-scatter"', html)
        self.assertIn("const aggregateModels=", html)
        self.assertIn("<b>Progress</b>1/1", html)
        match = re.search(r"const DATA=(\[.*?\]);", html, re.DOTALL)
        self.assertIsNotNone(match)
        data = json.loads(match.group(1))
        self.assertIsInstance(data, list)
        planned = [
            row for row in data
            if row.get("status") in {"scheduled", "paused", "stopped"}
        ]
        for row in planned:
            self.assertIsNone(row.get("heuristic_score"))
            self.assertIsNone(row.get("rating_score"))
            self.assertEqual("", row.get("rating"))
            self.assertEqual("", row.get("interpretation"))
            self.assertIsNone(row.get("vram_free_gb"))


if __name__ == "__main__":
    unittest.main()
