#!/usr/bin/env python3
"""Create a PR that reliably auto-closes its issue, or fail visibly.

Usage: python scripts/open_pr.py --title "<title>" [--body-file <path>]

Root cause it fixes: an issue stayed open after merge because
PR→issue auto-linking hung on two fragile assumptions in the implementer prose:
  1. a `(closes #N)` keyword in the *commit body* — squash-merge rebuilds the
     commit from the PR title and DROPS the feature-commit body, keyword and all;
  2. a hand-typed localized keyword in the PR body, while GitHub only parses
     English `close/fix/resolve` keywords.

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
# (observed while dogfooding this script). Poll before declaring the link
# broken — otherwise the §IV guard fires false-positive on every PR.
#
# Budget sizing: the old ~8s window (5×2.0s) was exhausted during a slow
# closing-reference update. Widened to ~48s (12×4.0s) to cover the observed
# ~30–40s lag. The
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
    result = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
    # `None` with requested capture means the reader thread failed.
    # Each call site formerly used `or ""`, replacing failure with emptiness—“gh returned
    # nothing”—and letting the script proceed on phantom data. A nonzero return code is
    # NOT an exception: the caller decides, and its error-reporting path must execute.
    if result.stdout is None or result.stderr is None:
        # Code 2, as in sibling `verify_pr_link.py`: infrastructure failure must differ
        # from a verdict. Code 1 is used for legitimate outcomes (“PR NOT linked”,
        # “gh pr create failed”); capture failure must remain a distinct outcome.
        print(
            f"error: capture failed for `{' '.join(cmd)}` (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
            file=sys.stderr,
        )
        sys.exit(2)
    return result


def _current_branch() -> str:
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


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
    loaded: list[dict[str, Any]] = json.loads(result.stdout)
    return loaded[0] if loaded else None


def _create_pr(title: str, body: str) -> str:
    result = _run(["gh", "pr", "create", "--base", "main", "--title", title, "--body", body])
    if result.returncode != 0:
        # `or "error: …"` remains for LEGITIMATELY empty stderr (a command failed
        # silently), not as a capture-failure workaround; `_run` catches that.
        print(result.stderr.strip() or "error: gh pr create failed", file=sys.stderr)
        sys.exit(1)
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _edit_pr_body(url: str, body: str) -> None:
    _run(["gh", "pr", "edit", url, "--body", body])


def _closing_refs_json(url: str) -> str | None:
    """JSON with closing references, or `None` when it could not be read.

    Previously no return-code check meant failed `gh pr view` returned `"{}"`,
    indistinguishable from an unlinked PR; the script reached a false `NOT linked`
    verdict while the gh failure never reached the operator. Return `None`, not
    `sys.exit`: this call lives **inside the retry loop**, so transient rate limiting
    after PR creation costs one failed attempt rather than the whole run."""
    result = _run(["gh", "pr", "view", url, "--json", "closingIssuesReferences"])
    if result.returncode != 0:
        print(
            f"warn: `gh pr view {url}` failed (rc={result.returncode}): "
            f"{result.stderr.strip()} — linkage unread, retrying.",
            file=sys.stderr,
        )
        return None
    return result.stdout


def _linkage_confirmed(url: str) -> bool:
    """Poll `closingIssuesReferences` until it reports a link (or attempts run out).

    Tolerates GitHub's async computation of the link after PR creation."""
    last_read_ok = False
    for attempt in range(LINKAGE_ATTEMPTS):
        refs = _closing_refs_json(url)
        last_read_ok = refs is not None
        if refs is not None and has_closing_reference(refs):
            return True
        if attempt < LINKAGE_ATTEMPTS - 1:
            time.sleep(LINKAGE_DELAY_S)
    if not last_read_ok:
        # “No references” is valid only if the FINAL read succeeded. Any successful read
        # is insufficient: the first is normally empty because GitHub computes
        # `closingIssuesReferences` asynchronously. One early success plus later
        # failures means final state was never observed, so False would judge absent data.
        print(
            f"error: the final linkage read for {url} failed — linkage is unknown, not absent.",
            file=sys.stderr,
        )
        sys.exit(2)
    return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Open a PR that auto-closes its issue.")
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
