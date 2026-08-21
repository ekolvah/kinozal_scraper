# Local CI gate

**Question this document answers:** How a contributor runs and interprets the local pre-commit quality gate.

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

**Output is budgeted, and this line is the forcing function.** `addopts` in
`pyproject.toml` carries `-q`, so a green run of the gate prints the summary rather than a
per-file progress map: measured 2026-08-15, `python -m pytest` fell from 8 090 to 2 368
characters and the whole gate from 9 056 to about 3 300. A failing run is untouched —
traceback, `E ` lines and the short summary all remain (1 430 → 1 003 characters). The
motive is the agent session, where every character is re-sent on each later call (#533),
so the flag stays global rather than moving into `ci_check.check_pytest()`: agents also
run `python -m pytest tests/test_x.py` by hand. There is deliberately no test asserting
the flag is present — that is a resource-only regression, which
[`testing.md`](testing.md#rule-when-a-test-is-not-worth-writing) sends to a forcing
function such as this paragraph rather than to a guard test (precedent #207).

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
[§Required status checks](ci-branch-protection.md#required-status-checks-branch-protection).

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
> deliberately does **not** use ([§Consciously not adopted](ci-tooling-decisions.md#consciously-not-adopted)).

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

The same policy owns a second `PreToolUse` event, matcher `Read` (`pre-read`, #534). The Bash
branch parses a command; this one measures the **bytes of the slice `Read` will actually
return** (`[offset, offset + limit)`, computed hook-side) against a 28 000-byte budget, and the
denial hands back the concrete `limit` that fits, the measured size, and an approximate token
figure. A threshold keyed on "is `limit` present" would have been a rename: `limit` counts
lines and `Read` truncates at 2000 of them, so `limit=2000` returns every file in this
repository whole. Same failure mode as the Bash branch — anything unmeasurable (missing file,
directory, non-UTF-8 bytes, a format where slicing is meaningless) yields no decision.

This is instant feedback that **complements, never replaces** `ci_check.py` (the
canonical pre-push gate), and is unrelated to the `pre-commit`/`tox` *framework*
([consciously declined](ci-tooling-decisions.md#consciously-not-adopted)) — that no-go is about a PR-time
tool-registry framework, this is a session-time editor hook.
