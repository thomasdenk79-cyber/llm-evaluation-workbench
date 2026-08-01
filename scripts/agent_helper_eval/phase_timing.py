"""Phase attribution: turn raw per-attempt phase seconds into an honest,
non-double-counting time breakdown for capacity planning.

Two deliberately distinct kinds of numbers are produced from the same raw
inputs (:class:`PhaseSeconds`, mirroring the ``*_seconds`` fields on
``agent_helper_eval.schema.SampleRecord``):

1. **Exclusive critical-path percentages** (:func:`compute_exclusive_critical_path`):
   four buckets -- LLM, local tools/tests, queue/idle, orchestration -- that
   are defined to always sum to (approximately) 100% of the attempt's
   *accounted-for* phase time. These answer "where did the time go, as a
   single pie that sums to 100%".
2. **Non-exclusive utilization/busy ratios**
   (:func:`compute_utilization_ratios`): model-busy%, GPU-active%,
   tool-runner-busy% -- each independently a percent of wall-clock time, NOT
   required (or expected) to sum to anything in particular. Under
   concurrency (see :mod:`agent_helper_eval.capacity_profile`) these can
   legitimately exceed 100% of one wall-clock window, because multiple
   simultaneously-busy requests overlap in time.

Overlap semantics are made explicit rather than implicit: raw phase seconds
reported by different instruments can genuinely overlap (for example
deterministic-test execution running concurrently with an orchestrator
review), so naively summing every phase and calling each fraction a percent
of wall-clock time would silently double count. This module instead:

- normalizes the four exclusive buckets against their own naive sum (the
  "accounted-for time" pie), which by construction always sums to 100%
  regardless of any overlap or gap versus the true wall-clock duration, and
- reports the mismatch between that naive sum and the true wall-clock
  duration as two *separate*, clearly-labelled diagnostics --
  ``overlap_ratio_percent`` (accounted time exceeded wall time: phases
  overlapped/were concurrent) and ``unaccounted_ratio_percent`` (wall time
  exceeded accounted time: some wall-clock time was not attributed to any
  instrumented phase at all) -- neither of which is folded silently into the
  100% split.

Every field this module reads is optional. Where a runtime cannot observe a
phase (most notably GitHub Copilot task-agent references, which typically
cannot expose any internal LLM-side phase timing at all), the corresponding
input is simply absent and the output degrades to ``None``/N/A rather than
an invented number.
"""

from __future__ import annotations

import dataclasses
from typing import Iterable, Optional, Sequence

#: The four *exclusive*, non-overlapping critical-path buckets. Always sum
#: to ~100% of accounted-for phase time -- see module docstring.
CRITICAL_PATH_BUCKETS = ("llm", "tool", "queue_idle", "orchestration")

#: Backends the harness has direct GPU visibility into (a local model
#: process it can query with e.g. nvidia-smi/psutil-style sampling). Used
#: only for the documented gpu_active_percent approximation below -- never
#: to fabricate a number for an opaque remote/agent backend.
_LOCAL_GPU_VISIBLE_BACKENDS = frozenset({"ollama", "llama_cpp"})


@dataclasses.dataclass
class PhaseSeconds:
    """Raw, optionally-observed phase timings for one sample/attempt.

    Field names mirror ``agent_helper_eval.schema.SampleRecord``'s
    ``PHASE_TIMING_SECONDS_FIELDS`` exactly, so :meth:`from_sample` is a
    simple attribute copy.
    """

    total_wall_seconds: Optional[float] = None
    llm_queue_seconds: Optional[float] = None
    llm_request_seconds: Optional[float] = None
    prompt_eval_seconds: Optional[float] = None
    generation_seconds: Optional[float] = None
    local_tool_exec_seconds: Optional[float] = None
    test_exec_seconds: Optional[float] = None
    orchestrator_review_seconds: Optional[float] = None
    idle_wait_seconds: Optional[float] = None
    overlap_seconds: Optional[float] = None

    @classmethod
    def from_sample(cls, sample: object) -> "PhaseSeconds":
        """Build from any object exposing the matching attribute names (in
        practice an ``agent_helper_eval.schema.SampleRecord``)."""

        return cls(
            total_wall_seconds=getattr(sample, "total_wall_seconds", None),
            llm_queue_seconds=getattr(sample, "llm_queue_seconds", None),
            llm_request_seconds=getattr(sample, "llm_request_seconds", None),
            prompt_eval_seconds=getattr(sample, "prompt_eval_seconds", None),
            generation_seconds=getattr(sample, "generation_seconds", None),
            local_tool_exec_seconds=getattr(sample, "local_tool_exec_seconds", None),
            test_exec_seconds=getattr(sample, "test_exec_seconds", None),
            orchestrator_review_seconds=getattr(sample, "orchestrator_review_seconds", None),
            idle_wait_seconds=getattr(sample, "idle_wait_seconds", None),
            overlap_seconds=getattr(sample, "overlap_seconds", None),
        )


