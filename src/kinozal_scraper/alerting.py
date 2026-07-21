"""Operator-facing failure alerting — канонический дом (#310).

Собирает воедино то, что раньше жило только в `telegram_summarizer`: маркер
`.run/technical_alert_sent` (гейтит generic curl-fallback в `run-script.yml`),
доставку текста алерта и — новое — читаемый per-source алерт для скрейпинг-
пайплайнов (`source_id: <ошибка>` вместо немого «run failed + link»).

**Топология маркера — job-global.** Все скрейперы + summarizer идут
последовательными шагами одного GH-job'а с общим workspace; единственный
потребитель маркера — guard curl-шага `hashFiles(...) == ''`. Поэтому маркер
означает «≥1 богатый алерт доставлен за этот run», а не «этот шаг доставил».
При провале доставки 2-го+ алерта backstop — красный run + логи (§III), не curl
(architect-review B1). Никакой per-step marker-инфры сознательно нет.
"""

from __future__ import annotations

import html as _html
import logging
import os
from pathlib import Path
from typing import Any

from kinozal_scraper.generic_pipeline import PipelineResult

logger = logging.getLogger(__name__)

_TECH_ALERT_MARKER = ".run/technical_alert_sent"


def mark_technical_alert_sent(path: str | None = None) -> None:
    marker_value = path if path is not None else os.getenv("TECH_ALERT_MARKER")
    marker = Path(marker_value or _TECH_ALERT_MARKER)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1", encoding="utf-8")


def send_required_text(notifier: Any, text: str) -> bool:
    ok = bool(notifier.send_text(text))
    if not ok:
        logger.error("Telegram delivery failed")
    return ok


def format_pipeline_failures(results: list[PipelineResult]) -> str:
    """Читаемый per-source алерт: `source_id: <первая ошибка>` по каждому failed.

    Сиблинг `telegram_summarizer.format_technical_alert`, но по `PipelineResult`
    (`source_id` + `errors`), а не `ChannelProcessResult`. HTML-эскейп — Telegram
    `parse_mode=HTML` (иначе `<`/`&` в ошибке ломают parser).
    """
    failed = [r for r in results if not r.ok]
    lines = [
        "⚠️ Ошибка пайплайна",
        "Источник упал — часть данных не собрана / не доставлена.",
        "",
    ]
    for result in failed[:10]:
        first = result.errors[0] if result.errors else "unknown error"
        lines.append(f"- {_html.escape(result.source_id)}: {_html.escape(first)}")
    if len(failed) > 10:
        lines.append(f"... и ещё {len(failed) - 10} failure(s)")
    return "\n".join(lines)


def format_config_rejection_alert(models: frozenset[str]) -> str:
    """Читаемый алерт про систематический config-reject Gemini (#340): модели
    отвергли наш запрос `400 INVALID_ARGUMENT` — это баг запроса, не quota. HTML-
    эскейп для Telegram `parse_mode=HTML`. Сиблинг `format_pipeline_failures`."""
    lines = [
        "⚠️ Gemini config-reject",
        "Модель(и) отвергли запрос (400 INVALID_ARGUMENT) — баг запроса, не quota. "
        "Уведомления доставлены через ротацию, но это нужно чинить:",
        "",
    ]
    lines.extend(f"- {_html.escape(m)}" for m in sorted(models))
    return "\n".join(lines)


def alert_config_rejections(notifier: Any, enricher: Any) -> bool:
    """Если энричер (ротатор) накопил `config_rejected_models`, доставить
    операторский алерт + пометить technical-marker; вернуть, был ли алерт.

    Caller делает `if alert_config_rejections(...) | report_failures(...): sys.exit(1)`
    — §IV: систематический config-reject доходит до оператора и краснит джоб, хотя
    ротация уже доставила уведомления (#340). `getattr` защищает не-ротатор
    (`NullEnricher`/`GeminiEnricher` не имеют свойства → пусто → False)."""
    models: frozenset[str] = getattr(enricher, "config_rejected_models", frozenset())
    if not models:
        return False
    if send_required_text(notifier, format_config_rejection_alert(models)):
        try:
            mark_technical_alert_sent()
        except Exception as exc:  # noqa: BLE001 — marker write failure must not crash the alert path
            logger.exception("Could not write technical alert marker: %s", exc)
    return True


def report_failures(notifier: Any, results: list[PipelineResult]) -> bool:
    """Отправить читаемый алерт по failed-результатам; вернуть, были ли сбои.

    Caller делает `if report_failures(...): sys.exit(1)` — §IV exit-код сохранён.
    Маркер ставится ТОЛЬКО при успешной доставке (зеркалит `deliver_results`):
    при провале `send_text` маркер не пишется, curl-fallback остаётся сетью для
    этого первого недоставленного алерта, а сам сбой виден в ERROR-логе.
    """
    failed = [r for r in results if not r.ok]
    if not failed:
        return False
    if send_required_text(notifier, format_pipeline_failures(results)):
        try:
            mark_technical_alert_sent()
        except Exception as exc:  # noqa: BLE001 — marker write failure must not crash the alert path
            logger.exception("Could not write technical alert marker: %s", exc)
    return True
