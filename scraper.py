import os
import logging
from datetime import datetime
import requests
import pandas as pd
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
import gspread
import json
from abc import ABC, abstractmethod
import re
from gspread.exceptions import APIError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from TelegramChannelSummarizer import TelegramChannelSummarizer


class GoogleSpreadsheet:
    """Класс для работы с Google Spreadsheet."""

    def __init__(self):
        """Инициализация подключения к Google Spreadsheet."""
        credentials = json.loads(os.environ['CREDENTIALS'])
        self.client = gspread.service_account_from_dict(credentials)
        self.sheet = self.client.open_by_url(
            'https://docs.google.com/spreadsheets/d/12E95cAZIT-_2MfEoo6T5Dm-uF8c8xPHZQQ3WcEZPQjo/edit?usp=sharing')

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((APIError, requests.exceptions.RequestException)),
        before=lambda retry_state: logger.info(f"Попытка {retry_state.attempt_number}")
    )
    def execute_with_retry(self, func, *args, **kwargs):
        """Выполняет функцию с механизмом повторных попыток."""
        return func(*args, **kwargs)

    def get_worksheet(self, index):
        """Получает рабочий лист по индексу."""
        return self.execute_with_retry(self._get_worksheet, index)

    def _get_worksheet(self, index):
        """Внутренний метод для получения рабочего листа."""
        logger.info(f"Получение рабочего листа с индексом {index}")
        start_time = datetime.now()
        worksheet = self.sheet.get_worksheet(index)
        end_time = datetime.now()
        logger.info(f"Рабочий лист успешно получен. Время выполнения: {end_time - start_time}")
        return worksheet

    def update_worksheet(self, worksheet, notified_movies):
        """Обновляет рабочий лист."""
        return self.execute_with_retry(self._update_worksheet, worksheet, notified_movies)

    def _update_worksheet(self, worksheet, notified_movies):
        """Внутренний метод для обновления рабочего листа."""
        logger.info("Обновление рабочего листа")
        start_time = datetime.now()
        result = worksheet.update(notified_movies.values.tolist())
        end_time = datetime.now()
        logger.info(f"Рабочий лист успешно обновлен. Время выполнения: {end_time - start_time}")
        logger.info(f"Результат обновления: {result}")
        return result

    def make_request(self, *args, **kwargs):
        """Выполняет HTTP-запрос с логированием и повторными попытками."""
        return self.execute_with_retry(self._make_request, *args, **kwargs)

    def _make_request(self, *args, **kwargs):
        """Внутренний метод для выполнения HTTP-запроса."""
        method = kwargs.get('method') or args[0]
        url = kwargs.get('url') or args[1]
        logger.info(f"Выполнение запроса {method} к {url}")
        self.log_request(requests.Request(method, url, **kwargs).prepare())
        response = self.client.request(*args, **kwargs)
        self.log_response(response)
        return response

    @staticmethod
    def log_request(request):
        """Логирует детали запроса."""
        logger.info(f"URL запроса: {request.url}")
        logger.info(f"Метод запроса: {request.method}")
        logger.info(f"Заголовки запроса: {request.headers}")
        logger.info(f"Тело запроса: {request.body}")

    @staticmethod
    def log_response(response):
        """Логирует детали ответа."""
        logger.info(f"Код статуса ответа: {response.status_code}")
        logger.info(f"Заголовки ответа: {response.headers}")
        logger.info(f"Содержимое ответа: {response.content}")



class Youtube:
    """Класс для работы с YouTube API."""

    def __init__(self):
        """Инициализация подключения к YouTube API."""
        self.credentials = os.environ['API_KEY']
        self.youtube = build('youtube', 'v3', developerKey=self.credentials)

    def get_trailer_url(self, film):
        """Получает URL трейлера фильма."""
        request = self.youtube.search().list(q=f"{film} trailer", part='id', maxResults=5)
        response = request.execute()
        for item in response['items']:
            if item['id'].get('kind') == 'youtube#video':
                return f"https://www.youtube.com/watch?v={item['id'].get('videoId')}"
        return None

