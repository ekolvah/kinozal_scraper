# Continuous-integration workflow

**Question this document answers:** How `ci.yml` runs the deterministic quality checks for a change.

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
