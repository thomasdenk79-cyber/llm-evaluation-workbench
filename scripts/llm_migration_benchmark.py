#!/usr/bin/env python3
import argparse
import csv
import glob
import hashlib
import html
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Dict, List, Optional, Set, Tuple

import psutil
from agent_helper_eval import local_lock

BENCHMARK_NAME = "ora-pg-py-33"
BENCHMARK_SPEC_VERSION = "2026-07-26"
BENCHMARK_LIBRARY_DIR = "benchmarks"
DEFAULT_BENCHMARK_ID = "ora-pg-py-33"
BENCHMARK_SCORING_MODE = "classic"  # classic | swe_lite
BENCHMARK_SWE_PASS_THRESHOLD = 85.0

# Oracle-specific syntax that must NOT appear in any PostgreSQL output
_ORACLE_FORBIDDEN_SQL = [
    "number(",       # Oracle NUMBER type
    "varchar2",      # Oracle string type
    "nvarchar2",
    "sysdate",       # Oracle current date
    "systimestamp",
    "rownum",        # Oracle row limiter
    "nvl(",          # Oracle null coalesce
    "decode(",       # Oracle conditional
    "sys_context(",  # Oracle context function
    "dbms_",         # Oracle packages
    "sql%rowcount",  # Oracle implicit cursor attribute
    "sql%found",
    "sql%notfound",
    "bulk collect",  # Oracle bulk operation
    "forall ",       # Oracle bulk DML
    "%type",         # Oracle anchored type
    "%rowtype",
    "is table of",   # Oracle collection type
    "nocache",       # Oracle sequence option
    "nocycle",       # Oracle sequence option
    "nextval",       # Oracle sequence syntax (seq.nextval)
    ":new.",         # Oracle trigger syntax
    ":old.",
    "pragma ",       # Oracle pragma
]

_ORACLE_FORBIDDEN_PY = [
    "cx_oracle",     # deprecated Oracle Python driver (use oracledb)
    "cx_Oracle",
]

