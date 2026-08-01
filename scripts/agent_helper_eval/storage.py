"""SQLite single source of truth (SSOT) plus deterministic CSV export/import.

Design:

- One SQLite database per campaign directory (``agent_helper.sqlite3``) is the
  canonical store. CSV files are deterministic, ordered exports derived from it
  -- never hand-edited, never a second source of truth.
- Column order in CSV exports always matches
  :data:`agent_helper_eval.schema.SAMPLE_CSV_COLUMNS` /
  ``AGGREGATE_CSV_COLUMNS`` exactly, so downstream tooling can rely on stable
  headers across campaigns.
- ``None`` is stored as SQL ``NULL`` (so aggregation math over the database
  stays correct) and only rendered as the literal ``"N/A"`` at CSV/report
  export time.
"""

from __future__ import annotations

import csv
import dataclasses
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from . import schema
from .schema import (
    AGGREGATE_CSV_COLUMNS,
    CAPACITY_PROFILE_CSV_COLUMNS,
    SAMPLE_CSV_COLUMNS,
    SCHEMA_VERSION,
    AggregateRecord,
    CapacityProfileRecord,
    SampleRecord,
    SchemaValidationError,
    validate_aggregate,
    validate_capacity_profile,
    validate_sample,
)

DB_FILENAME = "agent_helper.sqlite3"
SAMPLES_CSV_FILENAME = "agent_helper_samples.csv"
AGGREGATES_CSV_FILENAME = "agent_helper_aggregates.csv"
CAPACITY_PROFILE_CSV_FILENAME = "agent_helper_capacity_profile.csv"

_BOOL_COLUMNS = {
    "system_error_flag",
    "unsafe_behavior_flag",
    "output_placeholder_or_incomplete",
    "hard_gate_failed",
    "reviewer_evidence_is_heuristic_only",
    "preflight_quality_gate_passed",
    "preflight_memory_safety_checked",
}
_INT_COLUMNS = {
    "prompt_tokens",
    "output_tokens",
    "total_tokens",
    "retry_count",
    "iteration_index",
    "sample_seq",
    "task_count",
    "success_count",
    "error_count",
    "skipped_count",
    "retries_total",
    "accepted_sample_count",
    "not_usable_sample_count",
    "not_evaluated_sample_count",
    "unsafe_sample_count",
    "unresolved_task_count",
    "phase_timing_sample_count",
    "concurrency_level",
    "requests_issued",
    "error_count",
    "timeout_count",
}
_FLOAT_COLUMNS = {
    "elapsed_seconds",
    "tokens_per_second",
    "ttft_seconds",
    "cpu_time_seconds",
    "cpu_avg_percent",
    "cpu_max_percent",
    "ram_avg_mb",
    "ram_max_mb",
    "gpu_avg_percent",
    "gpu_max_percent",
    "vram_avg_mb",
    "vram_max_mb",
    "deterministic_score",
    "reviewer_score",
    "collaboration_score",
    "composite_score",
    "hardware_vram_total_mb",
    "success_rate_percent",
    "error_rate_percent",
    "elapsed_seconds_total",
    "elapsed_seconds_mean",
    "elapsed_seconds_p50",
    "elapsed_seconds_p95",
    "tokens_per_second_mean",
    "ttft_seconds_mean",
    "cpu_avg_percent_mean",
    "cpu_max_percent_max",
    "ram_avg_mb_mean",
    "ram_max_mb_max",
    "gpu_avg_percent_mean",
    "gpu_max_percent_max",
    "vram_avg_mb_mean",
    "vram_max_mb_max",
    "deterministic_score_mean",
    "reviewer_score_mean",
    "collaboration_score_mean",
    "overall_score",
    "iterations_mean",
    "campaign_elapsed_seconds_total",
    "acceptance_rate_percent",
    "time_to_accepted_result_seconds_mean",
    "rework_tokens_to_accept_mean",
    "total_wall_seconds",
    "llm_queue_seconds",
    "llm_request_seconds",
    "prompt_eval_seconds",
    "generation_seconds",
    "local_tool_exec_seconds",
    "test_exec_seconds",
    "orchestrator_review_seconds",
    "idle_wait_seconds",
    "overlap_seconds",
    "model_load_seconds",
    "orchestrator_cpu_time_seconds",
    "model_cpu_time_seconds",
    "model_load_seconds_mean",
    "orchestrator_cpu_time_seconds_mean",
    "model_cpu_time_seconds_mean",
    "llm_critical_path_percent",
    "tool_critical_path_percent",
    "queue_idle_critical_path_percent",
    "orchestration_critical_path_percent",
    "phase_timing_overlap_ratio_percent",
    "phase_timing_unaccounted_ratio_percent",
    "model_busy_percent",
    "gpu_active_percent",
    "tool_runner_busy_percent",
    "aggregate_tokens_per_second",
    "per_request_tokens_per_second_mean",
    "per_request_tokens_per_second_p50",
    "per_request_tokens_per_second_p95",
    "queue_seconds_mean",
    "service_seconds_mean",
    "latency_seconds_p95",
    "time_to_accepted_result_seconds",
    "single_request_baseline_tokens_per_second",
    "throughput_efficiency_percent",
    "vram_budget_mb",
    "vram_headroom_mb",
}



