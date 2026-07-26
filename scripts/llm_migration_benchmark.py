#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Dict, List, Optional, Tuple

import psutil

BENCH_TASKS = [
    {
        "id": "ddl_conversion",
        "title": "Oracle DDL to PostgreSQL",
        "prompt": (
            "Convert this Oracle DDL to PostgreSQL 16 compatible SQL. "
            "Return SQL only.\n\n"
            "CREATE TABLE ORDERS (\n"
            "  ID NUMBER(19) PRIMARY KEY,\n"
            "  ORDER_NO VARCHAR2(40) NOT NULL,\n"
            "  AMOUNT NUMBER(15,2) DEFAULT 0 NOT NULL,\n"
            "  CREATED_AT DATE DEFAULT SYSDATE,\n"
            "  PAYLOAD CLOB\n"
            ");\n"
            "CREATE INDEX IX_ORDERS_CREATED_AT ON ORDERS(CREATED_AT);\n"
        ),
        "required_keywords": [
            "create table",
            ["numeric", "integer", "bigint"],
            ["varchar", "character varying"],
            ["timestamp", "current_timestamp", "now()"],
            "text",
            "create index",
            "primary key",
            "not null",
        ],
    },
    {
        "id": "plsql_to_plpgsql",
        "title": "PL/SQL to PLpgSQL",
        "prompt": (
            "Convert this Oracle procedure to PostgreSQL PL/pgSQL. "
            "Return only the function/procedure SQL.\n\n"
            "CREATE OR REPLACE PROCEDURE set_order_total(p_id IN NUMBER, p_total IN NUMBER) AS\n"
            "BEGIN\n"
            "  UPDATE orders SET amount = NVL(p_total, 0), created_at = SYSDATE WHERE id = p_id;\n"
            "  IF SQL%ROWCOUNT = 0 THEN\n"
            "    INSERT INTO orders(id, order_no, amount, created_at) VALUES (p_id, 'NEW', NVL(p_total,0), SYSDATE);\n"
            "  END IF;\n"
            "END;\n"
            "/\n"
        ),
        "required_keywords": [
            "create or replace",
            ["plpgsql", "language plpgsql"],
            ["coalesce", "case when"],
            ["current_timestamp", "now()"],
            "if",
            "update orders",
            "insert into orders",
        ],
    },
    {
        "id": "validation_query",
        "title": "Migration validation SQL",
        "prompt": (
            "Create PostgreSQL SQL to validate Oracle->PostgreSQL migration for schema app_mig. "
            "Need: rowcount check per table, checksum-like aggregate, and list mismatches. "
            "Return SQL only."
        ),
        "required_keywords": [
            ["information_schema.tables", "pg_catalog.pg_tables"],
            "count(*)",
            ["md5", "sha", "hash"],
            "group by",
            "join",
            "mismatch",
        ],
    },
]


@dataclass
class BenchResult:
    backend: str
    model: str
    case_id: str
    case_title: str
    run: int
    wall_ms: float
    prompt_tokens: Optional[int]
    output_tokens: Optional[int]
    output_tps: Optional[float]
    quality_score: float
    keyword_hits: int
    keyword_total: int
    avg_cpu_pct: Optional[float]
    max_cpu_pct: Optional[float]
    avg_mem_pct: Optional[float]
    max_mem_pct: Optional[float]
    avg_gpu_pct: Optional[float]
    max_gpu_pct: Optional[float]
    avg_vram_used_mb: Optional[float]
    max_vram_used_mb: Optional[float]
    cpu_time_sec: float
    output_preview: str
    error: str


class SystemMonitor:
    def __init__(self, sample_interval_sec: float = 0.5):
        self.sample_interval_sec = sample_interval_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.cpu_samples: List[float] = []
        self.mem_samples: List[float] = []
        self.gpu_samples: List[float] = []
        self.vram_samples: List[float] = []
        self.nvidia_smi_available = self._check_nvidia_smi()

    @staticmethod
    def _check_nvidia_smi() -> bool:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            return r.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _query_gpu() -> Tuple[Optional[float], Optional[float]]:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if r.returncode != 0:
                return None, None
            lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
            if not lines:
                return None, None
            gpu_utils = []
            vram_used = []
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    gpu_utils.append(float(parts[0]))
                    vram_used.append(float(parts[1]))
            if not gpu_utils:
                return None, None
            return mean(gpu_utils), mean(vram_used)
        except Exception:
            return None, None

    def _run(self) -> None:
        psutil.cpu_percent(interval=None)
        while not self._stop.is_set():
            cpu = psutil.cpu_percent(interval=self.sample_interval_sec)
            mem = psutil.virtual_memory().percent
            self.cpu_samples.append(cpu)
            self.mem_samples.append(mem)
            if self.nvidia_smi_available:
                gpu, vram = self._query_gpu()
                if gpu is not None:
                    self.gpu_samples.append(gpu)
                if vram is not None:
                    self.vram_samples.append(vram)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def stats(self) -> Dict[str, Optional[float]]:
        return {
            "avg_cpu_pct": mean(self.cpu_samples) if self.cpu_samples else None,
            "max_cpu_pct": max(self.cpu_samples) if self.cpu_samples else None,
            "avg_mem_pct": mean(self.mem_samples) if self.mem_samples else None,
            "max_mem_pct": max(self.mem_samples) if self.mem_samples else None,
            "avg_gpu_pct": mean(self.gpu_samples) if self.gpu_samples else None,
            "max_gpu_pct": max(self.gpu_samples) if self.gpu_samples else None,
            "avg_vram_used_mb": mean(self.vram_samples) if self.vram_samples else None,
            "max_vram_used_mb": max(self.vram_samples) if self.vram_samples else None,
        }


