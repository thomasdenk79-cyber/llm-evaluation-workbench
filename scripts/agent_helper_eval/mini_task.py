"""The mini coding-task fixture: prompt, contract, static safety, and the
fixed deterministic test suite used to score a real model attempt.

This is deliberately **not** a keyword/regex test (unlike the legacy
``migration_llm_bench`` heuristic scorer): the model's generated code is
executed against a small, fixed ``unittest`` suite in a subprocess, and
``deterministic_score`` is the percentage of fixed tests that actually pass.

Safety model (defense in depth, not a full sandbox):

1. The model's response must contain exactly one fenced Python code block
   (see :func:`extract_code_block`) -- anything else is treated as
   placeholder/incomplete output and is never executed.
2. Before ever executing generated code, :func:`static_safety_scan` performs
   an AST-based static vet: only a small allow-list of side-effect-free
   stdlib imports is permitted, and a list of dangerous builtins/dunder
   attribute accesses is rejected. Any finding sets
   ``unsafe_behavior_flag=True`` and the code is **never executed**.
3. Only after both checks pass is the code written to the campaign's own
   artifact/work directory and run via a ``subprocess.run(..., timeout=...)``
   with strict, bounded execution time.

This static vet is a coarse, fast heuristic -- not a container/seccomp
sandbox. It is intentionally conservative (rejects more than a full sandbox
would need to) to keep this phase's scope small while still honestly
satisfying "detect unsafe paths" without a heavy new dependency.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Optional

TASK_ID = "mini-coding-tests-v1"

#: Exact prompt sent to the model. Deliberately small, self-contained, and
#: unambiguous about the required output contract (single fenced code
#: block, exact function name/signature) so scoring never depends on
#: fuzzy/keyword matching -- only on running the fixed tests below.
PROMPT = textwrap.dedent(
    '''\
    You must implement exactly one Python function with this exact name and
    signature, and return ONLY a single fenced Python code block containing
    the complete function definition -- no explanation text before or after
    the code block, and no other code blocks.

    def normalize(records):
        """
        Given a list of dict records, each with keys:
          - "id": str (required)
          - "value": int or None (required key, may be None)
          - "tag": str, optional (may be missing, None, or empty)

        Return a new list of dict records such that:
          1. Records where "value" is None are dropped entirely.
          2. Records are deduplicated by "id": when multiple input records
             share the same "id", keep only the one with the highest
             "value"; if there is an exact tie in "value", keep whichever
             occurrence came first in the input list.
          3. Each kept record's "tag" is normalized to lowercase; if "tag"
             is missing, None, or an empty string, use "unknown" instead.
          4. Each output record is a dict with exactly the keys "id",
             "value", "tag".
          5. The output list is sorted ascending by "id".

        The input list may be empty; return [] in that case. The function
        must be pure: no imports, no I/O, no network, no file access, no
        eval/exec/open/input, no global state.
        """

    Return only the fenced code block, for example:

    ```python
    def normalize(records):
        ...
    ```
    '''
).strip()

#: Fixed, pre-written unittest suite. Written into the campaign work
#: directory alongside the extracted candidate code as ``test_solution.py``
#: and run via ``python -m unittest test_solution`` in a subprocess.
FIXED_TEST_SOURCE = textwrap.dedent(
    '''\
    import unittest

    from solution import normalize


    class NormalizeTests(unittest.TestCase):
        def test_empty_list(self):
            self.assertEqual(normalize([]), [])

        def test_drops_none_value(self):
            records = [{"id": "a", "value": None, "tag": "X"}]
            self.assertEqual(normalize(records), [])

        def test_dedup_keeps_highest_value(self):
            records = [
                {"id": "a", "value": 1, "tag": "x"},
                {"id": "a", "value": 5, "tag": "y"},
            ]
            self.assertEqual(normalize(records), [{"id": "a", "value": 5, "tag": "y"}])

        def test_dedup_tie_keeps_first_occurrence(self):
            records = [
                {"id": "a", "value": 5, "tag": "first"},
                {"id": "a", "value": 5, "tag": "second"},
            ]
            self.assertEqual(normalize(records), [{"id": "a", "value": 5, "tag": "first"}])

        def test_missing_tag_defaults_unknown(self):
            records = [{"id": "a", "value": 1}]
            self.assertEqual(normalize(records), [{"id": "a", "value": 1, "tag": "unknown"}])

        def test_empty_tag_defaults_unknown(self):
            records = [{"id": "a", "value": 1, "tag": ""}]
            self.assertEqual(normalize(records), [{"id": "a", "value": 1, "tag": "unknown"}])

        def test_tag_is_lowercased(self):
            records = [{"id": "a", "value": 1, "tag": "MixedCase"}]
            self.assertEqual(normalize(records)[0]["tag"], "mixedcase")

        def test_sorted_ascending_by_id(self):
            records = [
                {"id": "c", "value": 1, "tag": "x"},
                {"id": "a", "value": 1, "tag": "y"},
                {"id": "b", "value": 1, "tag": "z"},
            ]
            self.assertEqual([r["id"] for r in normalize(records)], ["a", "b", "c"])

        def test_multiple_ids_with_none_dropped(self):
            records = [
                {"id": "x", "value": None, "tag": "drop"},
                {"id": "y", "value": 10, "tag": "Y1"},
                {"id": "y", "value": 3, "tag": "Y2"},
            ]
            self.assertEqual(normalize(records), [{"id": "y", "value": 10, "tag": "y1"}])

        def test_output_has_only_the_three_keys(self):
            records = [{"id": "a", "value": 1, "tag": "x"}]
            result = normalize(records)
            self.assertEqual(set(result[0].keys()), {"id", "value", "tag"})


    if __name__ == "__main__":
        unittest.main()
    '''
).strip()

#: A known-correct reference solution used only by this package's own test
#: suite (never sent to/received from a model) to prove the fixed test
#: suite above is itself correct and that the executor scores a good
#: solution as 100%.
REFERENCE_SOLUTION_SOURCE = textwrap.dedent(
    '''\
    def normalize(records):
        best = {}
        for index, record in enumerate(records):
            if record.get("value") is None:
                continue
            key = record["id"]
            candidate = (record["value"], -index)
            if key not in best or candidate > best[key][0]:
                best[key] = (candidate, record)
        result = []
        for _, record in best.values():
            tag = record.get("tag") or "unknown"
            result.append({"id": record["id"], "value": record["value"], "tag": tag.lower()})
        return sorted(result, key=lambda item: item["id"])
    '''
).strip()

_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)

#: Small allow-list of side-effect-free stdlib modules. Nothing related to
#: I/O, OS, network, subprocess, threading, or introspection is permitted --
#: the fixture needs none of that.
ALLOWED_IMPORT_MODULES = frozenset({"typing", "dataclasses", "collections", "itertools", "functools", "operator", "re", "math", "statistics"})

#: Builtins/callables that must never appear as a call target in generated
#: code, regardless of whether they were imported (some are always
#: available, e.g. ``eval``/``open``).
DISALLOWED_CALL_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "open", "input", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "exit", "quit",
    "breakpoint",
})

#: Dunder attribute names that are common, legitimate protocol methods --
#: everything else matching ``__...__`` is rejected as a likely
#: sandbox-escape vector (e.g. ``__subclasses__``, ``__globals__``).
_ALLOWED_DUNDER_ATTRS = frozenset({"__init__", "__repr__", "__eq__", "__lt__", "__hash__", "__post_init__", "__len__", "__iter__"})

#: Cheap incompleteness/placeholder markers checked before ever running the
#: fixed tests -- catches an obviously stubbed-out response even if it
#: happens to be syntactically valid Python.
_PLACEHOLDER_MARKERS = ("todo", "not implemented", "notimplementederror", "fixme", "your code here", "# ...")


def extract_code_block(response_text: str) -> Optional[str]:
    """Extract the single fenced code block's contents, or ``None`` if the
    response does not contain one (treated as placeholder/incomplete
    output -- never executed)."""

    match = _CODE_BLOCK_RE.search(response_text or "")
    if not match:
        return None
    code = match.group(1).strip()
    return code or None


def static_safety_scan(source: str) -> list[str]:
    """AST-based static vet. Returns a list of human-readable findings
    (empty means "no findings" -- safe to execute). Never executes the code
    itself; a syntax error is itself reported as a finding (also unsafe to
    run, since it cannot even be parsed for further vetting)."""

    findings: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error (cannot statically vet): {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORT_MODULES:
                    findings.append(f"disallowed import: {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORT_MODULES:
                findings.append(f"disallowed import: {node.module!r}")
        elif isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in DISALLOWED_CALL_NAMES:
                findings.append(f"disallowed call: {name}()")
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr.endswith("__") and attr not in _ALLOWED_DUNDER_ATTRS:
                findings.append(f"disallowed dunder attribute access: .{attr}")
    # De-duplicate while preserving first-seen order for a stable artifact.
    seen: set[str] = set()
    unique_findings = []
    for finding in findings:
        if finding not in seen:
            seen.add(finding)
            unique_findings.append(finding)
    return unique_findings


def detect_placeholder_markers(source: str) -> list[str]:
    """Cheap lexical + structural incompleteness checks, run only on code
    that already passed :func:`static_safety_scan`."""

    findings: list[str] = []
    stripped = (source or "").strip()
    if not stripped:
        return ["empty code"]
    lowered = stripped.lower()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in lowered:
            findings.append(f"placeholder marker found: {marker!r}")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]
    has_normalize = any(
        isinstance(node, ast.FunctionDef) and node.name == "normalize" for node in ast.walk(tree)
    )
    if not has_normalize:
        findings.append("no top-level 'normalize' function defined")
    return findings


_RAN_RE = re.compile(r"Ran (\d+) tests?")
_FAILED_RE = re.compile(r"FAILED \(([^)]*)\)")


def _parse_unittest_counts(output: str) -> tuple[int, int]:
    """Parse a ``unittest`` run's stderr/stdout summary into
    ``(tests_total, tests_passed)``. Robust to ``-v`` or not, since the
    ``Ran N tests`` / ``OK`` / ``FAILED (...)`` summary lines are always
    printed regardless of verbosity."""

    ran_match = _RAN_RE.search(output)
    total = int(ran_match.group(1)) if ran_match else 0
    if total == 0:
        return 0, 0
    if re.search(r"(?<!\S)OK(?!\S)", output):
        return total, total
    failed_count = 0
    failed_match = _FAILED_RE.search(output)
    if failed_match:
        for part in failed_match.group(1).split(","):
            part = part.strip()
            count_match = re.match(r"(failures|errors)=(\d+)", part)
            if count_match:
                failed_count += int(count_match.group(2))
    return total, max(0, total - failed_count)


@dataclasses.dataclass
class MiniTaskExecutionResult:
    """Everything captured from one scoring attempt of the mini task."""

    extracted_code: Optional[str]
    placeholder_findings: list[str]
    unsafe_findings: list[str]
    executed: bool
    tests_total: int
    tests_passed: int
    deterministic_score: float
    output_placeholder_or_incomplete: bool
    unsafe_behavior_flag: bool
    test_exec_seconds: Optional[float]
    test_stdout: str
    test_stderr: str
    test_timed_out: bool

    def to_json(self) -> dict:
        return dataclasses.asdict(self)


def run_mini_task_tests(
    response_text: str,
    work_dir: Path,
    test_timeout_seconds: float = 20.0,
    python_executable: Optional[str] = None,
    clock=time.perf_counter,
) -> MiniTaskExecutionResult:
    """Score one model response against the fixed deterministic test suite.

    Never executes code that failed :func:`static_safety_scan`. Writes the
    extracted candidate code and the fixed test file into ``work_dir``
    (expected to be a campaign-scoped artifact/work directory, never a
    shared/system location) and runs the tests via
    ``python -m unittest test_solution`` with a strict ``timeout``.
    """

    extracted = extract_code_block(response_text)
    if extracted is None:
        return MiniTaskExecutionResult(
            extracted_code=None,
            placeholder_findings=["no fenced Python code block found in response"],
            unsafe_findings=[],
            executed=False,
            tests_total=0,
            tests_passed=0,
            deterministic_score=0.0,
            output_placeholder_or_incomplete=True,
            unsafe_behavior_flag=False,
            test_exec_seconds=None,
            test_stdout="",
            test_stderr="",
            test_timed_out=False,
        )

    unsafe_findings = static_safety_scan(extracted)
    if unsafe_findings:
        return MiniTaskExecutionResult(
            extracted_code=extracted,
            placeholder_findings=[],
            unsafe_findings=unsafe_findings,
            executed=False,
            tests_total=0,
            tests_passed=0,
            deterministic_score=0.0,
            output_placeholder_or_incomplete=False,
            unsafe_behavior_flag=True,
            test_exec_seconds=None,
            test_stdout="",
            test_stderr="",
            test_timed_out=False,
        )

    placeholder_findings = detect_placeholder_markers(extracted)
    if placeholder_findings:
        return MiniTaskExecutionResult(
            extracted_code=extracted,
            placeholder_findings=placeholder_findings,
            unsafe_findings=[],
            executed=False,
            tests_total=0,
            tests_passed=0,
            deterministic_score=0.0,
            output_placeholder_or_incomplete=True,
            unsafe_behavior_flag=False,
            test_exec_seconds=None,
            test_stdout="",
            test_stderr="",
            test_timed_out=False,
        )

    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "solution.py").write_text(extracted, encoding="utf-8")
    (work_dir / "test_solution.py").write_text(FIXED_TEST_SOURCE, encoding="utf-8")

    start = clock()
    timed_out = False
    try:
        completed = subprocess.run(
            [python_executable or sys.executable, "-m", "unittest", "test_solution", "-v"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=test_timeout_seconds,
        )
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, (bytes, bytearray)) else (exc.stdout or "")
        stderr_prefix = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")
        stderr = f"{stderr_prefix}\ntest execution timed out after {test_timeout_seconds}s"
    test_exec_seconds = round(clock() - start, 3)

    if timed_out:
        tests_total, tests_passed = 0, 0
    else:
        # unittest's own summary lines land on stderr by default.
        tests_total, tests_passed = _parse_unittest_counts(stderr or stdout)
    deterministic_score = round(100.0 * tests_passed / tests_total, 2) if tests_total > 0 else 0.0

    return MiniTaskExecutionResult(
        extracted_code=extracted,
        placeholder_findings=[],
        unsafe_findings=[],
        executed=not timed_out,
        tests_total=tests_total,
        tests_passed=tests_passed,
        deterministic_score=deterministic_score,
        output_placeholder_or_incomplete=False,
        unsafe_behavior_flag=False,
        test_exec_seconds=test_exec_seconds,
        test_stdout=stdout,
        test_stderr=stderr,
        test_timed_out=timed_out,
    )
