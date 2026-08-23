import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from actions import rom_catalog as rc


class _JsonResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DownloadResponse:
    headers = {"Content-Length": "7"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        yield b"archive"


def _split_console() -> rc.Console:
    return rc.Console(
        id="test",
        name="Test console",
        short="Test",
        archive_item="",
        archive_items=("part-one", "part-two"),
        archive_exts=(".7z",),
        thumb_repo="Test",
        rom_exts=(".iso", ".bin"),
        emulator="test",
    )


class SplitArchiveIndexTests(unittest.TestCase):
    def test_ps2_uses_individual_7z_files_from_all_archive_items(self):
        console = rc.CONSOLES["ps2"]

        self.assertTrue(console.has_catalog)
        self.assertEqual(console.archive_exts, (".7z", ".zip"))
        self.assertIn("ps2-redump-usa-chd-part-F", console.archive_items)
        self.assertIn("ps2-redump-usa-chd-part-M", console.archive_items)
        self.assertIn("rr-sony-playstation-2-e2", console.archive_items)
        self.assertIn(
            "metal-gear-solid-3-snake-eater-italy-ps2",
            console.archive_items,
        )
        self.assertIn(
            "metal-gear-solid-3-subsistence_202210",
            console.archive_items,
        )
        self.assertIn(
            "metal-gear-solid-3-snake-eater-usa_202603",
            console.archive_items,
        )
        self.assertIn("madagascar_20260114", console.archive_items)
        self.assertEqual(console.thumb_repo, "Sony_-_PlayStation_2")

    def test_fetch_index_aggregates_items_and_keeps_each_source(self):
        payloads = {
            "part-one": {"files": [
                {"name": "Alpha (USA).7z", "size": "123"},
                {"name": "Hidden (USA).7z", "size": "999", "private": "true"},
                {"name": "part-one_archive.torrent", "size": "1"},
            ]},
            "part-two": {"files": [
                {"name": "Beta (USA) (En,Es).7z", "size": "456"},
            ]},
        }

        def fake_get(url, **_kwargs):
            item = url.rsplit("/", 1)[-1]
            return _JsonResponse(payloads[item])

        with patch.object(rc.requests, "get", side_effect=fake_get):
            rows = rc._fetch_index(_split_console())

        self.assertEqual([row["stem"] for row in rows], [
            "Alpha (USA)", "Beta (USA) (En,Es)", "Hidden (USA)",
        ])
        self.assertEqual([row["archive_item"] for row in rows], [
            "part-one", "part-two", "part-one",
        ])
        self.assertTrue(rows[0]["available"])
        self.assertFalse(rows[2]["available"])

    def test_fetch_index_supports_an_item_specific_nested_prefix(self):
        console = _split_console()
        console.archive_items = ("europe-part",)
        console.archive_prefixes = (("europe-part", "europe/iso"),)

        payload = {"files": [{
            "name": "europe/iso/Game (Europe) (En,Es).7z",
            "size": "456",
            "private": "true",
        }]}
        with patch.object(rc.requests, "get", return_value=_JsonResponse(payload)):
            rows = rc._fetch_index(console)

        self.assertEqual(rows[0]["name"], "Game (Europe) (En,Es).7z")
        self.assertEqual(rows[0]["path"], "europe/iso/Game (Europe) (En,Es).7z")
        self.assertFalse(rows[0]["available"])

    def test_fetch_index_prefers_direct_image_over_duplicate_container(self):
        console = _split_console()
        console.rom_exts = (".iso", ".bin", ".chd")
        payloads = iter((
            _JsonResponse({"files": [{"name": "Game (USA).7z"}]}),
            _JsonResponse({"files": [{"name": "Game (USA).chd"}]}),
        ))

        with patch.object(rc.requests, "get", side_effect=lambda *_a, **_k: next(payloads)):
            rows = rc._fetch_index(console)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Game (USA).chd")

    def test_cache_is_invalidated_when_catalog_sources_change(self):
        original = _split_console()
        changed = _split_console()
        changed.archive_items = changed.archive_items + ("part-three",)
        rows = [{"name": "Game.7z", "stem": "Game", "archive_item": "part-one"}]

        with tempfile.TemporaryDirectory() as temp_name:
            index_file = Path(temp_name) / "index.json"
            with (
                patch.dict(rc.CONSOLES, {"test": original}),
                patch.object(rc, "_index_file", return_value=index_file),
            ):
                rc._store_cached("test", rows)
                self.assertEqual(rc._load_cached("test"), rows)
                rc.CONSOLES["test"] = changed
                self.assertIsNone(rc._load_cached("test"))

    def test_fetch_index_keeps_partial_results_when_one_item_is_unavailable(self):
        def fake_get(url, **_kwargs):
            if url.endswith("part-one"):
                raise requests.RequestException("offline")
            return _JsonResponse({"files": [{"name": "Beta (USA).7z"}]})

        with patch.object(rc.requests, "get", side_effect=fake_get):
            rows = rc._fetch_index(_split_console())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["archive_item"], "part-two")

    def test_rom_download_url_uses_the_row_source_item(self):
        rom = rc._to_rom(_split_console(), {
            "name": "Beta Game (USA).7z",
            "stem": "Beta Game (USA)",
            "archive_item": "part-two",
            "size": 456,
        })

        self.assertEqual(rom.filename, "Beta Game (USA).7z")
        self.assertIn("/part-two/", rom.download_url)
        self.assertTrue(rom.download_url.endswith("Beta%20Game%20%28USA%29.7z"))

    def test_standalone_madagascar_dump_gets_a_human_title(self):
        rom = rc._to_rom(rc.CONSOLES["ps2"], {
            "name": "SLUS_210.15.Madagascar.iso",
            "stem": "SLUS_210.15.Madagascar",
            "archive_item": "madagascar_20260114",
        })

        self.assertEqual(rom.title, "DreamWorks Madagascar")
        self.assertEqual(rom.region, "USA")

    def test_nested_public_subsistence_discs_get_redump_version_names(self):
        rom = rc._to_rom(rc.CONSOLES["ps2"], {
            "name": "MGS3Subsistance1.iso",
            "stem": "MGS3Subsistance1",
            "path": "DiscImageCreator/MGS3Subsistance1.iso",
            "archive_item": "metal-gear-solid-3-subsistence_202210",
            "available": True,
        })

        self.assertEqual(rom.title, "Metal Gear Solid 3 - Subsistence")
        self.assertEqual(rom.region, "Europa")
        self.assertEqual(rom.languages, ["En", "Fr"])
        self.assertIn("Disc 1", rom.edition)
        self.assertTrue(rom.available)

    def test_search_groups_versions_and_spanish_filter_includes_es_language(self):
        usa_spanish = rc.Rom(
            title="Example Game", console_id="test", stem="Example Game (USA) (En,Es)",
            filename="usa.7z", region="USA", languages=["En", "Es"], available=True,
        )
        europe_locked = rc.Rom(
            title="Example Game", console_id="test", stem="Example Game (Europe)",
            filename="eu.7z", region="Europa", available=False,
            unavailable_reason="Fuente restringida",
        )

        with patch.object(rc, "get_index", return_value=[europe_locked, usa_spanish]):
            results = rc.search("example game", "test")
            spanish = rc.search("", "test", "España")
            versions = rc.versions_for(usa_spanish)

        self.assertEqual(results, [usa_spanish])
        self.assertEqual(spanish, [usa_spanish])
        self.assertEqual(versions, [usa_spanish, europe_locked])
        self.assertIn("Español", rc.version_label(usa_spanish))
        self.assertIn("no disponible", rc.version_label(europe_locked))

    def test_search_matches_normalized_title_when_archive_filename_is_opaque(self):
        public = rc.Rom(
            title="Metal Gear Solid 3 - Subsistence", console_id="ps2",
            stem="MGS3Subsistance1", filename="MGS3Subsistance1.iso",
            region="Europa", available=True,
        )
        locked = rc.Rom(
            title=public.title, console_id="ps2",
            stem="Metal Gear Solid 3 - Subsistence (USA)", filename="locked.chd",
            region="USA", available=False,
        )

        with patch.object(rc, "get_index", return_value=[locked, public]):
            results = rc.search("metal gear solid 3", "ps2")

        self.assertEqual(results, [public])