SIEMENS_DEFAULT_MODELS = [
    "deepseek-v4-flash",
    "gpt-oss-120b",
    "qwen-3.6-27b",
    "Mistral-Small-24B-Instruct-2501-FP8-dynamic",
    "ministral-3-14b-instruct-2512",
]

# Qwen: disable thinking via chat_template_kwargs to avoid consuming all tokens in reasoning
SIEMENS_NO_THINKING_MODELS = {"qwen-3.6-27b"}

# DeepSeek Flash is a reasoning model — uses 'reasoning' field internally.
# Needs higher token budget; content extracted via reasoning fallback if needed.
SIEMENS_REASONING_MODELS = {"deepseek-v4-flash"}

# Per-model token overrides for cloud models that need more budget
SIEMENS_MODEL_MAX_TOKENS: Dict[str, int] = {
    "deepseek-v4-flash": 1500,
    "gpt-oss-120b": 600,
}


def load_siemens_token(args: argparse.Namespace) -> Optional[str]:
    """Load Siemens API token from env var, CLI arg, or token file."""
    token = os.environ.get("SIEMENS_LLM_TOKEN", "").strip()
    if token:
        return token
    if getattr(args, "siemens_token", None):
        return args.siemens_token.strip()
    candidates = []
    if getattr(args, "siemens_token_file", None):
        candidates.append(args.siemens_token_file)
    candidates += [
        os.path.join(os.path.expanduser("~"), ".siemens_llm_token"),
        os.path.join(os.path.expanduser("~"), "OneDrive - Siemens AG",
                     "tools", "myconfigfiles", "code.siemens.com_api_ai_token.txt"),
    ]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                # Token file may contain extra lines (e.g. a URL) — take only the SIAK- line
                for line in fh:
                    t = line.strip()
                    if t.startswith("SIAK-"):
                        return t
                    if t and not t.startswith("http"):
                        # Non-URL, non-empty first line as fallback
                        return t
        except OSError:
            continue
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Small LLM benchmark for Oracle->PostgreSQL migration tasks."
    )
    parser.add_argument(
        "--backend",
        choices=["ollama", "llama_cpp", "siemens", "both", "all"],
        default="both",
        help="Backend to benchmark. 'both'=ollama+llama_cpp, 'all'=ollama+llama_cpp+siemens.",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://127.0.0.1:11434/api/generate",
        help="Ollama generate API URL.",
    )
    parser.add_argument(
        "--ollama-model",
        action="append",
        dest="ollama_models",
        default=[],
        help="Ollama model name (repeatable). If not set, auto-detects qwen3.6 27/35 q4 and gpt-oss.",
    )
    parser.add_argument(
        "--llama-cli",
        default="llama-cli",
        help="Path to llama-cli executable.",
    )
    parser.add_argument(
        "--llama-server",
        default="",
        help="Path to llama-server executable (preferred on this machine).",
    )
    parser.add_argument(
        "--llama-model",
        action="append",
        dest="llama_models",
        default=[],
        help="llama.cpp model in format NAME=PATH (repeatable).",
    )
    parser.add_argument("--runs", type=int, default=1, help="Runs per case.")
    parser.add_argument(
        "--siemens-url",
        default="https://api.siemens.com/llm/v1",
        help="Siemens LLM API base URL (OpenAI-compatible).",
    )
    parser.add_argument(
        "--siemens-model",
        action="append",
        dest="siemens_models",
        default=[],
        help="Siemens model name (repeatable). Default: all three Siemens models.",
    )
    parser.add_argument(
        "--siemens-token",
        default="",
        help="Siemens API token. Prefer env var SIEMENS_LLM_TOKEN instead.",
    )
    parser.add_argument(
        "--siemens-token-file",
        default="",
        help="Path to file containing the Siemens API token.",
    )
    parser.add_argument(
        "--siemens-workers",
        type=int,
        default=5,
        help="Parallel workers for Siemens cloud API (default: 5 = one per model).",
    )
    parser.add_argument("--max-tokens", type=int, default=None, help="Max output tokens. None = no limit (model decides). Set only if you want to cap output length for speed.")
    parser.add_argument("--ctx-size", type=int, default=2048, help="Context size.")
    parser.add_argument("--threads", type=int, default=max(os.cpu_count() - 2, 1), help="CPU threads.")
    parser.add_argument("--ngl", type=int, default=0, help="llama.cpp GPU layers. 0 = CPU only (low VRAM safe).")
    parser.add_argument("--seed", type=int, default=42, help="Generation seed for reproducibility.")
    parser.add_argument("--temp", type=float, default=0.1, help="Sampling temperature.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p.")
    parser.add_argument("--repeat-penalty", type=float, default=1.05, help="Repeat penalty.")
    parser.add_argument("--llama-batch-size", type=int, default=1024, help="llama.cpp server batch size.")
    parser.add_argument("--llama-ubatch-size", type=int, default=256, help="llama.cpp server ubatch size.")
    parser.add_argument("--llama-fit-target-mib", type=int, default=1536, help="VRAM safety margin for --fit.")
    parser.add_argument(
        "--llama-reasoning",
        choices=["off", "on", "auto"],
        default="off",
        help="llama.cpp server reasoning mode.",
    )
    parser.add_argument("--timeout-sec", type=int, default=900, help="Timeout per generation call.")
    parser.add_argument(
        "--output-dir",
        default="benchmark_results",
        help="Directory for CSV/JSON results.",
    )
    return parser.parse_args()


def normalize(s: str) -> str:
    return s.lower().strip()


