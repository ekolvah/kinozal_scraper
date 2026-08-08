"""Map a structured review outcome to a deterministic workflow result.

Merge authority is deliberately narrower than report coverage (#458): `blocking`
reds the required check, `clean` and `rework` pass (the latter with a visible
`::warning::`), and every state that is *not* evidence — empty, malformed,
unknown outcome, or an unavailable live PR context — stays red. Absence of
evidence must never read as success (§IV).

The required check has two carriers (#478), so this module also answers *whether*
a carrier produced a usable verdict at all: `--classify` measures without judging,
which is what the failover step gates on. That question lives here because the
validity rule lives here; asked as a YAML `contains()`/`fromJSON()` expression it
would become a second, untestable home for the same policy.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from scripts.gh_io import publish_step_output, slurp_records

_REVIEW_CONTROLLER_PATHS = frozenset(
    {
        ".github/workflows/claude-review.yml",
        "scripts/check_branch_protection.py",
        "scripts/check_claude_review_outcome.py",
        "scripts/request_codex_review.py",
    }
)
# Public: carrier 2 translates its own review states into this vocabulary (#478),
# and a second private copy there would be a second merge bar.
VALID_OUTCOMES = frozenset({"clean", "rework", "blocking"})
_DEFAULT_PRODUCER = "Claude review"


class _Options:
    """Parsed CLI options; the payload itself is positional."""

    def __init__(self) -> None:
        self.live_pr_context_status: str | None = None
        self.repository: str | None = None
        self.pr_number: str | None = None
        self.producer: str = _DEFAULT_PRODUCER
        self.classify: bool = False


def _parse_options(args: list[str]) -> _Options:
    options = _Options()
    while args:
        option = args.pop(0)
        if option == "--classify":
            options.classify = True
            continue
        if not args:
            print(f"error: expected a value after {option}", file=sys.stderr)
            raise SystemExit(2)
        value = args.pop(0)
        if option == "--live-pr-context-status":
            options.live_pr_context_status = value
        elif option == "--repo":
            options.repository = value
        elif option == "--pr":
            options.pr_number = value
        elif option == "--producer":
            options.producer = value
        else:
            print(f"error: unexpected argument {option}", file=sys.stderr)
            raise SystemExit(2)

    return options


def _require_live_pr_context(status: str | None) -> None:
    if status is not None and status != "success":
        print(
            "error: live PR context is unavailable; inspect 'Fetch current PR context' "
            "and re-run after GitHub API access recovers.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def fetch_changed_paths(repository: str, pr_number: int) -> list[str]:
    """Read every PR path so review-controller changes cannot hide in pagination."""
    endpoint = f"repos/{repository}/pulls/{pr_number}/files?per_page=100"
    paths: list[str] = []
    for record in slurp_records(endpoint):
        path = record.get("filename")
        if not isinstance(path, str):
            raise RuntimeError(f"gh api {endpoint} returned an unexpected payload shape")
        paths.append(path)
    return paths


def controller_changed(changed_paths: Sequence[str]) -> bool:
    """Return whether this PR changes the review-controller surface."""
    return bool(_REVIEW_CONTROLLER_PATHS.intersection(changed_paths))


def _validate_controller_options(repository: str | None, pr_number: str | None) -> None:
    """Reject a partial or malformed controller-classification request."""
    if (repository is None) != (pr_number is None):
        print("error: --repo OWNER/REPO and --pr NUMBER must be provided together", file=sys.stderr)
        raise SystemExit(2)
    if pr_number is not None:
        try:
            int(pr_number)
        except ValueError as exc:
            print("error: --pr must be an integer", file=sys.stderr)
            raise SystemExit(2) from exc


def _is_controller_pr(repository: str | None, pr_number: str | None) -> bool:
    _validate_controller_options(repository, pr_number)
    if repository is None or pr_number is None:
        return False

    try:
        return controller_changed(fetch_changed_paths(repository, int(pr_number)))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: unable to classify review-controller PR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _report_validity(outcome: object) -> None:
    """Publish «did this carrier produce a usable verdict» and exit 0 either way.

    Measuring is not judging: a non-zero exit here would end the job before the
    second carrier was ever asked, and a `blocking` verdict is a result — treating
    it as invalid would let the failover overrule the carrier that found it.
    """
    publish_step_output(f"valid={'true' if outcome in VALID_OUTCOMES else 'false'}")


def main(argv: Sequence[str] | None = None) -> None:
    """Exit non-zero unless the carrier's validated outcome is ``clean`` or ``rework``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("error: expected one structured review outcome JSON value", file=sys.stderr)
        raise SystemExit(2)
    payload_arg = args.pop(0)
    options = _parse_options(args)

    try:
        payload = json.loads(payload_arg)
    except json.JSONDecodeError:
        payload = None
    outcome = payload.get("outcome") if isinstance(payload, dict) else None

    if options.classify:
        _report_validity(outcome)
        return

    producer = options.producer
    _require_live_pr_context(options.live_pr_context_status)
    _validate_controller_options(options.repository, options.pr_number)

    if payload_arg == "" and _is_controller_pr(options.repository, options.pr_number):
        print(
            "::warning::No structured review outcome was produced for this review-controller PR. "
            "If the review step failed, re-run it; otherwise complete the manual IDE-agent review "
            "before merge under the single-maintainer policy."
        )
        return
    if outcome == "clean":
        print(f"ok: {producer} outcome is clean")
        return
    if outcome == "rework":
        # #458: report completeness is not merge authority. The prompt requires
        # every finding to be reported, so a should-fix finding is the normal
        # outcome of a thorough review — reding the required check on it made a
        # green result unreachable by construction (ten rounds on PR #462, the
        # last four purely cosmetic). The findings stay visible in the PR and
        # become the maintainer's call, not an automatic barrier.
        print(
            f"::warning::{producer} reported should-fix findings. They are published "
            "in the PR and are the maintainer's call — not an automatic merge blocker. "
            "Only blocking findings red this check."
        )
        return
    if outcome == "blocking":
        print(
            f"error: {producer} reported blocking findings; resolve and re-run review.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    # Naming the carrier matters most here: two carriers whose «unavailable» reads
    # identically leave the operator unable to tell which one came back empty (§IV).
    print(f"error: {producer} unavailable: no valid structured outcome.", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
