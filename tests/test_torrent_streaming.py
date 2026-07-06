"""Tests for torrent-based streaming (movie_search, torrent_search, vlc_player)."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actions import movie_search as ms  # noqa: E402
from actions import torrent_search as ts  # noqa: E402
from actions import vlc_player as vp  # noqa: E402


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
    SEARCH_JSON = [
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

    @patch("actions.torrent_search._locate_node", side_effect=ts.TorrentSearchError("not found"))
    def test_search_without_node_raises(self, mock_locate):
        with self.assertRaises(ts.TorrentSearchError):
            ts.search("anything")

    def test_empty_query_raises(self):
        with self.assertRaises(ts.TorrentSearchError):
            ts.search("   ")

    @patch("actions.torrent_search._locate_node", return_value="node")
    @patch("actions.torrent_search.subprocess.run")
    def test_search_parses_vendored_script_json(self, mock_run, mock_locate):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(self.SEARCH_JSON),
            stderr="",
        )
        torrents = ts.search("fight club", limit=2)
        self.assertEqual(len(torrents), 2)
        self.assertEqual(torrents[0].title, "Fight Club 1999 1080p")
        self.assertEqual(torrents[0].seeders, 500)

    @patch("actions.torrent_search._locate_node", return_value="node")
    @patch("actions.torrent_search.subprocess.run")
    def test_search_nonzero_exit_raises(self, mock_run, mock_locate):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="No results from any source")
        with self.assertRaises(ts.TorrentSearchError):
            ts.search("anything")

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
# VLC Player (WebTorrent stream)                                            #
# --------------------------------------------------------------------------- #
class VLCPlayerTests(unittest.TestCase):
    def setUp(self):
        vp._stream_process = None

    @patch("actions.vlc_player._locate_node", return_value="node")
    @patch("actions.vlc_player.subprocess.Popen")
    def test_start_streaming_returns_url(self, mock_popen, mock_node):
        proc = MagicMock()
        url = "http://127.0.0.1:8888/webtorrent/hash/Movie.mp4"
        proc.stdout.readline.return_value = json.dumps({"url": url, "name": "Movie.mp4"}) + "\n"
        mock_popen.return_value = proc

        result = vp.start_streaming("magnet:?xt=urn:btih:abc123", "Inception")
        self.assertEqual(result, url)

    @patch("actions.vlc_player._locate_node", return_value="node")
    @patch("actions.vlc_player.subprocess.Popen")
    def test_start_streaming_no_output_raises(self, mock_popen, mock_node):
        proc = MagicMock()
        proc.stdout.readline.return_value = ""  # no URL line
        proc.poll.return_value = 1
        proc.stderr.read.return_value = "no seeders"
        mock_popen.return_value = proc

        with self.assertRaises(vp.VLCPlayerError):
            vp.start_streaming("magnet:?xt=urn:btih:abc123")

    @patch("actions.vlc_player._locate_node", side_effect=vp.VLCPlayerError("not found"))
    def test_start_streaming_without_node_raises(self, mock_node):
        with self.assertRaises(vp.VLCPlayerError):
            vp.start_streaming("magnet:?xt=urn:btih:abc")

    @patch("actions.vlc_player._locate_node", return_value="node")
    def test_get_status_when_available(self, mock_node):
        status = vp.get_status()
        self.assertIsNotNone(status)
        self.assertIn("ready", status)

    @patch("actions.vlc_player._locate_node", side_effect=vp.VLCPlayerError("not found"))
    def test_get_status_when_unavailable(self, mock_node):
        status = vp.get_status()
        self.assertIsNone(status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