def clean_model_output(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    cleaned = re.sub(r"(?is)<think>.*?</think>", " ", cleaned)
    cleaned = cleaned.replace("```sql", "").replace("```", "")
    cleaned = cleaned.strip()
    sql_start = re.search(r"(?is)\b(create|with|select|insert|update|delete|do)\b", cleaned)
    if sql_start:
        cleaned = cleaned[sql_start.start():]
    return cleaned.strip()


def keyword_hit(lowered: str, keyword_spec) -> bool:
    if isinstance(keyword_spec, str):
        return keyword_spec in lowered
    return any(str(item) in lowered for item in keyword_spec)


def score_output(text: str, required_keywords: List[object]) -> Tuple[float, int]:
    lowered = normalize(clean_model_output(text))
    hits = sum(1 for kw in required_keywords if keyword_hit(lowered, kw))
    base = (hits / len(required_keywords)) * 100.0
    if len(lowered) < 120:
        base -= 8.0
    return max(base, 0.0), hits


def run_command(command: List[str], timeout_sec: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )


def list_ollama_models() -> List[str]:
    result = run_command(["ollama", "list"], timeout_sec=20)
    if result.returncode != 0:
        return []
    lines = [line.rstrip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return []
    out = []
    for line in lines[1:]:
        parts = line.split()
        if parts:
            out.append(parts[0])
    return out


def find_model(candidates: List[str], include_terms: List[str]) -> Optional[str]:
    for name in candidates:
        lowered = name.lower()
        if all(term in lowered for term in include_terms):
            return name
    return None


def default_ollama_models() -> List[str]:
    available = list_ollama_models()
    wanted_patterns = [
        ["qwen3.6", "27b", "q4"],
        ["qwen3.6", "35b", "q4"],
        ["gpt-oss"],
    ]
    models = []
    for pattern in wanted_patterns:
        hit = find_model(available, pattern)
        if hit:
            models.append(hit)
    return models


def ollama_generate(
    api_url: str,
    model: str,
    prompt: str,
    args: argparse.Namespace,
) -> Dict[str, object]:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "seed": args.seed,
            "temperature": args.temp,
            "top_p": args.top_p,
            "repeat_penalty": args.repeat_penalty,
            "num_ctx": args.ctx_size,
            "num_predict": args.max_tokens if args.max_tokens is not None else -1,
            "num_thread": args.threads,
        },
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    attempts = 3
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=args.timeout_sec) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code >= 500 and attempt < attempts:
                time.sleep(2 * attempt)
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1.0)
                continue
            raise
    raise RuntimeError(f"Ollama generate failed after retries: {last_error}")


def run_ollama_case(
    model: str,
    case: Dict[str, object],
    run_id: int,
    args: argparse.Namespace,
) -> BenchResult:
    start = time.perf_counter()
    cpu_start = time.process_time()
    monitor = SystemMonitor()
    monitor.start()
    try:
        response = ollama_generate(args.ollama_url, model, case["prompt"], args)
        wall_ms = (time.perf_counter() - start) * 1000.0
        cpu_time = time.process_time() - cpu_start
        monitor.stop()
        m = monitor.stats()
        text = response.get("response", "")
        if not text and response.get("thinking"):
            text = response.get("thinking", "")
        quality, hits = score_output(text, case["required_keywords"])
        cleaned_text = clean_model_output(text)

        out_tokens = response.get("eval_count")
        prompt_tokens = response.get("prompt_eval_count")
        eval_duration_ns = response.get("eval_duration")
        output_tps = None
        if isinstance(out_tokens, int) and isinstance(eval_duration_ns, int) and eval_duration_ns > 0:
            output_tps = out_tokens / (eval_duration_ns / 1_000_000_000.0)

        return BenchResult(
            backend="ollama",
            model=model,
            case_id=case["id"],
            case_title=case["title"],
            run=run_id,
            wall_ms=wall_ms,
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            output_tokens=out_tokens if isinstance(out_tokens, int) else None,
            output_tps=output_tps,
            quality_score=quality,
            keyword_hits=hits,
            keyword_total=len(case["required_keywords"]),
            avg_cpu_pct=m["avg_cpu_pct"],
            max_cpu_pct=m["max_cpu_pct"],
            avg_mem_pct=m["avg_mem_pct"],
            max_mem_pct=m["max_mem_pct"],
            avg_gpu_pct=m["avg_gpu_pct"],
            max_gpu_pct=m["max_gpu_pct"],
            avg_vram_used_mb=m["avg_vram_used_mb"],
            max_vram_used_mb=m["max_vram_used_mb"],
            cpu_time_sec=cpu_time,
            output_preview=cleaned_text[:220].replace("\n", "\\n"),
            error="",
        )
    except urllib.error.URLError as exc:
        err = f"Ollama connection failed: {exc}"
    except Exception as exc:
        err = f"Ollama error: {exc}"

    monitor.stop()
    m = monitor.stats()
    return BenchResult(
        backend="ollama",
        model=model,
        case_id=case["id"],
        case_title=case["title"],
        run=run_id,
        wall_ms=(time.perf_counter() - start) * 1000.0,
        prompt_tokens=None,
        output_tokens=None,
        output_tps=None,
        quality_score=0.0,
        keyword_hits=0,
        keyword_total=len(case["required_keywords"]),
        avg_cpu_pct=m["avg_cpu_pct"],
        max_cpu_pct=m["max_cpu_pct"],
        avg_mem_pct=m["avg_mem_pct"],
        max_mem_pct=m["max_mem_pct"],
        avg_gpu_pct=m["avg_gpu_pct"],
        max_gpu_pct=m["max_gpu_pct"],
        avg_vram_used_mb=m["avg_vram_used_mb"],
        max_vram_used_mb=m["max_vram_used_mb"],
        cpu_time_sec=time.process_time() - cpu_start,
        output_preview="",
        error=err,
    )


