import os
import sys

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from modules.favorites import favorites_manager


class AddGameDialog(QDialog):
    """Диалог быстрого добавления игры в избранное."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить игру в избранное")
        self.resize(420, 160)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        form_layout = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Например: Cyberpunk 2077 или RimWorld")

        self.appid_input = QLineEdit()
        self.appid_input.setPlaceholderText("Необязательно, например: 294100")

        form_layout.addRow("Название игры:", self.name_input)
        form_layout.addRow("Steam AppID:", self.appid_input)
        layout.addLayout(form_layout)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_data(self):
        return self.name_input.text().strip(), self.appid_input.text().strip()


class FavoritesTabWidget(QWidget):
    """Вкладка управления избранными играми с быстрым переходом к модам и раздачам."""
    open_mods_requested = pyqtSignal(str, int)  # (game_name, appid)
    search_game_requested = pyqtSignal(str)     # (game_name)
    favorites_updated = pyqtSignal()

    def __init__(self, get_games_save_path_callback=None):
        super().__init__()
        self.get_games_save_path_callback = get_games_save_path_callback
        self._filtered_favorites = []
        self._setup_ui()
        self.refresh_list()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- Верхняя панель ---
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.header_label = QLabel("<b>⭐ Мои избранные игры</b>")
        self.header_label.setStyleSheet("font-size: 15px; color: #38bdf8;")

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Фильтр по названию...")
        self.filter_input.setClearButtonEnabled(True)
        self.filter_input.textChanged.connect(self.filter_favorites)

        self.add_button = QPushButton("➕ Добавить игру")
        self.add_button.setObjectName("accentBtn")
        self.add_button.clicked.connect(self.open_add_dialog)

        top_bar.addWidget(self.header_label)
        top_bar.addSpacing(15)
        top_bar.addWidget(self.filter_input, stretch=1)
        top_bar.addWidget(self.add_button)
        layout.addLayout(top_bar)

        # --- Таблица избранного ---
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Игра", "Steam AppID", "Добавлено", "Быстрые действия"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)

        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.table)

        # --- Статус-бар / Подсказка ---
        self.status_label = QLabel("Всего в избранном: 0")
        self.status_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(self.status_label)

    def get_games_path(self):
        if self.get_games_save_path_callback:
            return self.get_games_save_path_callback()
        return r"G:\games"

    def refresh_list(self):
        """Перезагружает и отображает список избранных игр."""
        favorites_manager.load()
        self.filter_favorites()

    def filter_favorites(self):
        filter_text = self.filter_input.text().strip().lower()
        all_favs = favorites_manager.get_all()

        if filter_text:
            self._filtered_favorites = [
                f for f in all_favs if filter_text in f.get("name", "").lower()
            ]
        else:
            self._filtered_favorites = all_favs

        self.table.setRowCount(len(self._filtered_favorites))

        for row, item in enumerate(self._filtered_favorites):
            name = item.get("name", "Unknown")
            appid = item.get("appid")
            added_at = item.get("added_at", "—")

            # Название
            name_item = QTableWidgetItem(f"⭐ {name}")
            name_item.setFont(self.font())

            # AppID
            appid_str = str(appid) if appid else "—"
            appid_item = QTableWidgetItem(appid_str)
            appid_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if appid:
                appid_item.setForeground(Qt.GlobalColor.cyan)

            # Дата добавления
            date_item = QTableWidgetItem(added_at)
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            date_item.setForeground(Qt.GlobalColor.gray)

            # Контейнер кнопок действий
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(6)

            # Кнопка: Открыть моды
            mods_btn = QPushButton("🔧 Моды")
            mods_btn.setObjectName("secondaryBtn")
            mods_btn.setToolTip("Открыть мастерскую модов для этой игры")
            mods_btn.clicked.connect(lambda _, n=name, a=appid: self.on_open_mods(n, a))

            # Кнопка: Найти раздачи
            torrent_btn = QPushButton("🔍 Торрент")
            torrent_btn.setObjectName("secondaryBtn")
            torrent_btn.setToolTip("Найти раздачи этой игры на RuTracker")
            torrent_btn.clicked.connect(lambda _, n=name: self.on_search_torrent(n))

            # Кнопка: Открыть папку (если есть)
            folder_btn = QPushButton("📁")
            folder_btn.setObjectName("secondaryBtn")
            folder_btn.setToolTip("Открыть локальную папку с игрой")
            folder_btn.clicked.connect(lambda _, n=name: self.on_open_folder(n))

            # Кнопка: Удалить
            delete_btn = QPushButton("✖")
            delete_btn.setObjectName("secondaryBtn")
            delete_btn.setStyleSheet("color: #ef4444; font-weight: bold;")
            delete_btn.setToolTip("Удалить из избранного")
            delete_btn.clicked.connect(lambda _, n=name, a=appid: self.on_remove_favorite(n, a))

            actions_layout.addWidget(mods_btn)
            actions_layout.addWidget(torrent_btn)
            actions_layout.addWidget(folder_btn)
            actions_layout.addWidget(delete_btn)

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, appid_item)
            self.table.setItem(row, 2, date_item)
            self.table.setCellWidget(row, 3, actions_widget)

        count = len(all_favs)
        self.status_label.setText(f"Всего в избранном: {count}")
        self.favorites_updated.emit()

    def open_add_dialog(self):
        dialog = AddGameDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, appid = dialog.get_data()
            if name:
                favorites_manager.add(name, appid=appid if appid else None)
                self.refresh_list()
            else:
                QMessageBox.warning(self, "Ошибка", "Название игры не может быть пустым.")

    def on_open_mods(self, name, appid):
        # Если appid нет, передаем 0, поиск игры в модах выполнится по названию
        aid = int(appid) if appid and str(appid).isdigit() else 0
        self.open_mods_requested.emit(name, aid)

    def on_search_torrent(self, name):
        self.search_game_requested.emit(name)

    def on_open_folder(self, name):
        base_path = self.get_games_path()
        candidates = [
            os.path.join(base_path, name),
            base_path
        ]
        found_path = None
        for c in candidates:
            if os.path.isdir(c):
                found_path = c
                break

        if found_path and os.path.exists(found_path):
            try:
                os.startfile(found_path)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось открыть папку:\n{e}")
        else:
            QMessageBox.information(
                self,
                "Папка не найдена",
                f"Папка с игрой '{name}' пока не найдена в {base_path}."
            )

    def on_remove_favorite(self, name, appid):
        favorites_manager.remove(name=name, appid=appid)
        self.refresh_list()
