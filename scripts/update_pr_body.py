#!/usr/bin/env python3
"""Safely replace a delivery PR body while preserving its issue link (#456)."""

from __future__ import annotations


def normalized_body(body: str, issue_number: int) -> str:
    """Return ``body`` with exactly one canonical issue-closing line."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> None:
    """Update one PR from a UTF-8 body file, then verify its issue linkage."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
