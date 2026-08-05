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

    python scripts\\bench_web_server.py                  # 127.0.0.1:8765
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
        if path in ("/", "/index.html", "/report"):
            self._send_file(REPORT_HTML, "text/html; charset=utf-8")
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
        match = re.match(r"^/api/control/(pause|resume|stop)$", parsed.path)
        if match:
            self._control_action(match.group(1))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"No such route: {parsed.path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                         help="Bind address. Non-localhost prints a loud warning (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--compression", choices=sorted(SUPPORTED_COMPRESSIONS), default=DEFAULT_COMPRESSION,
                         help="Default compression when a request doesn't specify ?compression= (default: none).")
    return parser


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
    print("Routes: GET / , GET /api/data , GET /api/status , "
          "POST /api/control/{pause,resume,stop}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
