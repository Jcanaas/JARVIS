"""Tests for actions.anime.sources — AnimeTosho primary, Torrentio fallback.

Regression coverage for the reported bug: episodes of other seasons / spinoffs
showing up for a given episode. AnimeTosho is queried by AniDB *episode* id
and reports how many files a release contains, so a single-file release is
provably the requested episode; Torrentio can only be asked by kitsu id and
never reports a file count.
"""
import unittest
from unittest.mock import MagicMock, patch


def _tosho_item(**over):
    item = {
        "title": "[MTBB] Shingeki no Kyojin - 01 (BD 1080p).mkv",
        "magnet_uri": "magnet:?xt=urn:btih:AAAA&tr=http%3A%2F%2Fnyaa.tracker.wf%3A7777%2Fannounce",
        "seeders": 152,
        "num_files": 1,
        "total_size": 3938751221,
    }
    item.update(over)
    return item


def _json_response(payload):
    return MagicMock(status_code=200, json=lambda: payload,
                     raise_for_status=lambda: None)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def test_parse_extracts_core_fields(self):
        t = self.sources._parse_tosho(_tosho_item())
        self.assertEqual(t.seeders, 152)
        self.assertEqual(t.provider, "AnimeTosho")
        self.assertTrue(t.single_file)
        self.assertIn("3.67 GB", t.size)

    def test_parse_marks_batches_as_not_single_file(self):
        t = self.sources._parse_tosho(_tosho_item(num_files=13))
        self.assertFalse(t.single_file)

    def test_parse_without_magnet_is_dropped(self):
        self.assertIsNone(self.sources._parse_tosho(_tosho_item(magnet_uri="")))

    def test_parse_clamps_sentinel_seeder_counts(self):
        """AnimeTosho reports an absurd seeder count when its tracker scrape
        failed; treating that as a real swarm would float dead releases."""
        t = self.sources._parse_tosho(_tosho_item(seeders=4294967295))
        self.assertEqual(t.seeders, 0)

    def test_parse_handles_non_numeric_seeders(self):
        t = self.sources._parse_tosho(_tosho_item(seeders=None))
        self.assertEqual(t.seeders, 0)

    def test_spanish_detection(self):
        t = self.sources._parse_tosho(
            _tosho_item(title="Attack on Titan - 01 [Castellano]"))
        self.assertTrue(t.spanish)

    def test_single_file_releases_need_no_file_index(self):
        """With one file in the torrent the daemon's largest-video-file
        fallback already lands on it, so we must not pin a stale index."""
        t = self.sources._parse_tosho(_tosho_item())
        self.assertEqual(t.file_idx, -1)


class MagnetAugmentationTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def test_appends_our_trackers(self):
        out = self.sources._augment_magnet("magnet:?xt=urn:btih:AAAA")
        self.assertIn("tracker.opentrackr.org", out)
        self.assertTrue(out.startswith("magnet:?xt=urn:btih:AAAA"))

    def test_does_not_duplicate_a_tracker_already_present(self):
        from actions.trackers import TRACKERS
        from urllib.parse import quote
        first = TRACKERS[0]
        base = f"magnet:?xt=urn:btih:AAAA&tr={quote(first)}"
        out = self.sources._augment_magnet(base)
        self.assertEqual(out.count(quote(first)), 1)

    def test_empty_magnet_passes_through(self):
        self.assertEqual(self.sources._augment_magnet(""), "")


class RankingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources
        self.TorrentResult = sources.TorrentResult

    def test_single_file_outranks_a_better_seeded_batch(self):
        """The core of the reported bug: a huge multi-season batch must not
        beat an unambiguous single-episode release."""
        batch = self.TorrentResult(title="Complete Collection S01-S04",
                                   magnet="m", seeders=800, single_file=False)
        single = self.TorrentResult(title="AoT - 01.mkv", magnet="m",
                                    seeders=150, single_file=True)
        ranked = self.sources._rank([batch, single])
        self.assertTrue(ranked[0].single_file)
        self.assertEqual(ranked[0].seeders, 150)

    def test_dead_releases_sink_below_seeded_ones(self):
        dead_single = self.TorrentResult(title="exact but dead", magnet="m",
                                         seeders=0, single_file=True)
        live_batch = self.TorrentResult(title="batch but alive", magnet="m",
                                        seeders=40, single_file=False)
        ranked = self.sources._rank([dead_single, live_batch])
        self.assertEqual(ranked[0].seeders, 40)

    def test_seeders_break_ties_within_a_tier(self):
        a = self.TorrentResult(title="a", magnet="m", seeders=10, single_file=True)
        b = self.TorrentResult(title="b", magnet="m", seeders=90, single_file=True)
        self.assertEqual([t.seeders for t in self.sources._rank([a, b])], [90, 10])


