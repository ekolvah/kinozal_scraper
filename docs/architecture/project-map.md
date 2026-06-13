# Project map — где живёт знание и какой файл на какой вопрос отвечает

**На какой вопрос отвечает этот файл:** «какой файл проекта на какой вопрос отвечает» (индекс)
**и** «где должно жить какое знание» (IA-policy: tier-модель + canonical-home правило). Это две
половины одного концерна — информационной архитектуры репозитория. **Не добавляй сюда контент,
не отвечающий на этот вопрос** (детали кода — в deep-dive `docs/architecture/*`; принципы — в
`principles.md`).

**Это индекс, не контент.** Одна строка на файл; содержимое самих файлов сюда не копируется
(иначе индекс станет ещё одним источником рассинхрона). Где факт дублируется сегодня — см.
раздел [«Известные дубли»](#известные-дубли-и-источник-истины).

## IA-policy: где живёт знание

### Tier-модель носителей знания (официальная, Claude Code)

Claude Code задаёт не имена `docs/*` (их стандарт не регламентирует — переименовывать не к чему),
а **иерархию носителей знания**:

| Tier | Назначение | Когда загружается |
|---|---|---|
| `CLAUDE.md` (root) | Тонкий роутер: что за app + env-граблии + указатели. **Цель < 200 строк** | каждую сессию, целиком |
| `.claude/rules/*.md` | Операционные инструкции, **один файл = одна тема**; можно path-scoped через frontmatter `paths:` | каждую сессию (или только при работе с matching-путями) |
| `.claude/commands/`, `.claude/agents/`, `.claude/settings*.json` | Команды (`/plan`,`/implement`) / сабагенты / permissions-deny | по вызову / при старте |
| `docs/architecture/*.md` | Reference: как устроен код (runtime/pipeline/storage/gemini/…) + этот project-map + `principles.md` | по требованию |
| `~/.claude/projects/<repo>/memory/` | Auto-memory: **только машинно/процессно-специфичное** (см. ниже) | `MEMORY.md` индекс — каждую сессию |

**Честно про токены.** Файлы в `.claude/rules/` *без* `paths:` грузятся каждую сессию ровно как
`CLAUDE.md` — это **не** «меньше токенов сходу». Выигрыш в: (a) **дедупе** (правило живёт в одном
месте), (b) **single-responsibility**, (c) **path-scoping** (`paths: [tests/**]` не грузится, когда
тесты не трогаем — единственный токен-позитивный случай).

### Canonical-home правило

> **У каждого факта — ровно один дом. Прочие упоминания — только ссылка, никогда не перефраз.**

- **Операционные процедурные правила** (workflow) → в `.claude/rules/` **целиком — правило и
  rationale вместе**, не расщепляются (расщепление само воссоздаёт дубль). Старое место → указатель.
- **Формулировки принципов §I–VI** → канон в [`principles.md`](principles.md), ссылка по номеру
  (`architect-reviewer.md`, `implement.md`); **нумерацию не трогать**.
- **Энфорс-факты** (git-запреты) → канон = `.claude/settings.json` `permissions.deny` (+ синхрон-тест
  `tests/test_settings_deny.py`). **Mirror-файлов не создавать** — дубль по определению.
- `.claude/rules/`-файл **не** содержит перефраз принципа или строки deny — только ссылку либо
  процедуру, которой больше нигде нет.

**Граница энфорсится человеком на ревью.** `grep` ловит лишь дословные копии, не семантический
перефраз; при переносе правила ревьюер проверяет, что в старом месте осталась **ссылка, а не
пересказ**. Скрипт-детектор семантических дублей сознательно **не строим** — он дал бы ложное
чувство покрытия (нарушение §IV: зелёный детектор, пропускающий перефраз, хуже честного «проверяет человек»).

### Конвенция-заголовков (header = канон, карта = производный индекс)

Каждый картируемый файл несёт **header** с единственным вопросом, на который он отвечает:
docstring для `.py`, верхняя строка-шапка для `.md`. Header — **канонический** ответ (живёт с
файлом, виден при редактировании — там, где соблазн подмешать чужое). Раздел [«Карта файлов»](#карта-файлов)
ниже — **производный навигационный индекс**; при дрейфе **header wins**. Авто-генерация карты из
заголовков + drift-чек в `ci_check` (паттерн `gen_test_coverage.py`) — отдельный follow-up, не
человеко-поддерживаемый навсегда.

### Memory ↔ repo: resolved-policy

**Проектные инструкции живут в репозитории** (`.claude/`, `docs/`, скрипты, шаблоны), а не в
приватной out-of-repo Claude-памяти. Out-of-repo память — **только** для машинно/окружение-специфичного
или стиля работы с конкретным оператором; иначе при клоне на другой машине проектное знание не
видно → источник истины расщепляется. Это **действующая политика, не backlog**: персона
`architect-review` раньше жила в памяти, её перенесли в репо (`.claude/agents/architect-reviewer.md`
+ гейт `validate_issue_sections.py` + `principles.md §Governance`), память удалили (#150).

## Карта файлов

### `.claude/` и корневые инструкции

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `~/.claude/CLAUDE.md` (глоб., вне репо) | Кросс-проектное (generic mindset для не-репо проектов). Repo-зеркало операционного mindset = `.claude/rules/mindset.md` | ✅ |
| `CLAUDE.md` (проект) | Микс: что делает app + Windows-граблии + резюме PR-workflow + индекс arch-доков | ❌ kitchen-sink |
| `.claude/rules/workflow.md` | Процедурные правила workflow (ветка/PR-дисциплина/labels/plan→implement/гейты) — канон, always-load | ✅ |
| `.claude/rules/testing.md` | Операционный чеклист написания тестов (RED-first/doubles/уровень/ci_check) — path-scoped `tests/**`, ссылается на §I/§II | ✅ |
| `.claude/rules/mindset.md` | Операционный mindset main-сессии: токен-тактики (чтение/spawn/TodoWrite) + указатели на цель-функцию (`architect-reviewer.md`)/§I,§IV,§V/workflow — always-load | ✅ |
| `.claude/commands/plan.md` | Как структурировать issue-body под 7 required секций (вкл. architect-review) | ✅ |
| `.claude/commands/implement.md` | Как исполнить issue с TDD red-green (10 шагов + запреты) | ✅ |
| `.claude/agents/architect-reviewer.md` | Персона ревьюера плана + что проверять + формат findings | ✅ |
| `.claude/settings.json` | Что запрещено агенту (`permissions.deny`) — источник истины запретов, трекается | ✅ |
| `.claude/settings.local.json` (gitignored) | Личный режим + permissions (defaultMode, allow: WebFetch/Skill) | ✅ (gitignored, личный) |

### `docs/architecture/`

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `principles.md` | Микс: §I–VI принципы (часть — RUNTIME: §III Delivery, §IV Visibility) + Quality Gates + Governance (workflow делегирован в `.claude/rules/workflow.md`) | ❌ runtime-принципы + dev-process вместе |
| `project-map.md` (этот файл) | Какой файл на какой вопрос отвечает + где живёт какое знание (IA-policy) + карта дублей | ✅ |
| `runtime.md` | Какие пайплайны / Protocols / data-flow | ✅ |
| `pipeline.md` | Слои, контракты `extract_from_*`, `NormalizedItem` | ✅ |
| `storage.md` | Storage Protocol, DI, row-schema, инварианты колонок | ✅ |
| `testing.md` | Как гарантируем качество: уровни тестов, что мокать (ссылается на `principles.md §II`, не дублирует) | ✅ |
| `test-coverage.md` | Микс: bug-taxonomy + autogen-инвентарь тестов | ❌ taxonomy дублирует testing.md (дубль #5) |
| `ci.md` | Микс: local/CI-гейты (dev-process) + production env-vars (runtime) | ❌ |
| `gemini.md` | Gemini: model rotation / quota / retry / prompts | ✅ |

### Скрипты и шаблоны процесса

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

### Исходники проекта

Реализация рантайма. Детали слоёв/протоколов — в deep-dive-доках колонки «Подробнее».
Тесты (`tests/`) и хелперы здесь не перечисляем.

| Файл | На какой вопрос отвечает | Подробнее |
|---|---|---|
| `generic_pipeline.py` | Общий слой пайплайна: `NormalizedItem`, `PipelineResult`, `extract_from_*` | `pipeline.md` |
| `kinozal_pipeline.py` | Извлечение/нормализация топа kinozal.tv + обогащение трейлером (`run_kinozal_pipeline`) | `pipeline.md` |
| `steam_pipeline.py` | Steam charts + appdetails + перевод (`run_steam_pipeline`) | `pipeline.md` |
| `events_pipeline.py` | Пайплайн событий / sold-out (`run_events_pipeline`) | `pipeline.md` |
| `json_pipeline.py` | Generic JSON-источники (`run_json_pipeline`) | `pipeline.md` |
| `github_trending_pipeline.py` | GitHub Trending + stars-today (`run_github_trending_pipeline`) | `pipeline.md` |
| `pipeline_config.py` | Fail-fast валидация `sources.json` + макросы (`validate_sources_config`) | `principles.md §VI` |
| `sheets_storage.py` | Storage Protocol: Google Sheets + `InMemoryStorage`, дедуп/row-schema | `storage.md` |
| `telegram_notifier.py` | Notifier Protocol: отправка в Telegram + `InMemoryNotifier` | `runtime.md` |
| `gemini_enricher.py` | Enricher Protocol через Gemini: rotation / quota / retry | `gemini.md` |
| `telegram_summarizer.py` | Доставка результатов суммаризации + technical-alert маркер | `runtime.md` |
| `TelegramChannelSummarizer.py` | Чтение Telegram-каналов (Telethon) + суммаризация через Gemini | `gemini.md` |
| `youtube.py` | Поиск YouTube-трейлера (`Youtube`) | `pipeline.md` |
| `text_utils.py` | Матч названия+года (`title_year_matches`) | — |
| `crypto.py` | Шифрование/дешифрование секретов (`encrypt_bytes`/`decrypt_bytes`) | — |

## Известные дубли и источник истины

Backlog де-дупликации. Severity: 🔴 высокая (включая реальные баги-рассинхроны), 🟡 средняя,
🟢 терпимая.

| # | Факт | Где продублирован | Канонический источник | Сев. |
|---|---|---|---|---|
| 1 | Набор required-секций issue | `validate_issue_sections.py` (tuple) + `feature.yml` + `bug.yml` + `principles.md` проза + ~~`CLAUDE.md` проза~~ (убрана #159) | скрипт (tuple); остальное → ссылка | 🔴 частично |
| 2 | Набор CI-проверок | `ci_check.py` vs `ci.yml` (нет coverage-drift!) vs `ci.md` («mirrors exactly» — ложь) | `ci_check.py` (**баг-рассинхрон → #153**) | 🔴 |
| 3 | ~~Dev-workflow правила (ветка/no-main-push/no-self-merge/one-PR/labels/plan→implement)~~ | ✅ закрыт #159: канон = `.claude/rules/workflow.md` (always-load); `principles.md §Dev Workflow` и `CLAUDE.md §PR Workflow` → указатели; §Governance легализует делегирование | `.claude/rules/workflow.md` | ✅ |
| 4 | ~~Test-First + «no mocks of internal»~~ | ✅ закрыт #160: канон = `principles.md §II`; `testing.md` → указатель «Canon: §II» (не перефраз); операционный чеклист → `.claude/rules/testing.md` (path-scoped) | `principles.md §II` | ✅ |
| 5 | Bug taxonomy | `test-coverage.md` ссылается на `testing.md#bug-taxonomy` как канон (keyed, без колонки Examples — не перефраз) | `testing.md` | ✅ resolved-by-link |
| 6 | `codex-` префикс ветки | `new_branch.py` + `ci.yml` trigger + `principles.md` + `CLAUDE.md` | конфиг/скрипт | 🟡 |
| 7 | `_EXCLUDE_DIRS` (mypy) | `ci_check.py` (вкл. `.audit-tmp`) vs `ci.yml` (без) — mismatch | единый источник (**→ #153**) | 🟡 |
| 8 | Data-flow диаграмма | `runtime.md` ≈ `pipeline.md` | runtime=обзор, pipeline=деталь (терпимо) | 🟢 |
| 9 | ~~Запреты git (push-main/force/no-verify/pr-merge/reset/branch-D)~~ | ✅ закрыт #154: `settings.json` (трекаемый) — источник истины; `tests/test_settings_deny.py` синхронит с `implement.md` | `settings.json` (энфорс) | ✅ |
| 10 | `pip-compile` в том же коммите | дважды внутри одного `CLAUDE.md` | один раз | 🟢 |
| 11 | ~~Операционный mindset / персона (приоритеты + токен-тактики)~~ | ✅ закрыт #161: канон персоны/цель-функции = `architect-reviewer.md`; токен-тактики → `.claude/rules/mindset.md` (репо, always-load); глоб. `~/.claude/CLAUDE.md` подрезается отдельным ручным шагом | `architect-reviewer.md` (персона) + `mindset.md` (тактики) | ✅ |
