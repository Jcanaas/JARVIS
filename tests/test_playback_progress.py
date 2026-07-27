import unittest
from unittest.mock import Mock, patch

from actions import ytmusic_headless
from ui import MainWindow


class _FakeSlider:
    def __init__(self):
        self.values = []
        self.signal_blocks = []

    def blockSignals(self, blocked):
        self.signal_blocks.append(blocked)

    def setValue(self, value):
        self.values.append(value)


class _FakeLabel:
    def __init__(self):
        self.texts = []

    def setText(self, text):
        self.texts.append(text)


class _FakePlaybackWindow:
    def __init__(self):
        self._user_dragging = False
        self._play_playing = True
        self._play_duration = 200.0
        self._play_title = "Test track"
        self._play_position = 0.0
        self._play_position_anchor = 0.0
        self._play_position_anchor_ts = 100.0
        self._play_ready = True
        self._play_buffering = False
        self._slider = _FakeSlider()
        self._time_lbl = _FakeLabel()


class PlaybackProgressTests(unittest.TestCase):
    def _current_from_background_meta(self, **metadata):
        old_thread = ytmusic_headless._autoplay_thread
        old_proc = ytmusic_headless._procs[ytmusic_headless._active_slot]
        old_meta = dict(ytmusic_headless._last_meta)
        thread = Mock()
        thread.is_alive.return_value = True
        process = Mock()
        process.poll.return_value = None
        ytmusic_headless._autoplay_thread = thread
        ytmusic_headless._procs[ytmusic_headless._active_slot] = process
        ytmusic_headless._last_meta.clear()
        ytmusic_headless._last_meta.update(
            {
                "title": "Test track",
                "artists": "Test artist",
                "videoId": "test-video",
                "position": 12.0,
                "duration": 200.0,
                "playing": True,
                "_sampled_at": 100.0,
                "_started": True,
                **metadata,
            }
        )
        try:
            return ytmusic_headless.current()
        finally:
            ytmusic_headless._autoplay_thread = old_thread
            ytmusic_headless._procs[ytmusic_headless._active_slot] = old_proc
            ytmusic_headless._last_meta.clear()
            ytmusic_headless._last_meta.update(old_meta)

    @patch("actions.ytmusic_headless._get_mpv_property")
    def test_current_preserves_subsecond_position(self, get_property):
        values = {
            "time-pos": 12.625,
            "pause": False,
            "duration": 245.75,
        }
        get_property.side_effect = values.get
        old_proc = ytmusic_headless._procs[ytmusic_headless._active_slot]
        old_thread = ytmusic_headless._autoplay_thread
        process = Mock()
        process.poll.return_value = None
        ytmusic_headless._procs[ytmusic_headless._active_slot] = process
        ytmusic_headless._autoplay_thread = None
        try:
            result = ytmusic_headless.current()
        finally:
            ytmusic_headless._procs[ytmusic_headless._active_slot] = old_proc
            ytmusic_headless._autoplay_thread = old_thread

        self.assertEqual(result["position"], 12.625)
        self.assertEqual(result["duration"], 245.75)

    @patch("actions.ytmusic_headless.time.monotonic", return_value=101.25)
    def test_current_extrapolates_background_sample_age(self, _monotonic):
        old_thread = ytmusic_headless._autoplay_thread
        old_proc = ytmusic_headless._procs[ytmusic_headless._active_slot]
        old_meta = dict(ytmusic_headless._last_meta)
        thread = Mock()
        thread.is_alive.return_value = True
        process = Mock()
        process.poll.return_value = None
        ytmusic_headless._procs[ytmusic_headless._active_slot] = process
        ytmusic_headless._autoplay_thread = thread
        ytmusic_headless._last_meta.update(
            {
                "position": 40.0,
                "duration": 200.0,
                "playing": True,
                "_sampled_at": 100.75,
                "_started": True,
                "ready": True,
                "buffering": False,
                "state": "playing",
            }
        )
        try:
            result = ytmusic_headless.current()
        finally:
            ytmusic_headless._autoplay_thread = old_thread
            ytmusic_headless._procs[ytmusic_headless._active_slot] = old_proc
            ytmusic_headless._last_meta.clear()
            ytmusic_headless._last_meta.update(old_meta)

        self.assertEqual(result["position"], 40.5)
        self.assertNotIn("_sampled_at", result)

    @patch("actions.ytmusic_headless.time.monotonic", return_value=110.0)
    def test_current_exposes_phase_and_only_extrapolates_while_playing(self, _monotonic):
        cases = (
            {
                "name": "not ready",
                "ready": False,
                "buffering": False,
                "state": "loading",
                "expected_position": 12.0,
            },
            {
                "name": "buffering",
                "ready": True,
                "buffering": True,
                "state": "buffering",
                "expected_position": 12.0,
            },
            {
                "name": "playing",
                "ready": True,
                "buffering": False,
                "state": "playing",
                "expected_position": 14.0,
            },
        )

        for case in cases:
            with self.subTest(case["name"]):
                result = self._current_from_background_meta(
                    ready=case["ready"],
                    buffering=case["buffering"],
                    state=case["state"],
                )

                self.assertEqual(result["ready"], case["ready"])
                self.assertEqual(result["buffering"], case["buffering"])
                self.assertEqual(result["state"], case["state"])
                self.assertEqual(result["position"], case["expected_position"])

    @patch("actions.ytmusic_headless.time.monotonic", return_value=130.0)
    def test_current_caps_extrapolation_from_stale_background_sample(self, _monotonic):
        result = self._current_from_background_meta(
            position=40.0,
            ready=True,
            buffering=False,
            state="playing",
            _sampled_at=100.0,
        )

        self.assertGreater(result["position"], 40.0)
        self.assertLessEqual(result["position"], 42.0)

    def test_current_marks_playback_stopped_when_mpv_process_died(self):
        old_thread = ytmusic_headless._autoplay_thread
        old_proc = ytmusic_headless._procs[ytmusic_headless._active_slot]
        old_meta = dict(ytmusic_headless._last_meta)
        thread = Mock()
        thread.is_alive.return_value = True
        process = Mock()
        process.poll.return_value = 1
        ytmusic_headless._autoplay_thread = thread
        ytmusic_headless._procs[ytmusic_headless._active_slot] = process
        ytmusic_headless._last_meta.update({"playing": True, "_sampled_at": 100.0})
        try:
            result = ytmusic_headless.current()
        finally:
            ytmusic_headless._autoplay_thread = old_thread
            ytmusic_headless._procs[ytmusic_headless._active_slot] = old_proc
            ytmusic_headless._last_meta.clear()
            ytmusic_headless._last_meta.update(old_meta)

        self.assertFalse(result["playing"])
        self.assertNotIn("_sampled_at", result)

    @patch("ui.time.monotonic", side_effect=(100.25, 100.5, 100.75))
    def test_ui_progress_keeps_zero_anchor_and_advances_linearly(self, _monotonic):
        window = _FakePlaybackWindow()

        positions = []
        for _ in range(3):
            MainWindow._tick_playback_progress(window)
            positions.append(window._play_position)

        self.assertEqual(positions, [0.25, 0.5, 0.75])
        self.assertEqual(window._play_position_anchor, 0.0)

    def test_ui_progress_waits_until_ready_and_not_buffering(self):
        cases = (
            {"name": "not ready", "ready": False, "buffering": False},
            {"name": "buffering", "ready": True, "buffering": True},
        )

        for case in cases:
            with self.subTest(case["name"]):
                window = _FakePlaybackWindow()
                window._play_position = 7.0
                window._play_position_anchor = 7.0
                window._play_ready = case["ready"]
                window._play_buffering = case["buffering"]

                with patch("ui.time.monotonic", return_value=110.0):
                    MainWindow._tick_playback_progress(window)

                self.assertEqual(window._play_position, 7.0)
                self.assertEqual(window._slider.values, [])
                self.assertEqual(window._time_lbl.texts, [])


if __name__ == "__main__":
    unittest.main()