BENCH_TASKS = [
    # ── SQL Task 1: DDL ──────────────────────────────────────────────────────
    {
        "id": "ddl_conversion",
        "title": "Oracle DDL to PostgreSQL",
        "prompt": (
            "Convert this Oracle DDL to PostgreSQL 16 compatible SQL. "
            "Use GENERATED ALWAYS AS IDENTITY for the primary key. "
            "Replace all Oracle-specific types and syntax. Return SQL only, no explanations.\n\n"
            "CREATE TABLE ORDERS (\n"
            "  ID NUMBER(19) PRIMARY KEY,\n"
            "  ORDER_NO VARCHAR2(40) NOT NULL,\n"
            "  AMOUNT NUMBER(15,2) DEFAULT 0 NOT NULL,\n"
            "  CREATED_AT DATE DEFAULT SYSDATE,\n"
            "  STATUS CHAR(1) DEFAULT 'A' NOT NULL,\n"
            "  PAYLOAD CLOB\n"
            ");\n"
            "CREATE INDEX IX_ORDERS_CREATED_AT ON ORDERS(CREATED_AT);\n"
            "CREATE INDEX IX_ORDERS_STATUS ON ORDERS(STATUS) WHERE STATUS != 'D';\n"
        ),
        "required_keywords": [
            "create table",
            ["generated always as identity", "bigint", "bigserial"],
            ["varchar", "character varying"],
            ["timestamp", "current_timestamp", "now()"],
            "text",
            "create index",
            "primary key",
            "not null",
            ["where status", "where (status"],   # partial index must survive
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_SQL,
    },
    # ── SQL Task 2: PL/SQL Procedure ─────────────────────────────────────────
    {
        "id": "plsql_to_plpgsql",
        "title": "PL/SQL to PLpgSQL",
        "prompt": (
            "Convert this Oracle procedure to PostgreSQL PL/pgSQL. "
            "Use FOUND instead of SQL%ROWCOUNT. Return only the function SQL, no explanations.\n\n"
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
            ["language plpgsql"],
            "coalesce(",
            ["current_timestamp", "now()"],
            "if not found",
            "update orders",
            "insert into orders",
            "$$",
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_SQL,
    },
    # ── SQL Task 3: Validation Query ─────────────────────────────────────────
    {
        "id": "validation_query",
        "title": "Migration validation SQL",
        "prompt": (
            "Create a PostgreSQL 16 SQL script to validate an Oracle→PostgreSQL migration "
            "for schema app_mig. Requirements:\n"
            "1. Row count check per table comparing source vs target\n"
            "2. MD5 checksum aggregate per table\n"
            "3. List tables where counts differ\n"
            "Use information_schema or pg_catalog. Return SQL only."
        ),
        "required_keywords": [
            ["information_schema.tables", "pg_catalog.pg_tables"],
            "count(*)",
            "md5(",
            "group by",
            "where",
            ["<>", "!=", "having"],   # must detect mismatches
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_SQL,
    },
    # ── SQL Task 4: Sequence + Identity ──────────────────────────────────────
    {
        "id": "sequence_conversion",
        "title": "Oracle SEQUENCE to PostgreSQL IDENTITY",
        "prompt": (
            "Convert this Oracle SEQUENCE and table to PostgreSQL 16. "
            "Use GENERATED ALWAYS AS IDENTITY — do NOT use a separate CREATE SEQUENCE. "
            "Return SQL only.\n\n"
            "CREATE SEQUENCE orders_seq START WITH 1000 INCREMENT BY 1 NOCACHE NOCYCLE;\n"
            "CREATE TABLE ORDERS (\n"
            "  ID NUMBER(19) DEFAULT orders_seq.NEXTVAL PRIMARY KEY,\n"
            "  ORDER_NO VARCHAR2(40) NOT NULL,\n"
            "  AMOUNT NUMBER(15,2) DEFAULT 0 NOT NULL\n"
            ");\n"
        ),
        "required_keywords": [
            "create table",
            "generated always as identity",
            ["start with 1000", "(start with 1000"],
            "primary key",
            "not null",
            ["varchar", "character varying"],
            ["numeric", "decimal"],
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_SQL + ["create sequence"],
    },
    # ── SQL Task 5: Trigger ───────────────────────────────────────────────────
    {
        "id": "trigger_conversion",
        "title": "Oracle Trigger to PostgreSQL",
        "prompt": (
            "Convert this Oracle trigger to PostgreSQL 16. "
            "PostgreSQL requires a separate trigger function returning TRIGGER. "
            "Use NEW.column := value syntax. Return SQL only.\n\n"
            "CREATE OR REPLACE TRIGGER orders_audit_trg\n"
            "BEFORE INSERT OR UPDATE ON orders\n"
            "FOR EACH ROW\n"
            "BEGIN\n"
            "  :NEW.updated_at := SYSDATE;\n"
            "  :NEW.updated_by := SYS_CONTEXT('USERENV','SESSION_USER');\n"
            "END;\n"
            "/\n"
        ),
        "required_keywords": [
            "create or replace function",
            "returns trigger",
            "language plpgsql",
            "create trigger",
            ["before insert or update", "before update or insert"],
            "for each row",
            ["execute function", "execute procedure"],
            ["new.updated_at", "new.updated_by"],
            ["current_timestamp", "now()", "current_user", "session_user"],
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_SQL,
    },
    # ── SQL Task 6: View with Oracle functions ───────────────────────────────
    {
        "id": "view_conversion",
        "title": "Oracle View: ROWNUM/NVL/DECODE to PostgreSQL",
        "prompt": (
            "Convert this Oracle view to PostgreSQL 16. "
            "Replace ROWNUM with ROW_NUMBER() OVER() or LIMIT. "
            "Replace NVL with COALESCE. Replace DECODE with CASE WHEN. "
            "Replace TRUNC(date) with date_trunc or ::date cast. "
            "Return SQL only.\n\n"
            "CREATE OR REPLACE VIEW v_active_orders AS\n"
            "SELECT\n"
            "  o.id,\n"
            "  o.order_no,\n"
            "  NVL(o.amount, 0) AS amount,\n"
            "  DECODE(o.status, 'A', 'Active', 'C', 'Closed', 'Unknown') AS status_label,\n"
            "  TRUNC(o.created_at) AS order_date,\n"
            "  ROWNUM AS row_num\n"
            "FROM orders o\n"
            "WHERE o.status != 'D'\n"
            "  AND ROWNUM <= 1000;\n"
        ),
        "required_keywords": [
            "create or replace view",
            "coalesce(",
            "case",
            "when",
            ["date_trunc(", "::date", "cast(created_at"],
            ["row_number()", "limit 1000", "fetch first 1000"],
            "where",
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_SQL,
    },
    # ── SQL Task 7: BULK COLLECT / FORALL refactor ───────────────────────────
    {
        "id": "bulk_collect_refactor",
        "title": "Oracle BULK COLLECT/FORALL to PostgreSQL",
        "prompt": (
            "Convert this Oracle PL/SQL to PostgreSQL PL/pgSQL. "
            "Replace BULK COLLECT with an array or cursor loop. "
            "Replace FORALL with a FOR loop or set-based INSERT/DELETE. "
            "Replace %TYPE with concrete types. Return SQL only.\n\n"
            "CREATE OR REPLACE PROCEDURE archive_old_orders(p_days IN NUMBER) AS\n"
            "  TYPE t_ids IS TABLE OF orders.id%TYPE;\n"
            "  v_ids t_ids;\n"
            "BEGIN\n"
            "  SELECT id BULK COLLECT INTO v_ids\n"
            "  FROM orders\n"
            "  WHERE created_at < SYSDATE - p_days;\n"
            "\n"
            "  FORALL i IN 1..v_ids.COUNT\n"
            "    INSERT INTO orders_archive SELECT * FROM orders WHERE id = v_ids(i);\n"
            "\n"
            "  FORALL i IN 1..v_ids.COUNT\n"
            "    DELETE FROM orders WHERE id = v_ids(i);\n"
            "END;\n"
            "/\n"
        ),
        "required_keywords": [
            "create or replace",
            ["procedure", "function"],
            "language plpgsql",
            ["interval", "current_timestamp - ", "now() -", "current_date -"],
            "insert into orders_archive",
            "delete from orders",
            ["for ", "foreach", "loop"],
            "$$",
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_SQL,
    },
    # ── Python Task 1: Type mapper ───────────────────────────────────────────
    {
        "id": "py_type_mapper",
        "title": "Python: Oracle→PG type mapping function",
        "prompt": (
            "Write a Python function oracle_to_pg_type(oracle_type: str, length: int = 0, "
            "precision: int = 0, scale: int = 0) -> str "
            "that maps Oracle data types to PostgreSQL types.\n"
            "Rules: NUMBER(p,s>0) → NUMERIC(p,s), NUMBER(p,0) → BIGINT if p>9 else INTEGER, "
            "NUMBER without precision → NUMERIC, "
            "VARCHAR2/NVARCHAR2 → VARCHAR(n), CHAR/NCHAR → CHAR(n), "
            "DATE → TIMESTAMP WITHOUT TIME ZONE, "
            "TIMESTAMP → TIMESTAMP WITHOUT TIME ZONE, "
            "CLOB/NCLOB → TEXT, BLOB → BYTEA, RAW → BYTEA, "
            "FLOAT/BINARY_FLOAT/BINARY_DOUBLE → DOUBLE PRECISION, "
            "INTERVAL YEAR TO MONTH → INTERVAL, INTERVAL DAY TO SECOND → INTERVAL.\n"
            "Handle case-insensitive input. Return only the Python code."
        ),
        "required_keywords": [
            "def oracle_to_pg_type",
            ["number", "varchar2", "clob"],
            ["numeric", "bigint", "integer"],
            "text",
            "bytea",
            ["if ", "elif "],
            "return",
            ".upper()",
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_PY,
    },
    # ── Python Task 2: DDL extractor ─────────────────────────────────────────
    {
        "id": "py_ddl_extractor",
        "title": "Python: Oracle DDL extractor via oracledb",
        "prompt": (
            "Write a Python function get_table_columns(host: str, port: int, service: str, "
            "user: str, password: str, table_name: str) -> list[dict] "
            "that connects to Oracle using the oracledb library (NOT cx_Oracle) and queries "
            "ALL_TAB_COLUMNS to return column definitions as a list of dicts with keys: "
            "column_name, data_type, data_length, data_precision, data_scale, nullable.\n"
            "Use a DSN connection string with oracledb.makedsn or oracledb.connect(dsn=...). "
            "Return only the Python code."
        ),
        "required_keywords": [
            "import oracledb",
            "def get_table_columns",
            ["makedsn", "dsn=", "oracledb.connect"],
            "all_tab_columns",
            ["cursor()", ".cursor()"],
            "fetchall",
            "return",
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_PY,
    },
    # ── Python Task 3: Rowcount validator ────────────────────────────────────
    {
        "id": "py_rowcount_validator",
        "title": "Python: Migration rowcount validator",
        "prompt": (
            "Write a Python function validate_rowcounts(oracle_conn, pg_conn, tables: list[str]) -> list[dict] "
            "that queries COUNT(*) for each table on both Oracle and PostgreSQL connections "
            "and returns a list of dicts with: table_name, oracle_count, pg_count, match (bool), diff (int).\n"
            "Use parameterized queries with bind variables (Oracle: :1 or :table_name, PG: %s). "
            "Use oracledb style for Oracle. Return only the Python code."
        ),
        "required_keywords": [
            "def validate_rowcounts",
            ["count(*)", "count( *)"],
            ["oracle_cur", "oracle_conn.cursor", "oc.execute"],
            ["pg_cur", "pg_conn.cursor", "pc.execute"],
            "match",
            "diff",
            "for ",
            "return",
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_PY,
    },
    # ── Python Task 4: Migrator class ────────────────────────────────────────
    {
        "id": "py_migration_class",
        "title": "Python: OracleToPgMigrator class",
        "prompt": (
            "Write a Python class OracleToPgMigrator with:\n"
            "- __init__(self, oracle_conn, pg_conn) storing both connections\n"
            "- transform_ddl(self, oracle_ddl: str) -> str  that uses regex (re module) to replace:\n"
            "  NUMBER(p,s) → NUMERIC(p,s), VARCHAR2(n) → VARCHAR(n), DATE → TIMESTAMP, "
            "  SYSDATE → CURRENT_TIMESTAMP, NVL( → COALESCE(\n"
            "- migrate_table(self, table_name: str, batch_size: int = 1000) -> dict  that copies rows "
            "from Oracle to PostgreSQL in batches and returns {table_name, rows_copied, errors: list}\n"
            "Use type hints throughout. Return only the Python code."
        ),
        "required_keywords": [
            "class OracleToPgMigrator",
            "def __init__",
            "def transform_ddl",
            "def migrate_table",
            "self.",
            "re.sub(",
            "batch_size",
            "return",
        ],
        "forbidden_keywords": _ORACLE_FORBIDDEN_PY,
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
    forbidden_hits: int
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
    local_model_startup_sec: Optional[float] = None
    local_model_shutdown_sec: Optional[float] = None
    benchmark_run_id: str = ""
    benchmark_name: str = BENCHMARK_NAME
    benchmark_spec_version: str = BENCHMARK_SPEC_VERSION
    benchmark_script_file: str = "scripts/llm_migration_benchmark.py"
    benchmark_task_count: int = 0
    benchmark_runs: int = 0
    benchmark_task_ids: str = ""
    benchmark_task_hash: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class SystemMonitor:
    def __init__(
        self,
        sample_interval_sec: float = 0.5,
        target_pids: Optional[List[int]] = None,
        process_name_filters: Optional[List[str]] = None,
    ):
        self.sample_interval_sec = sample_interval_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.cpu_samples: List[float] = []
        self.mem_samples: List[float] = []
        self.gpu_samples: List[float] = []
        self.vram_samples: List[float] = []
        self.target_pids: List[int] = [int(pid) for pid in (target_pids or []) if int(pid) > 0]
        self.process_name_filters: List[str] = [f.strip().lower() for f in (process_name_filters or []) if f.strip()]
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
            if self.target_pids or self.process_name_filters:
                mem = self._query_process_mem_percent()
            else:
                mem = psutil.virtual_memory().percent
            self.cpu_samples.append(cpu)
            if mem is not None:
                self.mem_samples.append(mem)
            if self.nvidia_smi_available:
                gpu, vram = self._query_gpu()
                if gpu is not None:
                    self.gpu_samples.append(gpu)
                if vram is not None:
                    self.vram_samples.append(vram)

    def _collect_target_pids(self) -> Set[int]:
        pids: Set[int] = set(self.target_pids)
        for root_pid in list(pids):
            try:
                proc = psutil.Process(root_pid)
                for child in proc.children(recursive=True):
                    pids.add(child.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if self.process_name_filters:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    pname = (proc.info.get("name") or "").lower()
                    pcmd = " ".join(proc.info.get("cmdline") or []).lower()
                    if any(f in pname or f in pcmd for f in self.process_name_filters):
                        pids.add(int(proc.info["pid"]))
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, TypeError):
                    continue
        return pids

    def _query_process_mem_percent(self) -> Optional[float]:
        total_mem = float(psutil.virtual_memory().total)
        if total_mem <= 0:
            return None
        rss_total = 0
        for pid in self._collect_target_pids():
            try:
                rss_total += int(psutil.Process(pid).memory_info().rss)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if rss_total <= 0:
            return None
        return (rss_total / total_mem) * 100.0

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


def benchmark_library_path() -> str:
    return os.path.join(os.getcwd(), BENCHMARK_LIBRARY_DIR)


def default_benchmark_suite() -> Dict[str, Any]:
    return {
        "benchmark_id": DEFAULT_BENCHMARK_ID,
        "name": BENCHMARK_NAME,
        "spec_version": BENCHMARK_SPEC_VERSION,
        "tasks": json.loads(json.dumps(BENCH_TASKS)),
    }


def ensure_default_benchmark_suite_file() -> str:
    lib_dir = benchmark_library_path()
    os.makedirs(lib_dir, exist_ok=True)
    path = os.path.join(lib_dir, f"{DEFAULT_BENCHMARK_ID}.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(default_benchmark_suite(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return path


def list_benchmark_suite_files() -> List[str]:
    lib_dir = benchmark_library_path()
    if not os.path.isdir(lib_dir):
        return []
    return sorted(glob.glob(os.path.join(lib_dir, "*.json")))


def validate_benchmark_suite(suite: Dict[str, Any], source: str) -> None:
    if not isinstance(suite, dict):
        raise ValueError(f"Benchmark suite must be an object: {source}")
    for key in ("name", "spec_version", "tasks"):
        if key not in suite:
            raise ValueError(f"Benchmark suite missing '{key}': {source}")
    if not isinstance(suite["tasks"], list) or not suite["tasks"]:
        raise ValueError(f"Benchmark suite tasks must be a non-empty list: {source}")
    for idx, task in enumerate(suite["tasks"], start=1):
        if not isinstance(task, dict):
            raise ValueError(f"Task #{idx} must be an object: {source}")
        for key in ("id", "title", "prompt", "required_keywords"):
            if key not in task:
                raise ValueError(f"Task #{idx} missing '{key}': {source}")
        if not isinstance(task["required_keywords"], list) or not task["required_keywords"]:
            raise ValueError(f"Task #{idx} required_keywords must be a non-empty list: {source}")
        if "forbidden_keywords" not in task:
            task["forbidden_keywords"] = []
    scoring = suite.get("scoring_profile")
    if scoring is not None:
        if not isinstance(scoring, dict):
            raise ValueError(f"Benchmark suite scoring_profile must be an object: {source}")
        mode = str(scoring.get("mode", "classic")).strip().lower()
        if mode not in ("classic", "swe_lite"):
            raise ValueError(f"Benchmark suite scoring_profile.mode must be classic|swe_lite: {source}")
        if "pass_threshold" in scoring:
            try:
                th = float(scoring["pass_threshold"])
            except (TypeError, ValueError):
                raise ValueError(f"Benchmark suite scoring_profile.pass_threshold invalid: {source}")
            if th < 0.0 or th > 100.0:
                raise ValueError(f"Benchmark suite scoring_profile.pass_threshold must be 0..100: {source}")


def load_benchmark_suite(args: argparse.Namespace) -> Tuple[Dict[str, Any], str]:
    default_path = ensure_default_benchmark_suite_file()
    if args.benchmark_file:
        source_path = os.path.abspath(args.benchmark_file)
    else:
        bench_id = args.benchmark_id or DEFAULT_BENCHMARK_ID
        source_path = os.path.join(benchmark_library_path(), f"{bench_id}.json")
        if not os.path.exists(source_path):
            source_path = default_path
    with open(source_path, encoding="utf-8") as fh:
        suite = json.load(fh)
    validate_benchmark_suite(suite, source_path)
    return suite, source_path


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

# No per-model token limits — cloud has no VRAM constraint, let models complete naturally.
SIEMENS_MODEL_MAX_TOKENS: Dict[str, int] = {}


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
        "--benchmark-id",
        default=DEFAULT_BENCHMARK_ID,
        help=f"Benchmark suite id from {BENCHMARK_LIBRARY_DIR}/<id>.json.",
    )
    parser.add_argument(
        "--benchmark-file",
        default="",
        help="Explicit benchmark suite JSON file path.",
    )
    parser.add_argument(
        "--list-benchmarks",
        action="store_true",
        help=f"List available benchmark suite files in {BENCHMARK_LIBRARY_DIR}/ and exit.",
    )
    parser.add_argument(
        "--resume",
        choices=["auto", "off"],
        default="auto",
        help="Resume from latest matching *_inprogress.csv (auto) or start fresh (off).",
    )
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
    parser.add_argument(
        "--llama-ngl",
        dest="ngl",
        type=int,
        help="Alias for --ngl (for campaign compatibility).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Generation seed for reproducibility.")
    parser.add_argument("--temp", type=float, default=0.1, help="Sampling temperature.")
    parser.add_argument("--top-p", type=float, default=0.9, help="Top-p.")
    parser.add_argument("--repeat-penalty", type=float, default=1.05, help="Repeat penalty.")
    parser.add_argument("--llama-batch-size", type=int, default=1024, help="llama.cpp server batch size.")
    parser.add_argument("--llama-ubatch-size", type=int, default=256, help="llama.cpp server ubatch size.")
    parser.add_argument("--llama-fit-target-mib", type=int, default=1536, help="VRAM safety margin for --fit.")
    parser.add_argument(
        "--llama-extra-args",
        default="",
        help="Extra args appended to llama-cli / llama-server command line.",
    )
    parser.add_argument(
        "--llama-reasoning",
        choices=["off", "on", "auto"],
        default="off",
        help="llama.cpp server reasoning mode.",
    )
    parser.add_argument("--timeout-sec", type=int, default=900, help="Timeout per generation call.")
    parser.add_argument(
        "--local-lease-wait-seconds",
        type=float,
        default=local_lock.DEFAULT_WAIT_SECONDS,
        help="Wait timeout for the shared local-llm lease (default: 3600 seconds).",
    )
    parser.add_argument(
        "--output-dir",
        default="benchmark_results",
        help="Directory for CSV/JSON results.",
    )
    parser.add_argument(
        "--report-file",
        default=os.path.join("docs", "project", "benchmark_report.md"),
        help="Markdown report path updated after each benchmark run.",
    )
    return parser.parse_args()


def normalize(s: str) -> str:
    return s.lower().strip()


def build_benchmark_metadata(run_tag: str, benchmark_source: str) -> Dict[str, object]:
    task_ids = [str(t["id"]) for t in BENCH_TASKS]
    task_hash = hashlib.sha1("|".join(task_ids).encode("utf-8")).hexdigest()[:12]
    return {
        "benchmark_run_id": run_tag,
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_spec_version": BENCHMARK_SPEC_VERSION,
        "benchmark_script_file": benchmark_source,
        "benchmark_task_count": len(BENCH_TASKS),
        "benchmark_runs": 0,
        "benchmark_task_ids": ",".join(task_ids),
        "benchmark_task_hash": task_hash,
    }


def apply_benchmark_metadata(result: BenchResult, meta: Dict[str, object]) -> BenchResult:
    result.benchmark_run_id = str(meta["benchmark_run_id"])
    result.benchmark_name = str(meta["benchmark_name"])
    result.benchmark_spec_version = str(meta["benchmark_spec_version"])
    result.benchmark_script_file = str(meta["benchmark_script_file"])
    result.benchmark_task_count = int(meta["benchmark_task_count"])
    result.benchmark_runs = int(meta["benchmark_runs"])
    result.benchmark_task_ids = str(meta["benchmark_task_ids"])
    result.benchmark_task_hash = str(meta["benchmark_task_hash"])
    return result


def parse_opt_int(value: str) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(str(value)))
    except ValueError:
        return None


def parse_opt_float(value: str) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def target_backends_for_run(backend_arg: str) -> Set[str]:
    if backend_arg == "ollama":
        return {"ollama"}
    if backend_arg == "llama_cpp":
        return {"llama_cpp"}
    if backend_arg == "siemens":
        return {"siemens"}
    if backend_arg == "both":
        return {"ollama", "llama_cpp"}
    return {"ollama", "llama_cpp", "siemens"}


def row_to_bench_result(row: Dict[str, str]) -> BenchResult:
    return BenchResult(
        backend=row.get("backend", ""),
        model=row.get("model", ""),
        case_id=row.get("case_id", ""),
        case_title=row.get("case_title", ""),
        run=parse_opt_int(row.get("run", "")) or 0,
        wall_ms=parse_opt_float(row.get("wall_ms", "")) or 0.0,
        prompt_tokens=parse_opt_int(row.get("prompt_tokens", "")),
        output_tokens=parse_opt_int(row.get("output_tokens", "")),
        output_tps=parse_opt_float(row.get("output_tps", "")),
        quality_score=parse_opt_float(row.get("quality_score", "")) or 0.0,
        keyword_hits=parse_opt_int(row.get("keyword_hits", "")) or 0,
        keyword_total=parse_opt_int(row.get("keyword_total", "")) or 0,
        forbidden_hits=parse_opt_int(row.get("forbidden_hits", "")) or 0,
        avg_cpu_pct=parse_opt_float(row.get("avg_cpu_pct", "")),
        max_cpu_pct=parse_opt_float(row.get("max_cpu_pct", "")),
        avg_mem_pct=parse_opt_float(row.get("avg_mem_pct", "")),
        max_mem_pct=parse_opt_float(row.get("max_mem_pct", "")),
        avg_gpu_pct=parse_opt_float(row.get("avg_gpu_pct", "")),
        max_gpu_pct=parse_opt_float(row.get("max_gpu_pct", "")),
        avg_vram_used_mb=parse_opt_float(row.get("avg_vram_used_mb", "")),
        max_vram_used_mb=parse_opt_float(row.get("max_vram_used_mb", "")),
        cpu_time_sec=parse_opt_float(row.get("cpu_time_sec", "")) or 0.0,
        output_preview=row.get("output_preview", ""),
        error=row.get("error", ""),
        local_model_startup_sec=parse_opt_float(row.get("local_model_startup_sec", "")),
        local_model_shutdown_sec=parse_opt_float(row.get("local_model_shutdown_sec", "")),
        benchmark_run_id=row.get("benchmark_run_id", ""),
        benchmark_name=row.get("benchmark_name", BENCHMARK_NAME),
        benchmark_spec_version=row.get("benchmark_spec_version", BENCHMARK_SPEC_VERSION),
        benchmark_script_file=row.get("benchmark_script_file", "scripts/llm_migration_benchmark.py"),
        benchmark_task_count=parse_opt_int(row.get("benchmark_task_count", "")) or 0,
        benchmark_runs=parse_opt_int(row.get("benchmark_runs", "")) or 0,
        benchmark_task_ids=row.get("benchmark_task_ids", ""),
        benchmark_task_hash=row.get("benchmark_task_hash", ""),
        recorded_at=row.get("recorded_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def try_resume_results(
    args: argparse.Namespace,
    benchmark_source: str,
    target_backends: Set[str],
) -> Tuple[Optional[str], List[BenchResult], str]:
    inprogress_files = sorted(
        glob.glob(os.path.join(args.output_dir, "migration_llm_bench_*_inprogress.csv")),
        key=lambda p: os.path.getmtime(p),
        reverse=True,
    )
    if not inprogress_files:
        return None, [], ""

    expected_task_ids = [str(t["id"]) for t in BENCH_TASKS]
    expected_task_hash = hashlib.sha1("|".join(expected_task_ids).encode("utf-8")).hexdigest()[:12]
    normalized_source = os.path.abspath(benchmark_source)

    for csv_path in inprogress_files:
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except OSError:
            continue
        if not rows:
            continue

        row0 = rows[0]
        row_name = row0.get("benchmark_name", "")
        row_version = row0.get("benchmark_spec_version", "")
        row_source = os.path.abspath(row0.get("benchmark_script_file", ""))
        row_hash = row0.get("benchmark_task_hash", "")
        row_runs = parse_opt_int(row0.get("benchmark_runs", ""))

        if row_name and row_name != BENCHMARK_NAME:
            continue
        if row_version and row_version != BENCHMARK_SPEC_VERSION:
            continue
        if row_source and row_source != normalized_source:
            continue
        if row_hash and row_hash != expected_task_hash:
            continue
        if row_runs is not None and row_runs != args.runs:
            continue

        scoped = [r for r in rows if r.get("backend", "") in target_backends]
        if not scoped:
            continue

        run_tag = row0.get("benchmark_run_id", "")
        if not run_tag:
            m = re.search(r"migration_llm_bench_(\d{8}_\d{6})_inprogress\.csv$", os.path.basename(csv_path))
            run_tag = m.group(1) if m else datetime.now().strftime("%Y%m%d_%H%M%S")
        parsed = [row_to_bench_result(r) for r in scoped]
        return run_tag, parsed, csv_path

    return None, [], ""


def parse_extra_args(raw: str) -> List[str]:
    if not raw:
        return []
    try:
        return shlex.split(raw, posix=False)
    except ValueError as exc:
        raise ValueError(f"Invalid --llama-extra-args: {exc}") from exc


def clean_model_output(text: str) -> str:
    if not text:
        return ""
    cleaned = text
    cleaned = re.sub(r"(?is)<think>.*?</think>", " ", cleaned)
    cleaned = cleaned.replace("```sql", "").replace("```python", "").replace("```", "")
    cleaned = cleaned.strip()
    # Find the first code token — SQL or Python
    code_start = re.search(
        r"(?im)^(create|with|select|insert|update|delete|do|import |from |def |class )",
        cleaned,
    )
    if code_start:
        cleaned = cleaned[code_start.start():]
    return cleaned.strip()


def keyword_hit(lowered: str, keyword_spec) -> bool:
    """True if keyword_spec matches. List = OR-group (any one suffices)."""
    if isinstance(keyword_spec, str):
        return keyword_spec in lowered
    return any(str(item) in lowered for item in keyword_spec)


def save_model_artifact(
    output_dir: str,
    backend: str,
    model: str,
    case_id: str,
    run_id: int,
    text: str,
) -> str:
    cleaned = clean_model_output(text)
    if not cleaned:
        return ""
    artifact_dir = os.path.join(output_dir, "artifacts")
    os.makedirs(artifact_dir, exist_ok=True)
    safe_parts = [
        re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
        for value in (backend, model, case_id)
    ]
    extension = ".html" if re.search(r"(?is)<!doctype\s+html|<html(?:\s|>)", cleaned) else ".txt"
    filename = f"{safe_parts[0]}__{safe_parts[1]}__{safe_parts[2]}__run-{run_id}{extension}"
    path = os.path.join(artifact_dir, filename)
    with open(path, "w", encoding="utf-8", newline="\n") as artifact:
        artifact.write(cleaned)
        artifact.write("\n")
    return path


def score_output(text: str, required_keywords: List[object],
                 forbidden_keywords: Optional[List[object]] = None) -> Tuple[float, int, int]:
    """Score model output against required and forbidden keyword lists.

    Returns (score_pct, positive_hits, forbidden_hits).
    Scoring:
      - Start from positive score: hits / total * 100
      - Each forbidden keyword hit deducts same weight as one missed required keyword
      - Short output (<120 chars) penalty: -8 points
      - Score capped at 0 minimum
    """
    lowered = normalize(clean_model_output(text))
    hits = sum(1 for kw in required_keywords if keyword_hit(lowered, kw))
    base = (hits / len(required_keywords)) * 100.0

    forbidden_hits = 0
    if forbidden_keywords:
        per_kw_penalty = 100.0 / len(required_keywords)
        for fkw in forbidden_keywords:
            if keyword_hit(lowered, fkw):
                forbidden_hits += 1
                base -= per_kw_penalty

    if len(lowered) < 120:
        base -= 8.0

    # SWE-lite mode: stricter, pass@1-like proxy with fewer samples.
    if BENCHMARK_SCORING_MODE == "swe_lite":
        req_ratio = (hits / len(required_keywords)) if required_keywords else 0.0
        pass_like = req_ratio >= 0.85 and forbidden_hits == 0 and len(lowered) >= 160
        if pass_like:
            return 100.0, hits, forbidden_hits
        fail_score = (req_ratio * 70.0) - (forbidden_hits * 15.0)
        if len(lowered) < 160:
            fail_score -= 10.0
        return max(fail_score, 0.0), hits, forbidden_hits

    return max(base, 0.0), hits, forbidden_hits


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


def ollama_warmup_model(api_url: str, model: str, timeout_sec: int) -> float:
    start = time.perf_counter()
    payload = {
        "model": model,
        "prompt": "warmup",
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 1},
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec):
        pass
    return time.perf_counter() - start


def ollama_unload_model(api_url: str, model: str, timeout_sec: int) -> float:
    start = time.perf_counter()
    payload = {
        "model": model,
        "prompt": "",
        "stream": False,
        "keep_alive": 0,
    }
    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec):
        pass
    return time.perf_counter() - start


def run_ollama_case(
    model: str,
    case: Dict[str, object],
    run_id: int,
    args: argparse.Namespace,
) -> BenchResult:
    start = time.perf_counter()
    cpu_start = time.process_time()
    monitor = SystemMonitor(process_name_filters=["ollama"])
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
        quality, hits, forbidden_hits = score_output(text, case["required_keywords"], case.get("forbidden_keywords"))
        cleaned_text = clean_model_output(text)
        save_model_artifact(args.output_dir, "ollama", model, str(case["id"]), run_id, cleaned_text)

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
            forbidden_hits=forbidden_hits,
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
        forbidden_hits=0,
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
    monitor = SystemMonitor(process_name_filters=["llama"])
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
            forbidden_hits=0,
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
    command.extend(parse_extra_args(args.llama_extra_args))
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
                forbidden_hits=0,
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
        save_model_artifact(args.output_dir, "llama_cpp", model_name, str(case["id"]), run_id, output_text)
        quality, hits, forbidden_hits = score_output(output_text, case["required_keywords"], case.get("forbidden_keywords"))
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
            forbidden_hits=forbidden_hits,
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
        forbidden_hits=0,
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
    completed_keys: Optional[Set[Tuple[str, str, str, int]]] = None,
) -> List[BenchResult]:
    results: List[BenchResult] = []
    startup_sec: Optional[float] = None
    shutdown_sec: Optional[float] = None

    def finalize_results() -> List[BenchResult]:
        for r in results:
            if r.local_model_startup_sec is None:
                r.local_model_startup_sec = startup_sec
            if r.local_model_shutdown_sec is None:
                r.local_model_shutdown_sec = shutdown_sec
        return results

    completed = completed_keys or set()

    if not os.path.exists(model_path):
        for case in BENCH_TASKS:
            for run_id in range(1, args.runs + 1):
                if ("llama_cpp", model_name, str(case["id"]), run_id) in completed:
                    continue
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
                        forbidden_hits=0,
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
        return finalize_results()

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
        "-t",
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
    server_args.extend(parse_extra_args(args.llama_extra_args))

    boot_start = time.perf_counter()
    try:
        server_log = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
        server_log.close()
        proc = subprocess.Popen(server_args, stdout=open(server_log.name, "a", encoding="utf-8", errors="replace"), stderr=subprocess.STDOUT)
    except Exception as exc:
        startup_sec = time.perf_counter() - boot_start
        err = f"Failed to start llama-server: {exc}"
        for case in BENCH_TASKS:
            for run_id in range(1, args.runs + 1):
                if ("llama_cpp", model_name, str(case["id"]), run_id) in completed:
                    continue
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
                        forbidden_hits=0,
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
        return finalize_results()

    try:
        if not wait_for_server_ready(port, timeout_sec=min(args.timeout_sec, 240)):
            startup_sec = time.perf_counter() - boot_start
            err = f"llama-server not ready on port {port}"
            for case in BENCH_TASKS:
                for run_id in range(1, args.runs + 1):
                    if ("llama_cpp", model_name, str(case["id"]), run_id) in completed:
                        continue
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
                            forbidden_hits=0,
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
            return finalize_results()
        startup_sec = time.perf_counter() - boot_start

        for case in BENCH_TASKS:
            for run_id in range(1, args.runs + 1):
                if ("llama_cpp", model_name, str(case["id"]), run_id) in completed:
                    continue
                start = time.perf_counter()
                cpu_start = time.process_time()
                monitor = SystemMonitor(target_pids=[proc.pid], process_name_filters=["llama"])
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
                    save_model_artifact(
                        args.output_dir,
                        "llama_cpp",
                        model_name,
                        str(case["id"]),
                        run_id,
                        cleaned_text,
                    )
                    quality, hits, forbidden_hits = score_output(text, case["required_keywords"], case.get("forbidden_keywords"))
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
                            forbidden_hits=forbidden_hits,
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
                            forbidden_hits=0,
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
        shutdown_start = time.perf_counter()
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        shutdown_sec = time.perf_counter() - shutdown_start

    return finalize_results()


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
        save_model_artifact(args.output_dir, "siemens", model, str(case["id"]), run_id, cleaned_text)
        quality, hits, forbidden_hits = score_output(text, case["required_keywords"], case.get("forbidden_keywords"))
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
            forbidden_hits=forbidden_hits,
            avg_cpu_pct=None,
            max_cpu_pct=None,
            avg_mem_pct=None,
            max_mem_pct=None,
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
        forbidden_hits=0,
        avg_cpu_pct=None,
        max_cpu_pct=None,
        avg_mem_pct=None,
        max_mem_pct=None,
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


def save_results(
    results: List[BenchResult],
    output_dir: str,
    run_tag: Optional[str] = None,
    inprogress: bool = False,
) -> Tuple[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    ts = run_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "_inprogress" if inprogress else ""
    csv_path = os.path.join(output_dir, f"migration_llm_bench_{ts}{suffix}.csv")
    json_path = os.path.join(output_dir, f"migration_llm_bench_{ts}{suffix}.json")

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "benchmark_run_id",
                "benchmark_name",
                "benchmark_spec_version",
                "benchmark_script_file",
                "benchmark_task_count",
                "benchmark_runs",
                "benchmark_task_ids",
                "benchmark_task_hash",
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
                "forbidden_hits",
                "avg_cpu_pct",
                "max_cpu_pct",
                "avg_mem_pct",
                "max_mem_pct",
                "avg_gpu_pct",
                "max_gpu_pct",
                "avg_vram_used_mb",
                "max_vram_used_mb",
                "local_model_startup_sec",
                "local_model_shutdown_sec",
                "cpu_time_sec",
                "recorded_at",
                "output_preview",
                "error",
            ]
        )
        for r in results:
            writer.writerow(
                [
                    r.benchmark_run_id,
                    r.benchmark_name,
                    r.benchmark_spec_version,
                    r.benchmark_script_file,
                    r.benchmark_task_count,
                    r.benchmark_runs,
                    r.benchmark_task_ids,
                    r.benchmark_task_hash,
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
                    r.forbidden_hits,
                    f"{r.avg_cpu_pct:.2f}" if r.avg_cpu_pct is not None else "",
                    f"{r.max_cpu_pct:.2f}" if r.max_cpu_pct is not None else "",
                    f"{r.avg_mem_pct:.2f}" if r.avg_mem_pct is not None else "",
                    f"{r.max_mem_pct:.2f}" if r.max_mem_pct is not None else "",
                    f"{r.avg_gpu_pct:.2f}" if r.avg_gpu_pct is not None else "",
                    f"{r.max_gpu_pct:.2f}" if r.max_gpu_pct is not None else "",
                    f"{r.avg_vram_used_mb:.2f}" if r.avg_vram_used_mb is not None else "",
                    f"{r.max_vram_used_mb:.2f}" if r.max_vram_used_mb is not None else "",
                    f"{r.local_model_startup_sec:.3f}" if r.local_model_startup_sec is not None else "",
                    f"{r.local_model_shutdown_sec:.3f}" if r.local_model_shutdown_sec is not None else "",
                    f"{r.cpu_time_sec:.3f}",
                    r.recorded_at,
                    r.output_preview,
                    r.error,
                ]
            )

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump([r.__dict__ for r in results], f, indent=2)

    # Persist final run rows into a long-lived history CSV for cross-run reporting.
    if not inprogress:
        history_csv = os.path.join(output_dir, "migration_llm_bench_history.csv")
        history_header = [
            "source_csv",
            "benchmark_run_id",
            "benchmark_name",
            "benchmark_spec_version",
            "benchmark_script_file",
            "benchmark_task_count",
            "benchmark_runs",
            "benchmark_task_ids",
            "benchmark_task_hash",
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
            "forbidden_hits",
            "avg_cpu_pct",
            "max_cpu_pct",
            "avg_mem_pct",
            "max_mem_pct",
            "avg_gpu_pct",
            "max_gpu_pct",
            "avg_vram_used_mb",
            "max_vram_used_mb",
            "local_model_startup_sec",
            "local_model_shutdown_sec",
            "cpu_time_sec",
            "recorded_at",
            "output_preview",
            "error",
        ]
        write_header = not os.path.exists(history_csv)
        with open(history_csv, "a", newline="", encoding="utf-8") as hf:
            writer = csv.writer(hf)
            if write_header:
                writer.writerow(history_header)
            source_name = os.path.basename(csv_path)
            for r in results:
                writer.writerow(
                    [
                        source_name,
                        r.benchmark_run_id,
                        r.benchmark_name,
                        r.benchmark_spec_version,
                        r.benchmark_script_file,
                        r.benchmark_task_count,
                        r.benchmark_runs,
                        r.benchmark_task_ids,
                        r.benchmark_task_hash,
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
                        r.forbidden_hits,
                        f"{r.avg_cpu_pct:.2f}" if r.avg_cpu_pct is not None else "",
                        f"{r.max_cpu_pct:.2f}" if r.max_cpu_pct is not None else "",
                        f"{r.avg_mem_pct:.2f}" if r.avg_mem_pct is not None else "",
                        f"{r.max_mem_pct:.2f}" if r.max_mem_pct is not None else "",
                        f"{r.avg_gpu_pct:.2f}" if r.avg_gpu_pct is not None else "",
                        f"{r.max_gpu_pct:.2f}" if r.max_gpu_pct is not None else "",
                        f"{r.avg_vram_used_mb:.2f}" if r.avg_vram_used_mb is not None else "",
                        f"{r.max_vram_used_mb:.2f}" if r.max_vram_used_mb is not None else "",
                        f"{r.local_model_startup_sec:.3f}" if r.local_model_startup_sec is not None else "",
                        f"{r.local_model_shutdown_sec:.3f}" if r.local_model_shutdown_sec is not None else "",
                        f"{r.cpu_time_sec:.3f}",
                        r.recorded_at,
                        r.output_preview,
                        r.error,
                    ]
                )

    return csv_path, json_path


def update_markdown_report(results_dir: str, report_path: str, args: Optional[argparse.Namespace] = None) -> None:
    all_csvs = sorted(glob.glob(os.path.join(results_dir, "migration_llm_bench_*.csv")))
    if not all_csvs:
        return

    inprogress_files = sorted(p for p in all_csvs if p.endswith("_inprogress.csv"))
    # Group completed snapshots by run tag once, reused for live + finished modes.
    tag_to_files: Dict[str, List[str]] = {}
    for p in all_csvs:
        base = os.path.basename(p)
        m = re.match(r"migration_llm_bench_(\d{8}_\d{6})\.csv$", base)
        if not m:
            continue
        tag = m.group(1)
        tag_to_files.setdefault(tag, []).append(p)

    if inprogress_files:
        # Live view: include running snapshots plus latest completed snapshot
        # so finished backends (e.g., Siemens) remain visible in one compact table.
        csv_files = list(inprogress_files)
        if tag_to_files:
            latest_tag = sorted(tag_to_files.keys())[-1]
            csv_files.extend(tag_to_files[latest_tag])
        csv_files = sorted(set(csv_files))
    else:
        # Finished view: take latest completed run tag only
        if not tag_to_files:
            return
        latest_tag = sorted(tag_to_files.keys())[-1]
        csv_files = sorted(tag_to_files[latest_tag])

    if not csv_files:
        return

    all_rows: List[Dict[str, str]] = []
    header: List[str] = []
    for csv_file in csv_files:
        with open(csv_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames and not header:
                header = list(reader.fieldnames)
            for row in reader:
                row["_file"] = os.path.basename(csv_file)
                all_rows.append(row)

    if not all_rows:
        return
    system_ram_gb = psutil.virtual_memory().total / (1024.0 ** 3)
    live_mode = any("_inprogress.csv" in os.path.basename(p) for p in csv_files)

    def to_float(v: str) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return None

    def to_int(v: str) -> Optional[int]:
        if v is None or v == "":
            return None
        try:
            return int(float(v))
        except ValueError:
            return None

    def fmt_eta(sec: Optional[float]) -> str:
        if sec is None:
            return ""
        s = max(int(sec), 0)
        h = s // 3600
        m = (s % 3600) // 60
        r = s % 60
        return f"{h:02d}:{m:02d}:{r:02d}"

    def parse_ts(value: str) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def fmt_clock_or_date(value: str, base_day) -> str:
        ts = parse_ts(value)
        if ts is None:
            return value or ""
        if ts.date() == base_day:
            return ts.strftime("%H:%M:%S")
        return ts.strftime("%Y-%m-%d %H:%M:%S")

    grouped: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
    for row in all_rows:
        grouped.setdefault((row.get("backend", ""), row.get("model", "")), []).append(row)

    summary_rows = []
    for (backend, model), rows in grouped.items():
        ok = [r for r in rows if not (r.get("error") or "").strip()]
        def avg(col: str) -> Optional[float]:
            vals = [to_float(r.get(col, "")) for r in ok]
            vals = [v for v in vals if v is not None]
            return mean(vals) if vals else None
        def avg_all(col: str) -> Optional[float]:
            vals = [to_float(r.get(col, "")) for r in rows]
            vals = [v for v in vals if v is not None]
            return mean(vals) if vals else None
        def mn(col: str) -> Optional[float]:
            vals = [to_float(r.get(col, "")) for r in ok]
            vals = [v for v in vals if v is not None]
            return min(vals) if vals else None
        def mx(col: str) -> Optional[float]:
            vals = [to_float(r.get(col, "")) for r in ok]
            vals = [v for v in vals if v is not None]
            return max(vals) if vals else None

        reliability = (len(ok) / len(rows)) * 100.0 if rows else 0.0
        benchmark_name_vals = sorted({r.get("benchmark_name", "") for r in rows if r.get("benchmark_name", "")})
        benchmark_name = ", ".join(benchmark_name_vals) if benchmark_name_vals else ""
        swe_like = any(str(n).startswith("swe-") for n in benchmark_name_vals)
        quality = avg("quality_score")
        if swe_like and rows:
            pass_hits = 0
            for rr in rows:
                if (rr.get("error") or "").strip():
                    continue
                rr_q = to_float(rr.get("quality_score", "")) or 0.0
                rr_forbidden = to_int(rr.get("forbidden_hits", "")) or 0
                if rr_q >= BENCHMARK_SWE_PASS_THRESHOLD and rr_forbidden == 0:
                    pass_hits += 1
            quality = (pass_hits / len(rows)) * 100.0
        wall_ms = avg("wall_ms")
        tps = avg("output_tps")
        max_gpu = mx("max_gpu_pct")
        min_gpu = mn("avg_gpu_pct")
        total_wall_ms = sum(to_float(r.get("wall_ms", "")) or 0.0 for r in ok)
        recorded_vals = [r.get("recorded_at", "") for r in rows if r.get("recorded_at", "")]
        run_started_at = min(recorded_vals) if recorded_vals else ""
        run_last_updated_at = max(recorded_vals) if recorded_vals else ""
        task_count = to_int(rows[0].get("benchmark_task_count", "")) if rows else None
        run_count = to_int(rows[0].get("benchmark_runs", "")) if rows else None
        if run_count is None:
            run_vals = [to_int(r.get("run", "")) for r in rows]
            run_vals = [v for v in run_vals if v is not None]
            run_count = max(run_vals) if run_vals else None
        expected_samples = (task_count * run_count) if (task_count and run_count) else None
        error_count = len([r for r in rows if (r.get("error") or "").strip()])
        if error_count > 0:
            status = "error" if not live_mode else "running-error"
        elif expected_samples is not None and len(rows) >= expected_samples:
            status = "done"
        else:
            status = "running" if live_mode else "partial"
        mem_avg_pct = avg("avg_mem_pct")
        mem_min_pct = mn("avg_mem_pct")
        mem_max_pct = mx("max_mem_pct")
        vram_avg_mb = avg("avg_vram_used_mb")
        vram_min_mb = mn("avg_vram_used_mb")
        vram_max_mb = mx("max_vram_used_mb")
        local_startup_s = avg_all("local_model_startup_sec")
        local_shutdown_s = avg_all("local_model_shutdown_sec")
        avg_wall_s = (wall_ms / 1000.0) if wall_ms is not None else None
        remaining_samples = (expected_samples - len(rows)) if expected_samples is not None else None
        if remaining_samples is not None and remaining_samples < 0:
            remaining_samples = 0
        samples_display = f"{len(rows)}/{expected_samples}" if expected_samples is not None else f"{len(rows)}/?"
        eta_seconds = (remaining_samples * avg_wall_s) if (remaining_samples is not None and avg_wall_s is not None and status.startswith("running")) else 0.0 if status == "done" else None
        eta_end = (datetime.now() + timedelta(seconds=eta_seconds)).strftime("%Y-%m-%d %H:%M:%S") if eta_seconds is not None else ""

        summary_rows.append(
            {
                "backend": backend,
                "model": model,
                "benchmark_name": benchmark_name,
                "samples": len(rows),
                "samples_display": samples_display,
                "reliability": reliability,
                "quality": quality,
                "wall_ms": wall_ms,
                "tps": tps,
                "cpu": avg("avg_cpu_pct"),
                "mem": mem_avg_pct,
                "gpu": avg("avg_gpu_pct"),
                "vram_mb": vram_avg_mb,
                "min_gpu": min_gpu,
                "max_gpu": max_gpu,
                "min_mem": mem_min_pct,
                "max_mem": mem_max_pct,
                "min_vram_mb": vram_min_mb,
                "max_vram_mb": vram_max_mb,
                "vram_gb": (vram_avg_mb / 1024.0) if vram_avg_mb is not None else None,
                "min_vram_gb": (vram_min_mb / 1024.0) if vram_min_mb is not None else None,
                "max_vram_gb": (vram_max_mb / 1024.0) if vram_max_mb is not None else None,
                "total_wall_ms": total_wall_ms,
                "run_started_at": run_started_at,
                "run_last_updated_at": run_last_updated_at,
                "local_startup_s": local_startup_s,
                "local_shutdown_s": local_shutdown_s,
                "expected_samples": expected_samples,
                "remaining_samples": remaining_samples,
                "eta_seconds": eta_seconds,
                "eta_left": fmt_eta(eta_seconds),
                "eta_end": eta_end,
                "error_count": error_count,
                "status": status,
                "swe_like": swe_like,
            }
        )

    def clamp_0_100(value: Optional[float]) -> float:
        if value is None:
            return 0.0
        return max(0.0, min(100.0, float(value)))

    def speed_score_abs(tps: Optional[float], wall_ms: Optional[float]) -> float:
        # Stable absolute speed score (independent of which subset is currently visible).
        tps_part = min((float(tps) / 120.0) * 100.0, 100.0) if tps is not None else 0.0
        wall_part = min((10000.0 / float(wall_ms)) * 100.0, 100.0) if (wall_ms is not None and wall_ms > 0) else 0.0
        return 0.7 * tps_part + 0.3 * wall_part

    for row in summary_rows:
        score_quality = clamp_0_100(row["quality"])
        score_speed = speed_score_abs(row["tps"], row["wall_ms"])
        score_rel = clamp_0_100(row["reliability"])
        if bool(row.get("swe_like")):
            # SWE-style interpretation: score is solution quality (pass-like rate),
            # while performance stays a separate metric.
            overall = score_quality
        else:
            overall = 0.70 * score_quality + 0.20 * score_rel + 0.10 * score_speed
        row["overall"] = overall
        qv = row["quality"] if row["quality"] is not None else 0.0
        rv = row["reliability"] if row["reliability"] is not None else 0.0
        if bool(row.get("swe_like")):
            if rv < 90 or qv < 35 or overall < 40:
                row["note"] = "Not suitable"
            elif overall >= 70 and qv >= 70 and rv >= 98:
                row["note"] = "Very suitable"
            elif overall >= 50 and qv >= 50 and rv >= 95:
                row["note"] = "Suitable"
            else:
                row["note"] = "Limited suitability"
        else:
            if rv < 90 or qv < 70 or overall < 60:
                row["note"] = "Not suitable"
            elif overall >= 88 and qv >= 90 and rv >= 98:
                row["note"] = "Very suitable"
            elif overall >= 75 and qv >= 83 and rv >= 95:
                row["note"] = "Suitable"
            else:
                row["note"] = "Limited suitability"

    summary_rows.sort(key=lambda r: (r["overall"], r["quality"] if r["quality"] is not None else -1), reverse=True)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_runs = len(all_rows)
    total_completed_samples = sum(int(r["samples"]) for r in summary_rows)
    total_expected_samples: Optional[int] = None
    expected_vals = [r["expected_samples"] for r in summary_rows if r.get("expected_samples") is not None]
    if expected_vals and len(expected_vals) == len(summary_rows):
        total_expected_samples = sum(int(v) for v in expected_vals if v is not None)
    total_remaining_samples = (
        max(total_expected_samples - total_completed_samples, 0)
        if total_expected_samples is not None
        else None
    )
    progress_display = (
        f"{total_completed_samples}/{total_expected_samples}"
        if total_expected_samples is not None
        else f"{total_completed_samples}/?"
    )
    time_marks = [parse_ts(r.get("recorded_at", "")) for r in all_rows]
    time_marks = [t for t in time_marks if t is not None]
    api_elapsed_seconds = sum((to_float(r.get("wall_ms", "")) or 0.0) for r in all_rows) / 1000.0
    overall_eta_seconds: Optional[float] = None
    overall_samples_per_min: Optional[float] = None
    if total_remaining_samples == 0 and total_expected_samples is not None:
        overall_eta_seconds = 0.0
    elif total_remaining_samples is not None and api_elapsed_seconds > 0 and total_completed_samples > 0:
        samples_per_second = total_completed_samples / api_elapsed_seconds
        if samples_per_second > 0:
            overall_eta_seconds = total_remaining_samples / samples_per_second
            overall_samples_per_min = samples_per_second * 60.0
    if overall_eta_seconds is None:
        per_model_etas = [r.get("eta_seconds") for r in summary_rows if isinstance(r.get("eta_seconds"), (int, float))]
        if per_model_etas:
            overall_eta_seconds = max(float(v) for v in per_model_etas)
    overall_eta_end = (
        (datetime.now() + timedelta(seconds=overall_eta_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        if overall_eta_seconds is not None
        else ""
    )
    report_day = datetime.now().date()
    overall_started_at = min(time_marks).strftime("%Y-%m-%d %H:%M:%S") if time_marks else ""
    overall_elapsed_seconds: Optional[float] = api_elapsed_seconds if api_elapsed_seconds > 0 else None
    overall_total_est_seconds: Optional[float] = None
    if overall_elapsed_seconds is not None and overall_eta_seconds is not None:
        overall_total_est_seconds = overall_elapsed_seconds + overall_eta_seconds
    report_dir = os.path.dirname(report_path)
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)

    # Cross-benchmark overview from persistent history.
    history_summary_rows: List[Dict[str, object]] = []
    history_csv = os.path.join(results_dir, "migration_llm_bench_history.csv")
    if os.path.exists(history_csv):
        try:
            with open(history_csv, newline="", encoding="utf-8") as hf:
                hreader = csv.DictReader(hf)
                hrows = [r for r in hreader if r.get("benchmark_name", "")]
            hgrouped: Dict[Tuple[str, str, str], List[Dict[str, str]]] = {}
            for hr in hrows:
                hgrouped.setdefault((hr.get("benchmark_name", ""), hr.get("backend", ""), hr.get("model", "")), []).append(hr)
            for (bench_name, backend_name, model_name), rows_h in hgrouped.items():
                ok_h = [r for r in rows_h if not (r.get("error") or "").strip()]
                if not ok_h:
                    continue
                quality_h = mean([to_float(r.get("quality_score", "")) or 0.0 for r in ok_h])
                wall_h = mean([to_float(r.get("wall_ms", "")) or 0.0 for r in ok_h])
                tps_vals_h = [to_float(r.get("output_tps", "")) for r in ok_h]
                tps_vals_h = [v for v in tps_vals_h if v is not None]
                tps_h = mean(tps_vals_h) if tps_vals_h else None
                cpu_vals_h = [to_float(r.get("avg_cpu_pct", "")) for r in ok_h]
                cpu_vals_h = [v for v in cpu_vals_h if v is not None]
                cpu_h = mean(cpu_vals_h) if cpu_vals_h else None
                gpu_vals_h = [to_float(r.get("avg_gpu_pct", "")) for r in ok_h]
                gpu_vals_h = [v for v in gpu_vals_h if v is not None]
                gpu_h = mean(gpu_vals_h) if gpu_vals_h else None
                mem_vals_h = [to_float(r.get("avg_mem_pct", "")) for r in ok_h]
                mem_vals_h = [v for v in mem_vals_h if v is not None]
                mem_pct_h = mean(mem_vals_h) if mem_vals_h else None
                vram_vals_h = [to_float(r.get("avg_vram_used_mb", "")) for r in ok_h]
                vram_vals_h = [v for v in vram_vals_h if v is not None]
                vram_mb_h = mean(vram_vals_h) if vram_vals_h else None
                rel_h = (len(ok_h) / len(rows_h)) * 100.0 if rows_h else 0.0
                error_count_h = len([r for r in rows_h if (r.get("error") or "").strip()])
                error_rate_h = (error_count_h / len(rows_h)) * 100.0 if rows_h else 0.0
                swe_like_h = str(bench_name).startswith("swe-")
                if swe_like_h:
                    pass_hits_h = 0
                    for rrh in rows_h:
                        if (rrh.get("error") or "").strip():
                            continue
                        rrh_q = to_float(rrh.get("quality_score", "")) or 0.0
                        rrh_forbidden = to_int(rrh.get("forbidden_hits", "")) or 0
                        if rrh_q >= BENCHMARK_SWE_PASS_THRESHOLD and rrh_forbidden == 0:
                            pass_hits_h += 1
                    quality_h = (pass_hits_h / len(rows_h)) * 100.0
                score_h = quality_h
                history_summary_rows.append(
                    {
                        "benchmark": bench_name,
                        "backend": backend_name,
                        "model": model_name,
                        "score": score_h,
                        "quality": quality_h,
                        "system_errors": error_count_h,
                        "system_error_rate": error_rate_h,
                        "run_success_rate": rel_h,
                        "wall_s": wall_h / 1000.0 if wall_h is not None else None,
                        "tps": tps_h,
                        "avg_cpu": cpu_h,
                        "avg_gpu": gpu_h,
                        "avg_ram_gb": ((mem_pct_h / 100.0) * system_ram_gb) if mem_pct_h is not None else None,
                        "avg_vram_gb": (vram_mb_h / 1024.0) if vram_mb_h is not None else None,
                    }
                )
            history_summary_rows.sort(key=lambda r: (str(r["benchmark"]), str(r["backend"]), -(float(r["score"]) if r.get("score") is not None else 0.0), str(r["model"])))
        except OSError:
            history_summary_rows = []

    def fmt(v: Optional[float], digits: int = 2) -> str:
        return f"{v:,.{digits}f}" if v is not None else ""

    def pct_to_gb(pct: Optional[float]) -> Optional[float]:
        if pct is None:
            return None
        return (pct / 100.0) * system_ram_gb

    def write_model_chart_svg(path: str, rows: List[Dict[str, object]], generated_at: str, benchmark_label: str) -> None:
        if not rows:
            return
        ranked = sorted(rows, key=lambda r: float(r.get("overall", 0.0) or 0.0), reverse=True)
        n = len(ranked)
        height = 130 + n * 34
        max_runtime = max(((float(r.get("wall_ms", 0.0) or 0.0) / 1000.0) for r in ranked), default=0.0)
        score_scale = 2.5  # 0..100 -> 0..250 px
        runtime_scale = (260.0 / max_runtime) if max_runtime > 0 else 0.0
        svg: List[str] = []
        svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="1220" height="{height}" viewBox="0 0 1220 {height}">')
        svg.append('<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f8fbff"/><stop offset="100%" stop-color="#eef4ff"/></linearGradient></defs>')
        svg.append('<rect x="0" y="0" width="1220" height="100%" fill="url(#bg)"/>')
        svg.append('<rect x="8" y="8" width="1204" height="48" rx="8" fill="#ffffff" stroke="#dbeafe"/>')
        svg.append('<text x="18" y="30" font-family="Segoe UI, Arial" font-size="18" font-weight="600" fill="#0f172a">Benchmark model overview</text>')
        svg.append(f'<text x="18" y="47" font-family="Segoe UI, Arial" font-size="12" fill="#334155">Benchmark: {html.escape(benchmark_label)} | Updated: {html.escape(generated_at)}</text>')
        svg.append('<text x="540" y="78" font-family="Segoe UI, Arial" font-size="12" fill="#334155">Heuristik-Score (0-100)</text>')
        svg.append('<text x="848" y="78" font-family="Segoe UI, Arial" font-size="12" fill="#334155">Runtime avg (s)</text>')
        for i, row in enumerate(ranked):
            y = 96 + (i * 34)
            model = html.escape(str(row.get("model", "")))
            backend = html.escape(str(row.get("backend", "")))
            benchmark = html.escape(str(row.get("benchmark_name", "")))
            score = float(row.get("overall", 0.0) or 0.0)
            runtime_s = float(row.get("wall_ms", 0.0) or 0.0) / 1000.0
            score_w = max(score * score_scale, 0.0)
            runtime_w = max(runtime_s * runtime_scale, 0.0)
            svg.append(f'<rect x="12" y="{y-11}" width="1196" height="28" rx="6" fill="#ffffff" stroke="#e5e7eb"/>')
            svg.append(f'<text x="18" y="{y + 7}" font-family="Consolas, monospace" font-size="11" fill="#1f2937">{benchmark} | {backend} | {model}</text>')
            svg.append(f'<rect x="540" y="{y-2}" width="{score_w:.1f}" height="12" fill="#2563eb"/>')
            svg.append(f'<text x="796" y="{y + 7}" font-family="Consolas, monospace" font-size="11" fill="#0f172a">{score:5.1f}</text>')
            svg.append(f'<rect x="848" y="{y-2}" width="{runtime_w:.1f}" height="12" fill="#f59e0b"/>')
            svg.append(f'<text x="1114" y="{y + 7}" font-family="Consolas, monospace" font-size="11" fill="#0f172a">{runtime_s:7.2f}s</text>')
        svg.append("</svg>")
        with open(path, "w", encoding="utf-8") as fsvg:
            fsvg.write("\n".join(svg))

    sortable_assets = [
        "<style>",
        ".table-filter-wrap { margin: 6px 0 8px 0; }",
        ".table-filter-input { min-width: 260px; max-width: 420px; padding: 4px 8px; }",
        "table.sortable thead th { cursor: pointer; user-select: none; }",
        "table.sortable thead th.sort-asc::after { content: ' ▲'; }",
        "table.sortable thead th.sort-desc::after { content: ' ▼'; }",
        "</style>",
        "<script>",
        "(function () {",
        "  function parseDuration(v) {",
        "    var m = String(v || '').trim().match(/^(\\d+):(\\d{2}):(\\d{2})$/);",
        "    if (!m) return null;",
        "    return (parseInt(m[1], 10) * 3600) + (parseInt(m[2], 10) * 60) + parseInt(m[3], 10);",
        "  }",
        "  function parseProgress(v) {",
        "    var m = String(v || '').trim().match(/^(\\d+)\\/(\\d+)$/);",
        "    if (!m) return null;",
        "    return (parseInt(m[1], 10) * 1000000) + parseInt(m[2], 10);",
        "  }",
        "  function keyFor(text) {",
        "    var s = String(text || '').trim();",
        "    var n = Number(s.replace(/,/g, ''));",
        "    if (!Number.isNaN(n) && s !== '') return { t: 'n', v: n };",
        "    var d = parseDuration(s);",
        "    if (d !== null) return { t: 'n', v: d };",
        "    var p = parseProgress(s);",
        "    if (p !== null) return { t: 'n', v: p };",
        "    var dt = Date.parse(s);",
        "    if (!Number.isNaN(dt)) return { t: 'n', v: dt };",
        "    return { t: 's', v: s.toLowerCase() };",
        "  }",
        "  function cmp(a, b) {",
        "    if (a.t === b.t && a.t === 'n') return a.v - b.v;",
        "    return String(a.v).localeCompare(String(b.v), undefined, { numeric: true, sensitivity: 'base' });",
        "  }",
        "  function bindFilter(table) {",
        "    var tbody = table.querySelector('tbody');",
        "    if (!tbody) return;",
        "    var wrap = document.createElement('div');",
        "    wrap.className = 'table-filter-wrap';",
        "    var input = document.createElement('input');",
        "    input.type = 'search';",
        "    input.className = 'table-filter-input';",
        "    input.placeholder = 'Filter rows...';",
        "    wrap.appendChild(input);",
        "    table.parentNode.insertBefore(wrap, table);",
        "    input.addEventListener('input', function () {",
        "      var q = String(input.value || '').trim().toLowerCase();",
        "      Array.from(tbody.querySelectorAll('tr')).forEach(function (tr) {",
        "        var hit = tr.textContent.toLowerCase().indexOf(q) >= 0;",
        "        tr.style.display = hit ? '' : 'none';",
        "      });",
        "    });",
        "  }",
        "  function bindSortable(table) {",
        "    var headers = table.querySelectorAll('thead th');",
        "    var tbody = table.querySelector('tbody');",
        "    if (!headers.length || !tbody) return;",
        "    headers.forEach(function (th, idx) {",
        "      th.addEventListener('click', function () {",
        "        var rows = Array.from(tbody.querySelectorAll('tr'));",
        "        var nextAsc = !th.classList.contains('sort-asc');",
        "        headers.forEach(function (h) { h.classList.remove('sort-asc', 'sort-desc'); });",
        "        th.classList.add(nextAsc ? 'sort-asc' : 'sort-desc');",
        "        rows.sort(function (ra, rb) {",
        "          var ka = keyFor((ra.children[idx] || {}).textContent || '');",
        "          var kb = keyFor((rb.children[idx] || {}).textContent || '');",
        "          var r = cmp(ka, kb);",
        "          return nextAsc ? r : -r;",
        "        });",
        "        rows.forEach(function (r) { tbody.appendChild(r); });",
        "      });",
        "    });",
        "  }",
        "  document.querySelectorAll('table.sortable').forEach(function (table) {",
        "    bindFilter(table);",
        "    bindSortable(table);",
        "  });",
        "})();",
        "</script>",
    ]

    lines: List[str] = []
    lines.append("# Benchmark Report")
    lines.append("")
    lines.append(f"- Generated: `{generated}`")
    lines.append(f"- Source files: `{len(csv_files)}` CSV")
    lines.append(f"- Total testcase runs: `{total_runs}`")
    lines.append(f"- Overall progress: `{progress_display}`")
    lines.append(f"- Overall ETA left: `{fmt_eta(overall_eta_seconds) if overall_eta_seconds is not None else 'n/a'}`")
    lines.append(f"- Overall ETA end: `{overall_eta_end if overall_eta_end else 'n/a'}`")
    if overall_samples_per_min is not None:
        lines.append(f"- Effective throughput: `{overall_samples_per_min:.2f} samples/min`")
    benchmark_names = sorted({r.get("benchmark_name", "") for r in all_rows if r.get("benchmark_name")})
    benchmark_versions = sorted({r.get("benchmark_spec_version", "") for r in all_rows if r.get("benchmark_spec_version")})
    benchmark_scripts = sorted({r.get("benchmark_script_file", "") for r in all_rows if r.get("benchmark_script_file")})
    benchmark_task_counts = sorted({r.get("benchmark_task_count", "") for r in all_rows if r.get("benchmark_task_count")})
    lines.append(f"- Benchmark name(s): `{', '.join(benchmark_names) if benchmark_names else 'n/a'}`")
    lines.append(f"- Benchmark version(s): `{', '.join(benchmark_versions) if benchmark_versions else 'n/a'}`")
    lines.append(f"- Benchmark script(s): `{', '.join(benchmark_scripts) if benchmark_scripts else 'n/a'}`")
    lines.append(f"- Benchmark task count(s): `{', '.join(benchmark_task_counts) if benchmark_task_counts else 'n/a'}`")
    lines.append("- Score semantics: `Heuristik-Score (keyword/rule-basiert, nicht SWE-offizieller Pass/Fail-Score)`")
    lines.append("")
    history_section_lines: List[str] = []
    if history_summary_rows:
        history_section_lines.append("## Cross-benchmark overview (history)")
        history_section_lines.append("")
        history_section_lines.append("<table>")
        history_section_lines.append("<thead><tr><th>Benchmark</th><th>Backend</th><th>Model</th><th>Heuristik-Score</th><th>Quality</th><th>System errors</th><th>System error %</th><th>Tok/s</th><th>CPU%(avg)</th><th>GPU%(avg)</th><th>RAM GB(avg)</th><th>VRAM GB(avg)</th><th>Wall-s(avg)</th></tr></thead>")
        history_section_lines.append("<tbody>")
        for hr in history_summary_rows:
            history_section_lines.append(
                "<tr>"
                f"<td>{html.escape(str(hr['benchmark']))}</td>"
                f"<td>{html.escape(str(hr['backend']))}</td>"
                f"<td>{html.escape(str(hr['model']))}</td>"
                f"<td>{fmt(hr.get('score'))}</td>"
                f"<td>{fmt(hr.get('quality'))}</td>"
                f"<td>{int(hr.get('system_errors') or 0)}</td>"
                f"<td>{fmt(hr.get('system_error_rate'))}</td>"
                f"<td>{fmt(hr.get('tps'))}</td>"
                f"<td>{fmt(hr.get('avg_cpu'))}</td>"
                f"<td>{fmt(hr.get('avg_gpu'))}</td>"
                f"<td>{fmt(hr.get('avg_ram_gb'))}</td>"
                f"<td>{fmt(hr.get('avg_vram_gb'))}</td>"
                f"<td>{fmt(hr.get('wall_s'), 2)}</td>"
                "</tr>"
            )
        history_section_lines.append("</tbody></table>")
        history_section_lines.append("")
    if args is not None:
        lines.append("## Current run settings")
        lines.append("")
        settings = [
            ("benchmark_id", args.benchmark_id),
            ("benchmark_file", args.benchmark_file),
            ("backend", args.backend),
            ("runs", args.runs),
            ("temp", args.temp),
            ("top_p", args.top_p),
            ("repeat_penalty", args.repeat_penalty),
            ("timeout_sec", args.timeout_sec),
            ("max_tokens", args.max_tokens),
            ("threads", args.threads),
            ("ngl", args.ngl),
            ("llama_server", args.llama_server),
            ("llama_batch_size", args.llama_batch_size),
            ("llama_ubatch_size", args.llama_ubatch_size),
            ("llama_fit_target_mib", args.llama_fit_target_mib),
            ("llama_reasoning", args.llama_reasoning),
            ("llama_extra_args", args.llama_extra_args),
            ("siemens_workers", args.siemens_workers),
        ]
        lines.append("<table>")
        lines.append("<thead><tr><th>Setting</th><th>Value</th></tr></thead>")
        lines.append("<tbody>")
        for key, value in settings:
            lines.append(f"<tr><td>{html.escape(str(key))}</td><td>{html.escape(str(value))}</td></tr>")
        lines.append("</tbody></table>")
        lines.append("")

    campaign_path = os.path.join("scripts", "run_local_campaign.ps1")
    campaign_text = ""
    campaign_ollama_models: List[str] = []
    campaign_llama_models: List[str] = []
    if os.path.exists(campaign_path):
        try:
            with open(campaign_path, encoding="utf-8") as f:
                campaign_text = f.read()
            extra_args = sorted(set(re.findall(r'--llama-extra-args",\s*"([^"]+)"', campaign_text)))
            ngl_args = sorted(set(re.findall(r'--llama-ngl",\s*"([^"]+)"', campaign_text)))
            llama_models_raw = re.findall(r'--llama-model",\s*"([^"]+)"', campaign_text)
            ollama_models_raw = re.findall(r'--ollama-model",\s*"([^"]+)"', campaign_text)
            llama_models = sorted(set(llama_models_raw))
            ollama_models = sorted(set(ollama_models_raw))
            campaign_ollama_models = list(ollama_models_raw)
            campaign_llama_models = [m.split("=", 1)[0].strip() for m in llama_models_raw if "=" in m]
            lines.append("## Configured llama.cpp campaign settings")
            lines.append("")
            lines.append("<table>")
            lines.append("<thead><tr><th>Key</th><th>Values</th></tr></thead>")
            lines.append("<tbody>")
            lines.append(f"<tr><td>ollama-models</td><td>{html.escape(' | '.join(ollama_models) if ollama_models else '')}</td></tr>")
            lines.append(f"<tr><td>llama-ngl</td><td>{html.escape(', '.join(ngl_args) if ngl_args else '')}</td></tr>")
            lines.append(f"<tr><td>llama-extra-args</td><td>{html.escape(' | '.join(extra_args) if extra_args else '')}</td></tr>")
            lines.append(f"<tr><td>llama-model specs</td><td>{html.escape(' | '.join(llama_models) if llama_models else '')}</td></tr>")
            lines.append("</tbody></table>")
            lines.append("")
        except OSError:
            pass

    summary_by_key: Dict[Tuple[str, str], Dict[str, object]] = {
        (str(r["backend"]), str(r["model"])): r for r in summary_rows
    }
    planned_model_keys = set(summary_by_key.keys())
    if args is not None:
        if args.backend in ("siemens", "all"):
            for m in (args.siemens_models or SIEMENS_DEFAULT_MODELS):
                planned_model_keys.add(("siemens", m))
        if args.backend in ("ollama", "both", "all"):
            for m in (args.ollama_models or []):
                planned_model_keys.add(("ollama", m))
        if args.backend in ("llama_cpp", "both", "all"):
            for name, _ in parse_llama_model_specs(args.llama_models or []):
                planned_model_keys.add(("llama_cpp", name))
    for m in campaign_ollama_models:
        planned_model_keys.add(("ollama", m))
    for m in campaign_llama_models:
        planned_model_keys.add(("llama_cpp", m))

    run_count_default = to_int(str(args.runs)) if args is not None else None
    if run_count_default is None:
        run_vals = [to_int(r.get("run", "")) for r in all_rows]
        run_vals = [v for v in run_vals if v is not None]
        run_count_default = max(run_vals) if run_vals else 1
    task_defs = [(str(t["id"]), str(t["title"])) for t in BENCH_TASKS]
    successful_wall_seconds: List[float] = []
    for row in summary_rows:
        try:
            wall_seconds = float(row["wall_ms"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        if wall_seconds > 0:
            successful_wall_seconds.append(wall_seconds)
    global_avg_wall_s = mean(successful_wall_seconds) if successful_wall_seconds else 10.0
    if global_avg_wall_s <= 0:
        global_avg_wall_s = 10.0

    row_lookup: Dict[Tuple[str, str, str, int], Dict[str, str]] = {}
    for row in all_rows:
        run_no = to_int(row.get("run", ""))
        if run_no is None:
            continue
        k = (row.get("backend", ""), row.get("model", ""), row.get("case_id", ""), run_no)
        prev = row_lookup.get(k)
        if prev is None or (row.get("recorded_at", "") > prev.get("recorded_at", "")):
            row_lookup[k] = row

    planned_rows: List[Dict[str, object]] = []
    for backend, model in sorted(planned_model_keys):
        sm = summary_by_key.get((backend, model))
        model_started_dt = parse_ts(str(sm["run_started_at"])) if sm and sm.get("run_started_at") else None
        model_last_update = str(sm["run_last_updated_at"]) if sm else ""
        model_eta_left = str(sm["eta_left"]) if sm else ""
        model_progress = str(sm["samples_display"]) if sm else f"0/{len(task_defs) * int(run_count_default or 1)}"
        model_overall = float(sm["overall"]) if sm and sm.get("overall") is not None else None
        model_status = str(sm["status"]) if sm else "scheduled"
        avg_sample_s = (float(sm["wall_ms"]) / 1000.0) if (sm and sm.get("wall_ms") is not None) else global_avg_wall_s
        if avg_sample_s <= 0:
            avg_sample_s = global_avg_wall_s
        running_key: Optional[Tuple[str, str, str, int]] = None
        ordered_keys: List[Tuple[str, str, str, int]] = []
        for case_id, _case_title in task_defs:
            for run_no in range(1, int(run_count_default or 1) + 1):
                ordered_keys.append((backend, model, case_id, run_no))
        missing = [k for k in ordered_keys if k not in row_lookup]
        if model_status.startswith("running") and missing:
            running_key = missing[0]
        for idx, (b, m, case_id, run_no) in enumerate(ordered_keys, start=1):
            existing = row_lookup.get((b, m, case_id, run_no))
            planned_dt: Optional[datetime] = None
            if model_started_dt is not None:
                planned_dt = model_started_dt + timedelta(seconds=(idx - 1) * avg_sample_s)
            planned_start = planned_dt.strftime("%Y-%m-%d %H:%M:%S") if planned_dt else ""
            if existing is not None:
                row_status = "error" if (existing.get("error", "").strip()) else "done"
                run_started = planned_start or str(sm["run_started_at"]) if sm else ""
                last_update = existing.get("recorded_at", "")
            else:
                row_status = "running" if running_key == (b, m, case_id, run_no) else "scheduled"
                run_started = str(sm["run_started_at"]) if (row_status == "running" and sm) else ""
                last_update = model_last_update if row_status == "running" else ""
            planned_rows.append(
                {
                    "benchmark": str(sm["benchmark_name"]) if sm else (benchmark_names[0] if benchmark_names else ""),
                    "backend": b,
                    "model": m,
                    "case_id": case_id,
                    "run": run_no,
                    "status": row_status,
                    "planned_start": planned_start,
                    "run_started": run_started,
                    "last_update": last_update,
                    "model_progress": model_progress,
                    "model_eta_left": model_eta_left,
                    "model_overall": model_overall,
                    "model_wall_s": avg_sample_s,
                }
            )

    model_live_rows: List[Dict[str, object]] = []
    now_dt = datetime.now()
    per_model_plans: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for pr in planned_rows:
        per_model_plans.setdefault((str(pr["backend"]), str(pr["model"])), []).append(pr)
    base_model_rows: Dict[Tuple[str, str], Dict[str, object]] = {}
    for (backend, model), rows_m in sorted(per_model_plans.items()):
        status_vals = [str(r["status"]) for r in rows_m]
        done_count = len([s for s in status_vals if s == "done"])
        err_count = len([s for s in status_vals if s == "error"])
        run_count_live = len([s for s in status_vals if s == "running"])
        sched_count = len([s for s in status_vals if s == "scheduled"])
        total_count = len(rows_m)
        if err_count > 0 and done_count < total_count:
            model_status = "running-error" if (run_count_live > 0 or sched_count > 0) else "error"
        elif done_count == total_count and total_count > 0:
            model_status = "done"
        elif run_count_live > 0:
            model_status = "running"
        else:
            model_status = "scheduled"
        planned_starts = [str(r["planned_start"]) for r in rows_m if str(r["planned_start"])]
        run_starts = [str(r["run_started"]) for r in rows_m if str(r["run_started"])]
        last_updates = [str(r["last_update"]) for r in rows_m if str(r["last_update"])]
        score_vals = [r["model_overall"] for r in rows_m if isinstance(r.get("model_overall"), (int, float))]
        wall_vals = [r["model_wall_s"] for r in rows_m if isinstance(r.get("model_wall_s"), (int, float))]
        benchmark_vals = [str(r["benchmark"]) for r in rows_m if str(r["benchmark"])]
        sm = summary_by_key.get((backend, model))
        run_started_ts = parse_ts(min(run_starts) if run_starts else "")
        last_update_ts = parse_ts(max(last_updates) if last_updates else "")
        elapsed_seconds: Optional[float] = (float(sm["total_wall_ms"]) / 1000.0) if (sm and sm.get("total_wall_ms") is not None) else None
        eta_left_seconds = float(sm["eta_seconds"]) if (sm and isinstance(sm.get("eta_seconds"), (int, float))) else None
        base_model_rows[(backend, model)] = {
            "benchmark": benchmark_vals[0] if benchmark_vals else (benchmark_names[0] if benchmark_names else ""),
            "backend": backend,
            "model": model,
            "status": model_status,
            "run_started": min(run_starts) if run_starts else "",
            "planned_start": min(planned_starts) if planned_starts else "",
            "last_update": max(last_updates) if last_updates else "",
            "progress": f"{done_count}/{total_count}",
            "done_count": done_count,
            "total_count": total_count,
            "elapsed_seconds": elapsed_seconds,
            "elapsed": fmt_eta(elapsed_seconds),
            "eta_left": str(sm["eta_left"]) if sm else "",
            "elapsed_left_est": fmt_eta(eta_left_seconds) if eta_left_seconds is not None else "",
            "eta_end": str(sm["eta_end"]) if sm else "",
            "score": float(sm["overall"]) if (sm and sm.get("overall") is not None) else (mean(score_vals) if score_vals else None),
            "wall_s": (float(sm["wall_ms"]) / 1000.0) if (sm and sm.get("wall_ms") is not None) else (mean(wall_vals) if wall_vals else None),
            "tps": float(sm["tps"]) if (sm and sm.get("tps") is not None) else None,
            "avg_cpu": float(sm["cpu"]) if (sm and sm.get("cpu") is not None) else None,
            "avg_gpu": float(sm["gpu"]) if (sm and sm.get("gpu") is not None) else None,
            "used_ram_gb": pct_to_gb(float(sm["mem"])) if (sm and sm.get("mem") is not None) else None,
            "used_vram_gb": float(sm["vram_gb"]) if (sm and sm.get("vram_gb") is not None) else None,
            "llm_startuptime": float(sm["local_startup_s"]) if (sm and sm.get("local_startup_s") is not None) else None,
            "llm_shutdowntime": float(sm["local_shutdown_s"]) if (sm and sm.get("local_shutdown_s") is not None) else None,
            "rating": str(sm["note"]) if sm else "",
        }

    planned_models_by_backend: Dict[str, Set[str]] = {"siemens": set(), "ollama": set(), "llama_cpp": set()}
    siemens_present = any(k[0] == "siemens" for k in base_model_rows.keys()) or any(r.get("backend", "") == "siemens" for r in all_rows)
    if siemens_present or (args is not None and args.backend in ("siemens", "all")):
        planned_models_by_backend["siemens"].update(SIEMENS_DEFAULT_MODELS)
    planned_models_by_backend["ollama"].update(campaign_ollama_models)
    planned_models_by_backend["llama_cpp"].update(campaign_llama_models)
    configured_llama_models: Set[str] = set(campaign_llama_models)
    if args is not None:
        planned_models_by_backend["ollama"].update(args.ollama_models or [])
        planned_models_by_backend["siemens"].update(args.siemens_models or [])
        for spec in (args.llama_models or []):
            if "=" in spec:
                llama_name = spec.split("=", 1)[0].strip()
                planned_models_by_backend["llama_cpp"].add(llama_name)
                configured_llama_models.add(llama_name)

    # User-facing planning target: keep 5 model rows per backend, but never with
    # synthetic "slot-*" names. Missing local llama.cpp entries are shown as
    # not configured using known model names from the Ollama plan.
    if len(planned_models_by_backend["llama_cpp"]) < 5:
        for ollama_model_name in sorted(planned_models_by_backend["ollama"]):
            if len(planned_models_by_backend["llama_cpp"]) >= 5:
                break
            if ollama_model_name not in planned_models_by_backend["llama_cpp"]:
                planned_models_by_backend["llama_cpp"].add(ollama_model_name)

    planned_model_keys: Set[Tuple[str, str]] = set()
    for backend_name, models_set in planned_models_by_backend.items():
        for model_name in sorted(models_set):
            planned_model_keys.add((backend_name, model_name))

    for (backend, model), base in sorted(base_model_rows.items()):
        model_live_rows.append(dict(base))

    for (backend, model) in sorted(planned_model_keys):
        if (backend, model) in base_model_rows:
            continue
        status = "scheduled"
        if backend == "llama_cpp" and model not in configured_llama_models:
            status = "not configured"
        model_live_rows.append(
            {
                "benchmark": benchmark_names[0] if benchmark_names else "",
                "backend": backend,
                "model": model,
                "status": status,
                "run_started": "",
                "planned_start": "",
                "last_update": "",
                "progress": "0/33" if status == "scheduled" else "n/a",
                "done_count": 0,
                "total_count": 0,
                "elapsed_seconds": None,
                "elapsed": "",
                "eta_left": "",
                "elapsed_left_est": "",
                "eta_end": "",
                "score": None,
                "wall_s": None,
                "tps": None,
                "avg_cpu": None,
                "avg_gpu": None,
                "used_ram_gb": None,
                "used_vram_gb": None,
                "llm_startuptime": None,
                "llm_shutdowntime": None,
                "rating": "",
            }
        )

    # Fill missing planned starts for scheduled rows to improve timeline + global ETA.
    duration_by_backend: Dict[str, float] = {}
    for b in ("siemens", "ollama", "llama_cpp"):
        vals: List[float] = []
        for r in model_live_rows:
            if str(r.get("backend")) != b:
                continue
            done_n = int(r.get("done_count") or 0)
            total_n = int(r.get("total_count") or 0)
            elapsed_s = r.get("elapsed_seconds")
            wall_s = r.get("wall_s")
            if total_n <= 0:
                continue
            if isinstance(elapsed_s, (int, float)) and done_n > 0:
                vals.append(float(elapsed_s) * float(total_n) / float(done_n))
            elif isinstance(wall_s, (int, float)) and float(wall_s) > 0:
                vals.append(float(wall_s) * float(total_n))
        if vals:
            duration_by_backend[b] = mean(vals)
    global_duration_fallback = mean(duration_by_backend.values()) if duration_by_backend else 24.0 * 60.0
    for b in ("siemens", "ollama", "llama_cpp"):
        duration_by_backend.setdefault(b, global_duration_fallback)

    local_backends = {"ollama", "llama_cpp"}
    local_cursor = now_dt
    for r in model_live_rows:
        if str(r.get("backend")) not in local_backends:
            continue
        if str(r.get("status")) in ("running", "done", "error", "running-error"):
            end_mark = parse_ts(str(r.get("last_update", ""))) or parse_ts(str(r.get("run_started", "")))
            if end_mark is not None and end_mark > local_cursor:
                local_cursor = end_mark
    for r in model_live_rows:
        if str(r.get("status")) != "scheduled" or str(r.get("planned_start", "")):
            continue
        backend_name = str(r.get("backend", ""))
        if backend_name in local_backends:
            planned_dt = local_cursor
            r["planned_start"] = planned_dt.strftime("%Y-%m-%d %H:%M:%S")
            local_cursor = planned_dt + timedelta(seconds=float(duration_by_backend.get(backend_name, global_duration_fallback)))
        elif backend_name == "siemens":
            r["planned_start"] = (parse_ts(overall_started_at) or now_dt).strftime("%Y-%m-%d %H:%M:%S")

    # Recompute global ETA using model slot projections (done/running/scheduled).
    projected_finish_times: List[datetime] = []
    for r in model_live_rows:
        status = str(r.get("status", ""))
        backend_name = str(r.get("backend", ""))
        run_started_ts = parse_ts(str(r.get("run_started", "")))
        planned_start_ts = parse_ts(str(r.get("planned_start", "")))
        last_update_ts = parse_ts(str(r.get("last_update", "")))
        done_n = int(r.get("done_count") or 0)
        total_n = int(r.get("total_count") or 0)
        elapsed_s = r.get("elapsed_seconds")
        wall_s = r.get("wall_s")
        duration_est = float(duration_by_backend.get(backend_name, global_duration_fallback))
        if total_n > 0:
            if isinstance(elapsed_s, (int, float)) and done_n > 0:
                duration_est = float(elapsed_s) * float(total_n) / float(done_n)
            elif isinstance(wall_s, (int, float)) and float(wall_s) > 0:
                duration_est = float(wall_s) * float(total_n)
        if status in ("done", "error", "running-error"):
            if last_update_ts is not None:
                projected_finish_times.append(last_update_ts)
            elif run_started_ts is not None:
                projected_finish_times.append(run_started_ts + timedelta(seconds=duration_est))
            continue
        if status == "running":
            start_ts = run_started_ts or planned_start_ts or now_dt
            projected_finish_times.append(start_ts + timedelta(seconds=duration_est))
            continue
        # scheduled
        start_ts = planned_start_ts or now_dt
        projected_finish_times.append(start_ts + timedelta(seconds=duration_est))

    if projected_finish_times:
        projected_finish = max(projected_finish_times)
        overall_eta_seconds = max((projected_finish - now_dt).total_seconds(), 0.0)
        overall_eta_end = projected_finish.strftime("%Y-%m-%d %H:%M:%S")
        if overall_elapsed_seconds is not None:
            overall_total_est_seconds = overall_elapsed_seconds + overall_eta_seconds

    def live_row_sort_key(row: Dict[str, object]) -> Tuple[int, float, float, str, str]:
        status = str(row.get("status", ""))
        last_update_ts = parse_ts(str(row.get("last_update", "")))
        planned_start_ts = parse_ts(str(row.get("planned_start", "")))
        run_started_ts = parse_ts(str(row.get("run_started", "")))
        if status in ("done", "error", "running-error"):
            end_epoch = last_update_ts.timestamp() if last_update_ts is not None else 0.0
            # finished rows first, newest end time first
            return (0, -end_epoch, 0.0, str(row.get("backend", "")), str(row.get("model", "")))
        if status == "running":
            start_epoch = run_started_ts.timestamp() if run_started_ts is not None else (
                planned_start_ts.timestamp() if planned_start_ts is not None else float("inf")
            )
            return (1, start_epoch, 0.0, str(row.get("backend", "")), str(row.get("model", "")))
        plan_epoch = planned_start_ts.timestamp() if planned_start_ts is not None else float("inf")
        return (2, plan_epoch, 0.0, str(row.get("backend", "")), str(row.get("model", "")))

    model_live_rows.sort(key=live_row_sort_key)

    chart_name = "benchmark_models_overview.svg"
    chart_path = os.path.join(report_dir, chart_name) if report_dir else chart_name
    write_model_chart_svg(chart_path, summary_rows, generated, ", ".join(benchmark_names) if benchmark_names else "n/a")
    lines.append("## Model runtime and heuristik-score chart")
    lines.append("")
    lines.append(f'<img src="{html.escape(chart_name)}" alt="Model runtime and heuristik-score chart" />')
    lines.append("")

    lines.append("## Live benchmark status")
    lines.append("")
    lines.append(f"- Bulk/campaign started: `{fmt_clock_or_date(overall_started_at, report_day) if overall_started_at else 'n/a'}`")
    lines.append(f"- Elapsed total: `{fmt_eta(overall_elapsed_seconds) if overall_elapsed_seconds is not None else 'n/a'}`")
    lines.append(f"- Elapsed left (estimated): `{fmt_eta(overall_eta_seconds) if overall_eta_seconds is not None else 'n/a'}`")
    lines.append(f"- Estimated total duration: `{fmt_eta(overall_total_est_seconds) if overall_total_est_seconds is not None else 'n/a'}`")
    lines.append(f"- Estimated finish: `{fmt_clock_or_date(overall_eta_end, report_day) if overall_eta_end else 'n/a'}`")
    lines.append("")
    lines.append("<table>")
    lines.append("<thead><tr><th>Benchmark</th><th>Backend</th><th>Model</th><th>Status</th><th>Planned start</th><th>Run started</th><th>Last update</th><th>Progress</th><th>Elapsed time</th><th>Elapsed left (est.)</th><th>ETA end</th><th>LLM start<br>(s)</th><th>LLM stop<br>(s)</th><th>Heuristik-Score</th><th>Gesamtbewertung</th><th>Tok/s</th><th>CPU%(avg)</th><th>GPU%(avg)</th><th>RAM GB(proc avg)</th><th>VRAM GB(avg)</th><th>Wall-s(avg)</th></tr></thead>")
    lines.append("<tbody>")
    for row in model_live_rows:
        lines.append(
            "<tr>"
            f"<td>{html.escape(str(row['benchmark']))}</td>"
            f"<td>{html.escape(str(row['backend']))}</td>"
            f"<td>{html.escape(str(row['model']))}</td>"
            f"<td>{html.escape(str(row['status']))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['planned_start']), report_day))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['run_started']), report_day))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['last_update']), report_day))}</td>"
            f"<td>{html.escape(str(row['progress']))}</td>"
            f"<td>{html.escape(str(row['elapsed']))}</td>"
            f"<td>{html.escape(str(row['elapsed_left_est']))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['eta_end']), report_day))}</td>"
            f"<td>{fmt(row['llm_startuptime'], 3)}</td>"
            f"<td>{fmt(row['llm_shutdowntime'], 3)}</td>"
            f"<td>{fmt(row['score'])}</td>"
            f"<td>{html.escape(str(row['rating']))}</td>"
            f"<td>{fmt(row['tps'])}</td>"
            f"<td>{fmt(row['avg_cpu'])}</td>"
            f"<td>{fmt(row['avg_gpu'])}</td>"
            f"<td>{fmt(row['used_ram_gb'])}</td>"
            f"<td>{fmt(row['used_vram_gb'])}</td>"
            f"<td>{fmt(row['wall_s'], 2)}</td>"
            "</tr>"
        )
    lines.append("</tbody></table>")
    lines.append("")
    details_report_path = report_path.replace(".md", "_details.md")
    details_report_name = os.path.basename(details_report_path)
    lines.append(f"- Detailed run-plan report: `{details_report_name}`")
    lines.append("")
    lines.append("## Raw metrics per test")
    lines.append("")
    lines.append("<details>")
    lines.append("<summary>Show full per-test table (all metrics)</summary>")
    lines.append("")
    lines.append("<table>")
    lines.append("<thead><tr>" + "".join(f"<th>{html.escape(col)}</th>" for col in (["_file"] + header)) + "</tr></thead>")
    lines.append("<tbody>")
    for row in all_rows:
        lines.append(
            "<tr>"
            + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in (["_file"] + header))
            + "</tr>"
        )
    lines.append("</tbody></table>")
    lines.append("</details>")
    lines.append("")
    lines.append("## TODO (auf Anforderung)")
    lines.append("")
    lines.append("- Sandbox-Eval-Harness mit PostgreSQL + Python-Tests einführen, damit ein separater Pass/Fail-Score als internet-naher Benchmark-Score ausgewiesen werden kann.")
    lines.append("")
    if history_section_lines:
        lines.extend(history_section_lines)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    report_html_path = report_path[:-3] + ".html" if report_path.lower().endswith(".md") else (report_path + ".html")
    html_lines: List[str] = []
    html_lines.append("<!doctype html>")
    html_lines.append("<html><head><meta charset=\"utf-8\"><title>Benchmark Report</title></head><body>")
    html_lines.append("<h1>Benchmark Report</h1>")
    html_lines.append(f"<p><b>Generated:</b> {html.escape(generated)}</p>")
    html_lines.append(f"<p><b>Overall progress:</b> {html.escape(progress_display)}</p>")
    html_lines.append(f"<p><b>Overall ETA left:</b> {html.escape(fmt_eta(overall_eta_seconds) if overall_eta_seconds is not None else 'n/a')}</p>")
    html_lines.append(f"<p><b>Overall ETA end:</b> {html.escape(overall_eta_end if overall_eta_end else 'n/a')}</p>")
    html_lines.append("<h2>Live benchmark status</h2>")
    html_lines.append("<table class=\"sortable\" id=\"live-status-table\">")
    html_lines.append("<thead><tr><th>Benchmark</th><th>Backend</th><th>Model</th><th>Status</th><th>Planned start</th><th>Run started</th><th>Last update</th><th>Progress</th><th>Elapsed time</th><th>Elapsed left (est.)</th><th>ETA end</th><th>LLM start<br>(s)</th><th>LLM stop<br>(s)</th><th>Heuristik-Score</th><th>Gesamtbewertung</th><th>Tok/s</th><th>CPU%(avg)</th><th>GPU%(avg)</th><th>RAM GB(proc avg)</th><th>VRAM GB(avg)</th><th>Wall-s(avg)</th></tr></thead>")
    html_lines.append("<tbody>")
    for row in model_live_rows:
        html_lines.append(
            "<tr>"
            f"<td>{html.escape(str(row['benchmark']))}</td>"
            f"<td>{html.escape(str(row['backend']))}</td>"
            f"<td>{html.escape(str(row['model']))}</td>"
            f"<td>{html.escape(str(row['status']))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['planned_start']), report_day))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['run_started']), report_day))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['last_update']), report_day))}</td>"
            f"<td>{html.escape(str(row['progress']))}</td>"
            f"<td>{html.escape(str(row['elapsed']))}</td>"
            f"<td>{html.escape(str(row['elapsed_left_est']))}</td>"
            f"<td>{html.escape(fmt_clock_or_date(str(row['eta_end']), report_day))}</td>"
            f"<td>{fmt(row['llm_startuptime'], 3)}</td>"
            f"<td>{fmt(row['llm_shutdowntime'], 3)}</td>"
            f"<td>{fmt(row['score'])}</td>"
            f"<td>{html.escape(str(row['rating']))}</td>"
            f"<td>{fmt(row['tps'])}</td>"
            f"<td>{fmt(row['avg_cpu'])}</td>"
            f"<td>{fmt(row['avg_gpu'])}</td>"
            f"<td>{fmt(row['used_ram_gb'])}</td>"
            f"<td>{fmt(row['used_vram_gb'])}</td>"
            f"<td>{fmt(row['wall_s'], 2)}</td>"
            "</tr>"
        )
    html_lines.append("</tbody></table>")
    html_lines.append("<h2>Raw metrics per test</h2>")
    html_lines.append("<table class=\"sortable\">")
    html_lines.append("<thead><tr>" + "".join(f"<th>{html.escape(col)}</th>" for col in (["_file"] + header)) + "</tr></thead>")
    html_lines.append("<tbody>")
    for row in all_rows:
        html_lines.append(
            "<tr>" + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in (["_file"] + header)) + "</tr>"
        )
    html_lines.append("</tbody></table>")
    if history_section_lines:
        html_lines.extend(history_section_lines)
    html_lines.extend(sortable_assets)
    html_lines.append("</body></html>")
    with open(report_html_path, "w", encoding="utf-8") as fhtml:
        fhtml.write("\n".join(html_lines))

    detail_lines: List[str] = []
    detail_lines.append("# Benchmark Detailed Live Status")
    detail_lines.append("")
    detail_lines.append(f"- Generated: `{generated}`")
    detail_lines.append(f"- Source report: `{os.path.basename(report_path)}`")
    detail_lines.append("")
    detail_lines.append("## Planned runs (case + run granularity)")
    detail_lines.append("")
    detail_lines.append("<table>")
    detail_lines.append("<thead><tr><th>Benchmark</th><th>Backend</th><th>Model</th><th>Case</th><th>Run</th><th>Status</th><th>Planned start</th><th>Run started</th><th>Last update</th><th>Model progress</th><th>Model ETA left</th><th>Model heuristik-score</th><th>Model wall-s(avg)</th></tr></thead>")
    detail_lines.append("<tbody>")
    for row in planned_rows:
        detail_lines.append(
            "<tr>"
            f"<td>{html.escape(str(row['benchmark']))}</td>"
            f"<td>{html.escape(str(row['backend']))}</td>"
            f"<td>{html.escape(str(row['model']))}</td>"
            f"<td>{html.escape(str(row['case_id']))}</td>"
            f"<td>{html.escape(str(row['run']))}</td>"
            f"<td>{html.escape(str(row['status']))}</td>"
            f"<td>{html.escape(str(row['planned_start']))}</td>"
            f"<td>{html.escape(str(row['run_started']))}</td>"
            f"<td>{html.escape(str(row['last_update']))}</td>"
            f"<td>{html.escape(str(row['model_progress']))}</td>"
            f"<td>{html.escape(str(row['model_eta_left']))}</td>"
            f"<td>{fmt(row['model_overall'])}</td>"
            f"<td>{fmt(row['model_wall_s'], 2)}</td>"
            "</tr>"
        )
    detail_lines.append("</tbody></table>")
    detail_lines.append("")

    with open(details_report_path, "w", encoding="utf-8") as fdetail:
        fdetail.write("\n".join(detail_lines))

    details_html_path = details_report_path[:-3] + ".html" if details_report_path.lower().endswith(".md") else (details_report_path + ".html")
    detail_html_lines: List[str] = []
    detail_html_lines.append("<!doctype html>")
    detail_html_lines.append("<html><head><meta charset=\"utf-8\"><title>Benchmark Detailed Live Status</title></head><body>")
    detail_html_lines.append("<h1>Benchmark Detailed Live Status</h1>")
    detail_html_lines.append(f"<p><b>Generated:</b> {html.escape(generated)}</p>")
    detail_html_lines.append("<table class=\"sortable\" id=\"details-planned-table\">")
    detail_html_lines.append("<thead><tr><th>Benchmark</th><th>Backend</th><th>Model</th><th>Case</th><th>Run</th><th>Status</th><th>Planned start</th><th>Run started</th><th>Last update</th><th>Model progress</th><th>Model ETA left</th><th>Model heuristik-score</th><th>Model wall-s(avg)</th></tr></thead>")
    detail_html_lines.append("<tbody>")
    for row in planned_rows:
        detail_html_lines.append(
            "<tr>"
            f"<td>{html.escape(str(row['benchmark']))}</td>"
            f"<td>{html.escape(str(row['backend']))}</td>"
            f"<td>{html.escape(str(row['model']))}</td>"
            f"<td>{html.escape(str(row['case_id']))}</td>"
            f"<td>{html.escape(str(row['run']))}</td>"
            f"<td>{html.escape(str(row['status']))}</td>"
            f"<td>{html.escape(str(row['planned_start']))}</td>"
            f"<td>{html.escape(str(row['run_started']))}</td>"
            f"<td>{html.escape(str(row['last_update']))}</td>"
            f"<td>{html.escape(str(row['model_progress']))}</td>"
            f"<td>{html.escape(str(row['model_eta_left']))}</td>"
            f"<td>{fmt(row['model_overall'])}</td>"
            f"<td>{fmt(row['model_wall_s'], 2)}</td>"
            "</tr>"
        )
    detail_html_lines.append("</tbody></table>")
    detail_html_lines.extend(sortable_assets)
    detail_html_lines.append("</body></html>")
    with open(details_html_path, "w", encoding="utf-8") as fdetail_html:
        fdetail_html.write("\n".join(detail_html_lines))


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
    print(f"{'Backend':<10} {'Model':<35} {'Overall':>8} {'Qual':>8} {'Tok/s':>8} {'Wall(ms)':>10} {'CPU%':>8} {'GPU%':>8} {'SysErr':>7}")
    for r in rows:
        t_str = f"{r['tps']:.2f}" if r["tps"] is not None else "n/a"
        cpu_str = f"{r['cpu']:.1f}" if r["cpu"] is not None else "n/a"
        gpu_str = f"{r['gpu']:.1f}" if r["gpu"] is not None else "n/a"
        print(
            f"{r['backend']:<10} {r['model']:<35} {r['overall']:>8.2f} {r['quality']:>8.2f} {t_str:>8} "
            f"{r['wall']:>10.1f} {cpu_str:>8} {gpu_str:>8} {r['errors']:>7}"
        )

    print("\n=== Errors (if any) ===")
    for r in results:
        if r.error:
            print(f"[{r.backend}/{r.model}/{r.case_id}/run{r.run}] {r.error}")


def save_progress_snapshot(
    results: List[BenchResult],
    args: argparse.Namespace,
    run_tag: str,
) -> None:
    if not results:
        return
    save_results(results, args.output_dir, run_tag=run_tag, inprogress=True)
    update_markdown_report(args.output_dir, args.report_file, args)


def main() -> int:
    args = parse_args()
    if args.list_benchmarks:
        ensure_default_benchmark_suite_file()
        files = list_benchmark_suite_files()
        print(f"Available benchmark suites in {BENCHMARK_LIBRARY_DIR}:")
        for p in files:
            print(f" - {os.path.basename(p)}")
        return 0

    try:
        suite, benchmark_source = load_benchmark_suite(args)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"ERROR: failed to load benchmark suite: {exc}")
        return 2

    global BENCHMARK_NAME, BENCHMARK_SPEC_VERSION, BENCH_TASKS, BENCHMARK_SCORING_MODE, BENCHMARK_SWE_PASS_THRESHOLD
    BENCHMARK_NAME = str(suite["name"])
    BENCHMARK_SPEC_VERSION = str(suite["spec_version"])
    BENCH_TASKS = list(suite["tasks"])
    scoring_profile = suite.get("scoring_profile", {}) if isinstance(suite.get("scoring_profile"), dict) else {}
    BENCHMARK_SCORING_MODE = str(scoring_profile.get("mode", "classic")).strip().lower() or "classic"
    BENCHMARK_SWE_PASS_THRESHOLD = float(scoring_profile.get("pass_threshold", 85.0))
    if not args.benchmark_file:
        args.benchmark_file = benchmark_source
    print(f"Benchmark suite: {BENCHMARK_NAME} ({BENCHMARK_SPEC_VERSION})")
    print(f"Benchmark source: {benchmark_source}")
    print(f"Benchmark tasks: {len(BENCH_TASKS)}")
    print(f"Scoring mode: {BENCHMARK_SCORING_MODE} (pass threshold {BENCHMARK_SWE_PASS_THRESHOLD:.1f})")

    target_backends = target_backends_for_run(args.backend)
    results: List[BenchResult] = []
    resumed_from = ""
    resumed_tag, resumed_results, resumed_from = (None, [], "")
    if args.resume == "auto":
        resumed_tag, resumed_results, resumed_from = try_resume_results(args, benchmark_source, target_backends)
        if resumed_tag and resumed_results:
            results.extend(resumed_results)
            print(f"Resuming from: {resumed_from}")
            print(f"Resume run id: {resumed_tag} | restored rows: {len(resumed_results)}")
    else:
        # Fresh start mode: remove stale in-progress snapshots.
        for stale in glob.glob(os.path.join(args.output_dir, "migration_llm_bench_*_inprogress.csv")):
            try:
                os.remove(stale)
            except OSError:
                pass
        for stale in glob.glob(os.path.join(args.output_dir, "migration_llm_bench_*_inprogress.json")):
            try:
                os.remove(stale)
            except OSError:
                pass

    run_tag = resumed_tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    benchmark_meta = build_benchmark_metadata(run_tag, benchmark_source)
    benchmark_meta["benchmark_runs"] = args.runs
    completed_keys: Set[Tuple[str, str, str, int]] = {
        (r.backend, r.model, r.case_id, r.run) for r in results if not (r.error or "").strip()
    }

    if args.backend in ("ollama", "both", "all"):
        ollama_models = args.ollama_models or default_ollama_models()
        if not ollama_models:
            print("No matching Ollama models found for qwen3.6 27/35 q4 and gpt-oss.")
        else:
            print(f"Ollama models: {', '.join(ollama_models)}")
            for model in ollama_models:
                try:
                    with local_lock.local_model_slot(
                        None,
                        "ollama",
                        model,
                        wait_seconds=args.local_lease_wait_seconds,
                    ):
                        startup_sec: Optional[float] = None
                        shutdown_sec: Optional[float] = None
                        model_result_indexes: List[int] = []
                        try:
                            startup_sec = ollama_warmup_model(args.ollama_url, model, args.timeout_sec)
                            print(f"[ollama] {model} warmup: {startup_sec:.3f}s")
                        except Exception as exc:
                            print(f"[ollama] {model} warmup failed: {exc}")
                        for case in BENCH_TASKS:
                            for run_id in range(1, args.runs + 1):
                                if ("ollama", model, str(case["id"]), run_id) in completed_keys:
                                    continue
                                print(f"[ollama] {model} | {case['id']} | run {run_id}")
                                result = run_ollama_case(model, case, run_id, args)
                                result.local_model_startup_sec = startup_sec
                                results.append(apply_benchmark_metadata(result, benchmark_meta))
                                model_result_indexes.append(len(results) - 1)
                                if not (result.error or "").strip():
                                    completed_keys.add(("ollama", model, str(case["id"]), run_id))
                                save_progress_snapshot(results, args, run_tag)
                        try:
                            shutdown_sec = ollama_unload_model(args.ollama_url, model, args.timeout_sec)
                            print(f"[ollama] {model} unload: {shutdown_sec:.3f}s")
                        except Exception as exc:
                            print(f"[ollama] {model} unload failed: {exc}")
                        for idx in model_result_indexes:
                            results[idx].local_model_shutdown_sec = shutdown_sec
                        save_progress_snapshot(results, args, run_tag)
                except local_lock.LocalModelLockError as exc:
                    print(f"ERROR: local-model lease unavailable for ollama/{model}: {exc}")
                    return 3

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
                    try:
                        with local_lock.local_model_slot(
                            None,
                            "llama_cpp",
                            model_name,
                            wait_seconds=args.local_lease_wait_seconds,
                        ):
                            print(f"[llama_cpp/server] {model_name} | loading model")
                            model_results = [
                                apply_benchmark_metadata(r, benchmark_meta)
                                for r in run_llama_server_model(args.llama_server, model_name, model_path, args, completed_keys=completed_keys)
                            ]
                            for row in model_results:
                                print(f"[llama_cpp/server] {model_name} | {row.case_id} | run {row.run}")
                                if not (row.error or "").strip():
                                    completed_keys.add(("llama_cpp", model_name, row.case_id, row.run))
                            results.extend(model_results)
                            save_progress_snapshot(results, args, run_tag)
                    except local_lock.LocalModelLockError as exc:
                        print(f"ERROR: local-model lease unavailable for llama_cpp/{model_name}: {exc}")
                        return 3
            else:
                for model_name, model_path in llama_models:
                    try:
                        with local_lock.local_model_slot(
                            None,
                            "llama_cpp",
                            model_name,
                            wait_seconds=args.local_lease_wait_seconds,
                        ):
                            for case in BENCH_TASKS:
                                for run_id in range(1, args.runs + 1):
                                    if ("llama_cpp", model_name, str(case["id"]), run_id) in completed_keys:
                                        continue
                                    print(f"[llama_cpp/cli] {model_name} | {case['id']} | run {run_id}")
                                    result = run_llama_cpp_case(model_name, model_path, case, run_id, args)
                                    results.append(apply_benchmark_metadata(result, benchmark_meta))
                                    if not (result.error or "").strip():
                                        completed_keys.add(("llama_cpp", model_name, str(case["id"]), run_id))
                                    save_progress_snapshot(results, args, run_tag)
                    except local_lock.LocalModelLockError as exc:
                        print(f"ERROR: local-model lease unavailable for llama_cpp/{model_name}: {exc}")
                        return 3

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
                futures = {}
                for m in siemens_models:
                    for case in BENCH_TASKS:
                        for run_id in range(1, args.runs + 1):
                            if ("siemens", m, str(case["id"]), run_id) in completed_keys:
                                continue
                            print(f'[siemens] {m} | {case["id"]} | run {run_id}', flush=True)
                            fut = pool.submit(run_siemens_case, args.siemens_url, token, m, case, run_id, args)
                            futures[fut] = (m, case["id"], run_id)
                for future in as_completed(futures):
                    mdl, case_id, run_id = futures[future]
                    try:
                        done_row = apply_benchmark_metadata(future.result(), benchmark_meta)
                        siemens_buf.append(done_row)
                        results.append(done_row)
                        if not (done_row.error or "").strip():
                            completed_keys.add(("siemens", mdl, case_id, run_id))
                        save_progress_snapshot(results, args, run_tag)
                    except Exception as exc:
                        print(f'[siemens] {mdl} FAILED {case_id} run {run_id}: {exc}')

    if not results:
        print("No benchmarks executed.")
        return 1

    print_summary(results)
    csv_path, json_path = save_results(results, args.output_dir, run_tag=run_tag, inprogress=False)
    progress_csv = os.path.join(args.output_dir, f"migration_llm_bench_{run_tag}_inprogress.csv")
    progress_json = os.path.join(args.output_dir, f"migration_llm_bench_{run_tag}_inprogress.json")
    for p in (progress_csv, progress_json):
        if os.path.exists(p):
            os.remove(p)
    update_markdown_report(args.output_dir, args.report_file, args)
    print(f"\nSaved CSV:  {csv_path}")
    print(f"Saved JSON: {json_path}")
    print(f"Updated report: {args.report_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
