"""Subprocess-output guard: two rules—encoding (#364) and defaults (#410).

`subprocess.run(..., text=True, capture_output=True)` **without** `encoding` on Windows
decodes child output with the ANSI code page (cp1252). With Cyrillic, this produces
`UnicodeDecodeError` in the stream reader; the reader dies, the buffer remains empty, and
`Popen._communicate` returns `stdout[0] if stdout else None`, i.e. **`stdout is None`**.
Thus #109 (“may return `stdout=None` despite `text=True`”) is not a separate Windows+git-bash
phenomenon but a symptom; repository-wide `(result.stdout or "")` were masking workarounds and
were **removed in #410**—the second rule here (`find_output_defaults`) now prohibits them. The
`None` check lives where output is read: in an `_run` seam when present, otherwise at the call site;
it cannot be centralized (see ledger), only the invariant is centralized.

Live case on 28.07.2026: a PostToolUse hook reported “ruff found issues,” but **the finding text
was lost with the dead stream**. A visibility tool went blind—§IV inside what §IV provides.

**Why a guard instead of “add the argument.”** Seven of nine call sites remembered the flag and two
did not; this is the third pass over the same class (#109 → #125 → #364). Per `principles.md`
(§Scripts over instructions), correctness depending on author memory is secured by an exit code.

**The guard boundaries are real, not excuses** (full rationale is in the
`docs/architecture/coverage-gaps.md`):

- **The child half is not covered.** Parent `encoding` is only half the contract: child Python writes
  to a pipe in its ANSI encoding unless run with `-X utf8`/`PYTHONUTF8` (the repository knows this—
  `ci_check.py` calls detect-secrets that way). Such a call-site guard marks green although it is broken.
- **No standard rule exists.** Neither ruff, bandit, nor pylint has a `subprocess` encoding rule
  (ruff `PLW1514` concerns only `open()`). This records that precedent #237 (“standard tools > bicycles”)
  must not be relitigated.
- **Alias import is closed, not documented**: `test_subprocess_helpers_are_not_imported_by_name`
  prohibits `from subprocess import run`, otherwise name-based analysis is bypassed in three letters.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
# Three directories are deliberate scope, not an omission: all tracked Python is there today
# (verified), while scanning from root is unsafe due to sibling venvs (`.venv-phoenix`, etc.)
# that caused trouble in #345. Expand when tracked `.py` appears outside them, with an explicit list.
_SCANNED_DIRS = ("scripts", "src", "tests")

# subprocess functions that launch a process and can capture its output.
_SPAWNERS = frozenset({"run", "check_output", "Popen"})

# kwargs that put a stream into TEXT mode, thus enabling parent-side decoding. `errors` also
# does so: according to subprocess docs it implies text mode and uses local encoding without `encoding`.
_TEXT_MODE_KWARGS = frozenset({"text", "universal_newlines", "encoding", "errors"})

# `CompletedProcess` attributes whose defaults replace capture failure with an empty value (#410).
# The invariant’s home is here: a shared seam helper is impossible (repository root is not on `sys.path`
# for `python scripts/foo.py`; see `scripts/issue_branch.py`), while unlike a helper, the guard also
# prevents adding the default again.
_OUTPUT_ATTRS = frozenset({"stdout", "stderr"})


def find_output_defaults(source: str, label: str) -> list[str]:
    """`<something>.stdout or <default>`—replaces capture failure with an empty value.

    The rule is deliberately narrow: it checks an `.stdout`/`.stderr` **attribute** left of
    `or`, not `or` generally. A broad rule would flag legitimate defaults
    (`os.environ.get(...) or ""`) and need weakening—while the assertion cannot be silenced; it has no `noqa`."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)):
            continue
        head = node.values[0]
        if isinstance(head, ast.Attribute) and head.attr in _OUTPUT_ATTRS:
            violations.append(f"{label}:{node.lineno}: `.{head.attr} or ...` default")
    return violations


def _kwargs(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}


