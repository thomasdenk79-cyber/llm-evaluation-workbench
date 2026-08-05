#!/usr/bin/env python3
"""Unified benchmark entrypoint.

The individual runners remain the implementation of each benchmark contract.
This module only resolves configuration, plans a campaign, delegates runs, and
maintains one durable detail/summary/dashboard view.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import html
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None


ROOT = Path(__file__).resolve().parents[1]
RUNNERS = {
    "agent-helper": "run_agent_helper_campaign.py",
    "cross-backend": "run_cross_backend_mini_benchmark.py",
    "hard-agent": "run_hard_agent_benchmark.py",
    "agent-understanding": "run_agent_understanding_benchmark.py",
    "model-understanding": "run_model_understanding_benchmark.py",
    "language-policy": "run_language_policy_retest.py",
    "sustained-ollama": "run_sustained_ollama_load.py",
}
DEFAULT_DETAIL = ROOT / "benchmark_results" / "unified_benchmark_detail.csv"
DEFAULT_CONFIG = ROOT / "config" / "benchmark.toml"
DEFAULT_PAUSE_FILE = ROOT / "pause.ini"
DEFAULT_STOP_FILE = ROOT / "stop.ini"
DEFAULT_LOCK_FILE = ROOT / ".benchmark_master.pid"
PAUSED_EXIT_CODE = 75
DEFAULT_SIEMENS_MODELS = [
    "deepseek-v4-flash",
    "gpt-oss-120b",
    "qwen-3.6-27b",
    "Mistral-Small-24B-Instruct-2501-FP8-dynamic",
    "ministral-3-14b-instruct-2512",
]


def _repo_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, help="TOML file containing campaign options.")
    p.add_argument("--suites", "--suite", dest="suites", help="Comma-separated suites or JSON suite IDs.")
    p.add_argument(
        "--backend",
        choices=["ollama", "llama_cpp", "siemens", "both", "all"],
        help="Backend/provider. 'both' is local only; 'all' also includes Siemens.",
    )
    p.add_argument("--models", action="append", help="Comma-separated model names or fnmatch patterns.")
    p.add_argument("--ollama-model", action="append", dest="ollama_models")
    p.add_argument("--ollama-url", help="Ollama base URL used for wildcard discovery.")
    p.add_argument("--llama-model", action="append", dest="llama_models", help="NAME=GGUF path (repeatable).")
    p.add_argument("--llama-server", help="llama-server executable path.")
    p.add_argument("--output", "--detail-csv", dest="detail_csv", type=Path, help="Unified detail CSV.")
    p.add_argument("--runs", type=int)
    p.add_argument("--timeout-sec", type=int)
    p.add_argument("--resume", choices=["auto", "off"])
    p.add_argument("--run", choices=["resume", "force"], help="Resume missing samples or start a new run output.")
    p.add_argument("--pause", action="store_true", help="Create pause.ini; the active run waits and can resume.")
    p.add_argument("--stop", action="store_true", help="Stop the active master run and its owned runner tree.")
    p.add_argument("--pause-file", type=Path)
    p.add_argument("--stop-file", type=Path,
                   help="Presence terminates the active suite (unlike pause.ini).")
    p.add_argument("--lock-file", type=Path)
    p.add_argument("--interactive", action="store_true", help="Use a dependency-free terminal selector.")
    p.add_argument("--show-matrix", action="store_true",
                   help="Print the expanded [[matrix]] wildcard plan (backend/model/benchmark) and exit; no run.")
    p.add_argument("--validate-config", action="store_true",
                   help="Validate the campaign and print its resolved plan without running it.")
    p.add_argument("--doctor", action="store_true",
                   help="Check campaign, backend availability, model paths, and result directories.")
    p.add_argument("--list-models", action="store_true", help="List installed Ollama models and configured GGUFs.")
    p.add_argument("--pull-ollama", metavar="MODEL", help="Pull an explicitly selected Ollama model.")
    p.add_argument("--llama-install", metavar="NAME=PATH", help="Register an existing GGUF path; never downloads.")
    p.add_argument("--install-ollama", action="store_true",
                   help="Offer the official Ollama installer, requiring interactive confirmation.")
    p.add_argument("--hwinfo-start-command", help="Opt-in command template, started before each run.")
    p.add_argument("--hwinfo-stop-command", help="Opt-in command template, run after each run.")
    p.add_argument("--hwinfo-executable", type=Path, help="Documented executable/path placeholder for templates.")
    p.add_argument("legacy_suite", nargs="?", help=argparse.SUPPRESS)
    p.add_argument("legacy_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return p


def _gui() -> list[str] | None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception:
        print("No CLI parameters were supplied and Tkinter is unavailable.")
        print("Use: python .\\scripts\\run_benchmark.py --suites migration --backend ollama --models '*qwen*'")
        return None
    result: list[str] = []
    root = tk.Tk()
    root.title("LLM benchmark")
    root.resizable(False, False)
    fields: dict[str, tk.Entry] = {}
    labels = [("Suites", "migration"), ("Backend", "ollama"), ("Models", ""), ("Runs", "1"), ("Output CSV", str(DEFAULT_DETAIL))]
    for row, (label, default) in enumerate(labels):
        tk.Label(root, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        entry = tk.Entry(root, width=58)
        entry.insert(0, default)
        entry.grid(row=row, column=1, padx=8, pady=4)
        fields[label] = entry
    def choose() -> None:
        path = filedialog.asksaveasfilename(initialfile="unified_benchmark_detail.csv")
        if path:
            fields["Output CSV"].delete(0, tk.END)
            fields["Output CSV"].insert(0, path)
    tk.Button(root, text="Choose…", command=choose).grid(row=4, column=2, padx=4)
    def submit() -> None:
        for key, flag in (("Suites", "--suites"), ("Backend", "--backend"), ("Models", "--models"),
                          ("Runs", "--runs"), ("Output CSV", "--output")):
            value = fields[key].get().strip()
            if value:
                result.extend([flag, value])
        root.destroy()
    tk.Button(root, text="Run", command=submit).grid(row=5, column=1, pady=8)
    root.mainloop()
    return result or None


def _terminal_menu() -> list[str] | None:
    """Small ANSI/numbered selector; deliberately uses only the standard library."""
    print("\033[36mLLM benchmark interactive setup\033[0m")
    suites = ["migration", "ora-pg-py-33", "living-memory", "agent-helper"]
    for i, value in enumerate(suites, 1):
        print(f"  {i}) {value}")
    selected = input("Suites (numbers or comma-separated names) [1]: ").strip() or "1"
    chosen = []
    for item in selected.split(","):
        item = item.strip()
        chosen.append(suites[int(item) - 1] if item.isdigit() and 1 <= int(item) <= len(suites) else item)
    backend = input("Backend [ollama/llama_cpp/both] (both): ").strip() or "both"
    models = input("Models/patterns (comma-separated, blank = configured): ").strip()
    return ["--suites", ",".join(chosen), "--backend", backend] + (["--models", models] if models else [])


def _ollama_inventory(url: str) -> list[str]:
    with urllib.request.urlopen(_ollama_base_url(url) + "/api/tags", timeout=5) as response:
        return sorted(str(item["name"]) for item in json.loads(response.read()).get("models", []) if item.get("name"))


def _ollama_base_url(url: str) -> str:
    base_url = url.rstrip("/")
    if base_url.endswith("/api/generate"):
        return base_url[:-len("/api/generate")]
    if base_url.endswith("/api"):
        return base_url[:-len("/api")]
    return base_url


def _ensure_ollama(url: str, *, auto_start: bool = True, timeout_sec: float = 30.0) -> tuple[bool, str]:
    """Return Ollama readiness and optionally start `ollama serve` once."""
    try:
        names = _ollama_inventory(url)
        return True, f"ready ({len(names)} installed models)"
    except Exception as first_error:
        if not auto_start:
            return False, str(first_error)
    executable = shutil.which("ollama")
    if not executable:
        return False, "ollama executable not found"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [executable, "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except OSError as exc:
        return False, f"could not start Ollama: {exc}"
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            names = _ollama_inventory(url)
            return True, f"started ({len(names)} installed models)"
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    return False, f"Ollama did not become ready: {last_error}"


def _model_management(args: argparse.Namespace, cfg: dict[str, Any]) -> int | None:
    if args.install_ollama:
        if not sys.stdin.isatty() or input("Run the official Ollama installer now? [y/N] ").lower() != "y":
            print("Ollama installation cancelled; install it from https://ollama.com/download/windows")
            return 0
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "irm https://ollama.com/install.ps1 | iex"], check=False)
        return 0
    url = str(_value(args, cfg, "ollama_url", "http://127.0.0.1:11434"))
    if args.list_models or args.pull_ollama:
        try:
            names = _ollama_inventory(url)
            print("Installed Ollama models:")
            print("\n".join(f"  {name}" for name in names) or "  (none)")
        except Exception as exc:
            print(f"Cannot list Ollama models: {exc}. Install/start Ollama: https://ollama.com/download")
            if args.list_models:
                ggufs = _split(_value(args, cfg, "llama_models", []))
                print("Configured llama.cpp GGUFs:")
                print("\n".join(f"  {item}" for item in ggufs) or "  (none)")
                return 1
        if args.pull_ollama:
            print(f"Pulling explicitly requested model {args.pull_ollama} …")
            try:
                return subprocess.run(["ollama", "pull", args.pull_ollama], check=False).returncode
            except OSError:
                print("Ollama executable not found. Install from https://ollama.com/download.")
                return 1
        if args.list_models:
            print("Configured llama.cpp GGUFs:")
            print("\n".join(f"  {item}" for item in _split(_value(args, cfg, "llama_models", []))) or "  (none)")
        return 0
    if args.llama_install:
        name, sep, path = args.llama_install.partition("=")
        if not sep or not name or not Path(path).is_file():
            print("--llama-install requires NAME=existing-GGUF-path; no download is performed.")
            return 2
        print(f"llama.cpp GGUF available: {name}={Path(path).resolve()}")
        return 0
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _is_master_process(pid: int) -> bool:
    if pid == os.getpid():
        return True
    if os.name != "nt":
        return True
    try:
        query = ("$p=Get-CimInstance Win32_Process -Filter 'ProcessId=%d' "
                 "| Select-Object -ExpandProperty CommandLine; if($p){$p}") % pid
        result = subprocess.run(["powershell", "-NoProfile", "-Command", query],
                                capture_output=True, text=True, timeout=5, check=False)
        return "run_benchmark.py" in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _read_lock(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
        return data if _pid_alive(pid) and _is_master_process(pid) else None
    except (OSError, ValueError, TypeError):
        return None


def _terminate_tree(pid: int) -> None:
    if pid == os.getpid() or not _pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
    else:
        subprocess.run(["kill", "-TERM", str(pid)], capture_output=True, check=False)


def _acquire_lock(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_lock(path)
    if existing:
        print(f"Another benchmark master is running (PID {existing['pid']}); aborting duplicate safely.")
        return False
    path.unlink(missing_ok=True)
    payload = {"pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(),
               "command": " ".join(sys.argv)}
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except FileExistsError:
        return False
    return True


def _release_lock(path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("pid", 0)) == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        path.unlink(missing_ok=True)


def _config(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    if tomllib is None:
        raise RuntimeError("TOML configuration requires Python 3.11+ (tomllib unavailable).")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return data.get("benchmark", data)


def _value(args: argparse.Namespace, cfg: dict[str, Any], name: str, default: Any = None) -> Any:
    value = getattr(args, name, None)
    return value if value is not None else cfg.get(name, default)


def _split(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [item.strip() for part in values for item in str(part).split(",") if item.strip()]


def _ollama_models(patterns: list[str], url: str = "http://127.0.0.1:11434") -> list[str]:
    if not patterns:
        return []
    base_url = url.rstrip("/")
    if base_url.endswith("/api/generate"):
        base_url = base_url[:-len("/api/generate")]
    elif base_url.endswith("/api"):
        base_url = base_url[:-len("/api")]
    try:
        with urllib.request.urlopen(base_url + "/api/tags", timeout=5) as response:
            tags = json.loads(response.read().decode("utf-8")).get("models", [])
        names = [str(item.get("name", "")) for item in tags if item.get("name")]
    except Exception as exc:
        raise RuntimeError(f"Cannot discover Ollama models for wildcard matching: {exc}") from exc
    return sorted({name for pattern in patterns for name in names if fnmatch.fnmatchcase(name, pattern)})


def _hwinfo(command: str | None, executable: str | None = None) -> subprocess.Popen[str] | None:
    if not command:
        return None
    rendered = command.replace("{executable}", executable or "")
    print(f"[HWiNFO] starting configured command: {rendered}")
    return subprocess.Popen(rendered, shell=True, text=True)


def _stop_hwinfo(command: str | None, executable: str | None, process: subprocess.Popen[str] | None) -> None:
    if command:
        rendered = command.replace("{executable}", executable or "")
        print(f"[HWiNFO] stopping configured command: {rendered}")
        subprocess.run(rendered, shell=True, check=False)
    if process is not None and process.poll() is None:
        process.terminate()


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _unload_ollama() -> None:
    """Unload all currently listed Ollama models without killing the service."""
    try:
        result = subprocess.run(
            ["ollama", "ps"], capture_output=True, text=True, timeout=15, check=False
        )
    except OSError:
        return
    names = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if parts:
            names.append(parts[0])
    for name in names:
        subprocess.run(["ollama", "stop", name], capture_output=True, text=True, timeout=30, check=False)


def _wait_for_resume(pause_file: Path) -> None:
    print(f"[PAUSED] Remove {pause_file} to resume.", flush=True)
    while pause_file.exists():
        _unload_ollama()
        time.sleep(3)


def _run_runner(command: list[str], pause_file: Path, stop_file: Path, work_dir: Path, detail: Path) -> int:
    """Run a child runner; pause suspends, stop terminates the suite."""
    while True:
        if stop_file.exists():
            return 130
        if pause_file.exists():
            _wait_for_resume(pause_file)
        process = subprocess.Popen(command, cwd=str(ROOT))
        while process.poll() is None:
            if stop_file.exists():
                print("[STOP] Terminating active benchmark suite.", flush=True)
                _terminate_tree(process.pid)
                _append_unified(detail, _read_runner_csvs(work_dir))
                _unload_ollama()
                return 130
            if pause_file.exists():
                print("[PAUSED] Suspending active benchmark runner.", flush=True)
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    _terminate_tree(process.pid)
                _append_unified(detail, _read_runner_csvs(work_dir))
                _unload_ollama()
                _wait_for_resume(pause_file)
                break
            time.sleep(1)
        else:
            return int(process.returncode or 0)
        print("[RESUME] Continuing from the durable detail CSV.", flush=True)


def _read_runner_csvs(work_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(work_dir.glob("migration_llm_bench_*.csv")):
        rows.extend(_read_rows(path))
    cross_backend = work_dir / "cross_backend_mini.csv"
    for row in _read_rows(cross_backend):
        quality = row.get("quality_percent", "")
        elapsed = row.get("elapsed_seconds", "")
        rows.append({
            **row,
            "benchmark_display": "Cross-backend mini coder - 1 task",
            "benchmark_name": "cross-backend-mini",
            "benchmark_task_count": "1",
            "benchmark_runs": "1",
            "samples": "1",
            "provider": "Local/Ollama" if row.get("backend") == "ollama" else "Local/llama.cpp",
            "run_started_at": row.get("timestamp", ""),
            "recorded_at": row.get("timestamp", ""),
            "last_update": row.get("timestamp", ""),
            "elapsed_seconds": elapsed,
            "wall_s": elapsed,
            "output_tps": row.get("tokens_per_second", ""),
            "system_errors": row.get("error", ""),
            "error_text": row.get("error", ""),
            "quality_score": quality,
            "heuristic_score": quality,
            "rating_score": row.get("score", ""),
            "interpretation": row.get("agent_suitability", ""),
            "launch_params": json.dumps({"configuration": row.get("configuration", "")}),
        })
    return rows


def _append_unified(detail: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    detail.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_rows(detail)
    fields: list[str] = []
    for row in existing + rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    key_of = lambda row: row.get("sample_id") or "|".join(row.get(k, "") for k in ("benchmark_run_id", "backend", "model", "case_id", "run"))
    merged: dict[str, dict[str, str]] = {key_of(row): row for row in existing}
    merged.update({key_of(row): row for row in rows})
    with detail.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in merged.values():
            writer.writerow({field: row.get(field, "") for field in fields})


def _summary(detail: Path) -> Path:
    return detail.with_name(detail.stem.replace("_detail", "_summary") + ".csv")


def _dashboard(detail: Path, planned: list[dict[str, str]]) -> None:
    rows = _read_rows(detail)
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row.get("benchmark_name", row.get("benchmark", "")), row.get("backend", ""), row.get("model", ""))].append(row)
    summary_rows = []
    for (suite, backend, model), items in grouped.items():
        ok = [r for r in items if not r.get("error")]
        def avg(key: str) -> str:
            vals = [float(r[key]) for r in ok if r.get(key) not in ("", None)]
            return f"{sum(vals) / len(vals):.2f}" if vals else ""
        summary_rows.append({"benchmark": suite, "backend": backend, "model": model,
                             "samples": str(len(items)), "score": avg("quality_score") or avg("score"),
                             "elapsed": avg("wall_ms") or avg("elapsed"), "errors": str(len(items) - len(ok))})
    out = _summary(detail)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["benchmark", "backend", "model", "samples", "score", "elapsed", "errors"])
        writer.writeheader(); writer.writerows(summary_rows)
    planned_html = "".join(f"<tr><td>{html.escape(p['suite'])}</td><td>{html.escape(p['backend'])}</td><td>{html.escape(p['model'])}</td><td>{html.escape(p['status'])}</td></tr>" for p in planned)
    history_html = "".join(f"<tr><td>{html.escape(r['benchmark'])}</td><td>{html.escape(r['backend'])}</td><td>{html.escape(r['model'])}</td><td>{r['samples']}</td><td>{r['score']}</td><td>{r['elapsed']}</td><td>{r['errors']}</td></tr>" for r in sorted(summary_rows, key=lambda x: (x["benchmark"], x["backend"], x["model"])))
    page = f"""<!doctype html><meta charset="utf-8"><title>Unified benchmark dashboard</title>
