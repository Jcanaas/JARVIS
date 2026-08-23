import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class BackgroundMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        real_file = Path(self.temp_dir.name) / "app_settings.json"

        env_patcher = patch.dict(os.environ, {"JARVIS_REAL_SESSION": "1"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        file_patcher = patch("actions.app_settings._FILE", real_file)
        file_patcher.start()
        self.addCleanup(file_patcher.stop)

        backup_patcher = patch("actions.app_settings._BACKUP_FILE", None)
        backup_patcher.start()
        self.addCleanup(backup_patcher.stop)

        from actions import app_settings
        app_settings._cache = None

    def test_subscribe_unsubscribe_roundtrip(self):
        from actions import background_monitor, app_settings

        msg = background_monitor.subscribe("F1")
        self.assertIn("F1", msg)
        self.assertIn("F1", app_settings.get("monitor_topics", []))

        # Duplicate subscribe is a no-op message, not a duplicate entry.
        background_monitor.subscribe("f1")
        self.assertEqual(app_settings.get("monitor_topics", []).count("F1"), 1)

        msg = background_monitor.unsubscribe("f1")
        self.assertIn("f1", msg)
        self.assertNotIn("F1", app_settings.get("monitor_topics", []))

    def test_list_topics_empty_and_populated(self):
        from actions import background_monitor

        self.assertIn("Not monitoring", background_monitor.list_topics())

        background_monitor.subscribe("Python releases")
        result = background_monitor.list_topics()
        self.assertIn("Python releases", result)

    def test_check_all_topics_dedupes_seen_hashes(self):
        from actions import background_monitor, app_settings

        background_monitor.subscribe("F1")

        fake_article = {
            "title": "New F1 rule announced",
            "snippet": "Something happened.",
            "url": "https://example.com/f1",
            "source": "ExampleNews",
        }

        with patch("actions.background_monitor._ddg_news_min", return_value=[fake_article]):
            first = background_monitor.check_all_topics()
            self.assertIn("F1", first)
            self.assertEqual(len(first["F1"]), 1)

            second = background_monitor.check_all_topics()
            self.assertEqual(second, {})

        seen = app_settings.get("monitor_seen_hashes", {})
        self.assertIn("F1", seen)
        self.assertEqual(len(seen["F1"]), 1)

    def test_unsubscribe_drops_seen_hashes(self):
        from actions import background_monitor, app_settings

        background_monitor.subscribe("F1")
        fake_article = {"title": "X", "snippet": "", "url": "https://example.com", "source": ""}
        with patch("actions.background_monitor._ddg_news_min", return_value=[fake_article]):
            background_monitor.check_all_topics()

        self.assertIn("F1", app_settings.get("monitor_seen_hashes", {}))
        background_monitor.unsubscribe("F1")
        self.assertNotIn("F1", app_settings.get("monitor_seen_hashes", {}))

    def test_build_alert_prompt_format(self):
        from actions import background_monitor

        new_by_topic = {
            "F1": [{"title": "Verstappen wins", "source": "Motorsport", "snippet": "Race recap."}]
        }
        prompt = background_monitor.build_alert_prompt(new_by_topic)
        self.assertIn("F1", prompt)
        self.assertIn("Verstappen wins", prompt)
        self.assertIn("[TOPIC_MONITOR_ALERT]", prompt)


if __name__ == "__main__":
    unittest.main()
