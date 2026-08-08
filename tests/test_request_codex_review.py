"""Tests for carrier 2 of the required review gate (#478).

Carrier 2 does not run in our runner: Codex reviews the pull request through its
GitHub integration on the maintainer's ChatGPT subscription, and this adapter only
asks and reads the answer. So the subject here is *what counts as an answer* —
whose review, on which head, and how its state becomes the outcome vocabulary the
enforcement step already owns.

The failure this guards against is the quiet one: reading someone else's review, or
a review of an earlier push, as a verdict on the head being merged. Both would turn
the required check green without anyone having reviewed the code that ships (§IV).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from scripts import request_codex_review
from scripts.check_agent_review_outcome import VALID_OUTCOMES
from scripts.check_agent_review_outcome import main as enforce_outcome
from scripts.request_codex_review import (
    CODEX_REVIEWER,
    STATE_OUTCOMES,
    find_verdict,
    main,
    poll_for_verdict,
)

_HEAD = "1111111111111111111111111111111111111111"  # pragma: allowlist secret
_OLDER = "2222222222222222222222222222222222222222"  # pragma: allowlist secret


def _review(
    state: str, *, sha: str = _HEAD, author: str = "chatgpt-codex-connector[bot]"
) -> dict[str, Any]:
    return {"state": state, "commit_id": sha, "user": {"login": author}}


class _Gh:
    """Answer `gh` calls from a scripted timeline instead of the network."""

    def __init__(self, timeline: list[list[dict[str, Any]]]) -> None:
        self.timeline = timeline
        self.calls: list[list[str]] = []
        self._read = 0

    def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if "/reviews" in " ".join(args):
            page = self.timeline[min(self._read, len(self.timeline) - 1)]
            self._read += 1
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=json.dumps(page), stderr=""
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    @property
    def reads(self) -> list[list[str]]:
        return [call for call in self.calls if "/reviews" in " ".join(call)]

    @property
    def requests(self) -> list[list[str]]:
        return [call for call in self.calls if "comment" in " ".join(call)]


class _Clock:
    """A monotonic clock that only advances when the code under test waits."""

    def __init__(self) -> None:
        self.now = 0.0

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


def _poll(gh: _Gh, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> str | None:
    monkeypatch.setattr(subprocess, "run", gh)
    clock = _Clock()
    return poll_for_verdict(
        "o/r", "7", _HEAD, sleep=clock.sleep, monotonic=clock.monotonic, **kwargs
    )


class TestWhatCountsAsAVerdict:
    def test_the_reviewer_is_the_codex_github_app(self) -> None:
        """Verified against the live API, not guessed from the product name.

        `gh api apps/chatgpt-codex-connector` → owner `openai`; the reviews it
        leaves are authored by the bot login below. A wrong login here reads every
        Codex review as absent and times the carrier out forever."""
        assert CODEX_REVIEWER == "chatgpt-codex-connector[bot]"

    @pytest.mark.parametrize(
        ("state", "outcome"),
        [("APPROVED", "clean"), ("COMMENTED", "rework"), ("CHANGES_REQUESTED", "blocking")],
    )
    def test_review_state_maps_to_the_shared_vocabulary(self, state: str, outcome: str) -> None:
        assert find_verdict([_review(state)], _HEAD, CODEX_REVIEWER) == outcome

    def test_the_mapping_targets_are_the_vocabulary_the_gate_enforces(self) -> None:
        """Two carriers with two vocabularies is two merge bars, chosen by quota."""
        assert set(STATE_OUTCOMES.values()) == set(VALID_OUTCOMES)

    def test_a_review_of_an_earlier_head_is_not_a_verdict_on_this_one(self) -> None:
        assert find_verdict([_review("APPROVED", sha=_OLDER)], _HEAD, CODEX_REVIEWER) is None

    def test_a_review_by_anyone_else_is_not_the_carriers_verdict(self) -> None:
        """Otherwise a maintainer's own approval satisfies the review gate."""
        assert find_verdict([_review("APPROVED", author="ekolvah")], _HEAD, CODEX_REVIEWER) is None

    def test_a_dismissed_review_is_not_a_verdict(self) -> None:
        assert find_verdict([_review("DISMISSED")], _HEAD, CODEX_REVIEWER) is None

    def test_the_latest_matching_review_wins(self) -> None:
        """Codex re-reviews on request; the answer is its last word on this head."""
        reviews = [_review("CHANGES_REQUESTED"), _review("APPROVED")]
        assert find_verdict(reviews, _HEAD, CODEX_REVIEWER) == "clean"

    def test_an_unexpected_payload_shape_is_loud(self) -> None:
        """A shape change upstream must not read as «no review yet» (§IV)."""
        with pytest.raises(RuntimeError, match="unexpected"):
            find_verdict({"message": "Not Found"}, _HEAD, CODEX_REVIEWER)


