import requests
from bs4 import BeautifulSoup

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
})


# ==========================================================
# 1. ПОИСК ИГР В МАГАЗИНЕ STEAM
# ==========================================================
def search_games(query: str, limit: int = 20) -> list[dict]:
    """Ищет игры в магазине Steam по названию."""
    text = query.strip()
    if not text:
        return []

    url = "https://store.steampowered.com/api/storesearch/"
    params = {
        "term": text,
        "l": "russian",
        "cc": "RU"
    }

    try:
        r = session.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return []

        items = r.json().get("items", [])
        return [
            {
                "name": game.get("name", "Unknown Game"),
                "appid": game.get("id")
            }
            for game in items[:limit]
            if game.get("id") is not None
        ]
    except Exception:
        return []


# ==========================================================
# Вспомогательный метод парсинга ID модов
# ==========================================================
def _fetch_workshop_modids(params: dict) -> list[str]:
    """Загружает страницу Workshop и извлекает уникальные ID модов."""
    url = "https://steamcommunity.com/workshop/browse/"

    try:
        r = session.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "lxml")
        links = soup.find_all(
            "a",
            href=lambda x: x and "filedetails/?id=" in x
        )

        modids = []
        seen = set()

        for link in links:
            href = link.get("href", "")
            if "id=" not in href:
                continue

            modid = href.split("id=")[1].split("&")[0]
            if modid.isdigit() and modid not in seen:
                seen.add(modid)
                modids.append(modid)

        return modids
    except Exception:
        return []


# ==========================================================
# 2. ПОЛУЧЕНИЕ ПОПУЛЯРНЫХ МОДОВ
# ==========================================================
def get_workshop_mods(appid: int, per_page: int = 30) -> list[dict]:
    """Получает популярные моды для указанного appid игры."""
    params = {
        "appid": appid,
        "numperpage": per_page
    }
    modids = _fetch_workshop_modids(params)
    if not modids:
        return []

    return get_mods_info(modids)


# ==========================================================
# 3. ПОИСК МОДОВ В WORKSHOP
# ==========================================================
def search_workshop(appid: int, query: str, per_page: int = 30) -> list[dict]:
    """Ищет моды в мастерской Steam по запросу."""
    query_str = query.strip()
    if not query_str:
        return get_workshop_mods(appid, per_page=per_page)

    params = {
        "appid": appid,
        "searchtext": query_str,
        "section": "items",
        "browsesort": "trend",
        "browsefilter": "trend",
        "actualsort": "trend",
        "p": 1,
        "numperpage": per_page
    }

    modids = _fetch_workshop_modids(params)
    if not modids:
        return []

    return get_mods_info(modids)


# ==========================================================
# 4. ПОЛУЧЕНИЕ ДЕТАЛЕЙ О МОДАХ ЧЕРЕЗ STEAM API
# ==========================================================
def get_mods_info(modids: list[str]) -> list[dict]:
    """Запрашивает метаданные (название, ID, превью) через официальный API Steam."""
    if not modids:
        return []

    # Steam Web API принимает до 100 элементов за 1 запрос
    batch = modids[:100]
    url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"

    data = {
        "itemcount": len(batch)
    }
    for i, modid in enumerate(batch):
        data[f"publishedfileids[{i}]"] = str(modid)

    try:
        r = session.post(url, data=data, timeout=20)
        if r.status_code != 200:
            return []

        js = r.json()
        details = js.get("response", {}).get("publishedfiledetails", [])

        mods = []
        for mod in details:
            file_id = mod.get("publishedfileid")
            if file_id:
                mods.append({
                    "id": str(file_id),
                    "title": mod.get("title") or f"Mod #{file_id}",
                    "preview_url": mod.get("preview_url", "")
                })

        return mods
    except Exception:
        return []
