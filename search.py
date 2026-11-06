import os
import urllib.parse
import browser_cookie3
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

load_dotenv()


class RuTrackerParser:

  def __init__(self):
    # Настоящие рабочие зеркала форума
    self.mirrors = [
        'https://rutracker.org',
        'https://rutracker.net',
    ]
    self.active_domain = self.mirrors[0]
    self.cookies = self._load_cookies()
    self.user_agent = os.getenv('USER_AGENT')

  def _load_cookies(self):
    env_session = os.getenv('RUTRACKER_SESSION')
    cf_token = os.getenv('CF_CLEARANCE')

    if env_session is None:
      print('1. Пытаюсь автоматически подтянуть куки из браузеров...')
      try:
        cj = browser_cookie3.load(domain_name='rutracker')
        cookies_dict = {cookie.name: cookie.value for cookie in cj}
        if 'bb_session' in cookies_dict or 'bb_data' in cookies_dict:
          print('   [Успех] Куки из браузера успешно подхвачены!')
          return cookies_dict
        print('   [!] Браузер прочитан, но ты не залогинен на RuTracker.')
      except Exception as e:
        print(f'   [!] Автоподбор не сработал: {e}')
      else: 
        print('2. Переключаюсь на резервные куки из файла .env...')

    if env_session != None:
      print(f'   [Успех] Использую ключ из .env ({env_session[:10]}...)')
      cookies_dict = {'bb_session': env_session}
      if cf_token:
        cookies_dict['cf_clearance'] = cf_token
      return cookies_dict

    print('   [Ошибка] В файле .env не найдена переменная RUTRACKER_SESSION!')
    return None

  def _fetch_page(self, endpoint):
    headers = {'User-Agent': self.user_agent} if self.user_agent else None

    for domain in self.mirrors:
      full_url = f"{domain}/{endpoint.lstrip('/')}"

      try:
        response = cffi_requests.get(
            full_url,
            cookies=self.cookies,
            headers=headers,
            impersonate='chrome',
            verify=False,
            timeout=10,
        )

        if response.status_code == 403:
          continue

        response.raise_for_status()
        self.active_domain = domain
        return response.content.decode('windows-1251', errors='ignore')

      except Exception:
        continue

    return None

  def search_topics(self, query, forum_ids=None):
    """Ищет раздачи по запросу с фильтрацией по выбранным разделам (forum_ids)."""
    if not self.cookies:
      print('Отмена: без кук RuTracker не отдаст результаты поиска.')
      return []

    encoded_query = query.encode('windows-1251')
    query_string = urllib.parse.quote(encoded_query)

    # Если переданы ID разделов, добавляем фильтр f=... к запросу
    if forum_ids:
      endpoint = f'forum/tracker.php?f={forum_ids}&nm={query_string}'
    else:
      endpoint = f'forum/tracker.php?nm={query_string}'

    html_text = self._fetch_page(endpoint)

    if not html_text:
      return []

    soup = BeautifulSoup(html_text, 'html.parser')
    links = soup.select('a.tLink, a.torTopic, a.topictitle')

    results = []
    seen_urls = set()

    for link in links:
      title = link.text.strip()
      href = link.get('href', '')

      if 'viewtopic.php?t=' in href and len(title) > 3 and '&start=' not in href:
        full_url = f"{self.active_domain}/forum/{href.lstrip('./')}"
        if full_url not in seen_urls:
          seen_urls.add(full_url)

          forum_name = 'Без раздела'
          row = link.find_parent('tr')
          if row:
            forum_tag = row.select_one('a.gen, td.f-name-col a, a[href*="?f="]')
            if forum_tag:
              forum_name = forum_tag.text.strip()

          results.append({
              'forum': forum_name,
              'title': title,
              'url': full_url,
          })

    return results


# --- СЛОВАРЬ РЕЖИМОВ (КАТЕГОРИЙ RUTRACKER) ---
CATEGORIES = {
    '1': {'name': 'Все разделы (без фильтра)', 'ids': None},
    '2': {
        'name': 'Все игры для ПК (сборный фильтр)',
        'ids': '635,50,52,51,53,54,127,646,647',
    },
    '3': {'name': 'Горячие новинки (Игры)', 'ids': '635'},
    '4': {'name': 'Ролевые игры (RPG)', 'ids': '52'},
    '5': {'name': 'Экшены (Шутеры / TPS)', 'ids': '50'},
    '6': {'name': 'Стратегии (RTS / TBS)', 'ids': '51'},
    '7': {'name': 'Симуляторы и Гонки', 'ids': '53,54'},
    '8': {'name': 'Приключения и Квесты', 'ids': '127'},
    '9': {'name': 'Игровые саундтреки (OST)', 'ids': '693,785'},
}


def choose_category():
  print('\n' + '=' * 50)
  print('  ВЫБЕРИ РЕЖИМ ПОИСКА (КАТЕГОРИЮ):')
  print('=' * 50)
  for key, val in CATEGORIES.items():
    print(f"  [{key}] {val['name']}")
  print('=' * 50)

  while True:
    choice = input('Введи номер режима (по умолчанию 2 — Все игры): ').strip()
    if not choice:
      return CATEGORIES['2']
    if choice in CATEGORIES:
      return CATEGORIES[choice]
    print('Неверный номер, выбери цифру из списка.')


# --- ИНТЕРАКТИВНЫЙ ЗАПУСК ---
if __name__ == '__main__':
  parser = RuTrackerParser()
  current_cat = choose_category()

  while True:
    print(f"\n[Активный режим: {current_cat['name']}]")
    print("Команды: 'режим' — сменить категорию | 'выход' — закрыть")
    query = input('Что ищем?: ').strip()

    if query.lower() in ('выход', 'exit', 'quit', 'q'):
      print('До встречи!')
      break

    if query.lower() in ('режим', 'mode', 'm', 'р'):
      current_cat = choose_category()
      continue

    if not query:
      print('Поисковый запрос не может быть пустым.')
      continue

    print(
        f'Ищу "{query}" в категории "{current_cat["name"]}"...'
    )
    results = parser.search_topics(query, forum_ids=current_cat['ids'])

    if not results:
      print('В этой категории ничего не найдено. Попробуй сменить режим (введи "режим").')
      continue

    total = len(results)
    page_size = 15
    current_index = 0

    print(f'\nВсего найдено раздач: {total}')

    while current_index < total:
      batch = results[current_index : current_index + page_size]
      print(
          f'\n--- Результаты {current_index + 1}–{current_index + len(batch)} из {total} ---\n'
      )

      for idx, item in enumerate(batch, current_index + 1):
        print(
            f"{idx}. [{item['forum']}]\n   {item['title']}\n  "
            f" Ссылка: {item['url']}\n"
        )

      current_index += page_size

      if current_index < total:
        next_action = input(
            f'Нажми [Enter] для следующих {min(page_size, total - current_index)} '
            'или введи "н" для нового поиска: '
        ).strip()

        if next_action.lower() != '':
          break
      else:
        print('--- Конец списка ---')