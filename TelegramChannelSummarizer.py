from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from datetime import datetime, timedelta
import pytz
import google.generativeai as genai
import asyncio
from telethon.sessions import StringSession
import os
from crypto import crypto  # Import the crypto module


class TelegramChannelSummarizer:
    telegram_api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('API_HASH')
    channel_urls = os.getenv('CHANNEL_URL')
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    phone_number = os.getenv('PHONE_NUMBER')
    TELETHON_SESSION = os.getenv('TELETHON_SESSION')

    crypto.load_encrypter_session()
    genai.configure(api_key=GOOGLE_API_KEY)

    @staticmethod
    def summarization_text(text):
        if not text:
            return ""

        model = genai.GenerativeModel('gemini-pro')
        request = text + (" Это текст сообщений из чата. "
                          "Проанализируй этот текст и выдели ключевые темы. "
                          "Будь лаконичным.")
        response = model.generate_content(request)

        print("------Original text------")
        print(text)
        print("------Summarization------")

        if not response.candidates:
            return ""

        print(response.text)
        return response.text

    @staticmethod
    def summarization():
        loop = asyncio.get_event_loop()
        channel_urls_list = TelegramChannelSummarizer.channel_urls.split(';')
        result = ''
        for url in channel_urls_list:
            text = loop.run_until_complete(TelegramChannelSummarizer.get_news_from_telegram_channel(url))
            print("-----Telegram channel: ", url, "-----")
            print(text)
            if text:
                result += f"\n-----Telegram channel: {url} -----\n" + TelegramChannelSummarizer.summarization_text(text)
        return result

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
                print(f"No text messages found in channel: {channel_url}")
                return ""

            result = '\n'.join(text_messages)
            return result
        except Exception as e:
            print(f"Error processing channel {channel_url}: {str(e)}")
            return ""
        finally:
            await client.disconnect()