def llm_bucket_seconds(phases: PhaseSeconds) -> Optional[float]:
    """The LLM-execution bucket.

    Prefers the top-level ``llm_request_seconds`` black-box duration; falls
    back to summing ``prompt_eval_seconds`` + ``generation_seconds`` when a
    runtime exposes only the finer-grained sub-phases (for example Ollama/
    llama.cpp's separate prompt-eval and eval/generation durations) but not
    a single top-level request duration. Never both are summed together
    (that would double count the same underlying LLM call).
    """

    if phases.llm_request_seconds is not None:
        return phases.llm_request_seconds
    parts = [v for v in (phases.prompt_eval_seconds, phases.generation_seconds) if v is not None]
    if not parts:
        return None
    return sum(parts)


def tool_bucket_seconds(phases: PhaseSeconds) -> Optional[float]:
    """Local tool execution + deterministic test execution time."""

    parts = [v for v in (phases.local_tool_exec_seconds, phases.test_exec_seconds) if v is not None]
    if not parts:
        return None
    return sum(parts)


def queue_idle_bucket_seconds(phases: PhaseSeconds) -> Optional[float]:
    """LLM-queue-wait + otherwise-unattributed idle time."""

    parts = [v for v in (phases.llm_queue_seconds, phases.idle_wait_seconds) if v is not None]
    if not parts:
        return None
    return sum(parts)


def orchestration_bucket_seconds(phases: PhaseSeconds) -> Optional[float]:
    """Orchestrator/reviewer scoring time."""

    return phases.orchestrator_review_seconds


def bucket_seconds(phases: PhaseSeconds) -> dict[str, Optional[float]]:
    """Return the four raw exclusive-bucket seconds (before normalization)."""

    return {
        "llm": llm_bucket_seconds(phases),
        "tool": tool_bucket_seconds(phases),
        "queue_idle": queue_idle_bucket_seconds(phases),
        "orchestration": orchestration_bucket_seconds(phases),
    }


@dataclasses.dataclass
class ExclusiveCriticalPath:
    """Result of :func:`compute_exclusive_critical_path`.

    ``llm_percent`` + ``tool_percent`` + ``queue_idle_percent`` +
    ``orchestration_percent`` always sum to ~100 when not ``None``.
    ``overlap_ratio_percent``/``unaccounted_ratio_percent`` are separate
    diagnostics, never part of that 100% split.
    """

    llm_percent: Optional[float]
    tool_percent: Optional[float]
    queue_idle_percent: Optional[float]
    orchestration_percent: Optional[float]
    #: How much of the naively-summed phase time exceeded the observed
    #: wall-clock duration (phases were concurrent/overlapping). None if
    #: not computable (no total_wall_seconds) or not applicable (no excess).
    overlap_ratio_percent: Optional[float]
    #: How much of the observed wall-clock duration was not attributed to
    #: any of the four buckets at all (an instrumentation gap). None if not
    #: computable or not applicable.
    unaccounted_ratio_percent: Optional[float]
    #: "no_data" (nothing to compute from), "phase_sum_only" (no
    #: total_wall_seconds available, so only relative shares of accounted
    #: time could be produced -- no overlap/unaccounted diagnostics), or
    #: "reconciled_with_wall_time" (total_wall_seconds was available and
    #: both diagnostics were attempted).
    basis: str


_NO_DATA = ExclusiveCriticalPath(None, None, None, None, None, None, "no_data")


