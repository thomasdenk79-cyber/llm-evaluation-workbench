"""Fast, deterministic unit tests for the agent-helper evaluation harness.

These tests never call a model, network, Ollama, or llama.cpp process. They
exercise: schema validation, aggregation/percentiles, the historical CSV
adapter, HTML escaping/collapse behavior in the report generator, and a
SQLite/CSV round-trip. Run with::

    python -m unittest test_agent_helper_eval -v

from within ``scripts/`` (matches the existing project test convention).
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as _dt
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agent_helper_eval import (
    capacity_profile,
    catalog,
    charts,
    fork_build,
    historical_adapter,
    live_gates,
    local_lock,
    mini_task,
    ollama_client,
    ollama_inventory,
    orchestrator,
    phase_timing,
    repair,
    resource_monitor,
    rubric,
    serial_campaign,
    storage,
)
from agent_helper_eval.aggregate import build_all_aggregates, percentile
from agent_helper_eval.model_inventory import FeasibilityProjection, ModelSpec
from agent_helper_eval.report import esc, render_artifact_link, render_report
from agent_helper_eval.report import CampaignReportData
from agent_helper_eval.schema import (
    CAPACITY_PROFILE_CSV_COLUMNS,
    AggregateRecord,
    CapacityProfileRecord,
    SAMPLE_CSV_COLUMNS,
    SCHEMA_VERSION,
    SampleRecord,
    utc_now_iso,
    validate_aggregate,
    validate_capacity_profile,
    validate_sample,
)

import run_agent_helper_campaign as campaign_cli


def _sample(**overrides) -> SampleRecord:
    """Build a minimally valid, ACCEPTED-by-default SampleRecord, with
    overrides for the fields a particular test wants to vary.

    ``acceptance_status``/``acceptance_reasons`` are auto-derived via
    :func:`agent_helper_eval.rubric.compute_sample_acceptance` from the
    resulting status/scores/flags -- exactly like every real call site must
    do -- unless a test explicitly overrides ``acceptance_status`` itself
    (for example to construct a deliberately inconsistent record for a
    schema-validation test)."""

    base = dict(
        schema_version=SCHEMA_VERSION,
        campaign_id="test-campaign",
        run_id="run-1",
        sample_id="sample-1",
        benchmark_set="agent-helper-catalog-v1",
        benchmark_version="agent-helper-catalog-v1",
        track="pure_model",
        task_id="connect-smoke-v1",
        task_name="Connect smoke",
        task_category="smoke",
        provider="local",
        backend="ollama",
        model="qwen3-coder:30b",
        runtime="ollama-runtime",
        quantization="Q4_K_M",
        start_time="2026-08-01T10:00:00.000+00:00",
        end_time="2026-08-01T10:00:05.000+00:00",
        elapsed_seconds=5.0,
        prompt_tokens=100,
        output_tokens=50,
        total_tokens=150,
        tokens_per_second=10.0,
        ttft_seconds=0.5,
        cpu_time_seconds=4.0,
        cpu_avg_percent=40.0,
        cpu_max_percent=80.0,
        ram_avg_mb=1000.0,
        ram_max_mb=2000.0,
        gpu_avg_percent=30.0,
        gpu_max_percent=60.0,
        vram_avg_mb=8000.0,
        vram_max_mb=9000.0,
        deterministic_score=90.0,
        reviewer_score=85.0,
        collaboration_score=80.0,
        composite_score=85.0,
        status="success",
        system_error_flag=False,
        system_error_code=None,
        system_error_message=None,
        retry_count=0,
        iteration_index=0,
        artifact_path="artifacts/sample-1.txt",
        artifact_hash="deadbeef",
        reviewer_provenance="synthetic",
        reviewer_notes=None,
        hardware_profile="test-rig",
        hardware_gpu_model="RTX 3060",
        hardware_vram_total_mb=12000.0,
        sample_seq=1,
        provenance="synthetic_dry_run",
        notes=None,
        unsafe_behavior_flag=None,
        output_placeholder_or_incomplete=None,
        acceptance_status="not_evaluated",
        acceptance_reasons=None,
    )
    base.update(overrides)
    if "acceptance_status" not in overrides:
        acceptance_status, acceptance_reasons = rubric.compute_sample_acceptance(
            status=base["status"],
            deterministic_score=base["deterministic_score"],
            reviewer_score=base["reviewer_score"],
            unsafe_behavior_flag=base["unsafe_behavior_flag"],
            output_placeholder_or_incomplete=base["output_placeholder_or_incomplete"],
        )
        base["acceptance_status"] = acceptance_status
        if "acceptance_reasons" not in overrides:
            base["acceptance_reasons"] = acceptance_reasons
    return SampleRecord(**base)


def _capacity_profile(**overrides) -> CapacityProfileRecord:
    """Build a minimally valid CapacityProfileRecord (concurrency_level=1,
    both mandatory preflight flags True) with overrides for whatever a
    particular test wants to vary."""

    base = dict(
        schema_version=SCHEMA_VERSION,
        campaign_id="test-campaign",
        run_id="run-1",
        benchmark_set="agent-helper-catalog-v1",
        backend="ollama",
        model="qwen3-coder:30b",
        runtime="ollama-runtime",
        quantization="Q4_K_M",
        concurrency_level=1,
        requests_issued=1,
        aggregate_tokens_per_second=40.0,
        per_request_tokens_per_second_mean=40.0,
        per_request_tokens_per_second_p50=40.0,
        per_request_tokens_per_second_p95=40.0,
        queue_seconds_mean=0.0,
        service_seconds_mean=0.5,
        latency_seconds_p95=2.0,
        gpu_avg_percent=60.0,
        gpu_max_percent=70.0,
        vram_avg_mb=7000.0,
        vram_max_mb=7500.0,
        ram_avg_mb=4000.0,
        ram_max_mb=4500.0,
        error_count=0,
        timeout_count=0,
        time_to_accepted_result_seconds=10.0,
        single_request_baseline_tokens_per_second=40.0,
        throughput_efficiency_percent=100.0,
        scaling_classification="scales-well",
        preflight_quality_gate_passed=True,
        preflight_memory_safety_checked=True,
        vram_budget_mb=12288.0,
        vram_headroom_mb=5000.0,
        notes=None,
        provenance="synthetic_dry_run",
        generated_at="2026-08-01T10:00:00.000+00:00",
    )
    base.update(overrides)
    return CapacityProfileRecord(**base)


def _model_spec(**overrides) -> ModelSpec:
    """Build a minimally valid ModelSpec (local Ollama, measured 12 GB fit,
    projected RTX 5090 fit) with overrides for whatever a particular test
    wants to vary."""

    base = dict(
        provider="local",
        backend="ollama",
        model_id="qwen3-coder:30b",
        display_name="Qwen3 Coder 30B (Ollama)",
        architecture="moe",
        params_billion=30.0,
        active_params_billion=3.3,
        quantization="Q4_K_M",
        context_length=131072,
        size_gb_on_disk=18.5,
        fits_12gb_vram=FeasibilityProjection(
            fits=True, confidence="measured", assumptions="Observed running locally."
        ),
        fits_rtx5090_32gb_projection=FeasibilityProjection(
            fits=True, confidence="projected", assumptions="Not tested on that hardware."
        ),
        notes=None,
    )
    base.update(overrides)
    return ModelSpec(**base)


def _aggregate(**overrides) -> AggregateRecord:
    """Build a minimally valid, USABLE-by-default AggregateRecord (passed
    the hard acceptance gate, tier-1-recommended, fully instrumented phase
    -attribution/utilization fields) with overrides for whatever a
    particular chart-rendering test wants to vary.

    Deliberately constructed directly (not run through
    :func:`agent_helper_eval.aggregate.build_all_aggregates`) so chart
    tests can freely control every field -- including phase-attribution
    percentages that require dedicated sample-level instrumentation fields
    to produce via the real pipeline -- in isolation; the aggregation
    pipeline itself already has dedicated ``AggregationTests``/
    ``PhaseTimingTests`` coverage elsewhere in this file."""

    base = dict(
        schema_version=SCHEMA_VERSION,
        campaign_id="test-campaign",
        run_id="run-1",
        benchmark_set="agent-helper-catalog-v1",
        benchmark_version="agent-helper-catalog-v1",
        track="pure_model",
        provider="local",
        backend="ollama",
        model="good-model:7b",
        runtime="ollama-runtime",
        quantization="Q4_K_M",
        task_count=5,
        success_count=5,
        error_count=0,
        skipped_count=0,
        success_rate_percent=100.0,
        error_rate_percent=0.0,
        elapsed_seconds_total=50.0,
        elapsed_seconds_mean=10.0,
        elapsed_seconds_p50=9.5,
        elapsed_seconds_p95=14.0,
        prompt_tokens_total=500,
        output_tokens_total=250,
        total_tokens_total=750,
        tokens_per_second_mean=25.0,
        ttft_seconds_mean=0.4,
        cpu_avg_percent_mean=40.0,
        cpu_max_percent_max=80.0,
        ram_avg_mb_mean=1000.0,
        ram_max_mb_max=2000.0,
        gpu_avg_percent_mean=30.0,
        gpu_max_percent_max=60.0,
        vram_avg_mb_mean=8000.0,
        vram_max_mb_max=9000.0,
        deterministic_score_mean=90.0,
        reviewer_score_mean=85.0,
        collaboration_score_mean=80.0,
        overall_score=87.0,
        retries_total=0,
        iterations_mean=1.0,
        campaign_elapsed_seconds_total=50.0,
        suitability_tier="tier-1-recommended",
        recommendation=None,
        notes=None,
        provenance="synthetic_dry_run",
        generated_at="2026-08-01T10:00:00.000+00:00",
        accepted_sample_count=5,
        not_usable_sample_count=0,
        not_evaluated_sample_count=0,
        acceptance_rate_percent=100.0,
        unsafe_sample_count=0,
        unresolved_task_count=0,
        time_to_accepted_result_seconds_mean=10.0,
        rework_tokens_to_accept_mean=150.0,
        hard_gate_failed=False,
        hard_gate_reasons=None,
        reviewer_evidence_is_heuristic_only=False,
        phase_timing_sample_count=5,
        llm_critical_path_percent=60.0,
        tool_critical_path_percent=20.0,
        queue_idle_critical_path_percent=15.0,
        orchestration_critical_path_percent=5.0,
        phase_timing_overlap_ratio_percent=2.0,
        phase_timing_unaccounted_ratio_percent=1.0,
        model_busy_percent=65.0,
        gpu_active_percent=55.0,
        tool_runner_busy_percent=20.0,
    )
    base.update(overrides)
    return AggregateRecord(**base)


class CatalogTests(unittest.TestCase):
    """Deterministic tests for the benchmark-catalog format and the
    external-catalog import contract (multi-agent workspace support)."""

    def _write_catalog(self, tmp: Path, payload: dict, filename: str = "catalog.json") -> Path:
        path = Path(tmp) / filename
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _valid_metadata_json(self, **overrides) -> dict:
        base = dict(
            catalog_id="unit-test-catalog-v1",
            author="unit-test-author",
            created_at="2026-08-01T00:00:00.000+00:00",
            source_notes="unit test fixture",
        )
        base.update(overrides)
        return base

    def _valid_task_json(self, **overrides) -> dict:
        base = dict(
            task_id="unit-test-task-v1",
            name="Unit test task",
            category="connectivity",
            tracks=["pure_model"],
            gates=["smoke"],
            description="A minimal valid task for catalog-loading tests.",
            deterministic_check_description=None,
            reviewer_rubric_hint=None,
            max_turns=1,
            timeout_seconds=60,
            requires_local_lock=True,
        )
        base.update(overrides)
        return base

    def _valid_catalog_json(self, **overrides) -> dict:
        base = dict(
            catalog_schema_version=catalog.CATALOG_SCHEMA_VERSION,
            catalog_metadata=self._valid_metadata_json(),
            tasks=[self._valid_task_json()],
        )
        base.update(overrides)
        return base

    def test_bundled_default_catalog_round_trips(self) -> None:
        metadata = catalog.CatalogMetadata(
            catalog_id="round-trip-test",
            author="unit-test",
            created_at="2026-08-01T00:00:00.000+00:00",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            catalog.save_catalog(list(catalog.DEFAULT_CATALOG), path, metadata=metadata)
            doc = catalog.load_catalog_document(path)
            # Legacy list-returning signature must still work unchanged.
            tasks_only = catalog.load_catalog(path)
        self.assertEqual(doc.catalog_schema_version, catalog.CATALOG_SCHEMA_VERSION)
        self.assertEqual(doc.metadata.catalog_id, "round-trip-test")
        self.assertEqual([t.task_id for t in doc.tasks], [t.task_id for t in catalog.DEFAULT_CATALOG])
        self.assertEqual(tasks_only, doc.tasks)

    def test_save_catalog_requires_metadata_argument(self) -> None:
        # save_catalog's signature requires metadata positionally/by keyword;
        # omitting it entirely is a TypeError at the call site, not a
        # CatalogValidationError -- this documents that contract.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            with self.assertRaises(TypeError):
                catalog.save_catalog(list(catalog.DEFAULT_CATALOG), path)  # type: ignore[call-arg]

    def test_external_example_catalog_file_loads_without_code_changes(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        example_path = repo_root / "benchmarks" / "agent-helper-catalog.example-external.json"
        self.assertTrue(example_path.exists(), "documented external-catalog example must exist")
        doc = catalog.load_catalog_document(example_path)
        self.assertEqual(doc.catalog_schema_version, catalog.CATALOG_SCHEMA_VERSION)
        self.assertTrue(doc.metadata.catalog_id)
        self.assertTrue(doc.metadata.author)
        self.assertEqual(len(doc.tasks), 1)
        self.assertEqual(doc.tasks[0].task_id, "example-external-smoke-v1")

    def test_missing_catalog_schema_version_is_rejected(self) -> None:
        payload = self._valid_catalog_json()
        del payload["catalog_schema_version"]
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError):
                catalog.load_catalog_document(path)

    def test_unrecognised_catalog_schema_version_is_rejected(self) -> None:
        payload = self._valid_catalog_json(catalog_schema_version="agent-helper-catalog-v99")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError) as ctx:
                catalog.load_catalog_document(path)
        self.assertIn("catalog_schema_version", str(ctx.exception))

    def test_foreign_benchmark_format_is_rejected_cleanly_not_as_typeerror(self) -> None:
        # Shape of the pre-existing legacy migration_llm_bench benchmark-set
        # files (benchmarks/swe-mixed-hard-24.json and similar) -- must never
        # be silently misparsed nor raise a raw TypeError/KeyError.
        payload = {
            "benchmark_id": "swe-mixed-hard-24",
            "name": "swe-mixed-hard-24",
            "spec_version": "2026-07-26",
            "scoring_profile": {"mode": "swe_lite", "pass_threshold": 85.0},
            "tasks": [{"id": "mixed_hotfix_sql", "title": "x", "prompt": "y", "required_keywords": []}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError):
                catalog.load_catalog_document(path)

    def test_missing_catalog_metadata_is_rejected(self) -> None:
        payload = self._valid_catalog_json()
        del payload["catalog_metadata"]
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError) as ctx:
                catalog.load_catalog_document(path)
        self.assertIn("catalog_metadata", str(ctx.exception))

    def test_empty_author_is_rejected_as_mandatory_provenance(self) -> None:
        payload = self._valid_catalog_json(catalog_metadata=self._valid_metadata_json(author=""))
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError) as ctx:
                catalog.load_catalog_document(path)
        self.assertIn("author", str(ctx.exception))

    def test_missing_created_at_is_rejected(self) -> None:
        payload = self._valid_catalog_json(catalog_metadata=self._valid_metadata_json(created_at=""))
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError) as ctx:
                catalog.load_catalog_document(path)
        self.assertIn("created_at", str(ctx.exception))

    def test_created_at_without_timezone_is_rejected(self) -> None:
        payload = self._valid_catalog_json(
            catalog_metadata=self._valid_metadata_json(created_at="2026-08-01T00:00:00")
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError) as ctx:
                catalog.load_catalog_document(path)
        self.assertIn("created_at", str(ctx.exception))

    def test_malformed_task_shape_raises_catalog_validation_error_not_typeerror(self) -> None:
        payload = self._valid_catalog_json(tasks=[{"unexpected_field": "no task_id here"}])
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError):
                catalog.load_catalog_document(path)

    def test_duplicate_task_id_still_rejected_via_document_loader(self) -> None:
        task = self._valid_task_json()
        payload = self._valid_catalog_json(tasks=[task, dict(task)])
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_catalog(tmp, payload)
            with self.assertRaises(catalog.CatalogValidationError) as ctx:
                catalog.load_catalog_document(path)
        self.assertIn("duplicate", str(ctx.exception))

    def test_not_valid_json_is_rejected_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(catalog.CatalogValidationError):
                catalog.load_catalog_document(path)

    def test_top_level_non_object_json_is_rejected_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text("[1, 2, 3]", encoding="utf-8")
            with self.assertRaises(catalog.CatalogValidationError):
                catalog.load_catalog_document(path)

    def test_generate_synthetic_samples_labels_benchmark_set_from_external_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            tasks = [catalog.TaskSpec.from_json(self._valid_task_json())]
            samples = orchestrator.generate_synthetic_samples(
                "ext-campaign",
                "ext-run",
                output_dir,
                tasks=tasks,
                benchmark_set="example-external-catalog-v1",
                benchmark_version="agent-helper-catalog-v1",
            )
        self.assertTrue(samples)
        self.assertTrue(all(s.benchmark_set == "example-external-catalog-v1" for s in samples))

    def test_generate_synthetic_samples_defaults_to_bundled_catalog_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            samples = orchestrator.generate_synthetic_samples(
                "default-campaign", "default-run", output_dir, seed=1
            )
        self.assertTrue(all(s.benchmark_set == "agent-helper-catalog-v1" for s in samples))


class SchemaValidationTests(unittest.TestCase):
    def test_valid_sample_has_no_errors(self) -> None:
        self.assertEqual(validate_sample(_sample()), [])

    def test_wrong_schema_version_is_rejected(self) -> None:
        errors = validate_sample(_sample(schema_version="agent-helper-v0"))
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_missing_required_string_is_rejected(self) -> None:
        errors = validate_sample(_sample(model=""))
        self.assertTrue(any("model" in e for e in errors))

    def test_bad_track_is_rejected(self) -> None:
        errors = validate_sample(_sample(track="not_a_track"))
        self.assertTrue(any("track" in e for e in errors))

    def test_timestamp_without_timezone_is_rejected(self) -> None:
        errors = validate_sample(_sample(start_time="2026-08-01T10:00:00"))
        self.assertTrue(any("start_time" in e for e in errors))

    def test_score_out_of_range_is_rejected(self) -> None:
        errors = validate_sample(_sample(reviewer_score=150.0))
        self.assertTrue(any("reviewer_score" in e for e in errors))

    def test_error_status_requires_error_flag(self) -> None:
        errors = validate_sample(_sample(status="error", system_error_flag=False))
        self.assertTrue(any("system_error_flag" in e for e in errors))

    def test_none_metrics_are_permitted(self) -> None:
        """Unavailable metrics (e.g. Copilot agent TPS) must validate as None,
        never rejected merely for being absent."""

        errors = validate_sample(
            _sample(
                tokens_per_second=None,
                ttft_seconds=None,
                cpu_avg_percent=None,
                ram_avg_mb=None,
                gpu_avg_percent=None,
                vram_avg_mb=None,
            )
        )
        self.assertEqual(errors, [])

    def test_bad_acceptance_status_is_rejected(self) -> None:
        errors = validate_sample(_sample(acceptance_status="maybe"))
        self.assertTrue(any("acceptance_status" in e for e in errors))

    def test_unsafe_flag_true_with_accepted_status_is_rejected(self) -> None:
        """A sample can never be flagged unsafe and still validate as
        'accepted' -- this is the load-bearing schema-level guarantee behind
        zero-tolerance for unsafe behavior."""

        errors = validate_sample(
            _sample(unsafe_behavior_flag=True, acceptance_status="accepted")
        )
        self.assertTrue(any("unsafe_behavior_flag" in e for e in errors))

    def test_placeholder_flag_true_with_accepted_status_is_rejected(self) -> None:
        errors = validate_sample(
            _sample(output_placeholder_or_incomplete=True, acceptance_status="accepted")
        )
        self.assertTrue(any("output_placeholder_or_incomplete" in e for e in errors))

    def test_default_sample_helper_is_accepted(self) -> None:
        """Sanity check on the test helper itself: with no overrides, the
        auto-derived acceptance_status must be 'accepted' (passing
        deterministic + reviewer scores, no unsafe/placeholder evidence)."""

        sample = _sample()
        self.assertEqual(sample.acceptance_status, "accepted")
        self.assertIsNone(sample.acceptance_reasons)


class AggregateSchemaValidationTests(unittest.TestCase):
    """Validation for the hard-gate consistency rules on AggregateRecord."""

    def _base_aggregate(self):
        samples = [
            _sample(sample_id=f"s-{i}", sample_seq=i, elapsed_seconds=float(i))
            for i in range(1, 4)
        ]
        return build_all_aggregates(samples, provenance="synthetic_dry_run")[0]

    def test_valid_aggregate_has_no_errors(self) -> None:
        self.assertEqual(validate_aggregate(self._base_aggregate()), [])

    def test_unsafe_sample_count_without_hard_gate_failed_is_rejected(self) -> None:
        tampered = dataclasses.replace(
            self._base_aggregate(), unsafe_sample_count=1, hard_gate_failed=False
        )
        errors = validate_aggregate(tampered)
        self.assertTrue(any("unsafe_sample_count" in e for e in errors))

    def test_hard_gate_failed_requires_not_usable_tier(self) -> None:
        tampered = dataclasses.replace(self._base_aggregate(), hard_gate_failed=True)
        errors = validate_aggregate(tampered)
        self.assertTrue(any("suitability_tier" in e for e in errors))

    def test_not_usable_tier_requires_hard_gate_failed(self) -> None:
        tampered = dataclasses.replace(
            self._base_aggregate(), suitability_tier="not-usable", hard_gate_failed=False
        )
        errors = validate_aggregate(tampered)
        self.assertTrue(any("hard_gate_failed" in e for e in errors))


class PercentileTests(unittest.TestCase):
    def test_single_value(self) -> None:
        self.assertEqual(percentile([42.0], 50), 42.0)
        self.assertEqual(percentile([42.0], 95), 42.0)

    def test_median_of_odd_count(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0], 50), 2.0)

    def test_p95_matches_numpy_linear_interpolation(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        # numpy.percentile(values, 95) == 9.55 for this data set.
        self.assertAlmostEqual(percentile(values, 95), 9.55, places=6)

    def test_empty_sequence_raises(self) -> None:
        with self.assertRaises(ValueError):
            percentile([], 50)

    def test_out_of_range_percentile_raises(self) -> None:
        with self.assertRaises(ValueError):
            percentile([1.0, 2.0], 150)


class AggregationTests(unittest.TestCase):
    def test_build_all_aggregates_groups_and_scores(self) -> None:
        fast = [
            _sample(sample_id=f"fast-{i}", model="fast-model", elapsed_seconds=2.0, sample_seq=i)
            for i in range(1, 4)
        ]
        slow = [
            _sample(sample_id=f"slow-{i}", model="slow-model", elapsed_seconds=8.0, sample_seq=i)
            for i in range(1, 4)
        ]
        aggregates = build_all_aggregates(fast + slow, provenance="synthetic_dry_run")
        by_model = {a.model: a for a in aggregates}
        self.assertEqual(set(by_model), {"fast-model", "slow-model"})
        self.assertEqual(by_model["fast-model"].task_count, 3)
        self.assertEqual(by_model["fast-model"].success_count, 3)
        self.assertEqual(by_model["fast-model"].success_rate_percent, 100.0)
        # The faster group must score at least as well on the relative speed
        # axis as the slower one -> its overall_score must not be lower,
        # all else being equal.
        self.assertGreaterEqual(
            by_model["fast-model"].overall_score, by_model["slow-model"].overall_score
        )

    def test_failing_deterministic_gate_caps_overall_score(self) -> None:
        """A fast-but-wrong model must not win merely on speed: overall_score
        must be capped at (or below) the deterministic_score once the gate
        fails, regardless of a very high reviewer/collaboration/speed score."""

        wrong_but_fast = [
            _sample(
                sample_id=f"wrongfast-{i}",
                model="wrong-fast-model",
                elapsed_seconds=1.0,
                deterministic_score=10.0,
                reviewer_score=95.0,
                collaboration_score=95.0,
                sample_seq=i,
            )
            for i in range(1, 4)
        ]
        aggregates = build_all_aggregates(wrong_but_fast, provenance="synthetic_dry_run")
        self.assertEqual(len(aggregates), 1)
        agg = aggregates[0]
        self.assertLessEqual(agg.overall_score, agg.deterministic_score_mean + 1e-9)
        # All three attempts failed the deterministic gate -> the acceptance
        # rate is 0%, which must hard-gate the whole model-run out of the
        # ranking entirely, independent of (and in addition to) the
        # overall_score cap asserted above.
        self.assertTrue(agg.hard_gate_failed)
        self.assertEqual(agg.suitability_tier, "not-usable")
        self.assertEqual(agg.accepted_sample_count, 0)
        self.assertEqual(agg.not_usable_sample_count, 3)
        self.assertIn("not usable", agg.recommendation.lower())

    def test_na_metrics_do_not_break_aggregation(self) -> None:
        """Copilot-agent-style samples with all speed/resource metrics unset
        must still aggregate cleanly, with N/A-equivalent (None) results for
        those specific metrics rather than raising or fabricating zeros."""

        copilot_like = [
            _sample(
                sample_id=f"copilot-{i}",
                provider="github",
                backend="copilot_agent",
                model="copilot-agent-ref",
                track="tool_agent",
                tokens_per_second=None,
                ttft_seconds=None,
                cpu_avg_percent=None,
                cpu_max_percent=None,
                ram_avg_mb=None,
                ram_max_mb=None,
                gpu_avg_percent=None,
                gpu_max_percent=None,
                vram_avg_mb=None,
                vram_max_mb=None,
                sample_seq=i,
            )
            for i in range(1, 3)
        ]
        aggregates = build_all_aggregates(copilot_like, provenance="synthetic_dry_run")
        self.assertEqual(len(aggregates), 1)
        agg = aggregates[0]
        self.assertIsNone(agg.tokens_per_second_mean)
        self.assertIsNone(agg.cpu_avg_percent_mean)
        self.assertIsNone(agg.vram_avg_mb_mean)
        self.assertEqual(agg.task_count, 2)


class HardAcceptanceGateTests(unittest.TestCase):
    """Direct tests for the sample- and aggregate-level hard acceptance gate,
    the load-bearing behavior behind "speed must never compensate for
    unusable quality"."""

    # -- compute_sample_acceptance -----------------------------------------

    def test_success_with_good_scores_is_accepted(self) -> None:
        status, reasons = rubric.compute_sample_acceptance(
            status="success", deterministic_score=95.0, reviewer_score=90.0
        )
        self.assertEqual(status, "accepted")
        self.assertIsNone(reasons)

    def test_error_status_is_not_usable_even_with_no_scores(self) -> None:
        status, reasons = rubric.compute_sample_acceptance(status="error")
        self.assertEqual(status, "not_usable")
        self.assertIn("status=error", reasons)

    def test_timeout_status_is_not_usable(self) -> None:
        status, reasons = rubric.compute_sample_acceptance(status="timeout")
        self.assertEqual(status, "not_usable")
        self.assertIn("status=timeout", reasons)

    def test_unsafe_behavior_forces_not_usable_despite_perfect_scores(self) -> None:
        """Zero tolerance: even a perfect deterministic/reviewer score cannot
        rescue an attempt flagged as unsafe."""

        status, reasons = rubric.compute_sample_acceptance(
            status="success",
            deterministic_score=100.0,
            reviewer_score=100.0,
            unsafe_behavior_flag=True,
        )
        self.assertEqual(status, "not_usable")
        self.assertIn("unsafe", reasons)

    def test_placeholder_output_forces_not_usable_despite_perfect_scores(self) -> None:
        status, reasons = rubric.compute_sample_acceptance(
            status="success",
            deterministic_score=100.0,
            reviewer_score=100.0,
            output_placeholder_or_incomplete=True,
        )
        self.assertEqual(status, "not_usable")
        self.assertIn("placeholder", reasons)

    def test_deterministic_below_threshold_is_not_usable(self) -> None:
        status, reasons = rubric.compute_sample_acceptance(
            status="success", deterministic_score=10.0, reviewer_score=95.0
        )
        self.assertEqual(status, "not_usable")
        self.assertIn("deterministic_score", reasons)

    def test_reviewer_below_threshold_is_not_usable(self) -> None:
        status, reasons = rubric.compute_sample_acceptance(
            status="success", deterministic_score=95.0, reviewer_score=10.0
        )
        self.assertEqual(status, "not_usable")
        self.assertIn("reviewer_score", reasons)

    def test_skipped_status_is_not_evaluated(self) -> None:
        status, reasons = rubric.compute_sample_acceptance(status="skipped")
        self.assertEqual(status, "not_evaluated")
        self.assertIsNotNone(reasons)

    def test_no_scoring_evidence_at_all_is_not_evaluated_not_accepted(self) -> None:
        """Silence must never be treated as acceptance, even with a
        'success' system status (for example a connect/format smoke task
        that has no deterministic or reviewer gate at all)."""

        status, reasons = rubric.compute_sample_acceptance(status="success")
        self.assertEqual(status, "not_evaluated")
        self.assertIsNotNone(reasons)

    # -- compute_aggregate_hard_gate -----------------------------------------

    def test_unsafe_count_always_fails_gate_regardless_of_acceptance_rate(self) -> None:
        failed, reasons = rubric.compute_aggregate_hard_gate(
            accepted_count=9,
            not_usable_count=0,
            unsafe_count=1,
            task_count=10,
        )
        self.assertTrue(failed)
        self.assertIn("unsafe", reasons)

    def test_high_acceptance_rate_passes_gate(self) -> None:
        failed, reasons = rubric.compute_aggregate_hard_gate(
            accepted_count=9,
            not_usable_count=1,
            unsafe_count=0,
            task_count=10,
        )
        self.assertFalse(failed)
        self.assertIsNone(reasons)

    def test_low_acceptance_rate_fails_gate(self) -> None:
        """Repeated fast failures (here: 1 accepted out of 5 evaluated, i.e.
        20% < the 50% minimum) must fail the gate even though the model was
        actually evaluated and did accept at least one attempt."""

        failed, reasons = rubric.compute_aggregate_hard_gate(
            accepted_count=1,
            not_usable_count=4,
            unsafe_count=0,
            task_count=5,
        )
        self.assertTrue(failed)
        self.assertIn("20.0%", reasons)

    def test_not_evaluated_samples_excluded_from_denominator(self) -> None:
        """A group with 1 accepted / 0 not_usable / many not_evaluated must
        pass the gate on a 100% evaluated-acceptance-rate basis -- missing
        evaluation data must never be treated as a quality failure."""

        failed, reasons = rubric.compute_aggregate_hard_gate(
            accepted_count=1,
            not_usable_count=0,
            unsafe_count=0,
            task_count=10,
        )
        self.assertFalse(failed)
        self.assertIsNone(reasons)

    def test_nothing_evaluated_does_not_fail_gate_on_rate_alone(self) -> None:
        failed, reasons = rubric.compute_aggregate_hard_gate(
            accepted_count=0,
            not_usable_count=0,
            unsafe_count=0,
            task_count=3,
        )
        self.assertFalse(failed)
        self.assertIsNone(reasons)

    # -- suitability_tier short-circuit --------------------------------------

    def test_suitability_tier_short_circuits_to_not_usable(self) -> None:
        """Even a perfect overall_score/success_rate must not escape the
        not-usable tier once hard_gate_failed is True."""

        tier = rubric.suitability_tier(
            overall_score=100.0,
            success_rate_percent=100.0,
            task_count=10,
            hard_gate_failed=True,
        )
        self.assertEqual(tier, rubric.SUITABILITY_TIER_NOT_USABLE)

    # -- aggregate-level: fast-unreliable vs slow-reliable -------------------

    def test_fast_unreliable_model_is_excluded_despite_being_faster(self) -> None:
        """The central proof for the user's requirement: a model that is
        much faster but mostly fails its deterministic/reviewer gates must be
        classified not-usable and excluded from ranking, while a slower
        model that reliably passes must remain ranked -- speed alone must
        never flip this outcome."""

        fast_unreliable = [
            _sample(
                sample_id=f"fastbad-{i}",
                model="fast-unreliable-model",
                elapsed_seconds=0.5,
                deterministic_score=5.0,
                reviewer_score=95.0,
                sample_seq=i,
            )
            for i in range(1, 4)
        ] + [
            _sample(
                sample_id="fastbad-4",
                model="fast-unreliable-model",
                elapsed_seconds=0.4,
                deterministic_score=95.0,
                reviewer_score=95.0,
                sample_seq=4,
            )
        ]
        slow_reliable = [
            _sample(
                sample_id=f"slowgood-{i}",
                model="slow-reliable-model",
                elapsed_seconds=20.0,
                deterministic_score=90.0,
                reviewer_score=85.0,
                sample_seq=i,
            )
            for i in range(1, 5)
        ]
        aggregates = build_all_aggregates(
            fast_unreliable + slow_reliable, provenance="synthetic_dry_run"
        )
        by_model = {a.model: a for a in aggregates}

        fast_agg = by_model["fast-unreliable-model"]
        slow_agg = by_model["slow-reliable-model"]

        # The fast model has a real accepted sample (and is objectively
        # faster than the slow model on raw elapsed time), yet its 25%
        # evaluated-acceptance-rate must still hard-gate the whole model-run
        # out of ranking -- speed never rescues it.
        self.assertTrue(fast_agg.hard_gate_failed)
        self.assertEqual(fast_agg.suitability_tier, "not-usable")
        self.assertIn("not usable", fast_agg.recommendation.lower())
        self.assertEqual(fast_agg.accepted_sample_count, 1)
        self.assertEqual(fast_agg.not_usable_sample_count, 3)
        self.assertAlmostEqual(fast_agg.acceptance_rate_percent, 25.0, places=1)

        # The slow-but-reliable model passes the gate and remains ranked
        # (not "not-usable"), with a real overall score.
        self.assertFalse(slow_agg.hard_gate_failed)
        self.assertNotEqual(slow_agg.suitability_tier, "not-usable")
        self.assertIsNotNone(slow_agg.overall_score)
        self.assertEqual(slow_agg.acceptance_rate_percent, 100.0)

        # Even though the fast-unreliable model is objectively faster in
        # raw elapsed seconds, it must never be preferred: it is excluded
        # from ranking entirely while the slower model is not.
        self.assertTrue(fast_agg.elapsed_seconds_mean < slow_agg.elapsed_seconds_mean)

    # -- reviewer_evidence_is_heuristic_only / keyword-score safeguard -------

    def test_is_heuristic_reviewer_provenance_matches_legacy_markers(self) -> None:
        self.assertTrue(
            rubric.is_heuristic_reviewer_provenance(
                "legacy-heuristic-keyword-score (migration_llm_bench)"
            )
        )
        self.assertTrue(rubric.is_heuristic_reviewer_provenance("Keyword-Match-Scorer"))
        self.assertFalse(rubric.is_heuristic_reviewer_provenance("human-orchestrator-review"))
        self.assertFalse(rubric.is_heuristic_reviewer_provenance(None))
        self.assertFalse(rubric.is_heuristic_reviewer_provenance(""))

    def test_suitability_tier_caps_heuristic_only_evidence_below_recommended(self) -> None:
        """The exact 'RNJ-1' scenario: a perfect overall_score/success_rate
        must never reach tier-1-recommended when the only reviewer evidence
        behind it is a legacy/heuristic keyword score."""

        tier = rubric.suitability_tier(
            overall_score=100.0,
            success_rate_percent=100.0,
            task_count=1,
            hard_gate_failed=False,
            reviewer_evidence_is_heuristic_only=True,
        )
        self.assertNotEqual(tier, rubric.SUITABILITY_TIER_RECOMMENDED)
        self.assertEqual(tier, rubric.SUITABILITY_TIER_CONDITIONAL)

    def test_single_sample_keyword_scored_model_is_not_labeled_recommended(self) -> None:
        """End-to-end aggregate-level proof: a model-run backed by exactly
        one sample with a perfect legacy-heuristic-keyword reviewer_score
        and no deterministic evidence at all must not be classified
        tier-1-recommended, and its recommendation text must carry an
        explicit caveat -- it must never be presented as validated/suitable
        on keyword score alone.

        A single-sample/single-category group is *also* capped by the
        evidence-stage guard (see rubric.evidence_stage()) before the
        heuristic-only-reviewer guard is even reached -- both guards
        independently forbid "tier-1-recommended" here, which is exactly
        the intended defense-in-depth (see
        HardAcceptanceGateTests.test_heuristic_only_reviewer_capped_even_with_full_suite_evidence
        below for the heuristic-only guard tested in isolation with
        sufficient evidence-stage coverage)."""

        keyword_scored = [
            _sample(
                sample_id="rnj-1-like",
                model="rnj-1:8b",
                deterministic_score=None,
                reviewer_score=100.0,
                reviewer_provenance="legacy-heuristic-keyword-score (migration_llm_bench)",
                acceptance_status="accepted",
                acceptance_reasons=None,
            )
        ]
        aggregates = build_all_aggregates(keyword_scored, provenance="historical_import")
        self.assertEqual(len(aggregates), 1)
        agg = aggregates[0]
        self.assertTrue(agg.reviewer_evidence_is_heuristic_only)
        self.assertEqual(agg.evidence_stage, "gate_only")
        self.assertNotEqual(agg.suitability_tier, "tier-1-recommended")
        self.assertEqual(agg.suitability_tier, "gate-passed-provisional")
        self.assertFalse(agg.hard_gate_failed)  # not excluded, just not "recommended"
        self.assertIn("not yet a validated", agg.recommendation.lower())

    def test_heuristic_only_reviewer_capped_even_with_full_suite_evidence(self) -> None:
        """The 'RNJ-1' scenario, isolated from the evidence-stage guard: even
        once a model-run has broad task-category coverage plus reviewer AND
        collaboration evidence (evidence_stage == 'full_suite'), a perfect
        overall_score must still never reach tier-1-recommended if the only
        reviewer evidence behind it is a legacy/heuristic keyword score --
        the heuristic-only-reviewer guard and the evidence-stage guard are
        independent, both must hold."""

        keyword_scored = [
            _sample(
                sample_id=f"rnj-1-like-{i}",
                model="rnj-1:8b",
                task_category=category,
                deterministic_score=None,
                reviewer_score=100.0,
                reviewer_provenance="legacy-heuristic-keyword-score (migration_llm_bench)",
                acceptance_status="accepted",
                acceptance_reasons=None,
                sample_seq=i,
            )
            for i, category in enumerate(
                ["connectivity", "coding", "review", "sql_migration"], start=1
            )
        ]
        aggregates = build_all_aggregates(keyword_scored, provenance="historical_import")
        self.assertEqual(len(aggregates), 1)
        agg = aggregates[0]
        self.assertEqual(agg.evidence_stage, "full_suite")
        self.assertTrue(agg.reviewer_evidence_is_heuristic_only)
        self.assertNotEqual(agg.suitability_tier, "tier-1-recommended")
        self.assertFalse(agg.hard_gate_failed)  # not excluded, just not "recommended"
        self.assertIn("caveat", agg.recommendation.lower())
        self.assertIn("not a validated", agg.recommendation.lower())

    def test_genuine_deterministic_evidence_is_never_capped_as_heuristic_only(self) -> None:
        """A model-run with real deterministic-gate evidence must never be
        flagged reviewer_evidence_is_heuristic_only=True, even if its
        reviewer_score also happens to come from a heuristic provenance
        string -- objective test evidence is real evidence.

        Uses 4 distinct task categories (plus the default sample's
        reviewer_score/collaboration_score) so evidence_stage reaches
        'full_suite' and the evidence-stage guard does not also cap the
        tier here -- isolating the specific behavior under test (the
        heuristic-only-reviewer guard) from the orthogonal evidence-stage
        guard (see HardAcceptanceGateTests.test_single_sample_keyword_scored_model_is_not_labeled_recommended
        for that guard tested on its own)."""

        real_evidence = [
            _sample(
                sample_id=f"real-{i}",
                model="genuinely-tested-model",
                task_category=category,
                deterministic_score=95.0,
                reviewer_score=95.0,
                reviewer_provenance="legacy-heuristic-keyword-score (migration_llm_bench)",
                sample_seq=i,
            )
            for i, category in enumerate(
                ["connectivity", "coding", "review", "sql_migration"], start=1
            )
        ]
        aggregates = build_all_aggregates(real_evidence, provenance="synthetic_dry_run")
        self.assertEqual(len(aggregates), 1)
        agg = aggregates[0]
        self.assertEqual(agg.evidence_stage, "full_suite")
        self.assertFalse(agg.reviewer_evidence_is_heuristic_only)
        self.assertEqual(agg.suitability_tier, "tier-1-recommended")


