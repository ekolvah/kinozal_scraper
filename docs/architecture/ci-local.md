# Local CI gate

**Question this document answers:** How a contributor runs and interprets the local pre-commit quality gate.

## Local pre-commit

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config --unset-all core.hooksPath   # a clone set up for the retired .githooks
pre-commit install --hook-type pre-push # activates the agent-process quality hook
python scripts/ci_check.py
```

The hook comes from `.pre-commit-config.yaml`, which the agent-process installer manages
(ADR-0013). With `core.hooksPath` set, `pre-commit install` refuses and no hook runs, so the
unset comes first. Run `git push` from a shell with the repository venv activated: the hook
resolves `python` and `bash` through the pusher's `PATH` (it removes only its own pre-commit
environment). On Windows, `bash` must be Git Bash (`usr\bin\bash.exe`), not WSL's
`System32\bash.exe`.

Runs every check in the `CHECKS` registry (`scripts/ci_check.py`), in order:
ruff format → ruff lint → detect-secrets → pytest → pip-audit (runtime) →
pip-audit (dev) → requirements consistency → mypy → import contracts. (Module-docstring presence
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

**`pre-push` runs one gate: the declared `test`.** The hook runs `python scripts/ci_check.py`,
the `test` named in `.github/agent-process-quality.json`, exactly as the plugin's CI job does.
The branch-protection drift check does not run on push; run
`agent-process activate_protection --pr <N> --dry-run` on demand; recovery is a manual ruleset edit in
Settings → Rules.
The first push after `pre-commit install` is slower still: pre-commit clones the plugin
repository at the pinned `rev` and builds its hook environment before `ci_check` starts. That
pause is network, like the `pip-audit` tail above, not a hang.

Before starting the gate, the hook asks `git rev-parse --local-env-vars` for Git's
repository-local environment names and unsets exactly those names. Git exports values such as
`GIT_DIR` while running a hook; without this boundary, a child launched after changing into an
unrelated temporary directory can still address the source repository, especially from a
linked worktree. Failure or empty output from the discovery command stops the push with exit
`2`, not permission to continue with inherited repository state.

**Single source of truth.** The registry is the *only* place the check set is
defined. CI does not re-list checks: the agent-process plugin's quality
workflow reads `.github/agent-process-quality.json`, which names
`ci_check.py --list-checks` (the registry as a JSON array) for `checks` and
`ci_check.py` for `test`, and runs `python scripts/ci_check.py --only <name>`
per listed name, so local and CI cannot drift (#153). If `ci_check.py` is green
locally, CI runs the identical checks. `tests/test_ci_check.py::TestStepParity`
pins the declaration to the registry (#597).

> **Disambiguation:** this section's title "Local pre-commit" names the
> pre-commit *moment* (the git-hook that runs before a push). The
> [`pre-commit`](https://pre-commit.com) framework only installs and launches that hook; it
> runs no file linters of its own, and `CHECKS` stays the single check registry
> ([`ci-tooling-decisions.md`](ci-tooling-decisions.md#consciously-not-adopted)).

### Gate CLI exit codes

Developer-flow gates whose caller distinguishes a domain verdict from missing
evidence use one contract: `0` means the gate passed, `1` means it computed an
explicit negative verdict, and `2` means usage was invalid or the gate could
not compute (tool invocation, output capture, or payload decoding failed).

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

### Session hooks (`scripts/hooks.py`)

A separate, *earlier* feedback layer that runs **during** an agent session, not
at push (#281). `.claude/settings.json` declares a `PostToolUse` hook (matcher
`Edit|Write`) invoking `python -m scripts.hooks on-edit`, which dispatches two cheap
checks in one process right after each file edit:

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
(`tests/test_hooks.py`).

The navigation policy (shell file reads and over-budget `Read`, `PreToolUse`) and the
memory checkpoint (a write under the agent's auto-memory directory, `PostToolUse`) are the
agent-process plugin's hooks, `navigation_policy` and `memory_checkpoint`, active here because
`.github/workflows/agent-process.yml` exists. The security carrier stays local:
`permissions.deny`, guarded by `tests/test_settings_deny.py`.

This is instant feedback that **complements, never replaces** `ci_check.py` (the
canonical pre-push gate), and is unrelated to the `pre-commit` framework that launches
that gate ([`ci-tooling-decisions.md`](ci-tooling-decisions.md#consciously-not-adopted)) — the framework
is a push-time hook launcher, this is a session-time editor hook.
