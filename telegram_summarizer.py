from __future__ import annotations

import html as _html
import logging
import os

from TelegramChannelSummarizer import ChannelSummary

logger = logging.getLogger(__name__)


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


if __name__ == "__main__":
    import google.generativeai as genai

    from crypto import crypto
    from gemini_enricher import get_generation_models
    from telegram_notifier import TelegramNotifier
    from TelegramChannelSummarizer import (
        GeminiSummarizer,
        TelethonReader,
        summarize_channels,
    )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

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

    summaries = summarize_channels(reader, summarizer, channel_urls)
    if summaries:
        notifier.send_text("🔍 Обзор сообщений в каналах за последние сутки:")
        for item in summaries:
            notifier.send_text(format_summary_message(item))
    else:
        notifier.send_text("За последние сутки в отслеживаемых каналах не было новых сообщений.")
