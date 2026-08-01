"""Tests for the trusted Claude-review and controller-bootstrap gate."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from scripts.check_claude_review import (
    bootstrap_approved,
    decision_from_evidence,
    fetch_changed_paths,
    main,
    outcome_from_comments,
    wait_for_outcome,
)

HEAD = "a" * 40


def _comment(body: str, author: str = "claude[bot]", author_type: str = "Bot") -> dict[str, Any]:
    return {"user": {"login": author, "type": author_type}, "body": body}


def _outcome(outcome: str, sha: str = HEAD) -> dict[str, Any]:
    return _comment(f"<!-- claude-review-outcome: sha={sha} outcome={outcome} -->")


def _bootstrap(sha: str = HEAD, author: str = "ekolvah") -> dict[str, Any]:
    return _comment(f"<!-- review-controller-bootstrap: sha={sha} -->", author, "User")


class TestOutcomeParsing:
    def test_clean_marker_from_real_claude_bot_is_accepted_for_current_head(self) -> None:
        assert outcome_from_comments([_outcome("clean")], HEAD) == "clean"

    def test_outcome_from_old_head_is_not_accepted(self) -> None:
        assert outcome_from_comments([_outcome("clean", "b" * 40)], HEAD) is None

    def test_marker_from_other_author_is_not_trusted(self) -> None:
        comments = [_comment(_outcome("clean")["body"], "contributor")]
        assert outcome_from_comments(comments, HEAD) is None

    def test_marker_with_claude_login_but_not_bot_type_is_not_trusted(self) -> None:
        comments = [_comment(_outcome("clean")["body"], "claude[bot]", "User")]
        assert outcome_from_comments(comments, HEAD) is None

    def test_last_current_head_outcome_wins(self) -> None:
        assert outcome_from_comments([_outcome("clean"), _outcome("rework")], HEAD) == "rework"


class TestWaitForOutcome:
    def test_wait_returns_clean_current_head_marker(self) -> None:
        calls = iter([[_comment("working")], [_outcome("clean")]])
        sleeps: list[float] = []

        assert (
            wait_for_outcome(lambda: next(calls), ["src/app.py"], HEAD, 2, 3, sleeps.append)
            == "clean"
        )
        assert sleeps == [2]

    def test_wait_times_out_without_valid_current_head_marker(self) -> None:
        assert (
            wait_for_outcome(
                lambda: [_comment("working")], ["src/app.py"], HEAD, 2, 3, lambda _: None
            )
            is None
        )

    def test_wait_rejects_stale_or_non_claude_marker(self) -> None:
        comments = [
            _outcome("clean", "b" * 40),
            _comment(_outcome("clean")["body"], "github-actions[bot]"),
        ]
        assert (
            wait_for_outcome(lambda: comments, ["src/app.py"], HEAD, 1, 3, lambda _: None) is None
        )


class TestControllerBootstrap:
    def test_exact_head_marker_from_configured_maintainer_is_accepted(self) -> None:
        assert bootstrap_approved([_bootstrap()], HEAD)

    def test_bootstrap_marker_from_other_account_is_rejected(self) -> None:
        assert not bootstrap_approved([_bootstrap(author="contributor")], HEAD)

    def test_stale_bootstrap_marker_is_rejected(self) -> None:
        assert not bootstrap_approved([_bootstrap("b" * 40)], HEAD)

    def test_controller_change_requires_bootstrap_not_claude_outcome(self) -> None:
        assert (
            decision_from_evidence(
                [_outcome("clean")],
                [".github/workflows/claude-review.yml"],
                HEAD,
            )
            is None
        )

    def test_controller_change_accepts_exact_maintainer_bootstrap(self) -> None:
        assert (
            decision_from_evidence(
                [_outcome("blocking"), _bootstrap()],
                ["scripts/check_claude_review.py"],
                HEAD,
            )
            == "bootstrap"
        )

    def test_regular_change_uses_claude_outcome(self) -> None:
        assert decision_from_evidence([_outcome("clean")], ["src/app.py"], HEAD) == "clean"


class TestReviewGateExitCodes:
    @staticmethod
    def _mock_evidence(
        monkeypatch: pytest.MonkeyPatch,
        comments: list[dict[str, Any]],
        paths: list[str],
    ) -> None:
        import scripts.check_claude_review as review_gate

        monkeypatch.setattr(review_gate, "fetch_comments", lambda *_args: comments)
        monkeypatch.setattr(review_gate, "fetch_changed_paths", lambda *_args: paths)

    def test_blocking_outcome_fails_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_evidence(monkeypatch, [_outcome("blocking")], ["src/app.py"])
        with pytest.raises(SystemExit) as exc:
            main(["--repo", "owner/repo", "--pr", "1", "--head-sha", HEAD])
        assert exc.value.code == 1

    def test_missing_evidence_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_evidence(monkeypatch, [_comment("summary")], ["src/app.py"])
        with pytest.raises(SystemExit) as exc:
            main(["--repo", "owner/repo", "--pr", "1", "--head-sha", HEAD])
        assert exc.value.code == 2

    def test_bootstrap_passes_the_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_evidence(
            monkeypatch, [_bootstrap()], [".github/workflows/agent-review-gate.yml"]
        )
        main(["--repo", "owner/repo", "--pr", "1", "--head-sha", HEAD])

    def test_controller_pr_can_defer_producer_marker_to_trusted_bootstrap(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._mock_evidence(
            monkeypatch, [_comment("summary")], [".github/workflows/claude-review.yml"]
        )

        main(
            [
                "--repo",
                "owner/repo",
                "--pr",
                "1",
                "--head-sha",
                HEAD,
                "--require-outcome-marker",
                "--allow-controller-bootstrap",
            ]
        )
        assert "warning:" in capsys.readouterr().out

    def test_producer_marker_accepts_real_claude_bot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_evidence(monkeypatch, [_outcome("clean")], ["src/app.py"])

        main(
            [
                "--repo",
                "owner/repo",
                "--pr",
                "1",
                "--head-sha",
                HEAD,
                "--require-outcome-marker",
            ]
        )

    def test_producer_without_marker_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_evidence(monkeypatch, [_comment("summary")], ["src/app.py"])

        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--repo",
                    "owner/repo",
                    "--pr",
                    "1",
                    "--head-sha",
                    HEAD,
                    "--require-outcome-marker",
                ]
            )
        assert exc.value.code == 2

    def test_mid_poll_transport_failure_exits_with_infrastructure_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import scripts.check_claude_review as review_gate

        calls: Iterator[list[dict[str, Any]] | RuntimeError] = iter(
            [[_comment("working")], RuntimeError("temporary GitHub API failure")]
        )
        monkeypatch.setattr(review_gate, "fetch_changed_paths", lambda *_args: ["src/app.py"])

        def fetch(*_args: Any) -> list[dict[str, Any]]:
            result = next(calls)
            if isinstance(result, RuntimeError):
                raise result
            return result

        monkeypatch.setattr(review_gate, "fetch_comments", fetch)
        monkeypatch.setattr(review_gate.time, "sleep", lambda _seconds: None)

        with pytest.raises(SystemExit) as exc:
            main(
                [
                    "--repo",
                    "owner/repo",
                    "--pr",
                    "1",
                    "--head-sha",
                    HEAD,
                    "--wait-seconds",
                    "10",
                ]
            )
        assert exc.value.code == 2

    def test_gh_transport_failure_is_distinct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="403")

        monkeypatch.setattr(subprocess, "run", fail)
        with pytest.raises(RuntimeError, match="failed: 403"):
            fetch_changed_paths("owner/repo", 1)

    def test_changed_paths_payload_must_be_file_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def bad_json(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout=json.dumps({}), stderr=""
            )

        monkeypatch.setattr(subprocess, "run", bad_json)
        with pytest.raises(RuntimeError, match="unexpected payload shape"):
            fetch_changed_paths("owner/repo", 1)
