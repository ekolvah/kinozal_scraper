"""RED tests for #320: `open_pr.py`—reliable PR→issue automatic linking.

Failure #319: the PR did not close issue #140 (GitHub does not parse Russian “Closes”; squash
dropped `(closes #N)` from the commit body). The fix is to deterministically build English
`Closes #N` in the PR body (survives squash) + read `closingIssuesReferences` after create
and fail visibly (§IV) if the linkage is empty.

`gh`/`git` are the external boundary, mocked through the `subprocess.run` seam (§II—not a mock
of internal logic). `closingIssuesReferences` is parsed tolerantly in both forms—the
flat CLI array and the `.nodes` wrapper (see `TestHasClosingReference`).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.open_pr import (
    LINKAGE_ATTEMPTS,
    LINKAGE_DELAY_S,
    ensure_closes_line,
    has_closing_reference,
    has_substantive_body,
    issue_number_from_branch,
    main,
)


@pytest.fixture
def report_body_file(tmp_path: Path) -> str:
    path = tmp_path / "report.md"
    path.write_text("## Summary\n\nDid it.\n", encoding="utf-8")
    return str(path)


class TestIssueNumberFromBranch:
    def test_extracts_number_from_issue_branch(self) -> None:
        assert issue_number_from_branch("issue-320-slug") == 320

    def test_multidigit_and_hyphen_slug(self) -> None:
        assert issue_number_from_branch("issue-140-trailers-data-prep") == 140

    def test_returns_none_for_non_issue_branch(self) -> None:
        assert issue_number_from_branch("feature/x") is None

    def test_returns_none_for_main(self) -> None:
        assert issue_number_from_branch("main") is None


class TestEnsureClosesLine:
    def test_injects_when_absent(self) -> None:
        body = "## Summary\n\nDid a thing.\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result

    def test_idempotent_when_already_present(self) -> None:
        body = "Closes #320\n\n## Summary\n"
        result = ensure_closes_line(body, 320)
        assert result.count("Closes #320") == 1

    def test_leaves_other_closes_untouched(self) -> None:
        # The script only ADDS its line; it does not rewrite another author's text: an existing
        # `Closes #999` (multi-issue PR) is preserved, and our `Closes #320` is added.
        body = "## Summary\n\nCloses #999\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result
        assert "#999" in result

    def test_bare_placeholder_left_inert(self) -> None:
        # A bare `Closes #` from the GitHub template is ignored (there is no number)—do not alter it
        # with regexp surgery; simply add our line with the number.
        body = "## Summary\n\nCloses #\n"
        result = ensure_closes_line(body, 320)
        assert "Closes #320" in result


class TestHasSubstantiveBody:
    def test_rejects_blank_and_closing_line_only_bodies(self) -> None:
        assert has_substantive_body(" \n\t\n", 320) is False
        assert has_substantive_body("\nCloses #320\n", 320) is False

    def test_accepts_report_content_alongside_existing_closing_line(self) -> None:
        assert has_substantive_body("Closes #320\n\n## Summary\n\nDid it.\n", 320) is True


class TestHasClosingReference:
    def test_true_when_flat_array(self) -> None:
        # Current CLI form: a flat array without `.nodes`.
        payload = json.dumps({"closingIssuesReferences": [{"number": 320, "url": "https://x/320"}]})
        assert has_closing_reference(payload) is True

    def test_true_when_nodes_wrapper(self) -> None:
        # GraphQL form (and where a future `gh` could move): the `.nodes` wrapper.
        payload = json.dumps({"closingIssuesReferences": {"nodes": [{"number": 320}]}})
        assert has_closing_reference(payload) is True

    def test_false_when_flat_empty(self) -> None:
        # Actual output of `gh pr view 319` (our failing case).
        assert has_closing_reference('{"closingIssuesReferences":[]}') is False

    def test_false_when_nodes_empty(self) -> None:
        assert has_closing_reference('{"closingIssuesReferences":{"nodes":[]}}') is False


class _GhDispatcher:
    """Double for the `gh`/`git` external boundary: dispatches `subprocess.run` by argv and records
    calls in `calls`. It lets tests verify `main()` orchestration (which commands and which
    body were used) without touching the network."""

    def __init__(
        self,
        *,
        branch: str,
        existing_pr: dict[str, Any] | None,
        refs_empty_reads: int = 0,
        create_fails: bool = False,
    ) -> None:
        # `refs_empty_reads` is how many INITIAL closingIssuesReferences reads return
        # empty before a non-empty one (models GitHub eventual consistency after create,
        # caught by dogfooding PR #321). A large number means linkage never appeared.
        self.branch = branch
        self.existing_pr = existing_pr
        self.refs_empty_reads = refs_empty_reads
        self.create_fails = create_fails
        self._refs_reads = 0
        self.calls: list[list[str]] = []

    def __call__(
        self, cmd: list[str], *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)

        def done(code: int, out: str) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, code, out, "")

        if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
            return done(0, self.branch + "\n")
        if cmd[:3] == ["gh", "pr", "list"]:
            # existing OPEN-PR probe (`--head <branch> --state open --json url,body`).
            # A closed PR is filtered by `--state open` → empty array, forcing create.
            return done(0, json.dumps([self.existing_pr] if self.existing_pr else []))
        if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
            fields = cmd[cmd.index("--json") + 1]
            if "closingIssuesReferences" in fields:
                self._refs_reads += 1
                empty = self._refs_reads <= self.refs_empty_reads
                refs = [] if empty else [{"number": 320, "url": "https://x/320"}]
                return done(0, json.dumps({"closingIssuesReferences": refs}))
            raise AssertionError(f"unexpected gh pr view: {cmd}")
        if cmd[:3] == ["gh", "pr", "create"]:
            if self.create_fails:
                return done(1, "")  # non-zero exit from gh pr create
            return done(0, "https://github.com/ekolvah/kinozal_scraper/pull/999\n")
        if cmd[:3] == ["gh", "pr", "edit"]:
            return done(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    def cmds(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


class TestMainVerification:
    def test_rejects_new_pr_without_body_file_before_creating(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None)
        monkeypatch.setattr(subprocess, "run", disp)

        with pytest.raises(SystemExit) as exc:
            main(["--title", "T"])

        assert exc.value.code == 2
        assert "--body-file" in capsys.readouterr().err
        assert not any(call[:3] == ["gh", "pr", "create"] for call in disp.calls)

    def test_rejects_link_only_body_before_creating(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        body_file = tmp_path / "body.md"
        body_file.write_text("Closes #320\n", encoding="utf-8")
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None)
        monkeypatch.setattr(subprocess, "run", disp)

        with pytest.raises(SystemExit) as exc:
            main(["--title", "T", "--body-file", str(body_file)])

        assert exc.value.code == 2
        assert "substantive" in capsys.readouterr().err
        assert not any(call[:3] == ["gh", "pr", "create"] for call in disp.calls)

    @pytest.mark.parametrize(
        ("body_path", "contents", "expected_error"),
        [
            ("missing.md", None, "cannot read --body-file"),
            ("invalid-utf8.md", b"\xff", "utf-8"),
        ],
    )
    def test_rejects_unreadable_report_before_creating(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        body_path: str,
        contents: bytes | None,
        expected_error: str,
    ) -> None:
        report = tmp_path / body_path
        if contents is not None:
            report.write_bytes(contents)
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None)
        monkeypatch.setattr(subprocess, "run", disp)

        with pytest.raises(SystemExit) as exc:
            main(["--title", "T", "--body-file", str(report)])

        assert exc.value.code == 2
        assert expected_error in capsys.readouterr().err
        assert not any(call[:3] == ["gh", "pr", "create"] for call in disp.calls)

    def test_creates_new_pr_from_substantive_body_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        body_file = tmp_path / "body.md"
        body_file.write_text("## Summary\n\nDid it.\n", encoding="utf-8")
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None)
        monkeypatch.setattr(subprocess, "run", disp)

        main(["--title", "T", "--body-file", str(body_file)])

        create = next(call for call in disp.calls if call[:3] == ["gh", "pr", "create"])
        assert create[create.index("--body") + 1].count("Closes #320") == 1

    def test_exits_1_when_linkage_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        report_body_file: str,
    ) -> None:
        # Linkage never appeared (all reads are empty) → a visible failure (§IV) with the PR URL.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty_reads=99)
        monkeypatch.setattr(subprocess, "run", disp)
        monkeypatch.setattr("time.sleep", lambda *_: None)  # do not wait for real backoff
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T", "--body-file", report_body_file])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "999" in err  # PR URL in the message
        assert "#320" in err  # remediation identifies the issue

    def test_failed_refs_read_is_visible_not_reported_as_missing_link(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        report_body_file: str,
    ) -> None:
        """A failed `gh pr view` means “linkage is unknown”, not “linkage is absent” (#410).

        Previously `_closing_refs_json` did not check returncode and returned `"{}"`, which
        is indistinguishable from an honest “no references”: the script polled and issued the
        `NOT linked` verdict, while the real cause (a `gh` failure) never reached the operator.

        Code **2**, not 1: one is occupied by the same script's legitimate verdicts
        (“PR NOT linked”, “gh pr create failed”), and merging an infrastructure failure into them
        would repeat the original error in another form. The same choice is made by sibling
        `verify_pr_link.py`. Tolerance for transients remains: a verdict is issued
        only when ALL read attempts fail—a single rate limit inside the retry loop costs an
        attempt, not the run."""

        def failing_refs_read(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["gh", "pr", "view"] and "closingIssuesReferences" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=1, stdout="", stderr="gh: rate limit exceeded"
                )
            return _GhDispatcher(branch="issue-320-x", existing_pr=None)(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "run", failing_refs_read)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T", "--body-file", report_body_file])
        assert exc.value.code == 2, "infra failure must not share the code of a verdict"
        err = capsys.readouterr().err
        assert "rate limit" in err, "the real cause must reach the operator"
        assert "linkage is unknown, not absent" in err, "must not read as a missing link"

    def test_early_success_then_read_failures_is_unknown_not_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        report_body_file: str,
    ) -> None:
        """Mixed case: one early successful read + a series of failures (#410).

        The first read is normally empty—GitHub computes `closingIssuesReferences`
        asynchronously (#321). Therefore “there was at least one successful read” does NOT authorize
        the “no references” verdict: the final state was not observed.
        Previously the condition required ALL attempts to fail, and this scenario produced a false
        `NOT linked` with useless remediation “add Closes #N”—even though
        `ensure_closes_line` had already added it."""
        state = {"reads": 0}
        base = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty_reads=99)

        def flaky(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if cmd[:3] == ["gh", "pr", "view"] and "closingIssuesReferences" in cmd:
                state["reads"] += 1
                if state["reads"] > 1:  # the first read succeeds; the rest fail
                    return subprocess.CompletedProcess(
                        args=cmd, returncode=1, stdout="", stderr="gh: rate limit exceeded"
                    )
            return base(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "run", flaky)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T", "--body-file", report_body_file])
        assert exc.value.code == 2, "an unobserved final state is not a verdict"
        err = capsys.readouterr().err
        assert "linkage is unknown, not absent" in err
        assert "NOT linked" not in err

    def test_retries_linkage_until_populated(
        self, monkeypatch: pytest.MonkeyPatch, report_body_file: str
    ) -> None:
        # Regression from dogfooding PR #321: GitHub computes closingIssuesReferences
        # asynchronously after create—the first read is empty, but linkage is CORRECT.
        # main() must poll again rather than exit 1 on the first race.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty_reads=1)
        monkeypatch.setattr(subprocess, "run", disp)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        main(["--title", "T", "--body-file", report_body_file])  # must not raise SystemExit
        refs_reads = sum(
            1
            for c in disp.calls
            if c[:3] == ["gh", "pr", "view"] and "closingIssuesReferences" in c
        )
        assert refs_reads >= 2, "линковка должна перечитываться после пустого первого чтения"

    def test_reuses_existing_pr_and_fixes_linkage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Repeat run: a PR for the branch already exists, with a body lacking the link → edit, NOT create.
        disp = _GhDispatcher(
            branch="issue-320-x",
            existing_pr={"url": "https://x/pull/999", "body": "## Summary\n\nDid it.\n"},
            refs_empty_reads=0,
        )
        monkeypatch.setattr(subprocess, "run", disp)
        main(["--title", "T"])
        joined = disp.cmds()
        assert not any(c.startswith("gh pr create") for c in joined), "create не должен вызываться"
        assert any(c.startswith("gh pr edit") for c in joined), "линковка чинится через edit"
        edit_cmd = next(c for c in disp.calls if c[:3] == ["gh", "pr", "edit"])
        assert any("Closes #320" in part for part in edit_cmd)

    def test_creates_new_pr_when_no_open_pr(
        self, monkeypatch: pytest.MonkeyPatch, report_body_file: str
    ) -> None:
        # Fix #4: `gh pr list --state open` does not see a CLOSED PR for the same branch →
        # an empty list → the script creates a new one rather than attaching to a dead PR.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, refs_empty_reads=0)
        monkeypatch.setattr(subprocess, "run", disp)
        main(["--title", "T", "--body-file", report_body_file])
        joined = disp.cmds()
        assert any(c.startswith("gh pr create") for c in joined), "должен создать новый PR"
        assert not any(c.startswith("gh pr edit") for c in joined), (
            "edit мёртвого PR не должен быть"
        )

    def test_exits_1_when_create_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        report_body_file: str,
    ) -> None:
        # `gh pr create` failed (no upstream / a PR already exists / network) → visible exit 1,
        # not a silent continuation to read linkage from an empty URL.
        disp = _GhDispatcher(branch="issue-320-x", existing_pr=None, create_fails=True)
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T", "--body-file", report_body_file])
        assert exc.value.code == 1
        assert "create" in capsys.readouterr().err.lower()

    def test_exits_2_for_non_issue_branch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        disp = _GhDispatcher(branch="feature/x", existing_pr=None)
        monkeypatch.setattr(subprocess, "run", disp)
        with pytest.raises(SystemExit) as exc:
            main(["--title", "T"])
        assert exc.value.code == 2


class TestLinkageBudget:
    """#352: the linkage polling budget (~8s) was exhausted on PR #349, where GitHub
    indexed `closingIssuesReferences` for ~30+s → a false-positive “issue NOT
    linked” with a correct `Closes #N`. The root cause was a narrow budget, not missing
    retry. The budget was expanded for the observed lag (~40s)."""

    def test_confirms_linkage_appearing_beyond_old_budget(
        self, monkeypatch: pytest.MonkeyPatch, report_body_file: str
    ) -> None:
        # Linkage appears on the ninth read (refs_empty_reads=8)—beyond the
        # old budget=5 (where main() would raise a false-positive SystemExit),
        # within the new one. main() must confirm linkage and not fail; the fast path
        # stops polling when it appears → exactly nine reads, not the whole budget.
        disp = _GhDispatcher(branch="issue-352-x", existing_pr=None, refs_empty_reads=8)
        monkeypatch.setattr(subprocess, "run", disp)
        monkeypatch.setattr("time.sleep", lambda *_: None)
        main(["--title", "T", "--body-file", report_body_file])  # must not raise SystemExit
        refs_reads = sum(
            1
            for c in disp.calls
            if c[:3] == ["gh", "pr", "view"] and "closingIssuesReferences" in c
        )
        assert refs_reads == 9, "fast-path останавливает поллинг на появлении линковки"

    def test_budget_covers_observed_indexing_lag(self) -> None:
        # Encode acceptance as a DURATION (~40s of observed lag), not a hardcoded
        # attempts×delay split—robust to repartitioning.
        assert LINKAGE_ATTEMPTS * LINKAGE_DELAY_S >= 40
