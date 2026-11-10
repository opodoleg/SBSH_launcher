"""
Менеджер сохранённых мод-паков (Saved Mod Packs / Пользовательские коллекции модов).

Функционал:
1. Создание, редактирование, удаление и хранение кастомных мод-паков в mod_packs.json.
2. Проверка статуса скачивания каждого мода в паке (в каталоге загрузок mods и папках игры).
3. Расчёт числовых соотношений скачанных модов (например, "Скачано: 15 / 20").
4. Расчёт точных процентов завершённых и ожидающих загрузок ("75.0% Скачано • 25.0% Ожидает").
5. Real-time обновление при изменениях в очереди загрузок и дисковых реестрах.
6. Пакетная отправка недостающих модов на скачивание через конвейер дедупликации.
"""

import json
import os
import sys
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


def get_base_dir() -> str:
    """Возвращает базовую директорию лаунчера."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class ModPacksManager(QObject):
    """
    Потокобезопасный менеджер кастомных мод-паков с персистентным JSON-хранилищем
    и real-time отслеживанием статусов загрузки.
    """
    packs_updated = pyqtSignal()
    pack_status_changed = pyqtSignal(str)  # pack_id

    def __init__(self, filepath: Optional[str] = None):
        super().__init__()
        self.base_dir = get_base_dir()
        self.filepath = filepath or os.path.join(self.base_dir, "mod_packs.json")
        self._lock = threading.RLock()
        self._packs: List[Dict[str, Any]] = []

        self.load()
        self._connect_signals()

    def _connect_signals(self):
        """Подключается к событиям очереди загрузок и реестров модов для real-time обновления."""
        try:
            from modules.downloaded_mods import downloaded_mods_manager
            downloaded_mods_manager.mods_updated.connect(self._on_external_mods_updated)
        except Exception:
            pass

        try:
            from modules.installed_mods import installed_mods_manager
            installed_mods_manager.mods_updated.connect(self._on_external_mods_updated)
        except Exception:
            pass

        try:
            from modules.download_queue import get_download_queue_manager
            qm = get_download_queue_manager()
            qm.task_finished.connect(lambda tid, succ, path: self._on_external_mods_updated())
            qm.task_added.connect(lambda task: self._on_external_mods_updated())
            qm.active_count_changed.connect(lambda cnt: self._on_external_mods_updated())
        except Exception:
            pass

    def _on_external_mods_updated(self):
        """Реактивный вызов при изменении файлов на диске или в очереди."""
        self.packs_updated.emit()

    # =========================================================================
    # ЗАГРУЗКА И СОХРАНЕНИЕ В ФАЙЛ
    # =========================================================================

    def load(self):
        """Загружает список мод-паков из mod_packs.json."""
        with self._lock:
            self._packs = []
            if os.path.isfile(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        self._packs = data
                except Exception as e:
                    print(f"[ModPacksManager] Ошибка чтения {self.filepath}: {e}")

    def save(self):
        """Атомарно сохраняет список мод-паков в mod_packs.json."""
        with self._lock:
            temp_path = self.filepath + ".tmp"
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(self._packs, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.filepath):
                    os.replace(temp_path, self.filepath)
                else:
                    os.rename(temp_path, self.filepath)
            except Exception as e:
                print(f"[ModPacksManager] Ошибка сохранения {self.filepath}: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

    # =========================================================================
    # CRUD ОПЕРАЦИИ С МОД-ПАКАМИ
    # =========================================================================

    def get_all_packs(self) -> List[Dict[str, Any]]:
        """Возвращает копию всех мод-паков."""
        with self._lock:
            return [dict(p) for p in self._packs]

    def get_pack(self, pack_id: str) -> Optional[Dict[str, Any]]:
        """Возвращает мод-пак по его ID."""
        with self._lock:
            for p in self._packs:
                if p.get("id") == pack_id:
                    return dict(p)
            return None

    def create_pack(
        self,
        name: str,
        game_name: str = "Cyberpunk 2077",
        game_domain: str = "cyberpunk2077",
        game_id: Any = 3333,
        description: str = "",
        initial_mods: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Создаёт новый мод-пак и сохраняет его на диск."""
        with self._lock:
            pack_id = f"pack_{uuid.uuid4().hex[:10]}"
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_pack = {
                "id": pack_id,
                "name": (name or "Новый мод-пак").strip(),
                "game_name": game_name or "Cyberpunk 2077",
                "game_domain": game_domain or "cyberpunk2077",
                "game_id": game_id,
                "description": description.strip(),
                "created_at": now_str,
                "updated_at": now_str,
                "mods": list(initial_mods or []),
            }
            self._packs.insert(0, new_pack)
            self.save()

        self.packs_updated.emit()
        self.pack_status_changed.emit(pack_id)
        return new_pack

    def update_pack(
        self,
        pack_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        game_name: Optional[str] = None,
        game_domain: Optional[str] = None,
        game_id: Optional[Any] = None,
    ) -> bool:
        """Обновляет метаданные существующего мод-пака."""
        with self._lock:
            for p in self._packs:
                if p.get("id") == pack_id:
                    if name is not None:
                        p["name"] = name.strip()
                    if description is not None:
                        p["description"] = description.strip()
                    if game_name is not None:
                        p["game_name"] = game_name
                    if game_domain is not None:
                        p["game_domain"] = game_domain
                    if game_id is not None:
                        p["game_id"] = game_id
                    p["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self.save()
                    self.packs_updated.emit()
                    self.pack_status_changed.emit(pack_id)
                    return True
        return False

    def delete_pack(self, pack_id: str) -> bool:
        """Удаляет мод-пак из списка."""
        with self._lock:
            found_idx = -1
            for idx, p in enumerate(self._packs):
                if p.get("id") == pack_id:
                    found_idx = idx
                    break
            if found_idx != -1:
                self._packs.pop(found_idx)
                self.save()
                self.packs_updated.emit()
                return True
        return False

    # =========================================================================
    # УПРАВЛЕНИЕ МОДАМИ ВНУТРИ ПАКА
    # =========================================================================

    def add_mod_to_pack(self, pack_id: str, mod_info: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Добавляет мод в выбранный мод-пак с защитой от дублирования внутри пака.
        """
        with self._lock:
            target_pack = None
            for p in self._packs:
                if p.get("id") == pack_id:
                    target_pack = p
                    break

            if not target_pack:
                return False, "Мод-пак не найден."

            mods_list = target_pack.setdefault("mods", [])
            new_mid = str(mod_info.get("mod_id", "")).strip()
            new_title = (mod_info.get("title") or mod_info.get("name") or "").strip().lower()

            # Проверка, нет ли уже такого мода в паке
            for m in mods_list:
                existing_mid = str(m.get("mod_id", "")).strip()
                existing_title = (m.get("title") or m.get("name") or "").strip().lower()
                if (new_mid and existing_mid and new_mid == existing_mid) or (
                    new_title and existing_title and new_title == existing_title
                ):
                    return False, f"Мод «{mod_info.get('title', new_mid)}» уже добавлен в этот мод-пак."

            # Нормализация записи мода
            entry = {
                "mod_id": mod_info.get("mod_id"),
                "title": mod_info.get("title") or mod_info.get("name") or f"Мод #{new_mid}",
                "source": (mod_info.get("source") or "nexus").lower(),
                "file_id": mod_info.get("file_id"),
                "file_name": mod_info.get("file_name") or "",
                "version": mod_info.get("version") or "",
                "category": mod_info.get("category") or "",
                "game_domain": mod_info.get("game_domain") or target_pack.get("game_domain", ""),
                "game_id": mod_info.get("game_id") or target_pack.get("game_id", ""),
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            mods_list.append(entry)
            target_pack["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            self.save()

        self.packs_updated.emit()
        self.pack_status_changed.emit(pack_id)
        return True, "Мод успешно добавлен в мод-пак!"

    def remove_mod_from_pack(self, pack_id: str, mod_id: Any) -> bool:
        """Удаляет мод из пака по его mod_id."""
        with self._lock:
            for p in self._packs:
                if p.get("id") == pack_id:
                    mods = p.get("mods", [])
                    str_target = str(mod_id).strip()
                    filtered = [m for m in mods if str(m.get("mod_id")).strip() != str_target]
                    if len(filtered) != len(mods):
                        p["mods"] = filtered
                        p["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                        self.save()
                        self.packs_updated.emit()
                        self.pack_status_changed.emit(pack_id)
                        return True
        return False

    # =========================================================================
    # РАСЧЁТ СТАТУСОВ, СООТНОШЕНИЙ И ПРОЦЕНТОВ
    # =========================================================================

    def check_pack_status(
        self,
        pack_or_id: Any,
        game_dir: Optional[str] = None,
        mods_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Проверяет статус скачивания каждого мода в паке:
        - Ищет в локальной папке загрузок mods и игровых каталогах (archive/pc/mod, r6, mods, bin).
        - Подсчитывает числовое соотношение скачанных модов (например, "Скачано: 15 / 20").
        - Вычисляет точный процент завершённости и ожидания ("75.0% Скачано • 25.0% Ожидает").
        - Определяет, находится ли мод в активной очереди прямо сейчас.
        """
        if isinstance(pack_or_id, str):
            pack = self.get_pack(pack_or_id)
        elif isinstance(pack_or_id, dict):
            pack = pack_or_id
        else:
            pack = None

        if not pack:
            return {
                "pack_id": "",
                "total_mods": 0,
                "downloaded_count": 0,
                "pending_count": 0,
                "queued_count": 0,
                "downloaded_percent": 0.0,
                "pending_percent": 0.0,
                "ratio_str": "0 / 0",
                "percent_str": "0% Скачано • 0% Ожидает",
                "is_complete": False,
                "mods_status": [],
            }

        mods = pack.get("mods", [])
        total = len(mods)

        # Ленивые импорты для исключения циклов
        from modules.mod_pipeline import mod_download_pipeline
        from modules.download_queue import get_download_queue_manager

        queue_mgr = get_download_queue_manager()
        active_tasks = queue_mgr.get_all_tasks()

        downloaded_count = 0
        queued_count = 0
        mods_status: List[Dict[str, Any]] = []

        for m in mods:
            m_id = m.get("mod_id")
            f_id = m.get("file_id")
            title = m.get("title") or m.get("name") or f"Мод #{m_id}"
            source = (m.get("source") or "nexus").lower()

            # 1. Проверяем локальное наличие на диске (папка загрузок + папки игры)
            check_result = mod_download_pipeline.check_local_mod(
                remote_mod=m,
                game_dir=game_dir,
                mods_dir=mods_dir,
            )

            local_p = check_result.matched_path or ""
            is_downloaded = check_result.is_present and (
                check_result.local_size > 0 or (local_p and os.path.exists(local_p))
            )

            # 2. Проверяем состояние в текущей очереди загрузок
            is_queued = False
            task_state = ""
            for t in active_tasks:
                if str(t.mod_id) == str(m_id) and t.status not in ["completed", "cancelled", "error"]:
                    is_queued = True
                    task_state = t.status_text or t.status
                    break

            if is_downloaded:
                downloaded_count += 1
            elif is_queued:
                queued_count += 1

            status_item = {
                "mod_id": m_id,
                "file_id": f_id,
                "title": title,
                "source": source,
                "version": m.get("version") or check_result.local_version or "",
                "is_downloaded": is_downloaded,
                "is_queued": is_queued,
                "task_state": task_state,
                "local_path": local_p,
                "local_size": check_result.local_size,
                "matched_source": check_result.matched_source,
                "status_text": (
                    "✅ Скачан" if is_downloaded else
                    ("⏳ В очереди" if is_queued else "❌ Не скачан")
                ),
            }
            mods_status.append(status_item)

        pending_count = total - downloaded_count
        downloaded_pct = round((downloaded_count / total * 100.0), 1) if total > 0 else 0.0
        pending_pct = round((pending_count / total * 100.0), 1) if total > 0 else 0.0

        ratio_str = f"{downloaded_count} / {total}"
        percent_str = f"{downloaded_pct}% Скачано • {pending_pct}% Ожидает"

        return {
            "pack_id": pack.get("id", ""),
            "pack_name": pack.get("name", ""),
            "game_name": pack.get("game_name", ""),
            "total_mods": total,
            "downloaded_count": downloaded_count,
            "pending_count": pending_count,
            "queued_count": queued_count,
            "downloaded_percent": downloaded_pct,
            "pending_percent": pending_pct,
            "ratio_str": ratio_str,
            "percent_str": percent_str,
            "is_complete": (downloaded_count == total and total > 0),
            "mods_status": mods_status,
        }

    # =========================================================================
    # ПАКЕТНОЕ СКАЧИВАНИЕ НЕДОСТАЮЩИХ МОДОВ
    # =========================================================================

    def download_missing_pack_mods(
        self,
        pack_id: str,
        game_dir: Optional[str] = None,
        mods_dir: Optional[str] = None,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[int, int, str]:
        """
        Сканирует пак и автоматически ставит в очередь только отсутствующие или устаревшие моды.
        Возвращает: (added_count, skipped_count, message).
        """
        pack = self.get_pack(pack_id)
        if not pack:
            return 0, 0, "Мод-пак не найден."

        mods = pack.get("mods", [])
        if not mods:
            return 0, 0, "Мод-пак пуст. Добавьте моды перед скачиванием."

        status = self.check_pack_status(pack, game_dir=game_dir, mods_dir=mods_dir)
        missing_mods = []

        for m_info, m_status in zip(mods, status["mods_status"]):
            if not m_status["is_downloaded"] and not m_status["is_queued"]:
                missing_mods.append(m_info)

        if not missing_mods:
            return 0, status["downloaded_count"], "Все моды пака уже скачаны и актуальны!"

        from modules.download_queue import get_download_queue_manager
        queue_mgr = get_download_queue_manager()

        queued_added = 0
        for m in missing_mods:
            source = (m.get("source") or "nexus").lower()
            m_id = m.get("mod_id")
            title = m.get("title") or f"Мод #{m_id}"

            if source == "nexus":
                f_id = m.get("file_id")
                domain = m.get("game_domain") or pack.get("game_domain") or "cyberpunk2077"
                g_id = m.get("game_id") or pack.get("game_id") or 3333
                queue_mgr.add_nexus_task(
                    mod_id=m_id,
                    file_id=f_id,
                    mod_title=title,
                    game_domain=domain,
                    game_id=g_id,
                    version=m.get("version", ""),
                    file_name=m.get("file_name", ""),
                )
                queued_added += 1
            elif source == "steam":
                appid = m.get("game_id") or 0
                queue_mgr.add_steam_task(
                    published_file_id=m_id,
                    app_id=int(appid) if appid else 0,
                    title=title,
                )
                queued_added += 1

        msg = (
            f"Поставлено в очередь на скачивание: {queued_added} модов. "
            f"Уже было скачано: {status['downloaded_count']}."
        )
        if log_cb:
            log_cb(msg)

        self.packs_updated.emit()
        self.pack_status_changed.emit(pack_id)
        return queued_added, status["downloaded_count"], msg


# Глобальный синглтон менеджера мод-паков
mod_packs_manager = ModPacksManager()
