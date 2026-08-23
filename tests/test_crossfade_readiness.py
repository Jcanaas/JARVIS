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
        alt_pipe = ytmusic_headless._PIPE_PATHS[1]
        outgoing_volumes = []
        incoming_volumes = []

        def active_ready(*_args, **_kwargs):
            self.assertEqual(ytmusic_headless._last_meta["videoId"], "outgoing")
            sequence.append("active-ready")
            return True

        def send(command, pipe=None):
            if command[:2] == ["set_property", "volume"]:
                if pipe == current_pipe:
                    self.assertEqual(sequence, ["active-ready", "active-ready"])
                    outgoing_volumes.append(command[2])
                elif pipe == alt_pipe:
                    incoming_volumes.append(command[2])
            return True

        snapshot = {
            "time-pos": 197.0,
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
        self.assertEqual(sequence, ["active-ready", "active-ready"])
        self.assertEqual(ytmusic_headless._last_meta["videoId"], "incoming")
        self.assertTrue(ytmusic_headless._last_meta["ready"])
        self.assertEqual(ytmusic_headless._last_meta["state"], "playing")
        self.assertGreater(len(outgoing_volumes), 2)
        self.assertEqual(outgoing_volumes[0], ytmusic_headless._user_volume)
        self.assertEqual(outgoing_volumes[-1], 0)
        self.assertEqual(incoming_volumes[-1], ytmusic_headless._user_volume)
        self.assertEqual(outgoing_volumes, sorted(outgoing_volumes, reverse=True))

    def test_incoming_readiness_timeout_preserves_outgoing_before_direct_fallback(self):
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
                "_wait_for_audio_ready",
                return_value=False,
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
        active_ready.assert_called_once_with(
            ytmusic_headless._PIPE_PATHS[1],
            timeout=12.0,
            cancel=ytmusic_headless._xfade_cancel,
        )
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

    def test_cancelled_crossfade_drops_the_incoming_track_without_promoting_it(self):
        """A skip taken while a crossfade is preparing replaces it. If the fade
        went on to promote its own track anyway, the user heard the song they
        just skipped past start right after the one they asked for — one press,
        two songs."""
        outgoing_process = ytmusic_headless._procs[0]
        incoming_process = ytmusic_headless._procs[1]
        current_pipe = ytmusic_headless._PIPE_PATHS[0]
        restored = []

        def send(command, pipe=None):
            if pipe == current_pipe and command[:2] == ["set_property", "volume"]:
                restored.append(command[2])
            return True

        def ready_then_cancelled(*_args, **_kwargs):
            # The skip lands while the incoming slot is warming up.
            ytmusic_headless._xfade_cancel.set()
            return False

        with (
            patch.object(ytmusic_headless, "_start_mpv", return_value=True),
            patch.object(
                ytmusic_headless, "_ipc_request", return_value={"error": "success"},
            ),
            patch.object(ytmusic_headless, "_send_command", side_effect=send),
            patch.object(
                ytmusic_headless,
                "_wait_for_audio_ready",
                side_effect=ready_then_cancelled,
            ),
            patch.object(ytmusic_headless, "_play_video") as fallback,
            patch("actions.ytmusic_headless.threading.Thread", _ImmediateThread),
        ):
            started = ytmusic_headless._begin_crossfade_overlap(
                "incoming", "Incoming", "Artist", "", 200
            )

        self.assertTrue(started)
        fallback.assert_not_called()
        incoming_process.terminate.assert_called_once_with()
        outgoing_process.terminate.assert_not_called()
        self.assertIsNone(ytmusic_headless._procs[1])
        self.assertEqual(ytmusic_headless._active_slot, 0)
        self.assertFalse(ytmusic_headless._xfade_in_progress)
        self.assertEqual(ytmusic_headless._last_meta["videoId"], "outgoing")
        self.assertEqual(restored, [ytmusic_headless._user_volume])

    def test_a_new_transition_cancels_the_running_crossfade(self):
        ytmusic_headless._xfade_in_progress = True
        ytmusic_headless._xfade_cancel.clear()
        self.addCleanup(ytmusic_headless._xfade_cancel.clear)

        with patch.object(ytmusic_headless, "_send_command", return_value=True):
            with patch.object(
                ytmusic_headless, "_play_video", return_value="Reproduciendo 'B'.",
            ):
                ytmusic_headless._crossfade_transition(
                    {"videoId": "b", "title": "B", "artists": "Artist"},
                    allow_crossfade=True,
                )

        self.assertTrue(ytmusic_headless._xfade_cancel.is_set())

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

    def test_crossfade_waits_for_the_actual_fade_window_after_preparing_audio(self):
        self.assertFalse(
            ytmusic_headless._crossfade_window_ready(
                {"time-pos": 150.0, "duration": 180.0}, 3.0
            )
        )
        self.assertTrue(
            ytmusic_headless._crossfade_window_ready(
                {"time-pos": 177.0, "duration": 180.0}, 3.0
            )
        )
        self.assertTrue(
            ytmusic_headless._crossfade_window_ready(
                {"time-pos": None, "duration": None, "idle-active": True}, 3.0
            )
        )

    def test_crossfade_starts_muted_playback_before_waiting_for_audio(self):
        """MPV may not create an audio output for a file loaded while paused."""
        alt_pipe = ytmusic_headless._PIPE_PATHS[1]
        incoming_started = []

        def send(command, pipe=None):
            if pipe == alt_pipe and command == ["set_property", "pause", False]:
                incoming_started.append(True)
            return True

        def audio_ready(*_args, **_kwargs):
            self.assertTrue(incoming_started)
            return False

        with (
            patch.object(ytmusic_headless, "_start_mpv", return_value=True),
            patch.object(ytmusic_headless, "_ipc_request", return_value={"error": "success"}),
            patch.object(ytmusic_headless, "_send_command", side_effect=send),
            patch.object(ytmusic_headless, "_wait_for_audio_output_ready") as output_ready,
            patch.object(ytmusic_headless, "_wait_for_audio_ready", side_effect=audio_ready),
            patch.object(ytmusic_headless, "_play_video"),
            # These tests are about fade sequencing. Going through the real
            # loopback proxy would start an HTTP server here — and with
            # threading.Thread patched below, its accept loop would run inline
            # and never return.
            patch.object(ytmusic_headless, "_playable_url", lambda _vid, url: url),
            patch("actions.ytmusic_headless.threading.Thread", _ImmediateThread),
        ):
            ytmusic_headless._begin_crossfade_overlap(
                "incoming", "Incoming", "Artist", "https://stream", 200
            )

        output_ready.assert_not_called()


if __name__ == "__main__":
    unittest.main()
