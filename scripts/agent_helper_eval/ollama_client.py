"""Real Ollama HTTP API client for the agent-helper evaluation harness.

This module talks to a local Ollama server's REST API
(``http://127.0.0.1:11434`` by default) to run an actual connect-gate check
or mini coding-task generation. Nothing in this module is called
automatically anywhere in this package's dry-run flow or test suite with a
*real* network transport -- production code injects
:func:`urllib_json_transport`/:func:`urllib_stream_transport`, tests always
inject a fake transport. A parent agent/human must explicitly invoke a CLI
command (``run_agent_helper_campaign.py connect-gate-run`` /
``mini-gate-run``, see :mod:`agent_helper_eval.live_gates`) to actually reach
a running Ollama server.

Endpoints used:

- ``GET  /api/ps``       -- list currently loaded models (preflight, the
  HTTP equivalent of the ``ollama ps`` CLI command).
- ``POST /api/generate`` (streamed, ``stream: true``) -- generate a
  completion. Streaming is used specifically so a genuine time-to-first-
  token can be measured; a non-streaming call cannot expose this.
- ``POST /api/generate`` with ``keep_alive: 0`` and an empty prompt --
  best-effort unload, always attempted by callers in a ``finally`` block.

Only the Python standard library (``urllib``) is used -- no ``requests``
dependency (not declared in ``requirements.txt``).
"""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.error
import urllib.request
from typing import Callable, Iterator, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

#: Default deterministic-ish decoding seed. Fixed so repeated runs are as
#: reproducible as Ollama/llama.cpp allow (full determinism is not
#: guaranteed across hardware/driver/runtime versions -- see docs).
DEFAULT_SEED = 20260801


class OllamaError(RuntimeError):
    """Raised for any Ollama HTTP API failure (connection, HTTP, protocol,
    or timeout)."""


class OllamaPreflightConflictError(RuntimeError):
    """Raised when :func:`ollama_ps` shows a conflicting model already
    loaded and reuse was not explicitly requested (see
    :func:`check_single_model_preflight`)."""


# ---------------------------------------------------------------------------
# Transport abstraction (production: urllib; tests: fakes)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class JsonResponse:
    status_code: int
    json_body: Optional[dict]
    text_body: str


#: ``(method, url, json_payload_or_None, timeout_seconds) -> JsonResponse``.
JsonTransport = Callable[[str, str, Optional[dict], float], JsonResponse]

#: ``(url, json_payload, timeout_seconds) -> Iterator[str]`` yielding raw
#: NDJSON lines as they arrive from a streamed ``POST``.
StreamTransport = Callable[[str, dict, float], Iterator[str]]


def urllib_json_transport(
    method: str, url: str, payload: Optional[dict], timeout_seconds: float
) -> JsonResponse:
    """Production, non-streaming JSON transport using stdlib ``urllib``."""

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            status_code = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if exc.fp is not None else ""
        status_code = exc.code
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise OllamaError(f"{method} {url} failed: {exc}") from exc
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = None
    return JsonResponse(status_code=status_code, json_body=body, text_body=raw)


