import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def search_torrent(query):
    url = (
        "https://thelastgame.org/"
        f"index.php?do=search&subaction=search&story={query}"
    )

    response = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(response.text, "html.parser")

    results = []
    added = set()

    # ❗ ВАЖНО: лучше ограничить контейнер (если найдёшь класс — замени тут)
    container = soup

    for link in container.find_all("a"):
        href = link.get("href")
        text = link.text.strip()

        if not href or not text:
            continue

        if ".html" not in href:
            continue

        key = (text, href)
        if key in added:
            continue
        added.add(key)

        results.append({
            "title": text,
            "url": href
        })

    return results
    