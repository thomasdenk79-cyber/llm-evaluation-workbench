"""Filesystem preflight lock enforcing "max one local model running at once".

The workspace hardware rule (12 GB VRAM notebook GPU) requires that at most
one local Ollama/llama.cpp model run at a time. This module implements a
simple, dependency-free, crash-safe exclusive lock backed by an atomically
created lock file so a campaign orchestrator can refuse to start a second
local-backend run while one is already in flight, instead of silently
overlapping two local models.

Siemens API models and GitHub Copilot agent references are not "local" in
this sense (they do not consume the notebook's own GPU/VRAM) and are exempt
from the lock; see :func:`is_local_backend`.

This module never starts, stops, or talks to a model; it only manages a lock
file on disk.

Relationship to post-quality-gate concurrency/capacity profiling (see
:mod:`agent_helper_eval.capacity_profile`): "max one local model" is a
*process/model-identity* lock -- at most one local (backend, model) pair may
be loaded at a time -- it is deliberately not a *request-count* lock. A
concurrency profile run (2 or 4 simultaneous requests, see
``CONCURRENCY_LEVELS``) still holds exactly one such lock for the single
already-accepted local model while issuing multiple concurrent requests
*against that one loaded model*; this module requires no change to support
that, since it was never counting in-flight requests, only concurrently
loaded local models/processes.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import json
import os
from pathlib import Path
from typing import Iterator, Optional

import psutil

#: Backends subject to the "max one local model" rule.
LOCAL_BACKENDS = frozenset({"ollama", "llama_cpp"})

DEFAULT_LOCK_FILENAME = "agent-helper-local-model.lock"


def is_local_backend(backend: str) -> bool:
    """True if ``backend`` runs on the local machine's own GPU/VRAM."""

    return backend in LOCAL_BACKENDS


class LocalModelLockError(RuntimeError):
    """Raised when a local-model lock cannot be acquired."""


@dataclasses.dataclass
class LockInfo:
    pid: int
    backend: str
    model: str
    acquired_at: str


def _read_lock_info(lock_path: Path) -> Optional[LockInfo]:
    try:
        raw = lock_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
        return LockInfo(**data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    return psutil.pid_exists(pid)


def acquire_local_model_lock(
    lock_path: Path, backend: str, model: str, pid: Optional[int] = None
) -> LockInfo:
    """Acquire the exclusive local-model lock or raise :class:`LocalModelLockError`.

    Uses an atomic create-exclusive file open so two concurrent preflight
    checks cannot both succeed. A stale lock (owning PID no longer alive) is
    automatically reclaimed.
    """

    if not is_local_backend(backend):
        raise ValueError(
            f"acquire_local_model_lock() is only for local backends "
            f"{sorted(LOCAL_BACKENDS)}, got {backend!r}"
        )

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_lock_info(lock_path)
    if existing is not None and _pid_is_alive(existing.pid):
        raise LocalModelLockError(
            f"local-model lock already held by pid={existing.pid} for "
            f"{existing.backend}/{existing.model} since {existing.acquired_at}; "
            "max one local model may run at a time"
        )
    if existing is not None:
        # Stale lock (owning process is gone): reclaim it.
        lock_path.unlink(missing_ok=True)

    info = LockInfo(
        pid=pid if pid is not None else os.getpid(),
        backend=backend,
        model=model,
        acquired_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    )
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dataclasses.asdict(info), handle)
    except OSError as exc:
        raise LocalModelLockError(
            f"could not acquire local-model lock at {lock_path}: {exc}"
        ) from exc
    return info


def release_local_model_lock(lock_path: Path, pid: Optional[int] = None) -> None:
    """Release the lock, only if it is currently owned by ``pid`` (default: this process)."""

    existing = _read_lock_info(lock_path)
    if existing is None:
        return
    owner_pid = pid if pid is not None else os.getpid()
    if existing.pid == owner_pid:
        lock_path.unlink(missing_ok=True)


@contextlib.contextmanager
def local_model_slot(lock_path: Path, backend: str, model: str) -> Iterator[LockInfo]:
    """Context manager: acquire the local-model lock, always release on exit."""

    info = acquire_local_model_lock(lock_path, backend, model)
    try:
        yield info
    finally:
        release_local_model_lock(lock_path)
