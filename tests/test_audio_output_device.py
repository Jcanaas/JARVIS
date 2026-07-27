import unittest
from unittest.mock import Mock, call, patch

from actions import app_settings
from actions import ytmusic_headless


class AudioOutputDeviceTests(unittest.TestCase):
    HEADSET = "wasapi/{00000000-0000-0000-0000-000000000001}"
    SPEAKERS = "wasapi/{00000000-0000-0000-0000-000000000002}"

    def setUp(self):
        self._old_audio_device = ytmusic_headless._audio_device
        self._old_procs = list(ytmusic_headless._procs)
        ytmusic_headless._audio_device = ""
        ytmusic_headless._procs[:] = [None] * len(ytmusic_headless._PIPE_PATHS)

    def tearDown(self):
        ytmusic_headless._audio_device = self._old_audio_device
        ytmusic_headless._procs[:] = self._old_procs

    @staticmethod
    def _live_process():
        process = Mock()
        process.poll.return_value = None
        return process

    @classmethod
    def _enumerated_devices(cls):
        return [
            {"name": "", "description": "Automatic"},
            {"name": cls.HEADSET, "description": "Bluetooth headset"},
        ]

    def test_unknown_guid_is_applied_as_auto_to_a_live_player_and_persisted(self):
        ytmusic_headless._audio_device = self.HEADSET
        ytmusic_headless._procs[0] = self._live_process()
        with (
            patch.object(
                ytmusic_headless,
                "list_audio_output_devices",
                return_value=self._enumerated_devices(),
            ),
            patch.object(
                ytmusic_headless,
                "_ipc_request",
                return_value={"error": "success"},
            ) as ipc_request,
            patch.object(app_settings, "set") as save_setting,
        ):
            ytmusic_headless.set_audio_output_device(self.SPEAKERS)

        self.assertEqual(ytmusic_headless._audio_device, "")
        save_setting.assert_called_once_with("audio_output_device", "")
        pipe = ytmusic_headless._PIPE_PATHS[0]
        self.assertEqual(
            ipc_request.call_args_list,
            [
                call(["set_property", "audio-device", "auto"], pipe=pipe),
                call(["ao-reload"], pipe=pipe),
            ],
        )

    def test_selecting_auto_does_not_spawn_device_enumeration(self):
        ytmusic_headless._procs[0] = self._live_process()
        with (
            patch.object(ytmusic_headless, "list_audio_output_devices") as list_devices,
            patch.object(
                ytmusic_headless,
                "_ipc_request",
                return_value={"error": "success"},
            ) as ipc_request,
            patch.object(app_settings, "set"),
        ):
            ytmusic_headless.set_audio_output_device("")

        list_devices.assert_not_called()
        ipc_request.assert_called_once_with(
            ["set_property", "audio-device", "auto"],
            pipe=ytmusic_headless._PIPE_PATHS[0],
        )

    def test_valid_guid_uses_confirmed_ipc_for_every_live_slot(self):
        processes = [self._live_process() for _ in ytmusic_headless._PIPE_PATHS]
        ytmusic_headless._procs[:] = processes

        with (
            patch.object(
                ytmusic_headless,
                "list_audio_output_devices",
                return_value=self._enumerated_devices(),
            ),
            patch.object(
                ytmusic_headless,
                "_ipc_request",
                return_value={"error": "success"},
            ) as ipc_request,
            patch.object(ytmusic_headless, "_send_command") as send_command,
            patch.object(app_settings, "set"),
        ):
            ytmusic_headless.set_audio_output_device(self.HEADSET)

        expected = [
            call(
                ["set_property", "audio-device", self.HEADSET],
                pipe=pipe,
            )
            for pipe in ytmusic_headless._PIPE_PATHS
        ]
        self.assertEqual(ipc_request.call_args_list, expected)
        send_command.assert_not_called()
        self.assertEqual(ytmusic_headless._audio_device, self.HEADSET)

    def test_ipc_error_falls_back_to_auto_reopens_ao_and_sanitizes_setting(self):
        ytmusic_headless._procs[0] = self._live_process()

        with (
            patch.object(
                ytmusic_headless,
                "list_audio_output_devices",
                return_value=self._enumerated_devices(),
            ),
            patch.object(
                ytmusic_headless,
                "_ipc_request",
                side_effect=[
                    {"error": "audio-device unavailable"},
                    {"error": "success"},
                    {"error": "success"},
                ],
            ) as ipc_request,
            patch.object(ytmusic_headless, "_send_command") as send_command,
            patch.object(app_settings, "set") as save_setting,
        ):
            ytmusic_headless.set_audio_output_device(self.HEADSET)

        pipe = ytmusic_headless._PIPE_PATHS[0]
        self.assertEqual(
            ipc_request.call_args_list,
            [
                call(
                    ["set_property", "audio-device", self.HEADSET],
                    pipe=pipe,
                ),
                call(["set_property", "audio-device", "auto"], pipe=pipe),
                call(["ao-reload"], pipe=pipe),
            ],
        )
        send_command.assert_not_called()
        self.assertEqual(ytmusic_headless._audio_device, "")
        save_setting.assert_called_with("audio_output_device", "")

    def test_monitor_falls_back_only_once_when_active_guid_disappears(self):
        ytmusic_headless._audio_device = self.HEADSET
        device_list = [
            {"name": "auto", "description": "Autoselect device"},
            {"name": self.SPEAKERS, "description": "Built-in speakers"},
        ]

        def apply_device(name=""):
            ytmusic_headless._audio_device = str(name or "").strip()
            return "Salida de audio: auto."

        with patch.object(
            ytmusic_headless,
            "set_audio_output_device",
            side_effect=apply_device,
        ) as set_device:
            first = ytmusic_headless._reconcile_audio_output_device_list(device_list)
            second = ytmusic_headless._reconcile_audio_output_device_list(device_list)

        self.assertTrue(first)
        self.assertFalse(second)
        set_device.assert_called_once_with("")


if __name__ == "__main__":
    unittest.main()
