"""Synchronize installed Ollama and llama.cpp models into OpenCode."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# Native model context is capability metadata, not an operating budget. The
# runtime guard requires a new phase before 98,304 tokens and keeps output
# bounded so tool-agent sessions do not reserve a full native context by default.
DEFAULT_CONTEXT = 98_304
DEFAULT_OUTPUT = 8_192
MAX_OPERATING_CONTEXT = 98_304
SHARD_PATTERN = re.compile(
    r"^(?P<base>.+)-(?P<part>\d{5})-of-(?P<total>\d{5})\.gguf$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GgufModel:
    alias: str
    path: Path
    parts: int
    size_bytes: int
    context: int


def request_json(url: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return result


def normalize_alias(name: str) -> str:
    alias = name.removesuffix(".gguf")
    alias = re.sub(r"-from-ollama$", "", alias, flags=re.IGNORECASE)
    alias = re.sub(r"^qwen[_-]qwen", "qwen", alias, flags=re.IGNORECASE)
    alias = re.sub(r"[^A-Za-z0-9.]+", "-", alias).strip("-").lower()
    if not alias:
        raise ValueError(f"Cannot derive model alias from {name!r}")
    return alias


def _read_exact(handle: Any, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("Unexpected end of GGUF metadata")
    return data


def _read_gguf_string(handle: Any) -> str:
    size = struct.unpack("<Q", _read_exact(handle, 8))[0]
    return _read_exact(handle, size).decode("utf-8")


def _skip_gguf_value(handle: Any, value_type: int) -> None:
    fixed_sizes = {
        0: 1,
        1: 1,
        2: 2,
        3: 2,
        4: 4,
        5: 4,
        6: 4,
        7: 1,
        10: 8,
        11: 8,
        12: 8,
    }
    if value_type in fixed_sizes:
        handle.seek(fixed_sizes[value_type], 1)
        return
    if value_type == 8:
        handle.seek(struct.unpack("<Q", _read_exact(handle, 8))[0], 1)
        return
    if value_type == 9:
        element_type = struct.unpack("<I", _read_exact(handle, 4))[0]
        count = struct.unpack("<Q", _read_exact(handle, 8))[0]
        if element_type in fixed_sizes:
            handle.seek(fixed_sizes[element_type] * count, 1)
        else:
            for _ in range(count):
                _skip_gguf_value(handle, element_type)
        return
    raise ValueError(f"Unsupported GGUF metadata type: {value_type}")


def read_gguf_context(path: Path) -> int:
    integer_formats = {
        0: "<B",
        1: "<b",
        2: "<H",
        3: "<h",
        4: "<I",
        5: "<i",
        10: "<Q",
        11: "<q",
    }
    with path.open("rb") as handle:
        if _read_exact(handle, 4) != b"GGUF":
            raise ValueError(f"Invalid GGUF file: {path}")
        version = struct.unpack("<I", _read_exact(handle, 4))[0]
        if version not in (2, 3):
            raise ValueError(f"Unsupported GGUF version {version}: {path}")
        _read_exact(handle, 8)
        metadata_count = struct.unpack("<Q", _read_exact(handle, 8))[0]
        for _ in range(metadata_count):
            key = _read_gguf_string(handle)
            value_type = struct.unpack("<I", _read_exact(handle, 4))[0]
            if key.endswith(".context_length"):
                value_format = integer_formats.get(value_type)
                if value_format is None:
                    raise ValueError(f"Invalid GGUF context type for {path}: {value_type}")
                context = int(
                    struct.unpack(
                        value_format,
                        _read_exact(handle, struct.calcsize(value_format)),
                    )[0]
                )
                if context <= 0:
                    raise ValueError(f"Invalid GGUF context length for {path}: {context}")
                return context
            _skip_gguf_value(handle, value_type)
    raise ValueError(f"GGUF context length not found: {path}")


def discover_gguf_models(root: Path) -> tuple[list[GgufModel], list[str]]:
    if not root.is_dir():
        raise FileNotFoundError(f"llama.cpp model directory not found: {root}")

    groups: dict[tuple[Path, str], list[tuple[Path, int, int]]] = {}
    for path in sorted(root.rglob("*.gguf")):
        if "mmproj" in path.name.lower():
            continue
        match = SHARD_PATTERN.match(path.name)
        if match:
            base = match.group("base")
            part = int(match.group("part"))
            total = int(match.group("total"))
        else:
            base = path.stem
            part = total = 1
        groups.setdefault((path.parent, base), []).append((path, part, total))

    models: list[GgufModel] = []
    warnings: list[str] = []
    aliases: set[str] = set()
    for (_, base), shards in sorted(groups.items(), key=lambda item: str(item[0]).lower()):
        expected = shards[0][2]
        parts = sorted({part for _, part, _ in shards})
        complete = (
            all(total == expected for _, _, total in shards)
            and parts == list(range(1, expected + 1))
        )
        if not complete:
            warnings.append(f"Skipped incomplete GGUF set {base}: {len(parts)}/{expected} parts")
            continue

        alias = normalize_alias(base)
        if alias in aliases:
            raise ValueError(f"Duplicate llama.cpp model alias: {alias}")
        aliases.add(alias)
        first = min(shards, key=lambda item: item[1])[0]
        models.append(
            GgufModel(
                alias=alias,
                path=first.resolve(),
                parts=expected,
                size_bytes=sum(path.stat().st_size for path, _, _ in shards),
                context=read_gguf_context(first),
            )
        )
    return sorted(models, key=lambda model: model.alias), warnings


def model_definition(
    display_name: str,
    context: int = DEFAULT_CONTEXT,
    image_input: bool = False,
) -> dict[str, object]:
    inputs = ["text", "image"] if image_input else ["text"]
    return {
        "name": display_name,
        "limit": {
            "context": min(context, MAX_OPERATING_CONTEXT),
            "output": min(context, DEFAULT_OUTPUT),
        },
        "modalities": {"input": inputs, "output": ["text"]},
    }


def discover_ollama_models(
    base_url: str,
    requester: Callable[[str, dict[str, object] | None], dict[str, Any]] = request_json,
    max_context: int | None = None,
) -> tuple[dict[str, object], list[str]]:
    tags = requester(f"{base_url.rstrip('/')}/api/tags", None).get("models", [])
    if not isinstance(tags, list):
        raise ValueError("Ollama /api/tags returned an invalid models list")

    models: dict[str, object] = {}
    warnings: list[str] = []
    for item in sorted(tags, key=lambda value: str(value.get("name", "")).lower()):
        name = str(item.get("name") or item.get("model") or "").strip()
        size = int(item.get("size") or 0)
        if not name:
            continue
        if size <= 0 or name.lower().endswith(":cloud"):
            warnings.append(f"Skipped non-local Ollama model: {name}")
            continue

        context = DEFAULT_CONTEXT
        image_input = False
        try:
            details = requester(
                f"{base_url.rstrip('/')}/api/show",
                {"model": name},
            )
            model_info = details.get("model_info", {})
            if isinstance(model_info, dict):
                candidates = [
                    value
                    for key, value in model_info.items()
                    if key.endswith(".context_length")
                    and isinstance(value, int)
                    and value > 0
                ]
                if candidates:
                    context = min(max(candidates), MAX_OPERATING_CONTEXT)
            capabilities = details.get("capabilities", [])
            image_input = isinstance(capabilities, list) and "vision" in capabilities
            parameters = details.get("parameters", "")
            if isinstance(parameters, str):
                configured = re.search(
                    r"(?m)^num_ctx\s+([0-9.eE+-]+)\s*$",
                    parameters,
                )
                if configured:
                    context = min(
                        max(context, int(float(configured.group(1)))),
                        MAX_OPERATING_CONTEXT,
                    )
        except (OSError, ValueError, urllib.error.URLError) as error:
            warnings.append(f"Using default metadata for {name}: {error}")

        if max_context is not None:
            context = min(context, max_context)
        models[name] = model_definition(f"Ollama | {name}", context, image_input)
    return models, warnings


def render_llama_presets(
    models: list[GgufModel],
    max_context: int | None = None,
) -> str:
    sections = []
    for model in models:
        if "\n" in str(model.path) or "=" in str(model.path):
            raise ValueError(f"Unsupported model path: {model.path}")
        sections.append(
            f"[{model.alias}]\n"
            f"model = {model.path}\n"
            f"ctx-size = {min(model.context, max_context) if max_context else model.context}\n"
            "flash-attn = on\n"
            "cache-type-k = q4_0\n"
            "cache-type-v = q4_0\n"
        )
    return "\n".join(sections)


def atomic_write(path: Path, content: str) -> bool:
    encoded = content.encode("utf-8")
    if path.is_file() and path.read_bytes() == encoded:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return True


def update_opencode_config(
    path: Path,
    ollama_models: dict[str, object] | None,
    llama_models: list[GgufModel],
    ollama_base_url: str,
    llama_base_url: str,
    max_context: int | None = None,
    dry_run: bool = False,
) -> bool:
    if not path.is_file():
        raise FileNotFoundError(f"OpenCode config not found: {path}")
    original = path.read_bytes()
    config = json.loads(original.decode("utf-8-sig"))
    if not isinstance(config, dict):
        raise ValueError("OpenCode config root must be an object")

    providers = config.setdefault("provider", {})
    if not isinstance(providers, dict):
        raise ValueError("OpenCode provider configuration must be an object")

    if ollama_models is not None:
        ollama = providers.setdefault("ollama", {})
        if not isinstance(ollama, dict):
            raise ValueError("OpenCode Ollama provider must be an object")
        ollama["npm"] = "@ai-sdk/openai-compatible"
        ollama["name"] = "ollama"
        ollama_options = ollama.setdefault("options", {})
        if not isinstance(ollama_options, dict):
            raise ValueError("OpenCode Ollama provider options must be an object")
        ollama_options["baseURL"] = f"{ollama_base_url.rstrip('/')}/v1"
        ollama["models"] = ollama_models

    providers["llama-cpp"] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "llama.cpp router",
        "options": {"baseURL": llama_base_url.rstrip("/")},
        "models": {
            model.alias: model_definition(
                f"llama.cpp | {model.alias}",
                min(model.context, max_context) if max_context else model.context,
            )
            for model in llama_models
        },
    }

    updated = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    if updated == original:
        return False
    if dry_run:
        return True

    backup = path.with_name(f"{path.name}.bak")
    shutil.copy2(path, backup)
    atomic_write(path, updated.decode("utf-8"))
    return True


def build_parser() -> argparse.ArgumentParser:
    home = Path.home()
    parser = argparse.ArgumentParser(
        description="Expose installed Ollama and llama.cpp models as separate OpenCode providers."
    )
    parser.add_argument(
        "--opencode-config",
        type=Path,
        default=home / ".config" / "opencode" / "opencode.json",
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--llama-model-dir",
        type=Path,
        default=home / "llama.cpp" / "models",
    )
    parser.add_argument(
        "--llama-preset",
        type=Path,
        default=home / "llama.cpp" / "models" / "router-models.ini",
    )
    parser.add_argument("--llama-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument(
        "--max-context",
        type=int,
        help="Cap the operating context for both Ollama and llama.cpp models.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.max_context is not None and args.max_context <= 0:
        raise ValueError("--max-context must be a positive integer")
    try:
        ollama_models, ollama_warnings = discover_ollama_models(
            args.ollama_url,
            max_context=args.max_context,
        )
        if not ollama_models:
            ollama_warnings.append(
                "No local Ollama models discovered; existing OpenCode entries were preserved"
            )
            ollama_models = None
    except (OSError, ValueError, urllib.error.URLError) as error:
        ollama_models = None
        ollama_warnings = [
            f"Ollama unavailable; existing OpenCode entries were preserved: {error}"
        ]
    llama_models, llama_warnings = discover_gguf_models(args.llama_model_dir)
    if not llama_models:
        raise RuntimeError("No complete llama.cpp GGUF models discovered")

    preset_changed = False
    if not args.dry_run:
        preset_changed = atomic_write(
            args.llama_preset,
            render_llama_presets(llama_models, args.max_context),
        )
    config_changed = update_opencode_config(
        args.opencode_config,
        ollama_models,
        llama_models,
        args.ollama_url,
        args.llama_url,
        args.max_context,
        args.dry_run,
    )

    if ollama_models is None:
        print("Ollama models: unavailable (existing OpenCode entries preserved)")
    else:
        print(f"Ollama models: {len(ollama_models)}")
        for name in ollama_models:
            print(f"  ollama/{name}")
    print(f"llama.cpp models: {len(llama_models)}")
    for model in llama_models:
        print(
            f"  llama-cpp/{model.alias} "
            f"({model.parts} part(s), {model.size_bytes / 1024**3:.2f} GiB, "
            f"context {model.context})"
        )
    print(
        f"OpenCode config: {'would update' if args.dry_run and config_changed else 'updated' if config_changed else 'current'}"
    )
    print(
        f"llama.cpp preset: {'not written (dry-run)' if args.dry_run else 'updated' if preset_changed else 'current'}"
    )
    for warning in (*ollama_warnings, *llama_warnings):
        print(f"WARNING: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileNotFoundError,
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
