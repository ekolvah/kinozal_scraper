"""Shared local safety policy for agent adapters.

GitHub branch protection is the authoritative barrier. This module supplies
the same defense-in-depth command policy to adapters that can enforce it.
"""

from __future__ import annotations

import re

FORBIDDEN_COMMANDS: tuple[str, ...] = (
    "git push origin main",
    "git push origin HEAD:main",
    "git push --force",
    "git push --force-with-lease",
    "git push -f",
    "git push --no-verify",
    "git commit --no-verify",
    "git branch -D",
    "git reset --hard",
    "gh pr merge",
    "gh repo delete",
    "sleep",
)

_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str], bool], ...] = (
    (
        "git push to main",
        re.compile(r"(?:^|[;&|])\s*git\s+push\s+origin\s+(?:main|head:main)(?=\s|:|$)"),
        False,
    ),
    (
        "forced push",
        re.compile(
            r"\bgit\s+push\b[^\n]*\s--force(?:-with-lease)?(?:\s|=|$)"
            r"|\bgit\s+push\s+-f(?:\s|$)"
        ),
        False,
    ),
    ("push without verification", re.compile(r"\bgit\s+push\b[^\n]*\s--no-verify"), False),
    ("commit without verification", re.compile(r"\bgit\s+commit\b[^\n]*\s--no-verify"), False),
    ("forced branch deletion", re.compile(r"\bgit\s+branch\s+-D(?:\s|$)"), True),
    ("hard reset", re.compile(r"\bgit\s+reset\s+--hard(?:\s|$)"), False),
    ("self-merge", re.compile(r"\bgh\s+pr\s+merge(?:\s|$)"), False),
    ("repository deletion", re.compile(r"\bgh\s+repo\s+delete(?:\s|$)"), False),
    ("sleep polling", re.compile(r"(?:^|[;&|])\s*sleep(?:\s|$)"), False),
)


def denied_reason(command: str) -> str | None:
    """Return a user-visible reason when a command violates repository policy."""
    normalized = command.lower()
    for name, pattern, case_sensitive in _FORBIDDEN_PATTERNS:
        if pattern.search(command if case_sensitive else normalized):
            return f"Blocked by repository policy: {name}."
    return None
