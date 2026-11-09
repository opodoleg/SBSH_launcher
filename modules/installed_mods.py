"""
Менеджер учёта установленных и скачанных модов (Steam Workshop & Nexus Mods).
Хранит реестр в persistent JSON-файле (installed_mods.json), отслеживает наличие
файлов на диске, выполняет глубокое сканирование игровых директорий (r6, bin, mods, archive/pc/mod)
и уведомляет UI через сигналы Qt.
"""

import json
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


def get_base_dir() -> str:
    """Возвращает базовую директорию лаунчера, корректно работающую как в скрипте, так и в .exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# Типы модов
MOD_TYPE_ARCHIVE = "Archive"          # .archive (archive/pc/mod)
MOD_TYPE_REDSCRIPT = "Redscript"      # .reds (r6/scripts)
MOD_TYPE_TWEAKXL = "TweakXL"          # .yaml, .yml, .tweak (r6/tweaks)
MOD_TYPE_CET = "CET (Lua)"            # init.lua (bin/x64/plugins/cyber_engine_tweaks/mods)
MOD_TYPE_REDMOD = "REDmod"            # info.json / archives (mods/<mod>)
MOD_TYPE_PLUGIN = "Plugin (DLL/ASI)"  # .asi, .dll (bin/x64/plugins)
MOD_TYPE_PAK = "Pak"                  # .pak
MOD_TYPE_WORKSHOP = "Steam Workshop"  # Steam Workshop items
MOD_TYPE_PACKAGE = "Архив (Zip/7z)"   # .zip, .7z, .rar
MOD_TYPE_UNKNOWN = "Мод"


# База данных популярных модов для распознавания по именам файлов/папок
KNOWN_NEXUS_MODS: Dict[str, Dict[str, Any]] = {
    # Cyber Engine Tweaks (CET)
    "cyberenginetweaks": {"mod_id": 107, "title": "Cyber Engine Tweaks", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_CET},
    "cet": {"mod_id": 107, "title": "Cyber Engine Tweaks", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_CET},

    # ArchiveXL
    "archivexl": {"mod_id": 4198, "title": "ArchiveXL", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_ARCHIVE},

    # TweakXL
    "tweakxl": {"mod_id": 4197, "title": "TweakXL", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_TWEAKXL},

    # redscript
    "redscript": {"mod_id": 1511, "title": "redscript", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_REDSCRIPT},

    # RED4ext
    "red4ext": {"mod_id": 2380, "title": "RED4ext", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_PLUGIN},

    # Codeware
    "codeware": {"mod_id": 7780, "title": "Codeware", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_PLUGIN},

    # Native Settings UI
    "nativesettingsui": {"mod_id": 3518, "title": "Native Settings UI", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_CET},
    "nativesettings": {"mod_id": 3518, "title": "Native Settings UI", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_CET},

    # Browser Extension
    "browserextension": {"mod_id": 10038, "title": "Browser Extension", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_CET},

    # cybercmd
    "cybercmd": {"mod_id": 5176, "title": "cybercmd", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_PLUGIN},

    # Equipment-EX
    "equipmentex": {"mod_id": 6943, "title": "Equipment-EX", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_ARCHIVE},

    # Input Loader
    "inputloader": {"mod_id": 4575, "title": "Input Loader", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_PLUGIN},

    # Mod Settings
    "modsettings": {"mod_id": 4885, "title": "Mod Settings", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_REDMOD},

    # Virtual Car Dealer
    "virtualcardealer": {"mod_id": 4454, "title": "Virtual Car Dealer", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_TWEAKXL},

    # Appearance Menu Mod (AMM)
    "appearancemenumod": {"mod_id": 790, "title": "Appearance Menu Mod", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_CET},
    "amm": {"mod_id": 790, "title": "Appearance Menu Mod", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_CET},

    # Let There Be Flight
    "lettherebeflight": {"mod_id": 5208, "title": "Let There Be Flight", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_PLUGIN},

    # RadioExt
    "radioext": {"mod_id": 4591, "title": "RadioExt", "game_domain": "cyberpunk2077", "game_id": 3333, "type": MOD_TYPE_PLUGIN},

    # The Witcher 3
    "scriptmerger": {"mod_id": 484, "title": "The Witcher 3 Mod Script Merger", "game_domain": "witcher3", "game_id": 952, "type": MOD_TYPE_PACKAGE},
    "communitypatch": {"mod_id": 3163, "title": "Community Patch - Base", "game_domain": "witcher3", "game_id": 952, "type": MOD_TYPE_PACKAGE},

    # Skyrim Special Edition
    "skse64": {"mod_id": 30379, "title": "Skyrim Script Extender (SKSE64)", "game_domain": "skyrimspecialedition", "game_id": 1704, "type": MOD_TYPE_PLUGIN},
    "skyui": {"mod_id": 12604, "title": "SkyUI", "game_domain": "skyrimspecialedition", "game_id": 1704, "type": MOD_TYPE_PACKAGE},
    "ussep": {"mod_id": 266, "title": "Unofficial Skyrim Special Edition Patch", "game_domain": "skyrimspecialedition", "game_id": 1704, "type": MOD_TYPE_PACKAGE},
}


def normalize_mod_key(name: str) -> str:
    """Приводит имя файла или папки к каноническому виду для сопоставления с базой."""
    if not name:
        return ""
    clean = re.sub(r"(\.archive|\.reds|\.lua|\.zip|\.7z|\.rar|\.dll|\.asi|\.pak|\.yaml|\.yml|\.tweak)$", "", name, flags=re.I)
    # Удаляем суффиксы версий вроде -v1.0.0, -107-1-32-0, _1.2
    clean = re.sub(r"[-_]v?\d+([\.-]\d+)*.*$", "", clean, flags=re.I)
    clean = re.sub(r"[^a-zA-Z0-9]", "", clean).lower()
    return clean


def format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "—"
    for unit in ["Б", "КБ", "МБ", "ГБ"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} ТБ"


class InstalledModsManager(QObject):
    """
    Менеджер учёта установленных и скачанных модов (Steam Workshop & Nexus Mods).
    Хранит реестр в persistent JSON-файле (installed_mods.json), отслеживает наличие
    файлов на диске и синхронизирует состояние при сканировании.
    """
    mods_updated = pyqtSignal()
    mod_installed = pyqtSignal(dict)
    mod_removed = pyqtSignal(str)

    def __init__(self, filepath: Optional[str] = None):
        super().__init__()
        if filepath is None:
            filepath = os.path.join(get_base_dir(), "installed_mods.json")
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
                    print(f"[InstalledModsManager] Ошибка загрузки installed_mods.json: {e}")
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
                print(f"[InstalledModsManager] Ошибка сохранения installed_mods.json: {e}")
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
        mod_type: str = MOD_TYPE_UNKNOWN,
        is_collection: bool = False,
    ) -> Dict[str, Any]:
        """
        Регистрирует скачанный или обнаруженный установленный мод.
        Если мод уже был зарегистрирован, обновляет данные, тип и путь.
        """
        with self._lock:
            key = self.make_mod_key(source, mod_id, file_id)
            clean_path = os.path.abspath(install_path) if install_path else ""
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            entry: Optional[Dict[str, Any]] = None
            for m in self._mods:
                if m.get("key") == key or (
                    m.get("source") == source and str(m.get("mod_id")) == str(mod_id)
                ) or (clean_path and m.get("install_path") == clean_path):
                    entry = m
                    break

            if entry is not None:
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
                if mod_type and mod_type != MOD_TYPE_UNKNOWN:
                    entry["mod_type"] = mod_type
                entry["is_collection"] = is_collection
                entry["updated_at"] = now_str
            else:
                entry = {
                    "key": key,
                    "source": source,
                    "mod_id": mod_id,
                    "file_id": file_id,
                    "title": title or f"Мод #{mod_id}",
                    "game_name": game_name or "Cyberpunk 2077",
                    "game_id": game_id,
                    "game_domain": game_domain,
                    "mod_type": mod_type,
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
        title: Optional[str] = None,
        check_disk: bool = True,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Проверяет, установлен ли данный мод.
        Проверяет совпадение по mod_id, а также по нормализованному названию (title).
        Возвращает (True, entry), если мод зарегистрирован и файл существует на диске.
        """
        mid_str = str(mod_id).strip() if mod_id is not None else ""
        src_clean = (source or "").strip().lower()
        gid_str = str(game_id).strip() if game_id is not None else ""
        norm_req_title = normalize_mod_key(title) if title else ""

        with self._lock:
            for m in self._mods:
                if src_clean and m.get("source", "").lower() != src_clean:
                    continue

                matched = False
                if mid_str and str(m.get("mod_id", "")).strip() == mid_str:
                    matched = True
                elif norm_req_title:
                    entry_title = normalize_mod_key(m.get("title", ""))
                    entry_fname = normalize_mod_key(m.get("file_name", ""))
                    if norm_req_title == entry_title or norm_req_title == entry_fname:
                        matched = True

                if not matched:
                    continue

                if gid_str and m.get("game_id") is not None:
                    if str(m.get("game_id")).strip() != gid_str:
                        continue

                path = m.get("install_path", "")
                if check_disk:
                    if path and os.path.exists(path):
                        return True, dict(m)
                    return False, dict(m)
                return True, dict(m)

        return False, None

    def get_all(
        self,
        source: Optional[str] = None,
        game_id: Optional[Any] = None,
        query: Optional[str] = None,
        only_existing_on_disk: bool = False,
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
                    mtype = m.get("mod_type", "").lower()
                    if (q_filter not in title and q_filter not in gname
                            and q_filter not in fname and q_filter not in mtype):
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

    def clean_duplicate_mods(
        self,
        dry_run: bool = False,
        retention_policy: str = "game_dir_first",
        custom_dirs: Optional[List[str]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        Выполняет комплексный поиск и автоматическую очистку дубликатов модов
        (archive/pc/mod, r6, bin, mods, downloads) с безопасным сохранением
        одного основного экземпляра и формированием детального отчёта.
        """
        from modules.duplicate_cleaner import duplicate_mod_cleaner
        return duplicate_mod_cleaner.execute_cleanup(
            dry_run=dry_run,
            retention_policy=retention_policy,
            custom_dirs=custom_dirs,
            log_callback=log_callback,
        )

    # =========================================================================
    # СКАНИРОВАНИЕ И СИНХРОНИЗАЦИЯ ИГРОВЫХ ДИРЕКТОРИЙ
    # =========================================================================

    def scan_game_root(
        self,
        game_dir: str,
        game_name: str = "Cyberpunk 2077",
        game_domain: str = "cyberpunk2077",
        game_id: int = 3333,
    ) -> int:
        """
        Глубокое сканирование корневой директории игры:
        - archive/pc/mod (*.archive)
        - r6/scripts (*.reds и подпапки)
        - r6/tweaks (*.yaml, *.yml, *.tweak)
        - bin/x64/plugins/cyber_engine_tweaks/mods (<папка>/init.lua)
        - bin/x64/plugins (*.asi, *.dll)
        - mods (<папка>/info.json или архивы REDmod)
        """
        if not game_dir or not os.path.isdir(game_dir):
            return 0

        game_root = os.path.abspath(game_dir)
        added_count = 0

        with self._lock:
            existing_paths = {os.path.abspath(m.get("install_path", "")) for m in self._mods if m.get("install_path")}

            # 1. Сканирование archive/pc/mod (*.archive)
            archive_dir = os.path.join(game_root, "archive", "pc", "mod")
            if os.path.isdir(archive_dir):
                for root, _, files in os.walk(archive_dir):
                    for f in files:
                        if f.lower().endswith(".archive"):
                            full_p = os.path.abspath(os.path.join(root, f))
                            if full_p in existing_paths:
                                continue
                            norm_k = normalize_mod_key(f)
                            known = KNOWN_NEXUS_MODS.get(norm_k)
                            m_id = known["mod_id"] if known else f
                            m_title = known["title"] if known else os.path.splitext(f)[0]
                            sz = format_size(os.path.getsize(full_p))

                            self.register_mod(
                                source="nexus" if known else "local",
                                mod_id=m_id,
                                title=m_title,
                                install_path=full_p,
                                game_name=game_name,
                                game_id=game_id,
                                game_domain=game_domain,
                                file_name=f,
                                size_human=sz,
                                mod_type=MOD_TYPE_ARCHIVE,
                            )
                            existing_paths.add(full_p)
                            added_count += 1

            # 2. Сканирование r6/scripts (redscript)
            r6_scripts_dir = os.path.join(game_root, "r6", "scripts")
            if os.path.isdir(r6_scripts_dir):
                for item in os.listdir(r6_scripts_dir):
                    item_p = os.path.abspath(os.path.join(r6_scripts_dir, item))
                    if item_p in existing_paths:
                        continue
                    if os.path.isdir(item_p):
                        # Папка скриптов
                        norm_k = normalize_mod_key(item)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else item
                        m_title = known["title"] if known else item

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=item_p,
                            game_name=game_name,
                            game_id=game_id,
                            game_domain=game_domain,
                            file_name=item,
                            mod_type=MOD_TYPE_REDSCRIPT,
                        )
                        existing_paths.add(item_p)
                        added_count += 1
                    elif item.lower().endswith(".reds"):
                        # Одиночный .reds файл
                        norm_k = normalize_mod_key(item)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else item
                        m_title = known["title"] if known else os.path.splitext(item)[0]
                        sz = format_size(os.path.getsize(item_p))

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=item_p,
                            game_name=game_name,
                            game_id=game_id,
                            game_domain=game_domain,
                            file_name=item,
                            size_human=sz,
                            mod_type=MOD_TYPE_REDSCRIPT,
                        )
                        existing_paths.add(item_p)
                        added_count += 1

            # 3. Сканирование r6/tweaks (TweakXL)
            r6_tweaks_dir = os.path.join(game_root, "r6", "tweaks")
            if os.path.isdir(r6_tweaks_dir):
                for item in os.listdir(r6_tweaks_dir):
                    item_p = os.path.abspath(os.path.join(r6_tweaks_dir, item))
                    if item_p in existing_paths:
                        continue
                    if os.path.isdir(item_p):
                        norm_k = normalize_mod_key(item)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else item
                        m_title = known["title"] if known else item

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=item_p,
                            game_name=game_name,
                            game_id=game_id,
                            game_domain=game_domain,
                            file_name=item,
                            mod_type=MOD_TYPE_TWEAKXL,
                        )
                        existing_paths.add(item_p)
                        added_count += 1
                    elif item.lower().endswith((".yaml", ".yml", ".tweak")):
                        norm_k = normalize_mod_key(item)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else item
                        m_title = known["title"] if known else os.path.splitext(item)[0]
                        sz = format_size(os.path.getsize(item_p))

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=item_p,
                            game_name=game_name,
                            game_id=game_id,
                            game_domain=game_domain,
                            file_name=item,
                            size_human=sz,
                            mod_type=MOD_TYPE_TWEAKXL,
                        )
                        existing_paths.add(item_p)
                        added_count += 1

            # 4. Сканирование bin/x64/plugins/cyber_engine_tweaks/mods (CET)
            cet_mods_dir = os.path.join(game_root, "bin", "x64", "plugins", "cyber_engine_tweaks", "mods")
            if os.path.isdir(cet_mods_dir):
                for item in os.listdir(cet_mods_dir):
                    mod_p = os.path.abspath(os.path.join(cet_mods_dir, item))
                    if os.path.isdir(mod_p) and mod_p not in existing_paths:
                        init_lua = os.path.join(mod_p, "init.lua")
                        if os.path.isfile(init_lua):
                            norm_k = normalize_mod_key(item)
                            known = KNOWN_NEXUS_MODS.get(norm_k)
                            m_id = known["mod_id"] if known else item
                            m_title = known["title"] if known else item

                            self.register_mod(
                                source="nexus" if known else "local",
                                mod_id=m_id,
                                title=m_title,
                                install_path=mod_p,
                                game_name=game_name,
                                game_id=game_id,
                                game_domain=game_domain,
                                file_name=item,
                                mod_type=MOD_TYPE_CET,
                            )
                            existing_paths.add(mod_p)
                            added_count += 1

            # 5. Сканирование bin/x64/plugins (*.asi, *.dll)
            plugins_dir = os.path.join(game_root, "bin", "x64", "plugins")
            if os.path.isdir(plugins_dir):
                for f in os.listdir(plugins_dir):
                    if f.lower().endswith((".dll", ".asi")):
                        full_p = os.path.abspath(os.path.join(plugins_dir, f))
                        if full_p in existing_paths:
                            continue
                        norm_k = normalize_mod_key(f)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else f
                        m_title = known["title"] if known else os.path.splitext(f)[0]
                        sz = format_size(os.path.getsize(full_p))

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=full_p,
                            game_name=game_name,
                            game_id=game_id,
                            game_domain=game_domain,
                            file_name=f,
                            size_human=sz,
                            mod_type=MOD_TYPE_PLUGIN,
                        )
                        existing_paths.add(full_p)
                        added_count += 1

            # 6. Сканирование mods/ (REDmod)
            redmod_dir = os.path.join(game_root, "mods")
            if os.path.isdir(redmod_dir):
                for item in os.listdir(redmod_dir):
                    mod_p = os.path.abspath(os.path.join(redmod_dir, item))
                    if os.path.isdir(mod_p) and mod_p not in existing_paths:
                        info_json = os.path.join(mod_p, "info.json")
                        title = item
                        version = ""
                        if os.path.isfile(info_json):
                            try:
                                with open(info_json, "r", encoding="utf-8") as jf:
                                    idata = json.load(jf)
                                    title = idata.get("name") or item
                                    version = idata.get("version", "")
                            except Exception:
                                pass

                        norm_k = normalize_mod_key(title)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else item
                        m_title = known["title"] if known else title

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=mod_p,
                            game_name=game_name,
                            game_id=game_id,
                            game_domain=game_domain,
                            file_name=item,
                            version=version,
                            mod_type=MOD_TYPE_REDMOD,
                        )
                        existing_paths.add(mod_p)
                        added_count += 1

        return added_count

    def scan_directory(self, folder_path: str) -> int:
        """
        Универсальное сканирование: определяет, является ли папка корнем игры
        или хранилищем модов, и выполняет соответствующий анализ.
        """
        if not folder_path or not os.path.isdir(folder_path):
            return 0

        clean_folder = os.path.abspath(folder_path)

        # Проверка: является ли папка корневой директорией игры (наличие r6, bin, mods, archive)
        is_game_root = any(
            os.path.isdir(os.path.join(clean_folder, sub))
            for sub in ["r6", "bin", "archive"]
        )
        if is_game_root:
            return self.scan_game_root(clean_folder)

        # Иначе сканируем как каталог загрузок/модов (с подпапками)
        added = 0
        with self._lock:
            existing_paths = {os.path.abspath(m.get("install_path", "")) for m in self._mods if m.get("install_path")}

            for root, dirs, files in os.walk(clean_folder):
                # Проверка папок Steam Workshop (числовые ID)
                for d in list(dirs):
                    full_d = os.path.abspath(os.path.join(root, d))
                    if d.isdigit() and full_d not in existing_paths:
                        mod_id = int(d)
                        self.register_mod(
                            source="steam",
                            mod_id=mod_id,
                            title=f"Steam Workshop Mod #{mod_id}",
                            install_path=full_d,
                            game_name="Steam Игра",
                            file_name=d,
                            mod_type=MOD_TYPE_WORKSHOP,
                        )
                        existing_paths.add(full_d)
                        added += 1

                # Проверка файлов модов (.zip, .7z, .rar, .archive, .pak)
                for f in files:
                    full_f = os.path.abspath(os.path.join(root, f))
                    if full_f in existing_paths:
                        continue
                    lower = f.lower()

                    if lower.endswith((".zip", ".7z", ".rar")):
                        norm_k = normalize_mod_key(f)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else f
                        m_title = known["title"] if known else os.path.splitext(f)[0]
                        sz = format_size(os.path.getsize(full_f))

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=full_f,
                            game_name="Nexus Mods",
                            file_name=f,
                            size_human=sz,
                            mod_type=MOD_TYPE_PACKAGE,
                            is_collection=lower.endswith(".7z") and "rev" in lower,
                        )
                        existing_paths.add(full_f)
                        added += 1

                    elif lower.endswith(".archive"):
                        norm_k = normalize_mod_key(f)
                        known = KNOWN_NEXUS_MODS.get(norm_k)
                        m_id = known["mod_id"] if known else f
                        m_title = known["title"] if known else os.path.splitext(f)[0]
                        sz = format_size(os.path.getsize(full_f))

                        self.register_mod(
                            source="nexus" if known else "local",
                            mod_id=m_id,
                            title=m_title,
                            install_path=full_f,
                            game_name="Cyberpunk 2077",
                            file_name=f,
                            size_human=sz,
                            mod_type=MOD_TYPE_ARCHIVE,
                        )
                        existing_paths.add(full_f)
                        added += 1

                    elif lower.endswith(".pak"):
                        m_title = os.path.splitext(f)[0]
                        sz = format_size(os.path.getsize(full_f))
                        self.register_mod(
                            source="local",
                            mod_id=f,
                            title=m_title,
                            install_path=full_f,
                            game_name="Игра",
                            file_name=f,
                            size_human=sz,
                            mod_type=MOD_TYPE_PAK,
                        )
                        existing_paths.add(full_f)
                        added += 1

        return added

    def scan_all_locations(self, mods_dir: str = "", games_dir: str = "") -> int:
        """
        Автоматическое комплексное сканирование всех сконфигурированных путей:
        - Папка модов (например, downloads/mods или пользовательская)
        - Папка игр (и поиск внутри неё папок вроде Cyberpunk 2077)
        - Стандартные игровые пути
        """
        total_added = 0

        # 1. Папка модов
        target_mods = mods_dir.strip() if mods_dir else os.path.join(get_base_dir(), "downloads", "mods")
        if os.path.isdir(target_mods):
            total_added += self.scan_directory(target_mods)

        # 2. Папка игр
        target_games = games_dir.strip() if games_dir else ""
        if target_games and os.path.isdir(target_games):
            # Проверяем, является ли сама папка игрой
            if any(os.path.isdir(os.path.join(target_games, s)) for s in ["r6", "bin", "archive"]):
                total_added += self.scan_game_root(target_games)
            else:
                # Сканируем подпапки с играми
                for d in os.listdir(target_games):
                    sub = os.path.join(target_games, d)
                    if os.path.isdir(sub):
                        if any(os.path.isdir(os.path.join(sub, s)) for s in ["r6", "bin", "archive"]):
                            total_added += self.scan_game_root(sub, game_name=d)

        # 3. Дополнительные известные расположения игр на диске
        known_search_paths = [
            r"G:\games\Cyberpunk 2077",
            r"C:\Games\Cyberpunk 2077",
            r"D:\Games\Cyberpunk 2077",
            r"C:\Program Files (x86)\Steam\steamapps\common\Cyberpunk 2077",
            r"D:\SteamLibrary\steamapps\common\Cyberpunk 2077",
            r"C:\GOG Games\Cyberpunk 2077",
        ]
        for p in known_search_paths:
            if os.path.isdir(p):
                total_added += self.scan_game_root(p, game_name="Cyberpunk 2077")

        return total_added


# Глобальный синглтон менеджера установленных модов
installed_mods_manager = InstalledModsManager()
