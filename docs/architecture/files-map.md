# Files map — на какой вопрос отвечает каждый файл

Навигационный/governance индекс процесса агентной разработки. Цель — single
responsibility: один файл = чёткий ответ на один вопрос, без микса concerns и без
дублирования фактов между файлами.

**Это индекс, не контент.** Одна строка на файл; содержимое самих файлов сюда не
копируется (иначе индекс станет ещё одним источником рассинхрона). Где факт дублируется
сегодня — см. раздел [«Известные дубли»](#известные-дубли-и-источник-истины) (backlog
де-дупликации, Phase B).

## `.claude/` и корневые инструкции

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `~/.claude/CLAUDE.md` (глоб., вне репо) | Кто я как агент: 3 стратегических приоритета; когда subagent / скрипт / memory | ✅ |
| `CLAUDE.md` (проект) | Микс: что делает app + Windows-граблии + резюме PR-workflow + индекс arch-доков | ❌ kitchen-sink |
| `.claude/commands/plan.md` | Как структурировать issue-body под 7 required секций (вкл. architect-review) | ✅ |
| `.claude/commands/implement.md` | Как исполнить issue с TDD red-green (10 шагов + запреты) | ✅ |
| `.claude/agents/architect-reviewer.md` | Персона ревьюера плана + что проверять + формат findings | ✅ |
| `.claude/settings.json` | Что запрещено агенту (`permissions.deny`) — источник истины запретов, трекается | ✅ |
| `.claude/settings.local.json` (gitignored) | Личный режим + permissions (defaultMode, allow: WebFetch/Skill) | ✅ (gitignored, личный) |

## `docs/architecture/`

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `principles.md` | Микс: §I–VI принципы (часть — RUNTIME: §III Delivery, §IV Visibility) + Dev Workflow + Quality Gates + Governance | ❌ runtime-принципы + dev-process вместе |
| `runtime.md` | Какие пайплайны / Protocols / data-flow | ✅ |
| `pipeline.md` | Слои, контракты `extract_from_*`, `NormalizedItem` | ✅ |
| `storage.md` | Storage Protocol, DI, row-schema, инварианты колонок | ✅ |
| `testing.md` | Как гарантируем качество: уровни тестов, что мокать | ⚠️ дублирует principles §I/§II (дубль #4) |
| `test-coverage.md` | Микс: bug-taxonomy + autogen-инвентарь тестов | ❌ taxonomy дублирует testing.md (дубль #5) |
| `ci.md` | Микс: local/CI-гейты (dev-process) + production env-vars (runtime) | ❌ |
| `gemini.md` | Gemini: model rotation / quota / retry / prompts | ✅ |
| `files-map.md` (этот файл) | На какой вопрос отвечает каждый файл процесса + карта дублей | ✅ |

## Скрипты и шаблоны процесса

| Файл | На какой вопрос отвечает |
|---|---|
| `scripts/validate_issue_sections.py` | Содержит ли issue все 7 required секций (gate `/plan` и `/implement`) |
| `scripts/issue_branch.py` / `scripts/new_branch.py` | Создание ветки `codex-issue-N-*` от свежего origin/main |
| `scripts/check_red.py` | Действительно ли тесты RED перед GREEN (контракт TDD-шага) |
| `scripts/ci_check.py` | Локальный pre-commit/pre-push гейт качества (зеркало CI job) |
| `scripts/gen_test_coverage.py` | Генерация `test-coverage.md` (защита от drift) |
| `.github/ISSUE_TEMPLATE/{feature,bug}.yml` | Структура нового issue (включая поле Architect review) |
| `.github/ISSUE_TEMPLATE/config.yml` | Конфиг чузера шаблонов issue |
| `.github/workflows/ci.yml` | Quality job на PR/push (должен зеркалить `ci_check.py`) |

## Известные дубли и источник истины

Backlog де-дупликации (Phase B — отдельные follow-up issue). Severity: 🔴 высокая
(включая реальные баги-рассинхроны), 🟡 средняя, 🟢 терпимая.

| # | Факт | Где продублирован | Канонический источник | Сев. |
|---|---|---|---|---|
| 1 | Набор required-секций issue | `validate_issue_sections.py` (tuple) + `feature.yml` + `bug.yml` + `principles.md` проза + `CLAUDE.md` проза | скрипт (tuple); остальное → ссылка | 🔴 |
| 2 | Набор CI-проверок | `ci_check.py` vs `ci.yml` (нет coverage-drift!) vs `ci.md` («mirrors exactly» — ложь) | `ci_check.py` (**баг-рассинхрон → #153**) | 🔴 |
| 3 | Dev-workflow правила (ветка/no-main-push/no-self-merge/one-PR/labels/plan→implement) | `CLAUDE.md` ≈ слово-в-слово `principles.md §Dev Workflow` | один источник, второй → ссылка | 🔴 |
| 4 | Test-First + «no mocks of internal» | `principles.md §I/§II` ≈ `testing.md` | `principles.md` | 🟡 |
| 5 | Bug taxonomy | `testing.md` ≈ `test-coverage.md` | `testing.md`; coverage.md → только инвентарь | 🟡 |
| 6 | `codex-` префикс ветки | `new_branch.py` + `ci.yml` trigger + `principles.md` + `CLAUDE.md` | конфиг/скрипт | 🟡 |
| 7 | `_EXCLUDE_DIRS` (mypy) | `ci_check.py` (вкл. `.audit-tmp`) vs `ci.yml` (без) — mismatch | единый источник (**→ #153**) | 🟡 |
| 8 | Data-flow диаграмма | `runtime.md` ≈ `pipeline.md` | runtime=обзор, pipeline=деталь (терпимо) | 🟢 |
| 9 | ~~Запреты git (push-main/force/no-verify/pr-merge/reset/branch-D)~~ | ✅ закрыт #154: `settings.json` (трекаемый) — источник истины; `tests/test_settings_deny.py` синхронит с `implement.md` | `settings.json` (энфорс) | ✅ |
| 10 | `pip-compile` в том же коммите | дважды внутри одного `CLAUDE.md` | один раз | 🟢 |
