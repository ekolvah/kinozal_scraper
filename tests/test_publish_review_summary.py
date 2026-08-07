"""Tests for the deterministic publisher of a carrier's review summary (#478).

The subject is authority, not formatting: the second review carrier has no comment
channel of its own, and the alternative to this script is handing a write token to a
shell driven by a model whose context holds an untrusted diff. So the assertions
below are about *who* does what — the body is built from data, the body never
becomes shell text, and a summary that fails to publish is loud rather than absent.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from scripts.publish_review_summary import MARKER, main, summary_body

_PRODUCER = "Codex code-review GitHub Action"
_HEAD = "0123456789abcdef0123456789abcdef01234567"
_OUTCOME = json.dumps(
    {"outcome": "rework", "summary": "### Findings\n- should-fix: naming in `x.py`"}
)


def _argv() -> list[str]:
    return [
        "--outcome",
        _OUTCOME,
        "--producer",
        _PRODUCER,
        "--repo",
        "o/r",
        "--pr",
        "7",
        "--head-sha",
        _HEAD,
    ]


class _Recorder:
    """Collect `gh` invocations instead of performing them."""

    def __init__(self, *, existing: list[dict[str, Any]] | None = None) -> None:
        self.existing = existing or []
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if "--paginate" in args or "/comments" in args[-1] and "--method" not in args:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=json.dumps(self.existing), stderr=""
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")

    def method_of(self, index: int) -> str:
        call = self.calls[index]
        return call[call.index("--method") + 1] if "--method" in call else "GET"


class TestSummaryBody:
    def test_body_carries_the_marker_producer_and_head(self) -> None:
        body = summary_body(_OUTCOME, _PRODUCER, _HEAD)

        assert MARKER.format(producer=_PRODUCER) in body, (
            "without its own marker the step cannot find the comment it wrote last "
            "round, and every re-run appends a near-identical summary"
        )
        assert _PRODUCER in body, (
            "a review whose producer is unnamed is indistinguishable from the other "
            "carrier's — the reader cannot tell which agent looked at this head (§IV)"
        )
        assert _HEAD in body, "the summary must state the revision its findings cover"
        assert "should-fix: naming in `x.py`" in body

    @pytest.mark.parametrize(
        "payload",
        ["", "not-json", "[]", "{}", '{"outcome":"rework"}', '{"outcome":"rework","summary":""}'],
    )
    def test_a_payload_without_a_summary_is_loud(self, payload: str) -> None:
        """A carrier that reviewed but published nothing is the §IV failure itself.

        Returning an empty body here would post a comment that says nothing, which
        reads exactly like a review that found nothing."""
        with pytest.raises(ValueError, match="summary"):
            summary_body(payload, _PRODUCER, _HEAD)


class TestPublishing:
    def test_first_run_creates_the_comment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(subprocess, "run", recorder)

        main(_argv())

        assert recorder.method_of(-1) == "POST"
        assert "repos/o/r/issues/7/comments" in recorder.calls[-1]

    def test_re_run_updates_its_own_comment_instead_of_appending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _Recorder(
            existing=[
                {"id": 11, "body": "unrelated human comment"},
                {"id": 42, "body": MARKER.format(producer=_PRODUCER) + "\nold summary"},
            ]
        )
        monkeypatch.setattr(subprocess, "run", recorder)

        main(_argv())

        assert recorder.method_of(-1) == "PATCH"
        assert "repos/o/r/issues/comments/42" in recorder.calls[-1]

    def test_body_travels_as_a_file_never_as_shell_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The summary is model output; interpolating it into a command is injection.

        `--input <file>` is the only channel that keeps backticks, `$(...)`, and
        newlines from being read by anything but the GitHub API."""
        recorder = _Recorder()
        monkeypatch.setattr(subprocess, "run", recorder)

        main(_argv())

        write = recorder.calls[-1]
        assert "--input" in write, "the request body must be handed over as a file"
        assert not any("should-fix" in arg for arg in write), (
            "model-authored text appeared in the command line"
        )

    def test_a_failed_publish_is_not_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def refuse(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="403")

        monkeypatch.setattr(subprocess, "run", refuse)
        with pytest.raises(SystemExit) as exc:
            main(_argv())
        assert exc.value.code != 0

    def test_broken_capture_is_not_read_as_no_comments(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`stdout is None` means the reader died on decoding (#364/#410).

        Treated as "no existing comment" it would silently turn every re-run into a
        new appended summary."""

        def capture_failed(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=None, stderr="")

        monkeypatch.setattr(subprocess, "run", capture_failed)
        with pytest.raises(SystemExit) as exc:
            main(_argv())
        assert exc.value.code != 0
