import os
import requests
import pandas as pd
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import json
from langdetect import detect
from abc import ABC, abstractmethod
import re
from telethon.sync import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from datetime import datetime, timedelta
import pytz
import google.generativeai as genai
import asyncio
from telethon.sessions import StringSession

class GoogleSpreadsheet:
    def __init__(self):
        self.scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        credentials = os.environ['CREDENTIALS']
        credentials_dict = json.loads(credentials)
        self.creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, self.scope)
        self.client = gspread.authorize(self.creds)
        self.sheet = self.client.open_by_url(
            'https://docs.google.com/spreadsheets/d/12E95cAZIT-_2MfEoo6T5Dm-uF8c8xPHZQQ3WcEZPQjo/edit?usp=sharing')

    def get_worksheet(self, index):
        return self.sheet.get_worksheet(index)

    def update_worksheet(self, worksheet, notified_movies):
        worksheet.update(notified_movies.values.tolist())


class Youtube:
    def __init__(self):
        self.credentials = os.environ['API_KEY']
        self.youtube = build('youtube', 'v3', developerKey=self.credentials)

    def get_trailer_url(self, film):
        request = self.youtube.search().list(
            q=film + ' trailer',
            part='id',
            maxResults=5
        )
        response = request.execute()
        for item in response['items']:
            if item['id'].get('kind') == 'youtube#video':
                video_id = item['id'].get('videoId')
                trailer_url = f'https://www.youtube.com/watch?v={video_id}'
                return trailer_url
        return None


class TelegramBot:
    def __init__(self):
        self.bot_token = os.environ['BOT_TOKEN']
        self.bot_chatID = os.environ['BOT_CHATID']

    def send_poster(self, film, poster, href, trailer):
        caption = '<a href="' + href + '">' + film + '</a>' + '\n\n' + '<a href="' + trailer + '">Trailer</a>'
        send_photo = 'https://api.telegram.org/bot' + self.bot_token + '/sendPhoto'
        data = {'chat_id': self.bot_chatID, 'photo': poster, 'parse_mode': 'HTML', 'caption': caption}

        response = requests.post(send_photo, data=data)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as err:
            send_message = 'https://api.telegram.org/bot' + self.bot_token + '/sendMessage'
            message_data = {'chat_id': self.bot_chatID,
                            'text': f"Film: {film}\nPoster: {poster}\nLink: {href}\nTrailer: {trailer}\nОшибка: {str(err)}\nПодробности: {response.text}"}
            requests.post(send_message, data=message_data)


class Scraper(ABC):
    def __init__(self, spreadsheet: GoogleSpreadsheet, youtube: Youtube, telegram_bot: TelegramBot):
        self.spreadsheet = spreadsheet
        self.youtube = youtube
        self.telegram_bot = telegram_bot
        self.notified_events = self.get_notified_events()

        print("-----Notified Events-----")
        print(self.notified_events)

        self.top_events = self.get_top_events()

        print("-----Top Events-----")
        print(self.top_events)

        self.new_events = self.get_new_events()

        print("-----New Events-----")
        print(self.new_events)


    @abstractmethod
    def add_prefix(self, link):
        pass

    def get_notified_events(self):
        worksheet = self.spreadsheet.get_worksheet(0)
        return pd.DataFrame(worksheet.get_all_values(), columns=['events', 'posters', 'href'])

    @abstractmethod
    def get_top_events(self):
        pass

    def get_new_events(self):
        new_events = self.top_events[~self.top_events['events'].isin(self.notified_events['events'])]
        return new_events

    def run(self):
        for index in self.new_events.index:
            entity_name = self.new_events.loc[index, 'events']
            poster = self.new_events.loc[index, 'posters']
            href = self.new_events.loc[index, 'href']
            trailer = self.youtube.get_trailer_url(entity_name)
            self.telegram_bot.send_poster(entity_name, poster, href, trailer)

        self.notified_events = pd.concat([self.notified_events, self.new_events])
        self.spreadsheet.update_worksheet(self.spreadsheet.get_worksheet(0), self.notified_events)


