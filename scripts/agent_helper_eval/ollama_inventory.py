"""Local Ollama model inventory discovery -- list/show only, never generate.

This module talks to a local Ollama server's REST API purely to *discover*
what is already installed:

- ``GET  /api/tags``        -- the HTTP equivalent of ``ollama list``.
- ``POST /api/show``        -- the HTTP equivalent of ``ollama show <tag>``.

Neither endpoint loads a model into VRAM or generates a token -- unlike
``POST /api/generate`` (see :mod:`agent_helper_eval.ollama_client`), which is
the only call in this whole package that actually runs a model. Discovery is
therefore safe to run at any time, including while iterating a serial
campaign plan, without violating the "max one local model" rule.

Every field on :class:`DiscoveredOllamaModel` is either copied verbatim from
Ollama's own JSON response or derived through a small, explicitly documented
and narrowly-scoped heuristic (see ``_classify_architecture``/
``_classify_cloud``) -- nothing is invented. Where Ollama's response does not
contain enough evidence to classify a field, it is left ``None``/``"unknown"``
rather than guessed.

A discovery snapshot is a plain, versioned JSON document
(:data:`INVENTORY_SNAPSHOT_SCHEMA_VERSION`) written into one campaign's own
output directory (see :func:`save_inventory_snapshot`) -- it is a generated
artifact, never a substitute for (and never overwrites) the hand-curated
``benchmarks/agent-helper-model-inventory.example.json`` inventory file.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Optional, Sequence

from . import ollama_client
from .model_inventory import FeasibilityProjection, ModelSpec
from .schema import utc_now_iso

INVENTORY_SNAPSHOT_SCHEMA_VERSION = "agent-helper-ollama-inventory-v1"

#: Written under a campaign's own output directory
#: (``benchmark_results/agent-helper/<campaign_id>/``) -- never the shared
#: hand-curated example inventory under ``benchmarks/``.
INVENTORY_SNAPSHOT_FILENAME = "ollama_inventory_snapshot.json"

# Ollama's own cloud-model naming convention embeds "cloud" directly in the
# tag it reports (for example "qwen3-coder:480b-cloud",
# "deepseek-v3.1:671b-cloud") -- this pattern is read from the tag string
# Ollama itself returns, never guessed from anything else.
_CLOUD_TAG_RE = re.compile(r"(?:^|[:\-])cloud(?:$|[:\-])", re.IGNORECASE)

# Architecture-classification hints: substrings of a family/architecture
# name (as reported by Ollama's own ``/api/show`` ``details``/``model_info``)
# that are real, documented naming evidence for a Mixture-of-Experts or
# dense transformer design. Order matters: MoE hints are checked first,
# since some family strings contain both (for example "qwen3moe" contains
# "qwen3").
_MOE_ARCHITECTURE_HINTS = ("moe", "mixtral")
_DENSE_ARCHITECTURE_HINTS = (
    "llama", "gemma", "phi", "qwen2", "qwen3", "mistral", "stablelm",
    "starcoder", "command-r", "granite",
)

_PARAMETER_SIZE_RE = re.compile(r"^\s*([\d.]+)\s*([BMK])\s*$", re.IGNORECASE)
_PARAMETER_SIZE_MULTIPLIERS = {"B": 1.0, "M": 1.0 / 1_000.0, "K": 1.0 / 1_000_000.0}


class OllamaInventoryError(RuntimeError):
    """Raised when discovery itself cannot proceed at all (for example the
    Ollama daemon is unreachable for ``GET /api/tags``)."""


@dataclasses.dataclass
class DiscoveredOllamaModel:
    """One installed Ollama tag, as discovered via ``/api/tags`` + ``/api/show``.

    ``None``/``"unknown"`` always means "no evidence available" -- never a
    fabricated default.
    """

    tag: str
    model_id: Optional[str]
    digest: Optional[str]
    size_bytes: Optional[int]
    modified_at: Optional[str]
    architecture: str = "unknown"
    architecture_evidence: Optional[str] = None
    parameters_billion: Optional[float] = None
    parameters_source: Optional[str] = None
    quantization: Optional[str] = None
    context_length: Optional[int] = None
    capabilities: tuple = ()
    family: Optional[str] = None
    families: tuple = ()
    is_cloud: bool = False
    cloud_evidence: Optional[str] = None
    #: Set when ``/api/show`` failed for this one tag -- the tag is still
    #: included (using only the ``/api/tags``-level facts), never silently
    #: dropped from the snapshot.
    show_error: Optional[str] = None

    def to_json(self) -> dict:
        data = dataclasses.asdict(self)
        data["capabilities"] = list(self.capabilities)
        data["families"] = list(self.families)
        return data

    @classmethod
    def from_json(cls, data: dict) -> "DiscoveredOllamaModel":
        payload = dict(data)
        payload["capabilities"] = tuple(payload.get("capabilities") or ())
        payload["families"] = tuple(payload.get("families") or ())
        return cls(**payload)


@dataclasses.dataclass
class DiscoveryResult:
    schema_version: str
    generated_at: str
    base_url: str
    models: list  # list[DiscoveredOllamaModel]
    #: Set only if the whole ``GET /api/tags`` call failed -- ``models`` is
    #: then always empty (nothing could be discovered at all), never a
    #: fabricated partial list.
    tags_error: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "base_url": self.base_url,
            "tags_error": self.tags_error,
            "models": [model.to_json() for model in self.models],
        }

    @classmethod
    def from_json(cls, data: dict) -> "DiscoveryResult":
        return cls(
            schema_version=data["schema_version"],
            generated_at=data["generated_at"],
            base_url=data["base_url"],
            tags_error=data.get("tags_error"),
            models=[DiscoveredOllamaModel.from_json(m) for m in data.get("models", [])],
        )


def _classify_cloud(tag: str) -> tuple[bool, Optional[str]]:
    if _CLOUD_TAG_RE.search(tag):
        return True, f"tag {tag!r} matches the Ollama cloud-model tag naming convention"
    return False, None


def _classify_architecture(
    model_info: dict, family: Optional[str], families: Sequence[str]
) -> tuple[str, Optional[str]]:
    # 1. Strongest possible evidence: an explicit expert-count field in
    #    Ollama's own model_info (for example "qwen3moe.expert_count": 128).
    for key, value in (model_info or {}).items():
        if key.endswith("expert_count"):
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count > 1:
                return "moe", f"model_info[{key!r}]={count} (>1 experts)"
    # 2. Family/architecture name hints (MoE checked before dense, since a
    #    MoE variant's family string is often a superset of the dense one's,
    #    e.g. "qwen3moe" contains "qwen3").
    haystacks = [value.lower() for value in ([family] if family else []) + list(families) if value]
    for hint in _MOE_ARCHITECTURE_HINTS:
        if any(hint in haystack for haystack in haystacks):
            return "moe", f"family/architecture name contains {hint!r}"
    for hint in _DENSE_ARCHITECTURE_HINTS:
        if any(hint in haystack for haystack in haystacks):
            return "dense", f"family/architecture name contains {hint!r}"
    return "unknown", None


def _extract_parameters_billion(
    model_info: dict, details_parameter_size: Optional[str]
) -> tuple[Optional[float], Optional[str]]:
    for key, value in (model_info or {}).items():
        if key == "general.parameter_count":
            try:
                return round(float(value) / 1_000_000_000.0, 3), "model_info['general.parameter_count']"
            except (TypeError, ValueError):
                pass
    if details_parameter_size:
        match = _PARAMETER_SIZE_RE.match(details_parameter_size)
        if match:
            number = float(match.group(1))
            multiplier = _PARAMETER_SIZE_MULTIPLIERS[match.group(2).upper()]
            return round(number * multiplier, 3), f"details.parameter_size ({details_parameter_size!r})"
    return None, None


def _extract_context_length(model_info: dict) -> Optional[int]:
    for key, value in (model_info or {}).items():
        if key.endswith(".context_length"):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def discover_ollama_inventory(
    base_url: str,
    json_transport: ollama_client.JsonTransport,
    *,
    tags_timeout_seconds: float = 10.0,
    show_timeout_seconds: float = 10.0,
) -> DiscoveryResult:
    """Discover every installed Ollama tag via ``GET /api/tags`` + ``POST
    /api/show`` only. Never calls ``/api/generate``; never loads or unloads
    a model.

    Best-effort per tag: if ``/api/show`` fails for one specific tag, that
    tag is still included in the result (using only the ``/api/tags``-level
    facts, with ``show_error`` set) -- never silently dropped. Only a
    complete ``/api/tags`` failure yields an empty ``models`` list (with
    ``tags_error`` set).
    """

    generated_at = utc_now_iso()
    try:
        tags_response = json_transport(
            "GET", f"{base_url.rstrip('/')}/api/tags", None, tags_timeout_seconds
        )
    except ollama_client.OllamaError as exc:
        return DiscoveryResult(
            schema_version=INVENTORY_SNAPSHOT_SCHEMA_VERSION,
            generated_at=generated_at,
            base_url=base_url,
            models=[],
            tags_error=str(exc),
        )
    if tags_response.status_code != 200 or tags_response.json_body is None:
        return DiscoveryResult(
            schema_version=INVENTORY_SNAPSHOT_SCHEMA_VERSION,
            generated_at=generated_at,
            base_url=base_url,
            models=[],
            tags_error=f"GET /api/tags failed: HTTP {tags_response.status_code}: {tags_response.text_body[:300]!r}",
        )

    models: list[DiscoveredOllamaModel] = []
    for entry in tags_response.json_body.get("models", []):
        tag = entry.get("name") or entry.get("model") or ""
        details = entry.get("details") or {}
        family = details.get("family")
        families = tuple(details.get("families") or ())
        model_info: dict = {}
        capabilities: tuple = ()
        show_error: Optional[str] = None

        try:
            show_response = json_transport(
                "POST", f"{base_url.rstrip('/')}/api/show", {"model": tag}, show_timeout_seconds
            )
            if show_response.status_code == 200 and show_response.json_body is not None:
                body = show_response.json_body
                model_info = body.get("model_info") or {}
                capabilities = tuple(body.get("capabilities") or ())
                show_details = body.get("details") or {}
                family = show_details.get("family", family)
                families = tuple(show_details.get("families") or families)
            else:
                show_error = (
                    f"POST /api/show failed for {tag!r}: HTTP {show_response.status_code}"
                )
        except ollama_client.OllamaError as exc:
            show_error = f"POST /api/show failed for {tag!r}: {exc}"

        architecture, architecture_evidence = _classify_architecture(model_info, family, families)
        parameters_billion, parameters_source = _extract_parameters_billion(
            model_info, details.get("parameter_size")
        )
        context_length = _extract_context_length(model_info)
        is_cloud, cloud_evidence = _classify_cloud(tag)

        models.append(
            DiscoveredOllamaModel(
                tag=tag,
                model_id=entry.get("model") or tag,
                digest=entry.get("digest"),
                size_bytes=entry.get("size"),
                modified_at=entry.get("modified_at"),
                architecture=architecture,
                architecture_evidence=architecture_evidence,
                parameters_billion=parameters_billion,
                parameters_source=parameters_source,
                quantization=details.get("quantization_level"),
                context_length=context_length,
                capabilities=capabilities,
                family=family,
                families=families,
                is_cloud=is_cloud,
                cloud_evidence=cloud_evidence,
                show_error=show_error,
            )
        )

    return DiscoveryResult(
        schema_version=INVENTORY_SNAPSHOT_SCHEMA_VERSION,
        generated_at=generated_at,
        base_url=base_url,
        models=models,
    )


def save_inventory_snapshot(result: DiscoveryResult, campaign_output_dir: Path) -> Path:
    """Persist a discovery snapshot into ``campaign_output_dir`` (a
    campaign's own isolated output directory). Never writes to or overwrites
    the shared hand-curated ``benchmarks/agent-helper-model-inventory.example.json``.
    """

    campaign_output_dir.mkdir(parents=True, exist_ok=True)
    path = campaign_output_dir / INVENTORY_SNAPSHOT_FILENAME
    path.write_text(
        json.dumps(result.to_json(), indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_inventory_snapshot(path: Path) -> DiscoveryResult:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return DiscoveryResult.from_json(data)


def estimate_vram_fit(size_gb_on_disk: float, budget_gb: float) -> FeasibilityProjection:
    """A static, on-disk-size-only VRAM-fit estimate -- never a runtime
    measurement, so ``confidence`` is always ``"estimated"``, never
    ``"measured"`` (see ``model_inventory.validate_feasibility``).

    Rule of thumb: an on-disk (already-quantized) GGUF/Ollama blob size is a
    reasonable lower bound for the VRAM footprint; real usage adds roughly
    10-20% more for KV-cache/context/runtime overhead. This is documented in
    ``assumptions`` precisely so nobody mistakes it for a load test.
    """

    estimated_needed_gb = round(size_gb_on_disk * 1.2, 1)
    return FeasibilityProjection(
        fits=estimated_needed_gb <= budget_gb,
        confidence="estimated",
        assumptions=(
            f"on-disk size {size_gb_on_disk:.1f} GB x 1.2 (KV-cache/context/"
            f"runtime overhead rule-of-thumb) = ~{estimated_needed_gb:.1f} GB "
            f"vs {budget_gb:.0f} GB budget; no actual load was performed"
        ),
        notes="static file-size estimate only, not a load test",
    )


def to_model_spec(model: DiscoveredOllamaModel) -> ModelSpec:
    """Best-effort conversion of one discovered tag into a
    :class:`~agent_helper_eval.model_inventory.ModelSpec`, purely for reuse
    in report/feasibility display -- never for anything requiring a
    'measured' claim about hardware that was not actually tested."""

    size_gb = model.size_bytes / 1_000_000_000.0 if model.size_bytes is not None else None
    notes_parts: list[str] = []
    if model.is_cloud:
        notes_parts.append(f"cloud tag ({model.cloud_evidence})")
    if model.capabilities:
        notes_parts.append(f"capabilities={list(model.capabilities)}")
    if model.show_error:
        notes_parts.append(f"api/show error: {model.show_error}")
    return ModelSpec(
        provider="local",
        backend="ollama",
        model_id=model.tag,
        display_name=model.tag,
        architecture=model.architecture,
        params_billion=model.parameters_billion,
        quantization=model.quantization,
        context_length=model.context_length,
        size_gb_on_disk=size_gb,
        fits_12gb_vram=estimate_vram_fit(size_gb, 12.0) if size_gb is not None else None,
        fits_rtx5090_32gb_projection=estimate_vram_fit(size_gb, 32.0) if size_gb is not None else None,
        notes="; ".join(notes_parts) or None,
    )
