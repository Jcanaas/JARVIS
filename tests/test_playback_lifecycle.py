import threading
import time
import unittest
from unittest.mock import Mock, call, patch

from actions import ytmusic_headless


class _ImmediateThread:
    def __init__(self, target=None, **_kwargs):
        self._target = target

    def start(self):
        self._target()


class PlaybackLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.old_shutting_down = ytmusic_headless._shutting_down
        self.old_procs = list(ytmusic_headless._procs)
        self.old_active = ytmusic_headless._active_slot
        self.old_meta = dict(ytmusic_headless._last_meta)
        self.old_counts = dict(ytmusic_headless._reload_counts)
        self.old_givenup = set(ytmusic_headless._reload_givenup)
        self.old_xfade = ytmusic_headless._xfade_in_progress
        self.old_fading = ytmusic_headless._crossfade_fading_out
        ytmusic_headless._shutting_down = False
        ytmusic_headless._last_meta.update({
            "videoId": "video",
            "title": "Track",
            "artists": "Artist",
            "playing": True,
            "ready": False,
        })

    def tearDown(self):
        ytmusic_headless._shutting_down = self.old_shutting_down
        ytmusic_headless._procs[:] = self.old_procs
        ytmusic_headless._active_slot = self.old_active
        ytmusic_headless._last_meta.clear()
        ytmusic_headless._last_meta.update(self.old_meta)
        ytmusic_headless._reload_counts.clear()
        ytmusic_headless._reload_counts.update(self.old_counts)
        ytmusic_headless._reload_givenup.clear()
        ytmusic_headless._reload_givenup.update(self.old_givenup)
        ytmusic_headless._xfade_in_progress = self.old_xfade
        ytmusic_headless._crossfade_fading_out = self.old_fading
        shutdown_event = getattr(ytmusic_headless, "_shutdown_event", None)
        if shutdown_event is not None:
            shutdown_event.clear()

    def test_stream_verifier_exits_immediately_when_shutdown_is_signalled(self):
        shutdown_event = ytmusic_headless._shutdown_event
        shutdown_event.clear()
        read_state = Mock()

        with (
            patch.object(ytmusic_headless.threading, "Thread", _ImmediateThread),
            patch.object(shutdown_event, "wait", return_value=True),
            patch.object(ytmusic_headless, "_read_mpv_playback_state", read_state),
        ):
            ytmusic_headless._verify_stream_started("video", "Track", "Artist")

        read_state.assert_not_called()

    def test_cleanup_requests_graceful_quit_for_both_live_slots(self):
        processes = [Mock(), Mock()]
        for process in processes:
            process.poll.return_value = None
            process.wait.return_value = 0
        ytmusic_headless._procs[:] = processes
        ytmusic_headless._active_slot = 0

        with patch.object(ytmusic_headless, "_send_command", return_value=True) as send:
            ytmusic_headless._cleanup_on_exit()

        self.assertIn(call(["quit"], pipe=ytmusic_headless._PIPE_PATHS[0]), send.call_args_list)
        self.assertIn(call(["quit"], pipe=ytmusic_headless._PIPE_PATHS[1]), send.call_args_list)

    def test_simultaneous_recovery_requests_share_one_reload(self):
        entered = threading.Event()
        release = threading.Event()

        def ipc(_command):
            entered.set()
            self.assertTrue(release.wait(2))
            return {"error": "success"}

        ytmusic_headless._reload_counts.clear()
        ytmusic_headless._reload_givenup.clear()
        with (
            patch.object(ytmusic_headless, "_invalidate_cached_stream"),
            # Recovery re-resolves the stream itself now (mpv's ytdl_hook cannot:
            # YouTube 403s the open-ended range it opens with). Keep that off the
            # network here — this test is about the deduplication.
            patch.object(
                ytmusic_headless,
                "_resolve_stream_for_video",
                return_value=("https://cdn.example/fresh", 120),
            ),
            patch.object(ytmusic_headless, "_playable_url", lambda _vid, url: url),
            patch.object(ytmusic_headless, "_ipc_request", side_effect=ipc) as request,
        ):
            first = threading.Thread(
                target=ytmusic_headless._reload_current_stream,
                args=("video", "Track", "Artist", ""),
            )
            second = threading.Thread(
                target=ytmusic_headless._reload_current_stream,
                args=("video", "Track", "Artist", ""),
            )
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            time.sleep(0.05)
            release.set()
            first.join(1)
            second.join(1)

        self.assertEqual(request.call_count, 1)
        self.assertEqual(ytmusic_headless._reload_counts["video"], 1)

    def test_crossfade_exception_cleans_alt_slot_and_falls_back(self):
        active = Mock()
        alternate = Mock()
        active.poll.return_value = None
        alternate.poll.return_value = None
        ytmusic_headless._procs[:] = [active, alternate]
        ytmusic_headless._active_slot = 0
        ytmusic_headless._xfade_in_progress = False

        with (
            patch.object(ytmusic_headless, "_start_mpv", return_value=True),
            patch.object(
                ytmusic_headless,
                "_ipc_request",
                return_value={"error": "success"},
            ),
            patch.object(ytmusic_headless, "_send_command", return_value=True),
            patch.object(
                ytmusic_headless,
                "_wait_for_audio_ready",
                side_effect=RuntimeError("audio device vanished"),
            ),
            patch.object(ytmusic_headless, "_play_video", return_value="ok") as fallback,
            # Keep the loopback stream proxy out of it: patching threading.Thread
            # below would run its accept loop inline and never come back.
            patch.object(ytmusic_headless, "_playable_url", lambda _vid, url: url),
            patch.object(ytmusic_headless.threading, "Thread", _ImmediateThread),
        ):
            started = ytmusic_headless._begin_crossfade_overlap(
                "next", "Next", "Artist", "https://stream", 180
            )

        self.assertTrue(started)
        alternate.terminate.assert_called_once()
        fallback.assert_called_once_with("next", "Next", "Artist")
        self.assertFalse(ytmusic_headless._xfade_in_progress)
        self.assertFalse(ytmusic_headless._crossfade_fading_out)


if __name__ == "__main__":
    unittest.main()