class SevenZipDownloadTests(unittest.TestCase):
    def test_download_rejects_a_restricted_variant_before_network_access(self):
        rom = rc.Rom(
            title="Locked Game", console_id="test", filename="locked.7z",
            stem="Locked Game (Europe)", available=False,
            unavailable_reason="El archivo está restringido",
        )

        with patch.object(rc.requests, "get") as get:
            with self.assertRaisesRegex(rc.RomCatalogError, "restringido"):
                rc.download(rom)

        get.assert_not_called()

    def test_download_extracts_disc_image_and_removes_container(self):
        rom = rc.Rom(
            title="Beta Game",
            console_id="test",
            filename="Beta Game (USA).7z",
            stem="Beta Game (USA)",
            size_bytes=7,
            download_url="https://example.invalid/game.7z",
        )
        console = _split_console()

        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)

            def fake_run(args, **_kwargs):
                output = Path(next(arg[2:] for arg in args if arg.startswith("-o")))
                (output / "original-disc.iso").write_bytes(b"disc image")
                return subprocess.CompletedProcess(args, 0, "Everything is Ok", "")

            with (
                patch.dict(rc.CONSOLES, {"test": console}),
                patch.object(rc, "roms_dir", return_value=folder),
                patch.object(rc, "_seven_zip_executable", return_value="7zr"),
                patch.object(rc.requests, "get", return_value=_DownloadResponse()),
                patch.object(rc.subprocess, "run", side_effect=fake_run),
            ):
                result = rc.download(rom)

            self.assertEqual(result, folder / "Beta Game (USA).iso")
            self.assertEqual(result.read_bytes(), b"disc image")
            self.assertFalse((folder / rom.filename).exists())


if __name__ == "__main__":
    unittest.main()
