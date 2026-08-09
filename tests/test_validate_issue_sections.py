"""Tests for `scripts/validate_issue_sections.py` — the plan-completeness gate.

Covers gap detection (missing, empty, whitespace-only, setext headings, headings
inside fenced blocks, an unterminated fence), the mandatory Architect review and
ADR sections, and the Cyrillic body decode.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

import scripts.validate_issue_sections as validator
from scripts.validate_issue_sections import (
    REQUIRED_SECTIONS,
    _fetch_body,
    _split_by_h2,
    find_gaps,
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


def _section_content(section: str) -> str:
    if section == "Agent handoff":
        return (
            "planner: Claude [model]\n"
            "validation: `python scripts/validate_issue_sections.py 1` — passed\n"
            "next role: implementer\n"
            "handoff: ready"
        )
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
        monkeypatch.setattr(validator, "_fetch_body", lambda _n: body)
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
