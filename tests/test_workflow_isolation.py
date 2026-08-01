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
# soldout is singled out by #396: it is the one step that sleeps for hours between
# retries, so its position among the siblings is itself an invariant.
_SOLDOUT_RUN = re.compile(r"python -m kinozal_scraper\.soldout_pipeline")
_SUMMARIZER_RUN = re.compile(r"python -m kinozal_scraper\.telegram_summarizer")


def _doc() -> dict[Any, Any]:
    """The parsed workflow. Keys are `Any`: YAML 1.1 parses a bare `on:` as the
    boolean `True`, so the trigger block is not reachable under the string "on"."""
    return cast("dict[Any, Any]", yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8")))


def _steps() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", _doc()["jobs"]["run-script"]["steps"])


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


class TestSoldoutStepPlacement:
    """soldout — единственный шаг, который спит часами (#396), поэтому его место и
    его потолок в прогоне сами по себе инварианты."""

    def test_soldout_waits_last_of_all(self) -> None:
        """soldout ждёт до ~4.6 ч внутри шага (#396) — значит стоит последним.

        Разброс попыток во времени и есть фикс: Cloudflare режет датацентровые IP
        вероятностно, и единственное, что отличает доехавший прогон, — успела ли
        хоть одна из 24 разнесённых попыток. Цена — часы ожидания, и заплатить её
        можно только в самом конце: в середине прогона тот же шаг отложил бы
        доставку остальных источников на те же часы.

        Порядок ключей в YAML — ровно то, что следующий контрибьютор переставит,
        не заметив; сам GitHub Actions такой инвариант не выражает.
        """
        steps = _steps()
        soldout = [i for i, s in enumerate(steps) if _SOLDOUT_RUN.search(str(s.get("run", "")))]
        assert len(soldout) == 1, f"expected exactly one soldout step, got {len(soldout)}"
        earlier = [
            i
            for i, s in enumerate(steps)
            if i != soldout[0]
            and (
                _PIPELINE_RUN.search(str(s.get("run", "")))
                or _SUMMARIZER_RUN.search(str(s.get("run", "")))
            )
        ]
        assert earlier, "derived no sibling delivery steps — the regexes went stale"
        assert soldout[0] > max(earlier), (
            f"soldout step is at index {soldout[0]}, but delivery steps run at "
            f"{sorted(earlier)} — a step that sleeps for hours must not sit in front of them"
        )

    def test_soldout_step_has_a_timeout_below_the_job_ceiling(self) -> None:
        """Без своего таймаута сетевая патология доедет до оператора за 6 часов —
        столько GitHub Actions даёт job'у по умолчанию (#396)."""
        soldout = [s for s in _steps() if _SOLDOUT_RUN.search(str(s.get("run", "")))]
        assert len(soldout) == 1
        timeout = soldout[0].get("timeout-minutes")
        assert isinstance(timeout, int), (
            "soldout step must carry an explicit timeout-minutes — its patient retry "
            "policy makes an unbounded step a real possibility, not a theoretical one"
        )
        assert 0 < timeout < 360, (
            f"timeout-minutes={timeout} must be under the 360-minute job ceiling to fire first"
        )

    def test_daily_schedule_unchanged(self) -> None:
        """Фикс #396 сознательно НЕ трогает частоту: он разносит попытки внутри
        одного суточного прогона. Учащение расписания — другое решение с другой
        ценой (нагрузка на сайт, квота Gemini у соседних шагов), и молча оно
        приехать не должно."""
        triggers = _doc().get("on", _doc().get(True, {}))
        crons = [entry["cron"] for entry in triggers["schedule"]]
        assert crons == ["0 1 * * *"], f"daily cron changed to {crons}"
