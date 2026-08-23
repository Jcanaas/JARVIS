import unittest
from unittest.mock import patch

from actions import input_config
from actions import emulator_runtime
from actions.libretro import (
    ANALOG_ID_X,
    ANALOG_ID_Y,
    ANALOG_INDEX_LEFT,
    DEVICE_ANALOG,
    DEVICE_JOYPAD,
    JOYPAD,
    JOYPAD_MASK,
    LibretroCore,
)


class InputProfileTests(unittest.TestCase):
    def test_each_console_exposes_its_own_controls(self):
        gba = dict(input_config.button_order("gba"))
        snes = dict(input_config.button_order("snes"))
        ps2 = dict(input_config.button_order("ps2"))

        self.assertNotIn("x", gba)
        self.assertEqual(snes["x"], "X")
        self.assertEqual(ps2["b"], "Cruz")
        self.assertEqual(ps2["a"], "Círculo")
        self.assertEqual(ps2["y"], "Cuadrado")
        self.assertEqual(ps2["x"], "Triángulo")
        self.assertIn("l2", ps2)
        self.assertIn("r3", ps2)
        self.assertNotIn("Cruz", dict(input_config.button_order("n64")).values())

    def test_ps2_analogue_stick_is_not_also_bound_to_the_dpad(self):
        ps2 = input_config.defaults("ps2")
        for direction in ("up", "down", "left", "right"):
            self.assertFalse(any(signal.startswith("xinput:ls")
                                 for signal in ps2.pad[direction]))

    def test_ps2_uses_the_hardware_renderer(self):
        self.assertEqual(
            emulator_runtime.CORES["ps2"].options["pcsx2_renderer"],
            "OpenGL",
        )

    def test_saved_profiles_do_not_leak_between_consoles(self):
        storage = {}

        def get_value(key):
            return storage.get(key)

        def set_value(key, value):
            storage[key] = value

        with patch.object(input_config.app_settings, "get", side_effect=get_value), \
                patch.object(input_config.app_settings, "set", side_effect=set_value):
            gba = input_config.load("gba")
            gba.bind_key("a", 12345)
            input_config.save(gba, "gba")

            self.assertEqual(input_config.load("gba").keyboard["a"], [12345])
            self.assertNotEqual(input_config.load("ps2").keyboard["a"], [12345])

    def test_legacy_global_binding_is_migrated_only_to_gba(self):
        legacy = {
            "keyboard": {"a": [222]},
            "pad": {"a": ["xinput:y"]},
        }
        with patch.object(input_config.app_settings, "get", return_value=legacy):
            self.assertEqual(input_config.load("gba").keyboard["a"], [222])
            self.assertNotEqual(input_config.load("ps2").keyboard["a"], [222])


class LibretroInputTests(unittest.TestCase):
    def _core(self):
        core = LibretroCore.__new__(LibretroCore)
        core._pressed = set()
        core._axes = {}
        return core

    def test_joypad_mask_contains_every_pressed_button(self):
        core = self._core()
        core.set_button("b", True)
        core.set_button("start", True)

        mask = core._on_input_state(0, DEVICE_JOYPAD, 0, JOYPAD_MASK)

        self.assertEqual(mask & (1 << JOYPAD["b"]), 1 << JOYPAD["b"])
        self.assertEqual(mask & (1 << JOYPAD["start"]), 1 << JOYPAD["start"])

    def test_analog_sticks_are_returned_to_the_core(self):
        core = self._core()
        core.set_axis(ANALOG_INDEX_LEFT, ANALOG_ID_X, 12345)
        core.set_axis(ANALOG_INDEX_LEFT, ANALOG_ID_Y, -23456)

        self.assertEqual(
            core._on_input_state(0, DEVICE_ANALOG, ANALOG_INDEX_LEFT, ANALOG_ID_X),
            12345,
        )
        self.assertEqual(
            core._on_input_state(0, DEVICE_ANALOG, ANALOG_INDEX_LEFT, ANALOG_ID_Y),
            -23456,
        )


if __name__ == "__main__":
    unittest.main()
