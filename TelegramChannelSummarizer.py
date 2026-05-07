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
    CHAT_PROMPT = os.getenv('CHAT_PROMPT')
    BROADCAST_PROMPT = os.getenv('BROADCAST_PROMPT')

    _initialized = False

    @classmethod
    def _ensure_initialized(cls):
        if cls._initialized:
            return
        crypto.load_encrypter_session()
        genai.configure(api_key=cls.GOOGLE_API_KEY)
        cls._models = cls._build_model_list()
        if cls._models:
            logger.info(f"Available models for summarization: {cls._models}")
        else:
            logger.warning("No Gemini models available, summarization will be skipped")
        cls._initialized = True

    @staticmethod
    def _build_model_list():
        from gemini_enricher import get_generation_models
        return get_generation_models()

    @staticmethod
    def summarization_text(text, is_broadcast=False):
        if not text:
            return ""

        if is_broadcast:
            prompt = TelegramChannelSummarizer.BROADCAST_PROMPT or (
                " Это текст постов из телеграм канала. "
                "Проанализируй этот текст и выдели ключевые темы. "
                "Будь лаконичным."
            )
        else:
            prompt = TelegramChannelSummarizer.CHAT_PROMPT or (
                " Это текст сообщений из чата в формате 'Имя: Сообщение'. "
                "Проанализируй этот текст и выдели только основные обсуждаемые темы. "
                "Не пиши детали, кто что сказал, не указывай имена. "
                "Просто перечисли заголовки обсуждаемых тем. Будь максимально лаконичным."
            )
        request = text + prompt

        for model_name in TelegramChannelSummarizer._models:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(request)

                logger.info("------Оригинальный текст------")
                logger.info(text)
                logger.info("------Саммаризация------")

                if not response.candidates:
                    return ""

                logger.info(response.text)
                return response.text
            except google.api_core.exceptions.ResourceExhausted:
                logger.warning(f"model {model_name} quota exhausted, trying next")
                continue
            except Exception as e:
                logger.error(f"Error with model {model_name}: {e}")
                return ""

        logger.error("all models exhausted, could not summarize")
        return ""

    @staticmethod
    def summarization():
        TelegramChannelSummarizer._ensure_initialized()
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
                        "url": url,
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