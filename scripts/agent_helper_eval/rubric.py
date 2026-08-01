"""Orchestrator routing/rubric specification for agent-helper evaluation.

Design intent (see ``docs/project/agent_helper_benchmark.md`` for full
rationale):

1. **Deterministic correctness gates first.** A task with an objective test
   (unit tests, SQL result diff, lint/compile) must pass that gate before any
   other score can rescue it. A fast-but-wrong model must not win on TPS.
2. **Human/orchestrator review next.** For tasks without a deterministic
   oracle (architecture/planning, code review quality), a reviewer score
   dominates.
3. **Collaboration/iteration efficiency.** How much correction/dialogue was
   needed to reach an accepted result (fewer turns, less rework, better use
   of feedback is rewarded).
4. **Speed/resource/cost last.** Only differentiates between already-good
   candidates; never overrides a correctness failure.

This module contains no model calls and no network access -- it is pure,
deterministic scoring/aggregation logic operating on already-measured (or
synthetic, for tests) numbers.
"""

from __future__ import annotations

import dataclasses
from typing import Optional, Sequence

#: Canonical gate evaluation order (documentation + report ordering).
GATE_ORDER = ("deterministic", "reviewer", "collaboration", "speed_resource_cost")

#: Below this deterministic score (0..100), a candidate fails the correctness
#: gate outright, regardless of how fast or cheap it was.
DETERMINISTIC_PASS_THRESHOLD = 60.0

#: Relative weights applied once (a) the deterministic gate is not failing and
#: (b) not every score component is available. Missing components have their
#: weight redistributed proportionally across the remaining ones so a track
#: that structurally cannot expose a metric (for example GitHub Copilot
#: agent tokens/sec) is not unfairly punished for a metric it never claimed.
DEFAULT_WEIGHTS = {
    "reviewer": 0.5,
    "deterministic": 0.2,
    "collaboration": 0.2,
    "speed": 0.1,
}

SUITABILITY_TIER_RECOMMENDED = "tier-1-recommended"
SUITABILITY_TIER_CONDITIONAL = "tier-2-conditional"
SUITABILITY_TIER_NOT_RECOMMENDED = "tier-3-not-recommended"
SUITABILITY_TIER_INSUFFICIENT_DATA = "insufficient-data"
#: Hard-gate exclusion tier. Always wins over any score-based tier: a
#: candidate in this tier is *excluded from ranking regardless of TPS/speed*
#: (see compute_sample_acceptance / compute_aggregate_hard_gate below).
SUITABILITY_TIER_NOT_USABLE = "not-usable"
#: Stage/evidence-sufficiency exclusion tier -- distinct from
#: SUITABILITY_TIER_NOT_USABLE. A model-run in this tier did not *fail*
#: anything; it simply has not yet accumulated enough evidence (task/
#: category coverage, a genuine reviewer score, collaboration evidence) to
#: support a tier-1/tier-2 daily-runner suitability verdict. See
#: :func:`evidence_stage`/:func:`suitability_tier`. A connect-gate or
#: mini-gate campaign (one or two narrow smoke/mini samples) always lands
#: here, never at SUITABILITY_TIER_RECOMMENDED, no matter how high its
#: score is -- "passed a gate" is not the same claim as "suitable for
#: delegated daily-runner use".
SUITABILITY_TIER_GATE_PASSED_PROVISIONAL = "gate-passed-provisional"

#: Below this reviewer/orchestrator score (0..100), a candidate fails the
#: hard acceptance gate outright -- mirrors DETERMINISTIC_PASS_THRESHOLD for
#: tasks that have no deterministic oracle (architecture/planning, code
#: review quality) but still must not be "usable" on vibes alone.
REVIEWER_MIN_ACCEPTABLE_SCORE = 50.0

#: Minimum percentage of *evaluated* attempts (accepted + not_usable,
#: excluding not_evaluated) that must be accepted for a model-run to be
#: considered usable at all. Below this, the whole model-run is hard-gated
#: as "not-usable" even if its rare accepted attempts scored well and were
#: fast -- repeated fast failures must not be rewarded over one slower,
#: reliably accepted pass.
ACCEPTANCE_RATE_MIN_FOR_USABLE = 50.0