def _is_literal_false(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _is_pipe(node: ast.expr) -> bool:
    # `subprocess.PIPE` or bare `PIPE`—the latter exists only with an alias import,
    # separately prohibited below.
    return (isinstance(node, ast.Attribute) and node.attr == "PIPE") or (
        isinstance(node, ast.Name) and node.id == "PIPE"
    )


def _is_explicit_no_capture(node: ast.expr) -> bool:
    """`stdout=None` / `stdout=subprocess.DEVNULL`—explicitly decline capture."""
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    return isinstance(node, ast.Attribute) and node.attr == "DEVNULL"


def _captures_output(func_name: str, kwargs: dict[str, ast.expr]) -> bool:
    """Whether a call captures child output (and therefore decodes it).

    Everywhere use **fail closed**: treat an unknown (non-literal) value as capture.
    `capture_output=capture` exists in the repository today (`new_branch.py:29`); treating
    a variable as “does not capture” would skip a broken call site—a falsely green guard is worse than none.

    `check_output` captures stdout **by definition** (it returns it) and does not accept
    `capture_output`; without this branch it would be in `_SPAWNERS` but could never be flagged:
    falsely green in the precise form this guard is written against."""
    if func_name == "check_output":
        return True
    capture = kwargs.get("capture_output")
    if capture is not None and not _is_literal_false(capture):
        return True
    return any(
        _is_pipe(kwargs[stream]) or not _is_explicit_no_capture(kwargs[stream])
        for stream in ("stdout", "stderr")
        if stream in kwargs
    )


def find_violations(source: str, label: str) -> list[str]:
    """`subprocess` calls that decode child output without explicit encoding.

    Violation := captures output AND is in text mode AND lacks `encoding`. Binary mode
    (no text kwargs) is not a violation: the caller intentionally decodes there."""
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Check only attribute name, not receiver: `runner.run(...)` is flagged too. This is
        # deliberate—the error direction is safe (fail closed), while parsing receiver requires
        # name tracing. There are no false positives today; if one appears it cannot be silenced
        # (the assertion has no `noqa`), deliberately: fix the guard rather than suppress it.
        if not (isinstance(func, ast.Attribute) and func.attr in _SPAWNERS):
            continue
        kwargs = _kwargs(node)
        if not _captures_output(func.attr, kwargs):
            continue
        text_mode = [
            name
            for name in _TEXT_MODE_KWARGS & kwargs.keys()
            if not _is_literal_false(kwargs[name])
        ]
        if not text_mode:
            continue
        if "encoding" in kwargs:
            continue
        violations.append(f"{label}:{node.lineno}: {func.attr}(...) without encoding")
    return violations


def _scanned_files() -> list[Path]:
    return sorted(
        path
        for directory in _SCANNED_DIRS
        for path in (_REPO / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _imports_subprocess_by_name(source: str) -> list[str]:
    """Names imported directly from `subprocess`—they hide calls from analysis.

    Close the import rather than trace aliases: a boundary removed in three lines does not
    merit documentation prose (`principles.md`)."""
    return [
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
        for alias in node.names
        if alias.name in _SPAWNERS
    ]


class TestAnalyzer:
    """Analyzer units using inline source.

    Without them the guard is falsely green by construction: an analyzer unconditionally returning
    `[]` would pass the repository scan (there are no violations after fixing) and the false-positive
    check. The suite would then not distinguish a working guard from a stub."""

    def test_capturing_call_without_encoding_is_flagged(self) -> None:
        source = "import subprocess\nsubprocess.run(cmd, text=True, capture_output=True)\n"
        violations = find_violations(source, "sample.py")
        assert violations, "a capturing text-mode call without `encoding` must be flagged"
        assert "sample.py:2" in violations[0], (
            f"the violation must name file:line so it is actionable, got {violations[0]!r}"
        )

    def test_non_capturing_call_is_not_flagged(self) -> None:
        # `scripts/ci_check.py:24` — output goes to the console; the parent does not decode it.
        # A false positive here is especially costly: a pytest assertion has no `noqa`,
        # and nothing can suppress it — the guard must be precise, not suppressible.
        source = "import subprocess\nsubprocess.run(cmd)\n"
        assert find_violations(source, "sample.py") == []

    def test_non_literal_capture_flag_counts_as_capturing(self) -> None:
        # This form exists today: `scripts/new_branch.py:29` passes a variable.
        # Treat everything except the literal False as capturing.
        source = "import subprocess\nsubprocess.run(cmd, text=True, capture_output=capture)\n"
        assert find_violations(source, "sample.py")

    def test_errors_kwarg_alone_counts_as_text_mode(self) -> None:
        source = "import subprocess\nsubprocess.run(cmd, errors='replace', capture_output=True)\n"
        assert find_violations(source, "sample.py")

    def test_pipe_stdout_counts_as_capturing(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.Popen(cmd, stdout=subprocess.PIPE, universal_newlines=True)\n"
        )
        assert find_violations(source, "sample.py")

    def test_check_output_captures_implicitly(self) -> None:
        # `check_output` does not accept `capture_output` — it returns stdout. Without
        # a separate branch, it would be listed among tracked calls but could never be
        # flagged.
        source = "import subprocess\nsubprocess.check_output(cmd, text=True)\n"
        assert find_violations(source, "sample.py")

    def test_unknown_stdout_value_counts_as_capturing(self) -> None:
        # Fail closed, symmetrically with `capture_output=<variable>`: an unknown
        # value cannot be assumed not to capture output.
        source = "import subprocess\nsubprocess.run(cmd, stdout=sink, text=True)\n"
        assert find_violations(source, "sample.py")

    def test_explicit_devnull_is_not_capturing(self) -> None:
        source = "import subprocess\nsubprocess.run(cmd, stdout=subprocess.DEVNULL, text=True)\n"
        assert find_violations(source, "sample.py") == []

    def test_explicit_binary_mode_is_clean(self) -> None:
        # `text=False` is explicit binary mode: the caller deliberately decodes it.
        source = "import subprocess\nsubprocess.run(cmd, capture_output=True, text=False)\n"
        assert find_violations(source, "sample.py") == []

    def test_explicit_encoding_is_clean(self) -> None:
        source = (
            "import subprocess\n"
            "subprocess.run(cmd, text=True, capture_output=True, encoding='utf-8')\n"
        )
        assert find_violations(source, "sample.py") == []


class TestOutputDefaultAnalyzer:
    """Unit tests for the rule that prohibits defaults on subprocess output (#410).

    While the cause of `stdout=None` was unknown (#109), `or ""` looked like a
    safeguard. #364 showed it was a symptom of a reader stream that died during
    decoding and fixed the cause — after which the default replaced a **real
    capture failure** with an empty value, including in scripts that are gates
    themselves (`check_red`, `validate_issue_sections`, secret scan)."""

    def test_output_default_is_flagged(self) -> None:
        source = 'x = result.stdout or ""\n'
        violations = find_output_defaults(source, "sample.py")
        assert violations, "a default on captured output must be flagged"
        assert "sample.py:1" in violations[0], (
            f"the violation must name file:line so it is actionable, got {violations[0]!r}"
        )

    def test_non_empty_literal_default_is_flagged(self) -> None:
        # A form the first inventory version missed completely: grep searched for
        # `or ""`, while `or "{}"` / `or "[]"` occur in open_pr and verify_pr_link.
        source = 'data = json.loads(result.stdout or "[]")\n'
        assert find_output_defaults(source, "sample.py")

    def test_stderr_default_is_flagged(self) -> None:
        source = "msg = (proc.stderr or '').strip()\n"
        assert find_output_defaults(source, "sample.py")

    def test_unrelated_or_default_is_not_flagged(self) -> None:
        # The rule concerns subprocess output, not `or` generally — otherwise the
        # guard would flag legitimate defaults and would have to be weakened (nothing can suppress it).
        source = 'name = os.environ.get("X") or ""\nvalue = payload.title or "n/a"\n'
        assert find_output_defaults(source, "sample.py") == []


class TestRepoIsClean:
    def test_scan_covers_known_files(self) -> None:
        """§IV: "the set is non-empty" would pass even if the glob collapsed to one file.
        Therefore assert specific files — both contained a violation at the time of #364."""
        scanned = {path.relative_to(_REPO).as_posix() for path in _scanned_files()}
        for expected in (
            "scripts/hooks.py",
            "tests/test_github_trending_pipeline.py",
            "src/kinozal_scraper/alerting.py",
        ):
            assert expected in scanned, f"{expected} dropped out of the scanned set"

    def test_every_capturing_call_declares_utf8(self) -> None:
        violations = [
            violation
            for path in _scanned_files()
            for violation in find_violations(
                path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix()
            )
        ]
        assert not violations, (
            "subprocess call(s) decode the child's output with the OS code page — on "
            "Windows that loses the text on any Cyrillic byte (#364):\n  " + "\n  ".join(violations)
        )

    def test_no_output_defaults(self) -> None:
        violations = [
            violation
            for path in _scanned_files()
            for violation in find_output_defaults(
                path.read_text(encoding="utf-8"), path.relative_to(_REPO).as_posix()
            )
        ]
        assert not violations, (
            "default on captured subprocess output — since #364 closed the cause, "
            "`None` means the capture itself failed, so a default silently replaces a "
            "real failure with emptiness (#410):\n  " + "\n  ".join(violations)
        )

    def test_subprocess_helpers_are_not_imported_by_name(self) -> None:
        offenders = [
            f"{path.relative_to(_REPO).as_posix()}: {name}"
            for path in _scanned_files()
            for name in _imports_subprocess_by_name(path.read_text(encoding="utf-8"))
        ]
        assert not offenders, (
            "importing subprocess helpers by name hides the call from this guard — "
            f"use `subprocess.run(...)` instead: {offenders}"
        )
