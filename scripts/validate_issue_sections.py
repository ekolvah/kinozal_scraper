#!/usr/bin/env python3
"""Validate that a GitHub issue body contains all required sections.

Usage: python scripts/validate_issue_sections.py <issue-number>

Exits 0 if all required sections are present and non-empty. Otherwise
prints the list of gaps to stderr and exits 1. Consumed by `/plan` and
`/implement` so the agent does not have to "remember" the contract.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

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
)
MIN_CONTENT_CHARS = 5


def _split_by_h2(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current is not None:
                sections[current.lower()] = "\n".join(buf).strip()
            current = match.group(1)
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current.lower()] = "\n".join(buf).strip()
    return sections


def find_gaps(body: str) -> list[str]:
    sections = _split_by_h2(body)
    gaps: list[str] = []
    for name in REQUIRED_SECTIONS:
        content = sections.get(name.lower())
        if content is None or len(content) < MIN_CONTENT_CHARS:
            gaps.append(name)
    return gaps


def _fetch_body(issue_number: int) -> str:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "body,state"],
        check=True,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    stdout = result.stdout or ""
    data = json.loads(stdout)
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
        return
    print(f"error: issue #{n} missing/empty sections:", file=sys.stderr)
    for g in gaps:
        print(f"  - {g}", file=sys.stderr)
    print("run `/plan #" + str(n) + "` to fill them", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
