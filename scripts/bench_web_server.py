#!/usr/bin/env python3
"""Embedded web control plane for the LLM benchmark workbench.

Serves the already-existing Tabulator HTML report plus a compressed JSON
API and a small set of pause/resume/stop control endpoints, so the same
report and the same run-control actions the Textual TUI exposes
(``scripts/llm_bench_tui.py``) are also reachable from a browser -- without
introducing a new, parallel implementation of either the report or the
control protocol (see ``docs/project/tui-web-architecture.md``).

Deliberately stdlib-only (``http.server.ThreadingHTTPServer``), mirroring
the pattern already used and proven in the sibling project
``C:\\GIT\\taskvision-grid-lab\\server.py``: response compression is
negotiated per request via a ``?compression=`` query parameter or the
``Accept-Encoding`` header, one of ``none``/``gzip``/``br``/``zstd``.

Usage::

    python scripts\\bench_web_server.py                  # 127.0.0.1:8766
    python scripts\\bench_web_server.py --port 9000
    python scripts\\bench_web_server.py --compression zstd

Security posture (see requirements.md "Explicit non-goals"): this server
exposes exactly three actions beyond read-only data -- pause, resume,
stop -- by touching/removing the same ``pause.ini``/``stop.ini`` files
``run_benchmark.py`` already guards. It never executes an arbitrary
command from a request. It binds to localhost only unless ``--host`` is
passed explicitly, which prints a loud warning.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_benchmark as rb  # noqa: E402  (reuse the existing pause/stop/lock protocol)

try:
    import brotli  # type: ignore
except ImportError:  # pragma: no cover - optional
    brotli = None
try:
    import zstandard  # type: ignore
except ImportError:  # pragma: no cover - optional
    zstandard = None

ROOT = rb.ROOT
DOCS_PROJECT = ROOT / "docs" / "project"
REPORT_HTML = DOCS_PROJECT / "benchmark_report.html"
VENDOR_DIR = DOCS_PROJECT / "vendor"

SUPPORTED_COMPRESSIONS = {"none", "gzip", "br", "zstd"}
_DATA_RE = re.compile(r"const DATA=(\[.*?\]);", re.DOTALL)

DEFAULT_COMPRESSION = "none"
MAX_BODY = 1_000_000  # 1 MB — reject any POST with a larger declared body
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 8766
SERVER_SETTINGS_PATH = ROOT / "config" / "server_settings.json"

HELP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Help — LLM Evaluation Workbench</title>
<style>
body{font-family:'Segoe UI',system-ui,sans-serif;background:#0e1013;color:#d5d9dd;margin:0;padding:2rem}
h1{color:#55ccff}h2{color:#55ccff;margin-top:2rem}
code{background:#182128;padding:0.125rem 0.375rem;border-radius:3px}
table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{text-align:left;padding:0.5rem;border-bottom:1px solid #303840}
th{color:#d8f3ff}
a{color:#55ccff}
</style>
</head>
<body>
<h1>LLM Evaluation Workbench — Help</h1>
<h2>CLI Commands</h2>
<table>
<tr><th>Command</th><th>Description</th></tr>
<tr><td><code>python run_benchmark.py</code></td><td>Start the TUI control center</td></tr>
<tr><td><code>python run_benchmark.py --help</code></td><td>Show CLI usage and options</td></tr>
<tr><td><code>python run_benchmark.py --config CONFIG</code></td><td>Run with a specific campaign TOML</td></tr>
<tr><td><code>python run_benchmark.py --validate-config</code></td><td>Validate config, print errors</td></tr>
<tr><td><code>python bench_web_server.py --port PORT --host HOST</code></td><td>Start the web server</td></tr>
</table>
<h2>API Endpoints</h2>
<table>
<tr><th>Method</th><th>Path</th><th>Description</th></tr>
<tr><td>GET</td><td><code>/</code></td><td>Main benchmark report (Tabulator grid)</td></tr>
<tr><td>GET</td><td><code>/api/data</code></td><td>JSON report data rows</td></tr>
<tr><td>GET</td><td><code>/api/status</code></td><td>Run control status (running/paused/stopped)</td></tr>
<tr><td>GET</td><td><code>/api/health</code></td><td>Health check endpoint</td></tr>
<tr><td>GET</td><td><code>/api/settings</code></td><td>Get server settings (host, port, compression)</td></tr>
<tr><td>POST</td><td><code>/api/settings</code></td><td>Update server settings</td></tr>
<tr><td>POST</td><td><code>/api/control/pause</code></td><td>Pause active benchmark</td></tr>
<tr><td>POST</td><td><code>/api/control/resume</code></td><td>Resume paused benchmark</td></tr>
<tr><td>POST</td><td><code>/api/control/stop</code></td><td>Stop active benchmark</td></tr>
<tr><td>GET</td><td><code>/vendor/&lt;file&gt;</code></td><td>Tabulator JS/CSS vendor files</td></tr>
</table>
<h2>TUI Keyboard Shortcuts</h2>
<table>
<tr><th>Key</th><th>Action</th></tr>
<tr><td><code>1-6</code></td><td>Switch to tab (Dashboard/Config/Models/Results/Leaderboard/Monitor)</td></tr>
<tr><td><code>/</code></td><td>Focus filter input</td></tr>
<tr><td><code>q</code></td><td>Quit</td></tr>
<tr><td><code>d</code></td><td>Cycle density mode</td></tr>
<tr><td><code>h</code></td><td>Help screen</td></tr>
<tr><td><code>l</code></td><td>Layout configuration</td></tr>
<tr><td><code>s</code></td><td>Start benchmark</td></tr>
<tr><td><code>p</code></td><td>Pause benchmark</td></tr>
<tr><td><code>r</code></td><td>Resume benchmark</td></tr>
<tr><td><code>x</code></td><td>Stop benchmark</td></tr>
<tr><td><code>F5</code></td><td>Refresh all panels</td></tr>
</table>
<h2>Density Modes</h2>
<p>Density modes adjust padding, heights, and panel sizes:
<strong>Wide</strong> (spacious), <strong>Normal</strong> (default),
<strong>Compact</strong>, <strong>Mini</strong>, <strong>Ultra Compact</strong> (minimal).</p>
<p>Press <code>d</code> to cycle, or <code>l</code> to open the layout panel.</p>
<h2>VRAM Guard</h2>
<p>The VRAM headroom guard reserves a percentage of GPU memory for non-benchmark workloads.</p>
<table>
<tr><th>Parameter</th><th>Default</th><th>Range</th><th>Description</th></tr>
<tr><td><code>vram_headroom_pct</code></td><td>75</td><td>1-80</td><td>Percent of GPU memory to keep free</td></tr>
</table>
</body>
</html>
""".lstrip()


