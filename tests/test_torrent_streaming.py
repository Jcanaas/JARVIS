"""Tests for torrent-based streaming (movie_search, torrent_search, peerflix_player)."""
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
    IMDB_RESPONSE = {
        "description": [
            {
                "#IMDB_ID": "tt0137523",
                "#TITLE": "Fight Club",
                "#YEAR": "1999",
                "#IMG_POSTER": "https://m.media-amazon.com/images/M/poster.jpg",
                "description": "An inversion of the personality.",
                "#IMDB_IV": "8.8",
            },
            {
                "#IMDB_ID": "tt0903747",
                "#TITLE": "Breaking Bad",
                "#YEAR": "2008",
                "#IMG_POSTER": "https://m.media-amazon.com/images/M/bb.jpg",
                "description": "A chemistry teacher.",
                "#IMDB_IV": "9.5",
            },
        ]
    }

    @patch("actions.movie_search._http_get")
    def test_search_returns_movies(self, http_get):
        http_get.return_value = self.IMDB_RESPONSE
        results = ms.search("fight club")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "Fight Club")
        self.assertEqual(results[0].imdb_id, "tt0137523")

    def test_empty_query_raises(self):
        with self.assertRaises(ms.MovieSearchError):
            ms.search("   ")

    @patch("actions.movie_search._http_get", return_value={"description": []})
    def test_no_results_raises(self, http_get):
        with self.assertRaises(ms.MovieSearchError):
            ms.search("zzzznotexist")

    def test_parse_movie_extracts_fields(self):
        data = {
            "#IMDB_ID": "tt0137523",
            "#TITLE": "Fight Club",
            "#YEAR": "1999",
            "#IMG_POSTER": "https://m.media-amazon.com/images/poster.jpg",
            "description": "Synopsis here",
            "#IMDB_IV": "8.8",
        }
        movie = ms._parse_movie(data, "movie")
        self.assertEqual(movie.imdb_id, "tt0137523")
        self.assertEqual(movie.title, "Fight Club")
        self.assertEqual(movie.release_year, 1999)
        self.assertAlmostEqual(movie.rating, 8.8, places=1)

    @patch("actions.movie_search._http_get", return_value={"description": [{"#IMDB_ID": "tt0137523", "#TITLE": "Fight Club", "#YEAR": "1999"}]})
    def test_search_action_returns_formatted_string(self, http_get):
        out = ms.search_action({"query": "fight club"})
        self.assertIn("Encontré", out)
        self.assertIn("Fight Club", out)
        self.assertIn("1999", out)


# --------------------------------------------------------------------------- #
# Torrent Search                                                             #
# --------------------------------------------------------------------------- #
class TorrentSearchTests(unittest.TestCase):
    SEARCH_HTML = """
    <html>
      <table>
        <tbody>
          <tr>
            <td><a href="/torrent/12345/fight-club/">Fight Club 1999 1080p</a></td>
            <td>500</td>
            <td>100</td>
          </tr>
          <tr>
            <td><a href="/torrent/12346/fight-club-hd/">Fight Club HD</a></td>
            <td>300</td>
            <td>50</td>
          </tr>
        </tbody>
      </table>
    </html>
    """

    def test_torrent_dataclass(self):
        t = ts.Torrent("Fight Club", "magnet:?xt=urn:btih:abc", seeders=500, leechers=100)
        self.assertEqual(t.title, "Fight Club")
        self.assertEqual(t.seeders, 500)
        d = t.to_dict()
        self.assertIn("title", d)
        self.assertIn("magnet", d)

    @patch("actions.torrent_search._get_working_domain", return_value=None)
    def test_search_all_domains_down_raises(self, mock_domain):
        with self.assertRaises(ts.TorrentSearchError):
            ts.search("anything")

    def test_empty_query_raises(self):
        with self.assertRaises(ts.TorrentSearchError):
            ts.search("   ")

    @patch("actions.torrent_search._get_working_domain", return_value="https://1337x.to")
    @patch("actions.torrent_search.requests.head")
    def test_get_working_domain_tries_all(self, mock_head, _):
        # First call fails, second succeeds
        mock_head.side_effect = [
            Exception("down"),
            MagicMock(status_code=200),
        ]
        domain = ts._get_working_domain()
        self.assertIsNotNone(domain)

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
    @patch("actions.peerflix_player.shutil.which")
    def test_locate_peerflix_finds_global(self, which):
        which.return_value = "/usr/local/bin/peerflix"
        exe = pp._locate_peerflix()
        self.assertEqual(exe, "/usr/local/bin/peerflix")

    @patch("actions.peerflix_player.shutil.which")
    def test_locate_peerflix_falls_back_to_npx(self, which):
        # First call (peerflix) returns None, second (npx) succeeds
        which.side_effect = [None, "/usr/local/bin/npx"]
        exe = pp._locate_peerflix()
        self.assertIn("npx", exe)

    @patch("actions.peerflix_player.shutil.which", return_value=None)
    def test_locate_peerflix_all_fail_raises(self, which):
        with self.assertRaises(pp.PeerflixError):
            pp._locate_peerflix()

    @patch("actions.peerflix_player._locate_peerflix", return_value="peerflix")
    @patch("actions.peerflix_player.subprocess.Popen")
    def test_play_launches_peerflix(self, popen, locate):
        proc = MagicMock()
        popen.return_value = proc
        magnet = "magnet:?xt=urn:btih:abc123"
        result = pp.play(magnet, "Fight Club")
        self.assertEqual(result, proc)
        # Verify command includes magnet and title
        cmd = popen.call_args[0][0]
        self.assertIn(magnet, cmd)
        self.assertIn("Fight Club", cmd)

    @patch("actions.peerflix_player._locate_peerflix", side_effect=pp.PeerflixError("not found"))
    def test_play_without_peerflix_raises(self, locate):
        with self.assertRaises(pp.PeerflixError):
            pp.play("magnet:?xt=urn:btih:abc")

    @patch("actions.peerflix_player._locate_peerflix", return_value="peerflix")
    def test_get_status_when_available(self, locate):
        status = pp.get_status()
        self.assertIsNotNone(status)
        self.assertIn("ready", status)

    @patch("actions.peerflix_player._locate_peerflix", side_effect=pp.PeerflixError("not found"))
    def test_get_status_when_unavailable(self, locate):
        status = pp.get_status()
        self.assertIsNone(status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
