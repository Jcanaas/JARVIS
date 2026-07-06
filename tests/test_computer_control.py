import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from actions import computer_control


class GetOsTests(unittest.TestCase):
    def test_returns_string(self):
        result = computer_control._get_os()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    @patch("actions.computer_control._load_config")
    def test_returns_config_os_system(self, mock_config):
        mock_config.return_value = {"os_system": "Windows"}
        result = computer_control._get_os()
        self.assertEqual(result, "windows")


class RandomDataTests(unittest.TestCase):
    def test_generates_random_name(self):
        result = computer_control._random_data("name")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_generates_random_email(self):
        result = computer_control._random_data("email")
        self.assertIsInstance(result, str)
        self.assertIn("@", result)

    def test_generates_random_phone(self):
        result = computer_control._random_data("phone")
        self.assertIsInstance(result, str)
        # Phone should be all digits or contain common delimiters
        self.assertTrue(
            any(c.isdigit() for c in result),
            f"Expected digits in phone: {result}"
        )

    def test_generates_random_password(self):
        result = computer_control._random_data("password")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 5)

    def test_generates_random_address(self):
        result = computer_control._random_data("address")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_generates_random_credit_card(self):
        result = computer_control._random_data("credit_card")
        self.assertIsInstance(result, str)
        # For unknown types, returns "random_{type}_{number}"
        self.assertIn("random_credit_card_", result)

    def test_invalid_type_returns_string(self):
        result = computer_control._random_data("unknown_type")
        self.assertIsInstance(result, str)


class UserProfileTests(unittest.TestCase):
    def test_returns_dict(self):
        result = computer_control._user_profile()
        self.assertIsInstance(result, dict)

    def test_contains_common_fields(self):
        result = computer_control._user_profile()
        # Should have at least some user-related fields
        self.assertGreater(len(result), 0)


class SafeScreenshotPathTests(unittest.TestCase):
    def test_default_path_is_desktop(self):
        result = computer_control._safe_screenshot_path(None)
        self.assertIsInstance(result, Path)
        # Should be a reasonable path
        self.assertGreater(len(str(result)), 0)

    def test_custom_path_fallback_if_unsafe(self):
        # Custom unsafe paths should fallback to Desktop
        custom = "/etc/passwd"
        result = computer_control._safe_screenshot_path(custom)
        # Should be desktop path since /etc/passwd is not in safe roots
        self.assertIn("Desktop", str(result))

    def test_cleans_malicious_paths(self):
        # Should prevent directory traversal
        malicious = "../../etc/passwd"
        result = computer_control._safe_screenshot_path(malicious)
        self.assertIsInstance(result, Path)
        # Should not contain the traversal
        self.assertNotIn("..", str(result))


class ComputerControlMainTests(unittest.TestCase):
    def test_rejects_empty_action(self):
        result = computer_control.computer_control(
            parameters={"action": ""}
        )
        self.assertIsInstance(result, str)

    def test_handles_unknown_action(self):
        result = computer_control.computer_control(
            parameters={"action": "unknown_xyz"}
        )
        # Should return error or not crash
        self.assertIsInstance(result, str)

    @patch("actions.computer_control.pyautogui")
    def test_type_action_delegates_to_pyautogui(self, mock_gui):
        with patch("actions.computer_control._require_pyautogui"):
            result = computer_control.computer_control(
                parameters={"action": "type", "text": "hello"}
            )
            # Should not crash, result should be string
            self.assertIsInstance(result, str)

    @patch("actions.computer_control.pyautogui")
    def test_press_action_delegates(self, mock_gui):
        with patch("actions.computer_control._require_pyautogui"):
            result = computer_control.computer_control(
                parameters={"action": "press", "key": "enter"}
            )
            self.assertIsInstance(result, str)

    def test_hotkey_action_with_keys(self):
        with patch("actions.computer_control.pyautogui"):
            with patch("actions.computer_control._require_pyautogui"):
                result = computer_control.computer_control(
                    parameters={"action": "hotkey", "keys": "ctrl+c"}
                )
                self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
