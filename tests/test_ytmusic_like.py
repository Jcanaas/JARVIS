import unittest
from unittest.mock import Mock, patch

from actions import ytmusic
from actions import ytmusic_headless


class YTMusicLikeTests(unittest.TestCase):
    def setUp(self):
        with ytmusic._LIKED_VIDEO_IDS_LOCK:
            ytmusic._LIKED_VIDEO_IDS_CACHE.update(
                {"loaded": False, "ts": 0.0, "ids": frozenset()}
            )

    @patch("actions.ytmusic._get_ytmusic")
    def test_reads_exact_song_like_status(self, get_ytmusic):
        client = Mock()
        client.get_watch_playlist.return_value = {
            "tracks": [
                {"videoId": "song-id", "likeStatus": "LIKE"},
                {"videoId": "other-id", "likeStatus": "INDIFFERENT"},
            ]
        }
        get_ytmusic.return_value = client

        self.assertTrue(ytmusic.get_song_like_status("song-id"))

    @patch("actions.ytmusic._get_ytmusic")
    def test_reads_like_status_from_requested_counterpart(self, get_ytmusic):
        client = Mock()
        client.get_watch_playlist.return_value = {
            "tracks": [
                {
                    "videoId": "canonical-song-id",
                    "likeStatus": "INDIFFERENT",
                    "counterpart": {
                        "videoId": "requested-video-id",
                        "likeStatus": "LIKE",
                    },
                }
            ]
        }
        get_ytmusic.return_value = client

        self.assertTrue(ytmusic.get_song_like_status("requested-video-id"))

    @patch("actions.ytmusic._get_ytmusic")
    def test_missing_watch_song_uses_authoritative_liked_library(self, get_ytmusic):
        client = Mock()
        client.get_watch_playlist.return_value = {
            "tracks": [{"videoId": "different-song-id", "likeStatus": "LIKE"}]
        }
        client.get_liked_songs.return_value = {"tracks": []}
        get_ytmusic.return_value = client

        self.assertFalse(ytmusic.get_song_like_status("requested-video-id"))

    @patch("actions.ytmusic._get_ytmusic")
    def test_missing_watch_status_uses_authoritative_liked_library(self, get_ytmusic):
        client = Mock()
        client.get_watch_playlist.return_value = {
            "tracks": [{"videoId": "requested-video-id"}]
        }
        client.get_liked_songs.return_value = {"tracks": []}
        get_ytmusic.return_value = client

        self.assertFalse(ytmusic.get_song_like_status("requested-video-id"))

    @patch("actions.ytmusic._get_ytmusic")
    def test_falls_back_to_liked_library_when_watch_parser_fails(self, get_ytmusic):
        client = Mock()
        client.get_watch_playlist.side_effect = KeyError("endpoint")
        client.get_liked_songs.return_value = {
            "tracks": [
                {
                    "videoId": "requested-video-id",
                    "title": "Liked song",
                    "likeStatus": "LIKE",
                }
            ]
        }
        get_ytmusic.return_value = client

        self.assertTrue(ytmusic.get_song_like_status("requested-video-id"))
        client.get_liked_songs.assert_called_once_with(limit=None)

    @patch("actions.ytmusic._get_ytmusic")
    def test_uses_fresh_liked_library_cache_before_broken_watch_endpoint(
        self, get_ytmusic
    ):
        client = Mock()
        get_ytmusic.return_value = client
        with ytmusic._LIKED_VIDEO_IDS_LOCK:
            ytmusic._LIKED_VIDEO_IDS_CACHE.update(
                {
                    "loaded": True,
                    "ts": ytmusic.time.monotonic(),
                    "ids": frozenset({"requested-video-id"}),
                }
            )

        self.assertTrue(ytmusic.get_song_like_status("requested-video-id"))
        client.get_watch_playlist.assert_not_called()

    @patch("actions.ytmusic._get_ytmusic")
    def test_removes_like_with_indifferent_rating(self, get_ytmusic):
        client = Mock()
        get_ytmusic.return_value = client

        result = ytmusic.set_song_like("song-id", False)

        self.assertFalse(result)
        client.rate_song.assert_called_once_with("song-id", "INDIFFERENT")

    @patch("actions.ytmusic_headless._prefetch_next_tracks")
    @patch("actions.ytmusic_headless._ensure_autoplay_worker")
    @patch("actions.ytmusic_headless._send_command", return_value=True)
    @patch("actions.ytmusic_headless._ipc_request", return_value={"error": "success"})
    @patch("actions.ytmusic_headless._start_mpv", return_value=True)
    @patch("actions.ytmusic_headless._resolve_stream_for_video", return_value=("https://stream.test/audio", 180))
    @patch("actions.ytmusic_headless._wait_cached_stream", return_value=(None, 0))
    @patch("actions.ytmusic_headless._cached_stream", return_value=(None, 0))
    def test_headless_current_keeps_exact_video_id(
        self,
        _cached,
        _wait,
        _resolve,
        _start,
        ipc_request,
        _send,
        _worker,
        _prefetch,
    ):
        ytmusic_headless._play_video("song-id", "Song", "Artist")

        ipc_request.assert_called_once_with(
            ["loadfile", "https://music.youtube.com/watch?v=song-id", "replace"]
        )
        self.assertEqual(ytmusic_headless.current()["videoId"], "song-id")

    @patch("actions.ytmusic_headless._prefetch_next_tracks")
    @patch("actions.ytmusic_headless._ensure_autoplay_worker")
    @patch("actions.ytmusic_headless._send_command", return_value=True)
    @patch("actions.ytmusic_headless._ipc_request", return_value={"error": "success"})
    @patch("actions.ytmusic_headless._start_mpv", return_value=True)
    @patch("actions.ytmusic_headless._wait_cached_stream", return_value=(None, 0))
    @patch(
        "actions.ytmusic_headless._cached_stream",
        return_value=("https://cdn.test/audio.webm", 180),
    )
    def test_headless_reuses_a_ready_prefetched_stream_url(
        self,
        _cached,
        _wait,
        _start,
        ipc_request,
        _send,
        _worker,
        _prefetch,
    ):
        with patch("actions.stream_proxy.serve", return_value="http://127.0.0.1:1/s/tok") as serve:
            ytmusic_headless._play_video("song-id", "Song", "Artist")

        # The prefetched URL is reused, but mpv is pointed at the loopback proxy:
        # YouTube 403s the open-ended range ffmpeg opens a CDN URL with.
        serve.assert_called_once()
        self.assertEqual(serve.call_args.args[0], "https://cdn.test/audio.webm")
        ipc_request.assert_called_once_with(
            ["loadfile", "http://127.0.0.1:1/s/tok", "replace"]
        )

    @patch("actions.ytmusic_headless._send_command")
    @patch("actions.ytmusic_headless._ipc_request")
    @patch("actions.ytmusic_headless._start_mpv", return_value=False)
    @patch("actions.ytmusic_headless._wait_cached_stream", return_value=(None, 0))
    @patch("actions.ytmusic_headless._cached_stream", return_value=(None, 0))
    def test_headless_does_not_send_loadfile_when_mpv_fails_to_start(
        self,
        _cached,
        _wait,
        start,
        ipc_request,
        send,
    ):
        result = ytmusic_headless._play_video("song-id", "Song", "Artist")

        self.assertIn("mpv no pudo arrancarse", result)
        ipc_request.assert_not_called()
        send.assert_not_called()

    @patch("actions.ytmusic_headless._prefetch_next_tracks")
    @patch("actions.ytmusic_headless._ensure_autoplay_worker")
    @patch("actions.ytmusic_headless._send_command", return_value=True)
    @patch(
        "actions.ytmusic_headless._ipc_request",
        return_value={"error": "loading failed"},
    )
    @patch("actions.ytmusic_headless._start_mpv", return_value=True)
    @patch("actions.ytmusic_headless._wait_cached_stream", return_value=(None, 0))
    @patch("actions.ytmusic_headless._cached_stream", return_value=(None, 0))
    def test_headless_reports_error_when_loadfile_fails(
        self,
        _cached,
        _wait,
        start,
        _ipc_request,
        send,
        _worker,
        _prefetch,
    ):
        result = ytmusic_headless._play_video("song-id", "Song", "Artist")

        self.assertIn("No se pudo cargar la canción", result)
        send.assert_not_called()
        _worker.assert_not_called()

    @patch("actions.ytmusic_headless._apply_eq_to_slot")
    @patch("actions.ytmusic_headless._wait_for_pipe", return_value=False)
    @patch("actions.ytmusic_headless._create_windows_job_for_child")
    @patch("actions.ytmusic_headless.subprocess.Popen")
    @patch("actions.ytmusic_headless._mpv_available", return_value=True)
    def test_start_mpv_discards_process_when_pipe_never_becomes_ready(
        self,
        _available,
        popen,
        _job,
        _wait,
        _eq,
    ):
        old_proc = ytmusic_headless._procs[0]
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        ytmusic_headless._procs[0] = None
        try:
            self.assertFalse(ytmusic_headless._start_mpv(slot=0))
            self.assertIsNone(ytmusic_headless._procs[0])
            process.terminate.assert_called_once_with()
        finally:
            ytmusic_headless._procs[0] = old_proc

    def test_start_mpv_publishes_process_only_after_ipc_is_ready(self):
        old_proc = ytmusic_headless._procs[0]
        process = Mock()
        process.poll.return_value = None

        def observe_unpublished(_pipe, timeout_ms):
            self.assertEqual(timeout_ms, 6000)
            self.assertIsNone(ytmusic_headless._procs[0])
            return True

        ytmusic_headless._procs[0] = None
        try:
            with (
                patch.object(ytmusic_headless, "_mpv_available", return_value=True),
                patch.object(ytmusic_headless, "_disable_win_audio_ducking"),
                patch.object(
                    ytmusic_headless.subprocess,
                    "Popen",
                    return_value=process,
                ) as popen,
                patch.object(ytmusic_headless, "_create_windows_job_for_child"),
                patch.object(
                    ytmusic_headless,
                    "_wait_for_pipe",
                    side_effect=observe_unpublished,
                ),
            ):
                self.assertTrue(ytmusic_headless._start_mpv(slot=0))

            self.assertIs(ytmusic_headless._procs[0], process)
            launch_args = popen.call_args.args[0]
            self.assertIn("--cache-pause-initial=no", launch_args)
        finally:
            ytmusic_headless._procs[0] = old_proc

    @patch("actions.ytmusic_headless._prefetch_video")
    @patch("actions.ytmusic_headless._start_mpv", return_value=True)
    def test_prefetch_warms_player_without_duplicate_ytdlp_resolution(
        self,
        _start,
        prefetch_video,
    ):
        result = ytmusic_headless.prefetch_tracks(
            [{"videoId": "one"}, {"videoId": "two"}],
            start_index=0,
            count=2,
        )

        _start.assert_called_once_with()
        prefetch_video.assert_called_once_with("one")
        self.assertEqual(result["scheduled"], 1)


if __name__ == "__main__":
    unittest.main()
