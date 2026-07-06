import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from agent import executor
from agent.error_handler import ErrorDecision


class CallToolTests(unittest.TestCase):
    @patch("actions.web_search.web_search")
    def test_dispatches_known_tool_with_parameters(self, web_search):
        web_search.return_value = "3 results found"

        result = executor._call_tool("web_search", {"query": "cats"}, speak=None)

        web_search.assert_called_once_with(parameters={"query": "cats"})
        self.assertEqual(result, "3 results found")

    @patch("actions.web_search.web_search")
    def test_falsy_result_becomes_done(self, web_search):
        web_search.return_value = ""

        result = executor._call_tool("web_search", {"query": "cats"}, speak=None)

        self.assertEqual(result, "Done.")

    def test_generated_code_without_description_raises(self):
        with self.assertRaises(ValueError):
            executor._call_tool("generated_code", {}, speak=None)

    @patch("agent.executor._run_generated_code")
    def test_generated_code_with_description_delegates(self, run_generated):
        run_generated.return_value = "42"

        result = executor._call_tool(
            "generated_code", {"description": "compute the answer"}, speak=None
        )

        run_generated.assert_called_once_with("compute the answer", speak=None)
        self.assertEqual(result, "42")

    @patch("agent.executor._run_generated_code")
    def test_unknown_tool_falls_back_to_generated_code(self, run_generated):
        run_generated.return_value = "fallback result"

        result = executor._call_tool("some_unknown_tool", {"a": 1}, speak=None)

        run_generated.assert_called_once()
        self.assertIn("Accomplish this task", run_generated.call_args[0][0])
        self.assertEqual(result, "fallback result")

    @patch("actions.screen_processor.screen_process")
    def test_screen_process_returns_fixed_message(self, screen_process):
        result = executor._call_tool("screen_process", {"text": "what is this?"}, speak=None)

        screen_process.assert_called_once_with(parameters={"text": "what is this?"})
        self.assertEqual(result, "Screen captured and analyzed.")


class InjectContextTests(unittest.TestCase):
    def test_empty_step_results_returns_params_unchanged(self):
        params = {"action": "write", "path": "desktop"}

        result = executor._inject_context(params, "file_controller", {}, goal="g")

        self.assertEqual(result, params)

    def test_non_file_controller_tool_is_untouched(self):
        params = {"query": "cats"}
        step_results = {1: "a" * 200}

        result = executor._inject_context(params, "web_search", step_results, goal="g")

        self.assertEqual(result, params)

    def test_write_action_with_sufficient_content_is_untouched(self):
        long_content = "x" * 60
        params = {"action": "write", "content": long_content}
        step_results = {1: "a" * 200}

        result = executor._inject_context(params, "file_controller", step_results, goal="g")

        self.assertEqual(result["content"], long_content)

    @patch("agent.executor._translate_to_goal_language")
    def test_write_action_with_short_content_injects_prior_results(self, translate):
        translate.return_value = "TRANSLATED"
        params = {"action": "write", "content": ""}
        step_results = {1: "a" * 200, 2: "Done."}

        result = executor._inject_context(params, "file_controller", step_results, goal="g")

        translate.assert_called_once()
        self.assertEqual(result["content"], "TRANSLATED")

    def test_only_trivial_results_available_leaves_content_unchanged(self):
        params = {"action": "create_file", "content": ""}
        step_results = {1: "Done.", 2: "Completed.", 3: "short"}

        result = executor._inject_context(params, "file_controller", step_results, goal="g")

        self.assertEqual(result["content"], "")


