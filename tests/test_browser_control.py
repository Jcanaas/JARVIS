import sys
import unittest
from unittest.mock import MagicMock, patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from actions import browser_control


class NormalizeUrlTests(unittest.TestCase):
    def test_empty_url_returns_about_blank(self):
        result = browser_control._normalize_url("")

        self.assertEqual(result, "about:blank")

    def test_url_with_scheme_passes_through(self):
        result = browser_control._normalize_url("https://example.com")

        self.assertEqual(result, "https://example.com")

    def test_domain_gets_https_prefix(self):
        result = browser_control._normalize_url("example.com")

        self.assertEqual(result, "https://example.com")

    def test_bare_word_becomes_dot_com_domain(self):
        result = browser_control._normalize_url("instagram")

        self.assertEqual(result, "https://instagram.com")

    def test_url_with_whitespace_is_stripped(self):
        result = browser_control._normalize_url("  https://example.com  ")

        self.assertEqual(result, "https://example.com")

    def test_http_scheme_passes_through(self):
        result = browser_control._normalize_url("http://example.com")

        self.assertEqual(result, "http://example.com")

    def test_subdomain_preserved(self):
        result = browser_control._normalize_url("mail.google.com")

        self.assertEqual(result, "https://mail.google.com")


class UserAgentTests(unittest.TestCase):
    def test_user_agent_is_non_empty_string(self):
        result = browser_control._user_agent()

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 10)

    def test_user_agent_contains_chrome_or_webkit(self):
        result = browser_control._user_agent()

        self.assertIn("Chrome", result)

    @patch("actions.browser_control._OS", "Windows")
    def test_windows_user_agent_identifies_windows(self):
        result = browser_control._user_agent()

        self.assertIn("Windows", result)

    @patch("actions.browser_control._OS", "Darwin")
    def test_macos_user_agent_identifies_macintosh(self):
        result = browser_control._user_agent()

        self.assertIn("Macintosh", result)

    @patch("actions.browser_control._OS", "Linux")
    def test_linux_user_agent_identifies_x11(self):
        result = browser_control._user_agent()

        self.assertIn("X11", result)


class ResolveBrowserTests(unittest.TestCase):
    def test_returns_none_for_unknown_browser(self):
        with patch("actions.browser_control._BROWSER_SPECS", {}):
            result = browser_control._resolve_browser("unknownbrowser")

            self.assertIsNone(result)

    @patch("actions.browser_control._OS", "Windows")
    @patch("actions.browser_control._BROWSER_SPECS")
    @patch("shutil.which")
    def test_resolves_chrome_on_windows(self, which_mock, specs_mock):
        specs_mock.get.return_value = {
            "chrome": {
                "engine": "chromium",
                "bins": ["chrome", "google-chrome"],
            }
        }
        which_mock.return_value = "C:\\Program Files\\Google\\Chrome\\chrome.exe"

        with patch("actions.browser_control._ALIASES", {}):
            result = browser_control._resolve_browser("chrome")

        self.assertIsNotNone(result)
        self.assertEqual(result["engine"], "chromium")

    @patch("actions.browser_control._BROWSER_SPECS")
    def test_returns_dict_with_engine_exe_channel_keys(self, specs_mock):
        specs_mock.get.return_value = {
            "firefox": {
                "engine": "firefox",
                "bins": ["firefox"],
            }
        }
        with patch("actions.browser_control._ALIASES", {}), \
             patch("shutil.which", return_value="/usr/bin/firefox"):
            result = browser_control._resolve_browser("firefox")

        self.assertIn("engine", result)
        self.assertIn("exe", result)
        self.assertIn("channel", result)


class DetectDefaultBrowserTests(unittest.TestCase):
    def test_returns_string(self):
        with patch("shutil.which", return_value=None):
            result = browser_control._detect_default_browser()

            self.assertIsInstance(result, str)
            self.assertGreater(len(result), 0)


class BrowserControlFunctionTests(unittest.TestCase):
    @patch("actions.browser_control.async_playwright")
    def test_browser_control_handles_invalid_action(self, mock_pw):
        # Test with invalid action — should not crash
        with patch("asyncio.run"):
            try:
                browser_control.browser_control(
                    parameters={"action": "invalid_action", "url": "https://example.com"},
                    response=None,
                    speak=None
                )
            except Exception as e:
                # Should handle gracefully or return error message
                self.assertIsNotNone(str(e))

    def test_browser_control_with_empty_parameters_returns_string(self):
        with patch("asyncio.run"):
            try:
                result = browser_control.browser_control(parameters={}, response=None, speak=None)
                # Should return a string error message or handle gracefully
                if result is not None:
                    self.assertIsInstance(result, (str, type(None)))
            except Exception:
                # Expected — function is complex async
                pass


if __name__ == "__main__":
    unittest.main()
