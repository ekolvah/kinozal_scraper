# CI and deployment

**На какой вопрос отвечает этот файл:** какие автоматические гейты качества стоят
на пути изменения и на чём они работают. Ось — «гейт», а не «GitHub Actions»:
поэтому сюда же заезжает **локальный** plan-стадийный `architect-reviewer`
(`.claude/agents/`), который в CI не запускается, но по
[`principles.md §VII`](principles.md#vii-simplicity-first) стоит гейтом наравне с
cloud-ревью. Модельная поверхность агентного тулинга (обе половины) описана в
§«Model pinning» — это её единственный дом.

**Известный долг IA:** §«Environment variables» отвечает не на этот вопрос, а на
runtime-вопрос (прод-переменные пайплайна) — файл остаётся смесью, помеченной ❌ в
[`project-map.md`](project-map.md). Формулировка выше описывает **основную** ось, а
не утверждает, что расщепления не требуется.

## Local pre-commit

```bash
python scripts/ci_check.py
```

Runs every check in the `CHECKS` registry (`scripts/ci_check.py`), in order:
ruff format → ruff lint → detect-secrets → pytest → pip-audit (runtime) →
pip-audit (dev) → requirements consistency → mypy → import contracts. (Module-docstring presence
is enforced *inside* ruff lint via `D100`/`D104`/`D419`, not a separate step —
see the Module-docstring gate below.)

**Single source of truth.** The registry is the *only* place the check set is
defined. `ci.yml` does not re-list checks — each CI step runs
`python scripts/ci_check.py --only <name>`, so local and CI cannot drift. If
`ci_check.py` is green locally, CI runs the identical checks. Adding or removing
a check in the registry without updating `ci.yml` fails
`tests/test_ci_check.py::TestStepParity` (#153).

Pre-push hook: `.githooks/pre-push` runs `ci_check.py` automatically.
Activate: `git config core.hooksPath .githooks`

> **Disambiguation:** this section's title "Local pre-commit" names the
> pre-commit *moment* (the git-hook that runs before a push), **not** the
> [`pre-commit`](https://pre-commit.com) framework — which this repo
> deliberately does **not** use (next block).

### Secret scan (`secrets`, #389)

`detect_secrets.pre_commit_hook` over every tracked file, run as a registry check —
the local barrier between "an agent or contributor pasted a key into a source file"
and `origin/main`. It sits **right after `lint`**, before the slow gates: a leaked key
must redden the run in seconds, not after ~3 minutes of pytest + pip-audit. Cost is
~5 s for ~130 files.

**`-X utf8` is load-bearing, not decoration.** `detect_secrets/core/scan.py:261` opens
each file with the *platform default* encoding and silently swallows the resulting
`UnicodeDecodeError`. On Windows (cp1252) that skipped every file carrying a Russian
comment — most of this repo — so the gate ran, printed nothing and exited 0, while the
same commit was scanned in full on Linux CI. Caught on PR #394: green locally, red in
CI. UTF-8 mode makes both platforms scan the same set; without it "green locally"
carries no information (§IV silent skip + the #153 local↔CI drift class).
`tests/test_secrets_gate.py::TestGateFires::test_planted_secret_in_a_non_ascii_file_exits_nonzero`
pins it.

**It used to exist only as configuration and executed literally never.** Both layers
were broken independently, and each alone was enough to make it a no-op:

- `.pre-commit-config.yaml` declared a `Yelp/detect-secrets` hook, but
  `core.hooksPath` = `.githooks` (which holds only `pre-push`) and nothing invoked
  the `pre-commit` framework — so no hook in that file ever ran (the stripped
  trailing newline in external PR #370 is the same config's `end-of-file-fixer`
  not firing);
- `.secrets.baseline` carried `"plugins_used": []`, so the hook printed
  `No plugins to scan with!` and **exited 0** on a planted AWS key. Fixing only the
  wiring would have produced a green gate that scans with zero plugins.

**No baseline, by design.** `detect_secrets/pre_commit_hook.py` with `--baseline`
can return `3` after *rewriting* the baseline file in place (line-number drift),
which `ci_check` reports as a red push that already mutated a tracked file behind
your back — the mutation-during-a-gate pattern this repo rejects for
`scripts/hooks.py` — and the next run then fails differently with "baseline is
unstaged". A baseline is also a one-command "make the gate green" button for a
genuinely leaked key, and its paths are written in the host OS's separators, so a
Windows-generated baseline reddens Linux CI. Without `--baseline` the hook only ever
returns 0 or 1 and mutates nothing.

The two **captured HTML fixtures** (`tests/fixtures/cloudflare_block_403.html`,
`tests/fixtures/github_trending/trending_daily.html`) are excluded by
`ci_check._secrets_targets` — asset digests in third-party markup are high-entropy
false positives by construction. The exclusion is a tested pure function over
`git ls-files` output (always POSIX separators), not a tool-side regex whose
semantics shift with the OS path separator. For a false positive **inside** our own
code the escape hatch is an inline `# pragma: allowlist secret` at the site (see
`tests/test_secrets_gate.py`), never a blanket exclusion.

`_tracked_files()` exits 1 when `git ls-files` fails, and `check_secrets()` exits 1
on an empty file set: the hook returns 0 when handed no files, so a broken `git`
invocation would otherwise reproduce this issue's own defect one layer deeper (§IV).
`tests/test_secrets_gate.py` covers both, plus a planted key (non-zero) and a clean
file (zero).

`.pre-commit-config.yaml` and `.secrets.baseline` were **deleted** with this change,
and `pre-commit` dropped from `requirements-dev.in`: the config was the source of the
false "we have a secrets gate" signal, and after the move it would have been a second,
competing home for `detect-secrets` settings. Its `ruff`/`ruff-format` hooks are
already in the registry; `check-yaml`/`check-toml`/`check-json`/`trailing-whitespace`/
`end-of-file-fixer` never ran a single time, so removing them regresses nothing (no
replacement gates are introduced here — that would be a different unit).

### Session hooks (`scripts/hooks.py`, #281)

A separate, *earlier* feedback layer that runs **during** an agent session, not
at push. `.claude/settings.json` declares a single `PostToolUse` hook (matcher
`Edit|Write`) invoking `python "$CLAUDE_PROJECT_DIR/scripts/hooks.py" on-edit`,
which dispatches two cheap checks in one process right after each file edit:

- `*.py` → ruff **check-only** (`ruff format --check` + `ruff check`, **no
  `--fix`/format mutation** — the harness tracks file contents, so rewriting
  behind its back breaks the next Edit's `old_string` match). Remaining lint →
  stderr + exit 2 (PostToolUse exit 2 feeds stderr back to the agent).
- `requirements*.in` → a `pip-compile` reminder (workflow #7 is otherwise only
  prose — this makes forgetting it a *visible* marker, not a CI-time surprise).

§IV split: a malformed/empty payload is a silent no-op, but a ruff *exec*
failure (not installed / bad config) is a **visible, distinct** marker — a
silently-broken hook must not masquerade as "lint clean". Decision logic is pure
functions (`plan_checks`/`classify_ruff_result`) with unit tests
(`tests/test_hooks.py`); wiring is anti-drift-guarded by
`tests/test_settings_hooks.py` (mirrors `test_settings_deny.py`).

This is instant feedback that **complements, never replaces** `ci_check.py` (the
canonical pre-push gate), and is unrelated to the `pre-commit`/`tox` *framework*
consciously declined below (#255/#267) — that no-go is about a PR-time
tool-registry framework, this is a session-time editor hook.

### `pre-commit`/`tox` consciously not adopted (#255)

The `CHECKS` registry looks like a hand-rolled task-runner, so «why not the
off-the-shelf [`pre-commit`](https://pre-commit.com) framework, or `tox`?» is a
fair recurring question. Evaluated go/no-go in #255 → **no-go**, for a single
root reason plus supporting ones (genre mirrors the vulture drop below):

- **Root reason — `pre-commit` re-introduces the #153 drift class.** Every hook
  under `pre-commit` pins its tool version via `rev:` in
  `.pre-commit-config.yaml` and runs it in an **isolated venv**. That is a
  *parallel* source of each tool's version, separate from `requirements-dev.txt`
  (today `python -m ruff`/`mypy` run the single lockfile-pinned version). So
  `pre-commit` systemically forks the very version-drift class that the registry
  eliminated (#153). `mypy` is the sharp illustration: its isolated venv can't
  see project deps, forcing an `additional_dependencies:` list — a hand-copied
  duplicate of the dependency set outside `requirements.txt`.
- **Half the checks aren't file-linters.** `requirements` (pin/`.in` drift) and
  `imports` (import-linter via its Python API — the console script is
  unreliable-on-PATH on Windows + the #109 `subprocess stdout=None` pitfall,
  #234) are custom logic. Under `pre-commit` they'd stay scripts wrapped in
  `local` hooks — zero gain, same Windows-PATH exposure for `imports`.
- **The drift problem is already solved, cheaper.** The registry is the single
  source of truth and `tests/test_ci_check.py::TestStepParity` guards
  registry ↔ `ci.yml` parity — no new dependency, ~185 lines, and it houses the
  non-file-linter gates uniformly.
- **A *partial* migration is strictly worse.** file-linters → `pre-commit`,
  gates stay scripts ⇒ two overlapping systems and a **three-way** parity
  (`pre-commit` config ↔ registry ↔ `ci.yml`) whose third edge is **unguarded**
  until someone writes a new parity test — a net increase in surface, the
  opposite of the win.
- **No clean partial-win survives the drift cost.** (a) staged-only speed is
  marginal — `pytest`/`mypy`/`imports`/`pip-audit` are whole-project by nature;
  (b) contributor onboarding — `pre-commit install` merely replaces the one
  `git config core.hooksPath .githooks` line with its own install step (net
  wash).
- **`tox`/`nox` solve a problem we don't have.** They run a Python-**version
  matrix**; this project is pinned to a single 3.12. Clear no.

**Revisit trigger (wait-for-pain, not permanent dismissal):** flip to a partial
`pre-commit` (file-linters only) *iff* real contributor pain from manual
hook-version management appears; adopt `tox`/`nox` *iff* a genuine multi-version
Python matrix becomes a requirement.

The `.pre-commit-config.yaml` that survived this no-go was deleted in #389 — it ran
nothing (see [Secret scan](#secret-scan-secrets-389)) while reading as an active
gate, so the doc and the repo now say the same thing.

## CI workflow (`ci.yml`)

Triggers: `pull_request` (covers every PR branch) + `push` to `main` only
(post-merge gate — catches a semantic conflict between two PRs each green
in isolation). `issue-*` is deliberately **not** a push trigger: a PR branch
push would otherwise fire the `quality` job twice (once per event) for the
same commit. The required status check is the bare context `quality`
(event-agnostic — confirmed via `gh api …/required_status_checks` →
`contexts: ["quality"]`), so the `pull_request` run satisfies branch
protection on its own and dropping `issue-*` orphans nothing (#206). Do not
re-add `issue-*` to `push` to "get CI on a branch" — the `.githooks/pre-push`
hook already runs the identical `ci_check.py` locally before every push.

Steps: checkout → Python 3.12 → install deps → then one
`python scripts/ci_check.py --only <name>` step per registry check (format,
lint, pytest, pip-audit, pip-audit-dev, requirements, mypy, imports).
The per-step split keeps the GitHub Actions UI granular (you see *which* gate
failed) while the check set itself stays defined once, in `ci_check.py`.

mypy type-checks every `*.py` outside `_EXCLUDE_DIRS` (`.venv`, `.git`,
`__pycache__`, `.audit-tmp`, `.claude`) and any `pytest-cache-files-*` dir, via
`ci_check._find_modules()` — the same discovery used locally.

Imports between modules (`from kinozal_scraper.generic_pipeline import …`) are
absolute package imports: the sources live in the installable package
`src/kinozal_scraper/`, so mypy resolves them natively by package name — no
`mypy_path`, no whole-file-list trick, and a single-file invocation
(`mypy src/kinozal_scraper/github_popular_pipeline.py`) resolves the same way. The package
layout also makes mypy a **load-bearing** guard for the entry points: a
`python -m` module's `if __name__ == "__main__"` block is type-checked here even
though `import`-based tests never execute it (#237). The package must be
importable — CI runs `pip install -e . --no-deps` before the checks (the
canonical dependency source stays `requirements*.in/.txt`; the editable install
adds only the package itself, never shadowing the lockfile).

The `imports` check runs [import-linter](https://github.com/seddonym/import-linter)
against `.importlinter` (repo root), turning part of §II (Protocol Boundaries +
DI) into a deterministic gate. Two contracts, both green today — value is
*preventing future drift*, not cleaning existing violations (#234):

- **`adapter-no-auth`** (`forbidden`) — the real §II win: the service adapters
  (`sheets_storage`/`telegram_notifier`/`gemini_enricher`) must not import
  `kinozal_auth`. Encodes "implementations receive ready clients, not
  credentials — auth lives in the caller" as a machine rule. (`crypto` was the
  second forbidden module until #386 deleted that module outright.)
- **`pipeline-layers`** (`layers`) — pins dependency *direction*: orchestrators
  (`*_pipeline`) may import the adapters and the shared `generic_pipeline` core,
  never the reverse, and no orchestrator/adapter imports a sibling.

`check_imports()` calls import-linter's **Python API** (`importlinter.api`), not
the `lint-imports` console script — the console entry point is unreliable-on-PATH
on Windows and would reintroduce the #109 `subprocess stdout=None` pitfall. grimp
builds the graph statically (AST), so the `__main__` wiring blocks never execute.
`tests/test_import_contracts.py` is an anti-drift guard: it asserts the
contracts' *load-bearing fields* (which modules are forbidden/layered), so an
agent can't quietly gut a contract while keeping its name. A stricter
"orchestrators import only Protocol modules" contract is not expressible today —
the `Protocol` classes share a module with their concrete impls — and is
deferred to a Protocol-extraction refactor (#234 Out of scope). `principles.md`
is deliberately **not** edited: §II is tool-agnostic canon, so the tool mention
lives here and in `runtime.md`, not in the constitution.

### Complexity ratchet (lint, #233)

The `lint` check also enforces three ruff complexity rules — `C901` (mccabe
cyclomatic), `PLR0912` (too-many-branches), `PLR0915` (too-many-statements) —
as a **ratchet against method sprawl**: new/changed code over threshold fails
CI, legacy is grandfathered. No new dependency; it rides on the existing
`check_lint`. Thresholds: `max-complexity = 12` (`[tool.ruff.lint.mccabe]`),
PLR0912/PLR0915 on ruff defaults (12 branches / 50 statements). The C901 value
is **aligned with PLR0912's default branch threshold (12)**, not tuned to
today's code — that keeps the choice defensible against Goodhart/bikeshedding.

Six current violators are grandfathered with a per-function `# noqa: <exact
codes>` on the `def` line (precise, self-documenting at the site — not a
per-file ignore, which would blind the whole file to *new* sprawl).
`tests/test_complexity_ratchet.py` is an anti-drift guard (mirrors
`test_ruff_silence_rules.py` / `test_import_contracts.py`): it pins that the
codes stay in the effective select and the threshold keeps its value, so the
gate can't be silently gutted via `pyproject.toml`.

**Known limitation:** a blanket `# noqa` lets a grandfathered function grow *more*
complex undetected — ruff has no native baseline, so the ratchet protects new
code and new functions, not the frozen six. The real fix is splitting them,
tracked in #251 (§V documented-mitigation, not a silent assumption). `RUF100`
(self-cleaning noqa) was considered but deferred: it flags 18 pre-existing
unused-noqa across the repo — a separate cleanup, not this gate (#233 Out of
scope).

### Dead-code ratchet (lint, #235)

The `lint` check also enforces ruff `ERA001` (commented-out code) as a
**preventive ratchet**: new commented-out code fails CI. No new dependency; it
rides on the existing `check_lint`. The repo measured **clean** — the only hit
was an illustrative schema comment (`# [dedupe_key, title, ...]`) that ruff
mis-parses as a list literal; it was fixed by **rewording to prose**, not a
`# noqa` (the code was never dead, so suppressing a real detector would train
the escape hatch on a non-exception — §IV). For a *genuine* future false
positive, the escape hatch is a per-site `# noqa: ERA001` with a reason, not a
per-file ignore. `tests/test_ruff_dead_code_rule.py` is an anti-drift guard
(mirrors `test_ruff_silence_rules.py` / `test_complexity_ratchet.py`): it pins
that `ERA001` stays in the effective select and is not neutralised via
`ignore` / `per-file-ignores`.

**`vulture` (cross-module unused) was consciously dropped**, not added: the repo
measured **zero** cross-module dead code, and local unused is already caught by
ruff `F` (F401/F841). vulture is FP-prone on this codebase's dynamic shape
(pipeline registry, declarative config, `Protocol` impls, pytest fixtures,
`__main__` entrypoints) — it would add a dependency, a gate and a whitelist that
needs per-CI triage, to guard a hypothetical. Deferred **wait-for-pain**: revisit
if real cross-module dead code appears that `ERA001` cannot catch (#235 Out of
scope).

### Module-docstring gate (lint, #253)

Every source `.py` under `src/` must carry a non-empty top-level docstring — the
canonical "what question does this file answer", read just-in-time when the file
is opened. This was a bespoke 78-line `scripts/check_headers.py` (+ its test);
it is now three ruff rules riding on `check_lint`, no separate step, no
dependency:

- `D100` — missing docstring in a public **module**;
- `D104` — missing docstring in a public **package** (`__init__.py`; `D100`
  does **not** cover it — the delta that would have silently dropped the
  `kinozal_scraper/__init__.py` docstring requirement);
- `D419` — **empty**/whitespace-only docstring (reproduces the script's
  `doc.strip()` half).

Scope is a superset of the old gate: the script scanned `src/` only; the rules
run repo-wide with `per-file-ignores` `"tests/**" = [D100, D104, D419]` (tests
were never docstring-checked), so `src/` **and** `scripts/`/root are now
covered (both measured clean; `scripts/__init__.py` got a one-line docstring).
`D101`/`D103` (class/function docstrings) are deliberately **not** selected —
the gate is module/package-level only. One consciously-accepted narrowing vs.
the old script: `D100`/`D104` flag only *public* modules/packages, so a future
underscore-private `src/kinozal_scraper/_internal.py` would slip the gate where
the name-agnostic script caught it — low risk (no such module today), noted here
rather than silently. `tests/test_ruff_docstring_rule.py` is an
anti-drift guard (mirrors the silence/complexity/dead-code guards): it pins the
three codes in the effective select, out of global `ignore`, and not
neutralised for any `src`/`scripts` path via `per-file-ignores`.

The script's **§IV no-op guard** (fail if the scan found zero files — a
mis-pointed/empty `src/` going silently green, #237 B1) was **retired, not
lost**: that failure mode was an artefact of the script's parameterised
`root.rglob(Path("src"))`; `ruff check .` recurses the whole tree from cwd and
cannot miss the package that way. The residual "package vanished/empty" case is
caught **strictly harder** by `test_package_importable.py` (17 hard-coded
`import_module` asserts) + `test_repo_layout.py` (flat/root-drift) + mypy +
import-linter. Keeping a bespoke no-op mini-gate would be the very velcro this
change removes.

### Unused-arg / private-access ratchet (lint, #236)

The `lint` check also enforces three ruff rules with a *mixed* signal, triaged
per-hit before enabling (110 existing hits, zero real dead params in `src/`):

- `ARG001` — unused **function** argument;
- `ARG002` — unused **method** argument;
- `SLF001` — private-member access (`obj._x` where `obj` is not `self`/`cls`).

Like the complexity/dead-code ratchets, ruff has no native baseline, so the
value is **forward**: new `src/` code with a dead param or cross-class private
access fails CI. Two silencing mechanisms, deliberately different, and this is
the distinction the guard test pins:

- **`tests/**` is a *categorical* exemption** (`per-file-ignores`
  `"tests/**" = […, ARG001, ARG002, SLF001]`): white-box tests legitimately
  reach into private members (§II mandates calling internal helpers directly)
  and carry mock signatures whose params are dictated by the mocked callable,
  not by usage. This is **unlike `ERA001`**, where tests are *not* exempt
  (commented-out code is dead regardless of file role) — so the surface pattern
  "tests always get a per-file-ignore" must **not** be cargo-culted from here.
- **Individual false positives in `src/` get a per-site `# noqa`.** The two
  hits are Protocol-conformance stubs (`NullEnricher.enrich`'s `item`,
  `InMemoryStorage.append_rows`'s `headers`) whose param is required by the
  interface but unused by that one implementation. A per-site noqa is the escape
  hatch for a *genuine* FP — never for a real detector hit (that would train the
  hatch on a non-exception, §IV).

The two `SLF001` src hits were **not** noqa'd: `RotatingGeminiEnricher` reached
into a sibling `GeminiEnricher._model_name` across the class boundary — a real
§II leak, not noise. It was root-caused (§V) with a public `model_name`
property, so `SLF001` has **zero** surviving src hits. `ARG003`/`004`/`005`
(classmethod/staticmethod/lambda unused args) stay unselected — a conscious
defer (#236 Out of scope), not a silent gap. `tests/test_ruff_arg_slf_rules.py`
is an anti-drift guard (mirrors the docstring/complexity/dead-code guards): it
pins the three codes in the effective select, out of global `ignore`, and not
neutralised for any `src`/`scripts` path via `per-file-ignores`.

## Claude review workflow (`claude-review.yml`)

Triggers: every `pull_request: opened/synchronize`.

Uses `anthropics/claude-code-action@v1` to run an automated code review on
every PR push. Posts inline comments at relevant lines and a top-level
verdict. Does **not** approve or merge — human reviewer keeps that.

Visibility is guaranteed via two layers:

- `track_progress: true` — the action itself posts a tracking comment on
  the PR at start ("Claude Code is reviewing…") and updates it as the
  run proceeds. Independent of whatever Claude does, this guarantees at
  least one visible signal that the review ran.
- The prompt instructs Claude to (a) post per-issue inline comments via
  `mcp__github_inline_comment__create_inline_comment` and (b) finish
  with a top-level summary via `Bash(gh pr comment ...)`. The earlier
  approach (`use_sticky_comment: true`) only controlled comment
  *format*, not whether Claude published anything — when Claude found no
  issues and didn't invoke a publishing tool, the PR stayed silent.

`show_full_output: true` is enabled while we're stabilising review
behaviour — full SDK transcript appears in Actions logs. Remove once
the loop is reliable; it adds noise and may surface internal model
chatter.

### Coverage-first prompt: no filtering at the search stage (#374)

The prompt used to say `Skip nitpicks — ruff handles formatting/lint`.
From Opus 4.7 on, models follow such an instruction **literally**: the
finding is made, judged below the stated bar, and silently not reported
— and a filtered finding is indistinguishable from no finding at all
(§IV). The same defect had a second instance at the *reporting* stage:
`post exactly "✅ Review complete — no blocking issues found."` forbade
adding anything else, so a run with three should-fix findings and zero
blocking was required to print that one line and nothing more. Removing
the filter from the input while leaving it on the output would have
fixed half the defect.

The contract now is **grade, never drop**:

- every finding is reported with `severity` (blocking / should-fix /
  nice-to-have) and `confidence` (high / medium / low) — the human
  filters, not the model;
- `blocking` is a concrete bar (wrong behaviour, failing or missing test
  for changed behaviour, misleading result, leaked secret, CLAUDE.md
  convention violation), not the qualitative word "nitpick";
- what the deterministic gate already catches (ruff / mypy in
  `ci_check.py`) is graded `nice-to-have, duplicate of ci_check` —
  ranked last because another executor covers it, not withheld;
- inline comments carry blocking / should-fix only, so the inline
  channel does not drown; everything else appears in the summary, which
  lists all findings grouped by severity. The fixed one-line summary is
  scoped to "nothing found at any severity".

`tests/test_claude_review_workflow.py` guards the **form** of this: no
suppression imperative at the start of a prompt line, `severity` +
`confidence` present, no `no blocking issues` gag string. It cannot
check the prompt's *semantics* — a filter rephrased as "be selective"
passes — so the qualitative half (that the blocking bar stays concrete,
that the ruff exception stays non-imperative) is held by this prose and
by review, not by an exit code.

### Model pinning and what a stale pin looks like

**Single home for the whole model surface (#374 + #392).** Two review
surfaces run on a Claude model: this cloud workflow and the local
plan-stage `architect-reviewer` (`.claude/agents/*.md`). The policy is
one — pin explicitly, never run on an alias — and it lives here. The
*canon* is the files themselves (the workflow's `claude_args`, the
agent's frontmatter); there is deliberately **no registry document
listing which agent runs on which model**, because a copy of the config
is exactly the thing that drifts away from it.

The action runs on whatever model the upstream default points at unless
we say otherwise, so a default change would move the review quality
without a line in any diff. The model is therefore pinned explicitly:

```yaml
claude_args: |
  --model claude-opus-5
```

`claude_args` is the action's documented passthrough to the Claude CLI
(`action.yml`: "Additional arguments to pass to Claude CLI"); the action
has **no `model` input**. This matters more than it looks: GitHub Actions
**silently ignores an unknown `with:` input**, so a misspelled input name
would leave the review unpinned while every static check stayed green.

The pin is **family-level** on purpose — this generation has no dated
snapshot id, so point-release upgrades inside Opus 5 are accepted; a
generation change is not.

**A stale pin is loud, and that is the design.** When the id is retired
or mistyped, the review job reds on every PR with a model-not-found
error. That is the forcing function, not a flake: bump the id in
`claude-review.yml` (the guard test only rejects short aliases like
`opus`/`sonnet`, so any full id passes). There is **no notification when
a newer model ships** — revision happens when the job goes red, not on a
calendar.

#### The local half: `.claude/agents/*.md` (#392)

The agent frontmatter carries the same policy:

```yaml
model: claude-opus-5
effort: high
```

`model: opus` — the previous value — is an **alias**, not an id: per the
[subagent docs](https://code.claude.com/docs/en/sub-agents) the field
takes an alias (`sonnet`/`opus`/`haiku`/`fable`), a full id, or
`inherit` (the default when the field is absent). When Opus 5 shipped on
2026-07-24, the plan-stage reviewer moved to a different model with no
line in any diff.

`effort` defaults to **inheriting the session level** — not to `high`,
as issue #392 originally assumed. Unpinned, the same plan review is
stricter or laxer depending on whose session ran it; pinning makes the
gate's rigor a repo decision.

**Loud-failure parity holds here too, with three documented
exceptions.** A retired or mistyped id surfaces as a visible error
(`There's an issue with the selected model (…)` / `Agent terminated
early due to an API error`) — Claude Code does **not** silently fall
back to the session model. But upstream model resolution is four deep,
and the frontmatter sits only third, so the pin does not defend against:

- **`CLAUDE_CODE_SUBAGENT_MODEL`** (priority 1) — an env var in the
  operator's shell wins over the file;
- **the per-invocation `model` parameter** (priority 2) — and this one
  is reachable **from inside this repo**: `.claude/commands/plan.md`
  spawns `architect-reviewer` via the Agent tool, whose `model`
  argument takes precedence over the frontmatter. Nothing stops a
  `/plan` run from passing one and silently defeating the pin. The same
  applies to `effort`;
- **an organisation's `availableModels` allowlist** — when it excludes
  the pinned value, Claude Code **skips it silently** and runs the
  subagent on the inherited model.

Only the second is in-repo, and even that one is a call-site
convention, not something the static guard can see. They are recorded
so that "pinned" is not read as a stronger guarantee than it is.

**Two guards, one denylist.** `tests/test_claude_review_workflow.py`
checks the workflow, `tests/test_agent_frontmatter.py` checks the agent
frontmatter, and both import the same set from
`tests/_model_pin_policy.py`. The first version kept a copy per file
"because the surfaces have different vocabularies" — wrong twice over:
these are **denylists**, so a union is strictly more conservative (it
can only reject more, never pass more), and the copies had already
drifted — `fable` was listed for the frontmatter and missing for the
CLI flag, so `--model fable` passed the cloud guard even though the
action's docs say the flag takes the same values.

**Verifying a prompt change**: a PR that edits `claude-review.yml`
itself gets a green review job **with no comment** — the action skips
reviewing its own workflow definition for security reasons. So neither
the prompt contract nor the resolved model is observable on that PR;
both are checked on the next unrelated PR (summary grading in the
comment, model line in the Actions transcript via `show_full_output`).

### One-time setup

1. Locally: `claude setup-token` (requires Claude Pro/Max subscription) → copy the token.
2. Repo Settings → Secrets and variables → Actions → New repository secret:
   - Name: `CLAUDE_CODE_OAUTH_TOKEN`
   - Value: the token from step 1.
3. The workflow consumes it via `${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}` passed as the action's `claude_code_oauth_token` input (separate from `anthropic_api_key`; OAuth tokens do not work as API keys).

The workflow also needs `id-token: write` in `permissions:` — `anthropics/claude-code-action@v1` uses OIDC for GitHub App auth, and without that scope every run fails with "Could not fetch an OIDC token".

No separate Anthropic API billing — usage counts against the Pro/Max subscription quota.

## Production workflow (`run-script.yml`)

Schedule: daily cron (UTC) defined in `run-script.yml` + manual `workflow_dispatch`.

Steps, in order:
1. **pytest** — smoke gate (`id: tests`), fails fast; a red gate blocks every prod pipeline below
2. **github_popular_pipeline.py** — GitHub `new_popular`
3. **github_trending_pipeline.py** — GitHub trending (HTML + enrichment)
4. **steam_pipeline.py** — Steam Most Played (Steam Charts API + appdetails)
5. **soldout_pipeline.py** — Soldout events
6. **kinozal_pipeline.py** — Kinozal movies
7. **telegram_summarizer.py** — `if: always()` (runs even if earlier steps fail)

### Pipeline-step isolation (#245)

**Root cause it fixes:** GitHub Actions steps carry an *implicit* `if: success()`. So a
single step's `exit 1` used to cascade into **skipping every later step** — a transient
third-party 403 in `soldout_pipeline` skipped `kinozal_pipeline` and suppressed movie
delivery for that run ([run 28493805028](https://github.com/ekolvah/kinozal_scraper/actions/runs/28493805028)).
Per-source isolation existed *inside* each `run_*_pipeline`, but not *between* the workflow
steps.

**Fix:** each pipeline step (2–6) carries
`if: ${{ !cancelled() && steps.tests.outcome == 'success' }}`:

- `!cancelled()` — the step runs even if an **earlier pipeline** step failed (defeats the
  implicit `if: success()` cascade), so a flaky source can't suppress an unrelated one.
- `steps.tests.outcome == 'success'` — but a **red smoke gate still skips every pipeline**
  (the hard prerequisite is preserved; that's why the gate carries `id: tests`).
- **No `continue-on-error`** — a failed source still exits 1 → job goes red → the existing
  `Send fallback failure alert` (`if: failure()`) fires. Failure stays visible (§IV); it is
  *not* masked into a green job. This is why a `continue-on-error` + aggregate-gate design
  was rejected: it masks `conclusion` and adds a moving part.

`telegram_summarizer` (step 7) is deliberately **not** isolated — it keeps `if: always()`
and no `continue-on-error`, so its own failure hard-fails the job (§IV). The invariant is
guarded statically by `tests/test_workflow_isolation.py::TestPipelineStepIsolation`, which
*derives* the pipeline set from the workflow (any step running `kinozal_scraper.*_pipeline`)
so a newly-added source is automatically held to it — a hand-maintained list would let the
next source slip back into the cascade.

**Readable per-source alert (#310).** A failed scraper step used to reach the operator only as
the generic `Send fallback failure alert` (`⚠️ … run failed: <url>`) — visible but not
*actionable*: which source, which error class lived only in the CI log (precedent: run
29224080924, soldout 403 required a log dig). Each scraper `__main__` now calls
`alerting.report_failures(notifier, results)` before `sys.exit(1)`: it sends a readable
`source_id: <error>` breakdown to Telegram (reusing `PipelineResult.errors`) and, **on
successful delivery only**, writes the job-global marker `.run/technical_alert_sent`. That
marker gates the `Send fallback failure alert` step (`hashFiles(...) == ''`), so a delivered
rich alert suppresses the generic curl one.

The marker is **job-global**: it means *"≥1 rich alert delivered this run"*, not "this step
delivered". So the curl fallback stays the net only for the **first** undelivered alert (or a
crash *before* `report_failures` — import error, etc.). If a **second** step's alert delivery
fails after an earlier one already set the marker, the backstop is the **red run + logs**
(§III), not curl — a consciously accepted gap (no per-step marker infra; see #310 Out of
scope). `telegram_summarizer` keeps its own richer `deliver_results` alert path; `report_failures`
and the marker helpers share one canonical home in `alerting.py`.

## Soldout probe workflow (`soldout-probe.yml`, #396) — ВРЕМЕННЫЙ

Измеритель доступности `soldoutticketbox.com` из датацентра GitHub Actions.
**Не гейт и не источник данных** — только замер, на который опирается решение по обходу
Cloudflare.

**Зачем.** RCA #396 установил, что исход ежедневного прогона решает единственная величина —
пробьёт ли хоть одна из 4 попыток вероятностный `cf-mitigated=challenge`. Историю по ней не
восстановить: диагностика `describe_block` приехала только 25.07 (#358). Без числа выбор между
«разнести попытки во времени» (бесплатно) и «residential-прокси» (платный сервис + секрет) —
покупка вслепую. Вторая причина: до пробника единственный способ дёрнуть fetch из датацентра —
прогнать `run-script.yml`, а он **рассылает в Telegram-канал**, т.е. каждая проверка гипотезы
стоила пользователю сообщений. У пробника нет ни одного `secrets.*`.

**Решающее правило (пред-зарегистрировано в issue до сбора данных).** Тайм-бокс 7 суток,
8 запусков в сутки × 3 попытки = 24 замера. Если ≥1 успех был в **каждые** сутки — достаточно
разнести попытки, прокси не покупаем; если хоть в одни сутки 0 из 24 — нужен другой egress.
Величина наблюдаемая напрямую, без предположений о независимости попыток.

**Коды выхода.** 403 — это *результат измерения*, прогон зелёный: красный workflow каждые
3 часа обесценил бы алертинг остальных. Красным пробник становится только при сбое **самого
инструмента** — пустой `PROBE_URL`, не-HTTP исключение, истёкший срок.

**Снять матрицу:**

```bash
gh run list --workflow=soldout-probe.yml --limit 60 --json databaseId -q '.[].databaseId' \
  | while read -r id; do gh run view "$id" --log | grep PROBE; done
```

В строке: `ts=` (UTC), `kind=html|poster`, `attempt=n/3`, `elapsed=`, `status=` + диагностика
`describe_block`. Меряются **оба** ломающихся пути: RCA дал разные доли (страница 1/4, постеры
0/8), поэтому «постеры вылечатся тем же обходом» — гипотеза, а не факт. Учти, что на блоке в логе
**две** строки: своя `PROBE …` и уже существующая `[http_fetch] …` из `_get` — `grep PROBE`
отбирает только замеры.

**Удаление.** Пробник, его workflow и `tests/test_probe.py` удаляются вместе с решением по #396.
Забыть не даст `_EXPIRES` в `scripts/probe.py`: после этой даты шаг печатает
`probe expired … delete per #396` и краснеет — механизм вместо обещания в доке.

## Environment variables

### Shared across pipelines

| Variable | Type | Used by |
|---|---|---|
| `CREDENTIALS` | secret | github_popular_pipeline, soldout_pipeline, kinozal_pipeline (Google Sheets service account JSON) |
| `SPREADSHEET_URL` | secret | github_popular_pipeline, soldout_pipeline, kinozal_pipeline |
| `TELEGRAM_BOT_TOKEN` | secret | all 4 steps |
| `TELEGRAM_CHAT_ID` | secret | all 4 steps |

### github_popular_pipeline / github_trending_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | secret | GitHub API auth (github_popular_pipeline only) |
| `GH_TOP_LIMIT` | var | max GitHub repos to fetch (github_popular_pipeline only) |
| `GH_TRENDING_LIMIT` | var | max GitHub trending repos to fetch (github_trending_pipeline; default 10) |
| `GOOGLE_API_KEY` | secret | Gemini API for enrichment |
| `LLM_MODEL` | var | preferred Gemini model |

### steam_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `STEAM_TOP_LIMIT` | var | max Steam Most Played entries to fetch |

### soldout_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `SOLDOUT_URL` | var | Soldout events page URL |

### kinozal_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `API_KEY` | secret | Kinozal API key |
| `KINOZAL_URLS` | var | Kinozal page URLs to scrape, формат `label\|url;...`; local fallback — env `KINOZAL_TOP_URL` (plain url). Если не задано ни то ни другое — pipeline логирует ошибку `no URLs configured`. Легаси-имя `URLS` **больше не читается** (clean rename, #263). `sources.json` `url`/`base_url` для скрейпинга **не читается** (только schema-placeholder), см. `kinozal_pipeline.py::_kinozal_urls` |
| `KINOZAL_EXCLUDED_GENRES` | var | **Опционально.** `;`-разделённый denylist жанров (case-insensitive), напр. `Hidden objects`. Новый элемент, чей жанр (с details-страницы) в списке, **не** уведомляется, но сохраняется в Sheets (dedup). Пусто/не задано → фильтр выключен, details-страницы не запрашиваются (0 оверхеда). См. `kinozal_pipeline.py::_split_by_excluded_genre` (#263) |
| `KINOZAL_USERNAME` | secret | **Опционально.** Логин аккаунта на зеркале `kinozal.guru` — включает автоматический fallback на зеркало при сбое `kinozal.tv` (см. блок ниже). Парный к `KINOZAL_PASSWORD`; **partial** (только один из двух) → WARNING + fallback отключён (не fail) |
| `KINOZAL_PASSWORD` | secret | **Опционально.** Пароль аккаунта `kinozal.guru`. Парный к `KINOZAL_USERNAME` |

> **Fallback на зеркало при недоступности `kinozal.tv` (#227):** primary —
> анонимный `kinozal.tv` (`KINOZAL_URLS` остаётся `.tv`, **переключать не нужно**). Если fetch какого-то
> URL падает (напр. 522), пайплайн автоматически повторяет тот же топ на зеркале **`kinozal.guru`**
> через авторизованную сессию. Логин **ленивый** — выполняется максимум раз за прогон и только при
> первом срабатывании fallback, поэтому здоровый `.tv`-прогон не платит за логин и не требует кредов.
>
> ⚠️ **Анонимный свап домена на `.guru` не работает** (проверено 2026-06-30): `kinozal.guru` гейтит
> весь контент за логином — `/top.php`, `/browse.php`, даже `/` → `302 .../login.php?m=5`. Поэтому
> fallback идёт через `kinozal_auth.py` (`POST /takelogin.php`, обычного не-VIP аккаунта достаточно —
> подтверждено живым прогоном).
>
> **Включение fallback:** задай оба секрета `KINOZAL_USERNAME` + `KINOZAL_PASSWORD`. Без них (или при
> partial) fallback отключён, и сбой `.tv` доходит видимой ошибкой `fetch failed ... (mirror
> fallback disabled)` + exit 1 (§IV) — как было до #227. Провал логина / both-failed тоже видимы:
> `mirror login failed` / `primary failed (...); mirror ... also failed (...)`.
> `sources.json` `base_url` остаётся `https://kinozal.tv` (дефолтный origin, когда primary жив) —
> зеркало туда не прописывать.
>
> **Ссылки следуют за фактическим origin (#247):** `Kinozal.fetch_listing` возвращает
> `(html, effective_base_url)` — `kinozal.tv` при успехе primary, `kinozal.guru` при mirror-fallback.
> Пайплайн резолвит относительные `url`/`image_url` листинга против этого базового хоста (per-fetch
> override статичного `base_url`), поэтому mirror-прогон даёт **`.guru`-ссылки** — живые для
> залогиненного получателя, а не мёртвые `.tv`. Это осознанный разворот исходного #227/#241 решения
> «`base_url` всегда `.tv` — canonical origin для ссылок» (основание: получатель залогинен на `.guru`,
> login-wall для него неактуален). Смешанный прогон (часть топов с `.tv`, часть с зеркала) даёт
> корректный хост у каждого item; dedupe стабилен (ключ — чистый title, host в него не входит →
> миграция старых `.tv`-строк в Sheet не нужна).
>
> **Details-fetch genre-фильтра на mirror-прогонах (#317):** т.к. #247 даёт `.guru`-ссылки, на
> mirror-днях `item.url` = `kinozal.guru/details.php?...`. `Kinozal.fetch_details` для mirror-host
> URL идёт через **авторизованную** сессию (как listing), а не анонимным primary: `.guru` гейтит и
> `details.php` за логином (см. ⚠️ выше), поэтому анонимный GET вернул бы `200` login-страницу без
> блока `Жанр:` — ложный успех, который except-triggered failover `fetch_listing` не ловит, и
> genre-фильтр тихо слепнет (`_parse_genre`=="" для всех → fail-open → всё уведомляется). Постеры
> `/i/poster/` зеркало отдаёт анонимно (verified), поэтому `fetch_poster` этот путь не затрагивает.
>
> Сейчас потребитель — production-cron (`run-script.yml` / `kinozal_pipeline.py`). E2E
> `tests/test_e2e_kinozal_titles.py` станет вторым потребителем после #136 (тест безусловно
> skip'нут, пока `kinozal.tv` отдаёт 522).

### telegram_summarizer

| Variable | Type | Purpose |
|---|---|---|
| `CHANNEL_URL` | var | semicolon-separated Telegram channel URLs/IDs |
| `GOOGLE_API_KEY` | secret | Gemini API for summarization |
| `API_HASH` | secret | Telethon app hash — **required, empty value fails fast** |
| `TELEGRAM_API_ID` | secret | Telethon app ID — **required, empty value fails fast** |
| `TELETHON_SESSION` | secret | Telethon session string — **required, empty value fails fast** |
| `LLM_MODEL` | var | preferred Gemini model |

`require_env` (`telegram_summarizer.py`) rejects an empty value, not just a missing
key: GitHub Actions expands an **undefined secret into an empty string**, so
`os.environ["X"]` cannot tell "not configured" from "configured". That gap is what
kept a user-account Telethon session (`anon.session.encrypted`) live in this public
repo — `TELETHON_SESSION` was never set, so the reader silently fell back to the
committed session file (#386).

#### Minting a new `TELETHON_SESSION`

Run locally, once, with the app credentials of the same Telegram account. Telethon
asks for the phone number and the login code; the printed string **is** a
credential — put it straight into the secret, never into a file in the repo:

```python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print(client.session.save())
```

```bash
gh secret set TELETHON_SESSION   # paste the string
```

Revoking the old session (Telegram → Settings → Devices) is what actually
invalidates a leaked one — re-encrypting or rotating a key cannot un-publish a blob.

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks
```
