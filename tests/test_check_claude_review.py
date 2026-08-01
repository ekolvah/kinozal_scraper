"""Tests for the deterministic Claude review outcome gate."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.check_claude_review import fetch_comments, main, outcome_from_comments


def _comment(body: str, author: str = "claude") -> dict[str, Any]:
    return {"user": {"login": author}, "body": body}


class TestOutcomeParsing:
    def test_clean_marker_from_claude_is_accepted(self) -> None:
        comments = [_comment("summary\n<!-- claude-review-outcome: run=1 outcome=clean -->")]
        assert outcome_from_comments(comments, "1") == "clean"

    def test_blocking_marker_is_detected(self) -> None:
        comments = [_comment("<!-- claude-review-outcome: run=1 outcome=blocking -->")]
        assert outcome_from_comments(comments, "1") == "blocking"

    def test_rework_marker_is_detected(self) -> None:
        comments = [_comment("<!-- claude-review-outcome: run=1 outcome=rework -->")]
        assert outcome_from_comments(comments, "1") == "rework"

    def test_marker_from_other_author_is_not_trusted(self) -> None:
        comments = [_comment("<!-- claude-review-outcome: run=1 outcome=clean -->", "contributor")]
        assert outcome_from_comments(comments, "1") is None

    def test_previous_run_does_not_satisfy_current_run(self) -> None:
        comments = [_comment("<!-- claude-review-outcome: run=old outcome=clean -->")]
        assert outcome_from_comments(comments, "new") is None


class TestReviewGateExitCodes:
    @staticmethod
    def _mock_fetch(monkeypatch: pytest.MonkeyPatch, comments: list[dict[str, Any]]) -> None:
        import scripts.check_claude_review as review_gate

        monkeypatch.setattr(review_gate, "fetch_comments", lambda *_args: comments)

    def test_blocking_outcome_fails_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_fetch(
            monkeypatch, [_comment("<!-- claude-review-outcome: run=1 outcome=blocking -->")]
        )
        with pytest.raises(SystemExit) as exc:
            main(["--repo", "owner/repo", "--pr", "1", "--run-id", "1"])
        assert exc.value.code == 1

    def test_rework_outcome_fails_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_fetch(
            monkeypatch, [_comment("<!-- claude-review-outcome: run=1 outcome=rework -->")]
        )
        with pytest.raises(SystemExit) as exc:
            main(["--repo", "owner/repo", "--pr", "1", "--run-id", "1"])
        assert exc.value.code == 1

    def test_missing_marker_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_fetch(monkeypatch, [_comment("summary without marker")])
        with pytest.raises(SystemExit) as exc:
            main(["--repo", "owner/repo", "--pr", "1", "--run-id", "1"])
        assert exc.value.code == 2

    def test_clean_outcome_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mock_fetch(
            monkeypatch, [_comment("<!-- claude-review-outcome: run=1 outcome=clean -->")]
        )
        main(["--repo", "owner/repo", "--pr", "1", "--run-id", "1"])

    def test_gh_transport_failure_is_distinct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="403")

        monkeypatch.setattr(subprocess, "run", fail)
        with pytest.raises(RuntimeError, match="failed: 403"):
            fetch_comments("owner/repo", 1)

    def test_gh_payload_must_be_comment_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def bad_json(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout=json.dumps({}), stderr=""
            )

        monkeypatch.setattr(subprocess, "run", bad_json)
        with pytest.raises(RuntimeError, match="unexpected payload shape"):
            fetch_comments("owner/repo", 1)
