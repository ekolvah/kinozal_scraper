# kinozal_scraper — context for Claude

## What the application does
Parses the kinozal.tv top list on a schedule (GitHub Actions, cron in `.github/workflows/run-script.yml`), deduplicates through Google Sheets, and sends new releases to Telegram. It also summarizes Telegram channels through Gemini.

## Environment

Windows + git-bash. Every pitfall below has recurred ≥2 times—do not reopen it.

- **Python**: `python`, NOT `python3` (the latter is a Microsoft Store stub that opens the store).
- **Utilities**: `jq` and `rg` are **absent**—parse JSON/text with pure-Python scripts in `scripts/`. `sed` and
  `awk` do exist (`/usr/bin/`). Reading files through the shell (`ls`, `find`, `cat`, `sed -n`, `grep`/`head`/`tail`
  on a file operand) is denied by a `PreToolUse` hook that names the replacement tool (#485); pipe stages are
  untouched. `awk` is not covered. The same hook meters `Read` itself: a slice over the byte budget is denied
  with the `limit` that fits handed back (#534). The earlier blanket "no `jq`/`sed`/`awk`" stated a preference as an
  environment fact, which is why it did not hold.
- **Paths**: `~/` does not resolve reliably in shell hooks and settings.json. Use absolute paths (`C:/Users/<username>/...` or `$HOME/...` in bash).
- **PowerShell ≠ bash**: `$null` (not `/dev/null`), `$env:VAR` (not `$VAR`), and backtick for line continuation. Invoke the Bash tool explicitly for POSIX scripts.
- **`subprocess.run` that captures output**: always use `encoding="utf-8"`, and **never use `or ""` for `stdout`/`stderr`**—`None` means broken capture (the stream reader died while decoding), while a default turns failure into emptiness. `tests/test_subprocess_encoding.py` enforces both rules (#364, #410). If the child is Python, it also needs `PYTHONUTF8=1`/`-X utf8`; this guard does not catch that.
- **Sporadic file locks / AV scanning** during long `git`/`pytest` runs: retry once before root-cause investigation. If it reproduces, investigate.
- **`ci_check.py` / `git push` with the pre-push hook take minutes** (timing is canonical in the [CI doc](docs/architecture/ci-local.md#local-pre-commit)): output pauses after `pytest` at `pip-audit`—that is a **network step, not a hang**. Do not kill the process or poll; make one foreground invocation with `timeout: 600000` ([mindset](.claude/rules/mindset.md)).
- **`tasklist` in the agent sandbox (the Bash tool on the maintainer’s Windows machine) returns empty output** (0 lines even without filtering); it works in a normal terminal. Do not infer “the process died” from it—this previously caused a second `ci_check` instance to be launched by mistake.

## Debugging

Root-cause-first and instrument-before-patching are adapter-neutral rules in
[`principles.md` §V](docs/architecture/principles.md#v-root-cause-before-fix),
including the required live observation when a design depends on external-system behaviour.

## Active work

Current work: [GitHub Issues](https://github.com/ekolvah/kinozal_scraper/issues)

## PR Workflow

Workflow procedural rules (roles, branch, PR discipline, labels, gates) are canonical in **[`docs/architecture/agent-process.md`](docs/architecture/agent-process.md)**. Claude runs planner/reviewer through `/plan #N` and implementer/fixer through `/implement #N`; the repository default for the latter is Codex `$implement-issue #N`, and the user chooses the route. Do not duplicate them here.

## Dependencies

The canonical rule is in [`agent-process.md`](docs/architecture/agent-process.md) (run pip-compile in the same commit when changing
`requirements*.in`). Mechanically, `scripts/ci_check.py` catches version drift and packages in `.in` without a pin
in `.txt`.

## Before every commit

`python scripts/ci_check.py` — see the [CI doc](docs/architecture/ci.md) for details.
`.githooks/pre-push` runs ci_check automatically before push—do not duplicate it manually.

## Architecture decisions

- **[Principles](docs/architecture/principles.md)** — source of truth: principles §I–VII + quality gates + governance. If it conflicts with this file, `principles.md` prevails.
- [Project map](docs/architecture/project-map.md) — the **complete navigation index** (which file answers which question) + IA policy (tier model, canonical home). Do **not** duplicate individual documents here—navigate through this index.
- [Mindset](.claude/rules/mindset.md) — Claude harness token tactics + pointers to the goal function/principles/process, always-load
