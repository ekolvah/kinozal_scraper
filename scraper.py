from bs4 import BeautifulSoup
import requests
import re
import pandas as pd
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

def get_sheet():
  scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
  credentials = os.environ['CREDENTIALS']
  credentials_dict = json.loads(credentials)
  creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
  client = gspread.authorize(creds)
  sheet = client.open_by_url('https://docs.google.com/spreadsheets/d/12E95cAZIT-_2MfEoo6T5Dm-uF8c8xPHZQQ3WcEZPQjo/edit?usp=sharing')
  return sheet

def save_notified_movies(worksheet, notified_movies):
  worksheet.update(values=notified_movies.values.tolist(), range_name=None)
  
def get_kinozal_top_movies():
  data = []
  soup = get_soup("https://kinozal.tv/top.php?j=&t=0&d=12&k=0&f=0&w=0&s=0")
  for link in soup.select('a[href^="/details.php"]'):
    title = str(link.get('title'))
    href = str(link.get('href'))
    poster = link.find('img').get('src')
    data.append([title, poster, href])
  df = pd.DataFrame(data, columns=['films', 'posters', 'href'])
  df['posters'] = df['posters'].apply(add_prefix)
  df['href'] = df['href'].apply(add_prefix)
  return df

def get_notified_movies(notified_movies_worksheet):
  notified_movies = pd.DataFrame(notified_movies_worksheet.get_all_values(), columns=['films', 'posters','href'])
  return notified_movies

def add_prefix(link):
  if link.startswith('http'):
    return link
  else:
    return 'https://kinozal.tv' + link

def get_new_movies(kinozal_top_movies, notified_movies):
  new_movies = kinozal_top_movies.merge(notified_movies, on='films', how='outer', indicator=True)
  new_movies = new_movies[new_movies['_merge'] == 'left_only']
  new_movies = new_movies.drop('posters_y', axis=1)
  new_movies = new_movies.drop('href_y', axis=1)
  new_movies = new_movies.drop('_merge', axis=1)
  new_movies = new_movies.rename(columns={'posters_x': 'posters'})
  new_movies = new_movies.rename(columns={'href_x': 'href'})
  new_movies['posters'] = new_movies['posters'].apply(add_prefix)
  new_movies['href'] = new_movies['href'].apply(add_prefix)
  return new_movies

def send_message_with_new_movies(new_movies):
  for index, row in new_movies.iterrows():
    film = row['films']
    poster = row['posters']
    href = row['href']
    telegram_bot_send_poster(film, poster, href)

def telegram_bot_send_poster(film, poster, href):
  bot_token = os.environ['BOT_TOKEN']
  bot_chatID = os.environ['BOT_CHATID']
  data = {'chat_id': bot_chatID, 'photo': poster, 'parse_mode': 'HTML','caption': '<a href="' + href + '">' + film + '</a>'}
  send_photo = 'https://api.telegram.org/bot' + bot_token + '/sendPhoto'
  response = requests.post(send_photo, data=data)
  return response.json()

def get_soup(URL):
  headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/46.0.2490.80 Safari/537.36',
      'Content-Type': 'text/html',
  }

  response = requests.get(URL, headers=headers)
  soup = BeautifulSoup(response.text, 'html.parser')
  if response.status_code != 200:
      print("******** fail ********** ")
  #print(response.url)
  #print(response.text)
  #print('---')
  return soup

def run_kinozal_scrapper():
  notified_movies_worksheet = get_sheet().get_worksheet(0)
  
  kinozal_top_movies = get_kinozal_top_movies()
  notified_movies = get_notified_movies(notified_movies_worksheet)
  new_movies = get_new_movies(kinozal_top_movies, notified_movies)
  
  send_message_with_new_movies(new_movies)
  
  notified_movies = pd.concat([notified_movies, new_movies])
  
  save_notified_movies(notified_movies_worksheet, notified_movies)
  
run_kinozal_scrapper()
