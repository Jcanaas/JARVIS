"""The loopback bridge that makes YouTube audio playable by mpv again.

YouTube answers 403 to the open-ended `Range: bytes=0-` ffmpeg opens every
stream with, and to any bounded range over ~1 MiB. The proxy accepts the range
mpv insists on and fulfils it upstream in chunks the CDN still serves.
"""
import threading
import unittest
import urllib.request
from unittest.mock import patch

from actions import stream_proxy


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 206):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def iter_content(self, size):
        for i in range(0, len(self._payload), size):
            yield self._payload[i:i + size]

    def close(self):
        pass


class _FakeCDN:
    """Serves a byte buffer, refusing ranges the way YouTube now does."""

    MAX_RANGE = stream_proxy.CHUNK_BYTES

    def __init__(self, body: bytes):
        self.body = body
        self.requests: list[str] = []
        self.refused = 0

    def get(self, url, headers=None, stream=False, timeout=None):
        rng = (headers or {}).get("Range", "")
        self.requests.append(rng)
        if not rng.startswith("bytes="):
            self.refused += 1
            return _FakeResponse(b"", status=403)
        first, _, last = rng[6:].partition("-")
        start = int(first)
        end = int(last) if last else len(self.body) - 1
        if end - start + 1 > self.MAX_RANGE:
            self.refused += 1
            return _FakeResponse(b"", status=403)
        resp = _FakeResponse(self.body[start:end + 1])
        resp.headers = {"Content-Range": f"bytes {start}-{end}/{len(self.body)}"}
        return resp


class StreamProxyTests(unittest.TestCase):
    def tearDown(self):
        stream_proxy.shutdown()

    def _fetch(self, url: str, byte_range: str | None) -> tuple[int, bytes, dict]:
        request = urllib.request.Request(url)
        if byte_range:
            request.add_header("Range", byte_range)
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)

    def test_open_ended_range_is_served_in_chunks_the_cdn_accepts(self):
        body = bytes(range(256)) * 12_000          # ~3 MB, like a real m4a
        cdn = _FakeCDN(body)

        with patch.object(stream_proxy.requests, "get", cdn.get):
            url = stream_proxy.serve("https://cdn.example/video", size=len(body))
            status, payload, headers = self._fetch(url, "bytes=0-")

        self.assertEqual(status, 206)
        self.assertEqual(payload, body)
        self.assertEqual(cdn.refused, 0)
        self.assertEqual(headers["Content-Range"], f"bytes 0-{len(body) - 1}/{len(body)}")
        self.assertGreater(len(cdn.requests), 1)   # actually chunked
        for rng in cdn.requests:
            first, _, last = rng[6:].partition("-")
            self.assertLessEqual(int(last) - int(first) + 1, stream_proxy.CHUNK_BYTES)

    def test_seek_serves_the_requested_byte_range(self):
        body = bytes(range(256)) * 8_000
        cdn = _FakeCDN(body)

        with patch.object(stream_proxy.requests, "get", cdn.get):
            url = stream_proxy.serve("https://cdn.example/video", size=len(body))
            status, payload, headers = self._fetch(url, "bytes=1000000-1000999")

        self.assertEqual(status, 206)
        self.assertEqual(payload, body[1000000:1001000])
        self.assertEqual(
            headers["Content-Range"], f"bytes 1000000-1000999/{len(body)}"
        )

    def test_expired_upstream_url_is_re_resolved_mid_track(self):
        body = b"x" * 4096
        fresh = _FakeCDN(body)
        calls = {"n": 0}

        def flaky_get(url, headers=None, stream=False, timeout=None):
            if url == "https://cdn.example/stale":
                return _FakeResponse(b"", status=403)
            return fresh.get(url, headers=headers, stream=stream, timeout=timeout)

        def resolver():
            calls["n"] += 1
            return "https://cdn.example/fresh", 0

        with patch.object(stream_proxy.requests, "get", flaky_get):
            url = stream_proxy.serve(
                "https://cdn.example/stale", size=len(body), resolver=resolver
            )
            status, payload, _ = self._fetch(url, "bytes=0-")

        self.assertEqual(status, 206)
        self.assertEqual(payload, body)
        self.assertEqual(calls["n"], 1)

    def test_unknown_token_is_rejected(self):
        with patch.object(stream_proxy.requests, "get", _FakeCDN(b"abc").get):
            url = stream_proxy.serve("https://cdn.example/video", size=3)
        bogus = url.rsplit("/", 1)[0] + "/nope"
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self._fetch(bogus, None)
        self.assertEqual(caught.exception.code, 404)

    def test_it_only_listens_on_loopback(self):
        with patch.object(stream_proxy.requests, "get", _FakeCDN(b"abc").get):
            url = stream_proxy.serve("https://cdn.example/video", size=3)
        self.assertTrue(url.startswith("http://127.0.0.1:"))

    def test_size_falls_back_to_the_clen_query_parameter(self):
        body = b"y" * 2048
        cdn = _FakeCDN(body)
        with patch.object(stream_proxy.requests, "get", cdn.get):
            url = stream_proxy.serve(f"https://cdn.example/v?clen={len(body)}&itag=140")
            status, payload, _ = self._fetch(url, "bytes=0-")
        self.assertEqual(status, 206)
        self.assertEqual(payload, body)

    def test_old_entries_are_evicted(self):
        with patch.object(stream_proxy.requests, "get", _FakeCDN(b"abc").get):
            urls = [
                stream_proxy.serve(f"https://cdn.example/{i}", size=3)
                for i in range(stream_proxy._MAX_ENTRIES + 3)
            ]
        self.assertEqual(len(stream_proxy._entries), stream_proxy._MAX_ENTRIES)
        self.assertNotIn(urls[0].rsplit("/", 1)[1], stream_proxy._entries)


class RangeParsingTests(unittest.TestCase):
    def test_ranges(self):
        cases = {
            "bytes=0-": (0, 99),
            "bytes=10-20": (10, 20),
            "bytes=-15": (85, 99),
            "bytes=50-999": (50, 99),   # clamped to the real size
            "": (0, 99),
            "garbage": (0, 99),
        }
        for header, expected in cases.items():
            with self.subTest(header=header):
                self.assertEqual(stream_proxy._parse_range(header, 100), expected)


class ProxyThreadingTests(unittest.TestCase):
    def tearDown(self):
        stream_proxy.shutdown()

    def test_the_server_starts_once_for_many_tracks(self):
        with patch.object(stream_proxy.requests, "get", _FakeCDN(b"abc").get):
            ports = set()
            errors = []

            def register(i):
                try:
                    ports.add(stream_proxy.serve(f"https://cdn.example/{i}", size=3).split(":")[2].split("/")[0])
                except Exception as exc:  # pragma: no cover - surfaced by assert
                    errors.append(exc)

            threads = [threading.Thread(target=register, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(ports), 1)


if __name__ == "__main__":
    unittest.main()
