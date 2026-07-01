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
from collections.abc import Callable
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


def check_pytest() -> None:
    print("==> pytest")
    _run([sys.executable, "-m", "pytest"])


def check_headers() -> None:
    print("==> check_headers")
    _run([sys.executable, "scripts/check_headers.py"])


def check_pip_audit() -> None:
    print("==> pip-audit (runtime)")
    _run([sys.executable, "-m", "pip_audit", "-r", "requirements.txt"])


def check_pip_audit_dev() -> None:
    print("==> pip-audit (dev)")
    _run([sys.executable, "-m", "pip_audit", "-r", "requirements-dev.txt"])


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
    "pytest": check_pytest,
    "headers": check_headers,
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
