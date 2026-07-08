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
    def test_search_passes_spanish_flag_and_parses_field(self, mock_run, mock_locate):
        payload = [{
            "name": "Fight Club Castellano", "magnet": "magnet:?xt=urn:btih:esp",
            "seeders": 120, "leechers": 5, "size": "2GB", "spanish": True,
        }]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
        torrents = ts.search("fight club", spanish=True)
        self.assertTrue(torrents[0].spanish)
        # The --spanish flag must reach the node script.
        cmd = mock_run.call_args[0][0]
        self.assertIn("--spanish", cmd)

    @patch("actions.torrent_search._locate_node", return_value="node")
    @patch("actions.torrent_search.subprocess.run")
    def test_search_omits_spanish_flag_when_disabled(self, mock_run, mock_locate):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(self.SEARCH_JSON), stderr="")
        ts.search("fight club", spanish=False)
        cmd = mock_run.call_args[0][0]
        self.assertNotIn("--spanish", cmd)

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


# --------------------------------------------------------------------------- #
# OpenSubtitles                                                              #
# --------------------------------------------------------------------------- #
from actions import opensubtitles as osub  # noqa: E402


class OpenSubtitlesTests(unittest.TestCase):
    SEARCH_RESPONSE = [
        {
            "SubDownloadLink": "https://dl.opensubtitles.org/sub111.gz",
            "LanguageName": "Spanish",
            "MovieReleaseName": "Inception.2010.1080p",
            "MovieYear": "2010",
            "SubDownloadsCnt": "5000",
            "SubFileName": "Inception.es.srt",
        },
        {
            "SubDownloadLink": "https://dl.opensubtitles.org/sub222.gz",
            "LanguageName": "Spanish",
            "MovieReleaseName": "Inception.2010.720p",
            "MovieYear": "1999",
            "SubDownloadsCnt": "9000",
            "SubFileName": "Inception2.es.srt",
        },
    ]

    @patch("actions.opensubtitles.requests.get")
    def test_search_prioritises_year_then_downloads(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: self.SEARCH_RESPONSE,
            raise_for_status=lambda: None,
        )
        # With year=2010, the 2010 entry wins even though it has fewer downloads.
        subs = osub.search("inception", language="es", year=2010)
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0].year, 2010)
        self.assertIn("sub111", subs[0].download_link)

    @patch("actions.opensubtitles.requests.get")
    def test_search_sorts_by_downloads_without_year(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: self.SEARCH_RESPONSE,
            raise_for_status=lambda: None,
        )
        subs = osub.search("inception", language="es")
        # No year given → most-downloaded first.
        self.assertEqual(subs[0].downloads, 9000)

    def test_search_empty_query_raises(self):
        with self.assertRaises(osub.OpenSubtitlesError):
            osub.search("   ")

    @patch("actions.opensubtitles.time.sleep", lambda *a: None)
    @patch("actions.opensubtitles.requests.get", return_value=MagicMock(
        status_code=200, json=lambda: [], raise_for_status=lambda: None))
    def test_search_no_results_raises(self, mock_get):
        with self.assertRaises(osub.OpenSubtitlesError):
            osub.search("zzznotexist")

    @patch("actions.opensubtitles.time.sleep", lambda *a: None)
    @patch("actions.opensubtitles.requests.get")
    def test_search_retries_then_succeeds(self, mock_get):
        # First call returns empty (rate-limited), second returns results.
        empty = MagicMock(status_code=200, json=lambda: [], raise_for_status=lambda: None)
        full = MagicMock(status_code=200, json=lambda: self.SEARCH_RESPONSE,
                         raise_for_status=lambda: None)
        mock_get.side_effect = [empty, full]
        subs = osub.search("inception", language="es")
        self.assertTrue(len(subs) >= 1)

    @patch("actions.opensubtitles.requests.get")
    def test_download_decompresses_gzip(self, mock_get):
        import gzip
        srt = b"1\n00:00:01,000 --> 00:00:02,000\nHola\n"
        mock_get.return_value = MagicMock(
            status_code=200,
            content=gzip.compress(srt),
            raise_for_status=lambda: None,
        )
        import tempfile
        from pathlib import Path
        sub = osub.Subtitle("https://dl.example/sub.gz", "Spanish", "rel", 10, "sub.srt", 2010)
        with tempfile.TemporaryDirectory() as tmp:
            path = osub.download(sub, dest_dir=Path(tmp))
            self.assertTrue(path.endswith("sub.srt"))
            self.assertEqual(Path(path).read_bytes(), srt)

    @patch("actions.opensubtitles.requests.get")
    def test_download_accepts_plain_bytes(self, mock_get):
        srt = b"1\n00:00:01,000 --> 00:00:02,000\nHi\n"
        mock_get.return_value = MagicMock(
            status_code=200, content=srt, raise_for_status=lambda: None)
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = osub.download("https://dl.example/plain.srt", dest_dir=Path(tmp))
            self.assertEqual(Path(path).read_bytes(), srt)

    def test_download_no_link_raises(self):
        with self.assertRaises(osub.OpenSubtitlesError):
            osub.download("")