#: Substrings (case-insensitive) that mark a ``reviewer_provenance`` string
#: as a legacy/heuristic keyword-matching scorer rather than a genuine
#: human/orchestrator review -- see :func:`is_heuristic_reviewer_provenance`.
HEURISTIC_REVIEWER_PROVENANCE_MARKERS = ("heuristic", "keyword")


def is_heuristic_reviewer_provenance(provenance: Optional[str]) -> bool:
    """True if ``provenance`` indicates a legacy/heuristic keyword-matching
    scorer rather than a genuine human/orchestrator review.

    This is the softer, non-excluding counterpart to
    :func:`compute_aggregate_hard_gate`'s zero-tolerance checks: a model-run
    whose only reviewer evidence is a keyword/heuristic score (for example
    an imported legacy ``migration_llm_bench`` row, see
    ``historical_adapter.LEGACY_REVIEWER_PROVENANCE``) must never be labeled
    "suitable"/"tier-1-recommended" on that strength alone, regardless of how
    high the raw number is -- see :func:`suitability_tier`.
    """

    if not provenance:
        return False
    lowered = provenance.lower()
    return any(marker in lowered for marker in HEURISTIC_REVIEWER_PROVENANCE_MARKERS)


#: Evidence-sufficiency stage vocabulary -- mirrors schema.EVIDENCE_STAGES
#: exactly (kept as separate string constants here since schema.py must not
#: import rubric.py, to avoid a circular import; equality is enforced by a
#: unit test).
EVIDENCE_STAGE_GATE_ONLY = "gate_only"
EVIDENCE_STAGE_PARTIAL_SUITE = "partial_suite"
EVIDENCE_STAGE_FULL_SUITE = "full_suite"

#: Minimum number of *distinct* task categories a model-run must have been
#: evaluated against (see catalog.TaskSpec.category, e.g. "connectivity",
#: "coding", "review", "sql_migration", "frontend", "architecture",
#: "collaboration" -- 7 categories total in catalog.DEFAULT_CATALOG) before
#: it can be considered to have "full_suite" evidence coverage. A connect-
#: gate/mini-gate campaign only ever covers 1-2 categories (connectivity +
#: coding); this threshold is deliberately below the full count of 7 so
#: that a genuinely broad (if not literally exhaustive) evaluation is not
#: perpetually stuck at "partial_suite".
MIN_TASK_CATEGORIES_FOR_TIER1 = 4


def evidence_stage(
    distinct_task_categories_evaluated: int,
    has_reviewer_score_evidence: bool,
    has_collaboration_score_evidence: bool,
    min_task_categories_for_tier1: int = MIN_TASK_CATEGORIES_FOR_TIER1,
) -> str:
    """Classify how much real evidence backs a model-run's suitability
    verdict, independent of its score.

    This is the fix for pilot-review finding #1: passing a narrow connect-
    gate or mini-gate smoke test is real, valid evidence that a model *can
    be reached and can produce syntactically/behaviourally correct output
    for one tiny task* -- but it is not the same claim as "suitable for
    delegated daily-runner use", which requires broader task/category
    coverage, a genuine (non-heuristic) reviewer/orchestrator score, and
    collaboration/iteration evidence. See :func:`suitability_tier`, which
    uses this to cap the reported tier so that a smoke-gate pass is never
    worded as "tier-1-recommended"/"strong candidate".

    Returns one of:

    - ``EVIDENCE_STAGE_GATE_ONLY``: fewer than 2 distinct task categories
      evaluated (a single connect-gate or mini-gate run) -- always capped
      at :data:`SUITABILITY_TIER_GATE_PASSED_PROVISIONAL`.
    - ``EVIDENCE_STAGE_PARTIAL_SUITE``: at least 2 categories evaluated, but
      fewer than ``min_task_categories_for_tier1`` categories, or missing
      genuine reviewer/collaboration evidence -- capped at
      :data:`SUITABILITY_TIER_CONDITIONAL` (never
      :data:`SUITABILITY_TIER_RECOMMENDED`).
    - ``EVIDENCE_STAGE_FULL_SUITE``: broad category coverage *and* genuine
      reviewer evidence *and* collaboration evidence -- eligible for the
      full tier range (subject to every other existing rule, including the
      heuristic-only-reviewer cap and the hard acceptance gate).
    """

    if distinct_task_categories_evaluated < 2:
        return EVIDENCE_STAGE_GATE_ONLY
    if (
        distinct_task_categories_evaluated < min_task_categories_for_tier1
        or not has_reviewer_score_evidence
        or not has_collaboration_score_evidence
    ):
        return EVIDENCE_STAGE_PARTIAL_SUITE
    return EVIDENCE_STAGE_FULL_SUITE


