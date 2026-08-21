#!/usr/bin/env python3
"""Surface untracked follow-up promises from an issue's Out of scope section."""

from __future__ import annotations

import json
import re
import subprocess
import sys

from markdown_it import MarkdownIt

_MD = MarkdownIt("commonmark")
_FOLLOW_UP_RE = re.compile(
    r"\bfollow(?:-|\s)?up\b"
    r"|\bseparate\s+(?:issue|ticket)\b"
    r"|\bотдельн\w*\s+(?:issue|тикет\w*|задач\w*)\b",
    re.IGNORECASE,
)
_ISSUE_REFERENCE_RE = re.compile(r"#\d+\b")
_REJECTION_RE = re.compile(r"\bwontfix\b|\bwon['’]?t\s+fix\b|\byagni\b", re.IGNORECASE)


def _out_of_scope_token_range(body: str) -> list:
    """Return tokens after the Out of scope h2 and before the next h2."""
    tokens = _MD.parse(body)
    start: int | None = None
    for index, token in enumerate(tokens):
        if token.type != "heading_open" or token.tag != "h2":
            continue
        title = tokens[index + 1].content.strip().lower()
        if start is None:
            if title == "out of scope":
                start = index + 3
            continue
        return tokens[start:index]
    return [] if start is None else tokens[start:]


def _top_level_bullets(body: str) -> list[str]:
    """Extract direct prose from top-level list items in Out of scope."""
    tokens = _out_of_scope_token_range(body)
    bullets: list[str] = []
    current: list[str] | None = None
    for token in tokens:
        if token.type == "list_item_open" and token.level == 1:
            current = []
        elif token.type == "list_item_close" and token.level == 1:
            if current is not None:
                text = " ".join(" ".join(current).split())
                if text:
                    bullets.append(text)
            current = None
        elif current is not None and token.type == "inline" and token.level == 3:
            current.append(token.content)
    return bullets


def find_orphan_scope_reminders(body: str) -> list[str]:
    """Return actionable reminders for orphaned top-level scope bullets."""
    return [
        bullet
        for bullet in _top_level_bullets(body)
        if _FOLLOW_UP_RE.search(bullet)
        and not _ISSUE_REFERENCE_RE.search(bullet)
        and not _REJECTION_RE.search(bullet)
    ]


def format_reminders(issue_number: int, body: str) -> list[str]:
    """Format reminder findings for a hand-off transcript."""
    return [
        f"reminder: issue #{issue_number} Out of scope follow-up has no #N/wontfix/YAGNI: {bullet}"
        for bullet in find_orphan_scope_reminders(body)
    ]


def _fetch_body(issue_number: int) -> str:
    """Read one open issue body through ``gh``."""
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "body,state"],
        check=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.stdout is None or result.stderr is None:
        raise RuntimeError(f"capture failed for `gh issue view {issue_number}`")
    if result.returncode != 0:
        detail = result.stderr.strip() or "no stderr"
        raise RuntimeError(f"`gh issue view {issue_number}` failed: {detail}")
    data = json.loads(result.stdout)
    if data.get("state") != "OPEN":
        raise RuntimeError(f"issue #{issue_number} is not OPEN (state={data.get('state')})")
    return data.get("body") or ""


def main(argv: list[str] | None = None) -> int:
    """Run the non-blocking reminder for one issue number."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage: python scripts/check_orphan_scope.py <issue-number>", file=sys.stderr)
        return 2
    try:
        issue_number = int(args[0])
    except ValueError:
        print(f"error: issue number must be int (got {args[0]!r})", file=sys.stderr)
        return 2
    try:
        body = _fetch_body(issue_number)
    except (json.JSONDecodeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    reminders = format_reminders(issue_number, body)
    if reminders:
        print("\n".join(reminders))
    else:
        print(f"ok: issue #{issue_number} has no orphaned scope reminders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
