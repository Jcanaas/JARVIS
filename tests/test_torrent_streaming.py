"""Tests for torrent-based streaming (movie_search, torrent_search, peerflix_player)."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actions import movie_search as ms  # noqa: E402
from actions import torrent_search as ts  # noqa: E402
from actions import peerflix_player as pp  # noqa: E402


# --------------------------------------------------------------------------- #
# TMDB / Movie Search                                                        #
# --------------------------------------------------------------------------- #
class MovieSearchTests(unittest.TestCase):
    MOVIE_RESPONSE = {
        "results": [
            {
                "id": 19404,
                "title": "Fight Club",
                "release_date": "1999-10-15",
                "poster_path": "/fqv8v6AycXKsivp1T5yKwLg39AS.jpg",
                "overview": "A ticking-time-bomb inversion of the personality.",
                "vote_average": 8.8,
                "media_type": "movie",
            },
            {
                "id": 1396,
                "name": "Breaking Bad",
                "first_air_date": "2008-01-20",
                "poster_path": "/ggFHVNu6YYI5L9pIbPbVfnQEers.jpg",
                "overview": "A high school chemistry teacher.",
                "vote_average": 9.5,
                "media_type": "tv",
            },
        ]
    }

    @patch("actions.movie_search._get_api_key", return_value="fake_key")
    @patch("actions.movie_search._http_get")
    def test_search_returns_movies_and_tv(self, http_get, get_key):
        http_get.return_value = self.MOVIE_RESPONSE
        results = ms.search("fight club")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Fight Club")
        self.assertEqual(results[0].media_type, "movie")
        self.assertEqual(results[1].title, "Breaking Bad")
        self.assertEqual(results[1].media_type, "tv")

    @patch("actions.movie_search._get_api_key", return_value="")
    def test_search_without_api_key_raises(self, get_key):
        with self.assertRaises(ms.MovieSearchError):
            ms.search("anything")

    @patch("actions.movie_search._get_api_key", return_value="fake_key")
    def test_empty_query_raises(self, get_key):
        with self.assertRaises(ms.MovieSearchError):
            ms.search("   ")

    @patch("actions.movie_search._get_api_key", return_value="fake_key")
    @patch("actions.movie_search._http_get", return_value={"results": []})
    def test_no_results_raises(self, http_get, get_key):
        with self.assertRaises(ms.MovieSearchError):
            ms.search("zzzznotexist")

    def test_parse_movie_extracts_fields(self):
        data = {
            "id": 19404,
            "title": "Fight Club",
            "release_date": "1999-10-15",
            "poster_path": "/fqv8v6AycXKsivp1T5yKwLg39AS.jpg",
            "overview": "Synopsis here",
            "vote_average": 8.8,
        }
        movie = ms._parse_movie(data, "movie")
        self.assertEqual(movie.tmdb_id, 19404)
        self.assertEqual(movie.title, "Fight Club")
        self.assertEqual(movie.release_year, 1999)
        self.assertIn("w342", movie.poster_url)  # TMDB_IMG_BASE
        self.assertAlmostEqual(movie.rating, 8.8, places=1)

    @patch("actions.movie_search._get_api_key", return_value="fake_key")
    @patch("actions.movie_search._http_get", return_value={"results": [{"id": 19404, "title": "Fight Club", "release_date": "1999-10-15"}]})
    def test_search_action_returns_formatted_string(self, http_get, get_key):
        out = ms.search_action({"query": "fight club"})
        self.assertIn("Encontré", out)
        self.assertIn("Fight Club", out)
        self.assertIn("1999", out)


# --------------------------------------------------------------------------- #
# Torrent Search                                                             #
# --------------------------------------------------------------------------- #
class TorrentSearchTests(unittest.TestCase):
    TORLINK_JSON = [
        {
            "name": "Fight Club 1999 1080p",
            "magnet": "magnet:?xt=urn:btih:abc123",
            "seeders": 500,
            "leechers": 100,
            "size": "1.4GB",
        },
        {
            "name": "Fight Club HD",
            "magnet": "magnet:?xt=urn:btih:def456",
            "seeders": 300,
            "leechers": 50,
            "size": "700MB",
        },
    ]

    def test_torrent_dataclass(self):
        t = ts.Torrent("Fight Club", "magnet:?xt=urn:btih:abc", seeders=500, leechers=100)
        self.assertEqual(t.title, "Fight Club")
        self.assertEqual(t.seeders, 500)
        d = t.to_dict()
        self.assertIn("title", d)
        self.assertIn("magnet", d)

    @patch("actions.torrent_search._locate_torlink", side_effect=ts.TorrentSearchError("not found"))
    def test_search_without_torlink_raises(self, mock_locate):
        with self.assertRaises(ts.TorrentSearchError):
            ts.search("anything")

    def test_empty_query_raises(self):
        with self.assertRaises(ts.TorrentSearchError):
            ts.search("   ")

    @patch("actions.torrent_search._locate_torlink", return_value="torlink")
    @patch("actions.torrent_search.subprocess.run")
    def test_search_parses_torlink_json(self, mock_run, mock_locate):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(self.TORLINK_JSON),
        )
        torrents = ts.search("fight club", limit=2)
        self.assertEqual(len(torrents), 2)
        self.assertEqual(torrents[0].title, "Fight Club 1999 1080p")
        self.assertEqual(torrents[0].seeders, 500)

    @patch("actions.torrent_search.search")
    def test_search_action_returns_formatted_string(self, mock_search):
        mock_search.return_value = [
            ts.Torrent("Fight Club 1080p", "magnet:?xt=urn:btih:abc", seeders=500),
            ts.Torrent("Fight Club HD", "magnet:?xt=urn:btih:def", seeders=300),
        ]
        out = ts.search_action({"query": "fight club"})
        self.assertIn("Encontré", out)
        self.assertIn("Fight Club", out)
        self.assertIn("📤", out)  # Seeders emoji


# --------------------------------------------------------------------------- #
# Peerflix Player                                                            #
# --------------------------------------------------------------------------- #
class PeerflixPlayerTests(unittest.TestCase):
    @patch("actions.peerflix_player.webbrowser.open")
    def test_play_opens_peerflix_url(self, mock_open):
        magnet = "magnet:?xt=urn:btih:abc123"
        result = pp.play(magnet, "Fight Club")
        self.assertTrue(result)
        mock_open.assert_called_once()
        # Verify URL contains peerflix.mov
        url = mock_open.call_args[0][0]
        self.assertIn("peerflix.mov", url)
        self.assertIn("magnet=", url)

    @patch("actions.peerflix_player.webbrowser.open")
    def test_play_encodes_magnet_and_title(self, mock_open):
        magnet = "magnet:?xt=urn:btih:test&tr=tracker"
        result = pp.play(magnet, "My Movie")
        self.assertTrue(result)
        url = mock_open.call_args[0][0]
        self.assertIn("title=", url)
        # Verify URL encoding (? and & should be encoded)
        self.assertNotIn("?xt=", url)

    @patch("actions.peerflix_player.webbrowser.open", side_effect=Exception("No browser"))
    def test_play_without_browser_raises(self, mock_open):
        with self.assertRaises(pp.PeerflixError):
            pp.play("magnet:?xt=urn:btih:abc")

    def test_get_status_always_ready(self):
        status = pp.get_status()
        self.assertIsNotNone(status)
        self.assertIn("ready", status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
