import unittest
from unittest.mock import Mock, patch

from actions import whatsapp_bridge_process


class WhatsAppBridgeProcessTests(unittest.TestCase):
    def tearDown(self):
        whatsapp_bridge_process._process = None
        whatsapp_bridge_process._job_handle = None

    @patch("actions.whatsapp_bridge_process.bridge_running", return_value=True)
    @patch("actions.whatsapp_bridge_process.subprocess.Popen")
    def test_does_not_start_duplicate_bridge(self, popen, _running):
        self.assertTrue(whatsapp_bridge_process.start_bridge())
        popen.assert_not_called()

    @patch("actions.whatsapp_bridge_process._attach_windows_job")
    @patch("actions.whatsapp_bridge_process.time.sleep")
    @patch(
        "actions.whatsapp_bridge_process.bridge_running",
        side_effect=[False, True],
    )
    @patch("actions.whatsapp_bridge_process.shutil.which", return_value="node")
    @patch("actions.whatsapp_bridge_process.subprocess.Popen")
    def test_starts_bridge_as_child(
        self, popen, _which, _running, _sleep, attach_job,
    ):
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process

        self.assertTrue(whatsapp_bridge_process.start_bridge())
        popen.assert_called_once()
        attach_job.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
