import html as _html
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

from TelegramChannelSummarizer import TelegramChannelSummarizer


if __name__ == "__main__":
    from telegram_notifier import TelegramNotifier

    notifier = TelegramNotifier(
        os.environ["TELEGRAM_BOT_TOKEN"],
        os.environ["TELEGRAM_CHAT_ID"],
    )

    summaries = TelegramChannelSummarizer.summarization()
    if summaries:
        notifier.send_text("🔍 Обзор сообщений в каналах за последние сутки:")

        for summary_item in summaries:
            channel = summary_item["channel"]
            channel_url = summary_item.get("url", "")
            summary_text = summary_item["summary"]
            if channel_url and isinstance(channel_url, str) and channel_url.startswith("http"):
                channel_label = f'<a href="{channel_url}">{_html.escape(channel)}</a>'
            else:
                channel_label = _html.escape(channel)
            message = f"📢 Канал: {channel_label}\n\n{_html.escape(summary_text)}"
            notifier.send_text(message)
    else:
        notifier.send_text("За последние сутки в отслеживаемых каналах не было новых сообщений.")
