"""Stream resolution for tracks that are not downloaded.

Starting a non-local track used to take many seconds because the same yt-dlp
extraction was performed twice: once by our own prefetch and again by mpv's
ytdl_hook, after a wait shorter than a normal resolution threw the first result
away.
"""
import unittest
from unittest.mock import patch

from actions import ytmusic_headless as hl


class StreamResolutionTests(unittest.TestCase):
    def setUp(self):
        with hl._stream_lock:
            hl._stream_cache.clear()
            hl._stream_loading.clear()
        # _last_meta is module state shared with every other music test.
        snapshot = dict(hl._last_meta)
        self.addCleanup(lambda: hl._last_meta.update(snapshot))

    def test_resolution_tries_the_library_before_spawning_the_executable(self):
        with patch.object(hl, "_ytdlp_module_stream", return_value=("http://cdn/a", 210)) as mod:
            with patch.object(hl, "_ytdlp_cmd") as cmd:
                url, dur = hl._get_stream_url_and_duration("https://x/watch?v=a")

        self.assertEqual((url, dur), ("http://cdn/a", 210))
        mod.assert_called_once()
        cmd.assert_not_called()

    def test_resolution_runs_without_cookies_first(self):
        """Pulling cookies out of a running browser costs seconds and fails
        outright while the browser holds its DB lock, which used to make every
        attempt fail even though anonymous resolution works."""
        calls = []

        def fake_module(url, use_cookies=False):
            calls.append(use_cookies)
            return ("http://cdn/a", 100) if use_cookies is False else (None, 0)

        with patch.object(hl, "_cookie_browser", return_value="edge"):
            with patch.object(hl, "_ytdlp_module_stream", side_effect=fake_module):
                hl._get_stream_url_and_duration("https://x/watch?v=a")

        self.assertEqual(calls, [False])

    def test_resolution_retries_with_cookies_only_after_a_failure(self):
        attempts = []

        def fake_module(url, use_cookies=False):
            attempts.append(use_cookies)
            return ("http://cdn/a", 5) if use_cookies else (None, 0)

        with patch.object(hl, "_cookie_browser", return_value="edge"):
            with patch.object(hl, "_ytdlp_module_stream", side_effect=fake_module):
                with patch.object(hl, "_ytdlp_cmd", return_value=None):
                    url, _ = hl._get_stream_url_and_duration("https://x/watch?v=a")

        self.assertEqual(attempts, [False, True])
        self.assertEqual(url, "http://cdn/a")

    def test_no_cookie_browser_means_no_second_pass(self):
        with patch.object(hl, "_cookie_browser", return_value=None):
            with patch.object(hl, "_ytdlp_module_stream", return_value=(None, 0)) as mod:
                with patch.object(hl, "_ytdlp_cmd", return_value=None):
                    self.assertEqual(hl._get_stream_url_and_duration("u"), (None, 0))

        self.assertEqual(mod.call_count, 1)

    def test_play_waits_for_an_in_flight_resolution_instead_of_reresolving(self):
        """The old 3.5 s cap expired before a typical ~4 s resolution finished,
        so mpv's hook redid the very same work from scratch."""
        def slow_resolve(url):
            import time
            time.sleep(0.4)          # longer than one polling interval
            return "http://cdn/late", 180

        sent = {}

        def fake_ipc(cmd, **kwargs):
            sent["cmd"] = cmd
            return {"error": "success"}

        with patch.object(hl, "_start_mpv", return_value=True):
            with patch.object(hl, "_get_stream_url_and_duration", side_effect=slow_resolve):
                with patch.object(hl, "_ipc_request", side_effect=fake_ipc):
                    with patch.object(hl, "_send_command", return_value=True):
                        with patch.object(hl, "_ensure_autoplay_worker"):
                            with patch.object(hl, "_prefetch_next_tracks"):
                                with patch.object(hl, "_verify_stream_started"):
                                    hl._play_video("vid1", "Title", "Artist")

        self.assertEqual(sent["cmd"][0], "loadfile")
        # The resolved stream is handed to mpv through the loopback proxy — see
        # test_the_resolved_stream_is_played_through_the_loopback_proxy.
        self.assertTrue(sent["cmd"][1].startswith("http://127.0.0.1:"))
        self.assertEqual(hl._last_meta["duration"], 180)

    def test_the_resolved_stream_is_played_through_the_loopback_proxy(self):
        """YouTube answers 403 to the open-ended range ffmpeg opens every stream
        with, so mpv must never be handed the CDN URL itself."""
        served = {}

        def fake_serve(url, **kwargs):
            served["url"] = url
            served["resolver"] = kwargs.get("resolver")
            return "http://127.0.0.1:9/stream/tok"

        with hl._stream_lock:
            hl._stream_cache["vid2"] = {
                "url": "https://cdn.example/media", "duration": 90, "ts": __import__("time").time(),
            }

        with patch.object(hl, "_start_mpv", return_value=True):
            with patch("actions.stream_proxy.serve", side_effect=fake_serve):
                with patch.object(hl, "_ipc_request", return_value={"error": "success"}) as ipc:
                    with patch.object(hl, "_send_command", return_value=True):
                        with patch.object(hl, "_ensure_autoplay_worker"):
                            with patch.object(hl, "_prefetch_next_tracks"):
                                with patch.object(hl, "_verify_stream_started"):
                                    hl._play_video("vid2", "Title", "Artist")

        self.assertEqual(served["url"], "https://cdn.example/media")
        self.assertIsNotNone(served["resolver"])   # can refresh an expired URL
        self.assertEqual(
            ipc.call_args.args[0], ["loadfile", "http://127.0.0.1:9/stream/tok", "replace"]
        )

    def test_a_local_file_is_played_directly_not_through_the_proxy(self):
        with patch("actions.stream_proxy.serve") as serve:
            self.assertEqual(hl._playable_url("vid", r"C:\music\song.m4a"), r"C:\music\song.m4a")
        serve.assert_not_called()

    def test_prefetch_skips_tracks_already_on_disk(self):
        scheduled = []
        tracks = [{"videoId": "local"}, {"videoId": "remote"}]

        with patch.object(hl, "_start_mpv", return_value=True):
            with patch.object(hl, "_prefetch_video", side_effect=scheduled.append):
                with patch("actions.offline_library.local_file_for",
                           side_effect=lambda v: "C:/x.m4a" if v == "local" else None):
                    hl.prefetch_tracks(tracks, 0, 2)

        self.assertEqual(scheduled, ["remote"])

    def test_mpv_is_started_without_browser_cookies(self):
        """mpv's ytdl_hook has no retry-without-cookies pass: a locked cookie DB
        made the hook fail and the track never played."""
        import subprocess

        class FakeProc:
            def poll(self):
                return None

        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            return FakeProc()

        old = hl._procs[0]
        self.addCleanup(lambda: hl._procs.__setitem__(0, old))
        hl._procs[0] = None
        with patch.object(hl, "_mpv_available", return_value=True):
            with patch.object(hl, "_disable_win_audio_ducking"):
                with patch.object(hl, "_create_windows_job_for_child", return_value=True):
                    with patch.object(hl, "_wait_for_pipe", return_value=True):
                        with patch.object(hl, "_cookie_browser", return_value="edge"):
                            with patch.object(subprocess, "Popen", side_effect=fake_popen):
                                self.assertTrue(hl._start_mpv_locked(0))

        self.assertFalse(
            [a for a in captured["args"] if "cookies-from-browser" in str(a)]
        )


class TogglePublishesStateTests(unittest.TestCase):
    def test_toggle_updates_the_published_state_immediately(self):
        """Remote clients re-read current() the instant they tap the button;
        'cycle pause' left the old state in place until the next 0.8 s poll."""
        with patch.object(hl, "_get_mpv_property", return_value=False):
            with patch.object(hl, "_send_command", return_value=True) as send:
                hl.toggle_play()

        self.assertEqual(send.call_args.args[0], ["set_property", "pause", True])
        self.assertFalse(hl._last_meta["playing"])

        with patch.object(hl, "_get_mpv_property", return_value=True):
            with patch.object(hl, "_send_command", return_value=True) as send:
                hl.toggle_play()

        self.assertEqual(send.call_args.args[0], ["set_property", "pause", False])
        self.assertTrue(hl._last_meta["playing"])


if __name__ == "__main__":
    unittest.main()
