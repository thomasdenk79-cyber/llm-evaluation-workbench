#!/usr/bin/env python3
"""Safe integration tests - E2E without model runs, no network, no GPU.

Block 8: Integration Tests + Acceptance Prep.
Exercises full-system paths via subprocess CLI and in-process HTTP server,
never invoking a real LLM backend.

Run standalone::

    python scripts\\test_integration.py

Or with pytest::

    pytest scripts\\test_integration.py -v
"""

from __future__ import annotations

import http.client
import json
import subprocess
import sys
import time
import threading
import unittest
from pathlib import Path

# Resolve repo root from this file's location
ROOT = Path(__file__).resolve().parent.parent

# Ensure scripts/ is importable
sys.path.insert(0, str(ROOT / "scripts"))


# =============================================================================
# Campaign integration: validate, matrix, doctor, schema
# =============================================================================

class TestCampaignIntegration(unittest.TestCase):
    """End-to-end CLI flags on existing campaign configs (no model runs)."""

    # -- validate-config -------------------------------------------------------
    def test_validate_benchmark_toml(self):
        """--validate-config on benchmark.toml should exit 0 with group count."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"),
             "--config", str(ROOT / "config" / "benchmark.toml"),
             "--validate-config"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Validation failed (exit {result.returncode}):\n{result.stderr}\n{result.stdout}",
        )
        # The validate path prints "Campaign is valid: N execution group(s)."
        self.assertIn("execution group", result.stdout.lower())

    def test_validate_local_campaign_toml(self):
        """--validate-config on local-campaign.toml (has [[matrix]])."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"),
             "--config", str(ROOT / "config" / "local-campaign.toml"),
             "--validate-config"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Validation failed (exit {result.returncode}):\n{result.stderr}\n{result.stdout}",
        )
        self.assertIn("execution group", result.stdout.lower())

    # -- show-matrix -----------------------------------------------------------
    def test_show_matrix_expansion(self):
        """--show-matrix on local-campaign.toml should list resolved groups."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"),
             "--config", str(ROOT / "config" / "local-campaign.toml"),
             "--show-matrix"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Matrix show failed (exit {result.returncode}):\n{result.stderr}",
        )
        out_lower = result.stdout.lower()
        # Output contains lines like:  backend=ollama benchmark=... models=[...]
        self.assertTrue(
            "resolved" in out_lower or "group" in out_lower or "ollama" in out_lower,
            "Should show expanded matrix/backend info",
        )

    # -- doctor ----------------------------------------------------------------
    def test_doctor_check_runs(self):
        """--doctor on benchmark.toml should exit 0 or 1 (warnings OK)."""
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"),
             "--config", str(ROOT / "config" / "benchmark.toml"),
             "--doctor"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
        )
        # doctor returns 0 on clean, 1 when there are warnings/errors
        self.assertIn(
            result.returncode, (0, 1),
            f"Unexpected doctor exit code {result.returncode}: {result.stderr}",
        )
        self.assertIn("campaign:", result.stdout.lower())

    # -- generate-schema -------------------------------------------------------
    def test_generate_schema(self):
        """--generate-schema writes/update config/benchmark.schema.json."""
        schema_file = ROOT / "config" / "benchmark.schema.json"
        # Remove existing file so we can confirm it gets written
        if schema_file.exists():
            schema_file.unlink()

        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"),
             "--generate-schema"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Schema generation failed (exit {result.returncode}): {result.stderr}",
        )
        self.assertTrue(schema_file.exists(), "benchmark.schema.json not created")
        data = json.loads(schema_file.read_text())
        self.assertIn("$schema", data, "Should be valid JSON Schema")
        self.assertIn("properties", data, "Should have properties")


# =============================================================================
# TUI structure: import & screen presence
# =============================================================================

class TestTUIStructure(unittest.TestCase):
    """Verify TUI module imports cleanly and has expected screen classes."""

    def _import_tui(self):
        """Fresh import of llm_bench_tui."""
        # Remove any cached import to force re-read
        if "llm_bench_tui" in sys.modules:
            del sys.modules["llm_bench_tui"]
        import llm_bench_tui  # noqa: F811 - intentional reimport
        return llm_bench_tui

    def test_tui_imports(self):
        """TUI module must import without crashing."""
        tui_mod = self._import_tui()
        self.assertTrue(
            hasattr(tui_mod, "BenchmarkTUI"),
            "TUI module should expose BenchmarkTUI app class",
        )

    def test_tui_screen_classes_present(self):
        """TUI source should declare all expected pane/screen classes."""
        source = (ROOT / "scripts" / "llm_bench_tui.py").read_text(encoding="utf-8")
        expected_classes = [
            "DashboardPane",
            "ConfigPane",
            "ModelsPane",
            "ResultsPane",
            "LeaderboardPane",
            "AgentMonitorPane",
        ]
        for name in expected_classes:
            self.assertIn(name, source, f"TUI should define {name}")

    def test_tui_tab_panes_match(self):
        """Compose method should wire all six TabPane screens."""
        source = (ROOT / "scripts" / "llm_bench_tui.py").read_text(encoding="utf-8")
        # Tabs are declared as TabPane("Title", id="...")
        expected_panes = ["Dashboard", "Config", "Models", "Results", "Leaderboard", "Agent"]
        for label in expected_panes:
            self.assertIn(label, source, f"TUI should render a {label} tab")


# =============================================================================
# Web server integration: startup, endpoints, control, shutdown
# =============================================================================

class TestWebServerIntegration(unittest.TestCase):
    """Start web server on ephemeral port, exercise all endpoints, verify shutdown."""

    server = None
    port = 0
    thread = None

    @classmethod
    def setUpClass(cls):
        # Fresh import
        import bench_web_server  # noqa: F811 - guaranteed by sys.path
        cls.server = bench_web_server.create_server(("127.0.0.1", 0))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        __import__ = __builtins__["__import__"]
        for _ in range(20):
            try:
                conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=1)
                conn.request("GET", "/api/health")
                resp = conn.getresponse()
                resp.read()
                conn.close()
                break
            except Exception:
                time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()

    # -- helpers ---------------------------------------------------------------
    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.__class__.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode(errors="replace")
        conn.close()
        return resp.status, body

    def _post(self, path, body=b""):
        conn = http.client.HTTPConnection("127.0.0.1", self.__class__.port, timeout=5)
        headers = {}
        if body:
            headers["Content-Length"] = str(len(body))
        conn.request("POST", path, body, headers)
        resp = conn.getresponse()
        resp_body = resp.read().decode(errors="replace")
        conn.close()
        return resp.status, resp_body

    # -- GET endpoints ---------------------------------------------------------
    def test_root_returns_200(self):
        status, body = self._get("/")
        self.assertEqual(status, 200, "Root should return 200")
        self.assertIn("html", body.lower(), "Should return HTML content")

    def test_api_data_returns_json_list(self):
        status, body = self._get("/api/data")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIsInstance(data, list, "/api/data should return a JSON array")

    def test_api_health_ok(self):
        status, body = self._get("/api/health")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual(health["status"], "ok")
        self.assertIn("report_exists", health)
        self.assertIn("data_rows", health)

    def test_api_status_contains_running(self):
        status, body = self._get("/api/status")
        self.assertEqual(status, 200)
        st = json.loads(body)
        self.assertIn("running", st)

    # -- POST control protocol -------------------------------------------------
    def test_pause_resume_stop_cycle(self):
        """POST /api/control/pause creates file, /resume removes it, /stop creates stop.ini."""
        pause_file = ROOT / "pause.ini"
        stop_file = ROOT / "stop.ini"

        # Clean up before test
        pause_file.unlink(missing_ok=True)
        stop_file.unlink(missing_ok=True)

        # Pause → file created
        status, body = self._post("/api/control/pause")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "pause")
        self.assertTrue(pause_file.exists(), "pause.ini should exist after /pause")

        # Resume → file removed
        status, body = self._post("/api/control/resume")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["action"], "resume")
        self.assertFalse(pause_file.exists(), "pause.ini should be removed after /resume")

        # Stop → stop file created
        status, body = self._post("/api/control/stop")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["action"], "stop")
        self.assertTrue(stop_file.exists(), "stop.ini should exist after /stop")

        # Clean up
        stop_file.unlink(missing_ok=True)


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    unittest.main()
