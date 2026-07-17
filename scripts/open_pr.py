#!/usr/bin/env python3
"""Create a PR that reliably auto-closes its issue, or fail visibly (#320).

Usage: python scripts/open_pr.py --title "<title>" [--body-file <path>]

Root cause it fixes (precedent #319 → issue #140 stayed open after merge):
PR→issue auto-linking hung on two fragile assumptions in `/implement`'s prose:
  1. a `(closes #N)` keyword in the *commit body* — squash-merge rebuilds the
     commit from the PR title and DROPS the feature-commit body, keyword and all;
  2. a hand-typed keyword in the PR body — which #319 wrote in Russian
     («Закрывает #140»), and GitHub only parses English `close/fix/resolve`.

An English `Closes #N` in the PR *body* survives squash (the linkage is computed
from the body at PR-creation time, not from any commit). So this script derives N
from the `issue-N-slug` branch (guaranteed by `issue_branch.py`), forces
`Closes #N` into the body, then reads back `closingIssuesReferences` and FAILS
exit 1 if empty — a broken link becomes a visible anomaly (§IV), not a silently
open issue after merge.

`gh`/`git` are the external boundary, run through a single `_run` seam so tests
mock `subprocess.run` (§II — not a mock of internal logic).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

ISSUE_BRANCH_RE = re.compile(r"^issue-(\d+)-")
# Placeholder line from pull_request_template.md: `Closes #`, `Closes #999`,
# `Closes #320 and …`. Anchored to line start (MULTILINE) so it never rewrites a
# `#N` mid-sentence; scoped to `Closes …` so a legitimate second `Refs #other`
# cross-ref is left intact.
CLOSES_LINE_RE = re.compile(r"^Closes #\d*.*$", re.MULTILINE)


def issue_number_from_branch(branch: str) -> int | None:
    """Extract N from an `issue-N-slug` branch; None for any other branch."""
    match = ISSUE_BRANCH_RE.match(branch.strip())
    return int(match.group(1)) if match else None


def ensure_closes_line(body: str, n: int) -> str:
    """Return `body` guaranteed to carry exactly `Closes #n` (idempotent).

    Rewrites the template's `Closes …` placeholder line (bare or wrong number);
    if there is none, prepends `Closes #n`."""
    target = f"Closes #{n}"
    if CLOSES_LINE_RE.search(body):
        return CLOSES_LINE_RE.sub(target, body, count=1)
    if target in body:
        return body
    return f"{target}\n\n{body}" if body else f"{target}\n"


def has_closing_reference(view_json: str) -> bool:
    """True iff `gh pr view --json closingIssuesReferences` reports ≥1 link.

    CLI shape (verified live) is a FLAT array — `{"closingIssuesReferences":
    [{number,url,…}]}` — with no `.nodes` wrapper (that is `gh api graphql`)."""
    data = json.loads(view_json)
    return bool(data.get("closingIssuesReferences"))


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")


def _current_branch() -> str:
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return (result.stdout or "").strip()


def _existing_pr(branch: str) -> dict | None:
    """The open PR for `branch` (url+body), or None if there is none yet.

    Makes the whole script idempotent: a re-run after a network blip or a
    verification-fail must not hard-fail on `gh pr create` (PR already exists)."""
    result = _run(["gh", "pr", "view", branch, "--json", "url,body"])
    if result.returncode != 0:
        return None
    loaded: dict = json.loads(result.stdout or "{}")
    return loaded


def _create_pr(title: str, body: str) -> str:
    result = _run(["gh", "pr", "create", "--base", "main", "--title", title, "--body", body])
    if result.returncode != 0:
        print((result.stderr or "").strip() or "error: gh pr create failed", file=sys.stderr)
        sys.exit(1)
    lines = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _edit_pr_body(url: str, body: str) -> None:
    _run(["gh", "pr", "edit", url, "--body", body])


def _closing_refs_json(url: str) -> str:
    result = _run(["gh", "pr", "view", url, "--json", "closingIssuesReferences"])
    return result.stdout or "{}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Open a PR that auto-closes its issue (#320).")
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--body-file", help="path to PR body (Summary prose); Closes #N is forced in"
    )
    ns = parser.parse_args(argv)

    branch = _current_branch()
    n = issue_number_from_branch(branch)
    if n is None:
        print(
            f"error: not an issue-N-slug branch (got {branch!r}); open the PR manually",
            file=sys.stderr,
        )
        sys.exit(2)

    existing = _existing_pr(branch)
    if existing is not None:
        url = existing["url"]
        current_body = existing.get("body") or ""
        fixed = ensure_closes_line(current_body, n)
        if fixed != current_body:
            _edit_pr_body(url, fixed)
    else:
        body = ""
        if ns.body_file:
            with open(ns.body_file, encoding="utf-8") as handle:
                body = handle.read()
        url = _create_pr(ns.title, ensure_closes_line(body, n))

    if not has_closing_reference(_closing_refs_json(url)):
        print(
            f"error: PR {url} created but issue #{n} is NOT linked "
            f"(closingIssuesReferences empty) — merge will not close it. "
            f"Add `Closes #{n}` to the PR body and re-run.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(url)


if __name__ == "__main__":
    main()
