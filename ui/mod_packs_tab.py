"""
Вкладка управления сохранёнными мод-паками (Saved Mod Packs / Пользовательские коллекции модов).

Функционал:
1. Создание, хранение, экспорт и импорт кастомных паков модов.
2. Real-time отслеживание статуса каждого мода в паке (папка загрузок mods и официальные папки игры).
3. Точный числовой расчёт соотношений скачивания (например, "Скачано: 15 / 20").
4. Расчёт точных процентов («75.0% Скачано • 25.0% Ожидает») и динамические прогресс-бары.
5. Интеллектуальное пакетное скачивание недостающих модов в один клик.
"""

import json
import os
import sys
import webbrowser
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from modules.mod_packs import mod_packs_manager
from modules.download_queue import get_download_queue_manager


# Пресеты популярных игр для быстрого выбора
PRESET_GAMES = [
    {"name": "Cyberpunk 2077", "domain": "cyberpunk2077", "game_id": 3333},
    {"name": "The Witcher 3", "domain": "witcher3", "game_id": 952},
    {"name": "Skyrim Special Edition", "domain": "skyrimspecialedition", "game_id": 1704},
    {"name": "Fallout 4", "domain": "fallout4", "game_id": 1151},
    {"name": "Starfield", "domain": "starfield", "game_id": 4187},
    {"name": "Baldur's Gate 3", "domain": "baldursgate3", "game_id": 3474},
]


