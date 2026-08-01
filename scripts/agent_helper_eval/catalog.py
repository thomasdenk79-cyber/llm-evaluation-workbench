"""Benchmark catalog/spec for the agent-helper evaluation track.

This module is data and validation only -- it never executes a model, opens
a network connection, or starts Ollama/llama.cpp/a Copilot session. Model
campaigns that actually run these tasks are implemented and run later,
strictly serially, by the parent agent.

Each :class:`TaskSpec` documents one staged benchmark gate. ``tracks``
explicitly distinguishes the **pure-model** track (a single prompt/response
exchange, no tool access) from the **tool-agent** track (a delegated helper
task executed through an agentic tool such as OpenCode or the GitHub Copilot
CLI/agent, with real file/tool actions). Not every task necessarily supports
both tracks meaningfully (for example a pure connectivity smoke check is the
same shape in both tracks, while a repository-aware bug-review task is
mostly meaningful in the tool-agent track), so ``tracks`` is explicit per
task rather than assumed.

Multi-agent workspace / external catalog contribution
-------------------------------------------------------
This workspace is shared with at least one other concurrently-running
agent/session (working on a separate "Command Center" tool, cloud/tool
evaluations, and reports) that may author *additional* benchmark-set files
of its own and hand them to this harness. That other session never runs a
local Ollama/llama.cpp campaign -- that responsibility stays exclusively
with the agent-helper-eval subarea in this repo (see the coordination
section in ``docs/project/agent_helper_benchmark.md`` for the full
ownership note).

To make this safe without any code change here, a catalog JSON file is
accepted by :func:`load_catalog`/:func:`load_catalog_document` if, and only
if, it matches this documented, versioned contract:

1. Top-level ``catalog_schema_version`` must equal one of
   :data:`SUPPORTED_CATALOG_SCHEMA_VERSIONS` (today, only
   ``"agent-helper-catalog-v1"``). A missing or unrecognised value is
   rejected with a clear error rather than guessed at -- this is also what
   protects this loader from being pointed, by mistake, at a file that
   actually belongs to a different benchmark format (for example the
   legacy ``llm_migration_benchmark.py`` ``benchmark_id``/``spec_version``/
   ``required_keywords`` shape used by files such as
   ``benchmarks/swe-mixed-hard-24.json``).
2. Top-level ``catalog_metadata`` object is **mandatory** and must supply
   non-empty ``catalog_id``, ``author``, and ``created_at`` (ISO-8601 with
   timezone) fields -- see :class:`CatalogMetadata`. This is the catalog-
   level provenance that lets a multi-agent workspace always trace which
   session/author contributed a given benchmark-set file; it is distinct
   from the per-sample ``SampleRecord.provenance`` field in
   :mod:`agent_helper_eval.schema`.
3. ``tasks`` is a list of objects whose keys match :class:`TaskSpec`'s field
   names exactly (validated the same way for every catalog, bundled or
   external).

New benchmark sets must always be added as a **new, separate** catalog file
under ``benchmarks/`` (never as an edit to an existing/shared catalog file
without an explicit handover note in ``AGENTS.md``/the changelog -- see the
coordination section in ``docs/project/agent_helper_benchmark.md``). See
``benchmarks/agent-helper-catalog.example-external.json`` for a minimal,
concrete, independently-loadable example of an externally authored catalog.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional

from .schema import is_timestamp_with_tz, TRACKS

CATALOG_SCHEMA_VERSION = "agent-helper-catalog-v1"

#: Schema versions :func:`load_catalog_document` currently accepts. A future,
#: backward-compatible ``agent-helper-catalog-v2`` would be added here, not
#: by loosening the version check -- a mismatched/missing version must
#: always fail loudly rather than being silently coerced.
SUPPORTED_CATALOG_SCHEMA_VERSIONS = (CATALOG_SCHEMA_VERSION,)

#: Gate types, evaluated in this conceptual order for a given task (see
#: ``docs/project/agent_helper_benchmark.md`` methodology section and
#: :mod:`agent_helper_eval.rubric` for how gate outcomes feed scoring).
GATE_TYPES = ("smoke", "deterministic_tests", "reviewer", "collaboration")

#: Outcome states a staged gate can reach without ambiguity. "skip" is used
#: when a gate does not apply to a given track/model (for example a
#: deterministic_tests gate skipped for an architecture/planning task that
#: has no objective oracle); "timeout" is a distinct outcome from "fail" so
#: reports and rubric can treat hangs differently from wrong-but-completed
#: answers.
GATE_OUTCOMES = ("pass", "fail", "skip", "timeout")


class CatalogValidationError(ValueError):
    """Raised when a benchmark catalog fails validation."""


@dataclasses.dataclass
class CatalogMetadata:
    """Mandatory catalog-file-level provenance.

    Distinct from the per-sample ``SampleRecord.provenance`` field in
    :mod:`agent_helper_eval.schema` (which records how one *result* row came
    to exist -- measured/historical_import/projected/synthetic_dry_run).
    ``CatalogMetadata`` instead records who authored the *benchmark
    definition itself*, so a multi-agent workspace can always trace which
    session/author contributed a given catalog file -- required because
    another concurrently-running agent/session may hand this harness an
    externally authored benchmark-set file (see the module docstring and
    ``docs/project/agent_helper_benchmark.md``).
    """

    catalog_id: str
    author: str
    created_at: str
    source_notes: Optional[str] = None

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "CatalogMetadata":
        return cls(**data)


def validate_catalog_metadata(metadata: CatalogMetadata) -> list[str]:
    errors: list[str] = []
    if not metadata.catalog_id:
        errors.append("catalog_metadata.catalog_id must be non-empty")
    if not metadata.author:
        errors.append(
            "catalog_metadata.author must be non-empty (mandatory for "
            "multi-agent provenance/traceability)"
        )
    if not metadata.created_at:
        errors.append("catalog_metadata.created_at must be non-empty (ISO-8601 with timezone)")
    elif not is_timestamp_with_tz(metadata.created_at):
        errors.append(
            "catalog_metadata.created_at must be an ISO-8601 timestamp with an "
            f"explicit timezone offset, got {metadata.created_at!r}"
        )
    return errors


@dataclasses.dataclass
class CatalogDocument:
    """A fully loaded and validated catalog file: version, metadata, tasks."""

    catalog_schema_version: str
    metadata: CatalogMetadata
    tasks: list["TaskSpec"]


@dataclasses.dataclass
class TaskSpec:
    """One staged benchmark task definition (data only, never executed here)."""

    task_id: str
    name: str
    category: str
    tracks: tuple[str, ...]
    gates: tuple[str, ...]
    description: str
    deterministic_check_description: Optional[str] = None
    reviewer_rubric_hint: Optional[str] = None
    max_turns: int = 1
    timeout_seconds: int = 300
    requires_local_lock: bool = True

    def to_json(self) -> dict:
        data = dataclasses.asdict(self)
        data["tracks"] = list(self.tracks)
        data["gates"] = list(self.gates)
        return data

    @classmethod
    def from_json(cls, data: dict) -> "TaskSpec":
        payload = dict(data)
        payload["tracks"] = tuple(payload.get("tracks", ()))
        payload["gates"] = tuple(payload.get("gates", ()))
        return cls(**payload)


def validate_task(task: TaskSpec) -> list[str]:
    errors: list[str] = []
    if not task.task_id:
        errors.append("task_id must be non-empty")
    if not task.name:
        errors.append("name must be non-empty")
    if not task.category:
        errors.append("category must be non-empty")
    if not task.tracks:
        errors.append(f"{task.task_id}: tracks must be non-empty")
    for track in task.tracks:
        if track not in TRACKS:
            errors.append(f"{task.task_id}: unknown track {track!r}, expected one of {TRACKS}")
    if not task.gates:
        errors.append(f"{task.task_id}: gates must be non-empty")
    for gate in task.gates:
        if gate not in GATE_TYPES:
            errors.append(f"{task.task_id}: unknown gate {gate!r}, expected one of {GATE_TYPES}")
    if task.max_turns < 1:
        errors.append(f"{task.task_id}: max_turns must be >= 1")
    if task.timeout_seconds <= 0:
        errors.append(f"{task.task_id}: timeout_seconds must be > 0")
    return errors


def validate_catalog(tasks: list[TaskSpec]) -> list[str]:
    """Validate an entire catalog: per-task rules plus unique task IDs."""

    errors: list[str] = []
    seen_ids: set[str] = set()
    for task in tasks:
        errors.extend(validate_task(task))
        if task.task_id in seen_ids:
            errors.append(f"duplicate task_id: {task.task_id!r}")
        seen_ids.add(task.task_id)
    return errors


def load_catalog(path: Path) -> list[TaskSpec]:
    """Load and validate a catalog JSON file. Raises on validation failure.

    Convenience wrapper around :func:`load_catalog_document` for the common
    case where only the task list is needed (every existing call site).
    Discards ``catalog_metadata`` -- use :func:`load_catalog_document`
    directly when that catalog-level provenance is needed (for example to
    label generated samples with the correct ``benchmark_set``/author, or to
    print it for a human/parent-agent reviewing an externally supplied
    catalog before a campaign).
    """

    return load_catalog_document(path).tasks


def load_catalog_document(path: Path) -> CatalogDocument:
    """Load, version-check, and fully validate a catalog JSON file.

    This is the single entry point an externally authored benchmark-set
    file (for example one contributed by the concurrently-running Command
    Center session) must be loadable through with **no code change in this
    module**, provided the file matches the documented contract described in
    this module's docstring: a recognised ``catalog_schema_version``, a
    ``catalog_metadata`` object with non-empty ``catalog_id``/``author``/
    ``created_at``, and a ``tasks`` list whose items match
    :class:`TaskSpec`'s field names.

    Any structural mismatch -- an unsupported/missing schema version,
    missing/invalid ``catalog_metadata``, or a task object with the wrong
    shape (for example a file that actually belongs to a different
    benchmark format, such as the legacy ``llm_migration_benchmark.py``
    ``benchmark_id``/``required_keywords`` shape) -- raises a single,
    clearly worded :class:`CatalogValidationError` instead of an opaque
    ``TypeError``/``KeyError``.
    """

    raw_text = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise CatalogValidationError(f"{path}: not valid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise CatalogValidationError(f"{path}: top-level JSON value must be an object")

    version = payload.get("catalog_schema_version")
    if version not in SUPPORTED_CATALOG_SCHEMA_VERSIONS:
        raise CatalogValidationError(
            f"{path}: unsupported or missing catalog_schema_version "
            f"{version!r}, expected one of {SUPPORTED_CATALOG_SCHEMA_VERSIONS} "
            "(this file may belong to a different benchmark format, e.g. the "
            "legacy migration_llm_bench 'benchmark_id'/'spec_version' shape "
            "-- it must not be loaded here)"
        )

    metadata_raw = payload.get("catalog_metadata")
    if not isinstance(metadata_raw, dict):
        raise CatalogValidationError(
            f"{path}: missing required top-level 'catalog_metadata' object "
            "(catalog_id/author/created_at are mandatory for every catalog "
            "file -- see the coordination/contribution section in "
            "docs/project/agent_helper_benchmark.md)"
        )
    try:
        metadata = CatalogMetadata.from_json(metadata_raw)
    except TypeError as exc:
        raise CatalogValidationError(f"{path}: malformed catalog_metadata ({exc})") from exc
    metadata_errors = validate_catalog_metadata(metadata)
    if metadata_errors:
        raise CatalogValidationError(f"{path}: " + "; ".join(metadata_errors))

    tasks_raw = payload.get("tasks")
    if not isinstance(tasks_raw, list):
        raise CatalogValidationError(f"{path}: 'tasks' must be a list")

    tasks: list[TaskSpec] = []
    for index, item in enumerate(tasks_raw):
        if not isinstance(item, dict):
            raise CatalogValidationError(f"{path}: tasks[{index}] must be an object")
        try:
            tasks.append(TaskSpec.from_json(item))
        except TypeError as exc:
            raise CatalogValidationError(
                f"{path}: tasks[{index}] does not match the TaskSpec schema "
                f"({exc}) -- this file may belong to a different benchmark "
                "format and must not be loaded here"
            ) from exc

    task_errors = validate_catalog(tasks)
    if task_errors:
        raise CatalogValidationError(f"{path}: " + "; ".join(task_errors))

    return CatalogDocument(catalog_schema_version=version, metadata=metadata, tasks=tasks)


def save_catalog(
    tasks: list[TaskSpec],
    path: Path,
    metadata: CatalogMetadata,
    version: str = CATALOG_SCHEMA_VERSION,
) -> None:
    """Serialize a catalog to JSON after validating it.

    ``metadata`` is mandatory (never defaulted/auto-generated) so every
    catalog file written by this function always carries catalog-level
    provenance -- see :class:`CatalogMetadata`.
    """

    errors = validate_catalog(tasks)
    errors.extend(validate_catalog_metadata(metadata))
    if errors:
        raise CatalogValidationError("; ".join(errors))
    payload = {
        "catalog_schema_version": version,
        "catalog_metadata": metadata.to_json(),
        "tasks": [task.to_json() for task in tasks],
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


#: The seven staged gates required by the agent-helper methodology. Both
#: tracks are listed where the task shape is meaningful in each; tool-only
#: tasks (repository-aware review/collaboration) still list "pure_model" so
#: a pure-model-only run can attempt them from a supplied fixture/diff, with
#: the tool-agent track expected to score materially higher there.
DEFAULT_CATALOG: list[TaskSpec] = [
    TaskSpec(
        task_id="connect-smoke-v1",
        name="Connect/format smoke check",
        category="connectivity",
        tracks=("pure_model", "tool_agent"),
        gates=("smoke",),
        description=(
            "Verify the backend/model is reachable and returns a well-formed "
            "response to a trivial prompt (for example 'reply with the single "
            "word OK'). No quality judgement, only connectivity + response "
            "format + basic token accounting."
        ),
        deterministic_check_description="Response is non-empty and matches the expected literal.",
        max_turns=1,
        timeout_seconds=60,
        requires_local_lock=True,
    ),
    TaskSpec(
        task_id="mini-coding-tests-v1",
        name="Mini coding task with deterministic tests",
        category="coding",
        tracks=("pure_model", "tool_agent"),
        gates=("deterministic_tests", "reviewer"),
        description=(
            "Implement a small, self-contained function/module against a "
            "written spec. Correctness is graded by running a fixed, "
            "pre-written unit test suite against the produced code."
        ),
        deterministic_check_description="All unit tests in the fixed pytest suite pass.",
        reviewer_rubric_hint="Code clarity/idiomaticity only matters once tests pass.",
        max_turns=1,
        timeout_seconds=300,
        requires_local_lock=True,
    ),
    TaskSpec(
        task_id="bug-review-v1",
        name="Bug-review task",
        category="review",
        tracks=("pure_model", "tool_agent"),
        gates=("reviewer",),
        description=(
            "Given a small diff or code excerpt containing one or more "
            "seeded bugs, identify and explain the bug(s) and propose a fix, "
            "without necessarily applying it."
        ),
        reviewer_rubric_hint=(
            "Award credit for correctly locating the seeded bug and its root "
            "cause; a plausible-sounding but wrong diagnosis scores low."
        ),
        max_turns=1,
        timeout_seconds=180,
        requires_local_lock=True,
    ),
    TaskSpec(
        task_id="sql-migration-v1",
        name="SQL/migration task",
        category="sql_migration",
        tracks=("pure_model", "tool_agent"),
        gates=("deterministic_tests", "reviewer"),
        description=(
            "Translate a small Oracle DDL/PLSQL fragment to PostgreSQL (or "
            "an equivalent migration fragment). Correctness is graded by "
            "executing the produced DDL/SQL against a disposable schema and "
            "diffing the result against an expected fixture."
        ),
        deterministic_check_description=(
            "Produced SQL executes without error and result rows/schema "
            "match the expected fixture."
        ),
        max_turns=1,
        timeout_seconds=300,
        requires_local_lock=True,
    ),
    TaskSpec(
        task_id="frontend-task-v1",
        name="Frontend task",
        category="frontend",
        tracks=("pure_model", "tool_agent"),
        gates=("deterministic_tests", "reviewer"),
        description=(
            "Implement or fix a small, self-contained HTML/CSS/JS component "
            "against a written spec (for example a sortable table or a "
            "responsive layout fragment)."
        ),
        deterministic_check_description=(
            "A fixed, headless-renderable check (for example a DOM/structure "
            "assertion script) passes against the produced markup."
        ),
        reviewer_rubric_hint="Accessibility and responsiveness are reviewer-graded, not deterministic.",
        max_turns=1,
        timeout_seconds=300,
        requires_local_lock=True,
    ),
    TaskSpec(
        task_id="architecture-planning-v1",
        name="Architecture/planning task",
        category="architecture",
        tracks=("pure_model", "tool_agent"),
        gates=("reviewer",),
        description=(
            "Produce a short architecture/migration plan for a described "
            "problem (no objective oracle exists for a 'correct' plan)."
        ),
        reviewer_rubric_hint=(
            "Score completeness, risk awareness, and internal consistency; "
            "there is no deterministic gate for this task category."
        ),
        max_turns=1,
        timeout_seconds=300,
        requires_local_lock=True,
    ),
    TaskSpec(
        task_id="multi-turn-collaboration-v1",
        name="Multi-turn correction/collaboration task",
        category="collaboration",
        tracks=("pure_model", "tool_agent"),
        gates=("deterministic_tests", "reviewer", "collaboration"),
        description=(
            "Start from a deliberately incomplete/incorrect first attempt at "
            "a coding task and issue up to N correction turns with targeted "
            "feedback. Measures how efficiently the model/agent converges to "
            "an accepted, test-passing result (see "
            "``rubric.time_to_accepted_result``)."
        ),
        deterministic_check_description="Final accepted attempt passes the fixed test suite.",
        reviewer_rubric_hint="Fewer, more targeted correction turns score higher at equal correctness.",
        max_turns=5,
        timeout_seconds=600,
        requires_local_lock=True,
    ),
]
