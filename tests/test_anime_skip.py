"""Tests for actions.anime.skip — AniSkip client, network mocked out."""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch


class SkipTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.skip as skip
        self.skip = skip
        import actions.anime.cache as cache
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._cache_root = patch.object(cache, "_ROOT", Path(self._temp_dir.name))
        self._cache_root.start()
        self.addCleanup(self._cache_root.stop)
        cache.invalidate("aniskip")

    def _mock_response(self, status_code, json_body):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_body
        if status_code >= 400 and status_code != 404:
            resp.raise_for_status.side_effect = Exception("http error")
        else:
            resp.raise_for_status.return_value = None
        return resp

    def test_missing_mal_id_returns_empty_without_a_request(self):
        with patch("requests.get") as mocked:
            result = self.skip.get_skip_windows(0, 1)
        self.assertEqual(result, [])
        mocked.assert_not_called()

    def test_missing_episode_returns_empty_without_a_request(self):
        with patch("requests.get") as mocked:
            result = self.skip.get_skip_windows(52991, 0)
        self.assertEqual(result, [])
        mocked.assert_not_called()

    def test_parses_op_and_ed_windows(self):
        body = {
            "found": True,
            "results": [
                {"interval": {"startTime": 3.2, "endTime": 93.2}, "skipType": "op"},
                {"interval": {"startTime": 1460, "endTime": 1560}, "skipType": "ed"},
            ],
        }
        with patch("requests.get", return_value=self._mock_response(200, body)):
            windows = self.skip.get_skip_windows(52991, 1, episode_length_s=1560)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0].kind, "op")
        self.assertEqual(windows[0].start_s, 3.2)
        self.assertEqual(windows[0].end_s, 93.2)
        self.assertEqual(windows[1].kind, "ed")

    def test_404_returns_empty_not_an_error(self):
        body = {"found": False, "results": [], "message": "No skip times found"}
        with patch("requests.get", return_value=self._mock_response(404, body)):
            windows = self.skip.get_skip_windows(999999999, 1)
        self.assertEqual(windows, [])

    def test_found_false_returns_empty(self):
        body = {"found": False, "results": []}
        with patch("requests.get", return_value=self._mock_response(200, body)):
            windows = self.skip.get_skip_windows(52991, 1)
        self.assertEqual(windows, [])

    def test_network_error_returns_empty_not_raise(self):
        import requests
        with patch("requests.get", side_effect=requests.RequestException("timeout")):
            windows = self.skip.get_skip_windows(52991, 1)
        self.assertEqual(windows, [])

    def test_malformed_json_returns_empty(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("bad json")
        with patch("requests.get", return_value=resp):
            windows = self.skip.get_skip_windows(52991, 1)
        self.assertEqual(windows, [])

    def test_unknown_skip_type_is_ignored(self):
        body = {
            "found": True,
            "results": [
                {"interval": {"startTime": 0, "endTime": 10}, "skipType": "mixed-op"},
                {"interval": {"startTime": 20, "endTime": 30}, "skipType": "op"},
            ],
        }
        with patch("requests.get", return_value=self._mock_response(200, body)):
            windows = self.skip.get_skip_windows(52991, 1)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].kind, "op")

    def test_zero_length_interval_is_dropped(self):
        body = {
            "found": True,
            "results": [{"interval": {"startTime": 10, "endTime": 10}, "skipType": "op"}],
        }
        with patch("requests.get", return_value=self._mock_response(200, body)):
            windows = self.skip.get_skip_windows(52991, 1)
        self.assertEqual(windows, [])

    def test_episode_length_is_passed_as_query_param_when_positive(self):
        body = {"found": True, "results": []}
        with patch("requests.get", return_value=self._mock_response(200, body)) as mocked:
            self.skip.get_skip_windows(52991, 1, episode_length_s=1560)
        _, kwargs = mocked.call_args
        self.assertIn(("episodeLength", 1560), kwargs["params"])

    def test_episode_length_omitted_when_zero(self):
        body = {"found": True, "results": []}
        with patch("requests.get", return_value=self._mock_response(200, body)) as mocked:
            self.skip.get_skip_windows(52991, 1, episode_length_s=0)
        _, kwargs = mocked.call_args
        self.assertNotIn("episodeLength", dict(kwargs["params"]))

    def test_result_is_cached_across_calls(self):
        body = {
            "found": True,
            "results": [{"interval": {"startTime": 0, "endTime": 90}, "skipType": "op"}],
        }
        with patch("requests.get", return_value=self._mock_response(200, body)) as mocked:
            self.skip.get_skip_windows(52991, 1, episode_length_s=1560)
            self.skip.get_skip_windows(52991, 1, episode_length_s=1560)
        mocked.assert_called_once()

    def test_empty_result_is_not_cached(self):
        """A genuine 'no data yet' shouldn't be pinned for the whole TTL —
        the episode might get skip times submitted later."""
        body = {"found": False, "results": []}
        with patch("requests.get", return_value=self._mock_response(404, body)) as mocked:
            self.skip.get_skip_windows(52991, 999)
            self.skip.get_skip_windows(52991, 999)
        self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
