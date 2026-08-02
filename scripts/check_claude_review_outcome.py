"""Map a Claude structured review outcome to a deterministic workflow result."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from scripts import check_claude_review as review_gate


def _parse_options(args: list[str]) -> tuple[str | None, str | None, str | None]:
    live_pr_context_status: str | None = None
    repository: str | None = None
    pr_number: str | None = None
    while args:
        option = args.pop(0)
        if not args:
            print(f"error: expected a value after {option}", file=sys.stderr)
            raise SystemExit(2)
        value = args.pop(0)
        if option == "--live-pr-context-status":
            live_pr_context_status = value
        elif option == "--repo":
            repository = value
        elif option == "--pr":
            pr_number = value
        else:
            print(f"error: unexpected argument {option}", file=sys.stderr)
            raise SystemExit(2)

    return live_pr_context_status, repository, pr_number


def _require_live_pr_context(status: str | None) -> None:
    if status is not None and status != "success":
        print(
            "error: live PR context is unavailable; inspect 'Fetch current PR context' "
            "and re-run after GitHub API access recovers.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _is_controller_pr(repository: str | None, pr_number: str | None) -> bool:
    if (repository is None) != (pr_number is None):
        print("error: --repo OWNER/REPO and --pr NUMBER must be provided together", file=sys.stderr)
        raise SystemExit(2)
    if repository is None or pr_number is None:
        return False

    try:
        return review_gate.controller_changed(
            review_gate.fetch_changed_paths(repository, int(pr_number))
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: unable to classify review-controller PR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main(argv: Sequence[str] | None = None) -> None:
    """Exit cleanly only for Claude's validated ``clean`` outcome."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("error: expected one structured review outcome JSON value", file=sys.stderr)
        raise SystemExit(2)
    payload_arg = args.pop(0)
    live_pr_context_status, repository, pr_number = _parse_options(args)
    _require_live_pr_context(live_pr_context_status)

    if _is_controller_pr(repository, pr_number):
        print("::warning::controller PR did not run a self-review; bootstrap remains required")
        return
    try:
        payload = json.loads(payload_arg)
    except json.JSONDecodeError:
        payload = None
    outcome = payload.get("outcome") if isinstance(payload, dict) else None
    if outcome == "clean":
        print("ok: Claude review outcome is clean")
        return
    if outcome in {"rework", "blocking"}:
        print(
            f"error: Claude review reported {outcome} findings; resolve and re-run review.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    print("error: Claude review unavailable: no valid structured outcome.", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
