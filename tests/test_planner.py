import json
import sys
import unittest
from unittest.mock import MagicMock, patch

# Source prints emoji status lines (e.g. "[Planner] ✅ ..."); on Windows the
# default console codec is cp1252, which can't encode them.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from agent import planner


def _mock_model(response_text: str):
    model = MagicMock()
    model.generate_content.return_value = MagicMock(text=response_text)
    return model


class CreatePlanTests(unittest.TestCase):
    @patch("actions.genai_client.get_model")
    def test_returns_parsed_plan_on_valid_json(self, get_model):
        plan_json = json.dumps({
            "goal": "search for cats",
            "steps": [
                {"step": 1, "tool": "web_search", "description": "Search cats",
                 "parameters": {"query": "cats"}, "critical": True}
            ]
        })
        get_model.return_value = _mock_model(plan_json)

        plan = planner.create_plan("search for cats")

        self.assertEqual(plan["goal"], "search for cats")
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["tool"], "web_search")

    @patch("actions.genai_client.get_model")
    def test_strips_markdown_fences_before_parsing(self, get_model):
        plan_json = "```json\n" + json.dumps({
            "goal": "g", "steps": [{"step": 1, "tool": "web_search",
             "description": "d", "parameters": {}, "critical": False}]
        }) + "\n```"
        get_model.return_value = _mock_model(plan_json)

        plan = planner.create_plan("g")

        self.assertEqual(len(plan["steps"]), 1)

    @patch("actions.genai_client.get_model")
    def test_generated_code_tool_is_replaced_with_web_search(self, get_model):
        plan_json = json.dumps({
            "goal": "do something exotic",
            "steps": [
                {"step": 1, "tool": "generated_code", "description": "write a script",
                 "parameters": {}, "critical": True}
            ]
        })
        get_model.return_value = _mock_model(plan_json)

        plan = planner.create_plan("do something exotic")

        self.assertEqual(plan["steps"][0]["tool"], "web_search")
        self.assertIn("query", plan["steps"][0]["parameters"])

    @patch("actions.genai_client.get_model")
    def test_invalid_json_falls_back_to_single_web_search_step(self, get_model):
        get_model.return_value = _mock_model("not valid json at all")

        plan = planner.create_plan("my goal")

        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["tool"], "web_search")
        self.assertEqual(plan["steps"][0]["parameters"]["query"], "my goal")

    @patch("actions.genai_client.get_model")
    def test_missing_steps_key_falls_back(self, get_model):
        get_model.return_value = _mock_model(json.dumps({"goal": "g"}))

        plan = planner.create_plan("g")

        self.assertEqual(plan["steps"][0]["tool"], "web_search")

    @patch("actions.genai_client.get_model")
    def test_model_exception_falls_back(self, get_model):
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError("API down")
        get_model.return_value = model

        plan = planner.create_plan("g")

        self.assertEqual(plan["steps"][0]["tool"], "web_search")

    @patch("actions.genai_client.get_model")
    def test_context_is_appended_to_prompt(self, get_model):
        get_model.return_value = _mock_model(json.dumps({
            "goal": "g", "steps": [{"step": 1, "tool": "web_search",
             "description": "d", "parameters": {}, "critical": False}]
        }))

        planner.create_plan("g", context="extra context here")

        sent_prompt = get_model.return_value.generate_content.call_args[0][0]
        self.assertIn("extra context here", sent_prompt)


class ReplanTests(unittest.TestCase):
    @patch("actions.genai_client.get_model")
    def test_returns_revised_plan(self, get_model):
        plan_json = json.dumps({
            "goal": "g",
            "steps": [{"step": 2, "tool": "file_controller", "description": "retry write",
                       "parameters": {}, "critical": False}]
        })
        get_model.return_value = _mock_model(plan_json)

        result = planner.replan(
            goal="g",
            completed_steps=[{"step": 1, "tool": "web_search"}],
            failed_step={"tool": "file_controller", "description": "write file"},
            error="disk full",
        )

        self.assertEqual(result["steps"][0]["tool"], "file_controller")

    @patch("actions.genai_client.get_model")
    def test_generated_code_replaced_in_replan(self, get_model):
        plan_json = json.dumps({
            "goal": "g",
            "steps": [{"step": 1, "tool": "generated_code", "description": "hack it",
                       "parameters": {}, "critical": False}]
        })
        get_model.return_value = _mock_model(plan_json)

        result = planner.replan("g", [], {"tool": "x", "description": "y"}, "err")

        self.assertEqual(result["steps"][0]["tool"], "web_search")

    @patch("actions.genai_client.get_model")
    def test_exception_falls_back(self, get_model):
        model = MagicMock()
        model.generate_content.side_effect = RuntimeError("boom")
        get_model.return_value = model

        result = planner.replan("g", [], {"tool": "x", "description": "y"}, "err")

        self.assertEqual(result["steps"][0]["tool"], "web_search")


class FallbackPlanTests(unittest.TestCase):
    def test_fallback_plan_structure(self):
        plan = planner._fallback_plan("find my keys")

        self.assertEqual(plan["goal"], "find my keys")
        self.assertEqual(len(plan["steps"]), 1)
        step = plan["steps"][0]
        self.assertEqual(step["tool"], "web_search")
        self.assertEqual(step["parameters"]["query"], "find my keys")
        self.assertTrue(step["critical"])


if __name__ == "__main__":
    unittest.main()
