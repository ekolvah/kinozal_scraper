# Project map — где живёт знание и какой файл на какой вопрос отвечает

**На какой вопрос отвечает этот файл:** «какой файл проекта на какой вопрос отвечает» (индекс)
**и** «где должно жить какое знание» (IA-policy: tier-модель + canonical-home правило). Это две
половины одного концерна — информационной архитектуры репозитория. **Не добавляй сюда контент,
не отвечающий на этот вопрос** (детали кода — в deep-dive `docs/architecture/*`; принципы — в
`principles.md`).

**Это индекс, не контент.** Одна строка на файл; содержимое самих файлов сюда не копируется
(иначе индекс станет ещё одним источником рассинхрона).

## IA-policy: где живёт знание

### Два слоя графа: навигация (дерево) vs ссылки (не дерево)

ИА репозитория — **не** «звезда» и **не** одно дерево, а два намеренно разных слоя; смешение их
в одной картинке и создаёт ложное ощущение звезды:

- **Containment (навигация)** — оглавление, по которому спускаешься: `CLAUDE.md` → `project-map.md`
  (этот файл — полный индекс «файл → вопрос») → конкретные доки/исходники. Слой **древовидный,
  одно-родительский**: полный перечень файлов живёт только здесь; `CLAUDE.md` на него **ссылается,
  а не дублирует**.
- **Reference (canonical-home ссылки)** — кто на чей канон-факт ссылается (`§II`, `#bug-taxonomy`,
  `permissions.deny`). Слой **намеренно НЕ древовидный**: один факт нужен из нескольких контекстов
  (напр. `principles.md §II` — из `testing.md`, `.claude/rules/testing.md`, `architect-reviewer.md`,
  `implement.md`), поэтому keyed-ссылки идут «вверх/вбок». Сделать их деревом нельзя — пришлось бы
  либо дублировать факт в каждую ветку (перефраз-дрейф, нарушение canonical-home), либо лишить
  потребителя указателя на канон.

Ребро `principles.md ↔ project-map.md` **двунаправленное намеренно** (principles делегирует IA-policy
сюда; этот файл описывает tier принципов) — это не цикл-ошибка.

### Tier-модель носителей знания (официальная, Claude Code)

Claude Code задаёт не имена `docs/*` (их стандарт не регламентирует — переименовывать не к чему),
а **иерархию носителей знания**:

