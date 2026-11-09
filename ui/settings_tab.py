import os
import sys

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Modl")))
from workshop import check_steamcmd
from modules.download_router import download_router
import requests


class RuleEditDialog(QDialog):
    """Диалог добавления или редактирования правила сортировки файлов."""

    def __init__(self, parent=None, ext: str = "", folder: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Правило сортировки файлов")
        self.setFixedWidth(460)
        self._setup_ui(ext, folder)

    def _setup_ui(self, ext: str, folder: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 18)

        layout.addWidget(QLabel("<b>Расширение файла:</b> (например: <code>.zip</code>, <code>.archive</code>, <code>.pak</code>)"))
        self.ext_input = QLineEdit(ext)
        self.ext_input.setPlaceholderText(".pak")
        layout.addWidget(self.ext_input)

        layout.addWidget(QLabel("<b>Целевая подпапка</b> (в папке модов) или <b>полный путь</b> на диске:"))
        folder_row = QHBoxLayout()
        self.folder_input = QLineEdit(folder)
        self.folder_input.setPlaceholderText("Archives или Mods/Paks")
        browse_btn = QPushButton("Обзор...")
        browse_btn.setObjectName("secondaryBtn")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_input, stretch=1)
        folder_row.addWidget(browse_btn)
        layout.addLayout(folder_row)

        info = QLabel("<small style='color: #94a3b8;'>Если указать относительный путь (например, <i>Archives</i>), папка будет создана внутри вашей директории модов. Если указать абсолютный (например, <i>C:\\Games\\Mod</i>) — файл сохранится строго туда.</small>")
        info.setWordWrap(True)
        layout.addWidget(info)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Сохранить")
        save_btn.setObjectName("accentBtn")
        save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _browse_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Выберите папку назначения", self.folder_input.text().strip())
        if f:
            self.folder_input.setText(f)

    def _on_save(self):
        ext = self.ext_input.text().strip()
        folder = self.folder_input.text().strip()
        if not ext:
            QMessageBox.warning(self, "Внимание", "Укажите расширение файла (например, .zip)")
            return
        if not folder:
            QMessageBox.warning(self, "Внимание", "Укажите целевую подпапку или путь")
            return
        self.accept()

    def get_data(self):
        ext = self.ext_input.text().strip().lower()
        if not ext.startswith("."):
            ext = "." + ext
        return ext, self.folder_input.text().strip()


