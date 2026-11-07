import os
import shutil
import sys
import webbrowser

from PyQt6.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
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

from Steam_api import get_workshop_mods, search_games, search_workshop
from steamCmd import SteamCMDWorker


# ==========================================================
# Фоновый поток для неблокирующих сетевых запросов (Steam API)
# ==========================================================
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


class SteamSearchUI(QWidget):
    def __init__(self):
        super().__init__()

        self.settings = QSettings("SBSH", "SteamWorkshopLauncher")

        self.steamcmd_path = self.settings.value(
            "steamcmd_path",
            r"G:\SteamCMD\steamcmd.exe"
        )
        self.mods_folder = self.settings.value("mods_folder", "")

        self.setWindowTitle("Steam Workshop Launcher")
        self.resize(1050, 680)
        self.setMinimumSize(800, 500)

        # Текущее состояние
        self.current_game = None
        self.current_appid = None
        self.current_mod = None
        self.last_text = ""
        self.mod_query = ""

        # Счетчики запросов для предотвращения race condition
        self.game_search_id = 0
        self.mod_search_id = 0
        self.game_worker = None
        self.mod_worker = None
        self.download_worker = None
        self.download_thread = None

        self._setup_ui()
        self._setup_timers()
        self._setup_signals()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(10)
        root_layout.setContentsMargins(15, 15, 15, 15)

        # --- Блок настроек путей ---
        settings_group = QGroupBox("Настройки SteamCMD и сохранения")
        settings_layout = QVBoxLayout(settings_group)

        # Строка SteamCMD
        steamcmd_row = QHBoxLayout()
        self.steamcmd_input = QLineEdit()
        self.steamcmd_input.setPlaceholderText("Путь к steamcmd.exe")
        self.steamcmd_input.setText(self.steamcmd_path)
        self.steamcmd_button = QPushButton("Выбрать SteamCMD")
        steamcmd_row.addWidget(self.steamcmd_input, stretch=4)
        steamcmd_row.addWidget(self.steamcmd_button, stretch=1)
        settings_layout.addLayout(steamcmd_row)

        # Строка папки модов
        mods_folder_row = QHBoxLayout()
        self.mods_path_input = QLineEdit()
        self.mods_path_input.setPlaceholderText("Папка для сохранения скачанных модов")
        self.mods_path_input.setText(self.mods_folder)
        self.mods_path_button = QPushButton("Выбрать папку для модов")
        mods_folder_row.addWidget(self.mods_path_input, stretch=4)
        mods_folder_row.addWidget(self.mods_path_button, stretch=1)
        settings_layout.addLayout(mods_folder_row)

        root_layout.addWidget(settings_group)

        # --- Основная панель со списками ---
        lists_layout = QHBoxLayout()
        lists_layout.setSpacing(15)

        # Левая колонка: Игры
        games_layout = QVBoxLayout()
        games_header = QLabel("<b>Поиск игры в Steam:</b>")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Введите название игры (от 2 символов)...")
        self.search_input.setClearButtonEnabled(True)
        self.games_list = QListWidget()
        games_layout.addWidget(games_header)
        games_layout.addWidget(self.search_input)
        games_layout.addWidget(self.games_list)
        lists_layout.addLayout(games_layout, stretch=1)

        # Правая колонка: Моды
        mods_layout = QVBoxLayout()
        mods_header = QLabel("<b>Моды мастерской Steam:</b>")
        self.mod_search_input = QLineEdit()
        self.mod_search_input.setPlaceholderText("Поиск модов для выбранной игры...")
        self.mod_search_input.setClearButtonEnabled(True)
        self.mods_list = QListWidget()
        mods_layout.addWidget(mods_header)
        mods_layout.addWidget(self.mod_search_input)
        mods_layout.addWidget(self.mods_list)
        lists_layout.addLayout(mods_layout, stretch=1)

        root_layout.addLayout(lists_layout)

        # --- Инфо о выбранном моде ---
        self.selected_mod_label = QLabel(
            "<i>Выберите игру слева, затем мод справа (двойной клик откроет страницу в браузере).</i>"
        )
        self.selected_mod_label.setTextFormat(Qt.TextFormat.RichText)
        root_layout.addWidget(self.selected_mod_label)

        # --- Кнопка скачивания и статус-бар ---
        self.download_button = QPushButton("Скачать выбранный мод через SteamCMD")
        self.download_button.setFixedHeight(38)
        root_layout.addWidget(self.download_button)

        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("color: #666; font-size: 11px;")
        root_layout.addWidget(self.status_label)

    def _setup_timers(self):
        # Таймеры для дебаунса ввода поиска
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.do_search)

        self.mod_timer = QTimer(self)
        self.mod_timer.setSingleShot(True)
        self.mod_timer.timeout.connect(self.do_mod_search)

    def _setup_signals(self):
        # Настройки путей
        self.steamcmd_button.clicked.connect(self.select_steamcmd)
        self.steamcmd_input.editingFinished.connect(self.save_steamcmd_path)
        self.mods_path_button.clicked.connect(self.select_mods_folder)
        self.mods_path_input.editingFinished.connect(self.save_mods_folder)

        # Поиск
        self.search_input.textChanged.connect(self.on_text_changed)
        self.mod_search_input.textChanged.connect(self.on_mod_text_changed)

        # Списки
        self.games_list.itemClicked.connect(self.on_game_selected)
        self.mods_list.itemClicked.connect(self.on_mod_selected)
        self.mods_list.itemDoubleClicked.connect(self.open_mod_workshop)

        # Скачивание
        self.download_button.clicked.connect(self.download_selected_mod)

    # ---------------- ПОИСК ИГР ----------------

    def on_text_changed(self, text):
        self.last_text = text
        self.timer.start(300)

    def do_search(self):
        text = self.last_text.strip()
        if len(text) < 2:
            self.games_list.clear()
            self.status_label.setText("Готов к работе")
            return

        self.game_search_id += 1
        self.status_label.setText(f"Поиск игр по запросу '{text}'...")

        # Запуск в фоновом потоке без заморозки GUI
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

    # ---------------- ВЫБОР ИГРЫ И МОДЫ ----------------

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

    def do_mod_search(self):
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
            item = QListWidgetItem(mod["title"])
            item.setData(Qt.ItemDataRole.UserRole, mod)
            self.mods_list.addItem(item)

        self.status_label.setText(f"Загружено модов: {len(mods)}")

    def on_mod_selected(self, item):
        self.current_mod = item.data(Qt.ItemDataRole.UserRole)
        self._update_selected_mod_info()

    def _update_selected_mod_info(self):
        if self.current_mod:
            title = self.current_mod.get("title", "Без названия")
            mod_id = self.current_mod.get("id", "N/A")
            self.selected_mod_label.setText(
                f"<b>Выбранный мод:</b> {title} &nbsp;|&nbsp; <b>ID:</b> {mod_id} "
                f"&nbsp;<i>(Двойной клик открывает страницу мода в браузере)</i>"
            )
        elif self.current_game:
            self.selected_mod_label.setText(
                f"<b>Игра:</b> {self.current_game['name']} (AppID: {self.current_appid}). Выберите мод из списка справа."
            )
        else:
            self.selected_mod_label.setText(
                "<i>Выберите игру слева, затем мод справа.</i>"
            )

    def open_mod_workshop(self, item):
        mod = item.data(Qt.ItemDataRole.UserRole)
        if not mod or not mod.get("id"):
            return

        webbrowser.open(
            f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod['id']}"
        )

    def on_network_error(self, req_id, error_message):
        self.status_label.setText(f"Ошибка сети: {error_message}")

    # ---------------- СКАЧИВАНИЕ МОДА ЧЕРЕЗ STEAMCMD ----------------

    def download_selected_mod(self):
        if not self.current_mod:
            QMessageBox.warning(
                self,
                "Предупреждение",
                "Пожалуйста, выберите мод из списка для скачивания."
            )
            return

        if not self.steamcmd_path or not os.path.isfile(self.steamcmd_path):
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Исполняемый файл SteamCMD не найден:\n{self.steamcmd_path}\n"
                "Пожалуйста, укажите верный путь к steamcmd.exe."
            )
            return

        if not self.mods_folder or not os.path.isdir(self.mods_folder):
            QMessageBox.warning(
                self,
                "Предупреждение",
                "Пожалуйста, укажите существующую папку для сохранения модов."
            )
            return

        # Блокировка кнопки и обновление интерфейса
        self.download_button.setEnabled(False)
        self.download_button.setText("Скачивание через SteamCMD...")
        mod_title = self.current_mod.get("title", "Мод")
        self.status_label.setText(f"Скачивание: {mod_title} (ID: {self.current_mod['id']})...")

        self.download_thread = QThread()
        self.download_worker = SteamCMDWorker(
            self.steamcmd_path,
            self.current_appid,
            self.current_mod["id"]
        )
        self.download_worker.moveToThread(self.download_thread)

        self.download_thread.started.connect(self.download_worker.run)
        self.download_worker.output.connect(self.on_download_output)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.on_download_error)

        # Очистка ресурсов потока
        self.download_worker.finished.connect(self.download_thread.quit)
        self.download_worker.finished.connect(self.download_worker.deleteLater)
        self.download_thread.finished.connect(self.download_thread.deleteLater)

        self.download_thread.start()

    def on_download_output(self, line):
        # Отображение последней строки вывода SteamCMD
        if line.strip():
            self.status_label.setText(f"SteamCMD: {line.strip()[:90]}")

    def on_download_error(self, err):
        self.status_label.setText(f"Ошибка SteamCMD: {err}")

    def download_finished(self, code):
        self.download_button.setEnabled(True)
        self.download_button.setText("Скачать выбранный мод через SteamCMD")

        if code != 0:
            self.status_label.setText("Ошибка при скачивании мода через SteamCMD")
            QMessageBox.warning(
                self,
                "Ошибка",
                f"SteamCMD завершил работу с кодом {code}.\n"
                "Не удалось скачать мод (проверьте доступность мода для анонимного скачивания)."
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

        if not os.path.exists(source):
            self.status_label.setText("Папка с модом не обнаружена")
            QMessageBox.warning(
                self,
                "Внимание",
                f"SteamCMD завершился, но папка с файлами мода не была найдена:\n{source}\n\n"
                "Возможно, разработчик игры отключил анонимное скачивание модов из мастерской."
            )
            return

        destination = os.path.join(
            self.mods_folder,
            str(self.current_mod["id"])
        )

        try:
            shutil.copytree(source, destination, dirs_exist_ok=True)
            self.status_label.setText(
                f"Мод '{self.current_mod['title']}' успешно скачан и скопирован!"
            )
            QMessageBox.information(
                self,
                "Готово",
                f"Мод успешно скачан и скопирован в папку:\n{destination}"
            )
        except Exception as e:
            self.status_label.setText("Ошибка при копировании файлов мода")
            QMessageBox.critical(
                self,
                "Ошибка копирования",
                f"Не удалось скопировать файлы мода:\n{e}"
            )

    # ---------------- ВЫБОР ПУТЕЙ И СОХРАНЕНИЕ ----------------

    def select_steamcmd(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите steamcmd.exe",
            self.steamcmd_path,
            "SteamCMD (steamcmd.exe);;Executable (*.exe)"
        )
        if filename:
            self.steamcmd_path = filename
            self.steamcmd_input.setText(filename)
            self.settings.setValue("steamcmd_path", filename)

    def save_steamcmd_path(self):
        self.steamcmd_path = self.steamcmd_input.text().strip()
        self.settings.setValue("steamcmd_path", self.steamcmd_path)

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

    def closeEvent(self, event):
        """Безопасная остановка потоков при закрытии окна."""
        if self.download_worker:
            self.download_worker.cancel()
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.quit()
            self.download_thread.wait(1000)
        event.accept()


if __name__ == "__main__":
    # Запуск единого SBSH Launcher (Игры + Моды + Загрузки)
    try:
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        from launcher import main as launcher_main
        launcher_main()
    except Exception:
        app = QApplication(sys.argv)
        window = SteamSearchUI()
        window.show()
        sys.exit(app.exec())
