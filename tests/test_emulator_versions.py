import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from actions import rom_catalog as rc
from ui.panels.emulators import EmulatorsModePanel


class EmulatorVersionSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selector_switches_from_a_locked_version_to_a_downloadable_one(self):
        public = rc.Rom(
            title="Metal Gear Solid 3 - Snake Eater",
            console_id="ps2",
            stem="Metal Gear Solid 3 - Snake Eater (Italy, PS2)",
            filename="mgs3.iso",
            region="Italia",
            available=True,
        )
        locked = rc.Rom(
            title=public.title,
            console_id="ps2",
            stem="Metal Gear Solid 3 - Snake Eater (Europe) (En,Fr)",
            filename="mgs3.7z",
            region="Europa",
            languages=["En", "Fr"],
            available=False,
            unavailable_reason="Fuente restringida",
        )
        panel = EmulatorsModePanel()

        with patch.object(rc, "versions_for", return_value=[public, locked]):
            panel._show_rom_detail(locked)
            self.assertEqual(panel._version_combo.count(), 2)
            self.assertFalse(panel._action_btn.isEnabled())
            self.assertIn("No disponible", panel._action_btn.text())

            panel._version_combo.setCurrentIndex(0)

        self.assertIs(panel._detail_rom, public)
        self.assertTrue(panel._action_btn.isEnabled())
        self.assertIn("Descargar", panel._action_btn.text())
        panel.shutdown()


if __name__ == "__main__":
    unittest.main()
