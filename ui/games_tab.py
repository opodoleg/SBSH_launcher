import os
import sys
import webbrowser

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Импорт логики RuTracker и qBittorrent
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from search import (
    CATEGORIES,
    DEFAULT_SAVE_PATH,
    RuTrackerParser,
    add_to_qbittorrent,
)
from modules.favorites import favorites_manager


class RuTrackerSearchWorker(QThread):
    """Фоновый поток для поиска раздач на RuTracker без зависания интерфейса."""
    results_ready = pyqtSignal(int, list)
    error_occurred = pyqtSignal(int, str)

    def __init__(self, req_id: int, parser: RuTrackerParser, query: str, forum_ids: str | None):
        super().__init__()
        self.req_id = req_id
        self.parser = parser
        self.query = query
        self.forum_ids = forum_ids

    def run(self):
        try:
            results = self.parser.search_topics(self.query, forum_ids=self.forum_ids)
            self.results_ready.emit(self.req_id, results)
        except Exception as e:
            self.error_occurred.emit(self.req_id, str(e))


class RuTrackerDownloadWorker(QThread):
    """Фоновый поток для скачивания торрента и мгновенной передачи в qBittorrent."""
    download_finished = pyqtSignal(bool, str, str)

    def __init__(self, parser: RuTrackerParser, item: dict, save_path: str):
        super().__init__()
        self.parser = parser
        self.item = item
        self.save_path = save_path

    def run(self):
        try:
            topic_id = self.item.get("id") or self.item.get("url")
            title = self.item.get("title", "Game")
            filepath = self.parser.download_torrent(topic_id, default_title=title)

            if not filepath or not os.path.isfile(filepath):
                self.download_finished.emit(
                    False,
                    "Не удалось скачать .torrent файл (проверьте сессию/куки RuTracker).",
                    ""
                )
                return

            # Добавляем в qBittorrent без показа диалоговых окон
            success = add_to_qbittorrent(filepath, save_path=self.save_path)
            if success:
                self.download_finished.emit(
                    True,
                    f"Раздача успешно добавлена в qBittorrent!\nПапка: {self.save_path}",
                    filepath
                )
            else:
                self.download_finished.emit(
                    False,
                    f"Торрент-файл сохранён ({os.path.basename(filepath)}), но не удалось отправить в qBittorrent.",
                    filepath
                )
        except Exception as e:
            self.download_finished.emit(False, f"Ошибка скачивания: {e}", "")


