"""Anti-drift guards for the local agent surface (`.claude/agents/*.md`, #392).

Статический гард без сети и кредов — жанр `tests/test_claude_review_workflow.py`
(#374, cloud-половина того же дефекта) и `tests/test_settings_deny.py`.

**Что стережём.** `model: opus` — это АЛИАС, а не id: по доке Claude Code
(https://code.claude.com/docs/en/sub-agents, §Choose a model) поле принимает алиас
(`sonnet`/`opus`/`haiku`/`fable`), полный id (`claude-opus-5`) или `inherit`
(дефолт при отсутствии поля). С релизом Opus 5 plan-стадийный ревьюер переехал на
другую модель без строчки в диффе — §IV: смена неотличима от её отсутствия.
`effort` (там же, таблица frontmatter-полей; значения `low|medium|high|xhigh|max`)
по умолчанию **наследуется от сессии**, поэтому без явного пина строгость
plan-гейта зависит от того, в какой сессии его вызвали — невоспроизводимо между
контрибьюторами.

**Denylist общий с cloud-гардом** — `tests/_model_pin_policy.py`. Держать копии в
двух файлах было ошибкой первой версии: наборы уже разошлись (`fable` был здесь и
отсутствовал там). Это denylist, поэтому объединение строго консервативнее — может
только отвергнуть лишнее, но не пропустить.

**Границы гарда, честно.** Frontmatter стережётся полностью; тело промпта — по той
же форме, что и cloud-половина (`TestCoverageFirstPrompt` ниже): наличие
coverage-first контракта + отсутствие снятых формулировок подавления. Что НЕ
ловится — **семантическая перефразировка** фильтра («будь избирателен», «пиши
только про важное»): её exit-code не проверяет ни здесь, ни в #374. Эта остаточная
дыра записана в ledger
[`testing.md#consciously-accepted-coverage-gaps`](../docs/architecture/testing.md#consciously-accepted-coverage-gaps),
чтобы отказ не переоткрыли как work-for-work.

Инвариант **производный от glob** и сегодня профилактический: агент в репо ровно
один. Смысл производности — чтобы следующий агент попал под правило автоматически,
а не через ручной список, который забудут дополнить.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from _model_pin_policy import UNPINNED_MODEL_VALUES

_AGENTS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "agents"

# Формулировки, снятые в #392 как скрытый severity-фильтр. Гард на ИЗВЕСТНУЮ форму
# дефекта — тот же жанр, что проверка gag-строки `no blocking issues` в
# `test_claude_review_workflow.py`. Регексп «любой императив в начале строки» здесь
# не годится: промпт русскоязычный, и легитимные инструкции тоже начинаются с «Не»
# («Не дублируй работу cloud claude-review» — это разграничение зон, не подавление).
_REMOVED_SUPPRESSION = ("не раздувай", "беспощаден", "краткость по умолчанию")

# Набор апстримный (дока Claude Code, таблица frontmatter-полей). Это копия, то есть
# потенциальный дрейф — держим осознанно, потому что опечатка в значении
# игнорируется молча. Forcing-function: красный тест здесь = сверься с докой,
# набор мог измениться; не «поправь тест под файл».
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _agent_files() -> list[Path]:
    # rglob, не glob: Claude Code сканирует `.claude/agents/` рекурсивно, поэтому
    # агент в подпапке был бы вне инварианта — и `test_agent_files_are_actually_scanned`
    # остался бы зелёным за счёт файла верхнего уровня, то есть тот самый тихий
    # вакуум, против которого он и написан, просто одной директорией глубже.
    return sorted(_AGENTS_DIR.rglob("*.md"))


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssertionError(f"{path.name}: no YAML frontmatter block")
    _, _, rest = text.partition("---")
    block, sep, _ = rest.partition("\n---")
    if not sep:
        raise AssertionError(f"{path.name}: unterminated YAML frontmatter block")
    return cast("dict[str, Any]", yaml.safe_load(block) or {})


class TestAgentModelPinned:
    def test_agent_files_are_actually_scanned(self) -> None:
        """Гард на пустой glob: без него переименование/переезд `.claude/agents/`
        делает оба инварианта ниже вакуумно-зелёными, и «нечего проверять»
        становится неотличимо от «всё в порядке» (§IV)."""
        assert _agent_files(), f"no agent definitions found under {_AGENTS_DIR}"

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_model_is_full_id(self, path: Path) -> None:
        model = str(_frontmatter(path).get("model", "")).strip()
        assert model, (
            f"{path.name}: no `model` in frontmatter — the subagent silently inherits "
            "the session model, so the review's rigor is not a repo decision (#392)"
        )
        assert model.lower() not in UNPINNED_MODEL_VALUES, (
            f"{path.name}: `model: {model}` is an alias or `inherit` — it resolves "
            "outside the repo, so the agent moves to another model with no line in "
            "any diff; pin a full id such as `claude-opus-5` (#392)"
        )

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_effort_pinned_to_allowed_level(self, path: Path) -> None:
        effort = str(_frontmatter(path).get("effort", "")).strip().lower()
        assert effort, (
            f"{path.name}: no `effort` in frontmatter — it defaults to inheriting the "
            "session level, so the same review is stricter or laxer depending on who "
            "ran it (#392)"
        )
        assert effort in _EFFORT_LEVELS, (
            f"{path.name}: `effort: {effort}` is not one of {sorted(_EFFORT_LEVELS)}; "
            "an unrecognised value is ignored silently. If the upstream set changed, "
            "check the Claude Code docs and update the set here — do not relax the test"
        )


class TestCoverageFirstPrompt:
    """Тело промпта: контракт «градация вместо фильтрации» (#392, acceptance #4).

    Зеркало `TestCoverageFirstPrompt` из `tests/test_claude_review_workflow.py`:
    там cloud-ревьюер, здесь локальный plan-ревьюер, дефект один и тот же —
    инструкция «покороче» конвертирует слабую находку в отсутствие находки, а не в
    низкую severity (подтверждено самим ревьюером на прямой вопрос, #392).

    **Substance-гард, а не косметика:** пин модели — одна строка, а содержание
    правки — именно переписанный промпт; без этих тестов поведенческое изменение
    ехало бы вообще без покрытия."""

    @staticmethod
    def _body(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        return text.partition("---")[2].partition("\n---")[2]

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_removed_suppression_phrases_stay_out(self, path: Path) -> None:
        body = self._body(path).lower()
        present = [phrase for phrase in _REMOVED_SUPPRESSION if phrase in body]
        assert not present, (
            f"{path.name}: suppression phrasing is back in the agent prompt {present} — "
            "it converts a weak finding into no finding at all instead of a low "
            "severity, and a filtered finding is indistinguishable from a review that "
            "never ran (§IV, #392)"
        )

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.name)
    def test_findings_are_graded_not_filtered(self, path: Path) -> None:
        body = self._body(path).lower()
        missing = [word for word in ("confidence", "blocking") if word not in body]
        assert not missing, (
            f"{path.name}: the grading contract is incomplete, missing {missing}; "
            "every finding must be reported with a severity bucket and a confidence, "
            "so that filtering happens at the reporting stage, not the search stage (#392)"
        )
