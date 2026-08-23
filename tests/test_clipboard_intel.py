import unittest
from unittest.mock import MagicMock, patch


class ClipboardIntelTests(unittest.TestCase):
    def _stub_model(self, return_text: str):
        stub_response = MagicMock()
        stub_response.text = return_text
        stub_model = MagicMock()
        stub_model.generate_content.return_value = stub_response
        return stub_model

    def test_summarize_calls_model_and_returns_text(self):
        from actions import clipboard_intel
        with patch("actions.genai_client.get_model", return_value=self._stub_model("resumen")):
            result = clipboard_intel.summarize("texto largo")
        self.assertEqual(result, "resumen")

    def test_translate_calls_model_and_returns_text(self):
        from actions import clipboard_intel
        with patch("actions.genai_client.get_model", return_value=self._stub_model("hello")):
            result = clipboard_intel.translate("hola", target_lang="en")
        self.assertEqual(result, "hello")

    def test_explain_calls_model_and_returns_text(self):
        from actions import clipboard_intel
        with patch("actions.genai_client.get_model", return_value=self._stub_model("explicacion")):
            result = clipboard_intel.explain("algo tecnico")
        self.assertEqual(result, "explicacion")

    def test_fix_calls_model_and_returns_text(self):
        from actions import clipboard_intel
        with patch("actions.genai_client.get_model", return_value=self._stub_model("texto corregido")):
            result = clipboard_intel.fix("texto con herrores")
        self.assertEqual(result, "texto corregido")

    def test_functions_fail_soft_on_exception(self):
        from actions import clipboard_intel
        with patch("actions.genai_client.get_model", side_effect=RuntimeError("boom")):
            self.assertEqual(clipboard_intel.summarize("x"), "")
            self.assertEqual(clipboard_intel.translate("x"), "")
            self.assertEqual(clipboard_intel.explain("x"), "")
            self.assertEqual(clipboard_intel.fix("x"), "")


if __name__ == "__main__":
    unittest.main()