COPILOT_METRICS_CAVEAT_EN = (
    "GitHub Copilot task-agent references cannot reliably expose raw "
    "tokens/sec, TTFT, or process-level CPU/RAM/GPU/VRAM unless the runtime "
    "surfaces it explicitly; those fields are recorded as N/A rather than "
    "estimated. Pure-model and tool-agent comparisons across such references "
    "are kept in separate leaderboard sections."
)
COPILOT_METRICS_CAVEAT_DE = (
    "GitHub-Copilot-Task-Agent-Referenzen koennen rohe Tokens/Sekunde, TTFT "
    "oder Prozess-CPU/RAM/GPU/VRAM nicht zuverlaessig offenlegen, sofern die "
    "Laufzeitumgebung sie nicht explizit liefert; diese Felder werden als "
    "N/A erfasst statt geschaetzt. Pure-Model- und Tool-Agent-Vergleiche mit "
    "solchen Referenzen bleiben in getrennten Leaderboard-Abschnitten."
)


def normalize_speed_score(
    elapsed_seconds: Optional[float], peer_elapsed_seconds: Sequence[float]
) -> Optional[float]:
    """Score a candidate's speed relative to the fastest peer in its group.

    Returns 100 for the fastest peer, proportionally less for slower ones,
    and ``None`` if ``elapsed_seconds`` is unavailable or non-positive. This
    keeps speed a *relative*, bounded signal (never dominant, per the rubric
    weights) instead of an unbounded raw number.
    """

    if elapsed_seconds is None or elapsed_seconds <= 0:
        return None
    candidates = [v for v in peer_elapsed_seconds if v is not None and v > 0]
    if not candidates:
        return None
    fastest = min(candidates)
    score = 100.0 * (fastest / elapsed_seconds)
    return round(min(score, 100.0), 2)


def _redistribute_weights(
    available: dict[str, Optional[float]], weights: dict[str, float]
) -> dict[str, float]:
    present = {k: w for k, w in weights.items() if available.get(k) is not None}
    total = sum(present.values())
    if total <= 0:
        return {}
    return {k: w / total for k, w in present.items()}


def compute_composite_score(
    deterministic_score: Optional[float],
    reviewer_score: Optional[float],
    collaboration_score: Optional[float],
    speed_score: Optional[float],
    weights: Optional[dict[str, float]] = None,
    pass_threshold: float = DETERMINISTIC_PASS_THRESHOLD,
) -> Optional[float]:
    """Combine component scores into one composite/overall score (0..100).

    A failing deterministic gate (``deterministic_score < pass_threshold``)
    caps the composite at the deterministic score itself: no amount of
    reviewer polish, collaboration efficiency, or raw speed can rescue a
    demonstrably wrong result. When the deterministic gate passes, is not
    applicable (``None``, e.g. architecture/planning tasks), all available
    components are combined using ``weights`` (defaulting to
    :data:`DEFAULT_WEIGHTS`), with weights of missing components
    redistributed proportionally across the remaining ones.
    """

    weights = dict(weights or DEFAULT_WEIGHTS)

    if deterministic_score is not None and deterministic_score < pass_threshold:
        return round(deterministic_score, 2)

    available = {
        "deterministic": deterministic_score,
        "reviewer": reviewer_score,
        "collaboration": collaboration_score,
        "speed": speed_score,
    }
    effective_weights = _redistribute_weights(available, weights)
    if not effective_weights:
        return None
    total = sum(
        available[key] * weight for key, weight in effective_weights.items()
    )
    return round(total, 2)


