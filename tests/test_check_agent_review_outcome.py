"""Tests for the deterministic agent structured-outcome verifier."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_agent_review_outcome import main


class TestOutcome:
    def test_rework_is_success_with_a_visible_warning(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#458: should-fix findings are a report, not merge authority.

        `rework` used to red a required check, and because the prompt requires
        reporting every finding, a green outcome was unreachable by construction —
        ten review rounds on PR #462, the last four of them cosmetic."""
        main(['{"outcome":"rework"}'])
        out = capsys.readouterr().out
        assert out.startswith("::warning::")
        assert "should-fix" in out
        assert "maintainer" in out

    def test_blocking_is_the_only_finding_outcome_that_reds(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(['{"outcome":"blocking"}'])
        assert exc.value.code == 1

    @pytest.mark.parametrize("payload", ["{}", "not-json", '{"outcome":"unknown"}', ""])
    def test_absent_evidence_is_still_not_success(self, payload: str) -> None:
        """Fail-closed half must not be weakened: no evidence != clean."""
        with pytest.raises(SystemExit) as exc:
            main([payload])
        assert exc.value.code == 2

    def test_clean_outcome_passes(self) -> None:
        main(['{"outcome":"clean"}'])

    def test_only_blocking_fails(self) -> None:
        """`rework` deliberately no longer reds this check — see
        `test_rework_is_success_with_a_visible_warning` (#458)."""
        with pytest.raises(SystemExit) as exc:
            main(['{"outcome":"blocking"}'])
        assert exc.value.code == 1

    @pytest.mark.parametrize("payload", ["", "{}", "not-json", '{"outcome":"unknown"}'])
    def test_missing_or_malformed_output_is_unavailable(self, payload: str) -> None:
        with pytest.raises(SystemExit) as exc:
            main([payload])
        assert exc.value.code == 2

    def test_an_empty_outcome_is_unavailable_on_every_pr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """#483: changed paths no longer decide anything; the carve-out is gone."""
        with pytest.raises(SystemExit) as exc:
            main([""])

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


class TestClassify:
    """The failover condition is a measurement, and it lives where the policy lives.

    Expressing «did carrier 1 produce a usable outcome» as a YAML expression would
    re-implement this module's validity rule in `contains()`/`fromJSON()` — a second
    home for the same policy, and one no test can reach. So the workflow asks the
    script and gates on its answer.
    """

    @pytest.mark.parametrize("outcome", ["clean", "rework", "blocking"])
    def test_a_real_verdict_is_valid_and_stops_the_failover(
        self, outcome: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`blocking` is a result, not a failure.

        Classifying it invalid would start the second carrier, which would then get
        to overrule the first one's blocking finding."""
        output = tmp_path / "github_output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))

        main([json.dumps({"outcome": outcome}), "--classify"])

        assert "valid=true" in output.read_text(encoding="utf-8")

    @pytest.mark.parametrize("payload", ["", "{}", "not-json", '{"outcome":"unknown"}'])
    def test_no_usable_verdict_is_invalid(
        self, payload: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output = tmp_path / "github_output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))

        main([payload, "--classify"])

        assert "valid=false" in output.read_text(encoding="utf-8")

    def test_classification_never_reds_the_job(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Measuring is not judging: the verdict belongs to the enforcement step.

        A non-zero exit here would fail the job before the second carrier ever ran."""
        monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github_output"))
        main(['{"outcome":"blocking"}', "--classify"])
        main(["", "--classify"])

    def test_classification_without_github_output_still_reaches_the_operator(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
        main(['{"outcome":"clean"}', "--classify"])
        assert "valid=true" in capsys.readouterr().out


class TestProducerAttribution:
    """Two carriers whose results read identically are one carrier for the reader."""

    def test_clean_outcome_names_its_producer(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(['{"outcome":"clean"}', "--producer", "Codex"])
        assert "Codex" in capsys.readouterr().out

    def test_rework_warning_names_its_producer(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(['{"outcome":"rework"}', "--producer", "Codex"])
        assert "Codex" in capsys.readouterr().out

    def test_blocking_error_names_its_producer(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(['{"outcome":"blocking"}', "--producer", "Codex"])
        assert "Codex" in capsys.readouterr().err

    def test_an_unavailable_review_names_the_carrier_that_was_asked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Otherwise «review unavailable» hides *which* carrier came back empty."""
        with pytest.raises(SystemExit):
            main(["", "--producer", "Codex"])
        assert "Codex" in capsys.readouterr().err
