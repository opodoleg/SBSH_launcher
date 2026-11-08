import os
import re
import shutil
import subprocess
import urllib.parse
import browser_cookie3
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
import sys
from dotenv import load_dotenv

if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    load_dotenv(os.path.join(exe_dir, '.env'))
load_dotenv()

# Путь по умолчанию для загрузки игр/файлов (можно переопределить через .env TORRENT_SAVE_PATH)
DEFAULT_SAVE_PATH = os.getenv('TORRENT_SAVE_PATH', 'G:/games')


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

          topic_id = None
          match = re.search(r't=(\d+)', href)
          if match:
            topic_id = match.group(1)

          forum_name = 'Без раздела'
          row = link.find_parent('tr')
          if row:
            forum_tag = row.select_one('a.gen, td.f-name-col a, a[href*="?f="]')
            if forum_tag:
              forum_name = forum_tag.text.strip()

          results.append({
              'id': topic_id,
              'forum': forum_name,
              'title': title,
              'url': full_url,
          })

    return results

  def download_torrent(self, topic_id_or_url, download_dir='downloads', default_title=''):
    """Скачивает .torrent файл по ID топика или ссылке и сохраняет в папку download_dir."""
    if not self.cookies:
      print('   [Ошибка] Без кук авторизации RuTracker не отдаст .torrent файл.')
      return None

    if str(topic_id_or_url).isdigit():
      topic_id = str(topic_id_or_url)
    else:
      match = re.search(r't=(\d+)', str(topic_id_or_url))
      if match:
        topic_id = match.group(1)
      else:
        print(f'   [Ошибка] Не удалось определить ID темы из: {topic_id_or_url}')
        return None

    endpoint = f'forum/dl.php?t={topic_id}'
    headers = {'User-Agent': self.user_agent} if self.user_agent else None
    os.makedirs(download_dir, exist_ok=True)

    for domain in self.mirrors:
      full_url = f"{domain}/{endpoint.lstrip('/')}"
      try:
        response = cffi_requests.get(
            full_url,
            cookies=self.cookies,
            headers=headers,
            impersonate='chrome',
            verify=False,
            timeout=15,
        )

        if response.status_code == 403:
          continue

        response.raise_for_status()

        if not response.content.startswith(b'd8:') and 'bittorrent' not in response.headers.get('content-type', ''):
          continue

        self.active_domain = domain

        filename = None
        cd = response.headers.get('content-disposition', '')
        if cd:
          m_utf8 = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
          if m_utf8:
            filename = urllib.parse.unquote(m_utf8.group(1))
          else:
            m_fn = re.search(r'filename="?([^";]+)"?', cd)
            if m_fn:
              filename = m_fn.group(1)

        if not filename:
          base_title = default_title if default_title else f'rutracker_{topic_id}'
          filename = f'{base_title}.torrent'

        clean_filename = re.sub(r'[\\/*?:"<>|]', '_', filename).strip(' .')
        if not clean_filename.endswith('.torrent'):
          clean_filename += '.torrent'

        filepath = os.path.join(download_dir, clean_filename)
        with open(filepath, 'wb') as f:
          f.write(response.content)

        return filepath

      except Exception:
        continue

    return None


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


def add_to_qbittorrent(torrent_path, save_path=DEFAULT_SAVE_PATH):
  """Добавляет торрент в qBittorrent сразу в указанную папку без диалоговых окон выбора."""
  abs_path = os.path.abspath(torrent_path)
  save_path = os.path.abspath(save_path)
  os.makedirs(save_path, exist_ok=True)

  # 1. Добавление через Web API qBittorrent (если запущен веб-интерфейс, например, порт 1489 или 8080)
  # Этот метод добавляет торрент бесшумно в фоне сразу по указанному пути, минуя любые всплывающие окна
  qb_urls = [
      os.getenv('QB_URL', 'http://localhost:1489'),
      'http://localhost:8080',
  ]
  for qb_url in qb_urls:
    try:
      with open(abs_path, 'rb') as f:
        r = requests.post(
            f"{qb_url.rstrip('/')}/api/v2/torrents/add",
            files={'torrents': f},
            data={'savepath': save_path, 'autoTMM': 'false'},
            timeout=3,
        )
      if r.status_code == 200 and r.text.strip().lower() in ('ok.', 'ok'):
        return True
    except Exception:
      pass

  # 2. Добавление через CLI с флагами --save-path и --skip-dialog=true (пропуск окна подтверждения)
  candidates = [
      os.getenv('QBITTORRENT_PATH'),
      r'C:\Program Files\qBittorrent\qbittorrent.exe',
      r'C:\Program Files (x86)\qBittorrent\qbittorrent.exe',
      os.path.expandvars(r'%LOCALAPPDATA%\Programs\qBittorrent\qbittorrent.exe'),
      shutil.which('qbittorrent'),
  ]

  for exe in candidates:
    if exe and os.path.isfile(exe):
      try:
        subprocess.Popen([
            exe,
            f'--save-path={save_path}',
            '--skip-dialog=true',
            abs_path,
        ])
        return True
      except Exception:
        pass

  # 3. Резервный вариант через ассоциацию файла в Windows
  if hasattr(os, 'startfile'):
    try:
      os.startfile(abs_path)
      return True
    except Exception:
      pass

  return False


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

    while True:
      if current_index < total:
        batch = results[current_index : current_index + page_size]
        print(
            f'\n--- Результаты {current_index + 1}–{current_index + len(batch)} из {total} ---\n'
        )

        for idx, item in enumerate(batch, current_index + 1):
          print(
              f"{idx}. [{item['forum']}]\n   {item['title']}\n  "
              f" Ссылка: {item['url']}\n"
          )

        current_index += len(batch)

      if current_index < total:
        prompt_text = (
            f"Введи [номер] для скачивания (1–{total}), "
            f"[Enter] для следующих {min(page_size, total - current_index)} "
            "или 'н' для нового поиска: "
        )
      else:
        prompt_text = (
            f"--- Конец списка (всего {total}) ---\n"
            f"Введи [номер] для скачивания (1–{total}) или 'н' для нового поиска: "
        )

      user_input = input(prompt_text).strip()

      if not user_input:
        if current_index < total:
          continue
        else:
          break

      if user_input.lower() in ('н', 'n', 'новый', 'search'):
        break

      if user_input.lower() in ('выход', 'exit', 'quit', 'q'):
        print('До встречи!')
        exit(0)

      if user_input.lower() in ('режим', 'mode', 'm', 'р'):
        current_cat = choose_category()
        break

      if user_input.isdigit():
        choice_idx = int(user_input) - 1
        if 0 <= choice_idx < total:
          item = results[choice_idx]
          print(f"\n[+] Скачиваю торрент #{choice_idx + 1}: {item['title']}...")
          filepath = parser.download_torrent(
              item.get('id') or item['url'],
              default_title=item['title'],
          )
          if filepath:
            abs_path = os.path.abspath(filepath)
            print(f"    [Успех] Торрент-файл сохранён:\n    -> {abs_path}")
            if add_to_qbittorrent(abs_path, DEFAULT_SAVE_PATH):
              print(f"    [qBittorrent] Загрузка запущена! Папка сохранения: {DEFAULT_SAVE_PATH}")
            else:
              print("    [!] Не удалось передать файл в qBittorrent автоматически.")
          else:
            print("    [Ошибка] Не удалось скачать .torrent файл (проверьте сессию / доступ к сайту).")
          print()
        else:
          print(f"[!] Неверный номер. Введите число от 1 до {total}.")
      else:
        print("[!] Неизвестная команда. Введите номер раздачи, нажмите [Enter] или введите 'н'.")