def compute_exclusive_critical_path(phases: PhaseSeconds) -> ExclusiveCriticalPath:
    """Derive the four exclusive critical-path percentages for one attempt.

    See the module docstring for the exact overlap/unaccounted-time
    semantics. Returns an all-``None`` ``"no_data"`` result when no phase
    seconds were observed at all (never fabricates a breakdown).
    """

    buckets = bucket_seconds(phases)
    if not any(v is not None for v in buckets.values()):
        return _NO_DATA

    naive_sum = sum(v or 0.0 for v in buckets.values())
    if naive_sum <= 0:
        return _NO_DATA

    def pct(value: Optional[float]) -> float:
        return round(100.0 * (value or 0.0) / naive_sum, 2)

    overlap_ratio_percent: Optional[float] = None
    unaccounted_ratio_percent: Optional[float] = None
    basis = "phase_sum_only"
    wall = phases.total_wall_seconds
    if wall is not None and wall > 0:
        basis = "reconciled_with_wall_time"
        if naive_sum > wall:
            overlap_ratio_percent = round(100.0 * (naive_sum - wall) / naive_sum, 2)
            unaccounted_ratio_percent = 0.0
        elif wall > naive_sum:
            unaccounted_ratio_percent = round(100.0 * (wall - naive_sum) / wall, 2)
            overlap_ratio_percent = 0.0
        else:
            overlap_ratio_percent = 0.0
            unaccounted_ratio_percent = 0.0

    return ExclusiveCriticalPath(
        llm_percent=pct(buckets["llm"]),
        tool_percent=pct(buckets["tool"]),
        queue_idle_percent=pct(buckets["queue_idle"]),
        orchestration_percent=pct(buckets["orchestration"]),
        overlap_ratio_percent=overlap_ratio_percent,
        unaccounted_ratio_percent=unaccounted_ratio_percent,
        basis=basis,
    )


@dataclasses.dataclass
class UtilizationRatios:
    """Result of :func:`compute_utilization_ratios`. Each field is an
    independent percent of wall-clock time; none are exclusive of the
    others and none are capped at 100 (see module docstring)."""

    model_busy_percent: Optional[float]
    gpu_active_percent: Optional[float]
    #: How gpu_active_percent was derived: "measured" (an independent
    #: gpu_active_seconds was supplied), "approximated_from_model_busy"
    #: (local backend, no independent measurement -- documented
    #: approximation), or "not_observable" (opaque backend, left N/A).
    gpu_active_basis: str
    tool_runner_busy_percent: Optional[float]


def compute_utilization_ratios(
    phases: PhaseSeconds,
    backend: Optional[str] = None,
    gpu_active_seconds: Optional[float] = None,
) -> UtilizationRatios:
    """Derive non-exclusive utilization/busy ratios for one attempt.

    ``gpu_active_seconds``, if supplied, is an independently measured
    GPU-active duration (preferred whenever a runtime can expose it).
    Otherwise, for local backends (``ollama``/``llama_cpp``) where the GPU
    is essentially only active while doing LLM work, ``gpu_active_percent``
    is approximated as equal to ``model_busy_percent`` -- a documented
    approximation, not an independent measurement. For backends the harness
    has no GPU visibility into at all (Siemens API, Copilot agent),
    ``gpu_active_percent`` stays ``None``/N/A.
    """

    wall = phases.total_wall_seconds
    llm_seconds = llm_bucket_seconds(phases)
    tool_seconds = tool_bucket_seconds(phases)

    model_busy_percent: Optional[float] = None
    tool_runner_busy_percent: Optional[float] = None
    if wall is not None and wall > 0:
        if llm_seconds is not None:
            model_busy_percent = round(100.0 * llm_seconds / wall, 2)
        if tool_seconds is not None:
            tool_runner_busy_percent = round(100.0 * tool_seconds / wall, 2)

    gpu_active_percent: Optional[float] = None
    gpu_active_basis = "not_observable"
    if wall is not None and wall > 0 and gpu_active_seconds is not None:
        gpu_active_percent = round(100.0 * gpu_active_seconds / wall, 2)
        gpu_active_basis = "measured"
    elif model_busy_percent is not None and backend in _LOCAL_GPU_VISIBLE_BACKENDS:
        gpu_active_percent = model_busy_percent
        gpu_active_basis = "approximated_from_model_busy"

    return UtilizationRatios(
        model_busy_percent=model_busy_percent,
        gpu_active_percent=gpu_active_percent,
        gpu_active_basis=gpu_active_basis,
        tool_runner_busy_percent=tool_runner_busy_percent,
    )


@dataclasses.dataclass
class PhaseTimingRollup:
    """Group-level (aggregate-row) phase-timing rollup, computed as a ratio
    of *summed* seconds across every sample that reported
    ``total_wall_seconds`` -- never a naive mean of per-sample percentages,
    which would over-weight short attempts relative to long ones.
    """

    phase_timing_sample_count: int
    llm_critical_path_percent: Optional[float]
    tool_critical_path_percent: Optional[float]
    queue_idle_critical_path_percent: Optional[float]
    orchestration_critical_path_percent: Optional[float]
    phase_timing_overlap_ratio_percent: Optional[float]
    phase_timing_unaccounted_ratio_percent: Optional[float]
    model_busy_percent: Optional[float]
    gpu_active_percent: Optional[float]
    tool_runner_busy_percent: Optional[float]


