import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from actions import audio_tags, ytmusic


class FormatSelectorTests(unittest.TestCase):
    def test_every_quality_asks_for_m4a_before_the_raw_best_stream(self):
        for quality in ("", "best", "low", "medium", "high"):
            selector = ytmusic._audio_format_selector(quality)
            first = selector.split("/")[0]
            self.assertIn("ext=m4a", first, f"quality={quality!r} -> {selector}")

    def test_a_fallback_remains_so_tracks_without_m4a_still_download(self):
        self.assertTrue(ytmusic._audio_format_selector("best").endswith("bestaudio/best"))


class BuildMetadataTests(unittest.TestCase):
    def test_track_dict_wins_over_ytdlp_info(self):
        meta = audio_tags.build_metadata(
            {"title": "Song", "artists": "Artist", "album": "Album", "thumbnail": "http://cover"},
            {"title": "Song (Official Video)", "artist": "Someone Else", "album": "Other"},
            lookup=False,
        )
        self.assertEqual(meta["title"], "Song")
        self.assertEqual(meta["artists"], "Artist")
        self.assertEqual(meta["album"], "Album")
        self.assertEqual(meta["cover_url"], "http://cover")

    def test_the_artist_prefix_is_dropped_from_video_style_titles(self):
        meta = audio_tags.build_metadata(
            {"title": "The Beatles - Blackbird", "artists": "The Beatles"}, {}, lookup=False
        )
        self.assertEqual(meta["title"], "Blackbird")

    def test_stripping_the_prefix_never_empties_the_title(self):
        meta = audio_tags.build_metadata({"title": "Muse -", "artists": "Muse"}, {}, lookup=False)
        self.assertEqual(meta["title"], "Muse -")
        meta = audio_tags.build_metadata({"title": "Muse", "artists": "Muse"}, {}, lookup=False)
        self.assertEqual(meta["title"], "Muse")

    def test_the_comment_holds_the_track_url_only_when_there_is_an_id(self):
        with_id = audio_tags.build_metadata({"title": "S", "videoId": "dQw4w9WgXcQ"}, {}, lookup=False)
        self.assertEqual(with_id["comment"], "https://music.youtube.com/watch?v=dQw4w9WgXcQ")
        without = audio_tags.build_metadata({"title": "S"}, {}, lookup=False)
        self.assertEqual(without["comment"], "")

    def test_topic_suffix_is_stripped_from_auto_generated_channels(self):
        meta = audio_tags.build_metadata({}, {"title": "Song", "uploader": "Artist - Topic"}, lookup=False)
        self.assertEqual(meta["artists"], "Artist")

    def test_itunes_only_fills_fields_youtube_left_empty(self):
        lookup = {
            "genre": "Rock",
            "album": "iTunes Album",
            "year": "1999",
            "track_number": 3,
            "track_total": 12,
            "disc_number": 1,
            "cover": "http://itunes/cover.jpg",
        }
        with patch.object(audio_tags, "lookup_itunes", return_value=lookup):
            meta = audio_tags.build_metadata({"title": "Song", "artists": "Artist", "album": "Real Album"}, {})
        self.assertEqual(meta["album"], "Real Album")
        self.assertEqual(meta["genre"], "Rock")
        self.assertEqual(meta["track_number"], 3)
        self.assertEqual(meta["cover_url"], "http://itunes/cover.jpg")

    def test_lookup_is_skipped_when_nothing_is_missing(self):
        with patch.object(audio_tags, "lookup_itunes") as lookup:
            audio_tags.build_metadata(
                {"title": "Song", "artists": "Artist", "album": "Album", "thumbnail": "http://cover"},
                {"genre": "Pop"},
            )
        lookup.assert_not_called()


