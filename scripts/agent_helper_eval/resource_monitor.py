"""Lightweight CPU/RAM/GPU/VRAM sampling for real (live) agent-helper attempts.

Mirrors the *semantics* of ``scripts/llm_migration_benchmark.py``'s existing
``SystemMonitor`` (psutil CPU/RAM sampling + a best-effort ``nvidia-smi``
subprocess poll for GPU/VRAM, avg/max reduction) so metrics stay comparable
across tracks -- but this module is a new, small, self-contained
implementation rather than a cross-file import, to keep this package
dependency-isolated from the monolithic legacy runner.

Two layers are kept deliberately separate:

- :func:`summarize_samples` -- a pure function (lists of numbers in, avg/max
  out). Fully deterministic and unit-testable without any timing/threading.
- :class:`ResourceMonitor` -- a background-thread sampler used by the real
  connect-gate/mini-gate executors. Its *lifecycle* (start/stop) is tested,
  but the exact sampled values are not asserted precisely since they depend
  on real, non-deterministic system load.

Never fabricates a value: when ``nvidia-smi`` is unavailable (no GPU, driver
missing, or the call fails/times out) GPU/VRAM fields are left ``None``
("N/A"), never defaulted to zero.
"""

from __future__ import annotations

import dataclasses
import subprocess
import threading
from typing import Callable, List, Optional, Sequence

import psutil


@dataclasses.dataclass
class ResourceStats:
    """Avg/max reduction of one attempt's resource samples. ``None`` means
    "no samples were ever collected for this metric" (N/A), never 0."""

    cpu_avg_percent: Optional[float]
    cpu_max_percent: Optional[float]
    ram_avg_mb: Optional[float]
    ram_max_mb: Optional[float]
    gpu_avg_percent: Optional[float]
    gpu_max_percent: Optional[float]
    vram_avg_mb: Optional[float]
    vram_max_mb: Optional[float]
    #: Best-effort delta of matching process(es)' cumulative CPU time (user +
    #: system seconds, via ``psutil.Process.cpu_times()``) across the
    #: monitored span -- genuine process CPU *time* (seconds of CPU actually
    #: consumed), never confused with ``cpu_avg_percent``/``cpu_max_percent``
    #: (system-wide utilization *percentage*). ``None`` ("N/A") when
    #: ``process_name_filters`` was not set, no matching process was ever
    #: found, or psutil access was denied -- never fabricated. See
    #: :meth:`ResourceMonitor._model_process_cpu_time_delta_seconds`.
    model_process_cpu_time_seconds: Optional[float] = None


def summarize_samples(
    cpu_samples: Sequence[float],
    ram_mb_samples: Sequence[float],
    gpu_samples: Sequence[float],
    vram_mb_samples: Sequence[float],
) -> ResourceStats:
    """Pure avg/max reduction over four independent sample series.

    Each series is reduced independently (they may have different lengths,
    for example when GPU sampling failed on every poll but CPU sampling
    succeeded). An empty series always yields ``None`` for both its avg and
    max fields -- never a fabricated ``0.0``.
    """

    def _avg(values: Sequence[float]) -> Optional[float]:
        return round(sum(values) / len(values), 2) if values else None

    def _max(values: Sequence[float]) -> Optional[float]:
        return round(max(values), 2) if values else None

    return ResourceStats(
        cpu_avg_percent=_avg(cpu_samples),
        cpu_max_percent=_max(cpu_samples),
        ram_avg_mb=_avg(ram_mb_samples),
        ram_max_mb=_max(ram_mb_samples),
        gpu_avg_percent=_avg(gpu_samples),
        gpu_max_percent=_max(gpu_samples),
        vram_avg_mb=_avg(vram_mb_samples),
        vram_max_mb=_max(vram_mb_samples),
    )


def query_nvidia_smi_gpu_vram(timeout_seconds: float = 3.0) -> tuple[Optional[float], Optional[float]]:
    """Best-effort single-sample ``(gpu_utilization_percent, vram_used_mb)``.

    Returns ``(None, None)`` -- never fabricated -- if ``nvidia-smi`` is not
    installed, the call fails, times out, or its output cannot be parsed.
    """

    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if completed.returncode != 0:
        return None, None
    first_line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) < 2:
        return None, None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None, None


GpuQuery = Callable[[], "tuple[Optional[float], Optional[float]]"]


