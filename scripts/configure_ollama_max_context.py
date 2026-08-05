"""Persist a safe operating context for every local Ollama model."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def request_json(
    url: str,
    payload: dict[str, object] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise ValueError(f"Ollama request failed ({error.code}): {details}") from error
    if not isinstance(result, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return result


def parse_num_ctx(parameters: object) -> int | None:
    if not isinstance(parameters, str):
        return None
    match = re.search(r"(?m)^num_ctx\s+([0-9.eE+-]+)\s*$", parameters)
    if not match:
        return None
    value = int(float(match.group(1)))
    return value if value > 0 else None


def native_context(model_info: object) -> int:
    if not isinstance(model_info, dict):
        raise ValueError("Ollama model_info must be an object")
    values = [
        int(value)
        for key, value in model_info.items()
        if key.endswith(".context_length")
        and isinstance(value, (int, float))
        and int(value) > 0
    ]
    if not values:
        raise ValueError("Ollama model does not declare a context length")
    return max(values)


def backup_name(model: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")
    digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}.Modelfile"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Set each local Ollama model to its highest declared context."
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path.home()
        / "AppData"
        / "Local"
        / "llm-evaluation-workbench"
        / "ollama-modelfiles",
    )
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument(
        "--max-context",
        type=int,
        help="Cap the operating context; native context remains the default when omitted.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    base_url = args.ollama_url.rstrip("/")
    tags = request_json(f"{base_url}/api/tags").get("models", [])
    if not isinstance(tags, list):
        raise ValueError("Ollama /api/tags returned an invalid models list")

    requested = set(args.models or [])
    if args.max_context is not None and args.max_context <= 0:
        raise ValueError("--max-context must be a positive integer")
    found: set[str] = set()
    changed = 0
    planned = 0
    for item in sorted(tags, key=lambda value: str(value.get("name", "")).lower()):
        name = str(item.get("name") or item.get("model") or "").strip()
        size = int(item.get("size") or 0)
        if not name or size <= 0 or name.lower().endswith(":cloud"):
            continue
        if requested and name not in requested:
            continue
        found.add(name)

        details = request_json(f"{base_url}/api/show", {"model": name})
        declared = native_context(details.get("model_info"))
        configured = parse_num_ctx(details.get("parameters"))
        native_target = max(declared, configured or 0)
        target = (
            min(native_target, args.max_context)
            if args.max_context is not None
            else native_target
        )
        if configured == target:
            print(f"current  {name}: {target}")
            continue

        modelfile = details.get("modelfile")
        if not isinstance(modelfile, str) or not modelfile.strip():
            raise ValueError(f"Ollama did not return a Modelfile for {name}")
        print(f"{'would set' if args.dry_run else 'setting '} {name}: {configured or 'default'} -> {target}")
        planned += 1
        if args.dry_run:
            continue

        args.backup_dir.mkdir(parents=True, exist_ok=True)
        backup = args.backup_dir / backup_name(name)
        if not backup.exists():
            backup.write_text(modelfile, encoding="utf-8")
        request_json(
            f"{base_url}/api/create",
            {
                "model": name,
                "from": name,
                "parameters": {"num_ctx": target},
                "stream": False,
            },
            timeout=600,
        )
        changed += 1

    missing = requested - found
    if missing:
        raise ValueError(f"Local Ollama model(s) not found: {', '.join(sorted(missing))}")
    if args.dry_run:
        print(f"Would configure: {planned}; already current: {len(found) - planned}")
    else:
        print(f"Configured models: {changed}; already current: {len(found) - changed}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
