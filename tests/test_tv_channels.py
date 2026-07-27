import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from actions.tv_channels import (
    Channel, _merge_overrides, _normalize_name, _parse_m3u, channel_groups,
)


SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="La1.es" tvg-logo="https://example.com/la1.png" group-title="General",La 1
https://ztnr.rtve.es/ztnr/1688877.m3u8
#EXTINF:-1 tvg-id="Antena3.es" tvg-logo="" group-title="General;Entertainment",Antena 3
#EXTVLCOPT:http-user-agent=Mozilla/5.0
#EXTVLCOPT:http-referrer=https://www.atresplayer.com/
https://example.com/antena3.m3u8
#EXTINF:-1,Sin atributos
https://example.com/plain.m3u8
"""


class ParseM3UTests(unittest.TestCase):
    def test_parses_basic_channel(self):
        chans = _parse_m3u(SAMPLE)
        self.assertEqual(len(chans), 3)
        la1 = chans[0]
        self.assertEqual(la1.name, "La 1")
        self.assertEqual(la1.tvg_id, "La1.es")
        self.assertEqual(la1.logo, "https://example.com/la1.png")
        self.assertEqual(la1.group, "General")
        self.assertEqual(la1.url, "https://ztnr.rtve.es/ztnr/1688877.m3u8")
        self.assertEqual(la1.vlc_opts, [])

    def test_extvlcopt_lines_become_media_options(self):
        a3 = _parse_m3u(SAMPLE)[1]
        self.assertEqual(a3.vlc_opts, [
            ":http-user-agent=Mozilla/5.0",
            ":http-referrer=https://www.atresplayer.com/",
        ])
        self.assertEqual(a3.url, "https://example.com/antena3.m3u8")

    def test_channel_without_attributes(self):
        plain = _parse_m3u(SAMPLE)[2]
        self.assertEqual(plain.name, "Sin atributos")
        self.assertEqual(plain.url, "https://example.com/plain.m3u8")

    def test_groups_split_on_semicolon(self):
        groups = channel_groups(_parse_m3u(SAMPLE))
        self.assertEqual(groups, ["Entertainment", "General"])

    def test_orphan_url_ignored(self):
        chans = _parse_m3u("https://example.com/orphan.m3u8\n")
        self.assertEqual(chans, [])


class MergeOverridesTests(unittest.TestCase):
    def test_normalize_strips_suffixes(self):
        self.assertEqual(_normalize_name("La 1 (720p) [Geo-blocked]"), "la 1")
        self.assertEqual(_normalize_name("  La   2  "), "la 2")

    def test_override_replaces_url_keeps_logo(self):
        base = [Channel(name="La 1 (720p)", url="https://drm/la1.m3u8",
                        logo="https://logo/la1.png", group="General")]
        extra = [Channel(name="La 1", url="https://clear/la1.m3u8",
                         vlc_opts=[":http-referrer=x"])]
        merged = _merge_overrides(base, extra)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].url, "https://clear/la1.m3u8")
        self.assertEqual(merged[0].vlc_opts, [":http-referrer=x"])
        self.assertEqual(merged[0].logo, "https://logo/la1.png")

    def test_unmatched_extra_appended(self):
        merged = _merge_overrides(
            [Channel(name="La 1", url="u1")],
            [Channel(name="Canal Nuevo", url="u2")])
        self.assertEqual([c.name for c in merged], ["La 1", "Canal Nuevo"])

    def test_templated_and_duplicate_extras_dropped(self):
        merged = _merge_overrides(
            [Channel(name="La 1", url="drm")],
            [Channel(name="La 1", url="https://ads/x.m3u8?did=[DEVICE_ID]"),
             Channel(name="La 1", url="clear"),
             Channel(name="La 1", url="clear-dup")])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].url, "clear")


if __name__ == "__main__":
    unittest.main()
