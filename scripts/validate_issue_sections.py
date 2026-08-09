#!/usr/bin/env python3
"""Validate that a GitHub issue body contains all required sections.

Usage: python scripts/validate_issue_sections.py <issue-number>

Exits 0 if all required sections are present and non-empty. A passing issue may
also print a non-blocking reminder for an explicit Out of scope follow-up that
has neither an issue reference nor a wontfix/YAGNI decision (#368). Otherwise
prints the list of gaps to stderr and exits 1. Consumed by role adapters so an
agent does not have to "remember" the hand-off contract.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Sequence

from markdown_it import MarkdownIt

check_orphan_scope = importlib.import_module(
    f"{__package__}.check_orphan_scope" if __package__ else "check_orphan_scope"
)

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Context / Why",
    "Acceptance criteria",
    "Test plan",
    "Implementation outline",
    "Docs to update",
    "Out of scope",
    # Architect-review findings (or an explicit `skipped: <reason>`). Enforced as
    # a gate so the review is a consciously-decided step, never silently skipped
    # (#150). Persona lives in `.claude/agents/architect-reviewer.md`; criteria in
    # `docs/architecture/principles.md`.
    "Architect review",
    # Link to the MADR record this issue's decision lands in, or an explicit
    # `none: <reason>`. Same shape and same rationale as `Architect review`: whether
    # a decision *deserves* a record is a cost-of-change judgement no script can make,
    # so the gate enforces that the question was **answered**, not that the answer is
    # right (#426). Route and entry filter: `project-map.md` §Canonical-home.
    "ADR",
    # Machine-independent provenance of the planning hand-off. The section
    # records that a planner actually validated the artifact and intentionally
    # passes it to an implementer; it does not contain prompts or transcripts.
    "Agent handoff",
)
MIN_CONTENT_CHARS = 5

_MD = MarkdownIt("commonmark")


def _split_by_h2(body: str) -> dict[str, str]:
    """Sections headed by `## `, parsed by CommonMark.

    **Why not regexp.** Markdown is not regular: whether `## X` is a heading depends
    on context—a fenced code block, indented block, or HTML block. A custom line parser
    already caused a defect: `## <required-section name>` inside ``` created
    a second section with the same key and overwrote the real one with the rest of the block,
    so a filled section was reported empty and `/implement` aborted on a nonexistent
    problem (#426). Chasing it with patches is endless (the first patch itself
    regressed an unmatched fence), so parsing is delegated to the
    [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) — CommonMark-
    implementation already present transitively. A side benefit: it sees the document
    exactly as GitHub renders it, including `Text` + `---` as
    h2 because GitHub does.
    """
    lines = body.splitlines()
    tokens = _MD.parse(body)
    # (heading, heading-start line, content-start line)
    heads: list[tuple[str, int, int]] = [
        (tokens[i + 1].content.strip(), token.map[0], token.map[1])
        for i, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2" and token.map
    ]
    sections: dict[str, str] = {}
    for index, (title, _, content_start) in enumerate(heads):
        content_end = heads[index + 1][1] if index + 1 < len(heads) else len(lines)
        sections[title.lower()] = "\n".join(lines[content_start:content_end]).strip()
    return sections


def handoff_gaps(content: str) -> list[str]:
    """Return the missing provenance fields in an otherwise present hand-off."""
    normalized = "\n".join(line.strip().lower() for line in content.splitlines())
    required = {
        "planner": "planner:",
        "validation": "validation:",
        "validator command": "validate_issue_sections.py",
        "validation result": "passed",
        "next role": "next role: implementer",
        "handoff status": "handoff: ready",
    }
    return [name for name, marker in required.items() if marker not in normalized]


def find_gaps(body: str, required: Sequence[str] = REQUIRED_SECTIONS) -> list[str]:
    """Empty or missing sections from `required`.

    The set is a parameter rather than a module constant: the parser's other consumer is
    the MADR-record guard (`tests/test_adr_records.py`) with its own h2 list. Forking it
    would create a second definition of an empty section (#426).
    """
    sections = _split_by_h2(body)
    gaps: list[str] = []
    for name in required:
        content = sections.get(name.lower())
        if content is None or len(content) < MIN_CONTENT_CHARS:
            gaps.append(name)
        elif name == "Agent handoff":
            missing_fields = handoff_gaps(content)
            if missing_fields:
                gaps.append(f"{name} (missing: {', '.join(missing_fields)})")
    return gaps


def _fetch_body(issue_number: int) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "body,state"],
        check=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.stdout is None or result.stderr is None:
        print(
            f"error: capture failed for `gh issue view {issue_number}` (rc={result.returncode})",
            file=sys.stderr,
        )
        sys.exit(2)
    if result.returncode != 0:
        detail = result.stderr.strip() or "no stderr"
        print(
            f"error: `gh issue view {issue_number}` failed (rc={result.returncode}): {detail}",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"error: invalid JSON from `gh issue view {issue_number}`: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    if data.get("state") != "OPEN":
        print(
            f"error: issue #{issue_number} is not OPEN (state={data.get('state')})", file=sys.stderr
        )
        sys.exit(2)
    return data.get("body") or ""


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_issue_sections.py <issue-number>", file=sys.stderr)
        sys.exit(2)
    try:
        n = int(sys.argv[1])
    except ValueError:
        print(f"error: issue number must be int (got {sys.argv[1]!r})", file=sys.stderr)
        sys.exit(2)
    body = _fetch_body(n)
    gaps = find_gaps(body)
    if not gaps:
        print(f"ok: issue #{n} has all {len(REQUIRED_SECTIONS)} required sections")
        for reminder in check_orphan_scope.format_reminders(n, body):
            print(reminder)
        return
    print(f"error: issue #{n} missing/empty sections:", file=sys.stderr)
    for g in gaps:
        print(f"  - {g}", file=sys.stderr)
    # The most common way to “lose” many sections is an unclosed ```; under CommonMark it
    # consumes the rest of the document and GitHub renders it as gray code. Give a hint,
    # not a detector heuristic: implementing one would again guess author intent.
    print(
        "hint: если секции в body видны глазами — проверь незакрытый ``` выше них: "
        "остаток документа становится кодовым блоком и на GitHub тоже",
        file=sys.stderr,
    )
    print("run `/plan #" + str(n) + "` to fill them", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