class TelegramBot:
    """Класс для работы с Telegram Bot API."""

    def __init__(self):
        """Инициализация Telegram бота."""
        self.bot_token = os.environ['BOT_TOKEN']
        self.bot_chatID = os.environ['BOT_CHATID']

    def send_text(self, text, is_error_message=False):
        """Отправляет текстовое сообщение."""
        if len(text) > 4096:
            logger.warning("Message too long, truncating to 4096 characters.")
            text = text[:4096]

        send_message = f'https://api.telegram.org/bot{self.bot_token}/sendMessage'
        message_data = {'chat_id': self.bot_chatID, 'text': text}
        self._send_request(send_message, message_data, is_error_message)

    def send_poster(self, film, poster, href, trailer):
        """Отправляет постер фильма."""
        caption = f'<a href="{href}">{film}</a>\n\n<a href="{trailer}">Trailer</a>'
        send_photo_url = f'https://api.telegram.org/bot{self.bot_token}/sendPhoto'
        data = {'chat_id': self.bot_chatID, 'photo': poster, 'parse_mode': 'HTML', 'caption': caption}

        try:
            # Попытка отправить по ссылке
            self._send_request(send_photo_url, data, raise_on_error=True)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Не удалось отправить постер по ссылке: {poster}. Ошибка: {e}. Попытка загрузить файл.")
            try:
                # Скачиваем изображение
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                img_response = requests.get(poster, headers=headers)
                img_response.raise_for_status()

                # Подготовка данных для отправки файла (удаляем ссылку на фото из данных)
                data_upload = data.copy()
                del data_upload['photo']

                # 'photo' - это имя поля для файла в API Telegram
                files = {'photo': ('image.jpg', img_response.content, 'image/jpeg')}

                # Отправка с файлом. Если это тоже не удастся, _send_request обработает ошибку.
                self._send_request(send_photo_url, data_upload, files=files)

            except Exception as download_error:
                logger.error(f"Не удалось скачать и отправить изображение: {download_error}")
                # Отправляем сообщение об ошибке, так как первая попытка была подавлена, а вторая не удалась
                self._send_error_message(data, download_error, f"Failed to download image after URL send failed. Original error: {e}")

    def _send_request(self, url, data, is_error_message=False, files=None, raise_on_error=False):
        """Отправляет запрос к Telegram API."""
        response = None
        try:
            response = requests.post(url, data=data, files=files)
            response.raise_for_status()
            return response
        except requests.exceptions.HTTPError as err:
            logger.error(f"Ошибка при отправке сообщения: {str(err)}")
            if raise_on_error:
                raise err
            if not is_error_message:  # Предотвращаем рекурсию
                self._send_error_message(data, err, response.text if response else "Нет ответа")
            else:
                logger.error(f"Не удалось отправить сообщение об ошибке. Детали ошибки: {str(err)}")

    def _send_error_message(self, data, error, details):
        """Отправляет сообщение об ошибке."""
        error_message = f"Ошибка: {str(error)}\nПодробности: {details}"
        for key, value in data.items():
            error_message += f"\n{key}: {value}"
        self.send_text(error_message, is_error_message=True)


