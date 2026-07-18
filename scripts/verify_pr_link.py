#!/usr/bin/env python3
"""CI gate: a PR from an `issue-N-slug` branch MUST close its issue (#320).

Run in CI as a module so `from scripts.open_pr import …` resolves:

    python -m scripts.verify_pr_link --branch "$HEAD_REF" --pr "$PR_NUMBER"

(`python scripts/verify_pr_link.py` would break the cross-script import — repo
root is not on `sys.path` then, same trap `issue_branch.py` documents.)

`open_pr.py` makes the right path cheap at PR-creation time, but it is invoked by
prose in `/implement` — an agent can forget it and `gh pr create` by hand,
re-opening #319 (issue #140 stayed open after merge). This gate makes the
invariant NON-bypassable: as a required check it fails the PR — and blocks the
merge — whenever an `issue-N` branch's PR closes no issue, regardless of HOW the
PR was created. It reuses `open_pr`'s pure `issue_number_from_branch` +
`has_closing_reference` (no duplicated parsing).

Polls `closingIssuesReferences` (reusing `open_pr`'s attempt/delay budget) rather
than reading once: GitHub computes the linkage asynchronously after PR creation,
and on the `opened` event this required check can race ahead of that computation
on a warm runner — a single read would then false-red a correctly-linked PR and
block its merge. Betting "CI startup latency covers the window" is a hope, not a
guarantee; the same poll `open_pr` needs at creation time, the gate needs too.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from scripts.open_pr import (
    LINKAGE_ATTEMPTS,
    LINKAGE_DELAY_S,
    has_closing_reference,
    issue_number_from_branch,
)


def link_required_but_missing(branch: str, refs_json: str) -> bool:
    """True iff `branch` is an `issue-N` branch but its PR closes no issue.

    A non-issue branch (fork PR, dependabot, manual branch) is not required to
    close anything, so the gate is N/A there — returns False."""
    if issue_number_from_branch(branch) is None:
        return False
    return not has_closing_reference(refs_json)


def _refs_json(pr: str) -> str:
    """Fetch `closingIssuesReferences` JSON for the PR, or exit 2 on a `gh` failure.

    Distinct exit 2 (infra/tool failure), NOT the empty `"{}"` fallback: as a
    required merge-blocking check, a transient `gh` error (auth/rate-limit/network)
    must not be misattributed as a real missing-linkage (exit 1) — that would fail
    the PR with a false diagnosis (§IV)."""
    result = subprocess.run(
        ["gh", "pr", "view", pr, "--json", "closingIssuesReferences"],
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(
            f"error: `gh pr view {pr}` failed (rc={result.returncode}): "
            f"{(result.stderr or '').strip()} — cannot verify PR→issue link.",
            file=sys.stderr,
        )
        sys.exit(2)
    return result.stdout or "{}"


def _link_missing_after_poll(branch: str, pr: str) -> bool:
    """True iff `branch` is an issue-N branch whose PR still shows no link after
    polling. A non-issue branch is N/A → no `gh` call, no poll. Wraps the pure
    `link_required_but_missing` with re-fetch/backoff to tolerate GitHub's async
    linkage computation; a `gh` failure inside `_refs_json` still exits 2."""
    if issue_number_from_branch(branch) is None:
        return False
    for attempt in range(LINKAGE_ATTEMPTS):
        if not link_required_but_missing(branch, _refs_json(pr)):
            return False
        if attempt < LINKAGE_ATTEMPTS - 1:
            time.sleep(LINKAGE_DELAY_S)
    return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="CI gate: PR from issue-N branch must close it.")
    parser.add_argument("--branch", required=True, help="PR head branch (github.head_ref)")
    parser.add_argument("--pr", required=True, help="PR number")
    ns = parser.parse_args(argv)

    if _link_missing_after_poll(ns.branch, ns.pr):
        n = issue_number_from_branch(ns.branch)
        print(
            f"error: PR #{ns.pr} from branch {ns.branch!r} does NOT close issue #{n} "
            f"(closingIssuesReferences empty) — it will stay open after merge. "
            f"Add `Closes #{n}` to the PR body (or use `python scripts/open_pr.py`).",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"ok: PR link check passed for branch {ns.branch!r}")


if __name__ == "__main__":
    main()
