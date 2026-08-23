import threading
import unittest

from actions.playback_controller import (
    PlaybackController,
    command_result,
    parameter_value,
)


class PlaybackControllerTests(unittest.TestCase):
    def test_commands_are_executed_in_submission_order_without_overlap(self):
        first_started = threading.Event()
        release_first = threading.Event()
        calls = []
        active = 0
        max_active = 0
        lock = threading.Lock()

        def dispatch(action, _params):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            calls.append(action)
            if action == "first":
                first_started.set()
                self.assertTrue(release_first.wait(2))
            with lock:
                active -= 1
            return True

        controller = PlaybackController(dispatch)
        try:
            first = controller.submit("first")
            self.assertTrue(first_started.wait(1))
            second = controller.submit("second")
            self.assertFalse(second.done())
            release_first.set()

            self.assertTrue(first.result(1).ok)
            self.assertTrue(second.result(1).ok)
        finally:
            controller.close()

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(max_active, 1)

    def test_exceptions_become_typed_failures_and_reach_result_callback(self):
        received = []

        def dispatch(_action, _params):
            raise RuntimeError("backend unavailable")

        controller = PlaybackController(dispatch, on_result=received.append)
        try:
            result = controller.submit("play").result(1)
        finally:
            controller.close()

        self.assertFalse(result.ok)
        self.assertEqual(result.action, "play")
        self.assertIn("backend unavailable", result.message)
        self.assertEqual(received, [result])

    def test_parameter_value_preserves_zero(self):
        self.assertEqual(parameter_value({"level": 0, "volume": 75}, "level", "volume"), 0)

    def test_backend_empty_playlist_message_is_a_failure(self):
        result = command_result("play_tracks", "La lista está vacía.")

        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
