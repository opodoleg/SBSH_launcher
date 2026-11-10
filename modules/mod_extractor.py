"""
Модуль распаковки и интеллектуальной маршрутизации архивов модов (Smart Mod Archive Extractor).

Функционал:
1. Поддержка множества форматов:
   - .zip (через встроенный zipfile)
   - .7z (через py7zr с поддержкой fallback на 7-Zip CLI)
   - .rar (через rarfile с поддержкой 7-Zip CLI / unrar)
2. Анализ структуры архива через pathlib (PurePosixPath):
   - Поиск стандартных корневых директорий Cyberpunk 2077: r6, archive, bin, mods, red4ext.
   - Снятие внешних папок-обёрток (wrapper folders, например: ModName_v1.0/r6/... -> r6/...).
   - Защита от дублирующихся вложенных путей (предотвращение r6/r6, archive/archive).
   - Автоматическая маршрутизация одиночных файлов (loose files) без структуры:
     * .archive, .xl -> archive/pc/mod/
     * .reds -> r6/scripts/
     * .tweaks, .yaml, .yml, .tweak -> r6/tweaks/
     * .asi, .dll -> bin/x64/plugins/ (или red4ext/plugins/)
3. Безопасная распаковка и логирование:
   - Автоматическое создание недостающих каталогов (parents=True, exist_ok=True).
   - Перезапись файлов при обновлении мода.
   - Возврат списка всех извлечённых относительных путей для сохранения в локальную базу
     и последующего безопасного удаления.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Попытка импорта py7zr и rarfile
try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

try:
    import rarfile
    HAS_RARFILE = True
except ImportError:
    HAS_RARFILE = False


# Стандартные корневые директории Cyberpunk 2077
STANDARD_ROOT_DIRS: Set[str] = {
    "r6",
    "archive",
    "bin",
    "mods",
    "red4ext",
}

# Правила маршрутизации для одиночных файлов (loose files)
LOOSE_EXTENSIONS_MAP: Dict[str, str] = {
    ".archive": "archive/pc/mod",
    ".xl": "archive/pc/mod",
    ".reds": "r6/scripts",
    ".tweaks": "r6/tweaks",
    ".yaml": "r6/tweaks",
    ".yml": "r6/tweaks",
    ".tweak": "r6/tweaks",
    ".asi": "bin/x64/plugins",
    ".dll": "bin/x64/plugins",
}


def find_7z_binary() -> Optional[str]:
    """Ищет исполняемый файл 7-Zip CLI на системе."""
    candidates = [
        shutil.which("7z"),
        shutil.which("7za"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
        r"C:\ProgramData\chocolatey\bin\7z.exe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


# Настройка rarfile если найден 7-Zip
if HAS_RARFILE:
    z7_bin = find_7z_binary()
    if z7_bin:
        rarfile.UNRAR_TOOL = z7_bin


@dataclass
class RoutedFile:
    """Информация о маршрутизации файла из архива в целевую папку игры."""
    archive_internal_path: str
    target_relative_path: str
    target_absolute_path: str
    file_size: int = 0
    rule_applied: str = ""


@dataclass
class ExtractionResult:
    """Итоговый результат распаковки архива мода."""
    archive_path: str
    game_root: str
    success: bool
    total_files_extracted: int = 0
    total_bytes_extracted: int = 0
    extracted_relative_paths: List[str] = field(default_factory=list)
    extracted_absolute_paths: List[str] = field(default_factory=list)
    routed_files: List[RoutedFile] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    error_message: str = ""
    is_wrapped: bool = False
    wrapper_folder: str = ""


class ModArchiveExtractor:
    """
    Класс для инспекции, интеллектуальной маршрутизации и безопасного
    извлечения файлов модов в директорию игры.
    """

    def __init__(self, seven_zip_bin: Optional[str] = None):
        self.seven_zip_bin = seven_zip_bin or find_7z_binary()

    # =========================================================================
    # ИНСПЕКЦИЯ СОДЕРЖИМОГО АРХИВА
    # =========================================================================

    def list_archive_members(self, archive_path: str) -> List[Tuple[str, int, bool]]:
        """
        Возвращает список элементов архива: [(internal_path, file_size, is_dir), ...]
        Поддерживает .zip, .7z, .rar.
        """
        ext = os.path.splitext(archive_path)[1].lower()
        members: List[Tuple[str, int, bool]] = []

        if ext == ".zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                for info in zf.infolist():
                    p_name = info.filename.replace("\\", "/")
                    is_dir = info.is_dir() or p_name.endswith("/")
                    members.append((p_name, info.file_size, is_dir))
            return members

        elif ext == ".7z":
            if HAS_PY7ZR:
                try:
                    with py7zr.SevenZipFile(archive_path, "r") as sz:
                        for entry in sz.list():
                            p_name = entry.filename.replace("\\", "/")
                            is_dir = entry.is_directory or p_name.endswith("/")
                            members.append((p_name, entry.uncompressed, is_dir))
                    return members
                except Exception as e:
                    # Fallback к CLI 7-Zip при необходимости
                    if not self.seven_zip_bin:
                        raise e

            if self.seven_zip_bin:
                return self._list_with_7z_cli(archive_path)
            raise RuntimeError(f"Для распаковки .7z требуется библиотека py7zr или 7-Zip CLI.")

        elif ext == ".rar":
            if HAS_RARFILE:
                try:
                    with rarfile.RarFile(archive_path, "r") as rf:
                        for info in rf.infolist():
                            p_name = info.filename.replace("\\", "/")
                            is_dir = info.is_dir() or p_name.endswith("/")
                            members.append((p_name, info.file_size, is_dir))
                    return members
                except Exception as e:
                    if not self.seven_zip_bin:
                        raise e

            if self.seven_zip_bin:
                return self._list_with_7z_cli(archive_path)
            raise RuntimeError(f"Для распаковки .rar требуется rarfile или 7-Zip CLI.")

        else:
            raise ValueError(f"Неподдерживаемый формат архива: {ext}")

    def _list_with_7z_cli(self, archive_path: str) -> List[Tuple[str, int, bool]]:
        """Получает список файлов через 7z l."""
        if not self.seven_zip_bin:
            raise RuntimeError("7-Zip CLI не найден.")

        cmd = [self.seven_zip_bin, "l", "-slt", os.path.abspath(archive_path)]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="ignore")
        if res.returncode != 0:
            raise RuntimeError(f"7-Zip ошибка при чтении архива: {res.stderr}")

        members = []
        cur_path = ""
        cur_size = 0
        cur_is_dir = False

        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("Path = "):
                cur_path = line[7:].replace("\\", "/")
            elif line.startswith("Size = "):
                try:
                    cur_size = int(line[7:])
                except ValueError:
                    cur_size = 0
            elif line.startswith("Folder = "):
                cur_is_dir = (line[9:].strip() == "+")
            elif not line and cur_path:
                if cur_path != os.path.basename(archive_path).replace("\\", "/"):
                    members.append((cur_path, cur_size, cur_is_dir))
                cur_path = ""
                cur_size = 0
                cur_is_dir = False

        if cur_path and cur_path != os.path.basename(archive_path).replace("\\", "/"):
            members.append((cur_path, cur_size, cur_is_dir))

        return members

    # =========================================================================
    # ИНТЕЛЛЕКТУАЛЬНАЯ МАРШРУТИЗАЦИЯ ПУТЕЙ (SMART ROUTING)
    # =========================================================================

    def build_routing_plan(
        self,
        archive_path: str,
        game_root: str,
    ) -> Tuple[List[RoutedFile], bool, str]:
        """
        Анализирует структуру архива с помощью pathlib.PurePosixPath
        и строит план распаковки:
        1. Снятие внешней обёртки (wrapper folder stripping).
        2. Предотвращение дублирующихся вложений (r6/r6 -> r6).
        3. Маршрутизация одиночных файлов (loose files) по расширению.
        """
        members = self.list_archive_members(archive_path)
        file_members = [m for m in members if not m[2]]  # Исключаем чистые папки

        routed_files: List[RoutedFile] = []
        is_wrapped = False
        detected_wrapper = ""

        # Шаг 1: Проверяем, есть ли в архиве стандартные корневые папки (r6, archive, bin, mods, red4ext)
        has_standard_root = False
        for raw_path, _, _ in file_members:
            norm_parts = [p.lower() for p in PurePosixPath(raw_path).parts]
            if any(p in STANDARD_ROOT_DIRS for p in norm_parts):
                has_standard_root = True
                break

        # Шаг 2: Маршрутизация каждого файла
        for raw_path, size, _ in file_members:
            p = PurePosixPath(raw_path)
            parts = p.parts
            lower_parts = [part.lower() for part in parts]

            if has_standard_root:
                # Ищем первое вхождение стандартной папки
                root_idx = -1
                for idx, part_lower in enumerate(lower_parts):
                    if part_lower in STANDARD_ROOT_DIRS:
                        root_idx = idx
                        break

                if root_idx != -1:
                    # Фиксируем наличие обёртки
                    if root_idx > 0:
                        is_wrapped = True
                        if not detected_wrapper:
                            detected_wrapper = "/".join(parts[:root_idx])

                    # Срезаем обёртку
                    relevant_parts = list(parts[root_idx:])

                    # Проверяем и устраняем дублирование папок (например, r6/r6 -> r6, archive/archive -> archive)
                    cleaned_parts = [relevant_parts[0]]
                    skip_next = False
                    for i in range(1, len(relevant_parts)):
                        if skip_next:
                            skip_next = False
                            continue
                        # Если текущая часть совпадает с предыдущей стандартной корневой папкой
                        if i == 1 and relevant_parts[i].lower() == relevant_parts[0].lower():
                            continue
                        cleaned_parts.append(relevant_parts[i])

                    rel_path_str = "/".join(cleaned_parts)
                    rule = "wrapper_stripped" if root_idx > 0 else "standard_root_direct"
                else:
                    # Файл не внутри стандартной папки, но архив имеет стандартную структуру (например, readme.txt в корне)
                    # Проверяем по расширению одиночного файла
                    ext = p.suffix.lower()
                    if ext in LOOSE_EXTENSIONS_MAP:
                        target_dir = LOOSE_EXTENSIONS_MAP[ext]
                        rel_path_str = f"{target_dir}/{p.name}"
                        rule = f"loose_ext_{ext}"
                    else:
                        # Служебный/документационный файл
                        rel_path_str = f"mods_info/{p.name}"
                        rule = "doc_file"

            else:
                # В архиве НЕТ стандартных корневых папок (одиночные файлы)
                ext = p.suffix.lower()
                fname = p.name

                if ext in LOOSE_EXTENSIONS_MAP:
                    target_dir = LOOSE_EXTENSIONS_MAP[ext]
                    rel_path_str = f"{target_dir}/{fname}"
                    rule = f"loose_routed_{ext}"
                else:
                    # Проверяем, нет ли подпапок вроде 'scripts' или 'tweaks'
                    if "scripts" in lower_parts:
                        rel_path_str = f"r6/scripts/{fname}"
                        rule = "subfolder_scripts"
                    elif "tweaks" in lower_parts:
                        rel_path_str = f"r6/tweaks/{fname}"
                        rule = "subfolder_tweaks"
                    elif "mod" in lower_parts or "pc" in lower_parts:
                        rel_path_str = f"archive/pc/mod/{fname}"
                        rule = "subfolder_archive"
                    else:
                        rel_path_str = f"mods_info/{fname}"
                        rule = "loose_other"

            # Нормализация для Windows
            target_rel = os.path.normpath(rel_path_str)
            target_abs = os.path.normpath(os.path.join(game_root, target_rel))

            routed_files.append(
                RoutedFile(
                    archive_internal_path=raw_path,
                    target_relative_path=target_rel,
                    target_absolute_path=target_abs,
                    file_size=size,
                    rule_applied=rule,
                )
            )

        return routed_files, is_wrapped, detected_wrapper

    # =========================================================================
    # БЕЗОПАСНАЯ РАСПАКОВКА И ИЗВЛЕЧЕНИЕ
    # =========================================================================

    def extract_mod_archive(
        self,
        archive_path: str,
        game_root: str,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> ExtractionResult:
        """
        Выполняет полную распаковку архива с маршрутизацией файлов в game_root:
        - Создаёт необходимые целевые папки (parents=True, exist_ok=True).
        - Перезаписывает существующие файлы при обновлении.
        - Возвращает ExtractionResult со списком всех извлечённых путей.
        """
        archive_abs = os.path.abspath(archive_path)
        game_root_abs = os.path.abspath(game_root)

        if not os.path.isfile(archive_abs):
            return ExtractionResult(
                archive_path=archive_abs,
                game_root=game_root_abs,
                success=False,
                error_message=f"Файл архива не найден: {archive_abs}",
            )

        if not os.path.isdir(game_root_abs):
            return ExtractionResult(
                archive_path=archive_abs,
                game_root=game_root_abs,
                success=False,
                error_message=f"Директория игры не найдена: {game_root_abs}",
            )

        ext = os.path.splitext(archive_abs)[1].lower()

        try:
            # 1. Построение плана маршрутизации
            route_plan, is_wrapped, wrapper_name = self.build_routing_plan(
                archive_path=archive_abs,
                game_root=game_root_abs,
            )
        except Exception as plan_err:
            return ExtractionResult(
                archive_path=archive_abs,
                game_root=game_root_abs,
                success=False,
                error_message=f"Ошибка анализа структуры архива: {plan_err}",
            )

        extracted_rel_paths: List[str] = []
        extracted_abs_paths: List[str] = []
        errors: List[str] = []
        total_bytes = 0

        # 2. Выполнение извлечения в зависимости от формата
        try:
            if ext == ".zip":
                self._extract_zip(archive_abs, route_plan, extracted_rel_paths, extracted_abs_paths, errors, progress_cb)
            elif ext == ".7z":
                self._extract_7z(archive_abs, route_plan, extracted_rel_paths, extracted_abs_paths, errors, progress_cb)
            elif ext == ".rar":
                self._extract_rar(archive_abs, route_plan, extracted_rel_paths, extracted_abs_paths, errors, progress_cb)
            else:
                return ExtractionResult(
                    archive_path=archive_abs,
                    game_root=game_root_abs,
                    success=False,
                    error_message=f"Неподдерживаемый формат архива: {ext}",
                )
        except Exception as ext_err:
            return ExtractionResult(
                archive_path=archive_abs,
                game_root=game_root_abs,
                success=False,
                error_message=f"Ошибка распаковки: {ext_err}",
                errors=errors,
            )

        for p in extracted_abs_paths:
            if os.path.isfile(p):
                total_bytes += os.path.getsize(p)

        success = len(extracted_rel_paths) > 0 and len(errors) == 0

        return ExtractionResult(
            archive_path=archive_abs,
            game_root=game_root_abs,
            success=success,
            total_files_extracted=len(extracted_rel_paths),
            total_bytes_extracted=total_bytes,
            extracted_relative_paths=extracted_rel_paths,
            extracted_absolute_paths=extracted_abs_paths,
            routed_files=route_plan,
            errors=errors,
            is_wrapped=is_wrapped,
            wrapper_folder=wrapper_name,
        )

    def _extract_zip(
        self,
        archive_path: str,
        route_plan: List[RoutedFile],
        extracted_rel: List[str],
        extracted_abs: List[str],
        errors: List[str],
        progress_cb: Optional[Callable[[int, int, str], None]],
    ):
        """Прямая распаковка .zip без промежуточной временной папки."""
        total_items = len(route_plan)
        with zipfile.ZipFile(archive_path, "r") as zf:
            for idx, item in enumerate(route_plan, start=1):
                try:
                    target_p = Path(item.target_absolute_path)
                    # Создаём все родительские папки
                    target_p.parent.mkdir(parents=True, exist_ok=True)

                    with zf.open(item.archive_internal_path) as src_f, open(target_p, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)

                    extracted_rel.append(item.target_relative_path)
                    extracted_abs.append(str(target_p))

                    if progress_cb:
                        progress_cb(idx, total_items, item.target_relative_path)
                except Exception as e:
                    errors.append(f"Не удалось извлечь {item.archive_internal_path}: {e}")

    def _extract_7z(
        self,
        archive_path: str,
        route_plan: List[RoutedFile],
        extracted_rel: List[str],
        extracted_abs: List[str],
        errors: List[str],
        progress_cb: Optional[Callable[[int, int, str], None]],
    ):
        """Распаковка .7z через py7zr или 7-Zip CLI во временный каталог с маршрутизацией."""
        with tempfile.TemporaryDirectory(prefix="sbsh_7z_") as tmp_dir:
            extracted_tmp = False

            if HAS_PY7ZR:
                try:
                    with py7zr.SevenZipFile(archive_path, "r") as sz:
                        sz.extractall(path=tmp_dir)
                    extracted_tmp = True
                except Exception as py7z_err:
                    if not self.seven_zip_bin:
                        raise py7z_err

            if not extracted_tmp and self.seven_zip_bin:
                cmd = [self.seven_zip_bin, "x", os.path.abspath(archive_path), f"-o{tmp_dir}", "-y"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if res.returncode != 0:
                    raise RuntimeError(f"7-Zip CLI ошибка распаковки: {res.stderr}")
                extracted_tmp = True

            if not extracted_tmp:
                raise RuntimeError("Не удалось распаковать .7z (нет py7zr и 7-Zip CLI)")

            self._copy_from_temp(tmp_dir, route_plan, extracted_rel, extracted_abs, errors, progress_cb)

    def _extract_rar(
        self,
        archive_path: str,
        route_plan: List[RoutedFile],
        extracted_rel: List[str],
        extracted_abs: List[str],
        errors: List[str],
        progress_cb: Optional[Callable[[int, int, str], None]],
    ):
        """Распаковка .rar через rarfile или 7-Zip CLI во временный каталог с маршрутизацией."""
        with tempfile.TemporaryDirectory(prefix="sbsh_rar_") as tmp_dir:
            extracted_tmp = False

            if HAS_RARFILE:
                try:
                    with rarfile.RarFile(archive_path, "r") as rf:
                        rf.extractall(path=tmp_dir)
                    extracted_tmp = True
                except Exception as rar_err:
                    if not self.seven_zip_bin:
                        raise rar_err

            if not extracted_tmp and self.seven_zip_bin:
                cmd = [self.seven_zip_bin, "x", os.path.abspath(archive_path), f"-o{tmp_dir}", "-y"]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if res.returncode != 0:
                    raise RuntimeError(f"7-Zip CLI ошибка распаковки .rar: {res.stderr}")
                extracted_tmp = True

            if not extracted_tmp:
                raise RuntimeError("Не удалось распаковать .rar (установите WinRAR/unrar или 7-Zip)")

            self._copy_from_temp(tmp_dir, route_plan, extracted_rel, extracted_abs, errors, progress_cb)

    def _copy_from_temp(
        self,
        tmp_dir: str,
        route_plan: List[RoutedFile],
        extracted_rel: List[str],
        extracted_abs: List[str],
        errors: List[str],
        progress_cb: Optional[Callable[[int, int, str], None]],
    ):
        """Копирует распакованные файлы из временной директории по маршрутному плану."""
        total_items = len(route_plan)
        for idx, item in enumerate(route_plan, start=1):
            try:
                # Внутренний путь файла внутри временной папки
                src_path = Path(tmp_dir) / Path(item.archive_internal_path)
                target_p = Path(item.target_absolute_path)

                if not src_path.is_file():
                    # Попытка поиска по нечувствительному к регистру пути
                    alt_found = None
                    for root, _, files in os.walk(tmp_dir):
                        for f in files:
                            full = Path(root) / f
                            if str(full).lower().endswith(Path(item.archive_internal_path).name.lower()):
                                alt_found = full
                                break
                        if alt_found:
                            break
                    if alt_found and alt_found.is_file():
                        src_path = alt_found
                    else:
                        errors.append(f"Файл не найден во временной папке: {item.archive_internal_path}")
                        continue

                target_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, target_p)

                extracted_rel.append(item.target_relative_path)
                extracted_abs.append(str(target_p))

                if progress_cb:
                    progress_cb(idx, total_items, item.target_relative_path)
            except Exception as e:
                errors.append(f"Ошибка копирования {item.archive_internal_path}: {e}")


# Глобальный синглтон экстрактора
mod_archive_extractor = ModArchiveExtractor()