def parse_llama_model_specs(specs: List[str]) -> List[Tuple[str, str]]:
    models = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"Invalid --llama-model '{spec}'. Use NAME=PATH.")
        name, path = spec.split("=", 1)
        name = name.strip()
        path = path.strip().strip('"')
        if not name or not path:
            raise ValueError(f"Invalid --llama-model '{spec}'. Use NAME=PATH.")
        models.append((name, path))
    return models


def parse_llama_metrics(stderr: str) -> Tuple[Optional[int], Optional[int], Optional[float]]:
    prompt_tokens = None
    output_tokens = None
    tps = None

    prompt_match = re.search(r"prompt eval time\s*=\s*[\d.]+\s*ms\s*/\s*(\d+)\s*tokens", stderr, flags=re.IGNORECASE)
    output_match = re.search(
        r"eval time\s*=\s*[\d.]+\s*ms\s*/\s*(\d+)\s*runs.*?([\d.]+)\s*tokens per second",
        stderr,
        flags=re.IGNORECASE,
    )
    if prompt_match:
        prompt_tokens = int(prompt_match.group(1))
    if output_match:
        output_tokens = int(output_match.group(1))
        tps = float(output_match.group(2))
    return prompt_tokens, output_tokens, tps


def run_llama_cpp_case(
    model_name: str,
    model_path: str,
    case: Dict[str, object],
    run_id: int,
    args: argparse.Namespace,
) -> BenchResult:
    start = time.perf_counter()
    cpu_start = time.process_time()
    monitor = SystemMonitor()
    monitor.start()
    if not os.path.exists(model_path):
        monitor.stop()
        m = monitor.stats()
        return BenchResult(
            backend="llama_cpp",
            model=model_name,
            case_id=case["id"],
            case_title=case["title"],
            run=run_id,
            wall_ms=0.0,
            prompt_tokens=None,
            output_tokens=None,
            output_tps=None,
            quality_score=0.0,
            keyword_hits=0,
            keyword_total=len(case["required_keywords"]),
            avg_cpu_pct=m["avg_cpu_pct"],
            max_cpu_pct=m["max_cpu_pct"],
            avg_mem_pct=m["avg_mem_pct"],
            max_mem_pct=m["max_mem_pct"],
            avg_gpu_pct=m["avg_gpu_pct"],
            max_gpu_pct=m["max_gpu_pct"],
            avg_vram_used_mb=m["avg_vram_used_mb"],
            max_vram_used_mb=m["max_vram_used_mb"],
            cpu_time_sec=time.process_time() - cpu_start,
            output_preview="",
            error=f"Model file not found: {model_path}",
        )

    command = [
        args.llama_cli,
        "-m",
        model_path,
        "-p",
        case["prompt"],
        "-n",
        str(args.max_tokens),
        "--ctx-size",
        str(args.ctx_size),
        "--temp",
        str(args.temp),
        "--top-p",
        str(args.top_p),
        "--repeat-penalty",
        str(args.repeat_penalty),
        "--threads",
        str(args.threads),
        "--n-gpu-layers",
        str(args.ngl),
        "--no-display-prompt",
    ]
    try:
        result = run_command(command, timeout_sec=args.timeout_sec)
        wall_ms = (time.perf_counter() - start) * 1000.0

        if result.returncode != 0:
            err_out = (result.stderr or result.stdout).strip()
            monitor.stop()
            m = monitor.stats()
            return BenchResult(
                backend="llama_cpp",
                model=model_name,
                case_id=case["id"],
                case_title=case["title"],
                run=run_id,
                wall_ms=wall_ms,
                prompt_tokens=None,
                output_tokens=None,
                output_tps=None,
                quality_score=0.0,
                keyword_hits=0,
                keyword_total=len(case["required_keywords"]),
                avg_cpu_pct=m["avg_cpu_pct"],
                max_cpu_pct=m["max_cpu_pct"],
                avg_mem_pct=m["avg_mem_pct"],
                max_mem_pct=m["max_mem_pct"],
                avg_gpu_pct=m["avg_gpu_pct"],
                max_gpu_pct=m["max_gpu_pct"],
                avg_vram_used_mb=m["avg_vram_used_mb"],
                max_vram_used_mb=m["max_vram_used_mb"],
                cpu_time_sec=time.process_time() - cpu_start,
                output_preview=clean_model_output(result.stdout or "")[:220].replace("\n", "\\n"),
                error=f"llama-cli failed ({result.returncode}): {err_out[:300]}",
            )

        output_text = clean_model_output((result.stdout or "").strip())
        quality, hits = score_output(output_text, case["required_keywords"])
        prompt_tokens, output_tokens, output_tps = parse_llama_metrics(result.stderr or "")
        monitor.stop()
        m = monitor.stats()
        return BenchResult(
            backend="llama_cpp",
            model=model_name,
            case_id=case["id"],
            case_title=case["title"],
            run=run_id,
            wall_ms=wall_ms,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            output_tps=output_tps,
            quality_score=quality,
            keyword_hits=hits,
            keyword_total=len(case["required_keywords"]),
            avg_cpu_pct=m["avg_cpu_pct"],
            max_cpu_pct=m["max_cpu_pct"],
            avg_mem_pct=m["avg_mem_pct"],
            max_mem_pct=m["max_mem_pct"],
            avg_gpu_pct=m["avg_gpu_pct"],
            max_gpu_pct=m["max_gpu_pct"],
            avg_vram_used_mb=m["avg_vram_used_mb"],
            max_vram_used_mb=m["max_vram_used_mb"],
            cpu_time_sec=time.process_time() - cpu_start,
            output_preview=output_text[:220].replace("\n", "\\n"),
            error="",
        )
    except FileNotFoundError:
        err = f"llama-cli not found: {args.llama_cli}"
    except subprocess.TimeoutExpired:
        err = f"llama-cli timeout after {args.timeout_sec}s"
    except Exception as exc:
        err = f"llama.cpp error: {exc}"

    monitor.stop()
    m = monitor.stats()
    return BenchResult(
        backend="llama_cpp",
        model=model_name,
        case_id=case["id"],
        case_title=case["title"],
        run=run_id,
        wall_ms=(time.perf_counter() - start) * 1000.0,
        prompt_tokens=None,
        output_tokens=None,
        output_tps=None,
        quality_score=0.0,
        keyword_hits=0,
        keyword_total=len(case["required_keywords"]),
        avg_cpu_pct=m["avg_cpu_pct"],
        max_cpu_pct=m["max_cpu_pct"],
        avg_mem_pct=m["avg_mem_pct"],
        max_mem_pct=m["max_mem_pct"],
        avg_gpu_pct=m["avg_gpu_pct"],
        max_gpu_pct=m["max_gpu_pct"],
        avg_vram_used_mb=m["avg_vram_used_mb"],
        max_vram_used_mb=m["max_vram_used_mb"],
        cpu_time_sec=time.process_time() - cpu_start,
        output_preview="",
        error=err,
    )


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server_ready(port: int, timeout_sec: int) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as _:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def run_llama_server_model(
    server_exe: str,
    model_name: str,
    model_path: str,
    args: argparse.Namespace,
) -> List[BenchResult]:
    results: List[BenchResult] = []
    if not os.path.exists(model_path):
        for case in BENCH_TASKS:
            for run_id in range(1, args.runs + 1):
                results.append(
                    BenchResult(
                        backend="llama_cpp",
                        model=model_name,
                        case_id=case["id"],
                        case_title=case["title"],
                        run=run_id,
                        wall_ms=0.0,
                        prompt_tokens=None,
                        output_tokens=None,
                        output_tps=None,
                        quality_score=0.0,
                        keyword_hits=0,
                        keyword_total=len(case["required_keywords"]),
                        avg_cpu_pct=None,
                        max_cpu_pct=None,
                        avg_mem_pct=None,
                        max_mem_pct=None,
                        avg_gpu_pct=None,
                        max_gpu_pct=None,
                        avg_vram_used_mb=None,
                        max_vram_used_mb=None,
                        cpu_time_sec=0.0,
                        output_preview="",
                        error=f"Model file not found: {model_path}",
                    )
                )
        return results

    port = pick_free_port()
    server_args = [
        server_exe,
        "-m",
        model_path,
        "-a",
        model_name,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-c",
        str(args.ctx_size),
        "--threads",
        str(args.threads),
        "--n-gpu-layers",
        str(args.ngl),
        "--reasoning",
        args.llama_reasoning,
        "--reasoning-format",
        "none",
        "--fit",
        "on",
        "--fit-target",
        str(args.llama_fit_target_mib),
        "-np",
        "1",
        "-b",
        str(args.llama_batch_size),
        "-ub",
        str(args.llama_ubatch_size),
        "--no-webui",
    ]

    try:
        proc = subprocess.Popen(server_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        err = f"Failed to start llama-server: {exc}"
        for case in BENCH_TASKS:
            for run_id in range(1, args.runs + 1):
                results.append(
                    BenchResult(
                        backend="llama_cpp",
                        model=model_name,
                        case_id=case["id"],
                        case_title=case["title"],
                        run=run_id,
                        wall_ms=0.0,
                        prompt_tokens=None,
                        output_tokens=None,
                        output_tps=None,
                        quality_score=0.0,
                        keyword_hits=0,
                        keyword_total=len(case["required_keywords"]),
                        avg_cpu_pct=None,
                        max_cpu_pct=None,
                        avg_mem_pct=None,
                        max_mem_pct=None,
                        avg_gpu_pct=None,
                        max_gpu_pct=None,
                        avg_vram_used_mb=None,
                        max_vram_used_mb=None,
                        cpu_time_sec=0.0,
                        output_preview="",
                        error=err,
                    )
                )
        return results

    try:
        if not wait_for_server_ready(port, timeout_sec=min(args.timeout_sec, 240)):
            err = f"llama-server not ready on port {port}"
            for case in BENCH_TASKS:
                for run_id in range(1, args.runs + 1):
                    results.append(
                        BenchResult(
                            backend="llama_cpp",
                            model=model_name,
                            case_id=case["id"],
                            case_title=case["title"],
                            run=run_id,
                            wall_ms=0.0,
                            prompt_tokens=None,
                            output_tokens=None,
                            output_tps=None,
                            quality_score=0.0,
                            keyword_hits=0,
                            keyword_total=len(case["required_keywords"]),
                            avg_cpu_pct=None,
                            max_cpu_pct=None,
                            avg_mem_pct=None,
                            max_mem_pct=None,
                            avg_gpu_pct=None,
                            max_gpu_pct=None,
                            avg_vram_used_mb=None,
                            max_vram_used_mb=None,
                            cpu_time_sec=0.0,
                            output_preview="",
                            error=err,
                        )
                    )
            return results

        for case in BENCH_TASKS:
            for run_id in range(1, args.runs + 1):
                start = time.perf_counter()
                cpu_start = time.process_time()
                monitor = SystemMonitor()
                monitor.start()
                try:
                    payload = {
                        "model": model_name,
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a PostgreSQL migration assistant. Return SQL only, no markdown, no explanations.",
                            },
                            {"role": "user", "content": case["prompt"]},
                        ],
                        **({"max_tokens": args.max_tokens} if args.max_tokens is not None else {}),
                        "seed": args.seed,
                        "temperature": args.temp,
                        "top_p": args.top_p,
                        "repeat_penalty": args.repeat_penalty,
                        "stream": False,
                    }
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=args.timeout_sec) as response:
                        raw = response.read().decode("utf-8")
                        parsed = json.loads(raw)

                    monitor.stop()
                    m = monitor.stats()
                    text = (
                        parsed.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    )
                    cleaned_text = clean_model_output(text)
                    quality, hits = score_output(text, case["required_keywords"])
                    wall_s = (time.perf_counter() - start)
                    usage = parsed.get("usage", {}) if isinstance(parsed, dict) else {}
                    prompt_tokens = usage.get("prompt_tokens")
                    output_tokens = usage.get("completion_tokens")
                    output_tps = None
                    if isinstance(output_tokens, (int, float)) and wall_s > 0:
                        output_tps = float(output_tokens) / wall_s
                    results.append(
                        BenchResult(
                            backend="llama_cpp",
                            model=model_name,
                            case_id=case["id"],
                            case_title=case["title"],
                            run=run_id,
                            wall_ms=wall_s * 1000.0,
                            prompt_tokens=int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None,
                            output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
                            output_tps=float(output_tps) if isinstance(output_tps, (int, float)) else None,
                            quality_score=quality,
                            keyword_hits=hits,
                            keyword_total=len(case["required_keywords"]),
                            avg_cpu_pct=m["avg_cpu_pct"],
                            max_cpu_pct=m["max_cpu_pct"],
                            avg_mem_pct=m["avg_mem_pct"],
                            max_mem_pct=m["max_mem_pct"],
                            avg_gpu_pct=m["avg_gpu_pct"],
                            max_gpu_pct=m["max_gpu_pct"],
                            avg_vram_used_mb=m["avg_vram_used_mb"],
                            max_vram_used_mb=m["max_vram_used_mb"],
                            cpu_time_sec=time.process_time() - cpu_start,
                            output_preview=cleaned_text[:220].replace("\n", "\\n"),
                            error="",
                        )
                    )
                except Exception as exc:
                    monitor.stop()
                    m = monitor.stats()
                    results.append(
                        BenchResult(
                            backend="llama_cpp",
                            model=model_name,
                            case_id=case["id"],
                            case_title=case["title"],
                            run=run_id,
                            wall_ms=(time.perf_counter() - start) * 1000.0,
                            prompt_tokens=None,
                            output_tokens=None,
                            output_tps=None,
                            quality_score=0.0,
                            keyword_hits=0,
                            keyword_total=len(case["required_keywords"]),
                            avg_cpu_pct=m["avg_cpu_pct"],
                            max_cpu_pct=m["max_cpu_pct"],
                            avg_mem_pct=m["avg_mem_pct"],
                            max_mem_pct=m["max_mem_pct"],
                            avg_gpu_pct=m["avg_gpu_pct"],
                            max_gpu_pct=m["max_gpu_pct"],
                            avg_vram_used_mb=m["avg_vram_used_mb"],
                            max_vram_used_mb=m["max_vram_used_mb"],
                            cpu_time_sec=time.process_time() - cpu_start,
                            output_preview="",
                            error=f"llama-server call failed: {exc}",
                        )
                    )
    finally:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    return results


