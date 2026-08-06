#!/usr/bin/env python3
"""HTTP-level integration tests for the benchmark web‑server hardening.

Covers Block 4 acceptance criteria:
  - Request body size limit (413 for > 1 MB)
  - Health‑check endpoint
  - Graceful no‑report page
  - CORS for localhost
  - All control POST endpoints return 200
  - Data endpoint returns JSON list
  - Server starts on an ephemeral port and shuts down cleanly

Run standalone::

    python scripts\\test_bench_web_server.py

Or with pytest::

    pytest scripts\\test_bench_web_server.py -v
"""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time
import unittest
from pathlib import Path

# Ensure the scripts/ directory is on sys.path regardless of how the test is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_web_server  # noqa: E402  # isort: skip


class TestWebServer(unittest.TestCase):
    """Integration test against a live ThreadingHTTPServer on an ephemeral port."""

    server: bench_web_server.ThreadingHTTPServer
    port: int
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = bench_web_server.create_server(("127.0.0.1", 0))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        # Brief pause so the listener is fully ready
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    # -- helpers ---------------------------------------------------------------
    def _get(self, path: str) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        headers = dict(resp.getheaders())
        conn.close()
        return resp.status, body, headers

    def _post(self, path: str, body: bytes = b"") -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", path, body, {"Content-Length": str(len(body))})
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        return resp.status, resp_body

    # -- GET routes ------------------------------------------------------------
    def test_get_root_returns_200(self) -> None:
        status, body, _ = self._get("/")
        self.assertEqual(status, 200)
        # Even the placeholder page is valid HTML
        self.assertIn("<html", body.lower())

    def test_get_root_serves_no_report_page(self) -> None:
        """When benchmark_report.html is absent, the server must not 404."""
        status, body, _ = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("benchmark", body.lower())

    def test_get_index_html_same_as_root(self) -> None:
        status_r, body_r, _ = self._get("/")
        status_i, body_i, _ = self._get("/index.html")
        self.assertEqual(status_r, status_i)
        self.assertEqual(body_r, body_i)

    def test_get_data_returns_json_list(self) -> None:
        status, body, _ = self._get("/api/data")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIsInstance(data, list)

    def test_get_data_empty_when_no_report(self) -> None:
        """When no report file exists, /api/data must return []."""
        if not bench_web_server.REPORT_HTML.exists():
            _, body, _ = self._get("/api/data")
            data = json.loads(body)
            self.assertEqual(data, [])
        else:
            # Report file exists from a previous run — data will be non-empty.
            # Verify /api/data still returns a valid JSON list.
            _, body, _ = self._get("/api/data")
            data = json.loads(body)
            self.assertIsInstance(data, list)

    # -- Health endpoint -------------------------------------------------------
    def test_get_health_returns_200(self) -> None:
        status, body, _ = self._get("/api/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["status"], "ok")

    def test_get_health_fields(self) -> None:
        _, body, _ = self._get("/api/health")
        data = json.loads(body)
        self.assertIn("report_exists", data)
        self.assertIn("data_rows", data)
        self.assertIsInstance(data["report_exists"], bool)
        self.assertIsInstance(data["data_rows"], int)

    # -- POST control endpoints ------------------------------------------------
    def test_post_pause_200(self) -> None:
        status, body = self._post("/api/control/pause")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "pause")

    def test_post_resume_200(self) -> None:
        status, body = self._post("/api/control/resume")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "resume")

    def test_post_stop_200(self) -> None:
        status, body = self._post("/api/control/stop")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "stop")

    # -- Request body size limit -----------------------------------------------
    def test_request_body_limit_rejects_oversized(self) -> None:
        """A POST with Content-Length > MAX_BODY (1 MB) must return 413."""
        # We only need to set a large Content-Length; the client won't actually
        # send body data, but the server checks the header and rejects early.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(
            "POST", "/api/control/pause",
            headers={"Content-Length": "2000000"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 413)
        body = resp.read().decode("utf-8")
        data = json.loads(body)
        self.assertIn("error", data)
        conn.close()

    def test_request_body_limit_exact_max_allowed(self) -> None:
        """A POST with exactly MAX_BODY bytes must be accepted (not 413)."""
        # MAX_BODY is 1 000 000 — we send the content-length header but the
        # body is empty. The guard only rejects when content_length > MAX_BODY,
        # so this should pass the size check and hit the normal POST route.
        status, _ = self._post(
            "/api/control/pause",
            body=b"x" * bench_web_server.MAX_BODY,
        )
        # Even though we sent a huge body (and the handler ignores POST body
        # for control actions), the status must not be 413.
        self.assertNotEqual(status, 413)

    # -- Status endpoint -------------------------------------------------------
    def test_get_status_returns_json(self) -> None:
        status, body, _ = self._get("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("running", data)

    # -- CORS for localhost ----------------------------------------------------
    def test_cors_header_present_for_localhost(self) -> None:
        _, _, headers = self._get("/api/health")
        # Headers are case‑insensitive in the dict produced by getheaders()
        cors = headers.get("access-control-allow-origin") or headers.get("Access-Control-Allow-Origin")
        self.assertIsNotNone(cors)
        self.assertEqual(cors, "*")

    # -- 404 for unknown routes ------------------------------------------------
    def test_unknown_route_returns_404(self) -> None:
        status, _, _ = self._get("/nonexistent")
        self.assertEqual(status, 404)

    def test_unknown_post_route_returns_404(self) -> None:
        status, _ = self._post("/api/control/frobnicate")
        self.assertEqual(status, 404)


# ── Standalone runner (pytest-compatible too) ───────────────────────────────
if __name__ == "__main__":
    unittest.main()
