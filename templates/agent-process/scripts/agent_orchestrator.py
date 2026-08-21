"""Budget-aware control plane for the repository's human-launched agent workflow.

This module deliberately routes evidence instead of invoking a model. Claude
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
    {
        "contract",
        "adapters",
        "adapter_routes",
        "carrier_selection",
        "adapter",
        "authority",
        "entry_evidence",
        "completion_evidence",
        "activation",
        "max_runs",
    }
)
_REQUIRED_INITIAL_ROLES = frozenset(
    {
        "discovery",
        "planner",
        "architect_reviewer",
        "implementer",
        "pr_reviewer",
        "fixer",
        "human_merge",
    }
)
_NON_CATALOGUE_STEPS = frozenset({"deterministic_ci"})


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
    # A bug issue observes before it plans. Two fields rather than one, for the
    # same reason `plan_completed` is not derived from `issue_kind`: "the block is owed"
    # and "the block exists" are different facts, and collapsing them would let a
    # missing observation read as a class that never needed one.
    evidence_required: bool = False
    evidence_completed: bool = False
    discovery_runs: int = 0
    planner_runs: int = 0
    architect_runs: int = 0
    implementer_runs: int = 0
    route: str | None = None


@dataclass(frozen=True)
class RouteDecision:
    """The next route step and a snapshot of roles with satisfied current evidence.

    ``completed_roles`` is deliberately not an invocation history. A selected
    role drops from the snapshot when its current route is blocked on more
    evidence. A fixer is present after it produces a new head that awaits
    review, then drops once that head has been reviewed. A rework route to the
    fixer is ``next``, rather than ``blocked``.
    """

    next_role: str
    status: str
    missing_evidence: tuple[str, ...]
    completed_roles: tuple[str, ...]
    adapter: str
    contract: str
    next_action: str
    route: str | None


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the single, repository-owned role catalogue."""
    catalog_path = path or _DEFAULT_CATALOG
    try:
        payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read role catalogue {catalog_path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("roles"), dict):
        raise ValueError("role catalogue must contain a mapping named 'roles'")
    roles = payload["roles"]
    missing_roles = _REQUIRED_INITIAL_ROLES - roles.keys()
    if missing_roles:
        raise ValueError(f"role catalogue is missing initial roles: {sorted(missing_roles)}")
    for name, role in roles.items():
        if not isinstance(role, dict) or not _REQUIRED_ROLE_FIELDS.issubset(role):
            raise ValueError(f"role {name!r} has an incomplete contract")
        if not isinstance(role["max_runs"], int) or role["max_runs"] < 1:
            raise ValueError(f"role {name!r} must have a positive max_runs")
        adapters = role["adapters"]
        if not isinstance(adapters, list) or role["adapter"] not in adapters:
            raise ValueError(f"role {name!r} selects an adapter outside its declared entry points")
        _validate_carrier_selection(name, role, adapters)
    return payload


def _validate_carrier_selection(name: str, role: Mapping[str, Any], adapters: list[Any]) -> None:
    """How a role's carrier is chosen, declared rather than inferred from a count.

    ``run_route`` is the human's choice of chat: each alternative maps to exactly
    one route, and full coverage keeps the map and the default from drifting apart.
    ``ci_failover`` is the workflow's choice at run time — the review gate asks the
    first carrier and falls back only when it returns no verdict; the run
    route does not select it, so it stays route-independent. ``sole`` is a role with
    one carrier. Deriving the mode from ``len(adapters)`` would make the review gate
    claim a `codex` run was reviewed by Codex, which is the defect again.
    """
    selection = role["carrier_selection"]
    routes = role["adapter_routes"]
    if selection not in {"sole", "run_route", "ci_failover"}:
        raise ValueError(
            f"role {name!r} declares an unknown carrier_selection {selection!r}; "
            "expected 'sole', 'run_route', or 'ci_failover'"
        )
    if (selection == "sole") != (len(adapters) == 1):
        raise ValueError(
            f"role {name!r} declares carrier_selection {selection!r} with {len(adapters)} "
            "adapter(s); 'sole' is for exactly one carrier and the other modes for several"
        )
    if selection in {"sole", "ci_failover"}:
        if routes is not None:
            raise ValueError(
                f"role {name!r} does not select its carrier by run route, so it is "
                "route-independent and adapter_routes must be null"
            )
        if selection == "ci_failover" and role["adapter"] != adapters[0]:
            raise ValueError(
                f"role {name!r} must select the carrier that is asked first: adapter "
                f"{role['adapter']!r} is not the head of its failover order"
            )
        return
    if not isinstance(routes, dict) or not all(
        isinstance(route, str) and isinstance(adapter, str) for route, adapter in routes.items()
    ):
        raise ValueError(f"role {name!r} must declare adapter_routes as a route-to-adapter map")
    if sorted(routes.values()) != sorted(adapters):
        raise ValueError(
            f"role {name!r} adapter_routes must cover every declared adapter exactly once"
        )


def _catalogue_routes(catalogue: Mapping[str, Any]) -> list[str]:
    known: set[str] = set()
    for role in catalogue["roles"].values():
        routes = role.get("adapter_routes")
        if isinstance(routes, dict):
            known.update(routes)
    return sorted(known)


