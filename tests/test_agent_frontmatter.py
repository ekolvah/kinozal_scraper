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
[`coverage-gaps.md`](../docs/architecture/coverage-gaps.md),
чтобы отказ не переоткрыли как work-for-work.

**Два разных скоупа — это не небрежность (#407).** Инварианты frontmatter
(`model`, `effort`) применяются к **каждому** агенту: пин обязателен всем, и
производность от glob нужна ровно затем, чтобы следующий агент попал под правило
автоматически, а не через ручной список, который забудут дополнить.
Промпт-контракт (`TestCoverageFirstPrompt`) — наоборот, только к тем, кто
**возвращает findings**: требовать `confidence`/`blocking` от агента, который их не
возвращает, значит гарантировать, что контрибьютор ослабит тест, а не добавит
осмысленный контракт. Зачисление идёт по свойству файла, а не по имени: #372
планирует `code-critic`, и суффикс `*-reviewer` молча НЕ зачислил бы настоящего
ревьюера — обмен видимой ловушки на тихую.

Сегодня агент в репо ровно один, так что оба скоупа профилактические.
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


# Секция, которой агент объявляет, что возвращает findings по корзинам severity.
# Именно её наличие делает coverage-first контракт осмысленным — поэтому зачисление
# идёт по ней, а не по имени файла (см. `TestFindingsContractScope`).
_FINDINGS_SECTION = "## Формат ответа"


def declares_findings_contract(body: str) -> bool:
    """Декларирует ли агент, что возвращает findings (и значит связан контрактом)."""
    return _FINDINGS_SECTION in body


def _agent_files() -> list[Path]:
    # rglob, не glob: Claude Code сканирует `.claude/agents/` рекурсивно, поэтому
    # агент в подпапке был бы вне инварианта — и `test_agent_files_are_actually_scanned`
    # остался бы зелёным за счёт файла верхнего уровня, то есть тот самый тихий
    # вакуум, против которого он и написан, просто одной директорией глубже.
    return sorted(_AGENTS_DIR.rglob("*.md"))


def _body(path: Path) -> str:
    return path.read_text(encoding="utf-8").partition("---")[2].partition("\n---")[2]


def _findings_agents() -> list[Path]:
    """Агенты, связанные промпт-контрактом, — подмножество, не все (#407)."""
    return [path for path in _agent_files() if declares_findings_contract(_body(path))]


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


class TestFindingsContractScope:
    """Зачисление под промпт-контракт — по свойству файла, не по имени (#407).

    Имя (`*-reviewer`) было бы худшим признаком: #372 планирует агента `code-critic`,
    то есть настоящий ревьюер НЕ попал бы под контракт и остался бы молча
    незащищённым. Свойство «декларирует секцию формата findings» тянется за тем
    самым, что делает контракт осмысленным."""

    def test_body_without_findings_section_is_not_enrolled(self) -> None:
        body = "Ты ищешь файлы по репозиторию и возвращаешь пути.\n\n## Когда вызван\n\nВсегда.\n"
        assert not declares_findings_contract(body)

    def test_body_with_findings_section_is_enrolled(self) -> None:
        body = "Ты ревьюишь план.\n\n## Формат ответа\n\n- **BLOCKING** — …\n"
        assert declares_findings_contract(body)


class TestCoverageFirstPrompt:
    """Тело промпта: контракт «градация вместо фильтрации» (#392, acceptance #4).

    Зеркало `TestCoverageFirstPrompt` из `tests/test_claude_review_workflow.py`:
    там cloud-ревьюер, здесь локальный plan-ревьюер, дефект один и тот же —
    инструкция «покороче» конвертирует слабую находку в отсутствие находки, а не в
    низкую severity (подтверждено самим ревьюером на прямой вопрос, #392).

    **Substance-гард, а не косметика:** пин модели — одна строка, а содержание
    правки — именно переписанный промпт; без этих тестов поведенческое изменение
    ехало бы вообще без покрытия.

    **Скоуп — подмножество агентов** (`_findings_agents`, #407), в отличие от
    frontmatter-инвариантов выше: требовать `confidence`/`blocking` от агента,
    который findings не возвращает, значит заставить контрибьютора ослабить тест."""

    def test_scope_is_not_empty(self) -> None:
        """§IV на суженном списке: сузить — не значит позволить ему тихо схлопнуться
        в ноль. Без этого переименование секции формата обнуляет весь класс, и
        «никого не проверяем» становится неотличимо от «все прошли»."""
        assert _findings_agents(), (
            "no agent declares a findings contract — either the section header "
            f"({_FINDINGS_SECTION!r}) drifted or the scope collapsed silently (#407)"
        )

    @pytest.mark.parametrize("path", _findings_agents(), ids=lambda p: p.name)
    def test_removed_suppression_phrases_stay_out(self, path: Path) -> None:
        body = _body(path).lower()
        present = [phrase for phrase in _REMOVED_SUPPRESSION if phrase in body]
        assert not present, (
            f"{path.name}: suppression phrasing is back in the agent prompt {present} — "
            "it converts a weak finding into no finding at all instead of a low "
            "severity, and a filtered finding is indistinguishable from a review that "
            "never ran (§IV, #392)"
        )

    @pytest.mark.parametrize("path", _findings_agents(), ids=lambda p: p.name)
    def test_findings_are_graded_not_filtered(self, path: Path) -> None:
        body = _body(path).lower()
        missing = [word for word in ("confidence", "blocking") if word not in body]
        assert not missing, (
            f"{path.name}: the grading contract is incomplete, missing {missing}; "
            "every finding must be reported with a severity bucket and a confidence, "
            "so that filtering happens at the reporting stage, not the search stage (#392)"
        )
