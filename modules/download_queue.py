"""
Модуль менеджера очереди загрузок модов (DownloadQueueManager).
Обеспечивает последовательное скачивание модов (Nexus Mods и Steam Workshop)
на базе queue.Queue и фонового потока-воркера с потокобезопасным уведомлением GUI через сигналы Qt.
"""

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
import json

from PyQt6.QtCore import QObject, pyqtSignal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "Modl")))
from Nexus_api import NexusAPI, format_bytes


def get_base_dir() -> str:
    """Возвращает базовую директорию лаунчера, корректно работающую как в скрипте, так и в .exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# Статусы задач
STATE_QUEUED = "queued"
STATE_GENERATING_URL = "generating_url"
STATE_DOWNLOADING = "downloading"
STATE_COMPLETED = "completed"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

STATUS_LABELS = {
    STATE_QUEUED: "В очереди",
    STATE_GENERATING_URL: "Генерация ссылки...",
    STATE_DOWNLOADING: "Скачивание",
    STATE_COMPLETED: "Готово",
    STATE_ERROR: "Ошибка",
    STATE_CANCELLED: "Отменено",
}


@dataclass
class DownloadTask:
    task_id: int
    source: str  # "nexus" или "steam"
    mod_id: int | str
    title: str
    file_name: str
    size_human: str = "—"
    status: str = STATE_QUEUED
    status_text: str = STATUS_LABELS[STATE_QUEUED]
    progress: int = 0
    speed_str: str = "—"
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error_message: str = ""
    saved_path: str = ""

    # Параметры Nexus Mods
    file_id: Optional[int] = None
    game_id: Optional[int] = None
    game_domain: Optional[str] = None

    # Параметры Steam Workshop
    appid: Optional[int | str] = None
    steamcmd_path: Optional[str] = None

    # Общие параметры
    save_dir: str = ""
    created_at: float = field(default_factory=time.time)

    # Управление отменой
    is_cancelled: bool = False
    _cancel_cb: Optional[Callable[[], None]] = None


class DownloadQueueManager(QObject):
    """
    Менеджер очереди загрузок на базе queue.Queue и фонового воркера.
    Все обновления GUI транслируются через pyqtSignal strictly в главный поток.
    """
    task_added = pyqtSignal(object)                    # DownloadTask
    task_updated = pyqtSignal(object)                  # DownloadTask
    task_progress = pyqtSignal(int, int, int, str)     # task_id, downloaded, total, speed
    task_finished = pyqtSignal(int, bool, str)         # task_id, is_success, path_or_error
    queue_cleared = pyqtSignal()
    active_count_changed = pyqtSignal(int)             # количество незавершённых задач

    def __init__(self, nexus_api: Optional[NexusAPI] = None, history_file: Optional[str] = None):
        super().__init__()
        self._nexus_api = nexus_api
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._tasks: List[DownloadTask] = []
        self._tasks_by_id: Dict[int, DownloadTask] = {}
        self._next_id = 1
        self._current_task: Optional[DownloadTask] = None
        self._is_running = True
        self._history_file = history_file or os.path.join(get_base_dir(), "download_history.json")

        # Загрузка персистентной истории загрузок
        self._load_history()

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="DownloadQueueWorker"
        )
        self._worker_thread.start()

    def _load_history(self):
        """Загружает сохраненную историю задач из JSON-файла."""
        with self._lock:
            if not os.path.exists(self._history_file):
                return
            try:
                with open(self._history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    return

                max_id = 0
                for item in data:
                    tid = item.get("task_id", 0)
                    if tid > max_id:
                        max_id = tid

                    st = item.get("status", STATE_QUEUED)
                    st_text = item.get("status_text", STATUS_LABELS.get(st, st))

                    # Если задача была прервана при закрытии приложения
                    if st in [STATE_DOWNLOADING, STATE_GENERATING_URL]:
                        st = STATE_CANCELLED
                        st_text = "Приостановлено (готово к докачке)"

                    task = DownloadTask(
                        task_id=tid,
                        source=item.get("source", "nexus"),
                        mod_id=item.get("mod_id", 0),
                        title=item.get("title", ""),
                        file_name=item.get("file_name", ""),
                        size_human=item.get("size_human", "—"),
                        status=st,
                        status_text=st_text,
                        progress=item.get("progress", 0),
                        speed_str="—",
                        downloaded_bytes=item.get("downloaded_bytes", 0),
                        total_bytes=item.get("total_bytes", 0),
                        error_message=item.get("error_message", ""),
                        saved_path=item.get("saved_path", ""),
                        file_id=item.get("file_id"),
                        game_id=item.get("game_id"),
                        game_domain=item.get("game_domain"),
                        appid=item.get("appid"),
                        steamcmd_path=item.get("steamcmd_path"),
                        save_dir=item.get("save_dir", ""),
                        created_at=item.get("created_at", time.time()),
                        is_cancelled=(st == STATE_CANCELLED),
                    )
                    self._tasks.append(task)
                    self._tasks_by_id[tid] = task

                    # Возобновляем невыполненные задачи, стоявшие в очереди
                    if st == STATE_QUEUED:
                        self._queue.put(tid)

                self._next_id = max_id + 1
            except Exception as e:
                print(f"[DownloadQueueManager] Ошибка чтения {self._history_file}: {e}")

    def save_history(self):
        """Атомарно сохраняет всю историю и состояние задач в JSON-файл."""
        with self._lock:
            temp_path = self._history_file + ".tmp"
            try:
                serialized = []
                for t in self._tasks:
                    serialized.append({
                        "task_id": t.task_id,
                        "source": t.source,
                        "mod_id": t.mod_id,
                        "title": t.title,
                        "file_name": t.file_name,
                        "size_human": t.size_human,
                        "status": t.status,
                        "status_text": t.status_text,
                        "progress": t.progress,
                        "speed_str": "—",
                        "downloaded_bytes": t.downloaded_bytes,
                        "total_bytes": t.total_bytes,
                        "error_message": t.error_message,
                        "saved_path": t.saved_path,
                        "file_id": t.file_id,
                        "game_id": t.game_id,
                        "game_domain": t.game_domain,
                        "appid": t.appid,
                        "steamcmd_path": t.steamcmd_path,
                        "save_dir": t.save_dir,
                        "created_at": t.created_at,
                    })
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(serialized, f, ensure_ascii=False, indent=2)
                if os.path.exists(self._history_file):
                    os.replace(temp_path, self._history_file)
                else:
                    os.rename(temp_path, self._history_file)
            except Exception as e:
                print(f"[DownloadQueueManager] Ошибка сохранения {self._history_file}: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

    def _get_nexus_api(self) -> NexusAPI:
        if self._nexus_api is None:
            self._nexus_api = NexusAPI()
        return self._nexus_api

    def add_nexus_task(
        self,
        mod_id: int | str,
        file_id: int,
        title: str,
        file_name: str,
        game_id: int,
        game_domain: str,
        save_dir: str,
        size_human: str = "—",
    ) -> DownloadTask:
        """Добавляет файл Nexus Mods в очередь загрузки."""
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            task = DownloadTask(
                task_id=tid,
                source="nexus",
                mod_id=mod_id,
                file_id=file_id,
                title=title or f"Mod #{mod_id}",
                file_name=file_name or f"file_{file_id}.zip",
                size_human=size_human or "—",
                game_id=game_id,
                game_domain=game_domain,
                save_dir=save_dir,
                status=STATE_QUEUED,
                status_text=STATUS_LABELS[STATE_QUEUED],
            )
            self._tasks.append(task)
            self._tasks_by_id[tid] = task
            self._queue.put(tid)
            self.save_history()
            self.task_added.emit(task)
            self._notify_active_count()
            return task

    def add_steam_task(
        self,
        appid: int | str,
        mod_id: int | str,
        title: str,
        save_dir: str,
        steamcmd_path: str,
        size_human: str = "—",
    ) -> DownloadTask:
        """Добавляет мод Steam Workshop в очередь загрузки."""
        with self._lock:
            tid = self._next_id
            self._next_id += 1
            task = DownloadTask(
                task_id=tid,
                source="steam",
                appid=appid,
                mod_id=mod_id,
                title=title or f"Workshop #{mod_id}",
                file_name=f"Workshop #{mod_id}",
                size_human=size_human or "—",
                save_dir=save_dir,
                steamcmd_path=steamcmd_path,
                status=STATE_QUEUED,
                status_text=STATUS_LABELS[STATE_QUEUED],
            )
            self._tasks.append(task)
            self._tasks_by_id[tid] = task
            self._queue.put(tid)
            self.save_history()
            self.task_added.emit(task)
            self._notify_active_count()
            return task

    def cancel_task(self, task_id: int) -> bool:
        """Отменяет задачу, если она в очереди или выполняется сейчас."""
        with self._lock:
            task = self._tasks_by_id.get(task_id)
            if not task:
                return False
            if task.status in [STATE_COMPLETED, STATE_ERROR, STATE_CANCELLED]:
                return False

            task.is_cancelled = True
            task.status = STATE_CANCELLED
            task.status_text = STATUS_LABELS[STATE_CANCELLED]
            task.speed_str = "—"

            if task._cancel_cb:
                try:
                    task._cancel_cb()
                except Exception:
                    pass

            self.save_history()
            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, False, "Отменено пользователем")
            self._notify_active_count()
            return True

    def retry_task(self, task_id: int) -> bool:
        """Возобновляет (повторяет) задачу со статусом ошибки или отмены."""
        with self._lock:
            task = self._tasks_by_id.get(task_id)
            if not task:
                return False
            if task.status not in [STATE_ERROR, STATE_CANCELLED]:
                return False

            task.is_cancelled = False
            task.status = STATE_QUEUED
            task.status_text = STATUS_LABELS[STATE_QUEUED]
            task.error_message = ""
            task.speed_str = "—"

            self._queue.put(task_id)
            self.save_history()
            self.task_updated.emit(task)
            self._notify_active_count()
            return True

    def clear_completed(self):
        """Очищает из списка все завершённые, ошибочные и отменённые задачи."""
        with self._lock:
            self._tasks = [
                t for t in self._tasks
                if t.status in [STATE_QUEUED, STATE_GENERATING_URL, STATE_DOWNLOADING]
            ]
            self._tasks_by_id = {t.task_id: t for t in self._tasks}
            self.save_history()
            self.queue_cleared.emit()
            self._notify_active_count()

    def get_all_tasks(self) -> List[DownloadTask]:
        with self._lock:
            return list(self._tasks)

    def get_task(self, task_id: int) -> Optional[DownloadTask]:
        with self._lock:
            return self._tasks_by_id.get(task_id)

    def get_active_count(self) -> int:
        """Возвращает количество незавершённых задач в очереди."""
        with self._lock:
            return sum(
                1 for t in self._tasks
                if t.status in [STATE_QUEUED, STATE_GENERATING_URL, STATE_DOWNLOADING]
            )

    def _notify_active_count(self):
        count = self.get_active_count()
        self.active_count_changed.emit(count)

    def stop(self):
        """Завершает фоновый воркер."""
        self._is_running = False
        with self._lock:
            if self._current_task and not self._current_task.is_cancelled:
                self._current_task.is_cancelled = True
                if self._current_task._cancel_cb:
                    try:
                        self._current_task._cancel_cb()
                    except Exception:
                        pass
        self.save_history()

    # ==========================================
    # ВОРКЕР И ВЫПОЛНЕНИЕ ЗАДАЧ
    # ==========================================

    def _worker_loop(self):
        """Фоновый цикл: берёт следующую задачу и выполняет её до конца."""
        while self._is_running:
            try:
                task_id = self._queue.get(timeout=0.4)
            except queue.Empty:
                continue

            with self._lock:
                task = self._tasks_by_id.get(task_id)
                if not task or task.is_cancelled:
                    self._queue.task_done()
                    self._notify_active_count()
                    continue
                self._current_task = task

            try:
                if task.source == "nexus":
                    self._execute_nexus_task(task)
                elif task.source == "steam":
                    self._execute_steam_task(task)
            except Exception as e:
                if not task.is_cancelled:
                    task.status = STATE_ERROR
                    task.status_text = STATUS_LABELS[STATE_ERROR]
                    task.error_message = str(e)
                    task.speed_str = "—"
                    self.task_updated.emit(task)
                    self.task_finished.emit(task.task_id, False, str(e))
            finally:
                with self._lock:
                    self._current_task = None
                self._queue.task_done()
                self.save_history()
                self._notify_active_count()

    def _execute_nexus_task(self, task: DownloadTask):
        if task.is_cancelled or not self._is_running:
            return

        # 1. Этап генерации ссылки
        task.status = STATE_GENERATING_URL
        task.status_text = STATUS_LABELS[STATE_GENERATING_URL]
        self.task_updated.emit(task)

        # Пауза 2.5 секунды перед запросом GenerateDownloadUrl для защиты от спама и Cloudflare
        for _ in range(25):
            if task.is_cancelled or not self._is_running:
                task.status = STATE_CANCELLED
                task.status_text = STATUS_LABELS[STATE_CANCELLED]
                self.task_updated.emit(task)
                return
            time.sleep(0.1)

        api = self._get_nexus_api()

        def on_progress(dl: int, total: int, speed: str):
            if task.is_cancelled or not self._is_running:
                return
            task.downloaded_bytes = dl
            task.total_bytes = total
            task.speed_str = speed
            if total > 0:
                task.progress = min(100, int((dl / total) * 100))
                task.status_text = f"Скачивание ({task.progress}%)"
            else:
                task.status_text = "Скачивание..."
            task.status = STATE_DOWNLOADING
            self.task_progress.emit(task.task_id, dl, total, speed)
            self.task_updated.emit(task)

        task.status = STATE_DOWNLOADING
        task.status_text = "Подключение к Nexus Mods..."
        self.task_updated.emit(task)

        def on_status(st: str):
            if task.is_cancelled or not self._is_running:
                return
            task.status_text = st
            self.task_updated.emit(task)

        try:
            saved_path = api.download_file(
                file_id=task.file_id,
                game_id=task.game_id,
                mod_id=task.mod_id,
                game_domain=task.game_domain,
                save_dir=task.save_dir,
                progress_callback=on_progress,
                cancel_flag=lambda: task.is_cancelled or not self._is_running,
                initial_file_name=task.file_name,
                allow_resume=True,
                status_callback=on_status,
            )

            if task.is_cancelled or not self._is_running:
                task.status = STATE_CANCELLED
                task.status_text = STATUS_LABELS[STATE_CANCELLED]
                self.task_updated.emit(task)
                return

            task.status = STATE_COMPLETED
            task.status_text = STATUS_LABELS[STATE_COMPLETED]
            task.progress = 100
            task.speed_str = "—"
            task.saved_path = saved_path

            # Автоматическая регистрация в памяти установленных модов
            try:
                from modules.installed_mods import installed_mods_manager
                installed_mods_manager.register_mod(
                    source="nexus",
                    mod_id=task.mod_id,
                    title=task.title,
                    install_path=saved_path,
                    game_id=task.game_id,
                    game_domain=task.game_domain or "",
                    file_id=task.file_id,
                    file_name=task.file_name or os.path.basename(saved_path),
                    size_human=task.size_human,
                )
            except Exception as reg_err:
                print(f"Ошибка регистрации установленного мода Nexus: {reg_err}")

            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, True, saved_path)

        except Exception as e:
            if task.is_cancelled or not self._is_running:
                task.status = STATE_CANCELLED
                task.status_text = STATUS_LABELS[STATE_CANCELLED]
                self.task_updated.emit(task)
            else:
                err_msg = str(e)
                task.status = STATE_ERROR
                task.status_text = STATUS_LABELS[STATE_ERROR]
                task.error_message = err_msg
                task.speed_str = "—"
                self.task_updated.emit(task)
                self.task_finished.emit(task.task_id, False, err_msg)

    def _execute_steam_task(self, task: DownloadTask):
        if task.is_cancelled or not self._is_running:
            return

        task.status = STATE_DOWNLOADING
        task.status_text = "Запуск SteamCMD..."
        self.task_updated.emit(task)

        steamcmd_path = task.steamcmd_path
        if not steamcmd_path or not os.path.isfile(steamcmd_path):
            task.status = STATE_ERROR
            task.status_text = "Ошибка: SteamCMD не найден"
            task.error_message = f"Файл {steamcmd_path} не существует"
            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, False, task.error_message)
            return

        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        cmd = [
            steamcmd_path,
            "+login", "anonymous",
            "+workshop_download_item",
            str(task.appid),
            str(task.mod_id),
            "+quit",
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=flags,
            )
        except Exception as e:
            task.status = STATE_ERROR
            task.status_text = "Ошибка запуска SteamCMD"
            task.error_message = str(e)
            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, False, str(e))
            return

        task._cancel_cb = lambda: proc.terminate()

        if proc.stdout:
            for line in proc.stdout:
                if task.is_cancelled or not self._is_running:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    task.status = STATE_CANCELLED
                    task.status_text = STATUS_LABELS[STATE_CANCELLED]
                    self.task_updated.emit(task)
                    return

                clean = line.strip()
                if clean:
                    # Извлечение прогресса, если SteamCMD выводит проценты
                    pct_match = re.search(r"(\d{1,3})\s*%", clean)
                    if pct_match:
                        try:
                            val = int(pct_match.group(1))
                            task.progress = min(100, max(0, val))
                        except Exception:
                            pass
                    task.status_text = f"SteamCMD: {clean[:45]}"
                    self.task_updated.emit(task)

        proc.wait()
        task._cancel_cb = None

        if task.is_cancelled or not self._is_running:
            task.status = STATE_CANCELLED
            task.status_text = STATUS_LABELS[STATE_CANCELLED]
            self.task_updated.emit(task)
            return

        if proc.returncode != 0:
            task.status = STATE_ERROR
            task.status_text = f"Ошибка SteamCMD ({proc.returncode})"
            task.error_message = f"SteamCMD завершился с кодом {proc.returncode}"
            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, False, task.error_message)
            return

        # Копирование из каталога SteamCMD в task.save_dir
        source_dir = os.path.join(
            os.path.dirname(steamcmd_path),
            "steamapps", "workshop", "content",
            str(task.appid), str(task.mod_id)
        )
        if not os.path.exists(source_dir):
            task.status = STATE_ERROR
            task.status_text = "Ошибка: папка мода не найдена"
            task.error_message = f"Каталог не найден: {source_dir} (анонимное скачивание может быть запрещено разработчиком)"
            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, False, task.error_message)
            return

        dest_dir = os.path.join(task.save_dir, str(task.mod_id)) if task.save_dir else source_dir
        try:
            if task.save_dir:
                os.makedirs(task.save_dir, exist_ok=True)
                shutil.copytree(source_dir, dest_dir, dirs_exist_ok=True)
            task.status = STATE_COMPLETED
            task.status_text = STATUS_LABELS[STATE_COMPLETED]
            task.progress = 100
            task.saved_path = dest_dir

            # Автоматическая регистрация в памяти установленных модов
            try:
                from modules.installed_mods import installed_mods_manager
                installed_mods_manager.register_mod(
                    source="steam",
                    mod_id=task.mod_id,
                    title=task.title,
                    install_path=dest_dir,
                    game_id=task.appid,
                    file_name=os.path.basename(dest_dir),
                    size_human=task.size_human,
                )
            except Exception as reg_err:
                print(f"Ошибка регистрации установленного мода Steam: {reg_err}")

            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, True, dest_dir)
        except Exception as e:
            task.status = STATE_ERROR
            task.status_text = "Ошибка копирования файлов"
            task.error_message = str(e)
            self.task_updated.emit(task)
            self.task_finished.emit(task.task_id, False, str(e))


# Глобальный синглтон менеджера очереди загрузок
_GLOBAL_QUEUE_MANAGER: Optional[DownloadQueueManager] = None
_GLOBAL_LOCK = threading.Lock()


def get_download_queue_manager(nexus_api: Optional[NexusAPI] = None) -> DownloadQueueManager:
    """Возвращает единый глобальный экземпляр DownloadQueueManager."""
    global _GLOBAL_QUEUE_MANAGER
    if _GLOBAL_QUEUE_MANAGER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_QUEUE_MANAGER is None:
                _GLOBAL_QUEUE_MANAGER = DownloadQueueManager(nexus_api=nexus_api)
    elif nexus_api is not None and _GLOBAL_QUEUE_MANAGER._nexus_api is None:
        _GLOBAL_QUEUE_MANAGER._nexus_api = nexus_api
    return _GLOBAL_QUEUE_MANAGER
