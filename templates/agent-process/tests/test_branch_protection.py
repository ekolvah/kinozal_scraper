"""Guards around required status checks for the `main` branch.

Actual enforcement lives in GitHub configuration **outside the repository**: the `pr-link`
workflow once existed and was red in the UI, yet blocked nothing because it was absent from
`required_status_checks.contexts`. The defect class is presence ≠ correctness.

There are three independent layers here:

* `TestDriftDetection` / `TestProtectionFetch`—the pure half of `scripts/check_branch_protection.py`
  (compare “declared ↔ actual” and distinguish “drift” from “tool failure,” §IV). A CI network run is
  unavailable: `GITHUB_TOKEN` lacks `administration` scope, and classic branch protection is invisible
  through the ruleset endpoint—coverage-gaps-quality-gates.md entry AD.
* `TestDeclarationMatchesWorkflows`—the offline half: script declaration is compared with real workflows.
  It compares the **effective check-run name** (`name:` of the job, otherwise its key): renaming a job would
  otherwise leave a required context permanently “Expected,” and `enforce_admins: true` would lock merging,
  including the fixing PR.
* `TestPrePushHook`—behavioral hook tests: it executes through `bash` in a temporary tree with call-counting
  stubs. Text grep is useless here—the original defect was `||` semantics, not line presence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.check_branch_protection import (
    NOT_REQUIRED,
    REQUIRED_CONTEXTS,
    contexts_from_protection,
    declaration_problems,
    fetch_protection,
    load_workflows,
    protection_drift,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"
_HOOK = _REPO_ROOT / ".githooks" / "pre-push"


class TestDriftDetection:
    def test_controller_gate_is_not_a_required_context(self) -> None:
        assert REQUIRED_CONTEXTS == ("quality", "pr-link", "agent-review")

    """Чистое сравнение объявленного состава контекстов с фактическим."""

    def test_missing_required_context_is_drift(self) -> None:
        """The exact defect: `pr-link` is declared but absent from GitHub."""
        missing, unexpected = protection_drift(("quality",), ("quality", "pr-link"))
        assert missing == ["pr-link"]
        assert unexpected == []

    def test_extra_context_in_github_is_drift(self) -> None:
        """An undeclared context is also drift: the canonical set lives in the repository."""
        missing, unexpected = protection_drift(("quality", "review"), ("quality",))
        assert missing == []
        assert unexpected == ["review"]

    def test_exact_match_is_clean(self) -> None:
        """A match yields an empty verdict in both directions."""
        assert protection_drift(("quality", "pr-link"), ("quality", "pr-link")) == ([], [])

    def test_order_does_not_matter(self) -> None:
        """GitHub does not guarantee `checks` order; comparison uses a set."""
        assert protection_drift(("pr-link", "quality"), ("quality", "pr-link")) == ([], [])


class TestAllowDrift:
    """A gate that regularly demands bypassing teaches bypassing.

    The maintainer removes `agent-review` from required to merge a PR whose review
    is red by construction; the drift detector then blocks every push to unrelated
    feature branches, and the only escape is `--no-verify`, which swallows
    `ci_check` too. `--allow-drift "<reason>"` makes the intentional temporary state
    expressible instead."""

    @staticmethod
    def _patch_actual(monkeypatch: pytest.MonkeyPatch, contexts: list[str]) -> None:
        import scripts.check_branch_protection as guard

        monkeypatch.setattr(
            guard,
            "fetch_protection",
            lambda: {"required_status_checks": {"checks": [{"context": c} for c in contexts]}},
        )

    def test_drift_without_the_flag_still_exits_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.check_branch_protection as guard

        self._patch_actual(monkeypatch, ["quality", "pr-link"])
        with pytest.raises(SystemExit) as exc:
            guard.main([])
        assert exc.value.code == 1

    def test_allow_drift_exits_zero_and_prints_the_reason(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import scripts.check_branch_protection as guard

        self._patch_actual(monkeypatch, ["quality", "pr-link"])
        guard.main(["--allow-drift", f"agent-review снят для мержа #{458}"])
        out = capsys.readouterr().out
        assert f"#{458}" in out, "the stated reason must reach the push output"

    def test_allow_drift_requires_a_reason(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.check_branch_protection as guard

        self._patch_actual(monkeypatch, ["quality", "pr-link"])
        with pytest.raises(SystemExit) as exc:
            guard.main(["--allow-drift"])
        assert exc.value.code == 2

    def test_no_drift_with_the_flag_is_still_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.check_branch_protection as guard

        self._patch_actual(monkeypatch, list(REQUIRED_CONTEXTS))
        guard.main(["--allow-drift", "не нужен"])


class TestProtectionFetch:
    """Network layer: tool failure (exit 2) is not masked as a verdict (exit 0/1)."""

    @staticmethod
    def _fake_run(
        monkeypatch: pytest.MonkeyPatch, *, stdout: str | None, stderr: str | None, rc: int
    ) -> None:
        """Replace `subprocess.run` with a fixed `gh api` result."""

        def _run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["gh"], returncode=rc, stdout=stdout, stderr=stderr
            )

        monkeypatch.setattr(subprocess, "run", _run)

    def test_gh_failure_exits_2_not_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nonzero `gh` rc is infrastructure failure, not “no drift” or “drift.”"""
        self._fake_run(monkeypatch, stdout="", stderr="HTTP 403", rc=1)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_none_stdout_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Broken output capture is also infrastructure failure, not an empty response."""
        self._fake_run(monkeypatch, stdout=None, stderr=None, rc=0)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_malformed_json_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful rc but unparseable body is also tool failure."""
        self._fake_run(monkeypatch, stdout="<html>proxy</html>", stderr="", rc=0)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_gh_timeout_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hung `gh` must not leave pre-push hanging without output."""

        def _hang(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

        monkeypatch.setattr(subprocess, "run", _hang)
        with pytest.raises(SystemExit) as exc:
            fetch_protection()
        assert exc.value.code == 2

    def test_absent_required_status_checks_is_drift_not_infra(self) -> None:
        """Removed protection is the most likely real scenario: drift, not failure."""
        assert contexts_from_protection({}) == ()
        assert contexts_from_protection({"required_status_checks": {}}) == ()

    def test_null_required_status_checks_is_drift_not_crash(self) -> None:
        """Explicit JSON `null` differs from a missing key; a traceback would yield code 1."""
        assert contexts_from_protection({"required_status_checks": None}) == ()
        assert contexts_from_protection({"required_status_checks": {"checks": None}}) == ()

    def test_contexts_are_read_from_the_checks_field(self) -> None:
        """Read the non-deprecated `checks[*].context` form, the same one written."""
        payload = {
            "required_status_checks": {
                "strict": True,
                "checks": [{"context": "quality", "app_id": 15368}, {"context": "pr-link"}],
            }
        }
        assert contexts_from_protection(payload) == ("quality", "pr-link")

    def test_actual_contexts_are_printed_on_success(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Actual list is always printed—the reproducible way to see it."""
        from scripts.check_branch_protection import main

        payload = {
            "required_status_checks": {"checks": [{"context": c} for c in REQUIRED_CONTEXTS]}
        }
        self._fake_run(monkeypatch, stdout=json.dumps(payload), stderr="", rc=0)
        main([])
        printed = capsys.readouterr().out
        for context in REQUIRED_CONTEXTS:
            assert context in printed


