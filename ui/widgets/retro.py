"""RetroScreen — the widget a libretro core renders into.

The core (actions/libretro.py) produces a framebuffer and a block of PCM once
per frame and asks for controller state; this widget supplies the other half of
that loop: it paces the frames, paints them, feeds the audio to a QAudioSink
and turns key events into joypad bits. That is the whole reason the emulator
lives *inside* Jarvis rather than in a window of its own.

Two details worth knowing before changing anything here:

- **Pacing is elapsed-time driven, not tick-driven.** A QTimer set to 16 ms
  does not fire at 59.7275 Hz, and the drift is audible long before it is
  visible — the audio queue either starves or grows unbounded. Each tick
  therefore runs however many frames are actually due, capped so a stall can't
  turn into a burst of fast-forward.
- **The framebuffer is not copied to paint it.** RGB565 and XRGB8888 both map
  straight onto a QImage format, and the core's pitch becomes the image's
  bytesPerLine, so a frame goes from the core to the screen with no per-pixel
  work in Python. The bytes object is held on the widget because QImage does
  not take ownership of it.
"""
from __future__ import annotations

import threading
import time

from PyQt6.QtCore import *
from PyQt6.QtGui import *
from PyQt6.QtWidgets import *

try:
    from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
    HAS_AUDIO = True
except Exception:  # QtMultimedia missing — video still works, silently
    HAS_AUDIO = False

from actions import input_config
from actions.libretro import (
    ANALOG_ID_X, ANALOG_ID_Y, ANALOG_INDEX_LEFT, ANALOG_INDEX_RIGHT,
    PIXEL_0RGB1555, PIXEL_RGB565, PIXEL_XRGB8888,
)

KEYMAP_HELP = (
    "Flechas: cruceta  ·  Z: B  ·  X: A  ·  A/S: L/R  ·  "
    "Enter: Start  ·  Retroceso: Select"
)


def keymap_help(console_id: str = "gba") -> str:
    """Describe the *current* keyboard bindings, not the shipped defaults."""
    bindings = input_config.load(console_id)
    parts = []
    for button, label in input_config.button_order(console_id):
        keys = bindings.keyboard.get(button) or []
        if not keys:
            continue
        parts.append(f"{label}: {input_config.key_label(keys[0])}")
    return "  ·  ".join(parts) if parts else "Sin teclas asignadas"

# Audio queue ceiling. Past this the game is producing faster than the device
# drains (fast-forward, or a paused sink), and keeping the surplus would only
# add latency — the oldest samples go instead.
_MAX_QUEUE_MS = 120


# The screen currently running a game, if any. Registered on attach() and
# cleared on detach() so callers outside the widget tree — notably the LAN
# dashboard, which turns a phone into a controller — can find the live core
# without walking the UI hierarchy from a background thread.
_active_screen = None
_active_lock = threading.Lock()


def _set_active_screen(screen) -> None:
    global _active_screen
    with _active_lock:
        _active_screen = screen


def _clear_active_screen(screen) -> None:
    """Only clears if `screen` is still the registered one: detach() also runs
    at the start of attach(), and on an already-idle screen."""
    global _active_screen
    with _active_lock:
        if _active_screen is screen:
            _active_screen = None


def active_screen():
    """The RetroScreen with a game loaded, or None."""
    with _active_lock:
        screen = _active_screen
    return screen if screen is not None and screen.is_running() else None


