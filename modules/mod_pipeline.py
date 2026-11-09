"""
Модуль интеллектуальной фильтрации, дедупликации и верификации модов перед скачиванием.

Реализует:
1. Фильтрацию и итерацию по алфавиту/букве (например, моды на букву 'a') и поисковому запросу.
2. Локальную проверку наличия и целостности (проверка папки загрузок mods, подпапок Archives,
   а также игровых директорий archive/pc/mod, r6/scripts, r6/tweaks, bin/x64, mods/REDmod).
3. Сравнение метаданных (имя, file_id, mod_id, версии semantic versioning, контрольные суммы MD5).
4. Умный пропуск с подробным логированием: пропуск уже скачанных и актуальных версий
   во избежание избыточных повторных загрузок, и отправка на скачивание только отсутствующих
   или обновлённых версий модов.
"""

import hashlib
import json
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

try:
    from packaging import version as pkg_version
except ImportError:
    pkg_version = None

from modules.downloaded_mods import downloaded_mods_manager
from modules.installed_mods import installed_mods_manager, normalize_mod_key, KNOWN_NEXUS_MODS
from modules.download_queue import get_download_queue_manager


def get_base_dir() -> str:
    """Возвращает базовую директорию лаунчера."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def clean_mod_title(title: str) -> str:
    """
    Очищает название мода от служебных префиксов категорий вида '[Modders Resources] ',
    '[Core] ', спецсимволов для точной алфавитной фильтрации.
    Например: '[Modders Resources] ArchiveXL' -> 'ArchiveXL'.
    """
    if not title:
        return ""
    # Удаляем квадратные скобки префиксов категорий
    cleaned = re.sub(r"^\[[^\]]+\]\s*", "", title.strip())
    # Удаляем начальные не-буквенно-цифровые символы
    cleaned = re.sub(r"^[^a-zA-Z0-9а-яА-ЯёЁ]+", "", cleaned)
    return cleaned.strip() or title.strip()


def compare_versions(local_ver: Optional[str], remote_ver: Optional[str]) -> int:
    """
    Сравнивает локальную и удалённую версии мода.
    Возвращает:
       1 если remote_ver строго новее local_ver (требуется обновление)
       0 если версии равны или эквивалентны
      -1 если local_ver новее remote_ver
    """
    if not local_ver or not remote_ver:
        return 0

    s_local = str(local_ver).strip().lstrip("vV ")
    s_remote = str(remote_ver).strip().lstrip("vV ")

    if s_local == s_remote:
        return 0

    # 1. Попытка сравнения через packaging.version
    if pkg_version is not None:
        try:
            pv_local = pkg_version.parse(s_local)
            pv_remote = pkg_version.parse(s_remote)
            if pv_remote > pv_local:
                return 1
            elif pv_remote < pv_local:
                return -1
            return 0
        except Exception:
            pass

    # 2. Fallback: покомпонентное числовое сравнение (например, 1.28.6 vs 1.29.0)
    p_local = [int(x) for x in re.findall(r"\d+", s_local)]
    p_remote = [int(x) for x in re.findall(r"\d+", s_remote)]
    if p_local and p_remote:
        max_len = max(len(p_local), len(p_remote))
        p_local += [0] * (max_len - len(p_local))
        p_remote += [0] * (max_len - len(p_remote))
        if p_remote > p_local:
            return 1
        elif p_remote < p_local:
            return -1
        return 0

    return 0


def calculate_file_md5(filepath: str, max_bytes: int = 16 * 1024 * 1024) -> str:
    """Быстрое вычисление MD5 хеша файла (до 16 МБ для моментальной валидации)."""
    if not os.path.isfile(filepath):
        return ""
    md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            read_bytes = 0
            while read_bytes < max_bytes:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                md5.update(chunk)
                read_bytes += len(chunk)
        return md5.hexdigest()
    except Exception:
        return ""


@dataclass
class LocalCheckResult:
    """Результат проверки локального наличия и целостности мода."""
    mod_title: str
    mod_id: Any
    file_id: Optional[Any] = None
    is_present: bool = False
    is_up_to_date: bool = False
    needs_download: bool = True
    matched_path: Optional[str] = None
    matched_source: str = ""  # "downloaded_index", "mods_folder", "game_archive", "game_r6", "game_mods", "installed_registry"
    local_version: Optional[str] = None
    remote_version: Optional[str] = None
    local_size: int = 0
    checksum_matched: Optional[bool] = None
    skip_reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineBatchResult:
    """Итоговая статистика пакетной фильтрации и скачивания модов."""
    total_input: int = 0
    matched_filter: int = 0
    skipped_count: int = 0
    queued_count: int = 0
    skipped_items: List[LocalCheckResult] = field(default_factory=list)
    queued_items: List[Dict[str, Any]] = field(default_factory=list)
    logs: List[str] = field(default_factory=list)


class ModDownloadPipeline(QObject):
    """
    Интеллектуальный конвейер фильтрации, валидации целостности и дедупликации загрузок модов.
    """
    pipeline_log = pyqtSignal(str)
    pipeline_progress = pyqtSignal(int, int, str)  # current, total, mod_title
    pipeline_finished = pyqtSignal(object)         # PipelineBatchResult

    def __init__(self, log_file: Optional[str] = None):
        super().__init__()
        self._lock = threading.RLock()
        self.log_file = log_file or os.path.join(get_base_dir(), "pipeline_download.log")

    def log(self, message: str, callback: Optional[Callable[[str], None]] = None):
        """Логирует событие конвейера в консоль, сигнал Qt, лог-файл и колбэк."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        full_line = f"[{ts}] {message}"
        print(full_line)
        self.pipeline_log.emit(full_line)
        if callback:
            try:
                callback(full_line)
            except Exception:
                pass

        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(full_line + "\n")
        except Exception:
            pass

    # =========================================================================
    # 1. ФИЛЬТРАЦИЯ И СОРТИРОВКА МОДОВ
    # =========================================================================

    def filter_and_sort_mods(
        self,
        mods: List[Dict[str, Any]],
        letter: Optional[str] = None,
        query: Optional[str] = None,
        alphabetical: bool = True,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Фильтрует моды по префиксу/первой букве и поисковому запросу.
        Сортирует список по алфавиту (A-Z).
        Если ни один мод не подошёл под фильтр — выводит лог и возвращает пустой список.
        """
        if not mods:
            self.log("[Pipeline] Список модов для фильтрации пуст.", log_callback)
            return []

        letter_clean = letter.strip().upper() if letter and letter.strip() else None
        query_clean = query.strip().lower() if query and query.strip() else None

        filtered: List[Tuple[str, Dict[str, Any]]] = []

        for m in mods:
            raw_title = str(m.get("title") or m.get("name") or m.get("mod_name") or f"Mod_{m.get('mod_id', '')}")
            clean_name = clean_mod_title(raw_title)

            # 1. Фильтрация по первой букве / префиксу (например, буква 'a')
            if letter_clean:
                # Первая значимая буква названия
                first_char = clean_name[:1].upper()
                if first_char != letter_clean:
                    continue

            # 2. Фильтрация по поисковому запросу (query)
            if query_clean:
                summary = str(m.get("summary") or m.get("description") or "").lower()
                author = str(m.get("author") or "").lower()
                if (query_clean not in clean_name.lower()
                        and query_clean not in summary
                        and query_clean not in author):
                    continue

            filtered.append((clean_name.lower(), m))

        if not filtered:
            msg = f"[Pipeline] Нет модов, соответствующих критерию фильтра (буква='{letter or '*' }', запрос='{query or '*' }'). Пропуск."
            self.log(msg, log_callback)
            return []

        # Сортировка по алфавиту (A-Z)
        if alphabetical:
            filtered.sort(key=lambda x: x[0])

        result_mods = [item[1] for item in filtered]
        self.log(
            f"[Pipeline] Отфильтровано {len(result_mods)} из {len(mods)} модов "
            f"(критерии: буква='{letter or '*' }', запрос='{query or '*' }', сортировка={alphabetical}).",
            log_callback
        )
        return result_mods

    # =========================================================================
    # 2. ПРОВЕРКА ЛОКАЛЬНОГО НАЛИЧИЯ И ЦЕЛОСТНОСТИ МОДА
    # =========================================================================

    def check_local_mod(
        self,
        remote_mod: Dict[str, Any],
        game_dir: Optional[str] = None,
        mods_dir: Optional[str] = None,
    ) -> LocalCheckResult:
        """
        Проверяет наличие мода на диске:
        1. В папке загрузок (mods/ и подпапках Archives, Cyberpunk 2077/archive/pc/mod и др.).
        2. В игровых папках (archive/pc/mod, r6/scripts, r6/tweaks, bin/x64/plugins, mods/REDmod).
        3. Сверяет метаданные: name, file_id, mod_id, version, checksum.
        4. Проверяет физическую целостность (размер > 0 байт, не повреждён).
        """
        source = remote_mod.get("source", "nexus")
        mod_id = remote_mod.get("mod_id") or remote_mod.get("id")
        file_id = remote_mod.get("file_id")
        raw_title = str(remote_mod.get("title") or remote_mod.get("name") or f"Mod #{mod_id}")
        clean_title = clean_mod_title(raw_title)
        remote_ver = str(remote_mod.get("version") or remote_mod.get("file_version") or "").strip()
        remote_fn = remote_mod.get("file_name") or remote_mod.get("name") or ""
        game_id = remote_mod.get("game_id") or 3333

        effective_mods_dir = os.path.abspath(mods_dir) if mods_dir else downloaded_mods_manager.mods_dir

        res = LocalCheckResult(
            mod_title=raw_title,
            mod_id=mod_id,
            file_id=file_id,
            remote_version=remote_ver,
        )

        # ---------------------------------------------------------------------
        # ЭТАП 1: Проверка в локальной базе скачанных модов (downloaded_mods_manager)
        # ---------------------------------------------------------------------
        is_dl, dl_data = downloaded_mods_manager.is_downloaded(
            source=source,
            mod_id=mod_id,
            file_id=file_id,
            filename=remote_fn,
            game_id=game_id,
            title=clean_title,
        )

        if is_dl and dl_data:
            p = dl_data.get("filepath", "")
            if p and os.path.isfile(p):
                try:
                    sz = os.path.getsize(p)
                    if sz > 0:
                        res.is_present = True
                        res.matched_path = p
                        res.matched_source = "download_folder"
                        res.local_size = sz
                        res.local_version = dl_data.get("version") or self._extract_version_from_filename(p)
                        res.details = dl_data
                except OSError:
                    pass

        # ---------------------------------------------------------------------
        # ЭТАП 2: Проверка в реестре установленных модов (installed_mods_manager)
        # ---------------------------------------------------------------------
        if not res.is_present:
            is_inst, inst_data = installed_mods_manager.is_installed(
                source=source,
                mod_id=mod_id,
                game_id=game_id,
                title=clean_title,
                check_disk=True,
            )
            if is_inst and inst_data:
                p = inst_data.get("install_path", "")
                if p and (os.path.isfile(p) or os.path.isdir(p)):
                    res.is_present = True
                    res.matched_path = p
                    res.matched_source = f"installed_{inst_data.get('mod_type', 'mod')}"
                    res.local_version = inst_data.get("version") or self._extract_version_from_filename(p)
                    res.details = inst_data

        # ---------------------------------------------------------------------
        # ЭТАП 3: Прямая проверка в игровых папках (archive/pc/mod, r6, bin, mods)
        # ---------------------------------------------------------------------
        candidate_game_dirs = []
        if game_dir and os.path.isdir(game_dir):
            candidate_game_dirs.append(os.path.abspath(game_dir))

        # Проверяем подпапку Cyberpunk 2077 внутри mods_dir (при автомаршрутизации)
        routed_cp_dir = os.path.join(effective_mods_dir, "Cyberpunk 2077")
        if os.path.isdir(routed_cp_dir):
            candidate_game_dirs.append(routed_cp_dir)

        norm_title = normalize_mod_key(clean_title)

        for g_dir in candidate_game_dirs:
            if res.is_present:
                break

            # 3.1. archive/pc/mod/*.archive
            arch_dir = os.path.join(g_dir, "archive", "pc", "mod")
            if os.path.isdir(arch_dir):
                for root, _, files in os.walk(arch_dir):
                    for f in files:
                        if f.lower().endswith(".archive"):
                            f_norm = normalize_mod_key(f)
                            if f_norm == norm_title or (mod_id and str(mod_id) in f):
                                full_p = os.path.abspath(os.path.join(root, f))
                                if os.path.isfile(full_p) and os.path.getsize(full_p) > 0:
                                    res.is_present = True
                                    res.matched_path = full_p
                                    res.matched_source = "game_archive"
                                    res.local_size = os.path.getsize(full_p)
                                    res.local_version = self._extract_version_from_filename(f)
                                    break
                    if res.is_present:
                        break

            # 3.2. r6/scripts/ (redscript)
            r6_scripts = os.path.join(g_dir, "r6", "scripts")
            if not res.is_present and os.path.isdir(r6_scripts):
                for item in os.listdir(r6_scripts):
                    if normalize_mod_key(item) == norm_title:
                        full_p = os.path.abspath(os.path.join(r6_scripts, item))
                        res.is_present = True
                        res.matched_path = full_p
                        res.matched_source = "game_r6_scripts"
                        break

            # 3.3. r6/tweaks/ (TweakXL)
            r6_tweaks = os.path.join(g_dir, "r6", "tweaks")
            if not res.is_present and os.path.isdir(r6_tweaks):
                for item in os.listdir(r6_tweaks):
                    if normalize_mod_key(item) == norm_title:
                        full_p = os.path.abspath(os.path.join(r6_tweaks, item))
                        res.is_present = True
                        res.matched_path = full_p
                        res.matched_source = "game_r6_tweaks"
                        break

            # 3.4. mods/ (REDmod)
            mods_red = os.path.join(g_dir, "mods")
            if not res.is_present and os.path.isdir(mods_red):
                for item in os.listdir(mods_red):
                    if normalize_mod_key(item) == norm_title:
                        full_p = os.path.abspath(os.path.join(mods_red, item))
                        res.is_present = True
                        res.matched_path = full_p
                        res.matched_source = "game_redmod"
                        break

        # ---------------------------------------------------------------------
        # ЭТАП 4: Сравнение версий и проверка актуальности
        # ---------------------------------------------------------------------
        if not res.is_present:
            res.is_up_to_date = False
            res.needs_download = True
            res.skip_reason = None
            return res

        # Если файл присутствует, проверяем контрольную сумму (если передана в remote_mod)
        remote_md5 = remote_mod.get("md5") or remote_mod.get("checksum")
        if remote_md5 and res.matched_path and os.path.isfile(res.matched_path):
            local_md5 = calculate_file_md5(res.matched_path)
            res.checksum_matched = bool(local_md5 and local_md5.lower() == str(remote_md5).lower())
            if res.checksum_matched:
                res.is_up_to_date = True
                res.needs_download = False
                res.skip_reason = f"Контрольная сумма MD5 совпадает ({local_md5[:8]}...)"
                return res

        # Сравнение версий
        if remote_ver and res.local_version:
            cmp_res = compare_versions(res.local_version, remote_ver)
            if cmp_res == 1:
                # Удаленная версия новее локальной
                res.is_up_to_date = False
                res.needs_download = True
                res.skip_reason = None
                return res
            else:
                # Локальная версия актуальна или новее
                res.is_up_to_date = True
                res.needs_download = False
                res.skip_reason = f"Локальная версия актуальна (v{res.local_version} >= v{remote_ver})"
                return res

        # Если точный file_id совпал — однозначно актуален
        if file_id and res.details.get("file_id") and str(file_id) == str(res.details.get("file_id")):
            res.is_up_to_date = True
            res.needs_download = False
            res.skip_reason = f"Идентичный file_id #{file_id} уже скачан"
            return res

        # По умолчанию существующий файл с ненулевым размером считается актуальным
        res.is_up_to_date = True
        res.needs_download = False
        res.skip_reason = f"Файл уже найден на диске ({os.path.basename(res.matched_path or '')})"
        return res

    def _extract_version_from_filename(self, filename_or_path: str) -> Optional[str]:
        """Извлекает версию вида 1.28.6 или v2.0 из имени файла."""
        name = os.path.basename(filename_or_path)
        m = re.search(r"[vV]?(\d+\.\d+(\.\d+)*(-[a-zA-Z0-9]+)?)", name)
        if m:
            return m.group(1)
        return None

    # =========================================================================
    # 3. ИСПОЛНЕНИЕ КОНВЕЙЕРА: SMART SKIPPING & QUEUING
    # =========================================================================

    def execute_pipeline(
        self,
        mods: List[Dict[str, Any]],
        letter: Optional[str] = None,
        query: Optional[str] = None,
        sort_alphabetical: bool = True,
        force: bool = False,
        save_dir: Optional[str] = None,
        game_dir: Optional[str] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> PipelineBatchResult:
        """
        Запускает полный цикл конвейера:
        1. Фильтрует моды по букве/запросу и сортирует по алфавиту.
        2. Проверяет каждый мод на локальное наличие, версию и целостность.
        3. Если мод уже присутствует и актуален — пропускает его с выводом лога/уведомления.
        4. Если мод отсутствует или доступна новая версия — добавляет его в очередь загрузок.
        """
        result = PipelineBatchResult(total_input=len(mods))

        # Шаг 1: Фильтрация и сортировка
        target_mods = self.filter_and_sort_mods(
            mods,
            letter=letter,
            query=query,
            alphabetical=sort_alphabetical,
            log_callback=log_callback,
        )
        result.matched_filter = len(target_mods)
        if not target_mods:
            self.pipeline_finished.emit(result)
            return result

        qm = get_download_queue_manager()
        effective_save_dir = save_dir or downloaded_mods_manager.mods_dir

        self.log(f"--- Старт обработки конвейера загрузок: {len(target_mods)} модов ---", log_callback)

        for idx, m in enumerate(target_mods, start=1):
            title = m.get("title") or m.get("name") or f"Mod #{m.get('mod_id', '')}"
            self.pipeline_progress.emit(idx, len(target_mods), title)

            # Шаг 2: Проверка локального наличия и актуальности
            check = self.check_local_mod(m, game_dir=game_dir, mods_dir=effective_save_dir)

            # Шаг 3: Логика пропуска / загрузки
            if check.is_present and check.is_up_to_date and not force:
                reason = check.skip_reason or "уже скачан и актуален"
                log_entry = (
                    f"⏭️ [ПРОПУСК] Мод '{title}' "
                    f"(версия: {check.local_version or 'N/A'}) "
                    f"уже присутствует на диске: [{check.matched_source}] '{check.matched_path}'. "
                    f"Причина: {reason}. Загрузка пропущена во избежание дублирования."
                )
                self.log(log_entry, log_callback)
                result.logs.append(log_entry)
                result.skipped_count += 1
                result.skipped_items.append(check)
            else:
                # Требуется скачивание
                file_id = m.get("file_id")
                mod_id = m.get("mod_id") or m.get("id") or file_id
                file_name = m.get("file_name") or m.get("name") or f"mod_{mod_id}_{file_id}.zip"
                size_str = m.get("size_human", "—")
                game_id = m.get("game_id", 3333)
                domain = m.get("game_domain", "cyberpunk2077")

                if check.is_present and not check.is_up_to_date:
                    log_entry = (
                        f"🔄 [ОБНОВЛЕНИЕ] Мод '{title}': найдена новая версия "
                        f"(удалённая {check.remote_version} > локальная {check.local_version}). "
                        f"Добавление в очередь загрузок."
                    )
                else:
                    log_entry = f"📥 [ЗАГРУЗКА] Мод '{title}' отсутствует локально. Добавление в очередь загрузок."

                self.log(log_entry, log_callback)
                result.logs.append(log_entry)

                # Постановка задачи в очередь скачивания
                task = qm.add_nexus_task(
                    mod_id=mod_id,
                    file_id=file_id or mod_id,
                    title=title,
                    file_name=file_name,
                    game_id=game_id,
                    game_domain=domain,
                    save_dir=effective_save_dir,
                    size_human=size_str,
                    force=force,
                )
                result.queued_count += 1
                result.queued_items.append({
                    "mod": m,
                    "task": task,
                    "check": check,
                })

        summary_msg = (
            f"✅ Конвейер завершён: Обработано {len(target_mods)} модов | "
            f"Пропущено (уже есть): {result.skipped_count} | "
            f"Добавлено в очередь: {result.queued_count}."
        )
        self.log(summary_msg, log_callback)
        result.logs.append(summary_msg)

        self.pipeline_finished.emit(result)
        return result


# Глобальный синглтон конвейера
mod_download_pipeline = ModDownloadPipeline()