def urllib_stream_transport(url: str, payload: dict, timeout_seconds: float) -> Iterator[str]:
    """Production streaming transport: yields each NDJSON line from a
    streamed ``POST /api/generate`` response as it arrives."""

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if line:
                    yield line
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8") if exc.fp is not None else str(exc)
        raise OllamaError(f"POST {url} failed with HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise OllamaError(f"POST {url} failed: {exc}") from exc


# ---------------------------------------------------------------------------
# GET /api/ps -- preflight "max one local model" check
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class OllamaPsEntry:
    name: str
    model: str
    size_bytes: Optional[int]
    digest: Optional[str]
    expires_at: Optional[str]
    size_vram_bytes: Optional[int]


def ollama_ps(
    base_url: str, transport: JsonTransport, timeout_seconds: float = 10.0
) -> list[OllamaPsEntry]:
    """The HTTP equivalent of the ``ollama ps`` CLI command: currently
    loaded models."""

    response = transport("GET", f"{base_url.rstrip('/')}/api/ps", None, timeout_seconds)
    if response.status_code != 200 or response.json_body is None:
        raise OllamaError(
            f"GET /api/ps failed: HTTP {response.status_code}: {response.text_body[:300]!r}"
        )
    entries: list[OllamaPsEntry] = []
    for item in response.json_body.get("models", []):
        entries.append(
            OllamaPsEntry(
                name=item.get("name") or item.get("model") or "",
                model=item.get("model") or item.get("name") or "",
                size_bytes=item.get("size"),
                digest=item.get("digest"),
                expires_at=item.get("expires_at"),
                size_vram_bytes=item.get("size_vram"),
            )
        )
    return entries


def check_single_model_preflight(
    loaded: list[OllamaPsEntry], target_model: str, allow_reuse: bool
) -> None:
    """Enforce the strict "max one local model" preflight policy.

    Raises :class:`OllamaPreflightConflictError` unless:

    - the daemon currently has nothing loaded at all, or
    - the daemon has *exactly* ``target_model`` loaded (nothing else) *and*
      ``allow_reuse`` is explicitly ``True``.

    This is deliberately conservative: even the same model already being
    loaded is refused by default. An operator must consciously pass
    ``allow_reuse=True`` (``--allow-reuse-loaded-model``) to reuse a warm
    model instead of silently assuming it is still the right one.
    """

    if not loaded:
        return
    other = [entry for entry in loaded if entry.model != target_model and entry.name != target_model]
    if other:
        names = ", ".join(sorted({entry.model or entry.name for entry in other}))
        raise OllamaPreflightConflictError(
            f"refusing to run {target_model!r}: Ollama already has a different "
            f"model loaded ({names}); unload it first -- max one local model "
            "may be loaded at a time (12 GB VRAM budget)"
        )
    if not allow_reuse:
        raise OllamaPreflightConflictError(
            f"{target_model!r} is already loaded in Ollama; pass "
            "allow_reuse=True (--allow-reuse-loaded-model) to explicitly "
            "permit reusing an already-warm model instead of treating this "
            "as a conflict"
        )


# ---------------------------------------------------------------------------
# POST /api/generate (streamed) -- deterministic generation + real TTFT
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class GenerateOptions:
    """Explicit, as-deterministic-as-achievable decoding options, with a
    bounded context/output size. Full determinism is not guaranteed by
    Ollama/llama.cpp across hardware/driver/runtime versions -- these are
    simply the most-deterministic settings realistically available."""

    temperature: float = 0.0
    seed: int = DEFAULT_SEED
    top_p: float = 1.0
    top_k: int = 1
    repeat_penalty: float = 1.1
    num_ctx: int = 4096
    num_predict: int = 512
    num_thread: Optional[int] = None

    def to_json(self) -> dict:
        data: dict = {
            "temperature": self.temperature,
            "seed": self.seed,
            "top_p": self.top_p,
            "top_k": self.top_k,
            "repeat_penalty": self.repeat_penalty,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }
        if self.num_thread is not None:
            data["num_thread"] = self.num_thread
        return data


@dataclasses.dataclass
class GenerateResult:
    """Everything captured from one real, streamed ``/api/generate`` call.

    Duration fields are in nanoseconds exactly as Ollama reports them
    (``None`` when Ollama's final message did not include that field --
    never fabricated); convert with ``/ 1e9`` at the call site.
    """

    raw_lines: list[str]
    response_text: str
    model: str
    done: bool
    done_reason: Optional[str]
    wall_seconds: float
    ttft_seconds: Optional[float]
    total_duration_ns: Optional[int]
    load_duration_ns: Optional[int]
    prompt_eval_count: Optional[int]
    prompt_eval_duration_ns: Optional[int]
    eval_count: Optional[int]
    eval_duration_ns: Optional[int]


def ollama_generate(
    base_url: str,
    model: str,
    prompt: str,
    options: GenerateOptions,
    transport: StreamTransport,
    timeout_seconds: float = 120.0,
    keep_alive: str = "10m",
    clock: Callable[[], float] = time.perf_counter,
) -> GenerateResult:
    """Run one real, streamed, deterministic-options generation call.

    ``keep_alive`` deliberately keeps the model warm during generation
    (Ollama's default-ish "10m"); callers are responsible for always
    unloading with :func:`ollama_unload` (``keep_alive=0``) in a ``finally``
    block once the whole attempt (including any test execution) is done.
    """

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "think": False,
        "keep_alive": keep_alive,
        "options": options.to_json(),
    }
    url = f"{base_url.rstrip('/')}/api/generate"
    start = clock()
    ttft: Optional[float] = None
    response_parts: list[str] = []
    raw_lines: list[str] = []
    final: dict = {}
    for line in transport(url, payload, timeout_seconds):
        raw_lines.append(line)
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        chunk = obj.get("response", "")
        if chunk and ttft is None:
            ttft = clock() - start
        if chunk:
            response_parts.append(chunk)
        if obj.get("done"):
            final = obj
    wall_seconds = clock() - start
    if not final:
        raise OllamaError(
            "Ollama stream ended without a final done=true message "
            f"({len(raw_lines)} line(s) received)"
        )
    return GenerateResult(
        raw_lines=raw_lines,
        response_text="".join(response_parts),
        model=final.get("model", model),
        done=bool(final.get("done", False)),
        done_reason=final.get("done_reason"),
        wall_seconds=round(wall_seconds, 3),
        ttft_seconds=round(ttft, 3) if ttft is not None else None,
        total_duration_ns=final.get("total_duration"),
        load_duration_ns=final.get("load_duration"),
        prompt_eval_count=final.get("prompt_eval_count"),
        prompt_eval_duration_ns=final.get("prompt_eval_duration"),
        eval_count=final.get("eval_count"),
        eval_duration_ns=final.get("eval_duration"),
    )


def ollama_unload(
    base_url: str, model: str, transport: JsonTransport, timeout_seconds: float = 15.0
) -> bool:
    """Best-effort unload via ``keep_alive: 0`` with an empty prompt.

    Returns ``True`` if the request completed without raising (regardless
    of response body content), ``False`` if it raised. Callers must never
    let an unload failure crash an already-completed/failed attempt --
    always call this from a ``finally`` block and just record the boolean.
    """

    try:
        transport(
            "POST",
            f"{base_url.rstrip('/')}/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
            timeout_seconds,
        )
        return True
    except OllamaError:
        return False


def ns_to_seconds(value: Optional[int]) -> Optional[float]:
    """Convert an Ollama nanosecond duration field to seconds, preserving
    ``None`` (never fabricating ``0.0`` for a field Ollama did not report)."""

    if value is None:
        return None
    return round(value / 1_000_000_000.0, 3)


def tokens_per_second(token_count: Optional[int], duration_ns: Optional[int]) -> Optional[float]:
    """Derive tokens/sec from a token count and an Ollama duration in
    nanoseconds. ``None`` if either input is missing or duration is zero."""

    if token_count is None or duration_ns is None or duration_ns <= 0:
        return None
    return round(token_count / (duration_ns / 1_000_000_000.0), 2)
