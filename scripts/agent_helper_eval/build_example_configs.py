"""Regenerate the example agent-helper catalog and model inventory JSON files.

Run this after changing :mod:`agent_helper_eval.catalog` or
:mod:`agent_helper_eval.model_inventory` so the checked-in example files in
``benchmarks/`` stay in sync with the dataclasses that define them. This
script never calls a model or a network API; it only serializes in-repo
Python data structures to JSON.

Usage::

    python scripts/agent_helper_eval/build_example_configs.py
"""

from __future__ import annotations

from pathlib import Path

from . import catalog
from .model_inventory import FeasibilityProjection, ModelSpec, load_inventory, save_inventory

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = REPO_ROOT / "benchmarks" / "agent-helper-catalog-v1.json"
INVENTORY_PATH = REPO_ROOT / "benchmarks" / "agent-helper-model-inventory.example.json"


def example_model_inventory() -> list[ModelSpec]:
    """A small, illustrative inventory covering all four supported backends.

    Values are realistic examples drawn from ``AGENTS.md`` and this repo's
    documented local recommendations, but this file ships with the repo as
    an *example*/template -- always verify installed tags/paths on the
    target machine before a real campaign (see the runbook).
    """

    return [
        ModelSpec(
            provider="local",
            backend="ollama",
            model_id="qwen3-coder:30b",
            display_name="Qwen3 Coder 30B (Ollama, MoE ~3.3B active)",
            architecture="moe",
            params_billion=30.0,
            active_params_billion=3.3,
            quantization="Q4_K_M",
            context_length=131072,
            size_gb_on_disk=18.5,
            fits_12gb_vram=FeasibilityProjection(
                fits=True,
                confidence="measured",
                assumptions=(
                    "Observed running with ngl=99 on the 12 GB RTX 3500 Ada "
                    "notebook GPU (see AGENTS.md local recommendations)."
                ),
            ),
            fits_rtx5090_32gb_projection=FeasibilityProjection(
                fits=True,
                confidence="projected",
                assumptions=(
                    "32 GB nominal VRAM is comfortably larger than the "
                    "~18.5 GB on-disk size; assumes similar quantization and "
                    "no other concurrent VRAM consumers. Not measured on that "
                    "hardware."
                ),
            ),
            notes="Example inventory entry; verify the tag with 'ollama list' before a real campaign.",
        ),
        ModelSpec(
            provider="local",
            backend="llama_cpp",
            model_id=(
                "gpt-oss:20b=C:\\Users\\z000g9hu\\llama.cpp\\models\\gpt-oss-20b-MXFP4.gguf"
            ),
            display_name="GPT-OSS 20B (llama.cpp, MXFP4)",
            architecture="dense",
            params_billion=20.0,
            quantization="MXFP4",
            context_length=131072,
            size_gb_on_disk=12.9,
            fits_12gb_vram=FeasibilityProjection(
                fits=True,
                confidence="measured",
                assumptions=(
                    "Measured on this workbench with ngl=99, Balanced power "
                    "plan (see AGENTS.md local recommendations)."
                ),
            ),
            fits_rtx5090_32gb_projection=FeasibilityProjection(
                fits=True,
                confidence="projected",
                assumptions=(
                    "Full offload plus a much larger context window would fit "
                    "well within 32 GB; not tested on that hardware."
                ),
            ),
            notes="Example inventory entry; path must match the local llama.cpp models directory.",
        ),
        ModelSpec(
            provider="siemens",
            backend="siemens_api",
            model_id="gpt-oss-120b",
            display_name="Siemens LLM API: gpt-oss-120b",
            architecture="unknown",
            fits_12gb_vram=None,
            fits_rtx5090_32gb_projection=None,
            notes=(
                "Cloud-hosted; local VRAM fit is not applicable. The auth "
                "token is read from a local token file at run time and must "
                "never be stored in this inventory."
            ),
        ),
        ModelSpec(
            provider="github",
            backend="copilot_agent",
            model_id="github-copilot-cli/default-agent",
            display_name="GitHub Copilot CLI agent reference (runtime-selected model)",
            architecture="unknown",
            fits_12gb_vram=None,
            fits_rtx5090_32gb_projection=None,
            notes=(
                "Tool-agent reference only. Raw tokens/sec, TTFT, and "
                "process-level resource metrics are not reliably exposed by "
                "this runtime; record as N/A rather than estimate. See "
                "rubric.COPILOT_METRICS_CAVEAT_EN / _DE."
            ),
        ),
    ]


def main() -> None:
    metadata = catalog.CatalogMetadata(
        catalog_id="agent-helper-catalog-v1",
        author="agent-helper-eval (bundled default catalog)",
        created_at="2026-08-01T00:00:00.000+00:00",
        source_notes=(
            "Regenerated by scripts/agent_helper_eval/build_example_configs.py "
            "from catalog.DEFAULT_CATALOG; not an externally authored file."
        ),
    )
    catalog.save_catalog(catalog.DEFAULT_CATALOG, CATALOG_PATH, metadata=metadata)
    catalog.load_catalog(CATALOG_PATH)  # re-validates from disk
    print(f"wrote {CATALOG_PATH}")

    inventory = example_model_inventory()
    save_inventory(inventory, INVENTORY_PATH)
    load_inventory(INVENTORY_PATH)  # re-validates from disk
    print(f"wrote {INVENTORY_PATH}")


if __name__ == "__main__":
    main()
