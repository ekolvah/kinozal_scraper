"""Tests for `scripts/validate_issue_sections.py` — the plan-completeness gate.

Covers gap detection (missing, empty, whitespace-only, setext headings, headings
inside fenced blocks, an unterminated fence), the mandatory Architect review and
ADR sections, the review-provenance marker that keeps self-review visible, and
the Cyrillic body decode.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import scripts.validate_issue_sections as validator
from scripts.validate_issue_sections import (
    REQUIRED_SECTIONS,
    _fetch_body,
    _split_by_h2,
    architect_review_provenance,
    find_gaps,
    reviewer_independence,
)


def _full_body() -> str:
    parts = []
    for s in REQUIRED_SECTIONS:
        parts.append(f"## {s}\n\n{_section_content(s)}\n")
    return "\n".join(parts)


# The six sections that predate the `Architect review` gate (#150). Hardcoded on
# purpose: it lets the RED tests below assert that `Architect review` is required
# *independently* of REQUIRED_SECTIONS, so they fail before the gate is added.
_LEGACY_SECTIONS = (
    "Context / Why",
    "Acceptance criteria",
    "Test plan",
    "Implementation outline",
    "Docs to update",
    "Out of scope",
)


def _body_with(sections: tuple[str, ...]) -> str:
    return "\n".join(f"## {s}\n\n{_section_content(s)}\n" for s in sections)


_INDEPENDENT_ADAPTER = "Claude architect-reviewer subagent"
_SELF_ADAPTER = "Codex $plan-issue #N self-review"


def _section_content(section: str) -> str:
    if section == "Agent handoff":
        return (
            "planner: Claude [model]\n"
            "validation: `python scripts/validate_issue_sections.py 1` — passed\n"
            "next role: implementer\n"
            "handoff: ready"
        )
    if section == "Architect review":
        # Since #474 the section opens with a provenance marker; a body without one
        # is a gap, so the shared fixture has to carry it.
        return f"reviewer: {_INDEPENDENT_ADAPTER}\n\nBLOCKING: real finding, long enough."
    return f"Real content для {section} which is long enough."


class TestFindGaps:
    def test_all_sections_filled_returns_no_gaps(self) -> None:
        assert find_gaps(_full_body()) == []

    def test_missing_section_listed(self) -> None:
        body = _full_body().replace(
            "## Out of scope\n\nReal content для Out of scope which is long enough.\n", ""
        )
        assert find_gaps(body) == ["Out of scope"]

    def test_empty_section_listed_even_if_header_present(self) -> None:
        body = "## Context / Why\n\n\n" + "\n".join(
            f"## {s}\n\n{_section_content(s)}\n" for s in REQUIRED_SECTIONS[1:]
        )
        assert find_gaps(body) == ["Context / Why"]

    def test_whitespace_only_section_listed(self) -> None:
        body = "## Context / Why\n\n   \n\n" + "\n".join(
            f"## {s}\n\n{_section_content(s)}\n" for s in REQUIRED_SECTIONS[1:]
        )
        assert find_gaps(body) == ["Context / Why"]

    def test_all_sections_missing_returns_all(self) -> None:
        assert find_gaps("") == list(REQUIRED_SECTIONS)

    def test_case_insensitive_header_match(self) -> None:
        body = _full_body().replace("## Context / Why", "## context / why")
        assert find_gaps(body) == []

    def test_extra_section_ignored(self) -> None:
        body = _full_body() + "\n## Extra\n\nNot required.\n"
        assert find_gaps(body) == []

    def test_custom_required_set(self) -> None:
        """The required section set is a parameter, not a module constant (#426).

        The MADR record guard (`tests/test_adr_records.py`) is the second consumer:
        it has its own h2 list but uses the same parser. Forking the parser would
        create a second definition of an empty section and let the two drift (§VII).
        """
        required = ("Context and Problem Statement", "Considered Options", "Decision Outcome")
        body = _body_with(required[:2])
        assert find_gaps(body, required=required) == ["Decision Outcome"]
        assert find_gaps(_body_with(required), required=required) == []

    def test_heading_inside_fenced_block_is_not_a_section(self) -> None:
        """A `## ` inside a fence is example content, not a heading.

        This rarely affects issue bodies but is more likely in MADR examples. A
        phantom section named like a real one would overwrite its content with
        the rest of the code block, making the gate lie in both directions.
        """
        body = _full_body().replace(
            "## Out of scope\n\nReal content для Out of scope which is long enough.\n",
            "## Out of scope\n\nЦитата шаблона:\n\n```md\n## Context / Why\n```\n",
        )
        assert find_gaps(body) == []
        # Fenced-block lines must remain in their section rather than disappear.
        assert "## Context / Why" in _split_by_h2(body)["out of scope"]

    def test_unterminated_fence_swallows_the_rest_as_github_renders_it(self) -> None:
        """An unterminated fence consumes the document remainder, correctly.

        CommonMark extends it to EOF, so GitHub renders every later section as
        code. Reporting those sections missing describes the real broken body
        instead of guessing the author's intent. `main` explains the open fence.
        """
        body = _full_body().replace(
            "## Context / Why\n\nReal content для Context / Why which is long enough.\n",
            "## Context / Why\n\nЗабыли закрыть:\n\n```md\n",
        )
        gaps = find_gaps(body)
        assert "Context / Why" not in gaps  # The section opened before the fence.
        assert "Acceptance criteria" in gaps  # Everything below is block content.

    def test_setext_heading_counts_as_section(self) -> None:
        """`Text` plus `---` is also h2, matching GitHub rendering.

        The regexp parser missed setext headings and treated populated sections
        as absent. This pins parity with what a reader sees in the rendered issue.
        """
        body = "\n".join(f"{s}\n---\n\n{_section_content(s)}\n" for s in REQUIRED_SECTIONS)
        assert find_gaps(body) == []


class TestArchitectReviewSection:
    """The `Architect review` gate (#150): every issue must carry the section,
    even if filled with an explicit `skipped: <reason>`. Guarantees the review
    is consciously decided, not silently forgotten."""

    def test_architect_review_required(self) -> None:
        # All six legacy sections filled, but no `Architect review` → must be a gap.
        body = _body_with(_LEGACY_SECTIONS)
        assert "Architect review" in find_gaps(body)

    def test_architect_review_filled_passes(self) -> None:
        # Narrow assertion: a filled section is not a gap. Stays correct even if
        # an 8th section is later added (would not fail for the wrong reason).
        body = _body_with((*_LEGACY_SECTIONS, "Architect review"))
        assert "Architect review" not in find_gaps(body)


class TestArchitectReviewProvenance:
    """Who reviewed the plan is a fact of the section, not a guess (#474).

    A filled section used to look identical whether an independent carrier reviewed
    the plan, its own author did, or nobody did and the prose was written to fill the
    gate. The marker cannot prove the review happened — no script can read who typed
    the text — but it forces the carrier to be **named**, which is what turns a passive
    default into a written claim, exactly as `planner:` does in `Agent handoff`.
    """

    @staticmethod
    def _body(review_section: str) -> str:
        body = _full_body()
        return body.replace(_section_content("Architect review"), review_section)

    def test_independent_adapter_marker_passes(self) -> None:
        assert find_gaps(self._body(f"reviewer: {_INDEPENDENT_ADAPTER}\n\nfindings")) == []

    def test_self_adapter_marker_passes_and_is_reported_as_self(self) -> None:
        section = f"reviewer: {_SELF_ADAPTER}\n\nfindings"
        assert find_gaps(self._body(section)) == []
        assert architect_review_provenance(section) == "self"

    def test_skipped_reason_still_passes(self) -> None:
        """The trivial route (#150) keeps working unchanged: no carrier to name."""
        assert find_gaps(self._body("skipped: one-line typo, no design decision")) == []
        assert architect_review_provenance("skipped: one-line typo") == "skipped"

    def test_architect_review_without_provenance_line_is_a_gap(self) -> None:
        gaps = find_gaps(self._body("BLOCKING: a real finding written by somebody."))
        assert gaps == ["Architect review (missing: reviewer provenance line)"]

    def test_marker_only_inside_findings_prose_is_a_gap(self) -> None:
        """Prose that merely *quotes* the marker must not satisfy the gate.

        This section is the one place in the issue where free-form review text lives,
        and a review of this very change discusses `reviewer: self` and `skipped:` by
        name. A substring match would therefore go green on the exact case the gate
        exists to catch — false coverage worse than no gate at all (§IV).
        """
        section = (
            "SHOULD-FIX: the plan lets a section pass while only quoting\n"
            f"`reviewer: {_SELF_ADAPTER}` or `skipped:` deep inside the prose."
        )
        assert find_gaps(self._body(section)) == [
            "Architect review (missing: reviewer provenance line)"
        ]
        assert architect_review_provenance(section) is None

    def test_unknown_adapter_name_is_a_gap(self) -> None:
        """The vocabulary is the role catalogue, so a carrier nobody declared is a gap."""
        gaps = find_gaps(self._body("reviewer: Some Other Agent\n\nfindings"))
        assert gaps == ["Architect review (missing: declared reviewer adapter)"]

    def test_reviewer_marker_without_adapter_is_a_gap(self) -> None:
        assert find_gaps(self._body("reviewer:\n\nfindings")) == [
            "Architect review (missing: declared reviewer adapter)"
        ]

    def test_marker_only_section_passes(self) -> None:
        """The gate does not judge how substantial the findings are — a deliberate
        boundary: scoring review quality by script is the false coverage this change
        argues against."""
        assert find_gaps(self._body(f"reviewer: {_SELF_ADAPTER}")) == []

    def test_independence_map_covers_the_declared_carriers(self) -> None:
        """The gate's vocabulary comes from the catalogue, not from a second copy."""
        assert reviewer_independence() == {
            _INDEPENDENT_ADAPTER: "independent",
            _SELF_ADAPTER: "self",
        }

    def test_self_review_reaches_the_operator_without_failing_validation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Passing silently would put self-review back where it started (§IV).

        Same shape as the orphan-scope reminder: the exit code stays 0 because
        self-review is a legitimate route; what changes is that it is said out loud.
        """
        monkeypatch.setattr(
            validator,
            "_fetch_issue",
            lambda _n: (self._body(f"reviewer: {_SELF_ADAPTER}\n\nfindings"), ()),
        )
        monkeypatch.setattr(sys, "argv", ["validate_issue_sections.py", "474"])

        validator.main()

        output = capsys.readouterr().out
        assert "ok: issue #474" in output
        assert "self-review" in output
        assert "not an independent check" in output

    def test_independent_review_says_nothing_extra(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(validator, "_fetch_issue", lambda _n: (_full_body(), ()))
        monkeypatch.setattr(sys, "argv", ["validate_issue_sections.py", "474"])

        validator.main()

        assert "self-review" not in capsys.readouterr().out


class TestAdrSection:
    """The `## ADR` gate (#426) requires either a record link or explicit `none`.

    As with `Architect review` (#150), whether a record is needed is a
    cost-of-change judgment. The gate therefore checks that the decision exists,
    not that it is correct, turning a fallible prose step into an exit code.
    """

    def test_adr_section_required(self) -> None:
        # All seven previous sections are filled; missing `ADR` must be a gap.
        body = _body_with((*_LEGACY_SECTIONS, "Architect review"))
        assert "ADR" in find_gaps(body)

    def test_adr_section_filled_passes(self) -> None:
        body = _body_with((*_LEGACY_SECTIONS, "Architect review", "ADR"))
        assert "ADR" not in find_gaps(body)


class TestAgentHandoffSection:
    def test_agent_handoff_required(self) -> None:
        body = _body_with((*_LEGACY_SECTIONS, "Architect review", "ADR"))
        assert "Agent handoff" in find_gaps(body)

    def test_agent_handoff_filled_passes(self) -> None:
        body = _body_with((*_LEGACY_SECTIONS, "Architect review", "ADR", "Agent handoff"))
        assert "Agent handoff" not in find_gaps(body)

    def test_agent_handoff_requires_all_handoff_fields(self) -> None:
        body = _full_body().replace("next role: implementer\n", "")
        assert find_gaps(body) == ["Agent handoff (missing: next role)"]


def _body_with_evidence(content: str) -> str:
    return f"{_full_body()}\n## Evidence\n\n{content}\n"


def _capture_evidence(path: str) -> str:
    return (
        "capture: `python scripts/capture_kinozal_fixture.py "
        f"https://kinozal.tv/details.php?id=1 {path}`\n"
        f"path: `{path}`"
    )


def test_bug_issue_without_evidence_section_is_a_gap(tmp_path: Path) -> None:
    gaps = find_gaps(_full_body(), issue_labels=("bug",), repo_root=tmp_path)
    assert "Evidence" in gaps


def test_evidence_naming_a_missing_path_is_a_gap(tmp_path: Path) -> None:
    body = _body_with_evidence(_capture_evidence("tests/fixtures/missing.html"))
    gaps = find_gaps(body, issue_labels=("bug",), repo_root=tmp_path)
    assert gaps == ["Evidence (missing: existing capture path or failed capture output)"]


def test_evidence_naming_an_existing_capture_passes(tmp_path: Path) -> None:
    capture = tmp_path / "tests" / "fixtures" / "captured.html"
    capture.parent.mkdir(parents=True)
    capture.write_text("<html>observed response</html>", encoding="utf-8")
    body = _body_with_evidence(_capture_evidence("tests/fixtures/captured.html"))
    assert find_gaps(body, issue_labels=("bug",), repo_root=tmp_path) == []


def test_evidence_accepts_a_source_specific_capture_command(tmp_path: Path) -> None:
    capture = tmp_path / "tests" / "fixtures" / "github" / "issue-509.json"
    capture.parent.mkdir(parents=True)
    capture.write_text('{"number": 509}', encoding="utf-8")
    relative_path = "tests/fixtures/github/issue-509.json"
    body = _body_with_evidence(
        "capture: `gh api repos/ekolvah/kinozal_scraper/issues/509 "
        f"> {relative_path}`\n"
        f"path: `{relative_path}`"
    )

    assert find_gaps(body, issue_labels=("bug",), repo_root=tmp_path) == []


def test_non_bug_issue_does_not_require_evidence(tmp_path: Path) -> None:
    assert find_gaps(_full_body(), issue_labels=("enhancement",), repo_root=tmp_path) == []


def test_non_external_bug_can_record_evidence_not_applicable(tmp_path: Path) -> None:
    body = _body_with_evidence(
        "n/a: the defect is pure review-gate exit-code logic with no external data source"
    )
    assert find_gaps(body, issue_labels=("bug",), repo_root=tmp_path) == []

    missing_reason = _body_with_evidence("n/a:")
    assert find_gaps(missing_reason, issue_labels=("bug",), repo_root=tmp_path) == [
        "Evidence (missing: n/a reason)"
    ]


def test_capture_failure_marker_requires_the_command_output(tmp_path: Path) -> None:
    evidence = _capture_evidence("tests/fixtures/source-unavailable.html")
    without_output = _body_with_evidence(f"{evidence}\nstatus: failed")
    assert find_gaps(without_output, issue_labels=("bug",), repo_root=tmp_path) == [
        "Evidence (missing: existing capture path or failed capture output)"
    ]

    with_output = _body_with_evidence(
        f"{evidence}\nstatus: failed\noutput:\n\n```text\n"
        "primary fetch failed; authenticated mirror login failed\n```"
    )
    assert find_gaps(with_output, issue_labels=("bug",), repo_root=tmp_path) == []


def test_capture_kinozal_fixture_writes_the_response_through_the_existing_fetcher(
    tmp_path: Path,
) -> None:
    capture_fixture = importlib.import_module("scripts.capture_kinozal_fixture")
    seen: list[str] = []

    class StubFetcher:
        def fetch_details(self, url: str) -> str:
            seen.append(url)
            return "<html>captured through\nKinozal.fetch_details</html>"

    target = tmp_path / "kinozal" / "details.html"
    source = "https://kinozal.tv/details.php?id=2112853"
    capture_fixture.capture(source, target, fetcher=StubFetcher())

    assert seen == [source]
    assert target.read_bytes() == b"<html>captured through\nKinozal.fetch_details</html>"


def test_new_external_data_parsing_test_with_inline_markup_is_reported() -> None:
    ratchet = importlib.import_module("scripts.check_fixture_ratchet")
    source = """
def test_parses_external_page():
    html = "<html><img class='cat_img_r' src='/pic/cat/6.gif'></html>"
    assert parse_page(html) == 6
"""
    assert ratchet.inline_external_data_test_nodes(
        source, path=Path("tests/test_new_source.py")
    ) == ["tests/test_new_source.py::test_parses_external_page"]

    repo_root = Path(__file__).resolve().parent.parent
    assert ratchet.scan_repository(repo_root) == []


def test_evidence_replay_has_positive_and_negative_arms() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    fixture_root = repo_root / "tests" / "fixtures" / "issue_509_rca"
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    for artifact in manifest["artifacts"]:
        payload = (fixture_root / artifact["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == "".join(artifact["sha256_chunks"])

    revision_one = (fixture_root / "issue_506_revision_1.txt").read_text(encoding="utf-8")
    assert "Evidence" in find_gaps(revision_one, issue_labels=("bug",), repo_root=repo_root)

    # The marker did not exist when #506 was planned, so AC3's negative replay is
    # the healthy archived body plus only the new mechanical marker. The observed
    # details-page facts remain the archived content, not a synthetic replacement.
    healthy = (fixture_root / "issue_506_final_item_level.txt").read_text(encoding="utf-8")
    # The unmodified current nine-section body remains valid when the conditional
    # bug Evidence rule is not requested.
    assert find_gaps(healthy) == []
    assert "### Live evidence" in healthy
    relative_capture = "tests/fixtures/issue_509_rca/issue_506_final_item_level.txt"
    healthy_with_marker = f"{healthy}\n## Evidence\n\n{_capture_evidence(relative_capture)}\n"
    assert find_gaps(healthy_with_marker, issue_labels=("bug",), repo_root=repo_root) == []


def test_main_passes_live_bug_label_to_evidence_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(validator, "_fetch_issue", lambda _n: (_full_body(), ("bug",)))
    monkeypatch.setattr(sys, "argv", ["validate_issue_sections.py", "509"])

    with pytest.raises(SystemExit) as exc:
        validator.main()

    assert exc.value.code == 1
    assert "Evidence" in capsys.readouterr().err


class TestOrphanScopeReminder:
    def test_valid_issue_surfaces_reminder_without_failing_validation(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        body = _full_body().replace(
            "Real content для Out of scope which is long enough.",
            "- Follow-up for the historical audit.",
        )
        monkeypatch.setattr(validator, "_fetch_issue", lambda _n: (body, ()))
        monkeypatch.setattr(sys, "argv", ["validate_issue_sections.py", "368"])

        validator.main()

        output = capsys.readouterr().out
        assert "ok: issue #368" in output
        assert "reminder" in output.lower()
        assert "Follow-up for the historical audit" in output


class TestFetchBodyEncoding:
    def test_cyrillic_body_decodes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cyrillic_body = "## Context / Why\n\nЭто кириллический контент с символом 0x81 в проблемной кодировке.\n"
        payload = '{"state": "OPEN", "body": ' + _json_string(cyrillic_body) + "}"

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            assert kwargs.get("encoding") == "utf-8", (
                "subprocess must request utf-8 to avoid cp1252 on Windows"
            )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=payload, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _fetch_body(122)
        assert "кириллический" in result


class TestFetchBodyFailures:
    @pytest.mark.parametrize(
        ("stdout", "stderr"),
        [
            (None, ""),
            ('{"state": "OPEN", "body": "valid"}', None),
        ],
    )
    def test_capture_failure_exits_two(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        stdout: str | None,
        stderr: str | None,
    ) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=stdout, stderr=stderr
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc:
            _fetch_body(413)

        assert exc.value.code == 2
        assert "capture failed" in capsys.readouterr().err

    def test_gh_failure_exits_two(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="network unavailable"
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc:
            _fetch_body(413)

        assert exc.value.code == 2
        error = capsys.readouterr().err
        assert "rc=1" in error
        assert "network unavailable" in error

    def test_malformed_payload_exits_two(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="not-json", stderr=""
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc:
            _fetch_body(413)

        assert exc.value.code == 2
        assert "invalid JSON" in capsys.readouterr().err


def _json_string(s: str) -> str:
    import json

    return json.dumps(s, ensure_ascii=False)