def compute_sample_acceptance(
    status: str,
    deterministic_score: Optional[float] = None,
    reviewer_score: Optional[float] = None,
    unsafe_behavior_flag: Optional[bool] = None,
    output_placeholder_or_incomplete: Optional[bool] = None,
    deterministic_pass_threshold: float = DETERMINISTIC_PASS_THRESHOLD,
    reviewer_min_acceptable_score: float = REVIEWER_MIN_ACCEPTABLE_SCORE,
) -> tuple[str, Optional[str]]:
    """Decide one attempt's hard-gate acceptance verdict.

    This is the single authoritative function for turning "raw" per-attempt
    evidence (system status, deterministic test score, reviewer/orchestrator
    score, unsafe-behavior/placeholder flags) into the controlled
    ``acceptance_status`` vocabulary
    (``agent_helper_eval.schema.ACCEPTANCE_STATUSES``). Every call site that
    constructs a ``SampleRecord`` must call this rather than setting
    ``acceptance_status`` ad hoc, so the "speed never compensates for
    unusable quality" policy is enforced in exactly one place.

    Zero-tolerance criteria (any one is enough to force ``"not_usable"``,
    regardless of any score or how fast the attempt was):

    - ``status`` is ``"error"`` or ``"timeout"``.
    - ``unsafe_behavior_flag`` is ``True``.
    - ``output_placeholder_or_incomplete`` is ``True``.
    - ``deterministic_score`` is present and below ``deterministic_pass_threshold``.
    - ``reviewer_score`` is present and below ``reviewer_min_acceptable_score``.

    If ``status`` is ``"skipped"`` and none of the above evidence applies,
    the verdict is ``"not_evaluated"`` (no attempt was made -- this is *not*
    the same as a quality failure). Likewise, if there is no scoring
    evidence at all (both ``deterministic_score`` and ``reviewer_score`` are
    ``None``, and none of the hard-failure conditions apply), the verdict is
    also ``"not_evaluated"``: silence is never treated as acceptance.

    Returns ``(acceptance_status, acceptance_reasons)`` where
    ``acceptance_reasons`` is a semicolon-joined human-readable string, or
    ``None`` when the verdict is ``"accepted"`` with nothing to report.
    """

    reasons: list[str] = []

    if status in ("error", "timeout"):
        reasons.append(f"status={status}")
    if unsafe_behavior_flag is True:
        reasons.append("unsafe behavior detected")
    if output_placeholder_or_incomplete is True:
        reasons.append("placeholder/incomplete output")
    if (
        deterministic_score is not None
        and deterministic_score < deterministic_pass_threshold
    ):
        reasons.append(
            f"deterministic_score {deterministic_score:.1f} below pass "
            f"threshold {deterministic_pass_threshold:.1f}"
        )
    if (
        reviewer_score is not None
        and reviewer_score < reviewer_min_acceptable_score
    ):
        reasons.append(
            f"reviewer_score {reviewer_score:.1f} below minimum acceptable "
            f"{reviewer_min_acceptable_score:.1f}"
        )

    if reasons:
        return "not_usable", "; ".join(reasons)

    if status == "skipped":
        return "not_evaluated", "skipped -- no attempt evaluated"

    have_evidence = deterministic_score is not None or reviewer_score is not None
    if status == "success" and have_evidence:
        return "accepted", None

    return "not_evaluated", "no deterministic/reviewer score evidence recorded"


