"""Serial, one-model-at-a-time gate-campaign planning and execution.

This module never runs a model itself just by being imported. Two
independent phases are provided:

1. **Planning** (:func:`build_serial_plan`): a pure function over an
   already-captured :class:`~agent_helper_eval.ollama_inventory.DiscoveryResult`
   snapshot. Always deterministic, always dry -- it only decides which
   discovered tags would be *included* (in a fixed order) versus *deferred*
   (with a mandatory, human-readable reason), never executes anything.
2. **Execution** (:func:`run_serial_campaign`): iterates ``plan.included`` in
   strict list order, calling exactly one connect gate then (only if the
   connect gate was accepted) exactly one mini gate per model, always
   sequentially -- there is no threading/async in this function, so overlap
   between two model attempts is structurally impossible, not just avoided
   by convention. Real execution only ever happens when the caller passes
   ``confirm=True`` *and* explicitly injects (or accepts the default) real
   gate callables; every unit test in this package injects fakes instead.

Design choices that intentionally mirror the harness's established
one-shot-gate philosophy (see :mod:`agent_helper_eval.live_gates`):

- **Resume never re-attempts a model/task pair that already has ANY
  persisted sample** (success, scored-rejected, error, or timeout) unless
  ``force_rerun`` explicitly names it. A connect/mini gate is one-shot by
  design; an implicit "retry because it failed last time" resume would
  contradict "repeated fast failures must score worse than one slower
  accepted pass" (see docs/project/agent_helper_benchmark.md, hard
  acceptance gate).
- **Retries are narrow**: only a raised, transient ``status == "error"``
  sample is eligible for ``max_retries`` (default ``0``). A genuinely scored
  rejection (deterministic tests failed) or a timeout is never auto-retried
  -- hammering a model that produced a wrong answer does not change the
  answer's quality.
- **A preflight/lock refusal (`LiveGateRefusedError`) halts the whole
  campaign by default** (`halt_on_preflight_refusal=True`): an unexpected
  already-loaded model or a stuck lock file is very likely to recur for
  every subsequent model too and warrants operator attention rather than
  silent skip-and-continue.
- **A gate function's own gate-boundary exception safety net (see
  :mod:`agent_helper_eval.live_gates`, ``GATE_BOUNDARY_EXCEPTION_ERROR_CODE``)
  never crashes this loop.** ``run_ollama_connect_gate``/
  ``run_ollama_mini_gate`` always return a normal
  ``LiveGateResult`` -- even when an unexpected subprocess/workspace/
  scoring exception happened after the real HTTP attempt -- with a
  persisted ``status="error"``/``acceptance_status="not_usable"`` sample.
  By default (``halt_on_gate_exception=False``) the campaign simply moves
  on to the next model, since a single model's workspace hiccup does not
  imply every other model will fail the same way; passing
  ``halt_on_gate_exception=True`` stops the campaign early instead (the
  failed sample and checkpoint are persisted identically either way -- this
  flag only decides whether to keep going).
- **A checkpoint is written both immediately before a gate call starts and
  immediately after its outcome is known** -- so a hard crash or
  ``kill -9`` mid-attempt always leaves a checkpoint on disk showing
  exactly which (model, gate) step was in flight (``in_progress``), not
  just the already-completed steps.
- **Nothing here fabricates a `SampleRecord` for a refusal or a skip.**
  Those are recorded only in the side-channel, atomically-written JSON
  checkpoint file (see :data:`SERIAL_PROGRESS_FILENAME`) -- the canonical
  SQLite/CSV schema only ever contains genuinely attempted rows, exactly as
  every other part of this harness already guarantees.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import live_gates, ollama_client, orchestrator, storage
from .ollama_inventory import DiscoveredOllamaModel, DiscoveryResult
from .schema import SampleRecord, utc_now_iso

SERIAL_PLAN_SCHEMA_VERSION = "agent-helper-serial-plan-v1"
SERIAL_PROGRESS_FILENAME = "serial_progress.json"
SERIAL_PLAN_FILENAME = "serial_plan.json"

#: Gate task IDs used purely to look up "was this (model, task) already
#: attempted" for resume -- re-exported here so callers need only import
#: this module, not also ``live_gates``.
CONNECT_GATE_TASK_ID = live_gates.CONNECT_GATE_TASK_ID
MINI_GATE_TASK_ID = live_gates.MINI_GATE_TASK_ID

ConnectGateFn = Callable[..., "live_gates.LiveGateResult"]
MiniGateFn = Callable[..., "live_gates.LiveGateResult"]
SleepFn = Callable[[float], None]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SerialPlanEntry:
    """One model the plan intends to run the connect+mini gates against, in
    the exact order it will be attempted."""

    order: int
    tag: str
    size_gb: Optional[float]
    architecture: str
    quantization: Optional[str]


@dataclasses.dataclass
class DeferredModelEntry:
    """One discovered model the plan will NOT run, with a mandatory,
    human-readable reason -- every discovered model appears in either
    ``SerialPlan.included`` or ``SerialPlan.deferred``, never silently
    dropped from view."""

    tag: str
    reason: str


@dataclasses.dataclass
class SerialPlan:
    schema_version: str
    generated_at: str
    campaign_id: str
    selection_mode: str  # "explicit_models" | "filtered"
    included: list  # list[SerialPlanEntry]
    deferred: list  # list[DeferredModelEntry]

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "campaign_id": self.campaign_id,
            "selection_mode": self.selection_mode,
            "included": [dataclasses.asdict(entry) for entry in self.included],
            "deferred": [dataclasses.asdict(entry) for entry in self.deferred],
        }


def _size_gb(model: DiscoveredOllamaModel) -> Optional[float]:
    return model.size_bytes / 1_000_000_000.0 if model.size_bytes is not None else None


def build_serial_plan(
    discovery: DiscoveryResult,
    campaign_id: str,
    *,
    generated_at: str,
    models: Optional[Sequence[str]] = None,
    include_cloud: bool = False,
    max_size_gb: Optional[float] = None,
    include_architectures: Optional[Sequence[str]] = None,
    exclude_architectures: Optional[Sequence[str]] = None,
    include_quantizations: Optional[Sequence[str]] = None,
    exclude_quantizations: Optional[Sequence[str]] = None,
    force_include: Optional[Sequence[str]] = None,
) -> SerialPlan:
    """Build a dry, human-reviewable serial execution plan.

    Two mutually exclusive selection modes:

    - ``models`` given (explicit list): exactly those tags, in the given
      order, become ``included`` (each must exist in ``discovery.models``,
      or it is recorded in ``deferred`` with an "not found in discovery
      snapshot" reason instead of silently failing). Every other discovered
      tag is recorded in ``deferred`` with an explicit
      "not selected (explicit --models list used)" reason -- transparency
      about non-selection is mandatory even in explicit mode.
    - ``models`` omitted (filter mode): every discovered tag is evaluated
      against the cloud/size/architecture/quantization filters, in
      discovery order. ``force_include`` bypasses an exclusion but the
      original "would have been excluded because ..." reason is preserved
      in the entry's notes so the operator can see it was a deliberate
      override, not an oversight.

    This function never executes anything; it only classifies.
    """

    force_include_set = {tag for tag in (force_include or ())}
    include_architectures_set = (
        {a.lower() for a in include_architectures} if include_architectures else None
    )
    exclude_architectures_set = {a.lower() for a in (exclude_architectures or ())}
    include_quantizations_set = (
        {q.lower() for q in include_quantizations} if include_quantizations else None
    )
    exclude_quantizations_set = {q.lower() for q in (exclude_quantizations or ())}

    by_tag = {model.tag: model for model in discovery.models}
    included: list[SerialPlanEntry] = []
    deferred: list[DeferredModelEntry] = []

    if models is not None:
        selection_mode = "explicit_models"
        selected_set = set(models)
        order = 0
        for tag in models:
            model = by_tag.get(tag)
            if model is None:
                deferred.append(
                    DeferredModelEntry(tag=tag, reason="not found in discovery snapshot")
                )
                continue
            included.append(
                SerialPlanEntry(
                    order=order,
                    tag=model.tag,
                    size_gb=_size_gb(model),
                    architecture=model.architecture,
                    quantization=model.quantization,
                )
            )
            order += 1
        for model in discovery.models:
            if model.tag not in selected_set:
                deferred.append(
                    DeferredModelEntry(
                        tag=model.tag,
                        reason="not selected (explicit --models list used; every other discovered "
                        "tag is deferred for full transparency)",
                    )
                )
        return SerialPlan(
            schema_version=SERIAL_PLAN_SCHEMA_VERSION,
            generated_at=generated_at,
            campaign_id=campaign_id,
            selection_mode=selection_mode,
            included=included,
            deferred=deferred,
        )

    selection_mode = "filtered"
    order = 0
    for model in discovery.models:
        exclusion_reasons: list[str] = []
        if model.is_cloud and not include_cloud:
            exclusion_reasons.append(f"cloud tag excluded by default ({model.cloud_evidence})")
        size_gb = _size_gb(model)
        if max_size_gb is not None and size_gb is not None and size_gb > max_size_gb:
            exclusion_reasons.append(f"size {size_gb:.1f} GB exceeds --max-size-gb {max_size_gb:.1f}")
        if include_architectures_set is not None and model.architecture.lower() not in include_architectures_set:
            exclusion_reasons.append(
                f"architecture {model.architecture!r} not in --include-architecture {sorted(include_architectures_set)}"
            )
        if model.architecture.lower() in exclude_architectures_set:
            exclusion_reasons.append(f"architecture {model.architecture!r} excluded by --exclude-architecture")
        quant = (model.quantization or "").lower()
        if include_quantizations_set is not None and quant not in include_quantizations_set:
            exclusion_reasons.append(
                f"quantization {model.quantization!r} not in --include-quantization {sorted(include_quantizations_set)}"
            )
        if quant in exclude_quantizations_set:
            exclusion_reasons.append(f"quantization {model.quantization!r} excluded by --exclude-quantization")

        if not exclusion_reasons:
            included.append(
                SerialPlanEntry(
                    order=order, tag=model.tag, size_gb=size_gb,
                    architecture=model.architecture, quantization=model.quantization,
                )
            )
            order += 1
        elif model.tag in force_include_set:
            included.append(
                SerialPlanEntry(
                    order=order, tag=model.tag, size_gb=size_gb,
                    architecture=model.architecture, quantization=model.quantization,
                )
            )
            order += 1
            deferred.append(
                DeferredModelEntry(
                    tag=model.tag,
                    reason=(
                        "force-included despite would-have-been-excluded reason(s): "
                        + "; ".join(exclusion_reasons)
                    ),
                )
            )
        else:
            deferred.append(DeferredModelEntry(tag=model.tag, reason="; ".join(exclusion_reasons)))

    return SerialPlan(
        schema_version=SERIAL_PLAN_SCHEMA_VERSION,
        generated_at=generated_at,
        campaign_id=campaign_id,
        selection_mode=selection_mode,
        included=included,
        deferred=deferred,
    )


def save_serial_plan(plan: SerialPlan, campaign_output_dir: Path) -> Path:
    campaign_output_dir.mkdir(parents=True, exist_ok=True)
    path = campaign_output_dir / SERIAL_PLAN_FILENAME
    path.write_text(json.dumps(plan.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ModelStepOutcome:
    """The outcome of one (model, gate) step, whether attempted, skipped, or
    refused. Exactly one of ``sample`` / ``skip_reason`` / ``refusal_reason``
    is set."""

    model: str
    gate: str  # "connect" | "mini"
    outcome: str  # SampleRecord.acceptance_status, or "skipped", "refused", "resumed"
    sample_id: Optional[str] = None
    skip_reason: Optional[str] = None
    refusal_reason: Optional[str] = None
    retries_used: int = 0
    #: ``SampleRecord.system_error_code`` of the persisted sample, when this
    #: outcome came from an actual attempt (never set for "skipped"/
    #: "refused"/"resumed"). Lets a caller distinguish an ordinary scored
    #: rejection from a :mod:`agent_helper_eval.live_gates` gate-boundary
    #: exception (``"GATE_BOUNDARY_UNEXPECTED_EXCEPTION"``, see
    #: ``halt_on_gate_exception`` on :func:`run_serial_campaign`).
    system_error_code: Optional[str] = None


@dataclasses.dataclass
class SerialCampaignResult:
    campaign_id: str
    executed: bool  # False whenever confirm=False (dry plan only)
    steps: list  # list[ModelStepOutcome]
    interrupted: bool = False
    halted_on_refusal: bool = False
    #: True only when ``halt_on_gate_exception=True`` was passed and a gate
    #: function's gate-boundary exception safety net actually fired for one
    #: of the attempted steps (see ``live_gates._build_gate_exception_sample``).
    #: The failed sample is still persisted either way; this only controls
    #: whether the *campaign* stops early or continues to the next model.
    halted_on_gate_exception: bool = False
    checkpoint_path: Optional[Path] = None


def _write_checkpoint(path: Path, payload: dict) -> None:
    """Atomic write-then-rename so a killed process never leaves a
    half-written, corrupt checkpoint behind."""

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _existing_sample_for(
    repo_root: Path, campaign_id: str, model: str, task_id: str
) -> Optional[SampleRecord]:
    """Best-effort lookup of any already-persisted sample for (model,
    task_id) in this campaign, used for resume-skip. Returns ``None`` (never
    raises) if the campaign database does not exist yet -- a brand-new
    campaign has nothing to resume from."""

    db_path = orchestrator.campaign_output_dir(repo_root, campaign_id) / storage.DB_FILENAME
    if not db_path.exists():
        return None
    conn = storage.connect(db_path)
    try:
        samples = [
            sample
            for sample in storage.fetch_samples(conn, campaign_id=campaign_id)
            if sample.model == model and sample.task_id == task_id
        ]
    finally:
        conn.close()
    if not samples:
        return None
    # Defensive: pick the most recent by start_time in case a future
    # correction/iteration command ever produces more than one row.
    samples.sort(key=lambda s: s.start_time)
    return samples[-1]


def run_serial_campaign(
    repo_root: Path,
    plan: SerialPlan,
    *,
    confirm: bool = False,
    resume: bool = True,
    force_rerun: Optional[Sequence[str]] = None,
    max_retries: int = 0,
    cooldown_seconds: float = 0.0,
    halt_on_preflight_refusal: bool = True,
    halt_on_gate_exception: bool = False,
    connect_gate_fn: ConnectGateFn = live_gates.run_ollama_connect_gate,
    mini_gate_fn: MiniGateFn = live_gates.run_ollama_mini_gate,
    sleep: SleepFn = time.sleep,
    connect_gate_kwargs: Optional[dict] = None,
    mini_gate_kwargs: Optional[dict] = None,
) -> SerialCampaignResult:
    """Execute ``plan.included`` strictly sequentially, one model fully at a
    time (connect gate, then -- only if accepted -- mini gate).

    ``confirm=False`` (the default) never calls a gate function at all; it
    only returns a ``SerialCampaignResult(executed=False, steps=[])`` so a
    caller can safely call this in "would-be-a-dry-run" contexts without an
    extra branch. Real execution requires ``confirm=True`` *and* a
    non-empty ``plan.included`` -- there is deliberately no way to run every
    discovered model "by accident".

    ``halt_on_gate_exception`` (default ``False``, i.e. *continue*):
    whenever a gate function's own gate-boundary exception safety net
    fires (an unexpected subprocess/workspace/scoring exception --
    ``SampleRecord.system_error_code ==
    live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE``), the failed sample is
    *always* persisted and checkpointed either way (the hard acceptance
    gate already scores it ``not_usable``, and resume will skip it next
    time) -- this flag only controls whether the whole campaign stops
    early (``True``, for an operator who wants to investigate before
    burning more model time) or safely moves on to the next model
    (``False``, the default: a single model's workspace/scoring hiccup
    should not by itself block every other model in the plan).
    """

    checkpoint_path = orchestrator.campaign_output_dir(repo_root, plan.campaign_id) / SERIAL_PROGRESS_FILENAME
    if not confirm:
        return SerialCampaignResult(
            campaign_id=plan.campaign_id, executed=False, steps=[], checkpoint_path=None
        )
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    force_rerun_set = set(force_rerun or ())
    connect_gate_kwargs = dict(connect_gate_kwargs or {})
    mini_gate_kwargs = dict(mini_gate_kwargs or {})

    steps: list[ModelStepOutcome] = []
    interrupted = False
    halted_on_refusal = False
    halted_on_gate_exception = False

    try:
        for index, entry in enumerate(plan.included):
            model = entry.tag
            force_this = model in force_rerun_set

            # --- connect gate -------------------------------------------------
            existing_connect = None if force_this else (
                _existing_sample_for(repo_root, plan.campaign_id, model, CONNECT_GATE_TASK_ID) if resume else None
            )
            if existing_connect is not None:
                connect_outcome = ModelStepOutcome(
                    model=model, gate="connect", outcome="resumed",
                    sample_id=existing_connect.sample_id,
                    skip_reason="already has a persisted connect-gate sample; resume skips it",
                )
                steps.append(connect_outcome)
                connect_accepted = existing_connect.acceptance_status == "accepted"
            else:
                # Checkpoint written *before* the gate call itself starts --
                # so a hard crash/kill mid-attempt still leaves a resumable,
                # inspectable "this model+gate was in flight" state on disk,
                # in addition to every already-completed step.
                _write_checkpoint(
                    checkpoint_path,
                    _checkpoint_payload(
                        plan, steps, interrupted=False, halted_on_refusal=halted_on_refusal,
                        in_progress={"model": model, "gate": "connect"},
                    ),
                )
                connect_outcome, connect_accepted = _run_one_gate_with_retry(
                    repo_root, plan.campaign_id, model, "connect", connect_gate_fn,
                    connect_gate_kwargs, max_retries,
                )
                steps.append(connect_outcome)
                if connect_outcome.outcome == "refused":
                    halted_on_refusal = True
                    if halt_on_preflight_refusal:
                        _write_checkpoint(checkpoint_path, _checkpoint_payload(plan, steps, interrupted=False, halted_on_refusal=True))
                        break
                    # Continuing past a refusal means we cannot know whether
                    # the connect gate would have been accepted, so the
                    # mini gate for this model must also be skipped.
                    steps.append(
                        ModelStepOutcome(
                            model=model, gate="mini", outcome="skipped",
                            skip_reason="connect gate was refused, mini gate skipped",
                        )
                    )
                    _write_checkpoint(checkpoint_path, _checkpoint_payload(plan, steps, interrupted=False, halted_on_refusal=True))
                    if cooldown_seconds > 0 and index < len(plan.included) - 1:
                        sleep(cooldown_seconds)
                    continue
                if (
                    halt_on_gate_exception
                    and connect_outcome.system_error_code == live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE
                ):
                    # The connect gate itself hit its gate-boundary
                    # exception safety net (see live_gates.py) -- the
                    # failed sample is already persisted, so this is
                    # purely a "stop the campaign for operator attention"
                    # decision, never a data-loss risk either way.
                    halted_on_gate_exception = True
                    _write_checkpoint(
                        checkpoint_path,
                        _checkpoint_payload(
                            plan, steps, interrupted=False, halted_on_refusal=halted_on_refusal,
                            halted_on_gate_exception=True,
                        ),
                    )
                    break

            # --- mini gate (only if connect was accepted) ---------------------
            if not connect_accepted:
                steps.append(
                    ModelStepOutcome(
                        model=model, gate="mini", outcome="skipped",
                        skip_reason="connect gate was not accepted, mini gate skipped",
                    )
                )
            else:
                existing_mini = None if force_this else (
                    _existing_sample_for(repo_root, plan.campaign_id, model, MINI_GATE_TASK_ID) if resume else None
                )
                if existing_mini is not None:
                    steps.append(
                        ModelStepOutcome(
                            model=model, gate="mini", outcome="resumed",
                            sample_id=existing_mini.sample_id,
                            skip_reason="already has a persisted mini-gate sample; resume skips it",
                        )
                    )
                else:
                    # Same pre-gate checkpoint as connect, above.
                    _write_checkpoint(
                        checkpoint_path,
                        _checkpoint_payload(
                            plan, steps, interrupted=False, halted_on_refusal=halted_on_refusal,
                            in_progress={"model": model, "gate": "mini"},
                        ),
                    )
                    mini_outcome, _mini_accepted = _run_one_gate_with_retry(
                        repo_root, plan.campaign_id, model, "mini", mini_gate_fn,
                        mini_gate_kwargs, max_retries,
                    )
                    steps.append(mini_outcome)
                    if mini_outcome.outcome == "refused":
                        halted_on_refusal = True
                        if halt_on_preflight_refusal:
                            _write_checkpoint(checkpoint_path, _checkpoint_payload(plan, steps, interrupted=False, halted_on_refusal=True))
                            break
                    elif (
                        halt_on_gate_exception
                        and mini_outcome.system_error_code == live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE
                    ):
                        halted_on_gate_exception = True
                        _write_checkpoint(
                            checkpoint_path,
                            _checkpoint_payload(
                                plan, steps, interrupted=False, halted_on_refusal=halted_on_refusal,
                                halted_on_gate_exception=True,
                            ),
                        )
                        break

            _write_checkpoint(
                checkpoint_path,
                _checkpoint_payload(
                    plan, steps, interrupted=False, halted_on_refusal=halted_on_refusal,
                    halted_on_gate_exception=halted_on_gate_exception,
                ),
            )
            if cooldown_seconds > 0 and index < len(plan.included) - 1:
                sleep(cooldown_seconds)
    except KeyboardInterrupt:
        interrupted = True
        _write_checkpoint(
            checkpoint_path,
            _checkpoint_payload(
                plan, steps, interrupted=True, halted_on_refusal=halted_on_refusal,
                halted_on_gate_exception=halted_on_gate_exception,
            ),
        )

    return SerialCampaignResult(
        campaign_id=plan.campaign_id,
        executed=True,
        steps=steps,
        interrupted=interrupted,
        halted_on_refusal=halted_on_refusal,
        halted_on_gate_exception=halted_on_gate_exception,
        checkpoint_path=checkpoint_path,
    )


def _checkpoint_payload(
    plan: SerialPlan,
    steps: Sequence[ModelStepOutcome],
    *,
    interrupted: bool,
    halted_on_refusal: bool,
    halted_on_gate_exception: bool = False,
    in_progress: Optional[dict] = None,
) -> dict:
    """``in_progress`` (``{"model": ..., "gate": "connect"|"mini"}``) marks a
    gate attempt that is *about to start* but has not yet produced an
    outcome -- written just before calling the gate function so a crash or
    ``kill -9`` mid-attempt still leaves a checkpoint on disk showing
    exactly which (model, gate) step was in flight, in addition to every
    already-completed step. It is always ``None`` once the corresponding
    outcome has been appended to ``steps``."""

    return {
        "campaign_id": plan.campaign_id,
        "written_at": utc_now_iso(),
        "interrupted": interrupted,
        "halted_on_refusal": halted_on_refusal,
        "halted_on_gate_exception": halted_on_gate_exception,
        "in_progress": in_progress,
        "steps": [dataclasses.asdict(step) for step in steps],
    }


def _run_one_gate_with_retry(
    repo_root: Path,
    campaign_id: str,
    model: str,
    gate: str,
    gate_fn: Callable[..., "live_gates.LiveGateResult"],
    gate_kwargs: dict,
    max_retries: int,
) -> tuple:
    attempts_used = 0
    while True:
        try:
            result = gate_fn(repo_root, campaign_id=campaign_id, model=model, **gate_kwargs)
        except live_gates.LiveGateRefusedError as exc:
            return (
                ModelStepOutcome(
                    model=model, gate=gate, outcome="refused",
                    refusal_reason=str(exc), retries_used=attempts_used,
                ),
                False,
            )
        sample = result.sample
        # Only a raised, classified-transient error is eligible for a
        # retry -- a scored rejection or a timeout is never auto-retried
        # (see module docstring: repeated fast failures must not be able to
        # mask a genuinely wrong/incomplete answer as "still working on
        # it").
        if sample.status == "error" and attempts_used < max_retries:
            attempts_used += 1
            continue
        outcome = ModelStepOutcome(
            model=model, gate=gate, outcome=sample.acceptance_status,
            sample_id=sample.sample_id, retries_used=attempts_used,
            system_error_code=sample.system_error_code,
        )
        return outcome, sample.acceptance_status == "accepted"