class PlayabilityTests(unittest.TestCase):
    """Codec inference from the release title.

    Inferred rather than asked of the browser because canPlayType() answered
    "probably" for HEVC in the pywebview webview and the file then failed to
    decode; ffprobe on the real stream showed hevc/Main 10/yuv420p10le.
    """

    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def test_plain_web_release_is_playable(self):
        self.assertTrue(self.sources.is_browser_playable(
            "[SubsPlease] One Piece - 1044 (1080p) [BBB89C1B].mkv"))

    def test_hevc_is_not_playable(self):
        self.assertFalse(self.sources.is_browser_playable(
            "[TatakaeSubs] Attack on Titan - S01E01 (BD 1080p HEVC OPUS)"))

    def test_x265_is_not_playable(self):
        self.assertFalse(self.sources.is_browser_playable(
            "Attack on Titan S01 1080p BluRay x265 10bit"))

    def test_ten_bit_is_not_playable(self):
        self.assertFalse(self.sources.is_browser_playable(
            "[Group] Show - 01 (BD 1080p Hi10P FLAC)"))

    def test_ac3_audio_is_not_playable(self):
        self.assertFalse(self.sources.is_browser_playable(
            "Show.S01E01.1080p.WEBRip.DDP.AC3.x264"))

    def test_unmarked_titles_are_assumed_playable(self):
        """Conservative on purpose: only known-bad markers demote. Most
        releases that say nothing are H.264/AAC WEB rips."""
        self.assertTrue(self.sources.is_browser_playable(
            "[MTBB] Shingeki no Kyojin - 01 (BD 1080p) [5F680D9F].mkv"))


class PlayableRankingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources
        self.TorrentResult = sources.TorrentResult

    def test_playable_wins_when_requested(self):
        hevc = self.TorrentResult(title="hevc", magnet="m", seeders=500,
                                  single_file=True, browser_playable=False)
        h264 = self.TorrentResult(title="h264", magnet="m", seeders=10,
                                  single_file=True, browser_playable=True)
        ranked = self.sources._rank([hevc, h264], prefer_playable=True)
        self.assertEqual(ranked[0].title, "h264")

    def test_codec_is_ignored_when_not_requested(self):
        """The VLC engine decodes everything, so demoting HEVC for it would
        cost quality for no benefit."""
        hevc = self.TorrentResult(title="hevc", magnet="m", seeders=500,
                                  single_file=True, browser_playable=False)
        h264 = self.TorrentResult(title="h264", magnet="m", seeders=10,
                                  single_file=True, browser_playable=True)
        ranked = self.sources._rank([hevc, h264], prefer_playable=False)
        self.assertEqual(ranked[0].title, "hevc")

    def test_dead_playable_still_loses_to_seeded_unplayable(self):
        dead = self.TorrentResult(title="dead", magnet="m", seeders=0,
                                  browser_playable=True)
        alive = self.TorrentResult(title="alive", magnet="m", seeders=30,
                                   browser_playable=False)
        ranked = self.sources._rank([dead, alive], prefer_playable=True)
        self.assertEqual(ranked[0].title, "alive")


class OversizedTests(unittest.TestCase):
    """A 24-minute episode at 3.67 GB is a Blu-ray-grade encode: slow to
    buffer, and in practice almost always HEVC/10-bit even when the title
    never says so. Size is the signal the title withholds."""

    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def test_huge_single_episode_is_flagged(self):
        t = self.sources._parse_tosho(_tosho_item(total_size=3_938_751_221))
        self.assertTrue(t.oversized)

    def test_normal_episode_is_not_flagged(self):
        t = self.sources._parse_tosho(_tosho_item(total_size=600_000_000))
        self.assertFalse(t.oversized)

    def test_a_big_batch_is_not_flagged(self):
        """A season batch is large by design; the check is per-episode."""
        t = self.sources._parse_tosho(
            _tosho_item(num_files=25, total_size=34_000_000_000))
        self.assertFalse(t.oversized)

    def test_right_episode_still_outranks_a_smaller_one(self):
        """A fast-loading wrong episode is the bug this path exists to fix,
        so size must never overrule being the correct episode."""
        TR = self.sources.TorrentResult
        correct_but_huge = TR(title="correct", magnet="m", seeders=150,
                              single_file=True, oversized=True)
        batch_but_small = TR(title="batch", magnet="m", seeders=150,
                             single_file=False, oversized=False)
        ranked = self.sources._rank([batch_but_small, correct_but_huge],
                                    prefer_playable=True)
        self.assertEqual(ranked[0].title, "correct")

    def test_size_breaks_ties_among_equally_correct_releases(self):
        TR = self.sources.TorrentResult
        huge = TR(title="huge", magnet="m", seeders=100, single_file=True,
                  oversized=True)
        lean = TR(title="lean", magnet="m", seeders=100, single_file=True,
                  oversized=False)
        ranked = self.sources._rank([huge, lean], prefer_playable=True)
        self.assertEqual(ranked[0].title, "lean")


class LookupTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def test_search_by_eid_returns_ranked_results(self):
        payload = [_tosho_item(), _tosho_item(num_files=13, seeders=800)]
        with patch("actions.anime.sources.requests.get",
                   return_value=_json_response(payload)):
            out = self.sources.search_animetosho_by_eid(148628)
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0].single_file)

    def test_search_by_eid_without_an_id_makes_no_request(self):
        with patch("actions.anime.sources.requests.get") as mocked:
            self.assertEqual(self.sources.search_animetosho_by_eid(0), [])
        mocked.assert_not_called()

    def test_search_by_eid_swallows_network_errors(self):
        import requests
        with patch("actions.anime.sources.requests.get",
                   side_effect=requests.RequestException("down")):
            self.assertEqual(self.sources.search_animetosho_by_eid(1), [])

    def test_search_by_eid_handles_unexpected_payload_shape(self):
        with patch("actions.anime.sources.requests.get",
                   return_value=_json_response({"error": "nope"})):
            self.assertEqual(self.sources.search_animetosho_by_eid(1), [])


class FindEpisodeTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def test_uses_animetosho_when_it_has_results(self):
        from actions.anime.sources import TorrentResult
        hit = TorrentResult(title="exact", magnet="m", seeders=10,
                            provider="AnimeTosho", single_file=True)
        with patch("actions.anime.sources._resolve_anidb_ids", return_value=(111, 222)), \
             patch("actions.anime.sources.search_animetosho_by_eid", return_value=[hit]), \
             patch("actions.anime.sources._nyaa_results", return_value=[]), \
             patch("actions.anime.sources.verify_seeders", side_effect=lambda x: x), \
             patch("actions.anime.sources._torrentio_fallback") as mocked_fallback:
            out = self.sources.find_episode("7442", episode=1)
        self.assertEqual(out[0].provider, "AnimeTosho")
        mocked_fallback.assert_not_called()

    def test_falls_back_to_torrentio_when_every_source_is_empty(self):
        from actions.anime.sources import TorrentResult
        backup = TorrentResult(title="from torrentio", magnet="m", seeders=5,
                               provider="Torrentio")
        with patch("actions.anime.sources._resolve_anidb_ids", return_value=(111, 222)), \
             patch("actions.anime.sources.search_animetosho_by_eid", return_value=[]), \
             patch("actions.anime.sources.search_animetosho_by_aid", return_value=[]), \
             patch("actions.anime.sources._nyaa_results", return_value=[]), \
             patch("actions.anime.sources.verify_seeders", side_effect=lambda x: x), \
             patch("actions.anime.sources._torrentio_fallback", return_value=[backup]):
            out = self.sources.find_episode("7442", episode=1)
        self.assertEqual(out[0].provider, "Torrentio")

    def test_falls_back_to_series_id_when_episode_id_is_unknown(self):
        """Films and specials often have no per-episode AniDB id; the whole
        series still yields identifiable single-file releases."""
        from actions.anime.sources import TorrentResult
        hit = TorrentResult(title="film", magnet="m", seeders=10, single_file=True)
        with patch("actions.anime.sources._resolve_anidb_ids", return_value=(0, 222)), \
             patch("actions.anime.sources.search_animetosho_by_eid") as by_eid, \
             patch("actions.anime.sources.search_animetosho_by_aid", return_value=[hit]) as by_aid, \
             patch("actions.anime.sources._nyaa_results", return_value=[]), \
             patch("actions.anime.sources.verify_seeders", side_effect=lambda x: x):
            out = self.sources.find_episode("7442", episode=0)
        by_eid.assert_not_called()
        by_aid.assert_called_once()
        self.assertEqual(len(out), 1)

    def test_include_fallback_false_skips_torrentio_entirely(self):
        with patch("actions.anime.sources._resolve_anidb_ids", return_value=(111, 222)), \
             patch("actions.anime.sources.search_animetosho_by_eid", return_value=[]), \
             patch("actions.anime.sources.search_animetosho_by_aid", return_value=[]), \
             patch("actions.anime.sources._nyaa_results", return_value=[]), \
             patch("actions.anime.sources.verify_seeders", side_effect=lambda x: x), \
             patch("actions.anime.sources._torrentio_fallback") as mocked_fallback:
            out = self.sources.find_episode("7442", episode=1, include_fallback=False)
        self.assertEqual(out, [])
        mocked_fallback.assert_not_called()

    def test_empty_kitsu_id_returns_nothing(self):
        self.assertEqual(self.sources.find_episode(""), [])

    def test_strips_kitsu_prefix(self):
        with patch("actions.anime.sources._resolve_anidb_ids",
                   return_value=(0, 0)) as mocked_resolve, \
             patch("actions.anime.sources._nyaa_results", return_value=[]), \
             patch("actions.anime.sources.verify_seeders", side_effect=lambda x: x), \
             patch("actions.anime.sources._torrentio_fallback", return_value=[]):
            self.sources.find_episode("kitsu:7442", episode=3)
        mocked_resolve.assert_called_once_with("7442", 3)


if __name__ == "__main__":
    unittest.main()
