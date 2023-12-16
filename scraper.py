from bs4 import BeautifulSoup
import requests
import re
import pandas as pd
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json

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
  scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
  credentials = os.environ['CREDENTIALS']
  credentials_dict = json.loads(credentials)
  creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
  client = gspread.authorize(creds)
  heet = client.open_by_url('https://docs.google.com/spreadsheets/d/1Et0qZnqYJTHCfk5FlFO9hcmNdF5o18_F/edit?usp=sharing&ouid=113359730219847558026&rtpof=true&sd=true')
  worksheet = sheet.get_worksheet(0)
  
  data = []
  soup = get_soup("https://kinozal.tv/top.php?j=&t=0&d=12&k=0&f=0&w=0&s=0")
  for link in soup.select('a[href^="/details.php"]'):
    title = str(link.get('title'))
    data.append(title)
  
  #df_prev = pd.DataFrame(['1', '2', '3', '4'], columns=['films'])
  #df = pd.DataFrame(['5', '2', '3', '4'], columns=['films'])
  
  df_prev = worksheet.get_all_values()
  df = pd.DataFrame(data, columns=['films'])
  diff = df.merge(df_prev, on='films', how='outer', indicator=True)
  diff = diff[diff['_merge'] == 'left_only']
  
  #print(diff['films'].to_list())
  
  if not diff.empty:
    telegram_bot_sendtext(diff.to_string())

  worksheet.update([df.columns.values.tolist()] + df.values.tolist())

run_kinozal_scrapper()
