from bs4 import BeautifulSoup
import requests
import re
import pandas as pd

def telegram_bot_sendtext(bot_message):
  bot_token = secret.bot_token
  bot_chatID = secret.bot_chatID
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
  file_name='/content/drive/MyDrive/Colab Notebooks/links.xlsx'
  data = []
  soup = get_soup("https://kinozal.tv/top.php?j=&t=0&d=12&k=0&f=0&w=0&s=0")
  for link in soup.select('a[href^="/details.php"]'):
    title = str(link.get('title'))
    data.append(title)
  df_prev = pd.DataFrame(['1', '2', '3', '4'], columns=['films'])
  df = pd.DataFrame(['5', '2', '3', '4'], columns=['films'])
  #df_prev = pd.read_excel(file_name)
  #df = pd.DataFrame(data, columns=['films'])
  diff = df.merge(df_prev, on='films', how='outer', indicator=True)
  diff = diff[diff['_merge'] == 'left_only']
  #print(diff['films'].to_list())
  if not diff.empty:
    telegram_bot_sendtext(diff.to_string())

  #with pd.ExcelWriter(file_name, engine='openpyxl', mode='w') as writer:
  #    df.to_excel(writer, index=False, sheet_name='films')

run_kinozal_scrapper()