class TestAskingAndWaiting:
    def test_an_existing_review_on_this_head_is_used_without_asking_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Automatic review is a repository setting; when it already answered for
        this head, spending a second review request buys nothing."""
        gh = _Gh([[_review("COMMENTED")]])

        assert _poll(gh, monkeypatch) == "rework"
        assert gh.requests == [], "the carrier had already answered for this head"

    def test_the_review_is_requested_once_and_then_awaited(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gh = _Gh([[], [], [_review("APPROVED")]])

        assert _poll(gh, monkeypatch, timeout_seconds=300, poll_seconds=10) == "clean"
        assert len(gh.requests) == 1, (
            "re-asking on every poll spams the PR and burns the subscription's review "
            f"budget on one head; requests={gh.requests}"
        )
        assert request_codex_review.REVIEW_REQUEST in " ".join(gh.requests[0])

    def test_waiting_is_bounded_and_the_timeout_leaves_no_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unbounded wait would hold the required check until the runner's own
        six-hour limit; an invented verdict would be worse. Neither: return None
        and let the enforcement step red the check as «no outcome»."""
        gh = _Gh([[]])

        assert _poll(gh, monkeypatch, timeout_seconds=60, poll_seconds=20) is None
        assert len(gh.reads) <= 5, "the poll loop must stop at the declared bound"

    def test_a_failed_gh_call_is_not_read_as_no_review(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="403")

        monkeypatch.setattr(subprocess, "run", refuse)
        clock = _Clock()
        with pytest.raises(RuntimeError, match="403"):
            poll_for_verdict("o/r", "7", _HEAD, sleep=clock.sleep, monotonic=clock.monotonic)

    def test_broken_capture_is_not_read_as_no_review(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`stdout is None` means the reader died on decoding (#364/#410)."""

        def capture_failed(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=None, stderr="")

        monkeypatch.setattr(subprocess, "run", capture_failed)
        clock = _Clock()
        with pytest.raises(RuntimeError, match="no stdout captured"):
            poll_for_verdict("o/r", "7", _HEAD, sleep=clock.sleep, monotonic=clock.monotonic)


class TestPublishedPayload:
    """The step hands its result to the enforcement step and to nobody else."""

    def _run(self, gh: _Gh, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str) -> str:
        output = tmp_path / "github_output"
        output.write_text("", encoding="utf-8")
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        monkeypatch.setattr(subprocess, "run", gh)
        # `main` takes no clock seam — the workflow gives it real seconds. Fake the
        # clock the module reads instead, or the timeout case below really sleeps.
        clock = _Clock()
        monkeypatch.setattr(time, "sleep", clock.sleep)
        monkeypatch.setattr(time, "monotonic", clock.monotonic)
        main(["--repo", "o/r", "--pr", "7", "--head-sha", _HEAD, *extra])
        return output.read_text(encoding="utf-8")

    def test_the_verdict_travels_as_the_payload_enforcement_parses(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Round-trip, not a shape assertion: the payload is fed to the script that
        enforces it, so the two cannot agree on paper and disagree in the job."""
        written = self._run(_Gh([[_review("CHANGES_REQUESTED")]]), monkeypatch, tmp_path)

        payload = written.split("payload=", 1)[1].strip()
        assert json.loads(payload)["outcome"] == "blocking"
        with pytest.raises(SystemExit) as exc:
            enforce_outcome([payload, "--producer", "Codex review"])
        assert exc.value.code == 1

    def test_a_timeout_is_visible_and_publishes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit 0 with an empty payload on purpose: the check is red either way, and
        keeping the verdict in one step keeps the log answering «who reviewed»."""
        written = self._run(
            _Gh([[]]), monkeypatch, tmp_path, "--timeout-seconds", "2", "--poll-seconds", "1"
        )

        assert "payload=\n" in written, (
            "the enforcement step reads this line; a payload written without its "
            f"newline is not the empty outcome it parses. got={written!r}"
        )
        assert "::warning::" in capsys.readouterr().out, (
            "a carrier that never answered must say so; silence here is "
            "indistinguishable from a carrier that was never asked (§IV)"
        )
