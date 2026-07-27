import unittest
from unittest.mock import patch

from actions import ytmusic_headless


class PlaybackReadinessTests(unittest.TestCase):
    @staticmethod
    def _snapshot(**overrides):
        state = {
            "time-pos": 0.0,
            "duration": 180.0,
            "pause": False,
            "eof-reached": False,
            "idle-active": False,
            "core-idle": False,
            "paused-for-cache": False,
            "seeking": False,
            "audio-out-params": {
                "format": "float",
                "samplerate": 48000,
                "channels": "stereo",
            },
            "current-ao": "wasapi",
        }
        state.update(overrides)
        return state

    def test_zero_position_is_ready_only_after_real_audio_output_opened(self):
        self.assertTrue(
            ytmusic_headless._mpv_audio_ready(self._snapshot())
        )
        self.assertFalse(
            ytmusic_headless._mpv_audio_ready(
                self._snapshot(**{"audio-out-params": None})
            )
        )
        self.assertFalse(
            ytmusic_headless._mpv_audio_ready(
                self._snapshot(**{"current-ao": "null"})
            )
        )

    def test_playback_phase_distinguishes_loading_buffering_and_playing(self):
        loading = ytmusic_headless._derive_playback_phase(
            self._snapshot(
                **{"audio-out-params": None, "current-ao": None, "core-idle": True}
            ),
            was_ready=False,
            requested_playing=True,
        )
        self.assertEqual(
            {key: loading[key] for key in ("ready", "buffering", "state", "progressing")},
            {"ready": False, "buffering": False, "state": "loading", "progressing": False},
        )

        buffering = ytmusic_headless._derive_playback_phase(
            self._snapshot(**{"paused-for-cache": True, "core-idle": True}),
            was_ready=True,
            requested_playing=True,
        )
        self.assertEqual(
            {key: buffering[key] for key in ("ready", "buffering", "state", "progressing")},
            {"ready": True, "buffering": True, "state": "buffering", "progressing": False},
        )

        playing = ytmusic_headless._derive_playback_phase(
            self._snapshot(),
            was_ready=False,
            requested_playing=True,
        )
        self.assertEqual(
            {key: playing[key] for key in ("ready", "buffering", "state", "progressing")},
            {"ready": True, "buffering": False, "state": "playing", "progressing": True},
        )

    def test_wait_for_audio_ready_accepts_position_zero(self):
        snapshots = iter(
            [
                self._snapshot(**{"audio-out-params": None, "current-ao": None}),
                self._snapshot(),
            ]
        )
        with (
            patch.object(
                ytmusic_headless,
                "_read_mpv_playback_state",
                side_effect=lambda pipe=None: next(snapshots),
            ),
            patch("actions.ytmusic_headless.time.monotonic", side_effect=[0.0, 0.0, 0.1]),
            patch("actions.ytmusic_headless.time.sleep"),
        ):
            self.assertTrue(
                ytmusic_headless._wait_for_audio_ready(
                    pipe="test-pipe", timeout=1.0, poll_interval=0.01
                )
            )

    def test_apply_snapshot_updates_public_contract_atomically(self):
        old_meta = dict(ytmusic_headless._last_meta)
        ytmusic_headless._last_meta.update(
            {
                "videoId": "track",
                "playing": True,
                "ready": False,
                "buffering": False,
                "state": "loading",
                "_started": False,
            }
        )
        try:
            phase = ytmusic_headless._apply_mpv_playback_state(self._snapshot())
            self.assertEqual(phase["state"], "playing")
            self.assertEqual(ytmusic_headless._last_meta["position"], 0.0)
            self.assertEqual(ytmusic_headless._last_meta["duration"], 180.0)
            self.assertTrue(ytmusic_headless._last_meta["ready"])
            self.assertFalse(ytmusic_headless._last_meta["buffering"])
            self.assertEqual(ytmusic_headless._last_meta["state"], "playing")
        finally:
            ytmusic_headless._last_meta.clear()
            ytmusic_headless._last_meta.update(old_meta)


if __name__ == "__main__":
    unittest.main()
