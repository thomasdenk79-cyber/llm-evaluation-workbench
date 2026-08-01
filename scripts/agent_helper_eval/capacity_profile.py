"""Post-quality-gate concurrency/capacity profile specification.

This module is data/spec and pure decision logic only -- it never issues a
concurrent request, never starts a model, and never touches Ollama/
llama.cpp/network. Model campaigns that actually run these concurrency
levels are implemented and executed later, strictly serially, by the parent
agent, exactly like the rest of this harness.

Purpose: determine whether running more local helper agents (more
concurrent requests against the SAME already-accepted local model)
meaningfully improves throughput, or merely causes queueing/thrashing that
adds no real benefit -- see :func:`classify_concurrency_scaling`.

**Mandatory ordering, enforced as code, not just documentation:** concurrency
profiling must never run before (1) the model's single-request result has
already passed the hard acceptance gate
(``agent_helper_eval.rubric.compute_aggregate_hard_gate``) and (2) an
explicit memory-safety/VRAM-headroom check for the 12 GB VRAM notebook GPU.
:func:`can_start_concurrency_profile` is the single authoritative decision
function for this; :func:`agent_helper_eval.schema.validate_capacity_profile`
independently re-enforces the same two preconditions at the record level, so
neither a caller bug nor a hand-edited record can bypass the gate.
"""

from __future__ import annotations

import dataclasses
from typing import Optional

from .schema import CONCURRENCY_LEVELS

#: Assumed VRAM budget for the current notebook GPU (12 GB), used as the
#: default reference for headroom checks. A different, actually-measured
#: card should override this rather than silently reusing the constant.
DEFAULT_VRAM_BUDGET_MB = 12288.0

#: Below this efficiency percent (aggregate TPS vs. perfect linear scaling
#: from the concurrency_level=1 baseline), added concurrency is considered
#: to provide essentially no real benefit -- queueing/resource contention
#: ("thrashing") dominates over any throughput gain.
THRASHING_EFFICIENCY_THRESHOLD_PERCENT = 50.0

#: At or above this efficiency percent, concurrency scales close to
#: linearly and adding concurrent local helper agents is a genuine
#: throughput win.
GOOD_SCALING_EFFICIENCY_THRESHOLD_PERCENT = 85.0

SCALING_CLASSIFICATIONS = (
    "scales-well",
    "diminishing-returns",
    "thrashing-or-no-benefit",
    "insufficient-data",
)


def classify_concurrency_scaling(efficiency_percent: Optional[float]) -> str:
    """Classify a concurrency level's throughput efficiency.

    ``efficiency_percent`` is
    ``100 * aggregate_tokens_per_second / (concurrency_level * single_request_baseline_tokens_per_second)``.
    100% means perfect linear scaling (N concurrent requests produced N
    times the single-request throughput); well below 100% means the
    additional concurrent requests mostly queued/contended for the same
    GPU/VRAM/compute rather than adding real throughput.

    This is the direct, quantitative answer to "does running more local
    helper agents against the same model improve throughput, or merely
    queue/thrash?" -- never inferred from raw TPS numbers alone, which
    would not distinguish "genuinely faster in aggregate" from "just as
    slow per request, multiplied by a queue".
    """

    if efficiency_percent is None:
        return "insufficient-data"
    if efficiency_percent >= GOOD_SCALING_EFFICIENCY_THRESHOLD_PERCENT:
        return "scales-well"
    if efficiency_percent >= THRASHING_EFFICIENCY_THRESHOLD_PERCENT:
        return "diminishing-returns"
    return "thrashing-or-no-benefit"


def compute_throughput_efficiency_percent(
    aggregate_tokens_per_second: Optional[float],
    concurrency_level: int,
    single_request_baseline_tokens_per_second: Optional[float],
) -> Optional[float]:
    """Compute the throughput-efficiency percent used by
    :func:`classify_concurrency_scaling`. Returns ``None`` if either
    throughput number is unavailable or the baseline is non-positive
    (never divide by zero / fabricate an efficiency number)."""

    if (
        aggregate_tokens_per_second is None
        or single_request_baseline_tokens_per_second is None
        or single_request_baseline_tokens_per_second <= 0
        or concurrency_level < 1
    ):
        return None
    expected_linear = concurrency_level * single_request_baseline_tokens_per_second
    if expected_linear <= 0:
        return None
    return round(100.0 * aggregate_tokens_per_second / expected_linear, 2)


@dataclasses.dataclass
class ConcurrencyPreflightResult:
    """Result of :func:`can_start_concurrency_profile`."""

    allowed: bool
    reasons: tuple[str, ...]