class MovieScraper(Scraper):
    @staticmethod
    def add_prefix(link):
        if link.startswith('http'):
            return link
        else:
            return 'https://kinozal.tv' + link

    def get_top_events(self):

        data = []
        URLS_COMMENTS_STR = os.getenv('URLS')
        PAIRS = URLS_COMMENTS_STR.split(";")
        URLS = [pair.split("|")[1] for pair in PAIRS]

        for url in URLS:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2490.80 Safari/537.36',
                'Content-Type': 'text/html',
            }
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.select('a[href^="/details.php"]'):
                title = str(link.get('title'))
                href = self.add_prefix(str(link.get('href')))
                poster = self.add_prefix(link.find('img').get('src'))
                data.append([title, poster, href])

        df = pd.DataFrame(data, columns=['events', 'posters', 'href'])
        return df.drop_duplicates()

    def run(self):
        for index in self.new_events.index:
            event = self.new_events.loc[index, 'events'].split('/')[0].strip()
            poster = self.new_events.loc[index, 'posters']
            href = self.new_events.loc[index, 'href']
            trailer = self.youtube.get_trailer_url(event.split('(')[0].strip())
            self.telegram_bot.send_poster(event, poster, href, trailer)

        self.notified_events = pd.concat([self.notified_events, self.new_events])
        self.spreadsheet.update_worksheet(self.spreadsheet.get_worksheet(0), self.notified_events)


class EventsScraper(Scraper):
    @staticmethod
    def add_prefix(link):
        if link.startswith('http'):
            return link
        else:
            return 'https://www.soldoutticketbox.com' + link

    def get_top_events(self):
        URLS_EVENTS_STR = os.getenv('URLS_EVENTS')
        PAIRS_EVENTS = URLS_EVENTS_STR.split(";")
        URLS_EVENTS = [pair.split("|")[1] for pair in PAIRS_EVENTS]

        data = []
        for url in URLS_EVENTS:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2490.80 Safari/537.36',
                'Content-Type': 'text/html',
            }
            response = requests.get(url, headers=headers)

            soup = BeautifulSoup(response.text, 'html.parser')
            for box in soup.find_all('div', class_='homeBoxEvent'):
                event = box.select_one('h2 a').text
                poster = self.add_prefix(str(box.select_one('.imgEvent')['src']))
                href = self.add_prefix(str(box.select_one('.homeBoxEventTop a')['href']))

                if re.search('[а-яА-Я]', event):
                    lang = 'ru'
                else:
                    lang = ''

                # Если событие на русском языке, добавляем информацию в наш список
                if lang == 'ru':
                    data.append([event, poster, href])

        df = pd.DataFrame(data, columns=['events', 'posters', 'href'])
        return df.drop_duplicates()

class TelegramChannelSummarizer:
    telegram_api_id = os.getenv('TELEGRAM_API_ID')
    api_hash = os.getenv('API_HASH')
    channel_urls = os.getenv('CHANNEL_URL')
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    phone_number = os.getenv('PHONE_NUMBER')
    TELETHON_SESSION = os.getenv('TELETHON_SESSION')

    genai.configure(api_key=GOOGLE_API_KEY)


    @staticmethod
    def summarization_text(text):
        model = genai.GenerativeModel('gemini-pro')
        request = text + (" Это текст сообщений из чата. "
                          "Проанализируй этот текст и выдели ключевые темы. "
                          "Ограничь ответ 100 символами.")
        response = model.generate_content(request)
        if response.candidates:
            #print("------Summarization------")
            #print(response.text)
            return response.text
        else:
            #print("------Summarization------")
            #print("No candidates were returned for the prompt.")
            return ""

    @staticmethod
    def summarization():
        loop = asyncio.get_event_loop()
        channel_urls_list = TelegramChannelSummarizer.channel_urls.split(';')
        result = ''
        for url in channel_urls_list:
            text = loop.run_until_complete(TelegramChannelSummarizer.get_news_from_telegram_channel(url))
            #print("-----Telegram channel: ", url, "-----")
            #print(text)
            result += f"\n-----Telegram channel: {url} -----\n" + TelegramChannelSummarizer.summarization_text(text)
        return result

    @staticmethod
    async def get_news_from_telegram_channel(channel_url):
        if TelegramChannelSummarizer.TELETHON_SESSION:
            client = TelegramClient(StringSession(TelegramChannelSummarizer.TELETHON_SESSION), TelegramChannelSummarizer.telegram_api_id, TelegramChannelSummarizer.api_hash)
        else:
            client = TelegramClient('anon', TelegramChannelSummarizer.telegram_api_id, TelegramChannelSummarizer.api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(TelegramChannelSummarizer.phone_number)
            await client.sign_in(TelegramChannelSummarizer.phone_number, input('Enter the code: '))
        async with client:
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
            result = '\n'.join([message.message for message in recent_messages])
            return result

if __name__ == "__main__":
    """
    spreadsheet = GoogleSpreadsheet()
    youtube = Youtube()
    telegram_bot = TelegramBot()
    movie_scraper = MovieScraper(spreadsheet, youtube, telegram_bot)
    movie_scraper.run()

    events_scraper = EventsScraper(spreadsheet, youtube, telegram_bot)
    events_scraper.run()
    """
    summary = TelegramChannelSummarizer.summarization()
    print(summary)
