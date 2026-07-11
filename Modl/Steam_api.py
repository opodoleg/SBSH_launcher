import requests
from bs4 import BeautifulSoup

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0"
})

# =========================
# 🔥 1. ПЕРВАЯ СТРАНИЦА WORKSHOP
# =========================
def get_workshop_mods(appid: int):
    print("GET DEFAULT MODS")
    url = "https://steamcommunity.com/workshop/browse/"

    r = session.get(
    url,
    params={"appid": appid, "numperpage": 30},
    timeout=30
    )

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

        if modid in seen:
            continue

        seen.add(modid)
        modids.append(modid)

    if not modids:
        return []

    mods_info = get_mods_info(modids)

    mods = []

    for mod in mods_info:
        mods.append({
            "id": mod.get("publishedfileid"),
            "title": mod.get("title", "Unknown")
        })

    return mods


# =========================
# 🔥 2. STEAM API DETAILS
# =========================
def get_mods_info(modids):
    url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"

    data = {
        "itemcount": len(modids)
    }

    for i, modid in enumerate(modids):
        data[f"publishedfileids[{i}]"] = str(modid)

    r = session.post(
    url,
    data=data,
    timeout=30
    )

    if r.status_code != 200:
        return []

    try:
        js = r.json()
    except Exception:
        return []

    return js.get("response", {}).get("publishedfiledetails", [])


# =========================
# 🔥 3. WORKSHOP SEARCH (30 ITEMS)
# =========================
def search_workshop(appid: int, query: str):
    print("TEST SEARCH WORKSHOP")
    url = "https://steamcommunity.com/workshop/browse/"

    params = {
        "appid": appid,
        "searchtext": query,
        "browsefilter": "trend",
        "browsesort": "trend",
        "section": "items",
        "actualsort": "trend",
        "p": 1
    }

    r = session.get(
        url,
        params=params,
        timeout=15
    )
    
    print("URL:", r.url)
    print("STATUS:", r.status_code)
    print("SIZE:", len(r.text))

    with open("steam_debug.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    
    if r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "lxml")

    links = soup.find_all(
        "a",
        href=lambda href: href and "filedetails/?id=" in href
    )

    modids = []
    seen = set()

    for link in links:
        href = link.get("href", "")

        if "id=" not in href:
            continue

        modid = href.split("id=")[1].split("&")[0]

        if modid in seen:
            continue

        seen.add(modid)
        modids.append(modid)

    if not modids:
        return []

    mods_info = get_mods_info(modids)

    mods = []

    for mod in mods_info:
        mods.append({
            "id": mod["publishedfileid"],
            "title": mod.get("title", "Unknown")
        })
        
    return mods


    