def siemens_generate(
    base_url: str,
    token: str,
    model: str,
    prompt: str,
    args: argparse.Namespace,
) -> Dict[str, object]:
    """Call Siemens OpenAI-compatible chat completions endpoint."""
    messages = [
        {
            "role": "system",
            "content": "You are a PostgreSQL migration assistant. Return SQL only, no markdown, no explanations.",
        },
        {"role": "user", "content": prompt},
    ]
    payload: Dict[str, object] = {
        "model": model,
        "messages": messages,
        "temperature": args.temp,
        "top_p": args.top_p,
        "stream": False,
    }
    # No max_tokens cap for cloud: no VRAM constraint, let model complete naturally.
    # Reasoning models get an explicit generous limit via SIEMENS_MODEL_MAX_TOKENS.
    explicit_max = SIEMENS_MODEL_MAX_TOKENS.get(model)
    if explicit_max is not None:
        payload["max_tokens"] = explicit_max
    # Disable thinking mode for Qwen to avoid silent token consumption
    if model in SIEMENS_NO_THINKING_MODELS:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    url = base_url.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    attempts = 3
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=args.timeout_sec) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (429, 502, 503) and attempt < attempts:
                time.sleep(3 * attempt)
                continue
            raise
        except Exception as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(1.5)
                continue
            raise
    raise RuntimeError(f"Siemens API failed after retries: {last_error}")


