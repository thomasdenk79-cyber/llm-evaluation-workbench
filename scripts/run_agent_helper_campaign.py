"""Agent-helper evaluation campaign CLI (harness only; no model calls here).

This is the single entry point for the agent-helper evaluation subarea
(``scripts/agent_helper_eval/``). It never calls a model, Ollama, llama.cpp,
or the Siemens/Copilot APIs by itself. Subcommands:

``dry-run``
    Generates a fully synthetic campaign (seeded RNG, no I/O beyond the
    local filesystem) to validate the storage/aggregation/report pipeline
    end-to-end. Safe to run repeatedly and offline.

``connect-gate-plan``
    Prints the exact, reviewable command for a connect/format smoke check
    per model in an inventory file. Prints only; never executes anything.

``report``
    Rebuilds the combined multi-campaign HTML report from every existing
    campaign directory under ``benchmark_results/agent-helper/`` (reads
    already-written SQLite databases only; does not call a model).

``import-legacy``
    Imports the legacy ``migration_llm_bench`` history CSV into a new,
    isolated, clearly-marked ``historical_import`` campaign directory under
    ``benchmark_results/agent-helper/`` and regenerates its report.

``connect-gate-run``
    **Real** one-model Ollama connect/format-smoke check (see
    :mod:`agent_helper_eval.live_gates`). Requires an explicit ``--model``
    and ``--campaign-id``; never iterates an inventory. Enforces the
    max-one-local-model preflight/lock and always unloads the model
    afterwards. This is the *only* subcommand in this file that can reach a
    real Ollama server -- it is never invoked automatically by anything
    else in this repository.

``mini-gate-run``
    **Real**, one-shot mini coding-task attempt against one explicitly
    named model (see :mod:`agent_helper_eval.live_gates`/
    :mod:`agent_helper_eval.mini_task`). Scores the response by running a
    fixed, deterministic unit-test suite in a subprocess -- never a keyword
    match. The hard acceptance gate always applies. A follow-up/correction
    iteration is intentionally a separate, later command; this one is a
    single attempt only.

``recompute-campaign``
    **No model call, ever.** Idempotent maintenance pass that re-derives an
    already-existing campaign's aggregates (and, where the historical
    phase-timing bug's exact fingerprint is detected, repairs affected
    sample rows from their own already-written artifact JSON) using the
    *current* rubric/schema logic, then re-exports both CSVs and rebuilds
    the local + combined HTML report (see
    :mod:`agent_helper_eval.repair`). Use this after a schema/rubric fix to
    bring an older campaign's aggregates/report in line without re-running
    any model.

``ollama-inventory-snapshot``
    **Real, read-only** discovery: ``GET /api/tags`` + ``POST /api/show``
    only (see :mod:`agent_helper_eval.ollama_inventory`) -- the HTTP
    equivalent of ``ollama list``/``ollama show``. Never loads or generates
    anything. Persists a JSON snapshot into the campaign's own output
    directory; never overwrites the shared hand-curated example inventory.

``serial-plan``
    Always a dry plan (never executes anything). Builds and prints, in
    strict execution order, which discovered models would be attempted
    (``included``) and which would be deferred/excluded (``deferred``, each
    with a mandatory reason) -- see :mod:`agent_helper_eval.serial_campaign`.

``serial-execute``
    **Real**, strictly sequential connect-then-mini gate campaign across
    several models, one model fully at a time (never parallel). Without
    ``--confirm`` this behaves exactly like ``serial-plan`` and executes
    nothing. With ``--confirm``, an explicit ``--models`` list or at least
    one filter flag is also required -- there is no way to run every
    discovered model "by accident". Enforces the max-one-local-model
    preflight/lock per model, always unloads in a ``finally`` block, skips
    the mini gate whenever the connect gate was not accepted, resumes
    without repeating already-attempted (model, task) pairs, retries only a
    raised transient error (never a timeout or a scored rejection), and
    writes an interruption-safe JSON checkpoint after every single step.

Examples
--------

.. code-block:: powershell

    # No-model dry-run: validates the whole harness end-to-end.
    python .\\scripts\\run_agent_helper_campaign.py dry-run --campaign-id dry-run-2026-08-01

    # Same, but against an externally authored catalog file (no code change
    # needed as long as it matches the documented agent-helper-catalog-v1
    # schema -- see benchmarks/agent-helper-catalog.example-external.json).
    python .\\scripts\\run_agent_helper_campaign.py dry-run --campaign-id dry-run-external `
        --catalog .\\benchmarks\\agent-helper-catalog.example-external.json

    # Print (do not run) the exact connect-gate command for every model in
    # the example inventory.
    python .\\scripts\\run_agent_helper_campaign.py connect-gate-plan `
        --inventory .\\benchmarks\\agent-helper-model-inventory.example.json

    # Rebuild the combined report from whatever campaigns already exist.
    python .\\scripts\\run_agent_helper_campaign.py report

    # REAL one-model connect gate. Requires a running local Ollama server
    # (``ollama serve``) with the named model available; this script does
    # not start Ollama or pull models for you.
    python .\\scripts\\run_agent_helper_campaign.py connect-gate-run `
        --campaign-id live-2026-08-01 --model qwen3-coder:30b

    # REAL one-shot mini coding-task gate for the same model/campaign.
    python .\\scripts\\run_agent_helper_campaign.py mini-gate-run `
        --campaign-id live-2026-08-01 --model qwen3-coder:30b

    # No-model maintenance pass: recompute an existing campaign's aggregates
    # (and repair the historical llm_queue/llm_request phase-timing bug's
    # exact fingerprint where recoverable from artifact JSON) after a
    # schema/rubric fix, and rebuild its report.
    python .\\scripts\\run_agent_helper_campaign.py recompute-campaign `
        --campaign-id agent-helper-pilot-20260801

    # REAL, read-only inventory discovery + snapshot (list/show only).
    python .\\scripts\\run_agent_helper_campaign.py ollama-inventory-snapshot `
        --campaign-id serial-pilot-2026-08-15

    # No-model dry plan over the freshly discovered inventory.
    python .\\scripts\\run_agent_helper_campaign.py serial-plan `
        --campaign-id serial-pilot-2026-08-15

    # REAL small explicit 4-5 model serial pilot (connect then mini gate,
    # strictly one model at a time).
    python .\\scripts\\run_agent_helper_campaign.py serial-execute `
        --campaign-id serial-pilot-2026-08-15 --confirm `
        --models "qwen3-coder:30b,llama3.1:8b,deepseek-coder-v2:16b,phi4:14b"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from agent_helper_eval import (
    catalog,
    historical_adapter,
    live_gates,
    local_lock,
    ollama_client,
    ollama_inventory,
    orchestrator,
    repair,
    serial_campaign,
    storage,
)
from agent_helper_eval.aggregate import build_all_aggregates
from agent_helper_eval.model_inventory import load_inventory
from agent_helper_eval.report import CampaignReportData, render_report
from agent_helper_eval.schema import utc_now_iso

ROOT = Path(__file__).resolve().parents[1]


def _cmd_dry_run(args: argparse.Namespace) -> int:
    tasks_kwargs: dict = {}
    if args.catalog is not None:
        doc = catalog.load_catalog_document(args.catalog)
        print(f"catalog_file:         {args.catalog}")
        print(f"catalog_id:           {doc.metadata.catalog_id}")
        print(f"catalog_author:       {doc.metadata.author}")
        print(f"catalog_created_at:   {doc.metadata.created_at}")
        print(f"catalog_task_count:   {len(doc.tasks)}")
        tasks_kwargs = dict(
            tasks=doc.tasks,
            benchmark_set=doc.metadata.catalog_id,
            benchmark_version=doc.catalog_schema_version,
        )
    result = orchestrator.run_dry_run(
        ROOT, campaign_id=args.campaign_id, seed=args.seed, **tasks_kwargs
    )
    print(f"campaign_id:          {result.campaign_id}")
    print(f"output_dir:           {result.output_dir}")
    print(f"sqlite_db:            {result.db_path}")
    print(f"samples_csv:          {result.samples_csv}")
    print(f"aggregates_csv:       {result.aggregates_csv}")
    print(f"capacity_profile_csv: {result.capacity_profile_csv}")
    print(f"report_html:          {result.report_path}")
    print(f"sample_count:         {result.sample_count}")
    print(f"aggregate_count:      {result.aggregate_count}")
    print(f"capacity_profile_count: {result.capacity_profile_count}")
    return 0


def _cmd_connect_gate_plan(args: argparse.Namespace) -> int:
    models = load_inventory(args.inventory)
    plan = orchestrator.build_connect_gate_plan(models)
    print(
        "The following commands are NOT executed by this script. Review and "
        "run at most one at a time, respecting the max-one-local-model lock."
    )
    for check in plan:
        print("-" * 72)
        print(f"backend:       {check.backend}")
        print(f"model:         {check.model}")
        print(f"command:       {check.command}")
        print(f"description:   {check.description}")
        print(f"pass_criteria: {check.pass_criteria}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    report_path = orchestrator.build_combined_report(ROOT)
    if report_path is None:
        print("No agent-helper campaign directories with data found yet.")
        return 1
    print(f"report_html: {report_path}")
    return 0


def _cmd_import_legacy(args: argparse.Namespace) -> int:
    campaign_id = args.campaign_id or f"legacy-import-{args.csv.stem}"
    samples = historical_adapter.import_legacy_history_csv(args.csv, campaign_id=campaign_id)
    if not samples:
        print(f"No rows found in {args.csv}")
        return 1

    output_dir = orchestrator.campaign_output_dir(ROOT, campaign_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = output_dir / storage.DB_FILENAME
    conn = storage.connect(db_path)
    try:
        storage.insert_samples(conn, samples)
        aggregates = build_all_aggregates(samples, provenance="historical_import")
        storage.insert_aggregates(conn, aggregates)
        samples_csv = output_dir / storage.SAMPLES_CSV_FILENAME
        aggregates_csv = output_dir / storage.AGGREGATES_CSV_FILENAME
        storage.export_samples_csv(conn, samples_csv, campaign_id=campaign_id)
        storage.export_aggregates_csv(conn, aggregates_csv, campaign_id=campaign_id)
        stored_samples = storage.fetch_samples(conn, campaign_id=campaign_id)
        stored_aggregates = storage.fetch_aggregates(conn, campaign_id=campaign_id)
    finally:
        conn.close()

    local_report = output_dir / "report.html"
    local_report.write_text(
        render_report(
            [
                CampaignReportData(
                    campaign_id=campaign_id,
                    generated_at=utc_now_iso(),
                    title=f"{campaign_id} (historical import, legacy migration_llm_bench)",
                    aggregates=stored_aggregates,
                    samples=stored_samples,
                )
            ]
        ),
        encoding="utf-8",
    )
    combined = orchestrator.build_combined_report(ROOT)

    print(f"campaign_id:    {campaign_id}")
    print(f"output_dir:     {output_dir}")
    print(f"imported rows:  {len(samples)}")
    print(f"samples_csv:    {samples_csv}")
    print(f"aggregates_csv: {aggregates_csv}")
    print(f"report_html:    {combined or local_report}")
    return 0


def _cmd_recompute_campaign(args: argparse.Namespace) -> int:
    try:
        result = repair.recompute_campaign(ROOT, campaign_id=args.campaign_id)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"campaign_id:            {result.campaign_id}")
    print(f"output_dir:             {result.output_dir}")
    print(f"repaired_sample_count:  {result.repaired_sample_count}")
    print(f"sample_count:           {result.sample_count}")
    print(f"aggregate_count:        {result.aggregate_count}")
    print(f"samples_csv:            {result.samples_csv}")
    print(f"aggregates_csv:         {result.aggregates_csv}")
    print(f"report_html:            {result.combined_report_path or result.report_path}")
    return 0


def _print_live_gate_result(gate_name: str, result: "live_gates.LiveGateResult") -> None:
    sample = result.sample
    print(f"gate:               {gate_name}")
    print(f"campaign_id:        {sample.campaign_id}")
    print(f"sample_id:          {sample.sample_id}")
    print(f"model:              {sample.backend}/{sample.model}")
    print(f"status:             {sample.status}")
    print(f"acceptance_status:  {sample.acceptance_status}")
    if sample.acceptance_reasons:
        print(f"acceptance_reasons: {sample.acceptance_reasons}")
    print(f"deterministic_score: {sample.deterministic_score}")
    print(f"elapsed_seconds:    {sample.elapsed_seconds}")
    print(f"tokens_per_second:  {sample.tokens_per_second}")
    print(f"ttft_seconds:       {sample.ttft_seconds}")
    print(f"artifact_path:      {result.output_dir / sample.artifact_path if sample.artifact_path else 'N/A'}")
    print(f"sqlite_db:          {result.db_path}")
    print(f"samples_csv:        {result.samples_csv}")
    print(f"aggregates_csv:     {result.aggregates_csv}")
    print(f"report_html:        {result.combined_report_path or result.report_path}")


def _cmd_connect_gate_run(args: argparse.Namespace) -> int:
    kwargs = dict(
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        allow_reuse_loaded_model=args.allow_reuse_loaded_model,
        provider=args.provider,
        runtime=args.runtime,
        quantization=args.quantization,
        seed=args.seed,
        num_predict=args.num_predict,
        num_ctx=args.num_ctx,
        hardware_profile=args.hardware_profile,
        hardware_gpu_model=args.hardware_gpu_model,
        hardware_vram_total_mb=args.hardware_vram_total_mb,
        lock_wait_seconds=args.local_lease_wait_seconds,
    )
    if args.keep_alive is not None:
        kwargs["keep_alive"] = args.keep_alive
    try:
        result = live_gates.run_ollama_connect_gate(ROOT, campaign_id=args.campaign_id, model=args.model, **kwargs)
    except live_gates.LiveGateRefusedError as exc:
        print(f"REFUSED (nothing attempted, nothing persisted): {exc}")
        return 2
    _print_live_gate_result("connect-gate-run", result)
    return 0


def _cmd_mini_gate_run(args: argparse.Namespace) -> int:
    kwargs = dict(
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        test_timeout_seconds=args.test_timeout_seconds,
        allow_reuse_loaded_model=args.allow_reuse_loaded_model,
        provider=args.provider,
        runtime=args.runtime,
        quantization=args.quantization,
        seed=args.seed,
        num_predict=args.num_predict,
        num_ctx=args.num_ctx,
        hardware_profile=args.hardware_profile,
        hardware_gpu_model=args.hardware_gpu_model,
        hardware_vram_total_mb=args.hardware_vram_total_mb,
        lock_wait_seconds=args.local_lease_wait_seconds,
    )
    if args.keep_alive is not None:
        kwargs["keep_alive"] = args.keep_alive
    try:
        result = live_gates.run_ollama_mini_gate(ROOT, campaign_id=args.campaign_id, model=args.model, **kwargs)
    except live_gates.LiveGateRefusedError as exc:
        print(f"REFUSED (nothing attempted, nothing persisted): {exc}")
        return 2
    _print_live_gate_result("mini-gate-run", result)
    return 0


def _parse_csv_list(value: Optional[str]) -> Optional[tuple]:
    if value is None:
        return None
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or None


def _cmd_ollama_inventory_snapshot(args: argparse.Namespace) -> int:
    result = ollama_inventory.discover_ollama_inventory(
        args.base_url,
        ollama_client.urllib_json_transport,
        tags_timeout_seconds=args.tags_timeout_seconds,
        show_timeout_seconds=args.show_timeout_seconds,
    )
    output_dir = orchestrator.campaign_output_dir(ROOT, args.campaign_id)
    snapshot_path = ollama_inventory.save_inventory_snapshot(result, output_dir)
    print(f"schema_version: {result.schema_version}")
    print(f"generated_at:   {result.generated_at}")
    print(f"base_url:       {result.base_url}")
    if result.tags_error:
        print(f"tags_error:     {result.tags_error}")
    print(f"model_count:    {len(result.models)}")
    cloud_count = sum(1 for m in result.models if m.is_cloud)
    print(f"cloud_count:    {cloud_count}")
    for model in result.models:
        size_gb = f"{model.size_bytes / 1_000_000_000.0:.1f}GB" if model.size_bytes is not None else "N/A"
        cloud_marker = " [cloud]" if model.is_cloud else ""
        show_err = f" (api/show error: {model.show_error})" if model.show_error else ""
        print(f"  - {model.tag}: {size_gb}, architecture={model.architecture}, quantization={model.quantization}{cloud_marker}{show_err}")
    print(f"snapshot_path:  {snapshot_path}")
    return 0


def _load_discovery_for_plan(args: argparse.Namespace) -> "ollama_inventory.DiscoveryResult":
    if args.snapshot is not None:
        return ollama_inventory.load_inventory_snapshot(args.snapshot)
    # No snapshot given: discover fresh (list/show only, never
    # loads/generates) and persist it for provenance, same as the dedicated
    # ollama-inventory-snapshot subcommand.
    result = ollama_inventory.discover_ollama_inventory(args.base_url, ollama_client.urllib_json_transport)
    output_dir = orchestrator.campaign_output_dir(ROOT, args.campaign_id)
    ollama_inventory.save_inventory_snapshot(result, output_dir)
    return result


def _build_plan_from_args(args: argparse.Namespace) -> "serial_campaign.SerialPlan":
    discovery = _load_discovery_for_plan(args)
    models = _parse_csv_list(args.models)
    plan = serial_campaign.build_serial_plan(
        discovery,
        args.campaign_id,
        generated_at=utc_now_iso(),
        models=models,
        include_cloud=args.include_cloud,
        max_size_gb=args.max_size_gb,
        include_architectures=_parse_csv_list(args.include_architecture),
        exclude_architectures=_parse_csv_list(args.exclude_architecture),
        include_quantizations=_parse_csv_list(args.include_quantization),
        exclude_quantizations=_parse_csv_list(args.exclude_quantization),
        force_include=_parse_csv_list(args.force_include),
    )
    return plan


def _print_plan(plan: "serial_campaign.SerialPlan") -> None:
    print(f"schema_version: {plan.schema_version}")
    print(f"campaign_id:    {plan.campaign_id}")
    print(f"selection_mode: {plan.selection_mode}")
    print(f"included ({len(plan.included)}), in strict execution order:")
    for entry in plan.included:
        size = f"{entry.size_gb:.1f}GB" if entry.size_gb is not None else "N/A"
        print(f"  {entry.order}. {entry.tag} (size={size}, architecture={entry.architecture}, quantization={entry.quantization})")
    print(f"deferred ({len(plan.deferred)}), NOT executed unless the selection is widened:")
    for deferred in plan.deferred:
        print(f"  - {deferred.tag}: {deferred.reason}")


def _cmd_serial_plan(args: argparse.Namespace) -> int:
    plan = _build_plan_from_args(args)
    output_dir = orchestrator.campaign_output_dir(ROOT, args.campaign_id)
    plan_path = serial_campaign.save_serial_plan(plan, output_dir)
    _print_plan(plan)
    print(f"plan_path:      {plan_path}")
    print("This is a plan ONLY -- nothing was executed. Use 'serial-execute --confirm ...' to run it.")
    return 0


def _has_explicit_selection(args: argparse.Namespace) -> bool:
    return bool(
        args.models
        or args.max_size_gb is not None
        or args.include_architecture
        or args.exclude_architecture
        or args.include_quantization
        or args.exclude_quantization
        or args.force_include
        or args.include_cloud
    )


def _cmd_serial_execute(args: argparse.Namespace) -> int:
    plan = _build_plan_from_args(args)
    output_dir = orchestrator.campaign_output_dir(ROOT, args.campaign_id)
    serial_campaign.save_serial_plan(plan, output_dir)
    _print_plan(plan)

    if not args.confirm:
        print("DRY PLAN ONLY (no --confirm given) -- nothing was executed.")
        return 0
    if not _has_explicit_selection(args):
        print(
            "REFUSED: --confirm requires an explicit --models list or at least one "
            "filter flag (--max-size-gb/--include-architecture/--exclude-architecture/"
            "--include-quantization/--exclude-quantization/--force-include/--include-cloud). "
            "Running every discovered model by accident is never allowed."
        )
        return 2
    if not plan.included:
        print("Nothing to execute: the plan's 'included' list is empty.")
        return 1

    connect_gate_kwargs = dict(
        base_url=args.base_url,
        timeout_seconds=args.connect_timeout_seconds,
        allow_reuse_loaded_model=args.allow_reuse_loaded_model,
        provider=args.provider,
        runtime=args.runtime,
        quantization=args.quantization,
        seed=args.seed,
        hardware_profile=args.hardware_profile,
        hardware_gpu_model=args.hardware_gpu_model,
        hardware_vram_total_mb=args.hardware_vram_total_mb,
    )
    mini_gate_kwargs = dict(
        base_url=args.base_url,
        timeout_seconds=args.mini_timeout_seconds,
        test_timeout_seconds=args.mini_test_timeout_seconds,
        allow_reuse_loaded_model=args.allow_reuse_loaded_model,
        provider=args.provider,
        runtime=args.runtime,
        quantization=args.quantization,
        seed=args.seed,
        hardware_profile=args.hardware_profile,
        hardware_gpu_model=args.hardware_gpu_model,
        hardware_vram_total_mb=args.hardware_vram_total_mb,
    )

    result = serial_campaign.run_serial_campaign(
        ROOT,
        plan,
        confirm=True,
        resume=not args.no_resume,
        force_rerun=_parse_csv_list(args.force_rerun),
        max_retries=args.max_retries,
        cooldown_seconds=args.cooldown_seconds,
        halt_on_preflight_refusal=not args.continue_on_refusal,
        halt_on_gate_exception=args.halt_on_gate_exception,
        connect_gate_kwargs=connect_gate_kwargs,
        mini_gate_kwargs=mini_gate_kwargs,
    )

    print("-" * 72)
    print(f"executed:                 {result.executed}")
    print(f"interrupted:              {result.interrupted}")
    print(f"halted_on_refusal:        {result.halted_on_refusal}")
    print(f"halted_on_gate_exception: {result.halted_on_gate_exception}")
    print(f"checkpoint_path:          {result.checkpoint_path}")
    for step in result.steps:
        detail = step.sample_id or step.skip_reason or step.refusal_reason or ""
        print(f"  [{step.model}] {step.gate}: {step.outcome}  {detail}")
    return 0


def _add_live_gate_common_arguments(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--campaign-id", required=True, help="Isolated campaign directory name under benchmark_results/agent-helper/.")
    subparser.add_argument("--model", required=True, help="Exact Ollama model tag, e.g. qwen3-coder:30b. Never a list/inventory.")
    subparser.add_argument("--base-url", default=ollama_client.DEFAULT_BASE_URL)
    subparser.add_argument(
        "--allow-reuse-loaded-model",
        action="store_true",
        help=(
            "Permit reusing a model Ollama already reports as loaded, if and "
            "only if it is exactly --model. Without this flag, ANY already-"
            "loaded model (including the same one) causes a refusal."
        ),
    )
    subparser.add_argument("--seed", type=int, default=ollama_client.DEFAULT_SEED)
    subparser.add_argument(
        "--keep-alive",
        default=None,
        help="Ollama keep_alive during generation (default depends on the gate); keep_alive=0 unload always happens afterwards regardless.",
    )
    subparser.add_argument("--provider", default="local")
    subparser.add_argument("--runtime", default="ollama")
    subparser.add_argument("--quantization", default=None)
    subparser.add_argument("--hardware-profile", default=None, help="Operator-declared hardware profile label (never inferred/fabricated).")
    subparser.add_argument("--hardware-gpu-model", default=None)
    subparser.add_argument("--hardware-vram-total-mb", type=float, default=None)
    subparser.add_argument(
        "--local-lease-wait-seconds",
        type=float,
        default=local_lock.DEFAULT_WAIT_SECONDS,
        help="Wait timeout for the shared local-llm lease (default: 3600 seconds).",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    dry_run = subparsers.add_parser("dry-run", help="Synthetic, no-model pipeline validation run.")
    dry_run.add_argument("--campaign-id", required=True)
    dry_run.add_argument("--seed", type=int, default=1234)
    dry_run.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help=(
            "Optional path to a benchmark catalog JSON file (agent-helper-catalog-v1 "
            "schema, including an externally authored one -- see "
            "benchmarks/agent-helper-catalog.example-external.json). Defaults to the "
            "bundled catalog.DEFAULT_CATALOG when omitted."
        ),
    )
    dry_run.set_defaults(func=_cmd_dry_run)

    connect_plan = subparsers.add_parser(
        "connect-gate-plan", help="Print (do not run) the connect-gate command per model."
    )
    connect_plan.add_argument(
        "--inventory",
        type=Path,
        default=ROOT / "benchmarks" / "agent-helper-model-inventory.example.json",
    )
    connect_plan.set_defaults(func=_cmd_connect_gate_plan)

    report = subparsers.add_parser("report", help="Rebuild the combined report from existing campaigns.")
    report.set_defaults(func=_cmd_report)

    import_legacy = subparsers.add_parser(
        "import-legacy", help="Import the legacy migration_llm_bench CSV into an isolated campaign."
    )
    import_legacy.add_argument("--csv", type=Path, required=True)
    import_legacy.add_argument("--campaign-id", default=None)
    import_legacy.set_defaults(func=_cmd_import_legacy)

    connect_gate_run = subparsers.add_parser(
        "connect-gate-run",
        help="REAL one-model Ollama connect/format-smoke check (reaches a running Ollama server).",
    )
    _add_live_gate_common_arguments(connect_gate_run)
    connect_gate_run.add_argument("--timeout-seconds", type=float, default=30.0)
    connect_gate_run.add_argument("--num-predict", type=int, default=16)
    connect_gate_run.add_argument("--num-ctx", type=int, default=512)
    connect_gate_run.set_defaults(func=_cmd_connect_gate_run)

    mini_gate_run = subparsers.add_parser(
        "mini-gate-run",
        help="REAL one-shot mini coding-task gate for one model (reaches a running Ollama server).",
    )
    _add_live_gate_common_arguments(mini_gate_run)
    mini_gate_run.add_argument("--timeout-seconds", type=float, default=180.0)
    mini_gate_run.add_argument("--test-timeout-seconds", type=float, default=20.0)
    mini_gate_run.add_argument("--num-predict", type=int, default=800)
    mini_gate_run.add_argument("--num-ctx", type=int, default=4096)
    mini_gate_run.set_defaults(func=_cmd_mini_gate_run)

    recompute_campaign = subparsers.add_parser(
        "recompute-campaign",
        help=(
            "No model call, ever. Idempotent maintenance pass: recompute an "
            "existing campaign's aggregates from its stored samples using "
            "current rubric/schema logic, repair the historical llm_queue/"
            "llm_request phase-timing bug fingerprint where recoverable from "
            "artifact JSON, re-export both CSVs, and rebuild the report."
        ),
    )
    recompute_campaign.add_argument("--campaign-id", required=True)
    recompute_campaign.set_defaults(func=_cmd_recompute_campaign)

    inventory_snapshot = subparsers.add_parser(
        "ollama-inventory-snapshot",
        help=(
            "REAL, read-only Ollama discovery: GET /api/tags + POST /api/show only "
            "(never loads or generates). Persists a JSON snapshot into the "
            "campaign's own output directory; never overwrites the hand-curated "
            "example inventory."
        ),
    )
    inventory_snapshot.add_argument("--campaign-id", required=True)
    inventory_snapshot.add_argument("--base-url", default=ollama_client.DEFAULT_BASE_URL)
    inventory_snapshot.add_argument("--tags-timeout-seconds", type=float, default=10.0)
    inventory_snapshot.add_argument("--show-timeout-seconds", type=float, default=10.0)
    inventory_snapshot.set_defaults(func=_cmd_ollama_inventory_snapshot)

    def add_serial_selection_arguments(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--campaign-id", required=True)
        subparser.add_argument("--base-url", default=ollama_client.DEFAULT_BASE_URL)
        subparser.add_argument(
            "--snapshot",
            type=Path,
            default=None,
            help=(
                "Reuse a previously saved ollama_inventory_snapshot.json instead of "
                "discovering fresh. When omitted, discovery runs fresh (list/show "
                "only, never loads/generates) and is also saved for provenance."
            ),
        )
        subparser.add_argument(
            "--models",
            default=None,
            help=(
                "Comma-separated explicit tag list, e.g. 'qwen3-coder:30b,llama3.1:8b'. "
                "When given, every other discovered tag is recorded as deferred for "
                "full transparency. Mutually exclusive in spirit with the filter flags "
                "below (filters are ignored once --models is given)."
            ),
        )
        subparser.add_argument("--include-cloud", action="store_true", help="Include cloud-tagged models (excluded by default).")
        subparser.add_argument("--max-size-gb", type=float, default=None)
        subparser.add_argument("--include-architecture", default=None, help="Comma-separated allow-list, e.g. 'dense' or 'moe,dense'.")
        subparser.add_argument("--exclude-architecture", default=None, help="Comma-separated deny-list.")
        subparser.add_argument("--include-quantization", default=None, help="Comma-separated allow-list, e.g. 'q4_k_m'.")
        subparser.add_argument("--exclude-quantization", default=None, help="Comma-separated deny-list.")
        subparser.add_argument(
            "--force-include",
            default=None,
            help=(
                "Comma-separated tags to include despite an exclusion filter match "
                "(for example an explicitly wanted huge/expected-fail model). The "
                "original exclusion reason is preserved in the plan's deferred-style "
                "notes for transparency."
            ),
        )

    serial_plan = subparsers.add_parser(
        "serial-plan",
        help=(
            "Always a DRY plan -- never executes anything. Shows exactly which "
            "discovered models would be attempted (in order) and which would be "
            "deferred/excluded, with a mandatory reason for every deferral."
        ),
    )
    add_serial_selection_arguments(serial_plan)
    serial_plan.set_defaults(func=_cmd_serial_plan)

    serial_execute = subparsers.add_parser(
        "serial-execute",
        help=(
            "REAL, strictly sequential connect-then-mini gate campaign across "
            "several models, one model fully at a time. Without --confirm this "
            "only prints the plan (identical to serial-plan) and executes "
            "nothing. With --confirm, an explicit --models list or at least one "
            "filter flag is also required -- running every discovered model by "
            "accident is never allowed."
        ),
    )
    add_serial_selection_arguments(serial_execute)
    serial_execute.add_argument("--confirm", action="store_true", help="Required to actually execute anything.")
    serial_execute.add_argument("--no-resume", action="store_true", help="Disable resume-skip entirely: every model in the plan is attempted again, even if a sample already exists. Prefer --force-rerun for a narrower override.")
    serial_execute.add_argument("--force-rerun", default=None, help="Comma-separated tags to re-attempt even though a sample already exists for them.")
    serial_execute.add_argument("--max-retries", type=int, default=0, help="Retries ONLY a raised transient status=='error' sample; never a timeout or a scored rejection.")
    serial_execute.add_argument("--cooldown-seconds", type=float, default=0.0, help="Sleep between each model's full connect+mini attempt sequence.")
    serial_execute.add_argument("--continue-on-refusal", action="store_true", help="By default a preflight/lock refusal halts the whole campaign; this flag continues past it instead.")
    serial_execute.add_argument("--halt-on-gate-exception", action="store_true", help="By default an unexpected gate-boundary exception (e.g. a workspace/subprocess error) is persisted as a failed sample and the campaign continues to the next model; this flag halts the whole campaign instead.")
    serial_execute.add_argument("--allow-reuse-loaded-model", action="store_true")
    serial_execute.add_argument("--seed", type=int, default=ollama_client.DEFAULT_SEED)
    serial_execute.add_argument("--provider", default="local")
    serial_execute.add_argument("--runtime", default="ollama")
    serial_execute.add_argument("--quantization", default=None)
    serial_execute.add_argument("--hardware-profile", default=None)
    serial_execute.add_argument("--hardware-gpu-model", default=None)
    serial_execute.add_argument("--hardware-vram-total-mb", type=float, default=None)
    serial_execute.add_argument("--connect-timeout-seconds", type=float, default=30.0)
    serial_execute.add_argument("--mini-timeout-seconds", type=float, default=180.0)
    serial_execute.add_argument("--mini-test-timeout-seconds", type=float, default=20.0)
    serial_execute.set_defaults(func=_cmd_serial_execute)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
