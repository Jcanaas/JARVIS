import unittest
from unittest.mock import Mock, patch

from actions import ytmusic
from actions import ytmusic_headless


class YTMusicLikeTests(unittest.TestCase):
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
    def test_removes_like_with_indifferent_rating(self, get_ytmusic):
        client = Mock()
        get_ytmusic.return_value = client

        result = ytmusic.set_song_like("song-id", False)

        self.assertFalse(result)
        client.rate_song.assert_called_once_with("song-id", "INDIFFERENT")

    @patch("actions.ytmusic_headless._prefetch_next_tracks")
    @patch("actions.ytmusic_headless._ensure_autoplay_worker")
    @patch("actions.ytmusic_headless._send_command", return_value=True)
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
        _send,
        _worker,
        _prefetch,
    ):
        ytmusic_headless._play_video("song-id", "Song", "Artist")

        self.assertEqual(ytmusic_headless.current()["videoId"], "song-id")

    @patch("actions.ytmusic_headless._send_command")
    @patch("actions.ytmusic_headless._start_mpv")
    @patch("actions.ytmusic_headless._resolve_stream_for_video", return_value=(None, 0))
    @patch("actions.ytmusic_headless._wait_cached_stream", return_value=(None, 0))
    @patch("actions.ytmusic_headless._cached_stream", return_value=(None, 0))
    def test_headless_does_not_send_page_url_when_stream_resolution_fails(
        self,
        _cached,
        _wait,
        _resolve,
        start,
        send,
    ):
        result = ytmusic_headless._play_video("song-id", "Song", "Artist")

        self.assertIn("No se pudo resolver", result)
        start.assert_not_called()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
