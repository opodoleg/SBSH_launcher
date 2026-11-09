import os
import shutil
import sys
import webbrowser

from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# Обеспечиваем доступность директорий в sys.path
CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, '..'))
for p in [CURRENT_DIR, ROOT_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

from Steam_api import get_workshop_mods, search_games, search_workshop
from steamCmd import SteamCMDWorker
from Nexus_api import (
    DEFAULT_GAME_DOMAIN,
    DEFAULT_GAME_ID,
    NexusAPI,
    bbcode_to_html,
    format_bytes,
    get_categories_for_game,
    NEXUS_CATEGORIES_BY_GAME,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from modules.saved_games import saved_games_manager
from modules.download_queue import get_download_queue_manager
from modules.installed_mods import installed_mods_manager


# ==========================================================
# Фоновый поток для неблокирующих сетевых запросов
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


class NexusDownloadWorker(QThread):
    """Поток для потоковой загрузки архивов с Nexus Mods с обновлением прогресса."""
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        nexus_api: NexusAPI,
        file_id: int,
        game_id: int,
        mod_id: int,
        game_domain: str,
        save_dir: str,
    ):
        super().__init__()
        self.nexus_api = nexus_api
        self.file_id = file_id
        self.game_id = game_id
        self.mod_id = mod_id
        self.game_domain = game_domain
        self.save_dir = save_dir
        self._is_cancelled = False

    def run(self):
        try:
            path = self.nexus_api.download_file(
                file_id=self.file_id,
                game_id=self.game_id,
                mod_id=self.mod_id,
                game_domain=self.game_domain,
                save_dir=self.save_dir,
                progress_callback=self._on_progress,
                cancel_flag=lambda: self._is_cancelled,
            )
            self.finished.emit(path)
        except Exception as e:
            self.error.emit(str(e))

    def _on_progress(self, downloaded: int, total: int, speed_str: str):
        self.progress.emit(downloaded, total, speed_str)

    def cancel(self):
        self._is_cancelled = True


class AddSavedGameDialog(QDialog):
    """Диалог записи новой игры в список сохранённых."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Записать игру в список")
        self.resize(450, 200)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Например: RimWorld, Project Zomboid...")

        appid_row = QHBoxLayout()
        self.appid_input = QLineEdit()
        self.appid_input.setPlaceholderText("Числовой AppID (например: 294100)")

        self.find_btn = QPushButton("🔎 Найти AppID")
        self.find_btn.clicked.connect(self.find_appid)

        appid_row.addWidget(self.appid_input, stretch=1)
        appid_row.addWidget(self.find_btn)

        form.addRow("Название игры:", self.name_input)
        form.addRow("Steam AppID:", appid_row)
        layout.addLayout(form)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.status_lbl)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.validate_and_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def find_appid(self):
        query = self.name_input.text().strip()
        if not query:
            self.status_lbl.setText("Сначала введите название игры.")
            return

        self.status_lbl.setText("Поиск AppID в Steam...")
        games = search_games(query, limit=1)
        if games:
            found = games[0]
            self.name_input.setText(found["name"])
            self.appid_input.setText(str(found["appid"]))
            self.status_lbl.setText(f"Найдено: {found['name']} (AppID: {found['appid']})")
        else:
            self.status_lbl.setText("Игра не найдена в Steam. Введите AppID вручную.")

    def validate_and_accept(self):
        name = self.name_input.text().strip()
        appid_str = self.appid_input.text().strip()
        if not name or not appid_str.isdigit():
            QMessageBox.warning(self, "Ошибка", "Укажите корректное название и числовой AppID.")
            return
        self.accept()

    def get_data(self):
        return self.name_input.text().strip(), int(self.appid_input.text().strip())


# Кэш байтов картинок в памяти для быстрого переключения
_IMAGE_CACHE: Dict[str, bytes] = {}


class ImageDownloadWorker(QThread):
    """Поток для фоновой загрузки изображений и скриншотов."""
    loaded = pyqtSignal(str, bytes)

    def __init__(self, url: str, session=None):
        super().__init__()
        self.url = url
        # Не храним curl_cffi session, так как curl_cffi не потокобезопасна

    def run(self):
        if not self.url:
            self.loaded.emit(self.url, b"")
            return
        if self.url in _IMAGE_CACHE:
            self.loaded.emit(self.url, _IMAGE_CACHE[self.url])
            return
        try:
            import requests as py_requests
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            }
            resp = py_requests.get(self.url, headers=headers, timeout=12)
            if resp.status_code == 200 and resp.content:
                _IMAGE_CACHE[self.url] = resp.content
                self.loaded.emit(self.url, resp.content)
                return
        except Exception:
            pass
        self.loaded.emit(self.url, b"")


class ScreenshotViewerDialog(QDialog):
    """Модальное окно для полноразмерного просмотра скриншотов мода с Nexus Mods."""

    def __init__(self, parent, urls: List[str], current_index: int = 0, session=None, titles: Optional[List[str]] = None):
        super().__init__(parent)
        self.urls = urls or []
        self.titles = titles or []
        self.current_index = max(0, min(current_index, len(urls) - 1)) if urls else 0
        self.session = session
        self.current_pixmap = None
        self._workers = set()

        self.setWindowTitle(f"Просмотр скриншота ({self.current_index + 1} из {len(self.urls)})")
        self.resize(960, 660)
        self.setMinimumSize(600, 400)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b1120;
                color: #f8fafc;
            }
            QPushButton {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #64748b;
            }
            QPushButton:disabled {
                background-color: #0f172a;
                color: #475569;
                border-color: #1e293b;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # Верхняя панель
        top_bar = QHBoxLayout()
        self.counter_lbl = QLabel(f"Скриншот {self.current_index + 1} из {len(self.urls)}")
        self.counter_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #38bdf8;")
        top_bar.addWidget(self.counter_lbl)

        self.title_lbl = QLabel("")
        self.title_lbl.setStyleSheet("font-size: 13px; color: #e2e8f0;")
        top_bar.addWidget(self.title_lbl)
        top_bar.addStretch()

        self.fullscreen_btn = QPushButton("⛶ На весь экран")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)
        top_bar.addWidget(self.fullscreen_btn)

        close_btn = QPushButton("✕ Закрыть")
        close_btn.clicked.connect(self.accept)
        top_bar.addWidget(close_btn)
        layout.addLayout(top_bar)

        # Контейнер картинки
        self.image_label = QLabel("Загрузка скриншота...")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet("background-color: #020617; border: 1px solid #1e293b; border-radius: 8px;")
        layout.addWidget(self.image_label, stretch=1)

        # Нижняя панель навигации
        nav_bar = QHBoxLayout()
        self.prev_btn = QPushButton("◀ Предыдущий (←)")
        self.prev_btn.clicked.connect(self.show_prev)
        self.prev_btn.setEnabled(len(self.urls) > 1)

        self.browser_btn = QPushButton("🌐 Открыть оригинал в браузере")
        self.browser_btn.clicked.connect(self.open_in_browser)

        self.next_btn = QPushButton("Следующий ▶ (→)")
        self.next_btn.clicked.connect(self.show_next)
        self.next_btn.setEnabled(len(self.urls) > 1)

        nav_bar.addWidget(self.prev_btn)
        nav_bar.addStretch()
        nav_bar.addWidget(self.browser_btn)
        nav_bar.addStretch()
        nav_bar.addWidget(self.next_btn)
        layout.addLayout(nav_bar)

        self.load_current_image()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen_btn.setText("⛶ На весь экран")
        else:
            self.showFullScreen()
            self.fullscreen_btn.setText("🗗 В окно")

    def show_prev(self):
        if not self.urls:
            return
        self.current_index = (self.current_index - 1) % len(self.urls)
        self.load_current_image()

    def show_next(self):
        if not self.urls:
            return
        self.current_index = (self.current_index + 1) % len(self.urls)
        self.load_current_image()

    def open_in_browser(self):
        if self.urls and 0 <= self.current_index < len(self.urls):
            webbrowser.open(self.urls[self.current_index])

    def load_current_image(self):
        if not self.urls:
            self.image_label.setText("Скриншоты отсутствуют")
            return
        url = self.urls[self.current_index]
        title = self.titles[self.current_index] if (self.titles and self.current_index < len(self.titles)) else ""
        self.counter_lbl.setText(f"Скриншот {self.current_index + 1} из {len(self.urls)}")
        if title and title != f"Скриншот {self.current_index + 1}":
            self.title_lbl.setText(f" — <b>{title}</b>")
        else:
            self.title_lbl.setText("")

        title_suffix = f" — {title}" if title else ""
        self.setWindowTitle(f"Просмотр скриншота ({self.current_index + 1} из {len(self.urls)}){title_suffix}")
        self.image_label.setText("Загрузка изображения...")
        self.image_label.setPixmap(QPixmap())

        if url in _IMAGE_CACHE:
            pix = QPixmap()
            if pix.loadFromData(_IMAGE_CACHE[url]):
                self.current_pixmap = pix
                self.update_pixmap_display()
                return

        worker = ImageDownloadWorker(url)
        worker.loaded.connect(self.on_image_loaded)
        worker.finished.connect(lambda: self._workers.discard(worker))
        self._workers.add(worker)
        worker.start()

    def on_image_loaded(self, url, data):
        if not self.urls or url != self.urls[self.current_index] or not data:
            if not data:
                self.image_label.setText("Не удалось загрузить изображение.")
            return

        pix = QPixmap()
        if pix.loadFromData(data):
            self.current_pixmap = pix
            self.update_pixmap_display()
        else:
            self.image_label.setText("Не удалось отобразить изображение.")

    def update_pixmap_display(self):
        if self.current_pixmap and not self.current_pixmap.isNull():
            target_size = self.image_label.size()
            scaled = self.current_pixmap.scaled(
                target_size.width() - 20,
                target_size.height() - 20,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)
            self.image_label.setText("")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_pixmap_display()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            self.show_prev()
        elif event.key() == Qt.Key.Key_Right:
            self.show_next()
        elif event.key() == Qt.Key.Key_F11:
            self.toggle_fullscreen()
        elif event.key() == Qt.Key.Key_Escape:
            if self.isFullScreen():
                self.toggle_fullscreen()
            else:
                self.accept()
        else:
            super().keyPressEvent(event)


class ScreenshotCardWidget(QFrame):
    """Карточка миниатюры скриншота с заголовком, описанием и динамическим масштабированием."""
    clicked = pyqtSignal(int)

    def __init__(self, index: int, url: str, thumb_url: str, title: str = "", parent=None):
        super().__init__(parent)
        self.index = index
        self.url = url
        self.thumb_url = thumb_url
        self.title_text = (title or "").strip()
        self.raw_data: Optional[bytes] = None
        self.current_w = 160
        self.current_h = 95

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"Скриншот #{index + 1}" + (f": {self.title_text}" if self.title_text else "") +
            "\nНажмите для полноразмерного просмотра"
        )
        self.setStyleSheet("""
            ScreenshotCardWidget {
                background-color: #0b1120;
                border: 1px solid #334155;
                border-radius: 8px;
            }
            ScreenshotCardWidget:hover {
                background-color: #1e293b;
                border: 1px solid #38bdf8;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Контейнер изображения
        self.img_lbl = QLabel()
        self.img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img_lbl.setText("⏳")
        self.img_lbl.setStyleSheet("background-color: #020617; border-radius: 5px; color: #64748b;")
        self.img_lbl.setFixedSize(self.current_w, self.current_h)
        layout.addWidget(self.img_lbl)

        # Верхняя плашка: бейдж "Скриншот N"
        self.badge_lbl = QLabel(f"Скриншот {self.index + 1}")
        self.badge_lbl.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold;")
        layout.addWidget(self.badge_lbl)

        # Описание / заголовок
        self.title_lbl = QLabel(self.title_text or "Без описания")
        self.title_lbl.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.title_lbl.setWordWrap(False)
        layout.addWidget(self.title_lbl)

        self.update_dimensions(self.current_w, self.current_h)

    def update_dimensions(self, w: int, h: int):
        self.current_w = w
        self.current_h = h
        self.setFixedWidth(w + 14)
        self.img_lbl.setFixedSize(w, h)
        raw = self.title_text or "Без описания"
        elided = self.title_lbl.fontMetrics().elidedText(raw, Qt.TextElideMode.ElideRight, w)
        self.title_lbl.setText(elided)
        self._refresh_pixmap()

    def set_image_data(self, data: bytes):
        self.raw_data = data
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        if not self.raw_data:
            return
        pix = QPixmap()
        if pix.loadFromData(self.raw_data):
            scaled = pix.scaled(
                self.current_w, self.current_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            cropped = scaled.copy(
                max(0, (scaled.width() - self.current_w) // 2),
                max(0, (scaled.height() - self.current_h) // 2),
                self.current_w, self.current_h,
            )
            self.img_lbl.setPixmap(cropped)
            self.img_lbl.setText("")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


# Предустановленные игры для вкладки Nexus Mods
NEXUS_PRESET_GAMES = [
    {"name": "Cyberpunk 2077", "domain": "cyberpunk2077", "game_id": 3333},
    {"name": "The Witcher 3", "domain": "witcher3", "game_id": 952},
    {"name": "Skyrim Special Edition", "domain": "skyrimspecialedition", "game_id": 1704},
    {"name": "Fallout 4", "domain": "fallout4", "game_id": 1151},
    {"name": "Starfield", "domain": "starfield", "game_id": 4187},
    {"name": "Baldur's Gate 3", "domain": "baldursgate3", "game_id": 3474},
]


class SteamSearchUI(QWidget):
    def __init__(self):
        super().__init__()

        self.settings = QSettings("SBSH", "SteamWorkshopLauncher")

        self.steamcmd_path = self.settings.value(
            "steamcmd_path",
            r"G:\SteamCMD\steamcmd.exe"
        )
        self.mods_folder = self.settings.value("mods_folder", "")

        self.setWindowTitle("Мастерская модов — Steam & Nexus Mods")
        self.resize(1150, 750)
        self.setMinimumSize(900, 600)

        # Текущий источник: "steam" или "nexus"
        self.source_mode = "steam"

        # Состояние Steam
        self.current_game = None
        self.current_appid = None
        self.current_mod = None
        self.last_text = ""
        self.mod_query = ""

        # Состояние Nexus Mods
        self.nexus_api = NexusAPI()
        self.current_nexus_game = NEXUS_PRESET_GAMES[0]  # По умолчанию Cyberpunk 2077
        self.current_nexus_category = "all"
        self.current_nexus_files = []
        self.selected_nexus_file = None
        self.nexus_download_worker = None

        # Состояние коллекций Nexus Mods
        self.nexus_type = "mods"  # "mods" или "collections"
        self.nexus_collection_sort = "downloads"
        self.current_collection = None
        self.current_collection_details = None
        self.selected_collection_mod = None

        # Детали и скриншоты выбранного мода
        self.current_details = None
        self.current_screenshots = []
        self.current_thumbnails = []
        self.current_screenshot_titles = []
        self.screenshot_cards = []

        # Множества активных потоков для предотвращения преждевременного GC в Qt
        self._active_workers = set()
        self._active_image_workers = set()

        # Поколения / идентификаторы запросов для подавления устаревших ответов
        self._request_id = 0
        self._mods_req_id = 0
        self._details_req_id = 0

        self.game_search_id = 0
        self.mod_search_id = 0
        self.files_search_id = 0
        self.details_search_id = 0

        self.game_worker = None
        self.mod_worker = None
        self.files_worker = None
        self.details_worker = None
        self.download_worker = None
        self.download_thread = None

        # Память об установленных модах
        self.current_installed_data = None
        installed_mods_manager.mods_updated.connect(self._refresh_installed_badges_in_list)

        self._setup_ui()
        self._setup_timers()
        self._setup_signals()
        self.load_saved_games_list()
        self.load_nexus_games_list()
        self.populate_nexus_categories(self.current_nexus_game["domain"])

    def _register_worker(self, worker: QThread):
        """Сохраняет ссылку на поток до его завершения для предотвращения сбоев в Qt."""
        if not worker:
            return
        worker.finished.connect(lambda: self._active_workers.discard(worker))
        self._active_workers.add(worker)

    def _register_image_worker(self, worker: QThread):
        """Сохраняет ссылку на поток загрузки изображения до завершения."""
        if not worker:
            return
        worker.finished.connect(lambda: self._active_image_workers.discard(worker))
        self._active_image_workers.add(worker)

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

        # --- Переключатель источника модов ---
        source_bar = QHBoxLayout()
        source_bar.setSpacing(10)

        source_label = QLabel("<b>Источник модов:</b>")
        source_label.setStyleSheet("font-size: 13px;")

        self.source_combo = QComboBox()
        self.source_combo.addItem("🌐 Steam Workshop (SteamCMD)", "steam")
        self.source_combo.addItem("⚡ Nexus Mods (Прямое скачивание)", "nexus")
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)

        source_bar.addWidget(source_label)
        source_bar.addWidget(self.source_combo, stretch=1)
        source_bar.addStretch(2)
        root_layout.addLayout(source_bar)

        # --- Основной сплиттер (3 колонки: Игры | Список модов | Описание и скриншоты) ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ==========================================
        # Левая колонка: Игры (Steam / Nexus)
        # ==========================================
        self.games_tab_widget = QTabWidget()

        # 1. Окно сохранённых игр (Steam)
        saved_widget = QWidget()
        saved_layout = QVBoxLayout(saved_widget)
        saved_layout.setContentsMargins(0, 8, 0, 0)
        saved_layout.setSpacing(8)

        self.saved_games_list = QListWidget()
        self.saved_games_list.itemClicked.connect(self.on_saved_game_selected)

        saved_btn_row = QHBoxLayout()
        self.add_saved_game_btn = QPushButton("➕ Записать игру")
        self.add_saved_game_btn.clicked.connect(self.open_add_game_dialog)
        self.remove_saved_game_btn = QPushButton("🗑 Удалить")
        self.remove_saved_game_btn.clicked.connect(self.remove_selected_saved_game)
        saved_btn_row.addWidget(self.add_saved_game_btn)
        saved_btn_row.addWidget(self.remove_saved_game_btn)

        saved_layout.addWidget(self.saved_games_list)
        saved_layout.addLayout(saved_btn_row)

        # 2. Поиск в Steam
        search_widget = QWidget()
        search_layout = QVBoxLayout(search_widget)
        search_layout.setContentsMargins(0, 8, 0, 0)
        search_layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск новой игры в Steam (от 2 символов)...")
        self.search_input.setClearButtonEnabled(True)

        self.games_list = QListWidget()
        self.games_list.itemClicked.connect(self.on_search_game_selected)

        self.save_from_search_btn = QPushButton("⭐ Записать выбранную игру в список")
        self.save_from_search_btn.setEnabled(False)
        self.save_from_search_btn.clicked.connect(self.save_selected_search_game)

        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.games_list)
        search_layout.addWidget(self.save_from_search_btn)

        # 3. Игры Nexus Mods
        nexus_games_widget = QWidget()
        nexus_games_layout = QVBoxLayout(nexus_games_widget)
        nexus_games_layout.setContentsMargins(0, 8, 0, 0)
        nexus_games_layout.setSpacing(8)

        self.nexus_games_list = QListWidget()
        self.nexus_games_list.itemClicked.connect(self.on_nexus_game_selected)

        nexus_games_layout.addWidget(self.nexus_games_list)

        self.games_tab_widget.addTab(saved_widget, "⭐ Сохранённые игры")
        self.games_tab_widget.addTab(search_widget, "🔍 Поиск в Steam")
        self.games_tab_widget.addTab(nexus_games_widget, "⚡ Игры Nexus")

        self.main_splitter.addWidget(self.games_tab_widget)

        # ==========================================
        # Средняя колонка: Список модов
        # ==========================================
        mods_container = QWidget()
        mods_layout = QVBoxLayout(mods_container)
        mods_layout.setContentsMargins(0, 0, 0, 0)
        mods_layout.setSpacing(8)

        self.mods_header = QLabel("<b>Моды мастерской Steam:</b>")

        # Переключатель типа Nexus Mods: Моды или Коллекции
        self.nexus_type_container = QWidget()
        nexus_type_layout = QHBoxLayout(self.nexus_type_container)
        nexus_type_layout.setContentsMargins(0, 0, 0, 0)
        nexus_type_layout.setSpacing(6)

        self.btn_view_mods = QPushButton("📦 Одиночные моды")
        self.btn_view_mods.setCheckable(True)
        self.btn_view_mods.setChecked(True)
        self.btn_view_mods.clicked.connect(lambda: self.set_nexus_type("mods"))

        self.btn_view_collections = QPushButton("📚 Коллекции модов")
        self.btn_view_collections.setCheckable(True)
        self.btn_view_collections.setChecked(False)
        self.btn_view_collections.clicked.connect(lambda: self.set_nexus_type("collections"))

        nexus_type_layout.addWidget(self.btn_view_mods, stretch=1)
        nexus_type_layout.addWidget(self.btn_view_collections, stretch=1)
        self._update_nexus_type_buttons_style()
        self.nexus_type_container.setVisible(False)

        # Блок выбора категории Nexus Mods (виден только в режиме Nexus -> Моды)
        self.nexus_category_row = QHBoxLayout()
        self.nexus_category_lbl = QLabel("<b>Категория:</b>")
        self.nexus_category_combo = QComboBox()
        self.nexus_category_combo.currentIndexChanged.connect(self.on_nexus_category_changed)
        self.nexus_category_row.addWidget(self.nexus_category_lbl)
        self.nexus_category_row.addWidget(self.nexus_category_combo, stretch=1)

        self.nexus_category_container = QWidget()
        self.nexus_category_container.setLayout(self.nexus_category_row)
        self.nexus_category_container.setVisible(False)

        # Сортировка коллекций Nexus Mods (видна только в режиме Nexus -> Коллекции)
        self.nexus_collection_sort_container = QWidget()
        nexus_sort_layout = QHBoxLayout(self.nexus_collection_sort_container)
        nexus_sort_layout.setContentsMargins(0, 0, 0, 0)
        nexus_sort_layout.setSpacing(6)
        nexus_sort_lbl = QLabel("<b>Сортировка:</b>")
        nexus_sort_lbl.setStyleSheet("color: #94a3b8;")
        self.nexus_collection_sort_combo = QComboBox()
        self.nexus_collection_sort_combo.addItem("🔥 Топ по скачиваниям", "downloads")
        self.nexus_collection_sort_combo.addItem("👍 Топ по одобрениям", "endorsements")
        self.nexus_collection_sort_combo.addItem("⭐ Высокий рейтинг", "rating")
        self.nexus_collection_sort_combo.addItem("🔄 Недавно обновлённые", "recent")
        self.nexus_collection_sort_combo.addItem("✨ Новые", "new")
        self.nexus_collection_sort_combo.currentIndexChanged.connect(self.on_nexus_collection_sort_changed)
        nexus_sort_layout.addWidget(nexus_sort_lbl)
        nexus_sort_layout.addWidget(self.nexus_collection_sort_combo, stretch=1)
        self.nexus_collection_sort_container.setVisible(False)

        mod_search_row = QHBoxLayout()
        mod_search_row.setSpacing(6)

        self.mod_search_input = QLineEdit()
        self.mod_search_input.setPlaceholderText("Поиск модов для выбранной игры...")
        self.mod_search_input.setClearButtonEnabled(True)

        self.filter_only_installed_cb = QCheckBox("✓ Установленные")
        self.filter_only_installed_cb.setStyleSheet("color: #38bdf8; font-size: 11px;")
        self.filter_only_installed_cb.setToolTip("Показывать только скачанные и установленные на диске моды")
        self.filter_only_installed_cb.stateChanged.connect(self._filter_mods_list_installed)

        mod_search_row.addWidget(self.mod_search_input, stretch=1)
        mod_search_row.addWidget(self.filter_only_installed_cb)

        self.mods_list = QListWidget()
        self.mods_list.itemClicked.connect(self.on_mod_selected)
        self.mods_list.itemDoubleClicked.connect(self.open_mod_workshop)

        mods_layout.addWidget(self.mods_header)
        mods_layout.addWidget(self.nexus_type_container)
        mods_layout.addWidget(self.nexus_category_container)
        mods_layout.addWidget(self.nexus_collection_sort_container)
        mods_layout.addLayout(mod_search_row)
        mods_layout.addWidget(self.mods_list)

        self.main_splitter.addWidget(mods_container)

        # ==========================================
        # Правая колонка: Инспектор мода (Описание, Скриншоты, Файлы)
        # ==========================================
        self.details_container = QWidget()
        details_layout = QVBoxLayout(self.details_container)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)

        self.details_title = QLabel("<b>Выберите мод из списка</b>")
        self.details_title.setWordWrap(True)
        self.details_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #f8fafc;")

        self.details_meta = QLabel("Выберите игру слева и мод в центре для просмотра описания и скриншотов.")
        self.details_meta.setWordWrap(True)
        self.details_meta.setStyleSheet("font-size: 11px; color: #94a3b8;")

        # Для обратной совместимости сохраняем ссылку
        self.selected_mod_label = self.details_meta

        self.installed_badge = QLabel()
        self.installed_badge.setWordWrap(True)
        self.installed_badge.setStyleSheet(
            "background-color: #064e3b; color: #34d399; padding: 6px 10px; "
            "border-radius: 6px; font-size: 11px; font-weight: bold;"
        )
        self.installed_badge.setVisible(False)

        details_layout.addWidget(self.details_title)
        details_layout.addWidget(self.details_meta)
        details_layout.addWidget(self.installed_badge)

        # Блок скриншотов
        self.screenshots_box = QGroupBox("Скриншоты")
        self.screenshots_box.setVisible(False)
        screenshots_box_layout = QVBoxLayout(self.screenshots_box)
        screenshots_box_layout.setContentsMargins(6, 6, 6, 6)

        self.screenshots_scroll_widget = QWidget()
        self.screenshots_scroll_layout = QHBoxLayout(self.screenshots_scroll_widget)
        self.screenshots_scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.screenshots_scroll_layout.setSpacing(8)

        self.screenshots_scroll = QScrollArea()
        self.screenshots_scroll.setWidget(self.screenshots_scroll_widget)
        self.screenshots_scroll.setWidgetResizable(True)
        self.screenshots_scroll.setFixedHeight(170)
        self.screenshots_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.screenshots_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.screenshots_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        screenshots_box_layout.addWidget(self.screenshots_scroll)
        details_layout.addWidget(self.screenshots_box)

        # Блок описания
        self.description_box = QGroupBox("Описание мода")
        self.description_box.setVisible(False)
        description_box_layout = QVBoxLayout(self.description_box)
        description_box_layout.setContentsMargins(6, 6, 6, 6)

        self.description_text = QTextBrowser()
        self.description_text.setOpenExternalLinks(True)
        description_box_layout.addWidget(self.description_text)
        details_layout.addWidget(self.description_box, stretch=1)

        # Блок со списком модов внутри коллекции
        self.collection_mods_box = QGroupBox("Состав коллекции (моды)")
        self.collection_mods_box.setVisible(False)
        col_box_layout = QVBoxLayout(self.collection_mods_box)
        col_box_layout.setContentsMargins(6, 6, 6, 6)
        col_box_layout.setSpacing(6)

        col_filter_row = QHBoxLayout()
        self.col_mod_filter_input = QLineEdit()
        self.col_mod_filter_input.setPlaceholderText("🔎 Быстрый поиск в списке модов коллекции...")
        self.col_mod_filter_input.setClearButtonEnabled(True)
        self.col_mod_filter_input.textChanged.connect(self.filter_collection_mods)
        col_filter_row.addWidget(self.col_mod_filter_input)
        col_box_layout.addLayout(col_filter_row)

        self.collection_mods_table = QTableWidget()
        self.collection_mods_table.setColumnCount(5)
        self.collection_mods_table.setHorizontalHeaderLabels(["№", "Тип", "Мод", "Версия", "Размер"])
        self.collection_mods_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.collection_mods_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.collection_mods_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.collection_mods_table.verticalHeader().setVisible(False)
        self.collection_mods_table.horizontalHeader().setStretchLastSection(False)
        self.collection_mods_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.collection_mods_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.collection_mods_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.collection_mods_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.collection_mods_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.collection_mods_table.itemSelectionChanged.connect(self.on_collection_mod_selected)
        col_box_layout.addWidget(self.collection_mods_table)
        details_layout.addWidget(self.collection_mods_box, stretch=2)

        # Блок выбора файлов Nexus Mods (виден только в режиме Nexus -> Моды)
        self.nexus_files_row = QHBoxLayout()
        self.nexus_files_lbl = QLabel("<b>Файл для скачивания:</b>")
        self.nexus_files_combo = QComboBox()
        self.nexus_files_combo.currentIndexChanged.connect(self.on_nexus_file_selected)
        self.nexus_files_row.addWidget(self.nexus_files_lbl)
        self.nexus_files_row.addWidget(self.nexus_files_combo, stretch=1)

        self.nexus_files_container = QWidget()
        self.nexus_files_container.setLayout(self.nexus_files_row)
        self.nexus_files_container.setVisible(False)
        details_layout.addWidget(self.nexus_files_container)

        # Панель действий: Открыть в браузере и Скачать (для одиночных модов)
        self.single_mod_actions_container = QWidget()
        actions_row = QHBoxLayout(self.single_mod_actions_container)
        actions_row.setContentsMargins(0, 0, 0, 0)
        actions_row.setSpacing(10)

        self.browser_button = QPushButton("🌐 Открыть страницу")
        self.browser_button.setEnabled(False)
        self.browser_button.clicked.connect(self.open_mod_workshop)

        self.open_installed_folder_btn = QPushButton("📂 Папка")
        self.open_installed_folder_btn.setToolTip("Показать файлы мода в проводнике Windows")
        self.open_installed_folder_btn.setVisible(False)
        self.open_installed_folder_btn.clicked.connect(self.show_current_mod_in_explorer)

        self.delete_installed_mod_btn = QPushButton("🗑️")
        self.delete_installed_mod_btn.setToolTip("Удалить установленный мод с диска")
        self.delete_installed_mod_btn.setVisible(False)
        self.delete_installed_mod_btn.clicked.connect(self.delete_current_installed_mod)

        self.download_button = QPushButton("Скачать выбранный мод через SteamCMD")
        self.download_button.setFixedHeight(36)
        self.download_button.setEnabled(False)

        actions_row.addWidget(self.browser_button)
        actions_row.addWidget(self.open_installed_folder_btn)
        actions_row.addWidget(self.delete_installed_mod_btn)
        actions_row.addWidget(self.download_button, stretch=1)
        details_layout.addWidget(self.single_mod_actions_container)

        # Действия для коллекций Nexus Mods
        self.collection_actions_container = QWidget()
        col_actions_layout = QVBoxLayout(self.collection_actions_container)
        col_actions_layout.setContentsMargins(0, 0, 0, 0)
        col_actions_layout.setSpacing(6)

        c_row1 = QHBoxLayout()
        c_row1.setSpacing(8)
        self.download_collection_btn = QPushButton("📥 Скачать всю коллекцию в очередь")
        self.download_collection_btn.setFixedHeight(36)
        self.download_collection_btn.setEnabled(False)
        self.download_collection_btn.clicked.connect(self.download_entire_collection)

        self.download_col_manifest_btn = QPushButton("💾 Манифест (.7z)")
        self.download_col_manifest_btn.setFixedHeight(36)
        self.download_col_manifest_btn.setEnabled(False)
        self.download_col_manifest_btn.clicked.connect(self.download_collection_manifest)

        c_row1.addWidget(self.download_collection_btn, stretch=3)
        c_row1.addWidget(self.download_col_manifest_btn, stretch=1)

        c_row2 = QHBoxLayout()
        c_row2.setSpacing(8)
        self.col_browser_btn = QPushButton("🌐 Открыть на Nexus Mods")
        self.col_browser_btn.clicked.connect(self.open_mod_workshop)

        self.col_open_installed_btn = QPushButton("📂 Папка манифеста")
        self.col_open_installed_btn.setVisible(False)
        self.col_open_installed_btn.clicked.connect(self.show_current_mod_in_explorer)

        self.download_single_col_mod_btn = QPushButton("⬇️ Скачать выбранный мод из коллекции")
        self.download_single_col_mod_btn.setEnabled(False)
        self.download_single_col_mod_btn.clicked.connect(self.download_selected_collection_mod)

        c_row2.addWidget(self.col_browser_btn, stretch=1)
        c_row2.addWidget(self.col_open_installed_btn)
        c_row2.addWidget(self.download_single_col_mod_btn, stretch=2)

        col_actions_layout.addLayout(c_row1)
        col_actions_layout.addLayout(c_row2)
        self.collection_actions_container.setVisible(False)
        details_layout.addWidget(self.collection_actions_container)

        self.main_splitter.addWidget(self.details_container)
        self.main_splitter.setSizes([240, 360, 520])

        root_layout.addWidget(self.main_splitter, stretch=1)

        # --- Статус-бар ---
        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("color: #666; font-size: 11px;")
        root_layout.addWidget(self.status_label)

    def _setup_timers(self):
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.do_search)

        self.mod_timer = QTimer(self)
        self.mod_timer.setSingleShot(True)
        self.mod_timer.timeout.connect(self.do_mod_search)

        # Дебаунс-таймеры для предотвращения спама запросами при быстром переключении
        self.game_debounce_timer = QTimer(self)
        self.game_debounce_timer.setSingleShot(True)
        self.game_debounce_timer.timeout.connect(self._on_game_debounced)

        self.mod_debounce_timer = QTimer(self)
        self.mod_debounce_timer.setSingleShot(True)
        self.mod_debounce_timer.timeout.connect(self._on_mod_debounced)

    def _setup_signals(self):
        self.steamcmd_button.clicked.connect(self.select_steamcmd)
        self.steamcmd_input.editingFinished.connect(self.save_steamcmd_path)
        self.mods_path_button.clicked.connect(self.select_mods_folder)
        self.mods_path_input.editingFinished.connect(self.save_mods_folder)

        self.search_input.textChanged.connect(self.on_text_changed)
        self.mod_search_input.textChanged.connect(self.on_mod_text_changed)

        self.download_button.clicked.connect(self.download_selected_mod)
        self.games_tab_widget.currentChanged.connect(self.on_games_tab_changed)

    def _on_game_debounced(self):
        """Отправляет сетевой запрос за списком модов только когда пользователь остановился на игре/категории."""
        if self.source_mode == "nexus":
            if getattr(self, "nexus_type", "mods") == "collections":
                self.load_nexus_collections_for_current_game()
            else:
                self.load_nexus_mods_for_current_game()
        else:
            if self.current_appid:
                self.load_workshop_mods_for_current_game()

    def _on_mod_debounced(self):
        """Отправляет сетевой запрос за файлами и описанием только когда пользователь остановился на моде/коллекции."""
        if self.source_mode == "nexus":
            if getattr(self, "nexus_type", "mods") == "collections" and self.current_collection:
                slug = self.current_collection.get("slug")
                if slug:
                    self.load_nexus_collection_details(slug, self._details_req_id)
            elif self.current_mod:
                mid = self.current_mod.get("mod_id")
                if mid:
                    self.load_nexus_mod_files(mid, self._details_req_id)
                    self.load_nexus_mod_details(mid, self._details_req_id)

    def on_games_tab_changed(self, index: int):
        """Срабатывает при переключении вкладок игр (Сохранённые / Поиск / Nexus)."""
        self._request_id += 1
        self._mods_req_id = self._request_id
        self._details_req_id = self._request_id
        if hasattr(self, "game_debounce_timer"):
            self.game_debounce_timer.stop()
        if hasattr(self, "mod_debounce_timer"):
            self.mod_debounce_timer.stop()

    # ==========================================
    # ПЕРЕКЛЮЧЕНИЕ ИСТОЧНИКА (STEAM / NEXUS)
    # ==========================================

    def _update_nexus_type_buttons_style(self):
        is_mods = (getattr(self, "nexus_type", "mods") == "mods")
        active_style = (
            "QPushButton { background-color: #38bdf8; color: #0f172a; font-weight: bold; "
            "border: 1px solid #38bdf8; border-radius: 6px; padding: 7px 14px; font-size: 12px; }"
        )
        inactive_style = (
            "QPushButton { background-color: #22232e; color: #94a3b8; font-weight: normal; "
            "border: 1px solid #3b3e52; border-radius: 6px; padding: 7px 14px; font-size: 12px; } "
            "QPushButton:hover { background-color: #2d3040; color: #f1f2f8; }"
        )
        if hasattr(self, "btn_view_mods") and hasattr(self, "btn_view_collections"):
            self.btn_view_mods.setStyleSheet(active_style if is_mods else inactive_style)
            self.btn_view_collections.setStyleSheet(inactive_style if is_mods else active_style)

    def set_nexus_type(self, type_name: str):
        """Переключает режим отображения между одиночными модами и коллекциями."""
        if type_name == getattr(self, "nexus_type", "mods"):
            return
        self.nexus_type = type_name
        self.btn_view_mods.setChecked(type_name == "mods")
        self.btn_view_collections.setChecked(type_name == "collections")
        self._update_nexus_type_buttons_style()

        self.mods_list.clear()
        self.mod_search_input.clear()
        self.clear_collection_details()
        self.current_mod = None
        self.current_collection = None
        self._update_selected_mod_info()

        game_title = self.current_nexus_game["name"]
        if type_name == "collections":
            self.nexus_category_container.setVisible(False)
            self.nexus_collection_sort_container.setVisible(True)
            self.nexus_files_container.setVisible(False)
            self.collection_mods_box.setVisible(True)
            self.collection_actions_container.setVisible(True)
            self.single_mod_actions_container.setVisible(False)
            self.screenshots_box.setVisible(False)
            self.mods_header.setText(f"<b>Коллекции Nexus Mods ({game_title}):</b>")
            self.mod_search_input.setPlaceholderText(f"Поиск коллекций на Nexus ({game_title}) или оставьте пустым для топа...")
            self.load_nexus_collections_for_current_game()
        else:
            self.nexus_category_container.setVisible(True)
            self.nexus_collection_sort_container.setVisible(False)
            self.nexus_files_container.setVisible(True)
            self.collection_mods_box.setVisible(False)
            self.collection_actions_container.setVisible(False)
            self.single_mod_actions_container.setVisible(True)
            self.browser_button.setText("🌐 Открыть страницу")
            self.mods_header.setText(f"<b>Моды Nexus Mods ({game_title}):</b>")
            self.mod_search_input.setPlaceholderText(f"Поиск модов на Nexus ({game_title}) или оставьте пустым для топа...")
            self.download_button.setText("⚡ Скачать выбранный мод с Nexus Mods")
            self.populate_nexus_categories(self.current_nexus_game["domain"])
            self.load_nexus_mods_for_current_game()

    def on_nexus_collection_sort_changed(self, index: int):
        """Срабатывает при смене сортировки коллекций (топ скачиваний, рейтинг, новые)."""
        if index >= 0 and hasattr(self, "nexus_collection_sort_combo"):
            self.nexus_collection_sort = self.nexus_collection_sort_combo.itemData(index) or "downloads"
            self._request_id += 1
            self._mods_req_id = self._request_id
            self.mod_debounce_timer.stop()
            self.game_debounce_timer.start(250)

    def on_source_changed(self, index):
        mode = self.source_combo.itemData(index)
        self.source_mode = mode
        self.current_mod = None
        self.current_collection = None
        self.mods_list.clear()
        self.mod_search_input.clear()
        self.clear_collection_details()
        self._update_selected_mod_info()

        self._request_id += 1
        self._mods_req_id = self._request_id
        self._details_req_id = self._request_id
        self.game_debounce_timer.stop()
        self.mod_debounce_timer.stop()

        if mode == "steam":
            self.games_tab_widget.setTabVisible(0, True)
            self.games_tab_widget.setTabVisible(1, True)
            self.games_tab_widget.setTabVisible(2, False)
            self.games_tab_widget.setCurrentIndex(0)
            self.nexus_type_container.setVisible(False)
            self.nexus_category_container.setVisible(False)
            self.nexus_collection_sort_container.setVisible(False)
            self.nexus_files_container.setVisible(False)
            self.collection_mods_box.setVisible(False)
            self.collection_actions_container.setVisible(False)
            self.single_mod_actions_container.setVisible(True)

            self.mods_header.setText("<b>Моды мастерской Steam (Workshop):</b>")
            self.mod_search_input.setPlaceholderText("Поиск модов для выбранной игры...")
            self.download_button.setText("Скачать выбранный мод через SteamCMD")
            if self.current_appid:
                self.game_debounce_timer.start(250)
        else:
            self.games_tab_widget.setTabVisible(0, False)
            self.games_tab_widget.setTabVisible(1, False)
            self.games_tab_widget.setTabVisible(2, True)
            self.games_tab_widget.setCurrentIndex(2)
            self.nexus_type_container.setVisible(True)

            game_title = self.current_nexus_game["name"]
            if getattr(self, "nexus_type", "mods") == "collections":
                self.nexus_category_container.setVisible(False)
                self.nexus_collection_sort_container.setVisible(True)
                self.nexus_files_container.setVisible(False)
                self.collection_mods_box.setVisible(True)
                self.collection_actions_container.setVisible(True)
                self.single_mod_actions_container.setVisible(False)
                self.screenshots_box.setVisible(False)
                self.mods_header.setText(f"<b>Коллекции Nexus Mods ({game_title}):</b>")
                self.mod_search_input.setPlaceholderText(f"Поиск коллекций на Nexus ({game_title}) или оставьте пустым для топа...")
            else:
                self.nexus_category_container.setVisible(True)
                self.nexus_collection_sort_container.setVisible(False)
                self.nexus_files_container.setVisible(True)
                self.collection_mods_box.setVisible(False)
                self.collection_actions_container.setVisible(False)
                self.single_mod_actions_container.setVisible(True)
                self.browser_button.setText("🌐 Открыть страницу")
                self.mods_header.setText(f"<b>Моды Nexus Mods ({game_title}):</b>")
                self.mod_search_input.setPlaceholderText(f"Поиск модов на Nexus ({game_title}) или оставьте пустым для топа...")
                self.download_button.setText("⚡ Скачать выбранный мод с Nexus Mods")
                self.populate_nexus_categories(self.current_nexus_game["domain"])

            self.game_debounce_timer.start(250)

    def populate_nexus_categories(self, game_domain: str):
        """Заполняет выпадающий список категорий Nexus Mods для выбранной игры."""
        if not hasattr(self, "nexus_category_combo"):
            return
        self.nexus_category_combo.blockSignals(True)
        self.nexus_category_combo.clear()
        cats = get_categories_for_game(game_domain)
        for raw_c, disp_c in cats:
            self.nexus_category_combo.addItem(disp_c, raw_c)
        self.nexus_category_combo.setCurrentIndex(0)
        self.current_nexus_category = "all"
        self.nexus_category_combo.blockSignals(False)

    def on_nexus_category_changed(self, index):
        """Срабатывает при выборе категории из выпадающего списка."""
        if index >= 0 and hasattr(self, "nexus_category_combo"):
            self.current_nexus_category = self.nexus_category_combo.itemData(index) or "all"
            self._request_id += 1
            self._mods_req_id = self._request_id
            self.mod_debounce_timer.stop()
            self.game_debounce_timer.start(250)

    def load_nexus_games_list(self):
        """Заполняет список доступных игр для Nexus Mods."""
        self.nexus_games_list.clear()
        for g in NEXUS_PRESET_GAMES:
            item = QListWidgetItem(f"🎮 {g['name']} (ID: {g['game_id']})")
            item.setData(Qt.ItemDataRole.UserRole, g)
            self.nexus_games_list.addItem(item)

        # Выбираем первую игру (Cyberpunk 2077) по умолчанию
        if self.nexus_games_list.count() > 0:
            self.nexus_games_list.setCurrentRow(0)

        # Скрываем вкладку Nexus пока активен режим Steam
        self.games_tab_widget.setTabVisible(2, False)

    def on_nexus_game_selected(self, item):
        game = item.data(Qt.ItemDataRole.UserRole)
        if not game:
            return
        self.current_nexus_game = game
        self.current_mod = None
        self._update_selected_mod_info()
        self.mods_header.setText(f"<b>Моды Nexus Mods ({game['name']}):</b>")
        self.mod_search_input.setPlaceholderText(f"Поиск модов на Nexus ({game['name']}) или оставьте пустым для топа...")
        self.populate_nexus_categories(game["domain"])

        self._request_id += 1
        self._mods_req_id = self._request_id
        self.mod_debounce_timer.stop()
        self.game_debounce_timer.start(250)

    # ==========================================
    # РАБОТА С СОХРАНЁННЫМИ ИГРАМИ (STEAM)
    # ==========================================

    def load_saved_games_list(self):
        self.saved_games_list.clear()
        saved = saved_games_manager.get_all()
        for g in saved:
            item = QListWidgetItem(f"🎮 {g['name']} (AppID: {g['appid']})")
            item.setData(Qt.ItemDataRole.UserRole, g)
            self.saved_games_list.addItem(item)
        self.games_tab_widget.setTabText(0, f"⭐ Сохранённые игры ({len(saved)})")

    def on_saved_game_selected(self, item):
        game = item.data(Qt.ItemDataRole.UserRole)
        if not game:
            return
        self.current_game = game
        self.current_appid = game["appid"]
        self.current_mod = None
        self._update_selected_mod_info()

        self._request_id += 1
        self._mods_req_id = self._request_id
        self.mod_debounce_timer.stop()
        self.game_debounce_timer.start(250)

    def open_add_game_dialog(self):
        dialog = AddSavedGameDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, appid = dialog.get_data()
            saved_games_manager.add(name, appid)
            self.load_saved_games_list()
            self.status_label.setText(f"Игра '{name}' сохранена!")
            self.select_game(name, appid)

    def remove_selected_saved_game(self):
        item = self.saved_games_list.currentItem()
        if not item:
            QMessageBox.information(self, "Информация", "Выберите игру для удаления.")
            return
        game = item.data(Qt.ItemDataRole.UserRole)
        if not game:
            return
        reply = QMessageBox.question(
            self,
            "Удаление игры",
            f"Удалить '{game['name']}' из списка сохранённых?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            saved_games_manager.remove(appid=game.get("appid"))
            self.load_saved_games_list()
            self.status_label.setText(f"Игра '{game['name']}' удалена.")

    def save_selected_search_game(self):
        if not self.current_game or not self.current_appid:
            return
        name = self.current_game["name"]
        appid = self.current_appid
        saved_games_manager.add(name, appid)
        self.load_saved_games_list()
        self.save_from_search_btn.setText("★ Игра записана в сохранённые!")
        self.save_from_search_btn.setEnabled(False)
        self.status_label.setText(f"Игра '{name}' добавлена в сохранённые!")

    # ==========================================
    # ПОИСК В STEAM
    # ==========================================

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

        self.game_worker = AsyncWorker(self.game_search_id, search_games, text)
        self.game_worker.result_ready.connect(self.on_games_loaded)
        self.game_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.game_worker)
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

    def on_search_game_selected(self, item):
        game = item.data(Qt.ItemDataRole.UserRole)
        if not game:
            return

        self.current_game = game
        self.current_appid = game["appid"]
        self.current_mod = None
        self._update_selected_mod_info()

        if saved_games_manager.is_saved(appid=self.current_appid):
            self.save_from_search_btn.setText("★ Игра уже в сохранённых")
            self.save_from_search_btn.setEnabled(False)
        else:
            self.save_from_search_btn.setText(f"⭐ Записать '{game['name']}' в мои игры")
            self.save_from_search_btn.setEnabled(True)

        self._request_id += 1
        self._mods_req_id = self._request_id
        self.mod_debounce_timer.stop()
        self.game_debounce_timer.start(250)

    # ==========================================
    # ВЫБОР ИГРЫ И МОДОВ
    # ==========================================

    def load_workshop_mods_for_current_game(self):
        if not self.current_appid:
            return

        self.mods_list.clear()
        self.mod_search_input.clear()
        req_id = self._mods_req_id

        game_name = self.current_game.get("name", "Игры") if self.current_game else "Игры"
        self.mods_header.setText(f"<b>Моды для {game_name} (AppID: {self.current_appid}):</b>")
        self.status_label.setText(f"Загрузка популярных модов для {game_name}...")

        self.mod_worker = AsyncWorker(
            req_id,
            get_workshop_mods,
            self.current_appid,
        )
        self.mod_worker.result_ready.connect(self.on_mods_loaded)
        self.mod_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.mod_worker)
        self.mod_worker.start()

    def load_nexus_mods_for_current_game(self):
        """Загружает самые популярные моды с Nexus Mods для выбранной игры и категории."""
        self.mods_list.clear()
        self.nexus_files_combo.clear()
        req_id = self._mods_req_id

        game_info = self.current_nexus_game
        category = self.current_nexus_category
        cat_disp = self.nexus_category_combo.currentText() if hasattr(self, "nexus_category_combo") and self.nexus_category_combo.count() > 0 else "Все категории"
        self.status_label.setText(f"Загрузка топа модов Nexus Mods для {game_info['name']} ({cat_disp})...")

        # Поиск популярных модов
        self.mod_worker = AsyncWorker(
            req_id,
            self.nexus_api.search_mods,
            query="",
            category=category,
            game_id=game_info["game_id"],
            game_domain=game_info["domain"],
            count=25,
        )
        self.mod_worker.result_ready.connect(self.on_nexus_mods_loaded)
        self.mod_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.mod_worker)
        self.mod_worker.start()

    def load_nexus_collections_for_current_game(self):
        """Загружает коллекции модов Nexus Mods для выбранной игры."""
        self.mods_list.clear()
        self.clear_collection_details()
        req_id = self._mods_req_id

        game_info = self.current_nexus_game
        sort = getattr(self, "nexus_collection_sort", "downloads")
        self.status_label.setText(f"Загрузка коллекций Nexus Mods для {game_info['name']}...")

        self.mod_worker = AsyncWorker(
            req_id,
            self.nexus_api.search_collections,
            query="",
            sort=sort,
            game_id=game_info["game_id"],
            game_domain=game_info["domain"],
            count=30,
        )
        self.mod_worker.result_ready.connect(self.on_nexus_collections_loaded)
        self.mod_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.mod_worker)
        self.mod_worker.start()

    def on_mod_text_changed(self, text):
        self.mod_query = text
        self.mod_timer.start(350)

    def do_mod_search(self):
        query = self.mod_query.strip()
        self._request_id += 1
        self._mods_req_id = self._request_id
        req_id = self._mods_req_id

        if self.source_mode == "nexus":
            game_info = self.current_nexus_game
            if getattr(self, "nexus_type", "mods") == "collections":
                sort = getattr(self, "nexus_collection_sort", "downloads")
                label_target = f"'{query}'" if query else "топ коллекций"
                self.status_label.setText(f"Nexus Mods: поиск коллекций {label_target}...")
                self.mod_worker = AsyncWorker(
                    req_id,
                    self.nexus_api.search_collections,
                    query=query,
                    sort=sort,
                    game_id=game_info["game_id"],
                    game_domain=game_info["domain"],
                    count=30,
                )
                self.mod_worker.result_ready.connect(self.on_nexus_collections_loaded)
                self.mod_worker.error_occurred.connect(self.on_network_error)
                self._register_worker(self.mod_worker)
                self.mod_worker.start()
                return

            category = self.current_nexus_category
            cat_disp = self.nexus_category_combo.currentText() if hasattr(self, "nexus_category_combo") and self.nexus_category_combo.count() > 0 else "Все"
            label_target = f"'{query}'" if query else f"топ популярных ({cat_disp})"
            self.status_label.setText(f"Nexus Mods: поиск {label_target}...")
            self.mod_worker = AsyncWorker(
                req_id,
                self.nexus_api.search_mods,
                query=query,
                category=category,
                game_id=game_info["game_id"],
                game_domain=game_info["domain"],
                count=25,
            )
            self.mod_worker.result_ready.connect(self.on_nexus_mods_loaded)
            self.mod_worker.error_occurred.connect(self.on_network_error)
            self._register_worker(self.mod_worker)
            self.mod_worker.start()
            return

        # Режим Steam Workshop
        if not self.current_appid:
            return

        if not query:
            self.status_label.setText("Загрузка популярных модов Steam...")
            self.mod_worker = AsyncWorker(
                req_id,
                get_workshop_mods,
                self.current_appid,
            )
        else:
            self.status_label.setText(f"Поиск модов в Steam: '{query}'...")
            self.mod_worker = AsyncWorker(
                req_id,
                search_workshop,
                self.current_appid,
                query,
            )

        self.mod_worker.result_ready.connect(self.on_mods_loaded)
        self.mod_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.mod_worker)
        self.mod_worker.start()

    def on_mods_loaded(self, req_id, mods):
        if req_id != self._mods_req_id:
            return

        self.mods_list.clear()
        only_inst = self.filter_only_installed_cb.isChecked() if hasattr(self, "filter_only_installed_cb") else False
        installed_count = 0

        for mod in mods:
            mid = mod.get("id")
            installed, inst_data = installed_mods_manager.is_installed("steam", mid, game_id=self.current_appid)
            badge = "✅ [Установлен] " if installed else ""
            if installed:
                installed_count += 1

            item = QListWidgetItem(f"{badge}{mod['title']}")
            item.setData(Qt.ItemDataRole.UserRole, mod)
            item.setData(Qt.ItemDataRole.UserRole + 1, bool(installed))
            if installed:
                item.setForeground(QColor("#22c55e"))
            if only_inst and not installed:
                item.setHidden(True)
            self.mods_list.addItem(item)

        self.status_label.setText(f"Загружено модов: {len(mods)} (установлено: {installed_count})")

    def on_nexus_mods_loaded(self, req_id, mods):
        if req_id != self._mods_req_id:
            return

        self.mods_list.clear()
        only_inst = self.filter_only_installed_cb.isChecked() if hasattr(self, "filter_only_installed_cb") else False
        installed_count = 0
        gid = self.current_nexus_game.get("game_id")

        for mod in mods:
            mid = mod.get("mod_id")
            installed, inst_data = installed_mods_manager.is_installed("nexus", mid, game_id=gid)
            badge = "✅ [Установлен] " if installed else ""
            if installed:
                installed_count += 1

            dl_cnt = mod.get("downloads", 0)
            dl_str = f"📥 {dl_cnt:,}" if dl_cnt else ""
            cat = mod.get("category", "")
            cat_str = f"[{cat}] " if cat and cat != "Nexus" else ""
            author_str = f"👤 {mod.get('author')}" if mod.get("author") else ""

            parts = [p for p in [cat_str + mod['title'], dl_str, author_str] if p]
            item_text = "  |  ".join(parts) if len(parts) > 1 else parts[0]
            item_text = f"{badge}{item_text}"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, mod)
            item.setData(Qt.ItemDataRole.UserRole + 1, bool(installed))
            if installed:
                item.setForeground(QColor("#22c55e"))
            if only_inst and not installed:
                item.setHidden(True)
            self.mods_list.addItem(item)

        self.status_label.setText(f"Nexus Mods: найдено {len(mods)} модов (установлено: {installed_count})")

    def on_nexus_collections_loaded(self, req_id, collections):
        if req_id != self._mods_req_id:
            return

        self.mods_list.clear()
        only_inst = self.filter_only_installed_cb.isChecked() if hasattr(self, "filter_only_installed_cb") else False
        installed_count = 0

        for col in collections:
            slug = col.get("slug")
            installed, inst_data = installed_mods_manager.is_installed("nexus", slug)
            badge = "✅ [Установлена] " if installed else ""
            if installed:
                installed_count += 1

            cnt = col.get("mod_count", 0)
            sz = col.get("total_size_human", "—")
            dl = col.get("downloads", 0)
            dl_str = f"📥 {dl:,}" if dl else ""
            end = col.get("endorsements", 0)
            end_str = f"👍 {end:,}" if end else ""
            user = col.get("user")
            user_str = f"👤 {user}" if user else ""

            line1 = f"📚 {col['name']}  [📦 {cnt} модов | 💾 {sz}]"
            stats = [p for p in [dl_str, end_str, user_str] if p]
            line2 = "  |  ".join(stats) if stats else ""

            item_text = f"{line1}\n{line2}" if line2 else line1
            item_text = f"{badge}{item_text}"

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, col)
            item.setData(Qt.ItemDataRole.UserRole + 1, bool(installed))
            if installed:
                item.setForeground(QColor("#22c55e"))
            if only_inst and not installed:
                item.setHidden(True)
            self.mods_list.addItem(item)

        self.status_label.setText(f"Nexus Mods: найдено {len(collections)} коллекций (установлено: {installed_count})")

    def on_mod_selected(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if getattr(self, "nexus_type", "mods") == "collections" and self.source_mode == "nexus":
            self.current_collection = data
            self.current_mod = None
            self._update_selected_collection_info()
            self._request_id += 1
            self._details_req_id = self._request_id
            self.mod_debounce_timer.start(250)
            return

        self.current_mod = data
        self.current_collection = None
        self._update_selected_mod_info()

        if self.source_mode == "nexus" and self.current_mod:
            self._request_id += 1
            self._details_req_id = self._request_id
            self.mod_debounce_timer.start(250)

    def load_nexus_mod_files(self, mod_id: int, req_id: Optional[int] = None):
        """Загружает список доступных файлов для выбранного мода Nexus."""
        if not mod_id:
            return

        if req_id is None:
            self._request_id += 1
            self._details_req_id = self._request_id
            req_id = self._details_req_id

        self.nexus_files_combo.clear()
        self.nexus_files_combo.addItem("Загрузка файлов...")
        self.status_label.setText("Загрузка списка файлов мода с Nexus Mods...")

        domain = self.current_mod.get("game_domain", self.current_nexus_game["domain"]) if self.current_mod else self.current_nexus_game["domain"]
        self.files_worker = AsyncWorker(
            req_id,
            self.nexus_api.get_mod_files,
            mod_id=mod_id,
            game_domain=domain,
        )
        self.files_worker.result_ready.connect(self.on_nexus_files_loaded)
        self.files_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.files_worker)
        self.files_worker.start()

    def on_nexus_files_loaded(self, req_id, files):
        if req_id != self._details_req_id:
            return

        self.current_nexus_files = files
        self.nexus_files_combo.clear()

        if not files:
            self.nexus_files_combo.addItem("Файлы не найдены (проверьте страницу мода)")
            self.selected_nexus_file = None
            self.status_label.setText("Файлы для скачивания не найдены")
            return

        for f in files:
            ver = f" (v{f['version']})" if f.get("version") else ""
            label = f"{f['name']}{ver} [{f['size_human']}]"
            self.nexus_files_combo.addItem(label, f)

        self.selected_nexus_file = files[0]
        self.status_label.setText(f"Доступно файлов для скачивания: {len(files)}")

    def load_nexus_mod_details(self, mod_id: int, req_id: Optional[int] = None):
        """Загружает подробную информацию о моде (описание и скриншоты)."""
        if not mod_id:
            return

        if req_id is None:
            self._request_id += 1
            self._details_req_id = self._request_id
            req_id = self._details_req_id

        self.clear_screenshots()
        self.description_box.setVisible(True)
        self.description_text.setHtml("<i>Загрузка описания и скриншотов со страницы Nexus Mods...</i>")

        gid = self.current_mod.get("game_id", self.current_nexus_game["game_id"]) if self.current_mod else self.current_nexus_game["game_id"]
        domain = self.current_mod.get("game_domain", self.current_nexus_game["domain"]) if self.current_mod else self.current_nexus_game["domain"]

        self.details_worker = AsyncWorker(
            req_id,
            self.nexus_api.get_mod_details,
            mod_id=mod_id,
            game_id=gid,
            game_domain=domain,
        )
        self.details_worker.result_ready.connect(self.on_nexus_mod_details_loaded)
        self.details_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.details_worker)
        self.details_worker.start()

    def on_nexus_mod_details_loaded(self, req_id, details):
        if req_id != self._details_req_id or not self.current_mod:
            return

        self.current_details = details

        # 1. Текст описания
        html_desc = details.get("html_description") or ""
        if not html_desc and details.get("description"):
            html_desc = bbcode_to_html(details["description"])
        if not html_desc:
            summary = self.current_mod.get("summary", "")
            html_desc = f"<i>{summary}</i>" if summary else "<i>Описание отсутствует на странице мода.</i>"

        self.description_text.setHtml(html_desc)

        # 2. Метаданные (автор, категория, скачивания, одобрения, версия)
        title = self.current_mod.get("title", "Без названия")
        author = self.current_mod.get("author", "Неизвестен")
        category = self.current_mod.get("category", "")
        mod_id = self.current_mod.get("mod_id", "N/A")
        downloads = self.current_mod.get("downloads", 0)
        endorsements = details.get("endorsements", 0)
        ver = details.get("version", "")

        parts = [f"👤 <b>{author}</b>"]
        if category and category != "Nexus":
            parts.append(f"🏷️ <b>{category}</b>")
        if downloads:
            parts.append(f"📥 <b>{downloads:,}</b> скачиваний")
        if endorsements:
            parts.append(f"👍 <b>{endorsements:,}</b> одобрений")
        if ver:
            parts.append(f"📦 <b>v{ver}</b>")
        parts.append(f"<b>ID:</b> {mod_id}")

        self.details_meta.setText(" &nbsp;|&nbsp; ".join(parts))

        # 3. Галерея скриншотов
        screenshots = details.get("screenshots", [])
        thumbnails = details.get("thumbnails", [])
        titles = details.get("screenshot_titles", [])
        self.populate_screenshots(screenshots, thumbnails, titles)

    def populate_screenshots(self, screenshots: List[str], thumbnails: List[str], titles: Optional[List[str]] = None):
        """Отображает горизонтальную ленту карточек скриншотов с заголовками и описанием."""
        self.clear_screenshots()
        if not screenshots and not thumbnails:
            self.screenshots_box.setVisible(False)
            return

        self.current_screenshots = screenshots or thumbnails
        self.current_thumbnails = thumbnails or screenshots
        self.current_screenshot_titles = titles or [f"Скриншот {i+1}" for i in range(len(self.current_screenshots))]
        self.screenshots_box.setVisible(True)

        for i, thumb_url in enumerate(self.current_thumbnails):
            title = self.current_screenshot_titles[i] if i < len(self.current_screenshot_titles) else f"Скриншот {i+1}"
            full_url = self.current_screenshots[i] if i < len(self.current_screenshots) else thumb_url
            card = ScreenshotCardWidget(i, full_url, thumb_url, title, parent=self.screenshots_scroll_widget)
            card.clicked.connect(self.open_screenshot_viewer)
            self.screenshots_scroll_layout.addWidget(card)
            self.screenshot_cards.append(card)

            self.load_thumbnail_async(thumb_url, card)

        self.update_screenshots_scale()

    def update_screenshots_scale(self):
        """Динамически масштабирует миниатюры скриншотов при разворачивании окна на весь экран."""
        if not hasattr(self, "screenshots_scroll") or not hasattr(self, "screenshots_box"):
            return
        if self.screenshots_box.isHidden() or not hasattr(self, "screenshot_cards") or not self.screenshot_cards:
            return

        top_win = self.window()
        is_max = (top_win.isMaximized() if top_win else False) or self.isMaximized()
        win_w = top_win.width() if top_win else self.width()
        win_h = top_win.height() if top_win else self.height()

        if is_max or (win_h >= 880 and win_w >= 1350):
            thumb_w, thumb_h = 260, 150
            scroll_h = 240
        elif win_h >= 750 and win_w >= 1150:
            thumb_w, thumb_h = 205, 120
            scroll_h = 200
        else:
            thumb_w, thumb_h = 160, 95
            scroll_h = 170

        self.screenshots_scroll.setFixedHeight(scroll_h)
        for card in self.screenshot_cards:
            card.update_dimensions(thumb_w, thumb_h)

    def load_thumbnail_async(self, url: str, card: ScreenshotCardWidget):
        """Асинхронно скачивает картинку превью и передает в карточку."""
        if not url:
            return
        if url in _IMAGE_CACHE:
            card.set_image_data(_IMAGE_CACHE[url])
            return

        expected_req_id = self._details_req_id
        worker = ImageDownloadWorker(url)
        def on_done(u, data):
            if self._details_req_id != expected_req_id:
                return
            if data and u == url:
                _IMAGE_CACHE[u] = data
                try:
                    card.set_image_data(data)
                except Exception:
                    pass
        worker.loaded.connect(on_done)
        self._register_image_worker(worker)
        worker.start()

    def clear_screenshots(self):
        """Очищает предыдущие миниатюры скриншотов."""
        for worker in list(self._active_image_workers):
            try:
                worker.loaded.disconnect()
            except Exception:
                pass
        self._active_image_workers.clear()
        self.screenshot_cards.clear()

        while self.screenshots_scroll_layout.count() > 0:
            item = self.screenshots_scroll_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def open_screenshot_viewer(self, idx: int):
        """Открывает модальное окно полноразмерного просмотра скриншотов."""
        if not hasattr(self, "current_screenshots") or not self.current_screenshots:
            return
        titles = getattr(self, "current_screenshot_titles", None)
        dialog = ScreenshotViewerDialog(self, self.current_screenshots, idx, self.nexus_api.session, titles=titles)
        dialog.exec()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_screenshots_scale()

    def on_nexus_file_selected(self, index):
        if index >= 0:
            self.selected_nexus_file = self.nexus_files_combo.itemData(index)

    def _update_selected_mod_info(self):
        if self.source_mode == "nexus":
            if getattr(self, "nexus_type", "mods") == "collections":
                self._update_selected_collection_info()
                return

            if self.current_mod:
                title = self.current_mod.get("title", "Без названия")
                author = self.current_mod.get("author", "Неизвестен")
                category = self.current_mod.get("category", "")
                cat_badge = f" &nbsp;|&nbsp; 🏷️ <b>Категория:</b> {category}" if category and category != "Nexus" else ""
                mod_id = self.current_mod.get("mod_id", "N/A")
                dl = self.current_mod.get("downloads", 0)
                dl_badge = f" &nbsp;|&nbsp; 📥 <b>Скачиваний:</b> {dl:,}" if dl else ""

                gid = self.current_nexus_game.get("game_id")
                installed, inst_data = installed_mods_manager.is_installed("nexus", mod_id, game_id=gid)
                self.current_installed_data = inst_data if installed else None

                self.details_title.setText(f"<b>{title}</b>")
                self.details_meta.setText(f"👤 <b>Автор:</b> {author}{cat_badge}{dl_badge} &nbsp;|&nbsp; <b>ID:</b> {mod_id}")
                self.description_box.setVisible(True)
                self.download_button.setEnabled(True)
                self.browser_button.setEnabled(True)

                if installed and inst_data:
                    p = inst_data.get("install_path", "")
                    t = inst_data.get("installed_at", "")
                    self.installed_badge.setText(f"✅ Мод скачан и сохранён на диске (дата: {t})\n📂 Путь: {p}")
                    self.installed_badge.setVisible(True)
                    self.open_installed_folder_btn.setVisible(True)
                    self.delete_installed_mod_btn.setVisible(True)
                    self.download_button.setText("🔄 Скачать заново / Обновить")
                else:
                    self.installed_badge.setVisible(False)
                    self.open_installed_folder_btn.setVisible(False)
                    self.delete_installed_mod_btn.setVisible(False)
                    self.download_button.setText("⚡ Скачать выбранный мод с Nexus Mods")
            else:
                self.details_title.setText("<i>Выберите мод из списка</i>")
                self.details_meta.setText("Выберите игру слева и мод в центре для просмотра описания и скриншотов.")
                self.description_box.setVisible(False)
                self.screenshots_box.setVisible(False)
                self.download_button.setEnabled(False)
                self.browser_button.setEnabled(False)
                self.installed_badge.setVisible(False)
                self.open_installed_folder_btn.setVisible(False)
                self.delete_installed_mod_btn.setVisible(False)
                self.clear_screenshots()
            return

        # Режим Steam
        if self.current_mod:
            title = self.current_mod.get("title", "Без названия")
            mod_id = self.current_mod.get("id", "N/A")
            installed, inst_data = installed_mods_manager.is_installed("steam", mod_id, game_id=self.current_appid)
            self.current_installed_data = inst_data if installed else None

            self.details_title.setText(f"<b>{title}</b>")
            self.details_meta.setText(f"<b>Steam Workshop ID:</b> {mod_id} &nbsp;|&nbsp; <b>AppID:</b> {self.current_appid}")
            self.description_box.setVisible(True)
            self.screenshots_box.setVisible(False)
            self.description_text.setHtml(
                f"<b>Мод мастерской Steam Workshop:</b> {title}<br><br>"
                f"AppID игры: {self.current_appid}<br>"
                f"ID элемента: {mod_id}<br><br>"
                f"<i>Нажмите 'Скачать' для загрузки мода через SteamCMD или откройте страницу в браузере.</i>"
            )
            self.download_button.setEnabled(True)
            self.browser_button.setEnabled(True)

            if installed and inst_data:
                p = inst_data.get("install_path", "")
                t = inst_data.get("installed_at", "")
                self.installed_badge.setText(f"✅ Мод установлен на диске (дата: {t})\n📂 Путь: {p}")
                self.installed_badge.setVisible(True)
                self.open_installed_folder_btn.setVisible(True)
                self.delete_installed_mod_btn.setVisible(True)
                self.download_button.setText("🔄 Перекачать через SteamCMD")
            else:
                self.installed_badge.setVisible(False)
                self.open_installed_folder_btn.setVisible(False)
                self.delete_installed_mod_btn.setVisible(False)
                self.download_button.setText("⚡ Скачать выбранный мод через SteamCMD")
        else:
            self.details_title.setText("<i>Выберите мод из списка</i>")
            self.details_meta.setText("Выберите игру и мод для просмотра информации.")
            self.description_box.setVisible(False)
            self.screenshots_box.setVisible(False)
            self.download_button.setEnabled(False)
            self.browser_button.setEnabled(False)
            self.installed_badge.setVisible(False)
            self.open_installed_folder_btn.setVisible(False)
            self.delete_installed_mod_btn.setVisible(False)
            self.clear_screenshots()

    # ==========================================
    # РАБОТА С КОЛЛЕКЦИЯМИ NEXUS MODS
    # ==========================================

    def _update_selected_collection_info(self):
        if not self.current_collection:
            self.clear_collection_details()
            self.details_title.setText("<i>Выберите коллекцию из списка</i>")
            self.details_meta.setText("Выберите коллекцию для просмотра состава модов и описания.")
            self.description_box.setVisible(False)
            self.collection_mods_box.setVisible(False)
            self.collection_actions_container.setVisible(False)
            return

        c = self.current_collection
        name = c.get("name", "Без названия")
        user = c.get("user", "Неизвестен")
        cnt = c.get("mod_count", 0)
        sz = c.get("total_size_human", "—")
        dl = c.get("downloads", 0)
        end = c.get("endorsements", 0)
        summary = c.get("summary", "")

        slug = c.get("slug")
        installed, inst_data = installed_mods_manager.is_installed("nexus", slug)
        self.current_installed_data = inst_data if installed else None

        self.details_title.setText(f"<b>📚 {name}</b>")
        self.details_meta.setText(
            f"👤 <b>Куратор:</b> {user} &nbsp;|&nbsp; "
            f"📦 <b>{cnt}</b> модов &nbsp;|&nbsp; "
            f"💾 <b>{sz}</b> &nbsp;|&nbsp; "
            f"📥 <b>{dl:,}</b> скачиваний &nbsp;|&nbsp; "
            f"👍 <b>{end:,}</b> одобрений"
        )
        self.description_box.setVisible(True)
        self.description_text.setHtml(
            f"<i>{summary}</i><br><br><i>Загрузка полного состава коллекции и списка модов...</i>"
            if summary else "<i>Загрузка полного состава коллекции и списка модов...</i>"
        )
        self.screenshots_box.setVisible(False)
        self.collection_mods_box.setVisible(True)
        self.collection_actions_container.setVisible(True)
        self.download_collection_btn.setText(f"📥 Скачать всю коллекцию ({cnt} модов)")
        self.download_collection_btn.setEnabled(False)
        self.download_col_manifest_btn.setEnabled(False)
        self.download_single_col_mod_btn.setEnabled(False)
        self.col_browser_btn.setEnabled(True)

        if installed and inst_data:
            p = inst_data.get("install_path", "")
            t = inst_data.get("installed_at", "")
            self.installed_badge.setText(f"✅ Манифест коллекции скачан на диск (дата: {t})\n📂 Путь: {p}")
            self.installed_badge.setVisible(True)
            self.col_open_installed_btn.setVisible(True)
        else:
            self.installed_badge.setVisible(False)
            self.col_open_installed_btn.setVisible(False)

    def _filter_mods_list_installed(self):
        """Фильтрует видимость модов в списке по признаку установки."""
        only_installed = self.filter_only_installed_cb.isChecked() if hasattr(self, "filter_only_installed_cb") else False
        for i in range(self.mods_list.count()):
            item = self.mods_list.item(i)
            is_inst = item.data(Qt.ItemDataRole.UserRole + 1)
            item.setHidden(bool(only_installed and not is_inst))

    def _refresh_installed_badges_in_list(self):
        """Обновляет бейджи [✓ Установлен] и цвет в текущем списке модов."""
        if not hasattr(self, "mods_list"):
            return
        only_inst = self.filter_only_installed_cb.isChecked() if hasattr(self, "filter_only_installed_cb") else False
        gid = self.current_nexus_game.get("game_id") if self.source_mode == "nexus" else self.current_appid

        for i in range(self.mods_list.count()):
            item = self.mods_list.item(i)
            mod = item.data(Qt.ItemDataRole.UserRole)
            if not mod:
                continue

            if getattr(self, "nexus_type", "mods") == "collections" and self.source_mode == "nexus":
                slug = mod.get("slug")
                installed, _ = installed_mods_manager.is_installed("nexus", slug)
            elif self.source_mode == "nexus":
                mid = mod.get("mod_id")
                installed, _ = installed_mods_manager.is_installed("nexus", mid, game_id=gid)
            else:
                mid = mod.get("id")
                installed, _ = installed_mods_manager.is_installed("steam", mid, game_id=self.current_appid)

            item.setData(Qt.ItemDataRole.UserRole + 1, bool(installed))
            text = item.text()
            has_badge = text.startswith("✅ [Установлен] ") or text.startswith("✅ [Установлена] ")

            if installed:
                item.setForeground(QColor("#22c55e"))
                if not has_badge:
                    b_prefix = "✅ [Установлена] " if getattr(self, "nexus_type", "mods") == "collections" else "✅ [Установлен] "
                    item.setText(f"{b_prefix}{text}")
            else:
                item.setForeground(QColor("#e2e8f0"))
                if has_badge:
                    clean = text.replace("✅ [Установлен] ", "").replace("✅ [Установлена] ", "")
                    item.setText(clean)

            if only_inst:
                item.setHidden(not bool(installed))
            else:
                item.setHidden(False)

        if self.current_mod or self.current_collection:
            self._update_selected_mod_info()

    def show_current_mod_in_explorer(self):
        inst = getattr(self, "current_installed_data", None)
        if not inst:
            return
        path = inst.get("install_path")
        if not path:
            return
        clean = os.path.abspath(path)
        if os.name == "nt":
            if os.path.exists(clean):
                import subprocess
                subprocess.run(f'explorer /select,"{clean}"', shell=True)
            else:
                QMessageBox.warning(self, "Внимание", f"Файл не найден на диске:\n{clean}")
        else:
            if os.path.exists(clean):
                import webbrowser
                parent = os.path.dirname(clean) if os.path.isfile(clean) else clean
                webbrowser.open(parent)

    def delete_current_installed_mod(self):
        inst = getattr(self, "current_installed_data", None)
        if not inst:
            return
        title = inst.get("title", "Мод")
        path = inst.get("install_path", "")
        key = inst.get("key") or inst.get("mod_id")

        reply = QMessageBox.question(
            self,
            "Удаление мода",
            f"Вы уверены, что хотите удалить мод с диска?\n\n<b>{title}</b>\n\nПуть: {path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = installed_mods_manager.unregister_mod(key, delete_files=True)
            if not success and msg:
                QMessageBox.warning(self, "Предупреждение", f"Не удалось удалить файлы: {msg}")
            self._update_selected_mod_info()
            self._refresh_installed_badges_in_list()

    def clear_collection_details(self):
        self.current_collection_details = None
        self.selected_collection_mod = None
        if hasattr(self, "collection_mods_table"):
            self.collection_mods_table.setRowCount(0)
        if hasattr(self, "col_mod_filter_input"):
            self.col_mod_filter_input.clear()
        if hasattr(self, "download_collection_btn"):
            self.download_collection_btn.setEnabled(False)
            self.download_col_manifest_btn.setEnabled(False)
            self.download_single_col_mod_btn.setEnabled(False)

    def load_nexus_collection_details(self, slug: str, req_id: Optional[int] = None):
        """Запрашивает данные о коллекции и составе её модов."""
        if not slug:
            return

        if req_id is None:
            self._request_id += 1
            self._details_req_id = self._request_id
            req_id = self._details_req_id

        self.description_box.setVisible(True)
        self.description_text.setHtml("<i>Загрузка данных коллекции и списка модов со страниц Nexus Mods...</i>")
        self.collection_mods_table.setRowCount(0)
        self.download_collection_btn.setEnabled(False)
        self.download_col_manifest_btn.setEnabled(False)
        self.download_single_col_mod_btn.setEnabled(False)
        self.status_label.setText(f"Загрузка состава коллекции '{slug}'...")

        domain = self.current_collection.get("game_domain", self.current_nexus_game["domain"]) if self.current_collection else self.current_nexus_game["domain"]

        self.details_worker = AsyncWorker(
            req_id,
            self.nexus_api.get_collection_details,
            slug=slug,
            game_domain=domain,
        )
        self.details_worker.result_ready.connect(self.on_nexus_collection_details_loaded)
        self.details_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.details_worker)
        self.details_worker.start()

    def on_nexus_collection_details_loaded(self, req_id, details):
        if req_id != self._details_req_id or not details:
            return

        self.current_collection_details = details

        # 1. Текст описания в Markdown
        desc_md = details.get("description") or details.get("summary") or "<i>Описание отсутствует</i>"
        try:
            self.description_text.setMarkdown(desc_md)
        except Exception:
            self.description_text.setHtml(desc_md.replace("\n", "<br>"))

        # 2. Метаданные коллекции
        name = details.get("name", "Коллекция")
        user = details.get("user", "Неизвестен")
        cnt = details.get("mod_count", 0)
        sz = details.get("total_size_human", "—")
        dl = details.get("downloads", 0)
        end = details.get("endorsements", 0)
        rev = details.get("revision_number", 1)

        self.details_title.setText(f"<b>📚 {name}</b>")
        self.details_meta.setText(
            f"👤 Куратор: <b>{user}</b> &nbsp;|&nbsp; "
            f"📦 <b>{cnt}</b> модов &nbsp;|&nbsp; "
            f"💾 <b>{sz}</b> &nbsp;|&nbsp; "
            f"📥 <b>{dl:,}</b> скачиваний &nbsp;|&nbsp; "
            f"👍 <b>{end:,}</b> одобрений &nbsp;|&nbsp; "
            f"Ревизия <b>#{rev}</b>"
        )

        # 3. Заполнение таблицы модов коллекции
        mods = details.get("mods") or []
        self._populate_collection_mods_table(mods)

        # 4. Управление кнопками
        self.download_collection_btn.setText(f"📥 Скачать всю коллекцию ({cnt} модов)")
        self.download_collection_btn.setEnabled(len(mods) > 0)
        self.download_col_manifest_btn.setEnabled(bool(details.get("download_link")))
        self.col_browser_btn.setEnabled(True)

        self.status_label.setText(f"Коллекция '{name}' загружена: {len(mods)} модов")

    def _populate_collection_mods_table(self, mods: List[Dict[str, Any]]):
        """Заполняет таблицу модов коллекции данными."""
        self.collection_mods_table.setRowCount(0)
        self.collection_mods_table.setRowCount(len(mods))

        for row, m in enumerate(mods):
            idx_item = QTableWidgetItem(str(m.get("index", row + 1)))
            idx_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            opt = m.get("optional", False)
            type_str = "Опц." if opt else "Основной"
            type_item = QTableWidgetItem(type_str)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if opt:
                type_item.setForeground(Qt.GlobalColor.gray)
            else:
                type_item.setForeground(Qt.GlobalColor.cyan)

            name_item = QTableWidgetItem(m.get("name", "Mod"))
            name_item.setData(Qt.ItemDataRole.UserRole, m)

            ver_item = QTableWidgetItem(str(m.get("version", "")))
            ver_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            sz_item = QTableWidgetItem(m.get("size_human", "—"))
            sz_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            self.collection_mods_table.setItem(row, 0, idx_item)
            self.collection_mods_table.setItem(row, 1, type_item)
            self.collection_mods_table.setItem(row, 2, name_item)
            self.collection_mods_table.setItem(row, 3, ver_item)
            self.collection_mods_table.setItem(row, 4, sz_item)

    def filter_collection_mods(self, text: str):
        """Фильтрует строки таблицы состава коллекции по введенному тексту."""
        query = text.strip().lower()
        for row in range(self.collection_mods_table.rowCount()):
            name_item = self.collection_mods_table.item(row, 2)
            if not name_item:
                continue
            match = not query or (query in name_item.text().lower())
            self.collection_mods_table.setRowHidden(row, not match)

    def on_collection_mod_selected(self):
        """Срабатывает при клике на строку мода в таблице состава коллекции."""
        selected_rows = self.collection_mods_table.selectionModel().selectedRows()
        if not selected_rows:
            self.selected_collection_mod = None
            self.download_single_col_mod_btn.setEnabled(False)
            self.download_single_col_mod_btn.setText("⬇️ Скачать выбранный мод из коллекции")
            return

        row = selected_rows[0].row()
        item = self.collection_mods_table.item(row, 2)
        if item:
            self.selected_collection_mod = item.data(Qt.ItemDataRole.UserRole)
            m_name = self.selected_collection_mod.get("name", "мод")
            short_name = m_name[:24] + "..." if len(m_name) > 24 else m_name
            self.download_single_col_mod_btn.setText(f"⬇️ Скачать '{short_name}'")
            self.download_single_col_mod_btn.setEnabled(True)

    def download_entire_collection(self):
        """Добавляет все файлы модов коллекции в общую очередь загрузок."""
        if not self.current_collection_details:
            return

        mods = self.current_collection_details.get("mods") or []
        if not mods:
            QMessageBox.warning(self, "Внимание", "В выбранной коллекции нет доступных файлов для скачивания.")
            return

        col_name = self.current_collection_details.get("name", "Коллекция")
        total_sz = self.current_collection_details.get("total_size_human", "—")
        count = len(mods)

        req_mods = [m for m in mods if not m.get("optional", False)]
        opt_mods = [m for m in mods if m.get("optional", False)]

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Скачивание коллекции Nexus Mods")
        msg_box.setText(
            f"<b>Коллекция:</b> {col_name}<br>"
            f"<b>Всего модов:</b> {count} (обязательных: {len(req_mods)}, опциональных: {len(opt_mods)})<br>"
            f"<b>Общий размер:</b> ~{total_sz}<br><br>"
            "Все моды будут последовательно добавлены в очередь загрузок лаунчера "
            "с соблюдением интервалов между запросами (защита от лимитов Nexus Mods)."
        )

        btn_all = msg_box.addButton(f"Скачать ВСЕ ({count})", QMessageBox.ButtonRole.AcceptRole)
        btn_req = None
        if opt_mods:
            btn_req = msg_box.addButton(f"Только обязательные ({len(req_mods)})", QMessageBox.ButtonRole.ActionRole)
        btn_cancel = msg_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)

        msg_box.exec()
        clicked = msg_box.clickedButton()

        if clicked == btn_cancel:
            return

        mods_to_queue = req_mods if (btn_req and clicked == btn_req) else mods

        qm = get_download_queue_manager(self.nexus_api)
        domain = self.current_collection_details.get("game_domain", self.current_nexus_game["domain"])
        save_folder = self.mods_folder
        if not save_folder or not os.path.isdir(save_folder):
            save_folder = os.getenv("MODS_FOLDER") or os.path.join(os.getcwd(), "mods")
        os.makedirs(save_folder, exist_ok=True)

        added = 0
        for m in mods_to_queue:
            file_id = m.get("file_id")
            mod_id = m.get("mod_id")
            if not file_id:
                continue
            qm.add_nexus_task(
                mod_id=mod_id or file_id,
                file_id=file_id,
                title=f"[{col_name}] {m.get('name', 'Mod')}",
                file_name=f"{m.get('name', 'mod')}_{file_id}.zip",
                game_id=m.get("game_id", self.current_nexus_game["game_id"]),
                game_domain=domain,
                save_dir=save_folder,
                size_human=m.get("size_human", "—"),
            )
            added += 1

        self.download_collection_btn.setText(f"✓ Добавлено {added} модов в очередь!")
        QTimer.singleShot(2500, lambda: self.download_collection_btn.setText(f"📥 Скачать всю коллекцию ({count} модов)"))
        self.status_label.setText(f"Добавлено {added} модов коллекции '{col_name}' в очередь скачивания")
        QMessageBox.information(
            self,
            "Коллекция добавлена в очередь",
            f"Успешно добавлено {added} модов в очередь скачивания!\n\n"
            "Вы можете наблюдать за процессом скачивания в очереди загрузок."
        )

    def download_selected_collection_mod(self):
        """Добавляет выбранный мод из таблицы коллекции в очередь скачивания."""
        if not self.selected_collection_mod:
            return

        m = self.selected_collection_mod
        file_id = m.get("file_id")
        mod_id = m.get("mod_id")
        if not file_id:
            QMessageBox.warning(self, "Внимание", "У выбранного мода отсутствует file_id.")
            return

        col_name = self.current_collection_details.get("name", "Коллекция") if self.current_collection_details else "Коллекция"
        domain = self.current_collection_details.get("game_domain", self.current_nexus_game["domain"]) if self.current_collection_details else self.current_nexus_game["domain"]
        save_folder = self.mods_folder
        if not save_folder or not os.path.isdir(save_folder):
            save_folder = os.getenv("MODS_FOLDER") or os.path.join(os.getcwd(), "mods")
        os.makedirs(save_folder, exist_ok=True)

        qm = get_download_queue_manager(self.nexus_api)
        task = qm.add_nexus_task(
            mod_id=mod_id or file_id,
            file_id=file_id,
            title=f"[{col_name}] {m.get('name', 'Mod')}",
            file_name=f"{m.get('name', 'mod')}_{file_id}.zip",
            game_id=m.get("game_id", self.current_nexus_game["game_id"]),
            game_domain=domain,
            save_dir=save_folder,
            size_human=m.get("size_human", "—"),
        )
        self.download_single_col_mod_btn.setText(f"✓ Добавлен (#{task.task_id})!")
        m_name = m.get("name", "мод")
        short_name = m_name[:24] + "..." if len(m_name) > 24 else m_name
        QTimer.singleShot(1500, lambda: self.download_single_col_mod_btn.setText(f"⬇️ Скачать '{short_name}'"))

    def download_collection_manifest(self):
        """Скачивает файл манифеста Vortex коллекции (.7z)."""
        if not self.current_collection_details:
            return
        link = self.current_collection_details.get("download_link")
        if not link:
            QMessageBox.warning(self, "Внимание", "Для этой ревизии коллекции ссылка на манифест не найдена.")
            return

        self.status_label.setText("Получение прямой ссылки на манифест (.7z)...")
        manifest_url = self.nexus_api.get_collection_manifest_url(link)
        if not manifest_url:
            QMessageBox.warning(self, "Ошибка", "Не удалось сгенерировать прямую ссылку на манифест коллекции.")
            return

        save_folder = self.mods_folder
        if not save_folder or not os.path.isdir(save_folder):
            save_folder = os.getenv("MODS_FOLDER") or os.path.join(os.getcwd(), "mods")
        os.makedirs(save_folder, exist_ok=True)

        col_slug = self.current_collection_details.get("slug", "collection")
        col_name = self.current_collection_details.get("name", "collection")
        rev = self.current_collection_details.get("revision_number", 1)
        import re
        safe_name = re.sub(r'[\\/*?:"<>|]', "_", f"{col_name}_rev{rev}_{col_slug}.7z")
        target_path = os.path.join(save_folder, safe_name)

        try:
            dl_session = self.nexus_api.create_session()
            resp = dl_session.get(manifest_url, stream=True, timeout=20)
            if resp.status_code == 200:
                with open(target_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)

                # Регистрация манифеста в установленных модах
                try:
                    from modules.installed_mods import installed_mods_manager
                    installed_mods_manager.register_mod(
                        source="nexus",
                        mod_id=col_slug,
                        title=f"Коллекция: {col_name} (Rev #{rev})",
                        install_path=target_path,
                        game_domain=self.current_nexus_game["domain"],
                        file_name=safe_name,
                        version=f"rev_{rev}",
                        is_collection=True,
                    )
                except Exception:
                    pass

                QMessageBox.information(
                    self,
                    "Манифест скачан",
                    f"Файл манифеста коллекции успешно сохранён:\n{target_path}"
                )
                self.status_label.setText(f"Манифест коллекции сохранён в {safe_name}")
            else:
                QMessageBox.warning(self, "Ошибка", f"Сервер вернул статус {resp.status_code} при скачивании манифеста.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка скачивания", f"Не удалось скачать манифест: {e}")

    def select_game(self, game_name: str, appid: int):
        if appid and appid > 0:
            game = {"name": game_name, "appid": appid}
            self.current_game = game
            self.current_appid = appid
            self.current_mod = None
            self._update_selected_mod_info()
            self.games_tab_widget.setCurrentIndex(0)
            self.load_workshop_mods_for_current_game()
        else:
            self.games_tab_widget.setCurrentIndex(1)
            self.search_input.setText(game_name)
            self.do_search()

    def open_mod_workshop(self, item=None):
        if getattr(self, "nexus_type", "mods") == "collections" and self.source_mode == "nexus":
            url = None
            if self.current_collection_details and self.current_collection_details.get("url"):
                url = self.current_collection_details["url"]
            elif self.current_collection and self.current_collection.get("url"):
                url = self.current_collection["url"]
            elif self.current_nexus_game:
                url = f"https://next.nexusmods.com/{self.current_nexus_game['domain']}/collections"
            if url:
                webbrowser.open(url)
            return

        mod = self.current_mod
        if item and hasattr(item, "data"):
            mod = item.data(Qt.ItemDataRole.UserRole) or mod
        if not mod:
            return

        if self.source_mode == "nexus":
            url = mod.get("url") or f"https://www.nexusmods.com/{self.current_nexus_game['domain']}/mods/{mod.get('mod_id')}"
            webbrowser.open(url)
            return

        if mod.get("id"):
            webbrowser.open(
                f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod['id']}"
            )

    def on_network_error(self, req_id, error_message):
        self.status_label.setText(f"Ошибка сети: {error_message}")

    # ==========================================
    # СКАЧИВАНИЕ МОДА
    # ==========================================

    def download_selected_mod(self):
        if not self.current_mod:
            QMessageBox.warning(
                self,
                "Предупреждение",
                "Пожалуйста, выберите мод из списка для скачивания."
            )
            return

        if not self.mods_folder or not os.path.isdir(self.mods_folder):
            reply = QMessageBox.question(
                self,
                "Папка для модов не выбрана",
                "Папка для сохранения модов ещё не указана. Выбрать папку сейчас?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.select_mods_folder()
            if not self.mods_folder or not os.path.isdir(self.mods_folder):
                return

        # Если выбран Nexus Mods
        if self.source_mode == "nexus":
            self.download_nexus_mod()
            return

        # Иначе SteamCMD
        self.download_steam_mod()

    def download_nexus_mod(self):
        """Добавление мода с Nexus Mods в очередь скачивания."""
        file_info = self.selected_nexus_file
        if not file_info:
            QMessageBox.warning(
                self,
                "Внимание",
                "Подождите завершения загрузки списка файлов или выберите доступный файл из выпадающего списка."
            )
            return

        file_id = file_info.get("file_id")
        mod_id = self.current_mod.get("mod_id")
        game_id = self.current_mod.get("game_id", self.current_nexus_game["game_id"])
        domain = self.current_mod.get("game_domain", self.current_nexus_game["domain"])
        mod_title = self.current_mod.get("title", f"Mod #{mod_id}")
        file_name = file_info.get("name", f"nexus_{domain}_{file_id}.zip")
        size_str = file_info.get("size_human", "—")

        qm = get_download_queue_manager(self.nexus_api)
        task = qm.add_nexus_task(
            mod_id=mod_id,
            file_id=file_id,
            title=mod_title,
            file_name=file_name,
            game_id=game_id,
            game_domain=domain,
            save_dir=self.mods_folder,
            size_human=size_str,
        )

        self.status_label.setText(f"✓ Мод '{mod_title}' добавлен в очередь загрузок (#{task.task_id})")

        # Кратковременная обратная связь на кнопке без блокировки интерфейса
        self.download_button.setText(f"✓ Добавлен в очередь (#{task.task_id})")
        QTimer.singleShot(1400, lambda: self.download_button.setText("⚡ Скачать выбранный мод с Nexus Mods"))

    def download_steam_mod(self):
        """Добавление мода Steam Workshop в очередь скачивания."""
        if not self.steamcmd_path or not os.path.isfile(self.steamcmd_path):
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Исполняемый файл SteamCMD не найден:\n{self.steamcmd_path}\n"
                "Пожалуйста, укажите верный путь к steamcmd.exe."
            )
            return

        mod_title = self.current_mod.get("title", "Мод")
        mod_id = self.current_mod.get("id")

        qm = get_download_queue_manager(self.nexus_api)
        task = qm.add_steam_task(
            appid=self.current_appid,
            mod_id=mod_id,
            title=mod_title,
            save_dir=self.mods_folder,
            steamcmd_path=self.steamcmd_path,
        )

        self.status_label.setText(f"✓ Мод '{mod_title}' добавлен в очередь загрузок (#{task.task_id})")

        # Кратковременная обратная связь на кнопке без блокировки интерфейса
        self.download_button.setText(f"✓ Добавлен в очередь (#{task.task_id})")
        QTimer.singleShot(1400, lambda: self.download_button.setText("Скачать выбранный мод через SteamCMD"))

    # ==========================================
    # ВЫБОР ПУТЕЙ И СОХРАНЕНИЕ
    # ==========================================

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
        if self.download_worker:
            self.download_worker.cancel()
        if self.nexus_download_worker:
            self.nexus_download_worker.cancel()
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.quit()
            self.download_thread.wait(1000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SteamSearchUI()
    window.show()
    sys.exit(app.exec())
