#!/usr/bin/env python3
"""Set an issue's Status field in GitHub Project 1.

Usage: python scripts/set_issue_status.py <N> <planned|in-progress>

Stub: the RED tests in `tests/test_set_issue_status.py` define the contract.
"""

from __future__ import annotations

PROJECT_NUMBER = ""
PROJECT_OWNER = ""
PROJECT_ID = ""
STATUS_FIELD_ID = ""
OPTION_IDS: dict[str, str] = {}


def option_id_for_status(status: str) -> str:
    raise NotImplementedError


def set_status(issue_number: int, status: str) -> None:
    raise NotImplementedError


def main(argv: list[str] | None = None) -> None:
    raise NotImplementedError


if __name__ == "__main__":
    main()
