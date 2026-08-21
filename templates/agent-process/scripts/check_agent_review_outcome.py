"""Map a structured review outcome to a deterministic workflow result.

Merge authority is deliberately narrower than report coverage: `blocking`
reds the required check, `clean` and `rework` pass (the latter with a visible
`::warning::`), and every state that is *not* evidence — empty, malformed,
unknown outcome, or an unavailable live PR context — stays red. Absence of
evidence must never read as success (§IV).

The required check has two carriers, so this module also answers *whether*
a carrier produced a usable verdict at all: `--classify` measures without judging,
which is what the failover step gates on. That question lives here because the
validity rule lives here; asked as a YAML `contains()`/`fromJSON()` expression it
would become a second, untestable home for the same policy.

The rule has no path-based exception any more. A PR touching the review
controller used to pass with a `::warning::` on an empty outcome, because the
action could not review it at all: the App-token exchange refused a workflow file
that differs from `main`. The workflow now runs the review under `github.token`,
so such a PR gets an ordinary verdict — and an empty outcome is an unavailable
review here exactly as anywhere else.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from scripts.gh_io import publish_step_output

# Public: carrier 2 translates its own review states into this vocabulary,
# and a second private copy there would be a second merge bar.
VALID_OUTCOMES = frozenset({"clean", "rework", "blocking"})
_DEFAULT_PRODUCER = "Claude review"


class _Options:
    """Parsed CLI options; the payload itself is positional."""

    def __init__(self) -> None:
        self.live_pr_context_status: str | None = None
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

    if outcome == "clean":
        print(f"ok: {producer} outcome is clean")
        return
    if outcome == "rework":
        # Report completeness is not merge authority. The prompt requires
        # every finding to be reported, so a should-fix finding is the normal
        # outcome of a thorough review — reding the required check on it made a
        # green result unreachable by construction after repeated cosmetic-only
        # rounds. The findings stay visible in the PR and
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