def run_siemens_case(
    base_url: str,
    token: str,
    model: str,
    case: Dict[str, object],
    run_id: int,
    args: argparse.Namespace,
) -> BenchResult:
    start = time.perf_counter()
    cpu_start = time.process_time()
    monitor = SystemMonitor()
    monitor.start()
    try:
        parsed = siemens_generate(base_url, token, model, case["prompt"], args)
        wall_s = time.perf_counter() - start
        monitor.stop()
        m = monitor.stats()

        text = (
            parsed.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        cleaned_text = clean_model_output(text)
        quality, hits = score_output(text, case["required_keywords"])
        usage = parsed.get("usage", {}) if isinstance(parsed, dict) else {}
        prompt_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        output_tps = float(output_tokens) / wall_s if isinstance(output_tokens, (int, float)) and wall_s > 0 else None

        return BenchResult(
            backend="siemens",
            model=model,
            case_id=case["id"],
            case_title=case["title"],
            run=run_id,
            wall_ms=wall_s * 1000.0,
            prompt_tokens=int(prompt_tokens) if isinstance(prompt_tokens, (int, float)) else None,
            output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
            output_tps=output_tps,
            quality_score=quality,
            keyword_hits=hits,
            keyword_total=len(case["required_keywords"]),
            avg_cpu_pct=m["avg_cpu_pct"],
            max_cpu_pct=m["max_cpu_pct"],
            avg_mem_pct=m["avg_mem_pct"],
            max_mem_pct=m["max_mem_pct"],
            avg_gpu_pct=None,       # cloud-hosted, kein lokales GPU
            max_gpu_pct=None,
            avg_vram_used_mb=None,
            max_vram_used_mb=None,
            cpu_time_sec=time.process_time() - cpu_start,
            output_preview=cleaned_text[:220].replace("\n", "\\n"),
            error="",
        )
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        err = f"Siemens HTTP {exc.code}: {exc.reason} — {body}"
    except urllib.error.URLError as exc:
        err = f"Siemens connection failed: {exc}"
    except Exception as exc:
        err = f"Siemens error: {exc}"

    monitor.stop()
    m = monitor.stats()
    return BenchResult(
        backend="siemens",
        model=model,
        case_id=case["id"],
        case_title=case["title"],
        run=run_id,
        wall_ms=(time.perf_counter() - start) * 1000.0,
        prompt_tokens=None,
        output_tokens=None,
        output_tps=None,
        quality_score=0.0,
        keyword_hits=0,
        keyword_total=len(case["required_keywords"]),
        avg_cpu_pct=m["avg_cpu_pct"],
        max_cpu_pct=m["max_cpu_pct"],
        avg_mem_pct=m["avg_mem_pct"],
        max_mem_pct=m["max_mem_pct"],
        avg_gpu_pct=None,
        max_gpu_pct=None,
        avg_vram_used_mb=None,
        max_vram_used_mb=None,
        cpu_time_sec=time.process_time() - cpu_start,
        output_preview="",
        error=err,
    )


def run_siemens_model(base_url, token, model, args):
    """Run all benchmark cases for one Siemens model serially; models run in parallel."""
    model_results = []
    for case in BENCH_TASKS:
        for run_id in range(1, args.runs + 1):
            print(f'[siemens] {model} | {case["id"]} | run {run_id}', flush=True)
            model_results.append(run_siemens_case(base_url, token, model, case, run_id, args))
    return model_results


def save_results(results: List[BenchResult], output_dir: str) -> Tuple[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(output_dir, f"migration_llm_bench_{ts}.csv")
    json_path = os.path.join(output_dir, f"migration_llm_bench_{ts}.json")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "backend",
                "model",
                "case_id",
                "case_title",
                "run",
                "wall_ms",
                "prompt_tokens",
                "output_tokens",
                "output_tps",
                "quality_score",
                "keyword_hits",
                "keyword_total",
                "avg_cpu_pct",
                "max_cpu_pct",
                "avg_mem_pct",
                "max_mem_pct",
                "avg_gpu_pct",
                "max_gpu_pct",
                "avg_vram_used_mb",
                "max_vram_used_mb",
                "cpu_time_sec",
                "output_preview",
                "error",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.backend,
                    r.model,
                    r.case_id,
                    r.case_title,
                    r.run,
                    f"{r.wall_ms:.2f}",
                    r.prompt_tokens if r.prompt_tokens is not None else "",
                    r.output_tokens if r.output_tokens is not None else "",
                    f"{r.output_tps:.2f}" if r.output_tps is not None else "",
                    f"{r.quality_score:.2f}",
                    r.keyword_hits,
                    r.keyword_total,
                    f"{r.avg_cpu_pct:.2f}" if r.avg_cpu_pct is not None else "",
                    f"{r.max_cpu_pct:.2f}" if r.max_cpu_pct is not None else "",
                    f"{r.avg_mem_pct:.2f}" if r.avg_mem_pct is not None else "",
                    f"{r.max_mem_pct:.2f}" if r.max_mem_pct is not None else "",
                    f"{r.avg_gpu_pct:.2f}" if r.avg_gpu_pct is not None else "",
                    f"{r.max_gpu_pct:.2f}" if r.max_gpu_pct is not None else "",
                    f"{r.avg_vram_used_mb:.2f}" if r.avg_vram_used_mb is not None else "",
                    f"{r.max_vram_used_mb:.2f}" if r.max_vram_used_mb is not None else "",
                    f"{r.cpu_time_sec:.3f}",
                    r.output_preview,
                    r.error,
                ]
            )

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([r.__dict__ for r in results], f, indent=2)

    return csv_path, json_path


