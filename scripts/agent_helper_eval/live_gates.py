"""Real, one-model Ollama connect-gate and mini-gate execution.

This module is the only place in the agent-helper harness that is allowed to
actually reach a running Ollama server -- and even here, nothing runs
automatically: :func:`run_ollama_connect_gate`/:func:`run_ollama_mini_gate`
are only ever invoked by an explicit CLI command
(``scripts/run_agent_helper_campaign.py connect-gate-run`` /
``mini-gate-run``) naming exactly one ``--model`` and one ``--campaign-id``.
Nothing here iterates a model inventory or runs more than the one requested
attempt.

Execution sequence for both gates:

1. **Preflight** (:func:`ollama_client.ollama_ps` +
   :func:`ollama_client.check_single_model_preflight`): refuse to proceed if
   the Ollama daemon already has a *different* model loaded, or the same
   model loaded without ``allow_reuse_loaded_model=True`` explicitly passed.
   A preflight refusal raises before anything is attempted and persists
   **no** sample row -- nothing was actually run.
2. **Local lock** (:mod:`agent_helper_eval.local_lock`): acquire the
   filesystem "max one local model" lock so two of *our own* harness
   invocations can never overlap. Always released via the context manager.
3. **Real HTTP call** (:func:`ollama_client.ollama_generate`, streamed):
   deterministic options, bounded context/output, configurable timeout.
4. **Scoring**: connect-gate checks for the literal expected reply; mini-gate
   runs :func:`mini_task.run_mini_task_tests` (fixed unit tests, never a
   keyword match).
5. **Always unload** (:func:`ollama_client.ollama_unload`,
   ``keep_alive=0``) in a ``finally`` block, regardless of success/failure.
6. **Persist**: build one :class:`~agent_helper_eval.schema.SampleRecord`
   (``provenance="measured"``) through :func:`agent_helper_eval.rubric.compute_sample_acceptance`
   (the hard quality gate applies exactly like every other track) and write
   it to the SQLite SSOT + both canonical CSVs, then regenerate the local
   and combined HTML reports. Failed/timed-out attempts are persisted too
   (only a *preflight* refusal is not persisted, since nothing was
   attempted).
7. **Gate-boundary exception safety net**: any unexpected exception raised
   *after* the real HTTP attempt has already completed and been unloaded
   (e.g. the mini-task's subprocess/workspace step -- see
   :func:`_short_work_dir_name`) is caught by :func:`_build_gate_exception_sample`
   and persisted as a ``status="error"``/``acceptance_status="not_usable"``
   sample with ``system_error_code="GATE_BOUNDARY_UNEXPECTED_EXCEPTION"``,
   rather than propagating out of the gate function uncaught. A single task
   exception must never silently erase evidence of an attempt or crash a
   calling serial-campaign runner without a persisted, checkpointable
   outcome.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from . import catalog, local_lock, mini_task, ollama_client, orchestrator, resource_monitor, rubric, storage
from .aggregate import build_all_aggregates
from .report import CampaignReportData, render_report
from .schema import SampleRecord, SCHEMA_VERSION, utc_now_iso

CONNECT_GATE_TASK_ID = "connect-smoke-v1"
MINI_GATE_TASK_ID = mini_task.TASK_ID

#: ``SampleRecord.system_error_code`` used exclusively by
#: :func:`_build_gate_exception_sample` (the gate-boundary exception safety
#: net) -- re-exported so callers such as
#: :mod:`agent_helper_eval.serial_campaign` can distinguish this specific
#: failure mode from an ordinary transport/scoring error without
#: hardcoding the string twice.
GATE_BOUNDARY_EXCEPTION_ERROR_CODE = "GATE_BOUNDARY_UNEXPECTED_EXCEPTION"

#: Exact prompt sent for the connect gate -- deliberately trivial, matches
#: catalog.DEFAULT_CATALOG's connect-smoke-v1 deterministic_check_description.
CONNECT_GATE_PROMPT = "Reply with exactly the single word OK and nothing else."

DEFAULT_LOCK_FILENAME = local_lock.DEFAULT_LOCK_FILENAME


def _find_task(task_id: str) -> catalog.TaskSpec:
    for task in catalog.DEFAULT_CATALOG:
        if task.task_id == task_id:
            return task
    raise KeyError(f"no task {task_id!r} found in catalog.DEFAULT_CATALOG")


def _slug(value: str) -> str:
    return value.replace("/", "-").replace(":", "-").replace(" ", "-")


def _short_work_dir_name(sample_id: str) -> str:
    """A short (16 hex character), filesystem-safe, deterministic directory
    name derived from ``sample_id``, used for the mini-task's per-attempt
    subprocess working directory.

    ``mini_task_work/<full sample_id>`` (campaign id + model tag + task id
    + timestamp, all slugged together) can easily reach 200+ characters --
    observed in practice at 226 characters for a real
    ``agent-helper-serial-pilot-...``/``qwen3-coder:30b`` attempt -- which
    has been shown to raise ``NotADirectoryError: [WinError 267]`` ("The
    directory name is invalid") from ``subprocess.run(..., cwd=...)`` on
    Windows even though the path is nominally still under the classic
    260-character ``MAX_PATH``. Hashing ``sample_id`` down to a short,
    fixed-length name keeps the actual working-directory path length
    independent of how long campaign/model/task identifiers happen to be.
    The full ``sample_id`` is still recorded in the mini-gate's artifact
    JSON (``work_dir``/``sample_id`` keys) so which attempt produced which
    workspace is never lost.
    """

    return hashlib.sha256(sample_id.encode("utf-8")).hexdigest()[:16]


class LiveGateRefusedError(RuntimeError):
    """Raised when a live gate refuses to start (preflight/lock conflict).

    No sample row is persisted for a refusal -- nothing was attempted.
    """


@dataclasses.dataclass
class LiveGateResult:
    sample: SampleRecord
    output_dir: Path
    db_path: Path
    samples_csv: Path
    aggregates_csv: Path
    report_path: Optional[Path]
    combined_report_path: Optional[Path]


def _reconcile_total_wall_seconds(
    measured_wall_seconds: float,
    generate_result: Optional[ollama_client.GenerateResult],
    extra_after_llm_seconds: float = 0.0,
) -> float:
    """``total_wall_seconds`` must be a genuine envelope over every
    phase-timing sub-bucket reported on the sample (see
    ``schema.validate_sample``'s overlap-honesty check). Ollama's own
    ``total_duration`` (covering only the LLM-side load/prompt-eval/eval
    phases) is normally <= our client-measured wall time, but a small
    clock-skew/rounding mismatch between the two clocks is possible; widen
    the envelope to at least Ollama's own reported total *plus* whatever
    sequential, non-LLM work (test execution, orchestrator scoring)
    genuinely happened after it, rather than silently violating
    phase-timing honesty. ``extra_after_llm_seconds`` must be the same
    non-LLM seconds already folded into ``measured_wall_seconds`` -- this
    never invents time, it only guards against the LLM-side floor alone
    exceeding a too-small measured wall clock.
    """

    ollama_total_seconds = (
        ollama_client.ns_to_seconds(generate_result.total_duration_ns) if generate_result else None
    )
    if ollama_total_seconds is not None:
        floor_seconds = ollama_total_seconds + extra_after_llm_seconds
        return round(max(measured_wall_seconds, floor_seconds), 3)
    return round(measured_wall_seconds, 3)


def _classify_transport_error(exc: Exception) -> str:
    """Best-effort ``"timeout"`` vs ``"error"`` classification of an
    :class:`ollama_client.OllamaError`. Ollama/urllib do not always give a
    structured timeout signal through this stack, so this is a heuristic
    string match on the exception chain -- never fabricated beyond that."""

    text = str(exc).lower()
    cause = exc.__cause__
    if isinstance(cause, TimeoutError) or "timed out" in text or "timeout" in text:
        return "timeout"
    return "error"


def _write_json_artifact(artifacts_dir: Path, sample_id: str, payload: dict) -> tuple[str, str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    relative_path = f"artifacts/{sample_id}.json"
    absolute_path = artifacts_dir / f"{sample_id}.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str, sort_keys=True)
    absolute_path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return relative_path, digest


def _build_gate_exception_sample(
    *,
    gate_name: str,
    campaign_id: str,
    run_id: str,
    sample_id: str,
    task: catalog.TaskSpec,
    provider: str,
    model: str,
    runtime: str,
    quantization: Optional[str],
    start_time: str,
    attempt: "_AttemptContext",
    extra_wall_seconds: float,
    hardware_profile: Optional[str],
    hardware_gpu_model: Optional[str],
    hardware_vram_total_mb: Optional[float],
    output_dir: Path,
    exc: Exception,
) -> SampleRecord:
    """Gate-boundary safety net: build a fully persisted, honestly-N/A
    ``SampleRecord`` for an unexpected exception raised anywhere *after* the
    real Ollama HTTP attempt (``attempt``) has already completed and been
    unloaded -- e.g. the mini-task's subprocess/workspace step, artifact
    JSON serialization, or any other scoring/bookkeeping bug.

    This must never itself raise: a single unexpected task exception must
    not erase the fact that an attempt happened, and must not crash the
    calling serial-campaign runner without a checkpointable, inspectable
    result. ``deterministic_score``/``reviewer_score`` are left ``None``
    (never fabricated) and ``status="error"`` forces
    ``acceptance_status="not_usable"`` via the same hard-gate rubric every
    other sample goes through -- a mid-scoring crash can never be
    mistaken for an accepted result.
    """

    end_time = utc_now_iso()
    error_message = f"{type(exc).__name__}: {exc}"[:500]
    generate_result = attempt.generate_result if attempt is not None else None
    elapsed_seconds = round((attempt.attempt_wall_seconds if attempt is not None else 0.0) + max(0.0, extra_wall_seconds), 3)

    artifact_payload = {
        "gate": gate_name,
        "campaign_id": campaign_id,
        "sample_id": sample_id,
        "model": model,
        "gate_boundary_exception": True,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc)[:2000],
        "unload_ok": attempt.unload_ok if attempt is not None else None,
        "response_text": generate_result.response_text if generate_result else None,
    }
    try:
        artifact_path, artifact_hash = _write_json_artifact(output_dir / "artifacts", sample_id, artifact_payload)
    except Exception:
        # Even the fallback artifact write is best-effort: a workspace/
        # filesystem problem severe enough to break this too must still
        # leave a persisted sample row, just without an artifact link.
        artifact_path, artifact_hash = None, None

    acceptance_status, acceptance_reasons = rubric.compute_sample_acceptance(
        status="error",
        deterministic_score=None,
        reviewer_score=None,
        unsafe_behavior_flag=None,
        output_placeholder_or_incomplete=None,
    )

    return SampleRecord(
        schema_version=SCHEMA_VERSION,
        campaign_id=campaign_id,
        run_id=run_id,
        sample_id=sample_id,
        benchmark_set="agent-helper-catalog-v1",
        benchmark_version=catalog.CATALOG_SCHEMA_VERSION,
        track="pure_model",
        task_id=task.task_id,
        task_name=task.name,
        task_category=task.category,
        provider=provider,
        backend="ollama",
        model=model,
        runtime=runtime,
        quantization=quantization,
        start_time=start_time,
        end_time=end_time,
        elapsed_seconds=elapsed_seconds,
        prompt_tokens=generate_result.prompt_eval_count if generate_result else None,
        output_tokens=generate_result.eval_count if generate_result else None,
        total_tokens=None,
        tokens_per_second=None,
        ttft_seconds=generate_result.ttft_seconds if generate_result else None,
        cpu_time_seconds=None,
        cpu_avg_percent=attempt.resource_stats.cpu_avg_percent if attempt is not None else None,
        cpu_max_percent=attempt.resource_stats.cpu_max_percent if attempt is not None else None,
        ram_avg_mb=attempt.resource_stats.ram_avg_mb if attempt is not None else None,
        ram_max_mb=attempt.resource_stats.ram_max_mb if attempt is not None else None,
        gpu_avg_percent=attempt.resource_stats.gpu_avg_percent if attempt is not None else None,
        gpu_max_percent=attempt.resource_stats.gpu_max_percent if attempt is not None else None,
        vram_avg_mb=attempt.resource_stats.vram_avg_mb if attempt is not None else None,
        vram_max_mb=attempt.resource_stats.vram_max_mb if attempt is not None else None,
        deterministic_score=None,
        reviewer_score=None,
        collaboration_score=None,
        composite_score=None,
        status="error",
        system_error_flag=True,
        system_error_code=GATE_BOUNDARY_EXCEPTION_ERROR_CODE,
        system_error_message=error_message,
        retry_count=0,
        iteration_index=1,
        artifact_path=artifact_path,
        artifact_hash=artifact_hash,
        reviewer_provenance=f"gate-boundary-exception ({gate_name}; no scoring reached this point)",
        reviewer_notes=f"unexpected exception after the real HTTP attempt: {error_message}",
        hardware_profile=hardware_profile,
        hardware_gpu_model=hardware_gpu_model,
        hardware_vram_total_mb=hardware_vram_total_mb,
        sample_seq=1,
        provenance="measured",
        notes=f"gate-boundary exception; unload_ok={attempt.unload_ok if attempt is not None else None}",
        unsafe_behavior_flag=None,
        output_placeholder_or_incomplete=None,
        acceptance_status=acceptance_status,
        acceptance_reasons=acceptance_reasons,
        total_wall_seconds=elapsed_seconds,
        llm_queue_seconds=None,
        llm_request_seconds=attempt.generate_wall_seconds if attempt is not None else None,
        prompt_eval_seconds=None,
        generation_seconds=None,
        local_tool_exec_seconds=None,
        test_exec_seconds=None,
        orchestrator_review_seconds=None,
        idle_wait_seconds=None,
        overlap_seconds=None,
        model_load_seconds=None,
        orchestrator_cpu_time_seconds=None,
        model_cpu_time_seconds=attempt.resource_stats.model_process_cpu_time_seconds if attempt is not None else None,
        io_read=attempt.resource_stats.io_read if attempt is not None else None,
        io_write=attempt.resource_stats.io_write if attempt is not None else None,
    )


def _persist_and_report(repo_root: Path, campaign_id: str, sample: SampleRecord) -> LiveGateResult:
    """Persist one real sample through the SQLite SSOT + both CSVs and
    regenerate the local + combined HTML reports -- exactly the same
    pipeline :func:`orchestrator.run_dry_run` uses, so live and synthetic
    campaigns stay format-compatible."""

    output_dir = orchestrator.campaign_output_dir(repo_root, campaign_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / storage.DB_FILENAME
    conn = storage.connect(db_path)
    try:
        storage.insert_samples(conn, [sample])
        stored_samples = storage.fetch_samples(conn, campaign_id=campaign_id)
        aggregates = build_all_aggregates(stored_samples, provenance="measured")
        storage.insert_aggregates(conn, aggregates)
        samples_csv = output_dir / storage.SAMPLES_CSV_FILENAME
        aggregates_csv = output_dir / storage.AGGREGATES_CSV_FILENAME
        storage.export_samples_csv(conn, samples_csv, campaign_id=campaign_id)
        storage.export_aggregates_csv(conn, aggregates_csv, campaign_id=campaign_id)
        stored_aggregates = storage.fetch_aggregates(conn, campaign_id=campaign_id)
    finally:
        conn.close()

    local_report_path = output_dir / "report.html"
    local_report_path.write_text(
        render_report(
            [
                CampaignReportData(
                    campaign_id=campaign_id,
                    generated_at=utc_now_iso(),
                    title=f"{campaign_id} (live Ollama execution)",
                    aggregates=stored_aggregates,
                    samples=stored_samples,
                )
            ],
            model_inventory=orchestrator.load_default_model_inventory(repo_root),
        ),
        encoding="utf-8",
    )
    combined_report_path = orchestrator.build_combined_report(repo_root)

    return LiveGateResult(
        sample=sample,
        output_dir=output_dir,
        db_path=db_path,
        samples_csv=samples_csv,
        aggregates_csv=aggregates_csv,
        report_path=local_report_path,
        combined_report_path=combined_report_path,
    )


@dataclasses.dataclass
class _AttemptContext:
    """Shared bookkeeping produced by :func:`_run_generate_with_lifecycle`,
    consumed by each gate's own scoring step."""

    generate_result: Optional[ollama_client.GenerateResult]
    status: str
    system_error_flag: bool
    system_error_code: Optional[str]
    system_error_message: Optional[str]
    unload_ok: Optional[bool]
    resource_stats: resource_monitor.ResourceStats
    attempt_wall_seconds: float
    #: Client-measured wall-clock duration of *only* the ``ollama_generate()``
    #: HTTP call itself (excludes the subsequent unload call and any
    #: monitor/lifecycle overhead) -- this is the real measurement used for
    #: ``SampleRecord.llm_request_seconds`` (see pilot-review remediation
    #: note in docs/project/agent_helper_benchmark.md SS16.5). ``None`` only
    #: if the generate call raised before any timing could be recorded.
    generate_wall_seconds: Optional[float]


def _run_generate_with_lifecycle(
    base_url: str,
    model: str,
    prompt: str,
    options: ollama_client.GenerateOptions,
    timeout_seconds: float,
    keep_alive: str,
    json_transport: ollama_client.JsonTransport,
    stream_transport: ollama_client.StreamTransport,
    monitor_factory,
    unload_timeout_seconds: float,
) -> _AttemptContext:
    """Start resource monitoring, run one real generate call, and always
    unload -- regardless of success/failure -- in a ``finally`` block."""

    # process_name_filters=["ollama"] lets ResourceMonitor attribute RAM and
    # (best-effort) process CPU time specifically to the Ollama server
    # process(es) rather than the whole system -- see
    # SampleRecord.model_cpu_time_seconds / ResourceStats.model_process_cpu_time_seconds.
    monitor = monitor_factory(process_name_filters=["ollama"])
    monitor.start()
    attempt_start = time.perf_counter()
    generate_result: Optional[ollama_client.GenerateResult] = None
    status = "success"
    system_error_flag = False
    system_error_code: Optional[str] = None
    system_error_message: Optional[str] = None
    unload_ok: Optional[bool] = None
    generate_wall_seconds: Optional[float] = None
    try:
        generate_start = time.perf_counter()
        generate_result = ollama_client.ollama_generate(
            base_url,
            model,
            prompt,
            options,
            stream_transport,
            timeout_seconds=timeout_seconds,
            keep_alive=keep_alive,
        )
        generate_wall_seconds = round(time.perf_counter() - generate_start, 3)
    except ollama_client.OllamaError as exc:
        generate_wall_seconds = round(time.perf_counter() - generate_start, 3)
        status = _classify_transport_error(exc)
        system_error_flag = True
        system_error_code = "OLLAMA_GENERATE_FAILED"
        system_error_message = str(exc)[:500]
    finally:
        monitor.stop()
        unload_ok = ollama_client.ollama_unload(
            base_url, model, json_transport, timeout_seconds=unload_timeout_seconds
        )
    attempt_wall_seconds = round(time.perf_counter() - attempt_start, 3)
    return _AttemptContext(
        generate_result=generate_result,
        status=status,
        system_error_flag=system_error_flag,
        system_error_code=system_error_code,
        system_error_message=system_error_message,
        unload_ok=unload_ok,
        resource_stats=monitor.stats(),
        attempt_wall_seconds=attempt_wall_seconds,
        generate_wall_seconds=generate_wall_seconds,
    )


def run_ollama_connect_gate(
    repo_root: Path,
    campaign_id: str,
    model: str,
    *,
    base_url: str = ollama_client.DEFAULT_BASE_URL,
    timeout_seconds: float = 30.0,
    allow_reuse_loaded_model: bool = False,
    provider: str = "local",
    runtime: str = "ollama",
    quantization: Optional[str] = None,
    keep_alive: str = "5m",
    seed: int = ollama_client.DEFAULT_SEED,
    num_predict: int = 16,
    num_ctx: int = 512,
    hardware_profile: Optional[str] = None,
    hardware_gpu_model: Optional[str] = None,
    hardware_vram_total_mb: Optional[float] = None,
    lock_path: Optional[Path] = None,
    lock_wait_seconds: float = local_lock.DEFAULT_WAIT_SECONDS,
    json_transport: ollama_client.JsonTransport = ollama_client.urllib_json_transport,
    stream_transport: ollama_client.StreamTransport = ollama_client.urllib_stream_transport,
    monitor_factory=resource_monitor.ResourceMonitor,
) -> LiveGateResult:
    """Run one real Ollama connect/format-smoke check against ``model``.

    Requires an explicit ``model`` and ``campaign_id`` -- never iterates an
    inventory. Raises :class:`LiveGateRefusedError` (wrapping either a
    preflight conflict or a local-lock conflict) without persisting
    anything if the daemon/lock preflight refuses to even start.
    """

    task = _find_task(CONNECT_GATE_TASK_ID)
    try:
        with local_lock.local_model_slot(
            lock_path, "ollama", model, wait_seconds=lock_wait_seconds
        ):
            loaded = ollama_client.ollama_ps(
                base_url, json_transport, timeout_seconds=10.0
            )
            ollama_client.check_single_model_preflight(
                loaded, model, allow_reuse_loaded_model
            )
            orchestrator_cpu_start = time.process_time()
            start_time = utc_now_iso()
            options = ollama_client.GenerateOptions(seed=seed, num_predict=num_predict, num_ctx=num_ctx)
            attempt = _run_generate_with_lifecycle(
                base_url, model, CONNECT_GATE_PROMPT, options, timeout_seconds, keep_alive,
                json_transport, stream_transport, monitor_factory, unload_timeout_seconds=15.0,
            )
            end_time = utc_now_iso()
    except (
        local_lock.LocalModelLockError,
        ollama_client.OllamaPreflightConflictError,
        ollama_client.OllamaError,
    ) as exc:
        raise LiveGateRefusedError(str(exc)) from exc

    # run_id/sample_id/output_dir are computed here (right after the real
    # HTTP attempt, before any scoring) so the gate-boundary exception
    # safety net below always has a stable identity to persist a failed
    # sample under, even if scoring/artifact-writing itself blows up.
    attempt_stamp = start_time.replace(":", "").replace("-", "").replace(".", "").replace("+", "p")
    run_id = f"{campaign_id}-connect-gate-{_slug(model)}-{attempt_stamp}"
    sample_id = _slug(f"{campaign_id}-ollama-{model}-{CONNECT_GATE_TASK_ID}-pure_model-{attempt_stamp}")
    output_dir = orchestrator.campaign_output_dir(repo_root, campaign_id)
    post_generate_start = time.perf_counter()

    try:
        scoring_start = time.perf_counter()
        response_text = attempt.generate_result.response_text if attempt.generate_result else ""
        deterministic_score: Optional[float] = None
        output_placeholder_or_incomplete: Optional[bool] = None
        unsafe_behavior_flag: Optional[bool] = None
        notes: Optional[str] = None
        if attempt.status == "success":
            matched = "ok" in response_text.strip().lower()
            deterministic_score = 100.0 if matched else 0.0
            output_placeholder_or_incomplete = not bool(response_text.strip())
            unsafe_behavior_flag = False
            notes = "connect-gate: expected literal matched" if matched else "connect-gate: expected literal 'OK' not found in response"
        orchestrator_review_seconds = round(time.perf_counter() - scoring_start, 3)
        orchestrator_cpu_time_seconds = round(time.process_time() - orchestrator_cpu_start, 3)
        # total_wall_seconds/elapsed_seconds cover the *whole* attempt (real HTTP
        # call + unload + scoring), so phase-timing buckets never exceed it.
        total_wall_seconds = _reconcile_total_wall_seconds(
            attempt.attempt_wall_seconds + orchestrator_review_seconds,
            attempt.generate_result,
            extra_after_llm_seconds=orchestrator_review_seconds,
        )

        acceptance_status, acceptance_reasons = rubric.compute_sample_acceptance(
            status=attempt.status,
            deterministic_score=deterministic_score,
            reviewer_score=None,
            unsafe_behavior_flag=unsafe_behavior_flag,
            output_placeholder_or_incomplete=output_placeholder_or_incomplete,
        )

        artifact_payload = {
            "gate": "connect-gate",
            "campaign_id": campaign_id,
            "sample_id": sample_id,
            "model": model,
            "base_url": base_url,
            "prompt": CONNECT_GATE_PROMPT,
            "options": options.to_json(),
            "raw_response_lines": attempt.generate_result.raw_lines if attempt.generate_result else [],
            "response_text": response_text,
            "done_reason": attempt.generate_result.done_reason if attempt.generate_result else None,
            "unload_ok": attempt.unload_ok,
            "system_error_message": attempt.system_error_message,
        }
        artifact_path, artifact_hash = _write_json_artifact(output_dir / "artifacts", sample_id, artifact_payload)

        generate_result = attempt.generate_result
        sample = SampleRecord(
            schema_version=SCHEMA_VERSION,
            campaign_id=campaign_id,
            run_id=run_id,
            sample_id=sample_id,
            benchmark_set="agent-helper-catalog-v1",
            benchmark_version=catalog.CATALOG_SCHEMA_VERSION,
            track="pure_model",
            task_id=task.task_id,
            task_name=task.name,
            task_category=task.category,
            provider=provider,
            backend="ollama",
            model=model,
            runtime=runtime,
            quantization=quantization,
            start_time=start_time,
            end_time=end_time,
            elapsed_seconds=total_wall_seconds,
            prompt_tokens=generate_result.prompt_eval_count if generate_result else None,
            output_tokens=generate_result.eval_count if generate_result else None,
            total_tokens=(
                (generate_result.prompt_eval_count or 0) + (generate_result.eval_count or 0)
                if generate_result and (generate_result.prompt_eval_count is not None or generate_result.eval_count is not None)
                else None
            ),
            tokens_per_second=ollama_client.tokens_per_second(
                generate_result.eval_count if generate_result else None,
                generate_result.eval_duration_ns if generate_result else None,
            ),
            ttft_seconds=generate_result.ttft_seconds if generate_result else None,
            cpu_time_seconds=None,
            cpu_avg_percent=attempt.resource_stats.cpu_avg_percent,
            cpu_max_percent=attempt.resource_stats.cpu_max_percent,
            ram_avg_mb=attempt.resource_stats.ram_avg_mb,
            ram_max_mb=attempt.resource_stats.ram_max_mb,
            gpu_avg_percent=attempt.resource_stats.gpu_avg_percent,
            gpu_max_percent=attempt.resource_stats.gpu_max_percent,
            vram_avg_mb=attempt.resource_stats.vram_avg_mb,
            vram_max_mb=attempt.resource_stats.vram_max_mb,
            deterministic_score=deterministic_score,
            reviewer_score=None,
            collaboration_score=None,
            composite_score=None,
            status=attempt.status,
            system_error_flag=attempt.system_error_flag,
            system_error_code=attempt.system_error_code,
            system_error_message=attempt.system_error_message,
            retry_count=0,
            iteration_index=1,
            artifact_path=artifact_path,
            artifact_hash=artifact_hash,
            reviewer_provenance="deterministic-only (connect-gate has no reviewer/collaboration gate)",
            reviewer_notes=notes,
            hardware_profile=hardware_profile,
            hardware_gpu_model=hardware_gpu_model,
            hardware_vram_total_mb=hardware_vram_total_mb,
            sample_seq=1,
            provenance="measured",
            notes=None if attempt.status == "success" else f"unload_ok={attempt.unload_ok}",
            unsafe_behavior_flag=unsafe_behavior_flag,
            output_placeholder_or_incomplete=output_placeholder_or_incomplete,
            acceptance_status=acceptance_status,
            acceptance_reasons=acceptance_reasons,
            total_wall_seconds=total_wall_seconds,
            llm_queue_seconds=None,
            llm_request_seconds=attempt.generate_wall_seconds,
            prompt_eval_seconds=ollama_client.ns_to_seconds(generate_result.prompt_eval_duration_ns) if generate_result else None,
            generation_seconds=ollama_client.ns_to_seconds(generate_result.eval_duration_ns) if generate_result else None,
            local_tool_exec_seconds=None,
            test_exec_seconds=None,
            orchestrator_review_seconds=orchestrator_review_seconds,
            idle_wait_seconds=None,
            overlap_seconds=None,
            model_load_seconds=ollama_client.ns_to_seconds(generate_result.load_duration_ns) if generate_result else None,
            orchestrator_cpu_time_seconds=orchestrator_cpu_time_seconds,
            model_cpu_time_seconds=attempt.resource_stats.model_process_cpu_time_seconds,
            io_read=attempt.resource_stats.io_read,
            io_write=attempt.resource_stats.io_write,
        )
    except Exception as exc:  # noqa: BLE001 -- gate-boundary safety net, see module docstring.
        sample = _build_gate_exception_sample(
            gate_name="connect-gate",
            campaign_id=campaign_id,
            run_id=run_id,
            sample_id=sample_id,
            task=task,
            provider=provider,
            model=model,
            runtime=runtime,
            quantization=quantization,
            start_time=start_time,
            attempt=attempt,
            extra_wall_seconds=round(time.perf_counter() - post_generate_start, 3),
            hardware_profile=hardware_profile,
            hardware_gpu_model=hardware_gpu_model,
            hardware_vram_total_mb=hardware_vram_total_mb,
            output_dir=output_dir,
            exc=exc,
        )

    return _persist_and_report(repo_root, campaign_id, sample)


def run_ollama_mini_gate(
    repo_root: Path,
    campaign_id: str,
    model: str,
    *,
    base_url: str = ollama_client.DEFAULT_BASE_URL,
    timeout_seconds: float = 180.0,
    test_timeout_seconds: float = 20.0,
    allow_reuse_loaded_model: bool = False,
    provider: str = "local",
    runtime: str = "ollama",
    quantization: Optional[str] = None,
    keep_alive: str = "10m",
    seed: int = ollama_client.DEFAULT_SEED,
    num_predict: int = 800,
    num_ctx: int = 4096,
    hardware_profile: Optional[str] = None,
    hardware_gpu_model: Optional[str] = None,
    hardware_vram_total_mb: Optional[float] = None,
    lock_path: Optional[Path] = None,
    lock_wait_seconds: float = local_lock.DEFAULT_WAIT_SECONDS,
    json_transport: ollama_client.JsonTransport = ollama_client.urllib_json_transport,
    stream_transport: ollama_client.StreamTransport = ollama_client.urllib_stream_transport,
    monitor_factory=resource_monitor.ResourceMonitor,
) -> LiveGateResult:
    """Run one real, one-shot mini coding-task attempt against ``model``.

    This is a **single attempt only** -- a follow-up/correction iteration is
    an explicit, separate command added later (not this function). The hard
    acceptance gate (:func:`rubric.compute_sample_acceptance`) always
    applies: a syntactically-fine but test-failing solution is scored from
    the fixed deterministic test suite, never from keywords.
    """

    task = _find_task(MINI_GATE_TASK_ID)
    try:
        with local_lock.local_model_slot(
            lock_path, "ollama", model, wait_seconds=lock_wait_seconds
        ):
            loaded = ollama_client.ollama_ps(
                base_url, json_transport, timeout_seconds=10.0
            )
            ollama_client.check_single_model_preflight(
                loaded, model, allow_reuse_loaded_model
            )
            orchestrator_cpu_start = time.process_time()
            start_time = utc_now_iso()
            options = ollama_client.GenerateOptions(seed=seed, num_predict=num_predict, num_ctx=num_ctx)
            attempt = _run_generate_with_lifecycle(
                base_url, model, mini_task.PROMPT, options, timeout_seconds, keep_alive,
                json_transport, stream_transport, monitor_factory, unload_timeout_seconds=15.0,
            )
            end_time = utc_now_iso()
    except (
        local_lock.LocalModelLockError,
        ollama_client.OllamaPreflightConflictError,
        ollama_client.OllamaError,
    ) as exc:
        raise LiveGateRefusedError(str(exc)) from exc

    # run_id/sample_id/output_dir are computed here (right after the real
    # HTTP attempt, before any scoring) so the gate-boundary exception
    # safety net below always has a stable identity to persist a failed
    # sample under, even if scoring (e.g. the mini-task subprocess/
    # workspace step) itself blows up.
    attempt_stamp = start_time.replace(":", "").replace("-", "").replace(".", "").replace("+", "p")
    run_id = f"{campaign_id}-mini-gate-{_slug(model)}-{attempt_stamp}"
    sample_id = _slug(f"{campaign_id}-ollama-{model}-{MINI_GATE_TASK_ID}-pure_model-{attempt_stamp}")
    output_dir = orchestrator.campaign_output_dir(repo_root, campaign_id)
    post_generate_start = time.perf_counter()

    try:
        response_text = attempt.generate_result.response_text if attempt.generate_result else ""
        deterministic_score: Optional[float] = None
        output_placeholder_or_incomplete: Optional[bool] = None
        unsafe_behavior_flag: Optional[bool] = None
        mini_result: Optional[mini_task.MiniTaskExecutionResult] = None
        status = attempt.status
        test_exec_seconds: Optional[float] = None
        orchestrator_review_seconds = 0.0
        work_dir: Optional[Path] = None

        if attempt.status == "success":
            scoring_start = time.perf_counter()
            # A short, deterministic, hashed directory name -- independent
            # of how long campaign_id/model/task identifiers are -- keeps
            # this subprocess cwd well under Windows' historical MAX_PATH.
            # The full sample_id is still recorded in the artifact JSON
            # below for traceability (see _short_work_dir_name docstring).
            work_dir = output_dir / "mini_task_work" / _short_work_dir_name(sample_id)
            mini_result = mini_task.run_mini_task_tests(
                response_text, work_dir, test_timeout_seconds=test_timeout_seconds
            )
            scoring_wall = time.perf_counter() - scoring_start
            test_exec_seconds = mini_result.test_exec_seconds
            orchestrator_review_seconds = round(max(0.0, scoring_wall - (test_exec_seconds or 0.0)), 3)

            deterministic_score = mini_result.deterministic_score
            unsafe_behavior_flag = mini_result.unsafe_behavior_flag
            output_placeholder_or_incomplete = mini_result.output_placeholder_or_incomplete
            if mini_result.test_timed_out:
                status = "timeout"

        acceptance_status, acceptance_reasons = rubric.compute_sample_acceptance(
            status=status,
            deterministic_score=deterministic_score,
            reviewer_score=None,
            unsafe_behavior_flag=unsafe_behavior_flag,
            output_placeholder_or_incomplete=output_placeholder_or_incomplete,
        )

        system_error_flag = attempt.system_error_flag or status == "timeout"
        system_error_code = attempt.system_error_code or ("MINI_TASK_TEST_TIMEOUT" if status == "timeout" and not attempt.system_error_code else attempt.system_error_code)
        system_error_message = attempt.system_error_message
        if status == "timeout" and mini_result is not None and mini_result.test_timed_out and not system_error_message:
            system_error_message = f"deterministic test execution timed out after {test_timeout_seconds}s"

        orchestrator_cpu_time_seconds = round(time.process_time() - orchestrator_cpu_start, 3)

        # total_wall_seconds/elapsed_seconds cover the *whole* attempt (real HTTP
        # call + unload + scoring/test execution), so phase-timing buckets never
        # exceed it -- orchestrator_review_seconds above was already netted to
        # exclude test_exec_seconds, so this sum has no double-count.
        total_wall_seconds = _reconcile_total_wall_seconds(
            attempt.attempt_wall_seconds + (test_exec_seconds or 0.0) + orchestrator_review_seconds,
            attempt.generate_result,
            extra_after_llm_seconds=(test_exec_seconds or 0.0) + orchestrator_review_seconds,
        )

        artifact_payload = {
            "gate": "mini-gate",
            "campaign_id": campaign_id,
            "sample_id": sample_id,
            "model": model,
            "base_url": base_url,
            "prompt": mini_task.PROMPT,
            "options": options.to_json(),
            "raw_response_lines": attempt.generate_result.raw_lines if attempt.generate_result else [],
            "response_text": response_text,
            "done_reason": attempt.generate_result.done_reason if attempt.generate_result else None,
            "unload_ok": attempt.unload_ok,
            "system_error_message": attempt.system_error_message,
            "mini_task": mini_result.to_json() if mini_result is not None else None,
            "work_dir": str(work_dir.relative_to(output_dir)) if work_dir is not None else None,
        }
        artifact_path, artifact_hash = _write_json_artifact(output_dir / "artifacts", sample_id, artifact_payload)

        generate_result = attempt.generate_result
        sample = SampleRecord(
            schema_version=SCHEMA_VERSION,
            campaign_id=campaign_id,
            run_id=run_id,
            sample_id=sample_id,
            benchmark_set="agent-helper-catalog-v1",
            benchmark_version=catalog.CATALOG_SCHEMA_VERSION,
            track="pure_model",
            task_id=task.task_id,
            task_name=task.name,
            task_category=task.category,
            provider=provider,
            backend="ollama",
            model=model,
            runtime=runtime,
            quantization=quantization,
            start_time=start_time,
            end_time=end_time,
            elapsed_seconds=total_wall_seconds,
            prompt_tokens=generate_result.prompt_eval_count if generate_result else None,
            output_tokens=generate_result.eval_count if generate_result else None,
            total_tokens=(
                (generate_result.prompt_eval_count or 0) + (generate_result.eval_count or 0)
                if generate_result and (generate_result.prompt_eval_count is not None or generate_result.eval_count is not None)
                else None
            ),
            tokens_per_second=ollama_client.tokens_per_second(
                generate_result.eval_count if generate_result else None,
                generate_result.eval_duration_ns if generate_result else None,
            ),
            ttft_seconds=generate_result.ttft_seconds if generate_result else None,
            cpu_time_seconds=None,
            cpu_avg_percent=attempt.resource_stats.cpu_avg_percent,
            cpu_max_percent=attempt.resource_stats.cpu_max_percent,
            ram_avg_mb=attempt.resource_stats.ram_avg_mb,
            ram_max_mb=attempt.resource_stats.ram_max_mb,
            gpu_avg_percent=attempt.resource_stats.gpu_avg_percent,
            gpu_max_percent=attempt.resource_stats.gpu_max_percent,
            vram_avg_mb=attempt.resource_stats.vram_avg_mb,
            vram_max_mb=attempt.resource_stats.vram_max_mb,
            deterministic_score=deterministic_score,
            reviewer_score=None,
            collaboration_score=None,
            composite_score=None,
            status=status,
            system_error_flag=system_error_flag,
            system_error_code=system_error_code,
            system_error_message=system_error_message,
            retry_count=0,
            iteration_index=1,
            artifact_path=artifact_path,
            artifact_hash=artifact_hash,
            reviewer_provenance=(
                "deterministic-tests-only (one-shot; no reviewer/collaboration score "
                "yet -- a correction/reviewer loop is a later, separate command)"
            ),
            reviewer_notes=(
                None
                if mini_result is None
                else f"tests_passed={mini_result.tests_passed}/{mini_result.tests_total}; "
                f"unsafe_findings={mini_result.unsafe_findings}; "
                f"placeholder_findings={mini_result.placeholder_findings}"
            ),
            hardware_profile=hardware_profile,
            hardware_gpu_model=hardware_gpu_model,
            hardware_vram_total_mb=hardware_vram_total_mb,
            sample_seq=1,
            provenance="measured",
            notes=None if status == "success" else f"unload_ok={attempt.unload_ok}",
            unsafe_behavior_flag=unsafe_behavior_flag,
            output_placeholder_or_incomplete=output_placeholder_or_incomplete,
            acceptance_status=acceptance_status,
            acceptance_reasons=acceptance_reasons,
            total_wall_seconds=total_wall_seconds,
            llm_queue_seconds=None,
            llm_request_seconds=attempt.generate_wall_seconds,
            prompt_eval_seconds=ollama_client.ns_to_seconds(generate_result.prompt_eval_duration_ns) if generate_result else None,
            generation_seconds=ollama_client.ns_to_seconds(generate_result.eval_duration_ns) if generate_result else None,
            local_tool_exec_seconds=None,
            test_exec_seconds=test_exec_seconds,
            orchestrator_review_seconds=orchestrator_review_seconds,
            idle_wait_seconds=None,
            overlap_seconds=None,
            model_load_seconds=ollama_client.ns_to_seconds(generate_result.load_duration_ns) if generate_result else None,
            orchestrator_cpu_time_seconds=orchestrator_cpu_time_seconds,
            model_cpu_time_seconds=attempt.resource_stats.model_process_cpu_time_seconds,
        )
    except Exception as exc:  # noqa: BLE001 -- gate-boundary safety net, see module docstring.
        sample = _build_gate_exception_sample(
            gate_name="mini-gate",
            campaign_id=campaign_id,
            run_id=run_id,
            sample_id=sample_id,
            task=task,
            provider=provider,
            model=model,
            runtime=runtime,
            quantization=quantization,
            start_time=start_time,
            attempt=attempt,
            extra_wall_seconds=round(time.perf_counter() - post_generate_start, 3),
            hardware_profile=hardware_profile,
            hardware_gpu_model=hardware_gpu_model,
            hardware_vram_total_mb=hardware_vram_total_mb,
            output_dir=output_dir,
            exc=exc,
        )

    return _persist_and_report(repo_root, campaign_id, sample)
