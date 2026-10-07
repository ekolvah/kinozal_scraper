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
  the original `.pre-commit-config.yaml`; they ran **zero times** (`core.hooksPath` was
  `.githooks` then), so there is no regression and this PR does not add replacements. The
  plugin's `.pre-commit-config.yaml` (ADR-0013, #598) brings none of them back: its one hook
  runs `ci_check.py`. Recorded so "where is YAML validation?" is
  not reopened as a coverage gap: it is conscious non-scope, a separate unit
  (one PR, one logical unit: goal 3 of [the plugin's `principles.md`](https://github.com/ekolvah/agent-process-distribution/blob/main/skills/agent-process/principles.md#goal-function)).

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
  (`sys.path[0]` = `scripts/`; editable install adds only `src/`). Every script would need an importlib bootstrap (~8 lines), more
  boilerplate than removed code, while `python -m scripts.foo` would break the CLI, `settings.json`,
  pre-push, and documentation. In addition, some call sites cannot use a helper in principle:
  `ci_check._run` deliberately **does not** capture output, while
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

  **Not every new branch is covered — consciously (#410).** Tests pin the **distinguishing**
  decisions where confusing outcomes is costly: `hooks._run_ruff` →
  `setup_broken` signal, not exception (otherwise stderr reaches the user but not the agent);
  `ci_check._tracked_files` → "file set is unknown", not misleading "no files to scan".

- **Z. Relative-link integrity between `.md` files is not guarded (#418).** Moving the runtime half
  of `ci.md` to `operations.md` retargeted eight incoming pointers, half of which were prose and
  code comments rather than Markdown links. There is **no** "file exists + anchor resolves" gate,
  and it is consciously not introduced here: it is a separate logical unit (a `CHECKS` entry + tests + cost on every run), not an add-on to a documentation PR. More
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

- **AC. The form of an issue reference is not guarded (#620).** The link-form guard (`#N` only inside
  parentheses, never in a heading, `#` before a digit reserved for issues) was deleted with its
  rule. It gated writing style, not correctness, and it failed every OpenSpec plan, which names
  issues in running prose. **Accepted loss:** narrative `#N` may return to docs; a `#N` in a heading
  is caught only by its effect, when a rename dangles an inbound anchor (`tests/test_doc_links.py`).
  Recorded so "why no link-form guard?" is not reopened as work-for-work.

- **AD. The merge-gate drift check runs on demand only (ADR-0013, #600).** The merge gate is the
  plugin's ruleset, and `agent-process activate_protection --pr <N> --dry-run` is the only
  comparison of the live ruleset with the plugin's template; neither the pre-push hook nor CI runs
  it. **Accepted loss, not an oversight.** A CI probe would need an admin token in secrets
  (`GITHUB_TOKEN` lacks `administration` scope): a long-lived secret requires rotation, while an
  expired token turns the job red without real drift and teaches people to ignore the detector.
  The required context names come from the upstream managed workflows, so the repository
  declares nothing of its own a probe could compare against; and a lockout (a check that never
  reports) is recovered by a manual ruleset edit, which a CI job inside the locked repository
  could not perform anyway (recovery is a manual ruleset edit in Settings → Rules). The v1
  in-repository half — a declared context set guarded against the workflow jobs — left with the
  v1 workflows it described.

- **AR. §V's live observation has no carrier and no gate (#600, #626).** The v1 issue validator
  checked a bug's `## Evidence` block, and the repository-owned `/plan` chained a `discovery`
  subagent. #600 kept the block as prose and the subagent behind an instruction; #626 deleted
  both, because the plugin declined a discovery role (ekolvah/agent-process-distribution#307).
  The duty to observe the live system before designing how its data is read or classified is
  held only by the plugin's architect review, which reads the plugin's own
  `skills/agent-process/principles.md` §V. Capture commands are in
  [`testing.md`](testing.md#external-data-capture-routes). **Accepted by the maintainer
  (2026-10-01, #616)** rather than rebuilt locally: a repository-owned carrier or validator over
  plugin-owned artifacts would be the duplicated control plane ADR-0013 retires. **Revisit
  trigger:** a design about external data ships without an observation, or a plugin release
  drops the §V live-observation text.
