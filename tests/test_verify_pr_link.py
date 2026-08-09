"""Tests for #320 CI gate: `verify_pr_link` — the agent-independent linkage barrier.

`open_pr.py` makes the correct path cheap when creating a PR, but it is invoked in
prose by `/implement`. This gate makes the invariant **necessary**: a CI job fails a
PR from an `issue-N-*` branch with empty `closingIssuesReferences` (→ required check →
cannot merge), regardless of how the PR was created. It reuses the pure
`issue_number_from_branch` + `has_closing_reference` helpers from `open_pr` rather
than duplicating them.

`gh` is the external boundary, doubled through the `subprocess.run` seam (§II).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.verify_pr_link import link_required_but_missing, main


class TestLinkRequiredButMissing:
    def test_issue_branch_without_link_fails(self) -> None:
        assert link_required_but_missing("issue-320-x", '{"closingIssuesReferences":[]}') is True

    def test_issue_branch_with_link_passes(self) -> None:
        payload = json.dumps({"closingIssuesReferences": [{"number": 320}]})
        assert link_required_but_missing("issue-320-x", payload) is False

    def test_non_issue_branch_not_required(self) -> None:
        # Fork/manual branches need not close issues, so the gate is inapplicable.
        assert link_required_but_missing("feature/x", '{"closingIssuesReferences":[]}') is False


class TestMain:
    def _stub_refs(self, monkeypatch: pytest.MonkeyPatch, payload: str) -> None:
        def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, payload, "")

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_exits_1_when_issue_branch_unlinked(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._stub_refs(monkeypatch, '{"closingIssuesReferences":[]}')
        with pytest.raises(SystemExit) as exc:
            main(["--branch", "issue-320-x", "--pr", "321"])
        assert exc.value.code == 1
        assert "#320" in capsys.readouterr().err  # §IV: the message identifies the issue.

    def test_passes_when_linked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_refs(monkeypatch, json.dumps({"closingIssuesReferences": [{"number": 320}]}))
        main(["--branch", "issue-320-x", "--pr", "321"])  # Must not raise SystemExit.

    def test_polls_past_async_race(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # On `opened`, the gate can outrun GitHub's asynchronous linkage update.
        # Polling avoids a false red when the second read contains the link.
        empty = '{"closingIssuesReferences":[]}'
        linked = json.dumps({"closingIssuesReferences": [{"number": 320}]})
        reads = iter([empty, linked])

        def fake_run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, next(reads), "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        main(["--branch", "issue-320-x", "--pr", "321"])  # Must not raise SystemExit.

    def test_passes_for_non_issue_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub_refs(monkeypatch, '{"closingIssuesReferences":[]}')
        main(["--branch", "dependabot/pip/x", "--pr", "500"])  # Inapplicable gate: OK.

    def test_gh_failure_distinct_from_missing_link(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A transient auth/rate-limit failure must not masquerade as empty linkage.
        # Exit 2 means infrastructure failure; exit 1 means an invariant breach.
        def fake_run(cmd: list[str], *a: Any, **k: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "gh: could not resolve host")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc:
            main(["--branch", "issue-320-x", "--pr", "321"])
        assert exc.value.code == 2
        assert "gh" in capsys.readouterr().err.lower()
