"""Map a Claude structured review outcome to a deterministic workflow result."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence

_REVIEW_CONTROLLER_PATHS = frozenset(
    {
        ".github/workflows/claude-review.yml",
        "scripts/check_claude_review_outcome.py",
    }
)


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


def fetch_changed_paths(repository: str, pr_number: int) -> list[str]:
    """Read every PR path so review-controller changes cannot hide in pagination."""
    endpoint = f"repos/{repository}/pulls/{pr_number}/files?per_page=100"
    result = subprocess.run(
        ["gh", "api", endpoint, "--paginate", "--slurp"],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0 or result.stdout is None:
        detail = result.stderr.strip() if result.stderr else "no stderr captured"
        raise RuntimeError(f"gh api {endpoint} failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api {endpoint} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(f"gh api {endpoint} returned an unexpected payload shape")
    pages = payload if all(isinstance(page, list) for page in payload) else [payload]
    records = [record for page in pages for record in page]
    if not all(isinstance(record, Mapping) for record in records):
        raise RuntimeError(f"gh api {endpoint} returned an unexpected payload shape")

    paths: list[str] = []
    for record in records:
        path = record.get("filename")
        if not isinstance(path, str):
            raise RuntimeError(f"gh api {endpoint} returned an unexpected payload shape")
        paths.append(path)
    return paths


def controller_changed(changed_paths: Sequence[str]) -> bool:
    """Return whether this PR changes the review-controller surface."""
    return bool(_REVIEW_CONTROLLER_PATHS.intersection(changed_paths))


def _is_controller_pr(repository: str | None, pr_number: str | None) -> bool:
    if (repository is None) != (pr_number is None):
        print("error: --repo OWNER/REPO and --pr NUMBER must be provided together", file=sys.stderr)
        raise SystemExit(2)
    if repository is None or pr_number is None:
        return False

    try:
        return controller_changed(fetch_changed_paths(repository, int(pr_number)))
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
        print(
            "::warning::Claude self-skipped this review-controller PR. The maintainer must perform "
            "a manual IDE-agent review before merge under the single-maintainer policy."
        )
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
