import os
import sys

from PyQt6.QtCore import QSettings, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Modl")))
from workshop import check_steamcmd
import requests


class SettingsTabWidget(QWidget):
    """Вкладка управления путями и параметрами подключения."""
    settings_saved = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.settings = QSettings("SBSH", "Launcher")
        self._setup_ui()
        self.load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
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

        # Папка для модов
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

        layout.addStretch(1)

        # Кнопка сохранения
        save_btn = QPushButton("💾 Сохранить все настройки")
        save_btn.setObjectName("accentBtn")
        save_btn.setFixedHeight(40)
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)

    def load_settings(self):
        # Читаем сохраненные значения или берем значения по умолчанию
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

    def save_settings(self):
        self.settings.setValue("games_save_path", self.games_path_input.text().strip())
        self.settings.setValue("qb_url", self.qb_url_input.text().strip())
        self.settings.setValue("steamcmd_path", self.steamcmd_input.text().strip())
        self.settings.setValue("mods_folder", self.mods_path_input.text().strip())

        self.settings_saved.emit()
        QMessageBox.information(self, "Настройки сохранены", "Все параметры успешно сохранены!")

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
