"""Tests for actions.anime.health — BEP15 scrape of real swarm size.

Regression coverage for "nada me encuentra seeds": sources advertise seeder
counts that are inflated ~10x and sometimes fabricated (541 advertised, 0
real), so ranking on them put dead torrents first.
"""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HASH_A = bytes.fromhex("5b84577ddf66829f3cdaeafbe53b167b4e91b8b1")
HASH_B = bytes.fromhex("672aec4dcc004cee3fdd8ad5de299f9ad30c956b")


def _connect_response(transaction_id=0, connection_id=0x1234):
    return struct.pack(">IIQ", 0, transaction_id, connection_id)


def _scrape_response(counts):
    """counts: list of seeder numbers, in the order the hashes were sent."""
    body = b"".join(struct.pack(">III", s, 0, 0) for s in counts)
    return struct.pack(">II", 2, 0) + body


class ScrapeTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.health as health
        self.health = health

    def _fake_socket(self, responses):
        sock = MagicMock()
        sock.recvfrom.side_effect = [(r, None) for r in responses]
        return sock

    def test_parses_seeder_counts_for_each_hash(self):
        sock = self._fake_socket([_connect_response(), _scrape_response([6, 33])])
        with patch("socket.socket", return_value=sock), \
             patch("socket.gethostbyname", return_value="1.2.3.4"):
            out = self.health._scrape_one("udp://t.example:80/announce",
                                          [HASH_A, HASH_B])
        self.assertEqual(out, {HASH_A: 6, HASH_B: 33})

    def test_zero_seeders_is_reported_not_dropped(self):
        """A dead swarm is the single most important answer here."""
        sock = self._fake_socket([_connect_response(), _scrape_response([0])])
        with patch("socket.socket", return_value=sock), \
             patch("socket.gethostbyname", return_value="1.2.3.4"):
            out = self.health._scrape_one("udp://t.example:80/announce", [HASH_A])
        self.assertEqual(out, {HASH_A: 0})

    def test_non_udp_tracker_is_skipped(self):
        with patch("socket.socket") as sock:
            out = self.health._scrape_one("http://t.example/announce", [HASH_A])
        self.assertEqual(out, {})
        sock.assert_not_called()

    def test_dns_failure_returns_empty(self):
        with patch("socket.gethostbyname", side_effect=OSError("no dns")):
            out = self.health._scrape_one("udp://t.example:80/announce", [HASH_A])
        self.assertEqual(out, {})

    def test_timeout_returns_empty(self):
        sock = MagicMock()
        sock.recvfrom.side_effect = TimeoutError()
        with patch("socket.socket", return_value=sock), \
             patch("socket.gethostbyname", return_value="1.2.3.4"):
            out = self.health._scrape_one("udp://t.example:80/announce", [HASH_A])
        self.assertEqual(out, {})

    def test_wrong_action_in_connect_reply_is_rejected(self):
        bad = struct.pack(">IIQ", 3, 0, 0)  # action 3 = error
        sock = self._fake_socket([bad])
        with patch("socket.socket", return_value=sock), \
             patch("socket.gethostbyname", return_value="1.2.3.4"):
            out = self.health._scrape_one("udp://t.example:80/announce", [HASH_A])
        self.assertEqual(out, {})

    def test_truncated_scrape_body_stops_cleanly(self):
        """A tracker answering about fewer hashes than asked must not make us
        read past the buffer or invent counts."""
        short = struct.pack(">II", 2, 0) + struct.pack(">III", 9, 0, 0)
        sock = self._fake_socket([_connect_response(), short])
        with patch("socket.socket", return_value=sock), \
             patch("socket.gethostbyname", return_value="1.2.3.4"):
            out = self.health._scrape_one("udp://t.example:80/announce",
                                          [HASH_A, HASH_B])
        self.assertEqual(out, {HASH_A: 9})


class MergeTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.health as health
        self.health = health

    def test_takes_the_highest_count_across_trackers(self):
        """Each tracker only sees peers that announced to it, so the max is
        the closest single estimate of the real swarm."""
        with patch.object(self.health, "_scrape_one",
                          side_effect=[{HASH_A: 3}, {HASH_A: 41}, {HASH_A: 12}]), \
             patch.object(self.health, "TRACKERS", ["udp://a:80/a", "udp://b:80/a",
                                                     "udp://c:80/a"]):
            out = self.health.scrape_seeders([HASH_A])
        self.assertEqual(out, {HASH_A: 41})

    def test_all_trackers_failing_returns_empty_not_zeroes(self):
        """Callers must be able to tell 'unknown' from 'dead'."""
        with patch.object(self.health, "_scrape_one", return_value={}), \
             patch.object(self.health, "TRACKERS", ["udp://a:80/a"]):
            out = self.health.scrape_seeders([HASH_A])
        self.assertEqual(out, {})

    def test_ignores_malformed_hashes(self):
        with patch.object(self.health, "_scrape_one") as mocked, \
             patch.object(self.health, "TRACKERS", ["udp://a:80/a"]):
            mocked.return_value = {}
            self.health.scrape_seeders([b"too-short", b"", None])
        mocked.assert_not_called()

    def test_deduplicates_hashes_before_asking(self):
        with patch.object(self.health, "_scrape_one", return_value={}) as mocked, \
             patch.object(self.health, "TRACKERS", ["udp://a:80/a"]):
            self.health.scrape_seeders([HASH_A, HASH_A, HASH_A])
        sent = mocked.call_args[0][1]
        self.assertEqual(len(sent), 1)


class CacheTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.health as health
        import actions.anime.cache as cache
        self.health, self.cache = health, cache
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = cache._ROOT
        cache._ROOT = Path(self._tmp.name)

    def tearDown(self):
        self.cache._ROOT = self._orig
        self._tmp.cleanup()

    def test_second_call_is_served_from_cache(self):
        with patch.object(self.health, "scrape_seeders",
                          return_value={HASH_A: 7}) as mocked:
            self.health.scrape_seeders_cached([HASH_A])
            out = self.health.scrape_seeders_cached([HASH_A])
        self.assertEqual(out, {HASH_A: 7})
        mocked.assert_called_once()

    def test_zero_is_cached_like_any_other_count(self):
        with patch.object(self.health, "scrape_seeders",
                          return_value={HASH_A: 0}) as mocked:
            self.health.scrape_seeders_cached([HASH_A])
            out = self.health.scrape_seeders_cached([HASH_A])
        self.assertEqual(out, {HASH_A: 0})
        mocked.assert_called_once()

    def test_only_uncached_hashes_are_rescraped(self):
        with patch.object(self.health, "scrape_seeders", return_value={HASH_A: 5}):
            self.health.scrape_seeders_cached([HASH_A])
        with patch.object(self.health, "scrape_seeders",
                          return_value={HASH_B: 9}) as mocked:
            out = self.health.scrape_seeders_cached([HASH_A, HASH_B])
        self.assertEqual(mocked.call_args[0][0], [HASH_B])
        self.assertEqual(out, {HASH_A: 5, HASH_B: 9})


class VerifySeedersTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def _result(self, seeders, info_hash=HASH_A.hex()):
        return self.sources.TorrentResult(
            title="x", magnet="m", seeders=seeders, info_hash=info_hash)

    def test_replaces_advertised_count_with_the_real_one(self):
        r = self._result(541)
        with patch("actions.anime.health.scrape_seeders_cached",
                   return_value={HASH_A: 0}):
            self.sources.verify_seeders([r])
        self.assertEqual(r.seeders, 0)
        self.assertTrue(r.seeders_verified)

    def test_leaves_unknown_results_advertised_and_unverified(self):
        """No answer means the trackers didn't know, not that it's dead."""
        r = self._result(120)
        with patch("actions.anime.health.scrape_seeders_cached", return_value={}):
            self.sources.verify_seeders([r])
        self.assertEqual(r.seeders, 120)
        self.assertFalse(r.seeders_verified)

    def test_results_without_an_infohash_are_left_alone(self):
        r = self._result(50, info_hash="")
        with patch("actions.anime.health.scrape_seeders_cached",
                   return_value={HASH_A: 3}):
            self.sources.verify_seeders([r])
        self.assertEqual(r.seeders, 50)
        self.assertFalse(r.seeders_verified)

    def test_scrape_failure_does_not_break_the_search(self):
        r = self._result(80)
        with patch("actions.anime.health.scrape_seeders_cached",
                   side_effect=RuntimeError("udp blocked")):
            out = self.sources.verify_seeders([r])
        self.assertEqual(out[0].seeders, 80)


class InfoHashParsingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.sources as sources
        self.sources = sources

    def test_reads_a_hex_magnet(self):
        got = self.sources._info_hash_from_magnet(f"magnet:?xt=urn:btih:{HASH_A.hex()}&dn=x")
        self.assertEqual(got, HASH_A.hex())

    def test_reads_a_base32_magnet(self):
        """AnimeTosho serves base32 magnets; Torrentio serves hex."""
        import base64
        b32 = base64.b32encode(HASH_A).decode()
        got = self.sources._info_hash_from_magnet(f"magnet:?xt=urn:btih:{b32}&dn=x")
        self.assertEqual(got, HASH_A.hex())

    def test_magnet_without_a_hash_returns_empty(self):
        self.assertEqual(self.sources._info_hash_from_magnet("magnet:?dn=x"), "")

    def test_empty_input_returns_empty(self):
        self.assertEqual(self.sources._info_hash_from_magnet(""), "")


if __name__ == "__main__":
    unittest.main()
