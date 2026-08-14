#!/usr/bin/env python3
"""Validate that a GitHub issue body contains all required sections.

Usage: python scripts/validate_issue_sections.py <issue-number>

Which sections are required is resolved from the issue's one type label through
`.agents/orchestration/change-classes.yaml` (#516), so a change class is data
rather than a branch in this file.

Exits 0 if all required sections are present and non-empty, printing the resolved
class and its derived RED obligation. A passing issue may also print a
non-blocking reminder for an explicit Out of scope follow-up that has neither an
issue reference nor a wontfix/YAGNI decision (#368). Otherwise prints the list of
gaps to stderr and exits 1; an unreadable or structurally invalid catalogue exits
2, because then the verdict is neither a pass nor a fail. Consumed by role
adapters so an agent does not have to "remember" the hand-off contract.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml
from markdown_it import MarkdownIt

check_orphan_scope = importlib.import_module(
    f"{__package__}.check_orphan_scope" if __package__ else "check_orphan_scope"
)

REQUIRED_SECTIONS: tuple[str, ...] = (
    "Context / Why",
    "Acceptance criteria",
    "Test plan",
    "Implementation outline",
    "Docs to update",
    "Out of scope",
    # Architect-review findings (or an explicit `skipped: <reason>`). Enforced as
    # a gate so the review is a consciously-decided step, never silently skipped
    # (#150). Persona lives in `.claude/agents/architect-reviewer.md`; criteria in
    # `docs/architecture/principles.md`.
    "Architect review",
    # Link to the MADR record this issue's decision lands in, or an explicit
    # `none: <reason>`. Same shape and same rationale as `Architect review`: whether
    # a decision *deserves* a record is a cost-of-change judgement no script can make,
    # so the gate enforces that the question was **answered**, not that the answer is
    # right (#426). Route and entry filter: `project-map.md` §Canonical-home.
    "ADR",
    # Machine-independent provenance of the planning hand-off. The section
    # records that a planner actually validated the artifact and intentionally
    # passes it to an implementer; it does not contain prompts or transcripts.
    "Agent handoff",
)
EVIDENCE_SECTION = "Evidence"
BUG_LABEL = "bug"
# The taxonomy of governance convention 3, machine-readable so the change-class
# catalogue can be checked against it instead of against itself. Exactly one of these
# labels routes an issue to its row; anything else on the issue is a non-type label.
TYPE_LABELS: tuple[str, ...] = (
    "bug",
    "chore",
    "ci",
    "documentation",
    "enhancement",
    "perf",
    "refactor",
    "security",
    "testing",
)
TYPE_LABEL_GAP = "type label"
EVIDENCE_NA_PREFIX = "n/a:"
EVIDENCE_DECISION_FIELDS = (
    ("observed", "observed"),
    ("preserve", "preserve"),
    ("change", "change"),
    ("boundaries", "boundaries"),
    ("collateral", "collateral"),
    ("reuse", "reuse"),
    ("paired-test", "paired test"),
)
MIN_CONTENT_CHARS = 5

_MD = MarkdownIt("commonmark")

_ORCHESTRATION = Path(__file__).resolve().parents[1] / ".agents" / "orchestration"
_ROLE_CATALOGUE = _ORCHESTRATION / "roles.yaml"
_CHANGE_CLASS_CATALOGUE = _ORCHESTRATION / "change-classes.yaml"
_REVIEWER_ROLE = "architect_reviewer"
# The trivial-change escape (#150). It carries no carrier name on purpose: a skipped
# review has no reviewer to name.
SKIP_PREFIX = "skipped:"
REVIEWER_PREFIX = "reviewer:"
SELF_REVIEW = "self"


class CatalogueError(RuntimeError):
    """The role catalogue could not be read as the source of reviewer names."""


def reviewer_independence() -> dict[str, str]:
    """Map every `architect_reviewer` carrier onto `independent` or `self`.

    The catalogue is the single machine-readable list of carriers, so the gate reads it
    instead of keeping a second copy in a document or in this script (#474). Reading it
    also removes the contradiction an author-written kind allowed: the marker names only
    the carrier, and whether that carrier reviews its own plan is a property of the
    carrier, not a claim in the issue body.
    """
    try:
        catalogue = yaml.safe_load(_ROLE_CATALOGUE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogueError(f"cannot read {_ROLE_CATALOGUE}: {exc}") from exc
    try:
        declared = catalogue["roles"][_REVIEWER_ROLE]["adapter_independence"]
    except (KeyError, TypeError) as exc:
        raise CatalogueError(
            f"{_ROLE_CATALOGUE} declares no {_REVIEWER_ROLE}.adapter_independence"
        ) from exc
    if not isinstance(declared, dict) or not declared:
        raise CatalogueError(f"{_REVIEWER_ROLE}.adapter_independence is not a non-empty mapping")
    return declared


def change_class_requirements() -> dict[str, dict[str, tuple[str, ...]]]:
    """Per-type-label section deltas, relative to `REQUIRED_SECTIONS`.

    The change-class axis is data, exactly as the role axis already is (#516): a new
    class is a row in `.agents/orchestration/change-classes.yaml`, not another
    `if label == ...` branch here. The structural checks below make a row that cannot
    mean anything an exit-2 (`CatalogueError`) rather than a silently weaker gate: a
    typo in `omits` would otherwise drop a required section from the resolved set.
    """
    try:
        catalogue = yaml.safe_load(_CHANGE_CLASS_CATALOGUE.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogueError(f"cannot read {_CHANGE_CLASS_CATALOGUE}: {exc}") from exc
    classes = catalogue.get("classes") if isinstance(catalogue, dict) else None
    if not isinstance(classes, dict):
        raise CatalogueError(f"{_CHANGE_CLASS_CATALOGUE} declares no classes mapping")
    if set(classes) != set(TYPE_LABELS):
        raise CatalogueError(
            f"{_CHANGE_CLASS_CATALOGUE} rows {sorted(classes)} are not the type labels "
            f"{list(TYPE_LABELS)}"
        )
    base = set(REQUIRED_SECTIONS)
    resolved: dict[str, dict[str, tuple[str, ...]]] = {}
    for label, row in classes.items():
        if not isinstance(row, dict) or set(row) != {"adds", "omits"}:
            raise CatalogueError(
                f"{_CHANGE_CLASS_CATALOGUE} row {label!r} is not a mapping of adds/omits"
            )
        if not all(
            isinstance(row[key], list) and all(isinstance(item, str) for item in row[key])
            for key in ("adds", "omits")
        ):
            raise CatalogueError(
                f"{_CHANGE_CLASS_CATALOGUE} row {label!r} has a non-list adds/omits"
            )
        if not base.issuperset(row["omits"]):
            raise CatalogueError(
                f"{_CHANGE_CLASS_CATALOGUE} row {label!r} omits a section outside the base set: "
                f"{sorted(set(row['omits']) - base)}"
            )
        if base.intersection(row["adds"]):
            raise CatalogueError(
                f"{_CHANGE_CLASS_CATALOGUE} row {label!r} adds a section the base set already "
                f"requires: {sorted(base.intersection(row['adds']))}"
            )
        resolved[label] = {"adds": tuple(row["adds"]), "omits": tuple(row["omits"])}
    return resolved


def required_sections(label: str) -> tuple[str, ...]:
    """The resolved, ordered section set a `label` issue must carry."""
    row = change_class_requirements()[label]
    omitted = set(row["omits"])
    return (*(name for name in REQUIRED_SECTIONS if name not in omitted), *row["adds"])


def _resolve_class(labels: Sequence[str]) -> str | None:
    """The one type label routing this issue, or `None` when it is not exactly one."""
    present = sorted({label.casefold() for label in labels} & set(change_class_requirements()))
    return present[0] if len(present) == 1 else None


def type_label_gaps(labels: Sequence[str], issue_number: int) -> list[str]:
    """Return the routing gap when an issue carries zero or several type labels.

    The type label is the route key into the class matrix, so governance convention 3
    stops being prose an agent must remember and becomes an exit code. The message names
    the *maintainer's* fix: a planner may not edit labels (§Planner runbook), so pointing
    at `/plan` would send the issue to a role that cannot resolve the gap.
    """
    present = sorted({label.casefold() for label in labels} & set(change_class_requirements()))
    if len(present) == 1:
        return []
    detail = "none" if not present else f"several: {', '.join(present)}"
    return [
        f"{TYPE_LABEL_GAP} ({detail}; the maintainer fixes it with "
        f"`gh issue edit {issue_number} --add-label <type>`)"
    ]


def architect_review_provenance(content: str) -> str | None:
    """Classify the section's **first non-empty line**.

    Returns `independent`, `self`, `skipped`, or `None` when the line is not a
    provenance marker. Only the first line counts: findings prose is free-form and
    routinely quotes `reviewer:` or `skipped:` while discussing this very gate, so a
    substring match would go green on exactly the case the gate exists to catch (§IV).
    """
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if first.lower().startswith(SKIP_PREFIX):
        return "skipped" if first[len(SKIP_PREFIX) :].strip() else None
    if not first.lower().startswith(REVIEWER_PREFIX):
        return None
    adapter = first[len(REVIEWER_PREFIX) :].strip()
    return reviewer_independence().get(adapter)


def architect_review_gaps(content: str) -> list[str]:
    """Return what the section's provenance line is missing, mirroring `handoff_gaps`."""
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if not first.lower().startswith((SKIP_PREFIX, REVIEWER_PREFIX)):
        return ["reviewer provenance line"]
    if architect_review_provenance(content) is None:
        # The line names the right field but not a value the catalogue knows, so the
        # gap says which half is wrong instead of repeating "provenance line".
        return (
            ["skip reason"]
            if first.lower().startswith(SKIP_PREFIX)
            else ["declared reviewer adapter"]
        )
    return []


def _split_by_h2(body: str) -> dict[str, str]:
    """Sections headed by `## `, parsed by CommonMark.

    **Why not regexp.** Markdown is not regular: whether `## X` is a heading depends
    on context—a fenced code block, indented block, or HTML block. A custom line parser
    already caused a defect: `## <required-section name>` inside ``` created
    a second section with the same key and overwrote the real one with the rest of the block,
    so a filled section was reported empty and `/implement` aborted on a nonexistent
    problem (#426). Chasing it with patches is endless (the first patch itself
    regressed an unmatched fence), so parsing is delegated to the
    [`markdown-it-py`](https://github.com/executablebooks/markdown-it-py) — CommonMark-
    implementation already present transitively. A side benefit: it sees the document
    exactly as GitHub renders it, including `Text` + `---` as
    h2 because GitHub does.
    """
    lines = body.splitlines()
    tokens = _MD.parse(body)
    # (heading, heading-start line, content-start line)
    heads: list[tuple[str, int, int]] = [
        (tokens[i + 1].content.strip(), token.map[0], token.map[1])
        for i, token in enumerate(tokens)
        if token.type == "heading_open" and token.tag == "h2" and token.map
    ]
    sections: dict[str, str] = {}
    for index, (title, _, content_start) in enumerate(heads):
        content_end = heads[index + 1][1] if index + 1 < len(heads) else len(lines)
        sections[title.lower()] = "\n".join(lines[content_start:content_end]).strip()
    return sections


def handoff_gaps(content: str) -> list[str]:
    """Return the missing provenance fields in an otherwise present hand-off."""
    normalized = "\n".join(line.strip().lower() for line in content.splitlines())
    required = {
        "planner": "planner:",
        "validation": "validation:",
        "validator command": "validate_issue_sections.py",
        "validation result": "passed",
        "next role": "next role: implementer",
        "handoff status": "handoff: ready",
    }
    return [name for name, marker in required.items() if marker not in normalized]


def _evidence_field(content: str, name: str) -> str | None:
    prefix = f"{name}:"
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            value = stripped[len(prefix) :].strip()
            if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
                value = value[1:-1].strip()
            return value or None
    return None


def _failed_capture_has_output(content: str) -> bool:
    if (_evidence_field(content, "status") or "").lower() != "failed":
        return False
    lines = content.splitlines()
    output_index = next(
        (index for index, line in enumerate(lines) if line.strip().lower() == "output:"),
        None,
    )
    if output_index is None:
        return False
    output = "\n".join(lines[output_index + 1 :]).strip()
    if not output.startswith("```"):
        return False
    first_newline = output.find("\n")
    if first_newline < 0 or not output.endswith("```"):
        return False
    output = output[first_newline + 1 : -3].strip()
    return bool(output)


def evidence_gaps(content: str) -> list[str]:
    """Return mechanical gaps in a bug issue's observation and decision record."""
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if first.casefold().startswith(EVIDENCE_NA_PREFIX):
        if first[len(EVIDENCE_NA_PREFIX) :].strip():
            return []
        return ["n/a reason"]

    capture_command = _evidence_field(content, "capture")
    path_value = _evidence_field(content, "path")
    missing: list[str] = []
    if not capture_command:
        missing.append("capture command")
    if not path_value:
        missing.append("capture path")
    elif capture_command and path_value not in capture_command:
        missing.append("capture path in command")
    if missing:
        return missing

    assert path_value is not None
    candidate = Path(path_value)
    if candidate.is_absolute():
        return ["repository-relative capture path"]
    if not candidate.parts or candidate.parts[0] != "evidence" or ".." in candidate.parts:
        return ["capture path under evidence/"]

    if (_evidence_field(content, "status") or "").casefold() == "failed":
        if not _failed_capture_has_output(content):
            return ["failed capture output"]
        return ["successful capture"]

    return [
        label for field, label in EVIDENCE_DECISION_FIELDS if not _evidence_field(content, field)
    ]


_SECTION_CHECKS = {
    "Agent handoff": handoff_gaps,
    "Architect review": architect_review_gaps,
    EVIDENCE_SECTION: evidence_gaps,
}


def find_gaps(body: str, required: Sequence[str] = REQUIRED_SECTIONS) -> list[str]:
    """Empty or missing sections from `required`.

    The set is a parameter rather than a module constant: the parser's other consumers are
    the MADR-record guard (`tests/test_adr_records.py`) with its own h2 list, and `main()`
    with the set resolved from the issue's change class. Forking it would create a second
    definition of an empty section (#426).

    This function never resolves labels (#516). `Evidence` is checked because it is *in*
    the given set, not because a `bug` label was passed down here — that keeps one carrier
    for "which sections apply" and leaves this pure over its argument.
    """
    sections = _split_by_h2(body)
    gaps: list[str] = []
    for name in required:
        content = sections.get(name.lower())
        if content is None or not content.strip():
            gaps.append(name)
            continue
        check = _SECTION_CHECKS.get(name)
        if check is None:
            # A section with no field contract is judged only by having substance.
            if len(content) < MIN_CONTENT_CHARS:
                gaps.append(name)
            continue
        # A section that owns a field contract is judged by it at any length: `n/a:` is
        # shorter than the generic bar yet must be reported as a missing reason, not as
        # an empty section.
        missing_fields = check(content)
        if missing_fields:
            gaps.append(f"{name} (missing: {', '.join(missing_fields)})")
    return gaps


def _fetch_issue(issue_number: int) -> tuple[str, tuple[str, ...]]:
    result = subprocess.run(
        ["gh", "issue", "view", str(issue_number), "--json", "body,state,labels"],
        check=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    if result.stdout is None or result.stderr is None:
        print(
            f"error: capture failed for `gh issue view {issue_number}` (rc={result.returncode})",
            file=sys.stderr,
        )
        sys.exit(2)
    if result.returncode != 0:
        detail = result.stderr.strip() or "no stderr"
        print(
            f"error: `gh issue view {issue_number}` failed (rc={result.returncode}): {detail}",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"error: invalid JSON from `gh issue view {issue_number}`: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    if data.get("state") != "OPEN":
        print(
            f"error: issue #{issue_number} is not OPEN (state={data.get('state')})", file=sys.stderr
        )
        sys.exit(2)
    labels = tuple(
        label["name"]
        for label in data.get("labels", [])
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    )
    return data.get("body") or "", labels


def _fetch_body(issue_number: int) -> str:
    """Compatibility helper for consumers that need only the issue body."""
    return _fetch_issue(issue_number)[0]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/validate_issue_sections.py <issue-number>", file=sys.stderr)
        sys.exit(2)
    try:
        n = int(sys.argv[1])
    except ValueError:
        print(f"error: issue number must be int (got {sys.argv[1]!r})", file=sys.stderr)
        sys.exit(2)
    body, labels = _fetch_issue(n)
    try:
        # Label resolution lives here, not in `find_gaps`: which sections apply is a
        # property of the issue's change class, while the parser stays pure over the set
        # it is handed (#516).
        label_gaps = type_label_gaps(labels, n)
        change_class = None if label_gaps else _resolve_class(labels)
        required = REQUIRED_SECTIONS if change_class is None else required_sections(change_class)
        gaps = label_gaps + find_gaps(body, required=required)
    except CatalogueError as exc:
        # Same class as a failed `gh` capture: the verdict cannot be trusted, so it is
        # not reported as either pass or fail (§IV).
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    if not gaps:
        assert change_class is not None
        print(f"ok: issue #{n} has all {len(required)} required sections")
        # Both obligations are derived from the resolved set rather than stored, so a row
        # cannot claim one thing and require another.
        red = "RED required" if "Test plan" in required else "RED not required"
        print(f"class: {change_class} — {red}")
        if (
            "Architect review" in required
            and architect_review_provenance(_split_by_h2(body)["architect review"]) == SELF_REVIEW
        ):
            # Non-blocking, like the orphan-scope reminder (#368): self-review is a valid
            # route, and the point is that it reaches the reader rather than passing as
            # an independent check.
            print(
                f"note: issue #{n} carries a self-review — the plan was reviewed by the agent "
                "that wrote it, which is not an independent check"
            )
        for reminder in check_orphan_scope.format_reminders(n, body):
            print(reminder)
        return
    print(f"error: issue #{n} is not ready:", file=sys.stderr)
    for g in gaps:
        print(f"  - {g}", file=sys.stderr)
    if len(gaps) > len(label_gaps):
        # The most common way to “lose” many sections is an unclosed ```; under CommonMark it
        # consumes the rest of the document and GitHub renders it as gray code. Give a hint,
        # not a detector heuristic: implementing one would again guess author intent.
        print(
            "hint: если секции в body видны глазами — проверь незакрытый ``` выше них: "
            "остаток документа становится кодовым блоком и на GitHub тоже",
            file=sys.stderr,
        )
        print("run `/plan #" + str(n) + "` to fill them", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
