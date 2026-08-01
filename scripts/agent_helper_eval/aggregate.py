"""Aggregation from sample/task-attempt rows to model-run aggregate rows.

Grouping key for one aggregate row: ``(campaign_id, run_id, benchmark_set,
track, provider, backend, model)``. ``runtime``/``quantization`` are taken
from the group's samples (first non-null value); an inconsistency within a
group is recorded in the aggregate's ``notes`` field rather than silently
dropped.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Optional, Sequence

from . import rubric
from . import phase_timing
from .schema import (
    AggregateRecord,
    SampleRecord,
    SCHEMA_VERSION,
    utc_now_iso,
)


def percentile(values: Sequence[float], p: float) -> float:
    """Deterministic linear-interpolation percentile (matches numpy's default).

    ``p`` is in the 0..100 range. Raises ``ValueError`` for an empty sequence.
    """

    if not values:
        raise ValueError("percentile() requires at least one value")
    if not 0 <= p <= 100:
        raise ValueError(f"p must be within 0..100, got {p!r}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    lower_value = ordered[lower] * (upper - rank)
    upper_value = ordered[upper] * (rank - lower)
    return lower_value + upper_value


def _mean(values: Sequence[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _sum_or_none(values: Sequence[Optional[float]]) -> Optional[float]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


def _first_non_null(values: Iterable[Optional[str]]) -> Optional[str]:
    for value in values:
        if value:
            return value
    return None


def _acceptance_counts(group: Sequence[SampleRecord]) -> tuple[int, int, int, int]:
    """Return (accepted, not_usable, not_evaluated, unsafe) counts for a group."""

    accepted = sum(1 for s in group if s.acceptance_status == "accepted")
    not_usable = sum(1 for s in group if s.acceptance_status == "not_usable")
    not_evaluated = sum(1 for s in group if s.acceptance_status == "not_evaluated")
    unsafe = sum(1 for s in group if s.unsafe_behavior_flag is True)
    return accepted, not_usable, not_evaluated, unsafe


def _accepted_elapsed_mean(group: Sequence[SampleRecord]) -> Optional[float]:
    """Mean elapsed_seconds over only the *accepted* samples in a group.

    Performance is only ever a tie-breaker/optimization among accepted,
    usable outputs (per the hard acceptance gate policy) -- so the speed
    score feeding the composite/overall score must never be computed from
    attempts that failed the gate, no matter how fast they were.
    """

    values = [
        s.elapsed_seconds
        for s in group
        if s.acceptance_status == "accepted" and s.elapsed_seconds is not None
    ]
    return _mean(values)


def _group_hard_gate(group: Sequence[SampleRecord]) -> tuple[bool, Optional[str]]:
    accepted, not_usable, _not_evaluated, unsafe = _acceptance_counts(group)
    return rubric.compute_aggregate_hard_gate(accepted, not_usable, unsafe, len(group))


def _heuristic_reviewer_only(group: Sequence[SampleRecord]) -> bool:
    """True if this group's quality signal rests *entirely* on a legacy/
    heuristic keyword-matching reviewer score (see
    ``rubric.is_heuristic_reviewer_provenance``), with no deterministic
    test-oracle evidence anywhere in the group.

    This is the direct guard against labeling a model-run "suitable"/
    "tier-1-recommended" merely because an imported legacy row (or any
    other keyword/heuristic-scored sample) happened to score high --
    ``rubric.suitability_tier()`` caps the tier when this is True, and
    ``_recommendation()`` adds an explicit caveat.
    """

    if any(s.deterministic_score is not None for s in group):
        return False
    reviewer_samples = [s for s in group if s.reviewer_score is not None]
    if not reviewer_samples:
        return False
    return all(
        rubric.is_heuristic_reviewer_provenance(s.reviewer_provenance)
        for s in reviewer_samples
    )


def _evidence_stage_inputs(group: Sequence[SampleRecord]) -> tuple[int, bool, bool]:
    """Compute the three per-group evidence signals consumed by
    :func:`rubric.evidence_stage`:

    - the number of *distinct* ``task_category`` values evaluated anywhere
      in the group (see ``catalog.TaskSpec.category``);
    - whether *any* sample in the group carries a (non-None) reviewer score
      at all -- this is orthogonal to ``_heuristic_reviewer_only`` above
      (that guard is about the *quality* of reviewer evidence when present;
      this is about whether reviewer evidence exists at all);
    - whether *any* sample in the group carries a (non-None) collaboration
      score at all.

    A connect-gate or mini-gate campaign (one task category, no reviewer/
    collaboration scoring performed) always yields
    ``(1, False, False)`` here, which ``rubric.evidence_stage()`` classifies
    as ``EVIDENCE_STAGE_GATE_ONLY`` -- the fix for pilot-review finding #1.
    """

    distinct_categories = len({s.task_category for s in group if s.task_category})
    has_reviewer_evidence = any(s.reviewer_score is not None for s in group)
    has_collaboration_evidence = any(s.collaboration_score is not None for s in group)
    return distinct_categories, has_reviewer_evidence, has_collaboration_evidence



def _group_key(sample: SampleRecord) -> tuple:
    return (
        sample.campaign_id,
        sample.run_id,
        sample.benchmark_set,
        sample.track,
        sample.provider,
        sample.backend,
        sample.model,
    )


def group_samples(
    samples: Iterable[SampleRecord],
) -> dict[tuple, list[SampleRecord]]:
    """Group sample rows by ``(campaign_id, run_id, benchmark_set, track,
    provider, backend, model)``, preserving encounter order within each group.
    """

    grouped: dict[tuple, list[SampleRecord]] = defaultdict(list)
    for sample in samples:
        grouped[_group_key(sample)].append(sample)
    return dict(grouped)


def build_aggregate_for_group(
    group: Sequence[SampleRecord],
    peer_elapsed_seconds: Sequence[float] = (),
    weights: Optional[dict[str, float]] = None,
    provenance: str = "measured",
) -> AggregateRecord:
    """Build one :class:`AggregateRecord` from a single group of samples.

    ``peer_elapsed_seconds`` should contain the mean elapsed seconds of every
    competing group in the same ``(benchmark_set, track)`` scope, used to
    compute a bounded, relative speed score (see
    :func:`agent_helper_eval.rubric.normalize_speed_score`). Pass an empty
    sequence to skip relative speed scoring (the aggregate will simply omit
    a speed contribution to the composite score).
    """

    if not group:
        raise ValueError("build_aggregate_for_group() requires at least one sample")

    first = group[0]
    task_count = len(group)
    success_count = sum(1 for s in group if s.status == "success")
    error_count = sum(1 for s in group if s.status == "error")
    skipped_count = sum(1 for s in group if s.status == "skipped")
    # "timeout" is a distinct status but counts toward errors for rate purposes.
    error_count += sum(1 for s in group if s.status == "timeout")

    elapsed_values = [s.elapsed_seconds for s in group if s.elapsed_seconds is not None]
    tps_values = [s.tokens_per_second for s in group if s.tokens_per_second is not None]
    ttft_values = [s.ttft_seconds for s in group if s.ttft_seconds is not None]
    cpu_avg_values = [s.cpu_avg_percent for s in group if s.cpu_avg_percent is not None]
    cpu_max_values = [s.cpu_max_percent for s in group if s.cpu_max_percent is not None]
    ram_avg_values = [s.ram_avg_mb for s in group if s.ram_avg_mb is not None]
    ram_max_values = [s.ram_max_mb for s in group if s.ram_max_mb is not None]
    gpu_avg_values = [s.gpu_avg_percent for s in group if s.gpu_avg_percent is not None]
    gpu_max_values = [s.gpu_max_percent for s in group if s.gpu_max_percent is not None]
    vram_avg_values = [s.vram_avg_mb for s in group if s.vram_avg_mb is not None]
    vram_max_values = [s.vram_max_mb for s in group if s.vram_max_mb is not None]
    deterministic_values = [
        s.deterministic_score for s in group if s.deterministic_score is not None
    ]
    reviewer_values = [s.reviewer_score for s in group if s.reviewer_score is not None]
    collaboration_values = [
        s.collaboration_score for s in group if s.collaboration_score is not None
    ]

    elapsed_seconds_mean = _mean(elapsed_values)
    accepted_elapsed_mean = _accepted_elapsed_mean(group)
    speed_score = rubric.normalize_speed_score(accepted_elapsed_mean, peer_elapsed_seconds)
    deterministic_mean = _mean(deterministic_values)
    reviewer_mean = _mean(reviewer_values)
    collaboration_mean = _mean(collaboration_values)
    overall_score = rubric.compute_composite_score(
        deterministic_mean, reviewer_mean, collaboration_mean, speed_score, weights
    )
    success_rate = round(100.0 * success_count / task_count, 2)
    error_rate = round(100.0 * error_count / task_count, 2)

    accepted_count, not_usable_count, not_evaluated_count, unsafe_count = _acceptance_counts(
        group
    )
    acceptance_rate_percent = round(100.0 * accepted_count / task_count, 2)
    hard_gate_failed, hard_gate_reasons = rubric.compute_aggregate_hard_gate(
        accepted_count, not_usable_count, unsafe_count, task_count
    )
    heuristic_reviewer_only = _heuristic_reviewer_only(group)
    distinct_categories, has_reviewer_evidence, has_collaboration_evidence = (
        _evidence_stage_inputs(group)
    )
    evidence_stage_value = rubric.evidence_stage(
        distinct_categories, has_reviewer_evidence, has_collaboration_evidence
    )
    phase_rollup = phase_timing.rollup_group(group)

    # Per-task collaboration/rework analysis: how long (and how many tokens)
    # it took each distinct task to reach an accepted result, and how many
    # distinct tasks were attempted but *never* accepted at all -- a signal
    # that is independent of (and must not be masked by) raw attempt speed.
    tasks_by_id: dict[str, list[SampleRecord]] = defaultdict(list)
    for sample in group:
        tasks_by_id[sample.task_id].append(sample)
    unresolved_task_count = 0
    time_to_accept_values: list[float] = []
    rework_tokens_values: list[float] = []
    for task_samples in tasks_by_id.values():
        ordered = sorted(task_samples, key=lambda s: s.iteration_index)
        attempts = [
            rubric.AttemptOutcome(
                elapsed_seconds=s.elapsed_seconds or 0.0,
                total_tokens=s.total_tokens,
                accepted=(s.acceptance_status == "accepted"),
            )
            for s in ordered
        ]
        summary = rubric.time_to_accepted_result(attempts)
        if summary["accepted"]:
            time_to_accept_values.append(summary["time_to_accept_seconds"])
            if summary["total_tokens"] is not None:
                rework_tokens_values.append(float(summary["total_tokens"]))
        elif any(s.acceptance_status == "not_usable" for s in task_samples):
            # Only count as "unresolved" when there is direct evidence the
            # task was actually attempted and failed the gate -- a task with
            # no evaluation evidence at all ("not_evaluated" only) is missing
            # data, not a repeated-failure signal, and must not be conflated
            # with the latter.
            unresolved_task_count += 1
    time_to_accepted_result_seconds_mean = _mean(time_to_accept_values)
    rework_tokens_to_accept_mean = _mean(rework_tokens_values)

    inconsistent_runtime = len({s.runtime for s in group if s.runtime}) > 1
    inconsistent_quant = len({s.quantization for s in group if s.quantization}) > 1
    notes_parts: list[str] = []
    if inconsistent_runtime:
        notes_parts.append("runtime differed across samples in this group")
    if inconsistent_quant:
        notes_parts.append("quantization differed across samples in this group")

    model_load_values = [s.model_load_seconds for s in group if s.model_load_seconds is not None]
    orchestrator_cpu_values = [
        s.orchestrator_cpu_time_seconds for s in group if s.orchestrator_cpu_time_seconds is not None
    ]
    model_cpu_values = [s.model_cpu_time_seconds for s in group if s.model_cpu_time_seconds is not None]

    return AggregateRecord(
        schema_version=SCHEMA_VERSION,
        campaign_id=first.campaign_id,
        run_id=first.run_id,
        benchmark_set=first.benchmark_set,
        benchmark_version=first.benchmark_version,
        track=first.track,
        provider=first.provider,
        backend=first.backend,
        model=first.model,
        runtime=_first_non_null(s.runtime for s in group),
        quantization=_first_non_null(s.quantization for s in group),
        task_count=task_count,
        success_count=success_count,
        error_count=error_count,
        skipped_count=skipped_count,
        success_rate_percent=success_rate,
        error_rate_percent=error_rate,
        elapsed_seconds_total=_sum_or_none(elapsed_values),
        elapsed_seconds_mean=elapsed_seconds_mean,
        elapsed_seconds_p50=percentile(elapsed_values, 50) if elapsed_values else None,
        elapsed_seconds_p95=percentile(elapsed_values, 95) if elapsed_values else None,
        prompt_tokens_total=_int_sum_or_none(s.prompt_tokens for s in group),
        output_tokens_total=_int_sum_or_none(s.output_tokens for s in group),
        total_tokens_total=_int_sum_or_none(s.total_tokens for s in group),
        tokens_per_second_mean=_mean(tps_values),
        ttft_seconds_mean=_mean(ttft_values),
        cpu_avg_percent_mean=_mean(cpu_avg_values),
        cpu_max_percent_max=max(cpu_max_values) if cpu_max_values else None,
        ram_avg_mb_mean=_mean(ram_avg_values),
        ram_max_mb_max=max(ram_max_values) if ram_max_values else None,
        gpu_avg_percent_mean=_mean(gpu_avg_values),
        gpu_max_percent_max=max(gpu_max_values) if gpu_max_values else None,
        vram_avg_mb_mean=_mean(vram_avg_values),
        vram_max_mb_max=max(vram_max_values) if vram_max_values else None,
        deterministic_score_mean=deterministic_mean,
        reviewer_score_mean=reviewer_mean,
        collaboration_score_mean=collaboration_mean,
        overall_score=overall_score,
        retries_total=sum(s.retry_count for s in group),
        iterations_mean=_mean([float(s.iteration_index) for s in group]),
        campaign_elapsed_seconds_total=_sum_or_none(elapsed_values),
        suitability_tier=rubric.suitability_tier(
            overall_score,
            success_rate,
            task_count,
            hard_gate_failed=hard_gate_failed,
            reviewer_evidence_is_heuristic_only=heuristic_reviewer_only,
            evidence_stage=evidence_stage_value,
        ),
        recommendation=_recommendation(
            first.backend,
            first.model,
            overall_score,
            success_rate,
            hard_gate_failed=hard_gate_failed,
            hard_gate_reasons=hard_gate_reasons,
            heuristic_reviewer_only=heuristic_reviewer_only,
            evidence_stage=evidence_stage_value,
        ),
        notes="; ".join(notes_parts) if notes_parts else None,
        provenance=provenance,
        generated_at=utc_now_iso(),
        accepted_sample_count=accepted_count,
        not_usable_sample_count=not_usable_count,
        not_evaluated_sample_count=not_evaluated_count,
        acceptance_rate_percent=acceptance_rate_percent,
        unsafe_sample_count=unsafe_count,
        unresolved_task_count=unresolved_task_count,
        time_to_accepted_result_seconds_mean=time_to_accepted_result_seconds_mean,
        rework_tokens_to_accept_mean=rework_tokens_to_accept_mean,
        hard_gate_failed=hard_gate_failed,
        hard_gate_reasons=hard_gate_reasons,
        reviewer_evidence_is_heuristic_only=heuristic_reviewer_only,
        phase_timing_sample_count=phase_rollup.phase_timing_sample_count,
        llm_critical_path_percent=phase_rollup.llm_critical_path_percent,
        tool_critical_path_percent=phase_rollup.tool_critical_path_percent,
        queue_idle_critical_path_percent=phase_rollup.queue_idle_critical_path_percent,
        orchestration_critical_path_percent=phase_rollup.orchestration_critical_path_percent,
        phase_timing_overlap_ratio_percent=phase_rollup.phase_timing_overlap_ratio_percent,
        phase_timing_unaccounted_ratio_percent=phase_rollup.phase_timing_unaccounted_ratio_percent,
        model_busy_percent=phase_rollup.model_busy_percent,
        gpu_active_percent=phase_rollup.gpu_active_percent,
        tool_runner_busy_percent=phase_rollup.tool_runner_busy_percent,
        evidence_stage=evidence_stage_value,
        model_load_seconds_mean=_mean(model_load_values),
        orchestrator_cpu_time_seconds_mean=_mean(orchestrator_cpu_values),
        model_cpu_time_seconds_mean=_mean(model_cpu_values),
    )


def _int_sum_or_none(values: Iterable[Optional[int]]) -> Optional[int]:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


def _recommendation(
    backend: str,
    model: str,
    overall_score: Optional[float],
    success_rate: Optional[float],
    hard_gate_failed: bool = False,
    hard_gate_reasons: Optional[str] = None,
    heuristic_reviewer_only: bool = False,
    evidence_stage: str = "full_suite",
) -> str:
    if hard_gate_failed:
        reason_suffix = f" Reason(s): {hard_gate_reasons}." if hard_gate_reasons else ""
        return (
            f"{backend}/{model} is NOT USABLE and is excluded from ranking "
            "regardless of speed/TPS: it failed the hard acceptance gate. "
            "Speed is only ever a tie-breaker/optimization among accepted, "
            f"usable candidates -- it cannot compensate for unusable quality.{reason_suffix}"
        )
    if overall_score is None or success_rate is None:
        return f"Insufficient data for {backend}/{model}; run more samples before deciding."
    if evidence_stage == "gate_only":
        return (
            f"{backend}/{model} PASSED a narrow connect/mini smoke gate only "
            "(a single task category, no reviewer or collaboration evidence "
            "collected yet) -- this confirms the model is reachable and can "
            "produce a correct tiny result, but it is NOT yet a validated "
            "suitability recommendation. Do not treat this as tier-1/"
            "daily-runner-ready until broader task-category coverage, a "
            "genuine reviewer score, and collaboration evidence are collected."
        )
    heuristic_caveat = (
        " Caveat: this reflects a legacy/heuristic keyword-matching "
        "reviewer score only -- no deterministic test oracle or genuine "
        "human/orchestrator review ever ran on this model-run. Treat this "
        "as provisional evidence, NOT a validated suitability recommendation."
        if heuristic_reviewer_only
        else ""
    )
    partial_suite_caveat = (
        " Caveat: evidence base is a partial suite (missing full task-"
        "category coverage, a reviewer score, or collaboration evidence "
        "somewhere in this group) -- capped below 'tier-1-recommended' "
        "until full-suite evidence is collected."
        if evidence_stage == "partial_suite"
        else ""
    )
    if overall_score >= 80.0 and success_rate >= 50.0:
        if heuristic_reviewer_only:
            return (
                f"{backend}/{model} shows a high heuristic score, but this "
                f"is NOT a validated 'suitable' recommendation.{heuristic_caveat}"
            )
        if evidence_stage == "partial_suite":
            return (
                f"{backend}/{model} shows a high score on a partial evidence "
                "suite, but this is NOT yet a validated 'tier-1-recommended' "
                f"suitability verdict.{partial_suite_caveat}"
            )
        return f"{backend}/{model} is a strong candidate for delegated helper tasks in this set."
    if overall_score >= 60.0:
        return f"{backend}/{model} is conditionally usable; review failing samples before relying on it.{heuristic_caveat}{partial_suite_caveat}"
    return f"{backend}/{model} is not recommended for this benchmark set without further investigation.{heuristic_caveat}"


def build_all_aggregates(
    samples: Iterable[SampleRecord],
    weights: Optional[dict[str, float]] = None,
    provenance: str = "measured",
) -> list[AggregateRecord]:
    """Build aggregate rows for every group found in ``samples``.

    Speed scoring is relative within each ``(campaign_id, run_id,
    benchmark_set, track)`` scope so that, for example, a pure-model and a
    tool-agent result for the same benchmark set are not compared on raw
    speed against each other under different semantics.

    Per the hard acceptance gate policy, the peer pool used for speed
    scoring is restricted twice over: (1) groups that themselves fail the
    hard gate are excluded from the pool entirely (their bad results must
    not set the bar other candidates are compared against), and (2) only
    each remaining group's *accepted*-sample elapsed times count -- failed
    attempts, however fast, never contribute to a speed comparison. This is
    a two-pass algorithm: pass 1 determines each group's hard-gate status
    (which needs no peer data), pass 2 builds the restricted peer scope from
    pass 1's results, and only then are the final aggregate records built.
    """

    grouped = group_samples(samples)

    # Pass 1: hard-gate status + accepted-only elapsed mean per group. Both
    # are computed from a single group's own samples, so no peer data is
    # needed yet.
    hard_gate_by_key: dict[tuple, bool] = {}
    accepted_elapsed_mean_by_key: dict[tuple, Optional[float]] = {}
    for key, group in grouped.items():
        hard_gate_failed, _reasons = _group_hard_gate(group)
        hard_gate_by_key[key] = hard_gate_failed
        accepted_elapsed_mean_by_key[key] = _accepted_elapsed_mean(group)

    # Pass 2: build the restricted peer scope for relative speed scoring.
    scope_elapsed: dict[tuple, list[float]] = defaultdict(list)
    for key in grouped:
        if hard_gate_by_key[key]:
            continue
        mean_elapsed = accepted_elapsed_mean_by_key[key]
        if mean_elapsed is not None:
            campaign_id, run_id, benchmark_set, track = key[0], key[1], key[2], key[3]
            scope_elapsed[(campaign_id, run_id, benchmark_set, track)].append(mean_elapsed)

    aggregates: list[AggregateRecord] = []
    for key, group in grouped.items():
        campaign_id, run_id, benchmark_set, track = key[0], key[1], key[2], key[3]
        scope_key = (campaign_id, run_id, benchmark_set, track)
        # A hard-gate-failed group gets no peer pool at all: it must not be
        # speed-scored (excluded candidates are never ranked on performance),
        # and it must not appear as a peer for other groups either (already
        # enforced by pass 2 skipping it above).
        peer_values = [] if hard_gate_by_key[key] else list(scope_elapsed.get(scope_key, []))
        aggregates.append(
            build_aggregate_for_group(
                group, peer_elapsed_seconds=peer_values, weights=weights, provenance=provenance
            )
        )
    return aggregates
