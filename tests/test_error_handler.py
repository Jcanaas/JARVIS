import json
import sys
import unittest
from unittest.mock import MagicMock, patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from agent import error_handler
from agent.error_handler import ErrorDecision


def _mock_model(response_text: str):
    model = MagicMock()
    model.generate_content.return_value = MagicMock(text=response_text)
    return model


class AnalyzeErrorTests(unittest.TestCase):
    def test_forces_replan_when_max_attempts_reached(self):
        step = {"step": 1, "tool": "web_search", "description": "search"}

        result = error_handler.analyze_error(step, "timeout", attempt=2, max_attempts=2)

        self.assertEqual(result["decision"], ErrorDecision.REPLAN)
        self.assertEqual(result["max_retries"], 0)
        self.assertIn("Failed 2 times", result["reason"])

    @patch("actions.genai_client.get_model")
    def test_maps_decision_string_to_enum(self, get_model):
        get_model.return_value = _mock_model(json.dumps({
            "decision": "retry",
            "reason": "transient network error",
            "fix_suggestion": "",
            "max_retries": 1,
            "user_message": "Retrying, sir."
        }))
        step = {"step": 1, "tool": "web_search", "description": "search", "critical": False}

        result = error_handler.analyze_error(step, "timeout", attempt=1, max_attempts=3)

        self.assertEqual(result["decision"], ErrorDecision.RETRY)
        self.assertEqual(result["reason"], "transient network error")

    @patch("actions.genai_client.get_model")
    def test_unknown_decision_string_defaults_to_replan(self, get_model):
        get_model.return_value = _mock_model(json.dumps({
            "decision": "who-knows",
            "reason": "?",
        }))
        step = {"step": 1, "tool": "x", "description": "y", "critical": False}

        result = error_handler.analyze_error(step, "err", attempt=1, max_attempts=3)

        self.assertEqual(result["decision"], ErrorDecision.REPLAN)

    @patch("actions.genai_client.get_model")
    def test_critical_step_skip_is_upgraded_to_replan(self, get_model):
        get_model.return_value = _mock_model(json.dumps({
            "decision": "skip",
            "reason": "not important",
            "max_retries": 0,
            "user_message": "Skipping, sir.",
        }))
        step = {"step": 1, "tool": "file_controller", "description": "critical write", "critical": True}

        result = error_handler.analyze_error(step, "err", attempt=1, max_attempts=3)

        self.assertEqual(result["decision"], ErrorDecision.REPLAN)
        self.assertIn("critical", result["user_message"].lower())

    @patch("actions.genai_client.get_model")
    def test_non_critical_step_skip_is_kept(self, get_model):
        get_model.return_value = _mock_model(json.dumps({
            "decision": "skip",
            "reason": "not important",
            "max_retries": 0,
            "user_message": "Skipping, sir.",
        }))
        step = {"step": 1, "tool": "web_search", "description": "optional", "critical": False}

        result = error_handler.analyze_error(step, "err", attempt=1, max_attempts=3)

        self.assertEqual(result["decision"], ErrorDecision.SKIP)

    @patch("actions.genai_client.get_model")
    def test_model_exception_defaults_to_replan(self, get_model):
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError("api down")
        get_model.return_value = model
        step = {"step": 1, "tool": "x", "description": "y", "critical": False}

        result = error_handler.analyze_error(step, "err", attempt=1, max_attempts=3)

        self.assertEqual(result["decision"], ErrorDecision.REPLAN)
        self.assertEqual(result["reason"], "api down")

    @patch("actions.genai_client.get_model")
    def test_malformed_json_defaults_to_replan(self, get_model):
        get_model.return_value = _mock_model("not json")
        step = {"step": 1, "tool": "x", "description": "y", "critical": False}

        result = error_handler.analyze_error(step, "err", attempt=1, max_attempts=3)

        self.assertEqual(result["decision"], ErrorDecision.REPLAN)


class GenerateFixTests(unittest.TestCase):
    @patch("actions.genai_client.get_model")
    def test_returns_code_helper_step_with_cleaned_code(self, get_model):
        get_model.return_value = _mock_model("```python\nprint('hi')\n```")
        step = {"step": 3, "tool": "file_controller", "description": "write file",
                "depends_on": [1], "critical": True}

        fixed = error_handler.generate_fix(step, "disk full", "use a temp dir instead")

        self.assertEqual(fixed["step"], 3)
        self.assertEqual(fixed["tool"], "code_helper")
        self.assertEqual(fixed["parameters"]["code"], "print('hi')")
        self.assertEqual(fixed["parameters"]["action"], "run")
        self.assertEqual(fixed["depends_on"], [1])
        self.assertTrue(fixed["critical"])

    @patch("actions.genai_client.get_model")
    def test_exception_falls_back_to_generated_code_tool(self, get_model):
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError("api down")
        get_model.return_value = model
        step = {"step": 1, "tool": "web_search", "description": "search for X",
                "depends_on": [], "critical": False}

        fixed = error_handler.generate_fix(step, "err", "try differently")

        self.assertEqual(fixed["tool"], "generated_code")
        self.assertEqual(fixed["parameters"]["description"], "search for X")


if __name__ == "__main__":
    unittest.main()
