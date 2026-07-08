import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, QSize, QPoint

from ui import _compute_initial_window_geometry


class WindowGeometryTests(unittest.TestCase):
    def test_geometry_fits_screen_and_stays_centered(self):
        rect = _compute_initial_window_geometry(
            default_size=(1180, 760),
            screen_geometry=QRect(100, 50, 1000, 700),
            min_size=(900, 620),
            margin=40,
        )

        self.assertEqual(rect.size(), QSize(920, 620))
        self.assertEqual(rect.topLeft(), QPoint(140, 90))


if __name__ == "__main__":
    unittest.main()