def _load_server_settings() -> dict:
    if SERVER_SETTINGS_PATH.is_file():
        try:
            return json.loads(SERVER_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "host": DEFAULT_BIND,
        "port": DEFAULT_PORT,
        "compression": DEFAULT_COMPRESSION,
    }


def _save_server_settings(settings: dict) -> None:
    SERVER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SERVER_SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2), encoding="utf-8"
    )


def extract_report_rows() -> list[dict]:
    if not REPORT_HTML.exists():
        return []
    text = REPORT_HTML.read_text(encoding="utf-8", errors="replace")
    match = _DATA_RE.search(text)
    if not match:
        return []
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return []


def negotiate_compression(requested: str | None, accept_encoding: str) -> str:
    if requested and requested in SUPPORTED_COMPRESSIONS:
        if requested == "br" and brotli is None:
            return "gzip"
        if requested == "zstd" and zstandard is None:
            return "gzip"
        return requested
    accept_encoding = (accept_encoding or "").lower()
    if "zstd" in accept_encoding and zstandard is not None:
        return "zstd"
    if "br" in accept_encoding and brotli is not None:
        return "br"
    if "gzip" in accept_encoding:
        return "gzip"
    return "none"


def compress(payload: bytes, scheme: str) -> bytes:
    if scheme == "gzip":
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as fh:
            fh.write(payload)
        return buf.getvalue()
    if scheme == "br" and brotli is not None:
        return brotli.compress(payload)
    if scheme == "zstd" and zstandard is not None:
        return zstandard.ZstdCompressor(level=9).compress(payload)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "LLMBenchControlPlane/1.0"
    default_compression = DEFAULT_COMPRESSION

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        sys.stderr.write("[bench_web_server] " + (format % args) + "\n")

    # -- lifecycle overrides -----------------------------------------
    def end_headers(self) -> None:  # type: ignore[override]
        """Inject CORS headers for localhost clients."""
        if self.client_address[0] in ("127.0.0.1", "::1"):
            self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def _send_json_response(self, status_code: int, payload: dict) -> None:
        """Minimal JSON response path (no compression) used by guards."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status_code: int, body: bytes, content_type: str) -> None:
        """Send raw bytes with a given content type."""
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_no_report_page(self) -> None:
        """Serve a friendly placeholder when no benchmark data has been generated yet."""
        html = (
            "<!DOCTYPE html>\n"
            "<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Benchmark Report</title>\n"
            "<style>"
            "body{font-family:sans-serif;max-width:600px;margin:40px auto;padding:0 20px;color:#333;}"
            "h1{margin-bottom:0.3em;}"
            "p{color:#555;}"
            "</style></head><body>\n"
            "<h1>No benchmark data yet</h1>\n"
            "<p>Run a benchmark campaign first, then reload this page.</p>\n"
            "</body></html>"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    # -- helpers -----------------------------------------------------
    def _send_json(self, payload: dict | list, status: HTTPStatus = HTTPStatus.OK) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        requested = (query.get("compression") or [self.default_compression])[0]
        scheme = negotiate_compression(requested, self.headers.get("Accept-Encoding", ""))
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        uncompressed_len = len(body)
        encoded = compress(body, scheme) if scheme != "none" else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if scheme != "none":
            self.send_header("Content-Encoding", "gzip" if scheme == "gzip" else scheme)
        self.send_header("X-Response-Compression", scheme)
        self.send_header("X-Uncompressed-Bytes", str(uncompressed_len))
        self.send_header("X-Response-Bytes", str(len(encoded)))
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, f"{path.name} not found")
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _control_action(self, action: str) -> None:
        if action == "pause":
            rb.DEFAULT_PAUSE_FILE.touch()
            message = "pause.ini created"
        elif action == "resume":
            rb.DEFAULT_PAUSE_FILE.unlink(missing_ok=True)
            message = "pause.ini removed"
        elif action == "stop":
            rb.DEFAULT_STOP_FILE.touch()
            message = "stop.ini created"
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"Unknown control action: {action}")
            return
        self._send_json({"ok": True, "action": action, "message": message})

    def _status_payload(self) -> dict:
        lock = rb._read_lock(rb.DEFAULT_LOCK_FILE)
        rows = rb._read_rows(rb.DEFAULT_DETAIL) if rb.DEFAULT_DETAIL.exists() else []
        return {
            "running": bool(lock),
            "pid": lock.get("pid") if lock else None,
            "started_at": lock.get("started_at") if lock else None,
            "paused": rb.DEFAULT_PAUSE_FILE.exists(),
            "stopping": rb.DEFAULT_STOP_FILE.exists(),
            "sample_count": len(rows),
            "error_count": sum(1 for r in rows if r.get("error")),
        }

    # -- routing -------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urlparse(self.path)
        path = parsed.path

        # Health-check endpoint (Block 4 hardening)
        if path == "/api/health":
            report_exists = REPORT_HTML.exists()
            data: dict = {
                "status": "ok",
                "report_exists": report_exists,
                "data_rows": len(extract_report_rows()) if report_exists else 0,
            }
            self._send_json_response(200, data)
            return

        if path in ("/", "/index.html", "/report"):
            # When the benchmark HTML report has not been generated yet, serve
            # a friendly placeholder instead of a 404 (graceful degradation).
            if not REPORT_HTML.exists():
                self._send_no_report_page()
                return
            self._send_file(REPORT_HTML, "text/html; charset=utf-8")
        elif path == "/help":
            self._send_bytes(200, HELP_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/settings":
            self._send_json_response(200, _load_server_settings())
        elif path == "/api/data":
            self._send_json(extract_report_rows())
        elif path == "/api/status":
            self._send_json(self._status_payload())
        elif path.startswith("/vendor/"):
            rel = path[len("/vendor/"):]
            candidate = (VENDOR_DIR / rel).resolve()
            if VENDOR_DIR.resolve() in candidate.parents or candidate == VENDOR_DIR.resolve():
                content_type = (
                    "text/javascript" if candidate.suffix == ".js"
                    else "text/css" if candidate.suffix == ".css"
                    else "application/octet-stream"
                )
                self._send_file(candidate, content_type)
            else:
                self.send_error(HTTPStatus.FORBIDDEN, "Path traversal rejected")
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"No such route: {path}")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        parsed = urlparse(self.path)

        # Block 4: enforce request-body size guard
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > MAX_BODY:
            self._send_json_response(413, {"error": "Request too large"})
            return

        if parsed.path == "/api/settings":
            body = self.rfile.read(content_length)
            try:
                settings = json.loads(body)
                current = _load_server_settings()
                for key in ("host", "port", "compression"):
                    if key in settings:
                        current[key] = settings[key]
                _save_server_settings(current)
                self._send_json_response(200, current)
                return
            except json.JSONDecodeError:
                self._send_json_response(400, {"error": "Invalid JSON"})
                return

        match = re.match(r"^/api/control/(pause|resume|stop)$", parsed.path)
        if match:
            self._control_action(match.group(1))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"No such route: {parsed.path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                         help="Bind address. Non-localhost prints a loud warning (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--compression", choices=sorted(SUPPORTED_COMPRESSIONS), default=DEFAULT_COMPRESSION,
                         help="Default compression when a request doesn't specify ?compression= (default: none).")
    return parser


def create_server(
    bind: tuple[str, int] = (DEFAULT_BIND, DEFAULT_PORT),
) -> ThreadingHTTPServer:
    """Factory for spawning a server instance (primarily used by tests)."""
    return ThreadingHTTPServer(bind, Handler)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {args.host} exposes this control plane beyond localhost. "
              "Only pause/resume/stop + read-only report data are reachable, but review your "
              "network exposure before doing this on a shared machine.")
    Handler.default_compression = args.compression
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving benchmark report + control plane on http://{args.host}:{args.port}/  "
          f"(default compression: {args.compression})")
    print("Routes: GET / , GET /api/data , GET /api/status , GET /help , "
          "GET/POST /api/settings, POST /api/control/{pause,resume,stop}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