_EMPTY_ROLLUP = PhaseTimingRollup(0, None, None, None, None, None, None, None, None, None)


def rollup_group(
    samples: Sequence[object], backends: Optional[Iterable[str]] = None
) -> PhaseTimingRollup:
    """Roll up phase timing across every sample in one aggregate group.

    Only samples that reported ``total_wall_seconds`` contribute (a missing
    ``total_wall_seconds`` means this attempt was not phase-instrumented at
    all, so it is excluded rather than treated as "zero time in every
    bucket"). ``backends`` may supply a per-sample backend override for the
    ``gpu_active_percent`` approximation; when omitted, each sample object's
    own ``backend`` attribute is used.
    """

    samples = list(samples)
    backend_list = list(backends) if backends is not None else [
        getattr(s, "backend", None) for s in samples
    ]

    instrumented: list[tuple[PhaseSeconds, Optional[str]]] = []
    for sample, backend in zip(samples, backend_list):
        phases = PhaseSeconds.from_sample(sample)
        if phases.total_wall_seconds is not None and phases.total_wall_seconds > 0:
            instrumented.append((phases, backend))

    if not instrumented:
        return _EMPTY_ROLLUP

    total_wall = sum(p.total_wall_seconds for p, _ in instrumented)  # type: ignore[misc]
    bucket_sums = {"llm": 0.0, "tool": 0.0, "queue_idle": 0.0, "orchestration": 0.0}
    naive_sum_total = 0.0
    model_busy_seconds_total = 0.0
    model_busy_seconds_count = 0
    tool_busy_seconds_total = 0.0
    tool_busy_seconds_count = 0
    gpu_active_seconds_total = 0.0
    gpu_active_seconds_count = 0

    for phases, backend in instrumented:
        buckets = bucket_seconds(phases)
        for key, value in buckets.items():
            bucket_sums[key] += value or 0.0
        naive_sum_total += sum(v or 0.0 for v in buckets.values())

        llm_seconds = buckets["llm"]
        if llm_seconds is not None:
            model_busy_seconds_total += llm_seconds
            model_busy_seconds_count += 1
            if backend in _LOCAL_GPU_VISIBLE_BACKENDS:
                gpu_active_seconds_total += llm_seconds
                gpu_active_seconds_count += 1
        tool_seconds = buckets["tool"]
        if tool_seconds is not None:
            tool_busy_seconds_total += tool_seconds
            tool_busy_seconds_count += 1

    if naive_sum_total <= 0:
        critical_path = (None, None, None, None)
    else:
        critical_path = tuple(
            round(100.0 * bucket_sums[key] / naive_sum_total, 2)
            for key in CRITICAL_PATH_BUCKETS
        )

    overlap_ratio_percent: Optional[float] = None
    unaccounted_ratio_percent: Optional[float] = None
    if naive_sum_total > 0 and total_wall > 0:
        if naive_sum_total > total_wall:
            overlap_ratio_percent = round(100.0 * (naive_sum_total - total_wall) / naive_sum_total, 2)
            unaccounted_ratio_percent = 0.0
        elif total_wall > naive_sum_total:
            unaccounted_ratio_percent = round(100.0 * (total_wall - naive_sum_total) / total_wall, 2)
            overlap_ratio_percent = 0.0
        else:
            overlap_ratio_percent = 0.0
            unaccounted_ratio_percent = 0.0

    model_busy_percent = (
        round(100.0 * model_busy_seconds_total / total_wall, 2)
        if model_busy_seconds_count > 0 and total_wall > 0
        else None
    )
    tool_runner_busy_percent = (
        round(100.0 * tool_busy_seconds_total / total_wall, 2)
        if tool_busy_seconds_count > 0 and total_wall > 0
        else None
    )
    gpu_active_percent = (
        round(100.0 * gpu_active_seconds_total / total_wall, 2)
        if gpu_active_seconds_count > 0 and total_wall > 0
        else None
    )

    return PhaseTimingRollup(
        phase_timing_sample_count=len(instrumented),
        llm_critical_path_percent=critical_path[0],
        tool_critical_path_percent=critical_path[1],
        queue_idle_critical_path_percent=critical_path[2],
        orchestration_critical_path_percent=critical_path[3],
        phase_timing_overlap_ratio_percent=overlap_ratio_percent,
        phase_timing_unaccounted_ratio_percent=unaccounted_ratio_percent,
        model_busy_percent=model_busy_percent,
        gpu_active_percent=gpu_active_percent,
        tool_runner_busy_percent=tool_runner_busy_percent,
    )
