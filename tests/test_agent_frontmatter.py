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

**Почему набор алиасов здесь другой, чем в `test_claude_review_workflow.py`.** Там
CLI-алиасы флага `--model` внутри `claude_args`; здесь — значения frontmatter, у
которых своя лексика (`inherit`, `fable`). Наборы **легитимно** разные, поэтому не
сведены в общую константу: общий allowlist пришлось бы держать надмножеством и он
пропускал бы валидное-там-но-не-здесь. Политика одна (дом — `docs/architecture/ci.md`),
формы её проверки две.

**Границы гарда, честно.** Он ловит дрейф frontmatter. Он НЕ ловит возврат скрытого
severity-фильтра в тело промпта (acceptance #4 issue #392): регексп по русским
императивам («не раздувай», «будь беспощаден») был бы карв-аут-детектором, скроенным
под текущий текст — ровно то, что architect-review забраковал в #374 — и всё равно
пропускал бы перефразировку. Семантика промпта держится прозой доки и ревью.
Ledger `docs/architecture/testing.md#consciously-accepted-coverage-gaps` не
пополняется: ограничена глубина покрытия, а не отклонено покрытие.

Инвариант **производный от glob** и сегодня профилактический: агент в репо ровно
один. Смысл производности — чтобы следующий агент попал под правило автоматически,
а не через ручной список, который забудут дополнить.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

_AGENTS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "agents"

# Значения, которые резолвит НЕ репозиторий: алиасы уезжают на новое поколение
# вместе с апстримом, `inherit` — вместе с моделью сессии. И то и другое означает,
# что качество plan-гейта меняется без строчки в диффе.
_UNPINNED = frozenset({"sonnet", "opus", "haiku", "fable", "inherit"})

# Набор апстримный (дока Claude Code, таблица frontmatter-полей). Это копия, то есть
# потенциальный дрейф — держим осознанно, потому что опечатка в значении
# игнорируется молча. Forcing-function: красный тест здесь = сверься с докой,
# набор мог измениться; не «поправь тест под файл».
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


def _agent_files() -> list[Path]:
    return sorted(_AGENTS_DIR.glob("*.md"))


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
        assert model.lower() not in _UNPINNED, (
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
