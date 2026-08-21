# Coverage gaps: quality gates

**Question this document answers:** Which accepted test gaps concern repository guards, review, and quality-gate behavior.

- **V. Secret gate: captured HTML fixtures are outside the scan, and hooks that left with
  `pre-commit` are not replaced (#389).** The `ci_check` `secrets` step is covered by
  `tests/test_secrets_gate.py` (planted key → non-zero, clean file → 0, `git ls-files` failure and
  empty list → visible `exit 1`). **Consciously outside the scan:** `tests/fixtures/**/*.html` —
  captured third-party markup where asset hashes produce high-entropy false positives by construction
  (15 findings in two fixtures). Exclude by file, not baseline: on a match the baseline rewrites
  itself and returns rc=3, while regeneration is a button to make the gate green for a genuinely
  leaked key (rationale — [`ci-local.md`](ci-local.md#secret-scan-secrets)). The cost is that a key written
  **inside** such a fixture is not caught by this gate; the server-side layer remains (GitHub push
  protection). **The second item is not a gap but absent code:**
  `check-yaml`/`check-toml`/`check-json`/`trailing-whitespace`/`end-of-file-fixer` left with
  `.pre-commit-config.yaml`; they ran **zero times** (`core.hooksPath` = `.githooks`), so there is
  no regression and this PR does not add replacements. Recorded so "where is YAML validation?" is
  not reopened as a coverage gap: it is conscious non-scope, a separate unit
  (`agent-process.md`, Governance conventions).

- **W. Reviewer prompts: form is guarded, semantics are not (#374, #392).** Neither reviewer —
  cloud (`.github/workflows/agent-review.yml`) nor local
  (`.claude/agents/architect-reviewer.md`) — contains a severity filter *at the discovery stage*:
  the model follows such a filter literally and a finding silently never reaches the PR. Guards
  catch **known forms**, and the two guards keep different shapes:
  `tests/test_agent_review_workflow.py` checks a suppression imperative at line
  start, presence of `severity` **and** `confidence`, and absence of the gag line
  `no blocking issues`; `tests/test_agent_frontmatter.py` denies removed wording verbatim —
  `do not inflate` / `ruthless` / `brevity by default` — and requires `confidence` and `blocking`.
  **The verbatim denylist covers only the English return path** (#470): the phrasings actually
  removed were Russian, and they are now kept out transitively by `check_language.py`, which
  covers `.claude/**` Markdown prose. Narrowing or dropping the language gate therefore silently
  reopens that hole — the dependency is recorded here because it is invisible in either test.
  The frontmatter guard
  applies **only** to agents declaring the findings contract (#407); other agents do not need these
  tokens. **Semantic paraphrase is consciously NOT covered** ("be selective", "only report what
  matters"): checking prompt meaning would require an LLM call for every suite run, therefore cost
  more and be less deterministic than the subject under test; while a regex over an open set of
  phrasings creates a change detector tailored to current text (the carve-out "allowed if `ruff` is
  nearby" is exactly such a detector, rejected for architect review, #374). Residual protection is
  the prose in [`ci-agent-review.md`](ci-agent-review.md#coverage-first-prompt-no-filtering-at-the-search-stage) and the plan
  reviewer itself. Recorded so "why is there no prompt test?" is not reopened: the test exists;
  only its semantic half was rejected.

- **X. Subprocess encoding: the guard protects the parent side, not the child (#364).**
  `tests/test_subprocess_encoding.py` (AST over `scripts/**`, `src/**`, `tests/**`) requires explicit
  `encoding` on a call that captures text-mode output — without it, Windows decodes with the OS code
  page and loses all output at the first Cyrillic byte. **The child half is consciously uncovered:**
  child Python writes to the pipe in its ANSI encoding until it receives `PYTHONUTF8=1`/`-X utf8`,
  and the call-site guard marks such a case green. This cannot be checked statically: the necessary
  environment is assembled at runtime (`ci_check` passes `-X utf8` to detect-secrets;
  `test_github_trending_pipeline` puts `PYTHONUTF8` in constructed `env`), while requiring a flag on
  **every** Python launch would create false positives where output is known ASCII. A shared
  `run_text()` helper was **rejected, not deferred (#410)** for a technical reason: the repository
  root is **never on `sys.path`** under documented CLI `python scripts/foo.py`
  (`sys.path[0]` = `scripts/`; editable install adds only `src/`) — mechanics already documented in
  `scripts/issue_branch.py`. Every script would need an importlib bootstrap (~8 lines), more
  boilerplate than removed code, while `python -m scripts.foo` would break the CLI, `settings.json`,
  pre-push, and documentation. In addition, three call sites cannot use a helper in principle:
  `ci_check._run` and `new_branch._run(capture=False)` deliberately **do not** capture output, while
  `ci_check._tracked_files` is deliberately **binary**. Instead of a helper, the invariant is held
  by a **rule in the guard itself** — unlike a helper, it also prevents reintroducing the default.
  `PYTHONUTF8=1` as the **sole remedy was also rejected**: it fixes both halves at once, but lives in
  environment state, is invisible in a fresh clone, and does not protect a call site launched
  differently; its guarantee is weaker than a source gate. Recorded so "why not a helper / environment
  variable?" is not reopened as work-for-work.

  **Boundaries of the "output default is forbidden" rule (#410).** It recognises
  `<expression>.stdout or …` / `.stderr or …` by the **attribute left of `or`**, and
  consciously does NOT catch: (a) reassignment to an intermediate variable
  (`out = proc.stdout` → `out or ""`), (b) `getattr(proc, "stdout") or ""`,
  (c) the equivalent `if proc.stdout is None: proc.stdout = ""`. Expanding to value
  tracing is data flow, not syntax: its cost rises qualitatively while catching the same one class.
  The rule guarantees that the **direct** idiom cannot return; the repository has no indirect form
  today, and a human catches one in review. It is deliberately narrow also because a broad rule
  ("any `or ""`") would flag legitimate defaults (`os.environ.get(...) or ""`) and would have to be
  weakened — a pytest assertion has no `noqa` with which to silence it.

  **Not every new branch is covered — consciously (#410).** Tests pin three **distinguishing**
  decisions where confusing outcomes is costly: `check_red` → code 2 ("gate broken"), not 1
  ("tests are not red") — `/implement` step 3 treats them differently; `hooks._run_ruff` →
  `setup_broken` signal, not exception (otherwise stderr reaches the user but not the agent);
  `ci_check._tracked_files` → "file set is unknown", not misleading "no files to scan". Branches
  in `open_pr`/`set_issue_priority`/`issue_branch`/`validate_issue_sections`/`verify_pr_link` remain
  **without dedicated tests**: they have the same outcome ("visible error instead of emptiness"),
  no distinguishing decision, and five copies of one test would be change detectors. The guard rule
  protects them: the default cannot return without making `test_no_output_defaults` red. Recorded so
  the omission is a decision, not forgetfulness.

- **Z. Relative-link integrity between `.md` files is not guarded (#418).** Moving the runtime half
  of `ci.md` to `operations.md` retargeted eight incoming pointers, half of which were prose and
  code comments rather than Markdown links. There is **no** "file exists + anchor resolves" gate,
  and it is consciously not introduced here: it is a separate logical unit (a `CHECKS` entry +
  parity row in `ci.yml` + tests + cost on every run), not an add-on to a documentation PR. More
  importantly, **it would not have caught the discovered incident**: a comment in
  `test_kinozal_pipeline.py` linked to `ci.md:435`, i.e. **by line number**; the file existed, there
  was no anchor at all, and the link silently went stale. The root cause for that class is line-number
  links themselves; it was removed by replacing both such links with section anchors. Recorded so a
  future link checker is not justified by this incident — it concerns another class.

- **AA. "The document must not grow again" is not guarded (#419).** Compacting `ci.md`
  (618 → 417 lines) removed accumulated decision archaeology that already has a home — the relevant
  issue bodies (#235, #255, #396). The tempting anti-recurrence gate "file must have no more than N
  lines" was rejected as **Goodhart**: below a threshold, wording is compressed rather than
  archaeology removed, so the gate is green precisely when the defect is hidden. The semantic
  judgement "how much is rationale prose here and how much is rule" is the same class as the
  semantic-duplicate detector the repository consciously does not build (`project-map.md`); such a
  detector would provide false coverage (§IV). **The real anti-recurrence here is format, not rule:**
  a post-mortem cannot physically fit in a table or ledger row but does fit in a free section.
  Format > prose > gate. Recorded so "why is there no documentation-size gate?" is not reopened as
  work-for-work. **Recording boundary:** this concerns documentation read on demand, where size is
  only a *proxy* for quality. For the always-load set, conversely, a gate exists
  (`tests/test_always_load_budget.py`, #375): there bytes are not a proxy but the charge in every
  session, and the threshold acts as a ratchet rather than a quality norm. The question that
  distinguishes the cases is "is the metric a proxy or the cost itself?", not "size cannot be gated".

- **AB. The always-load budget measures a narrower set than the session preamble (#375).**
  `test_always_load_budget` counts `CLAUDE.md` + `.claude/rules/*.md` without `paths:`, but the
  preamble also includes subagent and slash-command `description:` fields and the `MEMORY.md`
  index. One threshold over this heterogeneous sum would impede diagnosis — a red test would not say
  where growth occurred — so **the gate consciously does not catch cost shifting there** (nor moving
  text into `docs/architecture/*`, which the agent still reads on demand). Growth specifically in
  agent/command frontmatter is a reason to add a **second** counter, not extend this one.

- **AC. A date in documentation is not guarded by a marker for dated material (#428).** The
  link-form guard (`tests/test_doc_narrative.py`) took two of three branches announced in the issue;
  the third — "`20\d\d-\d\d-\d\d` outside an explicit measurement marker" — was **not taken**.
  There are zero violations, no recurrence precedent, and the canon (`project-map.md` §"What
  documentation describes") says nothing about dates, so the predicate itself would be the only
  rule definition. Its closed marker vocabulary (`замер`, `проверено`, `measured`, …) would have to
  be inferred from seven live lines, fitting the text: the first legitimate "as of 2026-08-01" would
  make CI red for a correct document, and maintenance would become "it turned red → add a word".
  Recorded so the branch is not reopened as forgotten: revisit when there is a **measured** recurrence
  and a date rule in the canon, not vice versa.

- **AD. The network half of branch-protection verification is not run in CI (#436).**
  `scripts/check_branch_protection.py` compares the declared composition of required contexts with
  the actual one. Unit tests cover everything except one step — real `gh api` for configuration:
  `GITHUB_TOKEN` lacks `administration` scope, and classic branch protection is not visible through
  the ruleset endpoint (it returns `[]`), so a CI run would require a separate admin token in
  secrets. **Rejected for cost, not impossibility:** a long-lived secret requires rotation, while an
  expired token turns the job red without real drift and teaches people to ignore the detector.
  Compensation is `.githooks/pre-push` on every push (more frequent than a plausible cron), plus
  offline guard `tests/test_branch_protection.py`, which keeps the in-repository half
  (declaration ↔ workflow jobs) in CI. Revisit when enforcement moves to rulesets that make the
  configuration readable with ordinary repository read access.
