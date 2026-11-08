import os
import shutil
import sys
import webbrowser
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCursor, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Modl")))
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
    """Поток для загрузки файлов с Nexus Mods с оповещением о прогрессе."""
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
    """Диалог записи новой игры в список сохраненных игр."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Записать игру в список")
        self.resize(460, 220)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Например: Project Zomboid, Teardown...")

        appid_row = QHBoxLayout()
        self.appid_input = QLineEdit()
        self.appid_input.setPlaceholderText("Числовой AppID (например: 108600)")

        self.find_appid_btn = QPushButton("🔎 Найти AppID")
        self.find_appid_btn.setObjectName("secondaryBtn")
        self.find_appid_btn.clicked.connect(self.find_appid)

        appid_row.addWidget(self.appid_input, stretch=1)
        appid_row.addWidget(self.find_appid_btn)

        form.addRow("Название игры:", self.name_input)
        form.addRow("Steam AppID:", appid_row)
        layout.addLayout(form)

        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("color: #94a3b8; font-size: 11px;")
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
            QMessageBox.warning(self, "Ошибка", "Укажите название и корректный числовой Steam AppID.")
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
        # Не используем общий curl_cffi session для потокобезопасности

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


NEXUS_PRESET_GAMES = [
    {"name": "Cyberpunk 2077", "domain": "cyberpunk2077", "game_id": 3333},
    {"name": "The Witcher 3", "domain": "witcher3", "game_id": 952},
    {"name": "Skyrim Special Edition", "domain": "skyrimspecialedition", "game_id": 1704},
    {"name": "Fallout 4", "domain": "fallout4", "game_id": 1151},
    {"name": "Starfield", "domain": "starfield", "game_id": 4187},
    {"name": "Baldur's Gate 3", "domain": "baldursgate3", "game_id": 3474},
]


class ModsTabWidget(QWidget):
    """Вкладка скачивания модов из Steam Workshop и Nexus Mods."""

    def __init__(self, get_steamcmd_path_callback=None, get_mods_folder_callback=None):
        super().__init__()
        self.get_steamcmd_path_callback = get_steamcmd_path_callback
        self.get_mods_folder_callback = get_mods_folder_callback

        self.source_mode = "steam"

        # Состояние Steam
        self.current_game = None
        self.current_appid = None
        self.current_mod = None
        self.last_text = ""
        self.mod_query = ""

        # Состояние Nexus Mods
        self.nexus_api = NexusAPI()
        self.current_nexus_game = NEXUS_PRESET_GAMES[0]
        self.current_nexus_category = "all"
        self.current_nexus_files = []
        self.selected_nexus_file = None
        self.nexus_download_worker = None

        # Детали и скриншоты выбранного мода
        self.current_details = None
        self.current_screenshots = []
        self.current_thumbnails = []
        self.current_screenshot_titles = []
        self.screenshot_cards = []
        self._active_workers = set()
        self._active_image_workers = set()

        # Поколения / идентификаторы запросов для подавления устаревших ответов
        self._request_id = 0
        self._mods_req_id = 0
        self._details_req_id = 0

        self.details_search_id = 0
        self.details_worker = None

        self.game_search_id = 0
        self.mod_search_id = 0
        self.files_search_id = 0

        self.game_worker = None
        self.mod_worker = None
        self.files_worker = None
        self.download_worker = None
        self.download_thread = None

        self._setup_timers()
        self._setup_ui()
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
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- Переключатель источника (Steam / Nexus Mods) ---
        source_bar = QHBoxLayout()
        source_bar.setSpacing(10)

        source_label = QLabel("<b>Источник модов:</b>")
        source_label.setStyleSheet("color: #e2e8f0; font-size: 13px;")

        self.source_combo = QComboBox()
        self.source_combo.addItem("🌐 Steam Workshop (SteamCMD)", "steam")
        self.source_combo.addItem("⚡ Nexus Mods (Прямое скачивание)", "nexus")
        self.source_combo.currentIndexChanged.connect(self.on_source_changed)

        source_bar.addWidget(source_label)
        source_bar.addWidget(self.source_combo, stretch=1)
        source_bar.addStretch(2)
        layout.addLayout(source_bar)

        # --- Основной сплиттер (3 колонки: Игры | Список модов | Описание и скриншоты) ---
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # ==========================================
        # ЛЕВАЯ ПАНЕЛЬ: ИГРЫ
        # ==========================================
        self.games_tab_widget = QTabWidget()
        self.games_tab_widget.currentChanged.connect(self.on_games_tab_changed)

        # 1. Сохранённые игры (Steam)
        saved_widget = QWidget()
        saved_layout = QVBoxLayout(saved_widget)
        saved_layout.setContentsMargins(0, 8, 0, 0)
        saved_layout.setSpacing(8)

        self.saved_games_list = QListWidget()
        self.saved_games_list.itemClicked.connect(self.on_saved_game_selected)

        saved_buttons_row = QHBoxLayout()
        saved_buttons_row.setSpacing(8)

        self.add_saved_btn = QPushButton("➕ Записать игру")
        self.add_saved_btn.setObjectName("accentBtn")
        self.add_saved_btn.setToolTip("Добавить новую игру в список сохранённых")
        self.add_saved_btn.clicked.connect(self.open_add_game_dialog)

        self.remove_saved_btn = QPushButton("🗑 Удалить")
        self.remove_saved_btn.setObjectName("secondaryBtn")
        self.remove_saved_btn.setToolTip("Удалить выбранную игру из списка")
        self.remove_saved_btn.clicked.connect(self.remove_selected_saved_game)

        saved_buttons_row.addWidget(self.add_saved_btn)
        saved_buttons_row.addWidget(self.remove_saved_btn)

        saved_layout.addWidget(self.saved_games_list)
        saved_layout.addLayout(saved_buttons_row)

        # 2. Поиск игр в Steam
        search_widget = QWidget()
        search_layout = QVBoxLayout(search_widget)
        search_layout.setContentsMargins(0, 8, 0, 0)
        search_layout.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск новой игры в Steam (от 2 символов)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self.on_game_text_changed)

        self.games_list = QListWidget()
        self.games_list.itemClicked.connect(self.on_search_game_selected)

        self.save_from_search_btn = QPushButton("⭐ Записать выбранную игру в список")
        self.save_from_search_btn.setObjectName("secondaryBtn")
        self.save_from_search_btn.setEnabled(False)
        self.save_from_search_btn.clicked.connect(self.save_selected_search_game)

        search_layout.addWidget(self.search_input)
        search_layout.addWidget(self.games_list)
        search_layout.addWidget(self.save_from_search_btn)

        # 3. Игры Nexus Mods
        nexus_widget = QWidget()
        nexus_layout = QVBoxLayout(nexus_widget)
        nexus_layout.setContentsMargins(0, 8, 0, 0)
        nexus_layout.setSpacing(8)

        self.nexus_games_list = QListWidget()
        self.nexus_games_list.itemClicked.connect(self.on_nexus_game_selected)
        nexus_layout.addWidget(self.nexus_games_list)

        self.games_tab_widget.addTab(saved_widget, "⭐ Сохранённые игры")
        self.games_tab_widget.addTab(search_widget, "🔍 Поиск в Steam")
        self.games_tab_widget.addTab(nexus_widget, "⚡ Игры Nexus")

        self.main_splitter.addWidget(self.games_tab_widget)

        # ==========================================
        # СРЕДНЯЯ ПАНЕЛЬ: СПИСОК МОДОВ
        # ==========================================
        mods_container = QWidget()
        mods_layout = QVBoxLayout(mods_container)
        mods_layout.setContentsMargins(0, 0, 0, 0)
        mods_layout.setSpacing(8)

        self.active_game_label = QLabel("<b>Моды мастерской Steam (Workshop):</b>")
        self.active_game_label.setTextFormat(Qt.TextFormat.RichText)

        # Выбор категории Nexus Mods (виден только в режиме Nexus)
        self.nexus_category_row = QHBoxLayout()
        self.nexus_category_lbl = QLabel("<b>Категория:</b>")
        self.nexus_category_lbl.setStyleSheet("color: #94a3b8;")
        self.nexus_category_combo = QComboBox()
        self.nexus_category_combo.currentIndexChanged.connect(self.on_nexus_category_changed)
        self.nexus_category_row.addWidget(self.nexus_category_lbl)
        self.nexus_category_row.addWidget(self.nexus_category_combo, stretch=1)

        self.nexus_category_container = QWidget()
        self.nexus_category_container.setLayout(self.nexus_category_row)
        self.nexus_category_container.setVisible(False)

        self.mod_search_input = QLineEdit()
        self.mod_search_input.setPlaceholderText("Поиск модов для выбранной игры...")
        self.mod_search_input.setClearButtonEnabled(True)
        self.mod_search_input.textChanged.connect(self.on_mod_text_changed)

        self.mods_list = QListWidget()
        self.mods_list.itemClicked.connect(self.on_mod_selected)
        self.mods_list.itemDoubleClicked.connect(self.open_mod_workshop)

        mods_layout.addWidget(self.active_game_label)
        mods_layout.addWidget(self.nexus_category_container)
        mods_layout.addWidget(self.mod_search_input)
        mods_layout.addWidget(self.mods_list)

        self.main_splitter.addWidget(mods_container)

        # ==========================================
        # ПРАВАЯ ПАНЕЛЬ: ИНСПЕКТОР МОДА (ОПИСАНИЕ, СКРИНШОТЫ, ФАЙЛЫ)
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

        # Сохраняем ссылку для обратной совместимости
        self.info_label = self.details_meta

        details_layout.addWidget(self.details_title)
        details_layout.addWidget(self.details_meta)

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

        # Блок выбора файлов Nexus Mods
        self.nexus_files_row = QHBoxLayout()
        self.nexus_files_lbl = QLabel("<b>Файл для скачивания:</b>")
        self.nexus_files_lbl.setStyleSheet("color: #94a3b8;")
        self.nexus_files_combo = QComboBox()
        self.nexus_files_combo.currentIndexChanged.connect(self.on_nexus_file_selected)
        self.nexus_files_row.addWidget(self.nexus_files_lbl)
        self.nexus_files_row.addWidget(self.nexus_files_combo, stretch=1)

        self.nexus_files_container = QWidget()
        self.nexus_files_container.setLayout(self.nexus_files_row)
        self.nexus_files_container.setVisible(False)
        details_layout.addWidget(self.nexus_files_container)

        # Кнопка скачивания и ссылка на страницу
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)

        self.browser_button = QPushButton("🌐 Открыть в Workshop")
        self.browser_button.setObjectName("secondaryBtn")
        self.browser_button.setEnabled(False)
        self.browser_button.clicked.connect(self.open_mod_workshop)

        self.download_button = QPushButton("⚡ Скачать выбранный мод через SteamCMD")
        self.download_button.setObjectName("accentBtn")
        self.download_button.setFixedHeight(36)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.download_selected_mod)

        actions_row.addWidget(self.browser_button)
        actions_row.addWidget(self.download_button, stretch=1)
        details_layout.addLayout(actions_row)

        self.main_splitter.addWidget(self.details_container)
        self.main_splitter.setSizes([240, 360, 520])

        layout.addWidget(self.main_splitter, stretch=1)

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

        # Дебаунс-таймеры для предотвращения спама запросами при быстром переключении
        self.game_debounce_timer = QTimer(self)
        self.game_debounce_timer.setSingleShot(True)
        self.game_debounce_timer.timeout.connect(self._on_game_debounced)

        self.mod_debounce_timer = QTimer(self)
        self.mod_debounce_timer.setSingleShot(True)
        self.mod_debounce_timer.timeout.connect(self._on_mod_debounced)

    def _on_game_debounced(self):
        """Отправляет сетевой запрос за списком модов только когда пользователь остановился на игре/категории."""
        if self.source_mode == "nexus":
            self.load_nexus_mods_for_current_game()
        else:
            if self.current_appid:
                self.load_workshop_mods_for_current_game()

    def _on_mod_debounced(self):
        """Отправляет сетевой запрос за файлами и описанием только когда пользователь остановился на моде."""
        if self.source_mode == "nexus" and self.current_mod:
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

    def get_steamcmd_path(self):
        if self.get_steamcmd_path_callback:
            return self.get_steamcmd_path_callback()
        return r"G:\SteamCMD\steamcmd.exe"

    def get_mods_folder(self):
        if self.get_mods_folder_callback:
            return self.get_mods_folder_callback()
        return ""

    # ==========================================
    # ПЕРЕКЛЮЧЕНИЕ ИСТОЧНИКА
    # ==========================================

    def on_source_changed(self, index):
        mode = self.source_combo.itemData(index)
        self.source_mode = mode
        self.current_mod = None
        self.mods_list.clear()
        self.mod_search_input.clear()
        self.browser_button.setEnabled(False)
        self.download_button.setEnabled(False)
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
            self.nexus_category_container.setVisible(False)
            self.nexus_files_container.setVisible(False)

            self.browser_button.setText("🌐 Открыть в Workshop")
            self.active_game_label.setText("<b>Моды мастерской Steam (Workshop):</b>")
            self.mod_search_input.setPlaceholderText("Поиск модов для выбранной игры...")
            self.download_button.setText("⚡ Скачать выбранный мод через SteamCMD")
            if self.current_appid:
                self.game_debounce_timer.start(250)
        else:
            self.games_tab_widget.setTabVisible(0, False)
            self.games_tab_widget.setTabVisible(1, False)
            self.games_tab_widget.setTabVisible(2, True)
            self.games_tab_widget.setCurrentIndex(2)
            self.nexus_category_container.setVisible(True)
            self.nexus_files_container.setVisible(True)

            self.browser_button.setText("🌐 Открыть на Nexus Mods")
            game_title = self.current_nexus_game["name"]
            self.active_game_label.setText(f"<b>Моды Nexus Mods ({game_title}):</b>")
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
        self.nexus_games_list.clear()
        for g in NEXUS_PRESET_GAMES:
            item = QListWidgetItem(f"🎮 {g['name']} (ID: {g['game_id']})")
            item.setData(Qt.ItemDataRole.UserRole, g)
            self.nexus_games_list.addItem(item)

        if self.nexus_games_list.count() > 0:
            self.nexus_games_list.setCurrentRow(0)

        self.games_tab_widget.setTabVisible(2, False)

    def on_nexus_game_selected(self, item):
        game = item.data(Qt.ItemDataRole.UserRole)
        if not game:
            return
        self.current_nexus_game = game
        self.current_mod = None
        self.browser_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self._update_selected_mod_info()
        self.active_game_label.setText(f"<b>Моды Nexus Mods ({game['name']}):</b>")
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
        self.browser_button.setEnabled(False)
        self.download_button.setEnabled(False)

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
    # ПОИСК В МАГАЗИНЕ STEAM
    # ==========================================

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
        self.browser_button.setEnabled(False)
        self.download_button.setEnabled(False)

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
        self.active_game_label.setText(f"<b>Моды для {game_name} (AppID: {self.current_appid}):</b>")
        self.status_label.setText(f"Загрузка популярных модов для {game_name}...")

        self.mod_worker = AsyncWorker(req_id, get_workshop_mods, self.current_appid)
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

    def on_mod_text_changed(self, text):
        self.mod_query = text
        self.mod_timer.start(350)

    def do_search_mods(self):
        query = self.mod_query.strip()
        self._request_id += 1
        self._mods_req_id = self._request_id
        req_id = self._mods_req_id

        if self.source_mode == "nexus":
            game_info = self.current_nexus_game
            category = self.current_nexus_category
            cat_disp = self.nexus_category_combo.currentText() if hasattr(self, "nexus_category_combo") and self.nexus_category_combo.count() > 0 else "Все"
            desc = f"'{query}'" if query else f"топ популярных ({cat_disp})"
            self.status_label.setText(f"Nexus Mods: поиск {desc}...")
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
            self.mod_worker = AsyncWorker(req_id, get_workshop_mods, self.current_appid)
        else:
            self.status_label.setText(f"Поиск модов в Steam: '{query}'...")
            self.mod_worker = AsyncWorker(req_id, search_workshop, self.current_appid, query)

        self.mod_worker.result_ready.connect(self.on_mods_loaded)
        self.mod_worker.error_occurred.connect(self.on_network_error)
        self._register_worker(self.mod_worker)
        self.mod_worker.start()

    def on_mods_loaded(self, req_id, mods):
        if req_id != self._mods_req_id:
            return

        self.mods_list.clear()
        for mod in mods:
            item = QListWidgetItem(mod["title"])
            item.setData(Qt.ItemDataRole.UserRole, mod)
            self.mods_list.addItem(item)

        self.status_label.setText(f"Загружено модов: {len(mods)}")

    def on_nexus_mods_loaded(self, req_id, mods):
        if req_id != self._mods_req_id:
            return

        self.mods_list.clear()
        for mod in mods:
            dl_cnt = mod.get("downloads", 0)
            dl_str = f"📥 {dl_cnt:,}" if dl_cnt else ""
            cat = mod.get("category", "")
            cat_str = f"[{cat}] " if cat and cat != "Nexus" else ""
            author_str = f"👤 {mod.get('author')}" if mod.get("author") else ""

            parts = [p for p in [cat_str + mod['title'], dl_str, author_str] if p]
            item_text = "  |  ".join(parts) if len(parts) > 1 else parts[0]

            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, mod)
            self.mods_list.addItem(item)

        self.status_label.setText(f"Nexus Mods: найдено {len(mods)} модов")

    def on_mod_selected(self, item):
        self.current_mod = item.data(Qt.ItemDataRole.UserRole)
        self._update_selected_mod_info()

        if self.source_mode == "nexus" and self.current_mod:
            self._request_id += 1
            self._details_req_id = self._request_id
            self.mod_debounce_timer.start(250)

    def load_nexus_mod_files(self, mod_id: int, req_id: Optional[int] = None):
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
            if self.current_mod:
                title = self.current_mod.get("title", "Без названия")
                author = self.current_mod.get("author", "Неизвестен")
                category = self.current_mod.get("category", "")
                cat_badge = f" &nbsp;|&nbsp; 🏷️ <b>Категория:</b> {category}" if category and category != "Nexus" else ""
                mod_id = self.current_mod.get("mod_id", "N/A")
                dl = self.current_mod.get("downloads", 0)
                dl_badge = f" &nbsp;|&nbsp; 📥 <b>Скачиваний:</b> {dl:,}" if dl else ""

                self.details_title.setText(f"<b>{title}</b>")
                self.details_meta.setText(f"👤 <b>Автор:</b> {author}{cat_badge}{dl_badge} &nbsp;|&nbsp; <b>ID:</b> {mod_id}")
                self.description_box.setVisible(True)
                self.download_button.setEnabled(True)
                self.browser_button.setEnabled(True)
            else:
                self.details_title.setText("<i>Выберите мод из списка</i>")
                self.details_meta.setText("Выберите игру слева и мод в центре для просмотра описания и скриншотов.")
                self.description_box.setVisible(False)
                self.screenshots_box.setVisible(False)
                self.download_button.setEnabled(False)
                self.browser_button.setEnabled(False)
                self.clear_screenshots()
            return

        # Режим Steam
        if self.current_mod:
            title = self.current_mod.get("title", "Без названия")
            mod_id = self.current_mod.get("id", "N/A")
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
        else:
            self.details_title.setText("<i>Выберите мод из списка</i>")
            self.details_meta.setText("Выберите игру и мод для просмотра информации.")
            self.description_box.setVisible(False)
            self.screenshots_box.setVisible(False)
            self.download_button.setEnabled(False)

    def select_game(self, game_name: str, appid: int):
        if appid and appid > 0:
            game = {"name": game_name, "appid": appid}
            self.current_game = game
            self.current_appid = appid
            self.current_mod = None
            self.browser_button.setEnabled(False)
            self.download_button.setEnabled(False)
            self._update_selected_mod_info()
            self.games_tab_widget.setCurrentIndex(0)
            self.load_workshop_mods_for_current_game()
        else:
            self.games_tab_widget.setCurrentIndex(1)
            self.search_input.setText(game_name)
            self.do_search_games()

    def open_mod_workshop(self, item=None):
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
            webbrowser.open(f"https://steamcommunity.com/sharedfiles/filedetails/?id={mod['id']}")

    def on_network_error(self, req_id, error_message):
        self.status_label.setText(f"Ошибка сети: {error_message}")

    # ==========================================
    # СКАЧИВАНИЕ МОДА
    # ==========================================

    def download_selected_mod(self):
        if not self.current_mod:
            QMessageBox.warning(self, "Предупреждение", "Пожалуйста, выберите мод из списка.")
            return

        mods_folder = self.get_mods_folder()
        if not mods_folder or not os.path.isdir(mods_folder):
            reply = QMessageBox.question(
                self,
                "Папка для модов не указана",
                "Папка для сохранения модов ещё не указана в настройках. Выбрать папку сейчас?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                chosen = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения модов")
                if chosen:
                    self.get_mods_folder_callback = lambda: chosen
                    mods_folder = chosen
                else:
                    return
            else:
                return

        if self.source_mode == "nexus":
            self.download_nexus_mod(mods_folder)
            return

        self.download_steam_mod(mods_folder)

    def download_nexus_mod(self, mods_folder: str):
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
            save_dir=mods_folder,
            size_human=size_str,
        )

        self.status_label.setText(f"✓ Мод '{mod_title}' добавлен в очередь загрузок (#{task.task_id})")

        # Кратковременная визуальная обратная связь на кнопке без блокировки интерфейса
        self.download_button.setText(f"✓ Добавлен в очередь (#{task.task_id})")
        QTimer.singleShot(1400, lambda: self.download_button.setText("⚡ Скачать выбранный мод с Nexus Mods"))

    def download_steam_mod(self, mods_folder: str):
        steamcmd_path = self.get_steamcmd_path()
        if not steamcmd_path or not os.path.isfile(steamcmd_path):
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Исполняемый файл SteamCMD не найден:\n'{steamcmd_path}'\n"
                "Укажите верный путь во вкладке 'Настройки'.",
            )
            return

        mod_title = self.current_mod.get("title", "Мод")
        mod_id = self.current_mod.get("id")

        qm = get_download_queue_manager(self.nexus_api)
        task = qm.add_steam_task(
            appid=self.current_appid,
            mod_id=mod_id,
            title=mod_title,
            save_dir=mods_folder,
            steamcmd_path=steamcmd_path,
        )

        self.status_label.setText(f"✓ Мод '{mod_title}' добавлен в очередь загрузок (#{task.task_id})")

        # Кратковременная визуальная обратная связь на кнопке без блокировки интерфейса
        self.download_button.setText(f"✓ Добавлен в очередь (#{task.task_id})")
        QTimer.singleShot(1400, lambda: self.download_button.setText("⚡ Скачать выбранный мод через SteamCMD"))

    def stop_workers(self):
        qm = get_download_queue_manager()
        qm.stop()
