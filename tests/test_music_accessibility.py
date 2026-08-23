import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractAnimation, Qt
from PyQt6.QtGui import QColor, QKeyEvent, QPixmap
from PyQt6.QtWidgets import QApplication

from ui.widgets.buttons import ToggleSwitch, _LikeBtn, _MediaBtn
from ui.widgets.inputs import SearchGlowInput
from ui import MainWindow
from ui.panels.music import MusicModePanelV2


class MusicAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_media_button_paints_its_style_and_updates_accessible_action(self):
        button = _MediaBtn(_MediaBtn.PLAY)
        self.assertEqual(button.accessibleName(), "Reproducir")
        canvas = QPixmap(button.size())
        canvas.fill(Qt.GlobalColor.transparent)
        button.render(canvas)
        self.assertGreater(QColor(canvas.toImage().pixelColor(2, 2)).alpha(), 0)

        button.set_shape(_MediaBtn.PAUSE)

        self.assertEqual(button.accessibleName(), "Pausar")

    def test_like_button_exposes_checked_state_and_dynamic_action(self):
        button = _LikeBtn()
        self.assertTrue(button.isCheckable())
        self.assertIn("Marcar", button.accessibleName())

        button.set_liked(True)

        self.assertTrue(button.isChecked())
        self.assertIn("Quitar", button.accessibleName())

    def test_toggle_switch_can_be_operated_with_space(self):
        switch = ToggleSwitch(False)
        changed = []
        switch.toggled.connect(changed.append)
        self.assertNotEqual(switch.focusPolicy(), Qt.FocusPolicy.NoFocus)

        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.NoModifier,
        )
        switch.keyPressEvent(event)

        self.assertTrue(switch.isChecked())
        self.assertEqual(changed, [True])

    def test_search_without_filter_has_no_infinite_hidden_spin_animation(self):
        search = SearchGlowInput("Buscar música", show_filter=False)

        self.assertEqual(
            search._spin_anim.state(), QAbstractAnimation.State.Stopped
        )

    def test_playback_animation_timer_only_runs_while_audio_progresses(self):
        timer = Mock()
        timer.isActive.return_value = False
        window = SimpleNamespace(_playback_anim_timer=timer)

        MainWindow._set_playback_animation_active(window, True)

        timer.start.assert_called_once_with()
        timer.reset_mock()
        timer.isActive.return_value = True

        MainWindow._set_playback_animation_active(window, False)

        timer.stop.assert_called_once_with()

    def test_empty_library_has_a_helpful_visible_state(self):
        panel = MusicModePanelV2()
        self.addCleanup(panel._shutdown_thumb_executor)

        panel._handle_result("library_playlists", [])

        self.assertFalse(panel.status.isHidden())
        self.assertIn("playlist", panel.status.text().lower())

    def test_load_error_offers_a_keyboard_accessible_retry(self):
        panel = MusicModePanelV2()
        self.addCleanup(panel._shutdown_thumb_executor)
        retry = Mock()
        panel._retry_operation = ("library_playlists", retry)

        panel._handle_result("library_playlists", RuntimeError("sin conexión"))

        self.assertIn("Reintentar", panel.status.text())
        self.assertNotEqual(
            panel.status.textInteractionFlags(), Qt.TextInteractionFlag.NoTextInteraction
        )
        with patch.object(panel, "_run") as run:
            panel._retry_last_operation()
        run.assert_called_once_with("library_playlists", retry)

    def test_stale_first_playlist_page_cannot_replace_the_current_playlist(self):
        panel = MusicModePanelV2()
        self.addCleanup(panel._shutdown_thumb_executor)
        panel._playlist_request = 2
        panel._current_playlist = {"playlistId": "new", "title": "Nueva"}
        panel._table_kind = "playlist_tracks"
        panel._items = [{"videoId": "current", "title": "Actual"}]

        panel._handle_result(
            "playlist_tracks",
            {
                "request": 1,
                "playlist_id": "old",
                "tracks": [{"videoId": "stale", "title": "Antigua"}],
            },
        )

        self.assertEqual(panel._items, [{"videoId": "current", "title": "Actual"}])

    def test_stale_search_cannot_replace_newer_results(self):
        panel = MusicModePanelV2()
        self.addCleanup(panel._shutdown_thumb_executor)
        panel._search_request = 2
        panel._items = [{"videoId": "new", "title": "Resultado nuevo"}]

        panel._handle_result(
            "search_all",
            {
                "request": 1,
                "query": "consulta antigua",
                "results": {
                    "songs": [{"videoId": "old", "title": "Resultado antiguo"}],
                    "playlists": [],
                    "artists": [],
                },
            },
        )

        self.assertEqual(panel._items, [{"videoId": "new", "title": "Resultado nuevo"}])


if __name__ == "__main__":
    unittest.main()
