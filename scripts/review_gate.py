"""Decide whether the PR review/fix loop continues — with an exit code (#467)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

REVIEW_CONTEXT = "claude-review"
VERDICT_EXIT_CODES: dict[str, int] = {
    "ready-for-human": 0,
    "fix-blocking": 10,
    "escalate": 20,
    "review-pending": 30,
}


@dataclass(frozen=True)
class CheckRun:
    """One required check-run on the PR head."""

    name: str
    status: str
    conclusion: str


@dataclass(frozen=True)
class ReviewEvidence:
    """Everything the verdict is derived from; no role or provider is involved."""

    head_sha: str
    checks: tuple[CheckRun, ...]
    controller_pr: bool
    reviewed_heads: frozenset[str]
    pr_url: str
    review_run_url: str | None


@dataclass(frozen=True)
class Verdict:
    """The loop decision plus the exit code that carries it."""

    name: str
    exit_code: int
    reason: str
    next_action: str


def fixer_budget(catalogue: dict[str, Any]) -> int:
    """Return the fixer revision cap from the role catalogue."""
    raise NotImplementedError


def evaluate(evidence: ReviewEvidence, fixer_budget: int) -> Verdict:
    """Return the loop verdict for the current PR head."""
    raise NotImplementedError


def collect_evidence(pr: str) -> ReviewEvidence:
    """Read the live PR evidence the verdict needs."""
    raise NotImplementedError


def main(argv: Sequence[str] | None = None) -> None:
    """Print the verdict and exit with its code."""
    raise NotImplementedError
