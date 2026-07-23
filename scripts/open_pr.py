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
import time
from typing import Any, cast

ISSUE_BRANCH_RE = re.compile(r"^issue-(\d+)-")
# GitHub computes closingIssuesReferences asynchronously after `gh pr create`, so
# the first read races and can report empty even for a correct `Closes #N` body
# (observed dogfooding this script on PR #321). Poll before declaring the link
# broken — otherwise the §IV guard fires false-positive on every PR.
#
# Budget sizing (#352): the old ~8s window (5×2.0s) was exhausted on PR #349,
# where indexing took ~30+s → false-positive `NOT linked` for a correct `Closes
# #308`. Widened to ~48s (12×4.0s) to cover the observed ~30–40s lag. The
# fast-path returns on the first non-empty read, so a healthy PR pays nothing;
# the wider budget only lengthens the worst case on a genuine failure — rare,
# since `ensure_closes_line` forces the keyword in, and non-destructive (the PR
# already exists and the script is idempotent on re-run).
LINKAGE_ATTEMPTS = 12
LINKAGE_DELAY_S = 4.0


def issue_number_from_branch(branch: str) -> int | None:
    """Extract N from an `issue-N-slug` branch; None for any other branch."""
    match = ISSUE_BRANCH_RE.match(branch.strip())
    return int(match.group(1)) if match else None


def ensure_closes_line(body: str, n: int) -> str:
    """Return `body` guaranteed to carry a `Closes #n` line (idempotent).

    The script authors the body, so it only ever ADDS its own canonical line — it
    never rewrites existing text. That deliberately drops the old regex placeholder
    surgery: no chance of clobbering a legitimate `Closes #other` (multi-issue PR)
    or swallowing a line tail. A bare `Closes #` template placeholder is left as-is
    — GitHub ignores a keyword with no number, so it is inert, not a false link."""
    target = f"Closes #{n}"
    if any(line.strip() == target for line in body.splitlines()):
        return body
    return f"{target}\n\n{body}" if body else f"{target}\n"


def has_closing_reference(view_json: str) -> bool:
    """True iff `closingIssuesReferences` reports ≥1 link.

    Tolerates BOTH shapes: the flat CLI array `{"closingIssuesReferences": [...]}`
    (current `gh pr view --json`) and the `{"nodes": [...]}` wrapper (GraphQL, and
    what a future `gh` could switch to). The flat form is undocumented CLI-specific
    behaviour, so pinning to it alone would let a `gh` upgrade silently break BOTH
    this check and the CI gate at once."""
    data: dict[str, Any] = json.loads(view_json)
    refs: Any = data.get("closingIssuesReferences")
    if isinstance(refs, dict):  # GraphQL-style `.nodes` wrapper
        return bool(cast("dict[str, Any]", refs).get("nodes"))
    return bool(refs)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")


def _current_branch() -> str:
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return (result.stdout or "").strip()


def _existing_pr(branch: str) -> dict[str, Any] | None:
    """The OPEN PR for `branch` (url+body), or None if there is none yet.

    Makes the whole script idempotent: a re-run after a network blip or a
    verification-fail must not hard-fail on `gh pr create` (PR already exists).

    Uses `gh pr list --state open`, NOT `gh pr view <branch>`: the latter also
    returns a CLOSED (not merged) PR of the same branch, and the script would then
    edit that dead PR and poll its linkage forever instead of opening a fresh one."""
    result = _run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "url,body"])
    if result.returncode != 0:
        return None
    loaded: list[dict[str, Any]] = json.loads(result.stdout or "[]")
    return loaded[0] if loaded else None


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


def _linkage_confirmed(url: str) -> bool:
    """Poll `closingIssuesReferences` until it reports a link (or attempts run out).

    Tolerates GitHub's async computation of the link after PR creation."""
    for attempt in range(LINKAGE_ATTEMPTS):
        if has_closing_reference(_closing_refs_json(url)):
            return True
        if attempt < LINKAGE_ATTEMPTS - 1:
            time.sleep(LINKAGE_DELAY_S)
    return False


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

    if not _linkage_confirmed(url):
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
