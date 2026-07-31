# CI and quality gates

**На какой вопрос отвечает этот файл:** какие автоматические гейты качества стоят
на пути изменения и на чём они работают. Ось — «гейт», а не «GitHub Actions»:
поэтому сюда же заезжает **локальный** plan-стадийный `architect-reviewer`
(`.claude/agents/`), который в CI не запускается, но по
[`principles.md §VII`](principles.md#vii-simplicity-first) стоит гейтом наравне с
cloud-ревью. Модельная поверхность агентного тулинга (обе половины) описана в
§«Model pinning» — это её единственный дом.

**Чего здесь нет.** Как прод-прогон эксплуатируется — расписание, env-переменные и секреты,
изоляция падений, алертинг, runbook'и оператора → [`operations.md`](operations.md) (#418).
**И как гейт стал таким, какой он есть** — история решения живёт в issue/PR, не в теле дока
([`project-map.md` §«Что описывает документация»](project-map.md#что-описывает-документация-текущее-состояние-не-история-и-не-идеи)).
Операционный критерий, по которому фраза остаётся здесь: **её отсутствие заставит агента либо
совершить неверное действие, либо переделать уже отвергнутую работу**. Отвергнутый инструмент или
правило — строкой по месту своего гейта; инструмент целиком, у которого своей секции нет, — в
[§«Consciously not adopted»](#consciously-not-adopted) (#419).

## Local pre-commit

```bash
pip install -r requirements.txt -r requirements-dev.txt
git config core.hooksPath .githooks   # активирует .githooks/pre-push
python scripts/ci_check.py
```

Runs every check in the `CHECKS` registry (`scripts/ci_check.py`), in order:
ruff format → ruff lint → detect-secrets → pytest → pip-audit (runtime) →
pip-audit (dev) → requirements consistency → mypy → import contracts. (Module-docstring presence
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
`timeout: 600000` in `.claude/commands/implement.md` step 6 stops working and
needs revisiting together with this number.

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

**Two §IV invariants in `ci_check`, and neither is decoration:** `_tracked_files()` exits 1
when `git ls-files` fails, and `check_secrets()` exits 1 on an **empty file set**. The hook
itself returns 0 when handed no files — so a broken `git` invocation would otherwise
reproduce this gate's own historical defect (configured, green, scanning nothing) one layer
deeper. Do not "simplify" either exit away. `tests/test_secrets_gate.py` covers both, plus a
planted key (non-zero) and a clean file (zero).

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
at push (#281). `.claude/settings.json` declares a single `PostToolUse` hook (matcher
`Edit|Write`) invoking `python "$CLAUDE_PROJECT_DIR/scripts/hooks.py" on-edit`,
which dispatches two cheap checks in one process right after each file edit:

- `*.py` → ruff **check-only** (`ruff format --check` + `ruff check`, **no
  `--fix`/format mutation** — the harness tracks file contents, so rewriting
  behind its back breaks the next Edit's `old_string` match). Remaining lint →
  stderr + exit 2 (PostToolUse exit 2 feeds stderr back to the agent).
- `requirements*.in` → a `pip-compile` reminder (`workflow.md` §7 is otherwise only
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
same commit. The required status check is the bare context `quality`
(event-agnostic — verified once against `…/required_status_checks` →
`contexts: ["quality"]`; re-verifying needs repo-admin scope, hence the note),
so the `pull_request` run satisfies branch protection on its own and dropping
`issue-*` orphans nothing (#206). Do not re-add `issue-*` to
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
сегодня **невыразим**: `Protocol`-классы лежат в одном модуле со своими конкретными
реализациями. Пересмотр — вместе с Protocol-extraction рефакторингом (#234 Out of scope).

### Lint gates and ratchets (ruff)

Все четыре набора правил едут на существующем `check_lint` — без новой зависимости и без
отдельного шага реестра. У ruff **нет нативного baseline**, поэтому ценность у ратчетов
**forward**: новый или изменённый код через порог валит CI, легаси grandfather'ится.

| Правила (issue) | Тип | Что ловит | Порог / известная дыра |
|---|---|---|---|
| `C901`, `PLR0912`, `PLR0915` (#233) | ратчет | разрастание метода (цикломатика / ветки / стейтменты) | `max-complexity = 12` — **выровнен с дефолтным порогом веток PLR0912**, а не подогнан под сегодняшний код (защита от Goodhart/байкшеддинга); PLR0912/PLR0915 на дефолтах ruff (12 / 50). **Дыра:** blanket `# noqa` позволяет grandfathered-функции расти дальше незамеченной — ратчет защищает новый код и новые функции, не замороженную шестёрку. Настоящий фикс — распил (#251, §V documented-mitigation) |
| `ERA001` (#235) | ратчет | закомментированный код | Репо намерено **чистым**. `tests/**` **не** исключены: мёртвый код мёртв независимо от роли файла |
| `ARG001`, `ARG002`, `SLF001` (#236) | ратчет | неиспользуемый аргумент функции/метода, доступ к приватному члену чужого объекта | 110 существующих хитов триажированы поштучно, реальных мёртвых параметров в `src/` — ноль. `SLF001` в `src/` **нулевой**: два хита (`RotatingGeminiEnricher` лез в `GeminiEnricher._model_name` из двух мест) были одной настоящей §II-утечкой и сняты публичным свойством `model_name`, а не noqa. **Не выбраны** `ARG003`/`004`/`005` (classmethod/staticmethod/lambda) — сознательный defer (#236 Out of scope) |
| `D100`, `D104`, `D419` (#253) | **presence-гейт** (порога нет) | отсутствующий / пустой module- и package-docstring | Репо чисто, скоуп репо-широкий (`src/` **и** `scripts/`/root). `D101`/`D103` (класс/функция) сознательно **не** выбраны — гейт только уровня модуля. **Дыра:** `D100`/`D104` флагают только *публичные* модули, поэтому будущий `src/kinozal_scraper/_internal.py` проскочит (сегодня такого модуля нет) |

**Конвенция глушения — одна на все четыре, и именно её пинят гарды:**

- **Настоящий false positive глушится per-site** `# noqa: <точные коды>` с причиной — никогда
  per-file: per-file-ignore ослепляет весь файл к *новым* хитам. Живые примеры per-site:
  шесть grandfathered-функций на `def`-строке (#233) и два Protocol-conformance стаба
  (`NullEnricher.enrich`'s `item`, `InMemoryStorage.append_rows`'s `headers`), чей параметр
  требует интерфейс, а использует не эта реализация.
- **`# noqa` — escape hatch для FP, а не для настоящего срабатывания детектора.** Единственный
  хит `ERA001` (иллюстративный комментарий-схема `# [dedupe_key, title, ...]`, который ruff
  разбирает как список) был исправлен **переформулировкой в прозу**: код никогда не был мёртвым,
  и глушение обучило бы хатч на не-исключении (§IV).
- **`tests/**` исключаются категорически только там, где роль файла меняет смысл правила.**
  Для ARG/SLF — да (`per-file-ignores` `"tests/**"`): white-box-тесты по §II законно зовут
  приватные хелперы напрямую, а сигнатуры моков диктует мокируемый вызываемый объект, не
  использование. Для `D100`/`D104`/`D419` — тоже да, но **по другой причине**: тесты никогда и
  не были docstring-checked, старый `check_headers.py` сканировал только `src/`, и гейт этого не
  требовал ни дня. Для `ERA001` — **нет**. То есть в `per-file-ignores` для `tests/**` лежат
  шесть кодов из трёх наборов, и у каждого набора своё основание; поверхностный паттерн «тестам
  всегда дают per-file-ignore» отсюда **не** карго-культить.
- **У каждого правила — anti-drift-гард**, пинящий код в *effective select*, вне глобального
  `ignore` и не нейтрализованным через `per-file-ignores` ни для одного пути `src`/`scripts`:
  `tests/test_complexity_ratchet.py`, `test_ruff_dead_code_rule.py`, `test_ruff_arg_slf_rules.py`,
  `test_ruff_docstring_rule.py` (все — в жанре `test_ruff_silence_rules.py`).

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
  зарезервирован за issue/PR: номер правила — `workflow.md §7`, доска — `Project 1`; конвенция
  заменяет собой открытый словарь исключений в предикате. Эта ветка идёт по **всем**
  отслеживаемым файлам, а не только по `.md`: словарь закрыт (две формы), а `.py` и `.toml`
  дрейфуют так же — прозой её удержать не вышло. Разрешённая зона — только **сомкнутая**
  пара `()`, иначе одна незакрытая скобка молча превращает хвост абзаца в белый список; парность
  backtick'ов и границы ссылок отдаются парсеру, а `table` включён, чтобы скобка из одной ячейки
  не смыкалась со скобкой из другой. Записи MADR (`docs/adr/`) вне скоупа по жанру: запись —
  дом обоснования, датирована по конструкции и после принятия иммутабельна.

Каждый из них — presence / резолвимость / форма, **не** корректность: указатель на существующий, но
переставший быть домом темы файл, как и хроника, аккуратно уложенная в скобки, ловится человеком
на ревью. Границы каждого гарда названы в его docstring.

## Claude review workflow (`claude-review.yml`)

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
  via `Bash(gh pr comment ...)`. Controlling comment *format* is not enough: a run that finds
  no issues and invokes no publishing tool leaves the PR silent.

**Сознательно временное:** `show_full_output: true` (полный SDK-транскрипт в логах Actions) —
включён, пока стабилизируется поведение ревью; он шумит и может вынести наружу внутренний
model-chatter. **Триггер снятия:** цикл ревью перестал требовать разбора транскрипта, то есть
когда в последний раз транскрипт понадобился для диагностики — а не «когда-нибудь».

### Coverage-first prompt: no filtering at the search stage

**Механизм дефекта — причина существования контракта, а не археология.** Модель следует
инструкции-фильтру (`Skip nitpicks — ruff handles formatting/lint`) **буквально**: находка
делается, признаётся ниже заявленной планки и молча не докладывается — а отфильтрованная находка
неотличима от отсутствующей (§IV). Тот же дефект имеет вторую форму на **выходе**: предписание
«post exactly "✅ Review complete — no blocking issues found."» запрещало добавить что-либо ещё,
так что прогон с тремя should-fix и нулём blocking обязан был напечатать одну строку. Снять фильтр
на входе, оставив на выходе, — починить половину. По этому же механизму написан и
`.claude/agents/architect-reviewer.md`.

Контракт — **grade, never drop**:

- каждая находка репортится с `severity` (blocking / should-fix / nice-to-have) и `confidence`
  (high / medium / low) — фильтрует человек, не модель;
- `blocking` — конкретная планка (неверное поведение, падающий или отсутствующий тест на
  изменённое поведение, вводящий в заблуждение результат, утёкший секрет, нарушение конвенции
  `CLAUDE.md`), а не качественное слово «nitpick»;
- то, что уже ловит детерминированный гейт (ruff / mypy в `ci_check.py`), градуируется
  `nice-to-have, duplicate of ci_check` — ранжируется последним, потому что покрыто другим
  исполнителем, а не утаивается;
- **инлайн несёт только blocking / should-fix**, чтобы инлайн-канал не тонул; остальное — в
  summary, где перечислены все находки по severity. Фиксированная однострочная summary
  применима только к «не найдено ничего ни на одной severity».

`tests/test_claude_review_workflow.py` гардит **форму**: нет suppression-императива в начале
строки промпта, `severity` + `confidence` присутствуют, нет gag-строки `no blocking issues`.
Семантику он не проверяет — фильтр, перефразированный как «be selective», проходит; качественная
половина (что планка blocking остаётся конкретной, а исключение про ruff — не императивным)
держится этой прозой и ревью, не exit-code'ом.

### Model pinning and what a stale pin looks like

**Single home for the whole model surface (#374 + #392).** Two review surfaces run on a Claude
model: this cloud workflow and the local plan-stage `architect-reviewer` (`.claude/agents/*.md`).
The policy is one — pin explicitly, never run on an alias — and it lives here. The *canon* is the
files themselves (the workflow's `claude_args`, the agent's frontmatter); there is deliberately
**no registry document listing which agent runs on which model**, because a copy of the config is
exactly the thing that drifts away from it.

`claude-review.yml` несёт `claude_args: |` / `--model claude-opus-5`; frontmatter агента —
`model: claude-opus-5` + `effort: high`.

Четыре факта, без которых пин чинят неправильно:

1. **У экшена нет входа `model`.** `claude_args` — документированный passthrough в Claude CLI
   (`action.yml`: "Additional arguments to pass to Claude CLI"), и это важнее, чем выглядит:
   GitHub Actions **молча игнорирует неизвестный `with:`**, поэтому опечатка в имени входа
   оставила бы ревью неприпиненным при всех зелёных статических проверках.
2. **`effort` по умолчанию наследует уровень сессии** — не `high`. Без пина одна и та же
   plan-стадийная проверка строже или мягче в зависимости от того, чья сессия её запустила;
   пин делает строгость гейта решением репозитория.
3. **PR, правящий сам `claude-review.yml`, получает зелёный джоб без единого комментария** —
   экшен из соображений безопасности не ревьюит собственное определение воркфлоу. Ни контракт
   промпта, ни разрешённая модель на таком PR не наблюдаемы; оба проверяются на следующем
   не-связанном PR (градация в комментарии, строка модели — в транскрипте Actions). Это стоячий
   источник ложной тревоги «ревью сломалось».
4. **Гард отвергает только короткие алиасы** (`opus`/`sonnet`/`haiku`/`fable`) — любой полный id
   проходит. Уведомления «вышла новая модель» нет: ревизия происходит **по красному джобу**, не
   по календарю. Пин **family-level** намеренно: у этого поколения нет датированного snapshot-id,
   поэтому point-release внутри Opus 5 принимается, смена поколения — нет.

**Протухший пин громкий, и это дизайн.** Снятый или опечатанный id — видимая ошибка на каждом PR
(`There's an issue with the selected model (…)` / `Agent terminated early due to an API error`);
Claude Code **не** откатывается молча на сессионную модель. Но резолв модели выше по стеку, и
frontmatter в нём не первый, поэтому пин **не** защищает от трёх вещей:
`CLAUDE_CODE_SUBAGENT_MODEL` в шелле оператора; per-invocation аргумента `model` у Agent-тула —
**единственная из трёх достижима изнутри репо**, `.claude/commands/plan.md` спавнит
`architect-reviewer` именно так, и ничто не мешает передать `model`/`effort` и тихо победить пин;
организационного allowlist'а `availableModels` — при исключении пиннутого значения Claude Code
**молча** пропускает его и берёт унаследованную модель. Записано, чтобы «припинено» не читалось
как более сильная гарантия, чем оно есть.

**Два гарда, один денилист.** `tests/test_claude_review_workflow.py` проверяет воркфлоу,
`tests/test_agent_frontmatter.py` — frontmatter агента, оба импортируют общий набор из
`tests/_model_pin_policy.py`. Это **денилисты**, поэтому объединение строго консервативнее: может
только отвергнуть лишнее, но не пропустить.

### One-time setup

1. Locally: `claude setup-token` (requires Claude Pro/Max subscription) → copy the token.
2. Repo Settings → Secrets and variables → Actions → New repository secret:
   - Name: `CLAUDE_CODE_OAUTH_TOKEN`
   - Value: the token from step 1.
3. The workflow consumes it via `${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}` passed as the action's `claude_code_oauth_token` input (separate from `anthropic_api_key`; OAuth tokens do not work as API keys).

The workflow also needs `id-token: write` in `permissions:` — `anthropics/claude-code-action@v1` uses OIDC for GitHub App auth, and without that scope every run fails with "Could not fetch an OIDC token".

No separate Anthropic API billing — usage counts against the Pro/Max subscription quota.

## Production workflow (`run-script.yml`)

Прод-крон засчитан как **E2E-smoke гейт** в [`principles.md`](principles.md) §Quality Gates — это
единственная сторона прод-воркфлоу, отвечающая на вопрос этого файла. Расписание, порядок шагов,
собственный `pytest` smoke-gate воркфлоу, изоляция падений и алертинг — один дом,
[`operations.md` § Production workflow](operations.md#production-workflow-run-scriptyml).

## Consciously not adopted

**Что попадает сюда:** «не взяли инструмент или правило Y» — и только инструмент целиком,
у которого нет своей секции-гейта выше (иначе строкой по месту гейта). Остальные ветки маршрута
«куда идёт решение» — [`project-map.md`](project-map.md) §Canonical-home, там его канон.

- **`pre-commit` (#255) — no-go.** **Root reason:** каждый хук пинит версию тула через `rev:` и
  запускает его в **изолированном venv** — это второй источник версии тула помимо
  `requirements-dev.txt` (сегодня `python -m ruff`/`mypy` берут единственную залоченную версию),
  то есть системный возврат того же local↔CI drift-класса (#153). Резкая иллюстрация —
  `mypy`: его изолированный
  venv не видит зависимостей проекта, вынуждая держать `additional_dependencies:` —
  скопированный руками дубль набора зависимостей вне `requirements.txt`. **Ловушка частичной
  миграции:** file-линтеры в `pre-commit`, остальные гейты скриптами ⇒ две пересекающиеся
  системы и **трёхсторонний** parity (`pre-commit` config ↔ `CHECKS` ↔ `ci.yml`), чья третья
  грань **негардится** — рост поверхности вместо выигрыша. Половина проверок вообще не
  file-линтеры (`requirements`, `imports` — своя логика), под `pre-commit` они остались бы
  скриптами в `local`-хуках с нулевым выигрышем. **Revisit (wait-for-pain):** частичный
  `pre-commit` только для file-линтеров — *iff* появится реальная контрибьюторская боль от
  ручного управления версиями хуков.
- **`tox`/`nox` (#255) — no.** Решают матрицу **версий Python**; проект прибит к одной 3.12.
  **Revisit:** появится настоящее требование мульти-версионной матрицы.
- **Spec Kit (#114) — снят.** Его роль — спека → план → таски — покрыта локальным
  `/plan #N` → `/implement #N` (`.claude/commands/`), который живёт в репо, гейтится
  `scripts/validate_issue_sections.py` и держит план в теле issue, а не в отдельном дереве
  артефактов. Плата за внешний фреймворк — `/speckit-*`-команды и spec-файлы поверх того же
  контракта. **Revisit:** появится потребность, которой локальный flow не покрывает.
