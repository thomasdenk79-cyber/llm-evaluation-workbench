"""Idempotent, no-model-call maintenance utilities for already-persisted
agent-helper campaign data.

This module never talks to any model/API -- it only repairs and recomputes
already-stored SQLite/CSV data for one existing campaign directory, using
data that was already captured (typically the raw Ollama response fields
saved in that sample's own artifact JSON). It exists because two things are
true about this harness's design:

1. ``orchestrator.build_combined_report`` (and each gate's own
   ``_persist_and_report``) only *re-render* already-computed aggregate
   rows for every campaign except the one currently being appended to; they
   never recompute a *stale* campaign's aggregates from its samples. If a
   bug in the rubric/phase-timing logic that *builds*
   :class:`~agent_helper_eval.schema.AggregateRecord`/populates
   :class:`~agent_helper_eval.schema.SampleRecord` phase-timing fields is
   fixed, a campaign that already ran before the fix keeps its *old*, wrong
   aggregate rows and report until something explicitly recomputes them.
2. A one-time historical mislabeling bug (see
   ``docs/project/agent_helper_benchmark.md`` SS16.5, "pilot review
   remediation") had ``agent_helper_eval.live_gates`` write
   ``llm_queue_seconds`` from Ollama's ``load_duration`` (cold model-load
   time, not real queue time) while leaving ``llm_request_seconds`` unset.
   The true client-measured HTTP-wall duration was never persisted for
   those historical rows, but Ollama's own self-reported
   ``total_duration``/``load_duration`` *is* still available in each
   sample's on-disk artifact JSON -- so those specific historical rows can
   be honestly reclassified using data that was already captured, without
   re-running anything.

Use ``scripts/run_agent_helper_campaign.py recompute-campaign --campaign-id
<id>`` to invoke this; see that CLI's ``--help`` for the exact safe command.
Never calls any model/API; safe to run repeatedly (fully idempotent).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional

from . import ollama_client, orchestrator, storage
from .aggregate import build_all_aggregates
from .report import CampaignReportData, render_report
from .schema import SampleRecord, utc_now_iso, validate_sample


@dataclasses.dataclass
class RecomputeCampaignResult:
    campaign_id: str
    output_dir: Path
    repaired_sample_count: int
    sample_count: int
    aggregate_count: int
    samples_csv: Path
    aggregates_csv: Path
    report_path: Optional[Path]
    combined_report_path: Optional[Path]


def _looks_like_legacy_queue_mislabel(sample: SampleRecord) -> bool:
    """Fingerprint of the historical ``live_gates`` phase-timing bug: a
    measured Ollama sample with a populated ``llm_queue_seconds`` (actually
    cold-load time) and no ``llm_request_seconds`` at all. Deliberately
    narrow -- never touches a sample that already has ``llm_request_seconds``
    populated (idempotent: running this pass a second time is a no-op), and
    never touches non-Ollama/non-measured rows.
    """

    return (
        sample.backend == "ollama"
        and sample.provenance == "measured"
        and sample.llm_request_seconds is None
        and sample.llm_queue_seconds is not None
    )


def _load_artifact_ollama_durations(
    output_dir: Path, sample: SampleRecord
) -> Optional[dict]:
    """Best-effort re-derivation of Ollama's self-reported
    total/load duration (seconds) from a sample's own already-written
    artifact JSON. Returns ``None`` if the artifact is missing or does not
    have the expected raw-Ollama-response shape -- never raises; a missing
    or unparseable artifact simply means that one sample cannot be repaired,
    not a hard failure of the whole recompute pass.
    """

    if not sample.artifact_path:
        return None
    artifact_file = output_dir / sample.artifact_path
    if not artifact_file.exists():
        return None
    try:
        payload = json.loads(artifact_file.read_text(encoding="utf-8"))
        raw_lines = payload.get("raw_response_lines") or []
        if not raw_lines:
            return None
        final_line = json.loads(raw_lines[-1])
        total_ns = final_line.get("total_duration")
        load_ns = final_line.get("load_duration")
        if total_ns is None:
            return None
        return {
            "llm_request_seconds": ollama_client.ns_to_seconds(total_ns),
            "model_load_seconds": (
                ollama_client.ns_to_seconds(load_ns) if load_ns is not None else None
            ),
        }
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError, OSError):
        return None


def repair_legacy_llm_phase_timing(
    output_dir: Path, sample: SampleRecord
) -> tuple[SampleRecord, bool]:
    """Return ``(possibly-corrected sample, was_repaired)``.

    Only touches samples matching the exact historical bug fingerprint (see
    :func:`_looks_like_legacy_queue_mislabel`) and only when the real
    Ollama durations can still be recovered from that sample's own artifact
    JSON. Reclassifies the mislabeled ``llm_queue_seconds`` (really cold-load
    time) into the new ``model_load_seconds`` diagnostic field, clears
    ``llm_queue_seconds`` back to a genuine ``None`` (no real queuing
    happened in a single-shot connect/mini gate -- the lock is fail-fast,
    never blocking), and populates ``llm_request_seconds`` with Ollama's own
    measured ``total_duration`` -- a real measurement, just read from
    Ollama's own clock rather than the client wall-clock instrumentation
    used by every run after this fix.
    ``orchestrator_cpu_time_seconds``/``model_cpu_time_seconds`` cannot be
    recovered retroactively (never captured anywhere for these historical
    rows) and correctly stay ``None``/N/A.
    """

    if not _looks_like_legacy_queue_mislabel(sample):
        return sample, False
    durations = _load_artifact_ollama_durations(output_dir, sample)
    if durations is None:
        return sample, False
    repaired = dataclasses.replace(
        sample,
        llm_request_seconds=durations["llm_request_seconds"],
        model_load_seconds=durations["model_load_seconds"],
        llm_queue_seconds=None,
    )
    errors = validate_sample(repaired)
    if errors:
        # Never persist a repair that would fail the schema's own honesty
        # checks (e.g. the overlap-sum invariant) -- leave the sample
        # untouched; the caller's repaired-count simply won't include it.
        return sample, False
    return repaired, True


def recompute_campaign(repo_root: Path, campaign_id: str) -> RecomputeCampaignResult:
    """Idempotent, no-model-call maintenance pass for one already-existing
    campaign directory under ``benchmark_results/agent-helper/<campaign_id>/``.

    1. Opens the campaign's SQLite database (this alone additively migrates
       an older on-disk schema -- see ``storage._add_missing_columns()`` --
       so a campaign created before a later, backward-compatible schema
       addition keeps working).
    2. Applies :func:`repair_legacy_llm_phase_timing` to every stored sample
       (a narrow, idempotent, no-op-when-already-correct repair -- see its
       docstring).
    3. Recomputes every :class:`~agent_helper_eval.schema.AggregateRecord`
       from the (possibly repaired) stored samples using the *current*
       rubric/evidence-stage/phase-timing logic -- this is what actually
       re-labels an old over-confident suitability verdict down to
       ``gate-passed-provisional`` once evidence-stage capping applies.
    4. Re-exports both canonical CSVs and rebuilds the local + combined HTML
       report from the corrected SQLite data.

    Never calls any model/API. Safe to run repeatedly (fully idempotent --
    a second run against already-corrected data changes nothing).
    """

    output_dir = orchestrator.campaign_output_dir(repo_root, campaign_id)
    db_path = output_dir / storage.DB_FILENAME
    if not db_path.exists():
        raise FileNotFoundError(
            f"no campaign database found at {db_path} -- nothing to recompute"
        )

    conn = storage.connect(db_path)
    try:
        stored_samples = storage.fetch_samples(conn, campaign_id=campaign_id)
        repaired_samples: list[SampleRecord] = []
        repaired_count = 0
        for sample in stored_samples:
            fixed, was_repaired = repair_legacy_llm_phase_timing(output_dir, sample)
            repaired_samples.append(fixed)
            if was_repaired:
                repaired_count += 1
        if repaired_count:
            storage.insert_samples(conn, repaired_samples)
            stored_samples = storage.fetch_samples(conn, campaign_id=campaign_id)

        provenance = stored_samples[0].provenance if stored_samples else "measured"
        aggregates = build_all_aggregates(stored_samples, provenance=provenance)
        storage.insert_aggregates(conn, aggregates)

        samples_csv = output_dir / storage.SAMPLES_CSV_FILENAME
        aggregates_csv = output_dir / storage.AGGREGATES_CSV_FILENAME
        storage.export_samples_csv(conn, samples_csv, campaign_id=campaign_id)
        storage.export_aggregates_csv(conn, aggregates_csv, campaign_id=campaign_id)
        stored_aggregates = storage.fetch_aggregates(conn, campaign_id=campaign_id)
        stored_capacity_profiles = storage.fetch_capacity_profiles(conn, campaign_id=campaign_id)
    finally:
        conn.close()

    local_report_path = output_dir / "report.html"
    local_report_path.write_text(
        render_report(
            [
                CampaignReportData(
                    campaign_id=campaign_id,
                    generated_at=utc_now_iso(),
                    title=f"{campaign_id} (recomputed)",
                    aggregates=stored_aggregates,
                    samples=stored_samples,
                    capacity_profiles=stored_capacity_profiles,
                )
            ],
            model_inventory=orchestrator.load_default_model_inventory(repo_root),
        ),
        encoding="utf-8",
    )
    combined_report_path = orchestrator.build_combined_report(repo_root)

    return RecomputeCampaignResult(
        campaign_id=campaign_id,
        output_dir=output_dir,
        repaired_sample_count=repaired_count,
        sample_count=len(stored_samples),
        aggregate_count=len(stored_aggregates),
        samples_csv=samples_csv,
        aggregates_csv=aggregates_csv,
        report_path=local_report_path,
        combined_report_path=combined_report_path,
    )