def compute_aggregate_hard_gate(
    accepted_count: int,
    not_usable_count: int,
    unsafe_count: int,
    task_count: int,
    acceptance_rate_min_for_usable: float = ACCEPTANCE_RATE_MIN_FOR_USABLE,
) -> tuple[bool, Optional[str]]:
    """Decide whether a model-run (aggregate) fails the hard acceptance gate.

    This is the group-level counterpart to :func:`compute_sample_acceptance`:
    it decides whether a model-run as a whole must be excluded from ranking
    entirely (suitability tier ``"not-usable"``), regardless of how good its
    speed/TPS numbers are among the attempts it did accept.

    Rules:

    - ``unsafe_count > 0`` always fails the gate -- zero tolerance,
      independent of how rare the unsafe attempt was.
    - Otherwise, the acceptance rate is computed over *evaluated* attempts
      only (``accepted_count / (accepted_count + not_usable_count)``),
      deliberately excluding ``not_evaluated`` attempts from the denominator
      so that genuinely missing evaluation data (for example some historical
      rows lacking a quality score) is not misclassified as a hard quality
      failure -- that case falls back to the separate, softer
      ``"insufficient-data"`` tier instead. If nothing was ever evaluated
      (``accepted_count + not_usable_count == 0``), this function does not
      fail the gate on that basis alone.
    - If the evaluated acceptance rate is below
      ``acceptance_rate_min_for_usable``, the gate fails. This is what makes
      "repeated fast failures" score worse than "one slower accepted pass":
      a model that fails most of its attempts is hard-gated out even if its
      rare accepted attempt was both fast and high quality.

    Returns ``(hard_gate_failed, hard_gate_reasons)`` where
    ``hard_gate_reasons`` is a semicolon-joined human-readable string, or
    ``None`` when the gate passes.
    """

    reasons: list[str] = []
    if unsafe_count > 0:
        reasons.append(
            f"{unsafe_count} sample(s) flagged with unsafe behavior "
            "(zero tolerance)"
        )

    evaluated_count = accepted_count + not_usable_count
    if evaluated_count > 0:
        acceptance_rate = 100.0 * accepted_count / evaluated_count
        if acceptance_rate < acceptance_rate_min_for_usable:
            reasons.append(
                f"only {acceptance_rate:.1f}% of evaluated attempts accepted "
                f"(minimum {acceptance_rate_min_for_usable:.1f}%; "
                f"{accepted_count}/{evaluated_count} evaluated, "
                f"{task_count} total)"
            )

    if reasons:
        return True, "; ".join(reasons)
    return False, None


def suitability_tier(
    overall_score: Optional[float],
    success_rate_percent: Optional[float],
    task_count: int,
    hard_gate_failed: bool = False,
    reviewer_evidence_is_heuristic_only: bool = False,
    evidence_stage: str = EVIDENCE_STAGE_FULL_SUITE,
) -> str:
    """Classify a model-run into a coarse suitability tier for the leaderboard.

    ``hard_gate_failed`` (see :func:`compute_aggregate_hard_gate`) always
    short-circuits to :data:`SUITABILITY_TIER_NOT_USABLE`, *before* any
    score-based tier is considered -- speed/overall-score can never rescue a
    model-run that failed the hard acceptance gate (unsafe behavior, or too
    low a share of evaluated attempts accepted).

    ``reviewer_evidence_is_heuristic_only`` (see
    :func:`is_heuristic_reviewer_provenance`) caps the tier below
    :data:`SUITABILITY_TIER_RECOMMENDED`: a model-run whose entire quality
    signal rests on a legacy/heuristic keyword-matching reviewer score (no
    deterministic test-oracle evidence anywhere in the group) must never be
    labeled "recommended"/"suitable" on that strength alone, no matter how
    high the raw score is.

    ``evidence_stage`` (see :func:`evidence_stage` function above -- default
    :data:`EVIDENCE_STAGE_FULL_SUITE` for full backward compatibility with
    every existing caller/dry-run/historical-import aggregate, which already
    covers multiple task categories) additionally caps the tier *below*
    what the score alone would justify:

    - ``EVIDENCE_STAGE_GATE_ONLY`` always returns
      :data:`SUITABILITY_TIER_GATE_PASSED_PROVISIONAL` (once the hard gate
      and success-rate floor are satisfied) -- never tier-1 or tier-2,
      regardless of score. A connect-gate/mini-gate pass is real evidence
      that a model *works*, not evidence that it is *suitable for
      delegation*.
    - ``EVIDENCE_STAGE_PARTIAL_SUITE`` caps the tier at
      :data:`SUITABILITY_TIER_CONDITIONAL` -- never
      :data:`SUITABILITY_TIER_RECOMMENDED`.
    """

    if hard_gate_failed:
        return SUITABILITY_TIER_NOT_USABLE
    if task_count <= 0 or overall_score is None or success_rate_percent is None:
        return SUITABILITY_TIER_INSUFFICIENT_DATA
    if success_rate_percent < 50.0:
        return SUITABILITY_TIER_NOT_RECOMMENDED
    if evidence_stage == EVIDENCE_STAGE_GATE_ONLY:
        return SUITABILITY_TIER_GATE_PASSED_PROVISIONAL
    if overall_score >= 80.0:
        if reviewer_evidence_is_heuristic_only:
            return SUITABILITY_TIER_CONDITIONAL
        if evidence_stage == EVIDENCE_STAGE_PARTIAL_SUITE:
            return SUITABILITY_TIER_CONDITIONAL
        return SUITABILITY_TIER_RECOMMENDED
    if overall_score >= 60.0:
        return SUITABILITY_TIER_CONDITIONAL
    return SUITABILITY_TIER_NOT_RECOMMENDED



