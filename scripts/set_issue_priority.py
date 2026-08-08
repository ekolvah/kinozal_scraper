#!/usr/bin/env python3
"""Set or verify an issue's Priority field in GitHub Project 1.

Usage: python scripts/set_issue_priority.py <N> <High|Medium|Low>  (N = bare issue number)
       python scripts/set_issue_priority.py <N> --check

Why a script and not prose: an issue's priority lives as the single-select
**Priority** field of Project 1 ("kinozal_scraper — backlog & priority"), set via
two `gh project` calls (add the issue to the project, then edit the field). That
deterministic multi-step gh sequence used to live only in private agent memory —
a violation of the Memory↔repo policy (`docs/architecture/project-map.md`) and of
the `principles.md` "Scripts over instructions" canon (prose steps get skipped in long
pipelines). The agent process binds it: on issue creation the
agent asks the user for the priority, then runs this script.

The read-only ``--check`` mode is the implementation pre-flight gate (#376):
it proves that the selected issue has a supported Priority before a branch is
created. It matches the exact issue URL, not only its number, because a user
project can contain issues from more than one repository.

The Project/field/option IDs are hardcoded constants here (not in prose, not in
memory) — and because that hardcoding is this script's main drift source, ANY
non-zero `gh` exit (a stale option-id, revoked Project access, auth) is surfaced as
a visible anomaly (§IV): the script prints stderr and exits non-zero rather than
printing a false "priority set" confirmation. `gh` is the sole external boundary,
run through a single `_run` seam so tests mock `subprocess.run` (§II).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

# Sourced from `gh project field-list 1 --owner ekolvah` (Priority single-select).
PROJECT_NUMBER = "1"
PROJECT_OWNER = "ekolvah"
PROJECT_ID = "PVT_kwHOApeba84BdVeE"
PRIORITY_FIELD_ID = "PVTSSF_lAHOApeba84BdVeEzhX3uqs"
OPTION_IDS = {
    "high": "b9005885",
    "medium": "ca573e2f",
    "low": "3a2c2352",
}


def option_id_for_level(level: str) -> str:
    """Map a priority level (case-insensitive) to its single-select option id.

    Unknown level → ValueError (visible), never a silent default (§IV)."""
    key = level.strip().lower()
    try:
        return OPTION_IDS[key]
    except KeyError:
        allowed = "/".join(name.capitalize() for name in OPTION_IDS)
        raise ValueError(f"unknown priority level {level!r}; expected {allowed}") from None


def item_id_from_add_json(output: str | None) -> str:
    """Extract the project item id from `gh project item-add --format json` output.

    Tolerates `stdout=None` (грабля #109: Windows+git-bash can hand back None even
    with text=True) and any missing/blank/malformed payload → ValueError, so a broken
    add is a visible error, not a later opaque TypeError on a None item id."""
    try:
        data: dict[str, Any] = json.loads(output or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"could not parse `gh project item-add` output: {output!r}") from exc
    item_id = data.get("id")
    if not item_id:
        raise ValueError(f"`gh project item-add` returned no item id: {output!r}")
    return str(item_id)


def priority_from_project_json(output: str | None, issue_url: str) -> str | None:
    """Return the Priority for ``issue_url`` from ``gh project item-list`` JSON."""
    try:
        data: Any = json.loads(output or "")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"could not parse `gh project item-list` output: {output!r}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError(f"`gh project item-list` returned no items list: {output!r}")

    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError(f"`gh project item-list` returned a malformed item: {item!r}")
        content = item.get("content")
        if not isinstance(content, dict) or content.get("url") != issue_url:
            continue
        priority = item.get("priority")
        if priority is None or priority == "":
            return None
        if not isinstance(priority, str) or priority.lower() not in OPTION_IDS:
            allowed = "/".join(name.capitalize() for name in OPTION_IDS)
            raise ValueError(
                f"issue {issue_url} has unsupported Priority {priority!r}; expected {allowed}"
            )
        return priority.capitalize()
    return None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, text=True, capture_output=True, encoding="utf-8")
    # `None` = захват сломался (#364), а не «gh промолчал». Дефолт на этом месте
    # подменял бы отказ пустотой — ровно то, что чинит #410.
    if result.stdout is None or result.stderr is None:
        raise RuntimeError(
            f"capture failed for `{' '.join(cmd)}` (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    return result


def _checked(cmd: list[str], what: str) -> str:
    """Run `cmd`; on non-zero exit print stderr and exit 1 (§IV visible failure)."""
    result = _run(cmd)
    if result.returncode != 0:
        # `or f"error: …"` — обработка легитимно пустого stderr, не воркэраунд:
        # отказ захвата теперь ловится в `_run` (#410).
        print(result.stderr.strip() or f"error: {what} failed", file=sys.stderr)
        sys.exit(1)
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
            PRIORITY_FIELD_ID,
            "--project-id",
            PROJECT_ID,
            "--single-select-option-id",
            option_id,
        ],
        "gh project item-edit",
    )


def _project_items_json() -> str:
    return _checked(
        [
            "gh",
            "project",
            "item-list",
            PROJECT_NUMBER,
            "--owner",
            PROJECT_OWNER,
            "--limit",
            "1000",
            "--format",
            "json",
        ],
        "gh project item-list",
    )


def _check_priority(issue: int) -> None:
    issue_url = _issue_url(issue)
    priority = priority_from_project_json(_project_items_json(), issue_url)
    if priority is None:
        print(
            f"error: issue #{issue} has no Priority in GitHub Project {PROJECT_NUMBER}; "
            f"run `python scripts/set_issue_priority.py {issue} <High|Medium|Low>` first",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"ok: issue #{issue} priority is {priority}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Set or verify an issue's Priority in Project 1 (#351, #376)."
    )
    parser.add_argument("issue", type=int, help="issue number")
    parser.add_argument("level", nargs="?", help="priority level: High | Medium | Low")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the issue already has a supported Priority; do not mutate Project 1",
    )
    ns = parser.parse_args(argv)

    if ns.check:
        if ns.level is not None:
            parser.error("LEVEL and --check are mutually exclusive")
        try:
            _check_priority(ns.issue)
        except (RuntimeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        return
    if ns.level is None:
        parser.error("LEVEL is required unless --check is used")

    try:
        option_id = option_id_for_level(ns.level)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)

    url = _issue_url(ns.issue)
    item_id = _item_add(url)
    _item_edit(item_id, option_id)
    print(f"ok: issue #{ns.issue} priority set to {ns.level.capitalize()} (item {item_id})")


if __name__ == "__main__":
    main()
