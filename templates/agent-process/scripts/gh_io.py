"""The GitHub boundary the review gate talks across: `gh` reads and step outputs.

Nothing here is policy. How a `gh` call fails (non-zero exit, or `stdout is None`
because the reader died on decoding), how `--paginate --slurp` shapes a
collection, and how a step publishes `key=value` for the next step are all
contracts GitHub owns, not this repo. Carrier 2 was about to make a second
copy of each; a second copy is a place for them to drift apart silently.

`scripts/review_gate.py` keeps its own reader on purpose: it answers a transport
failure with `SystemExit(2)` because there such a failure must never be reported
as a loop verdict, while callers here want the failure as a value to wrap.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping


def run_gh(args: list[str]) -> str:
    """Return `gh`'s stdout, or raise loudly if it cannot be read (§IV)."""
    result = subprocess.run(
        ["gh", *args],
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() if result.stderr else "no stderr captured"
        raise RuntimeError(f"gh {' '.join(args)} failed: {detail}")
    if result.stdout is None:
        raise RuntimeError(f"gh {' '.join(args)}: no stdout captured (broken decoding)")
    return result.stdout


def flatten_pages(payload: object) -> list[Mapping[str, object]]:
    """Flatten a `--slurp` result into records, refusing any other shape.

    `gh` slurps into a list of pages, but a single-page answer arrives as a bare
    list of records. Anything else — an error object, a scalar — is a shape change
    upstream, and must not read as «the collection is empty».
    """
    if not isinstance(payload, list):
        raise RuntimeError(f"unexpected payload shape from --slurp: {type(payload).__name__}")
    pages = payload if all(isinstance(page, list) for page in payload) else [payload]
    records = [record for page in pages for record in page]
    if not all(isinstance(record, Mapping) for record in records):
        raise RuntimeError("unexpected payload shape from --slurp: a record is not an object")
    return records


def slurp_records(endpoint: str) -> list[Mapping[str, object]]:
    """Read a paginated REST collection whole, so nothing hides behind pagination."""
    raw = run_gh(["api", endpoint, "--paginate", "--slurp"])
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh api {endpoint} returned invalid JSON: {exc}") from exc
    return flatten_pages(payload)


def publish_step_output(line: str) -> None:
    """Print `key=value` and append it to `$GITHUB_OUTPUT` when running in Actions.

    Outside Actions the variable is unset and printing is the whole job — that is
    not an error. Being unable to write the file that *is* set is: the next step
    would then read a missing output as an empty one.
    """
    print(line)
    destination = os.environ.get("GITHUB_OUTPUT")
    if not destination:
        return
    try:
        with open(destination, "a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")
    except OSError as exc:
        print(f"error: cannot write GITHUB_OUTPUT {destination!r}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
