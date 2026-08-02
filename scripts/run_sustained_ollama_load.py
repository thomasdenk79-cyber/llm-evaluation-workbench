#!/usr/bin/env python3
"""Run a reproducible sustained local Ollama coding workload with 1 Hz telemetry."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Optional

import psutil

from agent_helper_eval import local_lock, ollama_client


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ROOT / "benchmark_results" / "sustained-ollama-load"
MODEL_CHOICES = (
    ("qwen3.6:35b", "Qwen 3.6 35B A3B Q4_K_M"),
    ("qwen3.6:35b-a3b-q5_K_M", "Qwen 3.6 35B A3B Q5_K_M"),
)
BENCHMARK_DURATION_SECONDS = 600.0
WORKLOAD_PROMPT = """You are a senior Python engineer. Produce only Python source code.
Implement a robust, typed `migrate_rows(source_rows, transform, batch_size=500)` generator
for an Oracle-to-PostgreSQL migration. It must validate batch_size, process input in batches,
yield transformed rows, wrap transform errors with the row index, and include docstrings.
Generate a complete implementation with helper functions and no explanation."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def optional_float(value: str) -> Optional[float]:
    value = value.strip()
    if not value or value.upper() in {"N/A", "[N/A]"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def active_power_scheme() -> str:
    completed = subprocess.run(
        ["powercfg", "/getactivescheme"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def nvidia_driver() -> str:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=driver_version",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


class TelemetrySampler:
    fields = (
        "timestamp_utc",
        "elapsed_seconds",
        "cpu_percent",
        "cpu_mhz",
        "ram_used_mb",
        "gpu_percent",
        "gpu_memory_percent",
        "vram_used_mb",
        "gpu_power_watts",
        "gpu_temperature_c",
        "gpu_sm_mhz",
        "gpu_memory_mhz",
    )

    def __init__(self, interval_seconds: float, progress: "ProgressDisplay") -> None:
        self.interval_seconds = interval_seconds
        self.progress = progress
        self.samples: list[dict[str, Optional[float] | str]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = 0.0

    def _gpu_sample(self) -> dict[str, Optional[float]]:
        command = [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,utilization.memory,memory.used,"
            "power.draw,temperature.gpu,clocks.sm,clocks.mem",
            "--format=csv,noheader,nounits",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=min(5.0, self.interval_seconds),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {field: None for field in self.fields[5:]}
        if completed.returncode != 0 or not completed.stdout.strip():
            return {field: None for field in self.fields[5:]}
        values = [optional_float(value) for value in completed.stdout.splitlines()[0].split(",")]
        keys = self.fields[5:]
        return dict(zip(keys, values, strict=True))

    def _sample_once(self) -> None:
        frequency = psutil.cpu_freq()
        row: dict[str, Optional[float] | str] = {
            "timestamp_utc": utc_now(),
            "elapsed_seconds": round(time.perf_counter() - self._started_at, 3),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "cpu_mhz": round(frequency.current, 2) if frequency else None,
            "ram_used_mb": round(psutil.virtual_memory().used / (1024 * 1024), 2),
        }
        row.update(self._gpu_sample())
        self.samples.append(row)
        self.progress.render(row)

    def _run(self) -> None:
        psutil.cpu_percent(interval=None)
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("telemetry sampler is already running")
        self._started_at = time.perf_counter()
        self._thread = threading.Thread(target=self._run, name="sustained-load-telemetry", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 5.0)


class ProgressDisplay:
    """Render a compact, live console view without affecting raw telemetry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._request_count = 0
        self._successful_request_count = 0
        self._tokens_per_second: list[float] = []
        self._last_print_elapsed = -5.0

    def record_request(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._request_count += 1
            if record["status"] == "success":
                self._successful_request_count += 1
                value = record.get("tokens_per_second")
                if isinstance(value, (int, float)):
                    self._tokens_per_second.append(float(value))

    def render(self, row: dict[str, Optional[float] | str]) -> None:
        elapsed = row["elapsed_seconds"]
        if not isinstance(elapsed, (int, float)) or elapsed - self._last_print_elapsed < 5.0:
            return
        self._last_print_elapsed = float(elapsed)
        with self._lock:
            average_tps = (
                sum(self._tokens_per_second) / len(self._tokens_per_second)
                if self._tokens_per_second
                else None
            )
            request_status = f"{self._successful_request_count}/{self._request_count} requests"
        def shown(name: str, unit: str = "") -> str:
            value = row.get(name)
            return f"{value:.1f}{unit}" if isinstance(value, (int, float)) else "N/A"
        print(
            f"[{elapsed:6.0f}s] {request_status} | tok/s avg "
            f"{average_tps:.2f}" if average_tps is not None else
            f"[{elapsed:6.0f}s] {request_status} | tok/s avg N/A",
            flush=True,
        )
        print(
            f"           CPU {shown('cpu_percent', '%')} @ {shown('cpu_mhz', ' MHz')} | "
            f"GPU {shown('gpu_percent', '%')} | VRAM {shown('vram_used_mb', ' MiB')} | "
            f"{shown('gpu_power_watts', ' W')} | {shown('gpu_temperature_c', ' C')} | "
            f"SM {shown('gpu_sm_mhz', ' MHz')}",
            flush=True,
        )


def prompt_choice(question: str, choices: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    print(question)
    for index, (_value, label) in enumerate(choices, start=1):
        print(f"  {index}. {label}")
    while True:
        selected = input("Auswahl: ").strip()
        if selected.isdigit() and 1 <= int(selected) <= len(choices):
            return choices[int(selected) - 1]
        print(f"Bitte eine Zahl zwischen 1 und {len(choices)} eingeben.")


def ollama_is_running() -> bool:
    try:
        ollama_client.ollama_ps(
            ollama_client.DEFAULT_BASE_URL,
            ollama_client.urllib_json_transport,
            timeout_seconds=3.0,
        )
        return True
    except ollama_client.OllamaError:
        return False


def start_ollama_server() -> subprocess.Popen[str]:
    executable = shutil.which("ollama")
    if executable is None:
        raise RuntimeError("Ollama was not found on PATH; start Ollama manually and retry")
    process = subprocess.Popen(
        [executable, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Ollama server exited during startup with code {process.returncode}")
        if ollama_is_running():
            return process
        time.sleep(0.5)
    process.terminate()
    raise RuntimeError("Ollama server did not become ready within 30 seconds")


def stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def numeric_summary(samples: list[dict[str, Optional[float] | str]], field: str) -> dict[str, Optional[float]]:
    values = [float(row[field]) for row in samples if isinstance(row.get(field), (int, float))]
    if not values:
        return {"mean": None, "min": None, "max": None}
    return {
        "mean": round(mean(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


def write_csv(path: Path, samples: list[dict[str, Optional[float] | str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TelemetrySampler.fields)
        writer.writeheader()
        writer.writerows(samples)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    return parser.parse_args()


def main() -> int:
    parse_args()
    model, model_label = prompt_choice("Welches Modell soll zehn Minuten laufen?", MODEL_CHOICES)
    cooler, cooler_label = prompt_choice(
        "Notebook-Kuehler-Zustand fuer diesen Lauf:",
        (("cooler-off", "Kuehler aus"), ("cooler-on", "Kuehler an")),
    )
    print(f"Start: {model_label}; {cooler_label}; Dauer: 10 Minuten.")

    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}-{cooler}"
    output_dir = DEFAULT_OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    requests_path = output_dir / "requests.jsonl"
    progress = ProgressDisplay()
    sampler = TelemetrySampler(1.0, progress)
    options = ollama_client.GenerateOptions(
        temperature=0.2,
        top_p=0.8,
        top_k=20,
        repeat_penalty=1.05,
        num_ctx=16384,
        num_predict=512,
    )
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "started_at_utc": utc_now(),
        "model": model,
        "model_label": model_label,
        "duration_seconds_target": BENCHMARK_DURATION_SECONDS,
        "sample_interval_seconds": 1.0,
        "label": cooler,
        "condition_note": f"interactive run; AC expected; {cooler_label}",
        "power_scheme": active_power_scheme(),
        "nvidia_driver": nvidia_driver(),
        "options": options.to_json(),
    }
    deadline = time.perf_counter() + BENCHMARK_DURATION_SECONDS
    request_records: list[dict[str, Any]] = []
    unload_ok: Optional[bool] = None
    started_server: Optional[subprocess.Popen[str]] = None

    try:
        if not ollama_is_running():
            print("Ollama läuft nicht; starte lokalen Ollama-Server.", flush=True)
            started_server = start_ollama_server()
        else:
            print("Ollama läuft bereits; der bestehende Server bleibt nach dem Lauf aktiv.", flush=True)
        with local_lock.local_model_slot(
            None,
            "ollama",
            model,
            wait_seconds=3600.0,
        ):
            loaded = ollama_client.ollama_ps(
                ollama_client.DEFAULT_BASE_URL,
                ollama_client.urllib_json_transport,
            )
            ollama_client.check_single_model_preflight(loaded, model, allow_reuse=False)
            sampler.start()
            with requests_path.open("w", encoding="utf-8") as request_file:
                while time.perf_counter() < deadline:
                    request_number = len(request_records) + 1
                    try:
                        result = ollama_client.ollama_generate(
                            ollama_client.DEFAULT_BASE_URL,
                            model,
                            WORKLOAD_PROMPT,
                            options,
                            ollama_client.urllib_stream_transport,
                            timeout_seconds=300.0,
                            keep_alive="-1",
                        )
                        record = {
                            "request": request_number,
                            "started_at_utc": utc_now(),
                            "status": "success",
                            "wall_seconds": result.wall_seconds,
                            "ttft_seconds": result.ttft_seconds,
                            "load_seconds": ollama_client.ns_to_seconds(result.load_duration_ns),
                            "output_tokens": result.eval_count,
                            "generation_seconds": ollama_client.ns_to_seconds(result.eval_duration_ns),
                            "tokens_per_second": ollama_client.tokens_per_second(
                                result.eval_count, result.eval_duration_ns
                            ),
                        }
                    except ollama_client.OllamaError as error:
                        record = {
                            "request": request_number,
                            "started_at_utc": utc_now(),
                            "status": "error",
                            "error": str(error),
                        }
                    request_records.append(record)
                    progress.record_request(record)
                    request_file.write(json.dumps(record, sort_keys=True) + "\n")
                    request_file.flush()
    finally:
        sampler.stop()
        unload_ok = ollama_client.ollama_unload(
            ollama_client.DEFAULT_BASE_URL,
            model,
            ollama_client.urllib_json_transport,
        )
        if started_server is not None:
            stop_server(started_server)

    write_csv(output_dir / "telemetry.csv", sampler.samples)
    successful_requests = [row for row in request_records if row["status"] == "success"]
    metadata.update(
        {
            "ended_at_utc": utc_now(),
            "unload_ok": unload_ok,
            "request_count": len(request_records),
            "successful_request_count": len(successful_requests),
            "tokens_per_second": numeric_summary(successful_requests, "tokens_per_second"),
            "wall_seconds": numeric_summary(successful_requests, "wall_seconds"),
            "telemetry_summary": {
                field: numeric_summary(sampler.samples, field)
                for field in TelemetrySampler.fields[2:]
            },
        }
    )
    (output_dir / "summary.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"run_id: {run_id}")
    print(f"output_dir: {output_dir}")
    print(f"requests: {len(successful_requests)}/{len(request_records)} successful")
    print(f"unload_ok: {unload_ok}")
    return 0 if len(successful_requests) == len(request_records) else 1


if __name__ == "__main__":
    sys.exit(main())
