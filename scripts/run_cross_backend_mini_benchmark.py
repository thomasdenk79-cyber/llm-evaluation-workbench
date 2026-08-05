"""Run the fixed mini-coder gate against Ollama and llama.cpp sequentially.

This is intentionally a small operational runner. It appends one row after
each completed model so the CSV can be followed with ``Get-Content -Wait``.
Only one local model is loaded at a time.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agent_helper_eval.mini_task import PROMPT, run_mini_task_tests


CSV_FIELDS = [
    "timestamp", "backend", "model", "configuration", "tests_total",
    "tests_passed", "quality_percent", "elapsed_seconds", "tokens_per_second",
    "ttft_seconds", "status", "error", "notes", "sample_id", "sample_name",
    "score", "errors", "rating", "agent_suitability", "io_read", "io_write",
]


def post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def stream_ollama(
    model: str,
    base_url: str,
    timeout: float,
    progress_path: Path,
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": model,
        "prompt": PROMPT,
        "stream": True,
        "keep_alive": 0,
        "options": {
            "temperature": 0.0,
            "seed": 20260803,
            "top_k": 1,
            "top_p": 1.0,
            "repeat_penalty": 1.1,
            "num_ctx": 4096,
            "num_predict": 512,
        },
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    chunks: list[str] = []
    final: dict[str, Any] = {}
    started = time.monotonic()
    last_chunk = started
    with urllib.request.urlopen(request, timeout=min(timeout, 90)) as response:
        while True:
            try:
                raw_line = response.readline()
            except (TimeoutError, socket.timeout) as exc:
                if time.monotonic() - last_chunk >= 300:
                    raise TimeoutError(
                        f"Ollama produced no token for 300 seconds: {model}"
                    ) from exc
                continue
            if not raw_line:
                break
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            item = json.loads(line)
            chunk = item.get("response", "")
            chunks.append(chunk)
            last_chunk = time.monotonic()
            if int(last_chunk - started) % 30 < 2:
                append_progress(progress_path, f"ollama {model}: {len(''.join(chunks))} chars")
            if item.get("done"):
                final = item
    return "".join(chunks), final


def append_progress(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
        handle.flush()


def wait_server(base_url: str, timeout: float, process: subprocess.Popen[str] | None = None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"llama.cpp server exited before becoming ready (code {process.returncode})"
            )
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=3) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise RuntimeError(f"llama.cpp server did not become ready: {base_url}")


def run_llama(
    alias: str,
    model_path: str,
    server_path: str,
    output_dir: Path,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    port = 18080
    server_url = f"http://127.0.0.1:{port}"
    command = [
        server_path, "-m", model_path, "--host", "127.0.0.1", "--port", str(port),
        "-ngl", "999", "-fa", "1", "-ctk", "q8_0", "-ctv", "q8_0",
        "-t", "16", "-c", "4096", "-b", "2048", "-ub", "512", "-np", "1",
        "-ot", r"blk\.[0-9]+\.ffn_(up|down|gate)_exps\.weight=CPU",
    ]
    log_path = output_dir / f"{alias}.llama-server.log"
    environment = os.environ.copy()
    cuda_root = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3")
    environment["PATH"] = os.pathsep.join(
        [str(cuda_root / "bin" / "x64"), str(cuda_root / "bin"), environment["PATH"]]
    )
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    try:
        wait_server(server_url, timeout, process)
        started = time.perf_counter()
        result = post_json(
            f"{server_url}/completion",
            {
                "prompt": PROMPT,
                "n_predict": 512,
                "temperature": 0.0,
                "seed": 20260803,
                "top_k": 1,
                "top_p": 1.0,
                "repeat_penalty": 1.1,
                "cache_prompt": True,
            },
            timeout,
        )
        result["_client_elapsed_seconds"] = time.perf_counter() - started
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        if "ggml_cuda_init: failed" in log_text:
            raise RuntimeError(
                "ik_llama.cpp CUDA initialization failed; CPU fallback is not valid GPU evidence"
            )
        return result.get("content", ""), result
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)


def metrics(backend: str, model: str, configuration: str, response: str,
            raw: dict[str, Any], work_dir: Path, elapsed: float, error: str = "") -> dict[str, Any]:
    if error:
        return {
            "backend": backend, "model": model, "configuration": configuration,
            "tests_total": 0, "tests_passed": 0, "quality_percent": 0,
            "elapsed_seconds": f"{elapsed:.3f}", "tokens_per_second": "",
            "ttft_seconds": "", "status": "ERROR", "error": error,
            "notes": "generation or server failure",
            "sample_id": f"{backend}:{model}:mini-coder-gate",
            "sample_name": "mini-coder-gate",
            "score": 0,
            "errors": error,
            "rating": 0,
            "agent_suitability": "not suitable",
            "io_read": "",
            "io_write": "",
        }
    result = run_mini_task_tests(response, work_dir)
    total = result.tests_total
    passed = result.tests_passed
    tps = None
    if backend == "ollama":
        duration = raw.get("eval_duration") or 0
        count = raw.get("eval_count") or 0
        tps = count / (duration / 1e9) if duration and count else None
    else:
        timings = raw.get("timings") or {}
        tps = timings.get("predicted_per_second")
    status = "PASSED" if total and passed == total else "NOT-USABLE"
    return {
        "backend": backend, "model": model, "configuration": configuration,
        "tests_total": total, "tests_passed": passed,
        "quality_percent": f"{result.deterministic_score:.1f}",
        "elapsed_seconds": f"{elapsed:.3f}",
        "tokens_per_second": f"{tps:.3f}" if tps is not None else "",
        "ttft_seconds": "",
        "status": status,
        "error": "; ".join(result.unsafe_findings + result.placeholder_findings),
        "notes": "fixed normalize(records) mini-coder gate",
        "sample_id": f"{backend}:{model}:mini-coder-gate",
        "sample_name": "mini-coder-gate",
        "score": f"{result.deterministic_score:.1f}",
        "errors": "; ".join(result.unsafe_findings + result.placeholder_findings),
        "rating": f"{result.deterministic_score:.1f}",
        "agent_suitability": "suitable" if status == "PASSED" else "not suitable",
        "io_read": "",
        "io_write": "",
    }


def append_row(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row})
        handle.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ollama-model", action="append", default=[])
    parser.add_argument("--llama-model", action="append", default=[],
                        help="alias=absolute-GGUF-path")
    parser.add_argument("--server", default=r"C:\Users\z000g9hu\llama.cpp-ik\build-cuda-v133-installed\bin\Release\llama-server.exe")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=float, default=3600,
                        help="maximum seconds per model; default is one hour")
    args = parser.parse_args()
    progress_path = args.output.parent / "mini_progress.log"

    for model in args.ollama_model:
        started = time.perf_counter()
        append_progress(progress_path, f"START ollama {model} timeout={args.timeout:.0f}s")
        try:
            response, raw = stream_ollama(model, args.ollama_url, args.timeout, progress_path)
            row = metrics("ollama", model, "ollama-default-q8kv", response, raw,
                          args.output.parent / "work" / "ollama" / model.replace(":", "_"),
                          time.perf_counter() - started)
        except Exception as exc:
            row = metrics("ollama", model, "ollama-default-q8kv", "", {}, Path("."),
                          time.perf_counter() - started, str(exc))
        append_row(args.output, row)
        append_progress(progress_path, f"DONE ollama {model} status={row['status']}")

    for entry in args.llama_model:
        if "=" not in entry:
            raise SystemExit(f"invalid --llama-model (expected alias=path): {entry}")
        alias, model_path = entry.split("=", 1)
        started = time.perf_counter()
        append_progress(progress_path, f"START llama.cpp {alias} timeout={args.timeout:.0f}s")
        try:
            response, raw = run_llama(alias, model_path, args.server, args.output.parent, args.timeout)
            row = metrics("llama.cpp", alias, "ik-cuda-ngl999-fa1-q8kv-cpu-experts",
                          response, raw, args.output.parent / "work" / "llama" / alias,
                          time.perf_counter() - started)
        except Exception as exc:
            row = metrics("llama.cpp", alias, "ik-cuda-ngl999-fa1-q8kv-cpu-experts",
                          "", {}, Path("."), time.perf_counter() - started, str(exc))
        append_row(args.output, row)
        append_progress(progress_path, f"DONE llama.cpp {alias} status={row['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
