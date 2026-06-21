from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, cast

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


def _ci_yml_on() -> dict[str, Any]:
    """The ci.yml `on:` trigger block. PyYAML parses bare `on:` as the boolean
    key `True` (YAML 1.1), not the string "on" — so the block lives at spec[True]."""
    spec = yaml.safe_load(_CI_YML.read_text(encoding="utf-8"))
    return cast(dict[str, Any], spec[True])


class TestStepParity:
    """The core defect (#153): ci.yml duplicated the check list by hand and drifted —
    coverage-doc and the `.in`-without-pin check were missing in CI. After the registry
    refactor, ci.yml references check *names* only, so parity is enforceable."""

    def test_ci_yml_runs_every_registered_check(self) -> None:
        assert _ci_yml_check_names() == set(CHECKS), (
            "ci.yml --only steps must cover exactly the ci_check registry — "
            "any divergence is the drift this issue fixes"
        )


class TestCITriggers:
    """anti-double-run (#206): ci.yml had `push: [main, "issue-*"]` alongside
    `pull_request`, so every push to an issue-branch with an open PR ran the
    `quality` job twice (push + pull_request events report the same context).
    The fix drops `issue-*` from push — PRs are covered by `pull_request`, and
    the required status check is the bare context `quality` (event-agnostic), so
    nothing is orphaned. Do NOT "fix" CI-not-running-on-a-branch by re-adding
    `issue-*` here — that resurrects the duplicate run."""

    def test_push_trigger_excludes_issue_branches(self) -> None:
        branches = _ci_yml_on()["push"]["branches"]
        assert "main" in branches, "push must still cover main (post-merge gate)"
        assert "issue-*" not in branches, (
            "issue-* in push re-introduces the double quality run — PR branches "
            "are already covered by the pull_request trigger (#206)"
        )

    def test_pull_request_trigger_present(self) -> None:
        # Presence check, not truthiness: `pull_request:` is empty → value is None.
        # The dedup relies on pull_request being the sole coverage of issue-branches.
        assert "pull_request" in _ci_yml_on()


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
