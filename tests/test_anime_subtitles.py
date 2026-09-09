"""Tests for actions.anime.subtitles — SRT->WebVTT conversion."""
import unittest
from unittest.mock import patch

from actions.anime.subtitles import srt_to_vtt, get_vtt_by_kitsu


class SrtToVttTests(unittest.TestCase):
    def test_adds_webvtt_header(self):
        vtt = srt_to_vtt("1\n00:00:01,000 --> 00:00:02,000\nHola\n")
        self.assertTrue(vtt.startswith("WEBVTT\n\n"))

    def test_converts_comma_to_dot_in_timestamps(self):
        vtt = srt_to_vtt("1\n00:00:01,000 --> 00:00:02,500\nHola\n")
        self.assertIn("00:00:01.000 --> 00:00:02.500", vtt)
        self.assertNotIn(",", vtt)

    def test_preserves_cue_text_and_index(self):
        srt = "1\n00:00:01,000 --> 00:00:02,000\nLínea uno\nLínea dos\n"
        vtt = srt_to_vtt(srt)
        self.assertIn("1\n", vtt)
        self.assertIn("Línea uno", vtt)
        self.assertIn("Línea dos", vtt)

    def test_handles_multiple_cues(self):
        srt = (
            "1\n00:00:01,000 --> 00:00:02,000\nUno\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nDos\n"
        )
        vtt = srt_to_vtt(srt)
        self.assertIn("00:00:01.000 --> 00:00:02.000", vtt)
        self.assertIn("00:00:03.000 --> 00:00:04.000", vtt)
        self.assertEqual(vtt.count("-->"), 2)

    def test_normalizes_crlf_line_endings(self):
        srt = "1\r\n00:00:01,000 --> 00:00:02,000\r\nHola\r\n"
        vtt = srt_to_vtt(srt)
        self.assertNotIn("\r", vtt)

    def test_strips_trailing_whitespace_but_keeps_single_trailing_newline(self):
        vtt = srt_to_vtt("1\n00:00:01,000 --> 00:00:02,000\nHola\n\n\n\n")
        self.assertTrue(vtt.endswith("Hola\n"))
        self.assertFalse(vtt.endswith("Hola\n\n"))


class GetVttByKitsuTests(unittest.TestCase):
    def test_downloads_and_converts(self):
        with patch("actions.stremio_subs.fetch_subtitle_by_kitsu") as mocked_fetch, \
             patch("builtins.open", create=True) as mocked_open:
            mocked_fetch.return_value = "/tmp/sub.srt"
            mocked_open.return_value.__enter__.return_value.read.return_value = (
                "1\n00:00:01,000 --> 00:00:02,000\nHola\n"
            )
            vtt = get_vtt_by_kitsu("46474", episode=1, language="es")
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:00:01.000", vtt)
        mocked_fetch.assert_called_once_with("46474", episode=1, language="es")

    def test_propagates_stremio_subs_error(self):
        from actions.stremio_subs import StremioSubsError
        with patch("actions.stremio_subs.fetch_subtitle_by_kitsu",
                   side_effect=StremioSubsError("no subs")):
            with self.assertRaises(StremioSubsError):
                get_vtt_by_kitsu("999999", episode=1)


if __name__ == "__main__":
    unittest.main()
