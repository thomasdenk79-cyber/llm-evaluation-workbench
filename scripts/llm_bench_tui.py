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

import asyncio
import csv
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from queue import Empty, Queue
from threading import Thread
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import webbrowser as _webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_helper_eval.fork_build as _fork_build  # noqa: E402  (Block 2: fork build automation)
import run_benchmark as rb  # noqa: E402  (reuse the existing control protocol, never duplicate it)

from rich.text import Text  # noqa: E402
from textual import events, work  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Container, Horizontal, Vertical, VerticalScroll  # noqa: E402
from textual.message import Message  # noqa: E402
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
)
from textual.reactive import reactive  # noqa: E402


def _open_file(path: str) -> None:
    """Cross-platform file/URL opener (Linux, macOS, Windows)."""
    p = Path(path)
    if p.is_file():
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        elif sys.platform == "win32":
            os.startfile(str(p))
        else:
            subprocess.Popen(["xdg-open", str(p)])
    else:
        _webbrowser.open(path)


ROOT = rb.ROOT
CONFIG_DIR = ROOT / "config"
BACKUP_DIR = ROOT / "benchmark_results" / "llm-inventory-backups"
MONITOR_SCRIPT = Path(os.environ.get(
    "AGENT_MONITOR_SCRIPT",
    r"C:\GIT\wt-command-center\scripts\agents\agent_monitor_ui.py",
))
MONITOR_COLLECTOR = Path(os.environ.get(
    "AGENT_MONITOR_COLLECTOR",
    r"C:\GIT\wt-command-center\scripts\agents\agent-monitor.ps1",
))
MONITOR_CONFIG = Path(os.environ.get(
    "AGENT_MONITOR_CONFIG",
    str(Path.home() / ".config" / "agent-monitor" / "config.toml"),
))
OLLAMA_PULL_CATALOG = [
    "qwen3.6:27b-q4_K_M",
    "qwen3.6:35b-a3b-q4_K_M",
    "qwen3-coder:30b",
    "gpt-oss:20b",
    "deepseek-coder-v2:16b",
    "devstral-small-2:24b",
    "llama3.1:8b",
    "phi4-mini:3.8b-q4_K_M",
]

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

DENSITY_MODES = [
    ("Wide", "wide"),
    ("Normal", "normal"),
    ("Compact", "compact"),
    ("Mini", "mini"),
    ("Ultra Compact", "ultra-compact"),
]

DENSITY_DEFAULT = "normal"

