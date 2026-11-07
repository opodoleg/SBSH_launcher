import os
import sys

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Подключение модулей интерфейса
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from ui.downloads_tab import DownloadsTabWidget
from ui.games_tab import GamesTabWidget
from ui.mods_tab import ModsTabWidget
from ui.settings_tab import SettingsTabWidget
from ui.styles import DARK_THEME_QSS


class SBSHLauncher(QMainWindow):
    """Главное окно SBSH Launcher с единым интерфейсом для скачивания игр и модов."""

    def __init__(self):
        super().__init__()
        self.settings = QSettings("SBSH", "Launcher")

        self.setWindowTitle("SBSH Launcher — Игры и Моды")
        self.resize(1150, 750)
        self.setMinimumSize(950, 600)

        self._restore_geometry()
        self._setup_ui()
        self._setup_connections()

    def _setup_ui(self):
        # Применение современной темной темы
        self.setStyleSheet(DARK_THEME_QSS)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # --- Верхний брендовый заголовок ---
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 2, 4, 6)

        title_label = QLabel("🚀 <b>SBSH Launcher</b>")
        title_font = QFont("Segoe UI", 16)
        title_font.setBold(True)
        title_label.setFont(title_font)
        title_label.setStyleSheet("color: #38bdf8;")

        subtitle_label = QLabel("Единый центр скачивания игр и модов Steam")
        subtitle_label.setStyleSheet("color: #94a3b8; font-size: 12px; margin-left: 8px;")

        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        header_layout.addStretch(1)

        # Индикатор qBittorrent
        self.qb_status_badge = QLabel("● qBittorrent: Онлайн")
        self.qb_status_badge.setStyleSheet(
            "background-color: #164e63; color: #38bdf8; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: bold;"
        )
        header_layout.addWidget(self.qb_status_badge)

        main_layout.addLayout(header_layout)

        # --- Основной контейнер вкладок ---
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # 1. Вкладка скачивания игр
        self.games_tab = GamesTabWidget(get_save_path_callback=self.get_games_save_path)
        self.tabs.addTab(self.games_tab, "🎮  Скачивание игр")

        # 2. Вкладка мастерской модов Steam
        self.mods_tab = ModsTabWidget(
            get_steamcmd_path_callback=self.get_steamcmd_path,
            get_mods_folder_callback=self.get_mods_folder
        )
        self.tabs.addTab(self.mods_tab, "🔧  Мастерская модов")

        # 3. Вкладка загрузок qBittorrent
        self.downloads_tab = DownloadsTabWidget(get_qb_url_callback=self.get_qb_url)
        self.tabs.addTab(self.downloads_tab, "📥  Загрузки")

        # 4. Вкладка настроек
        self.settings_tab = SettingsTabWidget()
        self.tabs.addTab(self.settings_tab, "⚙️  Настройки")

        main_layout.addWidget(self.tabs)

        # --- Статус бар ---
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet("color: #64748b; font-size: 11px;")
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("SBSH Launcher готов к работе")

    def _setup_connections(self):
        # Когда торрент успешно добавлен, переключаем на вкладку загрузок и обновляем список
        self.games_tab.torrent_added.connect(self.on_torrent_added)

        # При сохранении настроек обновляем состояние
        self.settings_tab.settings_saved.connect(self.on_settings_saved)

    def on_torrent_added(self):
        self.status_bar.showMessage("Торрент добавлен! Переключение во вкладку загрузок...", 3000)
        self.tabs.setCurrentIndex(2)  # Вкладка загрузок
        self.downloads_tab.fetch_torrents()

    def on_settings_saved(self):
        self.status_bar.showMessage("Настройки успешно обновлены", 3000)
        self.downloads_tab.fetch_torrents()

    # ---------------- ГЕТТЕРЫ НАСТРОЕК ----------------

    def get_games_save_path(self):
        return self.settings.value("games_save_path", r"G:\games")

    def get_qb_url(self):
        return self.settings.value("qb_url", "http://localhost:1489")

    def get_steamcmd_path(self):
        return self.settings.value("steamcmd_path", r"G:\SteamCMD\steamcmd.exe")

    def get_mods_folder(self):
        return self.settings.value("mods_folder", "")

    # ---------------- СОХРАНЕНИЕ ГЕОМЕТРИИ И ВЫХОД ----------------

    def _restore_geometry(self):
        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event):
        self.settings.setValue("window_geometry", self.saveGeometry())
        # Безопасное завершение рабочих потоков
        if hasattr(self.mods_tab, "stop_workers"):
            self.mods_tab.stop_workers()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = SBSHLauncher()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
