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

No async-linkage poll (unlike `open_pr._linkage_confirmed`): by the time this job
checks out, installs Python and runs, GitHub's async `closingIssuesReferences`
computation (~seconds after PR creation) has long settled — CI startup latency
covers the race window, so a single read suffices.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from scripts.open_pr import has_closing_reference, issue_number_from_branch


def link_required_but_missing(branch: str, refs_json: str) -> bool:
    """True iff `branch` is an `issue-N` branch but its PR closes no issue.

    A non-issue branch (fork PR, dependabot, manual branch) is not required to
    close anything, so the gate is N/A there — returns False."""
    if issue_number_from_branch(branch) is None:
        return False
    return not has_closing_reference(refs_json)


def _refs_json(pr: str) -> str:
    result = subprocess.run(
        ["gh", "pr", "view", pr, "--json", "closingIssuesReferences"],
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    return result.stdout or "{}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="CI gate: PR from issue-N branch must close it.")
    parser.add_argument("--branch", required=True, help="PR head branch (github.head_ref)")
    parser.add_argument("--pr", required=True, help="PR number")
    ns = parser.parse_args(argv)

    if link_required_but_missing(ns.branch, _refs_json(ns.pr)):
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