DENSITY_CSS = {
    "wide": """
    .toolbar { height: 3; padding: 0 2; }
    #dashboard_actions, #leaderboard_actions { height: 1; margin: 0 2; }
    .panel_title { height: 1; padding: 0 2; }
    #status_line { height: 2; padding: 0 2; }
    DataTable > .datatable--header { padding: 0 1; }
    #settings_panel { width: 40; padding: 0 2; }
    #settings_panel Label { height: 1; }
    #settings_panel Input, #settings_panel Select { height: 3; }
    .switch_row { height: 3; }
    #campaign_builder { height: 22; padding: 1 2; }
    #config_status, #models_status, #results_status, #monitor_status { height: 2; padding: 0 2; }
    .monitor_pane { padding: 0; }
    #board_table { height: 12; }
    #leaderboard_charts { height: 1fr; min-height: 10; }
    #chart, #scatter { padding: 1 2; }
    #run_log { height: 1fr; padding: 0 1; }
    """,
    "normal": """
    .toolbar { height: 3; padding: 0 1; }
    #dashboard_actions, #leaderboard_actions { height: 1; margin: 0 1; }
    .panel_title { height: 1; }
    #status_line { height: 2; padding: 0 1; }
    DataTable > .datatable--header { padding: 0 1; }
    #settings_panel { width: 34; padding: 0 1; }
    #settings_panel Label { height: 1; }
    #settings_panel Select { height: 3; }
    #settings_panel Input { height: 3; }
    .switch_row { height: 3; }
    #campaign_builder { height: 20; padding: 1; }
    #config_status, #models_status, #results_status, #monitor_status { height: 2; padding: 0 1; }
    .monitor_pane { padding: 0; }
    #board_table { height: 10; }
    #leaderboard_charts { height: 1fr; min-height: 8; }
    #chart, #scatter { padding: 1; }
    #run_log { height: 1fr; }
    """,
    "compact": """
    .toolbar { height: 2; padding: 0 1; }
    #dashboard_actions, #leaderboard_actions { height: 1; margin: 0 1; }
    .panel_title { height: 1; }
    #status_line { height: 1; padding: 0 1; }
    DataTable > .datatable--header { padding: 0 1; }
    #settings_panel { width: 30; padding: 0 1; }
    #settings_panel Label { height: 1; }
    #settings_panel Input, #settings_panel Select { height: 2; }
    .switch_row { height: 2; }
    #campaign_builder { height: 18; padding: 0 1; }
    #config_status, #models_status, #results_status, #monitor_status { height: 1; padding: 0 1; }
    .monitor_pane { padding: 0; }
    #board_table { height: 8; }
    #leaderboard_charts { height: 1fr; min-height: 6; }
    #chart, #scatter { padding: 0; }
    #run_log { height: 1fr; }
    """,
    "mini": """
    .toolbar { height: 1; padding: 0 1; }
    #dashboard_actions, #leaderboard_actions { height: 1; margin: 0; }
    .panel_title { height: 1; }
    #status_line { height: 1; padding: 0 1; }
    DataTable > .datatable--header { padding: 0 1; }
    #settings_panel { width: 26; padding: 0; }
    #settings_panel Label { height: 1; }
    #settings_panel Input, #settings_panel Select { height: 2; }
    .switch_row { height: 2; }
    #campaign_builder { height: 16; padding: 0; }
    #config_status, #models_status, #results_status, #monitor_status { height: 1; padding: 0 1; }
    .monitor_pane { padding: 0; }
    #board_table { height: 6; }
    #leaderboard_charts { height: 1fr; min-height: 4; }
    #chart, #scatter { padding: 0; }
    #run_log { height: 1fr; }
    """,
    "ultra-compact": """
    .toolbar { height: 1; padding: 0; }
    #dashboard_actions, #leaderboard_actions { height: 1; margin: 0; }
    .panel_title { display: none; }
    #status_line { height: 1; padding: 0 1; }
    DataTable > .datatable--header { padding: 0 1; }
    #settings_panel { width: 22; padding: 0; }
    #settings_panel Label { height: 1; }
    #settings_panel Input, #settings_panel Select { height: 1; }
    .switch_row { height: 1; }
    #campaign_builder { height: 14; padding: 0; }
    #config_status, #models_status, #results_status, #monitor_status { display: none; }
    .monitor_pane { padding: 0; }
    #board_table { height: 5; }
    #leaderboard_charts { height: 1fr; min-height: 3; }
    #chart, #scatter { padding: 0; }
    #run_log { height: 1fr; }
    """,
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


class ActionStrip(Static):
    """Compact mouse/keyboard command row matching Tommy's Agent Monitor."""

    class Selected(Message):
        def __init__(self, strip_id: str, action: str) -> None:
            super().__init__()
            self.strip_id = strip_id
            self.action = action

    def __init__(self, *actions: tuple[str, str, str], id: str | None = None) -> None:
        super().__init__(id=id)
        self.actions = actions

    def render(self) -> Text:
        line = Text()
        for index, (key, label, color) in enumerate(self.actions):
            if index:
                line.append("   ")
            line.append(key, style=f"bold {color}")
            line.append(f" {label}", style="#d5d9dd")
        return line

    def on_click(self, event: events.Click) -> None:
        cursor = 0
        for index, (key, label, _color) in enumerate(self.actions):
            if index:
                cursor += 3
            end = cursor + len(key) + 1 + len(label)
            if cursor <= event.x < end:
                self.post_message(
                    self.Selected(self.id or "", label.casefold().replace(" ", "_"))
                )
                event.stop()
                return
            cursor = end



class CollapsibleSection(Container):
    """Section that can be collapsed/expanded with a clickable header."""

    class Toggled(Message):
        def __init__(self, section_id: str, collapsed: bool) -> None:
            super().__init__()
            self.section_id = section_id
            self.collapsed = collapsed

    collapsed = reactive(False)

    DEFAULT_CSS = """
    CollapsibleSection {
        border: solid #303840;
        background: #101316;
    }
    CollapsibleSection .section-header {
        height: 1;
        background: #182128;
        color: #55ccff;
        text-style: bold;
        padding: 0 1;
    }
    CollapsibleSection .section-header:hover {
        background: #20262c;
    }
    CollapsibleSection .section-content {
        height: auto;
        max-height: 1fr;
        overflow: hidden;
    }
    CollapsibleSection.collapsed .section-header {
        color: #8b949e;
    }
    CollapsibleSection.collapsed .section-content {
        display: none;
    }
    """

    def __init__(
        self,
        title: str,
        *children,
        id: str | None = None,
        initially_collapsed: bool = False,
    ) -> None:
        super().__init__(id=id)
        self.title = title
        self._children = children
        self.collapsed = initially_collapsed

    def compose(self) -> ComposeResult:
        yield Static(
            f"{'[ ]' if self.collapsed else '[*]'} {self.title}",
            classes="section-header",
        )
        with Vertical(classes="section-content"):
            for child in self._children:
                yield child

    def on_click(self, event: events.Click) -> None:
        self.collapsed = not self.collapsed
        self.post_message(self.Toggled(self.id or "", self.collapsed))

    def watch_collapsed(self, value: bool) -> None:
        self.set_classes("collapsed" if value else "")
        self.query_one(".section-header", Static).update(
            f"{'[ ]' if value else '[*]'} {self.title}"
        )
        self.refresh_layout()



class LayoutPanel(ModalScreen[None]):
    """Modal for density mode, section visibility, and layout persistence."""

    class LayoutChanged(Message):
        def __init__(
            self,
            density: str = DENSITY_DEFAULT,
            sections: dict[str, bool] | None = None,
            column_priorities: dict[str, int] | None = None,
        ) -> None:
            super().__init__()
            self.density = density
            self.sections = sections or {}
            self.column_priorities = column_priorities or {}

    DEFAULT_CSS = """
    LayoutPanel {
        align: center middle;
        background: black;
    }
    LayoutPanel #layout_modal {
        width: 70;
        height: 24;
        background: #12161a;
        border: solid #55ccff;
        padding: 1 2;
    }
    LayoutPanel #layout_title {
        height: 1;
        background: #182128;
        color: #55ccff;
        content-align: center middle;
        text-style: bold;
    }
    LayoutPanel #layout_body {
        height: 1fr;
        padding: 0 1;
    }
    LayoutPanel #layout_footer {
        height: 3;
        background: #15191d;
        content-align: center middle;
    }
    LayoutPanel #layout_footer Button {
        width: 12;
        margin: 0 1;
    }
    LayoutPanel Select {
        height: 3;
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Layout & Density Configuration", id="layout_title"),
            Vertical(
                Label("Density Mode"),
                Select(
                    DENSITY_MODES,
                    value=DENSITY_DEFAULT,
                    id="layout_density",
                ),
                Label(""),
                Label("Section Visibility (check to show, uncheck to hide)"),
                SelectionList(*[
                    (f"  Dashboard", "dashboard", True),
                    (f"  Config", "config", True),
                    (f"  Models", "models", True),
                    (f"  Results", "results", True),
                    (f"  Leaderboard", "leaderboard", True),
                    (f"  Agent Monitor", "agent_monitor", True),
                ], id="layout_sections"),
                Label(""),
                Label("Column Priorities (DataTable display order, higher = more important)"),
                SelectionList(*[
                    (f"  Rating", "rating", True),
                    (f"  Model", "model", True),
                    (f"  Throughput (t/s)", "throughput", True),
                    (f"  Quality", "quality", True),
                    (f"  Latency (p95)", "latency", True),
                    (f"  Context", "context", False),
                    (f"  VRAM", "vram", False),
                    (f"  Runs", "runs", False),
                ], id="layout_columns"),
                id="layout_body",
            ),
            Horizontal(
                Button("Save & Apply", id="layout_apply", variant="primary"),
                Button("Reset to Defaults", id="layout_reset"),
                Button("Cancel", id="layout_cancel"),
                id="layout_footer",
            ),
            id="layout_modal",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "layout_cancel":
            self.dismiss(None)
        elif event.button.id == "layout_reset":
            self.query_one("#layout_density", Select).value = DENSITY_DEFAULT
            self.query_one("#layout_sections", SelectionList).select_all()
            for idx in range(5):
                self.query_one("#layout_columns", SelectionList).select(idx)
            for idx in range(5, 8):
                self.query_one("#layout_columns", SelectionList).deselect(idx)
        elif event.button.id == "layout_apply":
            density = self.query_one("#layout_density", Select).value
            sections = dict(self.query_one("#layout_sections", SelectionList).selected)
            columns = dict(self.query_one("#layout_columns", SelectionList).selected)
            self.dismiss(self.LayoutChanged(
                density=density,
                sections=sections,
                column_priorities=columns,
            ))

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-dialog { width: 92; height: 38; border: round #55aaff; background: #101215; padding: 1 2; }
    #help-title { color: #55ccff; text-style: bold; margin-bottom: 1; }
    #help-body { height: 1fr; color: #c5ccd2; }
    #help-actions { height: 1; background: #15191d; }
    """

    def compose(self) -> ComposeResult:
        with Container(id="help-dialog"):
            yield Static("LLM Evaluation Workbench · Help", id="help-title")
            yield Static(
                "QUICK START\n"
                "  Dashboard → Start runs the selected TOML campaign. Pause waits without\n"
                "  creating failures; Resume continues from durable CSV state; Stop ends\n"
                "  only the owned benchmark process tree.\n\n"
                "CAMPAIGN\n"
                "  Select one or more benchmark definitions and models. Backend chooses\n"
                "  Ollama, llama.cpp, Siemens, or a combined local run. Preview shows the\n"
                "  exact model × benchmark × backend plan before execution. Save persists\n"
                "  the visible selections to TOML — what you see is what will run.\n\n"
                "MODELS\n"
                "  Installed Ollama tags and complete GGUFs are discovered automatically.\n"
                "  Pull accepts a normal Ollama tag. Health checks never download models.\n\n"
                "RESULTS\n"
                "  Type / to filter. Click a header to sort. The browser report adds\n"
                "  per-column filters and ordered multi-column grouping.\n\n"
                "KEYS\n"
                "  1–6 tabs · S start · P pause · R resume · X stop · F5 refresh · "
                "/ filter · H help · Q quit\n\n"
                "CLI\n"
                "  python scripts\\run_benchmark.py\n"
                "  python scripts\\run_benchmark.py --config config\\benchmark.toml --show-matrix\n"
                "  python scripts\\run_benchmark.py --config config\\benchmark.toml --doctor",
                id="help-body",
            )
            yield ActionStrip(("Esc", "Close", "#ffcc33"), id="help-actions")

    def on_action_strip_selected(self, _event: ActionStrip.Selected) -> None:
        self.dismiss()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss()


class TuneConfirmScreen(ModalScreen[bool]):
    """Shows the VRAM-tune diff and requires explicit Apply / Cancel confirmation.

    Part of the Propose → Confirm → Apply control contract — no configuration
    is modified until the operator clicks **Apply**.
    """

    DEFAULT_CSS = """
    TuneConfirmScreen { align: center middle; }
    #tune-dialog {
        width: 110;
        height: 40;
        border: round #55aaff;
        background: #101215;
        padding: 1 2;
    }
    #tune-title { height: 2; color: #55ccff; text-style: bold; }
    #tune-body { height: 1fr; color: #c5ccd2; overflow-y: auto; }
    #tune-body .diff-current { color: #8b949e; }
    #tune-body .diff-proposed { color: #70e1a1; }
    #tune-body .diff-changed { color: #ffd166; }
    #tune-body .diff-none { color: #8b949e; }
    #tune-actions { height: 2; background: #15191d; }
    """

    def __init__(self, proposal_lines: list[str]) -> None:
        super().__init__()
        self._lines = proposal_lines

    def compose(self) -> ComposeResult:
        with Container(id="tune-dialog"):
            yield Static("VRAM Tune · Propose → Confirm → Apply", id="tune-title")
            with VerticalScroll(id="tune-body"):
                for line in self._lines:
                    yield Static(line)
            yield ActionStrip(
                ("A", "Apply", "#28c85a"),
                ("Esc", "Cancel", "#ff5555"),
                id="tune-actions",
            )

    def on_action_strip_selected(self, event: ActionStrip.Selected) -> None:
        if event.action == "apply":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(False)
        elif event.key == "a":
            self.dismiss(True)


class InfoScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    InfoScreen { align: center middle; }
    #info-dialog { width: 100; height: 36; border: round #55aaff; background: #101215; padding: 1 2; }
    #info-title { height: 2; color: #55ccff; text-style: bold; }
    #info-body { height: 1fr; color: #c5ccd2; }
    #info-actions { height: 1; }
    """

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text = title
        self.body_text = body

    def compose(self) -> ComposeResult:
        with Container(id="info-dialog"):
            yield Static(self.title_text, id="info-title")
            with VerticalScroll(id="info-body"):
                yield Static(self.body_text)
            yield ActionStrip(("Esc", "Close", "#ffcc33"), id="info-actions")

    def on_action_strip_selected(self, _event: ActionStrip.Selected) -> None:
        self.dismiss()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss()


# --------------------------------------------------------------------------
# TOML load/save -- a small hand-rolled writer, deliberately dependency-free
# (no ``tomli_w`` in this repo's requirements.txt today). Reads with the
# stdlib ``tomllib`` already used by ``run_benchmark.py``; only the small,
# flat ``[benchmark]`` table shape used by this repo's campaign files is
# supported, and the file's leading comment block is preserved verbatim.
# --------------------------------------------------------------------------

def load_campaign_toml(path: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_lines = []
    for line in lines:
        if line.strip().startswith("[") or (line.strip() and not line.strip().startswith("#")):
            break
        header_lines.append(line)
    header = "\n".join(header_lines).rstrip("\n")
    table = rb._config(path)
    matrix = rb._load_matrix(path, include_synthetic=False)
    return header, table, matrix


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


def _build_toml_text(
    header: str,
    table: dict[str, Any],
    matrix: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Render a campaign TOML to a string (used by both normal and atomic writer)."""
    out = []
    if header.strip():
        out.append(header)
    out.append("[benchmark]")
    for key, value in table.items():
        out.append(f"{key} = {_toml_value(value)}")
    for entry in matrix or []:
        out.append("")
        out.append("[[matrix]]")
        for key, value in entry.items():
            out.append(f"{key} = {_toml_value(value)}")
    return "\n".join(out) + "\n"


def write_campaign_toml(
    path: Path,
    header: str,
    table: dict[str, Any],
    matrix: Optional[list[dict[str, Any]]] = None,
) -> None:
    path.write_text(_build_toml_text(header, table, matrix), encoding="utf-8")


def write_campaign_toml_atomic(
    path: Path,
    header: str,
    table: dict[str, Any],
    matrix: Optional[list[dict[str, Any]]] = None,
) -> None:
    """Write TOML with .bak backup for atomic safety (Propose → Confirm → Apply).

    Saves a ``.bak`` copy before overwriting so the user can revert if the
    applied tuning is incorrect.
    """
    bak = Path(str(path) + ".bak")
    if path.exists():
        bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        bak.unlink(missing_ok=True)
    path.write_text(_build_toml_text(header, table, matrix), encoding="utf-8")


def revert_toml_from_backup(path: Path) -> bool:
    """Restore the campaign TOML from a ``.bak`` backup created by the atomic writer.

    Returns ``True`` if a backup was found and restored, ``False`` otherwise.
    """
    bak = Path(str(path) + ".bak")
    if not bak.exists():
        return False
    path.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
    bak.unlink(missing_ok=True)
    return True


def list_campaign_configs() -> list[Path]:
    return sorted(
        CONFIG_DIR.glob("*.toml"),
        key=lambda path: (path.name.casefold() != "benchmark.toml", path.name.casefold()),
    )


# --------------------------------------------------------------------------
# Ollama / llama.cpp discovery helpers (kept intentionally small; the full
# feasibility-classification logic lives in agent_helper_eval.ollama_inventory
# and is out of scope for this quick "what's installed" list).
# --------------------------------------------------------------------------

def fetch_ollama_tags(url: str) -> list[dict[str, Any]]:
    with urllib.request.urlopen(rb._ollama_base_url(url) + "/api/tags", timeout=5) as response:
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
        (name, str(path))
        for name, path in sorted(rb._available_benchmark_files().items())
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
# Error-boundary utilities — Block 3: TUI Robustness Hardening
# Ensures no data-loading exception can propagate to Textual's event loop.
# --------------------------------------------------------------------------

def _safe_load(load_func, error_message: str = "Data load error", default=None):
    """Safely execute a synchronous data-loading function.

    Returns the result on success, or ``default`` on any exception.
    Intended for use inside ``@work(thread=True)`` blocks as well as
    UI-thread callbacks — the caller is responsible for presenting any
    user-facing error messages.
    """
    try:
        return load_func()
    except Exception:
        return default


async def _safe_async_load(
    load_func,
    error_message: str = "Data load error",
    default=None,
    timeout: float | None = None,
):
    """Safely execute an async data-loading function with optional timeout.

    Returns the result on success, or ``default`` on any exception
    (including ``asyncio.TimeoutError``).  Used only at App-level
    coordination where await is available.
    """
    try:
        coro = load_func()
        if timeout is not None:
            return await asyncio.wait_for(coro, timeout=timeout)
        return await coro
    except asyncio.TimeoutError:
        return default
    except Exception:
        return default


def _error_status(widget: Static, message: str) -> None:
    """Display an error message on a Static status widget."""
    widget.update(f"[red]{message}[/red]")


def _retry_status(widget: Static, message: str) -> None:
    """Display a retry-prompt error message on a Static status widget."""
    widget.update(f"[red]{message} — press R to retry or F5 to refresh all[/red]")


# --------------------------------------------------------------------------
# Dashboard: run control (start/pause/resume/stop) over the existing
# pause.ini / stop.ini / .benchmark_master.pid file protocol.
# --------------------------------------------------------------------------

class DashboardPane(Vertical):
    def compose(self) -> ComposeResult:
        yield Static(id="status_line")
        yield ActionStrip(
            ("S", "Start", "#28c85a"),
            ("P", "Pause", "#ffcc33"),
            ("R", "Resume", "#55ccff"),
            ("X", "Stop", "#ff5555"),
            ("F", "Refresh", "#bd93f9"),
            ("W", "Web UI", "#28c85a"),
            ("H", "Help", "#ff8c1e"),
            id="dashboard_actions",
        )
        yield Log(id="run_log", highlight=True)

    def on_mount(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._run_log_path: Optional[Path] = None
        self._log_queue: Queue[str] = Queue()
        self._reader: Optional[Thread] = None
        self.refresh_status()
        self.set_interval(2.0, self.refresh_status)
        self.set_interval(0.2, self._pump_log)

    @work(thread=True, exclusive=True, group="dashboard-status")
    def refresh_status(self) -> None:
        """Refresh dashboard status with error boundaries.

        Every file-system call (lock read, detail CSV) is wrapped so that
        a corrupt or missing file never crashes the TUI.
        """
        try:
            _pause_file, _stop_file, lock_file, detail = self._active_paths()
            lock = _safe_load(
                lambda: rb._read_lock(lock_file),
                "Failed to read process lock",
                default=None,
            )
            paused = _pause_file.exists()
            stopping = _stop_file.exists()
            if lock:
                state = "⏸ paused" if paused else ("⏹ stopping" if stopping else "▶ running")
                line = f"[b]{state}[/b]  PID {lock['pid']}  started {lock.get('started_at', '?')}"
            else:
                line = "[dim]idle -- no benchmark master running[/dim]"
            if detail.exists():
                rows = _safe_load(
                    lambda: rb._read_rows(detail),
                    "Failed to read detail CSV",
                    default=[],
                )
                errors = sum(1 for r in rows if r.get("error"))
                line += f"   |  {len(rows)} samples in {detail.name} ({errors} errors)"
        except Exception as exc:
            line = f"[red]Dashboard error: {exc} — press F5 to refresh[/red]"
        self.app.call_from_thread(self._apply_status, line)

    def _apply_status(self, line: str) -> None:
        self.query_one("#status_line", Static).update(line)

    def on_action_strip_selected(self, event: ActionStrip.Selected) -> None:
        if event.strip_id != "dashboard_actions":
            return
        log = self.query_one("#run_log", Log)
        pause_file, stop_file, lock_file, _detail = self._active_paths()
        if event.action == "start":
            if rb._read_lock(lock_file):
                log.write_line("A benchmark master is already running; use Stop first.")
                return
            config_path = getattr(self.app, "active_config", rb.DEFAULT_CONFIG)
            errors = rb.validate_campaign(config_path)
            if errors:
                log.write_line("Campaign is invalid: " + " · ".join(errors))
                return
            command = [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"), "--config", str(config_path)]
            log.write_line(f"Starting: {' '.join(command)}")
            popen_kwargs: dict[str, Any] = {}
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            log_dir = ROOT / "benchmark_results" / "tui-runs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self._run_log_path = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            output = self._run_log_path.open("a", encoding="utf-8", buffering=1)
            try:
                self._proc = subprocess.Popen(
                    command,
                    cwd=str(ROOT),
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    **popen_kwargs,
                )
            finally:
                output.close()
            log.write_line(f"Detached log: {self._run_log_path.relative_to(ROOT)}")
            self._reader = Thread(target=self._read_child_output, daemon=True)
            self._reader.start()
        elif event.action == "pause":
            pause_file.touch()
            log.write_line(f"Pause requested ({pause_file.name} created).")
        elif event.action == "resume":
            pause_file.unlink(missing_ok=True)
            log.write_line(f"Resume requested ({pause_file.name} removed).")
        elif event.action == "stop":
            stop_file.touch()
            log.write_line(f"Stop requested ({stop_file.name} created); active runner will terminate cleanly.")
        elif event.action == "refresh":
            self.refresh_status()
        elif event.action == "web_ui":
            self._open_web_ui()
        elif event.action == "help":
            self.app.push_screen(HelpScreen())
        self.refresh_status()

    def _active_paths(self) -> tuple[Path, Path, Path, Path]:
        config = getattr(self.app, "active_config", rb.DEFAULT_CONFIG)
        cfg = rb._config(config) if config and config.is_file() else {}
        resolved: list[Path] = []
        for key, default in (
            ("pause_file", rb.DEFAULT_PAUSE_FILE),
            ("stop_file", rb.DEFAULT_STOP_FILE),
            ("lock_file", rb.DEFAULT_LOCK_FILE),
            ("detail_csv", rb.DEFAULT_DETAIL),
        ):
            path = Path(cfg.get(key, default))
            resolved.append(path if path.is_absolute() else ROOT / path)
        return resolved[0], resolved[1], resolved[2], resolved[3]

    def _open_web_ui(self) -> None:
        url = "http://127.0.0.1:8766/"
        try:
            urllib.request.urlopen(url + "api/status", timeout=0.5).close()
        except Exception:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen(
                [sys.executable, str(ROOT / "scripts" / "bench_web_server.py")],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        _open_file(url)

    def _read_child_output(self) -> None:
        process = self._proc
        path = self._run_log_path
        if not process or path is None:
            return
        position = 0
        while True:
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(position)
                    for line in handle.readlines():
                        self._log_queue.put(line.rstrip())
                    position = handle.tell()
            except OSError as exc:
                self._log_queue.put(f"[could not read run log: {exc}]")
                return
            if process.poll() is not None:
                return
            time.sleep(0.2)

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
        with Horizontal(classes="toolbar"):
            yield Select(options, id="config_select", value=options[0][1])
            yield ActionStrip(
                ("R", "Reload", "#55ccff"),
                ("S", "Save", "#28c85a"),
                ("V", "Validate", "#55ccff"),
                ("P", "Preview", "#ffcc33"),
                ("T", "Tune", "#28c85a"),
                ("B", "Revert", "#bd93f9"),
                ("A", "Add entry", "#55ccff"),
                ("X", "Remove entry", "#ff5555"),
                ("C", "Clone", "#bd93f9"),
                ("D", "Defaults", "#ff8c1e"),
                id="config_actions",
            )
        with Horizontal(id="campaign_builder"):
            with Vertical(classes="selector_panel"):
                yield Static("BENCHMARKS · multi-select", classes="panel_title")
                yield SelectionList(id="benchmark_selection")
            with Vertical(classes="selector_panel"):
                yield Static("MODELS · multi-select", classes="panel_title")
                yield SelectionList(id="model_selection")
            with Vertical(id="settings_panel"):
                yield Static("RUN SETTINGS", classes="panel_title")
                yield Label("Backend / provider")
                yield Select(
                    [(v, v) for v in ("ollama", "llama_cpp", "both", "siemens", "all")],
                    id="field_backend",
                )
                yield Label("Runs per task")
                yield Input(id="field_runs", type="integer")
                yield Label("Timeout seconds")
                yield Input(id="field_timeout_sec", type="integer")
                yield Label("Free VRAM target %")
                yield Input(id="field_vram_headroom_pct", type="number")
                yield Label("Ollama endpoint")
                yield Input(id="field_ollama_url")
                with Horizontal(classes="switch_row"):
                    yield Switch(id="field_resume")
                    yield Label("Resume completed samples")
        yield DataTable(id="plan_table")
        yield Static(id="config_status")

    def on_mount(self) -> None:
        plan = self.query_one("#plan_table", DataTable)
        plan.add_columns("#", "Backend", "Benchmark", "Models", "Parameters", "Status")
        plan.cursor_type = "row"
        tooltips = {
            "#field_backend": "Provider(s) used for each selected benchmark.",
            "#field_runs": "Independent repetitions per benchmark task. Default: 1.",
            "#field_timeout_sec": "Hard request timeout. Default: 900 seconds.",
            "#field_vram_headroom_pct": (
                "Target GPU memory left free after load. Default: 75%. "
                "Tune proposes settings but never rewrites them silently."
            ),
            "#field_ollama_url": "Ollama API base or /api/generate endpoint.",
            "#field_resume": "Skip already completed durable sample IDs.",
        }
        for selector, tooltip in tooltips.items():
            self.query_one(selector).tooltip = tooltip
        self._tune_proposal_data: dict[str, Any] = {}
        self._load_selected()

    def _current_path(self) -> Optional[Path]:
        value = self.query_one("#config_select", Select).value
        return Path(value) if value else None

    def _load_selected(self) -> None:
        path = self._current_path()
        status = self.query_one("#config_status", Static)
        if not path or not path.exists():
            return
        try:
            result = _safe_load(
                lambda: load_campaign_toml(path),
                "Failed to parse campaign TOML",
                default=None,
            )
            if result is None:
                _error_status(status, f"Cannot load {path.name} — TOML parse error; press R to reload")
                return
            header, table, matrix = result
            self._header, self._table, self._matrix = header, table, matrix
            self.app.active_config = path
            self.query_one("#field_backend", Select).value = str(table.get("backend", "ollama"))
            for key in ("runs", "timeout_sec"):
                self.query_one(f"#field_{key}", Input).value = str(table.get(key, ""))
            self.query_one("#field_vram_headroom_pct", Input).value = str(table.get("vram_headroom_pct", 75))
            self.query_one("#field_ollama_url", Input).value = str(table.get("ollama_url", ""))
            self.query_one("#field_resume", Switch).value = str(table.get("resume", "auto")) == "auto"
            self._set_benchmark_options(table, matrix)
            self._set_configured_model_options(table, matrix)
            self._refresh_model_options(table)
            self._preview()
            status.update(f"Loaded {path}")
        except Exception as exc:
            _error_status(status, f"Config load error: {exc}; press R to reload")

    def _set_benchmark_options(self, table: dict[str, Any], matrix: list[dict[str, Any]]) -> None:
        selected = set(rb._split(table.get("benchmarks", [])))
        if not selected:
            for entry in matrix:
                selected.update(rb._split(entry.get("benchmarks", [])))
        if not selected:
            runner_args = rb._split(table.get("runner_args", []))
            if "--benchmark-file" in runner_args:
                index = runner_args.index("--benchmark-file")
                if index + 1 < len(runner_args):
                    selected.add(Path(runner_args[index + 1]).stem)
        if not selected:
            selected.update(rb._split(table.get("suites", "migration")))
        files = benchmark_files()
        options = [(name, name, name in selected) for name, _path in files]
        for pattern in sorted(selected - {name for name, _path in files}):
            options.insert(0, (f"Pattern: {pattern}", pattern, True))
        self.query_one("#benchmark_selection", SelectionList).set_options(options)

    def _set_configured_model_options(
        self,
        table: dict[str, Any],
        matrix: list[dict[str, Any]],
    ) -> None:
        selected_ollama = set(rb._split(table.get("models", [])) + rb._split(table.get("ollama_models", [])))
        selected_llama = {name for name, _ in gguf_registry(table)}
        selected_siemens = set(rb._split(table.get("siemens_models", [])))
        for entry in matrix:
            backend = str(entry.get("backend", table.get("backend", "")))
            models = set(rb._split(entry.get("models", [])))
            if backend == "ollama":
                selected_ollama.update(models)
            elif backend == "llama_cpp":
                selected_llama.update(models)
            elif backend == "siemens":
                selected_siemens.update(models)
        if str(table.get("backend", "")) in ("siemens", "all") and not selected_siemens:
            selected_siemens.update(rb.DEFAULT_SIEMENS_MODELS)
        choices: list[tuple[str, str, bool]] = []
        choices.extend(
            (f"[Ollama] {name}", f"ollama|{name}", True)
            for name in sorted(selected_ollama)
        )
        ggufs = dict(gguf_registry(table))
        for name, path in discover_gguf_models():
            ggufs.setdefault(name, path)
        choices.extend((f"[llama.cpp] {name}", f"llama|{name}={path}", name in selected_llama)
                       for name, path in sorted(ggufs.items()))
        choices.extend(
            (f"[Siemens] {name}", f"siemens|{name}", name in selected_siemens)
            for name in sorted(selected_siemens | set(rb.DEFAULT_SIEMENS_MODELS))
        )
        self.query_one("#model_selection", SelectionList).set_options(choices)

    @work(thread=True, exclusive=True, group="config-model-discovery")
    def _refresh_model_options(self, table: dict[str, Any]) -> None:
        selected = set(rb._split(table.get("models", [])) + rb._split(table.get("ollama_models", [])))
        url = rb._ollama_base_url(
            str(table.get("ollama_url", "http://127.0.0.1:11434"))
        )
        try:
            installed = {
                str(item.get("name"))
                for item in fetch_ollama_tags(url)
                if item.get("name")
            }
        except Exception:
            installed = set()
        self.app.call_from_thread(self._merge_ollama_options, selected, installed)

    def _merge_ollama_options(self, selected: set[str], installed: set[str]) -> None:
        table = self.query_one("#model_selection", SelectionList)
        current = set(table.selected)
        static_values = [
            (str(option.prompt), option.value, option.value in current)
            for option in table.options
            if not str(option.value).startswith("ollama|")
        ]
        ollama_values = [
            (f"[Ollama] {name}", f"ollama|{name}", f"ollama|{name}" in current or name in selected)
            for name in sorted(selected | installed)
        ]
        table.set_options([*ollama_values, *static_values])

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "config_select":
            self._load_selected()

    def on_action_strip_selected(self, event: ActionStrip.Selected) -> None:
        if event.strip_id != "config_actions":
            return
        status = self.query_one("#config_status", Static)
        path = self._current_path()
        if not path:
            return
        if event.action == "reload":
            self._load_selected()
        elif event.action == "save":
            errors = self._save()
            status.update(
                "[red]" + " · ".join(errors) + "[/red]"
                if errors else f"Saved {path} at {datetime.now().strftime('%H:%M:%S')}"
            )
        elif event.action == "validate":
            errors = self._visible_errors()
            status.update("[red]" + " · ".join(errors) + "[/red]" if errors else "[green]Campaign is valid[/green]")
        elif event.action == "preview":
            self._preview()
        elif event.action == "tune":
            self._show_tuning_proposal()
        elif event.action == "add_entry":
            self._add_matrix_entry()
        elif event.action == "remove_entry":
            self._remove_matrix_entry()
        elif event.action == "clone":
            clone = CONFIG_DIR / f"campaign-{datetime.now().strftime('%Y%m%d-%H%M%S')}.toml"
            clone.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            self.query_one("#config_select", Select).set_options(
                [(p.name, str(p)) for p in list_campaign_configs()]
            )
            self.query_one("#config_select", Select).value = str(clone)
            status.update(f"Cloned to {clone.name}")
        elif event.action == "defaults":
            table = dict(self._table)
            table.update(DEFAULT_KNOBS)
            table["vram_headroom_pct"] = 75
            write_campaign_toml(path, self._header, table, self._matrix)
            self._table = table
            self._load_selected()
            status.update(f"Defaults restored in {path.name}; model and benchmark selections kept.")
        elif event.action == "revert":
            if revert_toml_from_backup(path):
                self._load_selected()
                status.update(f"Reverted {path.name} from .bak backup.")
            else:
                status.update(f"[dim]No .bak backup found for {path.name}[/dim]")

    def _show_tuning_proposal(self) -> None:
        raw_headroom = self.query_one("#field_vram_headroom_pct", Input).value
        try:
            headroom_pct = int(float(raw_headroom))
        except ValueError:
            self.query_one("#config_status", Static).update("[red]VRAM target must be numeric[/red]")
            return
        selected = [str(value) for value in self.query_one("#model_selection", SelectionList).selected]
        self._build_tuning_proposal(headroom_pct, selected)

    @work(thread=True, exclusive=True, group="tuning-proposal")
    def _build_tuning_proposal(self, headroom_pct: int, selected: list[str]) -> None:
        """Propose VRAM-tuned parameters and present a Confirm → Apply modal.

        Reads ``nvidia-smi`` for available VRAM, then calls ``_derive_vram_params``
        per selected model to build a diff of current-vs-proposed runner_args.
        The operator must explicitly confirm before any TOML is modified.
        """

        total_mb: float | None = None
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode == 0:
                total_mb = float(result.stdout.splitlines()[0].strip())
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass

        usable_mb = total_mb * (1 - headroom_pct / 100) if total_mb else None
        header_lines: list[str] = [
            f"Target free VRAM: {headroom_pct:.1f}%",
        ]
        if total_mb is not None:
            header_lines.append(f"GPU VRAM: {total_mb / 1024:.1f} GB  (usable after headroom: {usable_mb / 1024:.1f} GB)")
        else:
            header_lines.append("GPU VRAM: unavailable (nvidia-smi not reachable)")
            header_lines.append("(Conservative defaults will be proposed; verify on real hardware.)")
        header_lines.append("")

        # Build per-model diff rows
        diff_rows: list[tuple[str, str, dict, dict]] = []
        for model_value in selected:
            pipe_idx = model_value.find("|")
            if pipe_idx < 0:
                continue
            source = model_value[:pipe_idx]
            spec = model_value[pipe_idx + 1:]
            name, sep, path = spec.partition("=")

            derived = rb._derive_vram_params(
                model_name=name,
                backend="llama_cpp" if source == "llama" else source,
                headroom_pct=headroom_pct,
                available_vram_mb=usable_mb,
                model_size_bytes=Path(path).stat().st_size if (source == "llama" and Path(path).is_file()) else None,
            )

            # Current runner_args (from TOML)
            current_args = dict(self._table.get("runner_args", {}))
            if isinstance(current_args, list):
                current_args = {}
                ri = iter(rb._split(current_args if isinstance(current_args, str) else current_args))
                for k in ri:
                    try:
                        current_args[k.lstrip("-")] = next(ri)
                    except StopIteration:
                        break

            diff_rows.append((name, source, current_args, derived or {}))

        proposal_lines = list(header_lines)
        for name, source, current_args, proposed in diff_rows:
            backend_label = "llama.cpp" if source == "llama" else source
            proposal_lines.append(f"{name} ({backend_label}):")
            backend = "llama_cpp" if source == "llama" else source
            param_keys = set(proposed.keys()) | set(current_args.keys())
            if not param_keys:
                proposal_lines.append("  (no parameters to propose)")
            for key in sorted(param_keys):
                current_val = current_args.get(key, "<not set>")
                proposed_val = proposed.get(key, "<unchanged>")
                if str(current_val) == str(proposed_val):
                    proposal_lines.append(f"  {key}: {current_val}")
                else:
                    proposal_lines.append(f"  {key}: {current_val} → {proposed_val}")
            proposal_lines.append("")

        proposal_lines.extend([
            "Review the proposed values above. Apply writes the TOML with .bak backup.",
            "Revert (B key) restores the .bak if tuning is incorrect.",
        ])

        # Store proposed data for later apply
        self._tune_proposal_data = {
            "headroom_pct": headroom_pct,
            "diff_rows": diff_rows,
        }

        self.app.call_from_thread(
            self.app.push_screen,
            TuneConfirmScreen(proposal_lines),
            self._on_tune_confirmed,
        )

    def _on_tune_confirmed(self, confirmed: bool) -> None:
        """Callback when the TuneConfirmScreen closes."""
        if not confirmed:
            self.query_one("#config_status", Static).update("[dim]Tune cancelled — no configuration changed.[/dim]")
            return
        path = self._current_path()
        if not path:
            return

        # Apply: update table with proposed values using atomic write
        table = dict(self._table)
        diff_rows = getattr(self, "_tune_proposal_data", {}).get("diff_rows", [])

        # Build new runner_args list from proposed parameters
        runner_args: list[str] = []
        for name, source, _current, proposed in diff_rows:
            backend = "llama_cpp" if source == "llama" else source
            for key, value in proposed.items():
                runner_args.extend([f"--{key}" if not key.startswith("--") else key, str(value)])

        # Deduplicate: keep last seen key
        seen: dict[str, int] = {}
        compact: list[str] = []
        for i, item in enumerate(runner_args):
            if item.startswith("--"):
                seen[item.lstrip("-")] = i
                compact.append(item)
            else:
                compact.append(item)
        # Simple approach: flatten the proposed params into runner_args
        table["runner_args"] = runner_args

        # Also store headroom_pct
        headroom = getattr(self, "_tune_proposal_data", {}).get("headroom_pct", 5)
        table["vram_headroom_pct"] = headroom

        write_campaign_toml_atomic(path, self._header, table, self._matrix)
        self._table = table
        self._load_selected()
        self.query_one("#config_status", Static).update(
            f"[green]Applied VRAM tuning (headroom {headroom}%) — "
            f"{path.name}.bak saved for revert.[/green]"
        )

    def _add_matrix_entry(self) -> None:
        benchmarks = list(self.query_one("#benchmark_selection", SelectionList).selected)
        selected = [str(value) for value in self.query_one("#model_selection", SelectionList).selected]
        backend_value = self.query_one("#field_backend", Select).value
        backend = str(backend_value) if backend_value is not Select.BLANK else ""
        if not benchmarks or not selected or not backend:
            self.query_one("#config_status", Static).update(
                "[red]Select backend, benchmark, and model before adding an entry[/red]"
            )
            return
        backends = (
            ["ollama", "llama_cpp"] if backend == "both"
            else ["ollama", "llama_cpp", "siemens"] if backend == "all"
            else [backend]
        )
        for item_backend in backends:
            prefix = "llama" if item_backend == "llama_cpp" else item_backend
            models = [
                value.split("|", 1)[1].partition("=")[0]
                for value in selected
                if value.startswith(prefix + "|")
            ]
            if models:
                self._matrix.append({
                    "backend": item_backend,
                    "models": models,
                    "benchmarks": benchmarks,
                })
        self._preview()
        self.query_one("#config_status", Static).update(
            f"Added matrix entry · {len(self._matrix)} definitions (Save to persist)"
        )

    def _remove_matrix_entry(self) -> None:
        if not self._matrix:
            self.query_one("#config_status", Static).update("This campaign uses one flat run plan.")
            return
        cursor = self.query_one("#plan_table", DataTable).cursor_row
        if cursor < 0 or cursor >= len(getattr(self, "_plan_entry_indices", [])):
            self.query_one("#config_status", Static).update("Select a plan row first.")
            return
        entry_index = self._plan_entry_indices[cursor]
        if entry_index is None:
            self.query_one("#config_status", Static).update("The selected row is not a matrix entry.")
            return
        del self._matrix[entry_index]
        self._preview()
        self.query_one("#config_status", Static).update(
            f"Removed matrix entry {entry_index + 1} (Save to persist)"
        )

    def _visible_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.query_one("#benchmark_selection", SelectionList).selected:
            errors.append("select at least one benchmark")
        if not self.query_one("#model_selection", SelectionList).selected:
            errors.append("select at least one model")
        backend_value = self.query_one("#field_backend", Select).value
        backend = str(backend_value) if backend_value is not Select.BLANK else ""
        selected = [str(value) for value in self.query_one("#model_selection", SelectionList).selected]
        required = (
            ["ollama", "llama"] if backend == "both"
            else ["ollama", "llama", "siemens"] if backend == "all"
            else ["llama" if backend == "llama_cpp" else backend]
        )
        for prefix in required:
            if prefix and not any(value.startswith(prefix + "|") for value in selected):
                errors.append(f"select a model for {prefix.replace('llama', 'llama.cpp')}")
        for field, label, minimum in (
            ("#field_runs", "runs", 1),
            ("#field_timeout_sec", "timeout", 1),
        ):
            raw = self.query_one(field, Input).value.strip()
            if not raw.isdigit() or int(raw) < minimum:
                errors.append(f"{label} must be ≥ {minimum}")
        try:
            headroom = float(self.query_one("#field_vram_headroom_pct", Input).value)
            if not 1 <= headroom <= 80:
                errors.append("VRAM target must be 1–80%")
        except ValueError:
            errors.append("VRAM target must be numeric")
        return errors

    def _save(self) -> list[str]:
        errors = self._visible_errors()
        path = self._current_path()
        if errors or path is None:
            return errors
        table = dict(self._table)
        backend = self.query_one("#field_backend", Select).value
        if backend is not Select.BLANK:
            table["backend"] = str(backend)
        table["runs"] = int(self.query_one("#field_runs", Input).value)
        table["timeout_sec"] = int(self.query_one("#field_timeout_sec", Input).value)
        table["vram_headroom_pct"] = float(self.query_one("#field_vram_headroom_pct", Input).value)
        table["resume"] = "auto" if self.query_one("#field_resume", Switch).value else "off"
        table["ollama_url"] = self.query_one("#field_ollama_url", Input).value.strip()
        selected_models = self.query_one("#model_selection", SelectionList).selected
        table["models"] = [
            str(value).split("|", 1)[1]
            for value in selected_models
            if str(value).startswith("ollama|")
        ]
        table["llama_models"] = [
            str(value).split("|", 1)[1]
            for value in selected_models
            if str(value).startswith("llama|")
        ]
        table["siemens_models"] = [
            str(value).split("|", 1)[1]
            for value in selected_models
            if str(value).startswith("siemens|")
        ]
        table["benchmarks"] = list(self.query_one("#benchmark_selection", SelectionList).selected)
        runner_args = rb._split(table.get("runner_args", []))
        if "--benchmark-file" in runner_args:
            index = runner_args.index("--benchmark-file")
            del runner_args[index:index + 2]
        table["runner_args"] = runner_args
        write_campaign_toml_atomic(path, self._header, table, self._matrix)
        self._table = table
        self.app.active_config = path
        self._preview()
        return []

    def _preview(self) -> None:
        plan = self.query_one("#plan_table", DataTable)
        plan.clear()
        status = self.query_one("#config_status", Static)
        try:
            indexed_groups: list[tuple[dict[str, Any], int | None]] = []
            if self._matrix:
                args = _safe_load(lambda: rb._parser().parse_args([]), "Failed to build preview parser", default=[])
                if isinstance(args, list):
                    _retry_status(status, "Preview: could not build argument parser")
                    return
                for entry_index, entry in enumerate(self._matrix):
                    groups = _safe_load(
                        lambda: rb.expand_matrix(
                            [entry], args, self._table, resolve_wildcards=False,
                        ),
                        f"Error expanding matrix entry {entry_index}",
                        default=[],
                    )
                    indexed_groups.extend((group, entry_index) for group in (groups or []))
            else:
                backend_value = self.query_one("#field_backend", Select).value
                backend = str(backend_value) if backend_value is not Select.BLANK else "ollama"
                backends = (
                    ["ollama", "llama_cpp"] if backend == "both"
                    else ["ollama", "llama_cpp", "siemens"] if backend == "all"
                    else [backend]
                )
                selected = [str(value) for value in self.query_one("#model_selection", SelectionList).selected]
                by_backend = {
                    "ollama": [value.split("|", 1)[1] for value in selected if value.startswith("ollama|")],
                    "llama_cpp": [
                        value.split("|", 1)[1].partition("=")[0]
                        for value in selected if value.startswith("llama|")
                    ],
                    "siemens": [value.split("|", 1)[1] for value in selected if value.startswith("siemens|")],
                }
                files = dict(_safe_load(benchmark_files, "Failed to list benchmark files", default=[]))
                benchmarks = list(self.query_one("#benchmark_selection", SelectionList).selected)
                indexed_groups = [
                    ({
                        "backend": item_backend,
                        "benchmark_stem": benchmark,
                        "benchmark_file": files.get(benchmark),
                        "models": by_backend.get(item_backend, []),
                    }, None)
                    for item_backend in backends
                    for benchmark in benchmarks
                ]
            self._plan_entry_indices = []
            for index, (group, entry_index) in enumerate(indexed_groups, 1):
                models = ", ".join(group.get("models", [])) or "(none)"
                params = " ".join(str(value) for value in group.get("runner_args", []))
                plan.add_row(
                    str(index),
                    str(group.get("backend", "")),
                    str(group.get("benchmark_stem", "")),
                    models,
                    params,
                    "ready" if group.get("models") and group.get("benchmark_file") else "incomplete",
                )
                self._plan_entry_indices.append(entry_index)
            status.update(
                f"{len(indexed_groups)} execution groups · "
                + (
                    f"{len(self._matrix)} matrix definitions; selectors add new entries"
                    if self._matrix else "selectors are the exact saved run plan"
                )
            )
        except Exception as exc:
            _error_status(status, f"Preview error: {exc}; press V to validate")


# --------------------------------------------------------------------------
# Models & backends
# --------------------------------------------------------------------------

class ModelsPane(Vertical):
    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Select([], id="pull_model", prompt="Select Ollama model to pull")
            yield ActionStrip(
                ("R", "Refresh", "#55ccff"),
                ("P", "Pull", "#28c85a"),
                ("G", "Register GGUF", "#bd93f9"),
                ("B", "Backup", "#ffcc33"),
                ("E", "Rebuild", "#ff5555"),
                ("D", "Doctor", "#ff8c1e"),
                id="model_actions",
            )
        yield DataTable(id="models_table")
        yield Static(id="models_status")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Source", "Name", "Size", "Status", "Location / notes")
        table.cursor_type = "row"
        self.refresh_models()

    def _config_table(self) -> dict[str, Any]:
        configs = list_campaign_configs()
        if not configs:
            return {}
        active = getattr(self.app, "active_config", None)
        return rb._config(active if active and active.is_file() else configs[0])

    @work(thread=True, exclusive=True, group="model-inventory")
    def refresh_models(self) -> None:
        """Refresh the model inventory with error boundaries on every data source."""
        rows: list[tuple[str, str, str, str, str]] = []
        candidates = set(OLLAMA_PULL_CATALOG)
        installed: set[str] = set()
        ollama_note = "Models unavailable — no error"
        gguf_note = ""
        siemens_count = 0

        try:
            cfg = _safe_load(self._config_table, "Cannot load campaign config", default={})
            if cfg is None:
                cfg = {}
        except Exception:
            cfg = {}

        # --- Ollama inventory (network call) ---
        url = rb._ollama_base_url(str(cfg.get("ollama_url", "http://127.0.0.1:11434")))
        try:
            tags = fetch_ollama_tags(url)
            for entry in sorted(tags, key=lambda e: str(e.get("name", ""))):
                size_gb = (entry.get("size") or 0) / (1024 ** 3)
                rows.append(("Ollama", str(entry.get("name", "")), f"{size_gb:.1f} GB", "installed", ""))
            installed = {str(entry.get("name", "")) for entry in tags}
            configured = rb._split(cfg.get("models", [])) + rb._split(cfg.get("ollama_models", []))
            candidates.update(name for name in configured if "*" not in name and "?" not in name)
            ollama_note = f"Ollama ready · {len(tags)} installed"
        except asyncio.TimeoutError:
            ollama_note = "Ollama connection timed out — press R to retry"
        except Exception as exc:
            ollama_note = f"Ollama unavailable: {exc}"

        # --- GGUF discovery (filesystem) ---
        try:
            registered = dict(_safe_load(lambda: gguf_registry(cfg), "GGUF registry error", default={}))
            discovered = _safe_load(discover_gguf_models, "GGUF discovery error", default=[]) or []
            for name, path in discovered:
                registered.setdefault(name, path)
            for name, path in sorted(registered.items()):
                p = Path(path)
                size = f"{p.stat().st_size / (1024**3):.1f} GB" if p.is_file() else "missing"
                rows.append(("llama.cpp", name, size, "installed" if p.is_file() else "missing", path))
            gguf_note = f" · {len(registered)} GGUF"
        except Exception as exc:
            gguf_note = f" · GGUF scan error: {exc}"

        # --- Siemens cloud models ---
        try:
            siemens_models = rb._split(cfg.get("siemens_models", [])) or rb.DEFAULT_SIEMENS_MODELS
            for name in siemens_models:
                rows.append(("Siemens", name, "cloud", "configured", "api.siemens.com"))
                siemens_count += 1
        except Exception:
            pass

        note = f"{ollama_note}{gguf_note}"

        self.app.call_from_thread(
            self._apply_inventory,
            rows,
            sorted(candidates - installed),
            note,
        )

    def _apply_inventory(
        self,
        rows: list[tuple[str, str, str, str, str]],
        candidates: list[str],
        note: str,
    ) -> None:
        table = self.query_one("#models_table", DataTable)
        table.clear()
        for row in rows:
            table.add_row(*row)
        selector = self.query_one("#pull_model", Select)
        selector.set_options([(name, name) for name in candidates])
        if not candidates:
            selector.clear()
        self.query_one("#models_status", Static).update(
            f"{note} · {len(rows)} models/backends visible · {len(candidates)} pull candidates"
        )

    def on_action_strip_selected(self, event: ActionStrip.Selected) -> None:
        if event.strip_id != "model_actions":
            return
        status = self.query_one("#models_status", Static)
        if event.action == "refresh":
            self.refresh_models()
        elif event.action == "pull":
            selected = self.query_one("#pull_model", Select).value
            if selected not in (Select.BLANK, None, ""):
                self._do_pull(str(selected))
            else:
                status.update("Select an Ollama model first.")
        elif event.action == "register_gguf":
            self.app.push_screen(PromptModal("NAME=path\\to\\model.gguf"), self._do_register)
        elif event.action == "rebuild":
            self._do_rebuild()
        elif event.action == "backup":
            self._do_backup()
        elif event.action == "doctor":
            config = getattr(self.app, "active_config", rb.DEFAULT_CONFIG)
            self._run_doctor(config)

    @work(thread=True, exclusive=True, group="model-doctor")
    def _run_doctor(self, config: Path) -> None:
        command = [sys.executable, str(ROOT / "scripts" / "run_benchmark.py"), "--config", str(config), "--doctor"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        message = (result.stdout or result.stderr).strip().replace("\n", " · ")
        self.app.call_from_thread(
            self._show_models_status,
            message or f"Doctor exited {result.returncode}",
        )

    def _show_models_status(self, message: str) -> None:
        self.query_one("#models_status", Static).update(message)

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
        config = getattr(self.app, "active_config", None)
        configs = list_campaign_configs()
        if not config and configs:
            config = configs[0]
        if not config:
            status.update("No campaign TOML found to register into.")
            return
        header, table, matrix = load_campaign_toml(config)
        existing = rb._split(table.get("llama_models", []))
        existing.append(f"{name.strip()}={path.strip()}")
        table["llama_models"] = list(dict.fromkeys(existing))
        write_campaign_toml(config, header, table, matrix)
        status.update(f"Registered {name.strip()} into {config.name}.")
        self.refresh_models()

    def _do_rebuild(self) -> None:
        """Rebuild a llama.cpp source fork selected in the models table.

        Inspects the currently cursor-row for a llama.cpp / GGUF entry.
        If the path resolves to a source tree (detected via
        ``fork_build.can_rebuild``), triggers ``fork_build.build`` on a
        background thread. On any failure, writes ``install.md`` as a
        durable fallback and reports the path in the status bar.
        """

        def _execute() -> None:
            result = _fork_build.build(source_path, build_type="Release")
            self.app.call_from_thread(self._show_models_status, _rebuild_status(result))

        table = self.query_one("#models_table", DataTable)
        cursor = table.cursor_row
        if cursor is None:
            self._show_models_status("Select a row first.")
            return
        try:
            row = table.get_row_at(cursor)
        except (StopIteration, TypeError):
            self._show_models_status("No row selected.")
            return
        # row is (Source, Name, Size, Status, Location)
        source_label = str(row[0]) if len(row) > 0 else ""
        location = str(row[4]) if len(row) > 4 else ""
        if source_label != "llama.cpp":
            self._show_models_status(f"Rebuild only available for llama.cpp backends (row: {source_label}).")
            return
        if not location:
            self._show_models_status("Selected row has no source path — cannot rebuild.")
            return
        source_path = Path(location)
        # can_rebuild inspects the path's ancestors for a source tree
        if not _fork_build.can_rebuild(source_path):
            # Fall back: try the path itself
            if _fork_build.is_source_tree(source_path):
                source_path = source_path
            else:
                self._show_models_status("Selected row is not inside a llama.cpp source tree — rebuild not possible.")
                return
        status = self.query_one("#models_status", Static)
        status.update(f"Rebuilding {source_path} … (this may take a few minutes)")
        Thread(target=_execute, daemon=True).start()

    @staticmethod
    def _rebuild_status(result: "fork_build.ForkBuildResult") -> str:
        if result.success:
            return f"Rebuild successful — binary at {result.binary_path}"
        md = f" (see {result.install_md_path})" if result.install_md_path else ""
        # Show first 200 characters of log
        log_snippet = (result.log[:200] + "...") if result.log else ""
        return f"Rebuild failed{md}: {log_snippet}"

    def _do_backup(self) -> None:
        status = self.query_one("#models_status", Static)
        cfg = self._config_table()
        url = rb._ollama_base_url(str(cfg.get("ollama_url", "http://127.0.0.1:11434")))
        snapshot: dict[str, Any] = {
            "schema_version": "llm-inventory-backup-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ollama_executable": shutil.which("ollama") or "",
            "llama_server": str(cfg.get("llama_server", "")),
        }
        try:
            snapshot["ollama_models"] = fetch_ollama_tags(url)
        except Exception as exc:
            snapshot["ollama_models"] = []
            snapshot["ollama_error"] = str(exc)
        snapshot["llama_cpp_models"] = [{"name": n, "path": p} for n, p in gguf_registry(cfg)]
        active = getattr(self.app, "active_config", None)
        if active and active.is_file():
            snapshot["campaign_path"] = str(active)
            snapshot["campaign_toml"] = active.read_text(encoding="utf-8")
        out_dir = BACKUP_DIR / datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "inventory.json"
        out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        ollama_models = [
            str(item.get("name"))
            for item in snapshot.get("ollama_models", [])
            if item.get("name")
        ]
        ggufs = snapshot["llama_cpp_models"]
        install_lines = [
            "# LLM backend recovery",
            "",
            f"Generated: {snapshot['generated_at']}",
            "",
            "1. Install Ollama from https://ollama.com/download/windows and start it.",
            "2. Restore Ollama models:",
            "",
            "```powershell",
            *[f"ollama pull {name}" for name in ollama_models],
            "```",
            "",
            "3. Restore or rebuild llama.cpp and verify the configured server:",
            "",
            f"`{snapshot['llama_server'] or '(no llama-server configured)'}`",
            "",
            "4. Restore GGUF files at these paths:",
            "",
            *[f"- `{item['name']}` → `{item['path']}`" for item in ggufs],
            "",
            "5. Copy `campaign.toml` back into the workbench `config` directory and run `--doctor`.",
        ]
        (out_dir / "install.md").write_text("\n".join(install_lines) + "\n", encoding="utf-8")
        if active and active.is_file():
            (out_dir / "campaign.toml").write_text(active.read_text(encoding="utf-8"), encoding="utf-8")
        status.update(f"Recovery bundle written to {out_dir.relative_to(ROOT)}")


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
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="/ filter all columns", id="filter")
            yield ActionStrip(
                ("R", "Refresh", "#55ccff"),
                ("G", "Group", "#bd93f9"),
                ("C", "Clear group", "#ffcc33"),
                ("S", "Select columns", "#c678dd"),
                ("W", "Web report", "#28c85a"),
                ("E", "Excel", "#ff8c1e"),
                id="results_actions",
            )
        yield DataTable(id="results_table")
        yield Static(id="results_status")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        for key, title in COLUMNS:
            table.add_column(title, key=key)
        table.cursor_type = "row"
        self._rows: list[dict[str, Any]] = []
        self._sort_key = ""
        self._sort_reverse = False
        self._group_keys: list[str] = []
        self.refresh_results()

    def report_path(self) -> Path:
        return ROOT / "docs" / "project" / "benchmark_report.html"

    def refresh_results(self) -> None:
        """Reload report rows with error boundaries.

        Handles missing report file, empty report, and malformed embedded
        JSON without crashing the TUI.
        """
        status = self.query_one("#results_status", Static)
        try:
            report = self.report_path()
            if not report.exists():
                self._rows = []
                self._populate_table([])
                status.update("[dim]No results yet — run a benchmark campaign first[/dim]")
                return
            rows = _safe_load(
                lambda: load_report_rows(report),
                "Failed to parse report data",
                default=[],
            )
            self._rows = rows if rows is not None else []
            self._populate_table(self._rows)
            if not self._rows:
                status.update("[dim]No results yet — run a benchmark campaign first[/dim]")
            else:
                status.update(f"{len(self._rows)} rows from {report.relative_to(ROOT)}")
        except Exception as exc:
            _error_status(status, f"Results error: {exc} — press R to retry")

    def _populate_table(self, rows: list[dict[str, Any]]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        previous_group: tuple[str, ...] | None = None
        for row in rows:
            group = tuple(str(row.get(key, "")) for key in self._group_keys)
            if self._group_keys and group != previous_group:
                label = " · ".join(
                    f"{dict(COLUMNS).get(key, key)}={value or 'N/A'}"
                    for key, value in zip(self._group_keys, group)
                )
                table.add_row(f"[bold #55ccff]▼ {label}[/bold #55ccff]", *[""] * (len(COLUMNS) - 1))
                previous_group = group
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
        self._sort_reverse = not self._sort_reverse if self._sort_key == key else False
        self._sort_key = key
        def value(row: dict[str, Any]) -> tuple[int, Any]:
            raw = row.get(key)
            if isinstance(raw, (int, float)):
                return (0, float(raw))
            try:
                return (0, float(raw))
            except (TypeError, ValueError):
                return (1, str(raw or "").casefold())
        self._rows.sort(key=value, reverse=self._sort_reverse)
        self._populate_table(self._rows)
        self.query_one("#results_status", Static).update(
            f"Sorted {dict(COLUMNS).get(key, key)} {'descending' if self._sort_reverse else 'ascending'}"
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        needle = event.value.strip().lower()
        if not needle:
            self._populate_table(self._rows)
            return
        filtered = [r for r in self._rows if needle in json.dumps(r, ensure_ascii=False).lower()]
        self._populate_table(filtered)

    def on_action_strip_selected(self, event: ActionStrip.Selected) -> None:
        if event.strip_id != "results_actions":
            return
        if event.action == "refresh":
            self.refresh_results()
        elif event.action == "group":
            self.app.push_screen(
                PromptModal(
                    "Group fields in order (comma-separated)",
                    "provider,benchmark,backend",
                ),
                self._apply_grouping,
            )
        elif event.action == "clear_group":
            self._group_keys = []
            self._populate_table(self._rows)
            self.query_one("#results_status", Static).update("Grouping cleared")
        elif event.action == "select_columns":
            table = self.query_one(DataTable)
            visible = {
                (key, title)
                for key, title in COLUMNS
                if not table.columns.get(key).hidden
            }
            self.app.push_screen(
                ColumnSelectPanel(visible),
                self._apply_columns,
            )
        elif event.action == "web_report":
            _open_file(str(self.report_path()))  # noqa: S606 -- local file, user-triggered
        elif event.action == "excel":
            out = ROOT / "benchmark_results" / "benchmark_report_export.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=[key for key, _ in COLUMNS])
                writer.writeheader()
                for row in self._rows:
                    writer.writerow({key: row.get(key, "") for key, _ in COLUMNS})
            _open_file(str(out))  # noqa: S606

    def _apply_grouping(self, value: Optional[str]) -> None:
        if value is None:
            return
        valid = {key for key, _title in COLUMNS}
        requested = [item.strip() for item in value.split(",") if item.strip()]
        invalid = [item for item in requested if item not in valid]
        if invalid:
            self.query_one("#results_status", Static).update(
                f"Unknown group fields: {', '.join(invalid)}"
            )
            return
        self._group_keys = list(dict.fromkeys(requested))
        self._rows.sort(
            key=lambda row: tuple(str(row.get(key, "")).casefold() for key in self._group_keys)
        )
        self._populate_table(self._rows)
        self.query_one("#results_status", Static).update(
            "Grouped by " + " → ".join(self._group_keys)
        )

    def _apply_columns(self, selected: set[tuple[str, str]] | None) -> None:
        if selected is None or not selected:
            return
        table = self.query_one(DataTable)
        for key, title in COLUMNS:
            try:
                col = table.columns.get(key)
                col.hidden = (key, title) not in selected
            except KeyError:
                pass
        self._populate_table(self._rows)
        count = len(selected)
        self.query_one("#results_status", Static).update(f"{count}/{len(COLUMNS)} columns visible")


# --------------------------------------------------------------------------
# Leaderboard -- top 5 models overall + a simple ASCII bar chart (no extra
# charting dependency; see docs/project/tui-web-architecture.md for the
# `plotext` upgrade path if the project wants richer charts later).
# --------------------------------------------------------------------------

class ColumnSelectPanel(ModalScreen):
    """Modal to select visible columns in the Results table."""

    CSS = """
    ColumnSelectPanel {
        Alignment: center-middle;
    }
    """

    def __init__(self, default_selected: set[tuple[str, str]] | None = None) -> None:
        super().__init__()
        self._default = default_selected if default_selected is not None else set(COLUMNS)

    def compose(self) -> ComposeResult:
        yield Footer()
        with Center():
            with ScrollableContainer(id="panel"):
                yield Label("Select visible columns")
                yield SelectionList[str](id="columns", allow_multiple=True)
                with Horizontal():
                    yield Button("Select All", id="select_all", variant="primary")
                    yield Button("Deselect All", id="deselect_all", variant="default")
                    yield Button("Cancel", id="cancel")
                    yield Button("Apply", id="apply", variant="primary")

    def on_mount(self) -> None:
        sel = self.query_one("#columns", SelectionList[str])
        selected_keys = {key for key, _ in self._default}
        for key, title in COLUMNS:
            sel.add_option(key, f"{title} ({key})", key in selected_keys)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        sel = self.query_one("#columns", SelectionList[str])
        if event.button.id == "select_all":
            for option_id in sel.options:
                sel.set_option_enabled(option_id, True)
                sel.set_option_selected(option_id, True)
        elif event.button.id == "deselect_all":
            for option_id in sel.options:
                sel.set_option_selected(option_id, False)
        elif event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "apply":
            selected = set(COLUMNS) - {
                (key, title) for key, (_, title) in sel.options if key not in sel.selected
            }
            self.dismiss(selected)

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss(None)


class LeaderboardPane(Vertical):
    def compose(self) -> ComposeResult:
        yield ActionStrip(
            ("R", "Refresh", "#55ccff"),
            ("W", "Web report", "#28c85a"),
            id="leaderboard_actions",
        )
        yield DataTable(id="board_table")
        with Horizontal(id="leaderboard_charts"):
            yield Static(id="chart")
            yield Static(id="scatter")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("#", "Model", "Backend", "Heuristic (avg)", "Elapsed s (avg)")
        self.refresh_board()

    def refresh_board(self) -> None:
        """Refresh leaderboard with error boundaries.

        Handles missing report file, empty data, and malformed rows
        without crashing.  With no data the table and charts show
        "No results yet" instead of error text.
        """
        table = self.query_one(DataTable)
        table.clear()
        chart = self.query_one("#chart", Static)
        scatter = self.query_one("#scatter", Static)
        try:
            rows = _safe_load(
                lambda: load_report_rows(ROOT / "docs" / "project" / "benchmark_report.html"),
                "Failed to load report data for leaderboard",
                default=[],
            )
            if not rows:
                chart.update("[dim]No results yet — run a benchmark campaign first[/dim]")
                scatter.update("[dim]No data yet[/dim]")
                return
            by_model: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for row in rows:
                try:
                    by_model.setdefault(
                        (row.get("model", ""), row.get("backend", "")), [],
                    ).append(row)
                except (KeyError, TypeError):
                    continue  # skip malformed rows

            def avg(items: list[dict[str, Any]], key: str) -> float:
                values = [float(i[key]) for i in items if isinstance(i.get(key), (int, float))]
                return sum(values) / len(values) if values else 0.0

            ranked = sorted(
                ((model, backend, avg(items, "heuristic_score"), avg(items, "wall_seconds"))
                 for (model, backend), items in by_model.items()),
                key=lambda t: t[2], reverse=True,
            )[:5]
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
            chart.update("\n".join(chart_lines) or "[dim]No data yet[/dim]")
            scatter_lines = ["QUALITY ↕   speed →", "100 ┤"]
            for model, _backend, score, wall in ranked:
                speed = 1.0 / max(wall, 0.001)
                scatter_lines.append(
                    f"{score:>3.0f} ┤ {'·' * min(28, max(1, int(speed * 20)))}● {model[:18]}"
                )
            scatter_lines.append("  0 └────────────────────────────")
            scatter.update("\n".join(scatter_lines))
        except Exception as exc:
            _error_status(chart, f"Leaderboard error: {exc}")
            scatter.update("[dim]Error loading leaderboard data[/dim]")

    def on_action_strip_selected(self, event: ActionStrip.Selected) -> None:
        if event.strip_id != "leaderboard_actions":
            return
        if event.action == "refresh":
            self.refresh_board()
        elif event.action == "web_report":
            _open_file(str(ROOT / "docs" / "project" / "benchmark_report.html"))


class AgentMonitorPane(Vertical):
    """Embedded view backed by the canonical Agent Monitor collector."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Static("SYSTEM · waiting for collector", id="monitor_system")
            yield ActionStrip(
                ("R", "Restart collector", "#55ccff"),
                ("O", "Open full monitor", "#28c85a"),
                ("H", "Help", "#ffcc33"),
                id="monitor_actions",
            )
        with Container(classes="monitor_pane", id="agents_pane"):
            yield DataTable(id="agents_table", cell_padding=0)
        with Container(classes="monitor_pane", id="engines_pane"):
            yield DataTable(id="engines_table", cell_padding=0)
        yield Static(id="monitor_status")

    def on_mount(self) -> None:
        agents = self.query_one("#agents_table", DataTable)
        agents.add_columns("Agent", "Model / provider", "Action", "PID", "CPU", "RAM", "Tokens", "Ctx", "t/s")
        agents.cursor_type = "row"
        agents_pane = self.query_one("#agents_pane", Container)
        agents_pane.border_title = "Agents"
        engines = self.query_one("#engines_table", DataTable)
        engines.add_columns("Engine", "Model", "Device", "Size", "RAM", "VRAM", "VRAM free", "Tokens", "Ctx", "t/s")
        engines.cursor_type = "row"
        engines_pane = self.query_one("#engines_pane", Container)
        engines_pane.border_title = "Local LLMs"
        self._reader: Any = None
        self._snapshot: dict[str, Any] = {}
        self._start_collector()
        self.set_interval(0.25, self._poll_collector)

    def _load_monitor_module(self) -> Any:
        if not MONITOR_SCRIPT.is_file():
            raise RuntimeError(f"Agent Monitor not found: {MONITOR_SCRIPT}")
        spec = importlib.util.spec_from_file_location("workbench_agent_monitor", MONITOR_SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load Agent Monitor: {MONITOR_SCRIPT}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _start_collector(self) -> None:
        """Start the external agent-monitor collector with error boundaries."""
        status = self.query_one("#monitor_status", Static)
        if not MONITOR_COLLECTOR.is_file():
            self._reader = None
            status.update(
                f"[red]Agent monitor not available — collector missing: "
                f"{MONITOR_COLLECTOR}[/red]"
            )
            return
        try:
            module = self._load_monitor_module()
            module.configure_monitor_logging(MONITOR_CONFIG)
            module.ensure_toml_config(MONITOR_CONFIG)
            self._reader = module.BackendReader(MONITOR_COLLECTOR, 2, MONITOR_CONFIG)
            self._reader.start()
            status.update(
                f"Embedded collector running · canonical source {MONITOR_SCRIPT.parent}"
            )
        except RuntimeError as exc:
            self._reader = None
            status.update(
                f"[red]Agent monitor not available — {exc}[/red]"
            )
        except Exception as exc:
            self._reader = None
            status.update(f"[red]Collector startup failed: {exc}[/red]")

    def _poll_collector(self) -> None:
        """Poll the external collector with error boundaries.

        If the collector process crashes or ``latest()`` raises, the
        error is caught and shown in the status bar — no exception
        propagates to Textual's event loop.
        """
        if self._reader is None:
            return
        try:
            snapshot = self._reader.latest()
        except Exception:
            return
        if snapshot is None:
            process = self._reader.process
            if process is not None:
                try:
                    rc = process.poll()
                except Exception:
                    return
                if rc is not None:
                    self.query_one("#monitor_status", Static).update(
                        f"[red]Collector exited with code {rc}[/red]"
                    )
            return
        self._snapshot = snapshot
        self._render_snapshot()

    def _render_snapshot(self) -> None:
        """Render the latest monitor snapshot with error boundaries."""
        try:
            system = self._snapshot.get("System") or {}
            self.query_one("#monitor_system", Static).update(
                "  ".join(
                    (
                        f"CPU {system.get('CPU', '--')}%",
                        f"RAM {system.get('RamUsed', '--')}/{system.get('RamTotal', '--')} MB",
                        f"dGPU {system.get('DGpu', '--')}%",
                        f"VRAM {system.get('DVramUsed', '--')}/{system.get('DVramTotal', '--')} MB",
                        f"Disk ↓{system.get('DiskRead', '--')} ↑{system.get('DiskWrite', '--')}",
                    )
                )
            )
            agents = self.query_one("#agents_table", DataTable)
            agents.clear()
            for row in self._snapshot.get("Agents") or []:
                agents.add_row(
                    str(row.get("Agent") or "--"),
                    f"{row.get('Model') or '--'} / {row.get('Provider') or '--'}",
                    str(row.get("Action") or "--"),
                    str(row.get("PID") or "--"),
                    str(row.get("CPU") or "--"),
                    str(row.get("RAM") or "--"),
                    str(row.get("Tokens") or "--"),
                    str(row.get("ContextPercent") or "--"),
                    str(row.get("TPS") or "--"),
                )
            engines = self.query_one("#engines_table", DataTable)
            engines.clear()
            for row in self._snapshot.get("Engines") or []:
                engines.add_row(
                    str(row.get("Engine") or "--"),
                    str(row.get("Model") or "--"),
                    str(row.get("GPUDevice") or "--"),
                    str(row.get("Size") or "--"),
                    str(row.get("RAM") or "--"),
                    str(row.get("VRAM") or "--"),
                    str(row.get("VRAMFree") or "--"),
                    str(row.get("Tokens") or "--"),
                    str(row.get("ContextLimit") or "--"),
                    str(row.get("TPS") or "--"),
                )
            agents_list = self._snapshot.get("Agents") or []
            engines_list = self._snapshot.get("Engines") or []
            self.query_one("#monitor_status", Static).update(
                f"{len(agents_list)} agents · "
                f"{len(engines_list)} engines · live every 2s"
            )
        except Exception as exc:
            self.query_one("#monitor_status", Static).update(
                f"[red]Monitor render error: {exc}[/red]"
            )

    def on_action_strip_selected(self, event: ActionStrip.Selected) -> None:
        if event.strip_id != "monitor_actions":
            return
        if event.action == "restart_collector":
            if self._reader is None:
                self._start_collector()
            else:
                try:
                    self._reader.restart()
                    self.query_one("#monitor_status", Static).update("Collector restarted")
                except Exception as exc:
                    self.query_one("#monitor_status", Static).update(f"[red]Restart failed: {exc}[/red]")
        elif event.action == "open_full_monitor":
            command = (
                f"Set-Location -LiteralPath '{MONITOR_SCRIPT.parent}'; "
                f"python '{MONITOR_SCRIPT}' --mode terminal --backend-script '{MONITOR_COLLECTOR}' --refresh 2"
            )
            subprocess.Popen(["powershell.exe", "-NoExit", "-Command", command])
        elif event.action == "help":
            self.app.push_screen(HelpScreen())

    def on_unmount(self) -> None:
        if self._reader is not None:
            self._reader.stop()


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

class BenchmarkTUI(App):
    TITLE = "LLM Evaluation Workbench"
    SUB_TITLE = "Textual control center"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("h", "help", "Help"),
        Binding("f5", "refresh_all", "Refresh"),
        Binding("s", "run_control('start')", "Start"),
        Binding("p", "run_control('pause')", "Pause"),
        Binding("r", "run_control('resume')", "Resume"),
        Binding("x", "run_control('stop')", "Stop"),
        Binding("slash", "focus_filter", "Filter"),
        Binding("d", "cycle_density", "Density"),
        Binding("l", "toggle_layout", "Layout"),
        Binding("1", "show_tab('dashboard')", "Dashboard"),
        Binding("2", "show_tab('config')", "Config"),
        Binding("3", "show_tab('models')", "Models"),
        Binding("4", "show_tab('results')", "Results"),
        Binding("5", "show_tab('leaderboard')", "Leaderboard"),
        Binding("6", "show_tab('agent_monitor')", "Agent monitor"),
    ]
    density_mode = reactive(DENSITY_DEFAULT)
    _layout_settings: dict[str, Any] = {}
    CSS = """
    Screen { background: #0e1013; color: #d5d9dd; }
    Header { height: 1; background: #15191d; color: #55ccff; }
    Footer { height: 1; background: #15191d; color: #8b949e; }
    TabbedContent, ContentSwitcher, TabPane { background: #0e1013; }
    Tabs { height: 2; background: #15191d; }
    Tab { height: 2; padding: 0 2; background: #15191d; color: #8b949e; }
    Tab:hover { color: #d8f3ff; background: #20262c; }
    Tab.-active { background: #23313c; color: #55ccff; text-style: bold; }
    Input, Select, SelectionList {
        background: #15191d;
        color: #d5d9dd;
        border: solid #303840;
    }
    Input:focus, Select:focus, SelectionList:focus { border: solid #55aaff; }
    Button { min-width: 8; height: 3; border: none; background: #20262c; color: #d5d9dd; }
    Button:hover { background: #2c363f; color: #55ccff; }
    ActionStrip {
        height: 1;
        width: auto;
        min-width: 24;
        background: #15191d;
        color: #d5d9dd;
    }
    .toolbar {
        height: 3;
        min-height: 3;
        max-height: 3;
        align: left middle;
        background: #15191d;
        padding: 0 1;
    }
    .toolbar Select { height: 3; }
    .toolbar ActionStrip { margin-left: 2; }
    .panel_title { height: 1; color: #55ccff; text-style: bold; }
    DataTable {
        height: 1fr;
        background: #101316;
        color: #d5d9dd;
        border: solid #303840;
    }
    DataTable > .datatable--header {
        background: #182128;
        color: #d8f3ff;
        text-style: bold;
    }
    DataTable > .datatable--cursor { background: #263b49; color: #ffffff; }
    #status_line { height: 2; padding: 0 1; background: #101316; color: #8b949e; }
    #dashboard_actions, #leaderboard_actions {
        height: 1;
        min-height: 1;
        margin: 0 1;
    }
    #run_log { height: 1fr; border: solid #303840; background: #0b0d0f; }
    #config_select { width: 38; }
    #campaign_builder { height: 20; min-height: 15; max-height: 22; padding: 1; }
    .selector_panel { width: 1fr; height: 1fr; margin-right: 1; }
    .selector_panel SelectionList { height: 1fr; }
    #settings_panel {
        width: 34;
        height: 1fr;
        padding: 0 1;
        background: #12161a;
        border: solid #303840;
    }
    #settings_panel Label { height: 1; color: #8b949e; }
    #settings_panel Input, #settings_panel Select { height: 3; }
    .switch_row { height: 3; align: left middle; }
    .switch_row Switch { width: 8; }
    #plan_table { height: 1fr; min-height: 7; margin: 0 1; }
    #config_status, #models_status, #results_status, #monitor_status {
        height: 2;
        padding: 0 1;
        color: #8b949e;
        background: #15191d;
    }
    #pull_model { width: 36; }
    #filter { width: 32; }
    #board_table { height: 10; }
    #leaderboard_charts { height: 1fr; min-height: 8; }
    #chart, #scatter {
        width: 1fr;
        height: 1fr;
        border: solid #303840;
        padding: 1;
        background: #101316;
    }
    #monitor_system { width: 1fr; height: 1; color: #55ccff; }
    .monitor_pane { border: solid #303840; padding: 0; }
    #agents_pane { height: 1fr; }
    #engines_pane { height: 11; }
    #agents_table, #engines_table { border: none; }
    """

    def __init__(self) -> None:
        super().__init__()
        configs = list_campaign_configs()
        self.active_config = configs[0] if configs else rb.DEFAULT_CONFIG
        self._load_layout_settings()

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

    def _load_layout_settings(self) -> None:
        layout_path = CONFIG_DIR / "layout.json"
        if layout_path.is_file():
            try:
                data = json.loads(layout_path.read_text(encoding="utf-8"))
                self._layout_settings = data
                mode = data.get("density_mode", DENSITY_DEFAULT)
                if mode in DENSITY_CSS:
                    self.density_mode = mode
            except (json.JSONDecodeError, OSError):
                self._layout_settings = {}

    def _save_layout_settings(self) -> None:
        layout_path = CONFIG_DIR / "layout.json"
        self._layout_settings["density_mode"] = self.density_mode
        layout_path.write_text(json.dumps(self._layout_settings, indent=2), encoding="utf-8")

    def watch_density_mode(self, mode: str) -> None:
        mode_css = DENSITY_CSS.get(mode, DENSITY_CSS["normal"])
        self.css = self.CSS + "\n" + mode_css

    def action_cycle_density(self) -> None:
        modes = list(DENSITY_CSS.keys())
        idx = modes.index(self.density_mode) if self.density_mode in modes else 0
        next_mode = modes[(idx + 1) % len(modes)]
        self.density_mode = next_mode
        self._save_layout_settings()
        self.notify(f"Density mode: {[m[0] for m in DENSITY_MODES if m[1] == next_mode][0]}")

    def action_toggle_layout(self) -> None:
        self.push_screen(LayoutPanel(), self._on_layout_changed)

    def _on_layout_changed(self, result: LayoutPanel.LayoutChanged | None) -> None:
        if result is None:
            return
        if result.density != self.density_mode:
            self.density_mode = result.density
        self._layout_settings["sections"] = result.sections
        self._layout_settings["column_priorities"] = result.column_priorities
        self._save_layout_settings()
        self.notify(f"Layout updated (density: {[m[0] for m in DENSITY_MODES if m[1] == result.density][0]})")

    def action_show_tab(self, tab_id: str) -> None:
        self.query_one(TabbedContent).active = tab_id

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_run_control(self, action: str) -> None:
        pane = self.query_one(DashboardPane)
        pane.on_action_strip_selected(ActionStrip.Selected("dashboard_actions", action))

    def action_focus_filter(self) -> None:
        self.query_one(TabbedContent).active = "results"
        self.query_one("#filter", Input).focus()

    def action_refresh_all(self) -> None:
        """F5 — Reload data for all active screens and clear error states.

        Each pane's own error boundaries handle individual failures;
        this method simply re-triggers every pane's data-loading.
        """
        # Dashboard
        try:
            self.query_one(DashboardPane).refresh_status()
        except Exception:
            pass

        # Config
        try:
            pane = self.query_one(ConfigPane)
            pane.query_one("#config_status", Static).update("[dim]Reloading Config …[/dim]")
            pane._load_selected()
        except Exception:
            pass

        # Models
        try:
            self.query_one(ModelsPane).refresh_models()
        except Exception:
            pass

        # Results
        try:
            self.query_one(ResultsPane).refresh_results()
        except Exception:
            pass

        # Leaderboard
        try:
            self.query_one(LeaderboardPane).refresh_board()
        except Exception:
            pass

        # Agent Monitor — restart the collector
        try:
            self.query_one(AgentMonitorPane)._start_collector()
        except Exception:
            pass


def main() -> int:
    BenchmarkTUI().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