class SchemaVersionMismatchError(RuntimeError):
    """Raised when an existing database was created with another schema version."""


def _quoted_columns(columns: Iterable[str]) -> str:
    return ", ".join(f'"{c}"' for c in columns)


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (creating if needed) the campaign SQLite database.

    Ensures the ``schema_meta``, ``samples``, and ``aggregates`` tables exist
    and that any pre-existing database matches :data:`SCHEMA_VERSION`.
    """

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _ensure_schema(conn)
    except Exception:
        # Never leak an open sqlite3.Connection/file handle when schema
        # setup fails (matters most on Windows, where a lingering handle
        # can block the caller from even deleting/replacing the file).
        conn.close()
        raise
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta ("
        "  key TEXT PRIMARY KEY,"
        "  value TEXT NOT NULL"
        ")"
    )
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
    elif row["value"] != SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            f"database schema_version {row['value']!r} does not match "
            f"expected {SCHEMA_VERSION!r}; migrate explicitly instead of "
            "silently overwriting"
        )

    sample_columns_sql = ",\n".join(
        f'  "{name}" {_sql_type(name)}' for name in SAMPLE_CSV_COLUMNS
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS samples (\n"
        f"{sample_columns_sql},\n"
        f'  PRIMARY KEY ("sample_id")\n'
        f")"
    )
    _add_missing_columns(conn, "samples", SAMPLE_CSV_COLUMNS)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_samples_group ON samples "
        '("campaign_id", "benchmark_set", "track", "backend", "model")'
    )

    aggregate_columns_sql = ",\n".join(
        f'  "{name}" {_sql_type(name)}' for name in AGGREGATE_CSV_COLUMNS
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS aggregates (\n"
        f"{aggregate_columns_sql},\n"
        f'  PRIMARY KEY ("campaign_id", "run_id", "benchmark_set", "track", '
        f'"backend", "model")\n'
        f")"
    )
    _add_missing_columns(conn, "aggregates", AGGREGATE_CSV_COLUMNS)

    capacity_profile_columns_sql = ",\n".join(
        f'  "{name}" {_sql_type(name)}' for name in CAPACITY_PROFILE_CSV_COLUMNS
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS capacity_profile (\n"
        f"{capacity_profile_columns_sql},\n"
        f'  PRIMARY KEY ("campaign_id", "run_id", "benchmark_set", "backend", '
        f'"model", "concurrency_level")\n'
        f")"
    )
    _add_missing_columns(conn, "capacity_profile", CAPACITY_PROFILE_CSV_COLUMNS)
    conn.commit()


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: Iterable[str]) -> None:
    """Additively migrate an already-existing table to include every column
    in ``columns`` that it is currently missing.

    ``CREATE TABLE IF NOT EXISTS`` above is a no-op against a table that
    already exists (for example a campaign SQLite database written before a
    later, backward-compatible schema addition such as ``model_load_seconds``
    or ``evidence_stage``) -- without this, inserting/selecting the new
    dataclass fields against that older physical table would fail with
    "no such column". ``ALTER TABLE ... ADD COLUMN`` is the standard,
    genuinely additive SQLite migration for this: existing rows simply get
    ``NULL`` (rendered as "N/A") for the newly added column, no data is
    touched or lost. Safe to call every time a database is opened (already-
    present columns are skipped).
    """

    existing = {
        row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    for name in columns:
        if name in existing:
            continue
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {_sql_type(name)}')


def _sql_type(column: str) -> str:
    if column in _BOOL_COLUMNS:
        return "INTEGER"
    if column in _INT_COLUMNS:
        return "INTEGER"
    if column in _FLOAT_COLUMNS:
        return "REAL"
    return "TEXT"


def insert_sample(conn: sqlite3.Connection, record: SampleRecord) -> None:
    """Validate and insert (or replace) one sample row."""

    errors = validate_sample(record)
    if errors:
        raise SchemaValidationError("; ".join(errors))
    data = dataclasses.asdict(record)
    values = [_to_sql_value(name, data[name]) for name in SAMPLE_CSV_COLUMNS]
    placeholders = ", ".join("?" for _ in SAMPLE_CSV_COLUMNS)
    conn.execute(
        f'INSERT OR REPLACE INTO samples ({_quoted_columns(SAMPLE_CSV_COLUMNS)}) '
        f"VALUES ({placeholders})",
        values,
    )
    conn.commit()


def insert_samples(conn: sqlite3.Connection, records: Iterable[SampleRecord]) -> int:
    """Insert multiple sample rows in one transaction. Returns the count inserted."""

    count = 0
    for record in records:
        errors = validate_sample(record)
        if errors:
            raise SchemaValidationError(
                f"sample_id={record.sample_id!r}: " + "; ".join(errors)
            )
        data = dataclasses.asdict(record)
        values = [_to_sql_value(name, data[name]) for name in SAMPLE_CSV_COLUMNS]
        placeholders = ", ".join("?" for _ in SAMPLE_CSV_COLUMNS)
        conn.execute(
            f'INSERT OR REPLACE INTO samples ({_quoted_columns(SAMPLE_CSV_COLUMNS)}) '
            f"VALUES ({placeholders})",
            values,
        )
        count += 1
    conn.commit()
    return count


def insert_aggregate(conn: sqlite3.Connection, record: AggregateRecord) -> None:
    """Validate and insert (or replace) one aggregate row."""

    errors = validate_aggregate(record)
    if errors:
        raise SchemaValidationError("; ".join(errors))
    data = dataclasses.asdict(record)
    values = [_to_sql_value(name, data[name]) for name in AGGREGATE_CSV_COLUMNS]
    placeholders = ", ".join("?" for _ in AGGREGATE_CSV_COLUMNS)
    conn.execute(
        f'INSERT OR REPLACE INTO aggregates ({_quoted_columns(AGGREGATE_CSV_COLUMNS)}) '
        f"VALUES ({placeholders})",
        values,
    )
    conn.commit()


def insert_aggregates(
    conn: sqlite3.Connection, records: Iterable[AggregateRecord]
) -> int:
    """Insert multiple aggregate rows in one transaction. Returns the count."""

    count = 0
    for record in records:
        insert_aggregate(conn, record)
        count += 1
    return count


def insert_capacity_profile(conn: sqlite3.Connection, record: CapacityProfileRecord) -> None:
    """Validate and insert (or replace) one capacity-profile row."""

    errors = validate_capacity_profile(record)
    if errors:
        raise SchemaValidationError("; ".join(errors))
    data = dataclasses.asdict(record)
    values = [_to_sql_value(name, data[name]) for name in CAPACITY_PROFILE_CSV_COLUMNS]
    placeholders = ", ".join("?" for _ in CAPACITY_PROFILE_CSV_COLUMNS)
    conn.execute(
        f'INSERT OR REPLACE INTO capacity_profile '
        f'({_quoted_columns(CAPACITY_PROFILE_CSV_COLUMNS)}) '
        f"VALUES ({placeholders})",
        values,
    )
    conn.commit()


def insert_capacity_profiles(
    conn: sqlite3.Connection, records: Iterable[CapacityProfileRecord]
) -> int:
    """Insert multiple capacity-profile rows in one transaction. Returns the count."""

    count = 0
    for record in records:
        insert_capacity_profile(conn, record)
        count += 1
    return count


def _to_sql_value(column: str, value: object) -> object:
    if value is None:
        return None
    if column in _BOOL_COLUMNS:
        return 1 if value else 0
    return value


def fetch_samples(
    conn: sqlite3.Connection, campaign_id: Optional[str] = None
) -> list[SampleRecord]:
    """Fetch sample rows, optionally filtered to one campaign, as dataclasses."""

    if campaign_id is None:
        cursor = conn.execute(
            f"SELECT {_quoted_columns(SAMPLE_CSV_COLUMNS)} FROM samples "
            'ORDER BY "campaign_id", "sample_seq"'
        )
    else:
        cursor = conn.execute(
            f"SELECT {_quoted_columns(SAMPLE_CSV_COLUMNS)} FROM samples "
            'WHERE "campaign_id" = ? ORDER BY "sample_seq"',
            (campaign_id,),
        )
    return [_row_to_sample(row) for row in cursor.fetchall()]


def fetch_aggregates(
    conn: sqlite3.Connection, campaign_id: Optional[str] = None
) -> list[AggregateRecord]:
    """Fetch aggregate rows, optionally filtered to one campaign."""

    if campaign_id is None:
        cursor = conn.execute(
            f"SELECT {_quoted_columns(AGGREGATE_CSV_COLUMNS)} FROM aggregates "
            'ORDER BY "campaign_id", "overall_score" DESC'
        )
    else:
        cursor = conn.execute(
            f"SELECT {_quoted_columns(AGGREGATE_CSV_COLUMNS)} FROM aggregates "
            'WHERE "campaign_id" = ? ORDER BY "overall_score" DESC',
            (campaign_id,),
        )
    return [_row_to_aggregate(row) for row in cursor.fetchall()]


def fetch_capacity_profiles(
    conn: sqlite3.Connection, campaign_id: Optional[str] = None
) -> list[CapacityProfileRecord]:
    """Fetch capacity-profile rows, optionally filtered to one campaign."""

    if campaign_id is None:
        cursor = conn.execute(
            f"SELECT {_quoted_columns(CAPACITY_PROFILE_CSV_COLUMNS)} FROM capacity_profile "
            'ORDER BY "campaign_id", "backend", "model", "concurrency_level"'
        )
    else:
        cursor = conn.execute(
            f"SELECT {_quoted_columns(CAPACITY_PROFILE_CSV_COLUMNS)} FROM capacity_profile "
            'WHERE "campaign_id" = ? ORDER BY "backend", "model", "concurrency_level"',
            (campaign_id,),
        )
    return [_row_to_capacity_profile(row) for row in cursor.fetchall()]


def _row_to_sample(row: sqlite3.Row) -> SampleRecord:
    data = dict(row)
    data["system_error_flag"] = bool(data["system_error_flag"])
    for bool_col in ("unsafe_behavior_flag", "output_placeholder_or_incomplete"):
        if data[bool_col] is not None:
            data[bool_col] = bool(data[bool_col])
    return SampleRecord(**data)


def _row_to_aggregate(row: sqlite3.Row) -> AggregateRecord:
    data = dict(row)
    data["hard_gate_failed"] = bool(data["hard_gate_failed"])
    data["reviewer_evidence_is_heuristic_only"] = bool(
        data["reviewer_evidence_is_heuristic_only"]
    )
    return AggregateRecord(**data)


def _row_to_capacity_profile(row: sqlite3.Row) -> CapacityProfileRecord:
    data = dict(row)
    data["preflight_quality_gate_passed"] = bool(data["preflight_quality_gate_passed"])
    data["preflight_memory_safety_checked"] = bool(data["preflight_memory_safety_checked"])
    return CapacityProfileRecord(**data)


def export_samples_csv(
    conn: sqlite3.Connection, path: Path, campaign_id: Optional[str] = None
) -> int:
    """Export sample rows to a deterministic, ordered CSV file. Returns row count."""

    records = fetch_samples(conn, campaign_id=campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(SAMPLE_CSV_COLUMNS))
        writer.writeheader()
        for record in records:
            writer.writerow(schema.sample_to_csv_row(record))
    return len(records)


def export_aggregates_csv(
    conn: sqlite3.Connection, path: Path, campaign_id: Optional[str] = None
) -> int:
    """Export aggregate rows to a deterministic, ordered CSV file. Returns row count."""

    records = fetch_aggregates(conn, campaign_id=campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(AGGREGATE_CSV_COLUMNS))
        writer.writeheader()
        for record in records:
            writer.writerow(schema.aggregate_to_csv_row(record))
    return len(records)


def export_capacity_profile_csv(
    conn: sqlite3.Connection, path: Path, campaign_id: Optional[str] = None
) -> int:
    """Export capacity-profile rows to a deterministic, ordered CSV file.

    Returns the row count. Writing an empty file (header row only) is
    expected and correct for any campaign that has not run a post-quality-
    gate concurrency profile yet -- absence of rows is never papered over.
    """

    records = fetch_capacity_profiles(conn, campaign_id=campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CAPACITY_PROFILE_CSV_COLUMNS))
        writer.writeheader()
        for record in records:
            writer.writerow(schema.capacity_profile_to_csv_row(record))
    return len(records)


def read_samples_csv(path: Path) -> list[dict[str, str]]:
    """Read a previously exported samples CSV back into raw string dict rows.

    Used for CSV round-trip tests; values stay as strings (including the
    literal ``"N/A"`` placeholder) exactly as written by :func:`export_samples_csv`.
    """

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is not None and tuple(reader.fieldnames) != SAMPLE_CSV_COLUMNS:
            raise SchemaValidationError(
                "samples CSV header does not match SAMPLE_CSV_COLUMNS: "
                f"{reader.fieldnames!r}"
            )
        return list(reader)


def read_capacity_profile_csv(path: Path) -> list[dict[str, str]]:
    """Read a previously exported capacity-profile CSV back into raw string
    dict rows (mirrors :func:`read_samples_csv` for the third canonical
    table). Used for CSV round-trip tests."""

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is not None and tuple(reader.fieldnames) != CAPACITY_PROFILE_CSV_COLUMNS:
            raise SchemaValidationError(
                "capacity-profile CSV header does not match "
                f"CAPACITY_PROFILE_CSV_COLUMNS: {reader.fieldnames!r}"
            )
        return list(reader)
