"""Tests for actions.anime.nyaa and the multi-source merge.

Nyaa exists here because AnimeTosho stopped indexing new releases in May 2026
and shuts down in October 2026, so it can no longer serve anything currently
airing. Nyaa is fresh but matches by title only, which is why episode
filtering matters so much.
"""
import unittest
from unittest.mock import MagicMock, patch

_RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:nyaa="https://nyaa.si/xmlns/nyaa">
<channel>
  <item>
    <title>[SubsPlease] Sousou no Frieren - 01 (1080p) [ABC123].mkv</title>
    <nyaa:seeders>255</nyaa:seeders>
    <nyaa:leechers>12</nyaa:leechers>
    <nyaa:infoHash>374ff33f0fae5206c693c7c7e0bd7acc88ef6ad0</nyaa:infoHash>
    <nyaa:size>1.4 GiB</nyaa:size>
    <nyaa:trusted>Yes</nyaa:trusted>
  </item>
  <item>
    <title>[SubsPlease] Sousou no Frieren - 02 (1080p) [DEF456].mkv</title>
    <nyaa:seeders>90</nyaa:seeders>
    <nyaa:leechers>3</nyaa:leechers>
    <nyaa:infoHash>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</nyaa:infoHash>
    <nyaa:size>700.5 MiB</nyaa:size>
    <nyaa:trusted>No</nyaa:trusted>
  </item>
