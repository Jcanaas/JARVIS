"""Locking the workstation on Windows.

`pyautogui.hotkey("win", "l")` looks right but never works: Windows reserves
Win+L for real hardware input so synthetic events cannot fake the secure
attention sequence, and the call is silently ignored. Only the user32 API
actually locks. These tests pin that, since the failure mode is "nothing
happens" rather than an exception.

Nothing here ever calls the real API — that would lock the machine running
the suite.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from actions import computer_settings


@unittest.skipUnless(sys.platform == "win32", "Windows-specific locking path")
class LockScreenWindowsTests(unittest.TestCase):
    def test_uses_the_user32_api_and_not_the_win_l_hotkey(self):
        user32 = MagicMock()
        user32.LockWorkStation.return_value = 1
        windll = MagicMock(user32=user32)

        with patch("ctypes.windll", windll), \
             patch.object(computer_settings, "pyautogui") as gui:
            computer_settings.lock_screen()

        user32.LockWorkStation.assert_called_once()
        gui.hotkey.assert_not_called()

    def test_falls_back_to_the_hotkey_only_when_the_api_reports_failure(self):
        user32 = MagicMock()
        user32.LockWorkStation.return_value = 0  # API says it did not lock
        windll = MagicMock(user32=user32)

        with patch("ctypes.windll", windll), \
             patch.object(computer_settings, "pyautogui") as gui:
            computer_settings.lock_screen()

        gui.hotkey.assert_called_once_with("win", "l")


if __name__ == "__main__":
    unittest.main()
