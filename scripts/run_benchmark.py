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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, help="TOML file containing campaign options.")
    p.add_argument("--suites", "--suite", dest="suites", help="Comma-separated suites or JSON suite IDs.")
    p.add_argument("--backend", choices=["ollama", "llama_cpp", "both"], help="Local backend.")
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
    with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=5) as response:
        return sorted(str(item["name"]) for item in json.loads(response.read()).get("models", []) if item.get("name"))


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
    value = getattr(args, name)
    return value if value is not None else cfg.get(name, default)


def _split(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [item.strip() for part in values for item in str(part).split(",") if item.strip()]


def _ollama_models(patterns: list[str], url: str = "http://127.0.0.1:11434") -> list[str]:
    if not patterns:
        return []
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=5) as response:
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


def _runner_command(suite: str, args: argparse.Namespace, cfg: dict[str, Any], work_dir: Path) -> list[str]:
    if suite in RUNNERS:
        command = [sys.executable, str(Path(__file__).with_name(RUNNERS[suite])), *args.legacy_args]
        return command
    command = [sys.executable, str(Path(__file__).with_name("llm_migration_benchmark.py"))]
    if suite != "migration":
        command += ["--benchmark-id", suite]
    command += ["--backend", _value(args, cfg, "backend", "both"),
               "--output-dir", str(work_dir), "--report-file", str(ROOT / "docs" / "project" / "benchmark_report.md"),
               "--runs", str(_value(args, cfg, "runs", 1)),
               "--resume", ("off" if _value(args, cfg, "run", "resume") == "force"
                            else _value(args, cfg, "resume", "auto"))]
    command += list(args.legacy_args)
    backend = _value(args, cfg, "backend", "both")
    models = _split(_value(args, cfg, "models", [])) + _split(_value(args, cfg, "ollama_models", []))
    if backend in ("ollama", "both") and models:
        resolved = (
            _ollama_models(models, str(_value(args, cfg, "ollama_url", "http://127.0.0.1:11434")))
            if any("*" in m or "?" in m for m in models)
            else models
        )
        for model in resolved:
            command += ["--ollama-model", model]
    for spec in _split(_value(args, cfg, "llama_models", [])):
        command += ["--llama-model", spec]
    if _value(args, cfg, "ollama_url"):
        command += ["--ollama-url", str(_value(args, cfg, "ollama_url"))]
    if _value(args, cfg, "llama_server"):
        command += ["--llama-server", str(_value(args, cfg, "llama_server"))]
    if _value(args, cfg, "timeout_sec") is not None:
        command += ["--timeout-sec", str(_value(args, cfg, "timeout_sec"))]
    runner_args = _value(args, cfg, "runner_args", [])
    if isinstance(runner_args, str):
        runner_args = shlex.split(runner_args)
    if isinstance(runner_args, list):
        command += [str(value) for value in runner_args]
    return command


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        raw = ["--config", str(DEFAULT_CONFIG)] if DEFAULT_CONFIG.exists() else (_gui() or [])
        if not raw:
            return 2
    args = _parser().parse_args(raw)
    config_path = args.config or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    cfg = _config(config_path)
    managed = _model_management(args, cfg)
    if managed is not None:
        return managed
    pause_file = Path(_value(args, cfg, "pause_file", DEFAULT_PAUSE_FILE))
    stop_file = Path(_value(args, cfg, "stop_file", DEFAULT_STOP_FILE))
    lock_file = Path(_value(args, cfg, "lock_file", DEFAULT_LOCK_FILE))
    for path in (pause_file, stop_file, lock_file):
        if not path.is_absolute():
            path = ROOT / path
        if path == pause_file:
            pause_file = path
        elif path == stop_file:
            stop_file = path
        else:
            lock_file = path
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
    if not _acquire_lock(lock_file):
        return 2
    try:
        return _main_locked(args, cfg, pause_file, stop_file, lock_file)
    finally:
        _release_lock(lock_file)


def _main_locked(args: argparse.Namespace, cfg: dict[str, Any], pause_file: Path,
                 stop_file: Path, lock_file: Path) -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