| Tier | Назначение | Когда загружается |
|---|---|---|
| `CLAUDE.md` (root) | Тонкий роутер: что за app + env-граблии + указатели. **Цель < 200 строк** | каждую сессию, целиком |
| `.claude/rules/*.md` | Операционные инструкции, **один файл = одна тема**; можно path-scoped через frontmatter `paths:` | каждую сессию (или только при работе с matching-путями) |
| `.claude/commands/`, `.claude/agents/`, `.claude/settings*.json` | Команды (`/plan`,`/implement`) / сабагенты / permissions-deny | по вызову / при старте |
| `docs/architecture/*.md` | Reference: как устроен код (runtime/pipeline/storage/gemini/…) + этот project-map + `principles.md` | по требованию |
| `docs/initiatives/*.md` | Initiative/roadmap: why/scope/граф-зависимостей многошаговой инициативы (не «как устроен код») | по требованию |
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
файлом, виден при редактировании — там, где соблазн подмешать чужое; агент читает его JIT, открывая
файл). Раздел [«Карта файлов»](#карта-файлов) ниже — **производный навигационный индекс**; при
дрейфе **header wins**.

**Генерировать карту из заголовков мы сознательно НЕ стали** (#164): per-file текст «на какой вопрос
отвечает» дословно совпадал бы с docstring — генерируемая карта была бы второй копией канона
(редундантно с тем, что агент и так читает; статика стареет/жрёт токены; а курируемые суждения
SR ✅/❌ и дубли скрипт всё равно не выводит). Вместо генератора — дешёвый **presence-lint**
(`scripts/check_headers.py`, check `headers` в `ci_check`): каждый root source `.py` обязан нести
непустой module docstring, иначе red. Для исходников карта поэтому несёт не per-file копию вопроса,
а [**роутер уровня концернов**](#исходники-проекта) (концерн → файлы + deep-dive-указатель) —
orientation, которого в per-file docstring нет.

**Presence ≠ correctness.** Lint гарантирует, что docstring *есть* и непуст — но не что он *актуален*:
устаревший, но непустой docstring пройдёт. Расхождение docstring ↔ реальное назначение ловит человек
на ревью — та же честная §IV-позиция, что и для семантических дублей (зелёный детектор, дающий ложное
покрытие, хуже честного «проверяет человек»).

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
| `.claude/rules/mindset.md` | Операционный mindset main-сессии: **канон цель-функции** (3 приоритета) + токен-тактики (чтение/spawn/TodoWrite) + указатели на §I,§IV,§V/workflow — always-load | ✅ |
| `.claude/commands/plan.md` | Как структурировать issue-body под 7 required секций (вкл. architect-review) | ✅ |
| `.claude/commands/implement.md` | Как исполнить issue с TDD red-green (10 шагов + запреты) | ✅ |
| `.claude/agents/architect-reviewer.md` | Персона ревьюера плана + что проверять + формат findings; цель-функцию **читает из канона** `mindset.md §Цель-функция` (сабагент не грузит always-load rules — читает сам, копии не держит) | ✅ |
| `.claude/settings.json` | Что запрещено агенту (`permissions.deny`) — источник истины запретов, трекается | ✅ |
| `.claude/settings.local.json` (gitignored) | Личный режим + permissions (defaultMode, allow: WebFetch/Skill) | ✅ (gitignored, личный) |

### `docs/architecture/`

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `principles.md` | Микс: §I–VI принципы (часть — RUNTIME: §III Delivery, §IV Visibility) + Quality Gates + Governance (workflow делегирован в `.claude/rules/workflow.md`) | ❌ runtime-принципы + dev-process вместе |
| `project-map.md` (этот файл) | Какой файл на какой вопрос отвечает + где живёт какое знание (IA-policy) | ✅ |
| `runtime.md` | Какие пайплайны / Protocols / data-flow | ✅ |
| `pipeline.md` | Слои, контракты `extract_from_*`, `NormalizedItem` | ✅ |
| `storage.md` | Storage Protocol, DI, row-schema, инварианты колонок | ✅ |
| `testing.md` | Как гарантируем качество: уровни тестов, что мокать (ссылается на `principles.md §II`, не дублирует) | ✅ |
| `test-coverage.md` | Микс: bug-taxonomy (канон — `testing.md#bug-taxonomy`, keyed-ссылка, не перефраз) + autogen-инвентарь тестов | ❌ две темы |
| `ci.md` | Микс: local/CI-гейты (dev-process) + production env-vars (runtime) | ❌ |
| `gemini.md` | Gemini: model rotation / quota / retry / prompts | ✅ |

### `docs/initiatives/`

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `trailer-ai.md` | Why/scope инициативы «AI-улучшение подбора трейлера» + сквозные решения + 2 root-cause-дефекта + **канон графа зависимостей** roadmap (issue #138–#145) | ✅ |

### Скрипты и шаблоны процесса

| Файл | На какой вопрос отвечает |
|---|---|
| `scripts/validate_issue_sections.py` | Содержит ли issue все 7 required секций (gate `/plan` и `/implement`) |
| `scripts/issue_branch.py` / `scripts/new_branch.py` | Создание ветки `issue-N-*` от свежего origin/main |
| `scripts/check_red.py` | Действительно ли тесты RED перед GREEN (контракт TDD-шага) |
| `scripts/ci_check.py` | Локальный pre-commit/pre-push гейт качества (зеркало CI job) |
| `scripts/gen_test_coverage.py` | Генерация `test-coverage.md` (защита от drift) |
| `.github/workflows/ci.yml` | Quality job на PR/push (должен зеркалить `ci_check.py`) |

### Исходники проекта

**На какой вопрос отвечает каждый файл — в его module docstring** (канон, JIT при открытии; presence
гарантируется `headers`-lint). Здесь — только **роутер концерн → файлы** + указатель в deep-dive-док
для orientation, которого в per-file docstring нет. Тесты (`tests/`) и хелперы не перечисляем.

| Концерн | Файлы | Deep-dive |
|---|---|---|
| Слой пайплайна (ядро + контракты) | `generic_pipeline.py`, `pipeline_config.py` | `pipeline.md` (config → `principles.md §VI`) |
| Extraction/нормализация по источникам | `kinozal_pipeline.py`, `steam_pipeline.py`, `events_pipeline.py`, `json_pipeline.py`, `github_trending_pipeline.py` | `pipeline.md` |
| Boundaries (Protocol-границы наружу) | `sheets_storage.py` (storage), `telegram_notifier.py` / `telegram_summarizer.py` (notify), `gemini_enricher.py` / `TelegramChannelSummarizer.py` (Gemini) | `storage.md` · `runtime.md` · `gemini.md` |
| Утилиты | `youtube.py`, `text_utils.py`, `crypto.py` | — |

---

Бэклог дедупликации (статус-трекер) здесь больше не живёт — это IA-индекс, не доска задач.
Остаточный открытый долг трекается в [issue #177](https://github.com/ekolvah/kinozal_scraper/issues/177);
✅-закрытые пункты — в истории соответствующих PR.
