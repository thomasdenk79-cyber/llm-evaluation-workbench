"""Adapter: import the legacy ``migration_llm_bench`` history CSV into the
canonical agent-helper sample schema.

This module only *reads* the existing legacy CSV/JSON files produced by
``scripts/llm_migration_benchmark.py``; it never writes to them and never
changes their format. The legacy schema predates several agent-helper
concepts (deterministic vs. reviewer scoring, tool-agent tracks, TTFT,
RAM in MB rather than percent, timezone-aware timestamps, ...). Fields that
cannot be feasibly or honestly derived are imported as ``None`` (rendered as
the literal ``"N/A"`` at CSV/report export time) rather than guessed.

Known, documented limitations of this import path:

- The legacy CSV records RAM as a percentage of total system RAM
  (``avg_mem_pct`` / ``max_mem_pct``), while the canonical schema records RAM
  in MB. Without the legacy run's total system RAM captured alongside it,
  converting percent to MB would silently fabricate precision, so
  ``ram_avg_mb`` / ``ram_max_mb`` are imported as ``None`` (N/A).
- The legacy ``recorded_at`` timestamp has no timezone. This adapter assumes
  it was recorded in the machine's local timezone *at import time* and says
  so explicitly in ``notes``; it is not a measured fact.
- The legacy ``quality_score`` is a keyword/rule-based heuristic, not a
  deterministic pass/fail test result and not a human/orchestrator review.
  It is imported as ``reviewer_score`` with an explicit
  ``reviewer_provenance`` marker, and ``deterministic_score`` is left as
  ``None`` (N/A) because the legacy runner never executed a deterministic
  test oracle.
- The legacy track concept (pure prompt/response, no tool access) maps to
  the canonical ``"pure_model"`` track; the legacy runner never exercised a
  tool-agent track.
- ``csv_path`` is a fully configurable, arbitrary filesystem path: it may
  point inside this repository (for example one of the repo-local
  ``benchmark_results_resume_test*``/``benchmark_results_chaos_local``
  history CSVs) or entirely outside it (for example a user's home-directory
  ``benchmark_results\\migration_llm_bench_history.csv``). The resolved
  absolute path is recorded per sample in ``notes`` (``"source file: ..."``)
  for audit/provenance -- there is no dedicated schema column for this
  (adding one would be a silent, non-additive CSV change).
- The legacy ``error`` column is inspected to distinguish a genuine
  ``"timeout"`` status (message contains "timeout"/"timed out", for example
  ``"Siemens error: The read operation timed out"``) from a generic
  ``"error"`` status, matching the canonical ``schema.STATUSES`` vocabulary.
  Both statuses hard-fail the acceptance gate identically (see
  ``rubric.compute_sample_acceptance``); this only improves reporting
  precision, never ranking outcomes.
- The imported ``reviewer_score``/``reviewer_provenance`` is explicitly
  marked with a ``"confidence: estimated"`` note (never "measured") --
  reusing the same measured/estimated/projected/unknown vocabulary as
  ``model_inventory.CONFIDENCE_LEVELS`` -- and, because
  ``LEGACY_REVIEWER_PROVENANCE`` contains both "heuristic" and "keyword",
  ``rubric.is_heuristic_reviewer_provenance()`` always returns ``True`` for
  every imported row. This flows into
  ``AggregateRecord.reviewer_evidence_is_heuristic_only=True`` for any
  model-run whose evidence is exclusively historical-import rows, which in
  turn caps ``rubric.suitability_tier()`` below
  ``SUITABILITY_TIER_RECOMMENDED`` regardless of how high the raw
  ``quality_score`` is -- historical heuristic scores can never, by
  themselves, pass the hard "recommended" quality bar.
"""

from __future__ import annotations

import csv
import datetime as _dt
import re
from pathlib import Path
from typing import Optional

from . import rubric
from .schema import SCHEMA_VERSION, SampleRecord

LEGACY_TASK_CATEGORY = "legacy_migration_or_translation"
LEGACY_TRACK = "pure_model"
LEGACY_PROVIDER_BY_BACKEND = {
    "siemens": "siemens",
    "ollama": "local",
    "llama_cpp": "local",
}
LEGACY_REVIEWER_PROVENANCE = "legacy-heuristic-keyword-score (migration_llm_bench)"