@dataclasses.dataclass
class AttemptOutcome:
    """Minimal view of one attempt, used for time-to-accepted-result analysis."""

    elapsed_seconds: float
    total_tokens: Optional[int]
    accepted: bool


def time_to_accepted_result(attempts: Sequence[AttemptOutcome]) -> dict[str, object]:
    """Summarize how long/how many tokens it took to reach an accepted result.

    ``attempts`` must be given in chronological order (iteration 1, 2, ...).
    Returns a dict with ``accepted`` (bool), ``iterations_used`` (int, up to
    and including the accepted attempt, or all attempts if never accepted),
    ``time_to_accept_seconds`` (float, cumulative elapsed time), and
    ``total_tokens`` (int or ``None`` if no attempt reported tokens).
    """

    cumulative_seconds = 0.0
    cumulative_tokens = 0
    have_tokens = False
    for index, attempt in enumerate(attempts, start=1):
        cumulative_seconds += attempt.elapsed_seconds
        if attempt.total_tokens is not None:
            cumulative_tokens += attempt.total_tokens
            have_tokens = True
        if attempt.accepted:
            return {
                "accepted": True,
                "iterations_used": index,
                "time_to_accept_seconds": round(cumulative_seconds, 3),
                "total_tokens": cumulative_tokens if have_tokens else None,
            }
    return {
        "accepted": False,
        "iterations_used": len(attempts),
        "time_to_accept_seconds": round(cumulative_seconds, 3),
        "total_tokens": cumulative_tokens if have_tokens else None,
    }


def compare_collaboration_efficiency(
    label_a: str,
    attempts_a: Sequence[AttemptOutcome],
    label_b: str,
    attempts_b: Sequence[AttemptOutcome],
) -> dict[str, object]:
    """Compare two candidates on time-to-accepted-result and total tokens.

    Directly answers the methodology question "can more fast correction loops
    beat one slow high-quality pass?" by comparing cumulative wall-clock time
    and cumulative tokens actually spent reaching an accepted result, not just
    single-attempt speed. Returns a dict with both summaries plus a ``faster``
    and ``cheaper_in_tokens`` verdict (candidate label or ``"tie"``/``None``
    when not comparable).
    """

    summary_a = time_to_accepted_result(attempts_a)
    summary_b = time_to_accepted_result(attempts_b)

    faster: Optional[str]
    if not summary_a["accepted"] or not summary_b["accepted"]:
        faster = None
    elif summary_a["time_to_accept_seconds"] < summary_b["time_to_accept_seconds"]:
        faster = label_a
    elif summary_b["time_to_accept_seconds"] < summary_a["time_to_accept_seconds"]:
        faster = label_b
    else:
        faster = "tie"

    cheaper: Optional[str]
    tokens_a = summary_a["total_tokens"]
    tokens_b = summary_b["total_tokens"]
    if tokens_a is None or tokens_b is None:
        cheaper = None
    elif tokens_a < tokens_b:
        cheaper = label_a
    elif tokens_b < tokens_a:
        cheaper = label_b
    else:
        cheaper = "tie"

    return {
        label_a: summary_a,
        label_b: summary_b,
        "faster": faster,
        "cheaper_in_tokens": cheaper,
    }
