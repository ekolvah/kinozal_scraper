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

import pytest
import yaml
from _model_pin_policy import UNPINNED_MODEL_VALUES

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "claude-review.yml"
_REMOVED_GATE_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "agent-review-gate.yml"
)
_ACTION = "anthropics/claude-code-action"
_FALLBACK_ACTION = "openai/codex-action"

# Оба носителя ревьюят по одному контракту (#478). Промпт-гарды ниже параметризованы
# по ним обоим, иначе носитель 2 мог бы молча переоткрыть политику severity из #458:
# гард, знающий только про первый носитель, зеленеет на любом тексте второго.
_CARRIERS = ("Claude review", "Codex review")

# Императивы подавления в начале строки. Карв-аутов нет by design: легитимное
# сужение (находку, которую уже ловит ruff/mypy, понижаем как дубль детерминированного
# гейта) записано в промпте НЕ императивом, поэтому исключать нечего. Карв-аут вида
# «разрешено, если в строке есть слово ruff» был бы скроен ровно под текущий текст
# (change-detector) и пропускал бы любое подавление, упомянувшее ruff.
_SUPPRESSION = re.compile(r"^\s*(skip|ignore|omit|don't report|do not report)\b", re.IGNORECASE)


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
    step = _named_step("Codex review")
    assert _FALLBACK_ACTION in str(step.get("uses", "")), (
        "the second carrier must be the official Codex action; its inputs were verified "
        "against action.yml, and an unknown `with:` key is ignored silently by GitHub"
    )
    return step


def _carrier_prompt(carrier: str) -> str:
    return str(_inputs_for_step(_named_step(carrier))["prompt"])


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

    def test_fallback_is_gated_on_an_env_mapped_secret(self) -> None:
        """`secrets` недоступен в step-level `if:` — гард по env, а не по секрету.

        Секрет не задан → шаг пропущен → валидного outcome нет ни у кого → чек красный,
        ровно как сегодня. Платный путь включается заданием секрета, а не мержем."""
        data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
        job_env = data["jobs"]["claude-review"].get("env") or {}

        assert "OPENAI_API_KEY" in job_env, (
            "the fallback's credential must be mapped into job-level `env:`; the "
            "`secrets` context does not exist in a step-level `if:`"
        )
        assert "secrets.OPENAI_API_KEY" in str(job_env["OPENAI_API_KEY"])
        assert "env.OPENAI_API_KEY != ''" in str(_codex_step()["if"])

    def test_both_carriers_share_the_outcome_vocabulary(self) -> None:
        """Разные словари вердиктов = разная планка мержа в зависимости от квоты."""
        schema = str(_inputs_for_step(_codex_step())["output-schema"])
        claude_args = str(_inputs().get("claude_args", ""))

        assert '"outcome"' in schema
        for outcome in ("clean", "rework", "blocking"):
            assert outcome in schema and outcome in claude_args

    def test_fallback_returns_its_summary_as_data(self) -> None:
        """У codex-action нет MCP-канала комментариев, поэтому текст едет схемой."""
        schema = str(_inputs_for_step(_codex_step())["output-schema"])
        assert '"summary"' in schema, (
            "without a summary field the second carrier's findings never reach the "
            "human, and a review nobody reads is a review that did not happen (§IV)"
        )

    def test_fallback_model_pinned_to_full_id(self) -> None:
        model = str(_inputs_for_step(_codex_step()).get("model", ""))

        assert model, "an unpinned fallback moves to the action's upstream default silently"
        assert model.lower() not in UNPINNED_MODEL_VALUES

    def test_fallback_safety_strategy_is_explicit_and_never_unsafe(self) -> None:
        """`unsafe` даёт модели доступ к памяти процесса, где лежит её же API-ключ."""
        strategy = str(_inputs_for_step(_codex_step()).get("safety-strategy", ""))

        assert strategy, "the sandbox posture must be chosen, not inherited from a default"
        assert strategy != "unsafe"

    def test_fallback_result_is_read_with_bracket_syntax(self) -> None:
        """`outputs.final-message` парсится как вычитание — молча пустая строка.

        Имя выхода взято из `action.yml`, где сам экшен обращается к нему через
        `outputs['final-message']`."""
        for step in (_named_step("Publish Codex review summary"), _fallback_verifier()):
            rendered = str(step.get("run", "")) + str(step.get("env", ""))
            assert "outputs['final-message']" in rendered, (
                "a hyphenated output name must be read with bracket syntax; dotted "
                "access evaluates to an empty string and reds nothing"
            )

    def test_fallback_summary_is_published_without_giving_the_model_a_token(self) -> None:
        publish = _named_step("Publish Codex review summary")
        codex_inputs = _inputs_for_step(_codex_step())

        assert "python -m scripts.publish_review_summary" in str(publish["run"])
        assert "GH_TOKEN" in str(publish.get("env", {}))
        assert "GH_TOKEN" not in str(codex_inputs), (
            "the review model must not receive a write token: its context holds the "
            "untrusted diff and PR body"
        )
        assert "gh " not in str(codex_inputs.get("prompt", "")), (
            "publishing is a deterministic step, not something the model shells out to"
        )

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
