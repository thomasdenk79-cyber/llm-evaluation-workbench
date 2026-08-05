"""Canonical schema for the agent-helper evaluation harness.

Two record types are defined:

- :class:`SampleRecord` -- one row per subtask attempt (task-attempt level).
- :class:`AggregateRecord` -- one row per benchmark-set + backend + model run
  (model-run level), built from a group of ``SampleRecord`` rows.

Both are exported to CSV with a fixed, explicit column order
(``SAMPLE_CSV_COLUMNS`` / ``AGGREGATE_CSV_COLUMNS``). Columns must never be
reordered or silently removed; only additive, backward-compatible schema
changes are allowed within ``agent-helper-v1``. A breaking change requires a
new ``SCHEMA_VERSION`` and an explicit migration note in the changelog.

Unavailable metrics (for example GitHub Copilot agent tokens/sec) are stored
as ``None`` internally (a real SQL ``NULL``, so aggregation math stays
correct) and rendered as the literal string ``"N/A"`` in CSV/report output.
Never invent a value for a metric a runtime cannot expose.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import re
from typing import Any, Optional

SCHEMA_VERSION = "agent-helper-v1"

#: Literal placeholder written to CSV/report cells for genuinely unavailable
#: metrics. Never used to mean "zero" or "unknown due to a bug".
NA = "N/A"

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------

#: Pure-model track: a single prompt/response exchange with no tool access.
#: Tool-agent track: a delegated helper task run through an agentic tool
#: (for example OpenCode or the GitHub Copilot CLI/agent) with real actions.
TRACKS = ("pure_model", "tool_agent")

STATUSES = ("success", "error", "timeout", "skipped")

#: How a sample/aggregate row came to exist.
PROVENANCES = ("measured", "historical_import", "projected", "synthetic_dry_run")

#: Coarse fit/suitability tiers used in the aggregate report and leaderboard.
#: "not-usable" is a *hard*, gate-driven exclusion -- it always wins over any
#: score-based tier below it and means "excluded from ranking regardless of
#: speed/TPS" (see agent_helper_eval.rubric.compute_aggregate_hard_gate()).
#: "gate-passed-provisional" is a *stage/evidence-sufficiency* exclusion,
#: distinct from a quality-based tier: a model-run whose only evidence is a
#: narrow connect/mini smoke gate (no reviewer score, no collaboration
#: evidence, insufficient task/category coverage -- see
#: agent_helper_eval.rubric.evidence_stage()) can never be reported as
#: "tier-1-recommended"/"strong candidate" even with a perfect score, because
#: passing a smoke gate is not the same claim as being suitable for daily-
#: runner delegation.
SUITABILITY_TIERS = (
    "not-usable",
    "gate-passed-provisional",
    "tier-1-recommended",
    "tier-2-conditional",
    "tier-3-not-recommended",
    "insufficient-data",
)

#: How much real evidence backs a model-run's suitability verdict,
#: independent of how good its scores are -- see
#: agent_helper_eval.rubric.evidence_stage()/MIN_TASK_CATEGORIES_FOR_TIER1.
#: "gate_only": only connect/mini smoke-type gate(s) ran (typically 1-2
#: task categories, no reviewer/collaboration evidence at all).
#: "partial_suite": some broader coverage exists, but not enough distinct
#: task categories and/or missing reviewer or collaboration evidence.
#: "full_suite": meets the configured minimum bar (category coverage +
#: reviewer score + collaboration evidence) to even be eligible for
#: "tier-1-recommended".
EVIDENCE_STAGES = ("gate_only", "partial_suite", "full_suite")

#: Per-attempt acceptance verdict, computed by
#: agent_helper_eval.rubric.compute_sample_acceptance() from the attempt's
#: status, deterministic score, reviewer score, and unsafe/placeholder flags.
#: "accepted": positive evidence of a usable, correct, safe, complete result.
#: "not_usable": at least one hard-gate criterion failed (failed deterministic
#: tests, placeholder/incomplete output, unsafe behavior, or reviewer/
#: orchestrator quality below threshold, or a system error/timeout status).
#: "not_evaluated": no acceptance-gate evidence was recorded for this attempt
#: (never silently treated as "accepted").
ACCEPTANCE_STATUSES = ("accepted", "not_usable", "not_evaluated")


_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


class SchemaValidationError(ValueError):
    """Raised when a record violates the canonical agent-helper schema."""


def utc_now_iso() -> str:
    """Return the current time as an ISO-8601 string with an explicit offset."""

    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def is_timestamp_with_tz(value: str) -> bool:
    """True if ``value`` is an ISO-8601 timestamp with an explicit timezone."""

    return bool(_TIMESTAMP_RE.match(value))


def round_ms(value: Optional[float]) -> Optional[float]:
    """Round elapsed seconds to millisecond precision (3 decimals)."""

    if value is None:
        return None
    return round(float(value), 3)


# ---------------------------------------------------------------------------
# Sample / task-attempt record
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SampleRecord:
    """One row per subtask attempt (task-attempt level).

    Field order below is the authoritative CSV column order
    (see :data:`SAMPLE_CSV_COLUMNS`).
    """

    schema_version: str
    campaign_id: str
    run_id: str
    sample_id: str
    benchmark_set: str
    benchmark_version: str
    track: str
    task_id: str
    task_name: str
    task_category: str
    provider: str
    backend: str
    model: str
    runtime: Optional[str]
    quantization: Optional[str]
    start_time: str
    end_time: str
    elapsed_seconds: Optional[float]
    prompt_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    tokens_per_second: Optional[float]
    ttft_seconds: Optional[float]
    #: Generic/legacy "CPU time" column, retained as-is for historical-
    #: import compatibility (see historical_adapter.py, which parses the
    #: legacy migration_llm_bench runner's own ``cpu_time_sec`` column
    #: verbatim into here -- its exact producer-side meaning is whatever
    #: that legacy runner measured). New measured samples should prefer the
    #: explicitly-named, disambiguated orchestrator_cpu_time_seconds /
    #: model_cpu_time_seconds fields below instead of populating this
    #: generic column, so "whose CPU time is this?" is never ambiguous.
    cpu_time_seconds: Optional[float]
    cpu_avg_percent: Optional[float]
    cpu_max_percent: Optional[float]
    ram_avg_mb: Optional[float]
    ram_max_mb: Optional[float]
    gpu_avg_percent: Optional[float]
    gpu_max_percent: Optional[float]
    vram_avg_mb: Optional[float]
    vram_max_mb: Optional[float]
    deterministic_score: Optional[float]
    reviewer_score: Optional[float]
    collaboration_score: Optional[float]
    composite_score: Optional[float]
    status: str
    system_error_flag: bool
    system_error_code: Optional[str]
    system_error_message: Optional[str]
    retry_count: int
    iteration_index: int
    artifact_path: Optional[str]
    artifact_hash: Optional[str]
    reviewer_provenance: Optional[str]
    reviewer_notes: Optional[str]
    hardware_profile: Optional[str]
    hardware_gpu_model: Optional[str]
    hardware_vram_total_mb: Optional[float]
    sample_seq: int
    provenance: str
    notes: Optional[str] = None

    # -- Hard acceptance gate fields (appended; additive within agent-helper-v1) --
    #: True = confirmed unsafe behavior detected (destructive action outside
    #: scope, disallowed content, prompt-injection compliance, ...); False =
    #: explicitly checked and clear; None = not evaluated for this attempt.
    #: Zero tolerance: True always forces acceptance_status="not_usable".
    unsafe_behavior_flag: Optional[bool] = None
    #: True = the output is a stub/placeholder/truncated/incomplete
    #: deliverable; False = explicitly checked and complete; None = not
    #: evaluated. True always forces acceptance_status="not_usable".
    output_placeholder_or_incomplete: Optional[bool] = None
    #: Computed by agent_helper_eval.rubric.compute_sample_acceptance() from
    #: this attempt's status/scores/flags -- never set ad hoc. See
    #: ACCEPTANCE_STATUSES for the controlled vocabulary.
    acceptance_status: str = "not_evaluated"
    #: Semicolon-joined human-readable reasons for a "not_usable"/
    #: "not_evaluated" verdict (empty/None when "accepted").
    acceptance_reasons: Optional[str] = None

    # -- Phase attribution / capacity-planning timings (appended; additive --
    # within agent-helper-v1). Every field is instrumented "where observable"
    # only -- never estimated. GitHub Copilot task-agent references in
    # particular typically cannot expose any internal LLM-side phase timing
    # at all; leave those fields None (N/A) rather than guessing. See
    # agent_helper_eval.phase_timing for how these raw seconds are turned
    # into exclusive critical-path percentages and non-exclusive utilization
    # ratios without silently double-counting overlapping phases. --
    #: Full observed wall-clock duration of the attempt, used as the
    #: reconciliation denominator for critical-path percentages. May differ
    #: slightly from elapsed_seconds if instrumented by a different clock.
    total_wall_seconds: Optional[float] = None
    #: Time the LLM call spent queued/waiting before service started (for
    #: example blocked on the local one-model lock, or backend-side queuing
    #: under concurrency). Not LLM execution time itself.
    llm_queue_seconds: Optional[float] = None
    #: Top-level, measured wall-clock duration of the primary LLM API call
    #: (request sent to response fully received). Preferred over the
    #: prompt_eval_seconds/generation_seconds sum whenever a runtime exposes
    #: BOTH a top-level wall-clock measurement AND a finer split (e.g.
    #: Ollama/llama.cpp) -- see agent_helper_eval.phase_timing.llm_bucket_seconds().
    #: The whole API call (including any cold model-load time folded into
    #: it) is classified as LLM/API critical-path time; it must never be
    #: reclassified as queue/idle time (model load is service time, not
    #: waiting). Only leave this None when a runtime exposes *just* the
    #: prompt_eval/generation split with no measured top-level wall-clock at
    #: all.
    llm_request_seconds: Optional[float] = None
    #: Sub-phase of llm_request_seconds: prompt/prefill processing time
    #: (only when the runtime reports this split, e.g. Ollama/llama.cpp).
    #: Purely diagnostic/informational once llm_request_seconds is set --
    #: never summed on top of it (that would double count the same call).
    prompt_eval_seconds: Optional[float] = None
    #: Sub-phase of llm_request_seconds: output token generation/decode time.
    #: Same diagnostic-only status as prompt_eval_seconds above.
    generation_seconds: Optional[float] = None
    #: Time spent executing local tool actions (file edits, shell commands)
    #: by a tool-agent runtime, outside the LLM call itself.
    local_tool_exec_seconds: Optional[float] = None
    #: Time spent running deterministic tests against the produced artifact.
    test_exec_seconds: Optional[float] = None
    #: Time spent on orchestrator/reviewer scoring of the attempt.
    orchestrator_review_seconds: Optional[float] = None
    #: Dead time not attributable to any other phase (for example filesystem
    #: I/O wait, lock-acquisition wait outside llm_queue_seconds).
    idle_wait_seconds: Optional[float] = None
    #: Explicit bookkeeping for concurrent/overlapping phases (for example
    #: test execution running while orchestrator review also happens). This
    #: is what lets downstream percentage calculations stay honest about
    #: double-counting instead of silently summing overlapping durations --
    #: see agent_helper_eval.phase_timing.compute_exclusive_critical_path().
    overlap_seconds: Optional[float] = None

    # -- Phase-timing subcomponent + explicit process CPU-time fields --
    # (appended; additive within agent-helper-v1; see the pilot-review
    # remediation note in docs/project/agent_helper_benchmark.md SS16.5). --
    #: Diagnostic-only subcomponent of llm_request_seconds: the runtime's
    #: own reported cold model-load duration (e.g. Ollama's
    #: ``load_duration``). Already contained within llm_request_seconds'
    #: measured wall-clock -- never summed on top of it, and never folded
    #: into llm_queue_seconds/idle_wait_seconds (a model load is real LLM/
    #: API service time, not queueing/idle time).
    model_load_seconds: Optional[float] = None
    #: CPU time (seconds of CPU actually consumed, via ``time.process_time()``)
    #: of THIS harness/orchestrator process for the attempt -- distinct from
    #: cpu_avg_percent/cpu_max_percent (system-wide utilization percentage)
    #: and distinct from model_cpu_time_seconds below (the model backend's
    #: own process). None only if not measured for this attempt (e.g. a
    #: historical-import row that never captured it).
    orchestrator_cpu_time_seconds: Optional[float] = None
    #: Best-effort CPU time (seconds) of the local model backend's own
    #: process (e.g. the Ollama/llama.cpp server), via
    #: ``psutil.Process.cpu_times()`` deltas -- see
    #: agent_helper_eval.resource_monitor.ResourceMonitor. None/N/A when not
    #: attributable: a remote backend (Siemens API, GitHub Copilot agent)
    #: has no local process to measure at all, and a local measurement that
    #: failed (no matching process found, access denied) is left None
    #: rather than estimated.
    model_cpu_time_seconds: Optional[float] = None
    io_read: Optional[int] = None
    io_write: Optional[int] = None


SAMPLE_CSV_COLUMNS = tuple(f.name for f in dataclasses.fields(SampleRecord))

#: Raw phase-timing seconds fields on SampleRecord, in canonical bucket
#: reconciliation order (see agent_helper_eval.phase_timing).
PHASE_TIMING_SECONDS_FIELDS = (
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
)


def validate_sample(record: SampleRecord) -> list[str]:
    """Return a list of human-readable validation errors (empty if valid)."""

    errors: list[str] = []
    if record.schema_version != SCHEMA_VERSION:
        errors.append(
            f"schema_version mismatch: expected {SCHEMA_VERSION!r}, "
            f"got {record.schema_version!r}"
        )
    for required in (
        "campaign_id",
        "run_id",
        "sample_id",
        "benchmark_set",
        "benchmark_version",
        "task_id",
        "task_name",
        "task_category",
        "provider",
        "backend",
        "model",
    ):
        if not getattr(record, required):
            errors.append(f"{required} must be a non-empty string")
    if record.track not in TRACKS:
        errors.append(f"track must be one of {TRACKS}, got {record.track!r}")
    if record.status not in STATUSES:
        errors.append(f"status must be one of {STATUSES}, got {record.status!r}")
    if record.provenance not in PROVENANCES:
        errors.append(
            f"provenance must be one of {PROVENANCES}, got {record.provenance!r}"
        )
    if not is_timestamp_with_tz(record.start_time):
        errors.append(
            f"start_time must be ISO-8601 with an explicit timezone, "
            f"got {record.start_time!r}"
        )
    if not is_timestamp_with_tz(record.end_time):
        errors.append(
            f"end_time must be ISO-8601 with an explicit timezone, "
            f"got {record.end_time!r}"
        )
    if record.elapsed_seconds is not None and record.elapsed_seconds < 0:
        errors.append("elapsed_seconds must be >= 0")
    for score_field in (
        "deterministic_score",
        "reviewer_score",
        "collaboration_score",
        "composite_score",
    ):
        value = getattr(record, score_field)
        if value is not None and not (0.0 <= value <= 100.0):
            errors.append(f"{score_field} must be within 0..100, got {value!r}")
    if record.retry_count < 0:
        errors.append("retry_count must be >= 0")
    if record.iteration_index < 0:
        errors.append("iteration_index must be >= 0")
    if record.sample_seq < 0:
        errors.append("sample_seq must be >= 0")
    if record.status == "error" and not record.system_error_flag:
        errors.append("status=error requires system_error_flag=True")
    if record.acceptance_status not in ACCEPTANCE_STATUSES:
        errors.append(
            f"acceptance_status must be one of {ACCEPTANCE_STATUSES}, "
            f"got {record.acceptance_status!r}"
        )
    # Hard-gate consistency: a flagged unsafe/incomplete attempt can never be
    # silently stored as "accepted" -- catches a bug in whatever computed
    # acceptance_status rather than trusting it blindly.
    if record.unsafe_behavior_flag is True and record.acceptance_status != "not_usable":
        errors.append(
            "unsafe_behavior_flag=True requires acceptance_status='not_usable' "
            "(zero tolerance for unsafe behavior)"
        )
    if (
        record.output_placeholder_or_incomplete is True
        and record.acceptance_status != "not_usable"
    ):
        errors.append(
            "output_placeholder_or_incomplete=True requires "
            "acceptance_status='not_usable'"
        )
    for phase_field in PHASE_TIMING_SECONDS_FIELDS:
        value = getattr(record, phase_field)
        if value is not None and value < 0:
            errors.append(f"{phase_field} must be >= 0, got {value!r}")
    for non_negative_field in (
        "model_load_seconds",
        "orchestrator_cpu_time_seconds",
        "model_cpu_time_seconds",
    ):
        value = getattr(record, non_negative_field)
        if value is not None and value < 0:
            errors.append(f"{non_negative_field} must be >= 0, got {value!r}")
    # Overlap-honesty check: once overlap_seconds is subtracted, the naive
    # sum of the four exclusive buckets must not exceed the observed wall
    # time by more than a small floating-point tolerance -- this is what
    # keeps "make overlap semantics explicit" an enforced schema invariant
    # rather than just documentation (see agent_helper_eval.phase_timing).
    if record.total_wall_seconds is not None:
        llm_bucket = (
            record.llm_request_seconds
            if record.llm_request_seconds is not None
            else sum(
                v
                for v in (record.prompt_eval_seconds, record.generation_seconds)
                if v is not None
            )
            or None
        )
        bucket_values = [
            v
            for v in (
                llm_bucket,
                record.local_tool_exec_seconds,
                record.test_exec_seconds,
                record.orchestrator_review_seconds,
                record.llm_queue_seconds,
                record.idle_wait_seconds,
            )
            if v is not None
        ]
        if bucket_values:
            naive_sum = sum(bucket_values)
            overlap = record.overlap_seconds or 0.0
            tolerance = max(0.05, 0.01 * record.total_wall_seconds)
            if naive_sum - overlap > record.total_wall_seconds + tolerance:
                errors.append(
                    "phase-timing seconds exceed total_wall_seconds even "
                    f"after subtracting overlap_seconds ({naive_sum:.3f}s of "
                    f"phases - {overlap:.3f}s overlap > "
                    f"{record.total_wall_seconds:.3f}s wall time); check "
                    "instrumentation or record the missing overlap_seconds"
                )
    return errors


# ---------------------------------------------------------------------------
# Aggregate / model-run record
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class AggregateRecord:
    """One row per benchmark-set + backend + model run (model-run level)."""

    schema_version: str
    campaign_id: str
    run_id: str
    benchmark_set: str
    benchmark_version: str
    track: str
    provider: str
    backend: str
    model: str
    runtime: Optional[str]
    quantization: Optional[str]
    task_count: int
    success_count: int
    error_count: int
    skipped_count: int
    success_rate_percent: float
    error_rate_percent: float
    elapsed_seconds_total: Optional[float]
    elapsed_seconds_mean: Optional[float]
    elapsed_seconds_p50: Optional[float]
    elapsed_seconds_p95: Optional[float]
    prompt_tokens_total: Optional[int]
    output_tokens_total: Optional[int]
    total_tokens_total: Optional[int]
    tokens_per_second_mean: Optional[float]
    ttft_seconds_mean: Optional[float]
    cpu_avg_percent_mean: Optional[float]
    cpu_max_percent_max: Optional[float]
    ram_avg_mb_mean: Optional[float]
    ram_max_mb_max: Optional[float]
    gpu_avg_percent_mean: Optional[float]
    gpu_max_percent_max: Optional[float]
    vram_avg_mb_mean: Optional[float]
    vram_max_mb_max: Optional[float]
    deterministic_score_mean: Optional[float]
    reviewer_score_mean: Optional[float]
    collaboration_score_mean: Optional[float]
    overall_score: Optional[float]
    retries_total: int
    iterations_mean: Optional[float]
    campaign_elapsed_seconds_total: Optional[float]
    suitability_tier: str
    recommendation: Optional[str]
    notes: Optional[str]
    provenance: str
    generated_at: str

    # -- Hard acceptance gate fields (appended; additive within agent-helper-v1) --
    #: Counts of this group's samples by acceptance_status (see
    #: ACCEPTANCE_STATUSES); accepted + not_usable + not_evaluated == task_count.
    accepted_sample_count: int = 0
    not_usable_sample_count: int = 0
    not_evaluated_sample_count: int = 0
    #: 100 * accepted_sample_count / task_count (None if task_count == 0).
    acceptance_rate_percent: Optional[float] = None
    #: Count of samples with unsafe_behavior_flag == True. Any value > 0
    #: forces hard_gate_failed=True (zero tolerance).
    unsafe_sample_count: int = 0
    #: Distinct task_ids in this group that were attempted (>=1 iteration)
    #: but never reached an accepted result -- a direct "repeated failures
    #: never converged" signal, independent of how fast those attempts were.
    unresolved_task_count: int = 0
    #: Mean, over distinct task_ids that *did* reach acceptance, of the
    #: cumulative elapsed time from iteration 1 up to and including the
    #: accepted iteration (rubric.time_to_accepted_result()). None if no
    #: task in this group was ever accepted.
    time_to_accepted_result_seconds_mean: Optional[float] = None
    #: Mean cumulative tokens spent per accepted task up to acceptance -- a
    #: total review/rework cost proxy alongside time_to_accepted_result.
    rework_tokens_to_accept_mean: Optional[float] = None
    #: True if this model-run failed the hard acceptance gate
    #: (rubric.compute_aggregate_hard_gate()): unsafe behavior anywhere, or
    #: too low a share of evaluated attempts were accepted. A True value
    #: means "not-usable" and *excluded from ranking regardless of speed/
    #: TPS* -- performance is only ever a tie-breaker among accepted,
    #: usable candidates.
    hard_gate_failed: bool = False
    #: Semicolon-joined human-readable reasons for hard_gate_failed=True.
    hard_gate_reasons: Optional[str] = None
    #: True if every reviewer_score in this group came from a legacy/
    #: heuristic keyword-matching scorer (see
    #: rubric.is_heuristic_reviewer_provenance()) AND no deterministic
    #: test-oracle evidence exists anywhere in the group. This does NOT
    #: exclude the model-run (unlike hard_gate_failed) -- it caps
    #: suitability_tier below "tier-1-recommended" and adds an explicit
    #: caveat to the recommendation text, so a model is never labeled
    #: "suitable"/"strong candidate" on the strength of a keyword score
    #: alone (see rubric.suitability_tier()).
    reviewer_evidence_is_heuristic_only: bool = False

    # -- Phase attribution / capacity-planning rollups (appended; additive --
    # within agent-helper-v1). Computed by agent_helper_eval.phase_timing
    # as a *ratio of summed seconds* across every sample in the group that
    # reported total_wall_seconds (never a naive mean-of-percentages, which
    # would over-weight short attempts) -- see
    # agent_helper_eval.phase_timing.rollup_group(). --
    #: How many of this group's samples had at least total_wall_seconds
    #: instrumented. 0 means none of the four *_critical_path_percent /
    #: *_busy_percent fields below could be computed (all None/N/A) --
    #: reports must show "not instrumented" rather than a misleading 0%.
    phase_timing_sample_count: int = 0
    #: Exclusive critical-path share of LLM execution time (the four
    #: *_critical_path_percent fields always sum to ~100% of the group's
    #: total *accounted-for* phase time by construction -- see
    #: phase_timing.compute_exclusive_critical_path()).
    llm_critical_path_percent: Optional[float] = None
    #: Exclusive critical-path share of local tool execution + deterministic
    #: test execution time.
    tool_critical_path_percent: Optional[float] = None
    #: Exclusive critical-path share of LLM-queue-wait + otherwise
    #: unattributed idle time.
    queue_idle_critical_path_percent: Optional[float] = None
    #: Exclusive critical-path share of orchestrator/reviewer scoring time.
    orchestration_critical_path_percent: Optional[float] = None
    #: Diagnostic only (NOT part of the 100% split above): how much of the
    #: naively-summed phase time was concurrent/overlapping relative to the
    #: observed wall-clock time, i.e. how much would have been double
    #: counted if overlap were ignored. None when not computable.
    phase_timing_overlap_ratio_percent: Optional[float] = None
    #: Diagnostic only: how much observed wall-clock time was *not*
    #: attributed to any of the four phase buckets at all (an
    #: instrumentation gap), as a percent of wall-clock time. None when not
    #: computable.
    phase_timing_unaccounted_ratio_percent: Optional[float] = None
    #: Non-exclusive utilization ratio: LLM-busy time as a percent of
    #: wall-clock time. Unlike the critical-path percentages above, this is
    #: NOT capped at 100% -- under concurrency (see the capacity-profile
    #: spec) multiple simultaneously-busy requests can legitimately sum
    #: above 100% of one wall-clock window.
    model_busy_percent: Optional[float] = None
    #: Non-exclusive utilization ratio: GPU-active time as a percent of
    #: wall-clock time. Approximated as equal to model_busy_percent for
    #: local (ollama/llama_cpp) backends when no independent GPU-active
    #: measurement is available (documented approximation, see
    #: phase_timing.compute_utilization_ratios()); left None/N/A for
    #: backends the harness has no GPU visibility into (Siemens API,
    #: Copilot agent).
    gpu_active_percent: Optional[float] = None
    #: Non-exclusive utilization ratio: local-tool-runner-busy time as a
    #: percent of wall-clock time.
    tool_runner_busy_percent: Optional[float] = None

    # -- Evidence-sufficiency staging + explicit CPU-time rollups (appended;
    # additive within agent-helper-v1; see the pilot-review remediation note
    # in docs/project/agent_helper_benchmark.md SS16.5). --
    #: How much real evidence backs this group's suitability verdict,
    #: independent of its score -- see EVIDENCE_STAGES / rubric.evidence_stage().
    #: Defaults to "full_suite" for backward compatibility with every
    #: existing producer (dry-run/historical-import/capacity-profile
    #: aggregates already cover multiple categories); only
    #: agent_helper_eval.live_gates' single connect/mini-gate aggregates
    #: explicitly pass "gate_only". rubric.suitability_tier() caps the tier
    #: at "gate-passed-provisional" (never "tier-1-recommended") whenever
    #: this is not "full_suite".
    evidence_stage: str = "full_suite"
    #: Mean diagnostic-only model cold-load time across this group's
    #: instrumented samples (see SampleRecord.model_load_seconds). None if
    #: no sample in the group reported it.
    model_load_seconds_mean: Optional[float] = None
    #: Mean orchestrator/harness process CPU time across this group's
    #: samples (see SampleRecord.orchestrator_cpu_time_seconds).
    orchestrator_cpu_time_seconds_mean: Optional[float] = None
    #: Mean best-effort model-backend process CPU time across this group's
    #: samples (see SampleRecord.model_cpu_time_seconds). None if never
    #: attributable for any sample in the group.
    model_cpu_time_seconds_mean: Optional[float] = None
    io_read_total: Optional[int] = None
    io_write_total: Optional[int] = None


AGGREGATE_CSV_COLUMNS = tuple(f.name for f in dataclasses.fields(AggregateRecord))


def validate_aggregate(record: AggregateRecord) -> list[str]:
    """Return a list of human-readable validation errors (empty if valid)."""

    errors: list[str] = []
    if record.schema_version != SCHEMA_VERSION:
        errors.append(
            f"schema_version mismatch: expected {SCHEMA_VERSION!r}, "
            f"got {record.schema_version!r}"
        )
    for required in (
        "campaign_id",
        "run_id",
        "benchmark_set",
        "benchmark_version",
        "provider",
        "backend",
        "model",
    ):
        if not getattr(record, required):
            errors.append(f"{required} must be a non-empty string")
    if record.track not in TRACKS:
        errors.append(f"track must be one of {TRACKS}, got {record.track!r}")
    if record.provenance not in PROVENANCES:
        errors.append(
            f"provenance must be one of {PROVENANCES}, got {record.provenance!r}"
        )
    if record.suitability_tier not in SUITABILITY_TIERS:
        errors.append(
            f"suitability_tier must be one of {SUITABILITY_TIERS}, "
            f"got {record.suitability_tier!r}"
        )
    if record.task_count < 0:
        errors.append("task_count must be >= 0")
    counted = record.success_count + record.error_count + record.skipped_count
    if counted != record.task_count:
        errors.append(
            "success_count + error_count + skipped_count must equal task_count "
            f"({counted} != {record.task_count})"
        )
    if not is_timestamp_with_tz(record.generated_at):
        errors.append(
            f"generated_at must be ISO-8601 with an explicit timezone, "
            f"got {record.generated_at!r}"
        )
    for score_field in (
        "deterministic_score_mean",
        "reviewer_score_mean",
        "collaboration_score_mean",
        "overall_score",
    ):
        value = getattr(record, score_field)
        if value is not None and not (0.0 <= value <= 100.0):
            errors.append(f"{score_field} must be within 0..100, got {value!r}")
    accepted_counted = (
        record.accepted_sample_count
        + record.not_usable_sample_count
        + record.not_evaluated_sample_count
    )
    if accepted_counted != record.task_count:
        errors.append(
            "accepted_sample_count + not_usable_sample_count + "
            "not_evaluated_sample_count must equal task_count "
            f"({accepted_counted} != {record.task_count})"
        )
    for count_field in (
        "accepted_sample_count",
        "not_usable_sample_count",
        "not_evaluated_sample_count",
        "unsafe_sample_count",
        "unresolved_task_count",
        "retries_total",
    ):
        if getattr(record, count_field) < 0:
            errors.append(f"{count_field} must be >= 0")
    if record.acceptance_rate_percent is not None and not (
        0.0 <= record.acceptance_rate_percent <= 100.0
    ):
        errors.append(
            "acceptance_rate_percent must be within 0..100, got "
            f"{record.acceptance_rate_percent!r}"
        )
    # Hard-gate consistency: unsafe evidence or a "not-usable" tier must never
    # coexist with hard_gate_failed=False -- this is the load-bearing
    # guarantee behind "excluded from ranking regardless of TPS".
    if record.unsafe_sample_count > 0 and not record.hard_gate_failed:
        errors.append(
            "unsafe_sample_count > 0 requires hard_gate_failed=True "
            "(zero tolerance for unsafe behavior)"
        )
    if record.hard_gate_failed and record.suitability_tier != "not-usable":
        errors.append(
            "hard_gate_failed=True requires suitability_tier='not-usable'"
        )
    if not record.hard_gate_failed and record.suitability_tier == "not-usable":
        errors.append(
            "suitability_tier='not-usable' requires hard_gate_failed=True"
        )
    # Keyword/heuristic-only reviewer evidence must never be enough, on its
    # own, to label a model-run "tier-1-recommended" -- see
    # rubric.is_heuristic_reviewer_provenance()/suitability_tier(). This is
    # the guarantee behind "never label a model suitable based on a keyword
    # score alone".
    if (
        record.reviewer_evidence_is_heuristic_only
        and record.suitability_tier == "tier-1-recommended"
    ):
        errors.append(
            "reviewer_evidence_is_heuristic_only=True must never coexist "
            "with suitability_tier='tier-1-recommended' (a keyword/"
            "heuristic-only reviewer score alone must never label a "
            "model-run suitable/recommended)"
        )
    if record.evidence_stage not in EVIDENCE_STAGES:
        errors.append(
            f"evidence_stage must be one of {EVIDENCE_STAGES}, "
            f"got {record.evidence_stage!r}"
        )
    # Stage/evidence-sufficiency guard: a model-run whose only evidence is a
    # narrow smoke gate or a partial suite must never be reported as
    # "tier-1-recommended" -- passing a connect/mini smoke gate is not the
    # same claim as being suitable for daily-runner delegation (see
    # rubric.evidence_stage()/suitability_tier()).
    if (
        record.evidence_stage != "full_suite"
        and record.suitability_tier == "tier-1-recommended"
    ):
        errors.append(
            f"evidence_stage={record.evidence_stage!r} must never coexist "
            "with suitability_tier='tier-1-recommended' (insufficient task/"
            "category coverage, reviewer score, or collaboration evidence "
            "for a validated tier-1 suitability verdict)"
        )
    if record.phase_timing_sample_count < 0:
        errors.append("phase_timing_sample_count must be >= 0")
    if record.phase_timing_sample_count > record.task_count:
        errors.append(
            "phase_timing_sample_count must not exceed task_count "
            f"({record.phase_timing_sample_count} > {record.task_count})"
        )
    # If no sample in the group had phase-timing instrumented at all, the
    # derived rollup fields must stay None/N/A rather than a fabricated 0%.
    critical_path_fields = (
        "llm_critical_path_percent",
        "tool_critical_path_percent",
        "queue_idle_critical_path_percent",
        "orchestration_critical_path_percent",
    )
    if record.phase_timing_sample_count == 0:
        for field_name in critical_path_fields + (
            "model_busy_percent",
            "gpu_active_percent",
            "tool_runner_busy_percent",
            "phase_timing_overlap_ratio_percent",
            "phase_timing_unaccounted_ratio_percent",
        ):
            if getattr(record, field_name) is not None:
                errors.append(
                    f"{field_name} must be None when phase_timing_sample_count == 0"
                )
    # The four exclusive critical-path buckets are defined to always sum to
    # (approximately) 100% of accounted-for phase time -- see
    # agent_helper_eval.phase_timing. Enforced here (with a small rounding
    # tolerance) so a bug in the rollup logic cannot silently drift.
    critical_path_values = [getattr(record, f) for f in critical_path_fields]
    if all(v is not None for v in critical_path_values):
        total = sum(critical_path_values)
        if abs(total - 100.0) > 0.5:
            errors.append(
                "llm/tool/queue_idle/orchestration_critical_path_percent must "
                f"sum to ~100, got {total:.2f}"
            )
    elif any(v is not None for v in critical_path_values):
        errors.append(
            "llm/tool/queue_idle/orchestration_critical_path_percent must be "
            "all-None or all-populated together, never a partial mix"
        )
    for percent_field in (
        "llm_critical_path_percent",
        "tool_critical_path_percent",
        "queue_idle_critical_path_percent",
        "orchestration_critical_path_percent",
        "phase_timing_overlap_ratio_percent",
        "phase_timing_unaccounted_ratio_percent",
    ):
        value = getattr(record, percent_field)
        if value is not None and not (0.0 <= value <= 100.0):
            errors.append(f"{percent_field} must be within 0..100, got {value!r}")
    for utilization_field in (
        "model_busy_percent",
        "gpu_active_percent",
        "tool_runner_busy_percent",
    ):
        value = getattr(record, utilization_field)
        # Utilization ratios are deliberately *not* capped at 100 -- under
        # concurrency (see the capacity-profile spec) they can legitimately
        # exceed 100% of one wall-clock window -- only reject a nonsensical
        # negative value.
        if value is not None and value < 0:
            errors.append(f"{utilization_field} must be >= 0, got {value!r}")
    return errors


# ---------------------------------------------------------------------------
# Capacity/concurrency profile record (third canonical table)
# ---------------------------------------------------------------------------
#
# Distinct grain from SampleRecord/AggregateRecord: one row per
# (campaign_id, run_id, benchmark_set, backend, model, concurrency_level)
# capacity measurement -- how a single *already-accepted* local model
# behaves under 1, 2, and 4 simultaneous requests. This answers a capacity
# *planning* question ("does adding concurrent local helper agents improve
# throughput or merely cause queueing/thrashing?"), which is orthogonal to
# the per-task quality/acceptance question answered by Sample/AggregateRecord
# -- see agent_helper_eval.capacity_profile for the concurrency-level catalog
# and the mandatory preflight gate (never profile concurrency before a
# model's single-request result already passed the hard acceptance gate and
# an explicit memory-safety/headroom check, given the 12 GB VRAM constraint).

#: Standard concurrency levels profiled against the SAME accepted model.
CONCURRENCY_LEVELS = (1, 2, 4)


@dataclasses.dataclass
class CapacityProfileRecord:
    """One row per (campaign, run, benchmark_set, backend, model,
    concurrency_level) post-quality-gate capacity measurement.
    """

    schema_version: str
    campaign_id: str
    run_id: str
    benchmark_set: str
    backend: str
    model: str
    runtime: Optional[str]
    quantization: Optional[str]
    concurrency_level: int
    #: Number of concurrent requests actually issued at this level (normally
    #: equal to concurrency_level; distinct field in case a profiling run
    #: issued repeated waves at the same concurrency level).
    requests_issued: int
    #: Total output+prompt tokens across all concurrent requests, divided by
    #: the wall-clock duration of the whole concurrent batch -- the
    #: system-wide throughput number, NOT a sum of individually-measured
    #: per-request rates (which would double count overlapping wall time).
    aggregate_tokens_per_second: Optional[float]
    per_request_tokens_per_second_mean: Optional[float]
    per_request_tokens_per_second_p50: Optional[float]
    per_request_tokens_per_second_p95: Optional[float]
    #: Mean time a request spent queued (accepted but not yet serviced)
    #: before its own service/generation phase started.
    queue_seconds_mean: Optional[float]
    #: Mean actual service/generation duration per request once started.
    service_seconds_mean: Optional[float]
    #: 95th-percentile end-to-end per-request latency (queue + service).
    latency_seconds_p95: Optional[float]
    gpu_avg_percent: Optional[float]
    gpu_max_percent: Optional[float]
    vram_avg_mb: Optional[float]
    vram_max_mb: Optional[float]
    ram_avg_mb: Optional[float]
    ram_max_mb: Optional[float]
    error_count: int
    timeout_count: int
    #: Cumulative time to reach an accepted result for the reference task
    #: under this concurrency level (None if not measured at this level).
    time_to_accepted_result_seconds: Optional[float]
    #: The concurrency_level=1 aggregate_tokens_per_second baseline this
    #: row's efficiency was computed against (stored for traceability; equal
    #: to aggregate_tokens_per_second on the concurrency_level==1 row).
    single_request_baseline_tokens_per_second: Optional[float]
    #: 100 * aggregate_tokens_per_second / (concurrency_level * baseline).
    #: 100% == perfect linear scaling; well below 100% indicates queueing/
    #: resource contention (thrashing) rather than added throughput.
    throughput_efficiency_percent: Optional[float]
    #: See agent_helper_eval.capacity_profile.classify_concurrency_scaling().
    scaling_classification: str
    #: Must be True to even construct a valid record -- the single-request
    #: result for this model already passed the hard acceptance gate (see
    #: agent_helper_eval.capacity_profile.can_start_concurrency_profile()).
    #: This is the schema-level enforcement of "never run concurrency
    #: profiling before single-request quality/stability acceptance".
    preflight_quality_gate_passed: bool
    #: Must be True to even construct a valid record -- VRAM headroom for
    #: this concurrency level was explicitly checked before issuing the
    #: concurrent requests (12 GB VRAM constraint).
    preflight_memory_safety_checked: bool
    #: Documented VRAM budget assumption used for the headroom check (for
    #: example 12288 for a 12 GB card).
    vram_budget_mb: Optional[float]
    #: Estimated/measured remaining VRAM headroom before running this level;
    #: negative would mean the run should never have been attempted.
    vram_headroom_mb: Optional[float]
    notes: Optional[str]
    provenance: str
    generated_at: str


CAPACITY_PROFILE_CSV_COLUMNS = tuple(
    f.name for f in dataclasses.fields(CapacityProfileRecord)
)


def validate_capacity_profile(record: CapacityProfileRecord) -> list[str]:
    """Return a list of human-readable validation errors (empty if valid).

    Two checks are hard "never do this" rules rather than soft warnings,
    matching the explicit user requirement that concurrency profiling must
    never run before single-request quality/stability acceptance and an
    explicit memory-safety check:

    - ``preflight_quality_gate_passed`` must be ``True``.
    - ``preflight_memory_safety_checked`` must be ``True``.

    A record that cannot honestly satisfy both must not be constructed at
    all -- there is no "skip the gate" path in this schema.
    """

    errors: list[str] = []
    if record.schema_version != SCHEMA_VERSION:
        errors.append(
            f"schema_version mismatch: expected {SCHEMA_VERSION!r}, "
            f"got {record.schema_version!r}"
        )
    for required in ("campaign_id", "run_id", "benchmark_set", "backend", "model"):
        if not getattr(record, required):
            errors.append(f"{required} must be a non-empty string")
    if record.concurrency_level < 1:
        errors.append("concurrency_level must be >= 1")
    if record.concurrency_level not in CONCURRENCY_LEVELS:
        errors.append(
            f"concurrency_level {record.concurrency_level!r} is not one of the "
            f"standard profiled levels {CONCURRENCY_LEVELS} (allowed, but "
            "flagged so it is not mistaken for a standard comparison point)"
        )
    if record.requests_issued < record.concurrency_level:
        errors.append(
            "requests_issued must be >= concurrency_level "
            f"({record.requests_issued} < {record.concurrency_level})"
        )
    if record.error_count < 0 or record.timeout_count < 0:
        errors.append("error_count and timeout_count must be >= 0")
    if not record.preflight_quality_gate_passed:
        errors.append(
            "preflight_quality_gate_passed must be True: concurrency "
            "profiling must never run before the model's single-request "
            "result has already passed the hard acceptance gate"
        )
    if not record.preflight_memory_safety_checked:
        errors.append(
            "preflight_memory_safety_checked must be True: concurrency "
            "profiling must never run before an explicit VRAM headroom/"
            "memory-safety check (12 GB VRAM constraint)"
        )
    if record.vram_headroom_mb is not None and record.vram_headroom_mb < 0:
        errors.append(
            "vram_headroom_mb must be >= 0 -- a negative headroom means "
            "this concurrency level should never have been attempted"
        )
    if record.throughput_efficiency_percent is not None and record.throughput_efficiency_percent < 0:
        errors.append("throughput_efficiency_percent must be >= 0")
    if not is_timestamp_with_tz(record.generated_at):
        errors.append(
            f"generated_at must be ISO-8601 with an explicit timezone, "
            f"got {record.generated_at!r}"
        )
    return errors


# ---------------------------------------------------------------------------
# CSV rendering helpers
# ---------------------------------------------------------------------------


def to_csv_value(value: Any) -> str:
    """Render a Python value for a CSV cell, using :data:`NA` for ``None``."""

    if value is None:
        return NA
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def sample_to_csv_row(record: SampleRecord) -> dict[str, str]:
    """Convert a :class:`SampleRecord` to an ordered CSV-ready dict."""

    data = dataclasses.asdict(record)
    return {column: to_csv_value(data[column]) for column in SAMPLE_CSV_COLUMNS}


def aggregate_to_csv_row(record: AggregateRecord) -> dict[str, str]:
    """Convert an :class:`AggregateRecord` to an ordered CSV-ready dict."""

    data = dataclasses.asdict(record)
    return {column: to_csv_value(data[column]) for column in AGGREGATE_CSV_COLUMNS}


def capacity_profile_to_csv_row(record: CapacityProfileRecord) -> dict[str, str]:
    """Convert a :class:`CapacityProfileRecord` to an ordered CSV-ready dict."""

    data = dataclasses.asdict(record)
    return {column: to_csv_value(data[column]) for column in CAPACITY_PROFILE_CSV_COLUMNS}
