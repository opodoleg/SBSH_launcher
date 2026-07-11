import requests
import os
import threading
import time

QB_URL = "http://localhost:1489"

USERNAME = "admin"
PASSWORD = "admin1488"

session = requests.Session()


def login():

    try:

        response = session.post(
            f"{QB_URL}/api/v2/auth/login",
            data={
                "username": USERNAME,
                "password": PASSWORD
            },
            timeout=5
        )

        response.raise_for_status()

        if response.text != "Ok.":

            return {
                "success": False,
                "error": "Login failed"
            }

        return {
            "success": True,
            "message": "Connected to qBittorrent"
        }

    except requests.exceptions.RequestException as e:

        return {
            "success": False,
            "error": str(e)
        }


def live_torrent_view(interval=2):
    while True:
        result = get_torrents()

        os.system("cls" if os.name == "nt" else "clear")

        if not result["success"]:
            print("Error:", result["error"])
            time.sleep(interval)
            continue

        for t in result["data"]:
            name = t.get("name", "unknown")
            progress = t.get("progress", 0) * 100

            print(f"{name} - {progress:.1f}%")

        time.sleep(interval)

def ensure_login():

    try:

        response = session.get(
            f"{QB_URL}/api/v2/app/version",
            timeout=5
        )

        if response.status_code == 200:
            return True

    except:
        pass

    result = login()

    return result.get("success", False)


def get_torrents():

    if not ensure_login():

        return {
            "success": False,
            "error": "qBittorrent not connected"
        }

    try:

        response = session.get(
            f"{QB_URL}/api/v2/torrents/info",
            timeout=5
        )

        response.raise_for_status()

        return {
            "success": True,
            "data": response.json()
        }

    except requests.exceptions.RequestException as e:

        return {
            "success": False,
            "error": str(e)
        }


def add_magnet(magnet, save_path):

    if not ensure_login():

        return {
            "success": False,
            "error": "qBittorrent not connected"
        }

    try:

        os.makedirs(save_path, exist_ok=True)

        response = session.post(
            f"{QB_URL}/api/v2/torrents/add",
            data={
                "urls": magnet,
                "savepath": save_path
            },
            timeout=5
        )

        response.raise_for_status()

        return {
            "success": True,
            "message": "Magnet added"
        }

    except requests.exceptions.RequestException as e:

        return {
            "success": False,
            "error": str(e)
        }


def add_torrent_url(torrent_url, save_path):

    if not ensure_login():

        return {
            "success": False,
            "error": "qBittorrent not connected"
        }

    try:

        os.makedirs(save_path, exist_ok=True)

        response = session.post(
            f"{QB_URL}/api/v2/torrents/add",
            data={
                "urls": torrent_url,
                "savepath": save_path
            },
            timeout=5
        )

        response.raise_for_status()

        return {
            "success": True,
            "message": "Torrent added"
        }

    except requests.exceptions.RequestException as e:

        return {
            "success": False,
            "error": str(e)
        }