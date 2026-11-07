import os
import sys

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import requests


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


class DownloadsTabWidget(QWidget):
    """Вкладка мониторинга активных загрузок в qBittorrent."""

    def __init__(self, get_qb_url_callback=None):
        super().__init__()
        self.get_qb_url_callback = get_qb_url_callback
        self.current_torrents = []
        self.selected_hash = None
        self.fetch_worker = None

        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

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

    def _setup_timer(self):
        # Автоматическое обновление списка каждые 3 секунды
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

        # Сохраняем текущий выбранный хэш перед перерисовкой
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

            # Прогресс бар
            progress_val = int((t.get("progress", 0.0)) * 100)
            pbar = QProgressBar()
            pbar.setValue(progress_val)
            pbar.setFormat(f"{progress_val}%")

            # Скорость
            dlspeed = t.get("dlspeed", 0)
            speed_str = format_speed(dlspeed)
            speed_item = QTableWidgetItem(speed_str)
            speed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if dlspeed > 0:
                speed_item.setForeground(Qt.GlobalColor.green)

            # Статус
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
