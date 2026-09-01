import unittest

import numpy as np

from actions.emulator_frame import frame_to_bgr
from actions.libretro import PIXEL_0RGB1555, PIXEL_RGB565, PIXEL_XRGB8888


class FrameToBgrTests(unittest.TestCase):
    def test_returns_none_without_data(self):
        self.assertIsNone(frame_to_bgr(None, 4, 4, 16, PIXEL_RGB565))
        self.assertIsNone(frame_to_bgr(b"\x00" * 32, 0, 4, 16, PIXEL_RGB565))
        self.assertIsNone(frame_to_bgr(b"\x00" * 32, 4, 0, 16, PIXEL_RGB565))

    def test_xrgb8888_drops_alpha_and_keeps_bgr_order(self):
        # One 2x1 frame: pixel 0 pure red, pixel 1 pure green — memory order
        # is B,G,R,X per actions/libretro.py's _GL_BGRA note.
        pixel_red = bytes([0x00, 0x00, 0xFF, 0x00])
        pixel_green = bytes([0x00, 0xFF, 0x00, 0x00])
        data = pixel_red + pixel_green
        out = frame_to_bgr(data, width=2, height=1, pitch=8, pixel_format=PIXEL_XRGB8888)
        self.assertEqual(out.shape, (1, 2, 3))
        np.testing.assert_array_equal(out[0, 0], [0, 0, 255])   # B,G,R = red
        np.testing.assert_array_equal(out[0, 1], [0, 255, 0])   # B,G,R = green

    def test_xrgb8888_respects_pitch_wider_than_row(self):
        # pitch covers 3 pixels' worth of bytes but only 2 are wanted —
        # mirrors a core whose framebuffer stride exceeds the visible width.
        red = bytes([0x00, 0x00, 0xFF, 0x00])
        pad = bytes([0xAA, 0xAA, 0xAA, 0xAA])
        data = (red + red + pad) * 2
        out = frame_to_bgr(data, width=2, height=2, pitch=12, pixel_format=PIXEL_XRGB8888)
        self.assertEqual(out.shape, (2, 2, 3))
        np.testing.assert_array_equal(out[0, 1], [0, 0, 255])
        np.testing.assert_array_equal(out[1, 1], [0, 0, 255])

    def test_rgb565_decodes_pure_colours(self):
        # RGB565: 5 bits R, 6 bits G, 5 bits B, little-endian uint16.
        red = (0x1F << 11).to_bytes(2, "little")
        green = (0x3F << 5).to_bytes(2, "little")
        blue = (0x1F).to_bytes(2, "little")
        data = red + green + blue
        out = frame_to_bgr(data, width=3, height=1, pitch=6, pixel_format=PIXEL_RGB565)
        self.assertEqual(out.shape, (1, 3, 3))
        np.testing.assert_array_equal(out[0, 0], [0, 0, 248])    # red   -> B,G,R
        np.testing.assert_array_equal(out[0, 1], [0, 252, 0])    # green
        np.testing.assert_array_equal(out[0, 2], [248, 0, 0])    # blue

    def test_0rgb1555_decodes_pure_colours(self):
        red = (0x1F << 10).to_bytes(2, "little")
        green = (0x1F << 5).to_bytes(2, "little")
        blue = (0x1F).to_bytes(2, "little")
        data = red + green + blue
        out = frame_to_bgr(data, width=3, height=1, pitch=6, pixel_format=PIXEL_0RGB1555)
        self.assertEqual(out.shape, (1, 3, 3))
        np.testing.assert_array_equal(out[0, 0], [0, 0, 248])
        np.testing.assert_array_equal(out[0, 1], [0, 248, 0])
        np.testing.assert_array_equal(out[0, 2], [248, 0, 0])

    def test_unknown_pixel_format_falls_back_to_rgb565(self):
        # Matches ui/widgets/retro.py's own _build_image, whose else branch
        # treats anything that isn't XRGB8888/0RGB1555 as RGB565.
        red565 = (0x1F << 11).to_bytes(2, "little")
        out_known = frame_to_bgr(red565, 1, 1, 2, PIXEL_RGB565)
        out_unknown = frame_to_bgr(red565, 1, 1, 2, pixel_format=99)
        np.testing.assert_array_equal(out_known, out_unknown)


if __name__ == "__main__":
    unittest.main()
