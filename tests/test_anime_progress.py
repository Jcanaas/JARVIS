"""Tests for actions.anime.progress — isolated from the user's real data dir.

Every test gets its own SQLite file under a temp directory: the module keeps
a single module-level connection for cheap frequent writes, so tests reset
that connection between cases rather than opening/closing per call.
"""
import tempfile
import unittest
from pathlib import Path


class ProgressTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.progress as progress
        self.progress = progress
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = progress._DB_PATH
        self._orig_conn = progress._conn
        progress._DB_PATH = Path(self._tmp.name) / "anime_progress.sqlite3"
        progress._conn = None

    def tearDown(self):
        if self.progress._conn is not None:
            self.progress._conn.close()
        self.progress._conn = self._orig_conn
        self.progress._DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def test_kitsu_key_normalizes_prefix(self):
        self.assertEqual(self.progress.kitsu_key("46474"), "kitsu:46474")
        self.assertEqual(self.progress.kitsu_key("kitsu:46474"), "kitsu:46474")

    def test_save_and_get_roundtrip(self):
        key = self.progress.kitsu_key("46474")
        entry = self.progress.save(key, 1, position_s=300, duration_s=1560, title="Frieren")
        self.assertFalse(entry.completed)
        self.assertAlmostEqual(entry.fraction, 300 / 1560)

        fetched = self.progress.get(key, 1)
        self.assertEqual(fetched.title, "Frieren")
        self.assertEqual(fetched.position_s, 300)

    def test_save_marks_completed_past_threshold(self):
        key = self.progress.kitsu_key("46474")
        entry = self.progress.save(key, 2, position_s=1400, duration_s=1560)
        self.assertTrue(entry.completed)

    def test_completed_does_not_flicker_off_on_rewind(self):
        key = self.progress.kitsu_key("46474")
        self.progress.save(key, 2, position_s=1400, duration_s=1560)
        entry = self.progress.save(key, 2, position_s=10, duration_s=1560)
        self.assertTrue(entry.completed)

    def test_title_and_poster_are_sticky_once_set(self):
        key = self.progress.kitsu_key("46474")
        self.progress.save(key, 1, position_s=10, duration_s=1560,
                           title="Frieren", poster_url="http://x/p.jpg")
        entry = self.progress.save(key, 1, position_s=20, duration_s=1560)
        self.assertEqual(entry.title, "Frieren")
        self.assertEqual(entry.poster_url, "http://x/p.jpg")

    def test_mark_completed_without_position(self):
        key = self.progress.kitsu_key("46474")
        self.progress.mark_completed(key, 5, title="Frieren")
        entry = self.progress.get(key, 5)
        self.assertTrue(entry.completed)
        self.assertEqual(entry.position_s, 0)

    def test_episode_states_partial_vs_seen(self):
        key = self.progress.kitsu_key("46474")
        self.progress.save(key, 1, position_s=100, duration_s=1560)   # partial
        self.progress.save(key, 2, position_s=1500, duration_s=1560)  # seen
        states = self.progress.episode_states(key)
        self.assertEqual(states, {1: "partial", 2: "seen"})

    def test_episode_states_omits_untouched_episodes(self):
        key = self.progress.kitsu_key("46474")
        self.progress.save(key, 1, position_s=100, duration_s=1560)
        self.assertNotIn(3, self.progress.episode_states(key))

    def test_latest_episode_is_most_recently_touched(self):
        key = self.progress.kitsu_key("46474")
        self.progress.save(key, 1, position_s=100, duration_s=1560)
        self.progress.save(key, 2, position_s=200, duration_s=1560)
        self.assertEqual(self.progress.latest_episode(key).episode, 2)

    def test_continue_watching_excludes_completed_series(self):
        key = self.progress.kitsu_key("46474")
        self.progress.save(key, 1, position_s=1500, duration_s=1560)  # completed
        self.assertEqual(self.progress.continue_watching(), [])

    def test_continue_watching_returns_latest_unfinished_per_series(self):
        frieren = self.progress.kitsu_key("46474")
        onepiece = self.progress.kitsu_key("12")
        self.progress.save(frieren, 1, position_s=300, duration_s=1560)
        self.progress.save(onepiece, 1044, position_s=400, duration_s=1440)
        rail = self.progress.continue_watching()
        keys = {e.media_key for e in rail}
        self.assertEqual(keys, {frieren, onepiece})

    def test_continue_watching_respects_limit(self):
        for i in range(5):
            key = self.progress.kitsu_key(str(i))
            self.progress.save(key, 1, position_s=100, duration_s=1560)
        self.assertEqual(len(self.progress.continue_watching(limit=2)), 2)

    def test_delete_series_removes_all_its_episodes(self):
        key = self.progress.kitsu_key("46474")
        self.progress.save(key, 1, position_s=100, duration_s=1560)
        self.progress.save(key, 2, position_s=100, duration_s=1560)
        self.progress.delete_series(key)
        self.assertEqual(self.progress.episode_states(key), {})

    def test_delete_series_does_not_touch_other_series(self):
        frieren = self.progress.kitsu_key("46474")
        onepiece = self.progress.kitsu_key("12")
        self.progress.save(frieren, 1, position_s=100, duration_s=1560)
        self.progress.save(onepiece, 1, position_s=100, duration_s=1440)
        self.progress.delete_series(frieren)
        self.assertEqual(self.progress.episode_states(onepiece), {1: "partial"})

    def test_get_missing_entry_returns_none(self):
        key = self.progress.kitsu_key("999999")
        self.assertIsNone(self.progress.get(key, 1))

    def test_fraction_is_zero_when_duration_unknown(self):
        key = self.progress.kitsu_key("46474")
        entry = self.progress.save(key, 1, position_s=100, duration_s=0)
        self.assertEqual(entry.fraction, 0.0)


if __name__ == "__main__":
    unittest.main()
