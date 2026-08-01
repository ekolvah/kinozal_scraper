"""Budget-aware control plane for the repository's human-launched agent workflow.

This module deliberately routes evidence instead of invoking a model.  Claude
and Codex subscriptions remain human-launched adapters; a deterministic control
plane makes their state, authority, and bounded retries inspectable without
turning a subscription quota into an unattended API bill.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CATALOG = _REPO_ROOT / ".agents" / "orchestration" / "roles.yaml"
_REQUIRED_ROLE_FIELDS = frozenset(
    {"adapter", "authority", "entry_evidence", "completion_evidence", "activation", "max_runs"}
)
_INITIAL_ROLES = frozenset(
    {"planner", "architect_reviewer", "implementer", "pr_reviewer", "fixer", "human_merge"}
)


@dataclass(frozen=True)
class WorkflowState:
    plan_completed: bool
    issue_kind: str
    architect_completed: bool
    architect_skip_reason: str | None
    implementation_completed: bool
    ci_passed: bool
    head_sha: str | None
    reviewed_heads: tuple[str, ...]
    review_outcome: str | None
    fixer_revisions: int
    planner_runs: int = 0
    architect_runs: int = 0
    implementer_runs: int = 0


@dataclass(frozen=True)
class RouteDecision:
    next_role: str
    status: str
    missing_evidence: tuple[str, ...]
    completed_roles: tuple[str, ...]
    adapter: str
    next_action: str


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the single, repository-owned role catalogue."""
    catalog_path = path or _DEFAULT_CATALOG
    try:
        payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read role catalogue {catalog_path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), dict):
        raise ValueError("role catalogue must contain a mapping named 'roles'")
    roles = payload["roles"]
    if set(roles) != _INITIAL_ROLES:
        raise ValueError(f"role catalogue must declare exactly {sorted(_INITIAL_ROLES)}")
    for name, role in roles.items():
        if not isinstance(role, dict) or not _REQUIRED_ROLE_FIELDS.issubset(role):
            raise ValueError(f"role {name!r} has an incomplete contract")
        if not isinstance(role["max_runs"], int) or role["max_runs"] < 1:
            raise ValueError(f"role {name!r} must have a positive max_runs")
    return payload


def _completed_roles(state: WorkflowState) -> tuple[str, ...]:
    completed: list[str] = []
    if state.plan_completed:
        completed.append("planner")
    if state.architect_completed or state.architect_skip_reason:
        completed.append("architect_reviewer")
    if state.implementation_completed:
        completed.append("implementer")
    if state.head_sha and state.head_sha in state.reviewed_heads:
        completed.append("pr_reviewer")
    if state.fixer_revisions:
        completed.append("fixer")
    return tuple(completed)


def _decision(
    role: str,
    catalogue: Mapping[str, Any],
    state: WorkflowState,
    *,
    status: str = "next",
    missing: tuple[str, ...] = (),
    action: str | None = None,
) -> RouteDecision:
    role_data = catalogue["roles"].get(role)
    if role_data is None:
        return RouteDecision(
            next_role=role,
            status=status,
            missing_evidence=missing,
            completed_roles=_completed_roles(state),
            adapter="deterministic local command",
            next_action=action or role,
        )
    return RouteDecision(
        next_role=role,
        status=status,
        missing_evidence=missing,
        completed_roles=_completed_roles(state),
        adapter=str(role_data["adapter"]),
        next_action=action or str(role_data["adapter"]),
    )


def _planning_decision(state: WorkflowState, catalogue: dict[str, Any]) -> RouteDecision | None:
    roles = catalogue["roles"]
    if not state.plan_completed:
        if state.planner_runs >= roles["planner"]["max_runs"]:
            return _decision(
                "human_merge", catalogue, state, status="escalate", action="human plan decision"
            )
        return _decision("planner", catalogue, state)
    if state.issue_kind not in {"trivial", "nontrivial"}:
        return _decision(
            "planner",
            catalogue,
            state,
            status="blocked",
            missing=("issue_kind",),
            action="classify issue",
        )
    if state.issue_kind == "trivial":
        if not state.architect_skip_reason:
            return _decision(
                "planner",
                catalogue,
                state,
                status="blocked",
                missing=("architect_skip_reason",),
                action="record trivial-change architect skip reason",
            )
    elif not state.architect_completed:
        if state.architect_runs >= roles["architect_reviewer"]["max_runs"]:
            return _decision(
                "human_merge",
                catalogue,
                state,
                status="escalate",
                action="human architecture decision",
            )
        return _decision("architect_reviewer", catalogue, state)
    return None