class Scraper(ABC):
    """Абстрактный класс для скрапера."""

    def __init__(self, spreadsheet: GoogleSpreadsheet, youtube: Youtube, telegram_bot: TelegramBot):
        """Инициализация скрапера."""
        self.spreadsheet = spreadsheet
        self.youtube = youtube
        self.telegram_bot = telegram_bot
        self.notified_events = self.get_notified_events()
        self.top_events = self.get_top_events()
        self.new_events = self.get_new_events()

    @staticmethod
    @abstractmethod
    def add_prefix(link: str) -> str:
        """Добавляет префикс к ссылке."""
        pass

    def get_notified_events(self):
        """Получает уже оповещенные события."""
        worksheet = self.spreadsheet.get_worksheet(0)
        return pd.DataFrame(worksheet.get_all_values(), columns=['events', 'posters', 'href'])

    @abstractmethod
    def get_top_events(self):
        """Получает топовые события."""
        pass

    def get_new_events(self):
        """Получает новые события."""
        return self.top_events[~self.top_events['events'].isin(self.notified_events['events'])]

    def run(self):
        """Запускает процесс скрапинга и оповещения."""
        self._send_notifications()
        self._update_notified_events()

    def _send_notifications(self):
        """Отправляет уведомления о новых событиях."""
        for _, event in self.new_events.iterrows():
            trailer = self.youtube.get_trailer_url(event['events'])
            self.telegram_bot.send_poster(event['events'], event['posters'], event['href'], trailer)

    def _update_notified_events(self):
        """Обновляет список оповещенных событий."""
        self.notified_events = pd.concat([self.notified_events, self.new_events])
        self.spreadsheet.update_worksheet(self.spreadsheet.get_worksheet(0), self.notified_events)


class MovieScraper(Scraper):
    """Скрапер для фильмов."""

    @staticmethod
    def add_prefix(link: str) -> str:
        """Добавляет префикс к ссылке на фильм."""
        return link if link.startswith('http') else f'https://kinozal.tv{link}'

    def get_top_events(self):
        """Получает топовые фильмы."""
        data = []
        urls = [pair.split("|")[1] for pair in os.getenv('URLS').split(";")]
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2490.80 Safari/537.36',
            'Content-Type': 'text/html',
        }
        for url in urls:
            response = requests.get(url, headers=headers)
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.select('a[href^="/details.php"]'):
                data.append([
                    str(link.get('title')),
                    self.add_prefix(link.find('img').get('src')),
                    self.add_prefix(str(link.get('href')))
                ])
        return pd.DataFrame(data, columns=['events', 'posters', 'href']).drop_duplicates()

    def run(self):
        """Запускает процесс скрапинга фильмов и оповещения."""
        for _, event in self.new_events.iterrows():
            movie_title = event['events'].split('/')[0].strip().split('(')[0].strip()
            trailer = self.youtube.get_trailer_url(movie_title)
            self.telegram_bot.send_poster(movie_title, event['posters'], event['href'], trailer)
        self._update_notified_events()

if __name__ == "__main__":
    spreadsheet = GoogleSpreadsheet()
    youtube = Youtube()
    telegram_bot = TelegramBot()

    if os.getenv("USE_GENERIC_KINOZAL", "false").lower() == "true":
        from kinozal_pipeline import run_kinozal_pipeline
        from sheets_storage import SheetsStorage
        from telegram_notifier import TelegramNotifier

        _SPREADSHEET_URL = (
            "https://docs.google.com/spreadsheets/d/"
            "12E95cAZIT-_2MfEoo6T5Dm-uF8c8xPHZQQ3WcEZPQjo/edit?usp=sharing"
        )
        _storage = SheetsStorage(spreadsheet.client, _SPREADSHEET_URL)
        _notifier = TelegramNotifier(telegram_bot.bot_token, telegram_bot.bot_chatID)
        run_kinozal_pipeline(_storage, _notifier, youtube)
    else:
        movie_scraper = MovieScraper(spreadsheet, youtube, telegram_bot)
        movie_scraper.run()

    # Изменение: обрабатываем каждый канал отдельно
    summaries = TelegramChannelSummarizer.summarization()
    if summaries:
        # Отправляем заголовок
        telegram_bot.send_text("🔍 Обзор сообщений в каналах за последние сутки:")

        # Отправляем каждое саммари отдельным сообщением
        for summary_item in summaries:
            channel = summary_item["channel"]
            summary_text = summary_item["summary"]
            message = f"📢 Канал: {channel}\n\n{summary_text}"
            telegram_bot.send_text(message)
    else:
        telegram_bot.send_text("За последние сутки в отслеживаемых каналах не было новых сообщений.")
