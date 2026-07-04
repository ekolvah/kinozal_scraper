"""Доставка результатов суммаризации + technical-alert маркер."""

from __future__ import annotations

import html as _html
import logging
import os
from pathlib import Path
from typing import Any

from kinozal_scraper.TelegramChannelSummarizer import ChannelProcessResult, ChannelSummary

logger = logging.getLogger(__name__)

_TECH_ALERT_MARKER = ".run/technical_alert_sent"


def format_summary_message(summary: ChannelSummary) -> str:
    """Render one `ChannelSummary` for Telegram. Output is HTML-formatted:
    if `summary.url` is an `http[s]://` URL, the channel name is wrapped
    in an anchor tag; otherwise the channel name is plain HTML-escaped.
    The body line is always HTML-escaped to keep Telegram's parser
    happy regardless of what Gemini returned.
    """
    if summary.url and summary.url.startswith("http"):
        channel_label = f'<a href="{summary.url}">{_html.escape(summary.channel)}</a>'
    else:
        channel_label = _html.escape(summary.channel)
    return f"📢 Канал: {channel_label}\n\n{_html.escape(summary.summary)}"


def format_technical_alert(results: list[ChannelProcessResult]) -> str:
    failed = [r for r in results if r.status.endswith("_failed")]
    lines = [
        "⚠️ Ошибка Telegram summarizer",
        "Найдены сообщения, но часть данных не удалось доставить пользователю.",
        "",
    ]
    for result in failed[:10]:
        lines.append(
            "- "
            + _html.escape(result.channel)
            + f": {_html.escape(result.error_kind or result.status)}"
        )
    if len(failed) > 10:
        lines.append(f"... и ещё {len(failed) - 10} failure(s)")
    return "\n".join(lines)


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


def deliver_results(notifier: Any, results: list[ChannelProcessResult]) -> int:
    """Send whatever succeeded, then surface degradation. Returns the exit code.

    Working summaries are delivered first so a single failing channel never
    discards good content (Principle IV). Only after that does a technical
    alert fire for any failures, with a non-zero exit. A Telegram delivery
    failure at any step also returns non-zero.
    """
    failures = [r for r in results if r.status.endswith("_failed")]
    summaries = [
        ChannelSummary(channel=r.channel, url=r.url, summary=r.summary)
        for r in results
        if r.status == "summarized"
    ]

    if summaries:
        if not send_required_text(notifier, "🔍 Обзор сообщений в каналах за последние сутки:"):
            return 1
        for item in summaries:
            if not send_required_text(notifier, format_summary_message(item)):
                return 1
    elif not failures:
        if not send_required_text(
            notifier, "За последние сутки в отслеживаемых каналах не было новых сообщений."
        ):
            return 1

    if failures:
        if send_required_text(notifier, format_technical_alert(results)):
            try:
                mark_technical_alert_sent()
            except Exception as exc:  # noqa: BLE001 — marker write failure must not crash the alert path
                logger.exception("Could not write technical alert marker: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    import sys

    import google.generativeai as genai

    from kinozal_scraper.crypto import crypto
    from kinozal_scraper.gemini_enricher import get_generation_models
    from kinozal_scraper.telegram_notifier import TelegramNotifier
    from kinozal_scraper.TelegramChannelSummarizer import (
        GeminiSummarizer,
        TelethonReader,
        summarize_channel_results,
    )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    from kinozal_scraper.observability import init_sentry

    init_sentry()

    # Decrypt the Telethon session file from `anon.session.encrypted` →
    # `anon.session` so Telethon picks it up locally. Required before any
    # `TelegramClient(...)` construction.
    crypto.load_encrypter_session()

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    available_models = get_generation_models()
    if available_models:
        logger.info("Available models for summarization: %s", available_models)
    else:
        logger.warning("No Gemini models available, summarization will be skipped")

    reader = TelethonReader(
        api_id=os.getenv("TELEGRAM_API_ID"),
        api_hash=os.getenv("API_HASH"),
        session=os.getenv("TELETHON_SESSION"),
        phone=os.getenv("PHONE_NUMBER"),
    )
    summarizer = GeminiSummarizer(
        models=available_models,
        broadcast_prompt=os.getenv("BROADCAST_PROMPT"),
        chat_prompt=os.getenv("CHAT_PROMPT"),
    )

    channel_urls_raw = os.environ["CHANNEL_URL"]
    channel_urls = channel_urls_raw.split(";")

    notifier = TelegramNotifier(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
    )

    results = summarize_channel_results(reader, summarizer, channel_urls)
    sys.exit(deliver_results(notifier, results))
