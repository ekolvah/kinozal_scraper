#!/usr/bin/env python3
"""Safely replace a delivery PR body while preserving its issue link.

Usage: python -m scripts.update_pr_body <PR> --body-file <path>

The command is intentionally separate from ``open_pr.py``: re-running the PR
creator must stay idempotent and must not replace an existing report from a
possibly stale local file. This updater reads the PR head, derives the issue
from its ``issue-N-*`` branch, writes one canonical ``Closes #N`` line, edits
through a UTF-8 temporary body file, and verifies GitHub's computed linkage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.open_pr import _linkage_confirmed, ensure_closes_line, issue_number_from_branch


def normalized_body(body: str, issue_number: int) -> str:
    """Return ``body`` with exactly one canonical issue-closing line."""
    target = f"Closes #{issue_number}"
    result: list[str] = []
    found = False
    for line in body.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        if content.strip() != target:
            result.append(line)
            continue
        if not found:
            result.append(target + ending)
            found = True
    return ensure_closes_line("".join(result), issue_number)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
    if result.stdout is None or result.stderr is None:
        raise RuntimeError(
            f"capture failed for `{' '.join(cmd)}` (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result


def _checked(cmd: list[str]) -> str:
    result = _run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or "command returned no error text"
        raise RuntimeError(f"`{' '.join(cmd)}` failed (rc={result.returncode}): {detail}")
    return result.stdout


def _pr_metadata(pr: str) -> tuple[str, str]:
    output = _checked(["gh", "pr", "view", pr, "--json", "headRefName,url"])
    try:
        data: dict[str, Any] = json.loads(output)
        branch = data["headRefName"]
        url = data["url"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"could not parse PR metadata: {output!r}") from exc
    if not isinstance(branch, str) or not branch or not isinstance(url, str) or not url:
        raise ValueError(f"PR metadata is missing headRefName/url: {output!r}")
    return branch, url


def _edit_body(url: str, body: str) -> None:
    with tempfile.TemporaryDirectory(prefix="update-pr-body-") as directory:
        body_file = Path(directory) / "body.md"
        body_file.write_text(body, encoding="utf-8")
        _checked(["gh", "pr", "edit", url, "--body-file", str(body_file)])


def main(argv: list[str] | None = None) -> None:
    """Update one PR from a UTF-8 body file, then verify its issue linkage."""
    parser = argparse.ArgumentParser(
        description="Replace a delivery PR body without losing its issue-closing link."
    )
    parser.add_argument("pr", help="PR number or URL")
    parser.add_argument("--body-file", required=True, help="UTF-8 Markdown report body")
    ns = parser.parse_args(argv)

    try:
        requested_body = Path(ns.body_file).read_text(encoding="utf-8")
        branch, url = _pr_metadata(ns.pr)
        issue_number = issue_number_from_branch(branch)
        if issue_number is None:
            print(
                f"error: PR {url} head is not an issue-N-slug branch (got {branch!r})",
                file=sys.stderr,
            )
            sys.exit(2)
        _edit_body(url, normalized_body(requested_body, issue_number))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    if not _linkage_confirmed(url):
        print(
            f"error: PR {url} body was updated but issue #{issue_number} is NOT linked "
            "(closingIssuesReferences empty).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(url)


if __name__ == "__main__":
    main()
