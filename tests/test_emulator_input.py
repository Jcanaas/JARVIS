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

    def test_3ds_exposes_face_shoulders_and_dpad_not_gbas_fallback_set(self):
        # button_order() used to silently fall back to GBA's set for any
        # console missing from _BUTTON_ORDERS — 3ds was missing, so its
        # controls dialog showed GBA's buttons (no X/Y, wrong shoulder
        # labels, no L2/R2/L3/R3-shaped layout) instead of its own.
        threeds = dict(input_config.button_order("3ds"))
        for button in ("up", "down", "left", "right", "a", "b", "x", "y", "l", "r", "start", "select"):
            self.assertIn(button, threeds)
        self.assertNotIn("l2", threeds)  # emulator_runtime.pad_layout("3ds")'s triggers=False

    def test_3ds_circle_pad_keys_are_freed_from_button_bindings(self):
        # Same treatment as n64/ps2: W/A/S/D drive the circle pad directly
        # (Emulators.svelte's updateKeyboardStick(), mirroring
        # ui/widgets/retro.py's _STICK_KEYS) and must not also be bound to a
        # button, or a movement key would fire that button on every step.
        threeds = input_config.defaults("3ds")
        for button in ("y", "x", "r"):
            self.assertNotIn(0x57, threeds.keyboard[button])  # Qt.Key_W
            self.assertNotIn(0x41, threeds.keyboard[button])  # Qt.Key_A
            self.assertNotIn(0x53, threeds.keyboard[button])  # Qt.Key_S
            self.assertNotIn(0x44, threeds.keyboard[button])  # Qt.Key_D
        for direction in ("up", "down", "left", "right"):
            self.assertFalse(any(signal.startswith("xinput:ls")
                                 for signal in threeds.pad[direction]))

    def test_ps2_uses_the_hardware_renderer(self):
        self.assertEqual(
            emulator_runtime.CORES["ps2"].options["pcsx2_renderer"],
            "OpenGL",
        )

    def test_ps2_upscale_multiplier_is_2x_not_native(self):
        # Bumped from the original "1x Native (PS2)" once the desktop/mobile
        # UIs started stretching the native frame to fill real screen space
        # instead of showing it near-native-sized — measured directly:
        # 60.0fps sustained through BIOS/menus/30s of real gameplay at 2x,
        # not just an assumption that there was headroom.
        self.assertEqual(
            emulator_runtime.CORES["ps2"].options["pcsx2_upscale_multiplier"],
            "2x Native (~720p)",
        )

    def test_3ds_resolution_factor_is_3_not_the_cores_native_default(self):
        # The core's own default is "1" (never overridden before) — measured
        # directly: 3 sustains 59.3fps, 4 drops to 50.4fps (3DS targets
        # 60fps), so 3 is the highest factor still full-speed here.
        self.assertEqual(
            emulator_runtime.CORES["3ds"].options["citra_resolution_factor"],
            "3",
        )

    def test_resolution_option_reports_choices_and_default(self):
        with patch("actions.app_settings.get", return_value={}):
            opt = emulator_runtime.resolution_option("ps2")
        self.assertEqual(opt["key"], "pcsx2_upscale_multiplier")
        self.assertIn("2x Native (~720p)", opt["choices"])
        self.assertEqual(opt["current"], "2x Native (~720p)")  # CORES's own default

    def test_resolution_option_is_none_for_a_console_without_one(self):
        self.assertIsNone(emulator_runtime.resolution_option("gba"))

    def test_set_resolution_persists_and_is_then_reported_as_current(self):
        storage = {}

        def fake_get(key, default=None):
            return storage.get(key, default)

        def fake_set(key, value):
            storage[key] = value

        with patch("actions.app_settings.get", side_effect=fake_get), \
                patch("actions.app_settings.set", side_effect=fake_set):
            emulator_runtime.set_resolution("3ds", "2")
            self.assertEqual(storage["emulator_resolution"], {"3ds": "2"})
            opt = emulator_runtime.resolution_option("3ds")
        self.assertEqual(opt["current"], "2")

    def test_set_resolution_rejects_an_unknown_console(self):
        with self.assertRaises(ValueError):
            emulator_runtime.set_resolution("gba", "2")

    def test_set_resolution_rejects_a_value_the_core_never_declared(self):
        with self.assertRaises(ValueError):
            emulator_runtime.set_resolution("3ds", "999")

    def test_resolved_options_merges_the_saved_choice_into_cores_options(self):
        with patch("actions.app_settings.get", return_value={"3ds": "6"}):
            options = emulator_runtime.resolved_options("3ds")
        self.assertEqual(options["citra_resolution_factor"], "6")
        # Everything else from CORES stays untouched.
        self.assertEqual(options["citra_graphics_api"], "OpenGL")

    def test_resolved_options_falls_back_to_cores_default_with_no_saved_choice(self):
        with patch("actions.app_settings.get", return_value={}):
            options = emulator_runtime.resolved_options("3ds")
        self.assertEqual(options["citra_resolution_factor"], "3")

    def test_resolved_options_ignores_a_saved_choice_for_a_different_console(self):
        with patch("actions.app_settings.get", return_value={"ps2": "3x Native (~1080p)"}):
            options = emulator_runtime.resolved_options("3ds")
        self.assertEqual(options["citra_resolution_factor"], "3")  # CORES's own default, not ps2's

    def test_3ds_uses_opengl_not_vulkan(self):
        # Vulkan+RADV crashes immediately on AMD GPUs on Arch Linux — exactly
        # this machine's iGPU (see NOTE_webui_emulators_plan.md's Fase 6).
        self.assertEqual(
            emulator_runtime.CORES["3ds"].options["citra_graphics_api"],
            "OpenGL",
        )

    def test_3ds_pad_has_one_stick_and_no_catalog(self):
        layout = emulator_runtime.pad_layout("3ds")
        self.assertEqual(layout["sticks"], 1)
        self.assertTrue(layout["shoulders"])
        # New3DS's C-stick/ZL/ZR aren't wired up — same MVP scope as touch.
        self.assertFalse(layout["triggers"])

        from actions import rom_catalog
        console = rom_catalog.CONSOLES["3ds"]
        self.assertFalse(console.has_catalog)
        self.assertIn(".3ds", console.rom_exts)

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
