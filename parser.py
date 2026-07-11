import requests
from bs4 import BeautifulSoup

from modules.qb import add_torrent_url

def get_torrent_link(url):

    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    links = soup.find_all("a")

    for link in links:

        href = link.get("href")

        if not href:
            continue

        if "download&id=" in href:

            return href

    return None
