import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from actions import offline_library, ytmusic


class OfflineLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.registry = self.root / "offline_playlists.json"
        self.registry_patch = patch.object(
            offline_library, "_REGISTRY_FILE", self.registry
        )
        self.registry_patch.start()
        with offline_library._ACTIVE_SYNCS_LOCK:
            offline_library._ACTIVE_SYNCS.clear()
        with offline_library._INDEX_LOCK:
            offline_library._index_cache = {}
            offline_library._index_ts = 0.0

    def tearDown(self):
        with offline_library._ACTIVE_SYNCS_LOCK:
            offline_library._ACTIVE_SYNCS.clear()
        self.registry_patch.stop()
        self.tempdir.cleanup()

    def test_failed_playlist_listing_does_not_create_empty_offline_entry(self):
        playlist_dir = self.root / "audio" / "Broken"

        with (
            patch.object(ytmusic, "_playlist_output_dir", return_value=playlist_dir),
            patch.object(
                ytmusic,
                "list_playlist_tracks",
                side_effect=RuntimeError("network unavailable"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "network unavailable"):
                offline_library.sync_playlist("broken", title="Broken")

        self.assertFalse(offline_library.is_offline("broken"))

    def test_sync_checks_its_own_folder_not_another_playlist_global_index(self):
        video_id = "abcdefghijk"
        other_dir = self.root / "audio" / "Other"
        target_dir = self.root / "audio" / "Target"
        other_dir.mkdir(parents=True)
        (other_dir / f"001 - Song [{video_id}].m4a").write_bytes(b"audio")
        offline_library.mark_offline("other", "Other", str(other_dir))
        tracks = [{"videoId": video_id, "title": "Song", "artists": "Artist"}]

        with (
            patch.object(ytmusic, "_playlist_output_dir", return_value=target_dir),
            patch.object(ytmusic, "list_playlist_tracks", return_value=tracks),
            patch.object(ytmusic, "download_audio_tracks", return_value=[]) as download,
        ):
            offline_library.sync_playlist("target", title="Target")

        download.assert_called_once()
        self.assertEqual(download.call_args.args[0], tracks)

    def test_unmark_refuses_to_delete_a_playlist_while_it_is_syncing(self):
        playlist_dir = self.root / "audio" / "Busy"
        playlist_dir.mkdir(parents=True)
        marker = playlist_dir / "track.m4a"
        marker.write_bytes(b"audio")
        offline_library.mark_offline("busy", "Busy", str(playlist_dir))
        with offline_library._ACTIVE_SYNCS_LOCK:
            offline_library._ACTIVE_SYNCS["busy"] = True

        result = offline_library.unmark_offline("busy", delete_files=True)

        self.assertTrue(result.get("busy"))
        self.assertTrue(offline_library.is_offline("busy"))
        self.assertTrue(marker.exists())

    def test_unmark_never_deletes_a_directory_outside_the_audio_root(self):
        audio_root = self.root / "managed-audio"
        outsider = self.root / "unrelated-user-folder"
        outsider.mkdir()
        marker = outsider / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        offline_library.mark_offline("unsafe", "Unsafe", str(outsider))

        with patch.object(ytmusic, "_downloads_dir", return_value=audio_root):
            result = offline_library.unmark_offline("unsafe", delete_files=True)

        self.assertTrue(result.get("unsafe_path"))
        self.assertTrue(marker.exists())

    def test_registry_replace_failure_preserves_last_valid_file(self):
        original = {"saved": {"title": "Existing", "dir": "C:/music"}}
        self.registry.write_text(json.dumps(original), encoding="utf-8")

        with patch.object(Path, "replace", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                offline_library.mark_offline("new", "New", str(self.root / "audio"))

        self.assertEqual(json.loads(self.registry.read_text(encoding="utf-8")), original)

    def test_only_one_offline_sync_can_be_reserved_for_the_shared_ui(self):
        self.assertTrue(offline_library.reserve_sync("first"))
        self.assertFalse(offline_library.reserve_sync("second"))
        self.assertTrue(offline_library.is_syncing("first"))
        self.assertFalse(offline_library.is_syncing("second"))

    def test_sync_does_not_clear_a_global_cancel_owned_by_another_download(self):
        playlist_dir = self.root / "audio" / "Target"
        tracks = [{"videoId": "abcdefghijk", "title": "Song"}]

        with (
            patch.object(ytmusic, "_playlist_output_dir", return_value=playlist_dir),
            patch.object(ytmusic, "list_playlist_tracks", return_value=tracks),
            patch.object(ytmusic, "download_audio_tracks", return_value=[]),
            patch.object(ytmusic._DOWNLOAD_CANCEL_ALL, "clear") as clear_cancel,
        ):
            offline_library.sync_playlist("target", title="Target")

        clear_cancel.assert_not_called()

    def test_reconcile_order_rolls_back_every_file_if_a_rename_fails(self):
        playlist_dir = self.root / "audio" / "Playlist"
        playlist_dir.mkdir(parents=True)
        first = playlist_dir / "001 - First [aaaaaaaaaaa].m4a"
        second = playlist_dir / "002 - Second [bbbbbbbbbbb].m4a"
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        tracks = [
            {"videoId": "bbbbbbbbbbb", "title": "Second"},
            {"videoId": "aaaaaaaaaaa", "title": "First"},
        ]
        real_rename = Path.rename
        calls = [0]

        def flaky_rename(path, target):
            calls[0] += 1
            if calls[0] == 3:
                raise OSError("rename interrupted")
            return real_rename(path, target)

        with patch.object(Path, "rename", flaky_rename):
            with self.assertRaisesRegex(OSError, "rename interrupted"):
                offline_library._reconcile_order(playlist_dir, tracks)

        self.assertEqual(first.read_bytes(), b"first")
        self.assertEqual(second.read_bytes(), b"second")
        self.assertFalse(any("._sync_tmp_" in path.name for path in playlist_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
