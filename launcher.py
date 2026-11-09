import os
import sys
import traceback

# Определение базовой директории (поддержка обычного запуска и распакованного PyInstaller)
BASE_DIR = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

modl_dir = os.path.join(BASE_DIR, "Modl")
if modl_dir not in sys.path:
    sys.path.insert(0, modl_dir)

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Подключение модулей интерфейса
from ui.downloads_tab import DownloadsTabWidget
from ui.favorites_tab import FavoritesTabWidget
from ui.games_tab import GamesTabWidget
from ui.mods_tab import ModsTabWidget
from ui.settings_tab import SettingsTabWidget
from ui.styles import DARK_THEME_QSS
from modules.download_queue import get_download_queue_manager


def handle_exception(exc_type, exc_value, exc_tb):
    """Глобальный обработчик необработанных исключений для GUI."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    err_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    
    # Запись в лог-файл рядом с exe или в корне проекта
    log_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
    log_path = os.path.join(log_dir, "error_launcher.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(err_text + "\n" + "=" * 50 + "\n")
    except Exception:
        pass

    # Показ диалогового окна с ошибкой
    if QApplication.instance():
        QMessageBox.critical(
            None,
            "Ошибка SBSH Launcher",
            f"Произошла непредвиденная ошибка:\n\n{exc_value}\n\nПодробности записаны в {log_path}"
        )
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = handle_exception


class SBSHLauncher(QMainWindow):
    """Главное окно SBSH Launcher с единым интерфейсом для скачивания игр и модов."""

    def __init__(self):
        super().__init__()
        self.settings = QSettings("SBSH", "Launcher")

        self.setWindowTitle("SBSH Launcher — Игры и Моды")
        self.resize(1150, 750)
        self.setMinimumSize(950, 600)

        # Установка иконки окна
        icon_path = os.path.join(BASE_DIR, "app_icon.ico")
        if os.path.isfile(icon_path):
            app_icon = QIcon(icon_path)
            self.setWindowIcon(app_icon)
            QApplication.setWindowIcon(app_icon)

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

        # 3. Вкладка избранных игр
        self.favorites_tab = FavoritesTabWidget(get_games_save_path_callback=self.get_games_save_path)
        self.tabs.addTab(self.favorites_tab, "⭐  Избранное")

        # 4. Вкладка загрузок (Моды и qBittorrent)
        self.downloads_tab = DownloadsTabWidget(
            get_qb_url_callback=self.get_qb_url,
            get_mods_folder_callback=self.get_mods_folder
        )
        self.tabs.addTab(self.downloads_tab, "📥  Загрузки")

        # 5. Вкладка настроек
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

        # Синхронизация избранного между вкладками
        self.games_tab.favorites_changed.connect(self.favorites_tab.refresh_list)
        self.favorites_tab.favorites_updated.connect(self.on_favorites_updated)

        # Переход из Избранного в моды или раздачи
        self.favorites_tab.open_mods_requested.connect(self.on_open_mods_for_favorite)
        self.favorites_tab.search_game_requested.connect(self.on_search_game_for_favorite)

        # При сохранении настроек обновляем состояние
        self.settings_tab.settings_saved.connect(self.on_settings_saved)

        # Индикатор количества активных загрузок модов в заголовке вкладки
        self.queue_manager = get_download_queue_manager()
        self.queue_manager.active_count_changed.connect(self._on_download_active_count_changed)

    def _on_download_active_count_changed(self, count: int):
        tab_idx = 3
        if count > 0:
            self.tabs.setTabText(tab_idx, f"📥  Загрузки ({count})")
        else:
            self.tabs.setTabText(tab_idx, "📥  Загрузки")

    def on_torrent_added(self):
        self.status_bar.showMessage("Торрент добавлен! Переключение во вкладку загрузок...", 3000)
        self.tabs.setCurrentIndex(3)  # Вкладка загрузок
        # Переключаем подвкладку на qBittorrent
        if hasattr(self.downloads_tab, "sub_tabs"):
            self.downloads_tab.sub_tabs.setCurrentIndex(1)
        self.downloads_tab.fetch_torrents()

    def on_open_mods_for_favorite(self, name, appid):
        self.status_bar.showMessage(f"Открытие модов для '{name}'...", 3000)
        self.tabs.setCurrentIndex(1)  # Вкладка модов
        self.mods_tab.select_game(name, appid)

    def on_search_game_for_favorite(self, name):
        self.status_bar.showMessage(f"Поиск раздач для '{name}'...", 3000)
        self.tabs.setCurrentIndex(0)  # Вкладка игр
        self.games_tab.set_search_query(name)

    def on_favorites_updated(self):
        fav_count = len(self.favorites_tab._filtered_favorites)
        self.tabs.setTabText(2, f"⭐  Избранное ({fav_count})")

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
        # Безопасное завершение рабочих потоков и гарантированное сохранение состояния
        if hasattr(self.mods_tab, "stop_workers"):
            self.mods_tab.stop_workers()
        queue_mgr = get_download_queue_manager()
        queue_mgr.save_history()
        queue_mgr.stop()
        try:
            from modules.installed_mods import installed_mods_manager
            installed_mods_manager.save()
        except Exception:
            pass
        event.accept()


def main():
    # Настройка идентификатора приложения в Windows для корректного отображения иконки в панели задач
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("sbsh.launcher.games.mods.1.0")
        except Exception:
            pass

    app = QApplication(sys.argv)
    
    # Применение иконки приложения
    icon_path = os.path.join(BASE_DIR, "app_icon.ico")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = SBSHLauncher()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