def _role_adapter(name: str, role: Mapping[str, Any], route: str | None) -> str:
    """The entry point this run reaches the role through.

    A role the run route does not select — a human, or a GitHub Action that picks
    its own carrier at run time — answers every known route with its declared
    default: for ``ci_failover`` that is the carrier CI asks first. That is not a
    silent fallback: an unknown route never gets this far (see ``decide``).
    """
    routes = role.get("adapter_routes")
    if route is None or routes is None:
        return str(role["adapter"])
    if route not in routes:
        raise ValueError(
            f"role {name!r} has no adapter for route {route!r}; "
            f"its declared routes are {sorted(routes)}"
        )
    return str(routes[route])


def _completed_roles(state: WorkflowState) -> tuple[str, ...]:
    completed: list[str] = []
    if state.evidence_required and state.evidence_completed:
        completed.append("discovery")
    if state.plan_completed:
        completed.append("planner")
    if state.architect_completed or (state.issue_kind == "trivial" and state.architect_skip_reason):
        completed.append("architect_reviewer")
    if state.implementation_completed and state.ci_passed:
        completed.append("implementer")
    if (
        state.head_sha
        and state.head_sha in state.reviewed_heads
        and state.review_outcome in {"clean", "rework", "blocking"}
    ):
        completed.append("pr_reviewer")
    if state.fixer_revisions and state.head_sha and state.head_sha not in state.reviewed_heads:
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
    completed_roles = _completed_roles(state)
    if status == "blocked":
        completed_roles = tuple(completed for completed in completed_roles if completed != role)
    role_data = catalogue["roles"].get(role)
    if role_data is None:
        if role not in _NON_CATALOGUE_STEPS:
            raise ValueError(f"unknown route step: {role}")
        return RouteDecision(
            next_role=role,
            status=status,
            missing_evidence=missing,
            completed_roles=completed_roles,
            adapter="deterministic local command",
            contract="",
            next_action=action or role,
            route=state.route,
        )
    adapter = _role_adapter(role, role_data, state.route)
    return RouteDecision(
        next_role=role,
        status=status,
        missing_evidence=missing,
        completed_roles=completed_roles,
        adapter=adapter,
        contract=str(role_data["contract"]),
        next_action=action or adapter,
        route=state.route,
    )


def _discovery_decision(state: WorkflowState, catalogue: dict[str, Any]) -> RouteDecision | None:
    """The observation a bug plan needs as input, routed before the plan.

    Ahead of `_planning_decision` rather than inside it: sending the planner first would
    ask it to write a plan whose required input does not exist yet, which is how the
    `## Evidence` section came to be filled by whichever role happened to be running.
    """
    if not state.evidence_required or state.evidence_completed:
        return None
    if state.discovery_runs >= catalogue["roles"]["discovery"]["max_runs"]:
        return _decision(
            "human_merge", catalogue, state, status="escalate", action="human observation decision"
        )
    return _decision("discovery", catalogue, state)


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
            status="blocked",
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
        missing=("review_outcome",),
        action="record clean, rework, or blocking outcome",
    )


def decide(state: WorkflowState, catalogue: dict[str, Any]) -> RouteDecision:
    """Return the next bounded route step without performing it."""
    if state.route is not None:
        known = _catalogue_routes(catalogue)
        if state.route not in known:
            # Checked here rather than per role: a single-carrier step answers any
            # route, so a typo would otherwise resolve into a confident wrong name.
            raise ValueError(f"unknown run route {state.route!r}; declared routes are {known}")
    return (
        _discovery_decision(state, catalogue)
        or _planning_decision(state, catalogue)
        or _implementation_decision(state, catalogue)
        or _review_decision(state, catalogue)
    )


def _state_from_json(payload: Mapping[str, Any]) -> WorkflowState:
    try:
        raw_reviewed_heads = payload.get("reviewed_heads", ())
        if not isinstance(raw_reviewed_heads, (list, tuple)) or not all(
            isinstance(head, str) for head in raw_reviewed_heads
        ):
            raise TypeError("reviewed_heads must be a list of strings")
        reviewed_heads = tuple(raw_reviewed_heads)

        def optional_string(name: str) -> str | None:
            value = payload.get(name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or null")
            return value

        def boolean(name: str, *, default: bool | None = None) -> bool:
            value = payload[name] if default is None else payload.get(name, default)
            if not isinstance(value, bool):
                raise TypeError(f"{name} must be a boolean")
            return value

        def nonnegative_integer(name: str) -> int:
            value = payload.get(name, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise TypeError(f"{name} must be a non-negative integer")
            return int(value)

        return WorkflowState(
            plan_completed=boolean("plan_completed"),
            issue_kind=str(payload["issue_kind"]),
            architect_completed=boolean("architect_completed", default=False),
            architect_skip_reason=optional_string("architect_skip_reason"),
            implementation_completed=boolean("implementation_completed", default=False),
            ci_passed=boolean("ci_passed", default=False),
            head_sha=optional_string("head_sha"),
            reviewed_heads=reviewed_heads,
            review_outcome=optional_string("review_outcome"),
            fixer_revisions=nonnegative_integer("fixer_revisions"),
            evidence_required=boolean("evidence_required", default=False),
            evidence_completed=boolean("evidence_completed", default=False),
            discovery_runs=nonnegative_integer("discovery_runs"),
            planner_runs=nonnegative_integer("planner_runs"),
            architect_runs=nonnegative_integer("architect_runs"),
            implementer_runs=nonnegative_integer("implementer_runs"),
            route=optional_string("route"),
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
