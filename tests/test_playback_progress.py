import unittest
from unittest.mock import Mock, patch

from actions import ytmusic_headless


class PlaybackProgressTests(unittest.TestCase):
    @patch("actions.ytmusic_headless._get_mpv_property")
    def test_current_preserves_subsecond_position(self, get_property):
        values = {
            "time-pos": 12.625,
            "pause": False,
            "duration": 245.75,
        }
        get_property.side_effect = values.get
        old_proc = ytmusic_headless._proc
        old_thread = ytmusic_headless._autoplay_thread
        process = Mock()
        process.poll.return_value = None
        ytmusic_headless._proc = process
        ytmusic_headless._autoplay_thread = None
        try:
            result = ytmusic_headless.current()
        finally:
            ytmusic_headless._proc = old_proc
            ytmusic_headless._autoplay_thread = old_thread

        self.assertEqual(result["position"], 12.625)
        self.assertEqual(result["duration"], 245.75)

    @patch("actions.ytmusic_headless.time.monotonic", return_value=101.25)
    def test_current_extrapolates_background_sample_age(self, _monotonic):
        old_thread = ytmusic_headless._autoplay_thread
        old_proc = ytmusic_headless._proc
        old_meta = dict(ytmusic_headless._last_meta)
        thread = Mock()
        thread.is_alive.return_value = True
        process = Mock()
        process.poll.return_value = None
        ytmusic_headless._proc = process
        ytmusic_headless._autoplay_thread = thread
        ytmusic_headless._last_meta.update(
            {
                "position": 40.0,
                "duration": 200.0,
                "playing": True,
                "_sampled_at": 100.75,
            }
        )
        try:
            result = ytmusic_headless.current()
        finally:
            ytmusic_headless._autoplay_thread = old_thread
            ytmusic_headless._proc = old_proc
            ytmusic_headless._last_meta.clear()
            ytmusic_headless._last_meta.update(old_meta)

        self.assertEqual(result["position"], 40.5)
        self.assertNotIn("_sampled_at", result)

    def test_current_marks_playback_stopped_when_mpv_process_died(self):
        old_thread = ytmusic_headless._autoplay_thread
        old_proc = ytmusic_headless._proc
        old_meta = dict(ytmusic_headless._last_meta)
        thread = Mock()
        thread.is_alive.return_value = True
        process = Mock()
        process.poll.return_value = 1
        ytmusic_headless._autoplay_thread = thread
        ytmusic_headless._proc = process
        ytmusic_headless._last_meta.update({"playing": True, "_sampled_at": 100.0})
        try:
            result = ytmusic_headless.current()
        finally:
            ytmusic_headless._autoplay_thread = old_thread
            ytmusic_headless._proc = old_proc
            ytmusic_headless._last_meta.clear()
            ytmusic_headless._last_meta.update(old_meta)

        self.assertFalse(result["playing"])
        self.assertNotIn("_sampled_at", result)


if __name__ == "__main__":
    unittest.main()
