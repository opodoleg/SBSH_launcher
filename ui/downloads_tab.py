import os
import subprocess
import sys
import threading
import webbrowser

from PyQt6.QtCore import QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import requests
from modules.download_queue import (
    DownloadTask,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_DOWNLOADING,
    STATE_ERROR,
    STATE_GENERATING_URL,
    STATE_QUEUED,
    get_download_queue_manager,
)
from modules.installed_mods import installed_mods_manager


def format_bytes(size_bytes: int) -> str:
    """Форматирует байты в удобные КБ, МБ, ГБ."""
    if not size_bytes or size_bytes <= 0:
        return "0 Б"
    for unit in ["Б", "КБ", "МБ", "ГБ", "ТБ"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} ПБ"


def format_speed(speed_bytes_sec: int) -> str:
    """Форматирует скорость в КБ/с или МБ/с."""
    if not speed_bytes_sec or speed_bytes_sec <= 0:
        return "—"
    return f"{format_bytes(speed_bytes_sec)}/с"


STATE_TRANSLATIONS = {
    "downloading": "Загружается",
    "stalledDL": "Ожидание источников",
    "uploading": "Раздаётся",
    "stalledUP": "Ожидание раздачи",
    "pausedDL": "Пауза",
    "pausedUP": "Завершено (Пауза)",
    "stoppedDL": "Остановлен",
    "stoppedUP": "Завершено",
    "queuedDL": "В очереди",
    "queuedUP": "В очереди раздачи",
    "checkingDL": "Проверка файлов...",
    "checkingUP": "Проверка файлов...",
    "error": "Ошибка",
    "missingFiles": "Файлы отсутствуют",
}

STATUS_COLORS = {
    STATE_QUEUED: "#f59e0b",          # янтарный
    STATE_GENERATING_URL: "#38bdf8",  # небесный
    STATE_DOWNLOADING: "#60a5fa",     # ярко-синий
    STATE_COMPLETED: "#22c55e",       # изумрудный
    STATE_ERROR: "#ef4444",           # красный
    STATE_CANCELLED: "#64748b",       # приглушенный серый
}


class QBTFetchWorker(QThread):
    torrents_fetched = pyqtSignal(list)
    fetch_failed = pyqtSignal(str)

    def __init__(self, qb_url: str):
        super().__init__()
        self.qb_url = qb_url

    def run(self):
        try:
            url = f"{self.qb_url.rstrip('/')}/api/v2/torrents/info"
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                self.torrents_fetched.emit(r.json())
            else:
                self.fetch_failed.emit(f"HTTP статус: {r.status_code}")
        except Exception as e:
            self.fetch_failed.emit(str(e))



class DuplicateCleanupReportDialog(QDialog):
    """Диалоговое окно отчёта об обнаруженных и удалённых дубликатах модов."""

    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.report = report
        self.setWindowTitle(
            f"Отчёт об очистке дубликатов модов [{'Предпросмотр' if report.is_dry_run else 'Завершено'}]"
        )
        self.resize(940, 620)
        self.setMinimumSize(720, 460)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b1120;
                color: #f8fafc;
            }
            QLabel {
                color: #e2e8f0;
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
            QTableWidget {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 6px;
                gridline-color: #1e293b;
                color: #f8fafc;
            }
            QHeaderView::section {
                background-color: #1e293b;
                color: #94a3b8;
                padding: 6px 8px;
                border: none;
                font-weight: bold;
            }
            QLineEdit {
                background-color: #0f172a;
                border: 1px solid #334155;
                border-radius: 6px;
                color: #f8fafc;
                padding: 6px 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # 1. Верхний заголовок и карточки со статистикой
        header_row = QHBoxLayout()
        title_lbl = QLabel(
            "🧹 <b>Результаты очистки дубликатов модов</b>"
            if not report.is_dry_run else
            "🔍 <b>Предпросмотр очистки дубликатов модов (Dry-Run)</b>"
        )
        title_lbl.setStyleSheet("font-size: 16px; color: #38bdf8;")
        header_row.addWidget(title_lbl)
        header_row.addStretch(1)

        time_lbl = QLabel(f"📅 {report.timestamp}")
        time_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
        header_row.addWidget(time_lbl)
        layout.addLayout(header_row)

        # Карточки сводки
        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(10)

        def _make_badge(label: str, val: str, bg: str, fg: str):
            w = QWidget()
            w.setStyleSheet(f"background-color: {bg}; border-radius: 8px; border: 1px solid #334155;")
            vl = QVBoxLayout(w)
            vl.setContentsMargins(12, 8, 12, 8)
            vl.setSpacing(2)
            t_lbl = QLabel(label)
            t_lbl.setStyleSheet("color: #94a3b8; font-size: 11px;")
            v_lbl = QLabel(val)
            v_lbl.setStyleSheet(f"color: {fg}; font-size: 16px; font-weight: bold;")
            vl.addWidget(t_lbl)
            vl.addWidget(v_lbl)
            return w

        cards_layout.addWidget(_make_badge("Проверено файлов", str(report.total_scanned_files), "#1e293b", "#38bdf8"))
        cards_layout.addWidget(_make_badge("Групп дубликатов", str(report.total_duplicate_groups), "#1e293b", "#f59e0b"))
        count_lbl = "Удалено копий" if not report.is_dry_run else "К удалению"
        count_val = str(report.total_duplicates_removed if not report.is_dry_run else report.total_duplicates_found)
        cards_layout.addWidget(_make_badge(count_lbl, count_val, "#1e293b", "#ef4444" if not report.is_dry_run else "#f59e0b"))
        freed_lbl = "Освобождено" if not report.is_dry_run else "Будет освобождено"
        cards_layout.addWidget(_make_badge(freed_lbl, report.freed_human, "#064e3b", "#34d399"))
        layout.addLayout(cards_layout)

        # 2. Поле фильтрации
        filter_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔎 Поиск в отчёте по имени файла или пути...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._filter_table)
        filter_row.addWidget(self.search_input)
        layout.addLayout(filter_row)

        # 3. Таблица дубликатов
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Имя файла", "Размер", "Удалённый дубликат (путь)", "Сохранённый оригинал (путь)", "Причина сохранения"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self._populate_table()
        layout.addWidget(self.table)

        # 4. Нижняя панель действий
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.copy_btn = QPushButton("📋 Скопировать отчёт")
        self.copy_btn.clicked.connect(self._copy_to_clipboard)
        bottom_bar.addWidget(self.copy_btn)

        self.open_log_btn = QPushButton("📄 Открыть лог-файл")
        self.open_log_btn.clicked.connect(self._open_log_file)
        bottom_bar.addWidget(self.open_log_btn)

        bottom_bar.addStretch(1)

        close_btn = QPushButton("✕ Закрыть")
        close_btn.setObjectName("accentBtn")
        close_btn.clicked.connect(self.accept)
        bottom_bar.addWidget(close_btn)

        layout.addLayout(bottom_bar)

    def _populate_table(self):
        query = self.search_input.text().strip().lower()
        self.table.setRowCount(0)

        for item in self.report.removed_files:
            if query:
                in_fn = query in item.filename.lower()
                in_rem = query in item.removed_path.lower()
                in_kept = query in item.kept_path.lower()
                if not (in_fn or in_rem or in_kept):
                    continue

            r = self.table.rowCount()
            self.table.insertRow(r)

            # 0. Имя файла
            fn_item = QTableWidgetItem(f"📄 {item.filename}")
            fn_item.setToolTip(item.filename)
            self.table.setItem(r, 0, fn_item)

            # 1. Размер
            sz_item = QTableWidgetItem(item.size_human)
            sz_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 1, sz_item)

            # 2. Удалённый путь
            rem_item = QTableWidgetItem(item.removed_path)
            rem_item.setForeground(QColor("#f87171"))
            rem_item.setToolTip(f"Удалён: {item.removed_path}\nДата изменения: {item.mtime_removed}")
            self.table.setItem(r, 2, rem_item)

            # 3. Сохранённый путь
            kept_item = QTableWidgetItem(item.kept_path)
            kept_item.setForeground(QColor("#4ade80"))
            kept_item.setToolTip(f"Сохранён: {item.kept_path}\nДата изменения: {item.mtime_kept}")
            self.table.setItem(r, 3, kept_item)

            # 4. Причина
            reason_item = QTableWidgetItem(item.reason)
            reason_item.setForeground(QColor("#94a3b8"))
            self.table.setItem(r, 4, reason_item)

    def _filter_table(self):
        self._populate_table()

    def _copy_to_clipboard(self):
        cb = QApplication.clipboard()
        if cb and self.report.report_text:
            cb.setText(self.report.report_text)
            QMessageBox.information(self, "Скопировано", "Полный отчёт успешно скопирован в буфер обмена!")

    def _open_log_file(self):
        if self.report.log_filepath and os.path.exists(self.report.log_filepath):
            try:
                os.startfile(self.report.log_filepath)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось открыть файл лога: {e}")
        else:
            QMessageBox.information(self, "Информация", "Файл лога пока не создан на диске.")


