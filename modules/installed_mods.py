import json
import os
import shutil
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


class InstalledModsManager(QObject):
    """
    Менеджер учёта установленных и скачанных модов (Steam Workshop & Nexus Mods).
    Хранит реестр в persistent JSON-файле (installed_mods.json), отслеживает наличие
    файлов на диске и уведомляет UI через сигналы Qt.
    """
    mods_updated = pyqtSignal()
    mod_installed = pyqtSignal(dict)
    mod_removed = pyqtSignal(str)

    def __init__(self, filepath: Optional[str] = None):
        super().__init__()
        if filepath is None:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            filepath = os.path.join(base_dir, "installed_mods.json")
        self.filepath = filepath
        self._lock = threading.RLock()
        self._mods: List[Dict[str, Any]] = []
        self.load()

    # =========================================================================
    # ЗАГРУЗКА И СОХРАНЕНИЕ
    # =========================================================================

    def load(self):
        """Загружает список установленных модов из JSON."""
        with self._lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            self._mods = data
                            return
                except Exception as e:
                    print(f"Ошибка загрузки installed_mods.json: {e}")
            self._mods = []

    def save(self):
        """Атомарно сохраняет список установленных модов в JSON."""
        with self._lock:
            temp_path = self.filepath + ".tmp"
            try:
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self._mods, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.filepath):
                    os.replace(temp_path, self.filepath)
                else:
                    os.rename(temp_path, self.filepath)
            except Exception as e:
                print(f"Ошибка сохранения installed_mods.json: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

    # =========================================================================
    # РЕГИСТРАЦИЯ И УДАЛЕНИЕ
    # =========================================================================

    def make_mod_key(self, source: str, mod_id: Any, file_id: Any = None) -> str:
        """Генерирует стабильный строковый ключ для поиска мода."""
        src = (source or "unknown").strip().lower()
        mid = str(mod_id or "").strip()
        fid = str(file_id or "").strip()
        if fid and fid != mid and fid != "0" and fid != "None":
            return f"{src}_{mid}_{fid}"
        return f"{src}_{mid}"

    def register_mod(
        self,
        source: str,
        mod_id: Any,
        title: str,
        install_path: str,
        game_name: str = "",
        game_id: Any = None,
        game_domain: str = "",
        file_id: Any = None,
        file_name: str = "",
        version: str = "",
        size_human: str = "—",
        is_collection: bool = False,
    ) -> Dict[str, Any]:
        """
        Регистрирует скачанный или установленный мод.
        Если мод уже был зарегистрирован, обновляет данные и путь.
        """
        with self._lock:
            key = self.make_mod_key(source, mod_id, file_id)
            clean_path = os.path.abspath(install_path) if install_path else ""
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            entry: Optional[Dict[str, Any]] = None
            for m in self._mods:
                if m.get("key") == key or (
                    m.get("source") == source and str(m.get("mod_id")) == str(mod_id)
                ):
                    entry = m
                    break

            if entry is not None:
                # Обновляем существующую запись
                if title:
                    entry["title"] = title
                if clean_path:
                    entry["install_path"] = clean_path
                if game_name:
                    entry["game_name"] = game_name
                if game_id:
                    entry["game_id"] = game_id
                if game_domain:
                    entry["game_domain"] = game_domain
                if file_id:
                    entry["file_id"] = file_id
                if file_name:
                    entry["file_name"] = file_name
                if version:
                    entry["version"] = version
                if size_human and size_human != "—":
                    entry["size_human"] = size_human
                entry["is_collection"] = is_collection
                entry["updated_at"] = now_str
            else:
                entry = {
                    "key": key,
                    "source": source,
                    "mod_id": mod_id,
                    "file_id": file_id,
                    "title": title or f"Мод #{mod_id}",
                    "game_name": game_name or "Неизвестная игра",
                    "game_id": game_id,
                    "game_domain": game_domain,
                    "version": version or "",
                    "size_human": size_human or "—",
                    "install_path": clean_path,
                    "file_name": file_name or (os.path.basename(clean_path) if clean_path else ""),
                    "is_collection": is_collection,
                    "installed_at": now_str,
                    "updated_at": now_str,
                }
                self._mods.insert(0, entry)

            self.save()

        self.mod_installed.emit(entry)
        self.mods_updated.emit()
        return entry

    def unregister_mod(self, key_or_id: str, delete_files: bool = False) -> Tuple[bool, str]:
        """
        Удаляет мод из реестра и опционально стирает файлы/папку с диска.
        Возвращает (успех, сообщение_об_ошибке_или_статусе).
        """
        with self._lock:
            found_idx = -1
            target_entry: Optional[Dict[str, Any]] = None

            for i, m in enumerate(self._mods):
                if m.get("key") == key_or_id or str(m.get("mod_id")) == str(key_or_id):
                    found_idx = i
                    target_entry = m
                    break

            if found_idx == -1 or not target_entry:
                return False, "Мод не найден в реестре установленных."

            path = target_entry.get("install_path")
            err_msg = ""
            if delete_files and path and os.path.exists(path):
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                except Exception as e:
                    err_msg = f"Не удалось удалить файлы с диска: {e}"

            self._mods.pop(found_idx)
            self.save()

        self.mod_removed.emit(str(key_or_id))
        self.mods_updated.emit()
        return (True, err_msg) if not err_msg else (False, err_msg)

    # ==========================================
    # ПРОВЕРКИ И ВЫБОРКА
    # ==========================================

    def is_installed(
        self,
        source: str,
        mod_id: Any,
        game_id: Any = None,
        check_disk: bool = True
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Проверяет, установлен ли данный мод.
        Возвращает (True, entry), если мод зарегистрирован и (при check_disk=True)
        файл/директория действительно существуют на диске.
        """
        if mod_id is None:
            return False, None

        mid_str = str(mod_id).strip()
        src_clean = (source or "").strip().lower()
        gid_str = str(game_id).strip() if game_id is not None else ""

        with self._lock:
            for m in self._mods:
                if m.get("source", "").lower() != src_clean:
                    continue
                if str(m.get("mod_id", "")).strip() != mid_str:
                    continue
                if gid_str and m.get("game_id") is not None:
                    if str(m.get("game_id")).strip() != gid_str:
                        continue

                path = m.get("install_path", "")
                if check_disk:
                    if path and os.path.exists(path):
                        return True, dict(m)
                    # Если путь на диске не существует, считаем не установленным
                    return False, dict(m)
                return True, dict(m)

        return False, None

    def get_all(
        self,
        source: Optional[str] = None,
        game_id: Optional[Any] = None,
        query: Optional[str] = None,
        only_existing_on_disk: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Возвращает список установленных модов с актуальным статусом на диске.
        """
        with self._lock:
            result = []
            src_filter = source.strip().lower() if source else None
            gid_filter = str(game_id).strip() if game_id is not None else None
            q_filter = query.strip().lower() if query else None

            for m in self._mods:
                if src_filter and m.get("source", "").lower() != src_filter:
                    continue
                if gid_filter and str(m.get("game_id", "")).strip() != gid_filter:
                    continue
                if q_filter:
                    title = m.get("title", "").lower()
                    gname = m.get("game_name", "").lower()
                    fname = m.get("file_name", "").lower()
                    if q_filter not in title and q_filter not in gname and q_filter not in fname:
                        continue

                item = dict(m)
                path = item.get("install_path", "")
                exists = bool(path and os.path.exists(path))
                item["exists_on_disk"] = exists

                if only_existing_on_disk and not exists:
                    continue

                result.append(item)

            return result

    def clean_missing(self) -> int:
        """Удаляет из реестра записи модов, файлы которых больше не существуют на диске."""
        with self._lock:
            initial_count = len(self._mods)
            self._mods = [m for m in self._mods if m.get("install_path") and os.path.exists(m["install_path"])]
            removed = initial_count - len(self._mods)
            if removed > 0:
                self.save()

        if removed > 0:
            self.mods_updated.emit()
        return removed

    def scan_directory(self, folder_path: str) -> int:
        """
        Сканирует папку модов и автоматически регистрирует найденные папки (Steam Workshop)
        и архивы (Nexus Mods), если они ещё не были зарегистрированы.
        """
        if not folder_path or not os.path.isdir(folder_path):
            return 0

        added = 0
        with self._lock:
            existing_paths = {os.path.abspath(m.get("install_path", "")) for m in self._mods if m.get("install_path")}

            try:
                for entry in os.scandir(folder_path):
                    full_p = os.path.abspath(entry.path)
                    if full_p in existing_paths:
                        continue

                    if entry.is_dir():
                        dirname = entry.name
                        # Проверяем, является ли папка числовым ID (Steam Workshop ID)
                        if dirname.isdigit():
                            mod_id = int(dirname)
                            self._mods.append({
                                "key": f"steam_{mod_id}",
                                "source": "steam",
                                "mod_id": mod_id,
                                "file_id": None,
                                "title": f"Steam Workshop Mod #{mod_id}",
                                "game_name": "Steam Игра",
                                "game_id": None,
                                "game_domain": "",
                                "version": "",
                                "size_human": "—",
                                "install_path": full_p,
                                "file_name": dirname,
                                "is_collection": False,
                                "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            })
                            existing_paths.add(full_p)
                            added += 1

                    elif entry.is_file():
                        fname = entry.name
                        lower = fname.lower()
                        if lower.endswith((".zip", ".7z", ".rar")):
                            # Попытка извлечь nexus ID из имени файла
                            self._mods.append({
                                "key": f"nexus_{fname}",
                                "source": "nexus",
                                "mod_id": fname,
                                "file_id": None,
                                "title": os.path.splitext(fname)[0],
                                "game_name": "Nexus Mods",
                                "game_id": None,
                                "game_domain": "",
                                "version": "",
                                "size_human": f"{entry.stat().st_size / (1024*1024):.1f} МБ",
                                "install_path": full_p,
                                "file_name": fname,
                                "is_collection": lower.endswith(".7z") and "rev" in lower,
                                "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            })
                            existing_paths.add(full_p)
                            added += 1

            except Exception as e:
                print(f"Ошибка сканирования папки модов: {e}")

            if added > 0:
                self.save()

        if added > 0:
            self.mods_updated.emit()
        return added


# Глобальный экземпляр менеджера установленных модов
installed_mods_manager = InstalledModsManager()
