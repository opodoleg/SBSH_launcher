"""
Менеджер локальной базы и сканирования скачанных модов.
Управляет индексным файлом downloaded_mods.json в папке mods.
Обеспечивает обнаружение уже скачанных файлов, проверку размера (> 0 байт),
автоматическую очистку при ручном удалении файлов и блокировку дубликатов загрузок.
"""

import json
import os
import re
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


def get_mods_dir() -> str:
    """Возвращает абсолютный путь к папке mods в корне лаунчера."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    mods_path = os.path.join(base, "mods")
    os.makedirs(mods_path, exist_ok=True)
    return mods_path


class DownloadedModsManager(QObject):
    """
    Управляет индексом downloaded_mods.json внутри папки mods.
    """
    mods_updated = pyqtSignal()

    def __init__(self, mods_dir: Optional[str] = None):
        super().__init__()
        self.mods_dir = os.path.abspath(mods_dir) if mods_dir else get_mods_dir()
        self.index_file = os.path.join(self.mods_dir, "downloaded_mods.json")
        self._lock = threading.RLock()
        self._entries: List[Dict[str, Any]] = []

        # Загрузка и первичное сканирование папки
        self.load()
        self.scan_mods_folder()

    # =========================================================================
    # ЗАГРУЗКА И СОХРАНЕНИЕ ИНДЕКСА
    # =========================================================================

    def load(self):
        """Загружает downloaded_mods.json и удаляет записи, если файлы удалены."""
        with self._lock:
            self._entries = []
            if os.path.isfile(self.index_file):
                try:
                    with open(self.index_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        valid = []
                        for item in data:
                            p = item.get("filepath", "")
                            # Проверяем, что файл существует и его размер строго больше 0 байт
                            if p and os.path.isfile(p) and os.path.getsize(p) > 0:
                                valid.append(item)
                        self._entries = valid
                        if len(valid) != len(data):
                            self.save()
                        return
                except Exception as e:
                    print(f"[DownloadedModsManager] Ошибка чтения {self.index_file}: {e}")

    def save(self):
        """Атомарно сохраняет downloaded_mods.json."""
        with self._lock:
            temp_file = self.index_file + ".tmp"
            try:
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self._entries, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.index_file):
                    os.replace(temp_file, self.index_file)
                else:
                    os.rename(temp_file, self.index_file)
            except Exception as e:
                print(f"[DownloadedModsManager] Ошибка сохранения {self.index_file}: {e}")
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass

    # =========================================================================
    # ЗАПИСЬ И ПРОВЕРКА ЗАГРУЗКИ
    # =========================================================================

    def record_downloaded(
        self,
        source: str,
        mod_id: Any,
        filepath: str,
        file_id: Optional[Any] = None,
        game_id: Optional[Any] = None,
        game_domain: str = "cyberpunk2077",
        filename: str = "",
        version: str = "",
        title: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Регистрирует успешно скачанный мод в downloaded_mods.json.
        """
        clean_path = os.path.abspath(filepath)
        if not os.path.isfile(clean_path):
            return None
        sz = os.path.getsize(clean_path)
        if sz <= 0:
            return None

        fn = filename or os.path.basename(clean_path)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        with self._lock:
            entry: Optional[Dict[str, Any]] = None
            for e in self._entries:
                # Совпадение по пути или по (source, mod_id, file_id)
                if e.get("filepath") == clean_path:
                    entry = e
                    break
                if (file_id is not None and e.get("file_id") is not None
                        and str(e.get("file_id")) == str(file_id)
                        and e.get("source") == source):
                    entry = e
                    break

            if entry is not None:
                entry["filepath"] = clean_path
                entry["filename"] = fn
                entry["size_bytes"] = sz
                if version:
                    entry["version"] = version
                if title:
                    entry["title"] = title
                entry["downloaded_at"] = now_str
            else:
                entry = {
                    "source": source,
                    "game_id": game_id,
                    "game_domain": game_domain,
                    "mod_id": mod_id,
                    "file_id": file_id,
                    "filename": fn,
                    "version": version or "",
                    "title": title or fn,
                    "filepath": clean_path,
                    "size_bytes": sz,
                    "downloaded_at": now_str,
                }
                self._entries.insert(0, entry)

            self.save()

        self.mods_updated.emit()
        return entry

    # =========================================================================
    # ПРОВЕРКА НАЛИЧИЯ И БЛОКИРОВКА ДУБЛИКАТОВ
    # =========================================================================

    def is_downloaded(
        self,
        source: str = "nexus",
        mod_id: Optional[Any] = None,
        file_id: Optional[Any] = None,
        filename: Optional[str] = None,
        game_id: Optional[Any] = None,
        title: Optional[str] = None,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Проверяет, скачан ли данный файл в папку mods (или подпапки):
        - Сверяет file_id (версию файла Nexus)
        - Сверяет mod_id (или publishedfileid для Steam)
        - Сверяет точное имя файла
        - Гарантирует, что файл физически существует на диске и его размер > 0 байт.
        """
        fid_str = str(file_id).strip() if file_id is not None and str(file_id).strip() not in ["", "None", "0"] else None
        mid_str = str(mod_id).strip() if mod_id is not None and str(mod_id).strip() not in ["", "None", "0"] else None
        fn_clean = os.path.basename(filename.strip().lower()) if filename else None

        with self._lock:
            # 1. Поиск по базе downloaded_mods.json
            for e in self._entries:
                p = e.get("filepath", "")
                if not p or not os.path.isfile(p) or os.path.getsize(p) <= 0:
                    continue

                if e.get("source") != source:
                    continue

                # Проверка по file_id
                if fid_str and e.get("file_id") is not None and str(e.get("file_id")) == fid_str:
                    return True, dict(e)

                # Проверка по точной версии/имени файла
                if fn_clean and e.get("filename") and e.get("filename").lower() == fn_clean:
                    return True, dict(e)

                # Проверка по mod_id
                if mid_str and e.get("mod_id") is not None and str(e.get("mod_id")) == mid_str:
                    # Если передан file_id, но в записи file_id другой, не утверждаем, что это та же версия
                    if fid_str and e.get("file_id") is not None and str(e.get("file_id")) != fid_str:
                        pass
                    else:
                        return True, dict(e)

            # 2. Прямая проверка физических файлов в папке mods и её подпапках
            for root, _, files in os.walk(self.mods_dir):
                for f in files:
                    if f.endswith((".part", ".tmp")) or f == "downloaded_mods.json":
                        continue

                    full_p = os.path.abspath(os.path.join(root, f))
                    try:
                        sz = os.path.getsize(full_p)
                        if sz <= 0:
                            continue
                    except OSError:
                        continue

                    f_lower = f.lower()

                    # Проверка точного имени файла
                    if fn_clean and f_lower == fn_clean:
                        return True, {
                            "source": source,
                            "mod_id": mod_id,
                            "file_id": file_id,
                            "filename": f,
                            "filepath": full_p,
                            "size_bytes": sz,
                        }

                    # Проверка по file_id: суффикс _<file_id>.zip или nexus_<domain>_<file_id>.zip
                    if fid_str:
                        # Например, _103079.zip или _103079.rar
                        if f"_{fid_str}." in f_lower or f"_{fid_str}_" in f_lower:
                            return True, {
                                "source": source,
                                "mod_id": mod_id,
                                "file_id": int(fid_str) if fid_str.isdigit() else fid_str,
                                "filename": f,
                                "filepath": full_p,
                                "size_bytes": sz,
                            }

                    # Проверка по mod_id в имени файла: например "ArchiveXL 4198 1.28.6" или "-27020-"
                    if mid_str:
                        # Паттерн 1: пробел mod_id пробел
                        if f" {mid_str} " in f:
                            return True, {
                                "source": source,
                                "mod_id": int(mid_str) if mid_str.isdigit() else mid_str,
                                "file_id": file_id,
                                "filename": f,
                                "filepath": full_p,
                                "size_bytes": sz,
                            }
                        # Паттерн 2: -mod_id-
                        if f"-{mid_str}-" in f:
                            return True, {
                                "source": source,
                                "mod_id": int(mid_str) if mid_str.isdigit() else mid_str,
                                "file_id": file_id,
                                "filename": f,
                                "filepath": full_p,
                                "size_bytes": sz,
                            }

        return False, None

    # =========================================================================
    # СКАНИРОВАНИЕ ПАПКИ MODS
    # =========================================================================

    def scan_mods_folder(self) -> int:
        """
        Сканирует папку mods (и подпапки) для наполнения и валидации downloaded_mods.json.
        Удаляет записи, чьи файлы отсутствуют или имеют размер 0 байт.
        """
        if not os.path.isdir(self.mods_dir):
            return 0

        added = 0
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        with self._lock:
            # Сначала очистим записи, которых физически нет на диске
            pruned = []
            known_paths = set()
            for e in self._entries:
                p = e.get("filepath", "")
                if p and os.path.isfile(p) and os.path.getsize(p) > 0:
                    pruned.append(e)
                    known_paths.add(os.path.abspath(p))
            changed = len(pruned) != len(self._entries)
            self._entries = pruned

            # Регулярные выражения для парсинга названий файлов
            # Паттерн 1: Название <mod_id> <version> <дата/хэш>.ext
            p1 = re.compile(r"^(.+?)\s+(\d{2,8})\s+([0-9\.]+)\s+.*?\.(zip|7z|rar)$", re.IGNORECASE)
            # Паттерн 2: Название-<mod_id>-<version>-<timestamp>.ext
            p2 = re.compile(r"^(.+?)-(\d{2,8})-(.+?)\.(zip|7z|rar)$", re.IGNORECASE)
            # Паттерн 3: Название_<file_id>.ext
            p3 = re.compile(r"^(.+?)_(\d{4,9})\.(zip|7z|rar)$", re.IGNORECASE)
            # Паттерн 4: nexus_<domain>_<file_id>.ext
            p4 = re.compile(r"^nexus_([a-zA-Z0-9]+)_(\d+)\.(zip|7z|rar)$", re.IGNORECASE)

            for root, _, files in os.walk(self.mods_dir):
                for f in files:
                    if f.endswith((".part", ".tmp")) or f == "downloaded_mods.json":
                        continue

                    full_p = os.path.abspath(os.path.join(root, f))
                    if full_p in known_paths:
                        continue

                    try:
                        sz = os.path.getsize(full_p)
                        if sz <= 0:
                            continue
                    except OSError:
                        continue

                    title = os.path.splitext(f)[0]
                    mod_id = None
                    file_id = None
                    version = ""
                    source = "nexus"

                    m1 = p1.match(f)
                    if m1:
                        title = m1.group(1).strip()
                        mod_id = int(m1.group(2))
                        version = m1.group(3).strip()
                    else:
                        m2 = p2.match(f)
                        if m2:
                            title = m2.group(1).strip()
                            mod_id = int(m2.group(2))
                        else:
                            m3 = p3.match(f)
                            if m3:
                                title = m3.group(1).strip()
                                file_id = int(m3.group(2))
                            else:
                                m4 = p4.match(f)
                                if m4:
                                    file_id = int(m4.group(2))

                    entry = {
                        "source": source,
                        "game_id": 3333,
                        "game_domain": "cyberpunk2077",
                        "mod_id": mod_id if mod_id else f,
                        "file_id": file_id,
                        "filename": f,
                        "version": version,
                        "title": title,
                        "filepath": full_p,
                        "size_bytes": sz,
                        "downloaded_at": now_str,
                    }
                    self._entries.append(entry)
                    known_paths.add(full_p)
                    added += 1
                    changed = True

            if changed:
                self.save()

        if added > 0:
            self.mods_updated.emit()
        return added

    def get_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._entries)


# Глобальный синглтон менеджера скачанных модов
downloaded_mods_manager = DownloadedModsManager()
