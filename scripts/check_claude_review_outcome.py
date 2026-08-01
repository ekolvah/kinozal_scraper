"""Map a Claude structured review outcome to a deterministic workflow result."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    """Exit cleanly only for Claude's validated ``clean`` outcome."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("error: expected one structured review outcome JSON value", file=sys.stderr)
        raise SystemExit(2)
    try:
        payload = json.loads(args[0])
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