<style>body{{font:15px system-ui;background:#101827;color:#e5edf8;margin:2rem}}section{{background:#18243a;padding:1rem;margin:1rem 0;border-radius:12px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:.55rem;border-bottom:1px solid #34445f;text-align:left}}th{{cursor:pointer;color:#8dd7ff}}input{{padding:.6rem;width:28rem;background:#0d1524;color:white;border:1px solid #466}}.planned{{color:#ffd166}}.done{{color:#70e1a1}}</style>
<h1>Unified benchmark dashboard</h1><p>Updated {html.escape(datetime.now(timezone.utc).isoformat())}. Detail source: <code>{html.escape(str(detail))}</code></p>
<section><h2>Planned / running</h2><table><tr><th>Suite</th><th>Backend</th><th>Model</th><th>Status</th></tr>{planned_html}</table></section>
<section><h2>Aggregated history</h2><input id="filter" placeholder="Filter suite, backend, or model"><table id="history"><tr><th>Benchmark</th><th>Backend</th><th>Model</th><th>Samples</th><th>Score</th><th>Elapsed</th><th>Errors</th></tr>{history_html}</table></section>
<script>document.querySelector('#filter').oninput=e=>{{let q=e.target.value.toLowerCase();document.querySelectorAll('#history tr').forEach((r,i)=>{{if(i)r.hidden=!r.innerText.toLowerCase().includes(q)}})}};document.querySelectorAll('th').forEach((h)=>h.onclick=()=>{{let t=h.closest('table'),i=[...h.parentNode.children].indexOf(h),rs=[...t.querySelectorAll('tr')].slice(1);rs.sort((a,b)=>a.children[i].innerText.localeCompare(b.children[i].innerText));rs.forEach(r=>t.append(r))}})</script>"""
    detail.with_suffix(".html").write_text(page, encoding="utf-8")


def _runner_command(suite: str, args: argparse.Namespace, cfg: dict[str, Any], work_dir: Path,
                     overrides: dict[str, Any] | None = None) -> list[str]:
    overrides = overrides or {}
    option = lambda name, default=None: overrides.get(name, _value(args, cfg, name, default))
    if suite in RUNNERS:
        script = str(Path(__file__).with_name(RUNNERS[suite]))
        suite_args = cfg.get("suite_args", {})
        configured = suite_args.get(suite, []) if isinstance(suite_args, dict) else []
        if isinstance(configured, str):
            configured = shlex.split(configured)
        configured = [str(value) for value in configured] if isinstance(configured, list) else []
        command = [sys.executable, script, *configured, *args.legacy_args]
        if suite == "cross-backend":
            if "--output" not in command:
                command += ["--output", str(work_dir / "cross_backend_mini.csv")]
            for model in _split(_value(args, cfg, "models", [])) + _split(_value(args, cfg, "ollama_models", [])):
                if "*" not in model and "?" not in model:
                    command += ["--ollama-model", model]
            for spec in _split(_value(args, cfg, "llama_models", [])):
                command += ["--llama-model", spec]
            if _value(args, cfg, "llama_server"):
                command += ["--server", str(_value(args, cfg, "llama_server"))]
            if _value(args, cfg, "ollama_url"):
                command += ["--ollama-url", _ollama_base_url(str(_value(args, cfg, "ollama_url")))]
            if _value(args, cfg, "timeout_sec") is not None:
                command += ["--timeout", str(_value(args, cfg, "timeout_sec"))]
        return command
    command = [sys.executable, str(Path(__file__).with_name("llm_migration_benchmark.py"))]
    if suite != "migration":
        command += ["--benchmark-id", suite]
    command += ["--backend", str(option("backend", "both")),
               "--output-dir", str(work_dir), "--report-file", str(ROOT / "docs" / "project" / "benchmark_report.md"),
               "--runs", str(option("runs", 1)),
               "--resume", ("off" if option("run", "resume") == "force"
                            else option("resume", "auto"))]
    command += list(args.legacy_args)
    backend = str(option("backend", "both"))
    models = overrides.get("models")
    if models is None:
        models = _split(_value(args, cfg, "models", [])) + _split(_value(args, cfg, "ollama_models", []))
    if backend in ("ollama", "both") and models:
        resolved = (
            _ollama_models(models, str(_value(args, cfg, "ollama_url", "http://127.0.0.1:11434")))
            if any("*" in m or "?" in m for m in models)
            else models
        )
        for model in resolved:
            command += ["--ollama-model", model]
    llama_specs = _split(option("llama_models", []))
    if backend == "llama_cpp" and models is not None:
        aliases = {str(model) for model in models}
        llama_specs = [
            spec for spec in llama_specs
            if spec.partition("=")[0].strip() in aliases
        ]
    for spec in llama_specs:
        command += ["--llama-model", spec]
    if backend in ("siemens", "all"):
        siemens_models = (
            [str(model) for model in models]
            if models is not None
            else _split(option("siemens_models", []))
        )
        for model in siemens_models:
            command += ["--siemens-model", model]
    if option("ollama_url"):
        command += ["--ollama-url", str(option("ollama_url"))]
    if option("llama_server"):
        command += ["--llama-server", str(option("llama_server"))]
    if option("timeout_sec") is not None:
        command += ["--timeout-sec", str(option("timeout_sec"))]
    runner_args = option("runner_args", [])
    if isinstance(runner_args, str):
        runner_args = shlex.split(runner_args)
    runner_args = [str(value) for value in runner_args] if isinstance(runner_args, list) else []
    benchmark_file = overrides.get("benchmark_file")
    if benchmark_file:
        if "--benchmark-file" in runner_args:
            idx = runner_args.index("--benchmark-file")
            runner_args[idx + 1] = benchmark_file
        else:
            runner_args += ["--benchmark-file", benchmark_file]
    command += runner_args
    return command


# --------------------------------------------------------------------------
# Wildcard campaign matrix: `[[matrix]]` array-of-tables in a campaign TOML,
# each entry with `models` / `benchmarks` / `backend` fnmatch-style patterns,
# expanded into concrete (backend, benchmark_file, [models]) execution
# groups. Purely additive: a config with no `[[matrix]]` table behaves
# exactly as before. See docs/project/requirements.md §5.
# --------------------------------------------------------------------------

def _load_matrix(
    config_path: Path | None,
    *,
    include_synthetic: bool = True,
) -> list[dict[str, Any]]:
    if not config_path or tomllib is None or not config_path.exists():
        return []
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    matrix = data.get("matrix", [])
    if isinstance(matrix, list) and matrix:
        return matrix
    if not include_synthetic:
        return []
    cfg = data.get("benchmark", data)
    benchmarks = _split(cfg.get("benchmarks", []))
    if not benchmarks:
        return []
    configured = _split(cfg.get("backend", "ollama"))
    backends: list[str] = []
    for backend in configured:
        aliases = (
            ["ollama", "llama_cpp"] if backend == "both"
            else ["ollama", "llama_cpp", "siemens"] if backend == "all"
            else [backend]
        )
        backends.extend(item for item in aliases if item not in backends)
    entries: list[dict[str, Any]] = []
    for backend in backends:
        entry: dict[str, Any] = {"backend": backend, "benchmarks": benchmarks}
        if backend == "ollama":
            models = _split(cfg.get("models", [])) + _split(cfg.get("ollama_models", []))
        elif backend == "llama_cpp":
            models = [
                spec.partition("=")[0].strip()
                for spec in _split(cfg.get("llama_models", []))
                if "=" in spec
            ]
        else:
            models = _split(cfg.get("siemens_models", [])) or list(DEFAULT_SIEMENS_MODELS)
        if models:
            entry["models"] = models
        entries.append(entry)
    return entries


def _available_benchmark_files() -> dict[str, Path]:
    """Map only migration-compatible benchmark fixtures, never arbitrary JSON."""
    out: dict[str, Path] = {}
    benchmarks_dir = ROOT / "benchmarks"
    if benchmarks_dir.is_dir():
        for path in benchmarks_dir.glob("*.json"):
            if _is_migration_benchmark(path):
                out[path.stem] = path
    scripts_benchmarks = ROOT / "scripts" / "benchmarks"
    if scripts_benchmarks.is_dir():
        for path in scripts_benchmarks.glob("*.json"):
            if _is_migration_benchmark(path):
                out.setdefault(path.stem, path)
    return out


def _is_migration_benchmark(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    return (
        isinstance(payload.get("name"), str)
        and isinstance(payload.get("spec_version"), str)
        and isinstance(tasks, list)
        and bool(tasks)
        and all(
            isinstance(task, dict)
            and task.get("id")
            and task.get("prompt")
            and isinstance(task.get("required_keywords"), list)
            for task in tasks
        )
    )


def expand_matrix(
    matrix: list[dict[str, Any]],
    args: argparse.Namespace,
    cfg: dict[str, Any],
    *,
    resolve_wildcards: bool = True,
) -> list[dict[str, Any]]:
    """Expand `[[matrix]]` entries into concrete (backend, benchmark_file, models) groups.

    Each matrix entry may specify `models` (fnmatch patterns matched against
    installed Ollama tags and/or the configured llama.cpp GGUF registry),
    `benchmarks` (fnmatch patterns matched against benchmark fixture stems
    under `benchmarks/`), and `backend` (one or more of `ollama`,
    `llama_cpp`; defaults to the campaign's configured backend). Missing
    `models`/`benchmarks` in an entry means "all configured" for that axis.
    """
    if not matrix:
        return []
    ollama_url = str(_value(args, cfg, "ollama_url", "http://127.0.0.1:11434"))
    all_ollama_models = _split(_value(args, cfg, "models", [])) + _split(_value(args, cfg, "ollama_models", []))
    gguf_specs = _split(_value(args, cfg, "llama_models", []))
    gguf_names = [spec.split("=", 1)[0].strip() for spec in gguf_specs if "=" in spec]
    siemens_models = _split(_value(args, cfg, "siemens_models", [])) or list(DEFAULT_SIEMENS_MODELS)
    benchmark_files = _available_benchmark_files()
    groups: list[dict[str, Any]] = []
    for entry in matrix:
        configured_backends = _split(entry.get("backend", _value(args, cfg, "backend", "ollama")))
        backends: list[str] = []
        for backend in configured_backends:
            expanded = (
                ["ollama", "llama_cpp"] if backend == "both"
                else ["ollama", "llama_cpp", "siemens"] if backend == "all"
                else [backend]
            )
            backends.extend(item for item in expanded if item not in backends)
        benchmark_patterns = _split(entry.get("benchmarks", ["*"]))
        matched_benchmarks = sorted({
            stem for pattern in benchmark_patterns for stem in benchmark_files
            if fnmatch.fnmatchcase(stem, pattern)
        })
        for backend in backends:
            defaults = (
                all_ollama_models if backend == "ollama"
                else gguf_names if backend == "llama_cpp"
                else siemens_models
            )
            model_patterns = _split(entry.get("models", defaults or ["*"]))
            if backend == "ollama":
                has_wildcard = any("*" in p or "?" in p for p in model_patterns)
                if has_wildcard:
                    if not resolve_wildcards:
                        pool = sorted({
                            model for pattern in model_patterns for model in all_ollama_models
                            if fnmatch.fnmatchcase(model, pattern)
                        }) or list(model_patterns)
                    else:
                        try:
                            pool = _ollama_models(model_patterns, ollama_url)
                        except RuntimeError as exc:
                            pool = sorted({
                                model for pattern in model_patterns for model in all_ollama_models
                                if fnmatch.fnmatchcase(model, pattern)
                            })
                            print(
                                f"[matrix] Warning: Ollama discovery failed ({exc}); "
                                f"using {len(pool)} configured matches."
                            )
                else:
                    pool = list(model_patterns)
            elif backend == "llama_cpp":
                pool = sorted({name for pattern in model_patterns for name in gguf_names
                              if fnmatch.fnmatchcase(name, pattern)})
            elif backend == "siemens":
                pool = sorted({
                    name for pattern in model_patterns for name in siemens_models
                    if fnmatch.fnmatchcase(name, pattern)
                }) or ([] if any("*" in p or "?" in p for p in model_patterns) else model_patterns)
            else:
                pool = []
            for benchmark_stem in matched_benchmarks:
                groups.append({
                    "backend": backend,
                    "models": pool,
                    "benchmark_stem": benchmark_stem,
                    "benchmark_file": str(benchmark_files[benchmark_stem]) if benchmark_stem in benchmark_files else None,
                    **{
                        key: value for key, value in entry.items()
                        if key not in {"backend", "models", "benchmarks"}
                    },
                })
    return groups


def preview_campaign(config_path: Path) -> list[dict[str, Any]]:
    """Resolve the exact execution groups shown by CLI and TUI."""
    cfg = _config(config_path)
    args = _parser().parse_args(["--config", str(config_path)])
    matrix = _load_matrix(config_path)
    if matrix:
        return expand_matrix(matrix, args, cfg)
    suites = _split(cfg.get("suites", "migration"))
    backends = _split(cfg.get("backend", "both"))
    models = _split(cfg.get("models", [])) + _split(cfg.get("ollama_models", []))
    runner_args = _split(cfg.get("runner_args", []))
    explicit_file = ""
    if "--benchmark-file" in runner_args:
        index = runner_args.index("--benchmark-file")
        if index + 1 < len(runner_args):
            explicit_file = runner_args[index + 1]
    return [
        {
            "backend": backend,
            "models": models,
            "benchmark_stem": Path(explicit_file).stem if explicit_file else suite,
            "benchmark_file": explicit_file or suite,
        }
        for backend in backends
        for suite in suites
    ]


def validate_campaign(config_path: Path) -> list[str]:
    """Return user-actionable validation errors without starting a run."""
    errors: list[str] = []
    if not config_path.is_file():
        return [f"configuration not found: {config_path}"]
    try:
        cfg = _config(config_path)
    except (OSError, ValueError) as exc:
        return [f"invalid TOML: {exc}"]
    backend = str(cfg.get("backend", "both"))
    if backend not in {"ollama", "llama_cpp", "siemens", "both", "all"}:
        errors.append(f"unsupported backend: {backend}")
    for key in ("runs", "timeout_sec"):
        try:
            if int(cfg.get(key, 1)) < 1:
                errors.append(f"{key} must be at least 1")
        except (TypeError, ValueError):
            errors.append(f"{key} must be an integer")
    try:
        groups = preview_campaign(config_path)
    except Exception as exc:
        errors.append(f"campaign expansion failed: {exc}")
        return errors
    if not groups:
        errors.append("campaign resolves to zero execution groups")
    for group in groups:
        if not group.get("models") and group.get("backend") != "siemens":
            errors.append(
                f"{group.get('backend')} / {group.get('benchmark_stem')}: no models resolved"
            )
        benchmark_file = group.get("benchmark_file")
        if benchmark_file and str(benchmark_file).lower().endswith(".json"):
            path = Path(str(benchmark_file))
            if not path.is_absolute():
                path = ROOT / path
            if not path.is_file():
                errors.append(f"benchmark file not found: {path}")
    return list(dict.fromkeys(errors))


def doctor(config_path: Path) -> int:
    errors = validate_campaign(config_path)
    print(f"Campaign: {config_path}")
    print("Config: " + ("OK" if not errors else "INVALID"))
    for error in errors:
        print(f"  ERROR {error}")
    cfg = _config(config_path) if config_path.is_file() else {}
    backends = {
        group.get("backend")
        for group in preview_campaign(config_path)
    } if not errors else set()
    if "ollama" in backends:
        ready, detail = _ensure_ollama(str(cfg.get("ollama_url", "http://127.0.0.1:11434")), auto_start=False)
        print(f"Ollama: {'OK' if ready else 'UNAVAILABLE'} - {detail}")
        if not ready:
            errors.append("Ollama is unavailable")
    if "llama_cpp" in backends:
        specs = _split(cfg.get("llama_models", []))
        missing = [spec for spec in specs if "=" not in spec or not Path(spec.split("=", 1)[1]).is_file()]
        print(f"llama.cpp: {'OK' if specs and not missing else 'INCOMPLETE'} - {len(specs)} configured GGUFs")
        errors.extend(f"missing GGUF: {spec}" for spec in missing)
    if "siemens" in backends:
        print("Siemens: configured; token and endpoint are checked by the runner without exposing credentials")
    detail = Path(cfg.get("detail_csv", DEFAULT_DETAIL))
    if not detail.is_absolute():
        detail = ROOT / detail
    print(f"Results: {detail}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        if sys.stdin.isatty() and sys.stdout.isatty():
            # Interactive terminal, no flags: open the Textual control center
            # instead of the old Tkinter dialog (see
            # docs/project/tui-web-architecture.md). Falls back to the
            # previous behavior if Textual is unavailable for any reason, so
            # this never becomes a hard requirement for local automation.
            try:
                from llm_bench_tui import main as tui_main
            except Exception as exc:
                print(f"Textual TUI unavailable ({exc}); falling back to the classic prompt.")
            else:
                return tui_main()
        raw = ["--config", str(DEFAULT_CONFIG)] if DEFAULT_CONFIG.exists() else (_gui() or [])
        if not raw:
            return 2
    args = _parser().parse_args(raw)
    if args.legacy_suite and args.suites is None:
        args.suites = args.legacy_suite
    config_path = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    cfg = _config(config_path)
    managed = _model_management(args, cfg)
    if managed is not None:
        return managed
    if args.doctor:
        if config_path is None:
            print("No campaign config found; pass --config PATH.")
            return 2
        return doctor(config_path)
    if args.validate_config:
        if config_path is None:
            print("No campaign config found; pass --config PATH.")
            return 2
        errors = validate_campaign(config_path)
        if errors:
            print("Campaign is invalid:")
            for error in errors:
                print(f"  - {error}")
            return 2
        groups = preview_campaign(config_path)
        print(f"Campaign is valid: {len(groups)} execution groups.")
        return 0
    if args.show_matrix:
        if config_path is None:
            print("No campaign config found; pass --config PATH.")
            return 2
        groups = preview_campaign(config_path)
        if not groups:
            print(f"Campaign in {config_path} expands to no execution groups.")
            return 0
        print(f"Resolved {len(groups)} run groups:")
        for group in groups:
            models_display = ", ".join(group["models"]) or "(none matched)"
            bench_display = group["benchmark_file"] or f"{group['benchmark_stem']} (file not found)"
            print(f"  backend={group['backend']:<10} benchmark={bench_display:<40} models=[{models_display}]")
        return 0
    pause_file = _repo_path(_value(args, cfg, "pause_file", DEFAULT_PAUSE_FILE))
    stop_file = _repo_path(_value(args, cfg, "stop_file", DEFAULT_STOP_FILE))
    lock_file = _repo_path(_value(args, cfg, "lock_file", DEFAULT_LOCK_FILE))
    if args.stop:
        stop_file.touch()
        lock = _read_lock(lock_file)
        if lock:
            pid = int(lock["pid"])
            print(f"Stop requested for benchmark master PID {pid}; waiting for clean shutdown.")
            for _ in range(30):
                if not _pid_alive(pid):
                    break
                time.sleep(1)
            if _pid_alive(pid):
                print("Master did not exit in time; terminating only its owned process tree.")
                _terminate_tree(pid)
            _unload_ollama()
        else:
            print(f"Stop requested; no active benchmark found. Removing stale {stop_file}.")
            stop_file.unlink(missing_ok=True)
        return 0
    if args.pause:
        pause_file.touch()
        lock = _read_lock(lock_file)
        if lock:
            print(f"Pause requested via {pause_file}; active master PID {lock['pid']} will pause.")
        else:
            print(f"Pause file created at {pause_file}; no active benchmark was found.")
            print("Remove the file before starting a new benchmark.")
        return 0
    if not _read_lock(lock_file) and pause_file.exists():
        print(f"Removing stale {pause_file}; no benchmark master is running.")
        pause_file.unlink(missing_ok=True)
    if not args.stop and stop_file.exists() and not _read_lock(lock_file):
        print(f"Removing stale {stop_file}; no benchmark master is running.")
        stop_file.unlink(missing_ok=True)
    if args.interactive:
        selected = _terminal_menu()
        if selected:
            args = _parser().parse_args(selected + (["--config", str(config_path)] if config_path else []))
            cfg = _config(config_path)
    planned_backends = {
        group.get("backend")
        for group in preview_campaign(config_path)
    } if config_path else {str(_value(args, cfg, "backend", "both"))}
    if planned_backends & {"ollama", "both", "all"}:
        ready, detail = _ensure_ollama(
            str(_value(args, cfg, "ollama_url", "http://127.0.0.1:11434")),
            auto_start=True,
        )
        print(f"[ollama] {detail}")
        if not ready:
            print("Ollama is required by this campaign but could not be started.")
            return 1
    if not _acquire_lock(lock_file):
        return 2
    try:
        return _main_locked(args, cfg, pause_file, stop_file, lock_file, config_path)
    finally:
        _release_lock(lock_file)


def _main_locked(args: argparse.Namespace, cfg: dict[str, Any], pause_file: Path,
                 stop_file: Path, lock_file: Path, config_path: Path | None = None) -> int:
    matrix = _load_matrix(config_path)
    if matrix:
        return _main_locked_matrix(args, cfg, pause_file, stop_file, lock_file, matrix)
    suites = _split(_value(args, cfg, "suites", args.legacy_suite or "migration"))
    detail = Path(_value(args, cfg, "detail_csv", DEFAULT_DETAIL))
    if not detail.is_absolute():
        detail = ROOT / detail
    run_mode = _value(args, cfg, "run", "resume")
    run_id: str | None = None
    if run_mode == "force":
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        detail = detail.with_name(f"{detail.stem}_run_{run_id}{detail.suffix}")
        print(f"[FORCE] Writing a new run output: {detail}")
    backend = _value(args, cfg, "backend", "both")
    models = _split(_value(args, cfg, "models", [])) + _split(_value(args, cfg, "ollama_models", []))
    planned = [{"suite": suite, "backend": backend, "model": ", ".join(models) or "(auto)", "status": "planned"} for suite in suites]
    work_dir = detail.parent / ".master_runs" / run_id if run_id else detail.parent / ".master_runs"
    work_dir.mkdir(parents=True, exist_ok=True)
    # Reconcile durable runner output before the first dashboard/render and
    # before every suite; the child runner performs the sample-level skip.
    _append_unified(detail, _read_runner_csvs(work_dir))
    _dashboard(detail, planned)
    for item in planned:
        _append_unified(detail, _read_runner_csvs(work_dir))
        if stop_file.exists():
            _unload_ollama()
            item["status"] = "stopped"
            _dashboard(detail, planned)
            return 0
        if pause_file.exists():
            _wait_for_resume(pause_file)
        item["status"] = "running"; _dashboard(detail, planned)
        process = _hwinfo(_value(args, cfg, "hwinfo_start_command"), str(_value(args, cfg, "hwinfo_executable", "")))
        before = {p for p in work_dir.glob("migration_llm_bench_*.csv") if not p.name.endswith("_inprogress.csv")}
        try:
            command = _runner_command(item["suite"], args, cfg, work_dir)
            print("Running:", " ".join(shlex.quote(part) for part in command))
            try:
                code = _run_runner(command, pause_file, stop_file, work_dir, detail)
            except OSError as exc:
                if backend in ("ollama", "both") and "ollama" in str(exc).lower():
                    print("Ollama is unavailable; install/start it from https://ollama.com/download.")
                if backend in ("llama_cpp", "both"):
                    print("llama.cpp is unavailable; install llama-server/llama-cli and configure --llama-server.")
                item["status"] = f"failed ({exc})"
                _dashboard(detail, planned)
                return 1
            if code == 130:
                item["status"] = "stopped"
                _dashboard(detail, planned)
                _unload_ollama()
                return 0
            if code:
                item["status"] = f"failed ({code})"
                _dashboard(detail, planned)
                return code
            after = sorted(
                (p for p in work_dir.glob("migration_llm_bench_*.csv") if p not in before),
                key=lambda p: p.stat().st_mtime,
            )
            if after or code == 0:
                _append_unified(detail, _read_runner_csvs(work_dir))
            item["status"] = "completed"; _dashboard(detail, planned)
        finally:
            _stop_hwinfo(_value(args, cfg, "hwinfo_stop_command"), str(_value(args, cfg, "hwinfo_executable", "")), process)
    print(f"Unified detail CSV: {detail}")
    print(f"Unified dashboard: {detail.with_suffix('.html')}")
    return 0


def _main_locked_matrix(args: argparse.Namespace, cfg: dict[str, Any], pause_file: Path,
                         stop_file: Path, lock_file: Path, matrix: list[dict[str, Any]]) -> int:
    """Execute a `[[matrix]]`-expanded wildcard campaign.

    Mirrors `_main_locked`'s control-flow (pause/stop checks, dashboard,
    unified CSV) but plans one runner invocation per expanded
    (backend, benchmark_file, models) group instead of one per suite, since
    a matrix entry can fan out into many such groups.
    """
    groups = expand_matrix(matrix, args, cfg)
    if not groups:
        print("Matrix expanded to zero run groups (no models/benchmarks matched); nothing to do.")
        return 0
    detail = Path(_value(args, cfg, "detail_csv", DEFAULT_DETAIL))
    if not detail.is_absolute():
        detail = ROOT / detail
    run_mode = _value(args, cfg, "run", "resume")
    run_id: str | None = None
    if run_mode == "force":
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        detail = detail.with_name(f"{detail.stem}_run_{run_id}{detail.suffix}")
        print(f"[FORCE] Writing a new run output: {detail}")
    planned = [{
        "suite": g["benchmark_stem"], "backend": g["backend"],
        "model": ", ".join(g["models"]) or "(none matched)", "status": "planned",
    } for g in groups]
    work_dir = detail.parent / ".master_runs" / run_id if run_id else detail.parent / ".master_runs"
    work_dir.mkdir(parents=True, exist_ok=True)
    _append_unified(detail, _read_runner_csvs(work_dir))
    _dashboard(detail, planned)
    for item, group in zip(planned, groups):
        _append_unified(detail, _read_runner_csvs(work_dir))
        if stop_file.exists():
            _unload_ollama()
            item["status"] = "stopped"
            _dashboard(detail, planned)
            return 0
        if pause_file.exists():
            _wait_for_resume(pause_file)
        if not group["models"]:
            item["status"] = "skipped (no models matched)"; _dashboard(detail, planned)
            continue
        if not group["benchmark_file"]:
            item["status"] = f"skipped ({group['benchmark_stem']} fixture file not found)"; _dashboard(detail, planned)
            continue
        item["status"] = "running"; _dashboard(detail, planned)
        process = _hwinfo(_value(args, cfg, "hwinfo_start_command"), str(_value(args, cfg, "hwinfo_executable", "")))
        before = {p for p in work_dir.glob("migration_llm_bench_*.csv") if not p.name.endswith("_inprogress.csv")}
        try:
            command = _runner_command("migration", args, cfg, work_dir, overrides={
                **group,
                "backend": group["backend"],
                "models": group["models"],
                "benchmark_file": group["benchmark_file"],
            })
            print("Running:", " ".join(shlex.quote(part) for part in command))
            try:
                code = _run_runner(command, pause_file, stop_file, work_dir, detail)
            except OSError as exc:
                if group["backend"] == "ollama" and "ollama" in str(exc).lower():
                    print("Ollama is unavailable; install/start it from https://ollama.com/download.")
                if group["backend"] == "llama_cpp":
                    print("llama.cpp is unavailable; install llama-server/llama-cli and configure --llama-server.")
                item["status"] = f"failed ({exc})"
                _dashboard(detail, planned)
                return 1
            if code == 130:
                item["status"] = "stopped"
                _dashboard(detail, planned)
                _unload_ollama()
                return 0
            if code:
                item["status"] = f"failed ({code})"
                _dashboard(detail, planned)
                return code
            after = sorted(
                (p for p in work_dir.glob("migration_llm_bench_*.csv") if p not in before),
                key=lambda p: p.stat().st_mtime,
            )
            if after or code == 0:
                _append_unified(detail, _read_runner_csvs(work_dir))
            item["status"] = "completed"; _dashboard(detail, planned)
        finally:
            _stop_hwinfo(_value(args, cfg, "hwinfo_stop_command"), str(_value(args, cfg, "hwinfo_executable", "")), process)
    print(f"Unified detail CSV: {detail}")
    print(f"Unified dashboard: {detail.with_suffix('.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
