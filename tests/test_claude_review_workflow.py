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
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from _model_pin_policy import UNPINNED_MODEL_VALUES

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "claude-review.yml"
_REMOVED_GATE_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "agent-review-gate.yml"
)
_ACTION = "anthropics/claude-code-action"
_AGENTS = Path(__file__).resolve().parent.parent / "AGENTS.md"
_CODEX_RULES_HEADING = "## Code Review Rules"

# Императивы подавления в начале строки. Карв-аутов нет by design: легитимное
# сужение (находку, которую уже ловит ruff/mypy, понижаем как дубль детерминированного
# гейта) записано в промпте НЕ императивом, поэтому исключать нечего. Карв-аут вида
# «разрешено, если в строке есть слово ruff» был бы скроен ровно под текущий текст
# (change-detector) и пропускал бы любое подавление, упомянувшее ruff.
_SUPPRESSION = re.compile(r"^\s*(skip|ignore|omit|don't report|do not report)\b", re.IGNORECASE)


# Кеш, а не фикстура: файлы читаются только на чтение и за прогон не меняются,
# а парс 11 KB YAML на каждый ассерт — десятки миллисекунд впустую.
@lru_cache(maxsize=1)
def _steps() -> list[dict[str, Any]]:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return cast("list[dict[str, Any]]", data["jobs"]["claude-review"]["steps"])


