"""
Unit-тесты для модуля mod_packs (Saved Mod Packs / Пользовательские коллекции модов).
"""

import os
import shutil
import tempfile
import unittest

from modules.mod_packs import ModPacksManager


class TestModPacks(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="sbsh_modpack_test_")
        self.packs_file = os.path.join(self.test_dir, "mod_packs.json")
        self.mods_dir = os.path.join(self.test_dir, "mods")
        self.game_dir = os.path.join(self.test_dir, "Cyberpunk 2077")

        os.makedirs(self.mods_dir, exist_ok=True)
        os.makedirs(os.path.join(self.game_dir, "archive", "pc", "mod"), exist_ok=True)

        self.manager = ModPacksManager(filepath=self.packs_file)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_and_persistent_storage(self):
        """
        Проверка создания мод-пака и сохранения при перезапуске (персистентность).
        """
        pack = self.manager.create_pack(
            name="Cyberpunk Essentials",
            game_name="Cyberpunk 2077",
            game_domain="cyberpunk2077",
            game_id=3333,
            description="Базовый набор модов",
            initial_mods=[
                {"mod_id": 107, "title": "Cyber Engine Tweaks", "source": "nexus"},
                {"mod_id": 4198, "title": "ArchiveXL", "source": "nexus"},
            ],
        )

        self.assertIsNotNone(pack)
        self.assertEqual(pack["name"], "Cyberpunk Essentials")
        self.assertEqual(len(pack["mods"]), 2)
        self.assertTrue(os.path.exists(self.packs_file))

        # Перезагружаем менеджер с диска
        new_manager = ModPacksManager(filepath=self.packs_file)
        loaded_pack = new_manager.get_pack(pack["id"])
        self.assertIsNotNone(loaded_pack)
        self.assertEqual(loaded_pack["name"], "Cyberpunk Essentials")
        self.assertEqual(len(loaded_pack["mods"]), 2)

    def test_add_and_remove_mods(self):
        """
        Проверка добавления и удаления модов в паке.
        """
        pack = self.manager.create_pack(name="Test Pack")
        pack_id = pack["id"]

        success, msg = self.manager.add_mod_to_pack(
            pack_id,
            {"mod_id": 2380, "title": "RED4ext", "source": "nexus"}
        )
        self.assertTrue(success)

        # Повторное добавление того же мода должно блокироваться
        success2, msg2 = self.manager.add_mod_to_pack(
            pack_id,
            {"mod_id": 2380, "title": "RED4ext", "source": "nexus"}
        )
        self.assertFalse(success2)

        # Удаление
        rem_res = self.manager.remove_mod_from_pack(pack_id, 2380)
        self.assertTrue(rem_res)
        updated = self.manager.get_pack(pack_id)
        self.assertEqual(len(updated["mods"]), 0)

    def test_status_ratios_and_percentages(self):
        """
        Проверка расчёта числовых соотношений и процентов скачанных модов.
        """
        # Создаем 2 мода: один положим физически в game_dir, второго нет
        mod1_archive = os.path.join(self.game_dir, "archive", "pc", "mod", "ArchiveXL.archive")
        with open(mod1_archive, "wb") as f:
            f.write(b"archivexl binary data")

        pack = self.manager.create_pack(
            name="Status Test Pack",
            initial_mods=[
                {"mod_id": 4198, "title": "ArchiveXL", "source": "nexus"},
                {"mod_id": 99999, "title": "Missing Mod", "source": "nexus"},
            ]
        )

        status = self.manager.check_pack_status(
            pack["id"],
            game_dir=self.game_dir,
            mods_dir=self.mods_dir,
        )

        self.assertEqual(status["total_mods"], 2)
        self.assertEqual(status["downloaded_count"], 1)
        self.assertEqual(status["pending_count"], 1)
        self.assertEqual(status["downloaded_percent"], 50.0)
        self.assertEqual(status["pending_percent"], 50.0)
        self.assertEqual(status["ratio_str"], "1 / 2")
        self.assertEqual(status["percent_str"], "50.0% Скачано • 50.0% Ожидает")
        self.assertFalse(status["is_complete"])

    def test_complete_pack_status(self):
        """
        Проверка когда 100% модов скачано.
        """
        mod_file = os.path.join(self.mods_dir, "my_mod.zip")
        with open(mod_file, "wb") as f:
            f.write(b"mod zip data")

        # Зарегистрируем в downloaded_mods
        from modules.downloaded_mods import downloaded_mods_manager
        downloaded_mods_manager.record_downloaded(
            source="nexus",
            mod_id=777,
            filepath=mod_file,
            filename="my_mod.zip",
            title="Single Mod",
        )

        pack = self.manager.create_pack(
            name="All Downloaded Pack",
            initial_mods=[
                {"mod_id": 777, "title": "Single Mod", "source": "nexus"}
            ]
        )

        status = self.manager.check_pack_status(
            pack["id"],
            game_dir=self.game_dir,
            mods_dir=self.mods_dir,
        )

        self.assertEqual(status["total_mods"], 1)
        self.assertEqual(status["downloaded_count"], 1)
        self.assertEqual(status["downloaded_percent"], 100.0)
        self.assertEqual(status["ratio_str"], "1 / 1")
        self.assertTrue(status["is_complete"])


if __name__ == "__main__":
    unittest.main()
