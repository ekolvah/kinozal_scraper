"""Guard: pipeline steps in run-script.yml are isolated from each other (#245).

A transient failure of one source (e.g. a third-party 403 on soldout) must not
skip the other pipeline steps in the same scheduled run. GitHub Actions steps run
with an *implicit* `if: success()`, so one step's `exit 1` cascades into skipping
every later step — which is how a soldout flap suppressed kinozal delivery in run
28493805028. The fix isolates each pipeline step via
`if: ${{ !cancelled() && steps.<gate>.outcome == 'success' }}` (isolation from
siblings + hard test gate) while keeping the natural `exit 1` (no continue-on-error)
so a failed source still reds the job and fires the §IV fallback alert.

These are static YAML guards (no network/credentials), like `test_repo_layout.py`.
The pipeline set is *derived* from the workflow (every step whose `run` invokes a
`kinozal_scraper.*_pipeline` module), so a newly-added source is automatically held
to the invariant instead of slipping through a hand-maintained list.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "run-script.yml"
# A pipeline step is one that runs `python -m kinozal_scraper.<name>_pipeline`.
# telegram_summarizer (not *_pipeline) and the pytest gate are intentionally excluded.
_PIPELINE_RUN = re.compile(r"python -m kinozal_scraper\.\w+_pipeline")


def _steps() -> list[dict[str, Any]]:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return cast("list[dict[str, Any]]", data["jobs"]["run-script"]["steps"])


def _pipeline_steps() -> list[dict[str, Any]]:
    return [s for s in _steps() if _PIPELINE_RUN.search(str(s.get("run", "")))]


def _gate_step() -> dict[str, Any]:
    gates = [s for s in _steps() if "pytest" in str(s.get("run", ""))]
    assert len(gates) == 1, f"expected exactly one pytest gate step, got {len(gates)}"
    return gates[0]


def _norm(expr: str) -> str:
    """Collapse whitespace so `steps.tests.outcome == 'success'` matches regardless
    of spacing / `${{ }}` wrapping."""
    return re.sub(r"\s+", "", expr)


class TestPipelineStepIsolation:
    def test_every_pipeline_step_isolated_without_masking(self) -> None:
        pipelines = _pipeline_steps()
        assert len(pipelines) >= 5, (
            f"derived {len(pipelines)} pipeline steps, expected >=5 "
            "(github_popular/github_trending/steam/soldout/kinozal)"
        )
        gate_id = _gate_step().get("id")
        assert gate_id, "pytest gate step needs an `id` so pipeline steps can gate on its outcome"
        for step in pipelines:
            name = step.get("name", step.get("run"))
            cond = _norm(str(step.get("if", "")))
            assert "!cancelled()" in cond, (
                f"pipeline step {name!r} must run under !cancelled() so a sibling's "
                f"failure doesn't skip it (implicit if: success() cascades) — got if={step.get('if')!r}"
            )
            assert f"steps.{gate_id}.outcome=='success'" in cond, (
                f"pipeline step {name!r} must gate on the test step "
                f"(steps.{gate_id}.outcome == 'success') — got if={step.get('if')!r}"
            )
            assert step.get("continue-on-error") is not True, (
                f"pipeline step {name!r} must NOT set continue-on-error: a failed source "
                "must red the job so the §IV fallback alert fires, not be masked green"
            )

    def test_pure_logic_test_gate_is_hard_prerequisite(self) -> None:
        gate = _gate_step()
        assert gate.get("id"), "pytest gate step must have an `id`"
        assert "if" not in gate, (
            "pytest gate must run unconditionally (no isolation `if`) — it is the hard "
            "prerequisite; pipelines gate on ITS outcome, not the reverse"
        )
        assert gate.get("continue-on-error") is not True, (
            "pytest gate must not set continue-on-error — red tests must block prod pipelines"
        )
