"""Model inventory / campaign configuration format.

Describes installed local models (Ollama tags, llama.cpp GGUF paths),
Siemens API models, and GitHub Copilot agent references in one inventory
file so a campaign orchestrator can enumerate "what could be evaluated"
without embedding secrets or making any network call itself.

No secrets belong in an inventory file: no API keys, tokens, or bearer
values. :func:`scan_for_secrets` is a defensive lint that flags suspicious
keys/values so a reviewer (human or agent) catches an accidental leak before
it is committed; it is not a substitute for the Siemens token file
convention documented in ``AGENTS.md`` (read the first ``SIAK-`` line from a
local token file at call time, never store it in this repository).

Feasibility metadata (``fits_12gb_vram`` / ``fits_rtx5090_32gb_projection``)
is explicitly split into a measured/estimated ``confidence`` plus a required
``assumptions`` string. A hypothetical future GPU (RTX 5090, 32 GB) can never
be "measured" on the current 12 GB notebook -- only "estimated"/"projected"
-- and this is enforced by :func:`validate_model_spec`.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Optional

PROVIDERS = ("local", "siemens", "github")
BACKENDS = ("ollama", "llama_cpp", "siemens_api", "copilot_agent")
ARCHITECTURES = ("dense", "moe", "unknown")

#: Confidence levels for a feasibility projection. "measured" requires the
#: projection to be about hardware actually present and actually tested;
#: everything about not-yet-owned hardware must be "estimated" or
#: "projected".
CONFIDENCE_LEVELS = ("measured", "estimated", "projected", "unknown")
_NON_MEASURED_CONFIDENCE = {"estimated", "projected", "unknown"}

_SECRET_KEY_RE = re.compile(
    r'"(?:[^"]*)(api[_-]?key|token|secret|password|bearer|authorization)(?:[^"]*)"\s*:',
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(r"SIAK-[A-Za-z0-9]", re.IGNORECASE)


class ModelInventoryError(ValueError):
    """Raised when a model inventory file fails validation."""


@dataclasses.dataclass
class FeasibilityProjection:
    """A VRAM-fit projection for one target GPU/hardware profile."""

    fits: Optional[bool]
    confidence: str
    assumptions: str
    notes: Optional[str] = None

    def to_json(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_json(cls, data: Optional[dict]) -> Optional["FeasibilityProjection"]:
        if data is None:
            return None
        return cls(**data)


@dataclasses.dataclass
class ModelSpec:
    """One evaluable model/agent reference in the campaign inventory."""

    provider: str
    backend: str
    model_id: str
    display_name: str
    architecture: str = "unknown"
    params_billion: Optional[float] = None
    active_params_billion: Optional[float] = None
    quantization: Optional[str] = None
    context_length: Optional[int] = None
    size_gb_on_disk: Optional[float] = None
    fits_12gb_vram: Optional[FeasibilityProjection] = None
    fits_rtx5090_32gb_projection: Optional[FeasibilityProjection] = None
    notes: Optional[str] = None

    def to_json(self) -> dict:
        data = dataclasses.asdict(self)
        data["fits_12gb_vram"] = (
            self.fits_12gb_vram.to_json() if self.fits_12gb_vram else None
        )
        data["fits_rtx5090_32gb_projection"] = (
            self.fits_rtx5090_32gb_projection.to_json()
            if self.fits_rtx5090_32gb_projection
            else None
        )
        return data

    @classmethod
    def from_json(cls, data: dict) -> "ModelSpec":
        payload = dict(data)
        payload["fits_12gb_vram"] = FeasibilityProjection.from_json(
            payload.get("fits_12gb_vram")
        )
        payload["fits_rtx5090_32gb_projection"] = FeasibilityProjection.from_json(
            payload.get("fits_rtx5090_32gb_projection")
        )
        return cls(**payload)


def validate_feasibility(
    projection: Optional[FeasibilityProjection], field_name: str, allow_measured: bool
) -> list[str]:
    errors: list[str] = []
    if projection is None:
        return errors
    if projection.confidence not in CONFIDENCE_LEVELS:
        errors.append(
            f"{field_name}.confidence must be one of {CONFIDENCE_LEVELS}, "
            f"got {projection.confidence!r}"
        )
    if not allow_measured and projection.confidence == "measured":
        errors.append(
            f"{field_name}.confidence must not be 'measured' for hardware "
            "that has not actually been tested (use 'estimated' or 'projected')"
        )
    if not projection.assumptions:
        errors.append(f"{field_name}.assumptions must document the basis for this projection")
    return errors


def validate_model_spec(spec: ModelSpec) -> list[str]:
    errors: list[str] = []
    if spec.provider not in PROVIDERS:
        errors.append(f"provider must be one of {PROVIDERS}, got {spec.provider!r}")
    if spec.backend not in BACKENDS:
        errors.append(f"backend must be one of {BACKENDS}, got {spec.backend!r}")
    if not spec.model_id:
        errors.append("model_id must be non-empty")
    if not spec.display_name:
        errors.append("display_name must be non-empty")
    if spec.architecture not in ARCHITECTURES:
        errors.append(f"architecture must be one of {ARCHITECTURES}, got {spec.architecture!r}")
    # A 12 GB VRAM notebook GPU is real, currently-owned hardware, so a
    # measured fit claim is allowed there if the model was actually tried.
    errors.extend(validate_feasibility(spec.fits_12gb_vram, "fits_12gb_vram", allow_measured=True))
    # An RTX 5090 32 GB card is hypothetical/not-yet-owned hardware: never
    # allow a "measured" confidence for it.
    errors.extend(
        validate_feasibility(
            spec.fits_rtx5090_32gb_projection,
            "fits_rtx5090_32gb_projection",
            allow_measured=False,
        )
    )
    return errors


def validate_inventory(specs: list[ModelSpec]) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        errors.extend(f"{spec.model_id}: {e}" for e in validate_model_spec(spec))
        key = (spec.backend, spec.model_id)
        if key in seen:
            errors.append(f"duplicate (backend, model_id): {key!r}")
        seen.add(key)
    return errors


def scan_for_secrets(raw_text: str) -> list[str]:
    """Defensive lint: flag lines that look like they contain a secret.

    Looks for suspicious key names (``token``, ``api_key``, ``secret``,
    ``password``, ``bearer``, ``authorization``) and the Siemens token
    prefix (``SIAK-``). Returns human-readable findings; an empty list means
    no obvious secret pattern was found (not a cryptographic guarantee).
    """

    findings: list[str] = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if _SECRET_KEY_RE.search(line):
            findings.append(f"line {line_number}: suspicious key name ({line.strip()[:80]!r})")
        if _SECRET_VALUE_RE.search(line):
            findings.append(f"line {line_number}: looks like a Siemens API token literal")
    return findings


def load_inventory(path: Path) -> list[ModelSpec]:
    """Load, secret-scan, and validate a model inventory JSON file."""

    raw_text = Path(path).read_text(encoding="utf-8")
    secret_findings = scan_for_secrets(raw_text)
    if secret_findings:
        raise ModelInventoryError(
            "refusing to load inventory that looks like it contains secrets: "
            + "; ".join(secret_findings)
        )
    payload = json.loads(raw_text)
    specs = [ModelSpec.from_json(item) for item in payload.get("models", [])]
    errors = validate_inventory(specs)
    if errors:
        raise ModelInventoryError("; ".join(errors))
    return specs


def save_inventory(specs: list[ModelSpec], path: Path) -> None:
    """Validate and serialize a model inventory to JSON."""

    errors = validate_inventory(specs)
    if errors:
        raise ModelInventoryError("; ".join(errors))
    payload = {"models": [spec.to_json() for spec in specs]}
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    secret_findings = scan_for_secrets(text)
    if secret_findings:
        raise ModelInventoryError(
            "refusing to save inventory that looks like it contains secrets: "
            + "; ".join(secret_findings)
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding="utf-8")
