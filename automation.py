import requests
from bs4 import BeautifulSoup

from modules.qb import add_torrent_url


HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

DEFAULT_PATH = "G:/games"


def search_game(query):
    url = (
        "https://thelastgame.org/"
        f"index.php?do=search&subaction=search&story={query}"
    )

    response = requests.get(
        
        url,
        headers=HEADERS
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    results = []

    for a in soup.select("article.post h2.post-title a"):
        results.append({
            "title": a.get_text(strip=True),
            "url": a.get("href")
        })

    return results


def choose_game(results):

    print("\nFound games:\n")

    for i, game in enumerate(results):

        print(
            f"{i + 1}. "
            f"{game['title']}"
        )

    try:

        choice = int(
            input("\nChoose game number: ")
        )

        return results[choice - 1]

    except:

        print("Invalid choice")

        return None


def get_torrent_link(page_url):

    response = requests.get(
        page_url,
        headers=HEADERS
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    for link in soup.find_all("a"):

        href = link.get("href")

        if not href:
            continue

        if "download&id=" in href:

            return href

    return None


def get_save_path():

    path = input(
        f"\nDefault path: "
        f"{DEFAULT_PATH}\n"
        f"Enter path: "
    )

    if not path:
        return DEFAULT_PATH

    return path


def download_game(game_name, save_path):

    print("\nSearching game...")

    results = search_game(game_name)

    if not results:

        print("\nGame not found")

        return

    selected_game = choose_game(results)

    if not selected_game:
        return

    print("\nSelected game:")
    print(selected_game["title"])

    torrent_url = get_torrent_link(
        selected_game["url"]
    )

    if not torrent_url:

        print("\nTorrent not found")

        return

    print("\nTorrent:")
    print(torrent_url)

    result = add_torrent_url(
        torrent_url,
        save_path
    )

    print("\nResult:")
    print(result)


def main():

    game_name = input(
        "Enter game name: "
    )

    save_path = get_save_path()

    download_game(
        game_name,
        save_path
    )


main()