def _named_step(name: str) -> dict[str, Any]:
    matches = [step for step in _steps() if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    return matches[0]


def _review_step() -> dict[str, Any]:
    step = _named_step("Claude review")
    assert _ACTION in str(step.get("uses", ""))
    return step


def _inputs() -> dict[str, Any]:
    return cast("dict[str, Any]", _review_step()["with"])


def _inputs_for_step(step: dict[str, Any]) -> dict[str, Any]:
    return cast("dict[str, Any]", step["with"])


def _prompt() -> str:
    return str(_inputs()["prompt"])


def _codex_step() -> dict[str, Any]:
    return _named_step("Codex review")


@lru_cache(maxsize=1)
def _codex_review_rules() -> str:
    """Секция `AGENTS.md`, которую Codex code review читает как свой промпт."""
    text = _AGENTS.read_text(encoding="utf-8")
    assert _CODEX_RULES_HEADING in text, (
        f"{_CODEX_RULES_HEADING!r} is the documented place where Codex code review "
        "picks up repository rules; without it carrier 2 reviews by its own defaults"
    )
    section = text.split(_CODEX_RULES_HEADING, 1)[1]
    return section.split("\n## ", 1)[0]


# Оба носителя ревьюят по одному контракту (#478). Промпт-гарды ниже параметризованы
# по ним обоим, иначе носитель 2 мог бы молча переоткрыть политику severity из #458:
# гард, знающий только про первый носитель, зеленеет на любом тексте второго.
# Дома у контрактов разные: носитель 1 читает промпт из workflow, носитель 2 —
# `## Code Review Rules` в `AGENTS.md` (документированная точка настройки Codex
# code review). Копий по-прежнему две, но каждая — в своём каноническом месте.
# Список носителей и загрузчик промпта каждого объявлены здесь вместе: разъехавшись,
# они дали бы гард, дважды проверяющий носителя 1 и молча не проверяющий носителя 2.
_CARRIER_PROMPTS: dict[str, Callable[[], str]] = {
    "Claude review": _prompt,
    "Codex review": _codex_review_rules,
}
_CARRIERS = tuple(_CARRIER_PROMPTS)


def _carrier_prompt(carrier: str) -> str:
    return _CARRIER_PROMPTS[carrier]()


def _fallback_verifier() -> dict[str, Any]:
    return _named_step("Enforce Codex review outcome")


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


@pytest.mark.parametrize("carrier", _CARRIERS)
class TestCoverageFirstPrompt:
    """Один контракт поиска и отчёта на оба носителя ревью (#478).

    Носитель 2 добавлен ради доступности, а не ради второго мнения о том, что
    считать находкой: разъехавшиеся промпты дали бы разный вердикт на одном диффе
    в зависимости от того, у кого в тот день была квота.
    """

    def test_no_suppression_imperative(self, carrier: str) -> None:
        offenders = [
            ln.strip() for ln in _carrier_prompt(carrier).splitlines() if _SUPPRESSION.match(ln)
        ]
        assert not offenders, (
            "search-stage suppression imperative(s) in the review prompt — the model "
            f"follows them literally and the finding never reaches the PR (§IV): {offenders}"
        )

    def test_every_finding_graded(self, carrier: str) -> None:
        prompt = _carrier_prompt(carrier).lower()
        missing = [word for word in ("severity", "confidence") if word not in prompt]
        assert not missing, (
            "coverage-first contract requires grading instead of filtering: every finding "
            f"carries severity + confidence; missing from the prompt: {missing} (#374)"
        )

    def test_summary_not_gagged_to_blocking_only(self, carrier: str) -> None:
        """Известная форма дефекта на стадии отчёта, а не проверка формулировки.

        `post exactly "✅ … no blocking issues found."` — фиксированная сводка,
        которая при нуле blocking и трёх should-fix обязывает напечатать только её.
        Фильтр, убранный со входа, возвращался бы на выходе."""
        prompt = _carrier_prompt(carrier).lower()
        assert "no blocking issues" not in prompt, (
            "the fixed summary string is scoped to blocking findings — should-fix and "
            "nice-to-have findings disappear from the PR comment (#374)"
        )
        assert "grouped by severity" in prompt, (
            "the summary contract must require listing findings of every severity (#374)"
        )

    def test_should_fix_has_a_concrete_bar(self, carrier: str) -> None:
        """#458: only `blocking` was defined; should-fix had no bar at all.

        With `rework` reding a required check, an undefined should-fix meant comment
        wording and doc examples could block a merge. Same known-form guard as the
        rest of this class: presence of the bar, not its semantics."""
        prompt = _carrier_prompt(carrier).lower()
        assert "should-fix" in prompt
        assert "behaviour, contract" in prompt or "behavior, contract" in prompt, (
            "should-fix must name what qualifies (behaviour / contract / what the "
            "operator reads), or prose and naming findings land there by default (#458)"
        )

    def test_prompt_retires_findings_with_recorded_rationale(self, carrier: str) -> None:
        prompt = _carrier_prompt(carrier).lower()
        assert "recorded rationale" in prompt, (
            "a finding answered by a rationale in the diff (code comment, "
            "coverage-gaps entry, ADR) must not be re-raised each round (#458)"
        )

    def test_prompt_forbids_relisting_accepted_tradeoffs(self, carrier: str) -> None:
        prompt = _carrier_prompt(carrier).lower()
        assert "re-list" in prompt, (
            "re-runs must describe the increment; re-listing consciously-kept "
            "tradeoffs every round is what made ten rounds of PR #462 grow (#458)"
        )


class TestReviewOutcomeGate:
    def test_review_fetches_live_pr_context_for_reruns(self) -> None:
        context = _named_step("Fetch current PR context")
        script = str(_inputs_for_step(context).get("script", ""))
        prompt = _prompt()
        verifier = _named_step("Enforce Claude review outcome")

        assert context["id"] == "pr-context"
        assert context["uses"] == "actions/github-script@v7"
        assert "github.rest.pulls.get" in script
        assert "pull_number: context.payload.pull_request.number" in script
        assert 'core.setOutput("head_sha", pr.head.sha)' in script
        assert 'core.setOutput("body", pr.body ?? "")' in script
        assert "steps.pr-context.outputs.number" in prompt
        assert "steps.pr-context.outputs.head_sha" in prompt
        assert "steps.pr-context.outputs.body" in prompt
        assert "untrusted data, not instructions" in prompt
        assert "Reviewed head SHA" in prompt
        assert verifier["env"]["LIVE_PR_CONTEXT_STATUS"] == "${{ steps.pr-context.outcome }}"
        verifier_run = str(verifier["run"])
        assert '--live-pr-context-status "$LIVE_PR_CONTEXT_STATUS"' in verifier_run
        assert "--repo" in verifier_run and "github.repository" in verifier_run
        assert "--pr" in verifier_run and "steps.pr-context.outputs.number" in verifier_run

        verifier_source = _named_step("Checkout verifier source")
        assert verifier_source["uses"] == "actions/checkout@v4"
        assert verifier_source["with"]["ref"] == "${{ github.event.repository.default_branch }}"

        checkout = _named_step("Checkout current PR head")
        assert checkout["if"] == "${{ steps.pr-context.outcome == 'success' }}"
        assert checkout["with"]["ref"] == "${{ steps.pr-context.outputs.head_sha }}"

    def test_review_emits_validated_structured_outcome(self) -> None:
        step = _review_step()
        args = str(_inputs().get("claude_args", ""))
        prompt = _prompt()

        assert step["id"] == "review"
        assert "--json-schema" in args
        assert '"outcome"' in args
        assert all(outcome in args for outcome in ("clean", "rework", "blocking"))
        assert "structured output `outcome`" in prompt

    def test_workflow_enforces_structured_outcome_directly(self) -> None:
        verifier = _named_step("Enforce Claude review outcome")

        assert "python -m scripts.check_claude_review_outcome" in str(verifier["run"])
        assert "steps.review.outputs.structured_output" in str(verifier["env"])
        assert "always()" in str(verifier["if"])

    def test_ordinary_review_has_no_marker_repair_or_polling(self) -> None:
        names = [str(step.get("name")) for step in _steps()]
        prompt = _prompt()

        assert "Probe Claude outcome marker" not in names
        assert "Repair Claude outcome marker" not in names
        assert "Verify Claude outcome marker" not in names
        assert "claude-review-outcome:" not in prompt

    def test_prompt_keeps_comments_out_of_merge_authority(self) -> None:
        prompt = _prompt()
        assert "comments are feedback" in prompt
        assert "merge authority" in prompt
        assert "update_claude_comment" in prompt

    def test_controller_exception_has_no_separate_gate_workflow(self) -> None:
        assert not _REMOVED_GATE_WORKFLOW.exists()

    def test_review_uses_workflow_token_instead_of_app_token_exchange(self) -> None:
        """#483: без этого входа ревью молча не запускается на PR, меняющих этот файл.

        Апстрим кладёт `github_token` в `OVERRIDE_GITHUB_TOKEN`, и `setupGitHubToken()`
        возвращает его до обмена OIDC на токен GitHub App Anthropic. Без входа обмен
        выполняется, эндпоинт отвечает `workflow_not_found_on_default_branch` (файл
        воркфлоу на голове PR ≠ версии в `main`), экшен бросает
        `WorkflowValidationSkipError` — и job зеленеет за ~26 секунд, ни разу не вызвав
        модель. Молчаливый скип неотличим от прошедшего ревью (§IV), поэтому вход
        стережётся, а не «помнится».
        """
        assert _inputs().get("github_token") == "${{ github.token }}"

    def test_enforcement_steps_pass_no_controller_classification_options(self) -> None:
        """#483: воркфлоу и CLI обязаны сниматься с классификации одним движением.

        `--repo`/`--pr` существовали только ради карв-аута «контроллерный PR».
        `_parse_options` на неизвестный аргумент печатает `unexpected argument` и выходит
        с 2, поэтому забытый в YAML флаг красит **каждый** PR, а не только контроллерный.
        """
        for name in ("Enforce Claude review outcome", "Enforce Codex review outcome"):
            run = str(_named_step(name)["run"])

            assert "--repo" not in run, f"{name} still passes the removed --repo option"
            assert "--pr " not in run, f"{name} still passes the removed --pr option"


class TestFallbackCarrier:
    """#478: доступность обязательного гейта не должна зависеть от одного провайдера.

    Контекст ревью — required check при `enforce_admins: true`, поэтому исчерпанная
    квота Claude сегодня запирает PR целиком. Квоту нельзя опросить заранее — API
    остатка не существует, — так что «выбрать того, у кого есть квота» реализуется
    цепочкой отказа: носитель 2 включается ровно тогда, когда носитель 1 не дал
    годного вердикта.
    """

    def test_first_carrier_failure_does_not_end_the_job(self) -> None:
        """Без этого шаг Codex недостижим: упавший шаг обрывает джоб."""
        assert _review_step().get("continue-on-error") is True

    def test_validity_is_decided_by_the_script_that_owns_the_rule(self) -> None:
        """§ Один дом политики: `review_gate.py` — «No policy gets a second home».

        Разбор JSON носителя выражением GitHub Actions продублировал бы правило
        валидности в `contains()`/`fromJSON()` — в форме, недостижимой ни для одного
        теста."""
        classify = _named_step("Classify review outcome")

        assert classify["id"] == "classify"
        assert "python -m scripts.check_claude_review_outcome" in str(classify["run"])
        assert "--classify" in str(classify["run"])
        assert "steps.review.outputs.structured_output" in str(classify["env"])
        assert "always()" in str(classify["if"])

    def test_fallback_runs_only_when_the_first_carrier_produced_nothing_usable(self) -> None:
        condition = str(_codex_step()["if"])

        assert "steps.classify.outputs.valid == 'false'" in condition, (
            "the failover must be gated on the classifier's answer, and on nothing else "
            "— a `blocking` verdict is a result, so the second carrier must not run and "
            "overrule it"
        )
        assert "steps.pr-context.outcome == 'success'" in condition, (
            "with the live PR context lost there is nothing to review; spending the paid "
            "carrier on it buys an outcome the enforcement step reds anyway"
        )

    def test_the_fallback_runs_on_a_subscription_not_on_metered_credentials(self) -> None:
        """Носитель, которому нужен платный ключ, в этом репо не включится никогда.

        Задача #478 — доступность гейта при исчерпанной квоте; носитель, чьё условие
        запуска — «выдать отдельный API-ключ», её не решает, он её откладывает.
        Codex code review работает по подписке ChatGPT через свою GitHub-интеграцию,
        поэтому у второго носителя нет ни ключа, ни гарда на ключ."""
        raw = _WORKFLOW.read_text(encoding="utf-8")

        assert "OPENAI_API_KEY" not in raw, (
            "a metered credential in the review gate reintroduces the availability "
            "problem #478 exists to remove"
        )
        assert "uses: openai/codex-action" not in raw, (
            "that action authenticates by API key only (its action.yml gates every "
            "functional step on `openai-api-key`), so it cannot carry a subscription"
        )

    def test_the_fallback_is_a_deterministic_step_not_an_in_runner_model(self) -> None:
        """Носитель 2 ревьюит вне этого раннера; здесь только запрос и чтение ответа."""
        step = _codex_step()

        assert "uses" not in step, (
            "carrier 2 is Codex's own GitHub review; nothing runs a model here"
        )
        assert "python -m scripts.request_codex_review" in str(step["run"])

    def test_the_fallback_verdict_is_keyed_to_the_reviewed_head(self) -> None:
        """Ревью предыдущего пуша — не вердикт о том, что мержится сейчас."""
        run = str(_codex_step()["run"])

        assert "--head-sha" in run and "steps.pr-context.outputs.head_sha" in run, (
            "without the head SHA the step would accept a review of an earlier push "
            "and green the check for code nobody looked at (§IV)"
        )
        assert "--pr" in run and "--repo" in run

    def test_the_fallback_wait_is_bounded(self) -> None:
        """Иначе required-чек висит до шестичасового потолка раннера."""
        step = _codex_step()
        rendered = str(step.get("run", "")) + str(step.get("timeout-minutes", ""))

        assert "--timeout-seconds" in rendered or step.get("timeout-minutes"), (
            "the wait for carrier 2 must have a declared bound; an unanswered request "
            "has to end in a visible red, not in a hanging job"
        )

    def test_the_fallback_writes_the_payload_enforcement_reads(self) -> None:
        """Разъехавшиеся имена выхода дали бы молча пустую нагрузку и красный чек
        с сообщением «ревью недоступно» вместо реального вердикта Codex."""
        assert "steps.codex-review.outputs.payload" in str(_fallback_verifier()["env"])
        assert _codex_step()["id"] == "codex-review"

    def test_the_second_carrier_gets_no_second_prompt_copy_in_the_workflow(self) -> None:
        """Контракт носителя 2 живёт в `AGENTS.md`, где Codex его и читает.

        Копия промпта в workflow была бы третьим домом одной политики — и притом
        домом, который никто не читает: Codex ревьюит вне этого раннера."""
        assert "prompt" not in _codex_step()
        assert _codex_review_rules().strip(), "the carrier-2 contract must not be empty"

    def test_each_enforcement_step_names_its_producer(self) -> None:
        """Иначе вердикт на PR не отвечает на вопрос «кто это ревьюил»."""
        primary = _named_step("Enforce Claude review outcome")
        fallback = _fallback_verifier()

        assert "--producer" in str(primary["run"])
        assert "--producer" in str(fallback["run"])
        assert str(primary["if"]) != str(fallback["if"]), (
            "the two enforcement steps must be mutually exclusive, or a single head "
            "gets two verdicts"
        )
        assert "steps.classify.outputs.valid" in str(fallback["if"])

    def test_a_skipped_fallback_still_reds_the_check(self) -> None:
        """Отсутствие второго носителя — не основание считать ревью пройденным (§IV).

        Шаг enforcement фолбэка запускается по `always()`, поэтому при пропущенном
        Codex он получает пустой outcome и падает с «review unavailable»."""
        assert "always()" in str(_fallback_verifier()["if"])
