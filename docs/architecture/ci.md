# CI and deployment

## Local pre-commit

```bash
python scripts/ci_check.py
```

Runs every check in the `CHECKS` registry (`scripts/ci_check.py`), in order:
ruff format → ruff lint → pytest → module docstring presence → pip-audit
(runtime) → pip-audit (dev) → requirements consistency → mypy → import
contracts.

**Single source of truth.** The registry is the *only* place the check set is
defined. `ci.yml` does not re-list checks — each CI step runs
`python scripts/ci_check.py --only <name>`, so local and CI cannot drift. If
`ci_check.py` is green locally, CI runs the identical checks. Adding or removing
a check in the registry without updating `ci.yml` fails
`tests/test_ci_check.py::TestStepParity` (#153).

Pre-push hook: `.githooks/pre-push` runs `ci_check.py` automatically.
Activate: `git config core.hooksPath .githooks`

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
lint, pytest, headers, pip-audit, pip-audit-dev, requirements, mypy, imports).
The per-step split keeps the GitHub Actions UI granular (you see *which* gate
failed) while the check set itself stays defined once, in `ci_check.py`.

mypy type-checks every `*.py` outside `_EXCLUDE_DIRS` (`.venv`, `.git`,
`__pycache__`, `.audit-tmp`, `.claude`) and any `pytest-cache-files-*` dir, via
`ci_check._find_modules()` — the same discovery used locally.

Imports between modules (`from kinozal_scraper.generic_pipeline import …`) are
absolute package imports: the sources live in the installable package
`src/kinozal_scraper/`, so mypy resolves them natively by package name — no
`mypy_path`, no whole-file-list trick, and a single-file invocation
(`mypy src/kinozal_scraper/json_pipeline.py`) resolves the same way. The package
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
  `crypto`/`kinozal_auth`. Encodes "implementations receive ready clients, not
  credentials — auth lives in the caller" as a machine rule.
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
2. **json_pipeline.py** — GitHub `new_popular`
3. **github_trending_pipeline.py** — GitHub trending (HTML + enrichment)
4. **steam_pipeline.py** — Steam Most Played (Steam Charts API + appdetails)
5. **events_pipeline.py** — Soldout events
6. **kinozal_pipeline.py** — Kinozal movies
7. **telegram_summarizer.py** — `if: always()` (runs even if earlier steps fail)

### Pipeline-step isolation (#245)

**Root cause it fixes:** GitHub Actions steps carry an *implicit* `if: success()`. So a
single step's `exit 1` used to cascade into **skipping every later step** — a transient
third-party 403 in `events_pipeline` skipped `kinozal_pipeline` and suppressed movie
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

## Environment variables

### Shared across pipelines

| Variable | Type | Used by |
|---|---|---|
| `CREDENTIALS` | secret | json_pipeline, events_pipeline, kinozal_pipeline (Google Sheets service account JSON) |
| `SPREADSHEET_URL` | secret | json_pipeline, events_pipeline, kinozal_pipeline |
| `TELEGRAM_BOT_TOKEN` | secret | all 4 steps |
| `TELEGRAM_CHAT_ID` | secret | all 4 steps |

### json_pipeline / github_trending_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | secret | GitHub API auth (json_pipeline only) |
| `GH_TOP_LIMIT` | var | max GitHub repos to fetch (json_pipeline only) |
| `GH_TRENDING_LIMIT` | var | max GitHub trending repos to fetch (github_trending_pipeline; default 10) |
| `GOOGLE_API_KEY` | secret | Gemini API for enrichment |
| `LLM_MODEL` | var | preferred Gemini model |
| `GEMINI_EXCLUDED_MODELS` | var | comma-separated models to skip |

### steam_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `STEAM_TOP_LIMIT` | var | max Steam Most Played entries to fetch |

### events_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `SOLDOUT_URL` | var | Soldout events page URL |

### kinozal_pipeline

| Variable | Type | Purpose |
|---|---|---|
| `API_KEY` | secret | Kinozal API key |
| `URLS` | var | Kinozal page URLs to scrape, формат `label\|url;...`; local fallback — env `KINOZAL_TOP_URL` (plain url). Если не задано ни то ни другое — pipeline логирует ошибку `no URLs configured`. `sources.json` `url`/`base_url` для скрейпинга **не читается** (только schema-placeholder), см. `kinozal_pipeline.py::_kinozal_urls` |
| `KINOZAL_USERNAME` | secret | **Опционально.** Логин аккаунта на зеркале `kinozal.guru` — включает автоматический fallback на зеркало при сбое `kinozal.tv` (см. блок ниже). Парный к `KINOZAL_PASSWORD`; **partial** (только один из двух) → WARNING + fallback отключён (не fail) |
| `KINOZAL_PASSWORD` | secret | **Опционально.** Пароль аккаунта `kinozal.guru`. Парный к `KINOZAL_USERNAME` |

> **Fallback на зеркало при недоступности `kinozal.tv` (#227):** primary —
> анонимный `kinozal.tv` (`URLS` остаётся `.tv`, **переключать не нужно**). Если fetch какого-то
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
> Сейчас потребитель — production-cron (`run-script.yml` / `kinozal_pipeline.py`). E2E
> `tests/test_e2e_kinozal_titles.py` станет вторым потребителем после #136 (тест безусловно
> skip'нут, пока `kinozal.tv` отдаёт 522).

### telegram_summarizer

| Variable | Type | Purpose |
|---|---|---|
| `CHANNEL_URL` | var | semicolon-separated Telegram channel URLs/IDs |
| `GOOGLE_API_KEY` | secret | Gemini API for summarization |
| `API_HASH` | secret | Telethon app hash |
| `TELEGRAM_API_ID` | secret | Telethon app ID |
| `PHONE_NUMBER` | secret | Telethon auth phone |
| `TELETHON_SESSION` | secret | Telethon session string |
| `SECRET_KEY` | secret | crypto module key |
| `LLM_MODEL` | var | preferred Gemini model |
| `GEMINI_EXCLUDED_MODELS` | var | comma-separated models to skip |

## Setup

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks
```
