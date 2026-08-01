"""Map a Claude structured review outcome to a deterministic workflow result."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

try:  # module import in tests
    from scripts import check_claude_review as review_gate
except ModuleNotFoundError:  # direct `python scripts/...` workflow execution
    import check_claude_review as review_gate


def main(argv: Sequence[str] | None = None) -> None:
    """Exit cleanly only for Claude's validated ``clean`` outcome."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("error: expected one structured review outcome JSON value", file=sys.stderr)
        raise SystemExit(2)
    payload_arg = args.pop(0)
    if args:
        if len(args) != 4 or args[0] != "--repo" or args[2] != "--pr":
            print("error: expected optional --repo OWNER/REPO --pr NUMBER", file=sys.stderr)
            raise SystemExit(2)
        try:
            if review_gate.controller_changed(review_gate.fetch_changed_paths(args[1], int(args[3]))):
                print(
                    "::warning::controller PR did not run a self-review; bootstrap remains required"
                )
                return
        except (RuntimeError, ValueError) as exc:
            print(f"error: unable to classify review-controller PR: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
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
