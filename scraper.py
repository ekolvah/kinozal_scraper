import os
import requests
import pandas as pd
from bs4 import BeautifulSoup
from googleapiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials
import gspread
import json


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


class MovieScraper:

    def __init__(self, spreadsheet: GoogleSpreadsheet, youtube: Youtube, telegram_bot: TelegramBot):
        self.spreadsheet = spreadsheet
        self.youtube = youtube
        self.telegram_bot = telegram_bot

    @staticmethod
    def add_prefix(link):
        if link.startswith('http'):
            return link
        else:
            return 'https://kinozal.tv' + link

    def get_top_movies(self):

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

        df = pd.DataFrame(data, columns=['films', 'posters', 'href'])
        return df.drop_duplicates()

    def run(self):

        notified_movies_worksheet = self.spreadsheet.get_worksheet(0)
        top_movies = self.get_top_movies()

        notified_movies = pd.DataFrame(notified_movies_worksheet.get_all_values(), columns=['films', 'posters', 'href'])
        new_movies = top_movies.merge(notified_movies, on='films', how='outer', indicator=True)
        new_movies = new_movies[new_movies['_merge'] == 'left_only']
        new_movies = new_movies.drop(columns=['posters_y', 'href_y', '_merge'])
        new_movies = new_movies.rename(columns={'posters_x': 'posters', 'href_x': 'href'})
        new_movies['posters'] = new_movies['posters'].apply(self.add_prefix)
        new_movies['href'] = new_movies['href'].apply(self.add_prefix)

        for index, row in new_movies.iterrows():
            film = row['films'].split('/')[0].strip()
            poster = row['posters']
            href = row['href']
            trailer = self.youtube.get_trailer_url(film.split('(')[0].strip())
            self.telegram_bot.send_poster(film, poster, href, trailer)

        notified_movies = pd.concat([notified_movies, new_movies])
        self.spreadsheet.update_worksheet(notified_movies_worksheet, notified_movies)


if __name__ == "__main__":
    spreadsheet = GoogleSpreadsheet()
    youtube = Youtube()
    telegram_bot = TelegramBot()
    movie_scraper = MovieScraper(spreadsheet, youtube, telegram_bot)
    movie_scraper.run()
