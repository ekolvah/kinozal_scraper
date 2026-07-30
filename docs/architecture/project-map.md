# Project map — где живёт знание и какой файл на какой вопрос отвечает

**На какой вопрос отвечает этот файл:** «какой файл проекта на какой вопрос отвечает» (индекс)
**и** «где должно жить какое знание» (IA-policy: tier-модель + canonical-home правило). Это две
половины одного концерна — информационной архитектуры репозитория. **Не добавляй сюда контент,
не отвечающий на этот вопрос** (детали кода — в deep-dive `docs/architecture/*`; принципы — в
`principles.md`).

**Это индекс, не контент.** Одна строка на файл; содержимое самих файлов сюда не копируется
(иначе индекс станет ещё одним источником рассинхрона). Единственное отступление — `docs/adr/`:
он индексируется **строкой на каталог**, per-record строк здесь нет. Каталог растёт по записи на
решение, и per-file индекс разошёлся бы с реальностью на первой же новой записи; навигация внутри
каталога — по номеру записи, который и есть её адрес.

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
| `docs/adr/*.md` | Explanation: почему решение принято именно такое и какие альтернативы отвергнуты (MADR 4.0.0, append-only) | по ссылке из state-дока |
| `~/.claude/projects/<repo>/memory/` | Auto-memory: **только машинно/процессно-специфичное** (см. ниже) | `MEMORY.md` индекс — каждую сессию |

**Честно про токены.** Файлы в `.claude/rules/` *без* `paths:` грузятся каждую сессию ровно как
`CLAUDE.md` — это **не** «меньше токенов сходу». Выигрыш в: (a) **дедупе** (правило живёт в одном
месте), (b) **single-responsibility**, (c) **path-scoping** (`paths: [tests/**]` не грузится, когда
тесты не трогаем — единственный токен-позитивный случай).

