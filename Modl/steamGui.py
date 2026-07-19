import sys
import requests
import webbrowser
import os
import shutil

from steamCmd import (SteamCMDWorker)
from PyQt6.QtCore import Qt, QTimer, QThread,QSettings
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
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

        self.settings = QSettings("SBSH", "SteamWorkshopLauncher")

        self.steamcmd_path = self.settings.value(
            "steamcmd_path",
            r"G:\SteamCMD\steamcmd.exe"
        )

        self.mods_folder = self.settings.value(
            "mods_folder",
            ""
        )

        self.setWindowTitle("Steam Workshop Launcher")
        self.resize(1000, 600)

        self.current_appid = None
        self.current_mod = None
        self.last_text = ""
        self.mod_query = ""

        root_layout = QVBoxLayout()
        self.path_button = QPushButton("Выбрать SteamCMD")
        root_layout.addWidget(self.path_button)
        
        self.path_button.clicked.connect(self.select_steamcmd)
        # Поле для папки модов
        self.mods_path_input = QLineEdit()
        self.mods_path_input.setPlaceholderText("Папка для скачивания модов")
        self.mods_path_input.setText(self.mods_folder)
        # Кнопка выбора папки
        self.mods_path_button = QPushButton("Выбрать папку для модов")

        root_layout.addWidget(self.mods_path_input)
        root_layout.addWidget(self.mods_path_button)

        self.mods_path_button.clicked.connect(self.select_mods_folder)
        self.mods_path_input.editingFinished.connect(self.save_mods_folder)
       
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
            return
        self.download_thread = QThread()

        self.download_worker = SteamCMDWorker(
            self.steamcmd_path,
            self.current_appid,
            self.current_mod["id"]
            )

        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(
            self.download_worker.run
            )


        self.download_worker.output.connect(print)

        self.download_worker.finished.connect(
            self.download_finished
        )

        self.download_worker.error.connect(print)

        self.download_worker.finished.connect(
            self.download_thread.quit
        )

        self.download_worker.finished.connect(
            self.download_worker.deleteLater
        )

        self.download_thread.finished.connect(
            self.download_thread.deleteLater
        )

        self.download_thread.start()
        
    def download_finished(self, code):
        if code != 0:
            QMessageBox.warning(
                self,
                "Ошибка",
                "Не удалось скачать мод."
            )
            return

        source = os.path.join(
            os.path.dirname(self.steamcmd_path),
            "steamapps",
            "workshop",
            "content",
            str(self.current_appid),
            str(self.current_mod["id"])
        )

        destination = os.path.join(
            self.mods_folder,
            str(self.current_mod["id"])
        )

        shutil.copytree(
            source,
            destination,
            dirs_exist_ok=True
        )

        QMessageBox.information(
            self,
            "Готово",
            "Мод успешно скачан."
        )
             
    def select_steamcmd(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите steamcmd.exe",
            "",
            "SteamCMD (steamcmd.exe);;Executable (*.exe)"
        )

        if filename:
            self.steamcmd_path = filename

            self.settings.setValue(
                "steamcmd_path",
                filename
            )

            QMessageBox.information(
                self,
                "Готово",
                "Путь сохранён."
            )
    
    def select_mods_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для модов",
            self.mods_folder
        )

        if folder:
            self.mods_folder = folder
            self.mods_path_input.setText(folder)
            self.settings.setValue("mods_folder", folder)


    def save_mods_folder(self):
        self.mods_folder = self.mods_path_input.text().strip()
        self.settings.setValue("mods_folder", self.mods_folder)
                
if __name__ == "__main__":
    app = QApplication(sys.argv)

    window = SteamSearchUI()
    window.show()

    sys.exit(app.exec())
    