class GamesTabWidget(QWidget):
    """Вкладка поиска и скачивания игр через RuTracker и qBittorrent."""
    torrent_added = pyqtSignal()      # Сигнал для перехода в монитор загрузок
    favorites_changed = pyqtSignal()  # Сигнал при изменении избранного

    def __init__(self, get_save_path_callback=None):
        super().__init__()
        self.get_save_path_callback = get_save_path_callback
        self.parser = RuTrackerParser()

        self.current_results = []
        self.selected_item = None
        self.search_id = 0

        self.search_worker = None
        self.download_worker = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- Панель поиска ---
        search_bar = QHBoxLayout()
        search_bar.setSpacing(10)

        # Выбор категории
        self.category_combo = QComboBox()
        self.category_combo.setMinimumWidth(240)
        for key, cat in CATEGORIES.items():
            self.category_combo.addItem(cat["name"], userData=cat["ids"])
        # По умолчанию выбираем категорию "Все игры для ПК"
        self.category_combo.setCurrentIndex(1)

        # Поле поискового запроса
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Введите название игры (например: Cyberpunk, Witcher, S.T.A.L.K.E.R.)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.returnPressed.connect(self.start_search)

        # Кнопка поиска
        self.search_button = QPushButton("🔍 Найти игру")
        self.search_button.setMinimumWidth(120)
        self.search_button.clicked.connect(self.start_search)

        search_bar.addWidget(self.category_combo)
        search_bar.addWidget(self.search_input, stretch=1)
        search_bar.addWidget(self.search_button)
        layout.addLayout(search_bar)

        # --- Таблица результатов ---
        self.results_table = QTableWidget()
        self.results_table.setColumnCount(3)
        self.results_table.setHorizontalHeaderLabels(["Раздел", "Название раздачи", "Ссылка"])
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.results_table.itemSelectionChanged.connect(self.on_selection_changed)
        self.results_table.itemDoubleClicked.connect(self.open_in_browser)
        layout.addWidget(self.results_table)

        # --- Панель действий с выбранной игрой ---
        action_bar = QHBoxLayout()
        action_bar.setSpacing(12)

        self.info_label = QLabel("<i>Выберите игру из списка для скачивания.</i>")
        self.info_label.setTextFormat(Qt.TextFormat.RichText)

        self.browser_button = QPushButton("🌐 Открыть на RuTracker")
        self.browser_button.setObjectName("secondaryBtn")
        self.browser_button.setEnabled(False)
        self.browser_button.clicked.connect(self.open_in_browser)

        self.fav_button = QPushButton("⭐ В избранное")
        self.fav_button.setObjectName("secondaryBtn")
        self.fav_button.setEnabled(False)
        self.fav_button.clicked.connect(self.toggle_favorite)

        self.download_button = QPushButton("📥 Скачать в qBittorrent")
        self.download_button.setObjectName("accentBtn")
        self.download_button.setFixedHeight(38)
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self.start_download)

        action_bar.addWidget(self.info_label, stretch=1)
        action_bar.addWidget(self.fav_button)
        action_bar.addWidget(self.browser_button)
        action_bar.addWidget(self.download_button)
        layout.addLayout(action_bar)

        # --- Статус-бар вкладки ---
        self.status_label = QLabel("Готов к поиску")
        self.status_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(self.status_label)

    def get_save_path(self):
        if self.get_save_path_callback:
            return self.get_save_path_callback()
        return DEFAULT_SAVE_PATH

    # ---------------- ПОИСК ----------------

    def start_search(self):
        query = self.search_input.text().strip()
        if not query:
            self.status_label.setText("Введите поисковый запрос.")
            return

        forum_ids = self.category_combo.currentData()
        cat_name = self.category_combo.currentText()

        self.search_id += 1
        self.search_button.setEnabled(False)
        self.status_label.setText(f"Ищу '{query}' в категории '{cat_name}'...")
        self.results_table.setRowCount(0)
        self.current_results.clear()
        self.selected_item = None
        self._update_action_state()

        self.search_worker = RuTrackerSearchWorker(
            self.search_id,
            self.parser,
            query,
            forum_ids
        )
        self.search_worker.results_ready.connect(self.on_search_completed)
        self.search_worker.error_occurred.connect(self.on_search_error)
        self.search_worker.start()

    def on_search_completed(self, req_id, results):
        if req_id != self.search_id:
            return

        self.search_button.setEnabled(True)
        self.current_results = results
        self.results_table.setRowCount(len(results))

        for row, item in enumerate(results):
            forum_item = QTableWidgetItem(f"[{item.get('forum', 'Игры')}]")
            forum_item.setForeground(Qt.GlobalColor.cyan)

            title_item = QTableWidgetItem(item.get("title", "Без названия"))
            link_item = QTableWidgetItem("🔗 RuTracker")
            link_item.setForeground(Qt.GlobalColor.gray)

            self.results_table.setItem(row, 0, forum_item)
            self.results_table.setItem(row, 1, title_item)
            self.results_table.setItem(row, 2, link_item)

        if results:
            self.status_label.setText(f"Найдено раздач: {len(results)}")
        else:
            self.status_label.setText("Ничего не найдено. Попробуйте изменить запрос или категорию.")

    def on_search_error(self, req_id, error_message):
        if req_id != self.search_id:
            return
        self.search_button.setEnabled(True)
        self.status_label.setText(f"Ошибка поиска: {error_message}")
        QMessageBox.warning(self, "Ошибка поиска", f"Не удалось выполнить поиск:\n{error_message}")

    # ---------------- ВЫБОР И ДЕЙСТВИЯ ----------------

    def on_selection_changed(self):
        selected_rows = self.results_table.selectionModel().selectedRows()
        if not selected_rows:
            self.selected_item = None
            self._update_action_state()
            return

        row = selected_rows[0].row()
        if 0 <= row < len(self.current_results):
            self.selected_item = self.current_results[row]
            self._update_action_state()

    def _update_action_state(self):
        has_sel = self.selected_item is not None
        self.browser_button.setEnabled(has_sel)
        self.download_button.setEnabled(has_sel)
        self.fav_button.setEnabled(has_sel)

        if has_sel:
            title = self.selected_item.get("title", "")
            forum = self.selected_item.get("forum", "")
            self.info_label.setText(
                f"<b>Выбрано:</b> [{forum}] {title}"
            )
            if favorites_manager.is_favorite(name=title):
                self.fav_button.setText("★ В избранном")
                self.fav_button.setStyleSheet("color: #eab308; font-weight: bold;")
            else:
                self.fav_button.setText("⭐ В избранное")
                self.fav_button.setStyleSheet("")
        else:
            self.info_label.setText("<i>Выберите игру из списка для скачивания.</i>")
            self.fav_button.setText("⭐ В избранное")
            self.fav_button.setStyleSheet("")

    def toggle_favorite(self):
        if not self.selected_item:
            return
        title = self.selected_item.get("title", "")
        favorites_manager.toggle(name=title)
        self._update_action_state()
        self.favorites_changed.emit()

    def set_search_query(self, query: str):
        """Устанавливает запрос в строку поиска и автоматически запускает поиск."""
        self.search_input.setText(query)
        self.start_search()

    def open_in_browser(self):
        if not self.selected_item or not self.selected_item.get("url"):
            return
        webbrowser.open(self.selected_item["url"])

    # ---------------- СКАЧИВАНИЕ В QBITTORRENT ----------------

    def start_download(self):
        if not self.selected_item:
            return

        save_path = self.get_save_path()
        self.download_button.setEnabled(False)
        self.download_button.setText("Загрузка...")
        self.status_label.setText(f"Скачиваю торрент: {self.selected_item.get('title')}...")

        self.download_worker = RuTrackerDownloadWorker(
            self.parser,
            self.selected_item,
            save_path
        )
        self.download_worker.download_finished.connect(self.on_download_finished)
        self.download_worker.start()

    def on_download_finished(self, success, message, filepath):
        self.download_button.setEnabled(True)
        self.download_button.setText("📥 Скачать в qBittorrent")

        if success:
            self.status_label.setText("Загрузка запущена в qBittorrent!")
            QMessageBox.information(
                self,
                "Успех",
                f"Торрент успешно запущен в qBittorrent!\n\nПапка загрузки: {self.get_save_path()}\nФайл: {os.path.basename(filepath)}"
            )
            self.torrent_added.emit()
        else:
            self.status_label.setText("Ошибка добавления в qBittorrent")
            QMessageBox.warning(self, "Внимание", message)
