import json
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
