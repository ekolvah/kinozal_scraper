# Initiative: AI-улучшение подбора YouTube-трейлера (eval-driven, на Gemini)

**На какой вопрос отвечает этот файл:** зачем затеяна инициатива «умный подбор трейлера», какие
сквозные решения по ней приняты, какие root-cause-дефекты она обязана починить и в каком порядке
идут её 8 issue (#138–#145). Это **initiative/roadmap-носитель** (why/scope/граф зависимостей),
не «как устроен код» — деталям кода место в `docs/architecture/*`, детализации шагов — в самих issue.

## Цель

Превратить `kinozal_scraper` в **учебную песочницу AI Engineering / Data Preparation / LLMOps** на
реальной, а не выдуманной задаче. Рабочая боль-носитель: к новинке с kinozal прикладывается трейлер
с YouTube, и часто **прилетает чужой трейлер** — ролик с похожим названием про другое кино. Это
точка, где AI даёт реальную пользу, а не «фича ради учёбы».

Подход — **eval-driven**: сначала golden-set + harness + честный baseline, затем стратегии
сравниваются числами на golden-set, победитель идёт в прод. Исходно рассматривались две абстрактные
фичи (Smart Entity Extractor, Eval-driven Summarizer) — отброшены, т.к. плохо ложились на простой
корпус; переориентировались на трейлеры.

## Сквозные решения (приняты с владельцем + после architect-review)

- **Провайдер — Gemini**: реюз `RotatingGeminiEnricher` / `gemini_enricher.py` (ротация моделей,
  квоты, исключения `QuotaExhausted/ModelUnavailable/TryNextModel`).
- **Суммаризация-фича отброшена** — низкая польза на простом корпусе.
- **Embeddings (стратегия B) — прод-кандидат**, не только учебный замер: если выиграет golden-set,
  может пойти в прод (решение владельца, вопреки совету архитектора оставить лишь как учебный замер).
- **Observability — учебный optional-issue** (#145), не блокирует прод (вопреки совету выбросить).
- **Векторная БД** (Chroma/Qdrant/pgvector) — out of scope, отдельная будущая задача; косинус
  считаем в памяти.
- **Дешёвый детерминированный пред-фильтр + LLM только на спорных** — главная экономия
  рантайм-токенов: cron крутится в 04:00 UTC, общая Gemini-квота уже выедается основным
  summary-enrichment'ом. LLM при затруднении возвращает честный `None` (лучше без трейлера, чем
  чужой — §IV).

## Root-cause-дефекты, которые инициатива обязана починить

Не обойти workaround'ом — починить по корню (§V). Verified 2026-06-16: оба ещё в коде, обе issue OPEN.

- **§IV silent-skip** в `enrich_with_trailer` (`kinozal_pipeline.py:110,116` — docstring
  `Returns '' on any failure` поверх `try/except`). Сейчас «нет трейлера» и «сбой YouTube/Gemini»
  неотличимы — оба дают пустоту. Нужен видимый маркер + WARNING-лог. Чинится в #138.
- **§II mock-of-internal-logic**: golden-кейс `test_2026_film_skips_2015_kingsman_trailer`
  (`tests/test_kinozal_pipeline.py:165`) гоняет `_FilteringFakeYoutube`
  (`tests/test_kinozal_pipeline.py:62,70`), который **дублирует** `title_year_matches`, а не реальный
  путь — на нём нельзя строить honest baseline. Инвертируется на реальную стратегию в #139.

## Roadmap — граф зависимостей (канон здесь)

**Граф зависимостей roadmap — канон в этом файле.** Issue-тела #138–#145 несут только свою
**локальную** зависимость («блокируется #N») и ссылаются сюда за общей картиной — целиком граф в
issue не дублируется (canonical-home: один дом у факта, иначе дрейф, который ловит только человек).

Порядок: **#138 → #139 → #140 → #141 → (#142 ∥ #143) → #144 → #145**

| Issue | Шаг | Навык | Зависит от |
|---|---|---|---|
| [#138](https://github.com/ekolvah/kinozal_scraper/issues/138) | Оригинальное название в YouTube-запрос + §IV видимый маркер ⟵ первый PR, польза сразу | root-cause-фикс | — |
| [#139](https://github.com/ekolvah/kinozal_scraper/issues/139) | Eval-фундамент: golden-set + harness + baseline + `TrailerStrategy` Protocol | Evals (EDD) | #138 |
| [#140](https://github.com/ekolvah/kinozal_scraper/issues/140) | Data prep: метаданные фильма (`/details.php`) + список кандидатов | Data Preparation | #139 |
| [#141](https://github.com/ekolvah/kinozal_scraper/issues/141) | Детерминированный пред-фильтр (без LLM) ⟵ основная экономия токенов | дешёвая эвристика-baseline | #140 |
| [#142](https://github.com/ekolvah/kinozal_scraper/issues/142) | Стратегия A: LLM-picker (Structured Outputs, Gemini) | Structured Outputs | #141 |
| [#143](https://github.com/ekolvah/kinozal_scraper/issues/143) | Стратегия B: re-ranker на эмбеддингах (прод-кандидат) | Embeddings | #141 |
| [#144](https://github.com/ekolvah/kinozal_scraper/issues/144) | Сравнение по числам, выбор победителя, интеграция + fallback-цепочка | консолидация на данных | #142, #143 |
| [#145](https://github.com/ekolvah/kinozal_scraper/issues/145) | Observability (учебный, optional, не блокирует прод) | AI Observability / LLMOps | #144 |

Сквозная проверка прогресса — harness `scripts/eval_trailers.py` (по мере появления в #139):
счёт baseline → после #138 → #140 → пред-фильтр → A → B; победитель в #144 даёт минимум **Wrong**
(чужой трейлер штрафуется сильнее Miss). Прод-композиция (#144): пред-фильтр → (спорно) →
победитель A/B → (Gemini quota/сбой → **деградация в пред-фильтр, не в пустоту**) → `None` →
видимый маркер (§IV).

## Out of scope инициативы

- Векторная БД как хранилище эмбеддингов — отдельная будущая задача.
- Применение к steam/events/github_trending — фокус на kinozal-трейлерах.
- Суммаризация и её evals (исходная «Фича 2») — низкая польза на простом корпусе.
