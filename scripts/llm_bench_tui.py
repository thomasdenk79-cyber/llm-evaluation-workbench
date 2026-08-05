#!/usr/bin/env python3
"""Textual TUI control center for the LLM benchmark workbench.

Launched automatically by ``run_benchmark.py`` when it is invoked with no
CLI arguments in an interactive terminal (see ``main()`` there). Can also be
started directly:

    python scripts\\llm_bench_tui.py

This module is a *thin client* over state/control that already exists and
is already tested elsewhere in this repository -- it does not reimplement
the pause/stop/resume file protocol, the Ollama discovery calls, or the
Tabulator report's row-building/color-tiering logic. See
``docs/project/tui-web-architecture.md`` for the full concept/architecture
this file implements a slice of.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from queue import Empty, Queue
from threading import Thread
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_benchmark as rb  # noqa: E402  (reuse the existing control protocol, never duplicate it)

from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.screen import ModalScreen  # noqa: E402
from textual.widgets import (  # noqa: E402
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Log,
    Select,
    SelectionList,
    Static,
    Switch,
    TabbedContent,
    TabPane,
    Checkbox,
)

ROOT = rb.ROOT
CONFIG_DIR = ROOT / "config"
BACKUP_DIR = ROOT / "benchmark_results" / "llm-inventory-backups"

# Sane, generic reset-to-default values for the *tuning knobs* a campaign
# TOML exposes. Deliberately does not include a curated `models`/`suites`
# list -- resetting those would silently discard hand-picked campaign data,
# which is exactly the kind of "fix" a user did not ask for.
DEFAULT_KNOBS: dict[str, Any] = {
    "backend": "ollama",
    "runs": 1,
    "timeout_sec": 900,
    "resume": "auto",
    "run": "resume",
    "ollama_url": "http://127.0.0.1:11434/api/generate",
}

TIER_COLORS = {
    0: "grey50",
    1: "magenta",       # violet / ganz schlecht
    2: "red",           # schlecht
    3: "dark_orange",   # nicht gut
    4: "yellow",        # geht so
    5: "dodger_blue1",  # gut
    6: "green3",        # sehr gut
}
STATUS_COLORS = {
    "done": "green3",
    "error": "red",
    "failed": "red",
    "warning": "dark_orange",
    "scheduled": "grey62",
    "running": "dodger_blue1",
    "processing": "dodger_blue1",
    "stopped": "grey62",
    "paused": "dark_orange",
}


# --------------------------------------------------------------------------
# TOML load/save -- a small hand-rolled writer, deliberately dependency-free
# (no ``tomli_w`` in this repo's requirements.txt today). Reads with the
# stdlib ``tomllib`` already used by ``run_benchmark.py``; only the small,
# flat ``[benchmark]`` table shape used by this repo's campaign files is
# supported, and the file's leading comment block is preserved verbatim.
# --------------------------------------------------------------------------

def load_campaign_toml(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_lines = []
    for line in lines:
        if line.strip().startswith("[") or (line.strip() and not line.strip().startswith("#")):
            break
        header_lines.append(line)
    header = "\n".join(header_lines).rstrip("\n")
    table = rb._config(path)
    return header, table


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(str(value))


def _toml_value(value: Any) -> str:
    if isinstance(value, list):
        if len(value) <= 3:
            return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
        inner = ",\n  ".join(_toml_scalar(v) for v in value)
        return "[\n  " + inner + ",\n]"
    return _toml_scalar(value)


def write_campaign_toml(path: Path, header: str, table: dict[str, Any]) -> None:
    out = []
    if header.strip():
        out.append(header)
    out.append("[benchmark]")
    for key, value in table.items():
        out.append(f"{key} = {_toml_value(value)}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def list_campaign_configs() -> list[Path]:
    return sorted(CONFIG_DIR.glob("*.toml"))


# --------------------------------------------------------------------------
# Ollama / llama.cpp discovery helpers (kept intentionally small; the full
# feasibility-classification logic lives in agent_helper_eval.ollama_inventory
# and is out of scope for this quick "what's installed" list).
# --------------------------------------------------------------------------

def fetch_ollama_tags(url: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=5) as response:
        return json.loads(response.read().decode("utf-8")).get("models", [])


def gguf_registry(table: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for spec in rb._split(table.get("llama_models", [])):
        name, sep, path = spec.partition("=")
        if sep:
            out.append((name.strip(), path.strip()))
    return out


def discover_gguf_models() -> list[tuple[str, str]]:
    """Discover complete local GGUF files, even when TOML registration is absent."""
    roots = [
        Path.home() / "llama.cpp" / "models",
        Path.home() / "llama.cpp-ik" / "models",
    ]
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.gguf")):
            if path.name.lower().startswith("ggml-vocab-"):
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append((path.stem, resolved))
    return found


def benchmark_files() -> list[tuple[str, str]]:
    return [
        (path.stem, str(path))
        for path in sorted((ROOT / "benchmarks").glob("*.json"))
    ]


# --------------------------------------------------------------------------
# Report data: parse the *same* embedded JSON the Tabulator HTML grid uses,
# so the Results screen below can never drift from the HTML report -- it is
# reading the identical payload, not recomputing it.
# --------------------------------------------------------------------------

_DATA_RE = re.compile(r"const DATA=(\[.*?\]);", re.DOTALL)


def load_report_rows(html_path: Path) -> list[dict[str, Any]]:
    if not html_path.exists():
        return []
    text = html_path.read_text(encoding="utf-8", errors="replace")
    match = _DATA_RE.search(text)
    if not match:
        return []
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return []


# --------------------------------------------------------------------------
# Small modal for single-line text prompts (pull model / register GGUF).
# --------------------------------------------------------------------------

class PromptModal(ModalScreen[Optional[str]]):
    DEFAULT_CSS = """
    PromptModal { align: center middle; }
    #dialog { width: 70; padding: 1 2; border: round $accent; background: $panel; }
    #dialog Input { margin-top: 1; }
    #dialog Horizontal { margin-top: 1; height: 3; }
    """

    def __init__(self, title: str, placeholder: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self._title)
            yield Input(placeholder=self._placeholder, id="value")
            with Horizontal():
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one("#value", Input).value.strip() or None)
        else:
            self.dismiss(None)


# --------------------------------------------------------------------------
# Dashboard: run control (start/pause/resume/stop) over the existing
# pause.ini / stop.ini / .benchmark_master.pid file protocol.
# --------------------------------------------------------------------------

class DashboardPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Static(id="status_line")
        with Horizontal(id="controls"):
            yield Button("▶ Start", id="start", variant="success")
            yield Button("⏸ Pause", id="pause", variant="warning")
            yield Button("⏵ Resume", id="resume")
            yield Button("⏹ Stop", id="stop", variant="error")
            yield Button("↻ Refresh", id="refresh")
        yield Log(id="run_log", highlight=True)

    def on_mount(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._log_queue: Queue[str] = Queue()
        self._reader: Optional[Thread] = None
        self.refresh_status()
        self.set_interval(2.0, self.refresh_status)
        self.set_interval(0.2, self._pump_log)

    def refresh_status(self) -> None:
        lock = rb._read_lock(rb.DEFAULT_LOCK_FILE)
        paused = rb.DEFAULT_PAUSE_FILE.exists()
        stopping = rb.DEFAULT_STOP_FILE.exists()
        if lock:
            state = "⏸ paused" if paused else ("⏹ stopping" if stopping else "▶ running")
            line = f"[b]{state}[/b]  PID {lock['pid']}  started {lock.get('started_at', '?')}"
        else:
            line = "[dim]idle -- no benchmark master running[/dim]"
        detail = rb.DEFAULT_DETAIL
        if detail.exists():
            rows = rb._read_rows(detail)
            errors = sum(1 for r in rows if r.get("error"))
            line += f"   |  {len(rows)} samples in {detail.name} ({errors} errors)"
        self.query_one("#status_line", Static).update(line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        log = self.query_one("#run_log", Log)
        if event.button.id == "start":
            if rb._read_lock(rb.DEFAULT_LOCK_FILE):
                log.write_line("A benchmark master is already running; use Stop first.")
                return
            configs = list_campaign_configs()
            config_path = configs[0] if configs else rb.DEFAULT_CONFIG
            command = [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"), "--config", str(config_path)]
            log.write_line(f"Starting: {' '.join(command)}")
            popen_kwargs: dict[str, Any] = {}
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            self._proc = subprocess.Popen(
                command, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, text=True, bufsize=1, **popen_kwargs,
            )
            self._reader = Thread(target=self._read_child_output, daemon=True)
            self._reader.start()
        elif event.button.id == "pause":
            rb.DEFAULT_PAUSE_FILE.touch()
            log.write_line("Pause requested (pause.ini created).")
        elif event.button.id == "resume":
            rb.DEFAULT_PAUSE_FILE.unlink(missing_ok=True)
            log.write_line("Resume requested (pause.ini removed).")
        elif event.button.id == "stop":
            rb.DEFAULT_STOP_FILE.touch()
            log.write_line("Stop requested (stop.ini created); active runner will terminate cleanly.")
        elif event.button.id == "refresh":
            self.refresh_status()
        self.refresh_status()

    def _read_child_output(self) -> None:
        process = self._proc
        if not process or process.stdout is None:
            return
        for line in process.stdout:
            self._log_queue.put(line.rstrip())

    def _pump_log(self) -> None:
        log = self.query_one("#run_log", Log)
        while True:
            try:
                log.write_line(self._log_queue.get_nowait())
            except Empty:
                break
        if self._proc and self._proc.poll() is not None:
            log.write_line(f"[run_benchmark.py exited with code {self._proc.returncode}]")
            self._proc = None
            self._reader = None


# --------------------------------------------------------------------------
# Config editor
# --------------------------------------------------------------------------

class ConfigPane(Vertical):
    def compose(self) -> ComposeResult:
        configs = list_campaign_configs()
        options = [(p.name, str(p)) for p in configs] or [("(none found)", "")]
        with Horizontal():
            yield Select(options, id="config_select", value=options[0][1])
            yield Button("↻ Reload", id="reload")
        with Horizontal(classes="field_row"):
            yield Label("Benchmark", classes="field_label")
            yield Select(benchmark_files() or [("(none found)", "")], id="benchmark_file")
        with Horizontal(classes="field_row"):
            yield Label("Backend", classes="field_label")
            yield Select([(v, v) for v in ("ollama", "llama_cpp", "both")], id="field_backend")
            yield Label("Runs", classes="field_label compact_label")
            yield Input(id="field_runs")
            yield Label("Timeout", classes="field_label compact_label")
            yield Input(id="field_timeout_sec")
        with VerticalScroll(id="model_selection_wrap"):
            yield Label("Models (click to select; local GGUFs are discovered automatically)")
            yield SelectionList(id="model_selection")
        with Horizontal(classes="field_row"):
            yield Label("Ollama URL", classes="field_label")
            yield Input(id="field_ollama_url")
        with Horizontal():
            yield Button("💾 Save", id="save", variant="success")
            yield Switch(id="confirm_reset")
            yield Label("confirm reset")
            yield Button("⟲ Reset knobs to defaults", id="reset", variant="warning")
        yield Static(id="config_status")

    def on_mount(self) -> None:
        self._load_selected()

    def _current_path(self) -> Optional[Path]:
        value = self.query_one("#config_select", Select).value
        return Path(value) if value else None

    def _load_selected(self) -> None:
        path = self._current_path()
        if not path or not path.exists():
            return
        header, table = load_campaign_toml(path)
        self._header, self._table = header, table
        self.query_one("#field_backend", Select).value = str(table.get("backend", "ollama"))
        for key in ("runs", "timeout_sec"):
            self.query_one(f"#field_{key}", Input).value = str(table.get(key, ""))
        self.query_one("#field_ollama_url", Input).value = str(table.get("ollama_url", ""))
        benchmark_path = str(table.get("benchmark_file", ""))
        if not benchmark_path:
            configured_runner = rb._split(table.get("runner_args", []))
            if "--benchmark-file" in configured_runner:
                idx = configured_runner.index("--benchmark-file")
                if idx + 1 < len(configured_runner):
                    benchmark_path = configured_runner[idx + 1]
        benchmark_select = self.query_one("#benchmark_file", Select)
        benchmark_select.value = benchmark_path if benchmark_path else Select.BLANK
        self._refresh_model_options(table)
        self.query_one("#config_status", Static).update(f"Loaded {path}")

    def _refresh_model_options(self, table: dict[str, Any]) -> None:
        selected_ollama = set(rb._split(table.get("models", [])) + rb._split(table.get("ollama_models", [])))
        selected_llama = {name for name, _ in gguf_registry(table)}
        choices: list[tuple[str, str, bool]] = []
        try:
            ollama = fetch_ollama_tags(str(table.get("ollama_url", "http://127.0.0.1:11434")).replace("/api/generate", ""))
        except Exception:
            ollama = []
        names = sorted(set(selected_ollama) | {str(item.get("name")) for item in ollama if item.get("name")})
        choices.extend((f"[Ollama] {name}", f"ollama|{name}", name in selected_ollama) for name in names)
        ggufs = dict(gguf_registry(table))
        for name, path in discover_gguf_models():
            ggufs.setdefault(name, path)
        choices.extend((f"[llama.cpp] {name}", f"llama|{name}={path}", name in selected_llama)
                       for name, path in sorted(ggufs.items()))
        self.query_one("#model_selection", SelectionList).set_options(choices)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "config_select":
            self._load_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        status = self.query_one("#config_status", Static)
        path = self._current_path()
        if not path:
            return
        if event.button.id == "reload":
            self._load_selected()
        elif event.button.id == "save":
            table = dict(self._table)
            backend_value = self.query_one("#field_backend", Select).value
            table["backend"] = str(backend_value) if backend_value not in (Select.BLANK, None) else table.get("backend")
            for int_key in ("runs", "timeout_sec"):
                raw = self.query_one(f"#field_{int_key}", Input).value.strip()
                if raw.isdigit():
                    table[int_key] = int(raw)
            table["ollama_url"] = self.query_one("#field_ollama_url", Input).value.strip() or table.get("ollama_url")
            selected = self.query_one("#model_selection", SelectionList).selected
            table["models"] = [str(value).split("|", 1)[1] for value in selected if str(value).startswith("ollama|")]
            table["llama_models"] = [str(value).split("|", 1)[1] for value in selected if str(value).startswith("llama|")]
            benchmark_value = self.query_one("#benchmark_file", Select).value
            if benchmark_value not in (Select.BLANK, None, ""):
                runner_args = list(rb._split(table.get("runner_args", [])))
                if "--benchmark-file" in runner_args:
                    idx = runner_args.index("--benchmark-file")
                    if idx + 1 < len(runner_args):
                        runner_args[idx + 1] = str(benchmark_value)
                else:
                    runner_args.extend(["--benchmark-file", str(benchmark_value)])
                table["runner_args"] = runner_args
            write_campaign_toml(path, self._header, table)
            self._table = table
            status.update(f"Saved {path} at {datetime.now().strftime('%H:%M:%S')}")
        elif event.button.id == "reset":
            if not self.query_one("#confirm_reset", Switch).value:
                status.update("[b]Flip the confirm switch first[/b] -- reset only touches tuning knobs, not your model list.")
                return
            table = dict(self._table)
            table.update(DEFAULT_KNOBS)
            write_campaign_toml(path, self._header, table)
            self.query_one("#confirm_reset", Switch).value = False
            self._load_selected()
            status.update(f"Reset tuning knobs to defaults in {path} (models/suites left untouched).")


# --------------------------------------------------------------------------
# Models & backends
# --------------------------------------------------------------------------

class ModelsPane(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Button("↻ Refresh", id="refresh")
            yield Select([], id="pull_model", prompt="Select Ollama model to pull")
            yield Button("⬇ Pull selected", id="pull")
            yield Button("📎 Register GGUF path", id="register")
            yield Button("🗄 Backup inventory", id="backup")
        yield DataTable(id="models_table")
        yield Static(id="models_status")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Source", "Name", "Size", "Notes")
        table.cursor_type = "row"
        self.refresh_models()

    def _config_table(self) -> dict[str, Any]:
        configs = list_campaign_configs()
        if not configs:
            return {}
        return rb._config(configs[0])

    def refresh_models(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        status = self.query_one("#models_status", Static)
        cfg = self._config_table()
        url = str(cfg.get("ollama_url", "http://127.0.0.1:11434")).replace("/api/generate", "")
        try:
            tags = fetch_ollama_tags(url)
            for entry in sorted(tags, key=lambda e: str(e.get("name", ""))):
                size_gb = (entry.get("size") or 0) / (1024 ** 3)
                table.add_row("Ollama", str(entry.get("name", "")), f"{size_gb:.1f} GB", "")
            installed = {str(entry.get("name", "")) for entry in tags}
            configured = rb._split(cfg.get("models", [])) + rb._split(cfg.get("ollama_models", []))
            candidates = sorted(installed | set(configured))
            self.query_one("#pull_model", Select).set_options(
                [(name, name) for name in candidates if name not in installed]
            )
            ollama_note = f"{len(tags)} Ollama models ({sum(name not in installed for name in candidates)} pull candidates)"
        except Exception as exc:
            ollama_note = f"Ollama unavailable: {exc}"
        registered = dict(gguf_registry(cfg))
        for name, path in discover_gguf_models():
            registered.setdefault(name, path)
        for name, path in sorted(registered.items()):
            p = Path(path)
            size = f"{p.stat().st_size / (1024**3):.1f} GB" if p.is_file() else "missing"
            table.add_row("llama.cpp", name, size, path if p.is_file() else "path not found")
        status.update(ollama_note)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        status = self.query_one("#models_status", Static)
        if event.button.id == "refresh":
            self.refresh_models()
        elif event.button.id == "pull":
            selected = self.query_one("#pull_model", Select).value
            if selected not in (Select.BLANK, None, ""):
                self._do_pull(str(selected))
            else:
                status.update("Select an Ollama model first.")
        elif event.button.id == "register":
            self.app.push_screen(PromptModal("NAME=path\\to\\model.gguf"), self._do_register)
        elif event.button.id == "backup":
            self._do_backup()

    def _do_pull(self, value: Optional[str]) -> None:
        if not value:
            return
        status = self.query_one("#models_status", Static)
        status.update(f"Pulling {value} … (this can take a while)")
        try:
            subprocess.Popen(["ollama", "pull", value], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        except OSError as exc:
            status.update(f"Cannot start 'ollama pull': {exc}")

    def _do_register(self, value: Optional[str]) -> None:
        if not value or "=" not in value:
            return
        name, _, path = value.partition("=")
        status = self.query_one("#models_status", Static)
        if not Path(path.strip()).is_file():
            status.update(f"Path not found, not registering: {path}")
            return
        configs = list_campaign_configs()
        if not configs:
            status.update("No campaign TOML found to register into.")
            return
        header, table = load_campaign_toml(configs[0])
        existing = rb._split(table.get("llama_models", []))
        existing.append(f"{name.strip()}={path.strip()}")
        table["llama_models"] = existing
        write_campaign_toml(configs[0], header, table)
        status.update(f"Registered {name.strip()} into {configs[0].name}.")
        self.refresh_models()

    def _do_backup(self) -> None:
        status = self.query_one("#models_status", Static)
        cfg = self._config_table()
        url = str(cfg.get("ollama_url", "http://127.0.0.1:11434")).replace("/api/generate", "")
        snapshot: dict[str, Any] = {
            "schema_version": "llm-inventory-backup-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            snapshot["ollama_models"] = fetch_ollama_tags(url)
        except Exception as exc:
            snapshot["ollama_models"] = []
            snapshot["ollama_error"] = str(exc)
        snapshot["llama_cpp_models"] = [{"name": n, "path": p} for n, p in gguf_registry(cfg)]
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        out_path = BACKUP_DIR / f"inventory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        status.update(f"Backup written to {out_path.relative_to(ROOT)}")


# --------------------------------------------------------------------------
# Results / report -- reads the *same* JSON payload the Tabulator HTML grid
# renders, so the two views cannot drift.
# --------------------------------------------------------------------------

COLUMNS = [
    ("benchmark", "Benchmark"), ("status", "Status"), ("provider", "Provider"), ("backend", "Backend"),
    ("model", "Model"), ("runs", "Runs"), ("samples", "Tasks"), ("elapsed", "Elapsed"), ("heuristic_score", "Heuristic"),
    ("rating_score", "Score"), ("rating", "Suitability"), ("interpretation", "Interpretation"),
]


class ResultsPane(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Input(placeholder="Filter…", id="filter")
            yield Button("↻ Refresh", id="refresh")
            yield Button("🌐 Open in browser", id="open_browser")
            yield Button("📊 Open in Excel", id="open_excel")
        yield DataTable(id="results_table")
        yield Static(id="results_status")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for key, title in COLUMNS:
            table.add_column(title, key=key)
        table.cursor_type = "row"
        self._rows: list[dict[str, Any]] = []
        self.refresh_results()

    def report_path(self) -> Path:
        return ROOT / "docs" / "project" / "benchmark_report.html"

    def refresh_results(self) -> None:
        self._rows = load_report_rows(self.report_path())
        self._populate_table(self._rows)
        self.query_one("#results_status", Static).update(
            f"{len(self._rows)} rows from {self.report_path().relative_to(ROOT)}"
        )

    def _populate_table(self, rows: list[dict[str, Any]]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for row in rows:
            cells = []
            for key, _title in COLUMNS:
                value = row.get(key, "")
                text = str(value)
                if key == "status":
                    color = STATUS_COLORS.get(str(value).lower(), "white")
                    text = f"[{color}]{value}[/{color}]"
                elif key in ("heuristic_score", "rating_score"):
                    tier = row.get(f"{key}_tier", 0)
                    color = TIER_COLORS.get(tier, "white")
                    text = f"[{color}]{value}[/{color}]"
                elif key == "interpretation" and len(text) > 60:
                    text = text[:57] + "…"
                cells.append(text)
            table.add_row(*cells)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if event.data_table.id != "results_table":
            return
        key = str(event.column_key)
        self._rows.sort(key=lambda row: str(row.get(key, "")).lower())
        self._populate_table(self._rows)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        needle = event.value.strip().lower()
        if not needle:
            self._populate_table(self._rows)
            return
        filtered = [r for r in self._rows if needle in json.dumps(r, ensure_ascii=False).lower()]
        self._populate_table(filtered)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.refresh_results()
        elif event.button.id == "open_browser":
            os.startfile(str(self.report_path()))  # noqa: S606 -- local file, user-triggered
        elif event.button.id == "open_excel":
            out = ROOT / "benchmark_results" / "benchmark_report_export.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=[key for key, _ in COLUMNS])
                writer.writeheader()
                for row in self._rows:
                    writer.writerow({key: row.get(key, "") for key, _ in COLUMNS})
            os.startfile(str(out))  # noqa: S606


# --------------------------------------------------------------------------
# Leaderboard -- top 5 models overall + a simple ASCII bar chart (no extra
# charting dependency; see docs/project/tui-web-architecture.md for the
# `plotext` upgrade path if the project wants richer charts later).
# --------------------------------------------------------------------------

class LeaderboardPane(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Button("↻ Refresh", id="refresh")
        yield DataTable(id="board_table")
        yield Static(id="chart")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("#", "Model", "Backend", "Heuristic (avg)", "Elapsed s (avg)")
        self.refresh_board()

    def refresh_board(self) -> None:
        rows = load_report_rows(ROOT / "docs" / "project" / "benchmark_report.html")
        by_model: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            by_model.setdefault((row.get("model", ""), row.get("backend", "")), []).append(row)

        def avg(items: list[dict[str, Any]], key: str) -> float:
            values = [float(i[key]) for i in items if isinstance(i.get(key), (int, float))]
            return sum(values) / len(values) if values else 0.0

        ranked = sorted(
            ((model, backend, avg(items, "heuristic_score"), avg(items, "wall_seconds"))
             for (model, backend), items in by_model.items()),
            key=lambda t: t[2], reverse=True,
        )[:5]
        table = self.query_one(DataTable)
        table.clear()
        for i, (model, backend, score, wall) in enumerate(ranked, 1):
            table.add_row(str(i), model, backend, f"{score:.1f}", f"{wall:.1f}")

        chart_lines = []
        max_score = max((r[2] for r in ranked), default=1.0) or 1.0
        for model, backend, score, _wall in ranked:
            bar_len = int(40 * score / max_score)
            tier = 6 if score >= max_score * 0.9 else 5 if score >= max_score * 0.7 else 4 if score >= max_score * 0.5 else 3
            color = TIER_COLORS.get(tier, "white")
            bar = "█" * max(bar_len, 1)
            chart_lines.append(f"{model[:24]:<24} [{color}]{bar}[/{color}] {score:.1f}")
        self.query_one("#chart", Static).update("\n".join(chart_lines) or "No data yet.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh":
            self.refresh_board()


class AgentMonitorPane(Vertical):
    """Launcher bridge to the existing Tommy Agent Monitor, without duplicating it."""

    MONITOR = Path(r"C:\GIT\wt-command-center\scripts\agents\agent_monitor_ui.py")
    COLLECTOR = Path(r"C:\GIT\wt-command-center\scripts\agents\agent-monitor.ps1")

    def compose(self) -> ComposeResult:
        yield Static(
            "Existing Tommy Agent Monitor 3.x is kept as the monitoring implementation. "
            "This tab launches it with the same collector and configuration."
        )
        with Horizontal(id="controls"):
            yield Button("▶ Open monitor", id="open_monitor", variant="primary")
            yield Button("⏹ Stop monitor", id="stop_monitor", variant="error")
            yield Button("↻ Check", id="check_monitor")
        yield Static(id="monitor_status")

    def on_mount(self) -> None:
        self.check_monitor()

    def check_monitor(self) -> None:
        status = self.query_one("#monitor_status", Static)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*agent_monitor_ui.py*'} | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, check=False,
        )
        pids = [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]
        status.update(f"Monitor: {'running (PID ' + ', '.join(pids) + ')' if pids else 'not running'}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "open_monitor":
            if not self.MONITOR.is_file():
                self.query_one("#monitor_status", Static).update(f"Monitor not found: {self.MONITOR}")
                return
            command = (
                f"Set-Location -LiteralPath '{self.MONITOR.parent}'; "
                f"python '{self.MONITOR}' --mode terminal --backend-script '{self.COLLECTOR}' --refresh 2"
            )
            subprocess.Popen(["powershell.exe", "-NoExit", "-Command", command])
        elif event.button.id == "stop_monitor":
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*agent_monitor_ui.py*'} | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                check=False,
            )
        self.check_monitor()


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

class BenchmarkTUI(App):
    TITLE = "LLM Evaluation Workbench"
    SUB_TITLE = "Textual control center"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("1", "show_tab('dashboard')", "Dashboard"),
        Binding("2", "show_tab('config')", "Config"),
        Binding("3", "show_tab('models')", "Models"),
        Binding("4", "show_tab('results')", "Results"),
        Binding("5", "show_tab('leaderboard')", "Leaderboard"),
        Binding("6", "show_tab('agent_monitor')", "Agent monitor"),
    ]
    CSS = """
    Screen { background: #0b1420; color: #e8f8ff; }
    Header, Footer { background: #101f31; color: #dff4ff; }
    TabbedContent, TabPane { background: #0b1420; }
    Tab { background: #101f31; color: #9ebcd5; }
    Tab.-active { background: #173b5a; color: #ffffff; }
    Input, Select, SelectionList { background: #101f31; color: #e8f8ff; border: round #31577a; }
    Button { min-width: 12; }
    .field_row { height: 3; align: left middle; }
    .field_label { width: 18; content-align: left middle; }
    .compact_label { width: 8; margin-left: 1; }
    #controls, #fields, #dialog { padding: 0 1; }
    #config_select, #benchmark_file { width: 1fr; }
    #model_selection_wrap { height: 1fr; min-height: 8; border: round $panel-lighten-1; padding: 0 1; }
    #model_selection { height: 1fr; }
    #filter { width: 30; }
    DataTable { height: 1fr; }
    Log { height: 10; border: round $accent; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard", id="dashboard"):
                yield DashboardPane()
            with TabPane("Config", id="config"):
                yield ConfigPane()
            with TabPane("Models & backends", id="models"):
                yield ModelsPane()
            with TabPane("Results / report", id="results"):
                yield ResultsPane()
            with TabPane("Leaderboard", id="leaderboard"):
                yield LeaderboardPane()
            with TabPane("Agent monitor", id="agent_monitor"):
                yield AgentMonitorPane()
        yield Footer()

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id


def main() -> int:
    BenchmarkTUI().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
