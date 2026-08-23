import ctypes as ct
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from actions.libretro import LibretroCore


class _FakeLibretroLibrary:
    def __init__(self):
        self.game_info = None
        self.controller = None

    def retro_load_game(self, info_pointer):
        info = info_pointer._obj
        self.game_info = {
            "path": info.path,
            "data": info.data,
            "size": info.size,
        }
        return True

    def retro_set_controller_port_device(self, port, device):
        self.controller = (port, device)


class LibretroLoadGameTests(unittest.TestCase):
    def _core(self, *, need_fullpath):
        core = LibretroCore.__new__(LibretroCore)
        core._lib = _FakeLibretroLibrary()
        core._loaded = False
        core.need_fullpath = need_fullpath
        core._read_av_info = lambda: None
        return core

    def test_fullpath_core_receives_path_without_preloading_rom(self):
        core = self._core(need_fullpath=True)
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "game.chd"
            rom.write_bytes(b"small stand-in for a multi-gigabyte disc")

            with patch.object(Path, "read_bytes",
                              side_effect=AssertionError("ROM was preloaded")):
                core.load_game(rom)

        self.assertEqual(core._lib.game_info["path"], str(rom.absolute()).encode())
        self.assertIsNone(core._lib.game_info["data"])
        self.assertEqual(core._lib.game_info["size"], 0)
        self.assertIsNone(core._rom_buffer)
        self.assertTrue(core._loaded)

    def test_buffer_core_still_receives_rom_bytes(self):
        core = self._core(need_fullpath=False)
        with tempfile.TemporaryDirectory() as directory:
            rom = Path(directory) / "game.rom"
            rom.write_bytes(b"rom-data")
            core.load_game(rom)

        self.assertEqual(core._lib.game_info["path"], str(rom.absolute()).encode())
        self.assertIsNotNone(core._lib.game_info["data"])
        self.assertEqual(core._lib.game_info["size"], 8)
        self.assertTrue(core._loaded)


class LibretroEnvironmentTests(unittest.TestCase):
    def test_log_interface_returns_a_callable_function_pointer(self):
        class LogInterface(ct.Structure):
            _fields_ = [("log", ct.c_void_p)]

        core = LibretroCore.__new__(LibretroCore)
        messages = []
        callback_type = ct.CFUNCTYPE(None, ct.c_int, ct.c_char_p)
        core._cb_log = callback_type(
            lambda level, message: messages.append((level, message)))
        interface = LogInterface()

        self.assertTrue(core._on_environment(27, ct.byref(interface)))
        self.assertTrue(interface.log)

        callback_type(interface.log)(1, b"LRPS2 log")
        self.assertEqual(messages, [(1, b"LRPS2 log")])

    def test_legacy_core_variables_store_defaults_and_keep_user_choices(self):
        class Variable(ct.Structure):
            _fields_ = [("key", ct.c_char_p), ("value", ct.c_char_p)]

        core = LibretroCore.__new__(LibretroCore)
        core._options = {b"pcsx2_renderer": b"Software (SW)"}
        variables = (Variable * 3)(
            Variable(
                b"pcsx2_bios",
                b"BIOS; SCPH-70012.bin|SCPH-39001.bin",
            ),
            Variable(
                b"pcsx2_renderer",
                b"Renderer; Auto|Software (SW)",
            ),
            Variable(None, None),
        )

        self.assertTrue(core._on_environment(16, variables))
        self.assertEqual(core._options[b"pcsx2_bios"], b"SCPH-70012.bin")
        self.assertEqual(core._options[b"pcsx2_renderer"], b"Software (SW)")


class LibretroHardwareLifecycleTests(unittest.TestCase):
    def test_hardware_readback_reuses_the_same_frame_buffers(self):
        class FakeContext:
            def makeCurrent(self, surface):
                return True

        class FakeFramebuffer:
            def width(self):
                return 2

            def height(self):
                return 2

            def handle(self):
                return 7

        core = LibretroCore.__new__(LibretroCore)
        core._gl_context = FakeContext()
        core._gl_surface = object()
        core._fbo = FakeFramebuffer()
        core._gl_bind_framebuffer = lambda *args: None
        core._lock = threading.Lock()
        core.frame = None
        core.frame_width = 0
        core.frame_height = 0
        core.frame_pitch = 0
        core.pixel_format = 0

        # OpenGL supplies the lower row first.  Distinct row values also prove
        # that the persistent destination is still flipped for QImage.
        raw = bytes([1] * 8 + [2] * 8)

        def read_pixels(x, y, width, height, pixel_format, pixel_type, target):
            ct.memmove(target, raw, len(raw))

        core._gl_read_pixels = read_pixels

        core._read_hw_frame(2, 2)
        first_frame = core.frame
        first_readback = core._hw_readback_buffer
        core._read_hw_frame(2, 2)

        self.assertIs(core.frame, first_frame)
        self.assertIs(core._hw_readback_buffer, first_readback)
        self.assertEqual(bytes(core.frame), bytes([2] * 8 + [1] * 8))

    def test_graphics_context_is_destroyed_before_the_game_is_unloaded(self):
        events = []

        class FakeLibrary:
            def retro_unload_game(self):
                events.append("unload_game")

            def retro_deinit(self):
                events.append("deinit")

        core = LibretroCore.__new__(LibretroCore)
        core._lib = FakeLibrary()
        core._loaded = True
        core._vfs_handles = {}
        core._teardown_hw_render = lambda: events.append("destroy_context")
        core._free_library = lambda: events.append("free_library")

        core.unload()

        self.assertLess(events.index("destroy_context"),
                        events.index("unload_game"))

if __name__ == "__main__":
    unittest.main()
