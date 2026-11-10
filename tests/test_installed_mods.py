import os
import tempfile
import unittest
import json
from modules.installed_mods import InstalledModsManager


class TestInstalledModsManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.json_path = os.path.join(self.temp_dir.name, "installed_mods.json")
        self.manager = InstalledModsManager(filepath=self.json_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_is_installed_signature_and_file_id(self):
        # Create a mock file on disk
        fake_file = os.path.join(self.temp_dir.name, "mod1.archive")
        with open(fake_file, "w") as f:
            f.write("dummy")

        self.manager.register_mod(
            source="nexus",
            mod_id=12345,
            file_id=67890,
            game_id=3333,
            title="Awesome Mod",
            file_name="mod1.archive",
            install_path=fake_file,
        )

        # 1. Match by source, mod_id, file_id and extra kwargs
        installed, data = self.manager.is_installed(
            source="nexus",
            mod_id=12345,
            game_id=3333,
            file_id=67890,
            unexpected_arg="should_be_ignored_by_kwargs",
        )
        self.assertTrue(installed)
        self.assertIsNotNone(data)
        self.assertEqual(data["file_id"], 67890)

        # 2. Match with different file_id should fallback if mod_id matches
        installed, data = self.manager.is_installed(
            source="nexus",
            mod_id=12345,
            game_id=3333,
            file_id=99999,
        )
        self.assertTrue(installed)
        self.assertIsNotNone(data)
        self.assertEqual(data["mod_id"], 12345)

        # 3. Non-existent mod
        installed, data = self.manager.is_installed(
            source="nexus",
            mod_id=54321,
            game_id=3333,
            file_id=11111,
        )
        self.assertFalse(installed)
        self.assertIsNone(data)

    def test_is_installed_check_disk(self):
        fake_file = os.path.join(self.temp_dir.name, "mod2.archive")
        self.manager.register_mod(
            source="nexus",
            mod_id=22222,
            file_id=33333,
            game_id=3333,
            title="Missing File Mod",
            install_path=fake_file,
        )

        # File does not exist on disk
        installed, data = self.manager.is_installed(
            source="nexus",
            mod_id=22222,
            file_id=33333,
            check_disk=True,
        )
        self.assertFalse(installed)
        self.assertIsNotNone(data)

        # With check_disk=False, returns True
        installed, data = self.manager.is_installed(
            source="nexus",
            mod_id=22222,
            file_id=33333,
            check_disk=False,
        )
        self.assertTrue(installed)
        self.assertIsNotNone(data)


if __name__ == "__main__":
    unittest.main()