#: Confidence label for the imported legacy ``quality_score`` -- shares the
#: same measured/estimated/projected/unknown vocabulary as
#: ``model_inventory.CONFIDENCE_LEVELS`` for consistency across the codebase.
#: A keyword/rule-based heuristic score is never "measured" (no genuine
#: human/orchestrator review or deterministic test oracle ever ran on it),
#: so it is always imported as ``"estimated"``, recorded explicitly in
#: ``SampleRecord.notes`` (there is no dedicated schema column for this --
#: adding one would be a silent, non-additive CSV change).
LEGACY_QUALITY_SCORE_CONFIDENCE = "estimated"

#: Case-insensitive substrings in the legacy ``error`` column that indicate a
#: timeout specifically, rather than a generic system/backend error. The
#: canonical schema (``schema.STATUSES``) distinguishes ``"timeout"`` from
#: ``"error"`` -- importing every legacy failure as generic ``"error"`` would
#: silently lose this distinction (both still hard-fail the acceptance gate
#: identically via ``rubric.compute_sample_acceptance``, so ranking is
#: unaffected either way, but sample-level reporting precision is not).
_TIMEOUT_ERROR_MARKERS = ("timed out", "timeout")

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _safe_id_part(value: str) -> str:
    return _SAFE_ID_RE.sub("-", value.strip()) or "unknown"


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if value == "" or value.upper() == "N/A":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_int(value: Optional[str]) -> Optional[int]:
    parsed = _parse_float(value)
    if parsed is None:
        return None
    return int(round(parsed))


def _legacy_status(error_text: str) -> str:
    """Map a legacy ``error`` column value to a canonical ``schema.STATUSES``
    value, distinguishing ``"timeout"`` from a generic ``"error"`` where the
    legacy error message says so (for example ``"Siemens error: The read
    operation timed out"``). Both statuses hard-fail the acceptance gate
    identically (see ``rubric.compute_sample_acceptance``), so this only
    affects reporting precision, never ranking/acceptance outcomes.
    """

    if not error_text:
        return "success"
    lowered = error_text.lower()
    if any(marker in lowered for marker in _TIMEOUT_ERROR_MARKERS):
        return "timeout"
    return "error"


