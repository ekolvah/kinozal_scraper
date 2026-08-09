"""Anti-drift guards for the cloud review gate (`.github/workflows/agent-review.yml`, #374).

Static YAML guard: no network or credentials — the style of `tests/test_workflow_isolation.py`
and `tests/test_settings_deny.py`.

**What it guards.** The cloud-reviewer prompt contained a severity filter AT THE DISCOVERY STAGE
(“Skip nitpicks — ruff handles formatting/lint”). Since Opus 4.7, models follow this literally:
a finding is made, rated below the threshold, and silently not published, while a filtered finding
is indistinguishable from its absence (§IV). A second instance of the same defect existed at the
REPORTING stage: `post exactly "✅ … no blocking issues found."` prohibited adding should-fix
findings to the summary. Both classes produce a reviewer-missed bug — priority (1) of the objective
function, so the guard is justified: the regression concerns correctness, not resources only
(criterion: `docs/architecture/testing.md`, “when a test is NOT worth writing”).

**Guard boundaries, honestly.** It catches the KNOWN DEFECT FORM (a suppression imperative at the
start of a line, a summary gag line) and the PRESENCE of the coverage-first contract (severity +
confidence). An alternative wording of the filter (“be selective”, “only report what matters”) will
not be caught by an exit code — a script cannot check prompt semantics; `docs/architecture/ci.md`
prose and architect review uphold it. The `docs/architecture/coverage-gaps.md` ledger is not updated:
coverage depth is limited, not coverage declined.

This is the **cloud half** of model pinning. Local agents
(`.claude/agents/architect-reviewer.md` — `model: opus`, a short alias) are #392.
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

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "agent-review.yml"
_REMOVED_GATE_WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "agent-review-gate.yml"
)
_ACTION = "anthropics/claude-code-action"
_AGENTS = Path(__file__).resolve().parent.parent / "AGENTS.md"
_CODEX_RULES_HEADING = "## Code Review Rules"

# Suppression imperatives at the start of a line. There are no carve-outs by design: the legitimate
# narrowing (downgrade a finding already caught by ruff/mypy as a duplicate of a deterministic gate)
# is written in the prompt NOT as an imperative, so nothing needs exclusion. A carve-out such as
# “permitted when the line contains ruff” would be tailored precisely to the current text
# (change detector) and allow any suppression that mentioned ruff.
_SUPPRESSION = re.compile(r"^\s*(skip|ignore|omit|don't report|do not report)\b", re.IGNORECASE)


# Cache rather than fixture: files are read-only and do not change during a run,
# while parsing 11 KB of YAML for every assertion wastes tens of milliseconds.
@lru_cache(maxsize=1)
def _steps() -> list[dict[str, Any]]:
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return cast("list[dict[str, Any]]", data["jobs"]["agent-review"]["steps"])


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
    """The `AGENTS.md` section that Codex code review reads as its prompt."""
    text = _AGENTS.read_text(encoding="utf-8")
    assert _CODEX_RULES_HEADING in text, (
        f"{_CODEX_RULES_HEADING!r} is the documented place where Codex code review "
        "picks up repository rules; without it carrier 2 reviews by its own defaults"
    )
    section = text.split(_CODEX_RULES_HEADING, 1)[1]
    return section.split("\n## ", 1)[0]


# Both carriers review under one contract (#478). The prompt guards below are parameterized over
# both, otherwise carrier 2 could silently reopen the severity policy from #458: a guard that knows
# only the first carrier becomes green for any text in the second.
# The contracts have different homes: carrier 1 reads its prompt from the workflow, carrier 2 reads
# `## Code Review Rules` in `AGENTS.md` (the documented configuration point for Codex code review).
# There are still two copies, but each is in its canonical location.
# The carrier list and each prompt loader are declared together here: if separated, they could create
# a guard checking carrier 1 twice and silently not checking carrier 2.
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
        """The model is set by the `--model` CLI flag within `claude_args`.

        The input name is verified against the action's `action.yml` (`claude_args` is “Additional
        arguments to pass to Claude CLI”; the action has no separate `model` input), rather than
        guessed: GitHub Actions SILENTLY ignores an unknown `with:` input, and a guard for an
        invented name would be green with the pin absent."""
        args = str(_inputs().get("claude_args", ""))
        found = re.search(r"--model\s+(\S+)", args)
        assert found, (
            "agent-review must pin the model explicitly via "
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
    """One discovery and reporting contract for both review carriers (#478).

    Carrier 2 was added for availability, not a second opinion on what counts as a
    finding: divergent prompts would give different verdicts on the same diff depending
    on whose quota was available that day.
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
        """Known defect form at the reporting stage, not wording verification.

        `post exactly "✅ … no blocking issues found."` is a fixed summary that, with zero
        blocking and three should-fix findings, requires printing only itself. The filter
        removed at input would return at output."""
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


@pytest.mark.parametrize("carrier", _CARRIERS)
class TestDocumentationReviewPrompt:
    """Both review carriers own the semantic half of the docs policy (#432)."""

    def test_carriers_follow_linked_repository_docs(self, carrier: str) -> None:
        prompt = " ".join(_carrier_prompt(carrier).lower().split())
        assert "repository docs" in prompt and "links to" in prompt, (
            f"{carrier} reads CLAUDE.md but is not told to follow its repository-doc links"
        )

    def test_carriers_check_current_state_not_history(self, carrier: str) -> None:
        prompt = " ".join(_carrier_prompt(carrier).lower().split())
        missing = [
            marker
            for marker in (
                "docs/architecture/project-map.md",
                "current implemented state",
                "history",
                "ideas",
                "removing",
                "meaning",
            )
            if marker not in prompt
        ]
        assert not missing, (
            f"{carrier} does not own the semantic documentation review contract; "
            f"missing markers: {missing} (#432)"
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

        assert "python -m scripts.check_agent_review_outcome" in str(verifier["run"])
        assert "steps.review.outputs.structured_output" in str(verifier["env"])
        assert "always()" in str(verifier["if"])

    def test_ordinary_review_has_no_marker_repair_or_polling(self) -> None:
        names = [str(step.get("name")) for step in _steps()]
        prompt = _prompt()

        assert "Probe Claude outcome marker" not in names
        assert "Repair Claude outcome marker" not in names
        assert "Verify Claude outcome marker" not in names
        assert "agent-review-outcome:" not in prompt

    def test_prompt_keeps_comments_out_of_merge_authority(self) -> None:
        prompt = _prompt()
        assert "comments are feedback" in prompt
        assert "merge authority" in prompt
        assert "update_claude_comment" in prompt

    def test_controller_exception_has_no_separate_gate_workflow(self) -> None:
        assert not _REMOVED_GATE_WORKFLOW.exists()

    def test_review_uses_workflow_token_instead_of_app_token_exchange(self) -> None:
        """#483: without this input, review silently does not start on PRs changing this file.

        Upstream puts `github_token` in `OVERRIDE_GITHUB_TOKEN`, and `setupGitHubToken()`
        returns it before exchanging OIDC for the Anthropic GitHub App token. Without the input,
        the exchange occurs, the endpoint responds `workflow_not_found_on_default_branch` (the
        workflow file at the PR head differs from its version in `main`), the action raises
        `WorkflowValidationSkipError`, and the job turns green in ~26 seconds without ever calling
        the model. A silent skip is indistinguishable from completed review (§IV), so the input is
        guarded rather than “remembered”.
        """
        assert _inputs().get("github_token") == "${{ github.token }}"

    def test_enforcement_steps_pass_no_controller_classification_options(self) -> None:
        """#483: workflow and CLI must be removed from classification in one operation.

        `--repo`/`--pr` existed only for the “controller PR” carve-out. For an unknown argument,
        `_parse_options` prints `unexpected argument` and exits with 2, so a flag left in YAML turns
        **every** PR red, not only a controller PR.
        """
        for name in ("Enforce Claude review outcome", "Enforce Codex review outcome"):
            run = str(_named_step(name)["run"])

            assert "--repo" not in run, f"{name} still passes the removed --repo option"
            assert "--pr " not in run, f"{name} still passes the removed --pr option"


class TestFallbackCarrier:
    """#478: required-gate availability must not depend on one provider.

    The review context is a required check with `enforce_admins: true`, so an exhausted Claude
    quota locks the entire PR today. Quota cannot be queried in advance — no remaining-quota API
    exists — so “select the one with quota” is implemented by a failure chain: carrier 2 starts
    exactly when carrier 1 produced no usable verdict.
    """

    def test_first_carrier_failure_does_not_end_the_job(self) -> None:
        """Without this, the Codex step is unreachable: a failed step ends the job."""
        assert _review_step().get("continue-on-error") is True

    def test_validity_is_decided_by_the_script_that_owns_the_rule(self) -> None:
        """§ One policy home: `review_gate.py` — “No policy gets a second home”.

        Parsing carrier JSON with a GitHub Actions expression would duplicate the validity
        rule in `contains()`/`fromJSON()` — in a form unreachable by any test."""
        classify = _named_step("Classify review outcome")

        assert classify["id"] == "classify"
        assert "python -m scripts.check_agent_review_outcome" in str(classify["run"])
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
        """A carrier needing a paid key will never start in this repository.

        #478 concerns gate availability with an exhausted quota; a carrier whose start condition
        is “provide a separate API key” does not solve it, it postpones it. Codex code review runs
        on a ChatGPT subscription through its GitHub integration, so carrier 2 has neither a key
        nor a guard for a key."""
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
        """Carrier 2 reviews outside this runner; this only requests and reads the response."""
        step = _codex_step()

        assert "uses" not in step, (
            "carrier 2 is Codex's own GitHub review; nothing runs a model here"
        )
        assert "python -m scripts.request_codex_review" in str(step["run"])

    def test_the_fallback_verdict_is_keyed_to_the_reviewed_head(self) -> None:
        """A review of the previous push is not a verdict on what merges now."""
        run = str(_codex_step()["run"])

        assert "--head-sha" in run and "steps.pr-context.outputs.head_sha" in run, (
            "without the head SHA the step would accept a review of an earlier push "
            "and green the check for code nobody looked at (§IV)"
        )
        assert "--pr" in run and "--repo" in run

    def test_the_fallback_wait_is_bounded(self) -> None:
        """Otherwise the required check hangs until the runner's six-hour limit."""
        step = _codex_step()
        rendered = str(step.get("run", "")) + str(step.get("timeout-minutes", ""))

        assert "--timeout-seconds" in rendered or step.get("timeout-minutes"), (
            "the wait for carrier 2 must have a declared bound; an unanswered request "
            "has to end in a visible red, not in a hanging job"
        )

    def test_the_fallback_writes_the_payload_enforcement_reads(self) -> None:
        """Divergent output names would silently yield an empty payload and a red check
        with “review unavailable” instead of the actual Codex verdict."""
        assert "steps.codex-review.outputs.payload" in str(_fallback_verifier()["env"])
        assert _codex_step()["id"] == "codex-review"

    def test_the_second_carrier_gets_no_second_prompt_copy_in_the_workflow(self) -> None:
        """Carrier 2's contract lives in `AGENTS.md`, where Codex reads it.

        A prompt copy in the workflow would be a third home for one policy — and one that
        nobody reads: Codex reviews outside this runner."""
        assert "prompt" not in _codex_step()
        assert _codex_review_rules().strip(), "the carrier-2 contract must not be empty"

    def test_each_enforcement_step_names_its_producer(self) -> None:
        """Otherwise the PR verdict does not answer “who reviewed this?”"""
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
        """The absence of carrier 2 is not grounds to consider review passed (§IV).

        The fallback enforcement step runs under `always()`, so with Codex skipped it receives
        an empty outcome and fails with “review unavailable”."""
        assert "always()" in str(_fallback_verifier()["if"])
