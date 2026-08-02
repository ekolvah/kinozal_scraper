"""Tests for the deterministic Claude structured-outcome verifier."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.check_claude_review_outcome import fetch_changed_paths, main


class TestOutcome:
    def test_controller_skip_warns_about_manual_ide_review(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import scripts.check_claude_review_outcome as outcome_gate

        monkeypatch.setattr(
            outcome_gate,
            "fetch_changed_paths",
            lambda *_args: [".github/workflows/claude-review.yml"],
        )
        main(["", "--repo", "owner/repo", "--pr", "1"])
        warning = capsys.readouterr().out
        assert warning.startswith(
            "::warning::No structured review outcome was produced for this review-controller PR."
        )
        assert warning.endswith(
            "otherwise complete the manual IDE-agent review before merge under the single-maintainer policy.\n"
        )

    def test_controller_pr_enforces_a_real_blocking_outcome(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import scripts.check_claude_review_outcome as outcome_gate

        monkeypatch.setattr(
            outcome_gate,
            "fetch_changed_paths",
            lambda *_args: [".github/workflows/claude-review.yml"],
        )

        with pytest.raises(SystemExit) as exc:
            main(['{"outcome":"blocking"}', "--repo", "owner/repo", "--pr", "1"])

        assert exc.value.code == 1
        assert "Claude review reported blocking findings" in capsys.readouterr().err

    def test_clean_outcome_passes(self) -> None:
        main(['{"outcome":"clean"}'])

    @pytest.mark.parametrize("outcome", ["rework", "blocking"])
    def test_rework_and_blocking_fail(self, outcome: str) -> None:
        with pytest.raises(SystemExit) as exc:
            main([f'{{"outcome":"{outcome}"}}'])
        assert exc.value.code == 1

    @pytest.mark.parametrize("payload", ["", "{}", "not-json", '{"outcome":"unknown"}'])
    def test_missing_or_malformed_output_is_unavailable(self, payload: str) -> None:
        with pytest.raises(SystemExit) as exc:
            main([payload])
        assert exc.value.code == 2

    def test_ordinary_pr_without_an_outcome_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import scripts.check_claude_review_outcome as outcome_gate

        monkeypatch.setattr(outcome_gate, "fetch_changed_paths", lambda *_args: ["src/app.py"])
        with pytest.raises(SystemExit) as exc:
            main(["", "--repo", "owner/repo", "--pr", "1"])

        assert exc.value.code == 2
        assert "Claude review unavailable" in capsys.readouterr().err


class TestReviewOutcomeCli:
    def test_live_pr_context_failure_is_actionable(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(['{"outcome":"clean"}', "--live-pr-context-status", "failure"])

        assert exc.value.code == 2
        assert "live PR context is unavailable" in capsys.readouterr().err

    def test_controller_classification_failure_is_actionable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import scripts.check_claude_review_outcome as outcome_gate

        def fail(*_args: Any) -> list[str]:
            raise RuntimeError("403")

        monkeypatch.setattr(outcome_gate, "fetch_changed_paths", fail)
        with pytest.raises(SystemExit) as exc:
            main(["", "--repo", "owner/repo", "--pr", "1"])

        assert exc.value.code == 2
        assert "unable to classify review-controller PR: 403" in capsys.readouterr().err


class TestChangedPaths:
    def test_branch_protection_declaration_is_a_controller_path(self) -> None:
        import scripts.check_claude_review_outcome as outcome_gate

        assert outcome_gate.controller_changed(["scripts/check_branch_protection.py"])

    def test_gh_transport_failure_is_distinct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fail(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="403")

        monkeypatch.setattr(subprocess, "run", fail)
        with pytest.raises(RuntimeError, match="failed: 403"):
            fetch_changed_paths("owner/repo", 1)

    def test_missing_subprocess_stdout_is_distinct(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def capture_failed(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=None, stderr="")

        monkeypatch.setattr(subprocess, "run", capture_failed)
        with pytest.raises(RuntimeError, match="failed: no stderr captured"):
            fetch_changed_paths("owner/repo", 1)

    def test_changed_paths_payload_must_be_file_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def bad_json(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=["gh"], returncode=0, stdout=json.dumps({}), stderr=""
            )

        monkeypatch.setattr(subprocess, "run", bad_json)
        with pytest.raises(RuntimeError, match="unexpected payload shape"):
            fetch_changed_paths("owner/repo", 1)
