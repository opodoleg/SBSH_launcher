import sys
import requests
import webbrowser

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QMessageBox
)
session = requests.Session()

session.headers.update({
    "User-Agent": "Mozzila/5.0"
})

from Steam_api import (search_workshop,get_workshop_mods
                      )


def search_games(query):
    url = "https://store.steampowered.com/api/storesearch/"

    r = session.get(
        url,
        params={
            "term": query,
            "l": "russian",
            "cc": "RU"
        },
        timeout=30
    )

    if r.status_code != 200:
        return []
    try:
        games = r.json().get("items", [])

        return [
            {
                "name": game["name"],
                "appid": game["id"]
            }
            for game in games
        ]

    except requests.exceptions.JSONDecodeError:
        return []

class SteamSearchUI(QWidget):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Steam Workshop Launcher")
        self.resize(1000, 600)

        self.current_appid = None
        self.current_mod = None
        self.last_text = ""
        self.mod_query = ""

        root_layout = QVBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Введите название игры...")

        self.mod_search_input = QLineEdit()
        self.mod_search_input.setPlaceholderText("Поиск модов...")

        lists_layout = QHBoxLayout()

        self.games_list = QListWidget()
        self.mods_list = QListWidget()

        lists_layout.addWidget(self.games_list)
        lists_layout.addWidget(self.mods_list)

        self.download_button = QPushButton("Скачать через SteamCMD")

        root_layout.addWidget(self.search_input)
        root_layout.addWidget(self.mod_search_input)
        root_layout.addLayout(lists_layout)
        root_layout.addWidget(self.download_button)

        self.setLayout(root_layout)

        # timers
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.do_search)

        self.mod_timer = QTimer()
        self.mod_timer.setSingleShot(True)
        self.mod_timer.timeout.connect(self.do_mod_search)

        # signals
        self.search_input.textChanged.connect(self.on_text_changed)
        self.mod_search_input.textChanged.connect(self.on_mod_text_changed)

        self.games_list.itemClicked.connect(self.on_game_selected)
        self.mods_list.itemClicked.connect(self.on_mod_selected)
        self.mods_list.itemDoubleClicked.connect(self.open_mod_workshop)

        self.download_button.clicked.connect(self.download_selected_mod)

    # ---------------- GAME SEARCH ----------------

    def on_text_changed(self, text):
        self.last_text = text
        self.timer.start(300)

    def do_search(self):
        text = self.last_text.strip()

        if len(text) < 2:
            self.games_list.clear()
            return

        games = search_games(text)

        self.games_list.clear()

        for game in games[:20]:
            item = QListWidgetItem(game["name"])
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.games_list.addItem(item)

    # ---------------- MOD SEARCH ----------------


    def on_mod_text_changed(self, text):
        self.mod_query = text
        self.mod_timer.start(300)

    def do_mod_search(self):
        
        print("SEARCH:", self.mod_query)

        if not self.current_appid:
            return

        query = self.mod_query.strip()
        
        if not query:
            return
        
        mods = search_workshop(
            self.current_appid,
            query
        )

        print("SEARCH RESULT:", mods)

        self.mods_list.clear()

        for mod in mods:
            item = QListWidgetItem(mod["title"])
            
            item.setData(
                Qt.ItemDataRole.UserRole,
                mod
            )
            self.mods_list.addItem(item)
    # ---------------- GAME SELECT ----------------

    def on_game_selected(self, item):
        print("GAME SELECTED")
        game = item.data(Qt.ItemDataRole.UserRole)
        
        self.current_game = game
        self.current_appid = game["appid"]
        
        print(game)
        
        self.mods_list.clear()

        mods = get_workshop_mods(self.current_appid)

        print("SEARCH RESULT:", mods)
        print("MODS LOADED:", len(mods))

        for mod in mods:
            print(mod)
            mod_item = QListWidgetItem(mod["title"])

            mod_item.setData(
                Qt.ItemDataRole.UserRole,
                mod
            )
            self.mods_list.addItem(mod_item)
            
        print("ITEMS IN LIST:", self.mods_list.count())
        

    # ---------------- MOD SELECT ----------------

    def on_mod_selected(self, item):
        self.current_mod = item.data(Qt.ItemDataRole.UserRole)

    # ---------------- OPEN WORKSHOP ----------------

    def open_mod_workshop(self, item):
        mod = item.data(Qt.ItemDataRole.UserRole)
        if not mod:
            return
        
        webbrowser.open(
            f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod['id']}"
        )

    # ---------------- DOWNLOAD ----------------

    def download_selected_mod(self):
        if not self.current_mod:
            QMessageBox.warning(self, "Ошибка", "Выберите мод")
            return

        modid = self.current_mod["id"]

        print(
            "Скачать мод:",
            modid,
            "appid:",
            self.current_appid
        )

        # SteamCMD потом сюда:
        # subprocess.run([
        #   "steamcmd",
        #   "+login", "anonymous",
        #   "+workshop_download_item",
        #   str(self.current_appid),
        #   str(modid),
        #   "+quit"
        # ])

        

if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = SteamSearchUI()
    window.show()

    sys.exit(app.exec())
    
    
    
