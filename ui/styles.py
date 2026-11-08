"""
Современная темная тема для SBSH Launcher в стиле Steam / игровой платформы.
"""

DARK_THEME_QSS = """
/* Общие настройки окна */
QWidget {
    background-color: #1b1c24;
    color: #e2e4f0;
    font-family: "Segoe UI", "SF Pro Display", -apple-system, sans-serif;
    font-size: 13px;
}

/* Вкладки QTabWidget */
QTabWidget::pane {
    border: 1px solid #2e303e;
    background-color: #1e1f29;
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    background-color: #262835;
    color: #9da3b8;
    padding: 10px 20px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
    font-size: 13px;
    border: 1px solid #2e303e;
    border-bottom: none;
}

QTabBar::tab:selected {
    background-color: #1e1f29;
    color: #38bdf8;
    border-top: 2px solid #38bdf8;
    border-bottom: 1px solid #1e1f29;
}

QTabBar::tab:hover:!selected {
    background-color: #2f3242;
    color: #e2e4f0;
}

/* Группировочные блоки (QGroupBox) */
QGroupBox {
    font-weight: bold;
    border: 1px solid #323545;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 18px;
    background-color: #22232e;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #38bdf8;
}

/* Поля ввода (QLineEdit) */
QLineEdit {
    background-color: #2a2c3a;
    border: 1px solid #3b3e52;
    border-radius: 6px;
    padding: 8px 12px;
    color: #f1f2f8;
    selection-background-color: #0284c7;
}

QLineEdit:focus {
    border: 1px solid #38bdf8;
    background-color: #303344;
}

QLineEdit:disabled {
    background-color: #1d1e27;
    color: #64748b;
    border: 1px solid #272834;
}

/* Выпадающие списки (QComboBox) */
QComboBox {
    background-color: #2a2c3a;
    border: 1px solid #3b3e52;
    border-radius: 6px;
    padding: 8px 12px;
    color: #f1f2f8;
}

QComboBox:hover {
    border: 1px solid #38bdf8;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border-left: 1px solid #3b3e52;
}

QComboBox QAbstractItemView {
    background-color: #22232e;
    border: 1px solid #3b3e52;
    selection-background-color: #0284c7;
    selection-color: #ffffff;
    color: #e2e4f0;
    padding: 4px;
    outline: none;
}

/* Списки (QListWidget, QTableWidget) */
QListWidget, QTableWidget {
    background-color: #22232e;
    border: 1px solid #323545;
    border-radius: 6px;
    padding: 6px;
    color: #e2e4f0;
    outline: none;
}

QListWidget::item {
    padding: 10px 12px;
    border-radius: 6px;
    margin-bottom: 2px;
}

QListWidget::item:hover {
    background-color: #2d3040;
}

QListWidget::item:selected {
    background-color: #0369a1;
    color: #ffffff;
}

/* Таблицы */
QTableWidget {
    gridline-color: #2d3040;
    selection-background-color: #0369a1;
    selection-color: #ffffff;
}

QHeaderView::section {
    background-color: #282a38;
    color: #94a3b8;
    padding: 8px 10px;
    border: none;
    border-right: 1px solid #323545;
    border-bottom: 1px solid #323545;
    font-weight: 600;
}

/* Кнопки (QPushButton) */
QPushButton {
    background-color: #0284c7;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #0369a1;
}

QPushButton:pressed {
    background-color: #075985;
}

QPushButton:disabled {
    background-color: #2a2c3a;
    color: #64748b;
}

/* Второстепенные кнопки */
QPushButton#secondaryBtn {
    background-color: #2a2c3a;
    color: #cbd5e1;
    border: 1px solid #3b3e52;
}

QPushButton#secondaryBtn:hover {
    background-color: #35384a;
    border: 1px solid #475569;
    color: #f8fafc;
}

QPushButton#secondaryBtn:pressed {
    background-color: #21222d;
}

/* Кнопка успешного действия / Загрузка */
QPushButton#accentBtn {
    background-color: #10b981;
    color: #ffffff;
}

QPushButton#accentBtn:hover {
    background-color: #059669;
}

/* Прогресс-бар */
QProgressBar {
    border: 1px solid #323545;
    border-radius: 5px;
    text-align: center;
    background-color: #22232e;
    color: #ffffff;
    font-weight: bold;
    font-size: 11px;
}

QProgressBar::chunk {
    background-color: #0ea5e9;
    border-radius: 4px;
}

/* Полосы прокрутки */
QScrollBar:vertical {
    border: none;
    background-color: #1b1c24;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background-color: #3b3e52;
    min-height: 25px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background-color: #4f536e;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background-color: #1b1c24;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background-color: #3b3e52;
    min-width: 25px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #4f536e;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* Разделители QSplitter */
QSplitter::handle {
    background-color: #2b2d3c;
    width: 6px;
    height: 6px;
    border-radius: 3px;
}

QSplitter::handle:hover {
    background-color: #38bdf8;
}

/* Текстовые поля и описание QTextBrowser */
QTextBrowser, QTextEdit {
    background-color: #171821;
    color: #cbd5e1;
    border: 1px solid #2e303e;
    border-radius: 6px;
    padding: 8px;
    font-size: 12px;
}
"""
