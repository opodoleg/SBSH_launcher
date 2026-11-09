"""
Модуль поиска и безопасной очистки дубликатов модов (Duplicate Mod Cleaner).

Функционал:
1. Сканирование папки загрузок (mods, mods/Archives, mods/Cyberpunk 2077 и др.)
   и официальных игровых папок модов (archive/pc/mod, r6/scripts, r6/tweaks,
   bin/x64/plugins, mods/REDmod).
2. Критерии сопоставления:
   - Сравнение файлов по точному имени и расширению (.archive, .reds, .lua, .zip, .7z, .rar, .yaml, .dll и др.).
   - При совпадении имени и типа файла они помечаются как дубликаты.
3. Безопасное сохранение и удаление:
   - Сохранение только одного основного экземпляра (приоритет отдаётся файлу в официальной директории игры
     либо наиболее свежему по времени изменения).
   - Автоматическое удаление избыточных копий для освобождения места и предотвращения конфликтов модов.
4. Логирование и отчётность:
   - Запись подробного отчёта в duplicate_cleanup.log и формирование структурированных данных для UI.
   - Синхронизация с реестрами installed_mods.json и downloaded_mods.json.
"""

import os
import shutil
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, pyqtSignal


def get_base_dir() -> str:
    """Возвращает базовую директорию лаунчера."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def format_bytes(size_bytes: int) -> str:
    """Форматирует байты в читаемый вид (B, KB, MB, GB)."""
    if size_bytes < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size_bytes < 1024.0 or unit == "TB":
            return f"{size_bytes:.2f} {unit}" if unit in ["MB", "GB", "TB"] else f"{size_bytes:.0f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} GB"


# Расширения файлов модов для сопоставления
MOD_FILE_EXTENSIONS: Set[str] = {
    # Архивы игры
    ".archive",
    ".pak",
    ".bundle",
    # Скрипты и код
    ".reds",
    ".lua",
    ".asi",
    ".dll",
    # Конфигурации и твики
    ".yaml",
    ".yml",
    ".tweak",
    # Пакеты и дистрибутивы загрузок
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
    ".bz2",
}

# Общие служебные имена файлов, требующие квалификации по имени родительской папки
GENERIC_MOD_FILENAMES: Set[str] = {
    "init.lua",
    "info.json",
    "config.json",
    "settings.json",
    "mod.json",
    "module.json",
    "license.txt",
    "license",
    "readme.txt",
    "readme.md",
    ".gitkeep",
}


@dataclass
class ModFileInfo:
    """Информация об обнаруженном файле мода."""
    path: str
    filename: str
    stem: str
    extension: str
    size: int
    mtime: float
    mtime_str: str
    is_game_dir: bool
    dirpath: str
    group_key: str = ""


@dataclass
class DuplicateItem:
    """Информация об удалённом или помеченном дубликате."""
    filename: str
    extension: str
    removed_path: str
    kept_path: str
    size: int
    size_human: str
    mtime_removed: str
    mtime_kept: str
    reason: str
    was_in_game_dir: bool
    was_deleted: bool = False
    error: str = ""


@dataclass
class DuplicateCleanupReport:
    """Итоговый отчёт по очистке дубликатов."""
    total_scanned_files: int = 0
    total_duplicate_groups: int = 0
    total_duplicates_found: int = 0
    total_duplicates_removed: int = 0
    total_bytes_freed: int = 0
    freed_human: str = "0 B"
    removed_files: List[DuplicateItem] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    timestamp: str = ""
    log_filepath: str = ""
    report_text: str = ""
    is_dry_run: bool = False


class DuplicateModCleaner(QObject):
    """
    Класс поиска, анализа и безопасного удаления дубликатов файлов модов.
    """
    scan_progress = pyqtSignal(int, int, str)
    cleanup_finished = pyqtSignal(object)

    def __init__(
        self,
        mods_dir: Optional[str] = None,
        game_dirs: Optional[List[str]] = None,
        log_filepath: Optional[str] = None,
    ):
        super().__init__()
        self.base_dir = get_base_dir()
        self.mods_dir = os.path.abspath(mods_dir) if mods_dir else os.path.join(self.base_dir, "mods")
        self.custom_game_dirs = [os.path.abspath(p) for p in (game_dirs or []) if p and os.path.isdir(p)]
        self.log_filepath = log_filepath or os.path.join(self.base_dir, "duplicate_cleanup.log")
        self._lock = threading.RLock()

    # =========================================================================
    # ОБНАРУЖЕНИЕ ДИРЕКТОРИЙ
    # =========================================================================

    def get_candidate_game_dirs(self) -> List[str]:
        """Собирает список всех существующих корневых папок игр."""
        candidates = list(self.custom_game_dirs)

        # 1. Чтение из настроек QSettings
        try:
            from PyQt6.QtCore import QSettings
            st = QSettings("SBSH", "Launcher")
            for k in ["games_save_path", "cyberpunk_path", "game_path", "game_dir"]:
                v = st.value(k, "")
                if v and isinstance(v, str) and os.path.isdir(v):
                    p_abs = os.path.abspath(v)
                    if p_abs not in candidates:
                        candidates.append(p_abs)
        except Exception:
            pass

        # 2. Подпапка Cyberpunk 2077 внутри mods_dir
        routed_cp = os.path.join(self.mods_dir, "Cyberpunk 2077")
        if os.path.isdir(routed_cp) and routed_cp not in candidates:
            candidates.append(os.path.abspath(routed_cp))

        # 3. Известные пути установки игр
        known_paths = [
            r"G:\games\Cyberpunk 2077",
            r"C:\Games\Cyberpunk 2077",
            r"D:\Games\Cyberpunk 2077",
            r"C:\Program Files (x86)\Steam\steamapps\common\Cyberpunk 2077",
            r"D:\SteamLibrary\steamapps\common\Cyberpunk 2077",
            r"C:\GOG Games\Cyberpunk 2077",
            r"G:\SteamLibrary\steamapps\common\Cyberpunk 2077",
            r"E:\Games\Cyberpunk 2077",
        ]
        for p in known_paths:
            if os.path.isdir(p) and p not in candidates:
                candidates.append(os.path.abspath(p))

        return candidates

    def collect_target_directories(self) -> Tuple[List[str], Set[str]]:
        """
        Возвращает:
        - scan_dirs: список всех директорий для поиска файлов.
        - official_game_subdirs: множество путей, относящихся к официальным игровым папкам модов.
        """
        scan_dirs: List[str] = []
        official_game_subdirs: Set[str] = set()

        # 1. Папка загрузок модов
        if os.path.isdir(self.mods_dir):
            scan_dirs.append(self.mods_dir)

        # Дополнительная папка downloads/mods если существует отдельно
        legacy_mods = os.path.join(self.base_dir, "downloads", "mods")
        if os.path.isdir(legacy_mods) and os.path.abspath(legacy_mods) != self.mods_dir:
            scan_dirs.append(os.path.abspath(legacy_mods))

        # 2. Игровые папки модов
        game_roots = self.get_candidate_game_dirs()
        for root in game_roots:
            # archive/pc/mod
            arch_dir = os.path.join(root, "archive", "pc", "mod")
            if os.path.isdir(arch_dir):
                scan_dirs.append(arch_dir)
                official_game_subdirs.add(os.path.normcase(os.path.abspath(arch_dir)))

            # r6
            r6_dir = os.path.join(root, "r6")
            if os.path.isdir(r6_dir):
                scan_dirs.append(r6_dir)
                official_game_subdirs.add(os.path.normcase(os.path.abspath(r6_dir)))

            # bin/x64/plugins
            bin_dir = os.path.join(root, "bin", "x64", "plugins")
            if os.path.isdir(bin_dir):
                scan_dirs.append(bin_dir)
                official_game_subdirs.add(os.path.normcase(os.path.abspath(bin_dir)))

            # mods (REDmod)
            mods_redmod = os.path.join(root, "mods")
            if os.path.isdir(mods_redmod):
                # Исключаем случай, когда root - это корень самого лаунчера
                if os.path.normcase(os.path.abspath(mods_redmod)) != os.path.normcase(self.mods_dir):
                    scan_dirs.append(mods_redmod)
                    official_game_subdirs.add(os.path.normcase(os.path.abspath(mods_redmod)))

        # Удаляем дублирующиеся директории с сохранением порядка
        seen = set()
        unique_scan_dirs = []
        for d in scan_dirs:
            norm = os.path.normcase(os.path.abspath(d))
            if norm not in seen:
                seen.add(norm)
                unique_scan_dirs.append(os.path.abspath(d))

        return unique_scan_dirs, official_game_subdirs

    def is_in_game_directory(self, filepath: str, official_game_subdirs: Set[str]) -> bool:
        """Проверяет, лежит ли файл внутри официальной игровой директории."""
        norm_file = os.path.normcase(os.path.abspath(filepath))
        for g_sub in official_game_subdirs:
            if norm_file.startswith(g_sub + os.sep) or norm_file == g_sub:
                return True
        return False

    # =========================================================================
    # СКАНИРОВАНИЕ И ФОРМИРОВАНИЕ КЛЮЧЕЙ
    # =========================================================================

    def build_group_key(self, filename: str, parent_dir: str) -> str:
        """
        Формирует строковый ключ для сопоставления дубликатов:
        - По умолчанию: точное имя файла и расширение в нижнем регистре (stem.ext).
        - Для общих служебных файлов (init.lua, info.json): префикс родительской папки (folder/init.lua).
        """
        fn_lower = filename.strip().lower()
        if fn_lower in GENERIC_MOD_FILENAMES:
            parent_name = os.path.basename(os.path.abspath(parent_dir)).strip().lower()
            return f"{parent_name}/{fn_lower}"
        return fn_lower

    def scan_mod_files(
        self,
        custom_dirs: Optional[List[str]] = None,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[ModFileInfo]:
        """
        Сканирует директории и находит все файлы модов.
        """
        if custom_dirs:
            scan_dirs = [os.path.abspath(d) for d in custom_dirs if os.path.isdir(d)]
            _, official_game_subdirs = self.collect_target_directories()
        else:
            scan_dirs, official_game_subdirs = self.collect_target_directories()

        files_found: List[ModFileInfo] = []
        seen_paths: Set[str] = set()

        for d_idx, directory in enumerate(scan_dirs, start=1):
            if not os.path.isdir(directory):
                continue

            for root, _, files in os.walk(directory):
                for f in files:
                    full_p = os.path.abspath(os.path.join(root, f))
                    norm_p = os.path.normcase(full_p)
                    if norm_p in seen_paths:
                        continue

                    stem, ext = os.path.splitext(f)
                    ext_lower = ext.lower()

                    # Проверяем расширение
                    if ext_lower not in MOD_FILE_EXTENSIONS:
                        continue

                    try:
                        st = os.stat(full_p)
                        sz = st.st_size
                        mtime = st.st_mtime
                        mtime_dt = datetime.fromtimestamp(mtime)
                        mtime_str = mtime_dt.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        continue

                    # Пропускаем пустые временные файлы (0 байт)
                    if sz <= 0:
                        continue

                    is_game = self.is_in_game_directory(full_p, official_game_subdirs)
                    g_key = self.build_group_key(f, root)

                    info = ModFileInfo(
                        path=full_p,
                        filename=f,
                        stem=stem,
                        extension=ext_lower,
                        size=sz,
                        mtime=mtime,
                        mtime_str=mtime_str,
                        is_game_dir=is_game,
                        dirpath=root,
                        group_key=g_key,
                    )
                    files_found.append(info)
                    seen_paths.add(norm_p)

                    if progress_cb:
                        progress_cb(len(files_found), len(scan_dirs), f)

        return files_found

    # =========================================================================
    # ГРУППИРОВКА И ВЫБОР ОСНОВНОГО ЭКЗЕМПЛЯРА
    # =========================================================================

    def group_duplicates(self, files: List[ModFileInfo]) -> Dict[str, List[ModFileInfo]]:
        """Группирует файлы по точному имени и типу (расширению)."""
        groups: Dict[str, List[ModFileInfo]] = {}
        for f in files:
            groups.setdefault(f.group_key, []).append(f)

        # Оставляем только группы, где найдено 2 или более файлов с одинаковым именем и типом
        return {k: v for k, v in groups.items() if len(v) > 1}

    def choose_primary_file(
        self,
        group: List[ModFileInfo],
        retention_policy: str = "game_dir_first",
    ) -> Tuple[ModFileInfo, List[ModFileInfo], str]:
        """
        Выбирает один основной экземпляр для сохранения, а остальные помечает на удаление.

        Критерии безопасного сохранения:
        - retention_policy == "game_dir_first":
          1. Приоритет отдаётся экземпляру в официальной директории игры (is_game_dir=True).
          2. Если в игре найдено несколько копий — сохраняется самая свежая по дате изменения (mtime).
          3. Если более свежая версия лежит в папке загрузок, она копируется в игру на замену старой перед удалением.
          4. Если в игре файла нет (все копии в папках загрузок) — сохраняется самый свежий по mtime.
        - retention_policy == "newest_mtime":
          1. Сохраняется файл с самым новым mtime независимо от папки.
        """
        if len(group) == 1:
            return group[0], [], "Единственный экземпляр"

        if retention_policy == "game_dir_first":
            game_files = [f for f in group if f.is_game_dir]
            download_files = [f for f in group if not f.is_game_dir]

            if game_files:
                # Среди игровых файлов выбираем самый свежий
                game_files.sort(key=lambda x: (x.mtime, x.size), reverse=True)
                primary_game = game_files[0]

                # Проверяем, нет ли в папке загрузок более новой версии того же мода
                if download_files:
                    download_files.sort(key=lambda x: (x.mtime, x.size), reverse=True)
                    newest_download = download_files[0]

                    # Если загруженный файл строго новее файла в игре (более 2 секунд разницы)
                    if newest_download.mtime > primary_game.mtime + 2.0:
                        reason = (
                            f"Сохранён обновлённый экземпляр в официальной папке игры "
                            f"(заменён более свежей версией от {newest_download.mtime_str})"
                        )
                        # Помечаем загруженный экземпляр как источник обновления
                        duplicates = [f for f in group if f.path != primary_game.path]
                        return primary_game, duplicates, reason

                duplicates = [f for f in group if f.path != primary_game.path]
                reason = "Сохранён экземпляр в официальной директории игры (наиболее актуальный)"
                return primary_game, duplicates, reason
            else:
                # Все файлы находятся в папках загрузок — выбираем самый свежий по mtime
                sorted_files = sorted(group, key=lambda x: (x.mtime, x.size), reverse=True)
                primary = sorted_files[0]
                duplicates = sorted_files[1:]
                reason = f"Сохранён наиболее свежий файл по дате изменения ({primary.mtime_str})"
                return primary, duplicates, reason

        else:
            # Чистый приоритет mtime
            sorted_files = sorted(group, key=lambda x: (x.mtime, x.size), reverse=True)
            primary = sorted_files[0]
            duplicates = sorted_files[1:]
            loc = "в папке игры" if primary.is_game_dir else "в папке загрузок"
            reason = f"Сохранён самый новый экземпляр {loc} ({primary.mtime_str})"
            return primary, duplicates, reason

    # =========================================================================
    # ВЫПОЛНЕНИЕ ОЧИСТКИ
    # =========================================================================

    def execute_cleanup(
        self,
        dry_run: bool = False,
        retention_policy: str = "game_dir_first",
        custom_dirs: Optional[List[str]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> DuplicateCleanupReport:
        """
        Запускает полный цикл поиска и безопасной очистки дубликатов:
        1. Сканирует папки загрузок и модов игры.
        2. Находит файлы с одинаковым именем и расширением.
        3. Сохраняет 1 основной экземпляр (по правилу приоритета).
        4. Автоматически удаляет избыточные файлы (если dry_run=False).
        5. Обновляет реестры установленных и скачанных модов.
        6. Формирует детальный отчёт и записывает в duplicate_cleanup.log.
        """
        with self._lock:
            now_dt = datetime.now()
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

            def _log(msg: str):
                if log_callback:
                    log_callback(msg)

            _log(f"🔍 Запуск {'анализа' if dry_run else 'очистки'} дубликатов модов [{now_str}]...")

            # 1. Сканирование
            all_files = self.scan_mod_files(
                custom_dirs=custom_dirs,
                progress_cb=lambda count, total_d, fn: self.scan_progress.emit(count, total_d, fn),
            )
            _log(f"📂 Всего проверено подходящих файлов модов: {len(all_files)}")

            # 2. Группировка
            duplicate_groups = self.group_duplicates(all_files)
            _log(f"⚠️ Найдено групп дубликатов: {len(duplicate_groups)}")

            report = DuplicateCleanupReport(
                total_scanned_files=len(all_files),
                total_duplicate_groups=len(duplicate_groups),
                timestamp=now_str,
                log_filepath=self.log_filepath,
                is_dry_run=dry_run,
            )

            if not duplicate_groups:
                report.report_text = self._build_empty_report_text(report)
                self._save_log_file(report.report_text)
                self.cleanup_finished.emit(report)
                _log("✅ Дубликатов не обнаружено. Все файлы уникальны.")
                return report

            total_bytes_freed = 0
            removed_items: List[DuplicateItem] = []
            errors: List[str] = []
            deleted_paths: Set[str] = set()
            replacement_map: Dict[str, str] = {}  # deleted_path -> kept_path

            # 3. Обработка каждой группы
            for g_key, group in duplicate_groups.items():
                primary, duplicates, reason = self.choose_primary_file(
                    group, retention_policy=retention_policy
                )

                # Если требуется обновить игровой файл более свежей версией из загрузок
                if not dry_run and primary.is_game_dir:
                    newer_downloads = [
                        d for d in duplicates if not d.is_game_dir and d.mtime > primary.mtime + 2.0
                    ]
                    if newer_downloads:
                        best_download = max(newer_downloads, key=lambda x: x.mtime)
                        try:
                            _log(f"🔄 Обновление файла в игре: {primary.filename} из {best_download.path}")
                            shutil.copy2(best_download.path, primary.path)
                            # Обновляем метаданные primary
                            primary.size = os.path.getsize(primary.path)
                            primary.mtime = os.path.getmtime(primary.path)
                            primary.mtime_str = datetime.fromtimestamp(primary.mtime).strftime("%Y-%m-%d %H:%M:%S")
                        except Exception as copy_err:
                            err_msg = f"Ошибка обновления игрового файла {primary.path}: {copy_err}"
                            errors.append(err_msg)
                            _log(f"❌ {err_msg}")

                for dup in duplicates:
                    report.total_duplicates_found += 1
                    item = DuplicateItem(
                        filename=dup.filename,
                        extension=dup.extension,
                        removed_path=dup.path,
                        kept_path=primary.path,
                        size=dup.size,
                        size_human=format_bytes(dup.size),
                        mtime_removed=dup.mtime_str,
                        mtime_kept=primary.mtime_str,
                        reason=reason,
                        was_in_game_dir=dup.is_game_dir,
                    )

                    if not dry_run:
                        try:
                            if os.path.isfile(dup.path):
                                os.remove(dup.path)
                                item.was_deleted = True
                                total_bytes_freed += dup.size
                                report.total_duplicates_removed += 1
                                deleted_paths.add(os.path.normcase(os.path.abspath(dup.path)))
                                replacement_map[os.path.normcase(os.path.abspath(dup.path))] = primary.path
                                _log(f"🗑 Удалён дубликат: {dup.path} ({item.size_human})")
                        except Exception as del_err:
                            err_str = f"Не удалось удалить файл {dup.path}: {del_err}"
                            item.error = err_str
                            errors.append(err_str)
                            _log(f"❌ {err_str}")
                    else:
                        # В режиме dry_run только подсчитываем потенциальный объем
                        total_bytes_freed += dup.size
                        _log(f"🔍 [Предпросмотр] Будет удалён: {dup.path} ({item.size_human})")

                    removed_items.append(item)

            report.total_bytes_freed = total_bytes_freed
            report.freed_human = format_bytes(total_bytes_freed)
            report.removed_files = removed_items
            report.errors = errors

            # 4. Синхронизация реестров (если производилось удаление)
            if not dry_run and deleted_paths:
                self._sync_registries(deleted_paths, replacement_map, _log)

            # 5. Построение отчёта и сохранение лога
            report.report_text = self._build_full_report_text(report)
            self._save_log_file(report.report_text)

            self.cleanup_finished.emit(report)
            _log(
                f"🎉 Очистка завершена! Удалено дубликатов: {report.total_duplicates_removed}, "
                f"освобождено: {report.freed_human}"
            )
            return report

    # =========================================================================
    # СИНХРОНИЗАЦИЯ РЕЕСТРОВ
    # =========================================================================

    def _sync_registries(
        self,
        deleted_paths: Set[str],
        replacement_map: Dict[str, str],
        logger: Callable[[str], None],
    ):
        """Синхронизирует installed_mods.json и downloaded_mods.json после удаления файлов."""
        try:
            from modules.installed_mods import installed_mods_manager
            with installed_mods_manager._lock:
                mods = installed_mods_manager._mods
                updated = False
                for m in mods:
                    p = m.get("install_path", "")
                    if not p:
                        continue
                    norm = os.path.normcase(os.path.abspath(p))
                    if norm in deleted_paths:
                        # Перенаправляем на сохранённый основной файл
                        if norm in replacement_map and os.path.exists(replacement_map[norm]):
                            m["install_path"] = replacement_map[norm]
                            updated = True
                            logger(f"🔄 Реестр: путь мода {m.get('title')} перенаправлен на {replacement_map[norm]}")

                if updated:
                    installed_mods_manager.save()
                    installed_mods_manager.mods_updated.emit()

            # Дополнительно запускаем очистку несуществующих
            installed_mods_manager.clean_missing()
        except Exception as e:
            logger(f"⚠️ Ошибка синхронизации installed_mods: {e}")

        try:
            from modules.downloaded_mods import downloaded_mods_manager
            with downloaded_mods_manager._lock:
                initial_count = len(downloaded_mods_manager._entries)
                downloaded_mods_manager._entries = [
                    e for e in downloaded_mods_manager._entries
                    if os.path.normcase(os.path.abspath(e.get("filepath", ""))) not in deleted_paths
                    and os.path.isfile(e.get("filepath", ""))
                ]
                if len(downloaded_mods_manager._entries) != initial_count:
                    downloaded_mods_manager.save()
                    downloaded_mods_manager.mods_updated.emit()
                    logger(f"🔄 Индекс downloaded_mods.json очищен от удалённых записей")
        except Exception as e:
            logger(f"⚠️ Ошибка синхронизации downloaded_mods: {e}")

    # =========================================================================
    # ФОРМАТИРОВАНИЕ ОТЧЁТА И ЛОГИРОВАНИЕ
    # =========================================================================

    def _build_empty_report_text(self, report: DuplicateCleanupReport) -> str:
        lines = [
            "=" * 80,
            f"ОТЧЁТ ОБ ОЧИСТКЕ ДУБЛИКАТОВ МОДОВ [{report.timestamp}]",
            "=" * 80,
            f"Режим: {'Предпросмотр (Dry-Run)' if report.is_dry_run else 'Удаление дубликатов'}",
            f"Всего проверено файлов: {report.total_scanned_files}",
            "Дубликатов не обнаружено. Все файлы в папках игры и загрузок уникальны.",
            "=" * 80,
        ]
        return "\n".join(lines)

    def _build_full_report_text(self, report: DuplicateCleanupReport) -> str:
        lines = [
            "=" * 80,
            f"ОТЧЁТ ОБ ОЧИСТКЕ ДУБЛИКАТОВ МОДОВ [{report.timestamp}]",
            "=" * 80,
            f"Режим работы: {'Предпросмотр (без удаления файлов)' if report.is_dry_run else 'Автоматическое удаление дубликатов'}",
            f"Всего проверено файлов модов: {report.total_scanned_files}",
            f"Найдено групп дубликатов: {report.total_duplicate_groups}",
            f"Всего файлов-дубликатов: {report.total_duplicates_found}",
            f"{'Будет удалено' if report.is_dry_run else 'Успешно удалено'} файлов: {report.total_duplicates_removed if not report.is_dry_run else report.total_duplicates_found}",
            f"{'Будет освобождено' if report.is_dry_run else 'Освобождено дискового пространства'}: {report.freed_human} ({report.total_bytes_freed:,} байт)",
            "=" * 80,
            "",
            "ДЕТАЛИЗИРОВАННЫЙ СПИСОК УДАЛЁННЫХ ДУБЛИКАТОВ:",
            "-" * 80,
        ]

        for idx, item in enumerate(report.removed_files, start=1):
            action_tag = "[БУДЕТ УДАЛЁН]" if report.is_dry_run else ("[УДАЛЁН]" if item.was_deleted else "[ОШИБКА УДАЛЕНИЯ]")
            lines.append(f"[{idx}] {item.filename} ({item.size_human}) {action_tag}")
            lines.append(f"    ❌ Удалённый дубликат: {item.removed_path}")
            lines.append(f"       Дата изменения:   {item.mtime_removed}")
            lines.append(f"       Расположение:     {'Официальная папка игры' if item.was_in_game_dir else 'Папка загрузок'}")
            lines.append(f"    ✅ Сохранён оригинал: {item.kept_path}")
            lines.append(f"       Дата изменения:   {item.mtime_kept}")
            lines.append(f"       Причина:          {item.reason}")
            if item.error:
                lines.append(f"       ⚠️ Ошибка:        {item.error}")
            lines.append("")

        if report.errors:
            lines.append("-" * 80)
            lines.append("ОШИБКИ И ПРЕДУПРЕЖДЕНИЯ:")
            for err in report.errors:
                lines.append(f"  • {err}")
            lines.append("")

        lines.append("=" * 80)
        return "\n".join(lines)

    def _save_log_file(self, content: str):
        """Дописывает отчёт в persistent duplicate_cleanup.log."""
        try:
            log_dir = os.path.dirname(self.log_filepath)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            with open(self.log_filepath, "a", encoding="utf-8") as f:
                f.write(content + "\n\n")
        except Exception as e:
            print(f"[DuplicateModCleaner] Не удалось записать лог {self.log_filepath}: {e}")


# Глобальный экземпляр очистителя дубликатов
duplicate_mod_cleaner = DuplicateModCleaner()
