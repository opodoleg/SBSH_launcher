from fastapi import FastAPI, Form
from pydantic import BaseModel
import threading
import time
import os
from search import search_torrent
from modules.qb import (
    login,
    get_torrents,
    add_magnet
)

app = FastAPI()


# ----------------------------
# TORRENT MONITOR (CONSOLE)
# ----------------------------
def live_torrent_view():
    while True:
        result = get_torrents()

        print("\n" * 2)

        if not result["success"]:
            print("Error:", result["error"])
            time.sleep(2)
            continue

        torrents = result["data"]

        if not torrents:
            print("No torrents")
        else:
            for t in torrents:
                name = t.get("name", "unknown")
                progress = t.get("progress", 0) * 100
                state = t.get("state", "unknown")

                print(f"{name}")
                print(f"  [{progress:.1f}%] {state}")
                print("-" * 40)

        time.sleep(2)
        


# ----------------------------
# STARTUP EVENT
# ----------------------------
@app.on_event("startup")
async def startup_event():
    try:
        success = login()

        if success:
            print("qBittorrent connected")
        else:
            print("qBittorrent login failed")

    except Exception as e:
        print(f"Startup error: {e}")

    # запуск фонового монитора
    thread = threading.Thread(
        target=live_torrent_view,
        daemon=True
    )
    thread.start()


# ----------------------------
# API MODELS
# ----------------------------
class TorrentRequest(BaseModel):
    magnet: str
    save_path: str


# ----------------------------
# ROUTES
# ----------------------------
@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/search")
def search(q: str):
    return search_torrent(q)


@app.get("/torrents")
def torrents():
    return get_torrents()


@app.post("/add")
def add(
    magnet: str = Form(...),
    save_path: str = Form(...)
):
    return {
        "result": add_magnet(magnet, save_path)
    }