def _implementation_decision(
    state: WorkflowState, catalogue: dict[str, Any]
) -> RouteDecision | None:
    roles = catalogue["roles"]
    if not state.implementation_completed:
        if state.implementer_runs >= roles["implementer"]["max_runs"]:
            return _decision(
                "human_merge",
                catalogue,
                state,
                status="escalate",
                action="human implementation decision",
            )
        return _decision("implementer", catalogue, state)
    if not state.ci_passed:
        return _decision(
            "deterministic_ci",
            catalogue,
            state,
            action="python scripts/ci_check.py",
            missing=("ci_passed",),
        )
    return None


def _review_decision(state: WorkflowState, catalogue: dict[str, Any]) -> RouteDecision:
    roles = catalogue["roles"]
    if not state.head_sha:
        return _decision(
            "pr_reviewer",
            catalogue,
            state,
            status="blocked",
            missing=("head_sha",),
            action="record PR head SHA",
        )
    if state.head_sha not in state.reviewed_heads:
        return _decision("pr_reviewer", catalogue, state)
    if state.review_outcome is None:
        return _decision(
            "pr_reviewer",
            catalogue,
            state,
            status="blocked",
            missing=("review_outcome",),
            action="read current-head review outcome",
        )
    if state.review_outcome == "clean":
        return _decision("human_merge", catalogue, state)
    if state.review_outcome in {"rework", "blocking"}:
        if state.fixer_revisions >= roles["fixer"]["max_runs"]:
            return _decision(
                "human_merge",
                catalogue,
                state,
                status="escalate",
                action="human decision after fixer budget exhausted",
            )
        return _decision("fixer", catalogue, state)
    return _decision(
        "pr_reviewer",
        catalogue,
        state,
        status="blocked",
        missing=("valid_review_outcome",),
        action="record clean, rework, or blocking outcome",
    )


def decide(state: WorkflowState, catalogue: dict[str, Any]) -> RouteDecision:
    """Return the next bounded route step without performing it."""
    return (
        _planning_decision(state, catalogue)
        or _implementation_decision(state, catalogue)
        or _review_decision(state, catalogue)
    )


def _state_from_json(payload: Mapping[str, Any]) -> WorkflowState:
    try:
        reviewed_heads = tuple(str(head) for head in payload.get("reviewed_heads", ()))
        return WorkflowState(
            plan_completed=bool(payload["plan_completed"]),
            issue_kind=str(payload["issue_kind"]),
            architect_completed=bool(payload.get("architect_completed", False)),
            architect_skip_reason=payload.get("architect_skip_reason"),
            implementation_completed=bool(payload.get("implementation_completed", False)),
            ci_passed=bool(payload.get("ci_passed", False)),
            head_sha=payload.get("head_sha"),
            reviewed_heads=reviewed_heads,
            review_outcome=payload.get("review_outcome"),
            fixer_revisions=int(payload.get("fixer_revisions", 0)),
            planner_runs=int(payload.get("planner_runs", 0)),
            architect_runs=int(payload.get("architect_runs", 0)),
            implementer_runs=int(payload.get("implementer_runs", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid workflow state: {exc}") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_file", type=Path, help="JSON workflow-state input; it is read only")
    parser.add_argument("--catalog", type=Path, help="optional role catalogue path")
    args = parser.parse_args(argv)
    try:
        state_payload = json.loads(args.state_file.read_text(encoding="utf-8"))
        if not isinstance(state_payload, dict):
            raise ValueError("workflow state must be a JSON object")
        decision = decide(_state_from_json(state_payload), load_catalog(args.catalog))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(asdict(decision), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
