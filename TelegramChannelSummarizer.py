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
    LLM_MODEL = os.getenv('LLM_MODEL')

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
    def summarization_text(text, is_broadcast=False):
        if not text:
            return ""

        try:
            model = genai.GenerativeModel(TelegramChannelSummarizer.LLM_MODEL)
            if is_broadcast:
                prompt = (
                    " Это текст постов из телеграм канала. "
                    "Проанализируй этот текст и выдели ключевые темы. "
                    "Будь лаконичным."
                )
            else:
                prompt = (
                    " Это текст сообщений из чата в формате 'Имя: Сообщение'. "
                    "Проанализируй этот текст и выдели ключевые идеи и предложения. "
                    "Описывай суть предложенного, а не просто тему. "
                    "Указывай авторов ключевых мнений. Будь лаконичным."
                )
            request = text + prompt

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
        logger.info(f"Environment variable LLM_MODEL: {TelegramChannelSummarizer.LLM_MODEL}")
        channel_urls_list = TelegramChannelSummarizer.channel_urls.split(';')
        results = []  # Сохраняем результаты для каждого канала отдельно

        for url in channel_urls_list:
            # Используем asyncio.run для создания/закрытия event loop для каждого top-level async вызова.
            # ПОЛУЧАЕМ ТРИ ЗНАЧЕНИЯ: channel_title, text, is_broadcast
            channel_title, text, is_broadcast = asyncio.run(TelegramChannelSummarizer.get_news_from_telegram_channel(url))
            
            # Если название не удалось определить, используем URL или ID
            display_name = channel_title if channel_title else url

            logger.info(f"-----Telegram channel: {display_name} -----")
            logger.info(text)
            if text:
                summary = TelegramChannelSummarizer.summarization_text(text, is_broadcast)
                if summary:
                    results.append({
                        "channel": display_name, # Используем красивое имя
                        "summary": summary
                    })

        return results  # Возвращаем список с результатами для каждого канала
        
    @staticmethod
    async def get_news_from_telegram_channel(channel_url):
        # 1. Если channel_url - это строка с числом (например, "-1001537004903"), конвертируем в int
        if isinstance(channel_url, str) and channel_url.lstrip('-').isdigit():
            channel_url = int(channel_url)
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
            channel_title = getattr(entity, 'title', str(channel_url)) 
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

            # Создаем словарь пользователей для быстрого поиска по ID
            users = {user.id: user for user in posts.users}

            formatted_messages = []
            is_broadcast = getattr(entity, 'broadcast', False)
            logger.info(f"Channel '{channel_title}' is_broadcast: {is_broadcast}")
            for message in recent_messages:
                if message.message:
                    if is_broadcast:
                        formatted_messages.append(message.message)
                    else:
                        sender_name = "Unknown"
                        # Пытаемся найти имя отправителя
                        if message.sender_id:
                            sender = users.get(message.sender_id)
                            if sender:
                                first_name = sender.first_name or ""
                                last_name = sender.last_name or ""
                                sender_name = f"{first_name} {last_name}".strip()
                                # Если нет имени, берем юзернейм, если нет юзернейма - ID
                                if not sender_name and sender.username:
                                    sender_name = sender.username
                                if not sender_name:
                                    sender_name = str(sender.id)
                        
                        # Добавляем в список в формате "Имя: Сообщение"
                        formatted_messages.append(f"{sender_name}: {message.message}")

            if not formatted_messages:
                logger.info(f"No text messages found in channel: {channel_url}")
                return channel_title, "", is_broadcast # Возвращаем кортеж

            result = '\n'.join(formatted_messages)
            return channel_title, result, is_broadcast # Возвращаем название и текст
        except Exception as e:
            logger.error(f"Error processing channel {channel_url}: {str(e)}")
            return None, "", False # Возвращаем None при ошибке
        finally:
            await client.disconnect()