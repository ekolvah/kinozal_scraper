#!/usr/bin/env python3
"""Create or update the current issue PR from a fixed workspace report.

Usage: python scripts/publish_pr_report.py

The fixed `.codex/pr-body.md` path prevents an allowlisted command from reading
an arbitrary host file outside the workspace. Repository identity, branch,
issue, PR, and title are all derived and validated rather than accepted as
caller-controlled arguments.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# The documented entry point is ``python scripts/publish_pr_report.py``. In
# that form Python puts ``scripts/`` rather than the repository root on
# ``sys.path``, so make the package imports below work exactly as documented.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_open_pr = importlib.import_module("scripts.open_pr")
_push_issue_branch = importlib.import_module("scripts.push_issue_branch")
_update_pr_body = importlib.import_module("scripts.update_pr_body")

_create_pr = vars(_open_pr)["_create_pr"]
_existing_pr = vars(_open_pr)["_existing_pr"]
_linkage_confirmed = vars(_open_pr)["_linkage_confirmed"]
ensure_closes_line = _open_pr.ensure_closes_line
issue_number_from_branch = _open_pr.issue_number_from_branch
_capture = vars(_push_issue_branch)["_capture"]
push_command = _push_issue_branch.push_command
_edit_body = vars(_update_pr_body)["_edit_body"]
normalized_body = _update_pr_body.normalized_body

REPORT_PATH = REPO_ROOT / ".codex" / "pr-body.md"


def read_report(path: Path) -> str:
    """Read a non-empty regular report without following a file symlink."""
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"report must be a regular non-symlink file: {path}")
    body = path.read_text(encoding="utf-8")
    if not body.strip():
        raise ValueError(f"report is empty: {path}")
    return body


def _issue_title(issue_number: int) -> str:
    output = _capture(["gh", "issue", "view", str(issue_number), "--json", "title"])
    try:
        data: dict[str, Any] = json.loads(output)
        title = data["title"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"could not parse issue title: {output!r}") from exc
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"issue title is missing: {output!r}")
    return title.strip()


def main(argv: Sequence[str] | None = None) -> None:
    """Publish the fixed report to the PR whose head is the current issue branch."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("Usage: python scripts/publish_pr_report.py", file=sys.stderr)
        raise SystemExit(2)
    try:
        body = read_report(REPORT_PATH)
        remote_url = _capture(["git", "remote", "get-url", "origin"])
        branch = _capture(["git", "branch", "--show-current"])
        worktree_status = _capture(["git", "status", "--porcelain"])
        push_command(
            remote_url=remote_url,
            branch=branch,
            worktree_status=worktree_status,
        )
        issue_number = issue_number_from_branch(branch)
        if issue_number is None:
            raise ValueError(f"current branch is not an issue-N branch: {branch!r}")

        existing = _existing_pr(branch)
        if existing is None:
            title = _issue_title(issue_number)
            url = _create_pr(title, ensure_closes_line(body, issue_number))
        else:
            url = existing["url"]
            _edit_body(url, normalized_body(body, issue_number))
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if not _linkage_confirmed(url):
        print(
            f"error: PR {url} is not linked to issue #{issue_number}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print(url)


if __name__ == "__main__":
    main()
