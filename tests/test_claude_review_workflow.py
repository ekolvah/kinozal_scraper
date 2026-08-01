"""Anti-drift guards for the cloud review gate (`.github/workflows/claude-review.yml`, #374).

Статический YAML-гард: ни сети, ни кредов — жанр `tests/test_workflow_isolation.py`
и `tests/test_settings_deny.py`.

**Что стережём.** Промпт cloud-ревьюера содержал severity-фильтр НА СТАДИИ ПОИСКА
(«Skip nitpicks — ruff handles formatting/lint»). Начиная с Opus 4.7 модели следуют
такому буквально: находка делается, оценивается ниже планки и молча не публикуется,
а отфильтрованная находка неотличима от её отсутствия (§IV). Второй экземпляр того
же дефекта жил на стадии ОТЧЁТА: `post exactly "✅ … no blocking issues found."`
запрещал дописывать к сводке should-fix находки. Оба класса дают пропущенный
ревьюером баг — приоритет (1) цель-функции, поэтому гард оправдан: регресс
корректностный, не resource-only (критерий — `docs/architecture/testing.md`,
«when a test is NOT worth writing»).

**Границы гарда, честно.** Он ловит ИЗВЕСТНУЮ ФОРМУ дефекта (императив подавления
в начале строки, gag-строка сводки) и НАЛИЧИЕ coverage-first контракта (severity +
confidence). Переформулировку фильтра другими словами («be selective», «only report
what matters») exit-code не поймает — семантику промпта скриптом не проверить; её
держат проза `docs/architecture/ci.md` и architect-review. Ledger
`docs/architecture/coverage-gaps.md` не пополняется:
ограничена глубина покрытия, а не отклонено покрытие.

Это **cloud-половина** модельного пиннинга. Локальные агенты
(`.claude/agents/architect-reviewer.md` — `model: opus`, короткий алиас) — #392.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml
from _model_pin_policy import UNPINNED_MODEL_VALUES

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "claude-review.yml"
_GATE_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "agent-review-gate.yml"
)
_ACTION = "anthropics/claude-code-action"

# Императивы подавления в начале строки. Карв-аутов нет by design: легитимное
# сужение (находку, которую уже ловит ruff/mypy, понижаем как дубль детерминированного
# гейта) записано в промпте НЕ императивом, поэтому исключать нечего. Карв-аут вида
# «разрешено, если в строке есть слово ruff» был бы скроен ровно под текущий текст
# (change-detector) и пропускал бы любое подавление, упомянувшее ruff.
_SUPPRESSION = re.compile(r"^\s*(skip|ignore|omit|don't report|do not report)\b", re.IGNORECASE)


def _review_step() -> dict[str, Any]:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = cast("list[dict[str, Any]]", data["jobs"]["claude-review"]["steps"])
    matches = [s for s in steps if _ACTION in str(s.get("uses", ""))]
    assert len(matches) == 1, f"expected exactly one {_ACTION} step, got {len(matches)}"
    return matches[0]


def _inputs() -> dict[str, Any]:
    return cast("dict[str, Any]", _review_step()["with"])


def _prompt() -> str:
    return str(_inputs()["prompt"])


class TestReviewModelPinned:
    def test_model_pinned_to_full_id(self) -> None:
        """Модель задаётся флагом CLI `--model` внутри `claude_args`.

        Имя input'а сверено с `action.yml` экшена (`claude_args` — «Additional
        arguments to pass to Claude CLI»; отдельного input'а `model` у экшена нет),
        а не угадано: неизвестный `with:`-input GitHub Actions игнорирует МОЛЧА,
        и гард на выдуманное имя был бы зелёным при отсутствующем пине."""
        args = str(_inputs().get("claude_args", ""))
        found = re.search(r"--model\s+(\S+)", args)
        assert found, (
            "claude-review must pin the model explicitly via "
            f"`claude_args: --model <id>`; got claude_args={args!r} (#374)"
        )
        model = found.group(1)
        assert model.lower() not in UNPINNED_MODEL_VALUES, (
            f"model {model!r} resolves outside the repo (alias / floating pointer), so "
            "the review silently moves to another model when it is repointed (#374). "
            "The denylist is shared with the agent-frontmatter guard — see "
            "tests/_model_pin_policy.py"
        )


class TestCoverageFirstPrompt:
    def test_no_suppression_imperative(self) -> None:
        offenders = [ln.strip() for ln in _prompt().splitlines() if _SUPPRESSION.match(ln)]
        assert not offenders, (
            "search-stage suppression imperative(s) in the review prompt — the model "
            f"follows them literally and the finding never reaches the PR (§IV): {offenders}"
        )

    def test_every_finding_graded(self) -> None:
        prompt = _prompt().lower()
        missing = [word for word in ("severity", "confidence") if word not in prompt]
        assert not missing, (
            "coverage-first contract requires grading instead of filtering: every finding "
            f"carries severity + confidence; missing from the prompt: {missing} (#374)"
        )

    def test_summary_not_gagged_to_blocking_only(self) -> None:
        """Известная форма дефекта на стадии отчёта, а не проверка формулировки.

        `post exactly "✅ … no blocking issues found."` — фиксированная сводка,
        которая при нуле blocking и трёх should-fix обязывает напечатать только её.
        Фильтр, убранный со входа, возвращался бы на выходе."""
        prompt = _prompt().lower()
        assert "no blocking issues" not in prompt, (
            "the fixed summary string is scoped to blocking findings — should-fix and "
            "nice-to-have findings disappear from the PR comment (#374)"
        )
        assert "grouped by severity" in prompt, (
            "the summary contract must require listing findings of every severity (#374)"
        )


class TestReviewOutcomeGate:
    def test_prompt_requires_machine_readable_outcome_for_current_head(self) -> None:
        prompt = _prompt()
        assert (
            "claude-review-outcome: sha=${{ github.event.pull_request.head.sha }} outcome=blocking"
            in prompt
        )
        assert (
            "claude-review-outcome: sha=${{ github.event.pull_request.head.sha }} outcome=rework"
            in prompt
        )
        assert (
            "claude-review-outcome: sha=${{ github.event.pull_request.head.sha }} outcome=clean"
            in prompt
        )
        assert "update_claude_comment" in prompt

    def test_trusted_target_workflow_is_the_only_required_gate(self) -> None:
        data = yaml.safe_load(_GATE_WORKFLOW.read_text(encoding="utf-8"))
        assert "pull_request_target" in data[True]
        steps = cast("list[dict[str, Any]]", data["jobs"]["agent-review-gate"]["steps"])
        checkout = steps[0]
        assert checkout["uses"] == "actions/checkout@v4"
        assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
        verifier = steps[1]
        assert "trusted/scripts/check_claude_review.py" in str(verifier["run"])
        assert "--head-sha ${{ github.event.pull_request.head.sha }}" in str(verifier["run"])
        assert "--wait-seconds 360" in str(verifier["run"])