# --------------------------------------------------------------------------- #
# Torrentio (Spanish-source aggregator)                                      #
# --------------------------------------------------------------------------- #
from actions import torrentio as tio  # noqa: E402


class TorrentioTests(unittest.TestCase):
    RESPONSE = {
        "streams": [
            {
                "name": "Torrentio\n1080p",
                "title": "Matrix [BDremux][Castellano]\n\U0001f464 16 \U0001f4be 8 GB ⚙️ MejorTorrent\n\U0001f1ea\U0001f1f8",
                "infoHash": "aaaa1111bbbb2222cccc3333dddd4444eeee5555",
                "fileIdx": 0,
            },
            {
                "name": "Torrentio\n2160p",
                "title": "The.Matrix.1999.2160p.BluRay.x265\n\U0001f464 200 \U0001f4be 50 GB ⚙️ YTS",
                "infoHash": "1111aaaa2222bbbb3333cccc4444dddd5555eeee",
                "fileIdx": 0,
            },
        ]
    }

    def test_parse_stream_extracts_fields(self):
        s = tio._parse_stream(self.RESPONSE["streams"][0])
        self.assertEqual(s.seeders, 16)
        self.assertEqual(s.size, "8 GB")
        self.assertEqual(s.provider, "MejorTorrent")
        self.assertTrue(s.spanish)
        self.assertIn("btih:aaaa1111", s.magnet)

    def test_no_imdb_raises(self):
        with self.assertRaises(tio.TorrentioError):
            tio.search("")

    @patch("actions.torrentio.requests.get")
    def test_search_ranks_spanish_first(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: self.RESPONSE, raise_for_status=lambda: None)
        streams = tio.search("tt0133093", spanish=True)
        # The Castilian MejorTorrent release ranks above the higher-seeded YTS one.
        self.assertTrue(streams[0].spanish)
        self.assertEqual(streams[0].provider, "MejorTorrent")

    @patch("actions.torrentio.requests.get")
    def test_search_no_streams_raises(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"streams": []}, raise_for_status=lambda: None)
        with self.assertRaises(tio.TorrentioError):
            tio.search("tt0000000")


# --------------------------------------------------------------------------- #
# Peerflix addon (Spanish-focused Stremio source)                           #
# --------------------------------------------------------------------------- #
from actions import peerflix_addon as pfa  # noqa: E402


class PeerflixAddonTests(unittest.TestCase):
    RESPONSE = {
        "streams": [
            {
                "name": "Peerflix \U0001f1ea\U0001f1f8 1080p",
                "description": "Matrix [BDremux][Castellano]\nMatrix.mkv\n\U0001f464 11 \U0001f4be 18 GB",
                "infoHash": "aaaa1111bbbb2222cccc3333dddd4444eeee5555",
                "seed": 11,
                "sizebytes": 19327352832,
                "language": "Spanish",
                "quality": "1080p",
            },
            {
                "name": "Peerflix \U0001f1ea\U0001f1f8 4K",
                "description": "Matrix [4K UHDremux][Castellano]\n\U0001f464 14",
                "infoHash": "1111aaaa2222bbbb3333cccc4444dddd5555eeee",
                "seed": 14,
                "sizebytes": 60000000000,
                "language": "Spanish",
                "quality": "4K",
            },
        ]
    }

    def test_parse_uses_structured_fields(self):
        s = pfa._parse_stream(self.RESPONSE["streams"][0])
        self.assertEqual(s.seeders, 11)
        self.assertIn("GB", s.size)
        self.assertTrue(s.spanish)
        self.assertIn("btih:aaaa1111", s.magnet)

    def test_no_imdb_raises(self):
        with self.assertRaises(pfa.PeerflixAddonError):
            pfa.search("")

    @patch("actions.peerflix_addon.requests.get")
    def test_search_ranks_by_seeders(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: self.RESPONSE, raise_for_status=lambda: None)
        streams = pfa.search("tt0133093", spanish=True)
        self.assertEqual(len(streams), 2)
        # Both Spanish → higher seeders (4K, 14) first.
        self.assertEqual(streams[0].seeders, 14)

    @patch("actions.peerflix_addon.requests.get")
    def test_search_no_streams_raises(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200, json=lambda: {"streams": []}, raise_for_status=lambda: None)
        with self.assertRaises(pfa.PeerflixAddonError):
            pfa.search("tt0000000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