def print_summary(results: List[BenchResult]) -> None:
    grouped: Dict[Tuple[str, str], List[BenchResult]] = {}
    for r in results:
        grouped.setdefault((r.backend, r.model), []).append(r)

    rows = []
    for (backend, model), items in grouped.items():
        ok = [i for i in items if not i.error]
        errors = [i for i in items if i.error]
        if ok:
            avg_quality = mean(i.quality_score for i in ok)
            avg_wall = mean(i.wall_ms for i in ok)
            tps_vals = [i.output_tps for i in ok if i.output_tps is not None]
            avg_tps = mean(tps_vals) if tps_vals else None
        else:
            avg_quality = 0.0
            avg_wall = 0.0
            avg_tps = None
        cpu_vals = [i.avg_cpu_pct for i in ok if i.avg_cpu_pct is not None]
        gpu_vals = [i.avg_gpu_pct for i in ok if i.avg_gpu_pct is not None]
        cpu_avg = mean(cpu_vals) if cpu_vals else None
        gpu_avg = mean(gpu_vals) if gpu_vals else None
        reliability = (len(ok) / len(items)) * 100.0 if items else 0.0
        rows.append(
            {
                "backend": backend,
                "model": model,
                "quality": avg_quality,
                "wall": avg_wall,
                "tps": avg_tps,
                "errors": len(errors),
                "cpu": cpu_avg,
                "gpu": gpu_avg,
                "reliability": reliability,
            }
        )

    tps_values = [r["tps"] for r in rows if r["tps"] is not None]
    wall_values = [r["wall"] for r in rows if r["wall"] > 0]
    load_values = [(r["cpu"] or 0.0) + (r["gpu"] or 0.0) for r in rows]
    min_tps, max_tps = (min(tps_values), max(tps_values)) if tps_values else (0.0, 0.0)
    min_wall, max_wall = (min(wall_values), max(wall_values)) if wall_values else (0.0, 0.0)
    min_load, max_load = (min(load_values), max(load_values)) if load_values else (0.0, 0.0)

    def norm_high(value: Optional[float], lo: float, hi: float) -> float:
        if value is None:
            return 0.0
        if hi <= lo:
            return 100.0
        return ((value - lo) / (hi - lo)) * 100.0

    def norm_low(value: Optional[float], lo: float, hi: float) -> float:
        if value is None:
            return 0.0
        if hi <= lo:
            return 100.0
        return ((hi - value) / (hi - lo)) * 100.0

    for r in rows:
        speed_score = 0.6 * norm_high(r["tps"], min_tps, max_tps) + 0.4 * norm_low(r["wall"], min_wall, max_wall)
        load = (r["cpu"] or 0.0) + (r["gpu"] or 0.0)
        efficiency_score = norm_low(load, min_load, max_load)
        r["overall"] = 0.60 * r["quality"] + 0.20 * speed_score + 0.10 * r["reliability"] + 0.10 * efficiency_score

    rows.sort(key=lambda x: (x["overall"], x["quality"], x["tps"] if x["tps"] is not None else 0.0), reverse=True)

    print("\n=== Aggregated Results ===")
    print(f"{'Backend':<10} {'Model':<35} {'Overall':>8} {'Qual':>8} {'Tok/s':>8} {'Wall(ms)':>10} {'CPU%':>8} {'GPU%':>8} {'Rel%':>7} {'Err':>5}")
    for r in rows:
        t_str = f"{r['tps']:.2f}" if r["tps"] is not None else "n/a"
        cpu_str = f"{r['cpu']:.1f}" if r["cpu"] is not None else "n/a"
        gpu_str = f"{r['gpu']:.1f}" if r["gpu"] is not None else "n/a"
        print(
            f"{r['backend']:<10} {r['model']:<35} {r['overall']:>8.2f} {r['quality']:>8.2f} {t_str:>8} "
            f"{r['wall']:>10.1f} {cpu_str:>8} {gpu_str:>8} {r['reliability']:>7.1f} {r['errors']:>5}"
        )

    print("\n=== Errors (if any) ===")
    for r in results:
        if r.error:
            print(f"[{r.backend}/{r.model}/{r.case_id}/run{r.run}] {r.error}")


