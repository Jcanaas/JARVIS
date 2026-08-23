import unittest
from concurrent.futures import Future
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from actions.playback_controller import PlaybackCommandResult
from ui.panels.music import MusicModePanelV2


class MusicPanelPlaybackStateTests(unittest.TestCase):
    def test_detail_metadata_keeps_preview_until_full_cover_is_downloaded(self):
        class ImmediateThread:
            def __init__(self, target, daemon=False):
                self._target = target

            def start(self):
                self._target()

        panel = SimpleNamespace(
            _detail_request=0,
            _details_loading_key=(),
            _result_sig=Mock(),
            _thumb_cache={"https://art.test/full": b"full-image"},
            _fetch_thumb_b64=Mock(return_value="ZnVsbC1pbWFnZQ=="),
        )
        panel._details_key = lambda data: (data.get("videoId", ""), "", "")
        panel._data_uri = lambda raw: f"data:image/jpeg;base64,{raw.decode()}"
        track = {"videoId": "track-id", "title": "Track", "artists": "Artist"}
        details = {
            "videoId": "track-id",
            "title": "Track",
            "thumbnail": "https://art.test/full",
            "album": "Album",
        }

        with (
            patch("actions.ytmusic.get_song_details", return_value=details),
            patch("ui.panels.music.threading.Thread", ImmediateThread),
        ):
            MusicModePanelV2._load_now_playing_details(panel, track)

        first_op, first_payload = panel._result_sig.emit.call_args_list[0].args
        self.assertEqual(first_op, "now_playing_details")
        self.assertNotIn("thumbnail", first_payload["details"])
        self.assertEqual(first_payload["details"]["album"], "Album")

        second_op, second_payload = panel._result_sig.emit.call_args_list[1].args
        self.assertEqual(second_op, "now_playing_images")
        self.assertEqual(second_payload["details"]["thumbnail"], "https://art.test/full")

    def test_play_song_does_not_mark_row_until_backend_confirms(self):
        target = {
            "_kind": "song",
            "_index": 1,
            "videoId": "target",
            "title": "Target",
            "artists": "Artist",
        }
        panel = SimpleNamespace(
            _items=[
                {"_index": 0, "title": "Unavailable", "videoId": ""},
                target,
                {"_index": 2, "title": "Next", "videoId": "next"},
            ],
            _table_kind="album_tracks",
            _current_playlist=None,
            _mark_playing_row=Mock(),
            _send_playback=Mock(),
        )
        panel._safe_text = MethodType(MusicModePanelV2._safe_text, panel)
        panel._audio_tracks_from_items = MethodType(
            MusicModePanelV2._audio_tracks_from_items, panel
        )
        panel._audio_tracks_from_data = MethodType(
            MusicModePanelV2._audio_tracks_from_data, panel
        )
        panel._playable_tracks_with_selection = MethodType(
            MusicModePanelV2._playable_tracks_with_selection, panel
        )

        MusicModePanelV2._play_song(panel, target)

        panel._mark_playing_row.assert_not_called()
        action, params = panel._send_playback.call_args.args[:2]
        self.assertEqual(action, "play_tracks")
        self.assertEqual(params["start_index"], 0)

    def test_artist_page_uses_index_in_filtered_playable_tracks(self):
        target = {"videoId": "target", "title": "Target", "artists": "Artist"}
        panel = SimpleNamespace(
            _artist_page_data={
                "top_songs": [
                    {"videoId": "", "title": "Unavailable"},
                    target,
                ]
            },
            _send_playback=Mock(),
        )
        panel._audio_tracks_from_data = MethodType(
            MusicModePanelV2._audio_tracks_from_data, panel
        )
        panel._playable_tracks_with_selection = MethodType(
            MusicModePanelV2._playable_tracks_with_selection, panel
        )

        MusicModePanelV2._play_artist_page_track(panel, "top_songs", 1)

        _action, params = panel._send_playback.call_args.args[:2]
        self.assertEqual(params["start_index"], 0)

    def test_matching_track_prefers_video_id_over_duplicate_metadata(self):
        first = {"videoId": "first", "title": "Live", "artists": "Artist"}
        second = {"videoId": "second", "title": "Live", "artists": "Artist"}
        panel = SimpleNamespace(_items=[first, second])
        panel._safe_text = MethodType(MusicModePanelV2._safe_text, panel)

        matched = MusicModePanelV2._find_matching_track(
            panel, "Live", "Artist", video_id="second"
        )

        self.assertIs(matched, second)

    def test_playback_future_is_marshaled_back_to_panel_result_signal(self):
        future = Future()
        callback = Mock(return_value=future)
        panel = SimpleNamespace(
            window=Mock(return_value=SimpleNamespace(on_playback_command=callback)),
            _begin_playback_request=Mock(return_value=7),
            _result_sig=Mock(),
        )
        track = {"videoId": "target", "title": "Target", "artists": "Artist"}

        returned = MusicModePanelV2._send_playback(
            panel, "play_track", track, playback_data=track
        )
        future.set_result(PlaybackCommandResult("play_track", True, value=True))

        self.assertIs(returned, future)
        panel._begin_playback_request.assert_called_once_with(track)
        op, payload = panel._result_sig.emit.call_args.args
        self.assertEqual(op, "playback_command")
        self.assertEqual(payload["token"], 7)
        self.assertTrue(payload["result"].ok)

    def test_failed_playback_request_restores_confirmed_selection(self):
        panel = SimpleNamespace(
            _pending_playback={"token": 3, "data": {"title": "Target"}},
            status=Mock(),
            _clear_pending_playback=Mock(),
            _restore_playing_selection=Mock(),
            _set_playback_status=Mock(),
        )
        payload = {
            "token": 3,
            "result": PlaybackCommandResult(
                "play_track", False, message="No se pudo cargar la canción."
            ),
        }

        MusicModePanelV2._handle_playback_command_result(panel, payload)

        panel._clear_pending_playback.assert_called_once()
        panel._restore_playing_selection.assert_called_once()
        panel._set_playback_status.assert_called_with(
            "No se pudo iniciar la reproducción: No se pudo cargar la canción.",
            "error",
        )

    def test_pending_request_is_confirmed_only_by_the_expected_video(self):
        panel = SimpleNamespace(
            _pending_playback={
                "token": 4,
                "data": {
                    "videoId": "expected",
                    "title": "Same title",
                    "artists": "Artist",
                },
            },
            _clear_pending_playback=Mock(),
            _set_playback_status=Mock(),
        )
        panel._safe_text = MethodType(MusicModePanelV2._safe_text, panel)

        MusicModePanelV2._confirm_pending_playback(
            panel, "Same title", "Artist", "different"
        )
        panel._clear_pending_playback.assert_not_called()

        MusicModePanelV2._confirm_pending_playback(
            panel, "Same title", "Artist", "expected"
        )
        panel._clear_pending_playback.assert_called_once()
        panel._set_playback_status.assert_called_once_with("", "")

    def test_backend_rejection_without_message_is_still_an_error(self):
        panel = SimpleNamespace(
            _pending_playback={"token": 8, "data": {"title": "Target"}},
            _clear_pending_playback=Mock(),
            _restore_playing_selection=Mock(),
            _set_playback_status=Mock(),
        )

        MusicModePanelV2._handle_playback_command_result(panel, {
            "token": 8,
            "result": PlaybackCommandResult("play_track", False),
        })

        panel._clear_pending_playback.assert_called_once()
        message, state = panel._set_playback_status.call_args.args
        self.assertEqual(state, "error")
        self.assertIn("rechazó", message)

    def test_paused_current_track_uses_pause_marker_and_stays_selected(self):
        number_item = Mock()
        table = Mock()
        table.rowCount.return_value = 1
        table.item.return_value = number_item
        data = {
            "videoId": "track-id",
            "title": "Track",
            "artists": "Artist",
            "_index": 0,
        }
        panel = SimpleNamespace(
            _table_kind="songs",
            _table_revision=2,
            _playing_mark_revision=-1,
            table=table,
            _row_data=Mock(return_value=data),
            _set_row_data=Mock(),
        )
        panel._safe_text = MethodType(MusicModePanelV2._safe_text, panel)

        MusicModePanelV2._mark_playing_row(
            panel, "Track", "Artist", video_id="track-id", playing=False
        )

        self.assertTrue(data["_current_track"])
        self.assertFalse(data["_playing"])
        number_item.setText.assert_called_with("Ⅱ")
        table.selectRow.assert_called_once_with(0)

    def test_details_heading_announces_paused_state(self):
        panel = SimpleNamespace(details_heading=Mock())

        MusicModePanelV2._set_playback_heading(panel, playing=False)

        panel.details_heading.setText.assert_called_once_with("EN PAUSA")
        panel.details_heading.setAccessibleName.assert_called_once_with(
            "Estado: en pausa"
        )


if __name__ == "__main__":
    unittest.main()
