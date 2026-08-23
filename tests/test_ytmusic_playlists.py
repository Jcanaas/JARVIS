"""Regression test for a real ytmusicapi 1.12.0 bug: get_library_playlists()
aborts the ENTIRE list if a single playlist entry has no thumbnail data
(nav() raises KeyError deep in parse_playlist). Confirmed live against the
real API before/after this fix — see actions/ytmusic.py's
_get_library_playlists_resilient, which reimplements the same request/parse
shape with a per-item try/except instead of the stock bulk parse."""
import unittest
from unittest.mock import MagicMock, patch


class YtmusicPlaylistsResilienceTests(unittest.TestCase):
    def test_skips_unparseable_entry_instead_of_aborting(self):
        from actions import ytmusic

        good_raw = {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Good playlist"}]}}}
        bad_raw = {"musicTwoRowItemRenderer": {}}  # triggers parse_playlist's KeyError

        fake_yt = MagicMock()
        fake_yt._check_auth.return_value = None
        fake_yt._send_request.return_value = {"fake": "response"}

        fake_results = {"items": [{"ignored": "header"}, good_raw, bad_raw]}

        def fake_parse_playlist(data):
            if not data:
                raise KeyError("thumbnails")
            return {"title": "Good playlist", "playlistId": "PL123"}

        with patch("ytmusicapi.parsers.library.get_library_contents", return_value=fake_results), \
             patch("ytmusicapi.parsers.browsing.parse_playlist", side_effect=fake_parse_playlist):
            result = ytmusic._get_library_playlists_resilient(fake_yt, None)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Good playlist")

    def test_list_playlists_degrades_to_liked_songs_only_on_total_failure(self):
        from actions import ytmusic

        with patch("actions.ytmusic._get_ytmusic") as mock_get_yt, \
             patch("actions.ytmusic._get_library_playlists_resilient", side_effect=RuntimeError("boom")):
            mock_get_yt.return_value = MagicMock()
            with patch("actions.ytmusic.get_liked_songs", return_value=[]):
                result = ytmusic.list_playlists()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["playlistId"], "LM")


if __name__ == "__main__":
    unittest.main()
