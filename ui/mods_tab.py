import os
import shutil
import sys
import webbrowser

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Modl")))
from Steam_api import get_workshop_mods, search_games, search_workshop
from steamCmd import SteamCMDWorker


class AsyncWorker(QThread):
    result_ready = pyqtSignal(int, object)
    error_occurred = pyqtSignal(int, str)

    def __init__(self, request_id: int, func, *args, **kwargs):
        super().__init__()
        self.request_id = request_id
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.result_ready.emit(self.request_id, result)
        except Exception as e:
            self.error_occurred.emit(self.request_id, str(e))


class ModsTabWidget(QWidget):
    """Вкладка поиска и скачивания модов из Steam Workshop через SteamCMD."""

    def __init__(self, get_steamcmd_path_callback=None, get_mods_folder_callback=None):
        super().__init__()
        self.get_steamcmd_path_callback = get_steamcmd_path_callback
        self.get_mods_folder_callback = get_mods_folder_callback

        self.current_game = None
        self.current_appid = None
        self.current_mod = None
        self.last_text = ""
        self.mod_query = ""

        self.game_search_id = 0
        self.mod_search_id = 0

        self.game_worker = None
        self.mod_worker = None
        self.download_worker = None
        self.download_thread = None

        self._setup_ui()
        self._setup_timers()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Списки: левый (Игры), правый (Моды)
        lists_layout = QHBoxLayout()
        lists_layout.setSpacing(16)

        # --- Колонка игр ---
        games_layout = QVBoxLayout()
        games_layout.setSpacing(8)
        games_label = QLabel("<b>1. Поиск игры в Steam:</b>")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Введите название игры (от 2 символов)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.on_game_text_changed)

        self.games_list = QListWidget()
        self.games_list.itemClicked.connect(self.on_game_selected)

        games_layout.addWidget(games_label)
        games_layout.addWidget(self.search_input)
        games_layout.addWidget(self.games_list)
        lists_layout.addLayout(games_layout, stretch=1)

        # --- Колонка модов ---
        mods_layout = QVBoxLayout()
        mods_layout.setSpacing(8)
        mods_label = QLabel("<b>2. Моды мастерской Steam (Workshop):</b>")
        self.mod_search_input = QLineEdit()
        self.mod_search_input.setPlaceholderText("Поиск модов для выбранной игры...")
        self.mod_search_input.setClearButtonEnabled(True)
        self.mod_search_input.textChanged.connect(self.on_mod_text_changed)

        self.mods_list = QListWidget()
        self.mods_list.itemClicked.connect(self.on_mod_selected)
        self.mods_list.itemDoubleClicked.connect(self.open_mod_workshop)

        mods_layout.addWidget(mods_label)
        mods_layout.addWidget(self.mod_search_input)
        mods_layout.addWidget(self.mods_list)
        lists_layout.addLayout(mods_layout, stretch=1)

        layout.addLayout(lists_layout)

        # --- Информация о выбранном моде ---
        self.info_label = QLabel(
            "<i>Выберите игру слева, затем мод справа (двойной клик откроет страницу в браузере).</i>"
        )
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.info_label)

        # --- Кнопка скачивания ---
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.browser_button = QPushButton("🌐 Открыть в Workshop")
        self.browser_button.setObjectName("secondaryBtn")
        self.browser_button.setEnabled(False)
        self.browser_button.clicked.connect(self.open_mod_workshop)

        self.download_button = QPushButton("⚡ Скачать выбранный мод через SteamCMD")
        self.download_button.setObjectName("accentBtn")
        self.download_button.setFixedHeight(38)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.download_selected_mod)

        actions_row.addWidget(self.browser_button)
        actions_row.addWidget(self.download_button, stretch=1)
        layout.addLayout(actions_row)

        # --- Строка статуса ---
        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(self.status_label)

    def _setup_timers(self):
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.do_search_games)

        self.mod_timer = QTimer(self)
        self.mod_timer.setSingleShot(True)
        self.mod_timer.timeout.connect(self.do_search_mods)

    def get_steamcmd_path(self):
        if self.get_steamcmd_path_callback:
            return self.get_steamcmd_path_callback()
        return r"G:\SteamCMD\steamcmd.exe"

    def get_mods_folder(self):
        if self.get_mods_folder_callback:
            return self.get_mods_folder_callback()
        return ""

    # ---------------- ПОИСК ИГР ----------------

    def on_game_text_changed(self, text):
        self.last_text = text
        self.timer.start(300)

    def do_search_games(self):
        text = self.last_text.strip()
        if len(text) < 2:
            self.games_list.clear()
            self.status_label.setText("Готов к работе")
            return

        self.game_search_id += 1
        self.status_label.setText(f"Поиск игр по запросу '{text}'...")

        self.game_worker = AsyncWorker(self.game_search_id, search_games, text)
        self.game_worker.result_ready.connect(self.on_games_loaded)
        self.game_worker.error_occurred.connect(self.on_network_error)
        self.game_worker.start()

    def on_games_loaded(self, req_id, games):
        if req_id != self.game_search_id:
            return

        self.games_list.clear()
        for game in games:
            item = QListWidgetItem(f"{game['name']} (AppID: {game['appid']})")
            item.setData(Qt.ItemDataRole.UserRole, game)
            self.games_list.addItem(item)

        self.status_label.setText(f"Найдено игр: {len(games)}")

    # ---------------- ВЫБОР ИГРЫ И МОДОВ ----------------

    def on_game_selected(self, item):
        game = item.data(Qt.ItemDataRole.UserRole)
        if not game:
            return

        self.current_game = game
        self.current_appid = game["appid"]
        self.current_mod = None
        self._update_selected_mod_info()

        self.mods_list.clear()
        self.mod_search_input.clear()
        self.mod_search_id += 1

        self.status_label.setText(f"Загрузка популярных модов для {game['name']}...")

        self.mod_worker = AsyncWorker(
            self.mod_search_id,
            get_workshop_mods,
            self.current_appid
        )
        self.mod_worker.result_ready.connect(self.on_mods_loaded)
        self.mod_worker.error_occurred.connect(self.on_network_error)
        self.mod_worker.start()

    def on_mod_text_changed(self, text):
        self.mod_query = text
        self.mod_timer.start(350)

    def do_search_mods(self):
        if not self.current_appid:
            return

        query = self.mod_query.strip()
        self.mod_search_id += 1

        if not query:
            self.status_label.setText("Загрузка популярных модов...")
            self.mod_worker = AsyncWorker(
                self.mod_search_id,
                get_workshop_mods,
                self.current_appid
            )
        else:
            self.status_label.setText(f"Поиск модов: '{query}'...")
            self.mod_worker = AsyncWorker(
                self.mod_search_id,
                search_workshop,
                self.current_appid,
                query
            )

        self.mod_worker.result_ready.connect(self.on_mods_loaded)
        self.mod_worker.error_occurred.connect(self.on_network_error)
        self.mod_worker.start()

    def on_mods_loaded(self, req_id, mods):
        if req_id != self.mod_search_id:
            return

        self.mods_list.clear()
        for mod in mods:
            item = QListWidgetItem(mod.get("title", f"Mod #{mod.get('id')}"))
            item.setData(Qt.ItemDataRole.UserRole, mod)
            self.mods_list.addItem(item)

        self.status_label.setText(f"Загружено модов: {len(mods)}")

    def on_mod_selected(self, item):
        self.current_mod = item.data(Qt.ItemDataRole.UserRole)
        self._update_selected_mod_info()

    def _update_selected_mod_info(self):
        has_mod = self.current_mod is not None
        self.browser_button.setEnabled(has_mod)
        self.download_button.setEnabled(has_mod)

        if has_mod:
            title = self.current_mod.get("title", "Без названия")
            mod_id = self.current_mod.get("id", "N/A")
            self.info_label.setText(
                f"<b>Выбранный мод:</b> {title} &nbsp;|&nbsp; <b>ID:</b> {mod_id}"
            )
        elif self.current_game:
            self.info_label.setText(
                f"<b>Игра:</b> {self.current_game['name']} (AppID: {self.current_appid}). Выберите мод из списка справа."
            )
        else:
            self.info_label.setText(
                "<i>Выберите игру слева, затем мод справа.</i>"
            )

    def open_mod_workshop(self):
        if not self.current_mod or not self.current_mod.get("id"):
            return
        webbrowser.open(
            f"https://steamcommunity.com/sharedfiles/filedetails/?id={self.current_mod['id']}"
        )

    def on_network_error(self, req_id, error_message):
        self.status_label.setText(f"Ошибка сети: {error_message}")

    # ---------------- СКАЧИВАНИЕ МОДА ЧЕРЕЗ STEAMCMD ----------------

    def download_selected_mod(self):
        if not self.current_mod:
            QMessageBox.warning(self, "Предупреждение", "Выберите мод из списка.")
            return

        steamcmd_path = self.get_steamcmd_path()
        if not steamcmd_path or not os.path.isfile(steamcmd_path):
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Исполняемый файл SteamCMD не найден:\n{steamcmd_path}\n"
                "Укажите верный путь во вкладке 'Настройки'."
            )
            return

        mods_folder = self.get_mods_folder()
        if not mods_folder or not os.path.isdir(mods_folder):
            QMessageBox.warning(
                self,
                "Предупреждение",
                "Укажите существующую папку для сохранения модов во вкладке 'Настройки'."
            )
            return

        self.download_button.setEnabled(False)
        self.download_button.setText("Скачивание через SteamCMD...")
        mod_title = self.current_mod.get("title", "Мод")
        self.status_label.setText(f"Скачивание: {mod_title} (ID: {self.current_mod['id']})...")

        self.download_thread = QThread()
        self.download_worker = SteamCMDWorker(
            steamcmd_path,
            self.current_appid,
            self.current_mod["id"]
        )
        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.output.connect(self.on_download_output)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.on_download_error)

        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_worker.finished.connect(self.download_worker.deleteLater)
        self.download_thread.finished.connect(self.download_thread.deleteLater)

        self.download_thread.start()

    def on_download_output(self, line):
        if line.strip():
            self.status_label.setText(f"SteamCMD: {line.strip()[:95]}")

    def on_download_error(self, err):
        self.status_label.setText(f"Ошибка SteamCMD: {err}")

    def download_finished(self, code):
        self.download_button.setEnabled(True)
        self.download_button.setText("⚡ Скачать выбранный мод через SteamCMD")

        if code != 0:
            self.status_label.setText("Ошибка скачивания через SteamCMD")
            QMessageBox.warning(
                self,
                "Ошибка",
                f"SteamCMD завершил работу с кодом {code}.\n"
                "Не удалось скачать мод (возможно, игра не поддерживает анонимное скачивание модов)."
            )
            return

        steamcmd_path = self.get_steamcmd_path()
        source = os.path.join(
            os.path.dirname(steamcmd_path),
            "steamapps",
            "workshop",
            "content",
            str(self.current_appid),
            str(self.current_mod["id"])
        )

        if not os.path.exists(source):
            self.status_label.setText("Папка с модом не обнаружена")
            QMessageBox.warning(
                self,
                "Внимание",
                f"SteamCMD отработал, но папка с файлами мода не была создана:\n{source}\n\n"
                "Разработчик этой игры мог отключить анонимную загрузку модов из Workshop."
            )
            return

        mods_folder = self.get_mods_folder()
        destination = os.path.join(mods_folder, str(self.current_mod["id"]))

        try:
            shutil.copytree(source, destination, dirs_exist_ok=True)
            self.status_label.setText(f"Мод '{self.current_mod['title']}' успешно сохранен в {destination}")
            QMessageBox.information(
                self,
                "Готово",
                f"Мод успешно скачан и скопирован в папку:\n{destination}"
            )
        except Exception as e:
            self.status_label.setText("Ошибка при копировании файлов мода")
            QMessageBox.critical(self, "Ошибка копирования", f"Не удалось скопировать файлы мода:\n{e}")

    def stop_workers(self):
        if self.download_worker:
            self.download_worker.cancel()
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.quit()
            self.download_thread.wait(1000)