def main() -> int:
    args = parse_args()

    results: List[BenchResult] = []

    if args.backend in ("ollama", "both", "all"):
        ollama_models = args.ollama_models or default_ollama_models()
        if not ollama_models:
            print("No matching Ollama models found for qwen3.6 27/35 q4 and gpt-oss.")
        else:
            print(f"Ollama models: {', '.join(ollama_models)}")
            for model in ollama_models:
                for case in BENCH_TASKS:
                    for run_id in range(1, args.runs + 1):
                        print(f"[ollama] {model} | {case['id']} | run {run_id}")
                        results.append(run_ollama_case(model, case, run_id, args))

    if args.backend in ("llama_cpp", "both", "all"):
        try:
            llama_models = parse_llama_model_specs(args.llama_models)
        except ValueError as exc:
            print(str(exc))
            return 2
        if not llama_models:
            print("No llama.cpp models configured. Use --llama-model NAME=PATH.")
        else:
            print(f"llama.cpp models: {', '.join(name for name, _ in llama_models)}")
            if args.llama_server:
                for model_name, model_path in llama_models:
                    print(f"[llama_cpp/server] {model_name} | loading model")
                    model_results = run_llama_server_model(args.llama_server, model_name, model_path, args)
                    for row in model_results:
                        print(f"[llama_cpp/server] {model_name} | {row.case_id} | run {row.run}")
                    results.extend(model_results)
            else:
                for model_name, model_path in llama_models:
                    for case in BENCH_TASKS:
                        for run_id in range(1, args.runs + 1):
                            print(f"[llama_cpp/cli] {model_name} | {case['id']} | run {run_id}")
                            results.append(run_llama_cpp_case(model_name, model_path, case, run_id, args))

    if args.backend in ("siemens", "all"):
        token = load_siemens_token(args)
        if not token:
            print(
                "ERROR: Siemens API token not found. Set env var SIEMENS_LLM_TOKEN, "
                "use --siemens-token, or --siemens-token-file."
            )
            if args.backend == "siemens":
                return 2
        else:
            siemens_models = args.siemens_models or SIEMENS_DEFAULT_MODELS
            print(f"Siemens models: {', '.join(siemens_models)}")
            workers = min(args.siemens_workers, len(siemens_models))
            siemens_buf = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(run_siemens_model, args.siemens_url, token, m, args): m
                           for m in siemens_models}
                for future in as_completed(futures):
                    mdl = futures[future]
                    try:
                        siemens_buf.extend(future.result())
                    except Exception as exc:
                        print(f'[siemens] {mdl} FAILED: {exc}')
            results.extend(siemens_buf)

    if not results:
        print("No benchmarks executed.")
        return 1

    print_summary(results)
    csv_path, json_path = save_results(results, args.output_dir)
    print(f"\nSaved CSV:  {csv_path}")
    print(f"Saved JSON: {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