class AgentExecutorTests(unittest.TestCase):
    def test_execute_returns_message_when_plan_has_no_steps(self):
        with patch("agent.executor.create_plan", return_value={"goal": "g", "steps": []}):
            speak = MagicMock()
            result = executor.AgentExecutor().execute("g", speak=speak)

        self.assertIn("couldn't create a valid plan", result)
        speak.assert_called_once()

    def test_execute_returns_cancelled_when_flag_already_set(self):
        plan = {"goal": "g", "steps": [
            {"step": 1, "tool": "web_search", "description": "d", "parameters": {}}
        ]}
        cancel_flag = threading.Event()
        cancel_flag.set()

        with patch("agent.executor.create_plan", return_value=plan):
            result = executor.AgentExecutor().execute("g", cancel_flag=cancel_flag)

        self.assertEqual(result, "Task cancelled.")

    def test_execute_calls_summarize_on_full_success(self):
        plan = {"goal": "g", "steps": [
            {"step": 1, "tool": "web_search", "description": "d", "parameters": {}}
        ]}

        with patch("agent.executor.create_plan", return_value=plan), \
             patch("agent.executor._call_tool", return_value="ok") as call_tool, \
             patch.object(executor.AgentExecutor, "_summarize", return_value="All done, sir.") as summarize:
            result = executor.AgentExecutor().execute("g")

        call_tool.assert_called_once()
        summarize.assert_called_once()
        self.assertEqual(result, "All done, sir.")

    def test_execute_retries_on_retry_decision_then_succeeds(self):
        plan = {"goal": "g", "steps": [
            {"step": 1, "tool": "web_search", "description": "d", "parameters": {}}
        ]}
        recovery = {"decision": ErrorDecision.RETRY, "user_message": "Retrying, sir."}

        with patch("agent.executor.create_plan", return_value=plan), \
             patch("agent.executor._call_tool", side_effect=[RuntimeError("boom"), "ok"]) as call_tool, \
             patch("agent.executor.analyze_error", return_value=recovery), \
             patch("time.sleep"), \
             patch.object(executor.AgentExecutor, "_summarize", return_value="done"):
            result = executor.AgentExecutor().execute("g")

        self.assertEqual(call_tool.call_count, 2)
        self.assertEqual(result, "done")

    def test_execute_skip_decision_marks_step_done(self):
        plan = {"goal": "g", "steps": [
            {"step": 1, "tool": "web_search", "description": "d", "parameters": {}}
        ]}
        recovery = {"decision": ErrorDecision.SKIP, "user_message": "Skipping, sir."}

        with patch("agent.executor.create_plan", return_value=plan), \
             patch("agent.executor._call_tool", side_effect=RuntimeError("boom")), \
             patch("agent.executor.analyze_error", return_value=recovery), \
             patch.object(executor.AgentExecutor, "_summarize", return_value="done"):
            result = executor.AgentExecutor().execute("g")

        self.assertEqual(result, "done")

    def test_execute_abort_decision_returns_immediately(self):
        plan = {"goal": "g", "steps": [
            {"step": 1, "tool": "web_search", "description": "d", "parameters": {}}
        ]}
        recovery = {"decision": ErrorDecision.ABORT, "reason": "unsafe", "user_message": "Stopping, sir."}

        with patch("agent.executor.create_plan", return_value=plan), \
             patch("agent.executor._call_tool", side_effect=RuntimeError("boom")), \
             patch("agent.executor.analyze_error", return_value=recovery):
            speak = MagicMock()
            result = executor.AgentExecutor().execute("g", speak=speak)

        self.assertIn("Task aborted", result)
        self.assertIn("unsafe", result)

    def test_execute_exhausts_replan_attempts_and_fails(self):
        plan = {"goal": "g", "steps": [
            {"step": 1, "tool": "web_search", "description": "d", "parameters": {}}
        ]}
        recovery = {"decision": ErrorDecision.REPLAN, "reason": "wrong approach", "fix_suggestion": ""}

        with patch("agent.executor.create_plan", return_value=plan), \
             patch("agent.executor.replan", return_value=plan) as replan_fn, \
             patch("agent.executor._call_tool", side_effect=RuntimeError("boom")), \
             patch("agent.executor.analyze_error", return_value=recovery):
            speak = MagicMock()
            result = executor.AgentExecutor().execute("g", speak=speak)

        self.assertIn("Task failed after", result)
        self.assertEqual(replan_fn.call_count, executor.AgentExecutor.MAX_REPLAN_ATTEMPTS)

    def test_execute_fix_suggestion_recovers_without_replanning(self):
        plan = {"goal": "g", "steps": [
            {"step": 1, "tool": "code_helper", "description": "d", "parameters": {}}
        ]}
        recovery = {
            "decision": ErrorDecision.REPLAN,
            "reason": "wrong tool",
            "fix_suggestion": "try a different script",
        }
        fixed_step = {
            "tool": "generated_code",
            "parameters": {"description": "fixed version"},
        }

        with patch("agent.executor.create_plan", return_value=plan), \
             patch("agent.executor.replan") as replan_fn, \
             patch("agent.executor._call_tool", side_effect=[RuntimeError("boom"), "fixed ok"]), \
             patch("agent.executor.analyze_error", return_value=recovery), \
             patch("agent.executor.generate_fix", return_value=fixed_step), \
             patch.object(executor.AgentExecutor, "_summarize", return_value="done"):
            result = executor.AgentExecutor().execute("g")

        replan_fn.assert_not_called()
        self.assertEqual(result, "done")


class SummarizeTests(unittest.TestCase):
    @patch("actions.genai_client.get_model")
    def test_returns_model_generated_summary(self, get_model):
        model = MagicMock()
        model.generate_content.return_value = MagicMock(text="Task complete, sir.")
        get_model.return_value = model

        result = executor.AgentExecutor()._summarize("goal", [{"description": "did x"}], speak=None)

        self.assertEqual(result, "Task complete, sir.")

    @patch("actions.genai_client.get_model")
    def test_falls_back_on_exception(self, get_model):
        get_model.side_effect = RuntimeError("api down")

        result = executor.AgentExecutor()._summarize("my goal", [{"description": "a"}, {"description": "b"}], speak=None)

        self.assertIn("Completed 2 steps", result)
        self.assertIn("my goal", result)


if __name__ == "__main__":
    unittest.main()
