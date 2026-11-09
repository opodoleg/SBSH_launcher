"""
Модуль для поиска и прямого скачивания модов с Nexus Mods без платного API-ключа.
По умолчанию настроен для Cyberpunk 2077 (game_domain = "cyberpunk2077", game_id = 3333).

Поддерживает:
- Автоматический подбор кук авторизации через browser_cookie3 по образцу search.py
- Фильтрацию только 5 ключевых кук: nexusmods_session, nexusmods_session_refresh, PHPSESSID, cf_clearance, __cf_bm
- Резервное чтение кук из переменной NEXUS_COOKIES в .env
- Передачу кук в сессию curl_cffi.requests.Session(impersonate="chrome") для обхода Cloudflare
- Быстрый поиск модов через публичный GraphQL API v2 Nexus Mods
- Парсинг вкладки Files (?tab=files) с получением списка доступных файлов и их file_id
- Обход таймера Slow Download через внутренний эндпоинт GenerateDownloadUrl
- Потоковое скачивание файлов с отображением прогресс-бара
- Интерактивный консольный режим при прямом запуске
"""

import json
import os
import re
import sys
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional, Tuple

import browser_cookie3
from bs4 import BeautifulSoup
from curl_cffi import requests
from dotenv import load_dotenv

# Поддержка загрузки .env при обычном запуске и внутри скомпилированного EXE
if getattr(sys, "frozen", False):
    exe_dir = os.path.dirname(sys.executable)
    load_dotenv(os.path.join(exe_dir, ".env"))
else:
    load_dotenv()

# Настройки по умолчанию
DEFAULT_GAME_DOMAIN = "cyberpunk2077"
DEFAULT_GAME_ID = 3333
GRAPHQL_ENDPOINT = "https://api.nexusmods.com/v2/graphql"
GENERATE_DOWNLOAD_URL_ENDPOINT = (
    "https://www.nexusmods.com/Core/Libs/Common/Managers/Downloads?GenerateDownloadUrl"
)

# 5 целевых ключей кук Nexus Mods
TARGET_COOKIE_KEYS = {
    "nexusmods_session",
    "nexusmods_session_refresh",
    "PHPSESSID",
    "cf_clearance",
    "__cf_bm",
}

# Категории модов Nexus Mods для предустановленных игр
NEXUS_CATEGORIES_BY_GAME: Dict[str, List[Tuple[str, str]]] = {
    "cyberpunk2077": [
        ("all", "🌟 Все категории (Топ модов)"),
        ("Modders Resources", "📦 Основа и библиотеки (CET, ArchiveXL, RED4ext, TweakXL)"),
        ("Gameplay", "🎮 Геймплей и механики"),
        ("Armour and Clothing", "👕 Броня и одежда"),
        ("Appearance", "💇 Внешность и тело"),
        ("Visuals and Graphics", "🎨 Графика, свет и Reshade"),
        ("User Interface", "🖥 Интерфейс и HUD"),
        ("Characters", "👥 Персонажи и пресеты"),
        ("Vehicles", "🚗 Транспорт и управление"),
        ("Weapons", "🔫 Оружие"),
        ("Audio", "🎵 Музыка, радио и звуки"),
        ("Animations", "🎬 Анимации"),
        ("Utilities", "🛠 Утилиты и инструменты"),
        ("Apartment", "🏢 Квартиры и интерьеры"),
        ("Locations", "🗺 Локации"),
        ("Miscellaneous", "🧩 Разное"),
    ],
    "witcher3": [
        ("all", "🌟 Все категории (Топ модов)"),
        ("Gameplay Changes", "🎮 Изменения геймплея"),
        ("Models and Textures", "🎨 Модели и текстуры"),
        ("User Interface", "🖥 Интерфейс"),
        ("Visuals and Graphics", "✨ Графика и освещение"),
        ("Utilities", "🛠 Утилиты"),
        ("Skills and Leveling", "⚔ Прокачка и навыки"),
        ("Inventory", "🎒 Инвентарь"),
        ("Items", "📦 Предметы"),
        ("Characters", "👥 Персонажи"),
        ("Tweaks", "⚙ Твики"),
        ("Immersion", "🕯 Погружение"),
    ],
    "skyrimspecialedition": [
        ("all", "🌟 Все категории (Топ модов)"),
        ("Gameplay", "🎮 Геймплей"),
        ("Modders Resources", "📦 Основа и ресурсы"),
        ("Bug Fixes", "🔧 Исправления ошибок"),
        ("Patches", "🩹 Патчи"),
        ("User Interface", "🖥 Интерфейс"),
        ("Models and Textures", "🎨 Текстуры и модели"),
        ("Body, Face, and Hair", "💇 Внешность и тела"),
        ("Followers & Companions", "👥 Спутники и компаньоны"),
        ("Environmental", "🌲 Окружение и природа"),
        ("Animation", "🎬 Анимации"),
        ("Magic - Spells & Enchantments", "✨ Магия и заклинания"),
        ("Utilities", "🛠 Утилиты"),
    ],
    "fallout4": [
        ("all", "🌟 Все категории (Топ модов)"),
        ("Gameplay Effects and Changes", "🎮 Геймплей"),
        ("Weapons", "🔫 Оружие"),
        ("Armour", "🛡 Броня и экипировка"),
        ("Body, Face, and Hair", "💇 Внешность"),
        ("Modders Resources and Tutorials", "📦 Ресурсы"),
        ("User Interface", "🖥 Интерфейс"),
        ("Player Settlement", "🏘 Поселения"),
        ("Models and Textures", "🎨 Текстуры и модели"),
        ("Weather and Lighting", "🌦 Погода и освещение"),
        ("Utilities", "🛠 Утилиты"),
        ("Bug Fixes", "🔧 Исправления ошибок"),
    ],
    "starfield": [
        ("all", "🌟 Все категории (Топ модов)"),
        ("Gameplay", "🎮 Геймплей"),
        ("Ships", "🚀 Космические корабли"),
        ("User Interface", "🖥 Интерфейс"),
        ("Visuals", "🎨 Графика"),
        ("Clothing", "👕 Одежда"),
        ("Utilities", "🛠 Утилиты"),
        ("Modders Resources", "📦 Ресурсы"),
    ],
    "baldursgate3": [
        ("all", "🌟 Все категории (Топ модов)"),
        ("Gameplay", "🎮 Геймплей"),
        ("Classes", "🧙 Классы и подклассы"),
        ("Spells", "✨ Заклинания"),
        ("Races", "🧝 Расы"),
        ("Character Customisation", "💇 Кастомизация персонажей"),
        ("Equipment", "🛡 Снаряжение"),
        ("User Interface", "🖥 Интерфейс"),
        ("Utilities", "🛠 Утилиты"),
    ],
}


def get_categories_for_game(domain: str) -> List[Tuple[str, str]]:
    """Возвращает список категорий (raw_category, display_title) для выбранной игры."""
    return NEXUS_CATEGORIES_BY_GAME.get(
        domain,
        [("all", "🌟 Все категории (Топ модов)")]
    )