class CreateModPackDialog(QDialog):
    """Диалог создания или редактирования мод-пака."""

    def __init__(self, parent=None, pack_data: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.pack_data = pack_data
        is_edit = pack_data is not None
        self.setWindowTitle("Редактировать мод-пак" if is_edit else "Создать новый мод-пак")
        self.resize(500, 320)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b1120;
                color: #f8fafc;
            }
            QLabel {
                color: #cbd5e1;
                font-size: 13px;
                font-weight: 500;
            }
            QLineEdit, QTextEdit, QComboBox {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                color: #f8fafc;
                padding: 8px;
                font-size: 13px;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus {
                border-color: #38bdf8;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(12)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Например: Cyberpunk 2077 — Графический пак 2026")

        self.game_combo = QComboBox()
        for g in PRESET_GAMES:
            self.game_combo.addItem(f"🎮 {g['name']}", g)
        self.game_combo.addItem("🌐 Другая игра / Кастомная", {"name": "Другая игра", "domain": "", "game_id": 0})

        self.desc_input = QTextEdit()
        self.desc_input.setPlaceholderText("Краткое описание сборки модов...")
        self.desc_input.setMaximumHeight(90)

        if is_edit and pack_data:
            self.name_input.setText(pack_data.get("name", ""))
            self.desc_input.setPlainText(pack_data.get("description", ""))
            # Попытка восстановить выбранную игру
            g_name = pack_data.get("game_name", "")
            for i in range(self.game_combo.count()):
                d = self.game_combo.itemData(i)
                if d and d.get("name") == g_name:
                    self.game_combo.setCurrentIndex(i)
                    break

        form.addRow("Название пака:", self.name_input)
        form.addRow("Игра:", self.game_combo)
        form.addRow("Описание:", self.desc_input)
        layout.addLayout(form)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(10)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Сохранить изменения" if is_edit else "➕ Создать мод-пак")
        save_btn.setObjectName("accentBtn")
        save_btn.clicked.connect(self._validate_and_accept)

        btns_row.addStretch(1)
        btns_row.addWidget(cancel_btn)
        btns_row.addWidget(save_btn)
        layout.addLayout(btns_row)

    def _validate_and_accept(self):
        if not self.name_input.text().strip():
            QMessageBox.warning(self, "Предупреждение", "Пожалуйста, введите название мод-пака.")
            return
        self.accept()

    def get_data(self) -> Dict[str, Any]:
        game_info = self.game_combo.currentData() or {}
        return {
            "name": self.name_input.text().strip(),
            "game_name": game_info.get("name", "Cyberpunk 2077"),
            "game_domain": game_info.get("domain", "cyberpunk2077"),
            "game_id": game_info.get("game_id", 3333),
            "description": self.desc_input.toPlainText().strip(),
        }


class AddModManualDialog(QDialog):
    """Диалог добавления мода в мод-пак вручную по ID или названию."""

    def __init__(self, pack_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Добавить мод в «{pack_name}»")
        self.resize(460, 260)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b1120;
                color: #f8fafc;
            }
            QLabel {
                color: #cbd5e1;
                font-size: 13px;
            }
            QLineEdit, QComboBox {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                color: #f8fafc;
                padding: 8px;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        form = QFormLayout()
        form.setSpacing(10)

        self.source_combo = QComboBox()
        self.source_combo.addItem("⚡ Nexus Mods", "nexus")
        self.source_combo.addItem("🌐 Steam Workshop", "steam")

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Например: ArchiveXL или Cyber Engine Tweaks")

        self.mod_id_input = QLineEdit()
        self.mod_id_input.setPlaceholderText("ID мода (например: 4198 для Nexus или 108600 для Steam)")

        self.file_id_input = QLineEdit()
        self.file_id_input.setPlaceholderText("Опционально (File ID с Nexus Mods)")

        form.addRow("Источник:", self.source_combo)
        form.addRow("Название мода:", self.title_input)
        form.addRow("ID мода:", self.mod_id_input)
        form.addRow("ID файла (Nexus):", self.file_id_input)
        layout.addLayout(form)

        btns_row = QHBoxLayout()
        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.clicked.connect(self.reject)

        add_btn = QPushButton("➕ Добавить")
        add_btn.setObjectName("accentBtn")
        add_btn.clicked.connect(self._validate)

        btns_row.addStretch(1)
        btns_row.addWidget(cancel_btn)
        btns_row.addWidget(add_btn)
        layout.addLayout(btns_row)

    def _validate(self):
        if not self.title_input.text().strip() and not self.mod_id_input.text().strip():
            QMessageBox.warning(self, "Ошибка", "Укажите название или ID мода.")
            return
        self.accept()

    def get_data(self) -> Dict[str, Any]:
        return {
            "source": self.source_combo.currentData(),
            "title": self.title_input.text().strip(),
            "mod_id": self.mod_id_input.text().strip(),
            "file_id": self.file_id_input.text().strip(),
        }


class ModPackCardItemWidget(QWidget):
    """Кастомный виджет карточки мод-пака в левом списке."""

    def __init__(self, pack_info: Dict[str, Any], status_info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.pack_id = pack_info.get("id", "")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # 1. Заголовок и игра
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.title_lbl = QLabel(pack_info.get("name", "Без названия"))
        self.title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #f8fafc;")

        self.game_badge = QLabel(pack_info.get("game_name", "Cyberpunk 2077"))
        self.game_badge.setStyleSheet(
            "background-color: #1e293b; color: #38bdf8; padding: 2px 8px; "
            "border-radius: 4px; font-size: 10px; font-weight: bold;"
        )

        top_row.addWidget(self.title_lbl, stretch=1)
        top_row.addWidget(self.game_badge)
        layout.addLayout(top_row)

        # 2. Динамический прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)

        # 3. Нижняя строка со статистикой и соотношением
        stats_row = QHBoxLayout()
        stats_row.setSpacing(8)

        self.ratio_lbl = QLabel()
        self.ratio_lbl.setStyleSheet("font-size: 11px; font-weight: bold; color: #38bdf8;")

        self.percent_lbl = QLabel()
        self.percent_lbl.setStyleSheet("font-size: 11px; color: #94a3b8;")

        self.status_badge = QLabel()
        self.status_badge.setStyleSheet("font-size: 10px; font-weight: bold; border-radius: 4px; padding: 1px 6px;")

        stats_row.addWidget(self.ratio_lbl)
        stats_row.addWidget(self.percent_lbl)
        stats_row.addStretch(1)
        stats_row.addWidget(self.status_badge)
        layout.addLayout(stats_row)

        self.update_status(status_info)

    def update_status(self, st: Dict[str, Any]):
        total = st.get("total_mods", 0)
        downloaded = st.get("downloaded_count", 0)
        pct = st.get("downloaded_percent", 0.0)
        pending = st.get("pending_count", 0)
        pending_pct = st.get("pending_percent", 0.0)

        self.progress_bar.setValue(int(pct))
        self.ratio_lbl.setText(f"📥 Скачано: {downloaded} / {total}")
        self.percent_lbl.setText(f"({pct}% • Ожидает: {pending})")

        if total == 0:
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: #1e293b;
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: #64748b;
                    border-radius: 4px;
                }
            """)
            self.status_badge.setText("Пустой пак")
            self.status_badge.setStyleSheet("background-color: #1e293b; color: #94a3b8;")
        elif downloaded == total:
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: #1e293b;
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #34d399);
                    border-radius: 4px;
                }
            """)
            self.status_badge.setText("✅ Полностью скачан")
            self.status_badge.setStyleSheet("background-color: #064e3b; color: #34d399;")
        else:
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    background-color: #1e293b;
                    border: none;
                    border-radius: 4px;
                }
                QProgressBar::chunk {
                    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
                    border-radius: 4px;
                }
            """)
            self.status_badge.setText(f"⚡ Докачать ({pending})")
            self.status_badge.setStyleSheet("background-color: #164e63; color: #38bdf8;")


class ModPacksTabWidget(QWidget):
    """
    Основная вкладка «📦 Мод-паки»:
    - Управление пользовательскими коллекциями модов
    - Real-time мониторинг статусов, числовых соотношений и процентов
    - Пакетное скачивание недостающих модов
    """

    def __init__(self, get_mods_folder_callback=None):
        super().__init__()
        self.get_mods_folder_callback = get_mods_folder_callback
        self.selected_pack_id: Optional[str] = None
        self._pack_cards_map: Dict[str, ModPackCardItemWidget] = {}

        self._setup_ui()
        self._connect_signals()
        self.refresh_packs_list()

    def _connect_signals(self):
        """Реактивная подписка на изменения менеджера и очереди загрузок."""
        mod_packs_manager.packs_updated.connect(self.on_packs_updated)
        mod_packs_manager.pack_status_changed.connect(self.on_single_pack_status_changed)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setSpacing(12)
        root_layout.setContentsMargins(16, 16, 16, 16)

        # =====================================================================
        # 1. ВЕРХНЯЯ ПАНЕЛЬ ДЕЙСТВИЙ
        # =====================================================================
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        header_title = QLabel("<b>📦 Мои мод-паки (Коллекции)</b>")
        header_title.setStyleSheet("font-size: 16px; color: #38bdf8; font-weight: bold;")

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔎 Поиск по названию пака, игре или модам...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filter)

        self.game_filter_combo = QComboBox()
        self.game_filter_combo.addItem("Все игры", "")
        for g in PRESET_GAMES:
            self.game_filter_combo.addItem(f"🎮 {g['name']}", g["name"])
        self.game_filter_combo.currentIndexChanged.connect(self._apply_filter)

        self.create_pack_btn = QPushButton("➕ Создать мод-пак")
        self.create_pack_btn.setObjectName("accentBtn")
        self.create_pack_btn.setToolTip("Создать новую кастомную коллекцию модов")
        self.create_pack_btn.clicked.connect(self.open_create_pack_dialog)

        self.import_btn = QPushButton("📥 Импорт")
        self.import_btn.setObjectName("secondaryBtn")
        self.import_btn.setToolTip("Импортировать мод-пак из JSON-файла")
        self.import_btn.clicked.connect(self.import_pack_from_json)

        self.export_btn = QPushButton("📤 Экспорт")
        self.export_btn.setObjectName("secondaryBtn")
        self.export_btn.setToolTip("Экспортировать выбранный мод-пак в JSON")
        self.export_btn.clicked.connect(self.export_pack_to_json)

        self.refresh_btn = QPushButton("🔄")
        self.refresh_btn.setObjectName("secondaryBtn")
        self.refresh_btn.setToolTip("Пересканировать наличие модов на диске и обновить статистику")
        self.refresh_btn.clicked.connect(self.refresh_packs_list)

        top_bar.addWidget(header_title)
        top_bar.addSpacing(10)
        top_bar.addWidget(self.search_input, stretch=2)
        top_bar.addWidget(self.game_filter_combo, stretch=1)
        top_bar.addWidget(self.create_pack_btn)
        top_bar.addWidget(self.import_btn)
        top_bar.addWidget(self.export_btn)
        top_bar.addWidget(self.refresh_btn)

        root_layout.addLayout(top_bar)

        # =====================================================================
        # 2. ОСНОВНОЙ СПЛИТТЕР (ЛЕВАЯ КОЛОНКА: СПИСОК ПАКОВ | ПРАВАЯ: ДЕТАЛИ)
        # =====================================================================
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Левая колонка: список карточек мод-паков ---
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.packs_count_lbl = QLabel("0 мод-паков")
        self.packs_count_lbl.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 500;")
        left_layout.addWidget(self.packs_count_lbl)

        self.packs_list = QListWidget()
        self.packs_list.setSpacing(6)
        self.packs_list.setStyleSheet("""
            QListWidget {
                background-color: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 4px;
            }
            QListWidget::item {
                background-color: #1e293b;
                border-radius: 6px;
                border: 1px solid #334155;
            }
            QListWidget::item:hover {
                background-color: #26334d;
                border-color: #475569;
            }
            QListWidget::item:selected {
                background-color: #1e3a5f;
                border: 1px solid #38bdf8;
            }
        """)
        self.packs_list.itemClicked.connect(self.on_pack_card_selected)
        left_layout.addWidget(self.packs_list)

        self.splitter.addWidget(left_widget)

        # --- Правая колонка: инспектор пака и таблица модов ---
        self.right_widget = QWidget()
        self.right_layout = QVBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(0, 0, 0, 0)
        self.right_layout.setSpacing(10)

        # Карточка заголовка выбранного пака
        self.details_header_box = QWidget()
        self.details_header_box.setStyleSheet("""
            background-color: #0f172a;
            border: 1px solid #1e293b;
            border-radius: 8px;
        """)
        d_layout = QVBoxLayout(self.details_header_box)
        d_layout.setContentsMargins(16, 14, 16, 14)
        d_layout.setSpacing(10)

        # Заголовок, игра, даты
        pack_title_row = QHBoxLayout()
        self.cur_pack_title = QLabel("<b>Выберите мод-пак из списка слева</b>")
        self.cur_pack_title.setStyleSheet("font-size: 18px; color: #f8fafc; font-weight: bold;")
        self.cur_pack_game = QLabel()
        self.cur_pack_game.setStyleSheet(
            "background-color: #1e293b; color: #38bdf8; padding: 3px 10px; "
            "border-radius: 6px; font-size: 11px; font-weight: bold;"
        )
        self.cur_pack_game.setVisible(False)

        pack_title_row.addWidget(self.cur_pack_title, stretch=1)
        pack_title_row.addWidget(self.cur_pack_game)
        d_layout.addLayout(pack_title_row)

        self.cur_pack_desc = QLabel("Здесь отображаются состав сборки, процент готовности и кнопки управления.")
        self.cur_pack_desc.setWordWrap(True)
        self.cur_pack_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        d_layout.addWidget(self.cur_pack_desc)

        # Визуальная шкала прогресса и статистика
        self.stats_container = QWidget()
        sc_layout = QVBoxLayout(self.stats_container)
        sc_layout.setContentsMargins(0, 4, 0, 0)
        sc_layout.setSpacing(8)

        # Большой прогресс-бар
        self.main_progress_bar = QProgressBar()
        self.main_progress_bar.setFixedHeight(12)
        self.main_progress_bar.setTextVisible(False)
        self.main_progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1e293b;
                border-radius: 6px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
                border-radius: 6px;
            }
        """)
        sc_layout.addWidget(self.main_progress_bar)

        # Карточки показателей (Всего / Скачано / Ожидает / Соотношение)
        badges_row = QHBoxLayout()
        badges_row.setSpacing(10)

        def _make_stat_badge(title: str, text: str, bg: str, fg: str):
            w = QWidget()
            w.setStyleSheet(f"background-color: {bg}; border-radius: 6px; border: 1px solid #334155;")
            vl = QVBoxLayout(w)
            vl.setContentsMargins(10, 6, 10, 6)
            vl.setSpacing(2)
            tl = QLabel(title)
            tl.setStyleSheet("color: #94a3b8; font-size: 10px; font-weight: 500;")
            vl_val = QLabel(text)
            vl_val.setStyleSheet(f"color: {fg}; font-size: 14px; font-weight: bold;")
            vl.addWidget(tl)
            vl.addWidget(vl_val)
            return w, vl_val

        self.stat_w_total, self.stat_total_val = _make_stat_badge("Всего модов", "0", "#1e293b", "#f8fafc")
        self.stat_w_dl, self.stat_dl_val = _make_stat_badge("Скачано", "0 (0%)", "#064e3b", "#34d399")
        self.stat_w_pend, self.stat_pend_val = _make_stat_badge("Ожидает", "0 (0%)", "#1e293b", "#f59e0b")
        self.stat_w_ratio, self.stat_ratio_val = _make_stat_badge("Соотношение", "0 / 0", "#1e293b", "#38bdf8")

        badges_row.addWidget(self.stat_w_total)
        badges_row.addWidget(self.stat_w_dl)
        badges_row.addWidget(self.stat_w_pend)
        badges_row.addWidget(self.stat_w_ratio)
        badges_row.addStretch(1)

        sc_layout.addLayout(badges_row)
        d_layout.addWidget(self.stats_container)
        self.stats_container.setVisible(False)

        # Кнопки действий над выбранным паком
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.download_pack_btn = QPushButton("⚡ Скачать все недостающие")
        self.download_pack_btn.setObjectName("accentBtn")
        self.download_pack_btn.setToolTip("Поставить в очередь скачивания только те моды, которых ещё нет на диске")
        self.download_pack_btn.clicked.connect(self.download_missing_for_current_pack)

        self.add_mod_btn = QPushButton("➕ Добавить мод")
        self.add_mod_btn.setObjectName("secondaryBtn")
        self.add_mod_btn.clicked.connect(self.open_add_mod_dialog)

        self.edit_pack_btn = QPushButton("✏️ Редактировать")
        self.edit_pack_btn.setObjectName("secondaryBtn")
        self.edit_pack_btn.clicked.connect(self.open_edit_pack_dialog)

        self.delete_pack_btn = QPushButton("🗑 Удалить пак")
        self.delete_pack_btn.setObjectName("secondaryBtn")
        self.delete_pack_btn.clicked.connect(self.delete_current_pack)

        actions_row.addWidget(self.download_pack_btn)
        actions_row.addWidget(self.add_mod_btn)
        actions_row.addWidget(self.edit_pack_btn)
        actions_row.addWidget(self.delete_pack_btn)
        actions_row.addStretch(1)

        d_layout.addLayout(actions_row)
        self.right_layout.addWidget(self.details_header_box)

        # Таблица модов в паке
        self.mods_table = QTableWidget()
        self.mods_table.setColumnCount(6)
        self.mods_table.setHorizontalHeaderLabels([
            "Статус", "Название мода", "Источник", "Версия", "Локальный путь", "Действия"
        ])
        self.mods_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mods_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.mods_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mods_table.verticalHeader().setVisible(False)
        self.mods_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.mods_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.mods_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.mods_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.right_layout.addWidget(self.mods_table)

        self.splitter.addWidget(self.right_widget)
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 6)

        root_layout.addWidget(self.splitter)

        # Начальное состояние правых кнопок
        self._set_details_enabled(False)

    def _set_details_enabled(self, enabled: bool):
        self.download_pack_btn.setEnabled(enabled)
        self.add_mod_btn.setEnabled(enabled)
        self.edit_pack_btn.setEnabled(enabled)
        self.delete_pack_btn.setEnabled(enabled)
        self.export_btn.setEnabled(enabled)

    # =========================================================================
    # ЗАПОЛНЕНИЕ СПИСКА И ОБНОВЛЕНИЕ СТАТИСТИКИ
    # =========================================================================

    def refresh_packs_list(self):
        """Полная перерисовка списка паков и перерасчёт их статусов."""
        all_packs = mod_packs_manager.get_all_packs()
        filter_text = self.search_input.text().strip().lower()
        filter_game = self.game_filter_combo.currentData() or ""

        self.packs_list.clear()
        self._pack_cards_map.clear()

        visible_count = 0
        for pack in all_packs:
            # Фильтрация
            if filter_game and pack.get("game_name") != filter_game:
                continue

            if filter_text:
                in_name = filter_text in pack.get("name", "").lower()
                in_desc = filter_text in pack.get("description", "").lower()
                in_game = filter_text in pack.get("game_name", "").lower()
                in_mods = any(
                    filter_text in (m.get("title", "") or "").lower() for m in pack.get("mods", [])
                )
                if not (in_name or in_desc or in_game or in_mods):
                    continue

            # Проверяем статус пака на диске
            status = mod_packs_manager.check_pack_status(pack)

            item = QListWidgetItem(self.packs_list)
            item.setData(Qt.ItemDataRole.UserRole, pack.get("id"))

            card_widget = ModPackCardItemWidget(pack, status)
            item.setSizeHint(card_widget.sizeHint())

            self.packs_list.addItem(item)
            self.packs_list.setItemWidget(item, card_widget)
            self._pack_cards_map[pack.get("id")] = card_widget
            visible_count += 1

        self.packs_count_lbl.setText(f"Найдено: {visible_count} из {len(all_packs)} мод-паков")

        # Если был выбран пак, восстанавливаем выбор
        if self.selected_pack_id and self.selected_pack_id in self._pack_cards_map:
            self._select_pack_by_id(self.selected_pack_id)
        elif visible_count > 0:
            self.packs_list.setCurrentRow(0)
            first_item = self.packs_list.item(0)
            if first_item:
                self.on_pack_card_selected(first_item)
        else:
            self._clear_details()

    def _select_pack_by_id(self, pack_id: str):
        for i in range(self.packs_list.count()):
            item = self.packs_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == pack_id:
                self.packs_list.setCurrentItem(item)
                self.on_pack_card_selected(item)
                break

    def on_pack_card_selected(self, item: QListWidgetItem):
        """Выбор карточки мод-пака пользователем."""
        if not item:
            return
        pack_id = item.data(Qt.ItemDataRole.UserRole)
        self.selected_pack_id = pack_id
        self.display_pack_details(pack_id)

    def display_pack_details(self, pack_id: str):
        """Отображает подробную информацию, таблицу модов и статистику для выбранного пака."""
        pack = mod_packs_manager.get_pack(pack_id)
        if not pack:
            self._clear_details()
            return

        self._set_details_enabled(True)
        self.stats_container.setVisible(True)
        self.cur_pack_game.setVisible(True)

        self.cur_pack_title.setText(f"<b>{pack.get('name', 'Без названия')}</b>")
        self.cur_pack_game.setText(pack.get("game_name", "Cyberpunk 2077"))
        desc = pack.get("description", "") or "Описание отсутствует."
        self.cur_pack_desc.setText(desc)

        # Расчёт статусов каждого мода
        status = mod_packs_manager.check_pack_status(pack)

        total = status["total_mods"]
        downloaded = status["downloaded_count"]
        pending = status["pending_count"]
        pct = status["downloaded_percent"]
        pending_pct = status["pending_percent"]
        ratio_str = status["ratio_str"]

        self.main_progress_bar.setValue(int(pct))
        if downloaded == total and total > 0:
            self.main_progress_bar.setStyleSheet("""
                QProgressBar { background-color: #1e293b; border-radius: 6px; }
                QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #10b981, stop:1 #34d399); border-radius: 6px; }
            """)
        else:
            self.main_progress_bar.setStyleSheet("""
                QProgressBar { background-color: #1e293b; border-radius: 6px; }
                QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8); border-radius: 6px; }
            """)

        self.stat_total_val.setText(str(total))
        self.stat_dl_val.setText(f"{downloaded} ({pct}%)")
        self.stat_pend_val.setText(f"{pending} ({pending_pct}%)")
        self.stat_ratio_val.setText(ratio_str)

        # Текст кнопки скачивания
        if pending > 0:
            self.download_pack_btn.setText(f"⚡ Скачать недостающие ({pending})")
            self.download_pack_btn.setEnabled(True)
        else:
            self.download_pack_btn.setText("✅ Все моды скачаны")
            self.download_pack_btn.setEnabled(False)

        # Обновляем виджет карточки в списке
        if pack_id in self._pack_cards_map:
            self._pack_cards_map[pack_id].update_status(status)

        # Заполняем таблицу модов
        self._populate_mods_table(pack, status)

    def _populate_mods_table(self, pack: Dict[str, Any], status: Dict[str, Any]):
        mods_status = status.get("mods_status", [])
        self.mods_table.setRowCount(0)

        for row_idx, item in enumerate(mods_status):
            self.mods_table.insertRow(row_idx)

            # 0. Статус
            st_text = item.get("status_text", "—")
            st_cell = QTableWidgetItem(st_text)
            st_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if item.get("is_downloaded"):
                st_cell.setForeground(QColor("#34d399"))  # зеленый
            elif item.get("is_queued"):
                st_cell.setForeground(QColor("#f59e0b"))  # оранжевый
            else:
                st_cell.setForeground(QColor("#ef4444"))  # красный
            self.mods_table.setItem(row_idx, 0, st_cell)

            # 1. Название мода
            title_cell = QTableWidgetItem(item.get("title", ""))
            title_cell.setToolTip(f"ID: {item.get('mod_id')} | File: {item.get('file_id')}")
            self.mods_table.setItem(row_idx, 1, title_cell)

            # 2. Источник
            src = item.get("source", "nexus").upper()
            src_cell = QTableWidgetItem("Nexus" if src == "NEXUS" else "Steam")
            src_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.mods_table.setItem(row_idx, 2, src_cell)

            # 3. Версия
            ver_cell = QTableWidgetItem(item.get("version", "—") or "—")
            ver_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.mods_table.setItem(row_idx, 3, ver_cell)

            # 4. Локальный путь
            path_str = item.get("local_path") or "Файл отсутствует на диске"
            path_cell = QTableWidgetItem(path_str)
            path_cell.setForeground(QColor("#94a3b8" if item.get("is_downloaded") else "#64748b"))
            path_cell.setToolTip(path_str)
            self.mods_table.setItem(row_idx, 4, path_cell)

            # 5. Кнопки действий для строки
            actions_widget = QWidget()
            h_layout = QHBoxLayout(actions_widget)
            h_layout.setContentsMargins(4, 2, 4, 2)
            h_layout.setSpacing(6)

            if not item.get("is_downloaded"):
                dl_btn = QPushButton("⚡ Скачать")
                dl_btn.setObjectName("accentBtn")
                dl_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
                mod_ref = dict(item)
                dl_btn.clicked.connect(lambda _, m=mod_ref: self.download_single_mod(pack, m))
                h_layout.addWidget(dl_btn)
            else:
                open_btn = QPushButton("📁 Папка")
                open_btn.setObjectName("secondaryBtn")
                open_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
                p_ref = item.get("local_path")
                open_btn.clicked.connect(lambda _, p=p_ref: self.open_file_in_explorer(p))
                h_layout.addWidget(open_btn)

            del_btn = QPushButton("🗑")
            del_btn.setObjectName("secondaryBtn")
            del_btn.setStyleSheet("padding: 2px 8px; font-size: 11px;")
            del_btn.setToolTip("Удалить мод из этого пака")
            m_id = item.get("mod_id")
            del_btn.clicked.connect(lambda _, mid=m_id: self.remove_mod_from_current_pack(mid))
            h_layout.addWidget(del_btn)

            self.mods_table.setCellWidget(row_idx, 5, actions_widget)

    def _clear_details(self):
        self.selected_pack_id = None
        self._set_details_enabled(False)
        self.stats_container.setVisible(False)
        self.cur_pack_game.setVisible(False)
        self.cur_pack_title.setText("<b>Выберите мод-пак из списка слева</b>")
        self.cur_pack_desc.setText("Здесь отображаются состав сборки, процент готовности и кнопки управления.")
        self.mods_table.setRowCount(0)

    def _apply_filter(self):
        self.refresh_packs_list()

    # =========================================================================
    # REAL-TIME ОБНОВЛЕНИЯ
    # =========================================================================

    def on_packs_updated(self):
        """Вызывается при изменении мод-паков или завершении загрузки модов."""
        if self.selected_pack_id:
            # Обновляем карточку в списке и детальную панель
            pack = mod_packs_manager.get_pack(self.selected_pack_id)
            if pack:
                status = mod_packs_manager.check_pack_status(pack)
                if self.selected_pack_id in self._pack_cards_map:
                    self._pack_cards_map[self.selected_pack_id].update_status(status)
                self.display_pack_details(self.selected_pack_id)
                return

        self.refresh_packs_list()

    def on_single_pack_status_changed(self, pack_id: str):
        if self.selected_pack_id == pack_id:
            self.display_pack_details(pack_id)

    # =========================================================================
    # ДЕЙСТВИЯ: СОЗДАНИЕ, РЕДАКТИРОВАНИЕ, СКАЧИВАНИЕ, ЭКСПОРТ
    # =========================================================================

    def open_create_pack_dialog(self):
        """Открывает диалог создания нового мод-пака."""
        dlg = CreateModPackDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            new_pack = mod_packs_manager.create_pack(
                name=data["name"],
                game_name=data["game_name"],
                game_domain=data["game_domain"],
                game_id=data["game_id"],
                description=data["description"],
            )
            self.selected_pack_id = new_pack["id"]
            self.refresh_packs_list()
            self._select_pack_by_id(new_pack["id"])

    def open_edit_pack_dialog(self):
        """Редактирование текущего выбранного пака."""
        if not self.selected_pack_id:
            return
        pack = mod_packs_manager.get_pack(self.selected_pack_id)
        if not pack:
            return
        dlg = CreateModPackDialog(self, pack_data=pack)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            mod_packs_manager.update_pack(
                self.selected_pack_id,
                name=data["name"],
                description=data["description"],
                game_name=data["game_name"],
                game_domain=data["game_domain"],
                game_id=data["game_id"],
            )
            self.refresh_packs_list()

    def delete_current_pack(self):
        """Удаление текущего мод-пака."""
        if not self.selected_pack_id:
            return
        pack = mod_packs_manager.get_pack(self.selected_pack_id)
        name = pack.get("name") if pack else "пак"
        confirm = QMessageBox.question(
            self,
            "Удаление мод-пака",
            f"Вы уверены, что хотите удалить мод-пак «{name}»?\n"
            f"(Файлы модов на диске останутся нетронутыми).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            mod_packs_manager.delete_pack(self.selected_pack_id)
            self.selected_pack_id = None
            self.refresh_packs_list()

    def open_add_mod_dialog(self):
        """Добавление мода в выбранный пак вручную."""
        if not self.selected_pack_id:
            return
        pack = mod_packs_manager.get_pack(self.selected_pack_id)
        if not pack:
            return
        dlg = AddModManualDialog(pack.get("name", "мод-пак"), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            success, msg = mod_packs_manager.add_mod_to_pack(self.selected_pack_id, data)
            if not success:
                QMessageBox.warning(self, "Внимание", msg)
            else:
                self.display_pack_details(self.selected_pack_id)

    def remove_mod_from_current_pack(self, mod_id: Any):
        """Удаляет конкретный мод из пака."""
        if not self.selected_pack_id:
            return
        mod_packs_manager.remove_mod_from_pack(self.selected_pack_id, mod_id)
        self.display_pack_details(self.selected_pack_id)

    def download_missing_for_current_pack(self):
        """Пакетное скачивание всех недостающих модов из выбранного пака."""
        if not self.selected_pack_id:
            return

        added, skipped, msg = mod_packs_manager.download_missing_pack_mods(self.selected_pack_id)
        if added > 0:
            QMessageBox.information(
                self,
                "Загрузка мод-пака",
                f"✅ {msg}\n\n"
                f"Вы можете отслеживать прогресс загрузки во вкладке «📥 Загрузки»."
            )
        else:
            QMessageBox.information(self, "Загрузка мод-пака", msg)

        self.display_pack_details(self.selected_pack_id)

    def download_single_mod(self, pack: Dict[str, Any], mod_info: Dict[str, Any]):
        """Скачивание отдельного мода из таблицы."""
        qm = get_download_queue_manager()
        source = (mod_info.get("source") or "nexus").lower()
        m_id = mod_info.get("mod_id")
        title = mod_info.get("title") or f"Мод #{m_id}"

        if source == "nexus":
            f_id = mod_info.get("file_id")
            domain = mod_info.get("game_domain") or pack.get("game_domain") or "cyberpunk2077"
            g_id = mod_info.get("game_id") or pack.get("game_id") or 3333
            qm.add_nexus_task(
                mod_id=m_id,
                file_id=f_id,
                mod_title=title,
                game_domain=domain,
                game_id=g_id,
                version=mod_info.get("version", ""),
            )
        elif source == "steam":
            appid = mod_info.get("game_id") or 0
            qm.add_steam_task(
                published_file_id=m_id,
                app_id=int(appid) if appid else 0,
                title=title,
            )

        QMessageBox.information(
            self,
            "Добавлено в очередь",
            f"Мод «{title}» добавлен в очередь загрузки!"
        )
        if self.selected_pack_id:
            self.display_pack_details(self.selected_pack_id)

    def open_file_in_explorer(self, filepath: str):
        """Открывает папку с файлом в проводнике Windows."""
        if not filepath:
            return
        clean_p = os.path.abspath(filepath)
        if os.path.exists(clean_p):
            try:
                folder = clean_p if os.path.isdir(clean_p) else os.path.dirname(clean_p)
                os.startfile(folder)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось открыть проводник: {e}")
        else:
            QMessageBox.warning(self, "Файл не найден", f"Файл не найден по пути:\n{clean_p}")

    def export_pack_to_json(self):
        """Экспорт выбранного мод-пака в JSON-файл."""
        if not self.selected_pack_id:
            return
        pack = mod_packs_manager.get_pack(self.selected_pack_id)
        if not pack:
            return

        def_name = f"{pack.get('name', 'mod_pack')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Экспортировать мод-пак в файл",
            def_name,
            "JSON Files (*.json)"
        )
        if path:
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(pack, f, ensure_ascii=False, indent=2)
                QMessageBox.information(self, "Экспорт завершён", f"Мод-пак успешно сохранён:\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось сохранить файл: {e}")

    def import_pack_from_json(self):
        """Импорт мод-пака из стороннего JSON-файла."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Импортировать мод-пак из JSON",
            "",
            "JSON Files (*.json)"
        )
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict) and "name" in data:
                    new_pack = mod_packs_manager.create_pack(
                        name=data.get("name", "Импортированный пак"),
                        game_name=data.get("game_name", "Cyberpunk 2077"),
                        game_domain=data.get("game_domain", "cyberpunk2077"),
                        game_id=data.get("game_id", 3333),
                        description=data.get("description", ""),
                        initial_mods=data.get("mods", []),
                    )
                    self.selected_pack_id = new_pack["id"]
                    self.refresh_packs_list()
                    self._select_pack_by_id(new_pack["id"])
                    QMessageBox.information(
                        self,
                        "Импорт завершён",
                        f"Мод-пак «{new_pack['name']}» успешно импортирован!\n"
                        f"Модов в паке: {len(new_pack.get('mods', []))}"
                    )
                else:
                    QMessageBox.warning(self, "Ошибка формата", "Некорректная структура JSON мод-пака.")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка импорта", f"Не удалось прочитать файл: {e}")



class AddCurrentModToPackDialog(QDialog):
    """Диалог быстрого добавления выбранного мода в один из сохранённых мод-паков."""

    def __init__(self, mod_info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.mod_info = mod_info
        title = mod_info.get("title") or mod_info.get("name") or "Мод"
        self.setWindowTitle("Добавить мод в мод-пак")
        self.resize(440, 220)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b1120;
                color: #f8fafc;
            }
            QLabel {
                color: #cbd5e1;
                font-size: 13px;
            }
            QComboBox, QLineEdit {
                background-color: #1e293b;
                border: 1px solid #334155;
                border-radius: 6px;
                color: #f8fafc;
                padding: 8px;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        header_lbl = QLabel(f"Добавить «<b>{title}</b>» в мод-пак:")
        header_lbl.setWordWrap(True)
        layout.addWidget(header_lbl)

        form = QFormLayout()
        self.pack_combo = QComboBox()
        self.refresh_packs_combo()
        form.addRow("Выберите пак:", self.pack_combo)
        layout.addLayout(form)

        btns_row = QHBoxLayout()
        btns_row.setSpacing(8)

        new_pack_btn = QPushButton("➕ Новый пак...")
        new_pack_btn.setObjectName("secondaryBtn")
        new_pack_btn.clicked.connect(self.create_new_pack_on_fly)

        cancel_btn = QPushButton("Отмена")
        cancel_btn.setObjectName("secondaryBtn")
        cancel_btn.clicked.connect(self.reject)

        add_btn = QPushButton("✓ Добавить")
        add_btn.setObjectName("accentBtn")
        add_btn.clicked.connect(self.accept_add)

        btns_row.addWidget(new_pack_btn)
        btns_row.addStretch(1)
        btns_row.addWidget(cancel_btn)
        btns_row.addWidget(add_btn)
        layout.addLayout(btns_row)

    def refresh_packs_combo(self):
        self.pack_combo.clear()
        all_packs = mod_packs_manager.get_all_packs()
        if not all_packs:
            self.pack_combo.addItem("— Нет сохранённых мод-паков —", None)
            return
        for p in all_packs:
            g_name = p.get("game_name", "")
            cnt = len(p.get("mods", []))
            self.pack_combo.addItem(f"📦 {p.get('name')} ({g_name}, {cnt} модов)", p.get("id"))

    def create_new_pack_on_fly(self):
        dlg = CreateModPackDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            new_p = mod_packs_manager.create_pack(
                name=data["name"],
                game_name=data["game_name"],
                game_domain=data["game_domain"],
                game_id=data["game_id"],
                description=data["description"],
            )
            self.refresh_packs_combo()
            for i in range(self.pack_combo.count()):
                if self.pack_combo.itemData(i) == new_p["id"]:
                    self.pack_combo.setCurrentIndex(i)
                    break

    def accept_add(self):
        pack_id = self.pack_combo.currentData()
        if not pack_id:
            QMessageBox.warning(self, "Предупреждение", "Сначала создайте или выберите мод-пак.")
            return

        success, msg = mod_packs_manager.add_mod_to_pack(pack_id, self.mod_info)
        if success:
            QMessageBox.information(self, "Успешно", msg)
            self.accept()
        else:
            QMessageBox.warning(self, "Внимание", msg)
