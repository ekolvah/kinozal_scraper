from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from datetime import datetime, timedelta
import pytz
import google.generativeai as genai
import asyncio
from telethon.sessions import StringSession
import os
from crypto import crypto  # Import the crypto module
import logging
from tenacity import retry, wait_fixed, retry_if_exception_type, stop_after_attempt
import google.api_core.exceptions

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TelegramChannelSummarizer:
    telegram_api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('API_HASH')
    channel_urls = os.getenv('CHANNEL_URL')
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    phone_number = os.getenv('PHONE_NUMBER')
    TELETHON_SESSION = os.getenv('TELETHON_SESSION')

    # to comment it to be able to debug if you have no SECRET_KEY
    crypto.load_encrypter_session()
    genai.configure(api_key=GOOGLE_API_KEY)
    models = genai.list_models()
    for model in models:
        print(model.name)

    @staticmethod
    @retry(
        wait=wait_fixed(60),  # Ожидание 60 секунд (1 минута) между попытками
        retry=retry_if_exception_type(google.api_core.exceptions.ResourceExhausted),
        stop=stop_after_attempt(3),  # Максимальное количество попыток
        before_sleep=lambda retry_state: logger.warning(
            f"Повторная попытка через 1 минуту. Ошибка: {retry_state.outcome.exception()}")
    )
    def generate_with_retry(model, request):
        return model.generate_content(request)

    @staticmethod
    def summarization_text(text):
        if not text:
            return ""

        try:
            model = genai.GenerativeModel('gemini-2.0-flash-lite')
            request = text + (
                " Это текст сообщений из чата. "
                "Проанализируй этот текст и выдели ключевые темы. "
                "Будь лаконичным."
            )

            # Используем функцию с повторными попытками
            response = TelegramChannelSummarizer.generate_with_retry(model, request)

            logger.info("------Оригинальный текст------")
            logger.info(text)
            logger.info("------Саммаризация------")

            if not response.candidates:
                return ""

            logger.info(response.text)
            return response.text
        except Exception as e:
            logger.error(f"Error in summarization_text: {e}")
            return ""

    @staticmethod
    def summarization():
        loop = asyncio.get_event_loop()
        channel_urls_list = TelegramChannelSummarizer.channel_urls.split(';')
        results = []  # Сохраняем результаты для каждого канала отдельно

        for url in channel_urls_list:
            text = loop.run_until_complete(TelegramChannelSummarizer.get_news_from_telegram_channel(url))
            logger.info(f"-----Telegram channel: {url} -----")
            logger.info(text)
            if text:
                summary = TelegramChannelSummarizer.summarization_text(text)
                if summary:
                    results.append({
                        "channel": url,
                        "summary": summary
                    })

        return results  # Возвращаем список с результатами для каждого канала

    @staticmethod
    async def get_news_from_telegram_channel(channel_url):
        if TelegramChannelSummarizer.TELETHON_SESSION:
            client = TelegramClient(StringSession(TelegramChannelSummarizer.TELETHON_SESSION),
                                    TelegramChannelSummarizer.telegram_api_id, TelegramChannelSummarizer.api_hash)
        else:
            client = TelegramClient('anon', TelegramChannelSummarizer.telegram_api_id,
                                    TelegramChannelSummarizer.api_hash)

        try:
            await client.start()
            if not await client.is_user_authorized():
                await client.send_code_request(TelegramChannelSummarizer.phone_number)
                await client.sign_in(TelegramChannelSummarizer.phone_number, input('Enter the code: '))

            entity = await client.get_entity(channel_url)
            posts = await client(GetHistoryRequest(
                peer=entity,
                limit=100,
                offset_date=None,
                offset_id=0,
                max_id=0,
                min_id=0,
                add_offset=0,
                hash=0))

            one_day_ago = datetime.now(pytz.UTC) - timedelta(days=1)
            recent_messages = [message for message in posts.messages if message.date > one_day_ago]
            recent_messages.reverse()

            # Фильтруем сообщения, оставляя только те, которые содержат текст
            text_messages = [message.message for message in recent_messages if message.message]

            if not text_messages:
                logger.info(f"No text messages found in channel: {channel_url}")
                return ""

            result = '\n'.join(text_messages)
            return result
        except Exception as e:
            logger.error(f"Error processing channel {channel_url}: {str(e)}")
            return ""
        finally:
            await client.disconnect()