def bbcode_to_html(bbcode_text: str) -> str:
    """Преобразует BBCode разметку Nexus Mods в чистый HTML для отображения в QTextBrowser."""
    if not bbcode_text:
        return ""
    text = bbcode_text

    # Удаляем служебные теги спойлеров/заголовков
    text = re.sub(r'\[size=[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[/size\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[font=[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[/font\]', '', text, flags=re.IGNORECASE)

    # Жирный, курсив, подчёркивание, зачёркнутый
    text = re.sub(r'\[b\](.*?)\[/b\]', r'<b>\1</b>', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[i\](.*?)\[/i\]', r'<i>\1</i>', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[u\](.*?)\[/u\]', r'<u>\1</u>', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[s\](.*?)\[/s\]', r'<s>\1</s>', text, flags=re.DOTALL | re.IGNORECASE)

    # Ссылки
    text = re.sub(r'\[url=(https?://[^\]]+)\](.*?)\[/url\]', r'<a href="\1" style="color: #60a5fa; text-decoration: underline;">\2</a>', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[url\](https?://.*?)\[/url\]', r'<a href="\1" style="color: #60a5fa; text-decoration: underline;">\1</a>', text, flags=re.DOTALL | re.IGNORECASE)

    # Цвета
    text = re.sub(r'\[color=([#a-zA-Z0-9]+)\](.*?)\[/color\]', r'<span style="color: \1;">\2</span>', text, flags=re.DOTALL | re.IGNORECASE)

    # Центрирование
    text = re.sub(r'\[center\](.*?)\[/center\]', r'<div align="center">\1</div>', text, flags=re.DOTALL | re.IGNORECASE)

    # Цитаты
    text = re.sub(r'\[quote\](.*?)\[/quote\]', r'<blockquote style="border-left: 3px solid #6366f1; margin: 4px 0; padding-left: 8px; color: #94a3b8;">\1</blockquote>', text, flags=re.DOTALL | re.IGNORECASE)

    # Списки
    text = re.sub(r'\[list\](.*?)\[/list\]', r'<ul>\1</ul>', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[\*\](.*?)(?=\[\*\]|</ul>|$)', r'<li>\1</li>', text, flags=re.DOTALL | re.IGNORECASE)

    # Удаляем inline картинки из текста описания (скриншоты выводятся в отдельной галерее)
    text = re.sub(r'\[img\].*?\[/img\]', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Переносы строк
    text = text.replace('\r\n', '<br>').replace('\n', '<br>')
    text = re.sub(r'(<br\s*/?>\s*){3,}', '<br><br>', text)

    return text.strip()


def format_bytes(size_bytes: int) -> str:
    """Форматирует байты в удобные КБ, МБ, ГБ."""
    if not size_bytes or size_bytes <= 0:
        return "0 Б"
    for unit in ["Б", "КБ", "МБ", "ГБ", "ТБ"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} ПБ"


def parse_cookie_string(raw_str: str) -> Dict[str, str]:
    """Преобразует строку кук (формат Header или JSON) в словарь."""
    raw_str = raw_str.strip()
    if not raw_str:
        return {}

    # Проверка на JSON-формат
    if raw_str.startswith("{") and raw_str.endswith("}"):
        try:
            data = json.loads(raw_str)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass

    # Формат 'cookie1=val1; cookie2=val2'
    result = {}
    for part in raw_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
        elif part and "nexusmods_session" not in result:
            result["nexusmods_session"] = part
    return result


def get_nexus_cookies_and_session() -> Tuple[requests.Session, Dict[str, str], str]:
    """
    Автоматическое получение кук для nexusmods.com по образцу search.py.
    1. Использует browser_cookie3 для извлечения кук домена .nexusmods.com.
    2. Фильтрует строго 5 нужных ключей:
       - nexusmods_session
       - nexusmods_session_refresh
       - PHPSESSID
       - cf_clearance
       - __cf_bm
    3. Если browser_cookie3 не смог прочитать базу браузера, берёт куки из NEXUS_COOKIES в .env.
    4. Определяет подходящий профиль impersonate для curl_cffi в соответствии с браузером кук
       (например, 'firefox133' для Firefox или 'chrome124' для Chromium/Edge/Brave).
    Возвращает кортеж (session, cookies_dict, impersonate_profile).
    """
    cookies_dict: Dict[str, str] = {}
    source_browser: str = "chrome"

    print("1. Пытаюсь автоматически подтянуть куки из браузеров...")
    raw_cookies: Dict[str, str] = {}

    # Перебираем браузеры по отдельности
    loaders = [
        ("firefox", getattr(browser_cookie3, "firefox", None)),
        ("chrome", getattr(browser_cookie3, "chrome", None)),
        ("edge", getattr(browser_cookie3, "edge", None)),
        ("brave", getattr(browser_cookie3, "brave", None)),
        ("opera", getattr(browser_cookie3, "opera", None)),
        ("vivaldi", getattr(browser_cookie3, "vivaldi", None)),
        ("chromium", getattr(browser_cookie3, "chromium", None)),
    ]
    for b_name, loader in loaders:
        if not loader:
            continue
        try:
            cj = loader(domain_name="nexusmods.com")
            b_cookies = {c.name: c.value for c in cj}
            if any(k in b_cookies for k in TARGET_COOKIE_KEYS):
                raw_cookies = b_cookies
                source_browser = b_name
                break
        except Exception:
            continue

    if not raw_cookies:
        # Попытка общего вызова если одиночные не вернули
        try:
            cj = browser_cookie3.load(domain_name="nexusmods.com")
            raw_cookies = {c.name: c.value for c in cj}
        except Exception:
            pass

    if raw_cookies:
        # Фильтруем строго 5 целевых ключей
        cookies_dict = {k: v for k, v in raw_cookies.items() if k in TARGET_COOKIE_KEYS}
        if cookies_dict:
            print(f"   [Успех] Куки из браузера ({source_browser}) успешно подхвачены! (найдено {len(cookies_dict)} из {len(TARGET_COOKIE_KEYS)})")
        else:
            print("   [!] Браузер прочитан, но целевые куки Nexus Mods не найдены.")
    else:
        print("   [!] Автоподбор из браузеров не сработал.")

    # 2. Если в браузере кук нет, переключаемся на резервные куки из .env (по образцу search.py)
    if not cookies_dict:
        print("2. Переключаюсь на резервные куки из файла .env...")
        env_raw = os.getenv("NEXUS_COOKIES") or os.getenv("NEXUS_SESSION") or ""
        if env_raw.strip():
            parsed = parse_cookie_string(env_raw)
            cookies_dict = {k: v for k, v in parsed.items() if k in TARGET_COOKIE_KEYS}
            if cookies_dict:
                print(f"   [Успех] Использую куки из .env (найдено {len(cookies_dict)} из {len(TARGET_COOKIE_KEYS)})")
            else:
                print("   [!] В переменной NEXUS_COOKIES не найдены целевые ключи.")
        else:
            print("   [Ошибка] В файле .env не найдена переменная NEXUS_COOKIES!")

    # 3. Подбираем impersonate под найденный браузер (чтобы Cloudflare не блокировал из-за несоответствия TLS и cf_clearance)
    if source_browser == "firefox":
        impersonate_target = "firefox133"
    elif source_browser == "edge":
        impersonate_target = "edge101"
    else:
        impersonate_target = "chrome124"

    session = requests.Session(impersonate=impersonate_target)
    session.headers.update({
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    })

    if cookies_dict:
        session.cookies.update(cookies_dict)

    return session, cookies_dict, impersonate_target


class NexusAPI:
    """Клиент для работы с сайтом Nexus Mods без официального платного API-ключа."""

    def __init__(
        self,
        game_domain: str = DEFAULT_GAME_DOMAIN,
        game_id: int = DEFAULT_GAME_ID,
    ):
        self.game_domain = game_domain
        self.game_id = game_id
        self._lock = threading.RLock()

        # Автоматическое получение кук и инициализация сессии
        self.session, self.cookies, self.impersonate = get_nexus_cookies_and_session()

    def create_session(self) -> requests.Session:
        """Создает новый независимый поток-локальный экземпляр сессии curl_cffi с куками."""
        imp = getattr(self, "impersonate", "chrome124") or "chrome124"
        s = requests.Session(impersonate=imp)
        s.headers.update({
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        })
        if self.cookies:
            s.cookies.update(self.cookies)
        return s

    def search_mods(
        self,
        query: str = "",
        category: Optional[str] = None,
        game_id: Optional[int] = None,
        game_domain: Optional[str] = None,
        count: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Ищет моды по ключевому слову, категории или ссылке через публичный GraphQL v2 API Nexus Mods.
        Если query пустой, возвращает самые популярные моды (по количеству скачиваний).
        Возвращает список словарей: title, mod_id, author, summary, category, url, downloads, picture_url.
        """
        gid = game_id if game_id is not None else self.game_id
        domain = game_domain or self.game_domain
        text = (query or "").strip()

        # Если введена прямая ссылка на мод, извлекаем mod_id
        url_match = re.search(r"/mods/(\d+)", text)
        if url_match:
            mod_id = int(url_match.group(1))
            return self.get_mod_by_id(mod_id, gid, domain)

        # Если введено чисто число, пробуем сначала поиск по mod_id
        if text.isdigit():
            mod_results = self.get_mod_by_id(int(text), gid, domain)
            if mod_results:
                return mod_results

        # Формируем GraphQL запрос с сортировкой по популярности (downloads DESC)
        graphql_query = """
        query SearchMods($filter: ModsFilter, $sort: [ModsSort!], $count: Int) {
          mods(filter: $filter, sort: $sort, count: $count) {
            nodes {
              modId
              name
              summary
              author
              downloads
              pictureUrl
              category
              game {
                domainName
                id
              }
            }
            totalCount
          }
        }
        """

        filter_obj: Dict[str, Any] = {
            "gameId": {"value": str(gid), "op": "EQUALS"},
        }
        if text:
            filter_obj["name"] = {"value": text, "op": "WILDCARD"}

        if category and category.lower() not in ("all", "все", "все категории", ""):
            filter_obj["categoryName"] = {"value": category.strip(), "op": "EQUALS"}

        variables = {
            "filter": filter_obj,
            "sort": [
                {"downloads": {"direction": "DESC"}}
            ],
            "count": count,
        }

        with self._lock:
            try:
                resp = self.session.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": graphql_query, "variables": variables},
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    nodes = data.get("data", {}).get("mods", {}).get("nodes", [])
                    results = []
                    for n in nodes:
                        m_id = n.get("modId")
                        if not m_id:
                            continue
                        results.append({
                            "title": n.get("name", f"Mod {m_id}"),
                            "mod_id": m_id,
                            "author": n.get("author", "Неизвестен"),
                            "summary": n.get("summary", ""),
                            "category": n.get("category", "") or "Разное",
                            "downloads": n.get("downloads", 0),
                            "url": f"https://www.nexusmods.com/{domain}/mods/{m_id}",
                            "picture_url": n.get("pictureUrl", ""),
                            "game_domain": domain,
                            "game_id": gid,
                        })
                    return results

            except Exception as e:
                print(f"[NexusAPI] Ошибка GraphQL поиска: {e}")

        # Резервный вариант: веб-поиск через HTML страницу
        return self._fallback_web_search(text, gid, domain)

    def get_mod_by_id(
        self,
        mod_id: int,
        game_id: Optional[int] = None,
        game_domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Ищет один конкретный мод по его числовому ID."""
        gid = game_id if game_id is not None else self.game_id
        domain = game_domain or self.game_domain

        graphql_query = """
        query GetModById($filter: ModsFilter) {
          mods(filter: $filter) {
            nodes {
              modId
              name
              summary
              author
              downloads
              pictureUrl
              category
              game {
                domainName
                id
              }
            }
          }
        }
        """

        variables = {
            "filter": {
                "gameId": {"value": str(gid), "op": "EQUALS"},
                "modId": {"value": str(mod_id), "op": "EQUALS"},
            }
        }

        with self._lock:
            try:
                resp = self.session.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": graphql_query, "variables": variables},
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    nodes = resp.json().get("data", {}).get("mods", {}).get("nodes", [])
                    if nodes:
                        n = nodes[0]
                        return [{
                            "title": n.get("name", f"Mod {mod_id}"),
                            "mod_id": n.get("modId", mod_id),
                            "author": n.get("author", "Неизвестен"),
                            "summary": n.get("summary", ""),
                            "category": n.get("category", "") or "Разное",
                            "downloads": n.get("downloads", 0),
                            "url": f"https://www.nexusmods.com/{domain}/mods/{mod_id}",
                            "picture_url": n.get("pictureUrl", ""),
                            "game_domain": domain,
                            "game_id": gid,
                        }]
            except Exception:
                pass

        return [{
            "title": f"Nexus Mod #{mod_id}",
            "mod_id": mod_id,
            "author": "Nexus Mods",
            "summary": "",
            "category": "Nexus",
            "downloads": 0,
            "url": f"https://www.nexusmods.com/{domain}/mods/{mod_id}",
            "picture_url": "",
            "game_domain": domain,
            "game_id": gid,
        }]

    def search_collections(
        self,
        query: str = "",
        sort: str = "downloads",
        game_id: Optional[int] = None,
        game_domain: Optional[str] = None,
        count: int = 25,
    ) -> List[Dict[str, Any]]:
        """
        Поиск коллекций модов Nexus Mods через GraphQL CollectionsV2.
        Возвращает список словарей с описанием коллекций:
        - id, name, slug, summary, downloads, endorsements, tile_image, header_image,
          user, revision_number, mod_count, total_size_bytes, total_size_human,
          download_link, url, game_domain, game_id.
        """
        gid = game_id if game_id is not None else self.game_id
        domain = game_domain or self.game_domain
        text = (query or "").strip()

        graphql_query = """
        query SearchCollections($filter: CollectionsSearchFilter, $sort: [CollectionsSearchSort!], $count: Int) {
          collectionsV2(filter: $filter, sort: $sort, count: $count) {
            nodes {
              id
              name
              slug
              summary
              totalDownloads
              endorsements
              tileImage {
                url
              }
              headerImage {
                url
              }
              user {
                name
                avatar
              }
              latestPublishedRevision {
                revisionNumber
                modCount
                totalSize
                downloadLink
              }
            }
            totalCount
          }
        }
        """

        filter_obj: Dict[str, Any] = {
            "gameId": {"value": str(gid), "op": "EQUALS"},
            "hasPublishedRevision": {"value": True, "op": "EQUALS"},
        }
        if text:
            filter_obj["name"] = {"value": text, "op": "WILDCARD"}

        sort_map = {
            "downloads": {"downloads": {"direction": "DESC"}},
            "endorsements": {"endorsements": {"direction": "DESC"}},
            "rating": {"rating": {"direction": "DESC"}},
            "recent": {"updatedAt": {"direction": "DESC"}},
            "new": {"createdAt": {"direction": "DESC"}},
        }
        sort_obj = [sort_map.get(sort, {"downloads": {"direction": "DESC"}})]

        variables = {
            "filter": filter_obj,
            "sort": sort_obj,
            "count": count,
        }

        with self._lock:
            try:
                resp = self.session.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": graphql_query, "variables": variables},
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    nodes = data.get("data", {}).get("collectionsV2", {}).get("nodes", [])
                    results = []
                    for n in nodes:
                        c_id = n.get("id")
                        rev = n.get("latestPublishedRevision") or {}
                        raw_size = rev.get("totalSize")
                        try:
                            size_int = int(raw_size) if raw_size else 0
                        except (ValueError, TypeError):
                            size_int = 0

                        tile_img = n.get("tileImage") or {}
                        header_img = n.get("headerImage") or {}
                        user_obj = n.get("user") or {}

                        results.append({
                            "type": "collection",
                            "id": c_id,
                            "name": n.get("name", f"Collection {c_id}"),
                            "title": n.get("name", f"Collection {c_id}"),
                            "slug": n.get("slug", ""),
                            "summary": n.get("summary", ""),
                            "downloads": n.get("totalDownloads", 0),
                            "endorsements": n.get("endorsements", 0),
                            "tile_image": tile_img.get("url", ""),
                            "header_image": header_img.get("url", ""),
                            "user": user_obj.get("name", "Неизвестен"),
                            "author": user_obj.get("name", "Неизвестен"),
                            "user_avatar": user_obj.get("avatar", ""),
                            "revision_number": rev.get("revisionNumber", 1),
                            "mod_count": rev.get("modCount", 0),
                            "total_size_bytes": size_int,
                            "total_size_human": format_bytes(size_int),
                            "download_link": rev.get("downloadLink", ""),
                            "url": f"https://next.nexusmods.com/{domain}/collections/{n.get('slug', '')}",
                            "game_domain": domain,
                            "game_id": gid,
                        })
                    return results
            except Exception as e:
                print(f"[NexusAPI] Ошибка поиска коллекций: {e}")

        return []

    def get_collection_details(
        self,
        slug: str,
        game_domain: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Возвращает подробные данные о коллекции и полный список файлов модов в последней опубликованной ревизии.
        """
        if not slug:
            return None

        domain = game_domain or self.game_domain

        graphql_query = """
        query GetCollectionBySlug($slug: String!) {
          collection(slug: $slug) {
            id
            name
            slug
            summary
            description
            totalDownloads
            endorsements
            tileImage {
              url
            }
            headerImage {
              url
            }
            user {
              name
              avatar
            }
            latestPublishedRevision {
              id
              revisionNumber
              modCount
              totalSize
              downloadLink
              modFiles {
                fileId
                gameId
                optional
                version
                file {
                  id
                  name
                  sizeInBytes
                  version
                  modId
                  mod {
                    name
                    pictureUrl
                    author
                    summary
                  }
                }
              }
            }
          }
        }
        """

        with self._lock:
            try:
                resp = self.session.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": graphql_query, "variables": {"slug": slug}},
                    headers={"Content-Type": "application/json"},
                    timeout=20,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    c = data.get("data", {}).get("collection")
                    if not c:
                        return None

                    rev = c.get("latestPublishedRevision") or {}
                    raw_size = rev.get("totalSize")
                    try:
                        total_bytes = int(raw_size) if raw_size else 0
                    except (ValueError, TypeError):
                        total_bytes = 0

                    tile_img = c.get("tileImage") or {}
                    header_img = c.get("headerImage") or {}
                    user_obj = c.get("user") or {}

                    raw_mods = rev.get("modFiles") or []
                    parsed_mods = []
                    for idx, mf in enumerate(raw_mods):
                        f_info = mf.get("file") or {}
                        m_info = f_info.get("mod") or {}

                        f_size_raw = f_info.get("sizeInBytes")
                        try:
                            f_size_bytes = int(f_size_raw) if f_size_raw else 0
                        except (ValueError, TypeError):
                            f_size_bytes = 0

                        mod_id = f_info.get("modId")
                        file_id = mf.get("fileId")

                        parsed_mods.append({
                            "index": idx + 1,
                            "file_id": file_id,
                            "game_id": mf.get("gameId") or self.game_id,
                            "mod_id": mod_id,
                            "name": f_info.get("name") or (m_info.get("name") if m_info else f"Mod {mod_id}"),
                            "mod_name": m_info.get("name") or (f_info.get("name") if f_info else f"Mod {mod_id}"),
                            "author": m_info.get("author") or "Неизвестен",
                            "version": mf.get("version") or f_info.get("version") or "",
                            "size_bytes": f_size_bytes,
                            "size_human": format_bytes(f_size_bytes),
                            "optional": bool(mf.get("optional", False)),
                            "picture_url": m_info.get("pictureUrl") or "",
                            "summary": m_info.get("summary") or "",
                            "mod_url": f"https://www.nexusmods.com/{domain}/mods/{mod_id}" if mod_id else "",
                        })

                    return {
                        "type": "collection",
                        "id": c.get("id"),
                        "slug": c.get("slug", slug),
                        "name": c.get("name", "Без названия"),
                        "title": c.get("name", "Без названия"),
                        "summary": c.get("summary", ""),
                        "description": c.get("description", ""),
                        "downloads": c.get("totalDownloads", 0),
                        "endorsements": c.get("endorsements", 0),
                        "tile_image": tile_img.get("url", ""),
                        "header_image": header_img.get("url", ""),
                        "user": user_obj.get("name", "Неизвестен"),
                        "author": user_obj.get("name", "Неизвестен"),
                        "user_avatar": user_obj.get("avatar", ""),
                        "revision_number": rev.get("revisionNumber", 1),
                        "mod_count": rev.get("modCount", len(parsed_mods)),
                        "total_size_bytes": total_bytes,
                        "total_size_human": format_bytes(total_bytes),
                        "download_link": rev.get("downloadLink", ""),
                        "url": f"https://next.nexusmods.com/{domain}/collections/{slug}",
                        "game_domain": domain,
                        "mods": parsed_mods,
                    }
            except Exception as e:
                print(f"[NexusAPI] Ошибка получения деталей коллекции: {e}")

        return None

    def get_collection_manifest_url(self, download_link: str) -> Optional[str]:
        """
        Запрашивает прямую CDN ссылку для скачивания файла манифеста коллекции (.7z).
        """
        if not download_link:
            return None

        clean_link = download_link if download_link.startswith("/") else f"/{download_link}"
        url = f"https://api.nexusmods.com{clean_link}"

        with self._lock:
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    dl_links = data.get("download_links") or []
                    if dl_links and "URI" in dl_links[0]:
                        return dl_links[0]["URI"]
            except Exception as e:
                print(f"[NexusAPI] Ошибка получения ссылки на манифест: {e}")

        return None

    def _fallback_web_search(self, query: str, game_id: int, game_domain: str) -> List[Dict[str, Any]]:
        """Резервный парсинг HTML выдачи при сбое GraphQL."""
        with self._lock:
            try:
                if query:
                    url = f"https://www.nexusmods.com/{game_domain}/mods/?search%5Bname%5D={urllib.parse.quote(query)}"
                else:
                    url = f"https://www.nexusmods.com/{game_domain}/mods/"
                resp = self.session.get(url, timeout=15)
                if resp.status_code != 200:
                    return []

                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                seen_ids = set()

                for link in soup.select('a[href*="/mods/"]'):
                    href = link.get("href", "")
                    m = re.search(r"/mods/(\d+)", href)
                    if not m:
                        continue
                    mid = int(m.group(1))
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)

                    title = link.get_text(strip=True)
                    if not title or len(title) < 2 or title.lower() in ["mods", "files", "images"]:
                        continue

                    results.append({
                        "title": title,
                        "mod_id": mid,
                        "author": "Nexus Mods",
                        "summary": "",
                        "category": "Nexus",
                        "downloads": 0,
                        "url": f"https://www.nexusmods.com/{game_domain}/mods/{mid}",
                        "picture_url": "",
                        "game_domain": game_domain,
                        "game_id": game_id,
                    })

                return results
            except Exception:
                return []

    def get_mod_details(
        self,
        mod_id: int | str,
        game_id: Optional[int] = None,
        game_domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Получает подробную информацию о моде: полное описание (из GraphQL) и
        скриншоты (парсинг вкладки ?tab=images на сайте Nexus Mods).

        Возвращает словарь:
        - description: str   — полное описание мода (BBCode/HTML из Nexus)
        - screenshots: list  — список URL'ов полноразмерных скриншотов
        - thumbnails: list   — список URL'ов превьюшек
        - picture_url: str   — основное изображение мода
        - version: str       — версия мода
        - endorsements: int  — количество одобрений
        - updated_at: str    — дата последнего обновления
        """
        gid = game_id if game_id is not None else self.game_id
        domain = game_domain or self.game_domain
        mid = int(mod_id)

        result: Dict[str, Any] = {
            "description": "",
            "screenshots": [],
            "thumbnails": [],
            "screenshot_titles": [],
            "picture_url": "",
            "version": "",
            "endorsements": 0,
            "updated_at": "",
        }

        # 1) Получаем описание и метаданные из GraphQL
        graphql_query = """
        query GetModDetails($filter: ModsFilter) {
          mods(filter: $filter) {
            nodes {
              modId
              name
              description
              summary
              pictureUrl
              version
              endorsements
              updatedAt
              author
              category
              downloads
            }
          }
        }
        """

        variables = {
            "filter": {
                "gameId": {"value": str(gid), "op": "EQUALS"},
                "modId": {"value": str(mid), "op": "EQUALS"},
            }
        }

        with self._lock:
            try:
                resp = self.session.post(
                    GRAPHQL_ENDPOINT,
                    json={"query": graphql_query, "variables": variables},
                    headers={"Content-Type": "application/json"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    nodes = resp.json().get("data", {}).get("mods", {}).get("nodes", [])
                    if nodes:
                        n = nodes[0]
                        result["description"] = n.get("description", "")
                        result["picture_url"] = n.get("pictureUrl", "")
                        result["version"] = n.get("version", "")
                        result["endorsements"] = n.get("endorsements", 0)
                        result["updated_at"] = n.get("updatedAt", "")
            except Exception as e:
                print(f"[NexusAPI] Ошибка получения описания мода: {e}")

            # 2) Парсим скриншоты со вкладки ?tab=images
            try:
                images_url = f"https://www.nexusmods.com/{domain}/mods/{mid}?tab=images"
                resp_img = self.session.get(images_url, timeout=15)
                if resp_img.status_code == 200:
                    soup = BeautifulSoup(resp_img.text, "html.parser")
                    image_items = []
                    seen_full = set()

                    for a_tag in soup.select("a.mod-image, a[href*='/images/']"):
                        href = a_tag.get("href", "")
                        if ("staticdelivery.nexusmods.com/mods/" in href
                                and "/images/" in href
                                and "/thumbnails/" not in href
                                and "/headers/" not in href
                                and href not in seen_full):
                            seen_full.add(href)
                            thumb = a_tag.get("data-exthumbimage", "")
                            if not thumb:
                                img = a_tag.find("img")
                                if img:
                                    thumb = img.get("src", "")

                            title_el = a_tag.select_one(".tile-name, p.tile-name, .thumb-caption")
                            stitle = title_el.get_text(strip=True) if title_el else ""
                            if not stitle:
                                raw_sub = a_tag.get("data-sub-html", "") or a_tag.get("title", "")
                                stitle = re.sub(r"<[^>]+>", "", raw_sub).strip()

                            image_items.append({
                                "full": href,
                                "thumb": thumb or href,
                                "title": stitle,
                            })

                    if not image_items:
                        for a_tag in soup.select("a"):
                            href = a_tag.get("href", "")
                            if ("staticdelivery.nexusmods.com/mods/" in href
                                    and "/images/" in href
                                    and "/thumbnails/" not in href
                                    and "/headers/" not in href
                                    and href not in seen_full):
                                seen_full.add(href)
                                image_items.append({"full": href, "thumb": href, "title": ""})

                    for idx, it in enumerate(image_items[:20], 1):
                        result["screenshots"].append(it["full"])
                        result["thumbnails"].append(it["thumb"])
                        result["screenshot_titles"].append(it["title"] or f"Скриншот {idx}")
            except Exception as e:
                print(f"[NexusAPI] Ошибка парсинга скриншотов: {e}")

        # Если скриншоты со страницы не спарсились, используем главное изображение мода
        if not result["screenshots"] and result["picture_url"]:
            result["screenshots"].append(result["picture_url"])
            result["thumbnails"].append(result["picture_url"])
            result["screenshot_titles"].append("Обложка мода")
        elif not result["thumbnails"] and result["screenshots"]:
            result["thumbnails"] = list(result["screenshots"])
            result["screenshot_titles"] = [f"Скриншот {i+1}" for i in range(len(result["screenshots"]))]

        result["html_description"] = bbcode_to_html(result["description"])

        return result

    def get_mod_files(
        self,
        mod_id: int | str,
        game_domain: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Парсит вкладку Files (?tab=files) страницы мода и возвращает список доступных для скачивания файлов:
        - file_id (data-id)
        - name (data-name)
        - version (data-version)
        - size_kb, size_human
        - date
        - description
        """
        domain = game_domain or self.game_domain
        url = f"https://www.nexusmods.com/{domain}/mods/{mod_id}?tab=files"

        with self._lock:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                print(f"[NexusAPI] [!] Ошибка открытия вкладки файлов: статус {resp.status_code}")
                return []
            text_html = resp.text

        soup = BeautifulSoup(text_html, "html.parser")
        files: List[Dict[str, Any]] = []

        headers = soup.select("dt.file-expander-header")
        for dt in headers:
            fid = dt.get("data-id")
            if not fid:
                continue

            name = dt.get("data-name")
            if not name:
                p_el = dt.select_one("p, h3, .name")
                name = p_el.get_text(strip=True) if p_el else f"File {fid}"

            version = dt.get("data-version", "")
            size_kb_str = dt.get("data-size", "0")
            try:
                size_kb = int(size_kb_str)
                size_bytes = size_kb * 1024
            except ValueError:
                size_kb = 0
                size_bytes = 0

            date_str = dt.get("data-date", "")

            desc = ""
            dd = soup.select_one(f"dd[data-id='{fid}']")
            if dd:
                desc_el = dd.select_one(".files-description, .description")
                if desc_el:
                    desc = desc_el.get_text(strip=True)

            files.append({
                "file_id": int(fid) if str(fid).isdigit() else fid,
                "name": name,
                "version": version,
                "size_kb": size_kb,
                "size_bytes": size_bytes,
                "size_human": format_bytes(size_bytes),
                "date": date_str,
                "description": desc,
                "mod_id": mod_id,
                "game_domain": domain,
            })

        return files

    def get_download_url(
        self,
        file_id: int | str,
        game_id: Optional[int] = None,
        mod_id: Optional[int | str] = None,
        game_domain: Optional[str] = None,
    ) -> str:
        """
        Отправляет POST запрос к внутреннему эндпоинту GenerateDownloadUrl
        для бесплатного аккаунта (обход таймера Slow Download).
        Возвращает прямую ссылку на CDN.
        """
        gid = game_id if game_id is not None else self.game_id
        domain = game_domain or self.game_domain
        mod_path = f"/mods/{mod_id}" if mod_id else ""
        referer = f"https://www.nexusmods.com/{domain}{mod_path}?tab=files&file_id={file_id}"

        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Origin": "https://www.nexusmods.com",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        data = {
            "fid": str(file_id),
            "game_id": str(gid),
        }

        with self._lock:
            resp = self.session.post(
                GENERATE_DOWNLOAD_URL_ENDPOINT,
                data=data,
                headers=headers,
                timeout=20,
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Ошибка сервера Nexus Mods (HTTP {resp.status_code}): {resp.text[:200]}"
            )

        try:
            res_json = resp.json()
        except Exception:
            raise RuntimeError(f"Некорректный ответ от Nexus Mods: {resp.text[:200]}")

        direct_url = None
        if isinstance(res_json, dict):
            direct_url = res_json.get("url") or res_json.get("URI")
        elif isinstance(res_json, list) and len(res_json) > 0:
            first = res_json[0]
            if isinstance(first, dict):
                direct_url = first.get("url") or first.get("URI")
            elif isinstance(first, str):
                direct_url = first

        if not direct_url:
            raise RuntimeError(
                "Nexus Mods не вернул прямую ссылку на файл (ответ: "
                f"{json.dumps(res_json, ensure_ascii=False)[:150]}).\n"
                "Для скачивания на бесплатном аккаунте требуются действующие куки сессии.\n"
                "Авторизуйтесь на nexusmods.com в вашем браузере или укажите NEXUS_COOKIES в файле .env"
            )

        return direct_url

    def download_file(
        self,
        file_id: int | str,
        game_id: Optional[int] = None,
        mod_id: Optional[int | str] = None,
        game_domain: Optional[str] = None,
        save_dir: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        cancel_flag: Optional[Callable[[], bool]] = None,
        initial_file_name: Optional[str] = None,
        allow_resume: bool = True,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Скачивает файл с поддержкой докачки (HTTP Range / 206 Partial Content),
        прогресс-баром и автоматической сортировкой по расширениям:
        - Проверяет частичный файл (.part) и запрашивает докачку через Range: bytes=...
        - Если сервер вернул 206 Partial Content, дописывает в файл (режим 'ab')
        - Если сервер не поддерживает докачку (HTTP 200), начинает заново ('wb') с уведомлением
        - При ошибке или обрыве сети сохраняет .part на диске для последующего возобновления
        - Направляет файл в соответствующую подпапку через DownloadRouter
        - Возвращает абсолютный путь к сохранённому файлу
        """
        gid = game_id if game_id is not None else self.game_id
        domain = game_domain or self.game_domain

        base_target_dir = save_dir or os.getenv("MODS_FOLDER") or os.path.join(os.getcwd(), "downloads", "mods")

        # Определяем предварительную целевую папку и путь к .part файлу
        target_dir = base_target_dir
        if initial_file_name:
            try:
                from modules.download_router import download_router
                target_dir = download_router.get_destination_dir(base_target_dir, initial_file_name, domain)
            except Exception:
                target_dir = base_target_dir

        os.makedirs(target_dir, exist_ok=True)

        candidate_part: Optional[str] = None
        existing_bytes = 0
        if initial_file_name and allow_resume:
            cand = os.path.join(target_dir, initial_file_name + ".part")
            if os.path.exists(cand) and os.path.getsize(cand) > 0:
                candidate_part = cand
                existing_bytes = os.path.getsize(cand)

        print(f"[NexusAPI] Запрос прямой ссылки для file_id={file_id} (game_id={gid})...")
        download_url = self.get_download_url(
            file_id=file_id,
            game_id=gid,
            mod_id=mod_id,
            game_domain=domain,
        )

        print(f"[NexusAPI] Прямая ссылка получена! Запуск загрузки...")

        dl_session = self.create_session()
        headers = {}

        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"
            print(f"[NexusAPI] Обнаружен незавершённый файл ({format_bytes(existing_bytes)}). Запрос докачки: Range: bytes={existing_bytes}-")
            if status_callback:
                status_callback(f"Запрос докачки с {format_bytes(existing_bytes)}...")

        resp = dl_session.get(download_url, stream=True, headers=headers, timeout=35)

        # Обработка ошибки 416 (Range Not Satisfiable) — если частичный файл уже полон или поврежден
        if resp.status_code == 416:
            print(f"[NexusAPI] HTTP 416: Недопустимый диапазон. Сброс частичного файла и начало с 0...")
            if candidate_part and os.path.exists(candidate_part):
                try:
                    os.remove(candidate_part)
                except Exception:
                    pass
            existing_bytes = 0
            candidate_part = None
            if status_callback:
                status_callback("Диапазон недопустим, скачивание заново...")
            resp = dl_session.get(download_url, stream=True, timeout=35)

        resp.raise_for_status()

        # Определение истинного имени файла (Content-Disposition или URL)
        filename = None
        cd_header = resp.headers.get("content-disposition", "")
        if cd_header:
            fn_match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', cd_header, re.IGNORECASE)
            if fn_match:
                filename = urllib.parse.unquote(fn_match.group(1).strip())

        if not filename:
            if initial_file_name:
                filename = initial_file_name
            else:
                url_path = urllib.parse.urlparse(download_url).path
                base_name = os.path.basename(url_path)
                if base_name and "." in base_name:
                    filename = urllib.parse.unquote(base_name)
                else:
                    filename = f"nexus_{domain}_{file_id}.zip"

        # Финальная маршрутизация файла по расширению через DownloadRouter
        try:
            from modules.download_router import download_router
            target_dir = download_router.get_destination_dir(base_target_dir, filename, domain)
        except Exception:
            target_dir = base_target_dir
        os.makedirs(target_dir, exist_ok=True)

        final_path = os.path.join(target_dir, filename)
        temp_path = final_path + ".part"

        # Если файл был в candidate_part, но путь temp_path отличается, перемещаем его
        if candidate_part and os.path.exists(candidate_part) and candidate_part != temp_path:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                os.rename(candidate_part, temp_path)
            except Exception as move_err:
                print(f"[NexusAPI] Ошибка перемещения частичного файла: {move_err}")

        # Проверка ответа: поддержал ли сервер докачку (HTTP 206)
        if resp.status_code == 206:
            file_mode = "ab"
            downloaded = existing_bytes

            content_range = resp.headers.get("content-range", "")
            cr_match = re.search(r"/(\d+)", content_range) if content_range else None
            if cr_match:
                total_bytes = int(cr_match.group(1))
            else:
                content_len = int(resp.headers.get("content-length", 0))
                total_bytes = existing_bytes + content_len

            pct = f" ({int((downloaded / total_bytes) * 100)}%)" if total_bytes > 0 else ""
            print(f"[NexusAPI] Сервер поддерживает докачку (HTTP 206)! Возобновление с {format_bytes(existing_bytes)} из {format_bytes(total_bytes)}{pct}")
            if status_callback:
                status_callback(f"Докачка с {format_bytes(existing_bytes)} из {format_bytes(total_bytes)}")
            if progress_callback:
                progress_callback(downloaded, total_bytes, "Докачка...")
        else:
            # Статус 200: сервер не поддерживает докачку (или запрос был без Range)
            if existing_bytes > 0:
                print(f"[NexusAPI] Сервер не поддерживает докачку (HTTP 200). Загрузка начата заново с 0 Б.")
                if status_callback:
                    status_callback("Сервер не поддерживает докачку, скачивание с начала")
            file_mode = "wb"
            downloaded = 0
            existing_bytes = 0
            total_bytes = int(resp.headers.get("content-length", 0))

        chunk_size = 128 * 1024
        session_downloaded = 0
        start_time = time.time()
        last_update_time = start_time

        try:
            with open(temp_path, file_mode) as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if cancel_flag and cancel_flag():
                        f.flush()
                        print(f"\n[NexusAPI] Загрузка остановлена пользователем. Частичный файл сохранён: {temp_path} ({format_bytes(downloaded)})")
                        raise RuntimeError("Загрузка остановлена пользователем.")

                    if chunk:
                        f.write(chunk)
                        chunk_len = len(chunk)
                        downloaded += chunk_len
                        session_downloaded += chunk_len

                        now = time.time()
                        if now - last_update_time >= 0.25 or (total_bytes > 0 and downloaded >= total_bytes):
                            elapsed = now - start_time
                            speed = session_downloaded / elapsed if elapsed > 0 else 0
                            speed_str = f"{format_bytes(int(speed))}/с"

                            if progress_callback:
                                progress_callback(downloaded, total_bytes, speed_str)
                            else:
                                if total_bytes > 0:
                                    percent = (downloaded / total_bytes) * 100
                                    bar_len = 30
                                    filled = int(bar_len * downloaded / total_bytes)
                                    bar = "=" * filled + ">" + " " * (bar_len - filled - 1) if filled < bar_len else "=" * bar_len
                                    sys.stdout.write(
                                        f"\r[{bar}] {percent:5.1f}% ({format_bytes(downloaded)} / {format_bytes(total_bytes)}) {speed_str}   "
                                    )
                                else:
                                    sys.stdout.write(f"\rСкачано: {format_bytes(downloaded)} [{speed_str}]   ")
                                sys.stdout.flush()

                            last_update_time = now
                f.flush()
        except Exception as net_err:
            if cancel_flag and cancel_flag():
                raise
            print(f"\n[NexusAPI] Ошибка соединения при скачивании ({net_err}). Файл сохранён до {format_bytes(downloaded)} в {temp_path}")
            raise

        if not progress_callback:
            sys.stdout.write("\n")
            sys.stdout.flush()

        if os.path.exists(final_path):
            try:
                os.remove(final_path)
            except Exception:
                pass
        os.rename(temp_path, final_path)

        abs_path = os.path.abspath(final_path)
        print(f"[NexusAPI] [Успех] Файл сохранён: {abs_path}")
        return abs_path


# ==========================================================
# ИНТЕРАКТИВНЫЙ КОНСОЛЬНЫЙ РЕЖИМ (по аналогии с search.py)
# ==========================================================
def interactive_cli():
    """Интерактивный консольный интерфейс для поиска и скачивания модов с Nexus Mods."""
    print("=" * 65)
    print("⚡ Nexus Mods Downloader (Категории & Топ Популярных Модов)")
    print("=" * 65)

    api = NexusAPI()
    save_dir = os.getenv("MODS_FOLDER") or os.path.join(os.getcwd(), "downloads", "mods")
    os.makedirs(save_dir, exist_ok=True)

    print(f"📁 Папка сохранения: {os.path.abspath(save_dir)}")
    print(f"🎮 Игра по умолчанию: {api.game_domain} (ID: {api.game_id})")
    print(f"🔑 Активные ключи кук: {list(api.cookies.keys())}")

    current_category = "all"
    categories = get_categories_for_game(api.game_domain)

    def print_categories():
        print("\n📂 Доступные категории:")
        for idx, (raw_c, disp_c) in enumerate(categories):
            mark = " [*]" if raw_c == current_category else ""
            print(f"  {idx:2d}. {disp_c}{mark}")

    print_categories()

    # Сразу показываем самые популярные моды без нажатия
    print(f"\n🌟 Загрузка самых популярных модов для {api.game_domain}...")
    current_mods = api.search_mods(query="", category=current_category, count=25)
    cur_idx = 0
    total = len(current_mods)

    if current_mods:
        print(f"\n--- Топ популярных модов (всего {total}) ---")
        for i, m in enumerate(current_mods[:10], 1):
            dl_str = f"📥 {m.get('downloads', 0):,} скачиваний"
            cat_str = f"[{m.get('category')}] " if m.get("category") else ""
            print(f"{i:2d}. {cat_str}{m['title']} | {dl_str}")
        cur_idx = 10
    else:
        print("[!] Не удалось загрузить популярные моды.")

    while True:
        cat_disp = next((d for r, d in categories if r == current_category), current_category)
        try:
            print(f"\n[Категория: {cat_disp}]")
            print("Команды: [номер] - скачать, [поиск] - искать, [cat] - категории, [cat N] - сменить категорию, [q] - выход")
            query = input("NexusMods> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nВыход.")
            break

        if not query:
            # Если нажат Enter, листаем следующие моды
            if current_mods and cur_idx < len(current_mods):
                batch = current_mods[cur_idx : cur_idx + 10]
                print(f"\n--- Моды {cur_idx + 1}–{cur_idx + len(batch)} из {len(current_mods)} ---")
                for i, m in enumerate(batch, cur_idx + 1):
                    dl_str = f"📥 {m.get('downloads', 0):,} скачиваний"
                    cat_str = f"[{m.get('category')}] " if m.get("category") else ""
                    print(f"{i:2d}. {cat_str}{m['title']} | {dl_str}")
                cur_idx += len(batch)
            continue

        if query.lower() in ("exit", "quit", "q", "выход"):
            print("До встречи!")
            break

        if query.lower() == "cat" or query.lower() == "категории":
            print_categories()
            continue

        if query.lower().startswith("cat ") or query.lower().startswith("к "):
            arg = query.split(None, 1)[1].strip()
            if arg.isdigit():
                c_idx = int(arg)
                if 0 <= c_idx < len(categories):
                    current_category = categories[c_idx][0]
                    print(f"\n[+] Выбрана категория: {categories[c_idx][1]}")
                    print("Загрузка популярных модов выбранной категории...")
                    current_mods = api.search_mods(query="", category=current_category, count=25)
                    cur_idx = 0
                    if current_mods:
                        print(f"\n--- Топ популярных ({len(current_mods)} модов) ---")
                        for i, m in enumerate(current_mods[:10], 1):
                            dl_str = f"📥 {m.get('downloads', 0):,} скачиваний"
                            cat_str = f"[{m.get('category')}] " if m.get("category") else ""
                            print(f"{i:2d}. {cat_str}{m['title']} | {dl_str}")
                        cur_idx = 10
                    else:
                        print("[!] Модов в этой категории не найдено.")
                    continue
                else:
                    print(f"[!] Неверный номер категории (0..{len(categories)-1})")
                    continue

        if query.lower().startswith("game "):
            parts = query.split()
            if len(parts) >= 2:
                api.game_domain = parts[1]
                if len(parts) >= 3 and parts[2].isdigit():
                    api.game_id = int(parts[2])
                categories = get_categories_for_game(api.game_domain)
                current_category = "all"
                print(f"[+] Игра переключена на: {api.game_domain} (ID: {api.game_id})")
                print_categories()
                print("Загрузка популярных модов новой игры...")
                current_mods = api.search_mods(query="", category=current_category, count=25)
                cur_idx = 0
                if current_mods:
                    for i, m in enumerate(current_mods[:10], 1):
                        dl_str = f"📥 {m.get('downloads', 0):,} скачиваний"
                        cat_str = f"[{m.get('category')}] " if m.get("category") else ""
                        print(f"{i:2d}. {cat_str}{m['title']} | {dl_str}")
                    cur_idx = 10
            continue

        # Если введено чисто число и оно соответствует индексу показанного мода
        if query.isdigit() and current_mods and 1 <= int(query) <= len(current_mods):
            idx = int(query) - 1
            selected_mod = current_mods[idx]
            print(f"\n[+] Выбран мод: {selected_mod['title']} (ID: {selected_mod['mod_id']})")
            if selected_mod.get("summary"):
                print(f"    Описание: {selected_mod['summary'][:130]}...")

            print("Загрузка информации о моде и скриншотах...")
            try:
                details = api.get_mod_details(
                    selected_mod["mod_id"],
                    selected_mod.get("game_id", api.game_id),
                    selected_mod.get("game_domain", api.game_domain),
                )
                if details.get("screenshots"):
                    print(f"    Скриншотов доступно: {len(details['screenshots'])}")
                if details.get("endorsements"):
                    print(f"    Одобрений (endorsements): {details['endorsements']:,}")
            except Exception:
                pass

            print("Загрузка списка доступных файлов...")

            files = api.get_mod_files(selected_mod["mod_id"], selected_mod.get("game_domain"))
            if not files:
                print("[!] Не удалось найти доступные файлы для скачивания на странице мода.")
                continue

            print(f"\nДоступные файлы ({len(files)} шт.):")
            for f_idx, f_info in enumerate(files, 1):
                ver_str = f"v{f_info['version']}" if f_info.get("version") else ""
                print(f"  {f_idx}. {f_info['name']} {ver_str} [{f_info['size_human']}] (file_id: {f_info['file_id']})")

            f_choice = input(f"\nВыберите файл для скачивания (1–{len(files)}, по умолчанию 1): ").strip()
            if not f_choice:
                file_to_download = files[0]
            elif f_choice.isdigit() and 1 <= int(f_choice) <= len(files):
                file_to_download = files[int(f_choice) - 1]
            else:
                print("Отмена выбора файла.")
                continue

            print(f"\n[+] Скачивание: {file_to_download['name']}...")
            try:
                saved_path = api.download_file(
                    file_id=file_to_download["file_id"],
                    game_id=selected_mod.get("game_id", api.game_id),
                    mod_id=selected_mod["mod_id"],
                    game_domain=selected_mod.get("game_domain", api.game_domain),
                    save_dir=save_dir,
                )
                print(f"✅ Файл успешно скачан:\n-> {saved_path}")
            except Exception as err:
                print(f"❌ Ошибка скачивания: {err}")
            continue

        # Обычный текстовый поиск
        print(f"\n🔍 Поиск модов по запросу '{query}' (категория: {cat_disp})...")
        mods = api.search_mods(query=query, category=current_category, count=25)

        if not mods:
            print("[!] Ничего не найдено. Попробуйте другой запрос или категорию 'Все'.")
            continue

        current_mods = mods
        total = len(current_mods)
        cur_idx = 0
        batch = current_mods[:10]
        print(f"\n--- Результаты 1–{len(batch)} из {total} ---")
        for i, m in enumerate(batch, 1):
            dl_str = f"📥 {m.get('downloads', 0):,} скачиваний"
            cat_str = f"[{m.get('category')}] " if m.get("category") else ""
            print(f"{i:2d}. {cat_str}{m['title']} (Автор: {m['author']}) | {dl_str}")
            if m.get("summary"):
                print(f"    {m['summary'][:100]}...")
        cur_idx = len(batch)


if __name__ == "__main__":
    interactive_cli()