Суммарный размер этой безусловной платы загейчен `tests/test_always_load_budget.py` (#375):
порог — храповик, чтобы рост шёл осознанной правкой на ревью, а не молчаливым дрейфом (так
бюджет вырос на ~3.8 КБ в #416/#417). Чего гейт **не** ловит — ledger-запись **AB** в
[`coverage-gaps.md`](coverage-gaps.md).

### Canonical-home правило

> **У каждого факта — ровно один дом. Прочие упоминания — только ссылка, никогда не перефраз.**

- **Операционные процедурные правила** (workflow) → в `.claude/rules/` **целиком — правило и
  rationale вместе**, не расщепляются (расщепление само воссоздаёт дубль). Старое место → указатель.
  **Rationale ≠ нарратив** (#375): остаётся решение + одна фраза «почему оно до сих пор верно»
  (без неё правило выглядит ритуалом и его снесёт следующий «упрощатель»); уезжает нарратив
  «как мы к этому пришли» — даты, номера коммитов, что поймало конкретное ревью. Дом нарратива —
  тело issue/PR, в правиле остаётся голый `(#N)`. Перед удалением — сверить `gh issue view N`,
  что нарратив там правда есть: часть фактов бывает артефактом сессии.
- **Обоснование решения** («почему выбрано это, а не то, и почему до сих пор верно») → запись
  в репозитории со **стабильным ID**, а state-док на неё ссылается. Куда именно — **первое
  совпадение**: (1) «не покрыли тестом X» → [`coverage-gaps.md`](coverage-gaps.md); (2) «не взяли
  инструмент или правило Y» → [`ci.md` §Consciously not adopted](ci.md#consciously-not-adopted);
  (3) «архитектурное решение с высокой ценой разворота, задевающее несколько модулей или доков» →
  запись MADR в [`docs/adr/`](../adr/); (4) всё остальное записью **не становится** — его дом
  остаётся телом issue/PR. Фильтр (3) — cost-of-change: если архитектурным считать всякое
  решение, архитектурным не является ни одно, и каталог вырождается в свалку того же нарратива.
  Единообразить три дома не пытаемся: ledger по тестам работает как есть. **Почему запись, а не
  `(#N)`:** номер таски — адрес события во внешнем трекере, без тела в репо и без статуса, поэтому
  ссылка на него **вынуждает** пересказывать содержимое рядом с собой; ID записи пересказ снимает
  (обоснование выбора формата и замер — запись `0001` в каталоге).
- **Политика каталога `docs/adr/`** (дом — здесь, потому что она меняется, а принятая запись —
  нет): формат [MADR 4.0.0](../adr/template.md) дословно; имя `NNNN-slug.md`, номер — адрес
  записи; статус — **закрытый набор** `proposed` / `rejected` / `accepted` / `deprecated` /
  `superseded by ADR-NNNN` (апстрим отдаёт `status` свободной строкой, но без закрытого набора не
  выражается append-only-дисциплина); **принятая запись не переписывается** — правятся опечатки и
  битые ссылки, а смена решения выражается новой записью, на которую старая ссылается вперёд;
  ориентир объёма — до ~200 строк, длинный файл вытесняет из контекста то, ради чего его открыли.
  Структуру держит `tests/test_adr_records.py`.
- **Формулировки принципов §I–VII** → канон в [`principles.md`](principles.md), ссылка по номеру
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
дрейфе **header wins**. Практическое следствие для автора header'а: писать его **по факту
содержимого файла**, а не копированием своей строки из «Карты файлов» — копипаста инвертирует
зависимость, а расхождение с картой означает, что править надо карту.

**Форма header'а для `.md` — закрытый набор из двух маркеров** (#421): строка
`**На какой вопрос отвечает этот файл:**` либо её английский эквивалент
`**Question this document answers:**`, на языке самого дока, до первого `## `. Принимаются оба,
потому что репо двуязычен (`principles.md`/`testing.md` англоязычны), и требовать один язык
значило бы гнать churn-дифф с переводом ради формы. Расширение набора — правка **этой** строки,
а не теста под файл.

**Что считается картируемым файлом** (там же, #421): **`.md` под `docs/architecture/` и
`.claude/rules/`** — и это единственное правило скоупа, ничего не отсеивается вторым слоем
поверх него. Два уточнения — почему граница проходит именно так, а не расширение правила:

- **`.claude/agents/*.md` и `.claude/commands/*.md` вне скоупа не «в наказание», а потому что
  header у них уже есть — во frontmatter `description:`.** Требовать от них ещё и строку-маркер
  значило бы держать канон в двух местах. Обратный случай подтверждает границу:
  `.claude/rules/testing.md` frontmatter несёт (`paths:`), но `description:` нет — значит
  строка-маркер для него обязательна, и скоуп это уже обеспечивает.
- **`CLAUDE.md` исключён сознательно**: он тонкий роутер, помеченный в «Карте файлов»
  ❌ kitchen-sink, и требовать от него единственного вопроса значило бы зафиксировать гейтом ту
  роль, от которой его надо освобождать.
- **`docs/adr/` лежит вне `docs/architecture/` именно поэтому.** Запись MADR несёт свою шапку
  (frontmatter `status`/`date` + заголовок решения), и требовать от неё ещё и строки-маркера
  значило бы держать канон в двух местах — тот же аргумент, что для `.claude/agents/`. Каталог
  не остаётся без инварианта: его стережёт `tests/test_adr_records.py` (имя, уникальный номер,
  статус, резолв `superseded by`, обязательные секции).

**Генерировать карту из заголовков мы сознательно НЕ стали** (#164): per-file текст «на какой вопрос
отвечает» дословно совпадал бы с docstring — генерируемая карта была бы второй копией канона
(редундантно с тем, что агент и так читает; статика стареет/жрёт токены; а курируемые суждения
SR ✅/❌ и дубли скрипт всё равно не выводит). Вместо генератора — дешёвый **presence-lint**
(ruff `D100`/`D104`/`D419` в `check_lint`, #253 — раньше bespoke `scripts/check_headers.py`):
каждый source `.py` под `src/` обязан нести непустой module docstring, иначе red. Для исходников карта поэтому несёт не per-file копию вопроса,
а [**роутер уровня концернов**](#исходники-проекта) (концерн → файлы + deep-dive-указатель) —
orientation, которого в per-file docstring нет.

Для `.md` тот же presence загейчен `tests/test_doc_headers.py` (#421) — тестом, а не записью в
`CHECKS`: `test_ci_check.py::TestStepParity` требует parity реестра с `ci.yml`, поэтому запись
обязала бы завести ещё и `--only`-шаг ради статической проверки, которую и так гоняет `pytest`.
Скоуп производен от glob, чтобы следующий arch-док попадал под правило автоматически.

**Presence ≠ correctness.** Lint гарантирует, что docstring *есть* и непуст — но не что он *актуален*:
устаревший, но непустой docstring пройдёт. Ровно так же и `.md`-гард: он гарантирует лишь, что
**есть с чем спорить** о границе файла, — но не что header соответствует содержимому.
Расхождение docstring ↔ реальное назначение ловит человек
на ревью — та же честная §IV-позиция, что и для семантических дублей (зелёный детектор, дающий ложное
покрытие, хуже честного «проверяет человек»).

Ровно та же граница у гарда записей `docs/adr/` (`tests/test_adr_records.py`): он держит структуру —
имя, уникальный номер, статус, резолв `superseded by`, обязательные секции, — но **не** отличает
запись-заготовку с незаполненными `{placeholder}` от настоящей и не судит, достойно ли решение
записи (cost-of-change) и не устарело ли обоснование. Это не пробел покрытия, который стоило бы
закрывать тестом, а тот же класс семантического суждения: детектор здесь дал бы ложное покрытие.

### Что описывает документация: текущее состояние, не история и не идеи

> **`docs/` описывает текущее реализованное состояние продукта и архитектуры — решения как они
> есть сейчас. Это не свалка: знание, которое не является «текущим реализованным состоянием»,
> живёт в своём доме.**

- **Смена решения → правка существующего файла, не добавление нового.** Нужно актуальное описание
  существующих решений, а не changelog: история изменений живёт в git/PR, не в теле дока. Два файла
  про «как было» и «как стало» = гарантированный рассинхрон.
- **Обоснование решения → запись со стабильным ID, не абзац в state-доке.** Запрет нарратива без
  дома для rationale не работает — это измерено (ADR-0001: 174 повествовательных упоминания `#N`
  из 300). Маршрут — §Canonical-home выше; в state-доке остаётся решение, одна фраза «почему оно
  до сих пор верно» и ссылка.
- **Идеи, задачи, roadmap, нереализованные инициативы → GitHub issues** (они переживают переезд на
  другую машину так же, как репо — это и есть их durable-дом). Прецедент: #188 — попытку положить
  roadmap трейлер-инициативы в `docs/initiatives/` отклонили; scope распределён по issue #138–#145.

Существующие подсекции — **инстансы** этого зонтика, не отдельные правила: машинно/окружение-специфичное
→ out-of-repo память ([«Memory ↔ repo»](#memory--repo-resolved-policy) ниже); бэклог/статус-трекер
→ issues (остаточный долг — [#177](https://github.com/ekolvah/kinozal_scraper/issues/177), см. конец
файла). Каждый — частный случай «то, что не текущее-реализованное-состояние, живёт не в `docs/`».

### Memory ↔ repo: resolved-policy

Инстанс зонтика [«Что описывает документация»](#что-описывает-документация-текущее-состояние-не-история-и-не-идеи)
(машинно-специфичное → не `docs/`). **Проектные инструкции живут в репозитории** (`.claude/`, `docs/`,
скрипты, шаблоны), а не в приватной out-of-repo Claude-памяти. Out-of-repo память — **только** для машинно/окружение-специфичного
или стиля работы с конкретным оператором; иначе при клоне на другой машине проектное знание не
видно → источник истины расщепляется. Это **действующая политика, не backlog**: персона
`architect-review` раньше жила в памяти, её перенесли в репо (`.claude/agents/architect-reviewer.md`
+ гейт `validate_issue_sections.py` + `principles.md §Governance`), память удалили (#150). Тот же
переезд memory→repo — механика приоритета issue (поле Priority в GitHub Project #1): жила в приватной
памяти, теперь в репо как `scripts/set_issue_priority.py` (зашитые Project/field/option-ID + unit-тесты)
+ правило [`workflow.md`](../../.claude/rules/workflow.md) #11 (агент спрашивает приоритет у пользователя
→ скрипт), память удалена (#351).

**Гейт вместо прозы (#353).** Эта политика — сама была прозой и нарушалась дважды за одну сессию
(process-факты про приоритет и про open_pr link-lag клались в память вместо репо). **Root-cause:**
детерминируемый триггер (запись файла в memory-каталог) при уже существующей hook-инфраструктуре
оставили неэнфорснутым — прямое нарушение `mindset.md` «Скрипты > инструкции». Полностью загейтить
«эта проза должна быть скриптом» нельзя (семантическое суждение, класс semantic-dup — не строим), но
частный случай — запись в `.claude/projects/<slug>/memory/` — гейтится тривиально: `scripts/hooks.py`
(`_is_memory_write` → `memory_write_signal`, PostToolUse exit 2) выдаёт **checkpoint-reminder** «это
машинно/операторо-специфичное? иначе перенеси в репо». Это forcing-function (осознанная точка
решения), **не** семантический классификатор и **не** hard-block: предикат «писал в память» ≠
нарушение «писал процессное знание», поэтому сигнал срабатывает на всех записях (в т.ч. легитимных) —
false-positive-by-design, для редких memory-записей цена промаха низкая. PreToolUse-блокировка,
семантический классификатор и Agent Governance Toolkit осознанно out-of-scope.

## Карта файлов

### `.claude/` и корневые инструкции

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `~/.claude/CLAUDE.md` (глоб., вне репо) | Кросс-проектное (generic mindset для не-репо проектов). Repo-зеркало операционного mindset = `.claude/rules/mindset.md` | ✅ |
| `CLAUDE.md` (проект) | Микс: что делает app + Windows-граблии + резюме PR-workflow + индекс arch-доков | ❌ kitchen-sink |
| `.claude/rules/workflow.md` | Процедурные правила workflow (ветка/PR-дисциплина/labels/plan→implement/гейты) — канон, always-load | ✅ |
| `.claude/rules/testing.md` | Операционный чеклист написания тестов (RED-first/doubles/уровень/ci_check) — path-scoped `tests/**`, ссылается на §I/§II | ✅ |
| `.claude/rules/mindset.md` | Операционный mindset main-сессии: **канон цель-функции** (3 приоритета) + операционные токен-тактики main-сессии + указатели на §I,§IV,§V/workflow — always-load | ✅ |
| `.claude/commands/plan.md` | Как структурировать issue-body под 7 required секций (вкл. architect-review) | ✅ |
| `.claude/commands/implement.md` | Как исполнить issue с TDD red-green (10 шагов + запреты) | ✅ |
| `.claude/agents/architect-reviewer.md` | Персона ревьюера плана + что проверять + формат findings (coverage-first: градация, не фильтрация — #392); цель-функцию **читает из канона** `mindset.md §Цель-функция` (сабагент не грузит always-load rules — читает сам, копии не держит). Модель/`effort` — пин, политика и границы пина в [`ci.md §Model pinning`](ci.md), гард `tests/test_agent_frontmatter.py` | ✅ |
| `.claude/settings.json` | Что запрещено агенту (`permissions.deny`) — источник истины запретов, трекается | ✅ |
| `.claude/settings.local.json` (gitignored) | Личный режим + permissions (defaultMode, allow: WebFetch/Skill) | ✅ (gitignored, личный) |

### `docs/architecture/`

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `principles.md` | Микс: §I–VII принципы (часть — RUNTIME: §III Delivery, §IV Visibility) + Quality Gates + Governance (workflow делегирован в `.claude/rules/workflow.md`) | ❌ runtime-принципы + dev-process вместе |
| `project-map.md` (этот файл) | Какой файл на какой вопрос отвечает + где живёт какое знание (IA-policy) | ✅ |
| `runtime.md` | Что существует в рантайме и как связано: какие пайплайны, какие Protocol-границы, generic data-flow + модули, сознательно обходящие generic-паттерн (Telethon-direct). Широта, не глубина | ✅ |
| `pipeline.md` | Как устроен и ведёт себя **один** прогон: слои извлечения, контракты `extract_from_*` → `NormalizedItem`, «новый источник = конфиг, не код», error policy, шаблоны уведомлений, макросы, трейлеры **и поведение fetch** (HTML source config, mirror-fallback kinozal — #418) | ✅ |
| `storage.md` | Storage Protocol + реализации, DI, EAFP-создание листа и валидация схемы, поиск dedupe-ключа, row-schema, инварианты колонок, порядок записи | ✅ |
| `testing.md` | Как гарантируем качество: уровни тестов, bug-taxonomy, что мокать (ссылается на `principles.md §II`, не дублирует). Стратегия, не исключения | ✅ |
| `coverage-gaps.md` | Где мы сознательно **не** тестируем и почему: ledger `A`…`AB` со стабильными буквенными ID + модули без выделенных тестов. Выселен из `testing.md`, чтобы растущий список исключений не смешивался со стратегией | ✅ |
| `ci.md` | Гейты качества на пути изменения (local pre-commit, `ci.yml`, cloud `claude-review`) + **единственный дом политики модельного пиннинга агентного тулинга** (§Model pinning: обе поверхности — `claude-review.yml` и `.claude/agents/*.md`, границы пина, два гарда). Runtime-половина (env vars, прод-воркфлоу, пробник) выселена в `operations.md` (#418); от прод-крона остался только гейт-фасет (E2E-smoke по `principles.md` §Quality Gates). Обоснования решений свёрнуты до операционного минимума (#419): нарратив «как мы сюда пришли» живёт в issue/PR, здесь — только фраза, без которой агент ошибётся или переделает отвергнутое; отвергнутые инструменты — строкой по месту гейта либо в §Consciously not adopted | ✅ |
| `operations.md` | Как прод-прогон эксплуатируется: расписание и порядок шагов, env-переменные и секреты, изоляция падений (#245) и алертинг (#310), runbook'и оператора (ротация `TELETHON_SESSION`). Принял runtime-половину `ci.md` (#418) | ✅ с оговоркой: временный жилец — пробник #396 (измерительный инструмент, не эксплуатация); уедет вместе с решением по нему, `_EXPIRES` в `scripts/probe.py` не даст забыть |
| `gemini.md` | Gemini: model rotation / quota / retry / prompts / call-observability (token+latency `llm_call`-лог + Phoenix dev-recipe, #145) | ✅ |
| `llm-security.md` | LLM-угрозы enricher'а (OWASP LLM Top 10 → защиты/residual): prompt-injection fence, output-escaping, honest blast radius (#308) | ✅ |

### `docs/adr/`

| Файл | На какой вопрос отвечает | Single-responsibility? |
|---|---|---|
| `docs/adr/` (каталог целиком) | Почему принято именно это решение и что отвергнуто: записи MADR 4.0.0 со стабильным ID `NNNN`, append-only (смена решения = новая запись со `superseded by`). Вход — cost-of-change фильтр (§Canonical-home). `template.md` — шаблон апстрима дословно, гард — `tests/test_adr_records.py` | ✅ |

### Скрипты и шаблоны процесса

| Файл | На какой вопрос отвечает |
|---|---|
| `scripts/validate_issue_sections.py` | Содержит ли issue все 7 required секций (gate `/plan` и `/implement`) |
| `scripts/issue_branch.py` / `scripts/new_branch.py` | Создание ветки `issue-N-*` от свежего origin/main |
| `scripts/set_issue_priority.py` | Выставить приоритет issue (поле Priority в GitHub Project #1) через `gh project item-add`+`item-edit` с зашитыми Project/field/option-ID; вызывается агентом по правилу `workflow.md` #11 (спросил приоритет → скрипт). Механика переехала memory→repo (#351) |
| `scripts/check_red.py` | Действительно ли тесты RED перед GREEN (контракт TDD-шага) |
| `scripts/open_pr.py` | Создание PR с гарантированным `Closes #N` в body + пост-верификация `closingIssuesReferences` (иначе exit 1, §IV): чтобы PR надёжно автозакрывал issue при squash-мёрдже (#320, precedent #319→#140). Pre-flight — делает правый путь дешёвым; enforcement — `verify_pr_link.py` |
| `scripts/verify_pr_link.py` | CI-гейт (workflow `pr-link.yml`): PR из `issue-N` ветки обязан закрывать issue, иначе job red → required check блокирует мёрдж. Отдельный workflow (не `ci.yml`), т.к. триггерится и на `edited` (правка body убирает `Closes #N` → перепроверка), не гоняя тяжёлый `quality` на правку описания. Агент-независимый backstop к `open_pr.py` (переиспользует его чистые функции); enforcement через gate, не прозу (#320) |
| `scripts/ci_check.py` | Локальный pre-commit/pre-push гейт качества (зеркало CI job) |
| `scripts/eval_trailers.py` | Eval-harness подбора трейлера: три скоркарты — `TrailerStrategy` (YouTube-pick) + `evaluate_delivery` (прод-`select_trailer`, то что уходит юзеру, #379) + `evaluate_tmdb` (TMDB-источник) по frozen golden-set (Hit/Wrong/Miss относительно `correct`, офлайн) + `--record`/`--record-tmdb`/`--update-baseline`. **Гейт** — `tests/fixtures/trailer_baseline.json` (пофильмовый исход delivery) через `tests/test_eval_baseline.py`, не через запись в `ci_check` CHECKS. У набора две роли: «найди правильный» (accept-set `correct`) и «не бери чужой» (разметка `trap` — верифицированные чужие кандидаты в пуле, #380); deep-dive `testing.md#eval-harness--trailer-selection-139` (#139, #329, #379, #380) |
| `scripts/eval_summarizer.py` | RAGAS-eval суммаризатора `summary_ru`: faithfulness/answer_relevancy по frozen golden-set вместо regex `response_pattern` (vibe-check формата). Метрика — LLM-судья, поэтому live/API-gated (dev-run, не CI); граница `_evaluate_dataset` мокается, чистые швы под тестом. RAGAS — dev-only dep. Deep-dive `testing.md#eval-harness--summarizer-faithfulness-347` (#347) |
| `scripts/hooks.py` | Session-level `PostToolUse`-хук (`on-edit`): ruff check-only на `*.py` + pip-compile-reminder на `requirements*.in` — мгновенный feedback во время агентной сессии, дополняет `ci_check.py`; deep-dive [`ci.md`](ci.md#session-hooks-scriptshookspy-281) (#281) |
| `.github/workflows/ci.yml` | Quality job на PR/push (должен зеркалить `ci_check.py`) |
| `.importlinter` | §II protocol-boundaries как машинный контракт (гейт `imports` в `ci_check`): направление зависимостей + adapter-no-auth; deep-dive `ci.md` (#234) |

### Исходники проекта

**На какой вопрос отвечает каждый файл — в его module docstring** (канон, JIT при открытии; presence
гарантируется ruff `D100`/`D104`/`D419` в `check_lint`, #253 — bespoke `headers`-скрипт снят там же).
Здесь — только **роутер концерн → файлы** + указатель в deep-dive-док
для orientation, которого в per-file docstring нет. Тесты (`tests/`) и хелперы не перечисляем.

| Концерн | Файлы | Deep-dive |
|---|---|---|
| Слой пайплайна (ядро + контракты) | `src/kinozal_scraper/generic_pipeline.py`, `src/kinozal_scraper/pipeline_config.py` | `pipeline.md` (config → `principles.md §VI`) |
| Extraction/нормализация по источникам | `src/kinozal_scraper/kinozal_pipeline.py`, `src/kinozal_scraper/steam_pipeline.py`, `src/kinozal_scraper/soldout_pipeline.py`, `src/kinozal_scraper/github_popular_pipeline.py`, `src/kinozal_scraper/github_trending_pipeline.py` | `pipeline.md` |
| Boundaries (Protocol-границы наружу) | `src/kinozal_scraper/sheets_storage.py` (storage), `src/kinozal_scraper/telegram_notifier.py` / `src/kinozal_scraper/telegram_summarizer.py` (notify) / `src/kinozal_scraper/alerting.py` (канонический дом operator-alerting: маркер `.run/technical_alert_sent` + `report_failures` per-source failure-алерт, #310), `src/kinozal_scraper/gemini_enricher.py` / `src/kinozal_scraper/TelegramChannelSummarizer.py` (Gemini), `src/kinozal_scraper/llm_observability.py` (shared `llm_call`-breadcrumb обоих live-Gemini call site: `usage_metadata`-токены + latency, §IV-degraded, #145), `src/kinozal_scraper/http_fetch.py` (единый HTML-fetch: curl_cffi + impersonate, обходит Cloudflare TLS-фингерпринт — #217; `describe_block` — per-attempt диагностика анти-бот-блока `cf-ray`/`cf-mitigated`/error-code/`<title>`, #358) | `storage.md` · `runtime.md` · `gemini.md` |
| Подбор трейлера (retrieval → selection) | `src/kinozal_scraper/youtube.py` (retrieval: `search_candidates` — union RU+оригинал → `list[Candidate]`, #140), `src/kinozal_scraper/kinozal_pipeline.py` (`build_film_profile` — richer data-prep `FilmProfile` с details.php для harness, #140; `enrich_with_trailer` — **прод-композиция #144**: облегчённый title+year профиль → `select_trailer` (#379 — общая точка входа прода и замера: `search_candidates` → `HeuristicStrategy` → §IV-маркер), RU-приоритет закрывает #315, Gemini НЕ в hot path), `src/kinozal_scraper/trailer_strategy.py` (selection: `FilmProfile`/`Candidate`/`TrailerPick` + `TrailerStrategy` Protocol + baseline `FirstResultStrategy` #139 + language-aware `HeuristicStrategy` #141), `src/kinozal_scraper/trailer_picker_llm.py` (selection стратегия A: `LLMTrailerStrategy` — Gemini structured-output picker + `GeminiJsonGenerator`, #142), `src/kinozal_scraper/trailer_picker_embeddings.py` (selection стратегия B: `EmbeddingTrailerStrategy` — re-ranker на эмбеддингах, косинус+порог, + `GeminiEmbedder`, #143), `src/kinozal_scraper/tmdb_trailer.py` (альт-источник: TMDB `/movie/{id}/videos` — official+язык из метаданных; чистая `pick_trailer` + `TmdbClient` DI, offline-замер на harness, прод не подключён, #329) | `pipeline.md#trailer-retrieval-and-selection-140-141-144` · `testing.md#eval-harness--trailer-selection-139` |
| Утилиты | `src/kinozal_scraper/text_utils.py` | — |

---

Остаточный открытый долг трекается в [issue #177](https://github.com/ekolvah/kinozal_scraper/issues/177)
(инстанс зонтика [«Что описывает документация»](#что-описывает-документация-текущее-состояние-не-история-и-не-идеи):
бэклог/статус-трекер → issues, не `docs/`); ✅-закрытые пункты — в истории соответствующих PR.