class RetroScreen(QWidget):
    """Paints a libretro core's output and drives its frame loop."""

    fps_updated = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setMinimumSize(240, 160)
        self.setStyleSheet("background:#000000;")
        self.setCursor(Qt.CursorShape.BlankCursor)

        self._core = None
        self._frame_bytes = None
        self._image: QImage | None = None
        self._smooth = False
        self._keymap: dict[int, str] = {}
        self._padmap: dict[str, str] = {}
        self._pad_pressed: set[str] = set()
        self._gamepad = None
        self._console_id = "gba"
        self.reload_bindings()

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._last_tick = 0.0
        self._frame_debt = 0.0
        self._running = False

        self._sink = None
        self._audio_io = None
        self._audio_rate = 0
        self._core_rate = 0
        self._queue = bytearray()
        self._resampler = None

        # Rolling frame counter for the on-screen fps read-out.
        self._fps_frames = 0
        self._fps_clock = QElapsedTimer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reload_bindings(self) -> None:
        """Pick up bindings edited in the controls screen, mid-game included."""
        bindings = input_config.load(self._console_id)
        self._keymap = bindings.keymap()
        self._padmap = bindings.padmap()
        if self._core is not None:
            # Whatever was held under the old mapping would never see a
            # release, so start the new mapping from a clean slate.
            self._core.clear_input()
        self._pad_pressed.clear()

    def attach(self, core, console_id: str = "gba") -> None:
        """Bind a loaded core (with a game already in it) and start running."""
        self.detach()
        _set_active_screen(self)
        self._console_id = console_id
        self.reload_bindings()
        self._core = core
        self._open_audio(core.sample_rate)
        try:
            from actions import gamepad
            self._gamepad = gamepad.get_manager()
        except Exception:
            self._gamepad = None
        self._frame_debt = 0.0
        self._last_tick = time.perf_counter()
        self._fps_clock.restart()
        self._fps_frames = 0
        self._running = True
        # A short interval relative to the frame time lets the pacer land close
        # to each deadline instead of quantising to the timer's own period.
        self._timer.start(max(1, int(500.0 / max(1.0, core.fps))))
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.update()

    def detach(self) -> None:
        _clear_active_screen(self)
        self._timer.stop()
        self._running = False
        self._close_audio()
        if self._core is not None:
            self._core.clear_input()
        self._pad_pressed.clear()
        self._gamepad = None
        self._core = None
        self._image = None
        self._frame_bytes = None

    @property
    def core(self):
        return self._core

    def is_running(self) -> bool:
        return self._running

    def set_paused(self, paused: bool) -> None:
        if self._core is None:
            return
        if paused:
            self._timer.stop()
            self._running = False
            self._core.clear_input()
            if self._sink is not None:
                self._sink.suspend()
        else:
            if self._sink is not None:
                self._sink.resume()
            self._frame_debt = 0.0
            self._last_tick = time.perf_counter()
            self._running = True
            self._timer.start(max(1, int(500.0 / max(1.0, self._core.fps))))
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_smooth(self, smooth: bool) -> None:
        """Bilinear upscale vs. crisp nearest-neighbour pixels."""
        self._smooth = bool(smooth)
        self.update()

    # ------------------------------------------------------------------
    # Frame loop
    # ------------------------------------------------------------------

    def _tick(self):
        core = self._core
        if core is None or not self._running:
            return

        # perf_counter, not QElapsedTimer.restart(): the latter reports whole
        # milliseconds, and truncating ~0.3 ms on every 8 ms tick bled about
        # 6% off the frame rate — 56 fps instead of the core's 59.73.
        now = time.perf_counter()
        elapsed = now - self._last_tick
        self._last_tick = now
        # A long native frame or modal stall is old time, not work that the
        # emulator should repay by fast-forwarding for several seconds.
        due, self._frame_debt = _advance_frame_debt(
            self._frame_debt, elapsed, core.fps, max_catchup=4)
        if due <= 0:
            return

        self._poll_gamepad()
        for _ in range(due):
            core.run_frame()
        self._fps_frames += due
        self._push_audio(core.take_audio())
        self.update()

        if self._fps_clock.elapsed() >= 1000:
            self.fps_updated.emit(
                self._fps_frames * 1000.0 / max(1, self._fps_clock.elapsed()))
            self._fps_frames = 0
            self._fps_clock.restart()

    def _poll_gamepad(self):
        """Translate pad signals into joypad presses and releases.

        Only the *changes* are pushed: the keyboard drives the same buttons on
        the core, so re-asserting every pad button each frame would hold down
        anything the keyboard had just released.
        """
        if self._gamepad is None or self._core is None:
            return
        try:
            signals = self._gamepad.signals()
            axes = self._gamepad.axes() if hasattr(self._gamepad, "axes") else {}
        except Exception:
            return

        active = {self._padmap[s] for s in signals if s in self._padmap}
        for button in active - self._pad_pressed:
            self._core.set_button(button, True)
        for button in self._pad_pressed - active:
            self._core.set_button(button, False)
        self._pad_pressed = active
        if hasattr(self._core, "set_axis"):
            for index, prefix in ((ANALOG_INDEX_LEFT, "left"),
                                  (ANALOG_INDEX_RIGHT, "right")):
                self._core.set_axis(
                    index, ANALOG_ID_X, axes.get(f"{prefix}_x", 0))
                self._core.set_axis(
                    index, ANALOG_ID_Y, axes.get(f"{prefix}_y", 0))

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def _open_audio(self, core_rate: float):
        if not HAS_AUDIO:
            return
        try:
            device = QMediaDevices.defaultAudioOutput()
            if device is None or device.isNull():
                return
            fmt = QAudioFormat()
            fmt.setChannelCount(2)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            # Prefer the device's native clock. mGBA emits 65536 Hz; Windows
            # may advertise it as accepted while the driver resamples it
            # poorly. Feeding the Realtek/native 48 kHz clock avoids that
            # crackly device-side conversion.
            rate = device.preferredFormat().sampleRate() or 48000
            fmt.setSampleRate(rate)
            if not device.isFormatSupported(fmt):
                rate = int(round(core_rate)) or 48000
                fmt.setSampleRate(rate)
                if not device.isFormatSupported(fmt):
                    return
            self._audio_rate = rate
            self._core_rate = int(round(core_rate)) or rate
            self._resampler = _StreamingS16Resampler(
                self._core_rate, self._audio_rate)
            self._sink = QAudioSink(device, fmt, self)
            self._sink.setBufferSize(rate // 5 * 4)  # ~200 ms of stereo S16
            self._audio_io = self._sink.start()
        except Exception:
            self._sink = None
            self._audio_io = None

    def _close_audio(self):
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
        self._sink = None
        self._audio_io = None
        self._resampler = None
        self._queue.clear()

    def _push_audio(self, pcm: bytes):
        if not pcm or self._audio_io is None:
            return
        if self._core_rate != self._audio_rate:
            pcm = self._resampler.process(pcm) if self._resampler else pcm
        self._queue += pcm

        max_bytes = int(self._audio_rate * 4 * _MAX_QUEUE_MS / 1000)
        if len(self._queue) > max_bytes:
            del self._queue[:len(self._queue) - max_bytes]

        try:
            free = self._sink.bytesFree()
        except Exception:
            return
        if free <= 0:
            return
        chunk = bytes(self._queue[:free])
        if not chunk:
            return
        try:
            written = self._audio_io.write(chunk)
        except Exception:
            return
        if written and written > 0:
            del self._queue[:written]

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _build_image(self) -> QImage | None:
        core = self._core
        if core is None:
            return None
        data, width, height, pitch, pixel_format = core.current_frame()
        if not data or not width or not height:
            return None
        if pixel_format == PIXEL_XRGB8888:
            fmt = QImage.Format.Format_RGB32
        elif pixel_format == PIXEL_0RGB1555:
            fmt = QImage.Format.Format_RGB555
        else:
            fmt = QImage.Format.Format_RGB16  # RGB565, mGBA's default
        # Held on the widget: QImage wraps the buffer without owning it, so a
        # local would be collected out from under the painter.
        self._frame_bytes = data
        return QImage(self._frame_bytes, width, height, pitch, fmt)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))

        image = self._build_image()
        if image is None or image.isNull():
            painter.setPen(QColor("#5d6b73"))
            painter.setFont(QFont("Inter", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Sin señal")
            painter.end()
            return

        core = self._core
        ratio = getattr(core, "aspect_ratio", 0) or (image.width() / image.height())
        avail_w, avail_h = self.width(), self.height()
        target_w = avail_w
        target_h = int(round(target_w / ratio))
        if target_h > avail_h:
            target_h = avail_h
            target_w = int(round(target_h * ratio))

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._smooth)
        painter.drawImage(
            QRect((avail_w - target_w) // 2, (avail_h - target_h) // 2,
                  target_w, target_h),
            image,
        )
        painter.end()

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        button = self._keymap.get(Qt.Key(event.key()))
        if button and self._core is not None:
            self._core.set_button(button, True)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        button = self._keymap.get(Qt.Key(event.key()))
        if button and self._core is not None:
            self._core.set_button(button, False)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        # Otherwise a button held while alt-tabbing stays down forever: the
        # release event goes to whatever took the focus.
        if self._core is not None:
            self._core.clear_input()
        # clear_input also dropped the pad's buttons, so forget what was held
        # or the next poll would see "no change" and never press them again.
        # The pad keeps working without focus, which is the point of a pad.
        self._pad_pressed.clear()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        super().mousePressEvent(event)


def _advance_frame_debt(debt: float, elapsed: float, fps: float,
                        max_catchup: int = 4) -> tuple[int, float]:
    """Return frames due while dropping backlog older than max_catchup."""
    debt = min(max(0.0, debt + max(0.0, elapsed) * max(1.0, fps)),
               float(max_catchup))
    due = int(debt)
    return due, debt - due


class _StreamingS16Resampler:
    """Linear stereo S16 resampler that preserves phase across callbacks."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate = max(1, int(src_rate))
        self.dst_rate = max(1, int(dst_rate))
        self._step = self.src_rate / self.dst_rate
        self._buffer = None
        self._position = 0.0

    def process(self, pcm: bytes) -> bytes:
        if not pcm or self.src_rate == self.dst_rate:
            return pcm
        try:
            import numpy as np
        except Exception:
            return pcm
        samples = np.frombuffer(pcm, dtype=np.int16)
        frames = samples.size // 2
        if not frames:
            return b""
        incoming = samples[:frames * 2].reshape(frames, 2).astype(np.float32)
        self._buffer = (incoming if self._buffer is None
                        else np.concatenate((self._buffer, incoming), axis=0))
        if len(self._buffer) < 2 or self._position >= len(self._buffer) - 1:
            return b""

        positions = np.arange(
            self._position, len(self._buffer) - 1, self._step, dtype=np.float64)
        left = np.floor(positions).astype(np.int64)
        weight = (positions - left)[:, None]
        mixed = (self._buffer[left] * (1.0 - weight)
                 + self._buffer[left + 1] * weight)

        next_position = self._position + len(positions) * self._step
        drop = min(int(next_position), len(self._buffer) - 1)
        self._buffer = self._buffer[drop:]
        self._position = next_position - drop
        return np.clip(mixed, -32768, 32767).astype(np.int16).tobytes()
