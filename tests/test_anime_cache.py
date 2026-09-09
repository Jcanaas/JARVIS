"""Tests for actions.anime.cache, isolated from the user's real data dir.

The serve-stale-on-error tests are regression coverage for a real outage:
the whole strem.fun domain (Kitsu catalog *and* Torrentio) became unreachable
during development, and every catalog call raised even though usable data was
on disk, expired only by hours.
"""
import tempfile
import time
import unittest
from pathlib import Path


class CacheTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.cache as cache
        self.cache = cache
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_root = cache._ROOT
        cache._ROOT = Path(self._tmp.name)

    def tearDown(self):
        self.cache._ROOT = self._orig_root
        self._tmp.cleanup()

    # -- basic get/set ----------------------------------------------------

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.cache.get("meta", "nope"))

    def test_set_then_get_roundtrip(self):
        self.cache.set("meta", "42", {"title": "Frieren"})
        self.assertEqual(self.cache.get("meta", "42"), {"title": "Frieren"})

    def test_get_respects_ttl(self):
        self.cache.set("meta", "42", {"title": "Frieren"})
        self.assertIsNone(self.cache.get("meta", "42", ttl=0))

    def test_get_stale_ignores_ttl(self):
        self.cache.set("meta", "42", {"title": "Frieren"})
        # Expired for a normal read...
        self.assertIsNone(self.cache.get("meta", "42", ttl=0))
        # ...but still retrievable as stale.
        self.assertEqual(self.cache.get_stale("meta", "42"), {"title": "Frieren"})

    def test_get_stale_missing_returns_none(self):
        self.assertIsNone(self.cache.get_stale("meta", "nope"))

    def test_corrupt_entry_is_treated_as_a_miss(self):
        self.cache.set("meta", "42", {"title": "Frieren"})
        path = self.cache._path("meta", "42")
        path.write_text("{ this is not valid json", encoding="utf-8")
        self.assertIsNone(self.cache.get("meta", "42"))

    # -- cached() happy paths ---------------------------------------------

    def test_cached_calls_producer_on_miss(self):
        calls = []

        def producer():
            calls.append(1)
            return {"v": 1}

        self.assertEqual(self.cache.cached("meta", "k", producer), {"v": 1})
        self.assertEqual(len(calls), 1)

    def test_cached_skips_producer_on_fresh_hit(self):
        calls = []

        def producer():
            calls.append(1)
            return {"v": 1}

        self.cache.cached("meta", "k", producer)
        self.cache.cached("meta", "k", producer)
        self.assertEqual(len(calls), 1)

    def test_cached_does_not_store_empty_results(self):
        """A transient upstream hiccup returning [] must not get pinned for
        the whole TTL — the next call should retry."""
        calls = []

        def producer():
            calls.append(1)
            return []

        self.cache.cached("meta", "k", producer)
        self.cache.cached("meta", "k", producer)
        self.assertEqual(len(calls), 2)

    # -- serve-stale-on-error (the outage regression) ---------------------

    def test_cached_serves_stale_when_producer_raises(self):
        self.cache.set("catalog_trending", "trending", [{"title": "AoT"}])

        def failing_producer():
            raise RuntimeError("strem.fun unreachable")

        # ttl=0 forces the entry to read as expired, exactly like a real
        # 22-hour-old entry against a 6-hour TTL.
        result = self.cache.cached("catalog_trending", "trending",
                                   failing_producer, ttl=0)
        self.assertEqual(result, [{"title": "AoT"}])

    def test_cached_propagates_error_when_nothing_cached(self):
        def failing_producer():
            raise RuntimeError("strem.fun unreachable")

        with self.assertRaises(RuntimeError):
            self.cache.cached("catalog_trending", "never-fetched",
                              failing_producer)

    def test_cached_prefers_fresh_over_stale_fallback(self):
        """A working producer must still win over an existing stale entry —
        serve-stale is a fallback, not a cache-forever."""
        self.cache.set("meta", "k", {"v": "old"})
        result = self.cache.cached("meta", "k", lambda: {"v": "new"}, ttl=0)
        self.assertEqual(result, {"v": "new"})

    def test_stale_fallback_does_not_overwrite_the_stored_entry(self):
        """Serving stale must not refresh the timestamp — otherwise a long
        outage would keep resetting the clock and the entry would never be
        seen as expired once the service returns."""
        self.cache.set("meta", "k", {"v": "old"})
        before = self.cache._path("meta", "k").read_text(encoding="utf-8")

        def failing_producer():
            raise RuntimeError("down")

        self.cache.cached("meta", "k", failing_producer, ttl=0)
        after = self.cache._path("meta", "k").read_text(encoding="utf-8")
        self.assertEqual(before, after)

    # -- invalidate --------------------------------------------------------

    def test_invalidate_single_key(self):
        self.cache.set("meta", "a", {"v": 1})
        self.cache.set("meta", "b", {"v": 2})
        self.cache.invalidate("meta", "a")
        self.assertIsNone(self.cache.get("meta", "a"))
        self.assertIsNotNone(self.cache.get("meta", "b"))

    def test_invalidate_whole_namespace(self):
        self.cache.set("meta", "a", {"v": 1})
        self.cache.set("meta", "b", {"v": 2})
        self.cache.invalidate("meta")
        self.assertIsNone(self.cache.get("meta", "a"))
        self.assertIsNone(self.cache.get("meta", "b"))

    def test_invalidate_missing_key_does_not_raise(self):
        self.cache.invalidate("meta", "nope")


if __name__ == "__main__":
    unittest.main()