class ResourceMonitor:
    """Background-thread CPU/RAM/GPU/VRAM sampler for the span of one attempt.

    Usage::

        monitor = ResourceMonitor()
        monitor.start()
        try:
            ...  # do the timed work
        finally:
            monitor.stop()
        stats = monitor.stats()

    ``gpu_query`` is injectable so tests never depend on a real GPU/driver;
    production code uses :func:`query_nvidia_smi_gpu_vram` by default.
    ``process_name_filters`` restricts RAM sampling to matching process
    names (for example ``["ollama"]``); when empty, whole-system RAM used is
    sampled instead (a coarser, but still honest, proxy).
    """

    def __init__(
        self,
        sample_interval_seconds: float = 0.5,
        process_name_filters: Optional[Sequence[str]] = None,
        gpu_query: GpuQuery = query_nvidia_smi_gpu_vram,
    ) -> None:
        self._interval = sample_interval_seconds
        self._process_name_filters = [name.lower() for name in (process_name_filters or [])]
        self._gpu_query = gpu_query
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.cpu_samples: List[float] = []
        self.ram_mb_samples: List[float] = []
        self.gpu_samples: List[float] = []
        self.vram_mb_samples: List[float] = []
        # Cumulative (user+system) CPU-seconds per matching PID, snapshotted
        # by the background sampler. ``_first_cpu_times_by_pid`` is fixed at
        # the first successful sample; ``_last_cpu_times_by_pid`` is
        # overwritten on every sample. The delta between them (see
        # :meth:`_model_process_cpu_time_delta_seconds`) is a genuine,
        # best-effort process CPU-*time* measurement -- never fabricated
        # when no matching process is found.
        self._first_cpu_times_by_pid: Optional[dict[int, float]] = None
        self._last_cpu_times_by_pid: dict[int, float] = {}

    def _matching_pids(self) -> list[int]:
        pids: list[int] = []
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if any(needle in name for needle in self._process_name_filters):
                pids.append(proc.info["pid"])
        return pids

    def _sample_once(self) -> None:
        self.cpu_samples.append(psutil.cpu_percent(interval=None))
        if self._process_name_filters:
            total_rss = 0
            found_any = False
            current_cpu_times: dict[int, float] = {}
            for pid in self._matching_pids():
                try:
                    proc = psutil.Process(pid)
                    total_rss += proc.memory_info().rss
                    found_any = True
                    times = proc.cpu_times()
                    current_cpu_times[pid] = times.user + times.system
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if found_any:
                self.ram_mb_samples.append(total_rss / (1024 * 1024))
            if current_cpu_times:
                if self._first_cpu_times_by_pid is None:
                    self._first_cpu_times_by_pid = dict(current_cpu_times)
                self._last_cpu_times_by_pid = current_cpu_times
        else:
            self.ram_mb_samples.append(psutil.virtual_memory().used / (1024 * 1024))
        gpu_percent, vram_mb = self._gpu_query()
        if gpu_percent is not None:
            self.gpu_samples.append(gpu_percent)
        if vram_mb is not None:
            self.vram_mb_samples.append(vram_mb)

    def _model_process_cpu_time_delta_seconds(self) -> Optional[float]:
        """Best-effort delta of matching-process(es) cumulative CPU time.

        Returns ``None`` when ``process_name_filters`` was never set or no
        matching process was ever sampled. A PID present only in the last
        snapshot (a process that appeared mid-attempt, for example a model
        runner subprocess spawned on first load) contributes its full
        cumulative CPU time -- correct, since its baseline before existing
        is genuinely 0, not a fabricated/estimated value.
        """

        if not self._last_cpu_times_by_pid:
            return None
        first = self._first_cpu_times_by_pid or {}
        total = 0.0
        for pid, last_value in self._last_cpu_times_by_pid.items():
            total += max(last_value - first.get(pid, 0.0), 0.0)
        return round(total, 3)

    def _run(self) -> None:
        psutil.cpu_percent(interval=None)  # prime the non-blocking counter
        while not self._stop_event.is_set():
            self._sample_once()
            self._stop_event.wait(self._interval)

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("ResourceMonitor.start() called twice on the same instance")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="agent-helper-resource-monitor", daemon=True)
        self._thread.start()

    def stop(self, join_timeout_seconds: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout_seconds)

    def stats(self) -> ResourceStats:
        base = summarize_samples(
            self.cpu_samples, self.ram_mb_samples, self.gpu_samples, self.vram_mb_samples
        )
        return dataclasses.replace(
            base, model_process_cpu_time_seconds=self._model_process_cpu_time_delta_seconds()
        )
