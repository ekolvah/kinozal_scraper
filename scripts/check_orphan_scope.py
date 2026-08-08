#!/usr/bin/env python3
"""Surface untracked follow-up promises from an issue's Out of scope section."""

from __future__ import annotations


def find_orphan_scope_reminders(body: str) -> list[str]:
    """Return actionable reminders for orphaned top-level scope bullets."""
    raise NotImplementedError


def _fetch_body(issue_number: int) -> str:
    """Read one open issue body through ``gh``."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    """Run the non-blocking reminder for one issue number."""
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
