#!/usr/bin/env python3
"""Single source of truth for quality checks. Run before every commit:

    python scripts/ci_check.py            # run every check (pre-commit / pre-push)
    python scripts/ci_check.py --only X   # run one check by name (used by ci.yml)

`ci.yml` references checks by name via --only, so the check list cannot drift
between local and CI: there is exactly one registry (CHECKS) below.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

_EXCLUDE_DIRS = {".venv", ".git", "__pycache__", ".audit-tmp", ".claude"}


def _run(cmd: list[str]) -> None:
    if subprocess.run(cmd).returncode != 0:
        sys.exit(1)


def _find_modules() -> list[str]:
    return [
        str(p)
        for p in Path(".").rglob("*.py")
        if not (_EXCLUDE_DIRS & set(p.parts))
        and not any(part.startswith("pytest-cache-files-") for part in p.parts)
    ]


def check_format() -> None:
    print("==> ruff format")
    _run([sys.executable, "-m", "ruff", "format", "--check", "."])


def check_lint() -> None:
    print("==> ruff lint")
    _run([sys.executable, "-m", "ruff", "check", "."])


# Captured third-party HTML kept as test fixtures: asset digests and cache-busting
# hashes in someone else's markup read as high-entropy strings, i.e. false positives
# by construction. Excluded as *files* rather than whitelisted as secret hashes — a
# baseline would hand the gate a "make it green" button and, being written in the
# host OS's path separators, would redden CI when regenerated on Windows (#389).
# `git ls-files` always emits POSIX separators, so this pattern is platform-stable.
_CAPTURED_HTML_FIXTURES = re.compile(r"^tests/fixtures/.*\.html$")


def check_secrets() -> None:
    """Block a secret from reaching a commit (#389).

    The pre-commit hook config that used to declare this gate was never on any
    execution path (`core.hooksPath` points at `.githooks`, which has only
    `pre-push`), and the baseline it carried had an empty `plugins_used`, so the
    hook exited 0 on a planted key. Both layers were silent; the gate now runs
    here, in the one registry that `pre-push` and `ci.yml` both execute.
    """
    print("==> detect-secrets")
    targets = _secrets_targets(_tracked_files())
    if not targets:
        # `detect_secrets.pre_commit_hook` returns 0 for an empty file list, so a
        # vacuous scan would look exactly like a clean one (§IV).
        print("no files to scan — refusing to report a vacuous pass")
        sys.exit(1)
    _run(_secrets_cmd(targets))


def _tracked_files() -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        print("git ls-files failed — the file set to scan is unknown")
        sys.exit(1)
    # -z: git otherwise quotes paths with spaces/non-ASCII, yielding names that
    # don't exist on disk. `stdout or ""` — #109 (stdout=None on Windows+git-bash).
    return [name for name in (proc.stdout or "").split("\0") if name]


def _secrets_targets(files: Iterable[str]) -> list[str]:
    return [name for name in files if not _CAPTURED_HTML_FIXTURES.match(name)]


def _secrets_cmd(files: list[str]) -> list[str]:
    # `-X utf8`: detect-secrets opens files with the *platform default* encoding
    # (`core/scan.py:261`) and silently skips a UnicodeDecodeError, so on Windows
    # (cp1252) every file with a Russian comment — most of this repo — went
    # unscanned while the same commit was scanned in full on Linux CI. UTF-8 mode
    # makes the two agree; without it "green locally" means nothing (§IV, #153).
    # The module, not the `detect-secrets-hook` console script: unreliable-on-PATH on
    # Windows, same reason as check_imports. No `--baseline` — see check_secrets.
    return [sys.executable, "-X", "utf8", "-m", "detect_secrets.pre_commit_hook", *files]


def check_pytest() -> None:
    print("==> pytest")
    _run([sys.executable, "-m", "pytest"])


def check_pip_audit() -> None:
    print("==> pip-audit (runtime)")
    _run([sys.executable, "-m", "pip_audit", "-r", "requirements.txt"])


# Dev-only CVEs in the RAGAS eval tree (#347) that are unreachable in our offline
# text-summarization eval and cannot be cleared by a bump today. Suppressed here (not
# in prod audit) with a tracked follow-up (#360) to remove each ID once upstream ships
# a fix that the ragas 0.4.x tree accepts:
#   PYSEC-2026-2447  diskcache  unsafe pickle deserialization — local, needs an
#       attacker-controlled cache file; our cache is self-produced. No fix released.
#   PYSEC-2026-3046  ragas      SSRF in the Multi-Modal Faithfulness module — we run
#       text-only faithfulness/answer_relevancy, never the multimodal path. No fix released.
#   PYSEC-2026-76    langchain-openai  fixed in 1.1.14, but 1.1.14 requires openai>=2.26
#       while ragas 0.4.3's `instructor` pins openai<2.0 → uninstallable together. Low-sev
#       (image-URL sizing in a multimodal helper we don't call).
_DEV_AUDIT_IGNORES = ("PYSEC-2026-2447", "PYSEC-2026-3046", "PYSEC-2026-76")


def check_pip_audit_dev() -> None:
    print("==> pip-audit (dev)")
    ignores = [arg for vuln in _DEV_AUDIT_IGNORES for arg in ("--ignore-vuln", vuln)]
    _run([sys.executable, "-m", "pip_audit", "-r", "requirements-dev.txt", *ignores])


def _parse_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([a-zA-Z0-9_.\-\[\]]+)==(.+)$", line.strip())
        if m:
            name = re.sub(r"\[.*\]", "", m.group(1)).lower().replace("-", "_")
            pins[name] = m.group(2)
    return pins


def _parse_in_top_level(path: Path) -> set[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        m = re.match(r"^([a-zA-Z0-9_.\-\[\]]+)", stripped)
        if m:
            name = re.sub(r"\[.*\]", "", m.group(1)).lower().replace("-", "_")
            names.add(name)
    return names


def check_requirements() -> None:
    print("==> requirements consistency")
    req = _parse_pins(Path("requirements.txt"))
    dev = _parse_pins(Path("requirements-dev.txt"))
    bad = [(p, req[p], dev[p]) for p in req if p in dev and req[p] != dev[p]]
    if bad:
        print("requirements version mismatch — edit .in files and run pip-compile:")
        for p, rv, dv in bad:
            print(f"  {p}: requirements.txt={rv}, requirements-dev.txt={dv}")
        sys.exit(1)

    for in_path, txt_pins in [
        (Path("requirements.in"), req),
        (Path("requirements-dev.in"), dev),
    ]:
        if not in_path.exists():
            continue
        missing = sorted(_parse_in_top_level(in_path) - set(txt_pins))
        if missing:
            print(
                f"{in_path} declares packages absent from its .txt lockfile: {', '.join(missing)}"
            )
            print(f"  Run: pip-compile {in_path}")
            sys.exit(1)


def check_mypy() -> None:
    print("==> mypy")
    modules = _find_modules()
    if modules:
        _run([sys.executable, "-m", "mypy"] + modules)
    else:
        print("No modules to type-check; skipping.")


def check_imports() -> None:
    """§II protocol-boundaries as a machine gate (#234).

    Runs the import-linter contracts in `.importlinter` via its Python API — not
    the `lint-imports` console script (unreliable-on-PATH on Windows + the #109
    subprocess-stdout pitfall). grimp is static/AST, so nothing in the package
    actually executes. `src` is put on sys.path to mirror pytest's
    pythonpath=["src"], so the gate resolves `kinozal_scraper` even without the
    editable install present.
    """
    print("==> import-linter (§II boundaries)")
    src = str(Path("src").resolve())
    if src not in sys.path:
        sys.path.insert(0, src)
    from importlinter import api

    if not api.use_cases.lint_imports(config_filename=".importlinter"):
        sys.exit(1)


# Registry — the single source of truth for the quality check set. Order is the
# run order for a full pre-commit pass. ci.yml references these names via --only.
CHECKS: dict[str, Callable[[], None]] = {
    "format": check_format,
    "lint": check_lint,
    # Before the slow gates on purpose: a leaked key must redden the run in seconds,
    # not after ~3 minutes of pytest + pip-audit.
    "secrets": check_secrets,
    "pytest": check_pytest,
    "pip-audit": check_pip_audit,
    "pip-audit-dev": check_pip_audit_dev,
    "requirements": check_requirements,
    "mypy": check_mypy,
    "imports": check_imports,
}


def run_selected(only: str | None = None) -> None:
    if only is not None:
        if only not in CHECKS:
            print(f"unknown check {only!r}; known: {', '.join(CHECKS)}", file=sys.stderr)
            sys.exit(1)
        CHECKS[only]()
        return
    for fn in CHECKS.values():
        fn()
    print("==> all checks passed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run quality checks (or one via --only).")
    parser.add_argument(
        "--only",
        metavar="NAME",
        choices=sorted(CHECKS),
        help="run a single named check; default runs all",
    )
    args = parser.parse_args()
    run_selected(args.only)


if __name__ == "__main__":
    main()