class CoverArtTests(unittest.TestCase):
    def test_jpeg_and_png_pass_through_untouched(self):
        jpeg = b"\xff\xd8\xff" + b"payload"
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        self.assertEqual(audio_tags._as_jpeg_or_png(jpeg, ""), (jpeg, "image/jpeg"))
        self.assertEqual(audio_tags._as_jpeg_or_png(png, ""), (png, "image/png"))

    def test_webp_is_transcoded_because_mp4_and_id3_cannot_read_it(self):
        try:
            from PIL import Image
        except Exception:
            self.skipTest("Pillow not installed")
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="WEBP")

        data, mime = audio_tags._as_jpeg_or_png(buf.getvalue(), "image/webp")

        self.assertEqual(mime, "image/jpeg")
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))


class NormalizeContainerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_portable_files_are_left_alone(self):
        path = self.root / "song.m4a"
        path.write_bytes(b"audio")
        with patch.object(audio_tags, "transcoder", return_value=("ffmpeg", "ffmpeg")) as exe:
            self.assertEqual(audio_tags.normalize_container(path), str(path))
        exe.assert_not_called()

    def test_without_any_encoder_the_original_file_survives(self):
        path = self.root / "song.opus"
        path.write_bytes(b"audio")
        with patch.object(audio_tags, "transcoder", return_value=("", "")):
            self.assertEqual(audio_tags.normalize_container(path), str(path))
        self.assertTrue(path.exists())

    def test_the_bundled_mpv_is_used_when_ffmpeg_is_absent(self):
        with (
            patch.object(audio_tags, "ffmpeg_path", return_value=""),
            patch.object(audio_tags, "mpv_path", return_value=r"C:\app\mpv.exe"),
        ):
            self.assertEqual(audio_tags.transcoder(), ("mpv", r"C:\app\mpv.exe"))

    def test_ffmpeg_wins_when_both_are_present(self):
        with (
            patch.object(audio_tags, "ffmpeg_path", return_value=r"C:\app\ffmpeg.exe"),
            patch.object(audio_tags, "mpv_path", return_value=r"C:\app\mpv.exe"),
        ):
            self.assertEqual(audio_tags.transcoder()[0], "ffmpeg")

    def test_both_encoders_write_to_an_m4a_scratch_file(self):
        src = self.root / "song.webm"
        dst = self.root / "song.converting.m4a"
        for kind in ("ffmpeg", "mpv"):
            cmd = audio_tags._transcode_command(kind, "enc", src, dst, "192k")
            self.assertTrue(
                any(str(dst) in part for part in cmd),
                f"{kind} command never names the output: {cmd}",
            )
            self.assertTrue(any("aac" in part for part in cmd), cmd)

    def test_mpv_encodes_without_touching_the_users_playback_profile(self):
        cmd = audio_tags._transcode_command("mpv", "mpv", self.root / "a.webm", self.root / "b.m4a", "192k")
        self.assertIn("--no-config", cmd)
        self.assertIn("--vid=no", cmd)

    def test_successful_conversion_replaces_the_source(self):
        path = self.root / "song.webm"
        path.write_bytes(b"audio")

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"converted")
            return type("P", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

        with (
            patch.object(audio_tags, "transcoder", return_value=("ffmpeg", "ffmpeg")),
            patch.object(audio_tags.subprocess, "run", side_effect=fake_run),
        ):
            result = audio_tags.normalize_container(path)

        self.assertEqual(result, str(self.root / "song.m4a"))
        self.assertFalse(path.exists())
        self.assertEqual((self.root / "song.m4a").read_bytes(), b"converted")

    def test_a_failed_conversion_keeps_the_original_and_drops_the_partial(self):
        path = self.root / "song.webm"
        path.write_bytes(b"audio")

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"")
            return type("P", (), {"returncode": 1, "stdout": b"", "stderr": b"boom"})()

        with (
            patch.object(audio_tags, "transcoder", return_value=("ffmpeg", "ffmpeg")),
            patch.object(audio_tags.subprocess, "run", side_effect=fake_run),
        ):
            result = audio_tags.normalize_container(path)

        self.assertEqual(result, str(path))
        self.assertTrue(path.exists())
        self.assertFalse((self.root / "song.converting.m4a").exists())


class TagDispatchTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_each_container_reaches_its_own_tagger(self):
        cases = {".m4a": "_tag_mp4", ".mp3": "_tag_mp3", ".flac": "_tag_flac", ".opus": "_tag_vorbis"}
        for ext, func in cases.items():
            path = self.root / f"song{ext}"
            path.write_bytes(b"audio")
            with patch.object(audio_tags, func, return_value=True) as tagger:
                self.assertTrue(audio_tags.tag_audio_file(path, {"title": "Song"}))
            tagger.assert_called_once()

    def test_webm_is_refused_because_mutagen_cannot_open_a_matroska_container(self):
        path = self.root / "song.webm"
        path.write_bytes(b"audio")
        with patch.object(audio_tags, "_tag_vorbis") as tagger:
            self.assertFalse(audio_tags.tag_audio_file(path, {"title": "Song"}))
        tagger.assert_not_called()

    def test_cover_art_is_downloaded_once_from_the_metadata_url(self):
        path = self.root / "song.m4a"
        path.write_bytes(b"audio")
        with (
            patch.object(audio_tags, "fetch_cover", return_value=(b"jpegbytes", "image/jpeg")) as fetch,
            patch.object(audio_tags, "_tag_mp4", return_value=True) as tagger,
        ):
            audio_tags.tag_audio_file(path, {"title": "Song", "cover_url": "http://cover"})
        fetch.assert_called_once_with("http://cover")
        self.assertEqual(tagger.call_args.args[2], b"jpegbytes")

    def test_a_broken_tagger_never_fails_the_download(self):
        path = self.root / "song.m4a"
        path.write_bytes(b"audio")
        with patch.object(audio_tags, "_tag_mp4", side_effect=OSError("locked")):
            self.assertFalse(audio_tags.tag_audio_file(path, {"title": "Song"}))


class RetagTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_metadata_is_recovered_from_the_download_filename(self):
        path = self.root / "007 - Artist - Song [dQw4w9WgXcQ].opus"
        path.write_bytes(b"audio")
        with patch.object(audio_tags, "finalize_file", return_value={"path": str(path), "converted": False, "tagged": True}) as finalize:
            audio_tags.retag_file(path, lookup=False)
        track = finalize.call_args.args[1]
        self.assertEqual(track, {"title": "Song", "artists": "Artist", "videoId": "dQw4w9WgXcQ"})

    def test_the_video_id_in_the_name_is_used_to_ask_youtube_for_the_artist(self):
        path = self.root / "001 - WAN-TUN CLAN [W2PqNHJ-UGM].webm"
        path.write_bytes(b"audio")
        youtube = {"title": "WAN-TUN CLAN", "artists": "Blasskill", "thumbnail": "http://cover", "videoId": "W2PqNHJ-UGM"}
        with (
            patch.object(audio_tags, "lookup_youtube", return_value=youtube) as lookup,
            patch.object(audio_tags, "finalize_file", return_value={"path": str(path), "converted": False, "tagged": True}) as finalize,
        ):
            audio_tags.retag_file(path)
        lookup.assert_called_once_with("W2PqNHJ-UGM")
        track = finalize.call_args.args[1]
        self.assertEqual(track["artists"], "Blasskill")
        self.assertEqual(track["thumbnail"], "http://cover")

    def test_youtube_is_not_queried_when_lookups_are_disabled(self):
        path = self.root / "001 - Song [dQw4w9WgXcQ].m4a"
        path.write_bytes(b"audio")
        with (
            patch.object(audio_tags, "lookup_youtube") as lookup,
            patch.object(audio_tags, "finalize_file", return_value={"path": str(path), "converted": False, "tagged": True}),
        ):
            audio_tags.retag_file(path, lookup=False)
        lookup.assert_not_called()

    def test_a_title_only_filename_still_yields_a_title(self):
        path = self.root / "012 - Song [dQw4w9WgXcQ].m4a"
        path.write_bytes(b"audio")
        with patch.object(audio_tags, "finalize_file", return_value={"path": str(path), "converted": False, "tagged": True}) as finalize:
            audio_tags.retag_file(path, lookup=False)
        self.assertEqual(
            finalize.call_args.args[1],
            {"title": "Song", "artists": "", "videoId": "dQw4w9WgXcQ"},
        )

    def test_retag_directory_counts_real_writes_not_attempts(self):
        (self.root / "001 - A - One [aaaaaaaaaaa].opus").write_bytes(b"audio")
        (self.root / "002 - A - Two [bbbbbbbbbbb].m4a").write_bytes(b"audio")
        (self.root / "cover.jpg").write_bytes(b"image")

        def fake_finalize(path, track=None, info=None, **kwargs):
            out = Path(path).with_suffix(".m4a")
            return {"path": str(out), "converted": Path(path).suffix != ".m4a", "tagged": True}

        with patch.object(audio_tags, "finalize_file", side_effect=fake_finalize):
            result = audio_tags.retag_directory(self.root, lookup=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["tagged"], 2)
        self.assertEqual(result["converted"], 1)
        self.assertEqual(result["untaggable"], 0)

    def test_webm_that_could_not_be_converted_is_reported_as_untaggable(self):
        (self.root / "001 - A - One [aaaaaaaaaaa].webm").write_bytes(b"audio")

        with patch.object(audio_tags, "transcoder", return_value=("", "")):
            result = audio_tags.retag_directory(self.root, lookup=False)

        self.assertEqual(result["files"], 1)
        self.assertEqual(result["tagged"], 0)
        self.assertEqual(result["converted"], 0)
        self.assertEqual(result["untaggable"], 1)

    def test_leftover_scratch_files_from_a_killed_run_are_swept_not_tagged(self):
        (self.root / "001 - A - One [aaaaaaaaaaa].webm").write_bytes(b"audio")
        stale = self.root / "001 - A - One [aaaaaaaaaaa].converting.m4a"
        stale.write_bytes(b"half an encode")

        with patch.object(audio_tags, "finalize_file",
                          side_effect=lambda p, *a, **k: {"path": str(p), "converted": False, "tagged": True}):
            result = audio_tags.retag_directory(self.root, lookup=False)

        self.assertFalse(stale.exists())
        self.assertEqual(result["swept"], 1)
        self.assertEqual(result["files"], 1)

    def test_a_missing_mutagen_fails_loudly_instead_of_blanking_the_library(self):
        (self.root / "001 - A - One [aaaaaaaaaaa].webm").write_bytes(b"audio")

        with patch.object(audio_tags, "_MUTAGEN_OK", False):
            result = audio_tags.retag_directory(self.root)

        self.assertFalse(result["ok"])
        self.assertIn("mutagen", result["error"])

    def test_a_missing_folder_reports_an_error_instead_of_raising(self):
        result = audio_tags.retag_directory(self.root / "nope")
        self.assertFalse(result["ok"])

    def test_progress_is_reported_per_file_so_the_run_never_looks_frozen(self):
        for n in range(3):
            (self.root / f"00{n} - A - S{n} [aaaaaaaaaa{n}].opus").write_bytes(b"audio")
        seen = []

        with patch.object(audio_tags, "finalize_file",
                          side_effect=lambda p, *a, **k: {"path": str(p), "converted": False, "tagged": True}):
            audio_tags.retag_directory(self.root, lookup=False, progress_hook=seen.append)

        self.assertTrue(seen[0]["active"])
        self.assertFalse(seen[-1]["active"])
        self.assertEqual(seen[-1]["percent"], 100.0)
        percents = [s["percent"] for s in seen if s["active"]]
        self.assertEqual(percents, sorted(percents))
        self.assertGreaterEqual(len([s for s in seen if "/3" in s.get("detail", "")]), 3)

    def test_cancelling_stops_the_run_and_says_so(self):
        for n in range(4):
            (self.root / f"00{n} - A - S{n} [aaaaaaaaaa{n}].opus").write_bytes(b"audio")
        cancel = threading.Event()

        def fake_finalize(path, *args, **kwargs):
            cancel.set()
            return {"path": str(path), "converted": False, "tagged": True}

        with patch.object(audio_tags, "finalize_file", side_effect=fake_finalize):
            result = audio_tags.retag_directory(
                self.root, lookup=False, cancel_event=cancel, max_workers=1
            )

        self.assertTrue(result["cancelled"])
        self.assertLess(result["tagged"], result["files"])

    def test_files_that_are_already_repaired_are_skipped_without_network_calls(self):
        (self.root / "001 - A - One [aaaaaaaaaaa].m4a").write_bytes(b"audio")

        with (
            patch.object(audio_tags, "already_tagged", return_value=True),
            patch.object(audio_tags, "lookup_youtube") as youtube,
            patch.object(audio_tags, "finalize_file") as finalize,
        ):
            result = audio_tags.retag_directory(self.root)

        youtube.assert_not_called()
        finalize.assert_not_called()
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["tagged"], 1)

    def test_force_repairs_even_files_that_look_finished(self):
        path = self.root / "001 - A - One [aaaaaaaaaaa].m4a"
        path.write_bytes(b"audio")

        with (
            patch.object(audio_tags, "already_tagged", return_value=True),
            patch.object(audio_tags, "finalize_file",
                         return_value={"path": str(path), "converted": False, "tagged": True}) as finalize,
        ):
            result = audio_tags.retag_directory(self.root, lookup=False, force=True)

        finalize.assert_called_once()
        self.assertEqual(result["skipped"], 0)


class DownloadIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_downloaded_tracks_are_tagged_and_the_final_path_is_returned(self):
        downloaded = self.root / "001 - Song [dQw4w9WgXcQ].webm"
        tagged = self.root / "001 - Song [dQw4w9WgXcQ].m4a"
        info = {"id": "dQw4w9WgXcQ", "requested_downloads": [{"filepath": str(downloaded)}]}

        class FakeYDL:
            def __init__(self, opts=None):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, url, download=True):
                downloaded.write_bytes(b"audio")
                return info

            def prepare_filename(self, data):
                return str(downloaded)

        track = {"title": "Song", "artists": "Artist", "url": "http://music/watch?v=dQw4w9WgXcQ"}
        with (
            patch.object(ytmusic, "YoutubeDL", FakeYDL),
            patch.object(ytmusic, "_YTDLP_OK", True),
            patch.object(ytmusic.audio_tags, "finalize_download", return_value=str(tagged)) as finalize,
        ):
            saved = ytmusic.download_audio_tracks([track], output_dir=str(self.root), quality="best")

        self.assertEqual(saved, [str(tagged)])
        finalize.assert_called_once()
        self.assertEqual(finalize.call_args.args[0], str(downloaded))
        self.assertEqual(finalize.call_args.args[1], track)

    def test_the_real_written_file_is_preferred_over_the_name_template(self):
        real = self.root / "001 - Song [dQw4w9WgXcQ].m4a"
        real.write_bytes(b"audio")

        class FakeYDL:
            def prepare_filename(self, data):
                return str(self_root / "001 - Song [dQw4w9WgXcQ].webm")

        self_root = self.root
        info = {"requested_downloads": [{"filepath": str(real)}]}
        self.assertEqual(ytmusic._downloaded_path(FakeYDL(), info), str(real))

    def test_the_name_template_is_the_fallback_when_ytdlp_records_nothing(self):
        expected = self.root / "001 - Song [dQw4w9WgXcQ].m4a"

        class FakeYDL:
            def prepare_filename(self, data):
                return str(expected)

        self.assertEqual(ytmusic._downloaded_path(FakeYDL(), {}), str(expected))


if __name__ == "__main__":
    unittest.main()
