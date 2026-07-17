import unittest
from unittest.mock import patch

import main


class AutomaticMicrophoneSelectionTests(unittest.TestCase):
    HFP_DEVICE_NAMES = (
        "Bluetooth Microphone",
        "Headphones (Example Hands-Free)",
        "Headset Microphone",
        "Headphones (Example AG Audio)",
        "Auriculares (Ejemplo Manos libres)",
    )

    @staticmethod
    def _input(name):
        return {"name": name, "max_input_channels": 1}

    @patch("sounddevice.query_devices")
    def test_auto_rejects_each_bluetooth_hfp_name_marker(self, query_devices):
        safe_mic = self._input("USB Desktop Microphone")

        for hfp_name in self.HFP_DEVICE_NAMES:
            with self.subTest(hfp_name=hfp_name):
                query_devices.return_value = [self._input(hfp_name), safe_mic]

                self.assertEqual(main._pick_mic_device(), 1)

    @patch("sounddevice.query_devices")
    def test_auto_returns_none_when_only_hfp_inputs_exist(self, query_devices):
        query_devices.return_value = [
            self._input(name) for name in self.HFP_DEVICE_NAMES
        ]

        self.assertIsNone(main._pick_mic_device())

    @patch("sounddevice.query_devices")
    def test_auto_prefers_internal_microphone_when_available(self, query_devices):
        query_devices.return_value = [
            self._input("Headset Microphone (Hands-Free AG Audio)"),
            self._input("USB Podcast Microphone"),
            self._input("Microphone Array (Realtek Audio)"),
        ]

        self.assertEqual(main._pick_mic_device(), 2)


if __name__ == "__main__":
    unittest.main()
