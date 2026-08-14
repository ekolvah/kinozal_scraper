#!/usr/bin/env python3
"""Move an issue's Status field in GitHub Project 1.

Usage: python scripts/set_issue_status.py <N> <planned|in-progress>  (N = bare issue number)

Why a script and not prose: the board has four Status options, and the two middle
ones have no built-in Project automation — there is no trigger for "issue body
passed validation" or "branch created". Writing them is the same deterministic
`gh project item-add` + `item-edit` pair `set_issue_priority.py` already runs, so
it becomes a script with an exit code rather than a numbered step in a runbook
(`principles.md` "Scripts over instructions"). `Todo` and `Done` are rejected:
the built-in Project workflows own them, and a second writer would only race
them.

The canonical CLI token is `in-progress`, never `"In Progress"`: an unquoted
two-word argument is an argparse error in both bash and PowerShell, and the
process documentation has to be copy-pasteable in both.

This module imports nothing from the repository. Its two callers reach it from
two different module-loading routes — an importlib-conditional import in
`validate_issue_sections.py`, an absolute-path load in `issue_branch.py` — and
the repo root is on `sys.path` for neither documented CLI. That is also why the
Project constants are duplicated from `set_issue_priority.py` instead of being
extracted into a shared module; `tests/test_set_issue_status.py` guards the
drift that duplication creates.

`set_status` raises instead of exiting, because it runs inside two other CLIs
whose exit codes keep their own meaning; only `main` turns a failure into a
non-zero exit. Any nonzero `gh` exit is surfaced (§IV) rather than being reported
as a set status. `gh` is the sole external boundary, run through a single `_run`
seam so tests mock `subprocess.run` (§II).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

# Sourced from `gh project field-list 1 --owner ekolvah` (Status single-select).
PROJECT_NUMBER = "1"
PROJECT_OWNER = "ekolvah"
PROJECT_ID = "PVT_kwHOApeba84BdVeE"
STATUS_FIELD_ID = "PVTSSF_lAHOApeba84BdVeEzhX3ujY"
# `Todo` (f75ad846) and `Done` (98236657) are deliberately absent: they are written by the
# built-in Project automations, so this map is also the rejection list.
OPTION_IDS = {
    "planned": "334e22ea",
    "in-progress": "47fc9ee4",
}


def option_id_for_status(status: str) -> str:
    """Map a board status token to its single-select option id.

    Unknown or process-external status → ValueError (visible), never a silent default (§IV)."""
    key = status.strip().lower()
    try:
        return OPTION_IDS[key]
    except KeyError:
        allowed = "/".join(OPTION_IDS)
        raise ValueError(
            f"unknown board status {status!r}; expected {allowed} "
            "(Todo/Done belong to the built-in Project workflows)"
        ) from None


def item_id_from_add_json(output: str | None) -> str:
    """Extract the project item id from `gh project item-add --format json` output.

    `item-add` on an issue that is already an item returns the same item id with exit 0, so
    this stays the id lookup for every later write, not only the first one."""
    try:
        data: dict[str, Any] = json.loads(output or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"could not parse `gh project item-add` output: {output!r}") from exc
    item_id = data.get("id")
    if not item_id:
        raise ValueError(f"`gh project item-add` returned no item id: {output!r}")
    return str(item_id)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
    # `None` means capture failed (#364), not “gh was silent.” A default here
    # would replace failure with emptiness—the exact problem #410 fixes.
    if result.stdout is None or result.stderr is None:
        raise RuntimeError(
            f"capture failed for `{' '.join(cmd)}` (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result


def _checked(cmd: list[str], what: str) -> str:
    """Run `cmd`; on non-zero exit raise with the captured stderr (§IV visible failure)."""
    result = _run(cmd)
    if result.returncode != 0:
        detail = result.stderr.strip() or "no stderr"
        raise RuntimeError(f"{what} failed (rc={result.returncode}): {detail}")
    return result.stdout


def _issue_url(n: int) -> str:
    out = _checked(["gh", "issue", "view", str(n), "--json", "url"], "gh issue view")
    try:
        return str(json.loads(out)["url"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"could not read issue #{n} url: {out!r}") from exc


def _item_add(url: str) -> str:
    out = _checked(
        [
            "gh",
            "project",
            "item-add",
            PROJECT_NUMBER,
            "--owner",
            PROJECT_OWNER,
            "--url",
            url,
            "--format",
            "json",
        ],
        "gh project item-add",
    )
    return item_id_from_add_json(out)


def _item_edit(item_id: str, option_id: str) -> None:
    _checked(
        [
            "gh",
            "project",
            "item-edit",
            "--id",
            item_id,
            "--field-id",
            STATUS_FIELD_ID,
            "--project-id",
            PROJECT_ID,
            "--single-select-option-id",
            option_id,
        ],
        "gh project item-edit",
    )


def set_status(issue_number: int, status: str) -> None:
    """Set the board Status of `issue_number`, raising on an unusable status or a `gh` failure."""
    # Resolved first, so an unsupported status costs no `gh` call at all.
    option_id = option_id_for_status(status)
    item_id = _item_add(_issue_url(issue_number))
    _item_edit(item_id, option_id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Move an issue's Status in Project 1 (#519).")
    parser.add_argument("issue", type=int, help="issue number")
    parser.add_argument("status", help="board status: " + " | ".join(OPTION_IDS))
    ns = parser.parse_args(argv)

    try:
        set_status(ns.issue, ns.status)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"ok: issue #{ns.issue} status set to {ns.status}")


if __name__ == "__main__":
    main()
