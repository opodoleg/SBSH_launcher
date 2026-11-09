"""
Unit-тесты для модуля duplicate_cleaner (поиск и очистка дубликатов модов).
"""

import os
import shutil
import tempfile
import time
import unittest

from modules.duplicate_cleaner import (
    DuplicateModCleaner,
    format_bytes,
    MOD_FILE_EXTENSIONS,
)


class TestDuplicateCleaner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="sbsh_dup_test_")
        self.mods_dir = os.path.join(self.test_dir, "mods")
        self.game_dir = os.path.join(self.test_dir, "Cyberpunk 2077")
        self.log_file = os.path.join(self.test_dir, "test_cleanup.log")

        # Создаем файловую структуру
        os.makedirs(self.mods_dir, exist_ok=True)
        os.makedirs(os.path.join(self.mods_dir, "Archives"), exist_ok=True)
        os.makedirs(os.path.join(self.game_dir, "archive", "pc", "mod"), exist_ok=True)
        os.makedirs(os.path.join(self.game_dir, "r6", "scripts"), exist_ok=True)

        self.cleaner = DuplicateModCleaner(
            mods_dir=self.mods_dir,
            game_dirs=[self.game_dir],
            log_filepath=self.log_file,
        )

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_matching_criteria_exact_name_and_extension(self):
        """
        Проверка критерия сопоставления:
        Файлы с одинаковым именем и расширением должны группироваться как дубликаты.
        Файлы с разными расширениями или разными именами — не дубликаты.
        """
        f1 = os.path.join(self.mods_dir, "AwesomeMod.archive")
        f2 = os.path.join(self.mods_dir, "Archives", "AwesomeMod.archive")
        f_diff_ext = os.path.join(self.mods_dir, "AwesomeMod.zip")
        f_diff_name = os.path.join(self.mods_dir, "OtherMod.archive")

        with open(f1, "wb") as f:
            f.write(b"mod content 1")
        time.sleep(0.05)
        with open(f2, "wb") as f:
            f.write(b"mod content 2")
        with open(f_diff_ext, "wb") as f:
            f.write(b"zip content")
        with open(f_diff_name, "wb") as f:
            f.write(b"other content")

        files = self.cleaner.scan_mod_files()
        self.assertEqual(len(files), 4)

        groups = self.cleaner.group_duplicates(files)
        # Должна быть только 1 группа дубликатов: awesomemod.archive
        self.assertEqual(len(groups), 1)
        self.assertIn("awesomemod.archive", groups)
        self.assertEqual(len(groups["awesomemod.archive"]), 2)

    def test_safe_retention_prefer_game_directory(self):
        """
        Проверка приоритета сохранения:
        Если один экземпляр находится в официальной папке игры (archive/pc/mod),
        а второй — в папке загрузок (mods/), то экземпляр в игре сохраняется,
        а дубликат в папке загрузок автоматически удаляется.
        """
        game_archive = os.path.join(self.game_dir, "archive", "pc", "mod", "CyberEngineTweaks.archive")
        download_archive = os.path.join(self.mods_dir, "CyberEngineTweaks.archive")

        with open(game_archive, "wb") as f:
            f.write(b"game installed mod bytes")
        with open(download_archive, "wb") as f:
            f.write(b"download duplicate mod bytes")

        report = self.cleaner.execute_cleanup(dry_run=False, retention_policy="game_dir_first")

        self.assertEqual(report.total_duplicates_found, 1)
        self.assertEqual(report.total_duplicates_removed, 1)
        self.assertTrue(os.path.exists(game_archive), "Файл в официальной папке игры должен остаться!")
        self.assertFalse(os.path.exists(download_archive), "Дубликат в папке загрузок должен быть удалён!")
        self.assertIn(os.path.abspath(download_archive), [item.removed_path for item in report.removed_files])
        self.assertIn(os.path.abspath(game_archive), [item.kept_path for item in report.removed_files])

    def test_safe_retention_prefer_newest_in_downloads(self):
        """
        Если оба файла находятся в папках загрузок (например, mods и mods/Archives),
        сохраняется самый свежий файл по дате изменения (mtime).
        """
        old_file = os.path.join(self.mods_dir, "TweakXL_1.12.0.zip")
        new_file = os.path.join(self.mods_dir, "Archives", "TweakXL_1.12.0.zip")

        with open(old_file, "wb") as f:
            f.write(b"older version content")
        # Устанавливаем старое время
        os.utime(old_file, (1000000000, 1000000000))

        with open(new_file, "wb") as f:
            f.write(b"newer version content")
        # Устанавливаем более новое время
        os.utime(new_file, (1700000000, 1700000000))

        report = self.cleaner.execute_cleanup(dry_run=False, retention_policy="game_dir_first")

        self.assertEqual(report.total_duplicates_removed, 1)
        self.assertTrue(os.path.exists(new_file), "Более свежий файл должен сохраниться!")
        self.assertFalse(os.path.exists(old_file), "Устаревший дубликат должен быть удалён!")

    def test_dry_run_mode(self):
        """
        В режиме dry_run=True файлы физически не удаляются,
        но отчёт формируется и показывает потенциально освобождаемое место.
        """
        f1 = os.path.join(self.mods_dir, "ScriptMod.reds")
        f2 = os.path.join(self.mods_dir, "Archives", "ScriptMod.reds")

        content = b"x" * 2048
        with open(f1, "wb") as f:
            f.write(content)
        with open(f2, "wb") as f:
            f.write(content)

        report = self.cleaner.execute_cleanup(dry_run=True)

        self.assertTrue(report.is_dry_run)
        self.assertEqual(report.total_duplicates_found, 1)
        self.assertEqual(report.total_duplicates_removed, 0)
        self.assertEqual(report.total_bytes_freed, 2048)
        self.assertTrue(os.path.exists(f1), "В режиме dry_run f1 не должен удаляться!")
        self.assertTrue(os.path.exists(f2), "В режиме dry_run f2 не должен удаляться!")

    def test_logging_and_report_file(self):
        """
        Проверка записи подробного отчёта в duplicate_cleanup.log.
        """
        f1 = os.path.join(self.mods_dir, "Dupe.archive")
        f2 = os.path.join(self.mods_dir, "Archives", "Dupe.archive")

        with open(f1, "wb") as f:
            f.write(b"abc" * 100)
        with open(f2, "wb") as f:
            f.write(b"abc" * 100)

        report = self.cleaner.execute_cleanup(dry_run=False)

        self.assertTrue(os.path.exists(self.log_file), "Файл лога должен быть создан!")
        with open(self.log_file, "r", encoding="utf-8") as lf:
            log_content = lf.read()

        self.assertIn("ОТЧЁТ ОБ ОЧИСТКЕ ДУБЛИКАТОВ МОДОВ", log_content)
        self.assertIn("Dupe.archive", log_content)
        self.assertIn("Удалённый дубликат:", log_content)
        self.assertIn("Сохранён оригинал:", log_content)

    def test_registry_synchronization(self):
        """
        Проверка синхронизации с InstalledModsManager и DownloadedModsManager.
        """
        from modules.installed_mods import installed_mods_manager
        from modules.downloaded_mods import downloaded_mods_manager

        game_file = os.path.join(self.game_dir, "archive", "pc", "mod", "RegTest.archive")
        download_file = os.path.join(self.mods_dir, "RegTest.archive")

        with open(game_file, "wb") as f:
            f.write(b"game data")
        with open(download_file, "wb") as f:
            f.write(b"download data")

        # Регистрируем скачанный файл в installed_mods
        entry = installed_mods_manager.register_mod(
            source="nexus",
            mod_id=99999,
            title="Registry Test Mod",
            install_path=download_file,
            file_name="RegTest.archive",
            mod_type="Archive",
        )

        # Выполняем очистку (скачанный файл удалится, игровой сохранится)
        report = self.cleaner.execute_cleanup(dry_run=False, retention_policy="game_dir_first")

        self.assertEqual(report.total_duplicates_removed, 1)
        self.assertFalse(os.path.exists(download_file))
        self.assertTrue(os.path.exists(game_file))

        # Проверяем, что в реестре путь обновился на сохранённый игровой файл
        is_inst, inst = installed_mods_manager.is_installed(source="nexus", mod_id=99999, check_disk=False)
        self.assertTrue(is_inst)
        self.assertIsNotNone(inst)
        self.assertEqual(os.path.abspath(inst.get("install_path")), os.path.abspath(game_file))

        # Очищаем тестовую запись
        installed_mods_manager.unregister_mod(inst.get("key"), delete_files=False)


if __name__ == "__main__":
    unittest.main()

