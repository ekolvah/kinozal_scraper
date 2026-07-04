# Observability — Sentry error tracking (#278)

**На какой вопрос отвечает этот файл:** как устроен захват ошибок в рантайме (Sentry), почему
именно так, и какие ручные шаги нужны оператору, чтобы алертинг ожил.

## Зачем

Раньше единственный сигнал о падении — fallback-curl в `run-script.yml` (`⚠️ run failed: <url>`):
видно *что* упало, но не *что за ошибка*, транзиент или структурная поломка. Оператор кликал в
Actions на каждый транзиент. Sentry — готовый error-tracker (§«стандартные тулы > велосипеды»):
traceback + класс ошибки + группировка-дедуп + severity через alert-rules, без нашего кода-агрегатора.

## Механизм

`observability.init_sentry()` вызывается один раз на процесс в каждой из **6 точек входа** (5
pipeline'ов + `telegram_summarizer`), **после** `logging.basicConfig`, **до** тела pipeline.

- **Захват держится на дефолтной `LoggingIntegration`** (`event_level=logging.ERROR`).
  Per-source ошибки ловятся и логируются (`logger.exception(...)`), а не пробрасываются — Sentry
  automatic `excepthook` их не видит. Зато `LoggingIntegration` превращает каждый `logger.exception`
  в *событие с traceback*. Инвариант «handler логирует на ERROR» машинно-стережёт ruff **TRY400**.
  Это **load-bearing**: `tests/test_observability.py::TestCaptureMechanism` пиннит дефолт sentry-sdk,
  чтобы его смена на bump краснела, а не тихо гасила алерты. **Нельзя** передавать
  `default_integrations=False` или `integrations=`, роняющий `LoggingIntegration`.
- **Degrade-safe.** Нет `SENTRY_DSN` → no-op + видимый INFO, job зелёный. Код мержится и работает
  **до** того, как оператор заведёт secret.
- **Не load-bearing для доставки.** Кривой DSN (`BadDsn`) не роняет 6 pipeline'ов: ошибка init
  ERROR-логируется (видимо) и глотается — монитор не должен убивать продуктовую доставку.
- **`event_level=ERROR` — глобальная сеть.** Ловятся *все* ERROR-логи, включая рутинные
  handled-деградации (`gemini_enricher` «cannot list models» на quota; per-model retry в
  `TelegramChannelSummarizer`). Это **норма**: alert-fatigue режется на слое **доставки**
  (alert-rules), не захвата — Sentry задуман ловить широко, алертить выборочно.

## Fallback-curl остаётся

Шаг `Send fallback failure alert` в `run-script.yml` **не выпилен**: это единственный сигнал,
когда Python не стартовал / упал до `init_sentry` (сбой install, краш интерпретатора) — там Sentry
бессилен (процесса нет). Sentry аддитивен. Ось «job не запустился вообще» (dead-man's-switch) —
сознательно вне scope.

## Operator runbook — активация

Пока эти шаги не сделаны, код **инертен** (нет DSN → no-op). «Delivered» = «алертинг живой», а не
«PR смержен».

1. Завести проект в [Sentry](https://sentry.io) (free tier ~5k событий/мес), скопировать **DSN**.
2. `gh secret set SENTRY_DSN --body '<dsn>'` (repo secret; `SENTRY_ENVIRONMENT` уже задан в
   workflow как `production`).
3. В Sentry UI → Settings → Integrations включить **Telegram** (partner-maintained, ставится из
   каталога — без self-hosted моста).
4. Настроить **alert-rule = «новая issue / всплеск частоты»**, НЕ «каждое событие → Telegram» —
   иначе рутинные ERROR-логи (см. выше: gemini quota, per-model retry) зафлудят Telegram.
5. **Canary-verification:** триггернуть тестовую ошибку (например, временно сломать один источник
   в dry-run), убедиться, что событие (а) видно в Sentry и (б) дошло до Telegram. Затем откатить.

**Trade-offs (приняты осознанно):** трейсы уходят к SaaS (хобби-скрейпер без секретов в трейсах —
приемлемо); Telegram-коннектор partner-maintained; алерты от бота Sentry, не от kinozal-бота.
