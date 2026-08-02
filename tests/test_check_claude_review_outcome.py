"""Tests for the deterministic Claude structured-outcome verifier."""

from __future__ import annotations

import pytest

from scripts.check_claude_review_outcome import main


class TestOutcome:
    def test_controller_skip_is_not_a_clean_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import scripts.check_claude_review_outcome as outcome_gate

        monkeypatch.setattr(
            outcome_gate.review_gate,
            "fetch_changed_paths",
            lambda *_args: [".github/workflows/claude-review.yml"],
        )
        main(["", "--repo", "owner/repo", "--pr", "1"])

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


class TestReviewOutcomeCli:
    def test_live_pr_context_failure_is_actionable(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            main(['{"outcome":"clean"}', "--live-pr-context-status", "failure"])

        assert exc.value.code == 2
        assert "live PR context is unavailable" in capsys.readouterr().err
