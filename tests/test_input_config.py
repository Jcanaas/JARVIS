import unittest

from actions import input_config


class QtJsKeyTranslationTests(unittest.TestCase):
    """qt_key_from_js_code()/js_code_from_qt_key() are the only place that
    translates between browser KeyboardEvent.code strings and the Qt key
    codes actions/input_config.py's Bindings already store — so the web
    rebind UI (Fase B) shares one preference store with --legacy instead of
    forking a second, web-only one."""

    def test_every_key_the_default_web_mapping_uses_round_trips(self):
        # Matches webui/src/panels/Emulators.svelte's KEY_MAP exactly — if
        # any of these stopped translating, the default web controls would
        # silently stop matching --legacy's.
        codes = [
            "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
            "KeyX", "Space", "KeyZ", "KeyS", "KeyA", "KeyQ", "KeyW",
            "Digit1", "Digit2", "Digit3", "Digit4",
            "Enter", "NumpadEnter", "Backspace", "ShiftLeft", "ShiftRight",
        ]
        for code in codes:
            with self.subTest(code=code):
                qt_key = input_config.qt_key_from_js_code(code)
                self.assertIsNotNone(qt_key, f"{code} did not translate")
                self.assertIsInstance(qt_key, int)

    def test_named_keys_round_trip_exactly(self):
        for code in ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
                     "Enter", "Backspace", "Space"):
            qt_key = input_config.qt_key_from_js_code(code)
            self.assertEqual(input_config.js_code_from_qt_key(qt_key), code)

    def test_shift_left_and_right_collapse_to_the_same_qt_key(self):
        # Both are legitimate ways to press "Shift" — Qt has no left/right
        # distinction for this binding, so both must resolve to one value.
        left = input_config.qt_key_from_js_code("ShiftLeft")
        right = input_config.qt_key_from_js_code("ShiftRight")
        self.assertEqual(left, right)
        # The reverse mapping has to pick one canonical answer, not error
        # or return something unstable.
        self.assertEqual(input_config.js_code_from_qt_key(left), "ShiftLeft")

    def test_letters_and_digits_generalize_beyond_the_default_set(self):
        # qt_key_from_js_code must work for ANY letter/digit key, not just
        # the ones _BASE_KEYBOARD happens to use — a user rebinding to "P"
        # or "9" must work even though nothing in this module's defaults
        # ever mentions them.
        self.assertEqual(input_config.qt_key_from_js_code("KeyP"), ord("P"))
        self.assertEqual(input_config.qt_key_from_js_code("Digit9"), ord("9"))
        self.assertEqual(input_config.js_code_from_qt_key(ord("P")), "KeyP")
        self.assertEqual(input_config.js_code_from_qt_key(ord("9")), "Digit9")

    def test_untranslatable_keys_return_none_not_garbage(self):
        for code in ("MetaLeft", "AltRight", "ControlLeft", "", "NotAKey"):
            self.assertIsNone(input_config.qt_key_from_js_code(code))

    def test_qt_only_keys_have_no_js_equivalent(self):
        # A key legacy could bind (e.g. F5, Qt.Key_F5 = 0x01000034) that
        # this module never taught the web side about must not crash or
        # silently invent a fake code — the UI has to show "sin equivalente"
        # rather than a wrong label.
        self.assertIsNone(input_config.js_code_from_qt_key(0x01000034))


class BindKeyUsesTranslatableCodesTests(unittest.TestCase):
    """End-to-end through Bindings.bind_key(), the way the /api/emulator/
    controls/bind route (Fase B) will actually call this."""

    def test_bind_key_with_a_translated_code_is_readable_back(self):
        bindings = input_config.defaults("gba")
        qt_key = input_config.qt_key_from_js_code("KeyP")
        bindings.bind_key("a", qt_key)
        self.assertIn(qt_key, bindings.keyboard["a"])
        self.assertEqual(input_config.key_label(qt_key), "P")


if __name__ == "__main__":
    unittest.main()
