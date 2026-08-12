"""Guards for the opt-in user-level Codex delivery-rule template."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_RULES = _REPO / "docs" / "examples" / "kinozal-delivery.rules"


def _text() -> str:
    return _RULES.read_text(encoding="utf-8")


def test_rules_allow_only_delivery_entry_points() -> None:
    text = _text()
    for command in (
        "scripts/push_issue_branch.py",
        "scripts/open_pr.py",
        "gh\", \"pr\", \"checks",
        "scripts.review_gate",
        "gh\", \"run\", \"view",
    ):
        assert command in text
    assert text.count('decision = "allow"') == 5


def test_rules_forbid_direct_push_and_merge() -> None:
    text = _text()
    assert 'pattern = ["git", "push"]' in text
    assert 'pattern = ["gh", "pr", "merge"]' in text
    assert text.count('decision = "forbidden"') == 2


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI is not installed")
def test_execpolicy_accepts_the_rules_file() -> None:
    result = subprocess.run(
        [
            "codex",
            "execpolicy",
            "check",
            "--rules",
            str(_RULES),
            "--",
            "python",
            "scripts/push_issue_branch.py",
        ],
        cwd=_REPO,
        check=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )
    assert result.stdout is not None and result.stderr is not None
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "allow"
