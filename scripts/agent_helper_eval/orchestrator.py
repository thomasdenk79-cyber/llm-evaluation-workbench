"""Campaign orchestrator skeleton for the agent-helper evaluation harness.

This module intentionally separates three concerns that must never be
conflated:

1. **Synthetic dry-run** (:func:`run_dry_run`): generates fully synthetic
   sample data with an in-process seeded RNG. No subprocess, no network, no
   model, no Ollama/llama.cpp/Siemens/Copilot call of any kind. Safe to run
   any number of times to validate the storage/aggregation/report pipeline.
2. **Connect-gate planning** (:func:`build_connect_gate_plan`): returns the
   *exact* shell commands a human or parent agent should run to smoke-test
   connectivity for one model/backend. This function only builds strings; it
   never executes anything.
3. **Connect-gate execution** (:func:`run_connect_gate_check`): a thin
   executor abstraction. Production use injects a real subprocess-based
   executor (:func:`subprocess_executor`); tests inject a fake, in-memory
   executor. Nothing in this module calls :func:`subprocess_executor` by
   itself -- a caller (the parent agent, explicitly, later) must choose to
   wire it in.

Output isolation: every campaign this module writes lives under
``benchmark_results/agent-helper/<campaign_id>/`` -- never inside the
existing flat ``benchmark_results/`` layout used by
``scripts/llm_migration_benchmark.py``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import random
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import aggregate, capacity_profile, catalog, rubric, storage
from .model_inventory import ModelInventoryError, ModelSpec, load_inventory
from .report import CampaignReportData, render_report
from .schema import (
    AggregateRecord,
    CapacityProfileRecord,
    CONCURRENCY_LEVELS,
    SampleRecord,
    SCHEMA_VERSION,
    utc_now_iso,
)

AGENT_HELPER_SUBDIR = ("benchmark_results", "agent-helper")


def agent_helper_root(repo_root: Path) -> Path:
    """The dedicated, isolated root directory for all agent-helper campaigns."""

    root = repo_root
    for part in AGENT_HELPER_SUBDIR:
        root = root / part
    return root


def campaign_output_dir(repo_root: Path, campaign_id: str) -> Path:
    return agent_helper_root(repo_root) / campaign_id


#: Default, committed example inventory used purely for the report's
#: optional "model inventory & feasibility" section when no campaign-
#: specific inventory is supplied. Loading is always best-effort: a
#: missing file, invalid JSON, failed schema validation, or a suspicious
#: secret-like pattern must never break dry-run/report generation -- it
#: silently falls back to an empty inventory (the report already renders a
#: clear "no inventory loaded" message in that case).
DEFAULT_MODEL_INVENTORY_RELATIVE_PATH = ("benchmarks", "agent-helper-model-inventory.example.json")


def load_default_model_inventory(repo_root: Path) -> Sequence[ModelSpec]:
    """Best-effort load of the default example model inventory for report display.

    Returns an empty tuple (never raises) if the file is missing, invalid,
    fails schema validation, or looks like it contains a secret -- report
    generation must never depend on this succeeding.
    """

    path = repo_root
    for part in DEFAULT_MODEL_INVENTORY_RELATIVE_PATH:
        path = path / part
    if not path.exists():
        return ()
    try:
        return tuple(load_inventory(path))
    except (ModelInventoryError, OSError, ValueError, TypeError, KeyError):
        # TypeError/KeyError cover a malformed inventory entry missing a
        # required field (ModelSpec.from_json raises TypeError via
        # dataclass __init__, not ModelInventoryError, for that case) --
        # still never allowed to break dry-run/report generation.
        return ()


# ---------------------------------------------------------------------------
# 1. Synthetic dry-run (no model/network calls)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class DryRunFixtureModel:
    """A synthetic, in-memory-only "model" used purely to exercise the pipeline."""

    provider: str
    backend: str
    model: str
    runtime: Optional[str]
    quantization: Optional[str]


#: Name of the deliberate "fast but unusable" demonstration fixture (see
#: DEFAULT_DRY_RUN_MODELS below): proves, via the no-model dry-run alone,
#: that a model which is fast on every attempt is still excluded from
#: ranking by the hard acceptance gate when its outputs are not usable --
#: directly addressing "speed must never compensate for unusable quality".
FIXTURE_FAST_UNRELIABLE_MODEL = "fixture-fast-unreliable"

DEFAULT_DRY_RUN_MODELS: tuple[DryRunFixtureModel, ...] = (
    DryRunFixtureModel("local", "ollama", "fixture-fast-local", "ollama/0.0.0-fixture", "Q4_K_M"),
    DryRunFixtureModel("siemens", "siemens_api", "fixture-strong-cloud", "siemens-api-fixture", None),
    DryRunFixtureModel("github", "copilot_agent", "fixture-copilot-agent", "copilot-cli-fixture", None),
    DryRunFixtureModel(
        "local", "ollama", FIXTURE_FAST_UNRELIABLE_MODEL, "ollama/0.0.0-fixture", "Q4_K_M"
    ),
)

#: Deliberate, deterministic per-task overrides for
#: :data:`FIXTURE_FAST_UNRELIABLE_MODEL`, one per hard-gate criterion (plus
#: two genuinely accepted, fast results to prove a rare good attempt still
#: cannot rescue an overall unusable model-run). Never left to RNG chance --
#: this fixture exists specifically to demonstrate the hard acceptance gate
#: end-to-end without calling any real model.
_FIXTURE_FAST_UNRELIABLE_OVERRIDES: dict[str, dict[str, object]] = {
    "mini-coding-tests-v1": {
        "deterministic_score": 18.0,
        "reviewer_score": 92.0,
        "note": (
            "Demonstration: fails the fixed deterministic unit-test suite "
            "despite a deceptively high reviewer score. A failing "
            "deterministic gate caps/forces exclusion regardless of "
            "reviewer polish or how fast the attempt was."
        ),
    },
    "bug-review-v1": {
        "reviewer_score": 88.0,
        "unsafe_behavior_flag": True,
        "note": (
            "Demonstration: flagged for unsafe behavior (e.g. a proposed "
            "destructive action outside the requested scope). Zero "
            "tolerance forces exclusion regardless of the reviewer score."
        ),
    },
    "frontend-task-v1": {
        "deterministic_score": 75.0,
        "reviewer_score": 70.0,
        "output_placeholder_or_incomplete": True,
        "note": (
            "Demonstration: deterministic check and reviewer score both "
            "look acceptable, but the delivered component is a "
            "stub/placeholder. Placeholder detection forces exclusion "
            "regardless of any score."
        ),
    },
    "architecture-planning-v1": {
        "reviewer_score": 35.0,
        "note": (
            "Demonstration: this task has no deterministic oracle; the "
            "reviewer/orchestrator score alone is below the minimum "
            "acceptable threshold, so the attempt is not_usable even "
            "though it was fast."
        ),
    },
    "sql-migration-v1": {
        "deterministic_score": 95.0,
        "reviewer_score": 90.0,
        "note": (
            "Demonstration: one of this model's rare accepted, fast "
            "results -- shows that a single good attempt cannot rescue the "
            "model-run once its overall acceptance rate is too low."
        ),
    },
    "multi-turn-collaboration-v1": {
        "deterministic_score": 91.0,
        "reviewer_score": 85.0,
        "collaboration_score": 88.0,
        "note": "Demonstration: a second rare accepted, fast result.",
    },
}


def _write_artifact(artifacts_dir: Path, sample_id: str, content: str) -> tuple[str, str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    relative_path = f"artifacts/{sample_id}.txt"
    absolute_path = artifacts_dir / f"{sample_id}.txt"
    absolute_path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return relative_path, digest


def generate_synthetic_samples(
    campaign_id: str,
    run_id: str,
    output_dir: Path,
    tasks: Sequence[catalog.TaskSpec] = tuple(catalog.DEFAULT_CATALOG),
    models: Sequence[DryRunFixtureModel] = DEFAULT_DRY_RUN_MODELS,
    seed: int = 1234,
    benchmark_set: str = "agent-helper-catalog-v1",
    benchmark_version: str = catalog.CATALOG_SCHEMA_VERSION,
) -> list[SampleRecord]:
    """Deterministically synthesize sample rows for every (model, task, track).

    Uses a seeded RNG so the same ``seed`` always reproduces the same
    numbers; no wall-clock timing, no subprocess, no network access.

    ``benchmark_set``/``benchmark_version`` default to the bundled catalog's
    identity but must be overridden by the caller when ``tasks`` came from a
    *different* (for example externally authored) catalog document -- see
    :func:`run_dry_run` -- so generated samples are never mislabeled with the
    wrong catalog identity.
    """

    rng = random.Random(seed)
    artifacts_dir = output_dir / "artifacts"
    samples: list[SampleRecord] = []
    sample_seq = 0

    for model in models:
        is_copilot = model.backend == "copilot_agent"
        is_fast_unreliable = model.model == FIXTURE_FAST_UNRELIABLE_MODEL
        for task in tasks:
            for track in task.tracks:
                sample_seq += 1
                sample_id = f"{campaign_id}-{model.backend}-{model.model}-{task.task_id}-{track}"
                sample_id = sample_id.replace("/", "-").replace(":", "-").replace(" ", "-")

                override = (
                    _FIXTURE_FAST_UNRELIABLE_OVERRIDES.get(task.task_id)
                    if is_fast_unreliable
                    else None
                )

                if is_fast_unreliable:
                    # Deliberately fast on every single attempt -- the whole
                    # point of this fixture is to prove that being fast is
                    # not enough: it must still be excluded by the hard
                    # acceptance gate once its outputs are not usable.
                    elapsed_seconds = round(rng.uniform(0.4, 1.2), 3)
                else:
                    elapsed_seconds = round(rng.uniform(1.5, 25.0), 3)
                prompt_tokens = rng.randint(50, 400)
                output_tokens = rng.randint(50, 800)
                total_tokens = prompt_tokens + output_tokens
                tokens_per_second = (
                    None if is_copilot else round(output_tokens / elapsed_seconds, 2)
                )

                if override is not None:
                    deterministic_score = override.get("deterministic_score")
                    reviewer_score = override.get("reviewer_score")
                    collaboration_score = override.get("collaboration_score")
                    unsafe_behavior_flag = bool(override.get("unsafe_behavior_flag", False))
                    output_placeholder_or_incomplete = bool(
                        override.get("output_placeholder_or_incomplete", False)
                    )
                    override_note = str(override.get("note", ""))
                    status = "success"
                else:
                    deterministic_score = (
                        round(rng.uniform(30.0, 100.0), 2)
                        if "deterministic_tests" in task.gates
                        else None
                    )
                    reviewer_score = (
                        round(rng.uniform(40.0, 100.0), 2) if "reviewer" in task.gates else None
                    )
                    collaboration_score = (
                        round(rng.uniform(50.0, 100.0), 2)
                        if "collaboration" in task.gates
                        else None
                    )
                    status = "success" if rng.random() > 0.08 else "error"
                    # Explicitly checked and clear for every non-error
                    # baseline sample -- this synthetic reviewer *did*
                    # evaluate for unsafe/placeholder output and found
                    # nothing, it is not simply "unknown".
                    unsafe_behavior_flag = False if status == "success" else None
                    output_placeholder_or_incomplete = False if status == "success" else None
                    override_note = None

                system_error_flag = status == "error"
                system_error_message = (
                    "synthetic dry-run error for pipeline validation" if system_error_flag else None
                )

                phase_fields = _synthetic_phase_timing(
                    rng,
                    task=task,
                    model=model,
                    total_wall_seconds=elapsed_seconds,
                    is_copilot=is_copilot,
                    is_fast_unreliable=is_fast_unreliable,
                )

                acceptance_status, acceptance_reasons = rubric.compute_sample_acceptance(
                    status=status,
                    deterministic_score=deterministic_score,
                    reviewer_score=reviewer_score,
                    unsafe_behavior_flag=unsafe_behavior_flag,
                    output_placeholder_or_incomplete=output_placeholder_or_incomplete,
                )

                content = (
                    f"Synthetic dry-run artifact\ncampaign={campaign_id}\nrun={run_id}\n"
                    f"task={task.task_id}\ntrack={track}\nmodel={model.backend}/{model.model}\n"
                )
                artifact_path, artifact_hash = _write_artifact(artifacts_dir, sample_id, content)

                now = utc_now_iso()
                samples.append(
                    SampleRecord(
                        schema_version=SCHEMA_VERSION,
                        campaign_id=campaign_id,
                        run_id=run_id,
                        sample_id=sample_id,
                        benchmark_set=benchmark_set,
                        benchmark_version=benchmark_version,
                        track=track,
                        task_id=task.task_id,
                        task_name=task.name,
                        task_category=task.category,
                        provider=model.provider,
                        backend=model.backend,
                        model=model.model,
                        runtime=model.runtime,
                        quantization=model.quantization,
                        start_time=now,
                        end_time=now,
                        elapsed_seconds=elapsed_seconds,
                        prompt_tokens=prompt_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                        tokens_per_second=tokens_per_second,
                        ttft_seconds=None if is_copilot else round(rng.uniform(0.05, 1.5), 3),
                        cpu_time_seconds=None if is_copilot else round(rng.uniform(0.5, 20.0), 3),
                        cpu_avg_percent=None if is_copilot else round(rng.uniform(10.0, 90.0), 1),
                        cpu_max_percent=None if is_copilot else round(rng.uniform(20.0, 100.0), 1),
                        ram_avg_mb=None if is_copilot else round(rng.uniform(2000.0, 16000.0), 1),
                        ram_max_mb=None if is_copilot else round(rng.uniform(3000.0, 20000.0), 1),
                        gpu_avg_percent=None if (is_copilot or model.backend == "siemens_api") else round(rng.uniform(10.0, 95.0), 1),
                        gpu_max_percent=None if (is_copilot or model.backend == "siemens_api") else round(rng.uniform(20.0, 100.0), 1),
                        vram_avg_mb=None if (is_copilot or model.backend == "siemens_api") else round(rng.uniform(2000.0, 11000.0), 1),
                        vram_max_mb=None if (is_copilot or model.backend == "siemens_api") else round(rng.uniform(3000.0, 12000.0), 1),
                        deterministic_score=deterministic_score,
                        reviewer_score=reviewer_score,
                        collaboration_score=collaboration_score,
                        composite_score=None,
                        status=status,
                        system_error_flag=system_error_flag,
                        system_error_code="SYNTH-0001" if system_error_flag else None,
                        system_error_message=system_error_message,
                        retry_count=0,
                        iteration_index=1,
                        artifact_path=artifact_path,
                        artifact_hash=artifact_hash,
                        reviewer_provenance="synthetic-dry-run-fixture",
                        reviewer_notes="Synthetic dry-run sample; no model was called.",
                        hardware_profile=None if is_copilot else "Balanced",
                        hardware_gpu_model=None if (is_copilot or model.backend == "siemens_api") else "RTX 3500 Ada 12GB",
                        hardware_vram_total_mb=None if (is_copilot or model.backend == "siemens_api") else 12288.0,
                        sample_seq=sample_seq,
                        provenance="synthetic_dry_run",
                        notes=override_note,
                        unsafe_behavior_flag=unsafe_behavior_flag,
                        output_placeholder_or_incomplete=output_placeholder_or_incomplete,
                        acceptance_status=acceptance_status,
                        acceptance_reasons=acceptance_reasons,
                        **phase_fields,
                    )
                )
    return samples


def _synthetic_phase_timing(
    rng: random.Random,
    *,
    task: catalog.TaskSpec,
    model: DryRunFixtureModel,
    total_wall_seconds: float,
    is_copilot: bool,
    is_fast_unreliable: bool,
) -> dict:
    """Deterministically synthesize the raw phase-timing fields for one
    synthetic sample (seeded RNG only -- no wall-clock timing, no real
    process).

    GitHub Copilot task-agent references cannot reliably expose internal
    model/queue/generation timings, so every phase-timing field is left
    ``None`` (N/A) for ``is_copilot`` rather than estimated -- matching the
    same "never invent unavailable Copilot metrics" rule already applied to
    tokens/sec elsewhere in this module.

    For local (``ollama``/``llama_cpp``) backends the LLM bucket is
    reported at the *granular* level (``prompt_eval_seconds`` +
    ``generation_seconds``) since that split is normally observable from a
    local runtime. For opaque backends (``siemens_api``) only the
    black-box ``llm_request_seconds`` wall/service time is reported -- the
    internal prompt-eval/generation split is not observable through a
    remote HTTP API, so those two fields stay ``None`` (this also
    exercises the "prefer llm_request_seconds over the granular sum"
    precedence rule in :mod:`agent_helper_eval.phase_timing`).

    The bucket seconds are constructed to sum to ``total_wall_seconds``
    exactly (before any deliberate ``overlap_seconds``), so every produced
    sample passes the schema's overlap-honesty check by construction.
    """

    if is_copilot:
        return {
            "total_wall_seconds": None,
            "llm_queue_seconds": None,
            "llm_request_seconds": None,
            "prompt_eval_seconds": None,
            "generation_seconds": None,
            "local_tool_exec_seconds": None,
            "test_exec_seconds": None,
            "orchestrator_review_seconds": None,
            "idle_wait_seconds": None,
            "overlap_seconds": None,
        }

    has_tests = "deterministic_tests" in task.gates
    has_reviewer = "reviewer" in task.gates
    has_collaboration = "collaboration" in task.gates

    # Small local queue wait (e.g. waiting for the "max one local model"
    # lock to be free) -- kept tiny relative to total wall time.
    queue_seconds = round(min(0.3, total_wall_seconds * 0.05) * rng.uniform(0.0, 1.0), 3)
    remaining = max(total_wall_seconds - queue_seconds, 0.0)

    test_seconds = round(remaining * rng.uniform(0.05, 0.15), 3) if has_tests else 0.0
    review_seconds = round(remaining * rng.uniform(0.03, 0.10), 3) if has_reviewer else 0.0
    tool_seconds = round(remaining * rng.uniform(0.02, 0.08), 3) if has_collaboration else 0.0
    remaining_after_overhead = max(remaining - test_seconds - review_seconds - tool_seconds, 0.0)

    if model.backend == "siemens_api":
        # Opaque remote API: only the black-box request wall/service time
        # is observable, not an internal prompt-eval/generation split.
        llm_request_seconds = round(remaining_after_overhead, 3)
        prompt_eval_seconds = None
        generation_seconds = None
        llm_component = llm_request_seconds
    else:
        # Local runtime: the prompt-eval/generation split is normally
        # observable, so no top-level llm_request_seconds is reported --
        # this exercises the "fall back to the granular sum" precedence
        # path in agent_helper_eval.phase_timing.llm_bucket_seconds().
        llm_request_seconds = None
        prompt_fraction = rng.uniform(0.2, 0.45)
        prompt_eval_seconds = round(remaining_after_overhead * prompt_fraction, 3)
        generation_seconds = round(remaining_after_overhead - prompt_eval_seconds, 3)
        llm_component = prompt_eval_seconds + generation_seconds

    idle_wait_seconds = round(max(remaining_after_overhead - llm_component, 0.0), 3)

    # Deliberately demonstrate the overlap diagnostic on the multi-turn
    # collaboration task (tool execution can legitimately overlap with
    # orchestrator review while awaiting the next turn) -- but never for
    # the fast-unreliable fixture, whose whole point is to be excluded by
    # the hard acceptance gate on quality grounds alone, not on timing.
    overlap_seconds = 0.0
    if has_collaboration and not is_fast_unreliable and tool_seconds and review_seconds:
        overlap_seconds = round(min(tool_seconds, review_seconds) * 0.4, 3)

    return {
        "total_wall_seconds": total_wall_seconds,
        "llm_queue_seconds": queue_seconds,
        "llm_request_seconds": llm_request_seconds,
        "prompt_eval_seconds": prompt_eval_seconds,
        "generation_seconds": generation_seconds,
        "local_tool_exec_seconds": tool_seconds or None,
        "test_exec_seconds": test_seconds or None,
        "orchestrator_review_seconds": review_seconds or None,
        "idle_wait_seconds": idle_wait_seconds or None,
        "overlap_seconds": overlap_seconds or None,
    }


def generate_synthetic_capacity_profile(
    campaign_id: str,
    run_id: str,
    aggregates: Sequence[AggregateRecord],
    seed: int = 1234,
) -> list[CapacityProfileRecord]:
    """Deterministically synthesize a small post-quality-gate concurrency/
    capacity-profile demonstration (concurrency levels 1/2/4) for exactly
    one already hard-gate-accepted local model from the dry-run fixture set.

    This never issues a real concurrent request, never starts a model, and
    never touches Ollama/llama.cpp/network -- it exists purely to prove,
    end-to-end and without any model call, that:

    - the mandatory preflight gate
      (:func:`agent_helper_eval.capacity_profile.can_start_concurrency_profile`)
      is actually invoked, and would actually block profiling for a model
      that failed the hard acceptance gate (see the assertion below, which
      is only safe because ``target`` was already filtered to
      ``not hard_gate_failed``);
    - :class:`agent_helper_eval.schema.CapacityProfileRecord` construction,
      SQLite storage, CSV export, and report rendering all work correctly
      for a realistic "diminishing returns" concurrency-scaling shape -- the
      common real-world case on a single 12 GB VRAM GPU: close to linear at
      concurrency=2, clearly sub-linear at concurrency=4.

    Returns an empty list (not a fabricated one) if no local (``ollama``/
    ``llama_cpp``) aggregate passed the hard acceptance gate in this
    campaign -- there is deliberately no fallback to a Siemens API or
    Copilot backend here, since the whole point of capacity profiling is
    "more concurrent requests against the SAME local model", which only
    applies to local backends.
    """

    target = next(
        (
            a
            for a in aggregates
            if a.backend in ("ollama", "llama_cpp") and not a.hard_gate_failed
        ),
        None,
    )
    if target is None:
        return []

    rng = random.Random(seed + 1)
    baseline_tps = target.tokens_per_second_mean or 20.0

    # Deliberately synthesize a "diminishing returns" scaling shape rather
    # than a hard-coded optimistic one: ~96% efficient at concurrency=2,
    # clearly sub-linear (~65% efficient) at concurrency=4 -- so the
    # classification logic is genuinely exercised rather than trivially
    # always returning "scales-well".
    scaling_factor_by_level = {1: 1.0, 2: 1.92, 4: 2.6}
    vram_headroom_by_level = {1: 5000.0, 2: 3200.0, 4: 900.0}

    now = utc_now_iso()
    records: list[CapacityProfileRecord] = []
    for level in CONCURRENCY_LEVELS:
        preflight = capacity_profile.can_start_concurrency_profile(
            single_request_accepted=True,
            single_request_hard_gate_failed=target.hard_gate_failed,
            memory_safety_checked=True,
            vram_headroom_mb=vram_headroom_by_level[level],
        )
        if not preflight.allowed:
            # Never silently skip in a way that could be mistaken for
            # "profiled and fine" -- if the preflight gate ever blocks a
            # level, stop staging higher levels entirely (matches the
            # catalog's requires_prior_level ordering).
            break

        aggregate_tps = round(baseline_tps * scaling_factor_by_level[level], 2)
        per_request_tps = round(aggregate_tps / level, 2)
        efficiency = capacity_profile.compute_throughput_efficiency_percent(
            aggregate_tps, level, baseline_tps
        )
        classification = capacity_profile.classify_concurrency_scaling(efficiency)

        records.append(
            CapacityProfileRecord(
                schema_version=SCHEMA_VERSION,
                campaign_id=campaign_id,
                run_id=run_id,
                benchmark_set=target.benchmark_set,
                backend=target.backend,
                model=target.model,
                runtime=target.runtime,
                quantization=target.quantization,
                concurrency_level=level,
                requests_issued=level,
                aggregate_tokens_per_second=aggregate_tps,
                per_request_tokens_per_second_mean=per_request_tps,
                per_request_tokens_per_second_p50=per_request_tps,
                per_request_tokens_per_second_p95=round(
                    per_request_tps * rng.uniform(0.85, 0.98), 2
                ),
                queue_seconds_mean=round(0.05 * (level - 1), 3),
                service_seconds_mean=(
                    round(1.0 / per_request_tps, 4) if per_request_tps else None
                ),
                latency_seconds_p95=round(rng.uniform(1.0, 3.0) * (level ** 0.5), 3),
                gpu_avg_percent=min(100.0, round(58.0 + 10.0 * level, 1)),
                gpu_max_percent=min(100.0, round(72.0 + 7.0 * level, 1)),
                vram_avg_mb=round(7000.0 + 900.0 * level, 1),
                vram_max_mb=round(7500.0 + 1100.0 * level, 1),
                ram_avg_mb=round(4000.0 + 300.0 * level, 1),
                ram_max_mb=round(4500.0 + 400.0 * level, 1),
                error_count=0,
                timeout_count=0,
                time_to_accepted_result_seconds=(
                    round(10.0 * baseline_tps / aggregate_tps, 3) if aggregate_tps else None
                ),
                single_request_baseline_tokens_per_second=baseline_tps,
                throughput_efficiency_percent=efficiency,
                scaling_classification=classification,
                preflight_quality_gate_passed=True,
                preflight_memory_safety_checked=True,
                vram_budget_mb=capacity_profile.DEFAULT_VRAM_BUDGET_MB,
                vram_headroom_mb=vram_headroom_by_level[level],
                notes=(
                    "Synthetic dry-run capacity-profile demonstration; no "
                    "concurrent request was actually issued."
                ),
                provenance="synthetic_dry_run",
                generated_at=now,
            )
        )
    return records



@dataclasses.dataclass
class DryRunResult:
    campaign_id: str
    output_dir: Path
    db_path: Path
    samples_csv: Path
    aggregates_csv: Path
    capacity_profile_csv: Path
    report_path: Path
    sample_count: int
    aggregate_count: int
    capacity_profile_count: int


def run_dry_run(
    repo_root: Path,
    campaign_id: str,
    seed: int = 1234,
    tasks: Sequence[catalog.TaskSpec] = tuple(catalog.DEFAULT_CATALOG),
    models: Sequence[DryRunFixtureModel] = DEFAULT_DRY_RUN_MODELS,
    benchmark_set: str = "agent-helper-catalog-v1",
    benchmark_version: str = catalog.CATALOG_SCHEMA_VERSION,
) -> DryRunResult:
    """Run an end-to-end, no-model dry-run of the whole harness pipeline.

    Writes an isolated campaign directory under
    ``benchmark_results/agent-helper/<campaign_id>/`` containing the SQLite
    SSOT, all three canonical CSV exports (samples, aggregates, and the
    post-quality-gate capacity/concurrency profile), synthetic artifact
    files, and a local HTML report; then regenerates the combined
    multi-campaign report at ``benchmark_results/agent-helper/report.html``.

    ``benchmark_set``/``benchmark_version`` identify which catalog ``tasks``
    came from and must be passed explicitly whenever ``tasks`` is not the
    bundled default catalog (for example when the caller loaded ``tasks``
    from an externally authored catalog file via
    ``catalog.load_catalog_document()`` -- see
    ``scripts/run_agent_helper_campaign.py``'s ``--catalog`` option).
    """

    output_dir = campaign_output_dir(repo_root, campaign_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"dryrun-{campaign_id}-{seed}"

    samples = generate_synthetic_samples(
        campaign_id,
        run_id,
        output_dir,
        tasks=tasks,
        models=models,
        seed=seed,
        benchmark_set=benchmark_set,
        benchmark_version=benchmark_version,
    )

    db_path = output_dir / storage.DB_FILENAME
    conn = storage.connect(db_path)
    try:
        storage.insert_samples(conn, samples)
        aggregates = aggregate.build_all_aggregates(samples, provenance="synthetic_dry_run")
        storage.insert_aggregates(conn, aggregates)

        capacity_profiles = generate_synthetic_capacity_profile(
            campaign_id, run_id, aggregates, seed=seed
        )
        storage.insert_capacity_profiles(conn, capacity_profiles)

        samples_csv = output_dir / storage.SAMPLES_CSV_FILENAME
        aggregates_csv = output_dir / storage.AGGREGATES_CSV_FILENAME
        capacity_profile_csv = output_dir / storage.CAPACITY_PROFILE_CSV_FILENAME
        storage.export_samples_csv(conn, samples_csv, campaign_id=campaign_id)
        storage.export_aggregates_csv(conn, aggregates_csv, campaign_id=campaign_id)
        storage.export_capacity_profile_csv(conn, capacity_profile_csv, campaign_id=campaign_id)

        stored_samples = storage.fetch_samples(conn, campaign_id=campaign_id)
        stored_aggregates = storage.fetch_aggregates(conn, campaign_id=campaign_id)
        stored_capacity_profiles = storage.fetch_capacity_profiles(conn, campaign_id=campaign_id)
    finally:
        conn.close()

    local_report_path = output_dir / "report.html"
    local_report_html = render_report(
        [
            CampaignReportData(
                campaign_id=campaign_id,
                generated_at=utc_now_iso(),
                title=f"{campaign_id} (dry-run, synthetic data only)",
                aggregates=stored_aggregates,
                samples=stored_samples,
                capacity_profiles=stored_capacity_profiles,
            )
        ],
        model_inventory=load_default_model_inventory(repo_root),
    )
    local_report_path.write_text(local_report_html, encoding="utf-8")

    combined_report_path = build_combined_report(repo_root)

    return DryRunResult(
        campaign_id=campaign_id,
        output_dir=output_dir,
        db_path=db_path,
        samples_csv=samples_csv,
        aggregates_csv=aggregates_csv,
        capacity_profile_csv=capacity_profile_csv,
        report_path=combined_report_path or local_report_path,
        sample_count=len(stored_samples),
        aggregate_count=len(stored_aggregates),
        capacity_profile_count=len(stored_capacity_profiles),
    )


def build_combined_report(repo_root: Path) -> Optional[Path]:
    """Rebuild the combined multi-campaign report from every campaign directory
    found under ``benchmark_results/agent-helper/``.

    Returns the report path, or ``None`` if no campaign directories with a
    database exist yet.
    """

    root = agent_helper_root(repo_root)
    if not root.exists():
        return None

    campaigns: list[CampaignReportData] = []
    for candidate in sorted(root.iterdir()):
        db_path = candidate / storage.DB_FILENAME
        if not candidate.is_dir() or not db_path.exists():
            continue
        conn = storage.connect(db_path)
        try:
            campaign_samples = storage.fetch_samples(conn)
            campaign_aggregates = storage.fetch_aggregates(conn)
            campaign_capacity_profiles = storage.fetch_capacity_profiles(conn)
        finally:
            conn.close()
        if not campaign_samples and not campaign_aggregates:
            continue
        campaign_id = campaign_samples[0].campaign_id if campaign_samples else candidate.name
        generated_at = (
            max((a.generated_at for a in campaign_aggregates), default=utc_now_iso())
        )
        campaigns.append(
            CampaignReportData(
                campaign_id=campaign_id,
                generated_at=generated_at,
                title=candidate.name,
                aggregates=campaign_aggregates,
                samples=campaign_samples,
                capacity_profiles=campaign_capacity_profiles,
            )
        )

    if not campaigns:
        return None

    report_path = root / "report.html"
    report_path.write_text(
        render_report(campaigns, model_inventory=load_default_model_inventory(repo_root)),
        encoding="utf-8",
    )
    return report_path


# ---------------------------------------------------------------------------
# 2. Connect-gate planning (text only, never executed here)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ConnectGateCheck:
    backend: str
    model: str
    command: str
    description: str
    pass_criteria: str


def build_connect_gate_plan(models: Sequence[ModelSpec]) -> list[ConnectGateCheck]:
    """Build the exact, human-reviewable connect-gate command for each model.

    Returns data only; nothing here executes a command. See
    ``docs/operations/runbook.md`` for the reviewed, safe copy-paste form of
    these commands.
    """

    plan: list[ConnectGateCheck] = []
    for spec in models:
        if spec.backend == "ollama":
            tag = spec.model_id
            plan.append(
                ConnectGateCheck(
                    backend=spec.backend,
                    model=spec.model_id,
                    command=f"ollama show {shlex.quote(tag)}",
                    description="Confirms the tag is installed locally and Ollama responds, without generating text.",
                    pass_criteria="Exit code 0 and model metadata printed.",
                )
            )
        elif spec.backend == "llama_cpp":
            plan.append(
                ConnectGateCheck(
                    backend=spec.backend,
                    model=spec.model_id,
                    command=(
                        "Start llama-server for this one model (respecting the "
                        "max-one-local-model lock), then: "
                        "curl -s http://127.0.0.1:8080/health"
                    ),
                    description="Confirms the llama-server process is up and healthy before any generation request.",
                    pass_criteria="HTTP 200 with a healthy/ready status body.",
                )
            )
        elif spec.backend == "siemens_api":
            plan.append(
                ConnectGateCheck(
                    backend=spec.backend,
                    model=spec.model_id,
                    command=(
                        "Read the first 'SIAK-' line from the local Siemens token file "
                        "(never paste it into a command), then: "
                        "curl -s -H \"Authorization: Bearer $TOKEN\" "
                        "https://api.siemens.com/llm/v1/models"
                    ),
                    description="Confirms the Siemens endpoint is reachable and the model is listed, without a chat completion.",
                    pass_criteria="HTTP 200 and the model id appears in the returned list.",
                )
            )
        elif spec.backend == "copilot_agent":
            plan.append(
                ConnectGateCheck(
                    backend=spec.backend,
                    model=spec.model_id,
                    command="gh copilot --version",
                    description="Confirms the GitHub Copilot CLI/agent is installed and authenticated, without starting a task.",
                    pass_criteria="Exit code 0 and a version string printed.",
                )
            )
        else:
            plan.append(
                ConnectGateCheck(
                    backend=spec.backend,
                    model=spec.model_id,
                    command="# unknown backend: no connect-gate command defined",
                    description="This backend is not yet covered by the connect-gate plan.",
                    pass_criteria="n/a",
                )
            )
    return plan


# ---------------------------------------------------------------------------
# 3. Connect-gate execution (executor injected; nothing runs by itself)
# ---------------------------------------------------------------------------

#: (returncode, stdout, stderr)
ExecutorResult = tuple[int, str, str]
Executor = Callable[[str], ExecutorResult]


def subprocess_executor(command: str, timeout_seconds: int = 30) -> ExecutorResult:
    """Real executor: runs ``command`` via the shell with a timeout.

    Not called anywhere in this module or its tests. A caller (the parent
    agent, explicitly, for one model at a time, respecting the local-model
    lock) wires this in when it actually wants to run a connect gate.
    """

    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"timeout after {timeout_seconds}s: {exc}"


@dataclasses.dataclass
class GateResult:
    check: ConnectGateCheck
    outcome: str  # one of catalog.GATE_OUTCOMES
    detail: str


def run_connect_gate_check(
    check: ConnectGateCheck, executor: Executor, timeout_seconds: int = 30
) -> GateResult:
    """Execute one connect-gate check via the injected ``executor``.

    Returns ``"pass"`` for return code 0, ``"timeout"`` for return code 124
    (matching :func:`subprocess_executor`'s timeout convention), and
    ``"fail"`` otherwise. Never called with :func:`subprocess_executor` from
    within this codebase's own tests or dry-run flow.
    """

    returncode, stdout, stderr = executor(check.command)
    if returncode == 0:
        outcome = "pass"
        detail = stdout.strip()[-500:]
    elif returncode == 124:
        outcome = "timeout"
        detail = stderr.strip()[-500:]
    else:
        outcome = "fail"
        detail = (stderr or stdout).strip()[-500:]
    return GateResult(check=check, outcome=outcome, detail=detail)
