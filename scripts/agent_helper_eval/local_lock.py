"""Adapter from benchmark code to the canonical shared local-model lease."""

from __future__ import annotations

import contextlib
import importlib.util
import os
from pathlib import Path
import sys
from typing import Iterator, Optional


LOCAL_BACKENDS = frozenset({"ollama", "llama_cpp"})
DEFAULT_LOCK_FILENAME = "agent-helper-local-model.lock"
DEFAULT_RESOURCE_ID = "local-llm"
DEFAULT_WAIT_SECONDS = 3600.0
DEFAULT_TTL_SECONDS = 300.0


def _load_shared_module():
    configured = os.environ.get("LOCAL_MODEL_LEASE_SCRIPT")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            Path(r"C:\GIT\standards\scripts\local_model_lease.py"),
            repo_root / "standards" / "scripts" / "local_model_lease.py",
        ]
    )
    script_path = next((path for path in candidates if path.is_file()), None)
    if script_path is None:
        searched = ", ".join(str(path) for path in candidates)
        raise RuntimeError(
            "canonical local-model lease script not found; set "
            f"LOCAL_MODEL_LEASE_SCRIPT or install standards (searched: {searched})"
        )
    module_name = "_shared_local_model_lease"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load local-model lease script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_shared = _load_shared_module()
LocalModelLockError = _shared.LeaseError
LockInfo = _shared.LeaseMetadata
_owned_leases: dict[tuple[str, str], str] = {}


def _load_runtime_module():
    configured = os.environ.get("AI_RUNTIME_SCRIPT")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path(r"C:\GIT\standards\scripts\ai_runtime.py"),
            Path(__file__).resolve().parents[2] / "standards" / "scripts" / "ai_runtime.py",
        ]
    )
    script_path = next((path for path in candidates if path.is_file()), None)
    if script_path is None:
        raise RuntimeError(
            "AI runtime control-plane script not found; set AI_RUNTIME_SCRIPT or install standards"
        )
    module_name = "_shared_ai_runtime"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load AI runtime control-plane script: {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_runtime = _load_runtime_module()


def is_local_backend(backend: str) -> bool:
    return backend in LOCAL_BACKENDS


def _assert_runtime_available() -> None:
    try:
        policy = _runtime.load_policy(_runtime.DEFAULT_POLICY_PATH)
    except _runtime.RuntimeErrorBase as error:
        raise LocalModelLockError(f"cannot load AI runtime policy: {error}") from error
    reason = _runtime.local_block_reason(policy, _runtime.DEFAULT_STATE_DIR)
    if reason:
        raise LocalModelLockError(f"local model resource is blocked: {reason}")


def _manager(lock_path: Optional[Path]):
    root = Path(lock_path).parent if lock_path is not None else None
    return _shared.LeaseManager(root)


def acquire_local_model_lock(
    lock_path: Optional[Path],
    backend: str,
    model: str,
    pid: Optional[int] = None,
    *,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> LockInfo:
    if not is_local_backend(backend):
        raise ValueError(
            f"acquire_local_model_lock() is only for local backends "
            f"{sorted(LOCAL_BACKENDS)}, got {backend!r}"
        )
    _assert_runtime_available()
    manager = _manager(lock_path)
    owner_pid = os.getpid() if pid is None else pid
    owner = f"llm-evaluation-workbench:{owner_pid}"
    metadata = manager.acquire(
        DEFAULT_RESOURCE_ID,
        owner,
        f"{backend}/{model}",
        ttl_seconds=ttl_seconds,
        wait_seconds=wait_seconds,
        pid=owner_pid,
    )
    _owned_leases[(str(manager.root), str(owner_pid))] = metadata.lease_id
    return metadata


def release_local_model_lock(
    lock_path: Optional[Path],
    pid: Optional[int] = None,
) -> None:
    manager = _manager(lock_path)
    owner_pid = os.getpid() if pid is None else pid
    key = (str(manager.root), str(owner_pid))
    lease_id = _owned_leases.pop(key, None)
    if lease_id is not None:
        manager.release(DEFAULT_RESOURCE_ID, lease_id)


@contextlib.contextmanager
def local_model_slot(
    lock_path: Optional[Path],
    backend: str,
    model: str,
    *,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
) -> Iterator[LockInfo]:
    if not is_local_backend(backend):
        raise ValueError(
            f"local_model_slot() is only for local backends "
            f"{sorted(LOCAL_BACKENDS)}, got {backend!r}"
        )
    _assert_runtime_available()
    manager = _manager(lock_path)
    owner = f"llm-evaluation-workbench:{os.getpid()}"
    with manager.hold(
        DEFAULT_RESOURCE_ID,
        owner,
        f"{backend}/{model}",
        ttl_seconds=ttl_seconds,
        wait_seconds=wait_seconds,
    ) as metadata:
        yield metadata
