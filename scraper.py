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

def save_kinozal_top_movies(worksheet, kinozal_top_movies):
  worksheet.update(kinozal_top_movies.values.tolist())
  
#def save_kinozal_top_movies(kinozal_top_movies):
#def update_notified_movies(new_movies):
#def get_kinozal_top_movies():
#def get_notified_movies():
#def compare_movie_lists(kinozal_top_movies, notified_movies):

def send_message_with_new_movies(new_movies):
  if not new_movies.empty:
    telegram_bot_sendtext(new_movies.to_string())

def telegram_bot_sendtext(bot_message):
  bot_token = os.environ['BOT_TOKEN']
  bot_chatID = os.environ['BOT_CHATID']
  send_text = 'https://api.telegram.org/bot' + bot_token + '/sendMessage?chat_id=' + bot_chatID + '&parse_mode=Markdown&text=' + bot_message

  response = requests.get(send_text)
  #print(response.text)
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
  #kinozal_top_movies = get_kinozal_top_movies()
  #notified_movies = get_notified_movies()
  #new_movies = compare_movie_lists(kinozal_top_movies, notified_movies)

  sheet = get_sheet()
  kinozal_top_movies_worksheet = sheet.get_worksheet(0)
  
  data = []
  soup = get_soup("https://kinozal.tv/top.php?j=&t=0&d=12&k=0&f=0&w=0&s=0")
  for link in soup.select('a[href^="/details.php"]'):
    title = str(link.get('title'))
    data.append(title)
  
  notified_movies = pd.DataFrame(kinozal_top_movies_worksheet.get_all_values(), columns=['films'])
  
  kinozal_top_movies = pd.DataFrame(data, columns=['films'])
  diff = kinozal_top_movies.merge(notified_movies, on='films', how='outer', indicator=True)
  diff = diff[diff['_merge'] == 'left_only']
  
  #print(diff['films'].to_list())

  send_message_with_new_movies(diff)
  save_kinozal_top_movies(kinozal_top_movies_worksheet, kinozal_top_movies)
  #update_notified_movies(new_movies)

run_kinozal_scrapper()
