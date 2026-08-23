import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class AppSettingsTests(unittest.TestCase):
    def setUp(self):
        # Fresh import state for each test — reset module cache
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        # set() now refuses to write unless JARVIS_REAL_SESSION=1 (guards the
        # real user's config against stray scripts/tests — see
        # NOTE_settings_persistence.md). Every test here always patches
        # _FILE to a temp path first, so opting in is safe: it never touches
        # the real %LOCALAPPDATA%\Jarvis file.
        env_patcher = patch.dict(os.environ, {"JARVIS_REAL_SESSION": "1"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def test_get_returns_default_for_missing_key(self):
        with patch("actions.app_settings._FILE") as mock_file, \
             patch("actions.app_settings._cache", None):
            mock_file.read_text.side_effect = FileNotFoundError()
            from actions import app_settings
            app_settings._cache = {}

            result = app_settings.get("nonexistent")

            self.assertIsNone(result)

    def test_get_returns_registered_default(self):
        with patch("actions.app_settings._FILE") as mock_file:
            mock_file.read_text.side_effect = FileNotFoundError()
            from actions import app_settings
            app_settings._cache = {}

            result = app_settings.get("crossfade_seconds")

            self.assertEqual(result, 3)

    def test_get_returns_custom_default_if_provided(self):
        with patch("actions.app_settings._FILE") as mock_file:
            mock_file.read_text.side_effect = FileNotFoundError()
            from actions import app_settings
            app_settings._cache = {}

            result = app_settings.get("unknown_key", "my_default")

            self.assertEqual(result, "my_default")

    def test_get_returns_stored_value_over_defaults(self):
        with patch("actions.app_settings._FILE") as mock_file:
            mock_file.read_text.return_value = json.dumps({"crossfade_seconds": 5})
            from actions import app_settings
            app_settings._cache = None

            result = app_settings.get("crossfade_seconds")

            self.assertEqual(result, 5)

    def test_all_settings_merges_defaults_with_loaded(self):
        with patch("actions.app_settings._FILE") as mock_file:
            mock_file.read_text.return_value = json.dumps({"custom_key": "custom_value"})
            from actions import app_settings
            app_settings._cache = None

            result = app_settings.all_settings()

            self.assertIn("crossfade_seconds", result)
            self.assertEqual(result["crossfade_seconds"], 3)
            self.assertEqual(result["custom_key"], "custom_value")

    def test_set_writes_to_file(self):
        real_file = Path(self.temp_dir.name) / "app_settings.json"
        with patch("actions.app_settings._FILE", real_file), \
             patch("actions.app_settings._BACKUP_FILE", None):
            from actions import app_settings
            app_settings._cache = {}

            app_settings.set("my_key", "my_value")

            data = json.loads(real_file.read_text(encoding="utf-8"))
            self.assertEqual(data["my_key"], "my_value")

    def test_set_updates_cache(self):
        real_file = Path(self.temp_dir.name) / "app_settings.json"
        with patch("actions.app_settings._FILE", real_file), \
             patch("actions.app_settings._BACKUP_FILE", None):
            from actions import app_settings
            app_settings._cache = None

            app_settings.set("test_key", "test_value")
            result = app_settings.get("test_key")

            self.assertEqual(result, "test_value")

    def test_set_catches_io_errors_gracefully(self):
        with patch("actions.app_settings._FILE") as mock_file:
            mock_file.read_text.side_effect = FileNotFoundError()
            mock_file.parent.mkdir.side_effect = IOError("disk full")
            from actions import app_settings
            app_settings._cache = None

            # Should not raise
            app_settings.set("key", "value")

    def test_set_recovers_from_corrupt_primary_via_backup(self):
        real_file = Path(self.temp_dir.name) / "app_settings.json"
        backup_file = Path(self.temp_dir.name) / "app_settings.json.bak"
        backup_file.write_text(json.dumps({"kept_key": "kept_value"}), encoding="utf-8")
        real_file.write_text("not json {{", encoding="utf-8")
        with patch("actions.app_settings._FILE", real_file), \
             patch("actions.app_settings._BACKUP_FILE", None):
            from actions import app_settings
            app_settings._cache = None

            result = app_settings.get("kept_key")

            self.assertEqual(result, "kept_value")

    def test_set_does_not_clobber_key_written_after_cache_populated(self):
        """Regression test for the real recurring bug: set() used to reuse a
        stale module-level cache as the base dict, so any key that appeared
        on disk AFTER the cache was first loaded got silently deleted on the
        next write. set() must always re-read disk fresh before merging."""
        real_file = Path(self.temp_dir.name) / "app_settings.json"
        with patch("actions.app_settings._FILE", real_file), \
             patch("actions.app_settings._BACKUP_FILE", None):
            from actions import app_settings
            app_settings._cache = None

            # Populate the read cache with an early on-disk state.
            app_settings.set("first_key", "first_value")
            app_settings.get("first_key")

            # Simulate another writer (a restart, another process) adding a
            # key directly on disk, behind this process's cached back.
            data = json.loads(real_file.read_text(encoding="utf-8"))
            data["other_writer_key"] = "other_writer_value"
            real_file.write_text(json.dumps(data), encoding="utf-8")

            # This process's cache is still stale (doesn't know about
            # other_writer_key). Its next set() must not lose it.
            app_settings.set("first_key", "updated_value")

            on_disk = json.loads(real_file.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["first_key"], "updated_value")
            self.assertEqual(on_disk["other_writer_key"], "other_writer_value")

    def test_load_handles_invalid_json(self):
        with patch("actions.app_settings._FILE") as mock_file:
            mock_file.read_text.return_value = "not json {{"
            from actions import app_settings
            app_settings._cache = None

            result = app_settings.get("any_key", "fallback")

            self.assertEqual(result, "fallback")

    def test_load_handles_non_dict_json(self):
        with patch("actions.app_settings._FILE") as mock_file:
            mock_file.read_text.return_value = '"not a dict"'
            from actions import app_settings
            app_settings._cache = None

            result = app_settings.get("any_key", "fallback")

            self.assertEqual(result, "fallback")



if __name__ == "__main__":
    unittest.main()
