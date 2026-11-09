"""
Модуль маршрутизации и сортировки скачиваемых файлов по типам (расширениям).
Позволяет автоматически раскладывать загружаемые файлы по подпапкам
в зависимости от их расширения (например, .zip -> Archives, .archive -> Cyberpunk 2077/archive/pc/mod).
Хранит настройки в persistent JSON-файле (download_rules.json).
"""

import json
import os
import threading
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


DEFAULT_RULES: Dict[str, str] = {
    # Архивы
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".bz2": "Archives",
    ".xz": "Archives",
    ".tar.gz": "Archives",
    ".tar.bz2": "Archives",

    # Игровые архивы и моды
    ".archive": os.path.join("Cyberpunk 2077", "archive", "pc", "mod"),
    ".pak": "Paks",
    ".asi": "Plugins",
    ".dll": "Plugins",
    ".bin": "Binaries",

    # Программы и установщики
    ".exe": "Programs",
    ".msi": "Programs",

    # Видеофайлы
    ".mp4": "Videos",
    ".mkv": "Videos",
    ".avi": "Videos",
    ".webm": "Videos",

    # Изображения
    ".png": "Images",
    ".jpg": "Images",
    ".jpeg": "Images",
    ".webp": "Images",
    ".gif": "Images",

    # Документы и конфигурации
    ".pdf": "Documents",
    ".txt": "Documents",
    ".json": "Documents",
    ".xml": "Documents",
    ".ini": "Documents",
    ".yaml": "Documents",
    ".yml": "Documents",
}


class DownloadRouter(QObject):
    """
    Маршрутизатор загружаемых файлов по папкам на основе расширений.
    Потокобезопасен и синхронизируется через JSON-файл.
    """
    rules_changed = pyqtSignal()

    def __init__(self, filepath: Optional[str] = None):
        super().__init__()
        if filepath is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            filepath = os.path.join(base_dir, "download_rules.json")
        self.filepath = filepath
        self._lock = threading.RLock()
        self._enabled: bool = True
        self._rules: Dict[str, str] = {}
        self.load()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        with self._lock:
            self._enabled = bool(value)
            self.save()
        self.rules_changed.emit()

    def load(self):
        """Загружает правила из JSON или инициализирует значениями по умолчанию."""
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self._enabled = bool(data.get("enabled", True))
                            loaded_rules = data.get("rules", {})
                            if isinstance(loaded_rules, dict) and loaded_rules:
                                self._rules = {
                                    self._normalize_ext(k): str(v).strip()
                                    for k, v in loaded_rules.items()
                                }
                                return
                except Exception as e:
                    print(f"[DownloadRouter] Ошибка чтения {self.filepath}: {e}")

            # Инициализация по умолчанию
            self._enabled = True
            self._rules = dict(DEFAULT_RULES)
            self.save()

    def save(self):
        """Атомарно сохраняет правила в JSON."""
        with self._lock:
            payload = {
                "enabled": self._enabled,
                "rules": self._rules,
            }
            temp_path = self.filepath + ".tmp"
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.filepath):
                    os.replace(temp_path, self.filepath)
                else:
                    os.rename(temp_path, self.filepath)
            except Exception as e:
                print(f"[DownloadRouter] Ошибка сохранения {self.filepath}: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

    @staticmethod
    def _normalize_ext(ext: str) -> str:
        """Приводит расширение к виду .ext (в нижнем регистре с точкой впереди)."""
        clean = (ext or "").strip().lower()
        if clean and not clean.startswith("."):
            clean = "." + clean
        return clean

    @staticmethod
    def extract_extension(filename: str) -> str:
        """
        Извлекает расширение файла с поддержкой составных расширений (.tar.gz, .tar.bz2).
        """
        if not filename:
            return ""
        name_lower = os.path.basename(filename).lower()

        # Проверка составных расширений
        for compound in [".tar.gz", ".tar.bz2", ".tar.xz"]:
            if name_lower.endswith(compound):
                return compound

        _, ext = os.path.splitext(name_lower)
        return ext

    def get_destination_dir(
        self,
        base_dir: str,
        filename: str,
        game_domain: Optional[str] = None,
        auto_create: bool = True,
    ) -> str:
        """
        Определяет целевую папку для файла на основе его расширения.
        Если маршрутизация отключена или для расширения нет правила, возвращает base_dir.
        Если auto_create=True, автоматически создает папку на диске.
        """
        base = base_dir.strip() if base_dir else os.path.join(os.getcwd(), "downloads", "mods")

        with self._lock:
            if not self._enabled:
                if auto_create:
                    os.makedirs(base, exist_ok=True)
                return os.path.abspath(base)

            ext = self.extract_extension(filename)
            target_subfolder = self._rules.get(ext)

            if not target_subfolder:
                # Правило не найдено — сохраняем в базовую папку
                if auto_create:
                    os.makedirs(base, exist_ok=True)
                return os.path.abspath(base)

            # Если пользователь указал абсолютный путь в правиле
            if os.path.isabs(target_subfolder):
                dest = os.path.abspath(target_subfolder)
            else:
                dest = os.path.abspath(os.path.join(base, target_subfolder))

            if auto_create:
                try:
                    os.makedirs(dest, exist_ok=True)
                except Exception as e:
                    print(f"[DownloadRouter] Ошибка создания папки {dest}: {e}")
                    return os.path.abspath(base)

            return dest

    def get_all_rules(self) -> Dict[str, str]:
        """Возвращает копию текущих правил (расширение -> подпапка)."""
        with self._lock:
            return dict(self._rules)

    def set_all_rules(self, rules: Dict[str, str]):
        """Заменяет весь словарь правил и сохраняет."""
        with self._lock:
            self._rules = {
                self._normalize_ext(k): str(v).strip()
                for k, v in rules.items()
                if self._normalize_ext(k) and str(v).strip()
            }
            self.save()
        self.rules_changed.emit()

    def set_rule(self, extension: str, subfolder: str):
        """Добавляет или обновляет правило для указанного расширения."""
        norm_ext = self._normalize_ext(extension)
        sub = subfolder.strip()
        if not norm_ext or not sub:
            return
        with self._lock:
            self._rules[norm_ext] = sub
            self.save()
        self.rules_changed.emit()

    def remove_rule(self, extension: str) -> bool:
        """Удаляет правило для указанного расширения."""
        norm_ext = self._normalize_ext(extension)
        with self._lock:
            if norm_ext in self._rules:
                del self._rules[norm_ext]
                self.save()
                self.rules_changed.emit()
                return True
            return False

    def reset_to_defaults(self):
        """Сбрасывает все правила к значениям по умолчанию."""
        with self._lock:
            self._rules = dict(DEFAULT_RULES)
            self._enabled = True
            self.save()
        self.rules_changed.emit()


# Глобальный синглтон маршрутизатора загрузок
download_router = DownloadRouter()
