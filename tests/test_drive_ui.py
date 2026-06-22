import os
import io
import wave
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from ui import DriveModePanel


class DriveModePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_double_click_keeps_folder_inside_drive_panel(self):
        panel = DriveModePanel()
        panel._load_current_folder = Mock()
        item = QListWidgetItem("Carpeta")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {
                "id": "folder-id",
                "name": "Carpeta",
                "mimeType": "application/vnd.google-apps.folder",
                "webViewLink": "https://drive.google.com/drive/folders/folder-id",
            },
        )
        panel.file_list.addItem(item)
        panel.file_list.setCurrentItem(item)

        panel.file_list.itemDoubleClicked.emit(item)

        self.assertEqual(panel._current_folder_id, "folder-id")
        self.assertEqual(panel._current_folder_name, "Carpeta")
        self.assertEqual(panel._folder_stack, [("root", "Mi unidad")])
        self.assertEqual(panel.folder_path.text(), "Mi unidad  /  Carpeta")
        self.assertTrue(panel.folder_back.isEnabled())
        panel._load_current_folder.assert_called_once_with()
        panel.close()

    def test_folder_back_restores_previous_folder(self):
        panel = DriveModePanel()
        panel._load_current_folder = Mock()
        panel._folder_stack = [("root", "Mi unidad")]
        panel._current_folder_id = "folder-id"
        panel._current_folder_name = "Carpeta"

        panel.go_back_folder()

        self.assertEqual(panel._current_folder_id, "root")
        self.assertEqual(panel._current_folder_name, "Mi unidad")
        self.assertEqual(panel._folder_stack, [])
        self.assertEqual(panel.folder_path.text(), "Mi unidad")
        self.assertFalse(panel.folder_back.isEnabled())
        panel._load_current_folder.assert_called_once_with()
        panel.close()

    def test_audio_preview_uses_internal_player(self):
        panel = DriveModePanel()
        panel._preview_request = 1
        audio = io.BytesIO()
        with wave.open(audio, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\x00\x00" * 800)

        panel._apply_preview(
            1,
            {
                "kind": "audio",
                "data": audio.getvalue(),
                "mimeType": "audio/wav",
                "info": {"id": "audio-id", "name": "grabacion.wav"},
            },
        )

        self.assertIs(panel.preview_stack.currentWidget(), panel.preview_audio)
        self.assertTrue(panel._drive_audio_player.source().isLocalFile())
        self.assertEqual(panel.audio_title.text(), "grabacion.wav")
        panel.close()


if __name__ == "__main__":
    unittest.main()
