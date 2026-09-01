import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from actions.emulator_session import EmulatorSession, EmulatorSessionError


class _FakeCore:
    """Stands in for actions.libretro.LibretroCore — just enough surface for
    EmulatorSession to drive, at a fps high enough that a few-millisecond
    test sleep reliably produces several frames."""

    def __init__(self):
        self.fps = 1000.0
        self.hw_render_active = False
        self.run_frame_calls = 0
        self.unloaded = False
        self.sram = b""
        self.pressed = []
        self.reset_called = False
        self._loaded_lock = threading.Lock()
        self.threads_called_from: set = set()

    def load_game(self, rom_path):
        self.rom_path = rom_path

    def run_frame(self):
        with self._loaded_lock:
            self.run_frame_calls += 1
        self.threads_called_from.add(threading.current_thread().name)

    def current_frame(self):
        return (b"\x00" * 4, 2, 2, 8, 2)

    def take_audio(self):
        return b""

    def reset(self):
        self.reset_called = True
        self.threads_called_from.add(threading.current_thread().name)

    def set_button(self, button, pressed):
        self.pressed.append((button, pressed))

    def set_axis(self, index, axis, value):
        pass

    def clear_input(self):
        self.pressed.clear()

    def read_sram(self):
        return self.sram

    def write_sram(self, blob):
        self.sram = blob

    def save_state(self):
        self.threads_called_from.add(threading.current_thread().name)
        return b"STATE"

    def load_state(self, blob):
        self.loaded_state = blob
        self.threads_called_from.add(threading.current_thread().name)


class EmulatorSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rom = Path(self.tmp.name) / "game.gba"
        self.rom.write_bytes(b"ROM")
        self.saves_dir = Path(self.tmp.name) / "saves"
        self.saves_dir.mkdir()

        self.fake_core = _FakeCore()

        # _require_qapplication is a guard against a real Qt footgun (see the
        # module docstring) — irrelevant to what these tests exercise, so it's
        # patched out rather than requiring a display to run this suite.
        patcher_qapp = patch("actions.emulator_session._require_qapplication")
        patcher_qapp.start()
        self.addCleanup(patcher_qapp.stop)

        patcher_find_core = patch(
            "actions.emulator_runtime.find_core", return_value=Path("/fake/core.so")
        )
        patcher_find_core.start()
        self.addCleanup(patcher_find_core.stop)

        patcher_saves_dir = patch(
            "actions.emulator_runtime.saves_dir", return_value=self.saves_dir
        )
        patcher_saves_dir.start()
        self.addCleanup(patcher_saves_dir.stop)

        patcher_load = patch(
            "actions.libretro.load", return_value=self.fake_core
        )
        self.mock_load = patcher_load.start()
        self.addCleanup(patcher_load.stop)

        patcher_unload = patch("actions.libretro.unload")
        self.mock_unload = patcher_unload.start()
        self.addCleanup(patcher_unload.stop)

    def _session(self) -> EmulatorSession:
        return EmulatorSession()

    def _wait_for(self, predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.005)
        return False

    # ------------------------------------------------------------------
    def test_start_produces_frames_and_reports_running(self):
        session = self._session()
        session.start("gba", self.rom, "Test Game")
        try:
            self.assertTrue(self._wait_for(lambda: self.fake_core.run_frame_calls > 0))
            status = session.status()
            self.assertTrue(status["running"])
            self.assertEqual(status["console"], "gba")
            self.assertEqual(status["title"], "Test Game")
        finally:
            session.stop()

    def test_stop_unloads_the_core_and_flushes_sram(self):
        self.fake_core.sram = b"\x01\x02not-blank"

        session = self._session()
        session.start("gba", self.rom)
        self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
        session.stop()

        self.mock_unload.assert_called_once()
        self.assertFalse(session.is_running())
        srm = self.saves_dir / "game.srm"
        self.assertTrue(srm.is_file())
        self.assertEqual(srm.read_bytes(), b"\x01\x02not-blank")

    def test_stop_without_start_is_a_harmless_noop(self):
        session = self._session()
        session.stop()  # must not raise
        self.assertFalse(session.is_running())

    def test_double_start_is_rejected(self):
        session = self._session()
        session.start("gba", self.rom)
        try:
            with self.assertRaises(EmulatorSessionError):
                session.start("gba", self.rom)
        finally:
            session.stop()

    def test_start_rejects_a_missing_rom(self):
        session = self._session()
        with self.assertRaises(EmulatorSessionError):
            session.start("gba", Path(self.tmp.name) / "missing.gba")

    def test_start_rejects_a_missing_core(self):
        with patch("actions.emulator_runtime.find_core", return_value=None):
            session = self._session()
            with self.assertRaises(EmulatorSessionError):
                session.start("gba", self.rom)

    def test_pause_stops_frame_advancement(self):
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            session.pause(True)
            count_at_pause = self.fake_core.run_frame_calls
            time.sleep(0.05)
            self.assertEqual(self.fake_core.run_frame_calls, count_at_pause)
            self.assertTrue(session.status()["paused"])

            session.pause(False)
            self.assertTrue(
                self._wait_for(lambda: self.fake_core.run_frame_calls > count_at_pause)
            )
        finally:
            session.stop()

    def test_input_passthrough(self):
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            session.set_button("a", True)
            self.assertIn(("a", True), self.fake_core.pressed)
            session.clear_input()
            self.assertEqual(self.fake_core.pressed, [])
        finally:
            session.stop()

    def test_save_and_load_state_round_trip(self):
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            session.save_state()
            state_file = self.saves_dir / "game.state"
            self.assertTrue(state_file.is_file())
            self.assertEqual(state_file.read_bytes(), b"STATE")

            session.load_state()
            self.assertEqual(self.fake_core.loaded_state, b"STATE")
        finally:
            session.stop()

    def test_load_state_without_a_saved_state_raises(self):
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            with self.assertRaises(EmulatorSessionError):
                session.load_state()
        finally:
            session.stop()

    def test_reset_calls_through_to_the_core(self):
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            session.reset()
            self.assertTrue(self.fake_core.reset_called)
        finally:
            session.stop()

    def test_save_load_and_reset_run_on_the_emulation_thread(self):
        # Regression test: save_state()/load_state()/reset() used to call
        # straight into the core from whatever thread invoked them (e.g. a
        # Flask request thread in production). That's safe for a
        # software-rendered core but segfaults a hardware-rendered one,
        # whose GL context is only ever current on the emulation thread —
        # found live against the real PS2 core. This asserts they're routed
        # through _drain_pending_actions instead, called from run_frame's
        # own thread, and never from this test's (the caller's) thread.
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            session.save_state()
            session.load_state()
            session.reset()
            caller_thread = threading.current_thread().name
            self.assertNotIn(caller_thread, self.fake_core.threads_called_from)
            self.assertEqual(len(self.fake_core.threads_called_from), 1)
        finally:
            session.stop()

    def test_save_state_works_while_paused(self):
        # _drain_pending_actions runs before the pause early-continue in
        # _run()'s loop specifically so this doesn't hang.
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            session.pause(True)
            session.save_state()  # must not time out
            state_file = self.saves_dir / "game.state"
            self.assertTrue(state_file.is_file())
        finally:
            session.stop()

    def test_latest_frame_matches_the_core_format(self):
        session = self._session()
        session.start("gba", self.rom)
        try:
            self._wait_for(lambda: self.fake_core.run_frame_calls > 0)
            data, w, h, pitch, fmt = session.latest_frame()
            self.assertEqual((w, h, pitch, fmt), (2, 2, 8, 2))
        finally:
            session.stop()


class EmulatorSessionQtGuardTests(unittest.TestCase):
    """_require_qapplication is the guard against the shutdown segfault
    documented in the module's docstring — verified separately from the rest
    of the suite above, which patches it out."""

    def test_start_refuses_to_run_without_a_qapplication(self):
        with patch("actions.emulator_runtime.find_core", return_value=Path("/fake")), \
             patch("PyQt6.QtWidgets.QApplication.instance", return_value=None):
            with tempfile.TemporaryDirectory() as tmp:
                rom = Path(tmp) / "game.gba"
                rom.write_bytes(b"ROM")
                session = EmulatorSession()
                with self.assertRaises(EmulatorSessionError):
                    session.start("gba", rom)
                self.assertFalse(session.is_running())


class EmulatorPacingSharedWithLegacyTests(unittest.TestCase):
    """actions/emulator_pacing.py was extracted from
    ui/widgets/retro.py:_advance_frame_debt so the headless session and the
    legacy Qt widget share one implementation instead of drifting apart."""

    def test_retro_screen_uses_the_shared_pacing_function(self):
        from actions.emulator_pacing import advance_frame_debt
        from ui.widgets import retro

        self.assertIs(retro._advance_frame_debt, advance_frame_debt)


if __name__ == "__main__":
    unittest.main()