def can_start_concurrency_profile(
    single_request_accepted: bool,
    single_request_hard_gate_failed: bool,
    memory_safety_checked: bool,
    vram_headroom_mb: Optional[float] = None,
) -> ConcurrencyPreflightResult:
    """Decide whether a concurrency/capacity profile run may start at all.

    This is the single authoritative preflight gate a (future) orchestrator
    must call before issuing any concurrent request against a local model.
    All of the following must hold:

    - ``single_request_accepted`` is True: the model's single-request result
      for the reference task already reached ``acceptance_status ==
      "accepted"`` (see ``rubric.compute_sample_acceptance``). Concurrency
      profiling is a *performance* measurement among already-usable
      candidates -- it must never run first, and must never run for a model
      whose single-request quality/stability was never actually confirmed.
    - ``single_request_hard_gate_failed`` is False: the model-run as a whole
      must not have failed the aggregate hard acceptance gate (see
      ``rubric.compute_aggregate_hard_gate``). A model excluded from ranking
      for quality reasons must not be resource-profiled either.
    - ``memory_safety_checked`` is True: an explicit VRAM-headroom check was
      performed for the target concurrency level (12 GB VRAM constraint).
    - if ``vram_headroom_mb`` is supplied, it must not be negative (a
      negative headroom means the requested concurrency level is already
      known to be unsafe on this hardware).

    Returns a :class:`ConcurrencyPreflightResult`; ``allowed`` is only True
    when every one of the above holds.
    """

    reasons: list[str] = []
    if not single_request_accepted:
        reasons.append(
            "single-request result for this model is not yet accepted -- "
            "concurrency profiling must never run before single-request "
            "quality/stability acceptance"
        )
    if single_request_hard_gate_failed:
        reasons.append(
            "this model-run failed the hard acceptance gate -- excluded "
            "models must never be resource/concurrency-profiled"
        )
    if not memory_safety_checked:
        reasons.append(
            "no explicit memory-safety/VRAM-headroom check was performed "
            "for this concurrency level (12 GB VRAM constraint)"
        )
    if vram_headroom_mb is not None and vram_headroom_mb < 0:
        reasons.append(
            f"estimated VRAM headroom is negative ({vram_headroom_mb:.0f} MB) "
            "-- this concurrency level is not safe to attempt on this hardware"
        )
    return ConcurrencyPreflightResult(allowed=not reasons, reasons=tuple(reasons))


@dataclasses.dataclass
class ConcurrencyStageSpec:
    """One staged concurrency level in the capacity-profile catalog."""

    concurrency_level: int
    name: str
    description: str
    requires_prior_level: Optional[int]


#: The standard staged concurrency-profile catalog: 1 (baseline, always
#: required first), 2, then 4 simultaneous requests against the SAME
#: already-accepted local model. Each stage explicitly requires the prior
#: stage to have already completed -- concurrency is escalated step by
#: step, never jumped straight to 4, so a VRAM/stability problem at a lower
#: concurrency level is caught before attempting a higher one.
DEFAULT_CONCURRENCY_CATALOG: tuple[ConcurrencyStageSpec, ...] = (
    ConcurrencyStageSpec(
        concurrency_level=1,
        name="Single-request baseline",
        description=(
            "One request at a time against the already quality-gate-accepted "
            "model. Establishes the single_request_baseline_tokens_per_second "
            "every higher concurrency level's efficiency is measured against. "
            "This stage is itself gated by can_start_concurrency_profile() -- "
            "it still requires single-request acceptance and a memory-safety "
            "check before it runs, it simply has no *prior concurrency level* "
            "dependency."
        ),
        requires_prior_level=None,
    ),
    ConcurrencyStageSpec(
        concurrency_level=2,
        name="Two simultaneous requests",
        description=(
            "Two concurrent requests against the same loaded model. Requires "
            "the concurrency_level=1 baseline to have completed and an "
            "explicit re-check of VRAM headroom for the added concurrent "
            "context/KV-cache footprint before starting."
        ),
        requires_prior_level=1,
    ),
    ConcurrencyStageSpec(
        concurrency_level=4,
        name="Four simultaneous requests",
        description=(
            "Four concurrent requests against the same loaded model. "
            "Requires the concurrency_level=2 stage to have completed "
            "without a memory-safety violation and a fresh VRAM-headroom "
            "re-check -- on a 12 GB VRAM card this stage is the most likely "
            "to reveal thrashing (see classify_concurrency_scaling()) or an "
            "outright out-of-memory condition."
        ),
        requires_prior_level=2,
    ),
)


def validate_concurrency_catalog(
    catalog: tuple[ConcurrencyStageSpec, ...] = DEFAULT_CONCURRENCY_CATALOG,
) -> list[str]:
    """Validate a concurrency-profile catalog's structural invariants."""

    errors: list[str] = []
    levels_seen: set[int] = set()
    for stage in catalog:
        if stage.concurrency_level < 1:
            errors.append(f"concurrency_level must be >= 1, got {stage.concurrency_level!r}")
        if stage.concurrency_level in levels_seen:
            errors.append(f"duplicate concurrency_level: {stage.concurrency_level!r}")
        levels_seen.add(stage.concurrency_level)
        if (
            stage.requires_prior_level is not None
            and stage.requires_prior_level not in levels_seen
        ):
            errors.append(
                f"concurrency_level {stage.concurrency_level!r} requires prior "
                f"level {stage.requires_prior_level!r}, which has not appeared "
                "earlier in the catalog"
            )
    if levels_seen and set(CONCURRENCY_LEVELS) - levels_seen:
        errors.append(
            f"catalog is missing standard concurrency level(s): "
            f"{sorted(set(CONCURRENCY_LEVELS) - levels_seen)}"
        )
    return errors