</channel>
</rss>"""


def _rss_response(body=_RSS):
    return MagicMock(status_code=200, content=body, raise_for_status=lambda: None)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.nyaa as nyaa
        self.nyaa = nyaa

    def test_parses_items(self):
        with patch("actions.anime.nyaa.requests.get", return_value=_rss_response()):
            out = self.nyaa.search("frieren")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["seeders"], 255)
        self.assertEqual(out[0]["info_hash"],
                         "374ff33f0fae5206c693c7c7e0bd7acc88ef6ad0")
        self.assertTrue(out[0]["trusted"])

    def test_parses_sizes(self):
        with patch("actions.anime.nyaa.requests.get", return_value=_rss_response()):
            out = self.nyaa.search("frieren")
        self.assertEqual(out[0]["size_bytes"], int(1.4 * 1024**3))
        self.assertEqual(out[1]["size_bytes"], int(700.5 * 1024**2))

    def test_empty_query_makes_no_request(self):
        with patch("actions.anime.nyaa.requests.get") as mocked:
            self.assertEqual(self.nyaa.search("  "), [])
        mocked.assert_not_called()

    def test_network_error_raises_nyaa_error(self):
        import requests
        with patch("actions.anime.nyaa.requests.get",
                   side_effect=requests.RequestException("down")):
            with self.assertRaises(self.nyaa.NyaaError):
                self.nyaa.search("frieren")

    def test_malformed_xml_raises_nyaa_error(self):
        with patch("actions.anime.nyaa.requests.get",
                   return_value=_rss_response(b"<rss><broken>")):
            with self.assertRaises(self.nyaa.NyaaError):
                self.nyaa.search("frieren")

    def test_items_without_a_hash_are_skipped(self):
        body = _RSS.replace(
            b"<nyaa:infoHash>374ff33f0fae5206c693c7c7e0bd7acc88ef6ad0</nyaa:infoHash>",
            b"")
        with patch("actions.anime.nyaa.requests.get", return_value=_rss_response(body)):
            out = self.nyaa.search("frieren")
        self.assertEqual(len(out), 1)


class EpisodeMatchingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.nyaa as nyaa
        self.nyaa = nyaa

    def test_matches_a_dash_numbered_episode(self):
        self.assertTrue(self.nyaa._covers_episode(
            "[SubsPlease] Sousou no Frieren - 01 (1080p).mkv", 1))

    def test_rejects_a_different_episode(self):
        self.assertFalse(self.nyaa._covers_episode(
            "[SubsPlease] Sousou no Frieren - 02 (1080p).mkv", 1))

    def test_matches_inside_a_batch_range(self):
        self.assertTrue(self.nyaa._covers_episode(
            "[SubsPlease] Sousou no Frieren (01-28) (1080p) [Batch]", 14))

    def test_rejects_an_episode_outside_the_batch_range(self):
        self.assertFalse(self.nyaa._covers_episode(
            "[SubsPlease] Sousou no Frieren S2 (01-10) (1080p) [Batch]", 22))

    def test_matches_tilde_ranges(self):
        self.assertTrue(self.nyaa._covers_episode(
            "[Erai-raws] Shingeki no Kyojin S2 - 01~12 [1080p]", 5))

    def test_matches_sxxexx(self):
        self.assertTrue(self.nyaa._covers_episode(
            "Attack on Titan S02E05 [1080p].mkv", 5))

    def test_matches_a_versioned_episode(self):
        self.assertTrue(self.nyaa._covers_episode(
            "[LostYears] Attack on Titan S2 - 01v2 (1080p)", 1))

    def test_resolution_is_not_mistaken_for_an_episode(self):
        """'1080p' must not read as episodes 10-80."""
        self.assertFalse(self.nyaa._covers_episode(
            "[Group] Some Show - 03 (1080p) [XYZ].mkv", 50))

    def test_no_episode_requested_accepts_everything(self):
        self.assertTrue(self.nyaa._covers_episode("anything at all", 0))

    def test_search_episode_filters_out_other_episodes(self):
        with patch("actions.anime.nyaa.requests.get", return_value=_rss_response()):
            out = self.nyaa.search_episode(["Sousou no Frieren"], episode=1)
        self.assertEqual(len(out), 1)
        self.assertIn("- 01 ", out[0]["title"])

    def test_search_episode_tries_the_next_title_when_one_misses(self):
        with patch("actions.anime.nyaa.search", side_effect=[[], [{"title": "X - 01", "info_hash": "h"}]]) as mocked:
            out = self.nyaa.search_episode(["Romaji Title", "English Title"], episode=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(mocked.call_count, 2)


class MergeTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def _r(self, info_hash, provider, single_file=False):
        return self.sources.TorrentResult(
            title=provider, magnet="m", seeders=10, provider=provider,
            info_hash=info_hash, single_file=single_file)

    def test_dedupe_keeps_one_entry_per_infohash(self):
        out = self.sources._dedupe([self._r("aa", "Nyaa"),
                                    self._r("aa", "AnimeTosho")])
        self.assertEqual(len(out), 1)

    def test_dedupe_prefers_the_copy_that_knows_it_is_single_file(self):
        """AnimeTosho reports a file count; Nyaa cannot. The better-informed
        copy is what the ranking and the daemon both benefit from."""
        out = self.sources._dedupe([
            self._r("aa", "Nyaa", single_file=False),
            self._r("aa", "AnimeTosho", single_file=True),
        ])
        self.assertEqual(out[0].provider, "AnimeTosho")

    def test_dedupe_keeps_results_without_a_hash(self):
        out = self.sources._dedupe([self._r("", "Torrentio"),
                                    self._r("", "Torrentio")])
        self.assertEqual(len(out), 2)

    def test_find_episode_merges_both_sources(self):
        tosho = [self._r("aa", "AnimeTosho", single_file=True)]
        nyaa = [self._r("bb", "Nyaa")]
        with patch("actions.anime.sources._resolve_anidb_ids", return_value=(1, 2)), \
             patch("actions.anime.sources._animetosho_for", return_value=tosho), \
             patch("actions.anime.sources._nyaa_results", return_value=nyaa), \
             patch("actions.anime.sources.verify_seeders", side_effect=lambda x: x), \
             patch("actions.anime.sources._torrentio_fallback") as mocked_fallback:
            out = self.sources.find_episode("46474", episode=1)
        self.assertEqual({r.provider for r in out}, {"AnimeTosho", "Nyaa"})
        mocked_fallback.assert_not_called()

    def test_torrentio_only_runs_when_both_sources_come_back_empty(self):
        with patch("actions.anime.sources._resolve_anidb_ids", return_value=(1, 2)), \
             patch("actions.anime.sources._animetosho_for", return_value=[]), \
             patch("actions.anime.sources._nyaa_results", return_value=[]), \
             patch("actions.anime.sources.verify_seeders", side_effect=lambda x: x), \
             patch("actions.anime.sources._torrentio_fallback",
                   return_value=[self._r("cc", "Torrentio")]) as mocked_fallback:
            out = self.sources.find_episode("46474", episode=1)
        mocked_fallback.assert_called_once()
        self.assertEqual(out[0].provider, "Torrentio")


if __name__ == "__main__":
    unittest.main()
