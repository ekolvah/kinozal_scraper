from __future__ import annotations

import subprocess
from typing import Any

import pytest

from scripts.validate_issue_sections import (
    REQUIRED_SECTIONS,
    _fetch_body,
    find_gaps,
)


def _full_body() -> str:
    parts = []
    for s in REQUIRED_SECTIONS:
        parts.append(f"## {s}\n\nReal content для {s} which is long enough.\n")
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
    return "\n".join(
        f"## {s}\n\nReal content для {s} which is long enough.\n" for s in sections
    )


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
            f"## {s}\n\nReal content для {s} which is long enough.\n" for s in REQUIRED_SECTIONS[1:]
        )
        assert find_gaps(body) == ["Context / Why"]

    def test_whitespace_only_section_listed(self) -> None:
        body = "## Context / Why\n\n   \n\n" + "\n".join(
            f"## {s}\n\nReal content для {s} which is long enough.\n" for s in REQUIRED_SECTIONS[1:]
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


class TestArchitectReviewSection:
    """The `Architect review` gate (#150): every issue must carry the section,
    even if filled with an explicit `skipped: <reason>`. Guarantees the review
    is consciously decided, not silently forgotten."""

    def test_architect_review_required(self) -> None:
        # All six legacy sections filled, but no `Architect review` → must be a gap.
        body = _body_with(_LEGACY_SECTIONS)
        assert "Architect review" in find_gaps(body)

    def test_architect_review_filled_passes(self) -> None:
        body = _body_with((*_LEGACY_SECTIONS, "Architect review"))
        assert find_gaps(body) == []


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


def _json_string(s: str) -> str:
    import json

    return json.dumps(s, ensure_ascii=False)
