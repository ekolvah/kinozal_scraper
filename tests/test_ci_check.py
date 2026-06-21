from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci_check import CHECKS, _find_modules, _run, run_selected

_CI_YML = Path(".github/workflows/ci.yml")
_ONLY_RE = re.compile(r"scripts/ci_check\.py\s+--only\s+(\S+)")


def _ci_yml_check_names() -> set[str]:
    """Names passed to `ci_check.py --only X` in the ci.yml quality job."""
    spec = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    steps = spec["jobs"]["quality"]["steps"]
    names: set[str] = set()
    for step in steps:
        run = step.get("run", "")
        names.update(_ONLY_RE.findall(run))
    return names


class TestStepParity:
    """The core defect (#153): ci.yml duplicated the check list by hand and drifted —
    coverage-doc and the `.in`-without-pin check were missing in CI. After the registry
    refactor, ci.yml references check *names* only, so parity is enforceable."""

    def test_ci_yml_runs_every_registered_check(self) -> None:
        assert _ci_yml_check_names() == set(CHECKS), (
            "ci.yml --only steps must cover exactly the ci_check registry — "
            "any divergence is the drift this issue fixes"
        )


class TestFindModules:
    def test_excludes_audit_tmp_and_pytest_cache(self) -> None:
        modules = set(_find_modules())
        assert (
            "scripts/ci_check.py".replace("/", "\\") in modules or "scripts/ci_check.py" in modules
        )
        assert not any(".audit-tmp" in m for m in modules)
        assert not any("pytest-cache-files-" in m for m in modules)


class TestRunner:
    def test_unknown_check_name_exits_nonzero(self) -> None:
        # Fail-fast on a typo'd --only name (so a bad ci.yml reference is loud, not silent).
        with pytest.raises(SystemExit) as exc:
            run_selected("definitely-not-a-real-check")
        assert exc.value.code != 0

    def test_nonzero_step_exits_nonzero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A failing subprocess must propagate as sys.exit(1), not be swallowed.
        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc:
            _run(["any-command"])
        assert exc.value.code != 0
