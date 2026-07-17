import unittest
from unittest.mock import Mock, patch

from actions import ytmusic_headless


class _ImmediateThread:
    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        self._target()


class CrossfadeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.old_active = ytmusic_headless._active_slot
        self.old_procs = list(ytmusic_headless._procs)
        self.old_meta = dict(ytmusic_headless._last_meta)
        self.old_xfade = ytmusic_headless._xfade_in_progress
        self.old_fading = ytmusic_headless._crossfade_fading_out

        ytmusic_headless._active_slot = 0
        ytmusic_headless._procs[:] = [Mock(), Mock()]
        for process in ytmusic_headless._procs:
            process.poll.return_value = None
        ytmusic_headless._xfade_in_progress = False
        ytmusic_headless._crossfade_fading_out = False
        ytmusic_headless._last_meta.update(
            {
                "title": "Outgoing",
                "artists": "Artist",
                "videoId": "outgoing",
                "position": 170.0,
                "duration": 180.0,
                "playing": True,
                "ready": True,
                "buffering": False,
                "state": "playing",
            }
        )

    def tearDown(self):
        ytmusic_headless._active_slot = self.old_active
        ytmusic_headless._procs[:] = self.old_procs
        ytmusic_headless._last_meta.clear()
        ytmusic_headless._last_meta.update(self.old_meta)
        ytmusic_headless._xfade_in_progress = self.old_xfade
        ytmusic_headless._crossfade_fading_out = self.old_fading

    def test_crossfade_keeps_outgoing_track_until_incoming_is_actively_playing(self):
        sequence = []
        current_pipe = ytmusic_headless._PIPE_PATHS[0]

        def output_ready(*_args, **_kwargs):
            self.assertEqual(ytmusic_headless._last_meta["videoId"], "outgoing")
            sequence.append("output-ready")
            return True

        def active_ready(*_args, **_kwargs):
            self.assertEqual(ytmusic_headless._last_meta["videoId"], "outgoing")
            sequence.append("active-ready")
            return True

        def send(command, pipe=None):
            if pipe == current_pipe and command[:2] == ["set_property", "volume"]:
                self.assertEqual(sequence, ["output-ready", "active-ready"])
            return True

        snapshot = {
            "time-pos": 0.0,
            "duration": 200.0,
            "pause": False,
            "eof-reached": False,
            "idle-active": False,
            "core-idle": False,
            "paused-for-cache": False,
            "seeking": False,
            "audio-out-params": {"format": "float", "samplerate": 48000},
            "current-ao": "wasapi",
        }

        with (
            patch.object(ytmusic_headless, "_start_mpv", return_value=True),
            patch.object(
                ytmusic_headless,
                "_ipc_request",
                return_value={"error": "success"},
            ),
            patch.object(ytmusic_headless, "_send_command", side_effect=send),
            patch.object(
                ytmusic_headless,
                "_wait_for_audio_output_ready",
                side_effect=output_ready,
            ),
            patch.object(
                ytmusic_headless,
                "_wait_for_audio_ready",
                side_effect=active_ready,
            ),
            patch.object(
                ytmusic_headless,
                "_read_mpv_playback_state",
                return_value=snapshot,
            ),
            patch("actions.ytmusic_headless.threading.Thread", _ImmediateThread),
            patch("actions.ytmusic_headless.time.sleep"),
        ):
            started = ytmusic_headless._begin_crossfade_overlap(
                "incoming", "Incoming", "Artist", "", 200
            )

        self.assertTrue(started)
        self.assertEqual(sequence, ["output-ready", "active-ready"])
        self.assertEqual(ytmusic_headless._last_meta["videoId"], "incoming")
        self.assertTrue(ytmusic_headless._last_meta["ready"])
        self.assertEqual(ytmusic_headless._last_meta["state"], "playing")

    def test_output_readiness_timeout_preserves_outgoing_before_direct_fallback(self):
        current_pipe = ytmusic_headless._PIPE_PATHS[0]
        outgoing_process = ytmusic_headless._procs[0]
        incoming_process = ytmusic_headless._procs[1]
        events = []

        def send(command, pipe=None):
            if pipe == current_pipe and command[:2] == ["set_property", "volume"]:
                events.append(("outgoing-volume", command[2]))
            return True

        def direct_fallback(video_id, title, artists):
            self.assertEqual(video_id, "incoming")
            self.assertEqual(title, "Incoming")
            self.assertEqual(artists, "Artist")
            self.assertIsNone(ytmusic_headless._procs[1])
            self.assertFalse(ytmusic_headless._xfade_in_progress)
            self.assertFalse(ytmusic_headless._crossfade_fading_out)
            events.append(("fallback", video_id))
            return "Reproduciendo Incoming"

        with (
            patch.object(ytmusic_headless, "_start_mpv", return_value=True),
            patch.object(
                ytmusic_headless,
                "_ipc_request",
                return_value={"error": "success"},
            ),
            patch.object(ytmusic_headless, "_send_command", side_effect=send),
            patch.object(
                ytmusic_headless,
                "_wait_for_audio_output_ready",
                return_value=False,
            ) as output_ready,
            patch.object(
                ytmusic_headless,
                "_wait_for_audio_ready",
            ) as active_ready,
            patch.object(
                ytmusic_headless,
                "_play_video",
                side_effect=direct_fallback,
            ) as fallback,
            patch("actions.ytmusic_headless.threading.Thread", _ImmediateThread),
        ):
            started = ytmusic_headless._begin_crossfade_overlap(
                "incoming", "Incoming", "Artist", "", 200
            )

        self.assertTrue(started)
        output_ready.assert_called_once_with(
            ytmusic_headless._PIPE_PATHS[1], timeout=12.0
        )
        active_ready.assert_not_called()
        fallback.assert_called_once_with("incoming", "Incoming", "Artist")
        incoming_process.terminate.assert_called_once_with()
        outgoing_process.terminate.assert_not_called()
        self.assertIs(ytmusic_headless._procs[0], outgoing_process)
        self.assertIsNone(ytmusic_headless._procs[1])
        self.assertEqual(ytmusic_headless._active_slot, 0)
        self.assertFalse(ytmusic_headless._xfade_in_progress)
        self.assertFalse(ytmusic_headless._crossfade_fading_out)
        self.assertEqual(ytmusic_headless._last_meta["videoId"], "outgoing")
        self.assertEqual(
            events,
            [
                ("outgoing-volume", ytmusic_headless._user_volume),
                ("fallback", "incoming"),
            ],
        )

    def test_crossfade_starts_with_time_to_prepare_the_incoming_player(self):
        old_seconds = ytmusic_headless._crossfade_secs
        try:
            ytmusic_headless._crossfade_secs = 3
            self.assertEqual(ytmusic_headless._crossfade_trigger_margin(), 11.0)
        finally:
            ytmusic_headless._crossfade_secs = old_seconds

    def test_playing_a_track_prefetches_its_successor(self):
        with (
            patch.object(ytmusic_headless, "_start_mpv", return_value=True),
            patch.object(ytmusic_headless, "_cached_stream", return_value=("https://stream", 180)),
            patch.object(ytmusic_headless, "_ipc_request", return_value={"error": "success"}),
            patch.object(ytmusic_headless, "_send_command", return_value=True),
            patch.object(ytmusic_headless, "_ensure_autoplay_worker"),
            patch.object(ytmusic_headless, "_verify_stream_started"),
            patch.object(ytmusic_headless, "_prefetch_next_tracks") as prefetch,
        ):
            ytmusic_headless._play_video("current", "Current", "Artist")

        prefetch.assert_called_once_with()

    def test_crossfade_ignores_a_stale_sample_from_the_outgoing_player(self):
        old_active = ytmusic_headless._active_slot
        old_fading = ytmusic_headless._crossfade_fading_out
        try:
            ytmusic_headless._active_slot = 1
            ytmusic_headless._crossfade_fading_out = False
            self.assertFalse(ytmusic_headless._is_authoritative_playback_sample(0))
            self.assertTrue(ytmusic_headless._is_authoritative_playback_sample(1))
        finally:
            ytmusic_headless._active_slot = old_active
            ytmusic_headless._crossfade_fading_out = old_fading


if __name__ == "__main__":
    unittest.main()
