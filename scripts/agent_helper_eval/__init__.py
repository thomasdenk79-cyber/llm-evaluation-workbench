"""Agent-helper evaluation harness.

This package is a dedicated, additive subarea for benchmarking and reporting on
delegated "agent-helper" tasks across local Ollama/llama.cpp models, Siemens API
models, and GitHub Copilot agent references.

It is deliberately separate from ``scripts/llm_migration_benchmark.py`` (the
existing monolithic Oracle-to-PostgreSQL/translation benchmark runner) so that:

- the historical CSV/JSON/report schema of that runner stays untouched;
- this package can define its own canonical, versioned schema without any risk
  of silently changing existing columns or report semantics.

Scope of this package (see ``docs/project/agent_helper_benchmark.md``):

- ``schema``: canonical sample/aggregate record definitions and CSV columns.
- ``storage``: SQLite single source of truth (SSOT) plus deterministic CSV export.
- ``aggregate``: percentile/rate aggregation from sample rows to model-run rows.
- ``historical_adapter``: best-effort import of the legacy migration_llm_bench
  history CSV into the canonical schema, marking unavailable fields as ``N/A``.
- ``catalog``: benchmark catalog/spec (data only; this module never executes a
  model or calls a network API).
- ``model_inventory``: model/campaign configuration format (no secrets).
- ``rubric``: orchestrator routing/scoring specification.
- ``local_lock``: filesystem preflight lock enforcing max one local model.
- ``report``: self-contained, offline HTML report generator.
- ``orchestrator``: campaign skeleton with a no-model dry-run mode; the actual
  model campaigns are run later, strictly serially, by the parent agent.

Nothing in this package calls an LLM, a remote API, Ollama, or llama.cpp on
import. Network/model calls only happen when the parent agent explicitly runs
the campaign CLI (``scripts/run_agent_helper_campaign.py``) in a non-dry-run
mode.
"""

from __future__ import annotations

SCHEMA_VERSION = "agent-helper-v1"

__all__ = ["SCHEMA_VERSION"]
