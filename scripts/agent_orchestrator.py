"""Budget-aware control plane for the repository's human-launched agent workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


@dataclass(frozen=True)
class RouteDecision:
    next_role: str
    status: str
    missing_evidence: tuple[str, ...]
    completed_roles: tuple[str, ...]


def load_catalog() -> dict[str, Any]:
    raise NotImplementedError


def decide(state: WorkflowState, catalogue: dict[str, Any]) -> RouteDecision:
    raise NotImplementedError