class HistoricalAdapterTests(unittest.TestCase):
    def _write_legacy_csv(self, tmpdir: Path) -> Path:
        path = tmpdir / "legacy_history.csv"
        fieldnames = [
            "source_csv",
            "benchmark_run_id",
            "benchmark_name",
            "benchmark_spec_version",
            "backend",
            "model",
            "case_id",
            "case_title",
            "run",
            "wall_ms",
            "prompt_tokens",
            "output_tokens",
            "output_tps",
            "quality_score",
            "keyword_hits",
            "keyword_total",
            "forbidden_hits",
            "avg_cpu_pct",
            "max_cpu_pct",
            "avg_mem_pct",
            "max_mem_pct",
            "avg_gpu_pct",
            "max_gpu_pct",
            "avg_vram_used_mb",
            "max_vram_used_mb",
            "cpu_time_sec",
            "recorded_at",
            "output_preview",
            "error",
        ]
        rows = [
            {
                "source_csv": "old_run.csv",
                "benchmark_run_id": "run-42",
                "benchmark_name": "migration-set-1",
                "benchmark_spec_version": "v1",
                "backend": "ollama",
                "model": "llama3:8b",
                "case_id": "case-1",
                "case_title": "Translate SQL",
                "run": "1",
                "wall_ms": "4200",
                "prompt_tokens": "120",
                "output_tokens": "80",
                "output_tps": "19.0",
                "quality_score": "72.5",
                "keyword_hits": "4",
                "keyword_total": "5",
                "forbidden_hits": "0",
                "avg_cpu_pct": "35.0",
                "max_cpu_pct": "70.0",
                "avg_mem_pct": "40.0",
                "max_mem_pct": "55.0",
                "avg_gpu_pct": "20.0",
                "max_gpu_pct": "50.0",
                "avg_vram_used_mb": "4000",
                "max_vram_used_mb": "5000",
                "cpu_time_sec": "3.1",
                "recorded_at": "2025-01-01 10:00:00",
                "output_preview": "SELECT ...",
                "error": "",
            },
            {
                "source_csv": "old_run.csv",
                "benchmark_run_id": "run-42",
                "benchmark_name": "migration-set-1",
                "benchmark_spec_version": "v1",
                "backend": "siemens",
                "model": "gpt-oss-120b",
                "case_id": "case-2",
                "case_title": "Review bug",
                "run": "1",
                "wall_ms": "9800",
                "prompt_tokens": "300",
                "output_tokens": "150",
                "output_tps": "15.3",
                "quality_score": "40.0",
                "keyword_hits": "2",
                "keyword_total": "5",
                "forbidden_hits": "1",
                "avg_cpu_pct": "10.0",
                "max_cpu_pct": "25.0",
                "avg_mem_pct": "15.0",
                "max_mem_pct": "20.0",
                "avg_gpu_pct": "N/A",
                "max_gpu_pct": "N/A",
                "avg_vram_used_mb": "N/A",
                "max_vram_used_mb": "N/A",
                "cpu_time_sec": "1.2",
                "recorded_at": "2025-01-01 10:05:00",
                "output_preview": "There is a null pointer...",
                "error": "timeout contacting endpoint",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_import_maps_legacy_rows_to_canonical_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = self._write_legacy_csv(Path(tmp))
            samples = historical_adapter.import_legacy_history_csv(
                csv_path, campaign_id="legacy-test", local_tzinfo=_dt.timezone.utc
            )
        self.assertEqual(len(samples), 2)

        ok_row, error_row = samples
        # RAM must stay N/A (None) -- legacy stored a percentage, not MB.
        self.assertIsNone(ok_row.ram_avg_mb)
        self.assertIsNone(ok_row.ram_max_mb)
        # Legacy quality_score maps to reviewer_score, never deterministic_score.
        self.assertEqual(ok_row.reviewer_score, 72.5)
        self.assertIsNone(ok_row.deterministic_score)
        self.assertEqual(ok_row.reviewer_provenance, historical_adapter.LEGACY_REVIEWER_PROVENANCE)
        self.assertEqual(ok_row.track, "pure_model")
        self.assertEqual(ok_row.provenance, "historical_import")
        self.assertEqual(ok_row.status, "success")
        self.assertFalse(ok_row.system_error_flag)
        self.assertAlmostEqual(ok_row.elapsed_seconds, 4.2)
        self.assertEqual(ok_row.start_time, "2025-01-01T09:59:55.800+00:00")
        self.assertEqual(ok_row.end_time, "2025-01-01T10:00:00.000+00:00")

        self.assertEqual(error_row.status, "timeout")
        self.assertTrue(error_row.system_error_flag)
        self.assertEqual(error_row.system_error_code, "timeout")
        self.assertEqual(error_row.system_error_message, "timeout contacting endpoint")
        self.assertIsNone(error_row.vram_avg_mb)

        # Imported records must themselves be schema-valid.
        for sample in samples:
            self.assertEqual(validate_sample(sample), [])

    def test_import_result_is_schema_valid_and_isolated_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = self._write_legacy_csv(Path(tmp))
            samples = historical_adapter.import_legacy_history_csv(csv_path)
        self.assertTrue(all(s.campaign_id.startswith("legacy-import-") for s in samples))

    def test_import_accepts_arbitrary_external_path_outside_any_repo_layout(self) -> None:
        """csv_path must be a fully configurable, arbitrary filesystem path
        -- not restricted to a repo-relative location. This mirrors a real
        external source such as a user's home-directory
        benchmark_results\\migration_llm_bench_history.csv."""

        with tempfile.TemporaryDirectory() as tmp:
            external_dir = Path(tmp) / "not-a-repo" / "somewhere-else" / "benchmark_results"
            external_dir.mkdir(parents=True)
            csv_path = self._write_legacy_csv(external_dir)
            samples = historical_adapter.import_legacy_history_csv(
                csv_path, campaign_id="legacy-external-test", local_tzinfo=_dt.timezone.utc
            )
        self.assertEqual(len(samples), 2)
        for sample in samples:
            self.assertEqual(validate_sample(sample), [])

    def test_import_records_source_path_provenance_and_confidence_in_notes(self) -> None:
        """Requirement: historical import must record source/provenance/
        confidence. provenance is already a dedicated field
        (SampleRecord.provenance='historical_import',
        reviewer_provenance=LEGACY_REVIEWER_PROVENANCE); source (the actual
        external file imported) and an explicit confidence marker have no
        dedicated schema column (adding one would be a non-additive CSV
        change) so both must appear explicitly in notes."""

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = self._write_legacy_csv(Path(tmp))
            samples = historical_adapter.import_legacy_history_csv(
                csv_path, campaign_id="legacy-test", local_tzinfo=_dt.timezone.utc
            )
        ok_row = samples[0]
        self.assertIn(f"source file: {csv_path.resolve()}", ok_row.notes)
        self.assertIn(
            f"confidence: {historical_adapter.LEGACY_QUALITY_SCORE_CONFIDENCE}", ok_row.notes
        )
        self.assertEqual(ok_row.provenance, "historical_import")
        self.assertEqual(ok_row.reviewer_provenance, historical_adapter.LEGACY_REVIEWER_PROVENANCE)
        # The reviewer_provenance marker must trigger the heuristic-only
        # safeguard so this data can never pass the hard quality gate on
        # keyword score alone (see rubric.is_heuristic_reviewer_provenance).
        self.assertTrue(rubric.is_heuristic_reviewer_provenance(ok_row.reviewer_provenance))

    def test_legacy_error_text_containing_timeout_is_classified_as_timeout_status(self) -> None:
        """A legacy error message mentioning 'timeout'/'timed out' (for
        example the real-world 'Siemens error: The read operation timed
        out') must map to status='timeout', not a generic status='error',
        matching the canonical schema.STATUSES vocabulary. Both hard-fail
        the acceptance gate identically, so this only improves reporting
        precision."""

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = self._write_legacy_csv(Path(tmp))
            samples = historical_adapter.import_legacy_history_csv(
                csv_path, campaign_id="legacy-test", local_tzinfo=_dt.timezone.utc
            )
        error_row = samples[1]  # error text is "timeout contacting endpoint"
        self.assertEqual(error_row.status, "timeout")
        self.assertEqual(error_row.system_error_code, "timeout")
        self.assertEqual(error_row.acceptance_status, "not_usable")

    def test_legacy_error_text_without_timeout_marker_stays_generic_error(self) -> None:
        """A genuine non-timeout system error must not be misclassified as
        a timeout -- only messages actually containing a timeout marker are
        reclassified."""

        self.assertEqual(historical_adapter._legacy_status(""), "success")
        self.assertEqual(
            historical_adapter._legacy_status("connection refused by remote host"), "error"
        )
        self.assertEqual(
            historical_adapter._legacy_status("HTTP 500 Internal Server Error"), "error"
        )
        self.assertEqual(
            historical_adapter._legacy_status("Siemens error: The read operation timed out"),
            "timeout",
        )
        self.assertEqual(historical_adapter._legacy_status("Request TIMEOUT"), "timeout")

    def test_imported_heuristic_only_model_run_never_reaches_recommended_tier(self) -> None:
        """End-to-end via the real import path (not a hand-built _sample()):
        a model-run built exclusively from imported legacy rows with a high
        quality_score must still be capped below tier-1-recommended,
        because reviewer_evidence_is_heuristic_only is always True for
        historical-import-only evidence."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy_history.csv"
            fieldnames = [
                "source_csv", "benchmark_run_id", "benchmark_name",
                "benchmark_spec_version", "backend", "model", "case_id",
                "case_title", "run", "wall_ms", "prompt_tokens", "output_tokens",
                "output_tps", "quality_score", "keyword_hits", "keyword_total",
                "forbidden_hits", "avg_cpu_pct", "max_cpu_pct", "avg_mem_pct",
                "max_mem_pct", "avg_gpu_pct", "max_gpu_pct", "avg_vram_used_mb",
                "max_vram_used_mb", "cpu_time_sec", "recorded_at",
                "output_preview", "error",
            ]
            row = {name: "" for name in fieldnames}
            row.update(
                {
                    "benchmark_run_id": "run-hi-score",
                    "benchmark_name": "migration-set-1",
                    "benchmark_spec_version": "v1",
                    "backend": "siemens",
                    "model": "deepseek-v4-flash",
                    "case_id": "case-1",
                    "case_title": "High keyword score",
                    "run": "1",
                    "wall_ms": "1000",
                    "quality_score": "100.0",
                    "recorded_at": "2025-01-01 10:00:00",
                    "error": "",
                }
            )
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(row)
            samples = historical_adapter.import_legacy_history_csv(
                path, campaign_id="legacy-hi-score", local_tzinfo=_dt.timezone.utc
            )
        aggregates = build_all_aggregates(samples, provenance="historical_import")
        self.assertEqual(len(aggregates), 1)
        agg = aggregates[0]
        self.assertTrue(agg.reviewer_evidence_is_heuristic_only)
        self.assertNotEqual(agg.suitability_tier, rubric.SUITABILITY_TIER_RECOMMENDED)

    def test_retry_rows_sharing_case_id_and_run_get_distinct_sample_ids(self) -> None:
        """Regression test for a real bug found while validating against
        actual migration_llm_bench history data: the legacy CSV does not
        always increment its own "run" column for a same-task retry (a task
        can time out, then be retried and succeed, both rows recorded with
        an identical case_id and run="1"). Before sample_seq was folded into
        sample_id, this produced two IDENTICAL sample_ids, and because
        storage.py's samples table has PRIMARY KEY("sample_id") with
        INSERT OR REPLACE, the earlier (timeout) row was silently
        overwritten and its evidence lost. Both rows must now survive with
        distinct sample_ids and both must round-trip through SQLite."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy_history.csv"
            fieldnames = [
                "source_csv", "benchmark_run_id", "benchmark_name",
                "benchmark_spec_version", "backend", "model", "case_id",
                "case_title", "run", "wall_ms", "prompt_tokens", "output_tokens",
                "output_tps", "quality_score", "keyword_hits", "keyword_total",
                "forbidden_hits", "avg_cpu_pct", "max_cpu_pct", "avg_mem_pct",
                "max_mem_pct", "avg_gpu_pct", "max_gpu_pct", "avg_vram_used_mb",
                "max_vram_used_mb", "cpu_time_sec", "recorded_at",
                "output_preview", "error",
            ]
            base_row = {name: "" for name in fieldnames}
            base_row.update(
                {
                    "benchmark_run_id": "20260729_164425",
                    "benchmark_name": "ora-pg-py-33",
                    "benchmark_spec_version": "v1",
                    "backend": "siemens",
                    "model": "deepseek-v4-flash",
                    "case_id": "bulk_collect_refactor",
                    "case_title": "Bulk collect refactor",
                    "run": "1",
                    "wall_ms": "5000",
                    "recorded_at": "2025-01-01 10:00:00",
                }
            )
            timeout_row = dict(base_row)
            timeout_row["error"] = "Siemens error: The read operation timed out"
            retry_success_row = dict(base_row)
            retry_success_row["error"] = ""
            retry_success_row["quality_score"] = "80.0"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerow(timeout_row)
                writer.writerow(retry_success_row)
            samples = historical_adapter.import_legacy_history_csv(
                path, campaign_id="legacy-retry-collision-test", local_tzinfo=_dt.timezone.utc
            )

        self.assertEqual(len(samples), 2)
        self.assertNotEqual(samples[0].sample_id, samples[1].sample_id)
        self.assertEqual(samples[0].status, "timeout")
        self.assertEqual(samples[1].status, "success")

        # Prove both rows actually survive a SQLite round trip (this is the
        # exact mechanism -- PRIMARY KEY("sample_id") + INSERT OR REPLACE --
        # that silently dropped the timeout row before this fix).
        with tempfile.TemporaryDirectory() as db_tmp:
            conn = storage.connect(Path(db_tmp) / "agent_helper.sqlite3")
            try:
                storage.insert_samples(conn, samples)
                stored = storage.fetch_samples(conn, campaign_id="legacy-retry-collision-test")
            finally:
                conn.close()
        self.assertEqual(len(stored), 2)
        statuses = sorted(s.status for s in stored)
        self.assertEqual(statuses, ["success", "timeout"])


class ReportEscapingTests(unittest.TestCase):
    def test_esc_neutralizes_script_tags(self) -> None:
        rendered = esc('<script>alert(1)</script>&"\'')
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_esc_none_is_na(self) -> None:
        self.assertEqual(esc(None), "N/A")

    def test_artifact_link_rejects_absolute_windows_path(self) -> None:
        rendered = render_artifact_link("C:\\Users\\someone\\secret.txt")
        self.assertNotIn("<a href", rendered)
        self.assertIn("not linked", rendered)

    def test_artifact_link_rejects_traversal(self) -> None:
        rendered = render_artifact_link("../../etc/passwd")
        self.assertNotIn("<a href", rendered)

    def test_artifact_link_accepts_relative_path(self) -> None:
        rendered = render_artifact_link("artifacts/sample-1.txt")
        self.assertIn('<a href="artifacts/sample-1.txt"', rendered)

    def test_report_escapes_hostile_model_content_and_collapses_older_campaigns(self) -> None:
        hostile_sample = _sample(
            sample_id="hostile-1",
            task_name='<img src=x onerror=alert(1)>',
            reviewer_notes="</div><script>document.location='http://evil.example'</script>",
            artifact_path="C:\\absolute\\path.txt",
        )
        aggregates = build_all_aggregates([hostile_sample], provenance="synthetic_dry_run")

        newest = CampaignReportData(
            campaign_id="camp-newest",
            generated_at="2026-08-02T00:00:00.000+00:00",
            aggregates=aggregates,
            samples=[hostile_sample],
        )
        older = CampaignReportData(
            campaign_id="camp-older",
            generated_at="2026-08-01T00:00:00.000+00:00",
            aggregates=aggregates,
            samples=[hostile_sample],
        )
        html_out = render_report([older, newest], expand_latest=1)

        # The report itself legitimately contains one inline <script> block
        # for its own vanilla JS (sort/filter/lang-toggle); what must never
        # appear is the *hostile* payload as a live tag/attribute -- only as
        # inert, escaped text.
        self.assertNotIn("<script>document.location", html_out)
        self.assertNotIn("<img src=x onerror", html_out)
        self.assertIn("&lt;script&gt;", html_out)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html_out)

        # Newest campaign (sorted first) must be expanded; the older one
        # must be present but collapsed (no `open` attribute on its <details>).
        # Locate each campaign's <summary> marker (not just the first
        # occurrence of its id, which also appears earlier in the executive
        # summary text) so the preceding <details ...> can be found reliably.
        newest_marker = html_out.index("<summary><span>camp-newest")
        older_marker = html_out.index("<summary><span>camp-older")
        self.assertLess(newest_marker, older_marker)
        details_newest = html_out.rindex("<details", 0, newest_marker)
        details_older = html_out.rindex("<details", 0, older_marker)
        self.assertIn("open", html_out[details_newest : details_newest + 40])
        self.assertNotIn("open", html_out[details_older : details_older + 40])

    def test_hard_gated_model_never_appears_in_leaderboard_only_in_excluded_table(
        self,
    ) -> None:
        """Direct proof for the user's requirement that a hard-gated model
        must never be visually ranked as suitable: it must be entirely
        absent from the leaderboard table, present in the dedicated
        'excluded' table, and named in the executive summary's exclusion
        list -- regardless of how fast it was or how high its raw scores
        looked before the gate was applied."""

        # A model that is fast but fails 3 of 4 attempts -> hard-gated.
        excluded_samples = [
            _sample(
                sample_id=f"badfast-{i}",
                model="excluded-fast-model",
                elapsed_seconds=0.3,
                deterministic_score=5.0,
                reviewer_score=95.0,
                sample_seq=i,
            )
            for i in range(1, 4)
        ]
        # A model that reliably passes -> stays ranked.
        usable_samples = [
            _sample(
                sample_id=f"goodslow-{i}",
                model="usable-model",
                elapsed_seconds=10.0,
                deterministic_score=90.0,
                reviewer_score=85.0,
                sample_seq=i,
            )
            for i in range(1, 4)
        ]
        aggregates = build_all_aggregates(
            excluded_samples + usable_samples, provenance="synthetic_dry_run"
        )
        data = CampaignReportData(
            campaign_id="camp-hardgate",
            generated_at="2026-08-03T00:00:00.000+00:00",
            aggregates=aggregates,
            samples=excluded_samples + usable_samples,
        )
        html_out = render_report([data], expand_latest=1)

        leaderboard_start = html_out.index('id="leaderboard-c0"')
        leaderboard_end = html_out.index("</table>", leaderboard_start)
        leaderboard_html = html_out[leaderboard_start:leaderboard_end]
        self.assertIn("usable-model", leaderboard_html)
        self.assertNotIn("excluded-fast-model", leaderboard_html)

        excluded_start = html_out.index('id="excluded-c0"')
        excluded_end = html_out.index("</table>", excluded_start)
        excluded_html = html_out[excluded_start:excluded_end]
        self.assertIn("excluded-fast-model", excluded_html)
        self.assertNotIn("usable-model", excluded_html)

        # Executive summary must explicitly name the excluded model.
        exec_summary_start = html_out.index('class="exec-summary"')
        exec_summary_end = html_out.index("</div>", exec_summary_start)
        self.assertIn(
            "excluded-fast-model", html_out[exec_summary_start:exec_summary_end]
        )


class CsvRoundTripTests(unittest.TestCase):
    def test_samples_round_trip_through_sqlite_and_csv(self) -> None:
        samples = [
            _sample(sample_id="rt-1", sample_seq=1),
            _sample(
                sample_id="rt-2",
                sample_seq=2,
                provider="github",
                backend="copilot_agent",
                model="copilot-agent-ref",
                track="tool_agent",
                tokens_per_second=None,
                ttft_seconds=None,
                cpu_avg_percent=None,
                cpu_max_percent=None,
                ram_avg_mb=None,
                ram_max_mb=None,
                gpu_avg_percent=None,
                gpu_max_percent=None,
                vram_avg_mb=None,
                vram_max_mb=None,
                notes="copilot agent reference; speed/resource metrics N/A",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            conn = storage.connect(tmp_path / storage.DB_FILENAME)
            try:
                storage.insert_samples(conn, samples)
                fetched = storage.fetch_samples(conn, campaign_id="test-campaign")
                self.assertEqual(len(fetched), 2)

                csv_path = tmp_path / storage.SAMPLES_CSV_FILENAME
                row_count = storage.export_samples_csv(conn, csv_path, campaign_id="test-campaign")
                self.assertEqual(row_count, 2)
            finally:
                conn.close()

            rows = storage.read_samples_csv(csv_path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(tuple(rows[0].keys()), SAMPLE_CSV_COLUMNS)

            by_id = {row["sample_id"]: row for row in rows}
            # Unavailable metrics must round-trip as the literal "N/A" string.
            self.assertEqual(by_id["rt-2"]["tokens_per_second"], "N/A")
            self.assertEqual(by_id["rt-2"]["cpu_avg_percent"], "N/A")
            self.assertEqual(by_id["rt-2"]["vram_avg_mb"], "N/A")
            # Present numeric metrics must round-trip with millisecond-style
            # 3-decimal float formatting, not silently truncated.
            self.assertEqual(by_id["rt-1"]["elapsed_seconds"], "5.000")
            self.assertEqual(by_id["rt-1"]["tokens_per_second"], "10.000")
            self.assertEqual(by_id["rt-1"]["schema_version"], SCHEMA_VERSION)

    def test_reopening_database_with_wrong_schema_version_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / storage.DB_FILENAME
            conn = storage.connect(db_path)
            conn.execute(
                "UPDATE schema_meta SET value = 'agent-helper-v0' WHERE key = 'schema_version'"
            )
            conn.commit()
            conn.close()

            # storage.connect() must close its own sqlite3.Connection before
            # raising (no leaked handle to block Windows from deleting the
            # TemporaryDirectory right after this).
            with self.assertRaises(storage.SchemaVersionMismatchError):
                storage.connect(db_path)

    def test_csv_header_mismatch_is_detected_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_csv = Path(tmp) / "bad.csv"
            with bad_csv.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["not", "the", "right", "columns"])
                writer.writerow(["a", "b", "c", "d"])
            with self.assertRaises(Exception):
                storage.read_samples_csv(bad_csv)


class StorageMigrationTests(unittest.TestCase):
    """Regression guard for ``storage._add_missing_columns`` (pilot-review
    remediation): a physical ``samples`` table written before a later,
    backward-compatible schema addition (for example ``model_load_seconds``,
    ``orchestrator_cpu_time_seconds``, ``model_cpu_time_seconds``) must be
    additively migrated on next ``storage.connect()`` -- not fail with
    'no such column' -- and existing behavior for a fully up-to-date
    database must remain unaffected (``_add_missing_columns`` is a no-op
    when every column already exists)."""

    _NEW_COLUMNS = ("model_load_seconds", "orchestrator_cpu_time_seconds", "model_cpu_time_seconds")

    def _build_legacy_db(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
            older_columns = [c for c in SAMPLE_CSV_COLUMNS if c not in self._NEW_COLUMNS]
            columns_sql = ",\n".join(f'"{name}" TEXT' for name in older_columns)
            conn.execute(
                f'CREATE TABLE samples (\n{columns_sql},\n  PRIMARY KEY ("sample_id")\n)'
            )
            conn.commit()
        finally:
            conn.close()

    def test_connect_additively_migrates_a_table_missing_newer_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / storage.DB_FILENAME
            self._build_legacy_db(db_path)

            # Sanity check: the legacy table genuinely lacks the new columns
            # before storage.connect() ever touches it.
            raw = sqlite3.connect(str(db_path))
            try:
                existing_before = {row[1] for row in raw.execute('PRAGMA table_info("samples")')}
            finally:
                raw.close()
            for column in self._NEW_COLUMNS:
                self.assertNotIn(column, existing_before)

            conn = storage.connect(db_path)
            try:
                existing_after = {
                    row[1] for row in conn.execute('PRAGMA table_info("samples")').fetchall()
                }
                for column in self._NEW_COLUMNS:
                    self.assertIn(column, existing_after)

                record = _sample(
                    sample_id="migrated-1",
                    model_load_seconds=1.5,
                    orchestrator_cpu_time_seconds=0.2,
                    model_cpu_time_seconds=0.9,
                )
                storage.insert_sample(conn, record)
                fetched = storage.fetch_samples(conn, campaign_id=record.campaign_id)
            finally:
                conn.close()
            self.assertEqual(len(fetched), 1)
            self.assertEqual(fetched[0].model_load_seconds, 1.5)
            self.assertEqual(fetched[0].orchestrator_cpu_time_seconds, 0.2)
            self.assertEqual(fetched[0].model_cpu_time_seconds, 0.9)

    def test_add_missing_columns_is_a_no_op_on_an_up_to_date_table(self) -> None:
        """Calling ``storage.connect()`` a second time against an already
        fully migrated database must not error or duplicate columns."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / storage.DB_FILENAME
            conn = storage.connect(db_path)
            conn.close()
            # Re-open: every column already exists, so the ALTER TABLE loop
            # must skip all of them without raising "duplicate column name".
            conn = storage.connect(db_path)
            try:
                columns = [row[1] for row in conn.execute('PRAGMA table_info("samples")').fetchall()]
            finally:
                conn.close()
            self.assertEqual(len(columns), len(set(columns)))


class RepairAndRecomputeTests(unittest.TestCase):
    """Regression guard for ``agent_helper_eval.repair`` (pilot-review
    remediation, fix #2's data-repair path): the historical
    ``llm_queue_seconds``-holds-cold-load-time mislabeling must be
    reclassified from a sample's own already-written artifact JSON, never by
    re-running anything, and the fingerprint must stay narrow enough to
    never touch an already-correct or non-Ollama sample."""

    def _fresh_repo_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _write_legacy_measured_sample_with_artifact(
        self, output_dir: Path, *, sample_id: str, campaign_id: str
    ) -> SampleRecord:
        """Build one sample matching the exact historical bug fingerprint
        (``llm_queue_seconds`` populated from Ollama's cold-load duration,
        ``llm_request_seconds`` never set) plus its own artifact JSON, the
        way the pre-fix ``live_gates`` code actually wrote it."""

        artifacts_dir = output_dir / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = f"artifacts/{sample_id}.json"
        raw_final_line = json.dumps(
            {
                "model": "qwen3-coder:30b",
                "response": "",
                "done": True,
                "done_reason": "stop",
                "total_duration": 18_672_000_000,
                "load_duration": 9_379_000_000,
                "prompt_eval_count": 120,
                "prompt_eval_duration": 742_000_000,
                "eval_count": 80,
                "eval_duration": 8_239_000_000,
            }
        )
        (output_dir / artifact_path).write_text(
            json.dumps({"raw_response_lines": [raw_final_line]}), encoding="utf-8"
        )
        return _sample(
            sample_id=sample_id,
            campaign_id=campaign_id,
            backend="ollama",
            provenance="measured",
            artifact_path=artifact_path,
            artifact_hash="deadbeef",
            llm_queue_seconds=9.379,  # the historical bug: cold-load time mislabeled as queue
            llm_request_seconds=None,
            model_load_seconds=None,
            prompt_eval_seconds=0.742,
            generation_seconds=8.239,
            test_exec_seconds=0.162,
        )

    def test_repair_reclassifies_legacy_queue_mislabel_from_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            sample = self._write_legacy_measured_sample_with_artifact(
                output_dir, sample_id="legacy-1", campaign_id="legacy-campaign"
            )
            repaired, was_repaired = repair.repair_legacy_llm_phase_timing(output_dir, sample)
            self.assertTrue(was_repaired)
            self.assertIsNone(repaired.llm_queue_seconds)
            self.assertAlmostEqual(repaired.llm_request_seconds, 18.672, places=3)
            self.assertAlmostEqual(repaired.model_load_seconds, 9.379, places=3)
            self.assertEqual(validate_sample(repaired), [])

    def test_repair_is_idempotent_on_an_already_repaired_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            sample = self._write_legacy_measured_sample_with_artifact(
                output_dir, sample_id="legacy-2", campaign_id="legacy-campaign"
            )
            once, was_repaired_once = repair.repair_legacy_llm_phase_timing(output_dir, sample)
            self.assertTrue(was_repaired_once)
            twice, was_repaired_twice = repair.repair_legacy_llm_phase_timing(output_dir, once)
            self.assertFalse(was_repaired_twice)  # already has llm_request_seconds -- no-op
            self.assertEqual(once, twice)

    def test_repair_never_touches_a_non_ollama_or_already_correct_sample(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            already_correct = _sample(
                sample_id="already-correct",
                backend="ollama",
                provenance="measured",
                llm_request_seconds=18.672,
                llm_queue_seconds=None,
            )
            fixed, was_repaired = repair.repair_legacy_llm_phase_timing(output_dir, already_correct)
            self.assertFalse(was_repaired)
            self.assertEqual(fixed, already_correct)

            copilot_sample = _sample(
                sample_id="copilot-ref",
                backend="copilot_agent",
                provenance="measured",
                llm_queue_seconds=9.379,
                llm_request_seconds=None,
            )
            fixed2, was_repaired2 = repair.repair_legacy_llm_phase_timing(output_dir, copilot_sample)
            self.assertFalse(was_repaired2)
            self.assertEqual(fixed2, copilot_sample)

    def test_repair_leaves_sample_unchanged_when_artifact_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            sample = _sample(
                sample_id="no-artifact",
                backend="ollama",
                provenance="measured",
                artifact_path="artifacts/does-not-exist.json",
                llm_queue_seconds=9.379,
                llm_request_seconds=None,
            )
            fixed, was_repaired = repair.repair_legacy_llm_phase_timing(output_dir, sample)
            self.assertFalse(was_repaired)
            self.assertEqual(fixed, sample)

    def test_recompute_campaign_end_to_end_repairs_and_rebuilds_artifacts(self) -> None:
        """No-model-call end-to-end guard: given an on-disk campaign
        database/CSVs/report built the historical (buggy) way, running
        ``repair.recompute_campaign`` must repair the phase-timing mislabel,
        rebuild aggregates with the current (evidence-stage-capped) rubric,
        and re-export both CSVs plus the local HTML report -- without ever
        importing anything network/model-related."""

        repo_root = self._fresh_repo_root()
        campaign_id = "legacy-recompute-campaign"
        output_dir = orchestrator.campaign_output_dir(repo_root, campaign_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        sample = self._write_legacy_measured_sample_with_artifact(
            output_dir, sample_id="legacy-e2e-1", campaign_id=campaign_id
        )

        conn = storage.connect(output_dir / storage.DB_FILENAME)
        try:
            storage.insert_sample(conn, sample)
        finally:
            conn.close()

        result = repair.recompute_campaign(repo_root, campaign_id)

        self.assertEqual(result.campaign_id, campaign_id)
        self.assertEqual(result.repaired_sample_count, 1)
        self.assertEqual(result.sample_count, 1)
        self.assertEqual(result.aggregate_count, 1)
        self.assertTrue(result.samples_csv.exists())
        self.assertTrue(result.aggregates_csv.exists())
        self.assertTrue(result.report_path is not None and result.report_path.exists())

        conn = storage.connect(output_dir / storage.DB_FILENAME)
        try:
            repaired_rows = storage.fetch_samples(conn, campaign_id=campaign_id)
            aggregate_rows = storage.fetch_aggregates(conn, campaign_id=campaign_id)
        finally:
            conn.close()
        self.assertEqual(len(repaired_rows), 1)
        self.assertIsNone(repaired_rows[0].llm_queue_seconds)
        self.assertAlmostEqual(repaired_rows[0].llm_request_seconds, 18.672, places=3)
        self.assertEqual(len(aggregate_rows), 1)
        # Single-category, gate-only evidence must never be over-claimed as
        # a tier-1/daily-runner-recommended verdict (fix #1 regression guard).
        self.assertEqual(aggregate_rows[0].evidence_stage, "gate_only")
        self.assertNotIn("tier-1", aggregate_rows[0].suitability_tier)

        report_html = result.report_path.read_text(encoding="utf-8")
        self.assertIn("gate-passed-provisional", report_html)

        # Running the recompute a second time must be a fully idempotent
        # no-op repair pass (the sample is already corrected).
        second_result = repair.recompute_campaign(repo_root, campaign_id)
        self.assertEqual(second_result.repaired_sample_count, 0)

    def test_recompute_campaign_raises_clear_error_for_unknown_campaign(self) -> None:
        repo_root = self._fresh_repo_root()
        with self.assertRaises(FileNotFoundError):
            repair.recompute_campaign(repo_root, "campaign-that-does-not-exist")


class PhaseTimingTests(unittest.TestCase):
    """Fast, deterministic tests for agent_helper_eval.phase_timing: the
    exclusive critical-path percentages must always sum to ~100% (normalized
    against the naive phase-time sum, never against wall time directly),
    with overlap/unaccounted-time reported as separate diagnostics; the
    utilization ratios are independent, non-exclusive, and must differ for a
    local (GPU-visible) backend versus an opaque one."""

    def test_no_data_returns_all_none(self) -> None:
        result = phase_timing.compute_exclusive_critical_path(phase_timing.PhaseSeconds())
        self.assertEqual(result.basis, "no_data")
        self.assertIsNone(result.llm_percent)
        self.assertIsNone(result.overlap_ratio_percent)

    def test_no_overlap_sums_to_100_and_reconciles_wall_time(self) -> None:
        phases = phase_timing.PhaseSeconds(
            total_wall_seconds=10.0,
            llm_request_seconds=6.0,
            local_tool_exec_seconds=2.0,
            orchestrator_review_seconds=2.0,
        )
        result = phase_timing.compute_exclusive_critical_path(phases)
        total = (
            result.llm_percent
            + result.tool_percent
            + result.queue_idle_percent
            + result.orchestration_percent
        )
        self.assertAlmostEqual(total, 100.0, places=6)
        self.assertEqual(result.basis, "reconciled_with_wall_time")
        self.assertEqual(result.overlap_ratio_percent, 0.0)
        self.assertEqual(result.unaccounted_ratio_percent, 0.0)

    def test_overlap_still_sums_to_100_and_is_reported_separately(self) -> None:
        # Naive phase sum (12s) exceeds total_wall_seconds (10s) by 2s of
        # genuine overlap -- the four exclusive percentages must still sum
        # to exactly 100% (normalized against the naive sum), with the
        # excess reported only via overlap_ratio_percent.
        phases = phase_timing.PhaseSeconds(
            total_wall_seconds=10.0,
            llm_request_seconds=6.0,
            local_tool_exec_seconds=4.0,
            orchestrator_review_seconds=2.0,
            overlap_seconds=2.0,
        )
        result = phase_timing.compute_exclusive_critical_path(phases)
        total = (
            result.llm_percent
            + result.tool_percent
            + result.queue_idle_percent
            + result.orchestration_percent
        )
        self.assertAlmostEqual(total, 100.0, places=6)
        self.assertAlmostEqual(result.overlap_ratio_percent, 16.67, places=1)
        self.assertEqual(result.unaccounted_ratio_percent, 0.0)

    def test_unaccounted_time_reported_when_wall_exceeds_naive_sum(self) -> None:
        phases = phase_timing.PhaseSeconds(total_wall_seconds=10.0, llm_request_seconds=4.0)
        result = phase_timing.compute_exclusive_critical_path(phases)
        self.assertAlmostEqual(result.llm_percent, 100.0, places=6)
        self.assertEqual(result.overlap_ratio_percent, 0.0)
        self.assertGreater(result.unaccounted_ratio_percent, 0.0)

    def test_llm_bucket_prefers_request_seconds_over_granular_sum(self) -> None:
        # Both a top-level llm_request_seconds AND a granular
        # prompt_eval/generation split are present -- the top-level number
        # must win (never summed together, which would double count).
        phases = phase_timing.PhaseSeconds(
            llm_request_seconds=5.0, prompt_eval_seconds=2.0, generation_seconds=2.0
        )
        self.assertEqual(phase_timing.llm_bucket_seconds(phases), 5.0)

    def test_llm_bucket_falls_back_to_granular_sum_when_no_top_level(self) -> None:
        phases = phase_timing.PhaseSeconds(prompt_eval_seconds=2.0, generation_seconds=3.0)
        self.assertEqual(phase_timing.llm_bucket_seconds(phases), 5.0)

    def test_utilization_ratios_local_backend_approximates_gpu_active(self) -> None:
        phases = phase_timing.PhaseSeconds(total_wall_seconds=10.0, llm_request_seconds=6.0)
        result = phase_timing.compute_utilization_ratios(phases, backend="ollama")
        self.assertEqual(result.model_busy_percent, 60.0)
        self.assertEqual(result.gpu_active_percent, 60.0)
        self.assertEqual(result.gpu_active_basis, "approximated_from_model_busy")

    def test_utilization_ratios_opaque_backend_leaves_gpu_active_none(self) -> None:
        phases = phase_timing.PhaseSeconds(total_wall_seconds=10.0, llm_request_seconds=6.0)
        result = phase_timing.compute_utilization_ratios(phases, backend="copilot_agent")
        self.assertEqual(result.model_busy_percent, 60.0)
        self.assertIsNone(result.gpu_active_percent)
        self.assertEqual(result.gpu_active_basis, "not_observable")

    def test_utilization_ratios_can_exceed_100_under_concurrency(self) -> None:
        # A measured gpu_active_seconds larger than total_wall_seconds is a
        # legitimate concurrency signal (multiple simultaneously-busy
        # requests), not an error -- utilization ratios are deliberately not
        # capped at 100%.
        phases = phase_timing.PhaseSeconds(total_wall_seconds=10.0, llm_request_seconds=6.0)
        result = phase_timing.compute_utilization_ratios(
            phases, backend="ollama", gpu_active_seconds=18.0
        )
        self.assertEqual(result.gpu_active_percent, 180.0)
        self.assertEqual(result.gpu_active_basis, "measured")

    def test_rollup_group_uses_ratio_of_sums_not_mean_of_percentages(self) -> None:
        # Two samples with very different durations: a naive mean-of-
        # percentages would over-weight the short attempt. Ratio-of-sums
        # must instead reflect the combined time actually spent in each
        # bucket across both attempts.
        short_sample = _sample(
            sample_id="p-short",
            total_wall_seconds=2.0,
            llm_request_seconds=1.0,
            local_tool_exec_seconds=1.0,
        )
        long_sample = _sample(
            sample_id="p-long",
            total_wall_seconds=18.0,
            llm_request_seconds=17.0,
            local_tool_exec_seconds=1.0,
        )
        rollup = phase_timing.rollup_group([short_sample, long_sample])
        self.assertEqual(rollup.phase_timing_sample_count, 2)
        # Combined: llm=18s, tool=2s out of naive_sum=20s -> 90% / 10%.
        self.assertAlmostEqual(rollup.llm_critical_path_percent, 90.0, places=6)
        self.assertAlmostEqual(rollup.tool_critical_path_percent, 10.0, places=6)

    def test_rollup_group_excludes_uninstrumented_samples_entirely(self) -> None:
        instrumented = _sample(
            sample_id="p-instrumented", total_wall_seconds=10.0, llm_request_seconds=10.0
        )
        uninstrumented = _sample(sample_id="p-bare")  # no phase-timing fields set at all
        rollup = phase_timing.rollup_group([instrumented, uninstrumented])
        self.assertEqual(rollup.phase_timing_sample_count, 1)

    def test_rollup_group_with_no_instrumented_samples_returns_empty(self) -> None:
        rollup = phase_timing.rollup_group([_sample(sample_id="p-bare")])
        self.assertEqual(rollup.phase_timing_sample_count, 0)
        self.assertIsNone(rollup.llm_critical_path_percent)
        self.assertIsNone(rollup.model_busy_percent)


class CapacityProfileTests(unittest.TestCase):
    """Fast, deterministic tests for agent_helper_eval.capacity_profile: the
    mandatory preflight gate must block concurrency profiling whenever
    single-request acceptance or the memory-safety check is missing, and the
    scaling classification must correctly distinguish scales-well/
    diminishing-returns/thrashing from the raw efficiency percent."""

    def test_preflight_allows_when_all_conditions_hold(self) -> None:
        result = capacity_profile.can_start_concurrency_profile(
            single_request_accepted=True,
            single_request_hard_gate_failed=False,
            memory_safety_checked=True,
            vram_headroom_mb=2000.0,
        )
        self.assertTrue(result.allowed)
        self.assertEqual(result.reasons, ())

    def test_preflight_blocks_when_single_request_not_accepted(self) -> None:
        result = capacity_profile.can_start_concurrency_profile(
            single_request_accepted=False,
            single_request_hard_gate_failed=False,
            memory_safety_checked=True,
        )
        self.assertFalse(result.allowed)
        self.assertTrue(any("single-request" in r for r in result.reasons))

    def test_preflight_blocks_when_hard_gate_failed(self) -> None:
        result = capacity_profile.can_start_concurrency_profile(
            single_request_accepted=True,
            single_request_hard_gate_failed=True,
            memory_safety_checked=True,
        )
        self.assertFalse(result.allowed)
        self.assertTrue(any("hard acceptance gate" in r for r in result.reasons))

    def test_preflight_blocks_when_memory_safety_not_checked(self) -> None:
        result = capacity_profile.can_start_concurrency_profile(
            single_request_accepted=True,
            single_request_hard_gate_failed=False,
            memory_safety_checked=False,
        )
        self.assertFalse(result.allowed)
        self.assertTrue(any("memory-safety" in r for r in result.reasons))

    def test_preflight_blocks_on_negative_vram_headroom(self) -> None:
        result = capacity_profile.can_start_concurrency_profile(
            single_request_accepted=True,
            single_request_hard_gate_failed=False,
            memory_safety_checked=True,
            vram_headroom_mb=-500.0,
        )
        self.assertFalse(result.allowed)
        self.assertTrue(any("negative" in r for r in result.reasons))

    def test_preflight_blocks_on_every_condition_failing_simultaneously(self) -> None:
        result = capacity_profile.can_start_concurrency_profile(
            single_request_accepted=False,
            single_request_hard_gate_failed=True,
            memory_safety_checked=False,
            vram_headroom_mb=-1.0,
        )
        self.assertFalse(result.allowed)
        self.assertEqual(len(result.reasons), 4)

    def test_classify_scaling_thresholds(self) -> None:
        self.assertEqual(capacity_profile.classify_concurrency_scaling(None), "insufficient-data")
        self.assertEqual(capacity_profile.classify_concurrency_scaling(95.0), "scales-well")
        self.assertEqual(
            capacity_profile.classify_concurrency_scaling(
                capacity_profile.GOOD_SCALING_EFFICIENCY_THRESHOLD_PERCENT
            ),
            "scales-well",
        )
        self.assertEqual(capacity_profile.classify_concurrency_scaling(65.0), "diminishing-returns")
        self.assertEqual(
            capacity_profile.classify_concurrency_scaling(
                capacity_profile.THRASHING_EFFICIENCY_THRESHOLD_PERCENT
            ),
            "diminishing-returns",
        )
        self.assertEqual(
            capacity_profile.classify_concurrency_scaling(30.0), "thrashing-or-no-benefit"
        )

    def test_compute_throughput_efficiency_percent(self) -> None:
        # Perfect linear scaling at concurrency=2 (2x throughput from 2x
        # requests) must be exactly 100%.
        self.assertEqual(
            capacity_profile.compute_throughput_efficiency_percent(80.0, 2, 40.0), 100.0
        )
        # Half of perfect linear scaling -> 50%.
        self.assertEqual(
            capacity_profile.compute_throughput_efficiency_percent(40.0, 2, 40.0), 50.0
        )

    def test_compute_throughput_efficiency_percent_none_when_baseline_missing(self) -> None:
        self.assertIsNone(
            capacity_profile.compute_throughput_efficiency_percent(80.0, 2, None)
        )
        self.assertIsNone(
            capacity_profile.compute_throughput_efficiency_percent(80.0, 2, 0.0)
        )

    def test_default_concurrency_catalog_is_valid(self) -> None:
        self.assertEqual(capacity_profile.validate_concurrency_catalog(), [])

    def test_catalog_with_missing_standard_level_is_rejected(self) -> None:
        truncated = tuple(
            s
            for s in capacity_profile.DEFAULT_CONCURRENCY_CATALOG
            if s.concurrency_level != 4
        )
        errors = capacity_profile.validate_concurrency_catalog(truncated)
        self.assertTrue(any("missing standard concurrency level" in e for e in errors))


class CapacityProfileSchemaValidationTests(unittest.TestCase):
    """Schema-level enforcement for CapacityProfileRecord: a record that
    documents an unsafe/premature concurrency run must be rejected outright,
    not merely discouraged by convention."""

    def test_valid_capacity_profile_has_no_errors(self) -> None:
        self.assertEqual(validate_capacity_profile(_capacity_profile()), [])

    def test_quality_gate_not_passed_is_hard_rejected(self) -> None:
        errors = validate_capacity_profile(
            _capacity_profile(preflight_quality_gate_passed=False)
        )
        self.assertTrue(any("preflight_quality_gate_passed" in e for e in errors))

    def test_memory_safety_not_checked_is_hard_rejected(self) -> None:
        errors = validate_capacity_profile(
            _capacity_profile(preflight_memory_safety_checked=False)
        )
        self.assertTrue(any("preflight_memory_safety_checked" in e for e in errors))

    def test_negative_vram_headroom_is_rejected(self) -> None:
        errors = validate_capacity_profile(_capacity_profile(vram_headroom_mb=-1.0))
        self.assertTrue(any("vram_headroom_mb" in e for e in errors))

    def test_requests_issued_below_concurrency_level_is_rejected(self) -> None:
        errors = validate_capacity_profile(
            _capacity_profile(concurrency_level=4, requests_issued=2)
        )
        self.assertTrue(any("requests_issued" in e for e in errors))


class AggregatePhaseTimingWiringTests(unittest.TestCase):
    """Confirms build_aggregate_for_group actually wires
    phase_timing.rollup_group() into the AggregateRecord's phase-timing
    fields, for both an instrumented and an uninstrumented sample group."""

    def test_instrumented_group_populates_critical_path_percentages(self) -> None:
        samples = [
            _sample(
                sample_id="w-1",
                total_wall_seconds=10.0,
                llm_request_seconds=6.0,
                local_tool_exec_seconds=2.0,
                orchestrator_review_seconds=2.0,
            ),
            _sample(
                sample_id="w-2",
                sample_seq=2,
                total_wall_seconds=20.0,
                llm_request_seconds=12.0,
                local_tool_exec_seconds=4.0,
                orchestrator_review_seconds=4.0,
            ),
        ]
        agg = build_all_aggregates(samples, provenance="synthetic_dry_run")[0]
        self.assertEqual(agg.phase_timing_sample_count, 2)
        total = (
            agg.llm_critical_path_percent
            + agg.tool_critical_path_percent
            + agg.queue_idle_critical_path_percent
            + agg.orchestration_critical_path_percent
        )
        self.assertAlmostEqual(total, 100.0, places=6)
        self.assertEqual(validate_aggregate(agg), [])

    def test_uninstrumented_group_leaves_phase_timing_fields_none(self) -> None:
        samples = [_sample(sample_id="w-3"), _sample(sample_id="w-4", sample_seq=2)]
        agg = build_all_aggregates(samples, provenance="synthetic_dry_run")[0]
        self.assertEqual(agg.phase_timing_sample_count, 0)
        self.assertIsNone(agg.llm_critical_path_percent)
        self.assertIsNone(agg.model_busy_percent)
        self.assertEqual(validate_aggregate(agg), [])


class AggregateSchemaPhaseTimingValidationTests(unittest.TestCase):
    """Schema-level guards on the aggregate-level phase-timing rollup
    fields: the four critical-path percentages must either all be present
    and sum to ~100, or all be None together -- never a partial mix, and
    never a sum that silently drifts from 100."""

    def _base_aggregate(self):
        samples = [
            _sample(sample_id=f"s-{i}", sample_seq=i, elapsed_seconds=float(i))
            for i in range(1, 4)
        ]
        return build_all_aggregates(samples, provenance="synthetic_dry_run")[0]

    def _instrumented_aggregate(self):
        samples = [
            _sample(
                sample_id="pt-1",
                total_wall_seconds=10.0,
                llm_request_seconds=6.0,
                local_tool_exec_seconds=2.0,
                orchestrator_review_seconds=2.0,
            )
        ]
        return build_all_aggregates(samples, provenance="synthetic_dry_run")[0]

    def test_partial_critical_path_mix_is_rejected(self) -> None:
        tampered = dataclasses.replace(
            self._base_aggregate(),
            phase_timing_sample_count=1,
            llm_critical_path_percent=50.0,
            tool_critical_path_percent=None,
            queue_idle_critical_path_percent=None,
            orchestration_critical_path_percent=None,
        )
        errors = validate_aggregate(tampered)
        self.assertTrue(any("critical_path_percent" in e for e in errors))

    def test_critical_path_sum_not_equal_100_is_rejected(self) -> None:
        tampered = dataclasses.replace(
            self._base_aggregate(),
            phase_timing_sample_count=1,
            llm_critical_path_percent=50.0,
            tool_critical_path_percent=50.0,
            queue_idle_critical_path_percent=50.0,
            orchestration_critical_path_percent=50.0,
        )
        errors = validate_aggregate(tampered)
        self.assertTrue(any("critical_path_percent" in e for e in errors))

    def test_utilization_ratio_over_100_is_permitted(self) -> None:
        # Utilization ratios (unlike the exclusive critical-path
        # percentages) are allowed to exceed 100% under concurrency.
        tampered = dataclasses.replace(self._instrumented_aggregate(), model_busy_percent=180.0)
        errors = validate_aggregate(tampered)
        self.assertEqual(errors, [])

    def test_negative_utilization_ratio_is_rejected(self) -> None:
        tampered = dataclasses.replace(self._instrumented_aggregate(), model_busy_percent=-5.0)
        errors = validate_aggregate(tampered)
        self.assertTrue(any("model_busy_percent" in e for e in errors))


class CapacityProfileCsvRoundTripTests(unittest.TestCase):
    def test_capacity_profile_round_trips_through_sqlite_and_csv(self) -> None:
        records = [
            _capacity_profile(concurrency_level=1, requests_issued=1),
            _capacity_profile(
                concurrency_level=4,
                requests_issued=4,
                scaling_classification="thrashing-or-no-benefit",
                throughput_efficiency_percent=30.0,
                vram_headroom_mb=200.0,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            conn = storage.connect(tmp_path / storage.DB_FILENAME)
            try:
                storage.insert_capacity_profiles(conn, records)
                fetched = storage.fetch_capacity_profiles(conn, campaign_id="test-campaign")
                self.assertEqual(len(fetched), 2)

                csv_path = tmp_path / storage.CAPACITY_PROFILE_CSV_FILENAME
                row_count = storage.export_capacity_profile_csv(
                    conn, csv_path, campaign_id="test-campaign"
                )
                self.assertEqual(row_count, 2)
            finally:
                conn.close()

            rows = storage.read_capacity_profile_csv(csv_path)
            self.assertEqual(len(rows), 2)
            self.assertEqual(tuple(rows[0].keys()), CAPACITY_PROFILE_CSV_COLUMNS)
            by_level = {row["concurrency_level"]: row for row in rows}
            self.assertEqual(by_level["4"]["scaling_classification"], "thrashing-or-no-benefit")
            self.assertEqual(by_level["4"]["throughput_efficiency_percent"], "30.000")
            self.assertEqual(by_level["1"]["preflight_quality_gate_passed"], "true")

    def test_export_with_no_rows_writes_header_only_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            conn = storage.connect(tmp_path / storage.DB_FILENAME)
            try:
                csv_path = tmp_path / storage.CAPACITY_PROFILE_CSV_FILENAME
                row_count = storage.export_capacity_profile_csv(
                    conn, csv_path, campaign_id="no-such-campaign"
                )
                self.assertEqual(row_count, 0)
            finally:
                conn.close()
            rows = storage.read_capacity_profile_csv(csv_path)
            self.assertEqual(rows, [])


class PhaseTimingReportRenderingTests(unittest.TestCase):
    """The phase-attribution and capacity-profile report sections must
    render without error and reflect not-instrumented / capacity-profile-
    absent states honestly rather than fabricating data."""

    def test_report_renders_phase_timing_and_capacity_sections(self) -> None:
        samples = [
            _sample(
                sample_id="r-1",
                total_wall_seconds=10.0,
                llm_request_seconds=8.0,
                orchestrator_review_seconds=2.0,
            ),
        ]
        aggregates = build_all_aggregates(samples, provenance="synthetic_dry_run")
        capacity_profiles = [_capacity_profile()]
        html = render_report(
            [
                CampaignReportData(
                    campaign_id="camp-1",
                    generated_at=utc_now_iso(),
                    aggregates=aggregates,
                    samples=samples,
                    capacity_profiles=capacity_profiles,
                )
            ]
        )
        self.assertIn("Phase attribution", html)
        self.assertIn("Capacity/concurrency profile", html)
        self.assertIn("scales-well", html)

    def test_report_handles_missing_capacity_profiles_without_error(self) -> None:
        samples = [_sample(sample_id="r-2")]
        aggregates = build_all_aggregates(samples, provenance="synthetic_dry_run")
        html = render_report(
            [
                CampaignReportData(
                    campaign_id="camp-2",
                    generated_at=utc_now_iso(),
                    aggregates=aggregates,
                    samples=samples,
                )
            ]
        )
        self.assertIn("Capacity/concurrency profile", html)
        self.assertIn(
            "No capacity/concurrency profile available for this campaign", html
        )


class ModelInventoryReportRenderingTests(unittest.TestCase):
    """The report-wide, optional model-inventory/feasibility section must
    render a prominent, unambiguous measured-vs-projection distinction and
    must never claim a 'measured' result for hardware that was not
    actually tested (defense in depth alongside the schema-level
    ``validate_model_spec`` rule in ``model_inventory.py``)."""

    def _minimal_report_html(self, model_inventory) -> str:
        samples = [_sample(sample_id="inv-1")]
        aggregates = build_all_aggregates(samples, provenance="synthetic_dry_run")
        return render_report(
            [
                CampaignReportData(
                    campaign_id="camp-inv",
                    generated_at=utc_now_iso(),
                    aggregates=aggregates,
                    samples=samples,
                )
            ],
            model_inventory=model_inventory,
        )

    def test_empty_inventory_renders_fallback_message(self) -> None:
        html = self._minimal_report_html(())
        self.assertIn("No model inventory loaded for this report", html)

    def test_measured_12gb_fit_renders_measured_badge(self) -> None:
        html = self._minimal_report_html([_model_spec()])
        self.assertIn("MEASURED", html)
        self.assertIn("Observed running locally.", html)

    def test_rtx5090_projection_never_renders_measured_badge(self) -> None:
        # Even if a caller constructs a FeasibilityProjection with
        # confidence="measured" for the hypothetical RTX 5090 (bypassing
        # model_inventory.validate_model_spec, which would normally reject
        # this at load time), the report layer itself must still refuse to
        # render the "MEASURED" badge for that field -- allow_measured=False
        # is passed unconditionally for the RTX 5090 column, downgrading any
        # "measured" claim there to the projection/estimate bucket.
        spec = _model_spec(
            fits_rtx5090_32gb_projection=FeasibilityProjection(
                fits=True, confidence="measured", assumptions="Should never be trusted here."
            )
        )
        html = self._minimal_report_html([spec])
        # Exactly one genuine "measured" badge may appear -- the 12 GB
        # column's real measured fit -- never a second one for RTX 5090.
        self.assertEqual(html.count(">MEASURED<"), 1)
        # The tampered RTX 5090 record still renders (its assumptions text
        # is present) but only under the projection/estimate badge.
        self.assertIn("Should never be trusted here.", html)
        self.assertIn("PROJECTION/ESTIMATE", html)

    def test_projected_confidence_renders_projection_badge(self) -> None:
        html = self._minimal_report_html([_model_spec()])
        self.assertIn("PROJECTION/ESTIMATE", html)
        self.assertIn("Not tested on that hardware.", html)

    def test_none_feasibility_projection_renders_na(self) -> None:
        html = self._minimal_report_html(
            [_model_spec(fits_12gb_vram=None, fits_rtx5090_32gb_projection=None)]
        )
        self.assertIn("model-inventory", html)


class OrchestratorModelInventoryLoadingTests(unittest.TestCase):
    """Loading the default example inventory for the report's optional
    feasibility section must be best-effort: a missing or invalid file
    must never raise or break dry-run/report generation, only fall back
    to an empty inventory."""

    def test_missing_inventory_file_returns_empty_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = orchestrator.load_default_model_inventory(Path(tmp))
        self.assertEqual(result, ())

    def test_valid_inventory_file_loads_specs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            benchmarks_dir = repo_root / "benchmarks"
            benchmarks_dir.mkdir(parents=True)
            payload = {
                "models": [
                    {
                        "provider": "local",
                        "backend": "ollama",
                        "model_id": "test-model:1b",
                        "display_name": "Test Model 1B",
                    }
                ]
            }
            (benchmarks_dir / "agent-helper-model-inventory.example.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            result = orchestrator.load_default_model_inventory(repo_root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].model_id, "test-model:1b")

    def test_invalid_inventory_file_returns_empty_tuple_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            benchmarks_dir = repo_root / "benchmarks"
            benchmarks_dir.mkdir(parents=True)
            # Missing required "model_id"/"display_name" fails schema
            # validation inside load_inventory(); this must be swallowed,
            # not raised, by load_default_model_inventory().
            payload = {"models": [{"provider": "local", "backend": "ollama"}]}
            (benchmarks_dir / "agent-helper-model-inventory.example.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            result = orchestrator.load_default_model_inventory(repo_root)
        self.assertEqual(result, ())

    def test_secret_looking_inventory_file_returns_empty_tuple_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            benchmarks_dir = repo_root / "benchmarks"
            benchmarks_dir.mkdir(parents=True)
            # A literal Siemens token pattern must be refused by
            # model_inventory.scan_for_secrets(); load_default_model_inventory
            # must swallow that ModelInventoryError rather than propagate it.
            (benchmarks_dir / "agent-helper-model-inventory.example.json").write_text(
                '{"models": [{"provider": "siemens", "backend": "siemens_api", '
                '"model_id": "x", "display_name": "X", '
                '"notes": "token SIAK-abc123"}]}',
                encoding="utf-8",
            )
            result = orchestrator.load_default_model_inventory(repo_root)
        self.assertEqual(result, ())


class MethodologyContentTests(unittest.TestCase):
    """The methodology section must explicitly state the two-baseline
    comparison, the cost/provider-tier-independent gate, and the measured-
    vs-projection distinction (both DE and EN) rather than leaving these
    as implicit code behavior only."""

    def _report_html(self) -> str:
        samples = [_sample(sample_id="meth-1")]
        aggregates = build_all_aggregates(samples, provenance="synthetic_dry_run")
        return render_report(
            [
                CampaignReportData(
                    campaign_id="camp-meth",
                    generated_at=utc_now_iso(),
                    aggregates=aggregates,
                    samples=samples,
                )
            ]
        )

    def test_two_baselines_explained(self) -> None:
        html = self._report_html()
        self.assertIn("Two comparison baselines", html)
        self.assertIn("Zwei Vergleichsbasislinien", html)

    def test_gate_independent_of_cost_tier_explained(self) -> None:
        html = self._report_html()
        self.assertIn("Gate independent of cost/provider tier", html)
        self.assertIn("Kosten-/Anbieter-Tier", html)

    def test_measured_vs_projection_explained(self) -> None:
        html = self._report_html()
        self.assertIn("Measured vs. assumption/projection", html)
        self.assertIn("Gemessen vs. Annahme/Projektion", html)

    def test_canonical_data_source_explained(self) -> None:
        html = self._report_html()
        self.assertIn("Canonical data source", html)
        self.assertIn("Kanonische Datenquelle", html)


class ReportNavigationTests(unittest.TestCase):
    """Fullscreen/keyboard-shortcut navigation must be present in the
    self-contained report (offline, no external JS library) alongside the
    existing sort/filter/print affordances."""

    def _report_html(self) -> str:
        samples = [_sample(sample_id="nav-1")]
        aggregates = build_all_aggregates(samples, provenance="synthetic_dry_run")
        return render_report(
            [
                CampaignReportData(
                    campaign_id="camp-nav",
                    generated_at=utc_now_iso(),
                    aggregates=aggregates,
                    samples=samples,
                )
            ]
        )

    def test_fullscreen_button_and_handler_present(self) -> None:
        html = self._report_html()
        self.assertIn('id="fullscreenBtn"', html)
        self.assertIn("toggleFullscreen", html)
        self.assertIn("requestFullscreen", html)

    def test_keyboard_shortcut_handler_present(self) -> None:
        html = self._report_html()
        self.assertIn("initKeyboardShortcuts", html)
        # Guards against hijacking keystrokes while a user types in a
        # table filter input/textarea.
        self.assertIn("isTypingTarget", html)

    def test_shortcut_hint_is_bilingual(self) -> None:
        html = self._report_html()
        self.assertIn("Keyboard shortcuts", html)
        self.assertIn("Tastenkuerzel", html)


class ChartRenderingTests(unittest.TestCase):
    """Fast, deterministic tests for every inline-SVG/HTML chart in
    ``agent_helper_eval.charts``: graceful "not enough data" fallbacks,
    HTML-escaping of untrusted model/task content, the hard-acceptance
    -gate exclusion-from-ranking vs. diagnostic-marking distinction, the
    Pareto-frontier algorithm, and the never-"measured" RTX 5090 rule.
    None of these tests call a model, network, Ollama, or llama.cpp
    process."""

    # -- 1. KPI cards --------------------------------------------------

    def test_kpi_cards_no_data_returns_hint(self) -> None:
        html = charts.render_kpi_cards((), ())
        self.assertNotIn("kpi-grid", html)
        self.assertIn("hint", html)

    def test_kpi_cards_counts_exclude_hard_gated_model_from_usable_means(self) -> None:
        usable = _aggregate(backend="ollama", model="good", hard_gate_failed=False, tokens_per_second_mean=25.0)
        excluded = _aggregate(
            backend="llama.cpp",
            model="bad",
            hard_gate_failed=True,
            tokens_per_second_mean=999.0,  # deliberately fast -- must not pull up the usable mean
        )
        samples = [
            _sample(sample_id="s1", acceptance_status="accepted"),
            _sample(sample_id="s2", acceptance_status="not_usable"),
        ]
        html = charts.render_kpi_cards([usable, excluded], samples)
        self.assertIn("kpi-grid", html)
        self.assertIn(">2</div>", html)  # total model-runs
        self.assertIn(">1/2</div>", html)  # usable/total
        self.assertIn(">25.00</div>", html)  # mean tokens/s must be the usable model's own value, not 999
        self.assertNotIn("999.00", html)

    # -- 2. Quality-gate funnel -----------------------------------------

    def test_quality_gate_funnel_no_samples_returns_hint(self) -> None:
        html = charts.render_quality_gate_funnel([])
        self.assertIn("hint", html)
        self.assertNotIn("funnel-chart", html)

    def test_quality_gate_funnel_counts_and_hatch_pattern(self) -> None:
        samples = [
            _sample(sample_id="a", acceptance_status="accepted"),
            _sample(sample_id="b", acceptance_status="accepted"),
            _sample(sample_id="c", acceptance_status="not_usable"),
            _sample(sample_id="d", acceptance_status="not_evaluated"),
        ]
        html = charts.render_quality_gate_funnel(samples, id_prefix="c0")
        self.assertIn("funnel-chart", html)
        self.assertIn("hatch-notusable-c0-funnel", html)
        self.assertIn("2/4", html)  # accepted
        self.assertIn("1/4", html)  # not usable and not evaluated both 1/4

    # -- 3. Quality vs. speed scatter with Pareto frontier ---------------

    def test_quality_speed_chart_no_data_returns_hint(self) -> None:
        html = charts.render_quality_speed_chart(())
        self.assertIn("hint", html)

    def test_quality_speed_chart_excludes_hard_gated_model_from_plotted_points(self) -> None:
        usable = _aggregate(backend="ollama", model="good", overall_score=80.0, elapsed_seconds_mean=10.0)
        excluded = _aggregate(
            backend="llama.cpp",
            model="fast-but-wrong",
            hard_gate_failed=True,
            overall_score=95.0,
            elapsed_seconds_mean=1.0,
        )
        html = charts.render_quality_speed_chart([usable, excluded])
        # The excluded model must never appear as a plotted point (its
        # score/elapsed "title" string), only in the separate exclusion note.
        self.assertNotIn("llama.cpp/fast-but-wrong | score=", html)
        self.assertIn("llama.cpp/fast-but-wrong", html)
        self.assertIn("AUSGESCHLOSSEN", html)
        self.assertIn("EXCLUDED", html)
        self.assertIn("ollama/good | score=80.00", html)

    def test_quality_speed_chart_pareto_frontier_marks_only_non_dominated_points(self) -> None:
        fast_ok = _aggregate(backend="ollama", model="fast-ok", overall_score=70.0, elapsed_seconds_mean=5.0)
        slow_worse = _aggregate(
            backend="ollama", model="slow-worse", overall_score=60.0, elapsed_seconds_mean=10.0
        )
        slow_best = _aggregate(
            backend="ollama", model="slow-best", overall_score=95.0, elapsed_seconds_mean=20.0
        )
        html = charts.render_quality_speed_chart([fast_ok, slow_worse, slow_best])
        # fast_ok is first-by-speed => always on the frontier.
        self.assertIn("ollama/fast-ok | score=70.00 | elapsed=5.00s | tier=tier-1-recommended | Pareto-optimal", html)
        # slow_best strictly beats the running-max score (70) => on the frontier.
        self.assertIn(
            "ollama/slow-best | score=95.00 | elapsed=20.00s | tier=tier-1-recommended | Pareto-optimal", html
        )
        # slow_worse is slower AND worse than fast_ok => dominated, must not
        # carry the "Pareto-optimal" marker in its own title.
        self.assertIn("ollama/slow-worse | score=60.00 | elapsed=10.00s | tier=tier-1-recommended</title>", html)

    # -- 4. Time-to-accepted-result bars ---------------------------------

    def test_time_to_accepted_chart_no_data_returns_hint(self) -> None:
        html = charts.render_time_to_accepted_chart([_aggregate(time_to_accepted_result_seconds_mean=None)])
        self.assertIn("hint", html)

    def test_time_to_accepted_chart_excludes_hard_gated_model(self) -> None:
        usable = _aggregate(backend="ollama", model="good", time_to_accepted_result_seconds_mean=12.0)
        excluded = _aggregate(
            backend="llama.cpp",
            model="bad",
            hard_gate_failed=True,
            time_to_accepted_result_seconds_mean=1.0,
        )
        html = charts.render_time_to_accepted_chart([usable, excluded], id_prefix="c0")
        self.assertIn("ollama/good", html)
        self.assertIn("llama.cpp/bad", html)  # present in the exclusion note...
        self.assertIn("AUSGESCHLOSSEN", html)
        # ...but not rendered as a bar with its own time value.
        self.assertNotIn("llama.cpp/bad: 1.00s", html)

    # -- 5. Task-by-model quality heatmap ---------------------------------

    def test_task_model_heatmap_no_samples_returns_hint(self) -> None:
        html = charts.render_task_model_heatmap([])
        self.assertIn("hint", html)

    def test_task_model_heatmap_escapes_hostile_task_and_model_names(self) -> None:
        samples = [
            _sample(
                sample_id="x1",
                task_id="<script>alert(1)</script>",
                model="<img src=x onerror=alert(2)>",
            )
        ]
        html = charts.render_task_model_heatmap(samples)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(2)>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)

    def test_task_model_heatmap_uses_final_attempt_not_first(self) -> None:
        # First attempt fails; the retried (higher iteration_index) attempt
        # is accepted -- the heatmap cell must reflect the FINAL attempt.
        first = _sample(
            sample_id="r0",
            task_id="mini-coding-v1",
            model="good",
            iteration_index=0,
            retry_count=0,
            sample_seq=1,
            acceptance_status="not_usable",
            composite_score=10.0,
        )
        final = _sample(
            sample_id="r1",
            task_id="mini-coding-v1",
            model="good",
            iteration_index=1,
            retry_count=1,
            sample_seq=2,
            acceptance_status="accepted",
            composite_score=95.0,
        )
        html = charts.render_task_model_heatmap([first, final])
        self.assertIn("score=95.00", html)
        self.assertIn("iter=1", html)

    # -- 6. Latency percentiles (P50/P95/P99) ----------------------------

    def test_latency_percentile_chart_computes_p99_on_the_fly(self) -> None:
        agg = _aggregate(
            backend="ollama",
            model="good",
            elapsed_seconds_p50=5.0,
            elapsed_seconds_p95=9.0,
        )
        samples = [
            _sample(sample_id=f"lat-{i}", backend="ollama", model="good", elapsed_seconds=float(i))
            for i in range(1, 11)
        ]
        html = charts.render_latency_percentile_chart([agg], samples)
        expected_p99 = percentile([float(i) for i in range(1, 11)], 99)
        self.assertIn(f"{expected_p99:.2f}", html)

    def test_latency_percentile_chart_excludes_hard_gated_model(self) -> None:
        excluded = _aggregate(backend="llama.cpp", model="bad", hard_gate_failed=True)
        html = charts.render_latency_percentile_chart([excluded], [])
        self.assertIn("AUSGESCHLOSSEN", html)

    # -- 7/8. Critical path + utilization ratios -------------------------

    def test_critical_path_chart_no_instrumented_data_returns_hint(self) -> None:
        html = charts.render_critical_path_chart([_aggregate(phase_timing_sample_count=0)])
        self.assertIn("hint", html)
        self.assertNotIn("critical-path-chart", html)

    def test_critical_path_chart_marks_hard_gated_row_but_still_shows_it(self) -> None:
        excluded = _aggregate(backend="llama.cpp", model="bad", hard_gate_failed=True, phase_timing_sample_count=3)
        html = charts.render_critical_path_chart([excluded])
        self.assertIn("critical-path-chart", html)
        self.assertIn("llama.cpp/bad \u26a0", html)
        self.assertIn("NICHT NUTZBAR", html)

    def test_utilization_ratio_chart_can_exceed_100_percent(self) -> None:
        agg = _aggregate(model_busy_percent=180.0, gpu_active_percent=170.0, phase_timing_sample_count=4)
        html = charts.render_utilization_ratio_chart([agg])
        self.assertIn("180.00", html)
        self.assertIn("exceed 100%", html)

    # -- 9/10. CPU/GPU + RAM/VRAM -----------------------------------------

    def test_cpu_gpu_utilization_chart_no_data_returns_hint(self) -> None:
        agg = _aggregate(
            cpu_avg_percent_mean=None, cpu_max_percent_max=None, gpu_avg_percent_mean=None, gpu_max_percent_max=None
        )
        html = charts.render_cpu_gpu_utilization_chart([agg])
        self.assertIn("hint", html)

    def test_ram_vram_utilization_chart_renders_values(self) -> None:
        agg = _aggregate(ram_avg_mb_mean=1234.0, vram_max_mb_max=8888.0)
        html = charts.render_ram_vram_utilization_chart([agg])
        self.assertIn("1234", html)
        self.assertIn("8888", html)

    # -- 11. Concurrency scaling (1/2/4) ----------------------------------

    def test_concurrency_scaling_chart_no_profiles_returns_hint(self) -> None:
        html = charts.render_concurrency_scaling_chart([])
        self.assertIn("hint", html)

    def test_concurrency_scaling_chart_renders_all_levels(self) -> None:
        rows = [
            _capacity_profile(concurrency_level=1, aggregate_tokens_per_second=40.0),
            _capacity_profile(concurrency_level=2, aggregate_tokens_per_second=70.0),
            _capacity_profile(concurrency_level=4, aggregate_tokens_per_second=85.0),
        ]
        html = charts.render_concurrency_scaling_chart(rows)
        for level in (1, 2, 4):
            self.assertIn(f">{level}<", html)

    # -- 12. Reliability/error chart ---------------------------------------

    def test_reliability_chart_marks_hard_gated_row(self) -> None:
        excluded = _aggregate(
            backend="llama.cpp",
            model="bad",
            hard_gate_failed=True,
            task_count=4,
            success_count=1,
            error_count=3,
        )
        html = charts.render_reliability_chart([excluded])
        self.assertIn("llama.cpp/bad \u26a0", html)
        self.assertIn("Error: 3/4", html)

    # -- 13. Iteration/rework chart -----------------------------------------

    def test_iteration_rework_chart_renders_retries_and_iterations(self) -> None:
        agg = _aggregate(retries_total=7, iterations_mean=2.5)
        html = charts.render_iteration_rework_chart([agg])
        self.assertIn("7.00", html)
        self.assertIn("2.50", html)

    # -- 14. Measured-vs-projected RTX 5090 fit ------------------------------

    def test_feasibility_fit_chart_no_sized_specs_returns_hint(self) -> None:
        html = charts.render_feasibility_fit_chart([_model_spec(size_gb_on_disk=None)])
        self.assertIn("hint", html)

    def test_feasibility_fit_chart_never_renders_measured_marker_for_rtx5090(self) -> None:
        spec = _model_spec(
            fits_rtx5090_32gb_projection=FeasibilityProjection(
                fits=True, confidence="measured", assumptions="should never be trusted as measured"
            )
        )
        html = charts.render_feasibility_fit_chart([spec])
        # Defensive downgrade: even if a record somehow claims
        # confidence="measured" for the hypothetical RTX 5090, the marker
        # must render as the dashed-diamond "projected" shape, never the
        # solid-circle "measured" one.
        self.assertIn("hypothetisch", html)
        self.assertIn("<polygon", html)

    # -- 15. Cost/routing scenario chart --------------------------------------

    def test_routing_scenario_chart_excludes_hard_gated_model(self) -> None:
        usable = _aggregate(provider="local", backend="ollama", model="good", suitability_tier="tier-1-recommended")
        excluded = _aggregate(
            provider="local", backend="llama.cpp", model="bad", hard_gate_failed=True, suitability_tier="not-usable"
        )
        html = charts.render_routing_scenario_chart([usable, excluded])
        self.assertIn("AUSGESCHLOSSEN", html)
        self.assertNotIn("$", html)
        self.assertNotIn("\u20ac", html)  # no Euro sign either -- no fabricated cost figures

    # -- 16. Historical trend (report-wide) -----------------------------------

    def test_historical_trend_chart_no_campaigns_returns_hint(self) -> None:
        html = charts.render_historical_trend_chart([])
        self.assertIn("hint", html)

    def test_historical_trend_chart_orders_oldest_first_and_shows_best_usable_score(self) -> None:
        older = CampaignReportData(
            campaign_id="camp-old",
            generated_at="2026-01-01T00:00:00.000+00:00",
            aggregates=[_aggregate(overall_score=50.0)],
            samples=[],
        )
        newer = CampaignReportData(
            campaign_id="camp-new",
            generated_at="2026-06-01T00:00:00.000+00:00",
            aggregates=[
                _aggregate(overall_score=90.0),
                _aggregate(hard_gate_failed=True, overall_score=99.0, backend="llama.cpp", model="bad"),
            ],
            samples=[],
        )
        html = charts.render_historical_trend_chart([newer, older])
        old_idx = html.index("camp-old")
        new_idx = html.index("camp-new")
        self.assertLess(old_idx, new_idx)  # oldest listed first
        self.assertIn("best=90.00", html)  # best USABLE score, never the excluded 99.00

    # -- End-to-end wiring check ------------------------------------------

    def test_render_report_wires_in_new_visualizations_without_error(self) -> None:
        """Regression guard for the report.py <-> charts.py wiring itself
        (as opposed to charts.py in isolation above): every new chart call
        site must actually be reachable from render_report() without
        raising, and its output must appear in the final document."""

        samples = [_sample(sample_id="e2e-1")]
        aggregates = build_all_aggregates(samples, provenance="synthetic_dry_run")
        html = render_report(
            [
                CampaignReportData(
                    campaign_id="camp-e2e",
                    generated_at=utc_now_iso(),
                    aggregates=aggregates,
                    samples=samples,
                )
            ],
            model_inventory=[_model_spec()],
        )
        for marker in ("kpi-grid", "funnel-chart", "heatmap-chart", "feasibility-chart"):
            self.assertIn(marker, html)


# ---------------------------------------------------------------------------
# Real Ollama connect-gate/mini-gate executor tests (mocked transports only)
# ---------------------------------------------------------------------------
#
# Everything below exercises ``resource_monitor``, ``ollama_client``,
# ``mini_task``, ``live_gates``, and the CLI wiring in
# ``run_agent_helper_campaign.py``. Fake transports/monitors are always
# passed as *explicit function arguments* -- never via module-level
# monkeypatching, which cannot override an already-bound default parameter
# value (``json_transport: ... = ollama_client.urllib_json_transport`` is
# bound once, at import time, to the real function object). These tests
# never open a socket, never start/stop Ollama, and never spawn anything
# other than the local Python interpreter itself (for the mini-task's own
# fixed ``unittest`` scoring subprocess).


class _FakeMonitor:
    """A ``resource_monitor.ResourceMonitor``-compatible stub: instant
    start/stop, fixed stats, no real psutil/nvidia-smi sampling -- keeps
    these tests fast and fully deterministic."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self, join_timeout_seconds: float = 2.0) -> None:
        pass

    def stats(self) -> resource_monitor.ResourceStats:
        return resource_monitor.ResourceStats(
            cpu_avg_percent=12.5,
            cpu_max_percent=20.0,
            ram_avg_mb=1500.0,
            ram_max_mb=1800.0,
            gpu_avg_percent=None,
            gpu_max_percent=None,
            vram_avg_mb=None,
            vram_max_mb=None,
            model_process_cpu_time_seconds=3.25,
        )


def _fake_json_transport_no_models_loaded(method, url, payload, timeout_seconds):
    if url.endswith("/api/ps"):
        return ollama_client.JsonResponse(200, {"models": []}, "{}")
    return ollama_client.JsonResponse(200, {"done": True}, "{}")  # the unload call


def _fake_json_transport_conflicting_model(method, url, payload, timeout_seconds):
    if url.endswith("/api/ps"):
        return ollama_client.JsonResponse(
            200, {"models": [{"name": "some-other-model", "model": "some-other-model"}]}, "{}"
        )
    return ollama_client.JsonResponse(200, {"done": True}, "{}")


def _fake_stream_transport_ok_response(url, payload, timeout_seconds):
    yield json.dumps({"model": payload["model"], "response": "OK", "done": False})
    yield json.dumps(
        {
            "model": payload["model"],
            "response": "",
            "done": True,
            "done_reason": "stop",
            "total_duration": 30_000_000,
            "load_duration": 5_000_000,
            "prompt_eval_count": 8,
            "prompt_eval_duration": 2_000_000,
            "eval_count": 2,
            "eval_duration": 20_000_000,
        }
    )


def _fake_stream_transport_reference_solution(url, payload, timeout_seconds):
    response_text = f"```python\n{mini_task.REFERENCE_SOLUTION_SOURCE}\n```"
    yield json.dumps({"model": payload["model"], "response": response_text, "done": False})
    yield json.dumps(
        {
            "model": payload["model"],
            "response": "",
            "done": True,
            "done_reason": "stop",
            "total_duration": 500_000_000,
            "load_duration": 50_000_000,
            "prompt_eval_count": 120,
            "prompt_eval_duration": 20_000_000,
            "eval_count": 80,
            "eval_duration": 400_000_000,
        }
    )


def _fake_stream_transport_unsafe_response(url, payload, timeout_seconds):
    unsafe_code = "import os\ndef normalize(records):\n    return eval('[]')\n"
    response_text = f"```python\n{unsafe_code}\n```"
    yield json.dumps({"model": payload["model"], "response": response_text, "done": False})
    yield json.dumps(
        {
            "model": payload["model"],
            "response": "",
            "done": True,
            "done_reason": "stop",
            "total_duration": 100_000_000,
            "load_duration": 10_000_000,
            "prompt_eval_count": 100,
            "prompt_eval_duration": 10_000_000,
            "eval_count": 20,
            "eval_duration": 80_000_000,
        }
    )


def _fake_stream_transport_connection_error(url, payload, timeout_seconds):
    raise ollama_client.OllamaError("connection refused (simulated, no real socket opened)")


class ResourceMonitorTests(unittest.TestCase):
    """Deterministic tests for the pure avg/max reduction and the
    background-thread monitor lifecycle (GPU query is always injected, so
    no real ``nvidia-smi``/GPU dependency exists in these tests)."""

    def test_summarize_samples_empty_series_are_all_none(self) -> None:
        stats = resource_monitor.summarize_samples([], [], [], [])
        self.assertIsNone(stats.cpu_avg_percent)
        self.assertIsNone(stats.cpu_max_percent)
        self.assertIsNone(stats.ram_avg_mb)
        self.assertIsNone(stats.ram_max_mb)
        self.assertIsNone(stats.gpu_avg_percent)
        self.assertIsNone(stats.vram_max_mb)

    def test_summarize_samples_avg_max_reduction_is_per_series_independent(self) -> None:
        stats = resource_monitor.summarize_samples(
            cpu_samples=[10.0, 20.0, 30.0],
            ram_mb_samples=[1000.0, 3000.0],
            gpu_samples=[5.0],
            vram_mb_samples=[],
        )
        self.assertEqual(stats.cpu_avg_percent, 20.0)
        self.assertEqual(stats.cpu_max_percent, 30.0)
        self.assertEqual(stats.ram_avg_mb, 2000.0)
        self.assertEqual(stats.ram_max_mb, 3000.0)
        self.assertEqual(stats.gpu_avg_percent, 5.0)
        self.assertEqual(stats.gpu_max_percent, 5.0)
        self.assertIsNone(stats.vram_avg_mb)  # empty series -- never fabricated as 0.0
        self.assertIsNone(stats.vram_max_mb)

    def test_resource_monitor_lifecycle_with_injected_gpu_query(self) -> None:
        monitor = resource_monitor.ResourceMonitor(
            sample_interval_seconds=0.01, gpu_query=lambda: (42.0, 4096.0)
        )
        monitor.start()
        try:
            time.sleep(0.05)
        finally:
            monitor.stop(join_timeout_seconds=1.0)
        stats = monitor.stats()
        self.assertIsNotNone(stats.cpu_avg_percent)
        self.assertIsNotNone(stats.ram_avg_mb)
        self.assertEqual(stats.gpu_avg_percent, 42.0)
        self.assertEqual(stats.vram_avg_mb, 4096.0)

    def test_resource_monitor_double_start_raises(self) -> None:
        monitor = resource_monitor.ResourceMonitor(
            sample_interval_seconds=0.01, gpu_query=lambda: (None, None)
        )
        monitor.start()
        try:
            with self.assertRaises(RuntimeError):
                monitor.start()
        finally:
            monitor.stop()

    def test_model_process_cpu_time_delta_is_none_when_no_process_filter_set(self) -> None:
        """Without ``process_name_filters`` there is no matching-process
        concept at all, so ``model_process_cpu_time_seconds`` must stay
        honestly ``None`` -- never fabricated as 0.0."""
        monitor = resource_monitor.ResourceMonitor(
            sample_interval_seconds=0.01, gpu_query=lambda: (None, None)
        )
        monitor.start()
        try:
            time.sleep(0.03)
        finally:
            monitor.stop(join_timeout_seconds=1.0)
        self.assertIsNone(monitor.stats().model_process_cpu_time_seconds)

    def test_model_process_cpu_time_delta_seconds_is_none_before_any_sample(self) -> None:
        """Pilot-review regression guard (fix #3): a monitor that was
        constructed with a process filter but never actually sampled a
        matching process (for example the process exited before the first
        poll) must report N/A, not a fabricated 0.0."""
        monitor = resource_monitor.ResourceMonitor(
            sample_interval_seconds=0.01,
            process_name_filters=["a-process-name-that-will-never-exist-xyz"],
            gpu_query=lambda: (None, None),
        )
        self.assertIsNone(monitor._model_process_cpu_time_delta_seconds())

    def test_model_process_cpu_time_delta_seconds_computes_positive_delta_from_snapshots(self) -> None:
        """Pure-logic regression guard (fix #3): the delta must be
        last-minus-first cumulative CPU time per PID, summed across all
        matching PIDs -- this is genuine CPU *time* consumed, never the
        system-wide CPU *utilization percentage* (a distinct field)."""
        monitor = resource_monitor.ResourceMonitor(
            sample_interval_seconds=0.01,
            process_name_filters=["ollama"],
            gpu_query=lambda: (None, None),
        )
        monitor._first_cpu_times_by_pid = {111: 2.0, 222: 5.0}
        monitor._last_cpu_times_by_pid = {111: 3.5, 222: 9.25}
        self.assertEqual(monitor._model_process_cpu_time_delta_seconds(), 5.75)

    def test_model_process_cpu_time_delta_seconds_counts_pid_seen_only_in_last_snapshot(self) -> None:
        """A PID that appears only in the final snapshot (for example a
        model-runner subprocess spawned mid-attempt, after the monitor's
        first poll) must contribute its *full* cumulative CPU time, since
        its genuine baseline before existing is 0, not an unknown/estimated
        value."""
        monitor = resource_monitor.ResourceMonitor(
            sample_interval_seconds=0.01,
            process_name_filters=["ollama"],
            gpu_query=lambda: (None, None),
        )
        monitor._first_cpu_times_by_pid = {111: 2.0}
        monitor._last_cpu_times_by_pid = {111: 2.5, 333: 1.25}
        self.assertEqual(monitor._model_process_cpu_time_delta_seconds(), 1.75)

    def test_query_nvidia_smi_gpu_vram_never_raises_and_never_fabricates(self) -> None:
        # Real subprocess call to nvidia-smi (not a model/network call). This
        # machine may or may not have a GPU; either way this must never raise
        # and must return (None, None) rather than a fabricated 0.0 on failure.
        gpu_percent, vram_mb = resource_monitor.query_nvidia_smi_gpu_vram(timeout_seconds=1.0)
        self.assertTrue(gpu_percent is None or isinstance(gpu_percent, float))
        self.assertTrue(vram_mb is None or isinstance(vram_mb, float))


class OllamaClientTests(unittest.TestCase):
    """Deterministic tests for the Ollama HTTP client using fake transports
    only -- never a real ``urllib``/network call."""

    def test_ollama_ps_parses_models(self) -> None:
        def fake_json(method, url, payload, timeout_seconds):
            self.assertEqual(method, "GET")
            self.assertTrue(url.endswith("/api/ps"))
            return ollama_client.JsonResponse(
                200,
                {
                    "models": [
                        {
                            "name": "qwen3-coder:30b",
                            "model": "qwen3-coder:30b",
                            "size": 123,
                            "digest": "abc",
                            "expires_at": "later",
                            "size_vram": 456,
                        }
                    ]
                },
                "{}",
            )

        entries = ollama_client.ollama_ps("http://127.0.0.1:11434", fake_json)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].model, "qwen3-coder:30b")
        self.assertEqual(entries[0].size_vram_bytes, 456)

    def test_ollama_ps_raises_on_non_200(self) -> None:
        def fake_json(method, url, payload, timeout_seconds):
            return ollama_client.JsonResponse(500, None, "boom")

        with self.assertRaises(ollama_client.OllamaError):
            ollama_client.ollama_ps("http://x", fake_json)

    def test_preflight_ok_when_nothing_loaded(self) -> None:
        ollama_client.check_single_model_preflight([], "qwen3-coder:30b", allow_reuse=False)

    def test_preflight_refuses_different_model_even_with_reuse(self) -> None:
        loaded = [
            ollama_client.OllamaPsEntry(
                name="other", model="other", size_bytes=None, digest=None, expires_at=None, size_vram_bytes=None
            )
        ]
        with self.assertRaises(ollama_client.OllamaPreflightConflictError):
            ollama_client.check_single_model_preflight(loaded, "qwen3-coder:30b", allow_reuse=True)

    def test_preflight_refuses_same_model_without_explicit_reuse(self) -> None:
        loaded = [
            ollama_client.OllamaPsEntry(
                name="qwen3-coder:30b",
                model="qwen3-coder:30b",
                size_bytes=None,
                digest=None,
                expires_at=None,
                size_vram_bytes=None,
            )
        ]
        with self.assertRaises(ollama_client.OllamaPreflightConflictError):
            ollama_client.check_single_model_preflight(loaded, "qwen3-coder:30b", allow_reuse=False)

    def test_preflight_allows_same_model_with_explicit_reuse(self) -> None:
        loaded = [
            ollama_client.OllamaPsEntry(
                name="qwen3-coder:30b",
                model="qwen3-coder:30b",
                size_bytes=None,
                digest=None,
                expires_at=None,
                size_vram_bytes=None,
            )
        ]
        ollama_client.check_single_model_preflight(loaded, "qwen3-coder:30b", allow_reuse=True)

    def test_ollama_generate_captures_real_ttft_and_tokens(self) -> None:
        def fake_stream(url, payload, timeout_seconds):
            self.assertTrue(url.endswith("/api/generate"))
            self.assertTrue(payload["stream"])
            yield json.dumps({"model": payload["model"], "response": "OK", "done": False})
            yield json.dumps(
                {
                    "model": payload["model"],
                    "response": "",
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 50_000_000,
                    "load_duration": 5_000_000,
                    "prompt_eval_count": 5,
                    "prompt_eval_duration": 2_000_000,
                    "eval_count": 3,
                    "eval_duration": 40_000_000,
                }
            )

        clock_values = iter([0.0, 0.01, 0.02])
        result = ollama_client.ollama_generate(
            "http://x",
            "qwen3-coder:30b",
            "hi",
            ollama_client.GenerateOptions(num_predict=16, num_ctx=512),
            fake_stream,
            clock=lambda: next(clock_values),
        )
        self.assertEqual(result.response_text, "OK")
        self.assertEqual(result.ttft_seconds, 0.01)
        self.assertEqual(result.done_reason, "stop")
        self.assertEqual(ollama_client.ns_to_seconds(result.total_duration_ns), 0.05)
        self.assertEqual(ollama_client.tokens_per_second(result.eval_count, result.eval_duration_ns), 75.0)

    def test_ollama_generate_raises_on_missing_final_done(self) -> None:
        def fake_stream(url, payload, timeout_seconds):
            yield json.dumps({"model": payload["model"], "response": "partial", "done": False})

        with self.assertRaises(ollama_client.OllamaError):
            ollama_client.ollama_generate(
                "http://x", "m", "p", ollama_client.GenerateOptions(), fake_stream
            )

    def test_ollama_unload_is_best_effort_and_never_raises_on_failure(self) -> None:
        def failing_json(method, url, payload, timeout_seconds):
            raise ollama_client.OllamaError("boom")

        ok = ollama_client.ollama_unload("http://x", "m", failing_json)
        self.assertFalse(ok)

    def test_ollama_unload_reports_true_and_sends_keep_alive_zero(self) -> None:
        def fake_json(method, url, payload, timeout_seconds):
            self.assertEqual(payload["keep_alive"], 0)
            return ollama_client.JsonResponse(200, {}, "{}")

        ok = ollama_client.ollama_unload("http://x", "m", fake_json)
        self.assertTrue(ok)

    def test_ns_to_seconds_preserves_none(self) -> None:
        self.assertIsNone(ollama_client.ns_to_seconds(None))
        self.assertEqual(ollama_client.ns_to_seconds(1_500_000_000), 1.5)

    def test_tokens_per_second_none_when_inputs_missing_or_zero(self) -> None:
        self.assertIsNone(ollama_client.tokens_per_second(None, 1_000_000_000))
        self.assertIsNone(ollama_client.tokens_per_second(10, None))
        self.assertIsNone(ollama_client.tokens_per_second(10, 0))


class MiniTaskTests(unittest.TestCase):
    """Deterministic tests for the mini coding-task fixture: extraction,
    static safety scan, placeholder detection, and fixed-test scoring. All
    "model responses" here are hand-written strings -- no model is ever
    called."""

    def test_extract_code_block_returns_contents(self) -> None:
        text = "Sure, here:\n```python\ndef normalize(records):\n    return []\n```\nDone."
        code = mini_task.extract_code_block(text)
        self.assertIn("def normalize(records):", code)

    def test_extract_code_block_returns_none_when_missing(self) -> None:
        self.assertIsNone(mini_task.extract_code_block("no code block here"))

    def test_static_safety_scan_reference_solution_is_clean(self) -> None:
        self.assertEqual(mini_task.static_safety_scan(mini_task.REFERENCE_SOLUTION_SOURCE), [])

    def test_static_safety_scan_flags_disallowed_import(self) -> None:
        findings = mini_task.static_safety_scan("import os\ndef normalize(records):\n    return []\n")
        self.assertTrue(any("disallowed import" in f for f in findings))

    def test_static_safety_scan_flags_eval_call(self) -> None:
        findings = mini_task.static_safety_scan("def normalize(records):\n    return eval('[]')\n")
        self.assertTrue(any("eval" in f for f in findings))

    def test_static_safety_scan_flags_disallowed_dunder_attribute(self) -> None:
        findings = mini_task.static_safety_scan(
            "def normalize(records):\n    return [].__class__.__subclasses__()\n"
        )
        self.assertTrue(any("__subclasses__" in f for f in findings))

    def test_static_safety_scan_reports_syntax_error(self) -> None:
        findings = mini_task.static_safety_scan("def normalize(records:\n")
        self.assertTrue(any("syntax error" in f for f in findings))

    def test_detect_placeholder_markers_flags_todo(self) -> None:
        findings = mini_task.detect_placeholder_markers(
            "def normalize(records):\n    pass  # TODO implement\n"
        )
        self.assertTrue(any("placeholder marker" in f for f in findings))

    def test_detect_placeholder_markers_requires_normalize_function(self) -> None:
        findings = mini_task.detect_placeholder_markers("def other():\n    return []\n")
        self.assertTrue(any("no top-level 'normalize' function" in f for f in findings))

    def test_detect_placeholder_markers_clean_for_reference_solution(self) -> None:
        self.assertEqual(mini_task.detect_placeholder_markers(mini_task.REFERENCE_SOLUTION_SOURCE), [])

    def test_run_mini_task_tests_reference_solution_scores_100(self) -> None:
        response = f"```python\n{mini_task.REFERENCE_SOLUTION_SOURCE}\n```"
        with tempfile.TemporaryDirectory() as tmp:
            result = mini_task.run_mini_task_tests(response, Path(tmp) / "work")
        self.assertTrue(result.executed)
        self.assertEqual(result.tests_passed, result.tests_total)
        self.assertEqual(result.deterministic_score, 100.0)
        self.assertFalse(result.unsafe_behavior_flag)
        self.assertFalse(result.output_placeholder_or_incomplete)

    def test_run_mini_task_tests_wrong_solution_scores_partial(self) -> None:
        wrong_solution = "def normalize(records):\n    return records\n"
        response = f"```python\n{wrong_solution}\n```"
        with tempfile.TemporaryDirectory() as tmp:
            result = mini_task.run_mini_task_tests(response, Path(tmp) / "work")
        self.assertTrue(result.executed)
        self.assertGreater(result.tests_total, 0)
        self.assertLess(result.tests_passed, result.tests_total)
        self.assertLess(result.deterministic_score, 100.0)

    def test_run_mini_task_tests_unsafe_code_is_never_executed(self) -> None:
        unsafe_code = "import os\ndef normalize(records):\n    return eval('[]')\n"
        response = f"```python\n{unsafe_code}\n```"
        with tempfile.TemporaryDirectory() as tmp:
            result = mini_task.run_mini_task_tests(response, Path(tmp) / "work")
        self.assertFalse(result.executed)
        self.assertTrue(result.unsafe_behavior_flag)
        self.assertEqual(result.deterministic_score, 0.0)

    def test_run_mini_task_tests_missing_code_block_is_flagged_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = mini_task.run_mini_task_tests("no code here at all", Path(tmp) / "work")
        self.assertIsNone(result.extracted_code)
        self.assertTrue(result.output_placeholder_or_incomplete)
        self.assertFalse(result.executed)

    def test_run_mini_task_tests_timeout_is_detected_and_scored_zero(self) -> None:
        infinite_loop = "def normalize(records):\n    while True:\n        pass\n"
        response = f"```python\n{infinite_loop}\n```"
        with tempfile.TemporaryDirectory() as tmp:
            result = mini_task.run_mini_task_tests(response, Path(tmp) / "work", test_timeout_seconds=1.0)
        self.assertTrue(result.test_timed_out)
        self.assertFalse(result.executed)
        self.assertEqual(result.tests_total, 0)
        self.assertEqual(result.deterministic_score, 0.0)


class LiveGatesTests(unittest.TestCase):
    """Full mocked end-to-end tests for the real connect-gate/mini-gate
    orchestration. Fake transports/monitor are always passed as *explicit
    keyword arguments* to ``run_ollama_connect_gate``/``run_ollama_mini_gate``
    -- never via module monkeypatching -- so these tests can never reach a
    real Ollama server."""

    def setUp(self) -> None:
        lease_root = self._fresh_root() / "shared-lease"
        patcher = mock.patch.dict(
            os.environ, {"LOCAL_MODEL_LEASE_DIR": str(lease_root)}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        runtime_patcher = mock.patch.object(
            live_gates.local_lock, "_assert_runtime_available", return_value=None
        )
        runtime_patcher.start()
        self.addCleanup(runtime_patcher.stop)

    def _fresh_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_connect_gate_success_is_accepted_and_persisted(self) -> None:
        root = self._fresh_root()
        result = live_gates.run_ollama_connect_gate(
            root,
            campaign_id="unit-connect",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=_fake_stream_transport_ok_response,
            monitor_factory=_FakeMonitor,
        )
        self.assertEqual(result.sample.status, "success")
        self.assertEqual(result.sample.acceptance_status, "accepted")
        self.assertEqual(result.sample.deterministic_score, 100.0)
        self.assertEqual(validate_sample(result.sample), [])
        self.assertTrue(result.db_path.exists())
        self.assertTrue(result.samples_csv.exists())
        self.assertTrue(result.aggregates_csv.exists())
        conn = storage.connect(result.db_path)
        try:
            rows = storage.fetch_samples(conn, campaign_id="unit-connect")
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)

    def test_connect_gate_phase_timing_attributes_whole_call_to_llm_not_queue(self) -> None:
        """Pilot-review regression guard (fix #2): the measured wall time of
        the whole Ollama API call must land in ``llm_request_seconds`` (the
        LLM/API critical-path bucket), never in ``llm_queue_seconds`` -- a
        fail-fast preflight lock has no real queue/wait to measure. The
        Ollama-reported ``load_duration`` is a *sub-component* surfaced
        separately in ``model_load_seconds``, additive/backward-compatible,
        and must not be double-counted against the request wall time."""
        root = self._fresh_root()
        result = live_gates.run_ollama_connect_gate(
            root,
            campaign_id="unit-connect-phase-timing",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=_fake_stream_transport_ok_response,
            monitor_factory=_FakeMonitor,
        )
        sample = result.sample
        self.assertIsNone(sample.llm_queue_seconds)
        self.assertIsNotNone(sample.llm_request_seconds)
        self.assertGreaterEqual(sample.llm_request_seconds, 0.0)
        # load_duration=5_000_000ns from _fake_stream_transport_ok_response.
        self.assertAlmostEqual(sample.model_load_seconds, 0.005, places=6)
        self.assertEqual(validate_sample(sample), [])

    def test_connect_gate_captures_orchestrator_and_model_cpu_time(self) -> None:
        """Pilot-review regression guard (fix #3): orchestrator process CPU
        time must be captured via ``time.process_time()`` bracketing (never
        left as N/A when the harness itself did real work), and the
        monitor-reported model-process CPU time delta must be wired through
        verbatim -- distinct fields, never conflated."""
        root = self._fresh_root()
        result = live_gates.run_ollama_connect_gate(
            root,
            campaign_id="unit-connect-cpu-time",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=_fake_stream_transport_ok_response,
            monitor_factory=_FakeMonitor,
        )
        sample = result.sample
        self.assertIsNotNone(sample.orchestrator_cpu_time_seconds)
        self.assertGreaterEqual(sample.orchestrator_cpu_time_seconds, 0.0)
        self.assertEqual(sample.model_cpu_time_seconds, 3.25)  # from _FakeMonitor.stats()
        self.assertEqual(validate_sample(sample), [])

    def test_connect_gate_ollama_ps_conflict_refuses_and_persists_nothing(self) -> None:
        root = self._fresh_root()
        with self.assertRaises(live_gates.LiveGateRefusedError):
            live_gates.run_ollama_connect_gate(
                root,
                campaign_id="unit-connect-refused",
                model="qwen3-coder:30b",
                json_transport=_fake_json_transport_conflicting_model,
                stream_transport=_fake_stream_transport_ok_response,
                monitor_factory=_FakeMonitor,
            )
        db_path = orchestrator.campaign_output_dir(root, "unit-connect-refused") / storage.DB_FILENAME
        self.assertFalse(db_path.exists())

    def test_connect_gate_local_lock_conflict_refuses_and_persists_nothing(self) -> None:
        root = self._fresh_root()
        lock_path = orchestrator.agent_helper_root(root) / local_lock.DEFAULT_LOCK_FILENAME
        local_lock.acquire_local_model_lock(lock_path, "ollama", "some-other-model")
        try:
            with self.assertRaises(live_gates.LiveGateRefusedError):
                live_gates.run_ollama_connect_gate(
                    root,
                    campaign_id="unit-connect-lock-refused",
                    model="qwen3-coder:30b",
                    lock_path=lock_path,
                    lock_wait_seconds=0,
                    json_transport=_fake_json_transport_no_models_loaded,
                    stream_transport=_fake_stream_transport_ok_response,
                    monitor_factory=_FakeMonitor,
                )
        finally:
            local_lock.release_local_model_lock(lock_path)
        db_path = orchestrator.campaign_output_dir(root, "unit-connect-lock-refused") / storage.DB_FILENAME
        self.assertFalse(db_path.exists())

    def test_connect_gate_generate_error_is_persisted_as_error_not_usable(self) -> None:
        root = self._fresh_root()
        result = live_gates.run_ollama_connect_gate(
            root,
            campaign_id="unit-connect-error",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=_fake_stream_transport_connection_error,
            monitor_factory=_FakeMonitor,
        )
        self.assertEqual(result.sample.status, "error")
        self.assertEqual(result.sample.acceptance_status, "not_usable")
        self.assertTrue(result.sample.system_error_flag)
        self.assertEqual(validate_sample(result.sample), [])

    def test_mini_gate_reference_solution_is_accepted(self) -> None:
        root = self._fresh_root()
        result = live_gates.run_ollama_mini_gate(
            root,
            campaign_id="unit-mini",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=_fake_stream_transport_reference_solution,
            monitor_factory=_FakeMonitor,
        )
        self.assertEqual(result.sample.status, "success")
        self.assertEqual(result.sample.acceptance_status, "accepted")
        self.assertEqual(result.sample.deterministic_score, 100.0)
        self.assertIsNotNone(result.sample.test_exec_seconds)
        self.assertEqual(validate_sample(result.sample), [])

    def test_mini_gate_phase_timing_and_cpu_time_are_populated(self) -> None:
        """Pilot-review regression guard (fixes #2 and #3), mini-gate path:
        the whole measured Ollama call must land on the LLM/API critical
        path (never queue/idle), ``model_load_seconds`` must reflect the
        Ollama-reported sub-component, and orchestrator CPU time must be
        captured -- separate from the (also real) local test-execution
        time, which must remain outside the LLM bucket."""
        root = self._fresh_root()
        result = live_gates.run_ollama_mini_gate(
            root,
            campaign_id="unit-mini-phase-timing",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=_fake_stream_transport_reference_solution,
            monitor_factory=_FakeMonitor,
        )
        sample = result.sample
        self.assertIsNone(sample.llm_queue_seconds)
        self.assertIsNotNone(sample.llm_request_seconds)
        self.assertGreaterEqual(sample.llm_request_seconds, 0.0)
        # load_duration=50_000_000ns from _fake_stream_transport_reference_solution.
        self.assertAlmostEqual(sample.model_load_seconds, 0.05, places=6)
        self.assertIsNotNone(sample.test_exec_seconds)
        self.assertGreater(sample.test_exec_seconds, 0.0)
        self.assertIsNotNone(sample.orchestrator_cpu_time_seconds)
        self.assertGreaterEqual(sample.orchestrator_cpu_time_seconds, 0.0)
        self.assertEqual(sample.model_cpu_time_seconds, 3.25)
        self.assertEqual(validate_sample(sample), [])

    def test_mini_gate_unsafe_code_is_not_usable_and_never_executed(self) -> None:
        root = self._fresh_root()
        result = live_gates.run_ollama_mini_gate(
            root,
            campaign_id="unit-mini-unsafe",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=_fake_stream_transport_unsafe_response,
            monitor_factory=_FakeMonitor,
        )
        self.assertTrue(result.sample.unsafe_behavior_flag)
        self.assertEqual(result.sample.acceptance_status, "not_usable")
        self.assertEqual(result.sample.deterministic_score, 0.0)
        self.assertEqual(validate_sample(result.sample), [])

    def test_mini_gate_wrong_solution_is_not_usable_despite_running(self) -> None:
        """Regression guard for the hard acceptance gate: a solution that
        runs but fails the fixed tests must never be 'accepted' just
        because it executed without crashing."""

        def fake_stream(url, payload, timeout_seconds):
            wrong_solution = "def normalize(records):\n    return records\n"
            response_text = f"```python\n{wrong_solution}\n```"
            yield json.dumps({"model": payload["model"], "response": response_text, "done": False})
            yield json.dumps(
                {
                    "model": payload["model"],
                    "response": "",
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 200_000_000,
                    "load_duration": 20_000_000,
                    "prompt_eval_count": 100,
                    "prompt_eval_duration": 10_000_000,
                    "eval_count": 30,
                    "eval_duration": 170_000_000,
                }
            )

        root = self._fresh_root()
        result = live_gates.run_ollama_mini_gate(
            root,
            campaign_id="unit-mini-wrong",
            model="qwen3-coder:30b",
            json_transport=_fake_json_transport_no_models_loaded,
            stream_transport=fake_stream,
            monitor_factory=_FakeMonitor,
        )
        self.assertLess(result.sample.deterministic_score, 100.0)
        self.assertEqual(result.sample.acceptance_status, "not_usable")

    def test_short_work_dir_name_is_short_and_deterministic_for_long_sample_id(self) -> None:
        """Regression guard for the real ``agent-helper-serial-pilot-20260801``
        / ``qwen3-coder:30b`` failure: a 226-character mini-task working
        directory raised ``NotADirectoryError: [WinError 267]`` from
        ``subprocess.run(cwd=...)``. ``_short_work_dir_name`` must collapse
        even a very long, realistic ``sample_id`` down to a short, fixed
        16-hex-character, deterministic name."""
        long_sample_id = "-".join(
            [
                "agent-helper-serial-pilot-20260801",
                "ollama",
                "qwen3-coder-30b",
                mini_task.TASK_ID,
                "pure_model",
                "20260801T235959123456Z",
            ]
        )
        self.assertGreater(len(long_sample_id), 100)
        name = live_gates._short_work_dir_name(long_sample_id)
        self.assertEqual(len(name), 16)
        self.assertRegex(name, r"^[0-9a-f]{16}$")
        # Deterministic: same input always produces the same short name.
        self.assertEqual(name, live_gates._short_work_dir_name(long_sample_id))
        # Different sample_ids must (with overwhelming probability) collide-free.
        self.assertNotEqual(name, live_gates._short_work_dir_name(long_sample_id + "x"))

    def test_mini_gate_uses_short_hashed_work_dir_regardless_of_long_identifiers(self) -> None:
        """End-to-end guard: even with a deliberately long campaign_id and
        model tag (mirroring the real pilot failure's identifier lengths),
        the actual subprocess working directory used by
        ``mini_task.run_mini_task_tests`` must stay short."""
        root = self._fresh_root()
        captured_work_dirs: list[Path] = []
        real_run_mini_task_tests = mini_task.run_mini_task_tests

        def _capturing_run_mini_task_tests(response_text, work_dir, **kwargs):
            captured_work_dirs.append(Path(work_dir))
            return real_run_mini_task_tests(response_text, work_dir, **kwargs)

        long_campaign_id = "agent-helper-serial-pilot-20260801-long-identifier-regression-guard"
        long_model = "qwen3-coder:30b-a-very-long-synthetic-tag-suffix-for-testing"
        with mock.patch.object(live_gates.mini_task, "run_mini_task_tests", side_effect=_capturing_run_mini_task_tests):
            result = live_gates.run_ollama_mini_gate(
                root,
                campaign_id=long_campaign_id,
                model=long_model,
                json_transport=_fake_json_transport_no_models_loaded,
                stream_transport=_fake_stream_transport_reference_solution,
                monitor_factory=_FakeMonitor,
            )
        self.assertEqual(result.sample.status, "success")
        self.assertEqual(len(captured_work_dirs), 1)
        work_dir_name = captured_work_dirs[0].name
        self.assertEqual(len(work_dir_name), 16)
        # The full path must stay comfortably under Windows' classic MAX_PATH
        # even once a filename is appended inside it.
        self.assertLess(len(str(captured_work_dirs[0] / "test_solution.py")), 200)

    def test_mini_gate_workspace_exception_is_persisted_not_usable_not_raised(self) -> None:
        """Reproduces the real serial-pilot crash directly: an unexpected
        exception raised by ``mini_task.run_mini_task_tests`` (here, the
        exact ``NotADirectoryError`` Windows raised for the too-long real
        work_dir) must never propagate out of ``run_ollama_mini_gate`` --
        it must instead be caught by the gate-boundary safety net and
        persisted as a normal, schema-valid ``status='error'`` /
        ``acceptance_status='not_usable'`` sample with
        ``system_error_code==GATE_BOUNDARY_EXCEPTION_ERROR_CODE``."""
        root = self._fresh_root()
        with mock.patch.object(
            live_gates.mini_task,
            "run_mini_task_tests",
            side_effect=NotADirectoryError(267, "The directory name is invalid"),
        ):
            result = live_gates.run_ollama_mini_gate(
                root,
                campaign_id="unit-mini-gate-boundary-exception",
                model="qwen3-coder:30b",
                json_transport=_fake_json_transport_no_models_loaded,
                stream_transport=_fake_stream_transport_reference_solution,
                monitor_factory=_FakeMonitor,
            )
        sample = result.sample
        self.assertEqual(sample.status, "error")
        self.assertEqual(sample.acceptance_status, "not_usable")
        self.assertTrue(sample.system_error_flag)
        self.assertEqual(sample.system_error_code, live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE)
        self.assertIn("NotADirectoryError", sample.system_error_message)
        self.assertEqual(validate_sample(sample), [])
        # The safety net must actually persist the row -- not just build it.
        conn = storage.connect(result.db_path)
        try:
            rows = storage.fetch_samples(conn, campaign_id="unit-mini-gate-boundary-exception")
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sample_id, sample.sample_id)

    def test_connect_gate_boundary_exception_is_persisted_not_usable_not_raised(self) -> None:
        """Same gate-boundary safety net, connect-gate path: an unexpected
        exception raised deep inside post-generate scoring/bookkeeping
        (here, ``ollama_client.tokens_per_second``) must not propagate --
        it must be persisted the same way."""
        root = self._fresh_root()
        with mock.patch.object(
            live_gates.ollama_client, "tokens_per_second", side_effect=RuntimeError("boom")
        ):
            result = live_gates.run_ollama_connect_gate(
                root,
                campaign_id="unit-connect-gate-boundary-exception",
                model="qwen3-coder:30b",
                json_transport=_fake_json_transport_no_models_loaded,
                stream_transport=_fake_stream_transport_ok_response,
                monitor_factory=_FakeMonitor,
            )
        sample = result.sample
        self.assertEqual(sample.status, "error")
        self.assertEqual(sample.acceptance_status, "not_usable")
        self.assertTrue(sample.system_error_flag)
        self.assertEqual(sample.system_error_code, live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE)
        self.assertIn("RuntimeError", sample.system_error_message)
        self.assertEqual(validate_sample(sample), [])


class LiveGateCliWiringTests(unittest.TestCase):
    """Verifies ``run_agent_helper_campaign.py``'s ``connect-gate-run``/
    ``mini-gate-run`` argparse wiring and argument-forwarding. The
    ``live_gates`` functions are replaced with mocks here (a regular
    attribute lookup on the module at call time, unlike a function's own
    bound default parameter values), so no transport -- fake or real -- is
    ever invoked in this class."""

    @staticmethod
    def _fake_result() -> "live_gates.LiveGateResult":
        return live_gates.LiveGateResult(
            sample=_sample(sample_id="s-cli"),
            output_dir=Path("out"),
            db_path=Path("out/db"),
            samples_csv=Path("out/s.csv"),
            aggregates_csv=Path("out/a.csv"),
            report_path=Path("out/report.html"),
            combined_report_path=None,
        )

    def test_connect_gate_run_parses_defaults_and_overrides(self) -> None:
        args = campaign_cli.parse_args(
            [
                "connect-gate-run",
                "--campaign-id",
                "camp-1",
                "--model",
                "qwen3-coder:30b",
                "--timeout-seconds",
                "12.5",
                "--num-predict",
                "8",
                "--num-ctx",
                "256",
                "--allow-reuse-loaded-model",
            ]
        )
        self.assertEqual(args.subcommand, "connect-gate-run")
        self.assertEqual(args.campaign_id, "camp-1")
        self.assertEqual(args.model, "qwen3-coder:30b")
        self.assertEqual(args.timeout_seconds, 12.5)
        self.assertEqual(args.num_predict, 8)
        self.assertEqual(args.num_ctx, 256)
        self.assertTrue(args.allow_reuse_loaded_model)
        self.assertIsNone(args.keep_alive)

    def test_mini_gate_run_parses_defaults_and_overrides(self) -> None:
        args = campaign_cli.parse_args(
            [
                "mini-gate-run",
                "--campaign-id",
                "camp-2",
                "--model",
                "qwen3-coder:30b",
                "--test-timeout-seconds",
                "5.0",
            ]
        )
        self.assertEqual(args.subcommand, "mini-gate-run")
        self.assertEqual(args.test_timeout_seconds, 5.0)
        self.assertEqual(args.timeout_seconds, 180.0)
        self.assertEqual(args.num_predict, 800)
        self.assertEqual(args.num_ctx, 4096)

    def test_cmd_connect_gate_run_forwards_kwargs_and_omits_keep_alive_when_none(self) -> None:
        args = campaign_cli.parse_args(["connect-gate-run", "--campaign-id", "camp-3", "--model", "m"])
        with mock.patch.object(
            campaign_cli.live_gates, "run_ollama_connect_gate", return_value=self._fake_result()
        ) as mocked:
            rc = campaign_cli._cmd_connect_gate_run(args)
        self.assertEqual(rc, 0)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["campaign_id"], "camp-3")
        self.assertEqual(mocked.call_args.kwargs["model"], "m")
        self.assertNotIn("keep_alive", mocked.call_args.kwargs)

    def test_cmd_connect_gate_run_forwards_explicit_keep_alive(self) -> None:
        args = campaign_cli.parse_args(
            ["connect-gate-run", "--campaign-id", "camp-4", "--model", "m", "--keep-alive", "1m"]
        )
        with mock.patch.object(
            campaign_cli.live_gates, "run_ollama_connect_gate", return_value=self._fake_result()
        ) as mocked:
            campaign_cli._cmd_connect_gate_run(args)
        self.assertEqual(mocked.call_args.kwargs["keep_alive"], "1m")

    def test_cmd_connect_gate_run_refusal_returns_exit_code_2(self) -> None:
        args = campaign_cli.parse_args(["connect-gate-run", "--campaign-id", "camp-5", "--model", "m"])
        with mock.patch.object(
            campaign_cli.live_gates,
            "run_ollama_connect_gate",
            side_effect=live_gates.LiveGateRefusedError("simulated refusal"),
        ):
            rc = campaign_cli._cmd_connect_gate_run(args)
        self.assertEqual(rc, 2)

    def test_cmd_mini_gate_run_forwards_test_timeout_and_returns_success(self) -> None:
        args = campaign_cli.parse_args(
            ["mini-gate-run", "--campaign-id", "camp-6", "--model", "m", "--test-timeout-seconds", "9.0"]
        )
        with mock.patch.object(
            campaign_cli.live_gates, "run_ollama_mini_gate", return_value=self._fake_result()
        ) as mocked:
            rc = campaign_cli._cmd_mini_gate_run(args)
        self.assertEqual(rc, 0)
        self.assertEqual(mocked.call_args.kwargs["test_timeout_seconds"], 9.0)

    def test_cmd_mini_gate_run_refusal_returns_exit_code_2(self) -> None:
        args = campaign_cli.parse_args(["mini-gate-run", "--campaign-id", "camp-7", "--model", "m"])
        with mock.patch.object(
            campaign_cli.live_gates,
            "run_ollama_mini_gate",
            side_effect=live_gates.LiveGateRefusedError("simulated refusal"),
        ):
            rc = campaign_cli._cmd_mini_gate_run(args)
        self.assertEqual(rc, 2)


def _fake_tags_and_show_transport(tags_body: dict, show_bodies: dict):
    """Builds a fake ``JsonTransport`` serving fixed ``/api/tags``/``/api/show``
    bodies, keyed by the requested tag for ``/api/show``. Never opens a
    socket."""

    def transport(method, url, payload, timeout_seconds):
        if url.endswith("/api/tags"):
            assert method == "GET"
            return ollama_client.JsonResponse(200, tags_body, "{}")
        if url.endswith("/api/show"):
            assert method == "POST"
            tag = payload["model"]
            body = show_bodies.get(tag)
            if body is None:
                return ollama_client.JsonResponse(404, None, "not found")
            return ollama_client.JsonResponse(200, body, "{}")
        raise AssertionError(f"unexpected url {url}")

    return transport


class OllamaInventoryTests(unittest.TestCase):
    """Deterministic tests for /api/tags + /api/show discovery, using only
    fake transports -- never a real network/Ollama call."""

    def test_discovery_maps_tags_and_show_fields(self) -> None:
        tags_body = {
            "models": [
                {
                    "name": "qwen3-coder:30b",
                    "model": "qwen3-coder:30b",
                    "size": 20_000_000_000,
                    "digest": "sha256:abc",
                    "modified_at": "2026-08-01T00:00:00Z",
                    "details": {
                        "family": "qwen3",
                        "families": ["qwen3"],
                        "parameter_size": "30.5B",
                        "quantization_level": "Q4_K_M",
                    },
                }
            ]
        }
        show_bodies = {
            "qwen3-coder:30b": {
                "model_info": {
                    "general.parameter_count": 30_500_000_000,
                    "qwen3.context_length": 32768,
                },
                "capabilities": ["completion", "tools"],
                "details": {"family": "qwen3", "families": ["qwen3"]},
            }
        }
        transport = _fake_tags_and_show_transport(tags_body, show_bodies)
        result = ollama_inventory.discover_ollama_inventory("http://127.0.0.1:11434", transport)
        self.assertIsNone(result.tags_error)
        self.assertEqual(len(result.models), 1)
        model = result.models[0]
        self.assertEqual(model.tag, "qwen3-coder:30b")
        self.assertEqual(model.size_bytes, 20_000_000_000)
        self.assertEqual(model.quantization, "Q4_K_M")
        self.assertEqual(model.context_length, 32768)
        self.assertAlmostEqual(model.parameters_billion, 30.5, places=2)
        self.assertEqual(model.parameters_source, "model_info['general.parameter_count']")
        self.assertIn("tools", model.capabilities)
        self.assertFalse(model.is_cloud)
        self.assertEqual(model.architecture, "dense")
        self.assertIsNone(model.show_error)

    def test_cloud_tag_classified_by_naming_convention(self) -> None:
        tags_body = {"models": [{"name": "qwen3-coder:480b-cloud", "model": "qwen3-coder:480b-cloud", "size": 1, "digest": "d", "modified_at": "t", "details": {}}]}
        transport = _fake_tags_and_show_transport(tags_body, {})
        result = ollama_inventory.discover_ollama_inventory("http://x", transport)
        model = result.models[0]
        self.assertTrue(model.is_cloud)
        self.assertIsNotNone(model.cloud_evidence)

    def test_moe_expert_count_evidence_wins_over_dense_family_hint(self) -> None:
        tags_body = {"models": [{"name": "qwen3moe:30b", "model": "qwen3moe:30b", "size": 1, "digest": "d", "modified_at": "t", "details": {"family": "qwen3moe", "families": ["qwen3moe"]}}]}
        show_bodies = {"qwen3moe:30b": {"model_info": {"qwen3moe.expert_count": 128}}}
        transport = _fake_tags_and_show_transport(tags_body, show_bodies)
        result = ollama_inventory.discover_ollama_inventory("http://x", transport)
        model = result.models[0]
        self.assertEqual(model.architecture, "moe")
        self.assertIn("expert_count", model.architecture_evidence)

    def test_moe_family_hint_checked_before_dense_hint(self) -> None:
        # "qwen3moe" contains both "qwen3" (dense hint) and "moe" (MoE hint);
        # MoE must win since it is checked first.
        tags_body = {"models": [{"name": "qwen3moe:30b", "model": "qwen3moe:30b", "size": 1, "digest": "d", "modified_at": "t", "details": {"family": "qwen3moe", "families": []}}]}
        transport = _fake_tags_and_show_transport(tags_body, {})
        result = ollama_inventory.discover_ollama_inventory("http://x", transport)
        self.assertEqual(result.models[0].architecture, "moe")

    def test_no_architecture_evidence_yields_unknown(self) -> None:
        tags_body = {"models": [{"name": "mystery:1b", "model": "mystery:1b", "size": 1, "digest": "d", "modified_at": "t", "details": {"family": "mystery-arch", "families": []}}]}
        transport = _fake_tags_and_show_transport(tags_body, {})
        result = ollama_inventory.discover_ollama_inventory("http://x", transport)
        self.assertEqual(result.models[0].architecture, "unknown")
        self.assertIsNone(result.models[0].architecture_evidence)

    def test_show_failure_for_one_tag_does_not_drop_the_tag(self) -> None:
        tags_body = {"models": [{"name": "broken:1b", "model": "broken:1b", "size": 1, "digest": "d", "modified_at": "t", "details": {}}]}
        transport = _fake_tags_and_show_transport(tags_body, {})  # no show body -> 404
        result = ollama_inventory.discover_ollama_inventory("http://x", transport)
        self.assertEqual(len(result.models), 1)
        self.assertIsNotNone(result.models[0].show_error)

    def test_tags_endpoint_failure_yields_empty_models_and_error(self) -> None:
        def transport(method, url, payload, timeout_seconds):
            return ollama_client.JsonResponse(500, None, "boom")

        result = ollama_inventory.discover_ollama_inventory("http://x", transport)
        self.assertEqual(result.models, [])
        self.assertIsNotNone(result.tags_error)

    def test_tags_endpoint_connection_error_yields_empty_models_and_error(self) -> None:
        def transport(method, url, payload, timeout_seconds):
            raise ollama_client.OllamaError("connection refused (simulated)")

        result = ollama_inventory.discover_ollama_inventory("http://x", transport)
        self.assertEqual(result.models, [])
        self.assertIsNotNone(result.tags_error)

    def test_snapshot_save_and_load_round_trip(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        campaign_dir = Path(tmp.name) / "campaign-x"
        original = ollama_inventory.DiscoveryResult(
            schema_version=ollama_inventory.INVENTORY_SNAPSHOT_SCHEMA_VERSION,
            generated_at=utc_now_iso(),
            base_url="http://127.0.0.1:11434",
            models=[
                ollama_inventory.DiscoveredOllamaModel(
                    tag="qwen3-coder:30b", model_id="qwen3-coder:30b", digest="d",
                    size_bytes=1, modified_at="t", architecture="dense",
                    capabilities=("completion",), families=("qwen3",),
                )
            ],
        )
        path = ollama_inventory.save_inventory_snapshot(original, campaign_dir)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, ollama_inventory.INVENTORY_SNAPSHOT_FILENAME)
        loaded = ollama_inventory.load_inventory_snapshot(path)
        self.assertEqual(loaded.base_url, original.base_url)
        self.assertEqual(len(loaded.models), 1)
        self.assertEqual(loaded.models[0].tag, "qwen3-coder:30b")
        self.assertEqual(loaded.models[0].capabilities, ("completion",))

    def test_snapshot_never_touches_example_inventory_file(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo_root = Path(tmp.name)
        example_path = repo_root / "benchmarks" / "agent-helper-model-inventory.example.json"
        example_path.parent.mkdir(parents=True)
        example_path.write_text('{"hand_curated": true}', encoding="utf-8")
        campaign_dir = repo_root / "benchmark_results" / "agent-helper" / "camp"
        result = ollama_inventory.DiscoveryResult(
            schema_version=ollama_inventory.INVENTORY_SNAPSHOT_SCHEMA_VERSION,
            generated_at=utc_now_iso(), base_url="http://x", models=[],
        )
        ollama_inventory.save_inventory_snapshot(result, campaign_dir)
        self.assertEqual(example_path.read_text(encoding="utf-8"), '{"hand_curated": true}')

    def test_estimate_vram_fit_is_always_estimated_never_measured(self) -> None:
        projection = ollama_inventory.estimate_vram_fit(8.0, 12.0)
        self.assertEqual(projection.confidence, "estimated")
        self.assertTrue(projection.fits)
        too_big = ollama_inventory.estimate_vram_fit(30.0, 12.0)
        self.assertFalse(too_big.fits)

    def test_to_model_spec_produces_valid_spec_with_estimated_confidence(self) -> None:
        model = ollama_inventory.DiscoveredOllamaModel(
            tag="qwen3-coder:30b", model_id="qwen3-coder:30b", digest="d",
            size_bytes=20_000_000_000, modified_at="t", architecture="dense",
            parameters_billion=30.5, quantization="Q4_K_M", context_length=32768,
        )
        spec = ollama_inventory.to_model_spec(model)
        self.assertEqual(spec.provider, "local")
        self.assertEqual(spec.backend, "ollama")
        self.assertEqual(spec.fits_12gb_vram.confidence, "estimated")
        self.assertEqual(spec.fits_rtx5090_32gb_projection.confidence, "estimated")
        from agent_helper_eval.model_inventory import validate_model_spec

        self.assertEqual(validate_model_spec(spec), [])


def _discovery_with_tags(*specs) -> "ollama_inventory.DiscoveryResult":
    """``specs`` is a list of (tag, size_gb, architecture, quantization, is_cloud) tuples."""

    models = []
    for tag, size_gb, architecture, quantization, is_cloud in specs:
        models.append(
            ollama_inventory.DiscoveredOllamaModel(
                tag=tag, model_id=tag, digest="d", modified_at="t",
                size_bytes=int(size_gb * 1_000_000_000) if size_gb is not None else None,
                architecture=architecture, quantization=quantization, is_cloud=is_cloud,
                cloud_evidence="tag naming convention" if is_cloud else None,
            )
        )
    return ollama_inventory.DiscoveryResult(
        schema_version=ollama_inventory.INVENTORY_SNAPSHOT_SCHEMA_VERSION,
        generated_at=utc_now_iso(), base_url="http://x", models=models,
    )


class SerialPlanTests(unittest.TestCase):
    """Deterministic tests for build_serial_plan's two selection modes.
    Never touches the network/filesystem beyond in-memory dataclasses."""

    def test_explicit_models_mode_preserves_order_and_defers_the_rest(self) -> None:
        discovery = _discovery_with_tags(
            ("a:1b", 1.0, "dense", "q4", False),
            ("b:1b", 2.0, "dense", "q4", False),
            ("c:1b", 3.0, "dense", "q4", False),
        )
        plan = serial_campaign.build_serial_plan(
            discovery, "camp", generated_at="t", models=["c:1b", "a:1b"]
        )
        self.assertEqual(plan.selection_mode, "explicit_models")
        self.assertEqual([e.tag for e in plan.included], ["c:1b", "a:1b"])
        self.assertEqual([e.order for e in plan.included], [0, 1])
        deferred_tags = {d.tag for d in plan.deferred}
        self.assertEqual(deferred_tags, {"b:1b"})
        self.assertIn("not selected", plan.deferred[0].reason)

    def test_explicit_models_mode_reports_not_found_tag_as_deferred(self) -> None:
        discovery = _discovery_with_tags(("a:1b", 1.0, "dense", "q4", False))
        plan = serial_campaign.build_serial_plan(
            discovery, "camp", generated_at="t", models=["a:1b", "missing:9b"]
        )
        self.assertEqual([e.tag for e in plan.included], ["a:1b"])
        missing_entries = [d for d in plan.deferred if d.tag == "missing:9b"]
        self.assertEqual(len(missing_entries), 1)
        self.assertIn("not found", missing_entries[0].reason)

    def test_filter_mode_excludes_cloud_by_default(self) -> None:
        discovery = _discovery_with_tags(
            ("local:1b", 1.0, "dense", "q4", False),
            ("cloud:1b", 1.0, "dense", "q4", True),
        )
        plan = serial_campaign.build_serial_plan(discovery, "camp", generated_at="t")
        self.assertEqual(plan.selection_mode, "filtered")
        self.assertEqual([e.tag for e in plan.included], ["local:1b"])
        deferred = {d.tag: d.reason for d in plan.deferred}
        self.assertIn("cloud", deferred["cloud:1b"])

    def test_filter_mode_include_cloud_flag_includes_cloud_tags(self) -> None:
        discovery = _discovery_with_tags(("cloud:1b", 1.0, "dense", "q4", True))
        plan = serial_campaign.build_serial_plan(discovery, "camp", generated_at="t", include_cloud=True)
        self.assertEqual([e.tag for e in plan.included], ["cloud:1b"])
        self.assertEqual(plan.deferred, [])

    def test_filter_mode_max_size_excludes_large_models_with_reason(self) -> None:
        discovery = _discovery_with_tags(
            ("small:1b", 1.0, "dense", "q4", False),
            ("huge:400b", 250.0, "dense", "q4", False),
        )
        plan = serial_campaign.build_serial_plan(discovery, "camp", generated_at="t", max_size_gb=12.0)
        self.assertEqual([e.tag for e in plan.included], ["small:1b"])
        deferred = {d.tag: d.reason for d in plan.deferred}
        self.assertIn("size", deferred["huge:400b"])

    def test_filter_mode_architecture_and_quantization_filters(self) -> None:
        discovery = _discovery_with_tags(
            ("dense-q4:1b", 1.0, "dense", "q4_k_m", False),
            ("moe-q8:1b", 1.0, "moe", "q8_0", False),
        )
        plan = serial_campaign.build_serial_plan(
            discovery, "camp", generated_at="t",
            include_architectures=["dense"], include_quantizations=["q4_k_m"],
        )
        self.assertEqual([e.tag for e in plan.included], ["dense-q4:1b"])

    def test_filter_mode_force_include_bypasses_exclusion_but_keeps_reason_visible(self) -> None:
        discovery = _discovery_with_tags(("huge:671b", 400.0, "dense", "q4", False))
        plan = serial_campaign.build_serial_plan(
            discovery, "camp", generated_at="t", max_size_gb=12.0, force_include=["huge:671b"],
        )
        self.assertEqual([e.tag for e in plan.included], ["huge:671b"])
        # Still visible in deferred (as a transparency note), not silently hidden.
        force_notes = [d for d in plan.deferred if d.tag == "huge:671b"]
        self.assertEqual(len(force_notes), 1)
        self.assertIn("force-included", force_notes[0].reason)
        self.assertIn("size", force_notes[0].reason)

    def test_every_discovered_model_appears_in_included_or_deferred(self) -> None:
        discovery = _discovery_with_tags(
            ("a:1b", 1.0, "dense", "q4", False),
            ("b:1b", 200.0, "dense", "q4", False),
            ("c:1b", 1.0, "dense", "q4", True),
        )
        plan = serial_campaign.build_serial_plan(discovery, "camp", generated_at="t", max_size_gb=12.0)
        included_tags = {e.tag for e in plan.included}
        deferred_tags = {d.tag for d in plan.deferred}
        self.assertEqual(included_tags | deferred_tags, {"a:1b", "b:1b", "c:1b"})
        self.assertEqual(included_tags & deferred_tags, set())

    def test_save_serial_plan_writes_json(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        discovery = _discovery_with_tags(("a:1b", 1.0, "dense", "q4", False))
        plan = serial_campaign.build_serial_plan(discovery, "camp", generated_at="t")
        path = serial_campaign.save_serial_plan(plan, Path(tmp.name) / "camp")
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["campaign_id"], "camp")
        self.assertEqual(len(data["included"]), 1)


class _RecordingGate:
    """A fake connect/mini gate callable that records call order/timing and
    returns a scripted sequence of outcomes -- one entry consumed per call
    for a given model. Never touches a real transport."""

    def __init__(self) -> None:
        self.calls: list = []
        self._scripts: dict = {}

    def script(self, model: str, outcomes: list) -> None:
        self._scripts[model] = list(outcomes)

    def __call__(self, repo_root, *, campaign_id, model, **kwargs):
        self.calls.append((model, time.monotonic()))
        outcomes = self._scripts.get(model)
        if not outcomes:
            raise AssertionError(f"no scripted outcome left for model {model!r}")
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        sample = _sample(
            sample_id=f"{campaign_id}-{model}-{outcome['status']}",
            model=model,
            status=outcome["status"],
            deterministic_score=outcome.get("deterministic_score"),
            acceptance_status=outcome["acceptance_status"],
            acceptance_reasons=outcome.get("acceptance_reasons"),
            task_id=outcome.get("task_id", "connect-smoke-v1"),
            system_error_code=outcome.get("system_error_code"),
        )
        return live_gates.LiveGateResult(
            sample=sample,
            output_dir=Path("out"),
            db_path=Path("out/db"),
            samples_csv=Path("out/s.csv"),
            aggregates_csv=Path("out/a.csv"),
            report_path=Path("out/report.html"),
            combined_report_path=None,
        )


class SerialCampaignExecutionTests(unittest.TestCase):
    """Deterministic tests for run_serial_campaign's orchestration logic,
    using injected fake connect_gate_fn/mini_gate_fn -- never a real
    transport, and never live_gates' own persistence (that full-stack path
    is covered separately by SerialCampaignLiveGatesWiringTests below)."""

    def _fresh_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def _plan(self, root: Path, *tags: str) -> "serial_campaign.SerialPlan":
        discovery = _discovery_with_tags(*[(tag, 1.0, "dense", "q4", False) for tag in tags])
        return serial_campaign.build_serial_plan(discovery, "camp", generated_at="t", models=list(tags))

    def test_checkpoint_retries_transient_windows_replace_failure(self) -> None:
        root = self._fresh_root()
        path = root / "progress.json"
        real_replace = serial_campaign.os.replace
        attempts = 0

        def flaky_replace(source, destination):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError(5, "Access is denied")
            return real_replace(source, destination)

        with mock.patch.object(
            serial_campaign.os, "replace", side_effect=flaky_replace
        ), mock.patch.object(serial_campaign.time, "sleep") as sleep:
            serial_campaign._write_checkpoint(path, {"state": "ready"})

        self.assertEqual(attempts, 2)
        sleep.assert_called_once_with(0.02)
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"state": "ready"},
        )

    def test_confirm_false_never_calls_gates_and_returns_not_executed(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        connect_fn = _RecordingGate()
        mini_fn = _RecordingGate()
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=False, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertFalse(result.executed)
        self.assertEqual(result.steps, [])
        self.assertEqual(connect_fn.calls, [])
        self.assertEqual(mini_fn.calls, [])

    def test_accepted_connect_runs_mini_and_both_persist_in_order(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "connect-smoke-v1"}])
        mini_fn = _RecordingGate()
        mini_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertTrue(result.executed)
        self.assertEqual([(s.gate, s.outcome) for s in result.steps], [("connect", "accepted"), ("mini", "accepted")])
        self.assertEqual(len(connect_fn.calls), 1)
        self.assertEqual(len(mini_fn.calls), 1)

    def test_rejected_connect_skips_mini_with_persisted_reason(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [{"status": "success", "acceptance_status": "not_usable"}])
        mini_fn = _RecordingGate()
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertEqual(result.steps[0].outcome, "not_usable")
        self.assertEqual(result.steps[1].gate, "mini")
        self.assertEqual(result.steps[1].outcome, "skipped")
        self.assertIn("connect gate was not accepted", result.steps[1].skip_reason)
        self.assertEqual(mini_fn.calls, [])

    def test_timeout_sample_is_recorded_and_not_auto_retried(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [{"status": "timeout", "acceptance_status": "not_usable"}])
        mini_fn = _RecordingGate()
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, max_retries=5, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertEqual(len(connect_fn.calls), 1)  # no retry despite max_retries=5
        self.assertEqual(result.steps[0].retries_used, 0)

    def test_transient_error_is_retried_up_to_max_retries(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        connect_fn = _RecordingGate()
        connect_fn.script(
            "a:1b",
            [
                {"status": "error", "acceptance_status": "not_usable"},
                {"status": "error", "acceptance_status": "not_usable"},
                {"status": "success", "acceptance_status": "accepted"},
            ],
        )
        mini_fn = _RecordingGate()
        mini_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, max_retries=2, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertEqual(len(connect_fn.calls), 3)
        self.assertEqual(result.steps[0].outcome, "accepted")
        self.assertEqual(result.steps[0].retries_used, 2)

    def test_order_preservation_and_one_at_a_time_across_multiple_models(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b", "b:1b", "c:1b")
        connect_fn = _RecordingGate()
        mini_fn = _RecordingGate()
        for tag in ("a:1b", "b:1b", "c:1b"):
            connect_fn.script(tag, [{"status": "success", "acceptance_status": "accepted"}])
            mini_fn.script(tag, [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        serial_campaign.run_serial_campaign(root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn)
        # Connect gate call order must exactly match the plan's order, and
        # every connect call for a model happens before that model's mini
        # call (structurally guaranteed by the sequential for-loop, verified
        # here via call-order timestamps).
        self.assertEqual([m for m, _ in connect_fn.calls], ["a:1b", "b:1b", "c:1b"])
        self.assertEqual([m for m, _ in mini_fn.calls], ["a:1b", "b:1b", "c:1b"])

    def test_preflight_refusal_halts_campaign_by_default(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b", "b:1b")
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [live_gates.LiveGateRefusedError("some other model already loaded")])
        mini_fn = _RecordingGate()
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertTrue(result.halted_on_refusal)
        self.assertEqual(len(result.steps), 1)
        self.assertEqual(result.steps[0].outcome, "refused")
        self.assertEqual(len(connect_fn.calls), 1)  # "b:1b" never attempted

    def test_continue_on_refusal_skips_mini_and_proceeds_to_next_model(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b", "b:1b")
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [live_gates.LiveGateRefusedError("transient conflict")])
        connect_fn.script("b:1b", [{"status": "success", "acceptance_status": "accepted"}])
        mini_fn = _RecordingGate()
        mini_fn.script("b:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, halt_on_preflight_refusal=False,
            connect_gate_fn=connect_fn, mini_gate_fn=mini_fn,
        )
        self.assertTrue(result.halted_on_refusal)  # recorded even though we continued
        model_gate_outcomes = [(s.model, s.gate, s.outcome) for s in result.steps]
        self.assertIn(("a:1b", "mini", "skipped"), model_gate_outcomes)
        self.assertIn(("b:1b", "connect", "accepted"), model_gate_outcomes)
        self.assertIn(("b:1b", "mini", "accepted"), model_gate_outcomes)

    def test_interruption_writes_checkpoint_and_returns_without_raising(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b", "b:1b")
        connect_fn = _RecordingGate()

        def raise_keyboard_interrupt(repo_root, *, campaign_id, model, **kwargs):
            raise KeyboardInterrupt()

        connect_fn = raise_keyboard_interrupt
        mini_fn = _RecordingGate()
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertTrue(result.interrupted)
        self.assertTrue(result.checkpoint_path.exists())
        payload = json.loads(result.checkpoint_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["interrupted"])

    def test_checkpoint_is_written_atomically_via_tmp_rename(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted"}])
        mini_fn = _RecordingGate()
        mini_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertTrue(result.checkpoint_path.exists())
        self.assertFalse(result.checkpoint_path.with_suffix(".json.tmp").exists())

    def test_resume_skips_model_with_existing_sample_via_real_storage(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        output_dir = orchestrator.campaign_output_dir(root, "camp")
        output_dir.mkdir(parents=True)
        db_path = output_dir / storage.DB_FILENAME
        conn = storage.connect(db_path)
        try:
            storage.insert_samples(
                conn,
                [
                    _sample(
                        sample_id="existing-connect", model="a:1b",
                        task_id="connect-smoke-v1", campaign_id="camp",
                        acceptance_status="accepted",
                    )
                ],
            )
        finally:
            conn.close()
        connect_fn = _RecordingGate()  # no script -- must never be called
        mini_fn = _RecordingGate()
        mini_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertEqual(connect_fn.calls, [])
        self.assertEqual(result.steps[0].outcome, "resumed")
        self.assertEqual(len(mini_fn.calls), 1)  # mini still runs since connect was accepted

    def test_force_rerun_overrides_resume_skip(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        output_dir = orchestrator.campaign_output_dir(root, "camp")
        output_dir.mkdir(parents=True)
        db_path = output_dir / storage.DB_FILENAME
        conn = storage.connect(db_path)
        try:
            storage.insert_samples(
                conn,
                [
                    _sample(
                        sample_id="existing-connect", model="a:1b",
                        task_id="connect-smoke-v1", campaign_id="camp",
                        acceptance_status="accepted",
                    )
                ],
            )
        finally:
            conn.close()
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted"}])
        mini_fn = _RecordingGate()
        mini_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, force_rerun=["a:1b"],
            connect_gate_fn=connect_fn, mini_gate_fn=mini_fn,
        )
        self.assertEqual(len(connect_fn.calls), 1)
        self.assertEqual(result.steps[0].outcome, "accepted")

    def test_cooldown_sleep_invoked_between_models_but_not_after_last(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b", "b:1b")
        connect_fn = _RecordingGate()
        mini_fn = _RecordingGate()
        for tag in ("a:1b", "b:1b"):
            connect_fn.script(tag, [{"status": "success", "acceptance_status": "accepted"}])
            mini_fn.script(tag, [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        sleep_calls: list = []
        serial_campaign.run_serial_campaign(
            root, plan, confirm=True, cooldown_seconds=2.5,
            connect_gate_fn=connect_fn, mini_gate_fn=mini_fn, sleep=sleep_calls.append,
        )
        self.assertEqual(sleep_calls, [2.5])  # once, between a and b -- never after the last model

    def test_pre_gate_checkpoint_is_written_before_connect_gate_call_starts(self) -> None:
        """A checkpoint must exist on disk with 'in_progress' populated
        *before* the connect gate function is even invoked -- so a hard
        crash/kill mid-attempt still leaves a resumable, inspectable
        'this model+gate was in flight' state, not just already-finished
        steps."""
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        checkpoint_path = orchestrator.campaign_output_dir(root, "camp") / serial_campaign.SERIAL_PROGRESS_FILENAME
        observed_in_progress: list = []

        def observing_connect_fn(repo_root, *, campaign_id, model, **kwargs):
            self.assertTrue(checkpoint_path.exists())
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            observed_in_progress.append(payload.get("in_progress"))
            sample = _sample(sample_id="s1", model=model, status="success", acceptance_status="accepted")
            return live_gates.LiveGateResult(
                sample=sample, output_dir=Path("out"), db_path=Path("out/db"),
                samples_csv=Path("out/s.csv"), aggregates_csv=Path("out/a.csv"),
                report_path=Path("out/report.html"), combined_report_path=None,
            )

        mini_fn = _RecordingGate()
        mini_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=observing_connect_fn, mini_gate_fn=mini_fn
        )
        self.assertEqual(observed_in_progress, [{"model": "a:1b", "gate": "connect"}])

    def test_pre_gate_checkpoint_is_written_before_mini_gate_call_starts(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b")
        checkpoint_path = orchestrator.campaign_output_dir(root, "camp") / serial_campaign.SERIAL_PROGRESS_FILENAME
        connect_fn = _RecordingGate()
        connect_fn.script("a:1b", [{"status": "success", "acceptance_status": "accepted"}])
        observed_in_progress: list = []

        def observing_mini_fn(repo_root, *, campaign_id, model, **kwargs):
            self.assertTrue(checkpoint_path.exists())
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            observed_in_progress.append(payload.get("in_progress"))
            sample = _sample(
                sample_id="s2", model=model, status="success", acceptance_status="accepted",
                task_id="mini-coding-tests-v1",
            )
            return live_gates.LiveGateResult(
                sample=sample, output_dir=Path("out"), db_path=Path("out/db"),
                samples_csv=Path("out/s.csv"), aggregates_csv=Path("out/a.csv"),
                report_path=Path("out/report.html"), combined_report_path=None,
            )

        serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=observing_mini_fn
        )
        self.assertEqual(observed_in_progress, [{"model": "a:1b", "gate": "mini"}])

    def test_gate_boundary_exception_is_persisted_and_campaign_continues_by_default(self) -> None:
        """A gate-boundary exception (live_gates' own safety net -- see
        GATE_BOUNDARY_EXCEPTION_ERROR_CODE) never raises out of the gate
        function; by default (``halt_on_gate_exception=False``) the
        campaign must still move on to the next model, exactly like an
        ordinary scored rejection would."""
        root = self._fresh_root()
        plan = self._plan(root, "a:1b", "b:1b")
        connect_fn = _RecordingGate()
        connect_fn.script(
            "a:1b",
            [{
                "status": "error", "acceptance_status": "not_usable",
                "system_error_code": live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE,
            }],
        )
        connect_fn.script("b:1b", [{"status": "success", "acceptance_status": "accepted"}])
        mini_fn = _RecordingGate()
        mini_fn.script("b:1b", [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertFalse(result.halted_on_gate_exception)
        self.assertEqual(len(connect_fn.calls), 2)  # b:1b was still attempted
        self.assertEqual(result.steps[0].system_error_code, live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE)

    def test_halt_on_gate_exception_true_stops_campaign_early(self) -> None:
        root = self._fresh_root()
        plan = self._plan(root, "a:1b", "b:1b")
        connect_fn = _RecordingGate()
        connect_fn.script(
            "a:1b",
            [{
                "status": "error", "acceptance_status": "not_usable",
                "system_error_code": live_gates.GATE_BOUNDARY_EXCEPTION_ERROR_CODE,
            }],
        )
        mini_fn = _RecordingGate()
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, halt_on_gate_exception=True,
            connect_gate_fn=connect_fn, mini_gate_fn=mini_fn,
        )
        self.assertTrue(result.halted_on_gate_exception)
        self.assertEqual(len(connect_fn.calls), 1)  # "b:1b" never attempted
        checkpoint = json.loads(result.checkpoint_path.read_text(encoding="utf-8"))
        self.assertTrue(checkpoint["halted_on_gate_exception"])

    def test_resume_after_real_pilot_exact_state_reruns_only_missing_mini_gate(self) -> None:
        """Direct regression guard for the real
        agent-helper-serial-pilot-20260801 / qwen3-coder:30b incident: the
        campaign DB held an accepted connect-gate sample but no mini-gate
        sample at all (the mini gate crashed uncaught before persisting
        anything). Resuming with the same campaign/model list must
        recognize the existing connect-gate acceptance and attempt only
        the missing mini gate -- never re-run the already-accepted connect
        gate, and never silently skip the missing mini gate either."""
        root = self._fresh_root()
        campaign_id = "agent-helper-serial-pilot-20260801"
        model = "qwen3-coder:30b"
        discovery = _discovery_with_tags((model, 30.0, "dense", "q4", False))
        plan = serial_campaign.build_serial_plan(discovery, campaign_id, generated_at="t", models=[model])
        output_dir = orchestrator.campaign_output_dir(root, campaign_id)
        output_dir.mkdir(parents=True)
        db_path = output_dir / storage.DB_FILENAME
        conn = storage.connect(db_path)
        try:
            storage.insert_samples(
                conn,
                [
                    _sample(
                        sample_id="pilot-connect-accepted", model=model,
                        task_id="connect-smoke-v1", campaign_id=campaign_id,
                        acceptance_status="accepted",
                    )
                ],
            )
        finally:
            conn.close()
        connect_fn = _RecordingGate()  # no script -- must never be called again
        mini_fn = _RecordingGate()
        mini_fn.script(model, [{"status": "success", "acceptance_status": "accepted", "task_id": "mini-coding-tests-v1"}])
        result = serial_campaign.run_serial_campaign(
            root, plan, confirm=True, resume=True, connect_gate_fn=connect_fn, mini_gate_fn=mini_fn
        )
        self.assertEqual(connect_fn.calls, [])
        self.assertEqual(result.steps[0].outcome, "resumed")
        self.assertEqual(result.steps[0].gate, "connect")
        self.assertEqual(len(mini_fn.calls), 1)
        self.assertEqual(result.steps[1].gate, "mini")
        self.assertEqual(result.steps[1].outcome, "accepted")


class SerialCampaignLiveGatesWiringTests(unittest.TestCase):
    """One true end-to-end test through the real live_gates functions (with
    fake transports/monitor injected), confirming run_serial_campaign wires
    real connect_gate_fn/mini_gate_fn defaults correctly end to end."""

    def test_serial_campaign_through_real_live_gates_with_fake_transports(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        discovery = _discovery_with_tags(("qwen3-coder:30b", 1.0, "dense", "q4", False))
        plan = serial_campaign.build_serial_plan(
            discovery, "camp-e2e", generated_at="t", models=["qwen3-coder:30b"]
        )
        shared_kwargs = dict(
            json_transport=_fake_json_transport_no_models_loaded,
            monitor_factory=_FakeMonitor,
        )
        with mock.patch.object(
            live_gates.local_lock, "_assert_runtime_available", return_value=None
        ):
            result = serial_campaign.run_serial_campaign(
                root, plan, confirm=True,
                connect_gate_kwargs=dict(shared_kwargs, stream_transport=_fake_stream_transport_ok_response),
                mini_gate_kwargs=dict(shared_kwargs, stream_transport=_fake_stream_transport_reference_solution),
            )
        self.assertEqual([(s.gate, s.outcome) for s in result.steps], [("connect", "accepted"), ("mini", "accepted")])
        db_path = orchestrator.campaign_output_dir(root, "camp-e2e") / storage.DB_FILENAME
        conn = storage.connect(db_path)
        try:
            rows = storage.fetch_samples(conn, campaign_id="camp-e2e")
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)


class SerialCampaignCliWiringTests(unittest.TestCase):
    """Verifies the new ollama-inventory-snapshot/serial-plan/serial-execute
    argparse wiring. Discovery/gate functions are always mocked, and
    ``campaign_cli.ROOT`` is always patched to a throwaway temp directory
    for the duration of each test -- no real transport is ever reached and
    the real repository's benchmark_results/ tree is never touched."""

    def _tmp_root(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_serial_plan_parses_filters_and_models(self) -> None:
        args = campaign_cli.parse_args(
            [
                "serial-plan", "--campaign-id", "camp",
                "--models", "a:1b,b:1b",
                "--max-size-gb", "12",
                "--include-architecture", "dense,moe",
            ]
        )
        self.assertEqual(args.subcommand, "serial-plan")
        self.assertEqual(args.models, "a:1b,b:1b")
        self.assertEqual(args.max_size_gb, 12.0)

    def test_serial_execute_requires_confirm_and_selection_to_execute(self) -> None:
        args = campaign_cli.parse_args(["serial-execute", "--campaign-id", "camp"])
        self.assertFalse(args.confirm)
        with mock.patch.object(campaign_cli, "ROOT", self._tmp_root()), mock.patch.object(
            campaign_cli.ollama_inventory,
            "discover_ollama_inventory",
            return_value=ollama_inventory.DiscoveryResult(
                schema_version="v1", generated_at="t", base_url="http://x", models=[]
            ),
        ):
            rc = campaign_cli._cmd_serial_execute(args)
        self.assertEqual(rc, 0)  # dry plan only, no --confirm

    def test_serial_execute_refuses_confirm_without_explicit_selection(self) -> None:
        args = campaign_cli.parse_args(["serial-execute", "--campaign-id", "camp", "--confirm"])
        with mock.patch.object(campaign_cli, "ROOT", self._tmp_root()), mock.patch.object(
            campaign_cli.ollama_inventory,
            "discover_ollama_inventory",
            return_value=ollama_inventory.DiscoveryResult(
                schema_version="v1", generated_at="t", base_url="http://x", models=[]
            ),
        ):
            rc = campaign_cli._cmd_serial_execute(args)
        self.assertEqual(rc, 2)

    def test_serial_execute_confirm_with_explicit_models_calls_run_serial_campaign(self) -> None:
        args = campaign_cli.parse_args(
            ["serial-execute", "--campaign-id", "camp", "--confirm", "--models", "a:1b"]
        )
        fake_discovery = ollama_inventory.DiscoveryResult(
            schema_version="v1", generated_at="t", base_url="http://x",
            models=[
                ollama_inventory.DiscoveredOllamaModel(
                    tag="a:1b", model_id="a:1b", digest="d", size_bytes=1, modified_at="t"
                )
            ],
        )
        fake_result = serial_campaign.SerialCampaignResult(
            campaign_id="camp", executed=True, steps=[]
        )
        with mock.patch.object(campaign_cli, "ROOT", self._tmp_root()), mock.patch.object(
            campaign_cli.ollama_inventory, "discover_ollama_inventory", return_value=fake_discovery
        ), mock.patch.object(
            campaign_cli.serial_campaign, "run_serial_campaign", return_value=fake_result
        ) as mocked_run:
            rc = campaign_cli._cmd_serial_execute(args)
        self.assertEqual(rc, 0)
        mocked_run.assert_called_once()
        self.assertTrue(mocked_run.call_args.kwargs["confirm"])

    def test_ollama_inventory_snapshot_cli_calls_discover_and_save(self) -> None:
        args = campaign_cli.parse_args(["ollama-inventory-snapshot", "--campaign-id", "camp-inv"])
        fake_discovery = ollama_inventory.DiscoveryResult(
            schema_version="v1", generated_at="t", base_url="http://x", models=[]
        )
        with mock.patch.object(campaign_cli, "ROOT", self._tmp_root()), mock.patch.object(
            campaign_cli.ollama_inventory, "discover_ollama_inventory", return_value=fake_discovery
        ) as mocked_discover:
            rc = campaign_cli._cmd_ollama_inventory_snapshot(args)
        self.assertEqual(rc, 0)
        mocked_discover.assert_called_once()


class ForkBuildTests(unittest.TestCase):
    """Deterministic tests for fork_build module.
    Uses ``tempfile.TemporaryDirectory`` for filesystem isolation and
    mocks for ``subprocess.run`` and ``shutil.which`` — never invokes a
    real compiler or CMake."""

    def _source_root(self, markers: list[str | Path] | None = None) -> Path:
        """Create a minimal fake llama.cpp source checkout in a temp dir."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for marker in markers or ["CMakeLists.txt", "cmake", "ggml"]:
            p = root / marker
            p.mkdir(parents=True) if "/" in marker or "\\" in marker else p.touch()
            if "/" in marker or "\\" in marker:
                p.mkdir(parents=True, exist_ok=True)
        return root

    # -- is_source_tree --------------------------------------------------

    def test_is_source_tree_true(self) -> None:
        root = self._source_root()
        self.assertTrue(fork_build.is_source_tree(root))

    def test_is_source_tree_false_missing_cmake_dir(self) -> None:
        root = self._source_root(["CMakeLists.txt", "ggml"])
        self.assertFalse(fork_build.is_source_tree(root))

    def test_is_source_tree_false_not_a_dir(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertFalse(fork_build.is_source_tree(Path(tmp.name) / "nope"))

    # -- find_binary -----------------------------------------------------

    def test_find_binary_existing(self) -> None:
        root = self._source_root()
        bin_path = root / "build" / "bin" / "llama-server"
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.touch()
        self.assertEqual(fork_build.find_binary(root), bin_path)

    def test_find_binary_existing_exe(self) -> None:
        root = self._source_root()
        bin_path = root / "build" / "bin" / "llama-server.exe"
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.touch()
        self.assertEqual(fork_build.find_binary(root), bin_path)

    def test_find_binary_missing(self) -> None:
        root = self._source_root()
        self.assertIsNone(fork_build.find_binary(root))

    def test_find_binary_legacy_bin(self) -> None:
        root = self._source_root()
        bin_path = root / "bin" / "llama-server.exe"
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.touch()
        self.assertEqual(fork_build.find_binary(root), bin_path)

    # -- build: missing toolchain -> install.md --------------------------

    def test_build_missing_toolchain(self) -> None:
        root = self._source_root()
        with mock.patch.object(fork_build.shutil, "which", return_value=None):
            result = fork_build.build(root)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.install_md_path)
        self.assertTrue(result.install_md_path.exists())
        content = result.install_md_path.read_text(encoding="utf-8")
        self.assertIn("Build Instructions", content)
        self.assertIn("llama-server", content)

    def test_build_not_a_source_tree(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        with mock.patch.object(fork_build.shutil, "which", return_value=None):
            result = fork_build.build(root)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.install_md_path)
        self.assertFalse(result.success)

    def test_build_existing_binary_skips_build(self) -> None:
        root = self._source_root()
        bin_path = root / "bin" / "llama-server"
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.touch()
        # build() checks toolchain before existing binary; mock them present
        with mock.patch.object(fork_build.shutil, "which", side_effect=["/fake/cmake", "/fake/gcc"]):
            result = fork_build.build(root)
        self.assertTrue(result.success)
        self.assertEqual(result.binary_path, bin_path)

    def test_build_failure_emits_install_md(self) -> None:
        root = self._source_root()
        fake_cmake = Path("/fake/cmake")
        fake_cl = Path("/fake/cl")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="CMake Error: boom")

        with mock.patch.object(fork_build.shutil, "which", side_effect=[str(fake_cmake), str(fake_cl)]), \
             mock.patch.object(fork_build.subprocess, "run", side_effect=fake_run):
            result = fork_build.build(root)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.install_md_path)
        content = result.install_md_path.read_text(encoding="utf-8")
        self.assertIn("boom", content)

    # -- write_install_md ------------------------------------------------

    def test_write_install_md_creates_dirs_and_file(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        proj = Path(tmp.name) / "proj"
        proj.mkdir()
        md_path = fork_build.write_install_md(src, "sample error", project_root=proj)
        self.assertEqual(md_path, proj / "docs" / "operations" / "install.md")
        self.assertTrue(md_path.exists())
        content = md_path.read_text(encoding="utf-8")
        self.assertIn("sample error", content)
        self.assertIn(str(src), content)

    def test_write_install_md_truncates_very_long_errors(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "src"
        src.mkdir()
        long_error = "x" * 5000
        md_path = fork_build.write_install_md(src, long_error)
        content = md_path.read_text(encoding="utf-8")
        self.assertIn("(truncated for brevity)", content)

    # -- can_rebuild -----------------------------------------------------

    def test_can_rebuild_true(self) -> None:
        root = self._source_root()
        bin_path = root / "build" / "bin" / "llama-server"
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.touch()
        self.assertTrue(fork_build.can_rebuild(bin_path))

    def test_can_rebuild_false_no_source_ancestors(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake_bin = Path(tmp.name) / "somewhere" / "llama-server.exe"
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.touch()
        self.assertFalse(fork_build.can_rebuild(fake_bin))


if __name__ == "__main__":
    unittest.main()