class DownloadsTabWidget(QWidget):
    """
    Вкладка загрузок лаунчера.
    Содержит две подвкладки:
    1. Очередь загрузки модов (Steam Workshop & Nexus Mods)
    2. Загрузки игр через qBittorrent
    """

    def __init__(self, get_qb_url_callback=None, get_mods_folder_callback=None):
        super().__init__()
        self.get_qb_url_callback = get_qb_url_callback
        self.get_mods_folder_callback = get_mods_folder_callback

        # Состояние qBittorrent
        self.current_torrents = []
        self.selected_hash = None
        self.fetch_worker = None

        # Менеджер очереди загрузки модов
        self.queue_manager = get_download_queue_manager()

        # Состояние установленных модов
        self.installed_mods = []
        self.selected_installed_mod = None

        self._setup_ui()
        self._setup_queue_signals()
        self._setup_installed_signals()
        self._setup_torrents_timer()

        # Первичное заполнение таблиц
        self.refresh_mods_table()
        self.refresh_installed_mods_table()

        # Фоновое автосканирование директорий модов и игры при старте
        self._start_startup_scan()

    def _start_startup_scan(self):
        """Запускает неблокирующее фоновое сканирование настроенных папок."""
        def _scan():
            try:
                st = QSettings("SBSH", "Launcher")
                mods_f = st.value("mods_folder", "") or (self.get_mods_folder_callback() if self.get_mods_folder_callback else "")
                games_f = st.value("games_save_path", "")
                installed_mods_manager.scan_all_locations(mods_dir=mods_f, games_dir=games_f)
            except Exception as e:
                print(f"[DownloadsTabWidget] Ошибка фонового сканирования при старте: {e}")

        threading.Thread(target=_scan, daemon=True, name="StartupModScanner").start()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(8)
        root_layout.setContentsMargins(12, 12, 12, 12)

        self.sub_tabs = QTabWidget()
        self.sub_tabs.setDocumentMode(True)

        # 1. Вкладка "Очередь модов"
        self.mods_tab = QWidget()
        self._setup_mods_ui(self.mods_tab)
        self.sub_tabs.addTab(self.mods_tab, "📦  Очередь загрузок")

        # 2. Вкладка "Установленные моды"
        self.installed_tab = QWidget()
        self._setup_installed_mods_ui(self.installed_tab)
        self.sub_tabs.addTab(self.installed_tab, "💾  Установленные моды")

        # 3. Вкладка "Торренты игр"
        self.torrents_tab = QWidget()
        self._setup_torrents_ui(self.torrents_tab)
        self.sub_tabs.addTab(self.torrents_tab, "🎮  Игры (qBittorrent)")

        root_layout.addWidget(self.sub_tabs)

    # =========================================================================
    # ПОДВКЛАДКА 1: ОЧЕРЕДЬ ЗАГРУЗКИ МОДОВ
    # =========================================================================

    def _setup_mods_ui(self, parent_widget: QWidget):
        layout = QVBoxLayout(parent_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        # Верхняя панель управления очередью модов
        top_bar = QHBoxLayout()
        title_label = QLabel("<b>Очередь скачивания модов:</b>")
        title_label.setStyleSheet("font-size: 14px; color: #38bdf8;")

        self.mods_active_badge = QLabel("Очередь пуста")
        self.mods_active_badge.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; padding: 3px 8px; "
            "border-radius: 10px; font-size: 11px; font-weight: bold;"
        )

        top_bar.addWidget(title_label)
        top_bar.addWidget(self.mods_active_badge)
        top_bar.addStretch(1)

        self.resume_mod_btn = QPushButton("▶️ Возобновить / Докачать")
        self.resume_mod_btn.setObjectName("secondaryBtn")
        self.resume_mod_btn.setEnabled(False)
        self.resume_mod_btn.setToolTip("Возобновить или докачать прерванную загрузку с места обрыва")
        self.resume_mod_btn.clicked.connect(self.resume_selected_mod)

        self.cancel_mod_btn = QPushButton("❌ Отменить / Удалить")
        self.cancel_mod_btn.setObjectName("secondaryBtn")
        self.cancel_mod_btn.setEnabled(False)
        self.cancel_mod_btn.setToolTip("Отменить выбранную загрузку или убрать её из очереди")
        self.cancel_mod_btn.clicked.connect(self.cancel_selected_mod)

        self.clear_completed_btn = QPushButton("🧹 Очистить завершённые")
        self.clear_completed_btn.setObjectName("secondaryBtn")
        self.clear_completed_btn.setToolTip("Удалить из списка завершённые, ошибочные и отменённые загрузки")
        self.clear_completed_btn.clicked.connect(self.clear_completed_mods)

        self.open_mods_folder_btn = QPushButton("📁 Папка модов")
        self.open_mods_folder_btn.setObjectName("secondaryBtn")
        self.open_mods_folder_btn.setToolTip("Открыть папку, куда сохраняются скачанные моды")
        self.open_mods_folder_btn.clicked.connect(self.open_mods_folder)

        top_bar.addWidget(self.resume_mod_btn)
        top_bar.addWidget(self.cancel_mod_btn)
        top_bar.addWidget(self.clear_completed_btn)
        top_bar.addWidget(self.open_mods_folder_btn)
        layout.addLayout(top_bar)

        # Таблица очереди модов
        self.mods_table = QTableWidget()
        self.mods_table.setColumnCount(8)
        self.mods_table.setHorizontalHeaderLabels([
            "ID",
            "Мод",
            "Файл",
            "Источник",
            "Размер",
            "Прогресс",
            "Скорость",
            "Статус",
        ])
        self.mods_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.mods_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.mods_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.mods_table.verticalHeader().setVisible(False)

        header = self.mods_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.mods_table.setColumnWidth(5, 170)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)

        self.mods_table.itemSelectionChanged.connect(self._on_mod_selection_changed)
        self.mods_table.cellDoubleClicked.connect(self._on_mod_double_clicked)
        layout.addWidget(self.mods_table)

        # Нижняя информационная строка
        self.mods_status_label = QLabel("Очередь готова к приёму модов")
        self.mods_status_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(self.mods_status_label)

    def _setup_queue_signals(self):
        """Подключает сигналы менеджера очереди (потокобезопасно через Qt Signal/Slot)."""
        self.queue_manager.task_added.connect(self._on_task_added)
        self.queue_manager.task_updated.connect(self._on_task_updated)
        self.queue_manager.task_progress.connect(self._on_task_progress)
        self.queue_manager.task_finished.connect(self._on_task_finished)
        self.queue_manager.queue_cleared.connect(self._on_queue_cleared)
        self.queue_manager.active_count_changed.connect(self._on_active_count_changed)

    def refresh_mods_table(self):
        """Полная перерисовка таблицы модов."""
        tasks = self.queue_manager.get_all_tasks()
        selected_task_id = self._get_selected_task_id()

        self.mods_table.setRowCount(len(tasks))
        restore_row = -1

        for row, task in enumerate(tasks):
            if selected_task_id and task.task_id == selected_task_id:
                restore_row = row
            self._render_task_row(row, task)

        if restore_row >= 0:
            self.mods_table.selectRow(restore_row)

        self._update_mods_badge(len(tasks))

    def _get_row_by_task_id(self, task_id: int) -> int:
        for row in range(self.mods_table.rowCount()):
            item = self.mods_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == task_id:
                return row
        return -1

    def _get_selected_task_id(self) -> Optional[int]:
        row = self.mods_table.currentRow()
        if 0 <= row < self.mods_table.rowCount():
            item = self.mods_table.item(row, 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        return None

    def _render_task_row(self, row: int, task: DownloadTask):
        # 0. ID
        id_item = QTableWidgetItem(f"#{task.task_id}")
        id_item.setData(Qt.ItemDataRole.UserRole, task.task_id)
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mods_table.setItem(row, 0, id_item)

        # 1. Название мода
        title_item = QTableWidgetItem(task.title)
        self.mods_table.setItem(row, 1, title_item)

        # 2. Файл
        file_item = QTableWidgetItem(task.file_name)
        self.mods_table.setItem(row, 2, file_item)

        # 3. Источник
        src_label = "⚡ Nexus Mods" if task.source == "nexus" else "🌐 Steam"
        src_item = QTableWidgetItem(src_label)
        src_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mods_table.setItem(row, 3, src_item)

        # 4. Размер
        size_item = QTableWidgetItem(task.size_human or "—")
        size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mods_table.setItem(row, 4, size_item)

        # 5. Прогресс-бар
        pbar = self.mods_table.cellWidget(row, 5)
        if not isinstance(pbar, QProgressBar):
            pbar = QProgressBar()
            pbar.setRange(0, 100)
            pbar.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.mods_table.setCellWidget(row, 5, pbar)

        pbar.setValue(task.progress)
        pbar.setFormat(f"{task.progress}%")

        # 6. Скорость
        speed_item = QTableWidgetItem(task.speed_str or "—")
        speed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if task.status == STATE_DOWNLOADING and task.speed_str not in ["—", "0 Б/с"]:
            speed_item.setForeground(QColor("#22c55e"))
        self.mods_table.setItem(row, 6, speed_item)

        # 7. Статус
        state_item = QTableWidgetItem(task.status_text)
        state_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        color = STATUS_COLORS.get(task.status, "#e2e8f0")
        state_item.setForeground(QColor(color))

        if task.status == STATE_ERROR and task.error_message:
            state_item.setToolTip(f"Ошибка:\n{task.error_message}")
        elif task.status == STATE_COMPLETED and task.saved_path:
            state_item.setToolTip(f"Сохранено в:\n{task.saved_path}")

        self.mods_table.setItem(row, 7, state_item)

    def _on_task_added(self, task: DownloadTask):
        row = self.mods_table.rowCount()
        self.mods_table.insertRow(row)
        self._render_task_row(row, task)
        self._update_mods_badge()
        self.mods_status_label.setText(f"Мод '{task.title}' добавлен в очередь загрузок (#{task.task_id})")

    def _on_task_updated(self, task: DownloadTask):
        row = self._get_row_by_task_id(task.task_id)
        if row >= 0:
            self._render_task_row(row, task)
            self._on_mod_selection_changed()
        else:
            self.refresh_mods_table()

    def _on_task_progress(self, task_id: int, downloaded: int, total: int, speed: str):
        row = self._get_row_by_task_id(task_id)
        if row >= 0:
            task = self.queue_manager.get_task(task_id)
            if task:
                pbar = self.mods_table.cellWidget(row, 5)
                if isinstance(pbar, QProgressBar):
                    pbar.setValue(task.progress)
                    pbar.setFormat(f"{task.progress}%")

                speed_item = self.mods_table.item(row, 6)
                if speed_item:
                    speed_item.setText(speed)
                    if speed != "—":
                        speed_item.setForeground(QColor("#22c55e"))

                status_item = self.mods_table.item(row, 7)
                if status_item:
                    status_item.setText(task.status_text)

    def _on_task_finished(self, task_id: int, is_success: bool, path_or_error: str):
        row = self._get_row_by_task_id(task_id)
        if row >= 0:
            task = self.queue_manager.get_task(task_id)
            if task:
                self._render_task_row(row, task)

        self._update_mods_badge()
        task = self.queue_manager.get_task(task_id)
        if task:
            if is_success:
                self.mods_status_label.setText(f"✓ Загрузка завершена: {task.title} (#{task_id})")
            else:
                self.mods_status_label.setText(f"✕ Ошибка загрузки #{task_id}: {path_or_error[:80]}")

    def _on_queue_cleared(self):
        self.refresh_mods_table()

    def _on_active_count_changed(self, count: int):
        self._update_mods_badge()

    def _update_mods_badge(self, total_tasks: Optional[int] = None):
        active_count = self.queue_manager.get_active_count()
        if total_tasks is None:
            total_tasks = self.mods_table.rowCount()

        if active_count > 0:
            self.mods_active_badge.setText(f"● Активных: {active_count} (всего: {total_tasks})")
            self.mods_active_badge.setStyleSheet(
                "background-color: #164e63; color: #38bdf8; padding: 3px 8px; "
                "border-radius: 10px; font-size: 11px; font-weight: bold;"
            )
        else:
            self.mods_active_badge.setText(f"Очередь свободна (всего: {total_tasks})")
            self.mods_active_badge.setStyleSheet(
                "background-color: #1e293b; color: #94a3b8; padding: 3px 8px; "
                "border-radius: 10px; font-size: 11px;"
            )

    def _on_mod_selection_changed(self):
        task_id = self._get_selected_task_id()
        if task_id:
            task = self.queue_manager.get_task(task_id)
            if task:
                can_cancel = task.status in [STATE_QUEUED, STATE_GENERATING_URL, STATE_DOWNLOADING]
                can_resume = task.status in [STATE_ERROR, STATE_CANCELLED]
                self.cancel_mod_btn.setEnabled(can_cancel)
                self.resume_mod_btn.setEnabled(can_resume)
                if task.status == STATE_ERROR and task.error_message:
                    self.mods_status_label.setText(f"Ошибка #{task.task_id}: {task.error_message}")
                elif task.status == STATE_COMPLETED and task.saved_path:
                    self.mods_status_label.setText(f"Сохранено: {task.saved_path}")
                elif task.status == STATE_DOWNLOADING:
                    self.mods_status_label.setText(f"Скачивание: {task.title} ({task.speed_str})")
                return
        self.cancel_mod_btn.setEnabled(False)
        self.resume_mod_btn.setEnabled(False)

    def resume_selected_mod(self):
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        task = self.queue_manager.get_task(task_id)
        if not task:
            return
        if self.queue_manager.retry_task(task_id):
            self.mods_status_label.setText(f"Загрузка '{task.title}' (#{task.task_id}) возвращена в очередь для докачки")
            self._on_mod_selection_changed()

    def _on_mod_double_clicked(self, row: int, column: int):
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        task = self.queue_manager.get_task(task_id)
        if not task:
            return

        if task.status == STATE_COMPLETED and task.saved_path:
            path = task.saved_path
            if os.path.isfile(path):
                folder = os.path.dirname(path)
            else:
                folder = path
            if os.path.exists(folder):
                try:
                    os.startfile(folder)
                except Exception:
                    pass
        elif task.status == STATE_ERROR and task.error_message:
            QMessageBox.critical(
                self,
                f"Ошибка загрузки #{task.task_id}",
                f"Не удалось скачать '{task.title}':\n\n{task.error_message}"
            )

    def cancel_selected_mod(self):
        task_id = self._get_selected_task_id()
        if not task_id:
            return
        task = self.queue_manager.get_task(task_id)
        if not task:
            return

        reply = QMessageBox.question(
            self,
            "Отмена загрузки",
            f"Отменить загрузку мода '{task.title}' (#{task.task_id})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.queue_manager.cancel_task(task_id)
            self.mods_status_label.setText(f"Загрузка #{task_id} отменена пользователем.")

    def clear_completed_mods(self):
        self.queue_manager.clear_completed()
        self.mods_status_label.setText("Завершённые и отменённые задачи удалены из списка.")

    def open_mods_folder(self):
        folder = ""
        if self.get_mods_folder_callback:
            folder = self.get_mods_folder_callback()
        if not folder or not os.path.isdir(folder):
            from PyQt6.QtCore import QSettings
            folder = QSettings("SBSH", "SteamWorkshopLauncher").value("mods_folder", "")

        if folder and os.path.exists(folder):
            try:
                os.startfile(folder)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось открыть папку:\n{e}")
        else:
            QMessageBox.information(
                self,
                "Папка не найдена",
                f"Папка модов ещё не указана или не существует:\n{folder}\n"
                "Вы можете настроить путь во вкладке 'Настройки'."
            )

    # =========================================================================
    # ПОДВКЛАДКА 2: УСТАНОВЛЕННЫЕ И СОХРАНЁННЫЕ МОДЫ
    # =========================================================================

    def _setup_installed_signals(self):
        installed_mods_manager.mods_updated.connect(self.refresh_installed_mods_table)

    def _setup_installed_mods_ui(self, parent_widget: QWidget):
        layout = QVBoxLayout(parent_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        # Верхняя панель управления
        top_bar = QHBoxLayout()
        title_label = QLabel("<b>Реестр установленных и скачанных модов:</b>")
        title_label.setStyleSheet("font-size: 14px; color: #38bdf8;")

        self.installed_count_badge = QLabel("0 модов")
        self.installed_count_badge.setStyleSheet(
            "background-color: #1e293b; color: #94a3b8; padding: 3px 8px; "
            "border-radius: 10px; font-size: 11px; font-weight: bold;"
        )

        top_bar.addWidget(title_label)
        top_bar.addWidget(self.installed_count_badge)
        top_bar.addStretch(1)

        self.open_installed_folder_btn = QPushButton("📁 Папка модов")
        self.open_installed_folder_btn.setObjectName("secondaryBtn")
        self.open_installed_folder_btn.setToolTip("Открыть папку сохранения модов в проводнике")
        self.open_installed_folder_btn.clicked.connect(self.open_mods_folder)
        top_bar.addWidget(self.open_installed_folder_btn)

        self.scan_installed_btn = QPushButton("🔍 Сканировать игру и моды")
        self.scan_installed_btn.setObjectName("secondaryBtn")
        self.scan_installed_btn.setToolTip("Глубокое сканирование папок игры (r6, bin, mods, archive/pc/mod) и каталога загрузок")
        self.scan_installed_btn.clicked.connect(self.scan_installed_folder)
        top_bar.addWidget(self.scan_installed_btn)

        self.scan_custom_folder_btn = QPushButton("📁 Выбрать папку...")
        self.scan_custom_folder_btn.setObjectName("secondaryBtn")
        self.scan_custom_folder_btn.setToolTip("Выбрать конкретную директорию игры или модов на диске для глубокого сканирования")
        self.scan_custom_folder_btn.clicked.connect(self.scan_custom_folder)
        top_bar.addWidget(self.scan_custom_folder_btn)

        self.clean_missing_btn = QPushButton("🧹 Очистить отсутствующие")
        self.clean_missing_btn.setObjectName("secondaryBtn")
        self.clean_missing_btn.setToolTip("Убрать из реестра записи модов, удалённых с диска")
        self.clean_missing_btn.clicked.connect(self.clean_missing_installed_mods)
        top_bar.addWidget(self.clean_missing_btn)

        self.clean_duplicates_btn = QPushButton("🗑 Удалить дубликаты")
        self.clean_duplicates_btn.setObjectName("secondaryBtn")
        self.clean_duplicates_btn.setToolTip("Интеллектуальный поиск и безопасное удаление дубликатов файлов модов (archive/pc/mod, r6, mods, загрузки) с сохранением основного экземпляра")
        self.clean_duplicates_btn.clicked.connect(self.clean_duplicate_mods_action)
        top_bar.addWidget(self.clean_duplicates_btn)

        layout.addLayout(top_bar)

        # Панель фильтрации
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self.installed_search_input = QLineEdit()
        self.installed_search_input.setPlaceholderText("🔎 Поиск по названию мода, типу (Archive, CET, Redscript), игре или файлу...")
        self.installed_search_input.setClearButtonEnabled(True)
        self.installed_search_input.textChanged.connect(self.refresh_installed_mods_table)
        filter_row.addWidget(self.installed_search_input, stretch=3)

        self.installed_source_combo = QComboBox()
        self.installed_source_combo.addItem("Все источники", "")
        self.installed_source_combo.addItem("🌐 Nexus Mods", "nexus")
        self.installed_source_combo.addItem("⚡ Steam Workshop", "steam")
        self.installed_source_combo.addItem("💻 Локальные / Игра", "local")
        self.installed_source_combo.currentIndexChanged.connect(self.refresh_installed_mods_table)
        filter_row.addWidget(self.installed_source_combo, stretch=1)

        self.installed_only_disk_cb = QCheckBox("Только на диске")
        self.installed_only_disk_cb.setChecked(False)
        self.installed_only_disk_cb.stateChanged.connect(self.refresh_installed_mods_table)
        filter_row.addWidget(self.installed_only_disk_cb)

        layout.addLayout(filter_row)

        # Таблица установленных модов
        self.installed_table = QTableWidget()
        self.installed_table.setColumnCount(8)
        self.installed_table.setHorizontalHeaderLabels([
            "Название мода", "Тип", "Игра", "Источник", "Размер", "Дата установки", "Статус", "Путь к файлам"
        ])
        self.installed_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.installed_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.installed_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.installed_table.verticalHeader().setVisible(False)
        self.installed_table.horizontalHeader().setStretchLastSection(False)
        self.installed_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.installed_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.installed_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.installed_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.installed_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.installed_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.installed_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.installed_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        self.installed_table.setColumnWidth(7, 260)

        self.installed_table.itemSelectionChanged.connect(self.on_installed_selection_changed)
        self.installed_table.cellDoubleClicked.connect(self.on_installed_double_clicked)
        layout.addWidget(self.installed_table, stretch=1)

        # Нижняя панель действий для выбранного мода
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        self.show_in_explorer_btn = QPushButton("📂 Показать в проводнике")
        self.show_in_explorer_btn.setObjectName("secondaryBtn")
        self.show_in_explorer_btn.setEnabled(False)
        self.show_in_explorer_btn.clicked.connect(self.show_selected_in_explorer)
        bottom_bar.addWidget(self.show_in_explorer_btn)

        self.delete_installed_btn = QPushButton("🗑️ Удалить с диска")
        self.delete_installed_btn.setObjectName("dangerBtn")
        self.delete_installed_btn.setEnabled(False)
        self.delete_installed_btn.clicked.connect(self.delete_selected_installed_mod)
        bottom_bar.addWidget(self.delete_installed_btn)

        self.installed_selected_info = QLabel("Выберите мод из таблицы для управления файлами")
        self.installed_selected_info.setStyleSheet("color: #94a3b8; font-size: 11px;")
        bottom_bar.addWidget(self.installed_selected_info, stretch=1)

        layout.addLayout(bottom_bar)

    def refresh_installed_mods_table(self):
        if not hasattr(self, "installed_table"):
            return

        q = self.installed_search_input.text().strip() if hasattr(self, "installed_search_input") else ""
        src = self.installed_source_combo.currentData() if hasattr(self, "installed_source_combo") else ""
        only_disk = self.installed_only_disk_cb.isChecked() if hasattr(self, "installed_only_disk_cb") else False

        mods = installed_mods_manager.get_all(source=src, query=q, only_existing_on_disk=only_disk)
        self.installed_mods = mods
        self.installed_table.setRowCount(len(mods))

        total_count = len(installed_mods_manager.get_all())
        on_disk_count = sum(1 for m in mods if m.get("exists_on_disk"))
        if hasattr(self, "installed_count_badge"):
            self.installed_count_badge.setText(f"{len(mods)} из {total_count} модов (на диске: {on_disk_count})")
        if hasattr(self, "sub_tabs") and self.sub_tabs.count() > 1:
            self.sub_tabs.setTabText(1, f"💾  Установленные моды ({total_count})")

        TYPE_COLORS = {
            "Archive": "#38bdf8",
            "CET (Lua)": "#22c55e",
            "Redscript": "#f59e0b",
            "TweakXL": "#a855f7",
            "REDmod": "#ef4444",
            "Plugin (DLL/ASI)": "#06b6d4",
            "Steam Workshop": "#60a5fa",
            "Архив (Zip/7z)": "#94a3b8",
        }

        for row, m in enumerate(mods):
            exists = m.get("exists_on_disk", False)

            # 0. Название
            title_text = m.get("title", "Мод")
            if m.get("is_collection"):
                title_text = f"📚 {title_text}"
            title_item = QTableWidgetItem(title_text)
            title_item.setData(Qt.ItemDataRole.UserRole, m)

            # 1. Тип
            m_type = m.get("mod_type") or "Мод"
            type_item = QTableWidgetItem(m_type)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            c = TYPE_COLORS.get(m_type, "#94a3b8")
            type_item.setForeground(QColor(c))

            # 2. Игра
            g_name = m.get("game_name") or m.get("game_domain") or "—"
            game_item = QTableWidgetItem(str(g_name))

            # 3. Источник
            src = m.get("source", "")
            if src == "nexus":
                src_str = "🌐 Nexus Mods"
            elif src == "steam":
                src_str = "⚡ Steam Workshop"
            else:
                src_str = "💻 Локальный / Игра"
            source_item = QTableWidgetItem(src_str)
            source_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # 4. Размер
            size_item = QTableWidgetItem(m.get("size_human", "—"))
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            # 5. Дата
            date_item = QTableWidgetItem(m.get("installed_at", "—"))
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # 6. Статус
            if exists:
                status_item = QTableWidgetItem("✅ На диске")
                status_item.setForeground(QColor("#22c55e"))
            else:
                status_item = QTableWidgetItem("⚠️ Отсутствует")
                status_item.setForeground(QColor("#ef4444"))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # 7. Путь
            path_item = QTableWidgetItem(m.get("install_path", "—"))
            path_item.setToolTip(m.get("install_path", ""))

            self.installed_table.setItem(row, 0, title_item)
            self.installed_table.setItem(row, 1, type_item)
            self.installed_table.setItem(row, 2, game_item)
            self.installed_table.setItem(row, 3, source_item)
            self.installed_table.setItem(row, 4, size_item)
            self.installed_table.setItem(row, 5, date_item)
            self.installed_table.setItem(row, 6, status_item)
            self.installed_table.setItem(row, 7, path_item)

        self.on_installed_selection_changed()

    def on_installed_selection_changed(self):
        selected_rows = self.installed_table.selectionModel().selectedRows() if hasattr(self, "installed_table") else []
        if not selected_rows:
            self.selected_installed_mod = None
            if hasattr(self, "show_in_explorer_btn"):
                self.show_in_explorer_btn.setEnabled(False)
            if hasattr(self, "delete_installed_btn"):
                self.delete_installed_btn.setEnabled(False)
            if hasattr(self, "installed_selected_info"):
                self.installed_selected_info.setText("Выберите мод из таблицы для управления файлами")
            return

        row = selected_rows[0].row()
        item = self.installed_table.item(row, 0)
        if item:
            self.selected_installed_mod = item.data(Qt.ItemDataRole.UserRole)
            m = self.selected_installed_mod
            p = m.get("install_path", "")
            exists = m.get("exists_on_disk", False)
            status_text = "на диске" if exists else "файл не найден"

            if hasattr(self, "show_in_explorer_btn"):
                self.show_in_explorer_btn.setEnabled(bool(p and exists))
            if hasattr(self, "delete_installed_btn"):
                self.delete_installed_btn.setEnabled(True)
            if hasattr(self, "installed_selected_info"):
                self.installed_selected_info.setText(f"<b>{m.get('title')}</b> [{m.get('mod_type', 'Мод')}] — {status_text}: {p}")

    def on_installed_double_clicked(self, row: int, column: int):
        self.show_selected_in_explorer()

    def show_selected_in_explorer(self):
        mod = self.selected_installed_mod
        if not mod:
            return
        path = mod.get("install_path")
        if not path:
            return
        clean = os.path.abspath(path)
        if os.name == "nt":
            if os.path.exists(clean):
                subprocess.run(f'explorer /select,"{clean}"', shell=True)
            else:
                QMessageBox.warning(self, "Внимание", f"Файл не найден по пути:\n{clean}")
        else:
            if os.path.exists(clean):
                import webbrowser
                parent = os.path.dirname(clean) if os.path.isfile(clean) else clean
                webbrowser.open(parent)

    def delete_selected_installed_mod(self):
        mod = self.selected_installed_mod
        if not mod:
            return
        title = mod.get("title", "Мод")
        path = mod.get("install_path", "")
        key = mod.get("key") or mod.get("mod_id")

        reply = QMessageBox.question(
            self,
            "Удаление мода",
            f"Вы уверены, что хотите удалить мод:\n\n<b>{title}</b>\n\n"
            f"Файлы на диске ({path}) будут безвозвратно удалены.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            success, msg = installed_mods_manager.unregister_mod(key, delete_files=True)
            if not success and msg:
                QMessageBox.warning(self, "Предупреждение", f"Не удалось полностью удалить файлы: {msg}")
            self.refresh_installed_mods_table()

    def scan_installed_folder(self):
        """Комплексное сканирование всех игровых директорий и папок модов."""
        st = QSettings("SBSH", "Launcher")
        mods_f = st.value("mods_folder", "") or (self.get_mods_folder_callback() if self.get_mods_folder_callback else "")
        games_f = st.value("games_save_path", "")

        added = installed_mods_manager.scan_all_locations(mods_dir=mods_f, games_dir=games_f)
        total = len(installed_mods_manager.get_all())
        if added > 0:
            QMessageBox.information(
                self,
                "Синхронизация завершена",
                f"Сканирование завершено!\n\n"
                f"Обнаружено и синхронизировано новых модов: {added}\n"
                f"Всего модов в реестре: {total}"
            )
        else:
            QMessageBox.information(
                self,
                "Сканирование завершено",
                f"Новых модов не обнаружено. Все {total} модов в реестре актуальны."
            )
        self.refresh_installed_mods_table()

    def scan_custom_folder(self):
        """Выбор конкретной папки на диске для глубокого сканирования."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите директорию игры или модов для сканирования",
            os.getcwd()
        )
        if folder:
            added = installed_mods_manager.scan_directory(folder)
            total = len(installed_mods_manager.get_all())
            if added > 0:
                QMessageBox.information(
                    self,
                    "Сканирование папки",
                    f"Сканирование завершено!\n\nПуть: {folder}\n"
                    f"Обнаружено и синхронизировано модов: {added}\n"
                    f"Всего в реестре: {total}"
                )
            else:
                QMessageBox.information(
                    self,
                    "Сканирование папки",
                    f"В выбранной папке не найдено новых файлов модов.\nВсего в реестре: {total}"
                )
            self.refresh_installed_mods_table()

    def clean_missing_installed_mods(self):
        removed = installed_mods_manager.clean_missing()
        if removed > 0:
            QMessageBox.information(
                self,
                "Очистка реестра",
                f"Удалено {removed} записей о модах, файлы которых отсутствуют на диске."
            )
        else:
            QMessageBox.information(
                self,
                "Очистка реестра",
                "Все зарегистрированные моды присутствуют на диске. Очистка не требуется."
            )
        self.refresh_installed_mods_table()

    def clean_duplicate_mods_action(self):
        """Интеллектуальная комплексная очистка дубликатов файлов модов."""
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Очистка дубликатов модов")
        msg_box.setText(
            "<h3>Очистка дубликатов файлов модов</h3>"
            "<p>Будет выполнено сканирование директории загрузок (mods) и официальных папок игры "
            "(archive/pc/mod, r6, bin, mods).</p>"
            "<p><b>Критерии сопоставления и безопасного сохранения:</b><br>"
            "• При совпадении имени и типа файла (.archive, .reds, .lua, .zip и др.) файлы помечаются как дубликаты.<br>"
            "• Сохраняется только <b>один основной экземпляр</b> (приоритет у официальной папки игры "
            "или самого свежего по дате изменения файла).<br>"
            "• Избыточные дубликаты удаляются для освобождения места и предотвращения конфликтов модов.<br>"
            "• Подробный отчёт сохраняется в <code>duplicate_cleanup.log</code>.</p>"
            "<p>Выберите режим выполнения:</p>"
        )
        del_btn = msg_box.addButton("🗑 Найти и удалить дубликаты", QMessageBox.ButtonRole.AcceptRole)
        del_btn.setStyleSheet("background-color: #dc2626; color: white; font-weight: bold; padding: 6px 12px; border-radius: 6px;")
        dry_btn = msg_box.addButton("🔍 Только предпросмотр (Dry-Run)", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = msg_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)

        msg_box.exec()
        clicked = msg_box.clickedButton()
        if clicked == cancel_btn or clicked is None:
            return

        dry_run = (clicked == dry_btn)

        st = QSettings("SBSH", "Launcher")
        mods_f = st.value("mods_folder", "") or (self.get_mods_folder_callback() if self.get_mods_folder_callback else "")
        games_f = st.value("games_save_path", "")

        from modules.duplicate_cleaner import duplicate_mod_cleaner
        if mods_f:
            duplicate_mod_cleaner.mods_dir = os.path.abspath(mods_f)
        if games_f and os.path.isdir(games_f) and games_f not in duplicate_mod_cleaner.custom_game_dirs:
            duplicate_mod_cleaner.custom_game_dirs.append(os.path.abspath(games_f))

        report = duplicate_mod_cleaner.execute_cleanup(
            dry_run=dry_run,
            retention_policy="game_dir_first",
        )

        self.refresh_installed_mods_table()

        dialog = DuplicateCleanupReportDialog(report, parent=self)
        dialog.exec()

    # =========================================================================
    # ПОДВКЛАДКА 3: QBITTORRENT (ИГРЫ)
    # =========================================================================

    def _setup_torrents_ui(self, parent_widget: QWidget):
        layout = QVBoxLayout(parent_widget)
        layout.setSpacing(10)
        layout.setContentsMargins(8, 8, 8, 8)

        # Верхняя панель управления
        top_bar = QHBoxLayout()
        title_label = QLabel("<b>Активные и завершённые загрузки в qBittorrent:</b>")
        title_label.setStyleSheet("font-size: 14px; color: #38bdf8;")

        self.refresh_button = QPushButton("🔄 Обновить")
        self.refresh_button.setObjectName("secondaryBtn")
        self.refresh_button.clicked.connect(self.fetch_torrents)

        top_bar.addWidget(title_label, stretch=1)
        top_bar.addWidget(self.refresh_button)
        layout.addLayout(top_bar)

        # Таблица торрентов
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Название", "Размер", "Прогресс", "Скорость", "Статус"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 160)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        layout.addWidget(self.table)

        # Нижняя панель действий
        action_bar = QHBoxLayout()
        action_bar.setSpacing(10)

        self.pause_resume_btn = QPushButton("⏯ Пауза / Запуск")
        self.pause_resume_btn.setObjectName("secondaryBtn")
        self.pause_resume_btn.setEnabled(False)
        self.pause_resume_btn.clicked.connect(self.toggle_pause_resume)

        self.open_folder_btn = QPushButton("📁 Открыть папку с игрой")
        self.open_folder_btn.setObjectName("secondaryBtn")
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self.open_save_folder)

        self.delete_btn = QPushButton("🗑 Удалить")
        self.delete_btn.setObjectName("secondaryBtn")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.delete_torrent)

        self.status_label = QLabel("Подключение к qBittorrent...")
        self.status_label.setStyleSheet("color: #94a3b8; font-size: 11px;")

        action_bar.addWidget(self.status_label, stretch=1)
        action_bar.addWidget(self.pause_resume_btn)
        action_bar.addWidget(self.open_folder_btn)
        action_bar.addWidget(self.delete_btn)
        layout.addLayout(action_bar)

    def _setup_torrents_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.fetch_torrents)
        self.timer.start(3000)

    def get_qb_url(self):
        if self.get_qb_url_callback:
            return self.get_qb_url_callback()
        return "http://localhost:1489"

    def fetch_torrents(self):
        if self.fetch_worker and self.fetch_worker.isRunning():
            return

        qb_url = self.get_qb_url()
        self.fetch_worker = QBTFetchWorker(qb_url)
        self.fetch_worker.torrents_fetched.connect(self.on_torrents_fetched)
        self.fetch_worker.fetch_failed.connect(self.on_fetch_failed)
        self.fetch_worker.start()

    def on_torrents_fetched(self, torrents):
        self.current_torrents = torrents
        self.status_label.setText(f"Подключено к qBittorrent • Торрентов: {len(torrents)}")

        selected_row = self.table.currentRow()
        saved_hash = None
        if 0 <= selected_row < len(self.current_torrents):
            saved_hash = self.current_torrents[selected_row].get("hash")

        self.table.setRowCount(len(torrents))

        new_selected_row = -1
        for row, t in enumerate(torrents):
            if saved_hash and t.get("hash") == saved_hash:
                new_selected_row = row

            name_item = QTableWidgetItem(t.get("name", "Unknown"))
            size_item = QTableWidgetItem(format_bytes(t.get("size", 0)))
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            progress_val = int((t.get("progress", 0.0)) * 100)
            pbar = QProgressBar()
            pbar.setValue(progress_val)
            pbar.setFormat(f"{progress_val}%")

            dlspeed = t.get("dlspeed", 0)
            speed_str = format_speed(dlspeed)
            speed_item = QTableWidgetItem(speed_str)
            speed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if dlspeed > 0:
                speed_item.setForeground(QColor("#22c55e"))

            raw_state = t.get("state", "unknown")
            state_text = STATE_TRANSLATIONS.get(raw_state, raw_state)
            state_item = QTableWidgetItem(state_text)
            state_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, size_item)
            self.table.setCellWidget(row, 2, pbar)
            self.table.setItem(row, 3, speed_item)
            self.table.setItem(row, 4, state_item)

        if new_selected_row >= 0:
            self.table.selectRow(new_selected_row)

    def on_fetch_failed(self, error):
        self.status_label.setText(
            f"qBittorrent недоступен ({self.get_qb_url()}). Запустите клиент с Web UI."
        )

    def on_selection_changed(self):
        row = self.table.currentRow()
        has_sel = 0 <= row < len(self.current_torrents)
        self.pause_resume_btn.setEnabled(has_sel)
        self.open_folder_btn.setEnabled(has_sel)
        self.delete_btn.setEnabled(has_sel)

    def get_selected_torrent(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.current_torrents):
            return self.current_torrents[row]
        return None

    def toggle_pause_resume(self):
        t = self.get_selected_torrent()
        if not t:
            return

        t_hash = t.get("hash")
        state = t.get("state", "")
        qb_url = self.get_qb_url().rstrip("/")

        endpoint = "resume" if "pause" in state or "stopped" in state else "pause"
        try:
            requests.post(f"{qb_url}/api/v2/torrents/{endpoint}", data={"hashes": t_hash}, timeout=3)
            self.fetch_torrents()
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось изменить состояние торрента:\n{e}")

    def open_save_folder(self):
        t = self.get_selected_torrent()
        if not t:
            return

        save_path = t.get("save_path") or t.get("content_path")
        if save_path and os.path.exists(save_path):
            try:
                os.startfile(save_path)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось открыть папку:\n{e}")
        else:
            QMessageBox.information(self, "Информация", f"Путь ещё не создан или недоступен:\n{save_path}")

    def delete_torrent(self):
        t = self.get_selected_torrent()
        if not t:
            return

        t_hash = t.get("hash")
        name = t.get("name", "Торрент")

        reply = QMessageBox.question(
            self,
            "Удаление торрента",
            f"Удалить '{name}' из qBittorrent?\n(Сами скачанные файлы останутся на диске)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                qb_url = self.get_qb_url().rstrip("/")
                requests.post(
                    f"{qb_url}/api/v2/torrents/delete",
                    data={"hashes": t_hash, "deleteFiles": "false"},
                    timeout=3
                )
                self.fetch_torrents()
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось удалить торрент:\n{e}")