@pytest.mark.skipif(
    not _WORKFLOWS.is_dir(),
    reason="the generated project does not include GitHub workflow definitions",
)
class TestDeclarationMatchesWorkflows:
    """Offline half: the script declaration does not diverge from repository workflows."""

    def test_every_declared_context_is_a_real_job(self) -> None:
        """Real workflows + real declaration—no divergence.

        Non-emptiness of the declaration is checked separately: an empty list would
        vacuously satisfy everything else and silently guarantee nothing (§IV).
        """
        assert REQUIRED_CONTEXTS, "пустое объявление молча не гарантирует ничего"
        assert (
            declaration_problems(load_workflows(_WORKFLOWS), REQUIRED_CONTEXTS, NOT_REQUIRED) == []
        )

    def test_every_pull_request_job_is_declared_or_excluded(self) -> None:
        """A new PR job must be either required or excluded with a reason."""
        workflows = {
            "new.yml": {"on": {"pull_request": None}, "jobs": {"fresh-gate": {}}},
        }
        problems = declaration_problems(workflows, ("fresh-gate",), {})
        assert problems == []

        problems = declaration_problems(workflows, (), {})
        assert len(problems) == 1
        assert "fresh-gate" in problems[0]

    def test_excluded_job_needs_a_reason(self) -> None:
        """An exclusion without a reason is a forgotten decision, not an accepted one."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"advisory": {}}}}
        assert declaration_problems(workflows, (), {"advisory": "не блокирует fork-PR"}) == []
        assert declaration_problems(workflows, (), {"advisory": ""}) != []

    def test_declared_context_without_a_job_is_a_problem(self) -> None:
        """A declared context without a job is perpetual “Expected”; merge is locked forever."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}}}
        problems = declaration_problems(workflows, ("gate", "ghost"), {})
        assert len(problems) == 1
        assert "ghost" in problems[0]

    def test_job_name_override_defines_the_context(self) -> None:
        """The context is a job’s `name:` when set, not its job key."""
        workflows = {
            "new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {"name": "Gate (strict)"}}}
        }
        assert declaration_problems(workflows, ("Gate (strict)",), {}) == []
        assert declaration_problems(workflows, ("gate",), {}) != []

    def test_matrix_job_cannot_be_declared_as_bare_context(self) -> None:
        """A matrix expands context to `job (value)`—the bare name never reports."""
        workflows = {
            "new.yml": {
                "on": {"pull_request": None},
                "jobs": {"gate": {"strategy": {"matrix": {"python": ["3.12", "3.13"]}}}},
            }
        }
        problems = declaration_problems(workflows, ("gate",), {})
        assert len(problems) == 1
        assert "matrix" in problems[0].lower()

    def test_yaml_boolean_on_key_is_understood(self) -> None:
        """YAML 1.1 reads bare `on:` as `True`—the job must not be lost because of it."""
        workflows = {"new.yml": {True: {"pull_request": None}, "jobs": {"gate": {}}}}
        assert declaration_problems(workflows, (), {}) != []

    def test_trigger_filter_on_declared_context_is_a_problem(self) -> None:
        """`paths`/`branches` on a trigger means the context will not report on every PR."""
        for filter_key in ("paths", "paths-ignore", "branches", "branches-ignore"):
            workflows = {
                "new.yml": {
                    "on": {"pull_request": {filter_key: ["src/**"]}},
                    "jobs": {"gate": {}},
                }
            }
            problems = declaration_problems(workflows, ("gate",), {})
            assert len(problems) == 1, filter_key
            assert filter_key in problems[0]

    def test_unfiltered_trigger_is_clean(self) -> None:
        """`types:` is not a filter—it narrows events, not the PR set."""
        workflows = {
            "new.yml": {
                "on": {"pull_request": {"types": ["opened", "edited"]}},
                "jobs": {"gate": {}},
            }
        }
        assert declaration_problems(workflows, ("gate",), {}) == []

    def test_stale_exclusion_is_a_problem(self) -> None:
        """An exclusion outliving its removed job silently means nothing."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}}}
        problems = declaration_problems(workflows, ("gate",), {"ghost": "причина есть"})
        assert len(problems) == 1
        assert "ghost" in problems[0]

    def test_context_in_both_lists_is_a_problem(self) -> None:
        """A context both required and excluded is contradiction, not clarification."""
        workflows = {"new.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}}}
        problems = declaration_problems(workflows, ("gate",), {"gate": "причина есть"})
        assert len(problems) == 1
        assert "gate" in problems[0]

    def test_duplicate_effective_job_name_is_a_problem(self) -> None:
        """One check-run name for two jobs is ambiguity, not a detail."""
        workflows = {
            "a.yml": {"on": {"pull_request": None}, "jobs": {"gate": {}}},
            "b.yml": {"on": {"pull_request": None}, "jobs": {"other": {"name": "gate"}}},
        }
        problems = declaration_problems(workflows, ("gate",), {})
        assert any("более чем одному" in p for p in problems)

    def test_yaml_extension_workflow_is_loaded(self, tmp_path: Path) -> None:
        """GitHub accepts `.yaml`; a guard blind to it would be vacuously green."""
        (tmp_path / "a.yml").write_text(
            "on:\n  pull_request:\njobs:\n  one: {}\n", encoding="utf-8"
        )
        (tmp_path / "b.yaml").write_text(
            "on:\n  pull_request:\njobs:\n  two: {}\n", encoding="utf-8"
        )
        loaded = load_workflows(tmp_path)
        assert set(loaded) == {"a.yml", "b.yaml"}
        problems = declaration_problems(loaded, (), {})
        assert len(problems) == 2

    def test_empty_workflow_file_does_not_crash(self, tmp_path: Path) -> None:
        """`safe_load` returns an empty file as `None`—the wrapper must survive that."""
        (tmp_path / "empty.yml").write_text("", encoding="utf-8")
        assert load_workflows(tmp_path) == {"empty.yml": {}}
        assert declaration_problems(load_workflows(tmp_path), (), {}) == []

    def test_non_pull_request_workflow_is_ignored(self) -> None:
        """A cron job cannot be a required PR context, so it need not be declared."""
        workflows = {"cron.yml": {"on": {"schedule": [{"cron": "0 5 * * *"}]}, "jobs": {"run": {}}}}
        assert declaration_problems(workflows, (), {}) == []


@pytest.mark.skipif(
    not _HOOK.is_file(),
    reason="the generated project does not include a pre-push hook",
)
class TestPrePushHook:
    """The hook truly executes: stubs count calls, order, and stderr."""

    _STUB = (
        "#!/usr/bin/env bash\n"
        'printf "%s|%s|GIT_DIR=%s\\n" "$STUB_ID" "$*" "${GIT_DIR-unset}" '
        '>> "$PWD/hook-calls.log"\n'
        'if [ "$1" = "-c" ]; then exit 0; fi\n'
        'name=$(basename "$1")\n'
        'echo "stderr-from-$name" >&2\n'
        'if [ -f "rc-$name" ]; then exit "$(cat "rc-$name")"; fi\n'
        "exit 0\n"
    )

    @staticmethod
    def _stub(path: Path, stub_id: str) -> None:
        """Place an executable interpreter stub identifying itself with `stub_id`."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            TestPrePushHook._STUB.replace("$STUB_ID", stub_id), encoding="utf-8", newline="\n"
        )
        path.chmod(0o755)

    @staticmethod
    def _bash() -> str:
        """Absolute path to `bash`—the test below must restrict PATH without losing the shell."""
        bash = shutil.which("bash")
        assert bash, "bash недоступен: хук нечем исполнить, гард выродился бы в grep"
        return bash

    @classmethod
    def _run(
        cls, tmp_path: Path, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Execute the real `.githooks/pre-push` in a temporary tree."""
        subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
        return subprocess.run(
            [cls._bash(), _HOOK.as_posix()],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    @staticmethod
    def _gate_calls(tmp_path: Path) -> list[str]:
        """Gate invocations (the interpreter `-c` probe is excluded from the log)."""
        log = tmp_path / "hook-calls.log"
        if not log.exists():
            return []
        return [line for line in log.read_text(encoding="utf-8").splitlines() if ".py" in line]

    def test_runs_each_gate_exactly_once(self, tmp_path: Path) -> None:
        """Both gates run once—no repeats from the former `||` chain."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        result = self._run(tmp_path)
        calls = self._gate_calls(tmp_path)
        assert result.returncode == 0, result.stderr
        assert calls, "no gate invocations logged"
        assert sum("check_branch_protection.py" in c for c in calls) == 1
        assert sum("ci_check.py" in c for c in calls) == 1

    def test_clears_repository_local_git_environment(self, tmp_path: Path) -> None:
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        result = self._run(tmp_path, env={**os.environ, "GIT_DIR": str(_REPO_ROOT / ".git")})
        calls = self._gate_calls(tmp_path)

        assert result.returncode == 0, result.stderr
        assert calls, "no gate invocations logged"
        assert all("GIT_DIR=unset" in call for call in calls)

    @pytest.mark.parametrize(
        ("git_function", "diagnostic"),
        [
            ("git() { echo 'git discovery unavailable' >&2; return 1; }\n", "failed"),
            ("git() { return 0; }\n", "returned no names"),
        ],
    )
    def test_git_local_environment_discovery_failure_exits_two(
        self, tmp_path: Path, git_function: str, diagnostic: str
    ) -> None:
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        bash_env = tmp_path / "fail-git.sh"
        bash_env.write_text(
            git_function,
            encoding="utf-8",
            newline="\n",
        )
        env = {**os.environ, "BASH_ENV": bash_env.as_posix()}
        result = self._run(tmp_path, env=env)

        assert result.returncode == 2
        assert f"git rev-parse --local-env-vars {diagnostic}" in result.stderr
        assert not self._gate_calls(tmp_path)

    def test_protection_probe_runs_before_ci_check(self, tmp_path: Path) -> None:
        """Cheap network check runs first: drift must not cost a ci_check run."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        self._run(tmp_path)
        calls = self._gate_calls(tmp_path)
        assert "check_branch_protection.py" in calls[0]
        assert "ci_check.py" in calls[1]

    def test_allow_drift_reason_reaches_protection_probe(self, tmp_path: Path) -> None:
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        reason = "temporary branch-protection migration"
        result = self._run(tmp_path, env={**os.environ, "BRANCH_PROTECTION_ALLOW_DRIFT": reason})

        assert result.returncode == 0, result.stderr
        calls = self._gate_calls(tmp_path)
        assert f"check_branch_protection.py --allow-drift {reason}" in calls[0]
        assert "ci_check.py" in calls[1]

    def test_drift_blocks_push_without_running_ci_check(self, tmp_path: Path) -> None:
        """Drift fails push immediately, without paying ci_check minutes."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        (tmp_path / "rc-check_branch_protection.py").write_text("1", encoding="utf-8")
        result = self._run(tmp_path)
        assert result.returncode != 0
        assert not any("ci_check.py" in c for c in self._gate_calls(tmp_path))

    def test_failing_gate_is_not_rerun_under_the_next_interpreter(self, tmp_path: Path) -> None:
        """A red gate is a verdict, not an interpreter-discovery failure."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        self._stub(tmp_path / ".venv" / "bin" / "python", "bin")
        (tmp_path / "rc-ci_check.py").write_text("1", encoding="utf-8")
        result = self._run(tmp_path)
        calls = self._gate_calls(tmp_path)
        assert result.returncode != 0
        assert not any(c.startswith("bin|") for c in calls)
        assert sum("ci_check.py" in c for c in calls) == 1

    def test_gate_exit_code_is_propagated(self, tmp_path: Path) -> None:
        """`2` (tool did not run) and `1` (drift) must remain distinct externally."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        (tmp_path / "rc-check_branch_protection.py").write_text("2", encoding="utf-8")
        assert self._run(tmp_path).returncode == 2

    def test_gate_stderr_is_not_swallowed(self, tmp_path: Path) -> None:
        """`2>/dev/null` remains only on the interpreter probe, not a gate run."""
        self._stub(tmp_path / ".venv" / "Scripts" / "python", "scripts")
        (tmp_path / "rc-ci_check.py").write_text("1", encoding="utf-8")
        result = self._run(tmp_path)
        assert "stderr-from-ci_check.py" in result.stderr

    def test_hook_has_no_crlf_in_the_working_tree(self) -> None:
        """CRLF in shebang kills the hook entirely—`.gitattributes` keeps it LF-only.

        With `core.autocrlf=true` (Git for Windows default) and no attribute, a fresh clone would receive
        `#!/usr/bin/env bash\\r`, bash would fail with “bad interpreter,” and the pre-push gate would silently
        stop running.
        """
        assert b"\r\n" not in _HOOK.read_bytes()

    def test_missing_interpreter_fails_loudly(self, tmp_path: Path) -> None:
        """No candidate found—visible failure, not a silently skipped gate (§IV).

        **Preservation guard**: today’s hook already has this property (a missing interpreter makes
        `bash` fail with a nonzero code), so the test is not in the RED set—it pins what
        refactoring must not break.
        """
        env = {"PATH": str(Path(self._bash()).parent)}
        result = self._run(tmp_path, env=env)
        assert result.returncode != 0
        assert result.stderr.strip(), "молчаливый отказ хука неотличим от зелёного прогона"
