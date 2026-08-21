"""Decide whether the PR review/fix loop continues — with an exit code.

Run it as a module so the cross-script imports resolve:

    python -m scripts.review_gate <PR>

The stop rule («fix blocking findings; `should-fix` is the maintainer's call and
does not gate the loop») was prose in a long document and was repeatedly skipped;
later rounds only fixed defects introduced by an earlier round's own fix.
`principles.md` §Scripts over instructions names the
remedy — a deterministic step becomes a script with an exit code, not another
bullet. This gate replaces the existing «inspect the reviewer outcome» step; it
adds no round trip.

Severity comes from the place that already computes it deterministically: the
`agent-review` required context, whose conclusion `check_agent_review_outcome`
derives from the schema-validated outcome (`clean`/`rework` pass, `blocking`
reds). The review body is never parsed — a markdown parser would be a second,
fragile home for the same fact, and the should-fix list belongs to the human
reading the PR anyway.

`agent_orchestrator.decide()` is deliberately **not** reused. Reaching its one
relevant branch (`fixer_revisions >= max_runs`) would require synthesising
`plan_completed` / `architect_completed` / `implementation_completed` and a
stand-in `review_outcome` — factoids this gate never verified (§V) — plus three
dead route branches that would silently move the verdict whenever the router
changes. What is shared is *data*: the role catalogue and `REQUIRED_CONTEXTS`.
No policy gets a second home.

A PR touching the review controller is no longer a special case: the
review runs on it like on any other PR, so its green check carries the same
evidence and needs no escalation to a manual IDE review.

Scope note: `?branch=` counts every review run on that branch name, so a fork PR
or a reused branch name would over-count rounds. Neither exists in this
single-maintainer repository, whose branches are `issue-N-*` and created only by
`scripts/issue_branch.py`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from scripts.agent_orchestrator import load_catalog
from scripts.check_branch_protection import REQUIRED_CONTEXTS, REVIEW_CONTEXT

REVIEW_WORKFLOW_FILE = "agent-review.yml"
VERDICT_EXIT_CODES: dict[str, int] = {
    "ready-for-human": 0,
    "fix-blocking": 10,
    "escalate": 20,
    "review-pending": 30,
}
_NEXT_ACTIONS: dict[str, str] = {
    "ready-for-human": (
        "stop the loop and report the PR ready; any should-fix or nice-to-have finding "
        "is the maintainer's call, not another round"
    ),
    "fix-blocking": "make one minimal fixer commit, push it, then run this gate again",
    "escalate": "stop the loop and hand the named anomaly to the maintainer",
    "review-pending": (
        "wait once with `gh pr checks <PR> --watch`, then re-run this gate; a second "
        "review-pending goes to the maintainer — never a polling loop"
    ),
}
_PR_FIELDS = "state,url,headRefOid,headRefName,statusCheckRollup"


@dataclass(frozen=True)
class CheckRun:
    """One check-run on the PR head."""

    name: str
    status: str
    conclusion: str


@dataclass(frozen=True)
class ReviewEvidence:
    """Everything the verdict is derived from; no role or provider is involved."""

    head_sha: str
    checks: tuple[CheckRun, ...]
    reviewed_heads: frozenset[str]
    pr_url: str
    review_run_url: str | None


@dataclass(frozen=True)
class Verdict:
    """The loop decision plus the exit code that carries it."""

    name: str
    exit_code: int
    reason: str
    next_action: str


def fixer_budget(catalogue: dict[str, Any]) -> int:
    """Return the fixer revision cap from the role catalogue, never a literal."""
    return int(catalogue["roles"]["fixer"]["max_runs"])


def fixer_revisions(evidence: ReviewEvidence) -> int:
    """Rounds already spent: distinct heads reviewed, minus the first one.

    A re-run of the review on an unchanged head is the same round, so counting
    *distinct* heads keeps a transient infrastructure failure from eating the
    budget and escalating a PR that still had rounds left.
    """
    return max(0, len(evidence.reviewed_heads) - 1)


def _verdict(name: str, reason: str) -> Verdict:
    return Verdict(
        name=name,
        exit_code=VERDICT_EXIT_CODES[name],
        reason=reason,
        next_action=_NEXT_ACTIONS[name],
    )


def _red_reason(red: Sequence[str]) -> str:
    reason = f"red required checks on the current head: {', '.join(red)}"
    if REVIEW_CONTEXT in red:
        # The check-run conclusion cannot separate exit 1 (blocking findings)
        # from exit 2 (empty/malformed outcome, live PR context lost) in
        # check_agent_review_outcome. Both start the same way — open the run —
        # so the gate names the ambiguity instead of guessing (§IV).
        reason += (
            f"; a red {REVIEW_CONTEXT} means either blocking findings or a "
            "review unavailable (empty or malformed outcome, live PR context lost) — "
            "read the run before changing anything"
        )
    return reason


def evaluate(evidence: ReviewEvidence, fixer_budget: int) -> Verdict:
    """Return the loop verdict for the current PR head."""
    by_name = {check.name: check for check in evidence.checks}
    pending = [
        name
        for name in REQUIRED_CONTEXTS
        if name not in by_name or by_name[name].status != "COMPLETED"
    ]
    if pending:
        return _verdict(
            "review-pending",
            f"required checks are not final on {evidence.head_sha[:8]}: {', '.join(pending)}",
        )
    red = [name for name in REQUIRED_CONTEXTS if by_name[name].conclusion != "SUCCESS"]
    if red:
        spent = fixer_revisions(evidence)
        if spent >= fixer_budget:
            return _verdict(
                "escalate",
                f"fixer budget spent ({spent}/{fixer_budget}) while "
                f"{', '.join(red)} is still red — one more round is not routed",
            )
        return _verdict("fix-blocking", _red_reason(red))
    return _verdict(
        "ready-for-human",
        f"every required check is green on {evidence.head_sha[:8]}; no blocking finding",
    )


def _gh_json(args: list[str], description: str) -> Any:
    """Run a read-only `gh` command and parse its JSON, or exit 2.

    Exit 2 is deliberately outside the verdict table: a transport, auth, or
    capture failure is not evidence, and must never be reported as a loop
    decision. `stdout is None` means the reader died on decoding —
    it is an infrastructure failure, not an empty answer.
    """
    result = subprocess.run(args, text=True, capture_output=True, encoding="utf-8", check=False)
    if result.stdout is None or result.stderr is None:
        print(
            f"error: capture failed for `{description}` (rc={result.returncode})",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if result.returncode != 0:
        print(f"error: `{description}` failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: `{description}` returned invalid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _require(payload: Any, field: str, description: str) -> Any:
    if not isinstance(payload, dict) or field not in payload:
        print(f"error: `{description}` returned no {field}", file=sys.stderr)
        raise SystemExit(2)
    return payload[field]


def collect_evidence(pr: str) -> ReviewEvidence:
    """Read the live PR evidence the verdict needs; two read-only `gh` calls."""
    description = f"gh pr view {pr}"
    payload = _gh_json(["gh", "pr", "view", pr, "--json", _PR_FIELDS], description)
    state = _require(payload, "state", description)
    if state != "OPEN":
        print(
            f"error: PR #{pr} is {state}, not OPEN — the review/fix loop does not apply",
            file=sys.stderr,
        )
        raise SystemExit(2)
    head_sha = str(_require(payload, "headRefOid", description))
    branch = str(_require(payload, "headRefName", description))
    checks = tuple(
        CheckRun(
            name=str(entry.get("name", "")),
            status=str(entry.get("status", "")),
            conclusion=str(entry.get("conclusion", "")),
        )
        for entry in payload.get("statusCheckRollup") or ()
        # A legacy StatusContext carries no name/status/conclusion; keeping it
        # would look like a required context that never reported.
        if isinstance(entry, dict) and entry.get("__typename") == "CheckRun"
    )

    endpoint = (
        f"repos/{{owner}}/{{repo}}/actions/workflows/{REVIEW_WORKFLOW_FILE}/runs"
        f"?branch={quote(branch, safe='')}&status=completed&per_page=100"
    )
    runs_description = f"gh api {REVIEW_WORKFLOW_FILE} runs"
    runs = _require(
        _gh_json(["gh", "api", endpoint], runs_description), "workflow_runs", runs_description
    )
    reviewed_heads = frozenset(str(run["head_sha"]) for run in runs if run.get("head_sha"))
    review_run_url = next(
        (str(run.get("html_url")) for run in runs if run.get("head_sha") == head_sha), None
    )
    return ReviewEvidence(
        head_sha=head_sha,
        checks=checks,
        reviewed_heads=reviewed_heads,
        pr_url=str(payload.get("url", "")),
        review_run_url=review_run_url,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Print the verdict and exit with its code."""
    parser = argparse.ArgumentParser(description="Verdict on the PR review/fix loop.")
    parser.add_argument("pr", help="pull request number")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    evidence = collect_evidence(args.pr)
    budget = fixer_budget(load_catalog())
    verdict = evaluate(evidence, budget)

    print(f"verdict: {verdict.name}")
    print(f"reason: {verdict.reason}")
    print(f"next action: {verdict.next_action}")
    print(
        f"fixer revisions: {fixer_revisions(evidence)}/{budget} "
        f"(distinct heads reviewed by {REVIEW_CONTEXT}, minus the first; "
        "a re-run on an unchanged head does not count)"
    )
    print(f"PR: {evidence.pr_url}")
    print(f"review run: {evidence.review_run_url or 'not published for the current head'}")
    raise SystemExit(verdict.exit_code)


if __name__ == "__main__":
    main()