class SettingsTabWidget(QWidget):
    """Вкладка управления путями, сортировкой загрузок и параметрами подключения."""
    settings_saved = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.settings = QSettings("SBSH", "Launcher")
        self._setup_ui()
        self.load_settings()

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Скроллируемая область для удобства отображения всех настроек
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # ----------------- БЛОК: ИГРЫ И ТОРРЕНТЫ -----------------
        games_group = QGroupBox("🎮 Скачивание игр (qBittorrent)")
        games_layout = QVBoxLayout(games_group)
        games_layout.setSpacing(10)

        # Папка сохранения игр
        games_row = QHBoxLayout()
        self.games_path_input = QLineEdit()
        self.games_path_input.setPlaceholderText("Папка по умолчанию для скачивания игр (например: G:\\games)")
        browse_games_btn = QPushButton("Обзор...")
        browse_games_btn.setObjectName("secondaryBtn")
        browse_games_btn.clicked.connect(self.select_games_folder)
        open_games_btn = QPushButton("Открыть")
        open_games_btn.setObjectName("secondaryBtn")
        open_games_btn.clicked.connect(lambda: self.open_folder(self.games_path_input.text()))

        games_row.addWidget(self.games_path_input, stretch=4)
        games_row.addWidget(browse_games_btn)
        games_row.addWidget(open_games_btn)
        games_layout.addLayout(games_row)

        # qBittorrent Web API URL
        qb_row = QHBoxLayout()
        qb_label = QLabel("Адрес qBittorrent Web UI:")
        self.qb_url_input = QLineEdit()
        self.qb_url_input.setPlaceholderText("http://localhost:1489")
        test_qb_btn = QPushButton("Проверить связь")
        test_qb_btn.setObjectName("secondaryBtn")
        test_qb_btn.clicked.connect(self.test_qb_connection)

        qb_row.addWidget(qb_label)
        qb_row.addWidget(self.qb_url_input, stretch=2)
        qb_row.addWidget(test_qb_btn)
        games_layout.addLayout(qb_row)

        layout.addWidget(games_group)

        # ----------------- БЛОК: МОДЫ И STEAMCMD -----------------
        mods_group = QGroupBox("🔧 Мастерская Steam (SteamCMD)")
        mods_layout = QVBoxLayout(mods_group)
        mods_layout.setSpacing(10)

        # Путь к steamcmd.exe
        steamcmd_row = QHBoxLayout()
        self.steamcmd_input = QLineEdit()
        self.steamcmd_input.setPlaceholderText("Путь к steamcmd.exe")
        browse_cmd_btn = QPushButton("Обзор...")
        browse_cmd_btn.setObjectName("secondaryBtn")
        browse_cmd_btn.clicked.connect(self.select_steamcmd)
        test_cmd_btn = QPushButton("Проверить SteamCMD")
        test_cmd_btn.setObjectName("secondaryBtn")
        test_cmd_btn.clicked.connect(self.test_steamcmd)

        steamcmd_row.addWidget(self.steamcmd_input, stretch=4)
        steamcmd_row.addWidget(browse_cmd_btn)
        steamcmd_row.addWidget(test_cmd_btn)
        mods_layout.addLayout(steamcmd_row)

        # Базовая папка для модов
        mods_folder_row = QHBoxLayout()
        self.mods_path_input = QLineEdit()
        self.mods_path_input.setPlaceholderText("Папка для сохранения скачанных модов")
        browse_mods_btn = QPushButton("Обзор...")
        browse_mods_btn.setObjectName("secondaryBtn")
        browse_mods_btn.clicked.connect(self.select_mods_folder)
        open_mods_btn = QPushButton("Открыть")
        open_mods_btn.setObjectName("secondaryBtn")
        open_mods_btn.clicked.connect(lambda: self.open_folder(self.mods_path_input.text()))

        mods_folder_row.addWidget(self.mods_path_input, stretch=4)
        mods_folder_row.addWidget(browse_mods_btn)
        mods_folder_row.addWidget(open_mods_btn)
        mods_layout.addLayout(mods_folder_row)

        layout.addWidget(mods_group)

        # ----------------- БЛОК: МАРШРУТИЗАЦИЯ И СОРТИРОВКА ЗАГРУЗОК -----------------
        routing_group = QGroupBox("📂 Автосортировка загрузок по типам файлов (Расширения ➔ Папки)")
        routing_layout = QVBoxLayout(routing_group)
        routing_layout.setSpacing(10)

        routing_top_row = QHBoxLayout()
        self.routing_enabled_cb = QCheckBox("Включить автоматическую сортировку по подпапкам на основе расширений")
        self.routing_enabled_cb.setStyleSheet("font-weight: bold; color: #38bdf8;")
        routing_top_row.addWidget(self.routing_enabled_cb)
        routing_top_row.addStretch(1)
        routing_layout.addLayout(routing_top_row)

        routing_desc = QLabel(
            "При скачивании лаунчер автоматически определяет расширение файла, создаёт соответствующую папку "
            "и сохраняет файл туда. Например, <code>.zip</code> и <code>.7z</code> отправятся в <i>Archives</i>, "
            "а <code>.archive</code> для Cyberpunk 2077 — в <i>Cyberpunk 2077/archive/pc/mod</i>."
        )
        routing_desc.setTextFormat(Qt.TextFormat.RichText)
        routing_desc.setWordWrap(True)
        routing_desc.setStyleSheet("color: #94a3b8; font-size: 11px;")
        routing_layout.addWidget(routing_desc)

        # Таблица правил
        self.rules_table = QTableWidget()
        self.rules_table.setColumnCount(2)
        self.rules_table.setHorizontalHeaderLabels(["Расширение файла", "Целевая подпапка / путь"])
        self.rules_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.rules_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.rules_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setMinimumHeight(200)

        r_header = self.rules_table.horizontalHeader()
        r_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        r_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.rules_table.cellDoubleClicked.connect(lambda row, col: self.edit_selected_rule())
        routing_layout.addWidget(self.rules_table)

        # Кнопки управления таблицей правил
        rules_btn_row = QHBoxLayout()
        add_rule_btn = QPushButton("➕ Добавить правило")
        add_rule_btn.setObjectName("secondaryBtn")
        add_rule_btn.clicked.connect(self.add_rule)

        edit_rule_btn = QPushButton("✏️ Изменить")
        edit_rule_btn.setObjectName("secondaryBtn")
        edit_rule_btn.clicked.connect(self.edit_selected_rule)

        del_rule_btn = QPushButton("🗑️ Удалить")
        del_rule_btn.setObjectName("secondaryBtn")
        del_rule_btn.clicked.connect(self.delete_selected_rule)

        reset_rules_btn = QPushButton("🔄 Сбросить по умолчанию")
        reset_rules_btn.setObjectName("secondaryBtn")
        reset_rules_btn.clicked.connect(self.reset_rules_defaults)

        rules_btn_row.addWidget(add_rule_btn)
        rules_btn_row.addWidget(edit_rule_btn)
        rules_btn_row.addWidget(del_rule_btn)
        rules_btn_row.addStretch(1)
        rules_btn_row.addWidget(reset_rules_btn)
        routing_layout.addLayout(rules_btn_row)

        layout.addWidget(routing_group)

        # ----------------- БЛОК: СТАТУС СЕССИИ RUTRACKER -----------------
        session_group = QGroupBox("🔐 Авторизация RuTracker")
        session_layout = QVBoxLayout(session_group)
        session_label = QLabel(
            "Сессия подтягивается автоматически из браузера или из файла <b>.env</b>.<br>"
            "Если поиск работает — авторизация активна и куки найдены."
        )
        session_label.setTextFormat(Qt.TextFormat.RichText)
        session_label.setStyleSheet("color: #94a3b8;")
        session_layout.addWidget(session_label)
        layout.addWidget(session_group)

        # Кнопка сохранения
        save_btn = QPushButton("💾 Сохранить все настройки")
        save_btn.setObjectName("accentBtn")
        save_btn.setFixedHeight(42)
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)

        scroll.setWidget(content_widget)
        root_layout.addWidget(scroll)

    def load_settings(self):
        # Читаем сохраненные значения
        self.games_path_input.setText(
            self.settings.value("games_save_path", r"G:\games")
        )
        self.qb_url_input.setText(
            self.settings.value("qb_url", "http://localhost:1489")
        )
        self.steamcmd_input.setText(
            self.settings.value("steamcmd_path", r"G:\SteamCMD\steamcmd.exe")
        )
        self.mods_path_input.setText(
            self.settings.value("mods_folder", "")
        )

        # Загрузка правил сортировки загрузок
        self.routing_enabled_cb.setChecked(download_router.enabled)
        self.refresh_rules_table()

    def refresh_rules_table(self):
        """Заполняет таблицу правил сортировки файлов."""
        rules = download_router.get_all_rules()
        self.rules_table.setRowCount(len(rules))
        for row, (ext, folder) in enumerate(sorted(rules.items())):
            ext_item = QTableWidgetItem(ext)
            ext_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            folder_item = QTableWidgetItem(folder)
            self.rules_table.setItem(row, 0, ext_item)
            self.rules_table.setItem(row, 1, folder_item)

    def add_rule(self):
        dialog = RuleEditDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ext, folder = dialog.get_data()
            download_router.set_rule(ext, folder)
            self.refresh_rules_table()

    def edit_selected_rule(self):
        row = self.rules_table.currentRow()
        if row < 0 or row >= self.rules_table.rowCount():
            QMessageBox.information(self, "Выбор", "Выберите правило в таблице для редактирования")
            return
        ext_item = self.rules_table.item(row, 0)
        folder_item = self.rules_table.item(row, 1)
        ext = ext_item.text() if ext_item else ""
        folder = folder_item.text() if folder_item else ""

        dialog = RuleEditDialog(self, ext=ext, folder=folder)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_ext, new_folder = dialog.get_data()
            if new_ext != ext:
                download_router.remove_rule(ext)
            download_router.set_rule(new_ext, new_folder)
            self.refresh_rules_table()

    def delete_selected_rule(self):
        row = self.rules_table.currentRow()
        if row < 0 or row >= self.rules_table.rowCount():
            QMessageBox.information(self, "Выбор", "Выберите правило для удаления")
            return
        ext_item = self.rules_table.item(row, 0)
        if not ext_item:
            return
        ext = ext_item.text()
        reply = QMessageBox.question(
            self,
            "Удаление правила",
            f"Удалить правило сортировки для расширения '{ext}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            download_router.remove_rule(ext)
            self.refresh_rules_table()

    def reset_rules_defaults(self):
        reply = QMessageBox.question(
            self,
            "Сброс правил",
            "Сбросить все правила сортировки загрузок к значениям по умолчанию?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            download_router.reset_to_defaults()
            self.routing_enabled_cb.setChecked(True)
            self.refresh_rules_table()

    def save_settings(self):
        self.settings.setValue("games_save_path", self.games_path_input.text().strip())
        self.settings.setValue("qb_url", self.qb_url_input.text().strip())
        self.settings.setValue("steamcmd_path", self.steamcmd_input.text().strip())
        self.settings.setValue("mods_folder", self.mods_path_input.text().strip())

        # Сохранение состояния маршрутизатора
        download_router.enabled = self.routing_enabled_cb.isChecked()
        download_router.save()

        self.settings_saved.emit()
        QMessageBox.information(self, "Настройки сохранены", "Все параметры и правила сортировки успешно сохранены!")

    # ---------------- ОБЗОР ФАЙЛОВ И ПАПОК ----------------

    def select_games_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для скачивания игр",
            self.games_path_input.text()
        )
        if folder:
            self.games_path_input.setText(folder)

    def select_mods_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Выберите папку для модов",
            self.mods_path_input.text()
        )
        if folder:
            self.mods_path_input.setText(folder)

    def select_steamcmd(self):
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите steamcmd.exe",
            self.steamcmd_input.text(),
            "SteamCMD (steamcmd.exe);;Executable (*.exe)"
        )
        if filename:
            self.steamcmd_input.setText(filename)

    def open_folder(self, path):
        clean_path = path.strip()
        if clean_path and os.path.exists(clean_path):
            try:
                os.startfile(clean_path)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось открыть папку:\n{e}")
        else:
            QMessageBox.warning(self, "Предупреждение", f"Папка не найдена:\n{clean_path}")

    # ---------------- ПРОВЕРКИ ----------------

    def test_qb_connection(self):
        qb_url = self.qb_url_input.text().strip().rstrip("/")
        try:
            r = requests.get(f"{qb_url}/api/v2/app/version", timeout=3)
            if r.status_code == 200:
                QMessageBox.information(
                    self,
                    "Успех",
                    f"Подключение к qBittorrent успешно!\nВерсия клиента: {r.text}"
                )
            else:
                QMessageBox.warning(
                    self,
                    "Ошибка",
                    f"Ответ сервера с кодом {r.status_code}."
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Ошибка связи",
                f"Не удалось подключиться к {qb_url}:\n{e}\n\nУбедитесь, что qBittorrent запущен с включенным Web UI."
            )

    def test_steamcmd(self):
        cmd_path = self.steamcmd_input.text().strip()
        result = check_steamcmd(cmd_path)
        if result["success"]:
            QMessageBox.information(
                self,
                "Успех",
                f"SteamCMD найден и успешно отвечает!\nПуть: {cmd_path}"
            )
        else:
            QMessageBox.critical(
                self,
                "Ошибка",
                f"Не удалось запустить SteamCMD:\n{result.get('error') or result.get('stderr')}"
            )
