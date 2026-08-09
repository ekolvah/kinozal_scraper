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
git config core.hooksPath .githooks   # активирует .githooks/pre-push
python scripts/ci_check.py
```

Runs every check in the `CHECKS` registry (`scripts/ci_check.py`), in order:
ruff format → ruff lint → detect-secrets → pytest → pip-audit (runtime) →
pip-audit (dev) → requirements consistency → mypy → import contracts → language. The `language`
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
`pip-audit` working, not a hang) is in `CLAUDE.md` §Среда. If the measurement
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

**Сознательно не взято здесь:** контракт «оркестраторы импортируют только Protocol-модули» —
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

**Конвенция глушения — одна на все четыре, и именно её пинят гарды:**

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
- **У каждого правила — anti-drift-гард** (`tests/test_complexity_ratchet.py`,
  `test_ruff_dead_code_rule.py`, `test_ruff_arg_slf_rules.py`, `test_ruff_docstring_rule.py` — все
  в жанре `test_ruff_silence_rules.py`), но **глубина у них разная, и это не выровнено**:
  - *effective select* пинят все четыре;
  - ветку «код не лежит в глобальном `ignore`» и ветку `per-file-ignores` несут три из четырёх —
    **у ratchet-гарда (`C901`/`PLR0912`/`PLR0915`) их нет**, так что эту тройку сегодня можно
    молча положить в `ignore`, оставив все гарды зелёными. Пробел назван, а не закрыт: он старше
    расширения docstring-гейта на `tests/` (#433) и своей issue пока не имеет.
  - у ветки `per-file-ignores` **две формы, и форма следует из наличия легитимного исключения**:
    `ARG`/`SLF` проверяются по покрытию путей (`fnmatch` против sentinel-путей `src`/`scripts`),
    потому что `tests/**` им заглушён законно; у `ERA001` и docstring-кодов легитимных исключений
    нет, поэтому проверка строже — кода не должно быть в `per-file-ignores` **ни под каким
    паттерном** (иначе узкий возврат вида `"tests/test_x.py" = ["D100"]` прошёл бы мимо
    sentinel-зонда). Docstring-гард дополнительно читает `extend-`-близнецы обеих таблиц
    (`extend-ignore`, `extend-per-file-ignores`), которые ruff honours наравне; у трёх остальных
    этой ветки нет.

**§IV no-op-гард старого docstring-скрипта не перенесён — сознательно, а не потерян.** «Упасть,
если скан не нашёл ни одного файла» было артефактом параметризованного `root.rglob(Path("src"))`
в bespoke `scripts/check_headers.py`; `ruff check .` рекурсирует всё дерево от cwd и промахнуться
мимо пакета так не может. Остаточный случай «пакет исчез/пуст» ловят **строго жёстче**
`test_package_importable.py` (17 хардкод-`import_module`), `test_repo_layout.py`, mypy и
import-linter.

**Сознательно не взято здесь:**

- **`RUF100`** (самоочищающийся noqa) — отложен: 18 существующих unused-noqa по репо, это
  отдельная уборка, а не этот гейт (#233 Out of scope).
- **`vulture`** (cross-module unused) — не взят: репо намерено **нулём** cross-module мёртвого
  кода, а локальное неиспользуемое уже ловит ruff `F` (F401/F841). На динамической форме этого
  кода (реестр пайплайнов, декларативный конфиг, `Protocol`-реализации, pytest-фикстуры,
  `__main__`-энтрипоинты) vulture FP-склонен: зависимость + гейт + whitelist с per-CI-триажем
  ради гипотетики. **Revisit (wait-for-pain):** появится реальный cross-module мёртвый код,
  который `ERA001` не ловит (#235 Out of scope).

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

**Конвенция размещения None-check** (её требует гард, поэтому она здесь, а не в стиле-гайде):
проверка на `None` стоит там, где захваченный вывод **читается** — в `_run`-шве файла, если шов
есть; на call-site, если скрипт делает единственный вызов; инлайном для вызовов, которые шов
**намеренно** обходят (`new_branch`'s `git branch -d`, которому позволено падать и потому нельзя
через `check=True`-шов). Централизован только *инвариант* — здесь, в гарде: общий helper-модуль
невозможен, корень репо никогда не на `sys.path` при `python scripts/foo.py`
(см. [ledger](coverage-gaps.md)).

**Ни ruff, ни bandit, ни pylint это не покрывают** — стандартного правила на `subprocess`-encoding
нет ни у одного (ruff'ов `PLW1514` — про `open()`). Записано, чтобы прецедент «стандартные тулы >
велосипеды» (#237) не переоткрывали против этого гарда. Второй предел — гард проверяет только
**родительскую** сторону: дочерний Python всё равно пишет в кодовой странице ОС без
`PYTHONUTF8=1` / `-X utf8`. Оба предела в
[ledger'е принятых дыр](coverage-gaps.md); само правило — канон в
`CLAUDE.md` §Среда.

`scripts/hooks.py` additionally passes `errors="replace"` — per-call-site decision for a tool
whose entire job is visibility; the guard does not require `errors` anywhere.

### Doc guards

Статические гарды над `.md` (плюс одна репо-широкая ветка, см. ниже), все в жанре выше —
статическая проверка под `check_pytest`, без записи в реестр `CHECKS`. Заголовок этой секции намеренно не перечисляет файлы: якорь
генерится из его текста, и привязка адреса к волатильному перечню — тот же дефект, что номер
таски в заголовке.

- **header'ы** — каждый картируемый `.md` несёт строку «на какой вопрос отвечает этот файл»
  (конвенция — `project-map.md`, #421).
- **ссылки** — каждая внутренняя ссылка и каждый code-span вида `` `file.md#anchor` `` резолвится:
  цель есть в индексе, якорь совпадает со slug'ом заголовка по правилам github-slugger (#427).
  Скоуп и проверка существования — **индекс git** (`git ls-files -z`, фильтр по суффиксу уже в
  Python), а склейка пути — **лексическая** (`posixpath.normpath`), без единого обращения к ФС:
  gitignored-копии репо в `.claude/worktrees/` иначе краснели бы локально, а `Path.exists()` /
  `Path.resolve()` на Windows регистронезависимы и пропустили бы `` `Pipeline.md#…` `` локально, чтобы
  уронить CI на Linux. Разбор через `markdown-it-py`: ссылка внутри ```-блока не считается
  ссылкой, а текст заголовка нужен отрендеренный.
- **форма ссылки** — `#N` стоит скобочным указателем, а не членом предложения; в заголовке
  секции запрещён и в скобках, потому что якорь генерится из текста заголовка (#428). Сигил `#`
  зарезервирован за issue/PR: правило — `agent-process.md`, доска — `Project 1`; конвенция
  заменяет собой открытый словарь исключений в предикате. Эта ветка идёт по **всем**
  отслеживаемым файлам, а не только по `.md`: словарь закрыт по **токену** (`workflow`,
  `Project`) в обычной markdown-оправе, а `.py` и `.toml` дрейфуют так же — прозой её
  удержать не вышло. Построчный regexp, поэтому code-span её не глушит: иллюстрация правила
  пишется через метапеременную. Разрешённая зона — только **сомкнутая**
  пара `()`, иначе одна незакрытая скобка молча превращает хвост абзаца в белый список; парность
  backtick'ов и границы ссылок отдаются парсеру, а `table` включён, чтобы скобка из одной ячейки
  не смыкалась со скобкой из другой. Записи MADR (`docs/adr/`) вне скоупа по жанру: запись —
  дом обоснования, датирована по конструкции и после принятия иммутабельна.

Каждый из них — presence / резолвимость / форма, **не** корректность: указатель на существующий, но
переставший быть домом темы файл, как и хроника, аккуратно уложенная в скобки, ловится человеком
на ревью. Границы каждого гарда названы в его docstring.

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