def read_legacy_rows(csv_path: Path) -> list[dict[str, str]]:
    """Read the legacy CSV as raw string dict rows (no interpretation)."""

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def legacy_row_to_sample(
    row: dict[str, str],
    sample_seq: int,
    campaign_id: str,
    local_tzinfo: Optional[_dt.tzinfo] = None,
    source_path: Optional[Path] = None,
) -> SampleRecord:
    """Convert one raw legacy CSV row into a canonical :class:`SampleRecord`.

    ``local_tzinfo`` defaults to the importing machine's current local
    timezone offset; pass an explicit value (for example
    ``datetime.timezone.utc``) for reproducible tests.

    ``source_path`` is the legacy CSV file this row was read from -- it may
    be anywhere on disk, inside or outside this repository (for example a
    resume-history CSV under a repo-local ``benchmark_results_*`` directory,
    or an external path such as a user's home-directory
    ``benchmark_results\\migration_llm_bench_history.csv``). When given, its
    resolved absolute path is recorded in ``notes`` for audit/provenance --
    there is no dedicated schema column for it (adding one would be a
    silent, non-additive CSV change), and ``artifact_path`` is reserved for
    the legacy row's own ``source_csv`` value, which is a different,
    legacy-runner-recorded field and not necessarily the file actually
    passed to this import.
    """

    tzinfo = local_tzinfo or _dt.datetime.now().astimezone().tzinfo

    backend = (row.get("backend") or "").strip()
    model = (row.get("model") or "").strip()
    case_id = (row.get("case_id") or "").strip()
    run = (row.get("run") or "1").strip() or "1"
    benchmark_run_id = (row.get("benchmark_run_id") or "").strip()

    wall_ms = _parse_float(row.get("wall_ms"))
    elapsed_seconds = round(wall_ms / 1000.0, 3) if wall_ms is not None else None

    recorded_at_raw = (row.get("recorded_at") or "").strip()
    end_time_str: str
    start_time_str: str
    timestamp_note: Optional[str] = None
    if recorded_at_raw:
        try:
            end_dt = _dt.datetime.strptime(recorded_at_raw, "%Y-%m-%d %H:%M:%S")
            end_dt = end_dt.replace(tzinfo=tzinfo)
            start_dt = end_dt - _dt.timedelta(seconds=elapsed_seconds or 0.0)
            end_time_str = end_dt.isoformat(timespec="milliseconds")
            start_time_str = start_dt.isoformat(timespec="milliseconds")
            timestamp_note = (
                "legacy recorded_at had no timezone; assumed local system "
                "timezone at import time"
            )
        except ValueError:
            end_time_str = _dt.datetime.now(_dt.timezone.utc).isoformat(
                timespec="milliseconds"
            )
            start_time_str = end_time_str
            timestamp_note = (
                f"legacy recorded_at {recorded_at_raw!r} was unparsable; "
                "import time used as a placeholder"
            )
    else:
        end_time_str = _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="milliseconds"
        )
        start_time_str = end_time_str
        timestamp_note = "legacy row had no recorded_at; import time used as a placeholder"

    prompt_tokens = _parse_int(row.get("prompt_tokens"))
    output_tokens = _parse_int(row.get("output_tokens"))
    total_tokens = (
        prompt_tokens + output_tokens
        if prompt_tokens is not None and output_tokens is not None
        else None
    )

    error_text = (row.get("error") or "").strip()
    status = _legacy_status(error_text)

    quality_score = _parse_float(row.get("quality_score"))

    # The legacy pipeline never ran a deterministic test oracle and never
    # flagged unsafe/placeholder output explicitly, so this hard-gate
    # acceptance verdict is necessarily derived from the legacy heuristic
    # keyword/rule-based quality_score alone (via reviewer_score) plus the
    # error/success status -- it is a best-effort retrofit, not a genuine
    # re-review, and is documented as such in ``notes`` below.
    acceptance_status, acceptance_reasons = rubric.compute_sample_acceptance(
        status=status,
        deterministic_score=None,
        reviewer_score=quality_score,
        unsafe_behavior_flag=None,
        output_placeholder_or_incomplete=None,
    )

    sample_id = "-".join(
        [
            "legacy",
            _safe_id_part(benchmark_run_id or "unknownrun"),
            _safe_id_part(backend or "unknownbackend"),
            _safe_id_part(model or "unknownmodel"),
            _safe_id_part(case_id or "unknowncase"),
            f"run{_safe_id_part(run)}",
            # sample_seq (the strictly increasing per-CSV-row enumeration
            # index passed in by import_legacy_history_csv) is always
            # appended so sample_id stays globally unique even when the
            # legacy CSV itself never incremented its own "run" column for
            # a same-task retry (observed in real migration_llm_bench
            # history data: a task can appear twice with an identical
            # case_id+run after a timeout was retried). Without this,
            # colliding sample_ids would silently overwrite each other via
            # storage.py's PRIMARY KEY("sample_id") + INSERT OR REPLACE,
            # discarding real historical evidence (for example a timeout)
            # -- see HistoricalAdapterTests.
            f"seq{sample_seq}",
        ]
    )

    notes_parts = [
        "imported from legacy migration_llm_bench CSV",
        (
            "ram_avg_mb/ram_max_mb are N/A: legacy stored RAM as percent of "
            "total system RAM (avg_mem_pct/max_mem_pct), not MB, and cannot "
            "be honestly converted without the legacy machine's total RAM"
        ),
        (
            "acceptance_status is a best-effort retrofit using the legacy "
            "keyword/rule-based quality_score as reviewer_score; no "
            "deterministic test oracle or unsafe/placeholder review ever "
            "ran on this row, so this is not a genuine re-review"
        ),
        (
            f"confidence: {LEGACY_QUALITY_SCORE_CONFIDENCE} (reviewer_score "
            "is a heuristic keyword-match approximation, never 'measured' "
            "genuine review evidence -- see rubric.is_heuristic_reviewer_"
            "provenance / AggregateRecord.reviewer_evidence_is_heuristic_only, "
            "which this reviewer_provenance marker triggers)"
        ),
        f"legacy run index: {run}",
    ]
    if source_path is not None:
        notes_parts.append(f"source file: {Path(source_path).resolve()}")
    if timestamp_note:
        notes_parts.append(timestamp_note)
    keyword_hits = row.get("keyword_hits")
    keyword_total = row.get("keyword_total")
    forbidden_hits = row.get("forbidden_hits")
    if keyword_hits or keyword_total or forbidden_hits:
        notes_parts.append(
            f"legacy heuristic detail: keyword_hits={keyword_hits!r} "
            f"keyword_total={keyword_total!r} forbidden_hits={forbidden_hits!r}"
        )

    return SampleRecord(
        schema_version=SCHEMA_VERSION,
        campaign_id=campaign_id,
        run_id=benchmark_run_id or campaign_id,
        sample_id=sample_id,
        benchmark_set=(row.get("benchmark_name") or "").strip() or "unknown",
        benchmark_version=(row.get("benchmark_spec_version") or "").strip() or "unknown",
        track=LEGACY_TRACK,
        task_id=case_id or "unknown",
        task_name=(row.get("case_title") or case_id or "unknown").strip(),
        task_category=LEGACY_TASK_CATEGORY,
        provider=LEGACY_PROVIDER_BY_BACKEND.get(backend, backend or "unknown"),
        backend=backend or "unknown",
        model=model or "unknown",
        runtime=None,
        quantization=None,
        start_time=start_time_str,
        end_time=end_time_str,
        elapsed_seconds=elapsed_seconds,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        tokens_per_second=_parse_float(row.get("output_tps")),
        ttft_seconds=None,
        cpu_time_seconds=_parse_float(row.get("cpu_time_sec")),
        cpu_avg_percent=_parse_float(row.get("avg_cpu_pct")),
        cpu_max_percent=_parse_float(row.get("max_cpu_pct")),
        ram_avg_mb=None,
        ram_max_mb=None,
        gpu_avg_percent=_parse_float(row.get("avg_gpu_pct")),
        gpu_max_percent=_parse_float(row.get("max_gpu_pct")),
        vram_avg_mb=_parse_float(row.get("avg_vram_used_mb")),
        vram_max_mb=_parse_float(row.get("max_vram_used_mb")),
        deterministic_score=None,
        reviewer_score=quality_score,
        collaboration_score=None,
        composite_score=None,
        status=status,
        system_error_flag=bool(error_text),
        system_error_code=("timeout" if status == "timeout" else None),
        system_error_message=error_text or None,
        retry_count=0,
        iteration_index=0,
        artifact_path=(row.get("source_csv") or None),
        artifact_hash=None,
        reviewer_provenance=LEGACY_REVIEWER_PROVENANCE,
        reviewer_notes=(row.get("output_preview") or None),
        hardware_profile=None,
        hardware_gpu_model=None,
        hardware_vram_total_mb=None,
        sample_seq=sample_seq,
        provenance="historical_import",
        notes="; ".join(notes_parts),
        unsafe_behavior_flag=None,
        output_placeholder_or_incomplete=None,
        acceptance_status=acceptance_status,
        acceptance_reasons=acceptance_reasons,
    )


def import_legacy_history_csv(
    csv_path: Path,
    campaign_id: Optional[str] = None,
    local_tzinfo: Optional[_dt.tzinfo] = None,
) -> list[SampleRecord]:
    """Import an entire legacy history CSV into canonical sample records.

    ``csv_path`` may be any path accepted by :class:`pathlib.Path` -- a
    relative path, a path inside this repository (for example one of the
    repo-local ``benchmark_results_resume_test*``/``benchmark_results_chaos_
    local`` history CSVs), or an absolute path entirely outside the
    repository (for example a user's home-directory
    ``benchmark_results\\migration_llm_bench_history.csv``). This function
    never writes to ``csv_path`` and never requires it to live under the
    repository root.

    ``campaign_id`` defaults to ``"legacy-import-<csv file stem>"`` so
    imported data is clearly isolated from measured agent-helper campaigns.
    """

    resolved_campaign_id = campaign_id or f"legacy-import-{csv_path.stem}"
    rows = read_legacy_rows(csv_path)
    return [
        legacy_row_to_sample(
            row,
            sample_seq=index,
            campaign_id=resolved_campaign_id,
            local_tzinfo=local_tzinfo,
            source_path=csv_path,
        )
        for index, row in enumerate(rows, start=1)
    ]
