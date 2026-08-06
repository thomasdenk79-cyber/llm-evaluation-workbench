"""Detect and rebuild llama.cpp source forks.

This module provides toolchain-agnostic detection of llama.cpp-style source
trees and attempts an automated CMake build. Where building is not possible
(missing CMake, missing compiler, or the build itself fails), it emits a
durable ``install.md`` that an autonomous agent can follow to complete the
rebuild manually.

This is the operational counterpart to ``docs/project/requirements.md`` §2:
where a backend is a source fork, rebuild it. Where automatic rebuild is not
possible, produce a durable ``install.md`` an autonomous agent can follow.

Design principles:
- Never fabricates success: if the binary does not exist or the cmake step
  fails, ``ForkBuildResult.success`` is ``False`` and an ``install.md`` is
  generated on disk.
- No network access: all tool detection and build execution relies on the
  local filesystem and subprocess invocations only.
- Pure-detection helpers (``is_source_tree``, ``find_binary``) have no side
  effects and are trivially unit-testable.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ForkBuildResult:
    """Outcome of a single :func:`build` attempt.

    Attributes:
        success: True only if cmake configured and compiled without error
            and the expected ``llama-server`` binary was found.
        binary_path: Absolute path to the compiled ``llama-server`` binary
            (only set when ``success`` is ``True``).
        install_md_path: Absolute path to the generated ``install.md``
            instructions file (only set when ``success`` is ``False``).
        log: Captured stdout/stderr from the build process, or an explanation
            of the failure for cases where no subprocess was launched.
    """

    success: bool
    binary_path: Optional[Path] = None
    install_md_path: Optional[Path] = None
    log: str = ""


# ---------------------------------------------------------------------------
# Source-tree detection
# ---------------------------------------------------------------------------

def is_source_tree(path: Path) -> bool:
    """Detect if *path* is a plausible llama.cpp source checkout.

    Checks for the three structural markers that every official (or forked)
    llama.cpp repository carries at its top level:

    - ``CMakeLists.txt`` — the project's CMake root file.
    - ``cmake/`` — the CMake module helper directory.
    - ``ggml/`` — the core tensor-library subdirectory that is unique to
      the llama.cpp ecosystem.

    Returns ``False`` if *path* does not exist or any of the three markers
    is absent. Does not inspect git metadata — a shallow copy without a
    ``.git/`` folder is still accepted as a source tree.
    """

    if not path.is_dir():
        return False
    return all(
        p.exists()
        for p in (path / "CMakeLists.txt", path / "cmake", path / "ggml")
    )


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

def find_binary(source_path: Path) -> Optional[Path]:
    """Locate an already-compiled ``llama-server`` inside *source_path*.

    Searches the conventional output directories that CMake and some fork
    build scripts use. Returns the first existing candidate, or ``None`` if
    no binary is found.

    Candidates (checked in order):

    1. ``<source>/build/bin/llama-server(.exe)``
       — standard CMake out-of-source build output on both platforms.
    2. ``<source>/bin/llama-server(.exe)``
       — legacy in-source build output (some forks/CI leave the binary here).
    3. ``<source>/build/bin/Release/llama-server.exe``
       — Windows multi-config CMake layout.
    4. ``<source>/build/bin/RelWithDebInfo/llama-server.exe``
       — Windows multi-config CMake layout (alternate config).
    """

    candidates = [
        source_path / "build" / "bin" / "llama-server",
        source_path / "build" / "bin" / "llama-server.exe",
        source_path / "bin" / "llama-server",
        source_path / "bin" / "llama-server.exe",
        source_path / "build" / "bin" / "Release" / "llama-server.exe",
        source_path / "build" / "bin" / "RelWithDebInfo" / "llama-server.exe",
    ]
    return next((c for c in candidates if c.is_file()), None)


# ---------------------------------------------------------------------------
# Toolchain helpers
# ---------------------------------------------------------------------------

def _find_cmake() -> Optional[str]:
    """Return the absolute path to ``cmake``, or ``None`` if unavailable."""
    return shutil.which("cmake")


def _find_compiler() -> Optional[str]:
    """Return the path to a C/C++ compiler suitable for building llama.cpp.

    On Windows we look for the MSVC ``cl.exe`` (via the Microsoft Build
    Tools or Visual Studio Developer Command Prompt). On Unix-like platforms
    we check for ``gcc`` first, then ``clang``.
    """

    if platform.system() == "Windows":
        return shutil.which("cl")
    # Unix-like: prefer gcc, fall back to clang
    compiler = shutil.which("gcc")
    if compiler:
        return compiler
    return shutil.which("clang")


def _cpu_count() -> int:
    """Return the number of available CPU cores for parallel builds.

    Falls back to ``4`` when the runtime cannot determine the count (e.g.
    some minimal containers or cross-compiled environments).
    """

    try:
        return os.cpu_count() or 4
    except (OSError, AttributeError):
        return 4


# ---------------------------------------------------------------------------
# Build logic
# ---------------------------------------------------------------------------

def build(
    source_path: Path,
    build_type: str = "Release",
    enable_cuda: bool = False,
    cpu_jobs: Optional[int] = None,
) -> ForkBuildResult:
    """Attempt a full CMake configure + build of a llama.cpp source tree.

    Arguments:
        source_path: Root directory of the source checkout. Will be passed
            to :func:`is_source_tree`; if that returns ``False``, the
            function returns a ``ForkBuildResult`` with ``success=False``
            and a generated ``install.md``.
        build_type: CMake build type (``Release``, ``Debug``, or
            ``RelWithDebInfo``). Only relevant for single-config generators
            (Unix Makefiles, Ninja).
        enable_cuda: If ``True``, passes ``-DLLAMA_CUDA=ON`` to the
            configure step. If ``False``, the flag is omitted entirely,
            leaving the project to default to its cpu-only configuration
            (or to auto-detect the GPU backend if the fork has that logic).
        cpu_jobs: Parallelism level for the build step. Defaults to
            :func:`os.cpu_count`.

    Returns:
        A :class:`ForkBuildResult`. On success, ``success=True`` and
        ``binary_path`` points to the compiled ``llama-server``. On any
        failure, ``success=False`` and ``install_md_path`` points to a
        freshly generated ``docs/operations/install.md`` (relative to
        *source_path*) that documents the exact steps an autonomous agent
        can follow to retry the build.

    The build creates a ``build/`` directory alongside the source root and
    uses ``cmake --build`` with the appropriate parallelism. All
    stdout/stderr from the configure and build steps are captured into the
    returned ``log`` field for diagnostics.
    """

    log_parts: list[str] = []

    # -- Pre-flight: is this a llama.cpp source tree? -------------------
    if not is_source_tree(source_path):
        msg = (
            f"{source_path}: not recognized as a llama.cpp source "
            f"tree (CMakeLists.txt, cmake/, or ggml/ is missing)"
        )
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=msg,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    # -- Pre-flight: check toolchain -----------------------------------
    cmake_path = _find_cmake()
    if not cmake_path:
        msg = "CMake not found in PATH; install CMake >= 3.21 and retry"
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=msg,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    compiler_path = _find_compiler()
    if not compiler_path:
        msg = (
            f"No C/C++ compiler found in PATH (tried cl.exe on Windows, "
            f"gcc/clang on Unix); install a toolchain and retry"
        )
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=msg,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    log_parts.append(f"Found CMake at {cmake_path}")
    log_parts.append(f"Found compiler at {compiler_path}")

    # -- Check if binary already exists --------------------------------
    existing_binary = find_binary(source_path)
    if existing_binary:
        log_parts.append(f"Existing binary found at {existing_binary}")
        return ForkBuildResult(
            success=True,
            binary_path=existing_binary,
            log="\n".join(log_parts),
        )

    # -- Configure -----------------------------------------------------
    build_dir = source_path / "build"
    jobs = cpu_jobs if cpu_jobs is not None else _cpu_count()

    configure_cmd = [
        cmake_path,
        str(source_path),
        "-DCMAKE_BUILD_TYPE=" + build_type,
    ]

    # Pass compiler explicitly (helps on Windows when cl.exe is not the
    # default generator's compiler).
    if compiler_path:
        if platform.system() == "Windows":
            # CMake on Windows often needs CC/CXX set explicitly for
            # the Ninja/Visual Studio generators.
            configure_cmd.extend(["-DCMAKE_C_COMPILER=" + compiler_path])
            configure_cmd.extend(["-DCMAKE_CXX_COMPILER=" + compiler_path])
        else:
            configure_cmd.extend(["-DCMAKE_C_COMPILER=" + compiler_path])
            configure_cmd.extend(["-DCMAKE_CXX_COMPILER=" + compiler_path])

    if enable_cuda:
        configure_cmd.append("-DLLAMA_CUDA=ON")
        log_parts.append("CUDA acceleration enabled (LLAMA_CUDA=ON)")

    log_parts.append("Configuring: " + " ".join(configure_cmd))

    try:
        configure_result = subprocess.run(
            configure_cmd,
            cwd=str(build_dir),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        configure_output = configure_result.stdout + configure_result.stderr
        log_parts.append(configure_output)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        msg = f"CMake configure failed or timed out: {exc}"
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=configure_result.stdout + configure_result.stderr
                if isinstance(exc, subprocess.TimeoutExpired) else msg,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    if configure_result.returncode != 0:
        msg = (
            f"CMake configure exited with code {configure_result.returncode}; "
            "see log above for diagnostics"
        )
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=configure_output,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    # -- Build ---------------------------------------------------------
    build_cmd = [
        cmake_path,
        "--build",
        str(build_dir),
        "--config",
        build_type,
        "--parallel" if "Makefiles" in str(build_dir) or platform.system() != "Windows"
            else "-j",
        str(jobs),
    ]

    # Normalize parallelism flag: Ninja and Unix Makefiles use --parallel
    # or -j; Visual Studio uses /maxcpucount in its native form, but
    # ``cmake --build --parallel N`` handles this portably.
    build_cmd = [
        cmake_path,
        "--build",
        str(build_dir),
        "--config",
        build_type,
        "--parallel",
        str(jobs),
    ]

    log_parts.append(f"Building with {jobs} parallel jobs: " + " ".join(build_cmd))

    try:
        build_result = subprocess.run(
            build_cmd,
            cwd=str(build_dir),
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        build_output = build_result.stdout + build_result.stderr
        log_parts.append(build_output)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        msg = f"CMake build failed or timed out: {exc}"
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=getattr(exc, "stdout", "") + getattr(exc, "stderr", "") or msg,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    if build_result.returncode != 0:
        msg = (
            f"CMake build exited with code {build_result.returncode}; "
            "see log above for diagnostics"
        )
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=build_output,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    # -- Verify binary -------------------------------------------------
    binary = find_binary(source_path)
    if not binary:
        msg = (
            "CMake exited successfully but llama-server binary was not found "
            f"in the expected locations under {source_path}"
        )
        log_parts.append(msg)
        install_md = write_install_md(
            source_path=source_path,
            cmake_errors=build_output,
            project_root=source_path,
        )
        return ForkBuildResult(
            success=False,
            install_md_path=install_md,
            log="\n".join(log_parts),
        )

    log_parts.append(f"Build successful — binary at {binary}")
    return ForkBuildResult(
        success=True,
        binary_path=binary,
        log="\n".join(log_parts),
    )


# ---------------------------------------------------------------------------
# install.md generation
# ---------------------------------------------------------------------------

def write_install_md(
    source_path: Path,
    cmake_errors: str,
    project_root: Optional[Path] = None,
) -> Path:
    """Write a durable ``install.md`` that an autonomous agent can follow.

    The file is placed at ``docs/operations/install.md`` relative to
    *project_root* (or relative to *source_path* if *project_root* is
    ``None``). The generated document includes:

    - Exact CMake commands to configure and build.
    - Required dependencies (CMake version, compiler, CUDA toolkit).
    - The captured error output from the failed attempt.
    - Step-by-step numbered instructions suitable for an agent executing
      terminal commands.

    Arguments:
        source_path: Root of the llama.cpp source tree (used to populate
            paths in the generated document).
        cmake_errors: Raw stdout/stderr from the failed CMake invocation,
            or a plain-text explanation when no subprocess was run.
        project_root: Optional parent project directory (e.g. the
            ``llm-evaluation-workbench`` root). When provided, the file
            is written at ``<project_root>/docs/operations/install.md``;
            otherwise it is written at
            ``<source_path>/install.md``.

    Returns:
        The absolute path to the written ``install.md`` file.
    """

    if project_root is None:
        project_root = source_path

    dest_dir = project_root / "docs" / "operations"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "install.md"

    # Escape the source path for inclusion in the markdown document.
    src_str = str(source_path)

    # Truncate cmake_errors to avoid generating excessively large docs.
    displayed_errors = cmake_errors.strip()
    if len(displayed_errors) > 2000:
        displayed_errors = displayed_errors[:2000] + "\n\n...(truncated for brevity)..."

    content = textwrap.dedent(f"""\
        # llama.cpp Build Instructions

        > **Generated automatically** by the fork-build automation after a
        > build attempt failed. This document contains exact commands an
        > autonomous agent can execute to rebuild the backend.

        ## Source location

        ``{src_str}``

        ## Prerequisites

        1. **CMake** >= 3.21 (https://cmake.org/download/)
        2. **C/C++ compiler**:
           - Windows: MSVC (Visual Studio 2019+ or Build Tools)
           - Linux: ``gcc`` >= 9 or ``clang`` >= 10
           - macOS: Xcode CLT / ``clang``
        3. **Optional — CUDA** (for GPU acceleration): NVIDIA CUDA Toolkit
           11.x+ with matching driver. Set ``-DLLAMA_CUDA=ON`` to enable.

        ## Build commands

        ```bash
        cd "{src_str}"
        cmake -B build -DCMAKE_BUILD_TYPE=Release
        cmake --build build --config Release --parallel {_cpu_count()}
        ```

        ### With CUDA acceleration

        ```bash
        cd "{src_str}"
        cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CUDA=ON
        cmake --build build --config Release --parallel {_cpu_count()}
        ```

        ## Expected binary

        After a successful build, the ``llama-server`` binary should be
        located at one of:

        - ``{src_str}/build/bin/llama-server``
        - ``{src_str}/build/bin/llama-server.exe``

        ## Last failure diagnostics

        The following output was captured from the last automated build
        attempt:

        ```
        {displayed_errors}
        ```

        ## Verification

        After building, verify the binary runs:

        ```bash
        {src_str}/build/bin/llama-server --help
        ```

        If the binary is not found at the expected location, inspect the
        CMake output in the ``build/`` directory and check the project's
        own documentation for platform-specific output paths.
        """)

    dest.write_text(content, encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# Convenience helpers for the TUI integration
# ---------------------------------------------------------------------------

def can_rebuild(llama_server_path: Path) -> bool:
    """Determine whether a ``Rebuild`` action is available for a llama.cpp
    backend entry.

    A rebuild is possible if the parent directory of the registered
    ``llama-server`` binary (or the GGUF model's parent that points to a
    source tree) is a valid llama.cpp source tree.

    This is the predicate the TUI uses to conditionally show the Rebuild
    action in the Models & Backends pane.
    """

    # Walk up to the source root: the registered binary path may be nested
    # within <source>/build/bin/llama-server, so we check several ancestor
    # levels.
    candidates: list[Path] = [
        llama_server_path.parent,
        llama_server_path.parent.parent,
        llama_server_path.parent.parent.parent,
    ]
    # Also check the model file's parent for GGUF paths
    if llama_server_path.suffix == ".gguf":
        candidates.append(llama_server_path.parent.parent)

    return any(is_source_tree(c) for c in candidates if c.exists())
