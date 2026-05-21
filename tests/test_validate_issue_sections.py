from __future__ import annotations

from scripts.validate_issue_sections import REQUIRED_SECTIONS, find_gaps


def _full_body() -> str:
    parts = []
    for s in REQUIRED_SECTIONS:
        parts.append(f"## {s}\n\nReal content для {s} which is long enough.\n")
    return "\n".join(parts)


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

    def test_all_six_sections_missing_returns_all_six(self) -> None:
        assert find_gaps("") == list(REQUIRED_SECTIONS)

    def test_case_insensitive_header_match(self) -> None:
        body = _full_body().replace("## Context / Why", "## context / why")
        assert find_gaps(body) == []

    def test_extra_section_ignored(self) -> None:
        body = _full_body() + "\n## Extra\n\nNot required.\n"
        assert find_gaps(body) == []
