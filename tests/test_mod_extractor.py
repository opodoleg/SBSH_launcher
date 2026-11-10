"""
Unit-тесты для модуля mod_extractor (распаковка и интеллектуальная маршрутизация архивов модов).
"""

import os
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import py7zr

from modules.mod_extractor import ModArchiveExtractor


class TestModExtractor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="sbsh_extract_test_")
        self.game_root = os.path.join(self.test_dir, "Cyberpunk 2077")
        os.makedirs(self.game_root, exist_ok=True)
        self.extractor = ModArchiveExtractor()

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_direct_standard_roots_zip(self):
        """
        Архив со стандартными корневыми папками (r6, archive, bin) без обёртки.
        """
        zip_path = os.path.join(self.test_dir, "direct_mod.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("archive/pc/mod/awesome_weapon.archive", b"archive data")
            zf.writestr("r6/scripts/awesome_script.reds", b"redscript data")
            zf.writestr("r6/tweaks/awesome_tweak.yaml", b"yaml data")

        res = self.extractor.extract_mod_archive(zip_path, self.game_root)
        self.assertTrue(res.success)
        self.assertFalse(res.is_wrapped)
        self.assertEqual(res.total_files_extracted, 3)

        # Проверяем физические файлы
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "archive", "pc", "mod", "awesome_weapon.archive")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "r6", "scripts", "awesome_script.reds")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "r6", "tweaks", "awesome_tweak.yaml")))

        # Проверяем возвращаемый список относительных путей
        norm_rels = [os.path.normpath(p) for p in res.extracted_relative_paths]
        self.assertIn(os.path.normpath("archive/pc/mod/awesome_weapon.archive"), norm_rels)
        self.assertIn(os.path.normpath("r6/scripts/awesome_script.reds"), norm_rels)
        self.assertIn(os.path.normpath("r6/tweaks/awesome_tweak.yaml"), norm_rels)

    def test_wrapper_folder_stripping(self):
        """
        Архив с папкой-обёрткой (ModName_v1.0/archive/pc/mod/...) -> обёртка срезается.
        """
        zip_path = os.path.join(self.test_dir, "wrapped_mod.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("CyberEngineTweaks_v1.38/bin/x64/plugins/cyber_engine_tweaks/init.lua", b"cet lua")
            zf.writestr("CyberEngineTweaks_v1.38/r6/tweaks/cet_tweak.yaml", b"tweak data")

        res = self.extractor.extract_mod_archive(zip_path, self.game_root)
        self.assertTrue(res.success)
        self.assertTrue(res.is_wrapped)
        self.assertEqual(res.wrapper_folder, "CyberEngineTweaks_v1.38")

        # Файлы должны оказаться прямо в game_root/bin и game_root/r6 без префикса CyberEngineTweaks_v1.38
        self.assertTrue(os.path.isfile(
            os.path.join(self.game_root, "bin", "x64", "plugins", "cyber_engine_tweaks", "init.lua")
        ))
        self.assertTrue(os.path.isfile(
            os.path.join(self.game_root, "r6", "tweaks", "cet_tweak.yaml")
        ))

    def test_prevent_nested_path_duplicates(self):
        """
        Защита от вложенных ошибок автора мода: r6/r6/scripts/... -> r6/scripts/...
        """
        zip_path = os.path.join(self.test_dir, "nested_bug_mod.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("r6/r6/scripts/fix_script.reds", b"script bytes")
            zf.writestr("archive/archive/pc/mod/fix_mod.archive", b"archive bytes")

        res = self.extractor.extract_mod_archive(zip_path, self.game_root)
        self.assertTrue(res.success)

        # Не должно быть r6/r6 или archive/archive
        self.assertFalse(os.path.exists(os.path.join(self.game_root, "r6", "r6")))
        self.assertFalse(os.path.exists(os.path.join(self.game_root, "archive", "archive")))

        # Должно быть правильно: r6/scripts/fix_script.reds
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "r6", "scripts", "fix_script.reds")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "archive", "pc", "mod", "fix_mod.archive")))

    def test_loose_files_extension_routing(self):
        """
        Одиночные файлы без стандартных корневых папок раскладываются по расширению:
        .archive -> archive/pc/mod/
        .reds -> r6/scripts/
        .yaml / .tweaks -> r6/tweaks/
        """
        zip_path = os.path.join(self.test_dir, "loose_files.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("StandaloneGun.archive", b"gun archive")
            zf.writestr("StandaloneGun.xl", b"gun xl")
            zf.writestr("AimLogic.reds", b"redscript")
            zf.writestr("WeaponStats.yaml", b"yaml tweak")
            zf.writestr("DamageConfig.tweaks", b"tweaks file")

        res = self.extractor.extract_mod_archive(zip_path, self.game_root)
        self.assertTrue(res.success)
        self.assertEqual(res.total_files_extracted, 5)

        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "archive", "pc", "mod", "StandaloneGun.archive")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "archive", "pc", "mod", "StandaloneGun.xl")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "r6", "scripts", "AimLogic.reds")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "r6", "tweaks", "WeaponStats.yaml")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "r6", "tweaks", "DamageConfig.tweaks")))

    def test_7z_archive_extraction(self):
        """
        Проверка распаковки .7z архива через py7zr.
        """
        s7_path = os.path.join(self.test_dir, "archive_test.7z")
        with py7zr.SevenZipFile(s7_path, "w") as sz:
            sz.writestr(b"archivexl data 7z", "Wrapper_Mod/archive/pc/mod/ArchiveXL.archive")
            sz.writestr(b"tweakxl data 7z", "Wrapper_Mod/r6/tweaks/TweakXL.yaml")

        res = self.extractor.extract_mod_archive(s7_path, self.game_root)
        self.assertTrue(res.success)
        self.assertTrue(res.is_wrapped)
        self.assertEqual(res.total_files_extracted, 2)

        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "archive", "pc", "mod", "ArchiveXL.archive")))
        self.assertTrue(os.path.isfile(os.path.join(self.game_root, "r6", "tweaks", "TweakXL.yaml")))

    def test_overwrite_existing_files_on_update(self):
        """
        Проверка безопасной перезаписи файлов при обновлении мода.
        """
        target_file = os.path.join(self.game_root, "r6", "scripts", "UpdateTest.reds")
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, "wb") as f:
            f.write(b"version 1.0 old")

        zip_path = os.path.join(self.test_dir, "update_mod.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("r6/scripts/UpdateTest.reds", b"version 2.0 updated")

        res = self.extractor.extract_mod_archive(zip_path, self.game_root)
        self.assertTrue(res.success)

        with open(target_file, "rb") as f:
            new_content = f.read()
        self.assertEqual(new_content, b"version 2.0 updated")


if __name__ == "__main__":
    unittest.main()
