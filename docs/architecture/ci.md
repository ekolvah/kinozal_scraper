# CI and quality gates

**Question this document answers:** which automated quality gates stand on the change path and
what they operate on. Its axis is "gate", not "GitHub Actions": consequently it also includes the
**local** planning-stage `architect-reviewer` (`.claude/agents/`), which does not run in CI but is a
gate alongside cloud review under [`principles.md` §VII](principles.md#vii-simplicity-first). The
model surface of agent tooling (both halves) is described in §"Model pinning" — its only home.

**What is not here.** How the production run is operated — schedule, environment variables and
secrets, failure isolation, alerting, operator runbooks → [`operations.md`](operations.md) (#418).
**How a gate became what it is** is decision history and lives in the issue/PR, not the document
body ([`project-map.md` §"What documentation describes"](project-map.md#what-documentation-describes-current-state-not-history-or-ideas)).
The operational criterion for a sentence remaining here is: **without it, an agent would either act
incorrectly or redo rejected work**. A rejected tool or rule is a row beside its gate; a whole tool
without its own section is in [§"Consciously not adopted"](#consciously-not-adopted) (#419).

## Local pre-commit

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks   # activates .githooks/pre-push
python scripts/ci_check.py
```

Runs every check in the `CHECKS` registry (`scripts/ci_check.py`), in order:
ruff format → ruff lint → language → detect-secrets → pytest → pip-audit (runtime) →
pip-audit (dev) → requirements consistency → mypy → import contracts. The `language`
check runs `scripts/check_language.py` locally and in the matching `ci.yml` step; it enforces
English-only tracked Markdown prose and Python commentary. Its exit `0` is compliant text, `1`
is a policy violation, and `2` means trustworthy evidence could not be obtained. (Module-docstring presence
is enforced *inside* ruff lint via `D100`/`D104`/`D419`, not a separate step —
see the lint gates below.)

**Runtime — minutes, not seconds; this doc is the canonical number.** The two
`pip-audit` steps dominate (network calls to the advisory DB) and `pytest` is the
other slow one; the rest are seconds. Measured 2026-07-29 on the maintainer's
Windows box: **~8 minutes end-to-end**. The absolute figure drifts with the
dependency set — the shape (minutes, network-bound tail) is the durable part.
Operational consequence for agents (output going quiet after `pytest` is
`pip-audit` working, not a hang) is in `CLAUDE.md` §Environment. If the measurement
ever crosses the Bash tool's 10-minute ceiling, the derived constant
`timeout: 600000` in the implementer adapter stops working and
needs revisiting together with this number.

**`pre-push` runs two gates, not one.** Ahead of `ci_check.py` it runs
`scripts/check_branch_protection.py` — a single `gh` call (seconds, 30 s timeout) that compares
the declared required status checks against GitHub's actual config. It is deliberately first: a
mismatch aborts the push immediately instead of costing the eight minutes below, which also
leaves its message as the last thing on screen rather than scrolled away. Both non-zero codes
stop the push and the hook propagates them unchanged (`1` drift, `2` the tool itself failed), so
the two stay distinguishable. A drift you introduced on purpose (a check removed by hand for a
one-off merge) is declared with `--allow-drift "<reason>"`, never worked around with
`--no-verify`. Details and the reasoning are in
[§Required status checks](#required-status-checks-branch-protection).

Before probing an interpreter or starting either gate, the hook asks
`git rev-parse --local-env-vars` for Git's repository-local environment names
and unsets exactly those names. Git exports values such as `GIT_DIR` while
running a hook; without this boundary, a child launched after changing into an
unrelated temporary directory can still address the source repository,
especially from a linked worktree. Failure or empty output from the discovery
command is an infrastructure failure (exit `2`), not permission to continue
with inherited repository state. `BRANCH_PROTECTION_ALLOW_DRIFT` is not a
Git-local name and continues to reach the protection probe unchanged.

**Single source of truth.** The registry is the *only* place the check set is
defined. `ci.yml` does not re-list checks — each CI step runs
`python scripts/ci_check.py --only <name>`, so local and CI cannot drift. If
`ci_check.py` is green locally, CI runs the identical checks. Adding or removing
a check in the registry without updating `ci.yml` fails
`tests/test_ci_check.py::TestStepParity` (#153).

> **Disambiguation:** this section's title "Local pre-commit" names the
> pre-commit *moment* (the git-hook that runs before a push), **not** the
> [`pre-commit`](https://pre-commit.com) framework — which this repo
> deliberately does **not** use ([§Consciously not adopted](#consciously-not-adopted)).

### Gate CLI exit codes

Developer-flow gates whose caller distinguishes a domain verdict from missing
evidence use one contract: `0` means the gate passed, `1` means it computed an
explicit negative verdict, and `2` means usage was invalid or the gate could
not compute (tool invocation, output capture, or payload decoding failed).
`validate_issue_sections.py`, for example, reserves `1` for a successfully read
issue whose required sections are missing; an unreadable issue is `2`, so the
implementer is not sent back to rewrite a valid plan (#413).

`ci_check.py` remains deliberately narrower at the child-tool boundary: any
non-zero result from ruff, pytest, pip-audit, mypy, or import-linter means the
quality gate did not pass and `_run()` maps it to `1`. Its own file-discovery
precondition is distinguishable, however: failed `git ls-files` or broken
capture means the input set is unknown and exits `2`; a successfully captured
but empty set remains the explicit negative verdict `1`.

### Secret scan (`secrets`)

`detect_secrets.pre_commit_hook` over every tracked file, run as a registry check —
the local barrier between "an agent or contributor pasted a key into a source file"
and `origin/main`. It sits **right after `lint`**, before the slow gates: a leaked key
must redden the run in seconds, not after the minutes-long pytest + pip-audit tail. Cost is
~5 s for ~130 files (#389).

**`-X utf8` is load-bearing, not decoration.** `detect_secrets/core/scan.py:261` opens
each file with the *platform default* encoding and silently swallows the resulting
`UnicodeDecodeError`. On Windows (cp1252) that skips every file carrying a Russian
comment — most of this repo — so the gate runs, prints nothing and exits 0, while the
same commit is scanned in full on Linux CI: "green locally" would carry no information
(§IV silent skip + the #153 local↔CI drift class).
`tests/test_secrets_gate.py::TestGateFires::test_planted_secret_in_a_non_ascii_file_exits_nonzero`
pins it.

**Two §IV invariants in `ci_check`, and neither is decoration:** `_tracked_files()` exits 2
when `git ls-files` fails or its capture breaks, while `check_secrets()` exits 1 on a
successfully captured but **empty file set**. The hook itself returns 0 when handed no files —
so a broken `git` invocation would otherwise reproduce this gate's own historical defect
(configured, green, scanning nothing) one layer deeper. Do not "simplify" either exit away.
`tests/test_ci_check.py::TestTrackedFilesCaptureFailure` pins the infrastructure code;
`tests/test_secrets_gate.py` covers the empty set, a planted key (non-zero), and a clean file
(zero).

**No `--baseline`, by design.** With it, `detect_secrets/pre_commit_hook.py` can return `3`
after *rewriting* the baseline file in place (line-number drift) — a red push that already
mutated a tracked file behind your back, the mutation-during-a-gate pattern this repo rejects
— and the next run then fails differently with "baseline is unstaged". A baseline is also a
one-command "make the gate green" button for a genuinely leaked key, and its paths carry the
host OS's separators, so a Windows-generated baseline reddens Linux CI. Without it the hook
only ever returns 0 or 1 and mutates nothing.

The two **captured HTML fixtures** (`tests/fixtures/cloudflare_block_403.html`,
`tests/fixtures/github_trending/trending_daily.html`) are excluded by
`ci_check._secrets_targets` — asset digests in third-party markup are high-entropy
false positives by construction. The exclusion is a tested pure function over
`git ls-files` output (always POSIX separators), **not** a tool-side regex whose
semantics shift with the OS path separator. For a false positive **inside** our own
code the escape hatch is an inline `# pragma: allowlist secret` at the site (see
`tests/test_secrets_gate.py`), never a blanket exclusion.

### Session hooks (`scripts/hooks.py` and `.codex/hooks.json`)

A separate, *earlier* feedback layer that runs **during** an agent session, not
at push (#281). The Codex adapter declares a `PostToolUse` hook in
`.codex/hooks.json` (matcher `Edit|Write`) invoking `scripts/codex_hooks.py on-edit`.
It delegates to `scripts/hooks.py`, which dispatches two cheap checks in one process
right after each file edit:

- `*.py` → ruff **check-only** (`ruff format --check` + `ruff check`, **no
  `--fix`/format mutation** — the harness tracks file contents, so rewriting
  behind its back breaks the next Edit's `old_string` match). Remaining lint →
  stderr + exit 2 (PostToolUse exit 2 feeds stderr back to the agent).
- `requirements*.in` → a `pip-compile` reminder (the agent process is otherwise only
  prose — this makes forgetting it a *visible* marker, not a CI-time surprise).

§IV split: a malformed/empty payload is a silent no-op, but a ruff *exec*
failure (not installed / bad config) is a **visible, distinct** marker — a
silently-broken hook must not masquerade as "lint clean". Decision logic is pure
functions (`plan_checks`/`classify_ruff_result`) with unit tests
(`tests/test_hooks.py`); wiring is anti-drift-guarded by
`tests/test_settings_hooks.py` (mirrors `test_settings_deny.py`).

Claude adds a second event on the same entry point: a `PreToolUse` hook (matcher `Bash`)
invoking `python -m scripts.hooks pre-bash`, which asks `scripts/navigation_policy.py`
whether a stage reads the filesystem and, if so, denies it **with the replacement call named**
(#485). This is the token-economy carrier, deliberately distinct from the security carrier
`scripts/agent_policy.py`, and the two differ in failure mode: security denies on a malformed
payload, navigation fails **open**, because a policy that only claims "a cheaper route exists"
must never brick `Bash`. It is also why the navigation entries are *not* in `permissions.deny`
— a matching deny rule blocks before the hook runs and would swallow the message
(`tests/test_navigation_policy.py` guards that).

This is instant feedback that **complements, never replaces** `ci_check.py` (the
canonical pre-push gate), and is unrelated to the `pre-commit`/`tox` *framework*
([consciously declined](#consciously-not-adopted)) — that no-go is about a PR-time
tool-registry framework, this is a session-time editor hook.

## CI workflow (`ci.yml`)

Triggers: `pull_request` (covers every PR branch) + `push` to `main` only
(post-merge gate — catches a semantic conflict between two PRs each green
in isolation). `issue-*` is deliberately **not** a push trigger: a PR branch
push would otherwise fire the `quality` job twice (once per event) for the
same commit. `quality` is one of the branch's required status checks — a bare,
event-agnostic context, so the `pull_request` run satisfies branch protection on
its own and dropping `issue-*` orphans nothing (#206). Do not re-add `issue-*` to
`push` to "get CI on a branch" — the `.githooks/pre-push` hook already runs the
identical `ci_check.py` locally before every push.

Steps: checkout → Python 3.12 → install deps → then one
`python scripts/ci_check.py --only <name>` step per registry check (format,
lint, secrets, pytest, pip-audit, pip-audit-dev, requirements, mypy, imports).
The per-step split keeps the GitHub Actions UI granular (you see *which* gate
failed) while the check set itself stays defined once, in `ci_check.py`.

mypy gets a NUL-safe manifest from
`git ls-files -z --cached --others --exclude-standard`: existing tracked Python
files plus new, untracked Python files that standard Git ignore rules do not
exclude. Index entries deleted from the working tree before staging are
discarded instead of being passed to mypy as nonexistent paths.
That keeps a new source module in local scope before `git add`, while ignored
planning probes under `evidence/` stay out; the tracked `.gitignore` rule for
`evidence/` is therefore part of this boundary. A clean GitHub checkout has no
untracked candidates, so the same command naturally reduces to its tracked
Python files there. `_find_modules()` then applies the explicit
`_EXCLUDE_DIRS` (`.venv`, `.git`, `__pycache__`, `.audit-tmp`, `.claude`) and
`pytest-cache-files-*` filters over Git's platform-stable POSIX paths. The
secret gate deliberately remains narrower and calls `_tracked_files()`, so it
continues to scan tracked files only.

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
  credentials — auth lives in the caller" as a machine rule.
- **`pipeline-layers`** (`layers`) — pins dependency *direction*: orchestrators
  (`*_pipeline`) may import the adapters and the shared `generic_pipeline` core,
  never the reverse, and no orchestrator/adapter imports a sibling.

`check_imports()` calls import-linter's **Python API** (`importlinter.api`), not
the `lint-imports` console script — the console entry point is unreliable-on-PATH
on Windows and would reintroduce the `subprocess stdout=None` pitfall (#109). grimp
builds the graph statically (AST), so the `__main__` wiring blocks never execute.
`tests/test_import_contracts.py` is an anti-drift guard: it asserts the
contracts' *load-bearing fields* (which modules are forbidden/layered), so an
agent can't quietly gut a contract while keeping its name. `principles.md`
is deliberately **not** edited: §II is tool-agnostic canon, so the tool mention
lives here and in `runtime.md`, not in the constitution.

**Consciously not adopted here:** the contract "orchestrators import only Protocol modules" —
is currently **inexpressible**: `Protocol` classes reside in the same module as their concrete
implementations. Revisit with the Protocol-extraction refactor (#234 Out of scope).

### Lint gates and ratchets (ruff)

All four rule sets run in existing `check_lint` — without a new dependency or a separate registry
step. Ruff has **no native baseline**, so ratchets provide **forward** value: new or changed code
over the threshold fails CI while legacy is grandfathered.

| Rules (issue) | Type | What it catches | Threshold / known gap |
|---|---|---|---|
| `C901`, `PLR0912`, `PLR0915` (#233) | ratchet | method growth (cyclomatic complexity / branches / statements) | `max-complexity = 12` — **aligned with PLR0912's default branch threshold**, not tuned to current code (protection against Goodhart/bikeshedding); PLR0912/PLR0915 stay at Ruff defaults (12 / 50). **Gap:** blanket `# noqa` lets a grandfathered function grow further unnoticed — the ratchet protects new code and new functions, not the frozen six. The real fix is splitting (#251, §V documented-mitigation) |
| `ERA001` (#235) | ratchet | commented-out code | The repository is intentionally **clean**. `tests/**` are **not** excluded: dead code is dead regardless of file role |
| `ARG001`, `ARG002`, `SLF001` (#236) | ratchet | unused function/method argument; access to another object's private member | 110 existing hits were triaged individually; real dead parameters in `src/`: zero. `SLF001` in `src/` is **zero**: two hits (`RotatingGeminiEnricher` accessed `GeminiEnricher._model_name` from two places) were one real §II leak, removed with public property `model_name`, not noqa. `ARG003`/`004`/`005` (classmethod/staticmethod/lambda) were **not selected** — conscious defer (#236 Out of scope) |
| `D100`, `D104`, `D419` (#253) | **presence gate** (no threshold) | missing / empty module and package docstring | The repository is clean; scope is repository-wide: `src/`, `scripts/`/root **and `tests/`** (#433). `D101`/`D103` (class/function) were consciously **not** selected — the gate is module-level only. **Gap:** `D100`/`D104` flag only *public* modules, so both a future `src/kinozal_scraper/_internal.py` (none exists today) and three live `tests/_*.py` helpers can pass; they have docstrings, but the gate does not hold them. A second gap shared by all lint gates: `extend-exclude` in `[tool.ruff]` removes a whole tree from `ruff check`, and config-pinning guards do not see that |

#### Stable first-party classification

`[tool.ruff.lint.isort] known-first-party` explicitly names `kinozal_scraper` and `scripts`.
With both top-level package directories already present, Ruff 0.15.12 still classifies an import
whose leaf module does not exist as third-party in this layout; creating that leaf reclassifies the
unchanged import as first-party. A warm local cache can retain the first verdict while cold CI
computes the second. Explicit namespaces make the correct import group deterministic before
implementation exists (#440).

`--no-cache` is deliberately not added to `check_lint`: it recomputes the state-dependent verdict
instead of removing that dependency. Five interleaved local runs on 2026-08-09 measured median
`ruff check .` times of 94 ms with the warm cache and 112 ms with `--no-cache`; the 18 ms difference
is small, but the configuration fix removes the cause with no extra invocation or intentional
per-run cost.

**The silencing convention is one for all four, and guards pin it:**

- **A real false positive is silenced per site** with `# noqa: <exact codes>` and a reason — never
  per file: a per-file ignore blinds the whole file to *new* hits. Live per-site examples are six
  grandfathered functions on their `def` line (#233) and two Protocol-conformance stubs
  (`NullEnricher.enrich`'s `item`, `InMemoryStorage.append_rows`'s `headers`), whose parameter is
  required by the interface but unused by that implementation.
- **`# noqa` is an escape hatch for an FP, not a real detector hit.** The only `ERA001` hit (the
  illustrative diagram comment `# [dedupe_key, title, ...]`, which Ruff parses as a list) was fixed
  **by rewriting it as prose**: the code was never dead, and silencing would train the hatch on a
  non-exception (§IV).
- **`tests/**` are categorically excluded only where file role changes a rule's meaning.** For
  ARG/SLF, yes (`per-file-ignores` `"tests/**"`): §II white-box tests legitimately call private
  helpers directly, and mock signatures are dictated by the mocked callable, not usage. For
  `ERA001`, **no**: dead code is dead regardless of file role. For `D100`/`D104`/`D419`, **no
  longer** (#433): the exclusion had historical basis ("old `check_headers.py` scanned only
  `src/`"), not file role, and was revoked — a module docstring in `tests/` is the only navigation
  across 25 thousand lines, and 41 of 60 files wrote one voluntarily before the gate. Thus
  `per-file-ignores` for `tests/**` retain three codes from one set on one rationale; do **not**
  cargo-cult the superficial pattern "tests always receive a per-file ignore" from this.
- **Every rule has an anti-drift guard** (`tests/test_complexity_ratchet.py`,
  `test_ruff_dead_code_rule.py`, `test_ruff_arg_slf_rules.py`, `test_ruff_docstring_rule.py` — all
  in the style of `test_ruff_silence_rules.py`), but **their depth differs and is not equalized**:
  - all four pin the *effective select*;
  - three of four cover the “code is not in global `ignore`” branch and the `per-file-ignores`
    branch — **the ratchet guard (`C901`/`PLR0912`/`PLR0915`) has neither**, so today this trio
    can be silently put in `ignore` with every guard still green. The gap is named, not closed: it
    predates extension of the docstring gate to `tests/` (#433) and has no issue yet.
  - the `per-file-ignores` branch has **two forms, and the form follows the presence of a legitimate
    exception**: `ARG`/`SLF` are checked by path coverage (`fnmatch` against `src`/`scripts`
    sentinel paths) because `tests/**` is legitimately suppressed for them; `ERA001` and the
    docstring codes have no legitimate exception, so their check is stricter — the code must not
    occur in `per-file-ignores` **under any pattern** (otherwise a narrow return such as
    `"tests/test_x.py" = ["D100"]` would pass the sentinel probe). The docstring guard additionally
    reads the `extend-` twins of both tables (`extend-ignore`, `extend-per-file-ignores`), which
    ruff honours equally; the other three lack this branch.

**The §IV no-op guard from the old docstring script was not carried over — deliberately, not lost.**
“Fail if the scan found no files” was an artefact of parameterized `root.rglob(Path("src"))` in
bespoke `scripts/check_headers.py`; `ruff check .` recurses through the full tree from cwd and
cannot miss the package this way. The residual “package disappeared/is empty” case is caught
**strictly more strongly** by `test_package_importable.py` (17 hardcoded `import_module` calls),
`test_repo_layout.py`, mypy, and import-linter.

**Deliberately not included here:**

- **`RUF100`** (self-cleaning noqa) is deferred: 18 existing unused-noqa occurrences in the
  repository make this separate cleanup, not this gate (#233 Out of scope).
- **`vulture`** (cross-module unused) is not included: the repository deliberately has **zero**
  cross-module dead code, while ruff `F` (F401/F841) already catches locally unused code. In the
  dynamic form of this code (pipeline registry, declarative configuration, `Protocol`
  implementations, pytest fixtures, `__main__` entry points), vulture is false-positive prone:
  dependency + gate + whitelist with per-CI triage for a hypothetical. **Revisit (wait-for-pain):**
  real cross-module dead code that `ERA001` does not catch appears (#235 Out of scope).

### Subprocess output guard (`tests/test_subprocess_encoding.py`)

An AST guard over `scripts/**`, `src/**` and `tests/**`, enforcing **two**
rules that are two ends of the same defect (#364, #410):

1. **`encoding` is mandatory** on a call that captures output in text mode.
   Without it Windows decodes with the OS code page, the reader thread dies on
   the first Cyrillic byte, and the captured text is lost — which is also where
   the `stdout=None` gotcha comes from (#109: an empty buffer becomes `None`).
2. **No default on captured output** — `result.stdout or ""` and friends are
   banned. Once rule 1 closed the cause, `None` means *the capture itself
   failed*, so a default silently replaces a real failure with emptiness —
   inside scripts that are themselves gates (`check_red` would accept an empty
   junit report, `validate_issue_sections` an empty issue body, the secret scan
   an empty file list).

**Convention for locating the None check** (the guard requires it, so it belongs here rather than
in a style guide): the `None` check is where captured output is **read** — in the file's `_run`
seam if it has one; at the call site if the script makes a single call; inline for calls that
**deliberately** bypass the seam (`new_branch`'s `git branch -d`, which is allowed to fail and
therefore cannot use the `check=True` seam). Only the *invariant* is centralized — here, in the
guard: a shared helper module is impossible because repository root is never on `sys.path` under
`python scripts/foo.py` (see the [ledger](coverage-gaps.md)).

**Neither ruff, bandit, nor pylint covers this** — none has a standard rule for `subprocess`
encoding (ruff's `PLW1514` concerns `open()`). This records that the “standard tools > bicycles”
precedent (#237) must not be reopened against this guard. The second boundary is that the guard
checks only the **parent** side: child Python still writes in the OS code page without
`PYTHONUTF8=1` / `-X utf8`. Both boundaries are in the
[accepted-gaps ledger](coverage-gaps.md); the rule itself is canon in `CLAUDE.md` §Environment.

`scripts/hooks.py` additionally passes `errors="replace"` — per-call-site decision for a tool
whose entire job is visibility; the guard does not require `errors` anywhere.

### Doc guards

Static guards over `.md` (plus one repository-wide branch, see below), all of the genre above—
static checks under `check_pytest`, without an entry in the `CHECKS` registry. This section's heading deliberately does not list files: its anchor
is generated from its text, and tying an address to a volatile list is the same defect as a
task number in a heading.

- **headers**—every mapped `.md` carries the line “what question this file answers”
  (the convention is `project-map.md`, #421).
- **links**—every internal link and every code span in the form `` `file.md#anchor` `` resolves:
  the target exists in the index, and the anchor matches the heading slug under github-slugger rules (#427).
  The scope and existence check use the **git index** (`git ls-files -z`, with suffix filtering already in
  Python), while path joining is **lexical** (`posixpath.normpath`), with no filesystem access:
  otherwise gitignored repository copies in `.claude/worktrees/` would fail locally, and `Path.exists()` /
  `Path.resolve()` are case-insensitive on Windows and would allow `` `Pipeline.md#…` `` locally only to
  fail CI on Linux. Parsing uses `markdown-it-py`: a link inside a ``` block does not count as a
  link, and heading text must be rendered.
- **reference form**—`#N` is a parenthetical pointer, not a sentence member; in a section
  heading it is forbidden even in parentheses, because the anchor is generated from heading text (#428). The `#`
  sigil is reserved for issue/PRs: the rule is `agent-process.md`, the board is `Project 1`; the convention
  replaces an open dictionary of exceptions in the predicate. This branch covers **all**
  tracked files, not only `.md`: its dictionary is closed by **token** (`workflow`,
  `Project`) in ordinary Markdown wrapping, and `.py` and `.toml` drift the same way—prose
  could not hold it. It is a line regexp, so a code span does not suppress it: the rule's illustration
  is written through a metavariable. The allowed zone is only a **closed**
  `()` pair; otherwise one unclosed parenthesis silently turns the paragraph tail into an allowlist. Backtick
  pairing and link boundaries are delegated to the parser, and `table` is enabled so a parenthesis from one cell
  does not close with a parenthesis from another. MADR records (`docs/adr/`) are outside the scope by genre: a record is
  the home of rationale, dated by design, and immutable after acceptance.

Each is presence / resolvability / form, **not** correctness: a pointer to a file that exists but
has ceased to be the topic's home, like a chronicle carefully put in parentheses, is caught by a person
in review. The boundaries of every guard are named in its docstring.

## Required status checks (branch protection)

Three contexts block a merge into `main`: **`quality`** (`ci.yml`), **`pr-link`**, and
**`agent-review`** (`agent-review.yml`).
(`pr-link.yml` → `scripts/verify_pr_link.py`, a PR from an `issue-N` branch must close its
issue). The **machine-checked canon** of that set is `REQUIRED_CONTEXTS` in
`scripts/check_branch_protection.py` — this paragraph is prose that can rot, that constant is
compared against GitHub and against the workflow files.

The ordinary `agent-review` job is required because its deterministic final step reads the action's
schema-validated outcome directly: `clean` succeeds, `rework` succeeds **with a visible
`::warning::`**, `blocking` fails, and absent or malformed output is a readable
`review unavailable` failure.

**One context, two carriers (#478).** Required contexts are AND-ed, so a second required
context would make availability *worse* — both providers would need quota. The carriers
therefore sit inside this one job as an ordered failover: `Claude review` runs with
`continue-on-error`, `Classify review outcome` asks
`check_agent_review_outcome.py --classify` whether that produced a usable verdict, and
`Codex review` runs only when the answer is `false`. A `blocking` verdict is a result, so
it is never failed over and never overruled. Exactly one of the two enforcement steps
runs, each naming its producer, so a head never collects two verdicts.

Carrier 2 is **Codex code review through its GitHub integration**, not an action in this
runner: `openai/codex-action` authenticates by API key only, and a carrier switched on by
buying a key does not solve an availability problem. `scripts/request_codex_review.py` is
the whole adapter — it reads the existing reviews, posts `@codex review` once if none of
them answers for this head, then waits with a declared bound. Only a review by
`chatgpt-codex-connector[bot]` **on the current head SHA** counts, and its state is the
verdict: changes requested → `blocking`, a plain comment → `rework`, approved → `clean`.
That mapping is instructed, not guessed: `AGENTS.md` § Code Review Rules — the file Codex
reads for repository rules, and the second home of the review contract — tells the reviewer
to request changes only for a blocking finding. No answer within the bound leaves an empty
payload, and the enforcement step reds the check exactly as before. Rationale and rejected
options: [ADR 0003](../adr/0003-second-carrier-for-the-required-review-gate.md).

**Merge authority is narrower than report coverage (#458).** The prompt requires every finding to
be reported at every severity, so a should-fix finding is the normal outcome of a thorough review.
Reding the required check on it made a green result unreachable by construction: one delivery PR
went through ten review rounds, the last four of them cosmetic, two of those fixing wording
introduced by the previous round (#458). So only `blocking` blocks: bugs, security, a violated task contract, a
missing test for changed behaviour. `should-fix` findings stay visible in the PR and are the
maintainer's decision, not a condition for `clean`. What is *not* evidence — empty, malformed or
unknown outcome, unavailable live PR context — stays red: absence of evidence must never read as
success (§IV). A Claude comment is feedback for people,
not merge authority, so ordinary PRs neither poll GitHub comments nor start a second Claude invocation.
Transport or quota failure is therefore red and is re-run after the provider recovers; it is never
silently treated as `clean`.

Because that conclusion already separates blocking from non-blocking
deterministically, the agent-side loop reads it rather than the review body:
`python -m scripts.review_gate <PR>` turns the check's state on the current head
into an exit code, so «only `blocking` blocks» stops being a sentence an agent
can skip. Its verdicts are documented in
[agent-process.md](agent-process.md#review-gate-verdicts); the gate is read-only
and is not a CI job.

An ordinary fork PR has no Claude OAuth secret and remains red for its missing
outcome; a maintainer moves it onto a repository branch to run the required
review. Separately, no required context is trusted evidence on any fork: all
three execute PR-head code (`ci.yml`, `scripts/verify_pr_link.py`, and
`scripts/check_agent_review_outcome.py`), so a fork can make its own check
green. A controller-verifier fork therefore uses the accepted
single-maintainer fallback: the maintainer's IDE-agent review and merge
decision.

A PR changing the review controller itself is reviewed like any other (#483).
The `Claude review` step passes `github_token: ${{ github.token }}`, which the
action returns instead of exchanging OIDC for a GitHub App token — and the
App-token path is what refused to run whenever the head's workflow file differs
from `main`. Before that input, such a PR ended in `WorkflowValidationSkipError`:
a green `agent-review` with no model invocation at all, which is why an empty
outcome used to be excused there. The exception is gone with its cause; empty is
an unavailable review on every path. The trust model and what it costs are in
[ADR-0004](../adr/0004-controller-pr-review-runs-on-the-workflow-token.md).

**A required context blocks the merge when it does not report at all, not only when it is red.**
That happens when the head SHA never ran the job: a first-time contributor's fork PR awaiting
maintainer approval, disabled Actions, or a renamed workflow on the PR branch. `enforce_admins:
true` leaves no override. The cheap recovery is that `pr-link.yml` also triggers on `edited`, so
editing the PR title/description re-runs it; pushing a commit works too. The same lockout risk
that disqualifies `review` applies to `pr-link` and is **accepted** here: its trigger set covers
every PR event and it runs on `github.token` alone, so it has no secret to lose. Three ways to
manufacture that trap are guarded, because each one leaves a declared context permanently
"Expected" and locks out even the PR that would undo it: renaming the job (a required context is
the check-run name — a job's `name:`, else its key), putting a `strategy.matrix` on it (real
contexts become `job (value)`), and adding a `paths`/`paths-ignore`/`branches`/`branches-ignore`
filter to the workflow's `pull_request` trigger (the job then simply does not run on some PRs —
a docs-only PR against a `paths:`-filtered `ci.yml` is the realistic case).

With `strict: true` the "Update branch" button creates a new head SHA, so all required contexts re-run —
an expected extra minute, not a malfunction.

**Drift detection.** `python scripts/check_branch_protection.py` prints the actual contexts and
exits `1` on drift, `2` when the tool itself fails (no `gh`, no admin rights, unparseable
response) — a tool failure must not read as "no drift". `.githooks/pre-push` runs it before
`ci_check.py`, so drift costs seconds rather than a full gate run, and both non-zero codes stop
the push. Two consequences are deliberate and worth knowing: the hook is **local enforcement**
— server-side it decides nothing (`.githooks` is opt-in via `git config core.hooksPath`, and the
authoritative barrier stays branch protection itself), but wired through `|| exit $?` it blocks
the push, and that is intended: a detector that only printed would scroll past while the drift
survived. And the probe assumes the pusher holds admin rights on
the repository — true while this is a single-maintainer repo, and the first thing to revisit if
that changes. Why this is not a CI job — GitHub's `GITHUB_TOKEN` has no `administration` scope,
so a CI form needs a stored admin-scoped token whose rotation cost buys nothing here; the full
reasoning lives in the script's docstring.

**Declaring an intentional drift.** `--allow-drift "<reason>"` exits `0` and prints the reason
into the push output. It exists because the alternative was `--no-verify`, which also swallows
`ci_check` — a gate that regularly demands bypassing teaches bypassing, and the next bypass eats
a genuine red (#458). Scoping the check to pushes to `main` was considered and rejected: pushing
to `main` is forbidden by process, so that trigger would mean never checking at all.

## Agent review workflow (`agent-review.yml`)

Triggers: every `pull_request: opened/synchronize`. Uses
`anthropics/claude-code-action@v1` to run an automated code review on every PR push: inline
comments at relevant lines plus a top-level verdict. Does **not** approve or merge — the human
reviewer keeps that.

Visibility is guaranteed by two independent layers:

- `track_progress: true` — the action itself posts a tracking comment at start and updates it
  as the run proceeds. Whatever Claude does or doesn't do, this is at least one visible signal
  that the review ran.
- The prompt instructs Claude to (a) post per-issue inline comments via
  `mcp__github_inline_comment__create_inline_comment` and (b) finish with a top-level summary
  via `update_claude_comment`. Controlling comment *format* is not enough: a run that finds
  no issues and invokes no publishing tool leaves the PR silent.

The job first checks out the default-branch verifier source, so the deterministic step remains
importable if the live PR API read fails. The primary invocation returns a schema-validated `clean`,
`rework`, or `blocking` outcome. The following shell step maps it directly to the job result; no
marker, polling, or repair invocation is in the ordinary path. Before that invocation, the workflow
obtains the current PR number, body and head SHA through the GitHub API. A re-run keeps its original
event payload, so this explicit read is
what keeps a re-run from reviewing an old PR description or SHA. The body is passed only as fenced,
untrusted data in an action input — never interpolated into a shell command — and the requested
summary names the live head SHA. If that API read fails, the deterministic step reports `live PR
context is unavailable` and stays red; it does not spend quota on a second model call. A controller
change is now its own compatibility check, because the review runs on the PR head (#483): a red
`Claude review` step reporting schema validation means the reviewer is unavailable, so fix or
revert the controller change on that branch rather than weakening the gate.

**Deliberately temporary:** `show_full_output: true` (the full SDK transcript in Actions logs) is
enabled while review behaviour stabilizes; it is noisy and can expose internal model chatter.
**Removal trigger:** the review loop no longer requires transcript analysis — that is, when the
transcript was last needed for diagnosis, not “someday”.

### Coverage-first prompt: no filtering at the search stage

**The defect mechanism is why the contract exists, not archaeology.** The model follows a filter
instruction (`Skip nitpicks — ruff handles formatting/lint`) **literally**: it makes a finding,
rates it below the stated threshold, and silently does not report it — while a filtered finding is
indistinguishable from its absence (§IV). The same defect has a second form at **output**: the
instruction “post exactly "✅ Review complete — no blocking issues found."” prohibited adding
anything else, so a run with three should-fix and zero blocking had to print one line. Removing the
input filter while retaining the output filter fixes half the problem. The same mechanism defines
`.claude/agents/architect-reviewer.md`.

The contract is **grade, never drop**:

- every finding is reported with `severity` (blocking / should-fix / nice-to-have) and `confidence`
  (high / medium / low) — a human filters, not the model;
- `blocking` is a concrete bar (wrong behaviour, a failing or missing test for changed behaviour, a
  misleading result, a leaked secret, or a `CLAUDE.md` convention violation), not the qualitative
  word “nitpick”;
- anything already caught by a deterministic gate (ruff / mypy in `ci_check.py`) is graded
  `nice-to-have, duplicate of ci_check` — ranked last because another executor covers it, not
  withheld;
- **only blocking / should-fix go inline** so the inline channel does not drown; the rest go in the
  summary, which lists every finding by severity. A fixed one-line summary applies only to “nothing
  found at any severity”.

`tests/test_agent_review_workflow.py` guards **form**: no suppression imperative at the start of a
prompt line, `severity` + `confidence` present, and no `no blocking issues` gag line. It does not
check semantics — a filter reworded as “be selective” passes; the qualitative half (that the
blocking bar remains concrete and the ruff exception non-imperative) is upheld by this prose and
review, not an exit code.

### Model pinning and what a stale pin looks like

**Single home for the whole model surface (#374 + #392).** Two review surfaces run on a Claude
model: this cloud workflow and the local plan-stage `architect-reviewer` (`.claude/agents/*.md`).
The policy is one — pin explicitly, never run on an alias — and it lives here. The *canon* is the
files themselves (the workflow's `claude_args`, the agent's frontmatter); there is deliberately
**no registry document listing which agent runs on which model**, because a copy of the config is
exactly the thing that drifts away from it.

`agent-review.yml` contains `claude_args: |` / `--model claude-opus-5`; the agent frontmatter has
`model: claude-opus-5` + `effort: high`.

Four facts without which the pin is fixed incorrectly:

1. **The action has no `model` input.** `claude_args` is the documented Claude CLI passthrough
   (`action.yml`: "Additional arguments to pass to Claude CLI"), and this matters more than it
   appears: GitHub Actions **silently ignores an unknown `with:`**, so a typo in the input name
   would leave review unpinned with every static check green.
2. **`effort` defaults to inheriting the session level** — not `high`. Without a pin, the same
   plan-stage review is stricter or looser depending on whose session starts it; the pin makes gate
   strictness a repository decision.
3. **A PR changing `agent-review.yml` itself checks outcome only when it has one.** An empty
   outcome produces a visible warning; `clean` and `rework` pass, while `blocking` turns red.
   Prompt contract and permitted model are checked on the next unrelated PR.
4. **The guard rejects only short aliases** (`opus`/`sonnet`/`haiku`/`fable`) — any full ID passes.
   There is no “a new model was released” notification: revision happens **because of a red job**,
   not by calendar. The pin is deliberately **family-level**: this generation has no dated snapshot
   ID, so a point release within Opus 5 is accepted, while a generation change is not.

**A stale pin is loud, by design.** A removed or mistyped ID is a visible error on every PR
(`There's an issue with the selected model (…)` / `Agent terminated early due to an API error`);
Claude Code does **not** silently fall back to the session model. But model resolution is higher in
the stack, and frontmatter is not first there, so the pin does **not** protect against three things:
`CLAUDE_CODE_SUBAGENT_MODEL` in the operator's shell; the Agent tool's per-invocation `model`
argument — **the only one of the three reachable from inside the repository**,
`.claude/commands/plan.md` starts `architect-reviewer` exactly this way, and nothing prevents
passing `model`/`effort` and silently defeating the pin; the organisational `availableModels`
allowlist — when it excludes the pinned value, Claude Code **silently** skips it and takes the
inherited model. This is recorded so “pinned” is not read as a stronger guarantee than it is.

**Two guards, one denylist.** `tests/test_agent_review_workflow.py` checks the workflow,
`tests/test_agent_frontmatter.py` checks agent frontmatter; both import the shared set from
`tests/_model_pin_policy.py`. These are **denylists**, so their union is strictly more conservative:
it can only reject too much, not let something through.

### One-time setup

1. Locally: `claude setup-token` (requires Claude Pro/Max subscription) → copy the token.
2. Repo Settings → Secrets and variables → Actions → New repository secret:
   - Name: `CLAUDE_CODE_OAUTH_TOKEN`
   - Value: the token from step 1.
3. The workflow consumes it via `${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}` passed as the action's `claude_code_oauth_token` input (separate from `anthropic_api_key`; OAuth tokens do not work as API keys).

The workflow also needs `id-token: write` in `permissions:` — `anthropics/claude-code-action@v1` uses OIDC for GitHub App auth, and without that scope every run fails with "Could not fetch an OIDC token".

No separate Anthropic API billing — usage counts against the Pro/Max subscription quota.

## Production workflow (`run-script.yml`)

The production cron is counted as an **E2E smoke gate** in [`principles.md`](principles.md) §Quality Gates—this is
the only facet of the production workflow that answers this file's question. Scheduling, step order,
the workflow's own `pytest` smoke gate, failure isolation, and alerting belong to one home,
[`operations.md` § Production workflow](operations.md#production-workflow-run-scriptyml).

## Consciously not adopted

**What belongs here:** “tool or rule Y was not adopted”—and only a whole tool
without its own gate section above (otherwise, a line at the gate's location). The other branches of the
“where the decision goes” route are in [`project-map.md`](project-map.md) §Canonical-home, its canon.

- **`pre-commit` (#255)—no-go.** **Root reason:** every hook pins a tool version through `rev:` and
  runs it in an **isolated venv**—a second source of the tool version besides
  `requirements-dev.txt` (today `python -m ruff`/`mypy` use the single locked version),
  meaning a systematic return of the same local↔CI drift class (#153). A sharp illustration is
  `mypy`: its isolated
  venv cannot see project dependencies, forcing `additional_dependencies:`—
  a manually copied duplicate of the dependency set outside `requirements.txt`. **The partial-migration
  trap:** file linters in `pre-commit`, other gates as scripts ⇒ two overlapping
  systems and **three-way** parity (`pre-commit` config ↔ `CHECKS` ↔ `ci.yml`), whose third
  edge is **unguarded**—more surface area instead of benefit. Half the checks are not
  file linters at all (`requirements`, `imports` have their own logic); under `pre-commit`, they would remain
  scripts in `local` hooks with zero benefit. **Revisit (wait-for-pain):** partial
  `pre-commit` only for file linters—*iff* contributors experience real pain from
  manual hook-version management.
- **`tox`/`nox` (#255)—no.** They solve a matrix of **Python versions**; the project is pinned to one, 3.12.
  **Revisit:** a real requirement for a multi-version matrix emerges.
- **Spec Kit (#114)—removed.** Its role—specification → plan → tasks—is covered by local
  `/plan #N` → `$implement-issue #N`, which lives in the repository, is gated by
  `scripts/validate_issue_sections.py`, and keeps the plan in the issue body rather than a separate
  artifact tree. The cost of an external framework is `/speckit-*` commands and spec files on top of the same
  contract. **Revisit:** a need emerges that the local flow does not cover.
