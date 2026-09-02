"""A minimal libretro frontend driven from Python via ctypes.

This is what makes the Emuladores tab run *inside* Jarvis instead of shelling
out to mGBA.exe. A libretro core is a plain DLL with a dozen C entry points: it
hands the frontend a framebuffer and an audio buffer once per frame and asks
the frontend for input. Nothing about it needs a window of its own, so the
frames go straight into a QWidget (ui/widgets/retro.py) the same way a video
decoder's frames would.

Only a focused slice of the API is implemented — no disk-swap interface, no
rumble — but hardware rendering *is* supported (see "GL cores" below), because
several real systems (N64 at full accuracy, PS1, PS2) have no usable
software-only core.

Two hard rules of the libretro ABI shape this file:

- The callbacks the core keeps are raw C function pointers. Python must hold a
  reference to every ctypes callback object for as long as the core is loaded
  or they get garbage-collected and the core calls into freed memory.
- The pointer handed to the video callback is only valid *during* that call, so
  the frame is copied out immediately.

Cores also keep global state, so exactly one core is loaded per process — see
``load()``.

GL cores
--------
A core that needs a GPU calls RETRO_ENVIRONMENT_SET_HW_RENDER instead of
rendering to a buffer it hands the frontend. From then on the *frontend* owns
the GL context and an FBO; the core renders into that FBO directly using GL
calls of its own, and the video callback receives a sentinel
(``RETRO_HW_FRAME_BUFFER_VALID``) meaning "already on the GPU, go read it
back" instead of a pixel pointer.

This module accepts only desktop OpenGL / OpenGL Core cores (never GLES,
Vulkan or Direct3D — those would need a second rendering backend this project
has no other use for) and always uses an *offscreen* context: nothing here
opens a window of its own. ``ui/widgets/retro.py`` keeps running on the raster
(software) paint path throughout — a GL core's frames are pulled back to plain
bytes with ``glReadPixels`` and fed into the exact same QImage pipeline a
software core's frames use. That readback costs a bit of latency a native GL
widget wouldn't, but it means the widget, the frame pacer and the audio path
don't need a second implementation for the handful of consoles that need a
GPU — worth it at the resolutions these cores render (well under 1080p).
"""
from __future__ import annotations

import ctypes as ct
import os
import re
import threading
import time
from ctypes import (
    CFUNCTYPE, POINTER, Structure, c_bool, c_char_p, c_double, c_float,
    c_int, c_int16, c_int32, c_int64, c_size_t, c_uint, c_uint64, c_void_p,
)
from pathlib import Path
from typing import Optional

try:
    from PyQt6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
    from PyQt6.QtOpenGL import (
        QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    )
    import numpy as np
    HAS_GL = True
except Exception:  # a headless/no-Qt import context — GL cores just refuse
    HAS_GL = False

# --- Environment commands (libretro.h) -------------------------------------
ENV_SET_ROTATION = 1
ENV_GET_OVERSCAN = 2
ENV_GET_CAN_DUPE = 3
ENV_SET_MESSAGE = 6
ENV_SHUTDOWN = 7
ENV_SET_PERFORMANCE_LEVEL = 8
ENV_GET_SYSTEM_DIRECTORY = 9
ENV_SET_PIXEL_FORMAT = 10
ENV_SET_INPUT_DESCRIPTORS = 11
ENV_SET_KEYBOARD_CALLBACK = 12
ENV_SET_HW_RENDER = 14
ENV_GET_VARIABLE = 15
ENV_SET_VARIABLES = 16
ENV_GET_VARIABLE_UPDATE = 17
ENV_SET_SUPPORT_NO_GAME = 18
ENV_GET_LIBRETRO_PATH = 19
ENV_SET_AUDIO_CALLBACK = 22
ENV_GET_LOG_INTERFACE = 27
ENV_GET_CORE_ASSETS_DIRECTORY = 30
ENV_GET_SAVE_DIRECTORY = 31
ENV_SET_SYSTEM_AV_INFO = 32
ENV_SET_GEOMETRY = 37
ENV_GET_VFS_INTERFACE = 45 | 0x10000  # RETRO_ENVIRONMENT_EXPERIMENTAL
ENV_GET_CORE_OPTIONS_VERSION = 52
ENV_SET_CORE_OPTIONS_V2 = 67
ENV_SET_CORE_OPTIONS_V2_INTL = 68

# retro_vfs_interface access flags / seek positions (libretro.h)
_VFS_ACCESS_READ = 1 << 0
_VFS_ACCESS_WRITE = 1 << 1
_VFS_ACCESS_UPDATE_EXISTING = 1 << 2
_VFS_SEEK_START, _VFS_SEEK_CURRENT, _VFS_SEEK_END = 0, 1, 2
_VFS_STAT_IS_VALID = 1 << 0
_VFS_STAT_IS_DIRECTORY = 1 << 1

# Pixel formats
PIXEL_0RGB1555 = 0
PIXEL_XRGB8888 = 1
PIXEL_RGB565 = 2

# retro_get_memory_data ids
MEMORY_SAVE_RAM = 0
MEMORY_RTC = 1

# retro_hw_context_type — only these two are ever accepted (see module
# docstring); the rest exist so a refusal can be recognised, not honoured.
HW_CONTEXT_NONE = 0
HW_CONTEXT_OPENGL = 1
HW_CONTEXT_OPENGLES2 = 2
HW_CONTEXT_OPENGL_CORE = 3
HW_CONTEXT_OPENGLES3 = 4
HW_CONTEXT_OPENGLES_VERSION = 5
HW_CONTEXT_VULKAN = 6
_HW_CONTEXT_ACCEPTED = (HW_CONTEXT_OPENGL, HW_CONTEXT_OPENGL_CORE)

# ((void*)-1) on a 64-bit build — the core's way of saying "the frame is
# already in the framebuffer you gave me, there's no pointer".
_HW_FRAME_BUFFER_VALID = 0xFFFFFFFFFFFFFFFF

# A generous fixed size rather than one derived from reported geometry: some
# GL cores (PS1/PS2 upscalers) resize their internal render target as part of
# a core option and never revisit SET_GEOMETRY, so sizing off the first
# geometry read would clip a resolution change invisibly instead of erroring.
_HW_FBO_SIZE = 2048

DEVICE_JOYPAD = 1
DEVICE_ANALOG = 5
DEVICE_POINTER = 6
JOYPAD_MASK = 256
ANALOG_INDEX_LEFT = 0
ANALOG_INDEX_RIGHT = 1
ANALOG_INDEX_BUTTON = 2
ANALOG_ID_X = 0
ANALOG_ID_Y = 1

# RETRO_DEVICE_ID_POINTER_*
ID_POINTER_X = 0
ID_POINTER_Y = 1
ID_POINTER_PRESSED = 2

# RETRO_DEVICE_ID_JOYPAD_*
JOYPAD = {
    "b": 0, "y": 1, "select": 2, "start": 3,
    "up": 4, "down": 5, "left": 6, "right": 7,
    "a": 8, "x": 9, "l": 10, "r": 11,
    "l2": 12, "r2": 13, "l3": 14, "r3": 15,
}


class LibretroError(RuntimeError):
    """Raised when a core cannot be loaded or a game refused to start."""


# Set JARVIS_INPUT_DEBUG=1 to tally every retro_input_state_t query by
# (device, index, id) and dump it once a second, alongside the axes currently
# held. Worth keeping: "the sticks do nothing" is otherwise indistinguishable
# between the core never asking for RETRO_DEVICE_ANALOG and the frontend never
# holding a value — this told those two apart in one run.
# Set JARVIS_CORE_LOG=1 to echo whatever the core reports through
# RETRO_ENVIRONMENT_GET_LOG_INTERFACE (shader compile failures, missing GL
# extensions, BIOS/plugin complaints). See _on_log.
_CORE_LOG = bool(os.environ.get("JARVIS_CORE_LOG"))
_CORE_LOG_LEVELS = {0: "DEBUG", 1: "INFO", 2: "WARN", 3: "ERROR"}
_CORE_LOG_SPEC = re.compile(rb"%[-+ #0-9.*]*(?:hh|h|ll|l|z|j|t|L)?([diouxXeEfgGaAcspn%])")


def _format_core_log(fmt: bytes, args) -> str:
    """Expand a printf format against varargs recovered from registers.

    Deliberately forgiving: an unreadable argument becomes a placeholder
    rather than an exception, because this runs inside a callback the core
    invokes — raising here would unwind into native code.
    """
    out = bytearray()
    pos = 0
    index = 0
    for match in _CORE_LOG_SPEC.finditer(fmt):
        out += fmt[pos:match.start()]
        pos = match.end()
        kind = match.group(1)
        if kind == b"%":
            out += b"%"
            continue
        if index >= len(args):
            out += b"<?>"
            continue
        arg = args[index]
        index += 1
        try:
            if kind == b"s":
                out += (ct.cast(arg, c_char_p).value or b"") if arg else b"(null)"
            elif kind == b"p":
                out += f"0x{int(arg or 0):x}".encode()
            elif kind in (b"d", b"i"):
                value = int(arg or 0) & 0xFFFFFFFF
                out += str(value - (1 << 32) if value >> 31 else value).encode()
            elif kind in (b"u", b"o", b"x", b"X", b"c"):
                out += format(int(arg or 0) & 0xFFFFFFFF,
                              {b"x": "x", b"X": "X", b"o": "o"}.get(kind, "d")).encode()
            else:
                # Floating point travels in xmm registers, out of reach here.
                out += b"<?>"
        except Exception:
            out += b"<?>"
    out += fmt[pos:]
    return out.decode("utf-8", "replace").rstrip()

_INPUT_DEBUG = bool(os.environ.get("JARVIS_INPUT_DEBUG"))
_input_debug_counts: dict = {}
_input_debug_last = 0.0


def _input_debug_tally(port, device, index, button, axes):
    global _input_debug_last
    key = (port, device, index, button)
    _input_debug_counts[key] = _input_debug_counts.get(key, 0) + 1
    now = time.monotonic()
    if now - _input_debug_last < 1.0:
        return
    _input_debug_last = now
    analog = {k: v for k, v in _input_debug_counts.items() if k[1] == DEVICE_ANALOG}
    joypad = sum(v for k, v in _input_debug_counts.items() if k[1] == DEVICE_JOYPAD)
    other = sorted({k[1] for k in _input_debug_counts} - {DEVICE_JOYPAD, DEVICE_ANALOG})
    print(f"[INPUT] joypad_queries={joypad} analog_queries={sorted(analog.items())} "
          f"other_devices={other} axes_held={dict(axes)}", flush=True)
    _input_debug_counts.clear()


# --- Structs ---------------------------------------------------------------

class _Geometry(Structure):
    _fields_ = [
        ("base_width", c_uint), ("base_height", c_uint),
        ("max_width", c_uint), ("max_height", c_uint),
        ("aspect_ratio", c_float),
    ]


class _Timing(Structure):
    _fields_ = [("fps", c_double), ("sample_rate", c_double)]


class _AVInfo(Structure):
    _fields_ = [("geometry", _Geometry), ("timing", _Timing)]


class _SystemInfo(Structure):
    _fields_ = [
        ("library_name", c_char_p), ("library_version", c_char_p),
        ("valid_extensions", c_char_p),
        ("need_fullpath", c_bool), ("block_extract", c_bool),
    ]


class _GameInfo(Structure):
    _fields_ = [
        ("path", c_char_p), ("data", c_void_p),
        ("size", c_size_t), ("meta", c_char_p),
    ]


# retro_log_printf_t is variadic. ctypes cannot expand C varargs, so the two
# fixed parameters alone would only ever hand us the raw format string ("%s"),
# which is exactly what made the core's own diagnostics unreadable. Declaring
# extra fixed pointer parameters recovers the first four: on the x86-64 SysV
# ABI a variadic call passes its integer/pointer arguments in rdi, rsi, rdx,
# rcx, r8, r9 in order, so args 3-6 land where these four expect them. Reading
# them is only ever done when the format string says an argument is there —
# see _format_core_log. Cores that pass no arguments simply leave these
# unread. Harmless on other ABIs for the same reason: nothing is dereferenced
# unless a conversion specifier asks for it.
_LOG_CB = CFUNCTYPE(None, c_int, c_char_p, c_void_p, c_void_p, c_void_p, c_void_p)


class _LogCallback(Structure):
    _fields_ = [("log", _LOG_CB)]


class _Variable(Structure):
    """retro_variable — the core writes `key`, the frontend answers `value`."""
    _fields_ = [("key", c_char_p), ("value", c_char_p)]


# Core options, v2 API. A modern core (LRPS2 among them) publishes its option
# table through this and never falls back to the legacy SET_VARIABLES path, so
# without parsing it the frontend has no idea what the core's own defaults are
# — it can only answer "unknown" to every GET_VARIABLE it did not hardcode.
# Layout is fixed by libretro.h; values[] is a fixed-size array terminated by a
# NULL `value`.
_NUM_CORE_OPTION_VALUES_MAX = 128


class _CoreOptionValue(Structure):
    _fields_ = [("value", c_char_p), ("label", c_char_p)]


class _CoreOptionV2Definition(Structure):
    _fields_ = [
        ("key", c_char_p),
        ("desc", c_char_p),
        ("desc_categorized", c_char_p),
        ("info", c_char_p),
        ("info_categorized", c_char_p),
        ("category_key", c_char_p),
        ("values", _CoreOptionValue * _NUM_CORE_OPTION_VALUES_MAX),
        ("default_value", c_char_p),
    ]


class _CoreOptionsV2(Structure):
    _fields_ = [("categories", c_void_p), ("definitions", c_void_p)]


class _CoreOptionsV2Intl(Structure):
    _fields_ = [("us", POINTER(_CoreOptionsV2)), ("local", POINTER(_CoreOptionsV2))]


# retro_hw_render_callback. Two directions of ownership in one struct: the
# core fills in context_type/context_reset/context_destroy/depth/stencil/
# bottom_left_origin/version_major/version_minor before the environment call
# returns, and the frontend (this module) fills in get_current_framebuffer and
# get_proc_address — the same struct instance, read one way and written the
# other. Field order and types must match libretro.h exactly; ctypes' default
# struct alignment already matches the C compiler's on this platform, so
# nothing here needs an explicit _pack_.
_HW_CONTEXT_RESET_CB = CFUNCTYPE(None)
_HW_GET_FBO_CB = CFUNCTYPE(c_uint64)
_HW_GET_PROC_CB = CFUNCTYPE(c_void_p, c_char_p)


class _HWRenderCallback(Structure):
    _fields_ = [
        ("context_type", c_int),
        ("context_reset", _HW_CONTEXT_RESET_CB),
        ("get_current_framebuffer", _HW_GET_FBO_CB),
        ("get_proc_address", _HW_GET_PROC_CB),
        ("depth", c_bool),
        ("stencil", c_bool),
        ("bottom_left_origin", c_bool),
        ("version_major", c_uint),
        ("version_minor", c_uint),
        ("cache_context", c_bool),
        ("context_destroy", _HW_CONTEXT_RESET_CB),
        ("debug_context", c_bool),
    ]


class _VFSInterfaceInfo(Structure):
    """retro_vfs_interface_info — the core asks for a version, we hand back
    the interface (and implicitly confirm support by returning True)."""
    _fields_ = [("required_interface_version", c_uint), ("iface", c_void_p)]


# retro_vfs_interface's function pointer types, in field order. Every one of
# these operates on an *opaque* handle from the core's point of view — on our
# side that handle is just the id() of a Python file object, looked up in
# LibretroCore._vfs_handles, because ctypes can't hand a core a real PyObject*.
_VFS_GET_PATH_CB = CFUNCTYPE(c_char_p, c_void_p)
_VFS_OPEN_CB = CFUNCTYPE(c_void_p, c_char_p, c_uint, c_uint)
_VFS_CLOSE_CB = CFUNCTYPE(c_int, c_void_p)
_VFS_SIZE_CB = CFUNCTYPE(c_int64, c_void_p)
_VFS_TELL_CB = CFUNCTYPE(c_int64, c_void_p)
_VFS_SEEK_CB = CFUNCTYPE(c_int64, c_void_p, c_int64, c_int)
_VFS_READ_CB = CFUNCTYPE(c_int64, c_void_p, c_void_p, c_uint64)
_VFS_WRITE_CB = CFUNCTYPE(c_int64, c_void_p, c_void_p, c_uint64)
_VFS_FLUSH_CB = CFUNCTYPE(c_int, c_void_p)
_VFS_REMOVE_CB = CFUNCTYPE(c_int, c_char_p)
_VFS_RENAME_CB = CFUNCTYPE(c_int, c_char_p, c_char_p)
_VFS_TRUNCATE_CB = CFUNCTYPE(c_int64, c_void_p, c_int64)
_VFS_STAT_CB = CFUNCTYPE(c_int, c_char_p, POINTER(c_int32))
_VFS_MKDIR_CB = CFUNCTYPE(c_int, c_char_p)
_VFS_OPENDIR_CB = CFUNCTYPE(c_void_p, c_char_p, c_bool)
_VFS_READDIR_CB = CFUNCTYPE(c_bool, c_void_p)
_VFS_DIRENT_NAME_CB = CFUNCTYPE(c_char_p, c_void_p)
_VFS_DIRENT_IS_DIR_CB = CFUNCTYPE(c_bool, c_void_p)
_VFS_CLOSEDIR_CB = CFUNCTYPE(c_int, c_void_p)


class _VFSInterface(Structure):
    _fields_ = [
        ("get_path", _VFS_GET_PATH_CB), ("open", _VFS_OPEN_CB),
        ("close", _VFS_CLOSE_CB), ("size", _VFS_SIZE_CB),
        ("tell", _VFS_TELL_CB), ("seek", _VFS_SEEK_CB),
        ("read", _VFS_READ_CB), ("write", _VFS_WRITE_CB),
        ("flush", _VFS_FLUSH_CB), ("remove", _VFS_REMOVE_CB),
        ("rename", _VFS_RENAME_CB),
        ("truncate", _VFS_TRUNCATE_CB),                     # v2
        ("stat", _VFS_STAT_CB), ("mkdir", _VFS_MKDIR_CB),    # v3
        ("opendir", _VFS_OPENDIR_CB), ("readdir", _VFS_READDIR_CB),
        ("dirent_get_name", _VFS_DIRENT_NAME_CB),
        ("dirent_is_dir", _VFS_DIRENT_IS_DIR_CB),
        ("closedir", _VFS_CLOSEDIR_CB),
    ]


_ENV_CB = CFUNCTYPE(c_bool, c_uint, c_void_p)
_VIDEO_CB = CFUNCTYPE(None, c_void_p, c_uint, c_uint, c_size_t)
_AUDIO_CB = CFUNCTYPE(None, c_int16, c_int16)
_AUDIO_BATCH_CB = CFUNCTYPE(c_size_t, POINTER(c_int16), c_size_t)
_POLL_CB = CFUNCTYPE(None)
_STATE_CB = CFUNCTYPE(c_int16, c_uint, c_uint, c_uint, c_uint)


class LibretroCore:
    """One loaded core plus the game currently running in it."""

    def __init__(self, core_path: str | Path, system_dir: str | Path,
                 save_dir: str | Path, options: Optional[dict] = None):
        self.core_path = Path(core_path)
        if not self.core_path.is_file():
            raise LibretroError(f"No existe el core: {self.core_path}")

        # Core options, answered on RETRO_ENVIRONMENT_GET_VARIABLE. Stored as
        # bytes on the instance because the core keeps the pointer we hand it
        # rather than copying the string — a temporary would dangle.
        self._options: dict[bytes, bytes] = {
            str(k).encode(): str(v).encode()
            for k, v in (options or {}).items()
        }
        # Set by set_option() when a value changes after load — answered
        # once via ENV_GET_VARIABLE_UPDATE below, then cleared. This is the
        # standard libretro mechanism for "an option changed, re-read it",
        # but whether anything actually happens depends on the core polling
        # it — not guaranteed for every core. See set_option()'s own
        # docstring for which ones this was actually verified against.
        self._options_dirty = False

        self._system_dir = str(Path(system_dir).absolute()).encode()
        self._save_dir = str(Path(save_dir).absolute()).encode()
        Path(system_dir).mkdir(parents=True, exist_ok=True)
        Path(save_dir).mkdir(parents=True, exist_ok=True)

        try:
            self._lib = ct.CDLL(str(self.core_path))
        except OSError as exc:
            raise LibretroError(f"No pude cargar el core: {exc}") from exc

        self._bind()

        # Frame state, guarded because the audio thread drains samples while
        # the UI thread runs frames.
        self._lock = threading.Lock()
        self.frame: Optional[bytes | bytearray] = None
        self.frame_width = 0
        self.frame_height = 0
        self.frame_pitch = 0
        self.pixel_format = PIXEL_RGB565
        self._audio: bytearray = bytearray()
        self._pressed: set[int] = set()
        self._axes: dict[tuple[int, int], int] = {}
        self._pointer_x = 0
        self._pointer_y = 0
        self._pointer_pressed = False
        self._rotation = 0
        self._loaded = False

        # GL hardware-render state — see the module docstring's "GL cores"
        # section. Populated by ENV_SET_HW_RENDER, acted on once load_game()
        # confirms the ROM was accepted.
        self.hw_render_active = False
        self._hw_context_type = HW_CONTEXT_NONE
        self._hw_depth = False
        self._hw_stencil = False
        self._hw_version = (0, 0)
        self._hw_context_destroy_fn = None
        self._gl_context = None
        self._gl_surface = None
        self._fbo = None
        self._gl_bind_framebuffer = None
        self._gl_read_pixels = None
        # Hardware video used to allocate a ctypes array and a bytes object for
        # every frame.  At PS2 resolutions that is roughly 65 MB/s of allocator
        # churn, which eventually caused severe stutter and write-violation
        # crashes.  These two buffers are resized only when the video geometry
        # changes and are reused for every glReadPixels callback.
        self._hw_readback_buffer = None
        self._hw_frame_buffer: bytearray | None = None
        self._hw_buffer_shape = (0, 0)

        # VFS interface — many modern cores (PCSX2 among them) call
        # RETRO_ENVIRONMENT_GET_VFS_INTERFACE to do their own disc I/O rather
        # than trusting retro_load_game's preloaded-buffer path, and don't
        # null-check a refusal. self._vfs_handles maps the opaque handle we
        # hand back (its own id()) to the real Python file object / DirEntry
        # iterator, since ctypes can't pass a PyObject* through a C ABI.
        self._vfs_handles: dict[int, object] = {}
        self._vfs_iface = None  # kept alive: written into the core's struct

        # Callback objects must outlive the core (see module docstring).
        self._cb_env = _ENV_CB(self._on_environment)
        self._cb_video = _VIDEO_CB(self._on_video)
        self._cb_audio = _AUDIO_CB(self._on_audio_sample)
        self._cb_audio_batch = _AUDIO_BATCH_CB(self._on_audio_batch)
        self._cb_poll = _POLL_CB(self._on_input_poll)
        self._cb_state = _STATE_CB(self._on_input_state)
        self._cb_log = _LOG_CB(self._on_log)
        if HAS_GL:
            self._cb_get_fbo = _HW_GET_FBO_CB(self._get_current_framebuffer)
            self._cb_get_proc = _HW_GET_PROC_CB(self._get_proc_address)

        # VFS callback objects, same GC-safety rule as every other callback
        # here — see the module docstring.
        self._vfs_cb_get_path = _VFS_GET_PATH_CB(self._vfs_get_path)
        self._vfs_cb_open = _VFS_OPEN_CB(self._vfs_open)
        self._vfs_cb_close = _VFS_CLOSE_CB(self._vfs_close)
        self._vfs_cb_size = _VFS_SIZE_CB(self._vfs_size)
        self._vfs_cb_tell = _VFS_TELL_CB(self._vfs_tell)
        self._vfs_cb_seek = _VFS_SEEK_CB(self._vfs_seek)
        self._vfs_cb_read = _VFS_READ_CB(self._vfs_read)
        self._vfs_cb_write = _VFS_WRITE_CB(self._vfs_write)
        self._vfs_cb_flush = _VFS_FLUSH_CB(self._vfs_flush)
        self._vfs_cb_remove = _VFS_REMOVE_CB(self._vfs_remove)
        self._vfs_cb_rename = _VFS_RENAME_CB(self._vfs_rename)
        self._vfs_cb_truncate = _VFS_TRUNCATE_CB(self._vfs_truncate)
        self._vfs_cb_stat = _VFS_STAT_CB(self._vfs_stat)
        self._vfs_cb_mkdir = _VFS_MKDIR_CB(self._vfs_mkdir)
        self._vfs_cb_opendir = _VFS_OPENDIR_CB(self._vfs_opendir)
        self._vfs_cb_readdir = _VFS_READDIR_CB(self._vfs_readdir)
        self._vfs_cb_dirent_name = _VFS_DIRENT_NAME_CB(self._vfs_dirent_name)
        self._vfs_cb_dirent_is_dir = _VFS_DIRENT_IS_DIR_CB(self._vfs_dirent_is_dir)
        self._vfs_cb_closedir = _VFS_CLOSEDIR_CB(self._vfs_closedir)

        self._lib.retro_set_environment(self._cb_env)
        self._lib.retro_set_video_refresh(self._cb_video)
        self._lib.retro_set_audio_sample(self._cb_audio)
        self._lib.retro_set_audio_sample_batch(self._cb_audio_batch)
        self._lib.retro_set_input_poll(self._cb_poll)
        self._lib.retro_set_input_state(self._cb_state)
        self._lib.retro_init()

        info = _SystemInfo()
        self._lib.retro_get_system_info(ct.byref(info))
        self.name = (info.library_name or b"").decode(errors="ignore")
        self.version = (info.library_version or b"").decode(errors="ignore")
        self.extensions = (info.valid_extensions or b"").decode(errors="ignore")
        self.need_fullpath = bool(info.need_fullpath)
        self.block_extract = bool(info.block_extract)

        self.fps = 60.0
        self.sample_rate = 32000.0
        self.aspect_ratio = 4 / 3

    def _bind(self):
        lib = self._lib
        lib.retro_api_version.restype = c_uint
        lib.retro_get_system_info.argtypes = [POINTER(_SystemInfo)]
        lib.retro_get_system_av_info.argtypes = [POINTER(_AVInfo)]
        lib.retro_load_game.argtypes = [POINTER(_GameInfo)]
        lib.retro_load_game.restype = c_bool
        lib.retro_serialize_size.restype = c_size_t
        lib.retro_serialize.argtypes = [c_void_p, c_size_t]
        lib.retro_serialize.restype = c_bool
        lib.retro_unserialize.argtypes = [c_void_p, c_size_t]
        lib.retro_unserialize.restype = c_bool
        lib.retro_get_memory_data.argtypes = [c_uint]
        lib.retro_get_memory_data.restype = c_void_p
        lib.retro_get_memory_size.argtypes = [c_uint]
        lib.retro_get_memory_size.restype = c_size_t
        lib.retro_set_controller_port_device.argtypes = [c_uint, c_uint]

    # ------------------------------------------------------------------
    # libretro callbacks
    # ------------------------------------------------------------------

    def _on_environment(self, cmd: int, data) -> bool:
        if cmd in (ENV_GET_SYSTEM_DIRECTORY, ENV_GET_CORE_ASSETS_DIRECTORY):
            ct.cast(data, POINTER(c_char_p))[0] = self._system_dir
            return True
        if cmd == ENV_GET_SAVE_DIRECTORY:
            ct.cast(data, POINTER(c_char_p))[0] = self._save_dir
            return True
        if cmd == ENV_GET_LIBRETRO_PATH:
            ct.cast(data, POINTER(c_char_p))[0] = str(self.core_path).encode()
            return True
        if cmd == ENV_GET_LOG_INTERFACE:
            ct.cast(data, POINTER(_LogCallback))[0].log = self._cb_log
            return True
        if cmd == ENV_SET_PIXEL_FORMAT:
            self.pixel_format = int(ct.cast(data, POINTER(c_uint))[0])
            return self.pixel_format in (PIXEL_0RGB1555, PIXEL_XRGB8888, PIXEL_RGB565)
        if cmd == ENV_GET_CAN_DUPE:
            # True lets the core skip re-sending an unchanged frame; the
            # widget keeps painting the last one, which is what we want.
            ct.cast(data, POINTER(c_bool))[0] = True
            return True
        if cmd == ENV_GET_OVERSCAN:
            ct.cast(data, POINTER(c_bool))[0] = False
            return True
        if cmd == ENV_SET_ROTATION:
            self._rotation = int(ct.cast(data, POINTER(c_uint))[0]) % 4
            return True
        if cmd == ENV_GET_VARIABLE_UPDATE:
            ct.cast(data, POINTER(c_bool))[0] = self._options_dirty
            self._options_dirty = False
            return True
        if cmd == ENV_GET_CORE_OPTIONS_VERSION:
            # A core asks this BEFORE publishing its option table, to pick
            # which SET_CORE_OPTIONS* variant to call. Answering "unsupported"
            # here (the previous behaviour, by omission) is why LRPS2 never
            # sent its V2 table at all — not a parsing bug, a negotiation one:
            # _harvest_core_options_v2 was correct but never once ran.
            ct.cast(data, POINTER(c_uint))[0] = 2
            return True
        if cmd == ENV_GET_VARIABLE:
            variable = ct.cast(data, POINTER(_Variable))
            key = variable[0].key
            if key and key in self._options:
                variable[0].value = self._options[key]
                return True
            return False  # unset options keep the core's own default
        if cmd == ENV_SET_VARIABLES:
            # Legacy core-option table. Each value is
            # "Description; default|other|choices". Returning True while
            # throwing this data away leaves cores such as LRPS2 with no BIOS
            # selection even though they just published a valid default.
            variables = ct.cast(data, POINTER(_Variable))
            for index in range(4096):
                variable = variables[index]
                if not variable.key:
                    break
                if variable.key in self._options or not variable.value:
                    continue
                _description, separator, choices = variable.value.partition(b";")
                if not separator:
                    continue
                default = choices.strip().split(b"|", 1)[0].strip()
                if default:
                    self._options[variable.key] = default
            return True
        if cmd in (ENV_SET_SYSTEM_AV_INFO, ENV_SET_GEOMETRY):
            self._read_av_info()
            return True
        if cmd == ENV_SET_HW_RENDER:
            if not HAS_GL:
                return False
            hw = ct.cast(data, POINTER(_HWRenderCallback))
            if hw[0].context_type not in _HW_CONTEXT_ACCEPTED:
                return False  # GLES / Vulkan / D3D — not implemented here
            self._hw_context_type = hw[0].context_type
            self._hw_depth = bool(hw[0].depth)
            self._hw_stencil = bool(hw[0].stencil)
            self._hw_version = (int(hw[0].version_major), int(hw[0].version_minor))
            self._hw_context_destroy_fn = hw[0].context_destroy
            context_reset_fn = hw[0].context_reset
            # The context, FBO and get_proc_address must all be real *before*
            # this call returns — some cores (mupen64plus_next among them)
            # resolve GL entry points immediately, still inside
            # retro_load_game, not on some later frame. Deferring this until
            # after retro_load_game returned answered every proc-address
            # lookup with NULL, and the core wrote through it: a hard access
            # violation instead of a clean failure.
            try:
                self._init_hw_render()
            except LibretroError:
                return False
            hw[0].get_current_framebuffer = self._cb_get_fbo
            hw[0].get_proc_address = self._cb_get_proc
            if context_reset_fn:
                context_reset_fn()
            return True
        if cmd == ENV_GET_VFS_INTERFACE:
            info = ct.cast(data, POINTER(_VFSInterfaceInfo))
            if info[0].required_interface_version > 3:
                return False  # a version this module doesn't implement
            if self._vfs_iface is None:
                self._vfs_iface = _VFSInterface(
                    get_path=self._vfs_cb_get_path, open=self._vfs_cb_open,
                    close=self._vfs_cb_close, size=self._vfs_cb_size,
                    tell=self._vfs_cb_tell, seek=self._vfs_cb_seek,
                    read=self._vfs_cb_read, write=self._vfs_cb_write,
                    flush=self._vfs_cb_flush, remove=self._vfs_cb_remove,
                    rename=self._vfs_cb_rename, truncate=self._vfs_cb_truncate,
                    stat=self._vfs_cb_stat, mkdir=self._vfs_cb_mkdir,
                    opendir=self._vfs_cb_opendir, readdir=self._vfs_cb_readdir,
                    dirent_get_name=self._vfs_cb_dirent_name,
                    dirent_is_dir=self._vfs_cb_dirent_is_dir,
                    closedir=self._vfs_cb_closedir,
                )
            info[0].iface = ct.cast(ct.pointer(self._vfs_iface), c_void_p)
            return True
        if cmd in (ENV_SET_CORE_OPTIONS_V2, ENV_SET_CORE_OPTIONS_V2_INTL):
            # Acknowledging this while discarding the table was a half-truth:
            # the core is told the frontend owns its options, then hears
            # "unknown" for every one it did not get hardcoded here. Harvest
            # the declared defaults so unset options resolve to exactly what
            # the core intended.
            try:
                if cmd == ENV_SET_CORE_OPTIONS_V2_INTL:
                    intl = ct.cast(data, POINTER(_CoreOptionsV2Intl))
                    options = intl[0].us if intl and intl[0].us else None
                else:
                    options = ct.cast(data, POINTER(_CoreOptionsV2))
                if options and options[0].definitions:
                    self._harvest_core_options_v2(options[0].definitions)
            except Exception:
                pass  # a malformed table must not take the core down
            return True
        if cmd in (
            ENV_SET_INPUT_DESCRIPTORS, ENV_SET_MESSAGE,
            ENV_SET_PERFORMANCE_LEVEL, ENV_SET_SUPPORT_NO_GAME,
            ENV_SET_KEYBOARD_CALLBACK, ENV_SET_AUDIO_CALLBACK,
        ):
            return True
        if _CORE_LOG:
            # Ground truth beats guessing the exact libretro.h enum value for
            # every core-options variant a core might use (v0/v1/v2/INTL,
            # DISPLAY, ...). Whatever shows up here, unhandled, is what to
            # look up next instead of adding constants speculatively.
            print(f"[ENV] unhandled cmd={cmd} (0x{cmd:x})", flush=True)
        return False

    def _harvest_core_options_v2(self, definitions) -> None:
        """Record each option's declared default, without touching the values
        the caller asked for explicitly."""
        defs = ct.cast(definitions, POINTER(_CoreOptionV2Definition))
        for index in range(4096):  # bounded: the table ends at a NULL key
            entry = defs[index]
            if not entry.key:
                break
            if entry.key in self._options:
                continue  # an explicit choice always wins
            default = entry.default_value
            if not default and entry.values[0].value:
                default = entry.values[0].value  # implicit default: first value
            if default:
                self._options[entry.key] = default
            if _CORE_LOG:
                choices = []
                for slot in entry.values:
                    if not slot.value:
                        break
                    choices.append(slot.value.decode("utf-8", "replace"))
                print(f"[COREOPT] {entry.key.decode()} = "
                      f"{(default or b'?').decode('utf-8', 'replace')}"
                      f"  {choices}", flush=True)

    def _on_log(self, level: int, message, *args) -> None:
        # LRPS2 requires a non-NULL function even when the frontend does not
        # consume its logs, so this stays a quiet sink by default rather than
        # letting the core call through a null pointer. `args` carries the
        # first four varargs recovered by _LOG_CB — see its comment.
        #
        # JARVIS_CORE_LOG=1 surfaces it anyway: the raw format string is
        # usually enough to read ("Failed to compile shader: %s"), and a core
        # complaining about a missing GL extension or a broken shader has
        # nowhere else to say so — dropping it silently is what made
        # "some textures/shaders don't work" impossible to diagnose.
        if not _CORE_LOG:
            return None
        try:
            text = _format_core_log(message, args) if message else ""
        except Exception:
            return None
        if text:
            tag = _CORE_LOG_LEVELS.get(int(level), str(level))
            print(f"[CORE:{tag}] {text}", flush=True)
        return None

    # ------------------------------------------------------------------
    # VFS callbacks — a thin, defensive wrapper around Python's own file
    # I/O. Every one of these can be called from inside retro_load_game or
    # retro_run with a core-supplied path, so a bad path or an OS error must
    # come back as a C-level failure code (0/-1/NULL), never a raised Python
    # exception — an exception crossing back into the core's native frame is
    # exactly the undefined behaviour this module exists to avoid.
    # ------------------------------------------------------------------

    def _vfs_get_path(self, handle) -> bytes:
        obj = self._vfs_handles.get(handle)
        path = getattr(obj, "name", None) if obj is not None else None
        return (path or "").encode() if not isinstance(path, bytes) else path

    def _vfs_open(self, path, mode: int, hints: int):
        if not path:
            return 0
        try:
            if mode & _VFS_ACCESS_WRITE:
                pymode = "r+b" if mode & _VFS_ACCESS_UPDATE_EXISTING else "w+b"
                try:
                    fh = open(path, pymode)
                except FileNotFoundError:
                    fh = open(path, "w+b")
            else:
                fh = open(path, "rb")
        except OSError:
            return 0
        key = id(fh)
        self._vfs_handles[key] = fh
        return key

    def _vfs_close(self, handle) -> int:
        fh = self._vfs_handles.pop(handle, None)
        if fh is None:
            return -1
        try:
            fh.close()
            return 0
        except OSError:
            return -1

    def _vfs_size(self, handle) -> int:
        fh = self._vfs_handles.get(handle)
        if fh is None:
            return -1
        try:
            pos = fh.tell()
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(pos, 0)
            return size
        except OSError:
            return -1

    def _vfs_tell(self, handle) -> int:
        fh = self._vfs_handles.get(handle)
        try:
            return fh.tell() if fh is not None else -1
        except OSError:
            return -1

    def _vfs_seek(self, handle, offset: int, whence: int) -> int:
        fh = self._vfs_handles.get(handle)
        if fh is None:
            return -1
        py_whence = {_VFS_SEEK_START: 0, _VFS_SEEK_CURRENT: 1,
                    _VFS_SEEK_END: 2}.get(whence, 0)
        try:
            fh.seek(offset, py_whence)
            return fh.tell()
        except OSError:
            return -1

    def _vfs_read(self, handle, buf, length: int) -> int:
        fh = self._vfs_handles.get(handle)
        if fh is None or not buf:
            return -1
        try:
            data = fh.read(length)
        except OSError:
            return -1
        if data:
            ct.memmove(buf, data, len(data))
        return len(data)

    def _vfs_write(self, handle, buf, length: int) -> int:
        fh = self._vfs_handles.get(handle)
        if fh is None or not buf:
            return -1
        try:
            data = ct.string_at(buf, length)
            return fh.write(data)
        except OSError:
            return -1

    def _vfs_flush(self, handle) -> int:
        fh = self._vfs_handles.get(handle)
        if fh is None:
            return -1
        try:
            fh.flush()
            return 0
        except OSError:
            return -1

    def _vfs_remove(self, path) -> int:
        try:
            os.remove(path)
            return 0
        except OSError:
            return -1

    def _vfs_rename(self, old_path, new_path) -> int:
        try:
            os.replace(old_path, new_path)
            return 0
        except OSError:
            return -1

    def _vfs_truncate(self, handle, length: int) -> int:
        fh = self._vfs_handles.get(handle)
        if fh is None:
            return -1
        try:
            fh.truncate(length)
            return length
        except OSError:
            return -1

    def _vfs_stat(self, path, size_out) -> int:
        try:
            st = os.stat(path)
        except OSError:
            if size_out:
                size_out[0] = 0
            return 0  # not RETRO_VFS_STAT_IS_VALID
        import stat as _stat
        flags = _VFS_STAT_IS_VALID
        if _stat.S_ISDIR(st.st_mode):
            flags |= _VFS_STAT_IS_DIRECTORY
        if size_out:
            size_out[0] = st.st_size
        return flags

    def _vfs_mkdir(self, path) -> int:
        try:
            os.makedirs(path, exist_ok=True)
            return 0
        except OSError:
            return -1

    def _vfs_opendir(self, path, include_hidden: bool):
        if not path:
            return 0
        try:
            names = sorted(os.listdir(path))
        except OSError:
            return 0
        # An iterator that yields (name, is_dir) pairs, current position
        # tracked on the wrapper object itself since readdir()/dirent_*()
        # are separate calls sharing this one opaque handle.
        entries = []
        for name in names:
            if not include_hidden and name.startswith('.'):
                continue
            try:
                entries.append((name, os.path.isdir(os.path.join(path, name))))
            except OSError:
                continue
        wrapper = {"entries": entries, "index": -1}
        key = id(wrapper)
        self._vfs_handles[key] = wrapper
        return key

    def _vfs_readdir(self, handle) -> bool:
        wrapper = self._vfs_handles.get(handle)
        if wrapper is None:
            return False
        wrapper["index"] += 1
        return wrapper["index"] < len(wrapper["entries"])

    def _vfs_dirent_name(self, handle) -> bytes:
        wrapper = self._vfs_handles.get(handle)
        if not wrapper or not (0 <= wrapper["index"] < len(wrapper["entries"])):
            return b""
        return wrapper["entries"][wrapper["index"]][0].encode()

    def _vfs_dirent_is_dir(self, handle) -> bool:
        wrapper = self._vfs_handles.get(handle)
        if not wrapper or not (0 <= wrapper["index"] < len(wrapper["entries"])):
            return False
        return wrapper["entries"][wrapper["index"]][1]

    def _vfs_closedir(self, handle) -> int:
        return 0 if self._vfs_handles.pop(handle, None) is not None else -1

    def _get_current_framebuffer(self) -> int:
        """Called by a GL core, possibly every frame: which FBO to draw into."""
        return int(self._fbo.handle()) if self._fbo is not None else 0

    def _get_proc_address(self, symbol) -> int:
        """Called by a GL core to resolve its own GL entry points."""
        if not symbol or self._gl_context is None:
            return 0
        addr = self._gl_context.getProcAddress(bytes(symbol))
        return int(addr) if addr else 0

    def _on_video(self, data, width: int, height: int, pitch: int):
        if self.hw_render_active and data == _HW_FRAME_BUFFER_VALID:
            self._read_hw_frame(width, height)
            return
        if not data:
            return  # frame duping: the previous frame still stands
        with self._lock:
            self.frame = ct.string_at(data, pitch * height)
            self.frame_width = width
            self.frame_height = height
            self.frame_pitch = pitch

    def _on_audio_sample(self, left: int, right: int):
        with self._lock:
            self._audio += int(left & 0xFFFF).to_bytes(2, "little")
            self._audio += int(right & 0xFFFF).to_bytes(2, "little")

    def _on_audio_batch(self, data, frames: int) -> int:
        with self._lock:
            self._audio += ct.string_at(data, frames * 4)  # stereo int16
        return frames

    def _on_input_poll(self):
        pass  # input is pushed by the widget, nothing to sample here

    def _on_input_state(self, port: int, device: int, index: int, button: int) -> int:
        if _INPUT_DEBUG:
            _input_debug_tally(port, device, index, button, self._axes)
        if port != 0:
            return 0
        if device == DEVICE_JOYPAD:
            if button == JOYPAD_MASK:
                mask = 0
                for code in self._pressed:
                    mask |= 1 << code
                # retro_input_state_t returns int16_t even for the unsigned
                # 16-bit mask. Preserve its bit pattern when R3 (bit 15) is on.
                return ct.c_int16(mask).value
            return 1 if button in self._pressed else 0
        if device == DEVICE_ANALOG:
            return self._axes.get((index, button), 0)
        if device == DEVICE_POINTER:
            if button == ID_POINTER_X:
                return self._pointer_x
            if button == ID_POINTER_Y:
                return self._pointer_y
            if button == ID_POINTER_PRESSED:
                return 1 if self._pointer_pressed else 0
            return 0
        return 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _read_av_info(self):
        av = _AVInfo()
        self._lib.retro_get_system_av_info(ct.byref(av))
        self.fps = float(av.timing.fps) or 60.0
        self.sample_rate = float(av.timing.sample_rate) or 32000.0
        self.aspect_ratio = float(av.geometry.aspect_ratio) or (
            av.geometry.base_width / max(1, av.geometry.base_height)
        )
        if not self.frame_width:
            self.frame_width = int(av.geometry.base_width)
            self.frame_height = int(av.geometry.base_height)

    def load_game(self, rom_path: str | Path, device: int = DEVICE_JOYPAD) -> None:
        rom = Path(rom_path)
        if not rom.is_file():
            raise LibretroError(f"No existe la ROM: {rom}")

        # Both values are kept on self because native cores may retain these
        # pointers after retro_load_game returns. Cores with need_fullpath set
        # must open the file themselves; libretro requires data=NULL/size=0.
        self._rom_path = str(rom.absolute()).encode()
        if self.need_fullpath:
            self._rom_buffer = None
            data = None
            size = 0
        else:
            blob = rom.read_bytes()
            self._rom_buffer = ct.create_string_buffer(blob, len(blob))
            data = ct.cast(self._rom_buffer, c_void_p)
            size = len(blob)
        info = _GameInfo(
            path=self._rom_path,
            data=data,
            size=size,
            meta=None,
        )
        if not self._lib.retro_load_game(ct.byref(info)):
            raise LibretroError("El core rechazó la ROM")
        self._loaded = True
        # `device` defaults to JOYPAD and every caller currently leaves it at
        # that: passing DEVICE_ANALOG here to fix PS2/N64 sticks was tried and
        # reverted — PCSX2's core took it as "nothing plugged into this port"
        # and dropped digital buttons too (keyboard and the phone pad both
        # stopped working, BIOS reported no controller). Whatever fixes analog
        # sticks on those cores, it isn't this call.
        self._lib.retro_set_controller_port_device(0, device)
        self._read_av_info()

    def _init_hw_render(self) -> None:
        fmt = QSurfaceFormat()
        major, minor = self._hw_version
        if self._hw_context_type == HW_CONTEXT_OPENGL_CORE:
            fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
            fmt.setVersion(max(major, 3), minor if major >= 3 else 3)
        else:
            fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
            if major:
                fmt.setVersion(major, minor)
        if self._hw_depth:
            fmt.setDepthBufferSize(24)
        if self._hw_stencil:
            fmt.setStencilBufferSize(8)

        self._gl_surface = QOffscreenSurface()
        self._gl_surface.setFormat(fmt)
        self._gl_surface.create()
        if not self._gl_surface.isValid():
            raise LibretroError("No pude crear la superficie OpenGL para este core")

        self._gl_context = QOpenGLContext()
        self._gl_context.setFormat(fmt)
        if not self._gl_context.create():
            raise LibretroError("No pude crear el contexto OpenGL para este core")
        if not self._gl_context.makeCurrent(self._gl_surface):
            raise LibretroError("No pude activar el contexto OpenGL")

        fbo_format = QOpenGLFramebufferObjectFormat()
        if self._hw_depth and self._hw_stencil:
            attachment = QOpenGLFramebufferObject.Attachment.CombinedDepthStencil
        elif self._hw_depth:
            attachment = QOpenGLFramebufferObject.Attachment.Depth
        else:
            attachment = QOpenGLFramebufferObject.Attachment.NoAttachment
        fbo_format.setAttachment(attachment)
        self._fbo = QOpenGLFramebufferObject(_HW_FBO_SIZE, _HW_FBO_SIZE, fbo_format)
        if not self._fbo.isValid():
            raise LibretroError("No pude crear el framebuffer para este core")

        # glBindFramebuffer/glReadPixels: resolved via the context rather than
        # loaded from a fixed DLL because on Windows opengl32.dll only exports
        # GL 1.1 statically — anything from GL 3.0's FBO extension has to come
        # through wglGetProcAddress, which is exactly what getProcAddress does.
        bind_addr = self._gl_context.getProcAddress(b"glBindFramebuffer")
        read_addr = self._gl_context.getProcAddress(b"glReadPixels")
        if not bind_addr or not read_addr:
            raise LibretroError("El driver de vídeo no expone las funciones GL necesarias")
        self._gl_bind_framebuffer = CFUNCTYPE(None, c_uint, c_uint)(int(bind_addr))
        self._gl_read_pixels = CFUNCTYPE(
            None, c_int, c_int, c_int, c_int, c_uint, c_uint, c_void_p
        )(int(read_addr))

        self.hw_render_active = True
        # context_reset() is called by the caller (_on_environment), once it
        # has also finished writing get_current_framebuffer/get_proc_address
        # into the core's struct — calling it here would run it a step early.

    def _read_hw_frame(self, width: int, height: int) -> None:
        if self._gl_context is None or self._fbo is None:
            return
        w = max(0, min(int(width) or self._fbo.width(), self._fbo.width()))
        h = max(0, min(int(height) or self._fbo.height(), self._fbo.height()))
        if not w or not h:
            return
        if getattr(self, "_hw_buffer_shape", (0, 0)) != (w, h):
            size = w * h * 4
            self._hw_readback_buffer = (ct.c_ubyte * size)()
            self._hw_frame_buffer = bytearray(size)
            self._hw_buffer_shape = (w, h)
        try:
            self._gl_context.makeCurrent(self._gl_surface)
            _GL_FRAMEBUFFER = 0x8D40
            _GL_BGRA = 0x80E1       # matches QImage.Format_RGB32's byte order
            _GL_UNSIGNED_BYTE = 0x1401
            self._gl_bind_framebuffer(_GL_FRAMEBUFFER, self._fbo.handle())
            self._gl_read_pixels(0, 0, w, h, _GL_BGRA, _GL_UNSIGNED_BYTE,
                                 ct.cast(self._hw_readback_buffer, c_void_p))
            # glReadPixels always returns bottom-up rows (OpenGL's window-space
            # origin is bottom-left); QImage expects top-down.  np.copyto writes
            # into the persistent bytearray without creating a frame-sized
            # temporary bytes object.
            source = np.frombuffer(
                self._hw_readback_buffer, dtype=np.uint8
            ).reshape(h, w, 4)
            target = np.frombuffer(
                self._hw_frame_buffer, dtype=np.uint8
            ).reshape(h, w, 4)
            np.copyto(target, source[::-1])
        except Exception:
            return
        with self._lock:
            self.frame = self._hw_frame_buffer
            self.frame_width = w
            self.frame_height = h
            self.frame_pitch = w * 4
            self.pixel_format = PIXEL_XRGB8888

    def run_frame(self) -> None:
        if not self._loaded:
            return
        if self.hw_render_active:
            # The core issues GL calls straight out of retro_run(), so the
            # context has to be current on this thread first. Cheap to repeat
            # every frame and safer than trusting it stayed current across
            # whatever the caller did between frames.
            self._gl_context.makeCurrent(self._gl_surface)
        self._lib.retro_run()

    def reset(self) -> None:
        if self._loaded:
            self._lib.retro_reset()

    def unload(self) -> None:
        # A hardware core's context_destroy callback can pause its emulation
        # thread and close the GS. It must run while that thread still exists;
        # LRPS2's retro_unload_game() joins the thread, so calling the callback
        # afterwards deadlocks forever in cpu_thread_pause().
        self._teardown_hw_render()
        if self._loaded:
            try:
                self._lib.retro_unload_game()
            except Exception:
                pass
            self._loaded = False
        for handle in list(self._vfs_handles.values()):
            try:
                if hasattr(handle, "close"):
                    handle.close()
            except Exception:
                pass
        self._vfs_handles.clear()
        try:
            self._lib.retro_deinit()
        except Exception:
            pass
        self._free_library()

    def _teardown_hw_render(self) -> None:
        if self._gl_context is None:
            self._hw_readback_buffer = None
            self._hw_frame_buffer = None
            self._hw_buffer_shape = (0, 0)
            return
        try:
            self._gl_context.makeCurrent(self._gl_surface)
            if self._hw_context_destroy_fn:
                self._hw_context_destroy_fn()
        except Exception:
            pass
        self._fbo = None
        try:
            self._gl_context.doneCurrent()
        except Exception:
            pass
        self._gl_context = None
        self._gl_surface = None
        self._hw_readback_buffer = None
        self._hw_frame_buffer = None
        self._hw_buffer_shape = (0, 0)
        self.hw_render_active = False

    def _free_library(self) -> None:
        """Drop the DLL itself, not just the core's session.

        ``retro_deinit`` is not enough: ctypes caches a loaded library by path,
        so a second ``load()`` of the same core reuses the still-resident image.
        Some cores never fully reset their globals under that — ParaLLEl N64
        comes back alive but emulating nothing, which looks like a black screen
        after playing one game and starting another. Releasing the OS handle
        forces the next load to start from a clean image.
        """
        lib, self._lib = getattr(self, "_lib", None), None
        if lib is None:
            return
        handle = getattr(lib, "_handle", None)
        if handle is None:
            return
        try:
            if os.name == "nt":
                free = ct.windll.kernel32.FreeLibrary
                free.argtypes = [ct.c_void_p]
                free(ct.c_void_p(handle))
            else:
                dl = ct.CDLL(None)
                dl.dlclose.argtypes = [ct.c_void_p]
                dl.dlclose(ct.c_void_p(handle))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Frontend-facing state
    # ------------------------------------------------------------------

    def take_audio(self) -> bytes:
        """Drain every audio sample produced since the last call (S16 stereo)."""
        with self._lock:
            data = bytes(self._audio)
            self._audio.clear()
        return data

    def current_frame(self) -> tuple[Optional[bytes | bytearray], int, int, int, int]:
        with self._lock:
            return (self.frame, self.frame_width, self.frame_height,
                    self.frame_pitch, self.pixel_format)

    def set_button(self, button: str, pressed: bool) -> None:
        code = JOYPAD.get(button)
        if code is None:
            return
        if pressed:
            self._pressed.add(code)
        else:
            self._pressed.discard(code)

    def set_axis(self, index: int, axis: int, value: int) -> None:
        """Set one libretro analog axis (-32767..32767) for controller 1."""
        key = (int(index), int(axis))
        value = max(-32767, min(32767, int(value)))
        if value:
            self._axes[key] = value
        else:
            self._axes.pop(key, None)

    def set_pointer(self, x: int, y: int, pressed: bool) -> None:
        """Set the libretro pointer device (-0x7fff..0x7fff, full framebuffer)."""
        self._pointer_x = max(-0x7fff, min(0x7fff, int(x)))
        self._pointer_y = max(-0x7fff, min(0x7fff, int(y)))
        self._pointer_pressed = bool(pressed)

    def clear_input(self) -> None:
        self._pressed.clear()
        self._axes.clear()
        self._pointer_x = 0
        self._pointer_y = 0
        self._pointer_pressed = False

    def set_option(self, key: str, value: str) -> None:
        """Changes a core option after load and flags it for the core to
        re-read via RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE (see that branch
        of _on_environment). Whether the core actually acts on it is a
        per-core question, not a given from the option existing — measured
        directly against real running sessions, not assumed:

        - azahar_libretro (3DS): WORKS. citra_resolution_factor changed
          from a running session's own frame dimensions within 2 polls
          (~66ms) — the standalone Azahar app's live resolution-scale
          change is backed by the same underlying poll here.
        - pcsx2_libretro (PS2): does NOT pick this up. Called
          pcsx2_upscale_multiplier with a different value against a real
          running session; the frame stayed at its original dimensions —
          this core doesn't poll GET_VARIABLE_UPDATE, at least not for that
          option, so it needs a restart to apply a change like every other
          core did before this method existed.

        A core that ignores GET_VARIABLE_UPDATE just silently keeps using
        the old value until the next full restart — this is best-effort,
        not a guarantee, and callers (actions/emulator_runtime.py's
        set_resolution) must keep persisting the choice for that restart
        regardless of whether this had any live effect."""
        self._options[key.encode()] = str(value).encode()
        self._options_dirty = True

    # ------------------------------------------------------------------
    # Save states + battery saves
    # ------------------------------------------------------------------

    def save_state(self) -> bytes:
        size = int(self._lib.retro_serialize_size())
        if not size:
            raise LibretroError("Este core no soporta guardado rápido")
        buf = ct.create_string_buffer(size)
        if not self._lib.retro_serialize(ct.cast(buf, c_void_p), size):
            raise LibretroError("No pude guardar el estado")
        return buf.raw

    def load_state(self, blob: bytes) -> None:
        buf = ct.create_string_buffer(blob, len(blob))
        if not self._lib.retro_unserialize(ct.cast(buf, c_void_p), len(blob)):
            raise LibretroError("El estado guardado no es válido para esta ROM")

    def read_sram(self) -> bytes:
        """The cartridge's battery-backed save (what an in-game 'Save' writes)."""
        size = int(self._lib.retro_get_memory_size(MEMORY_SAVE_RAM))
        ptr = self._lib.retro_get_memory_data(MEMORY_SAVE_RAM)
        if not size or not ptr:
            return b""
        return ct.string_at(ptr, size)

    def write_sram(self, blob: bytes) -> None:
        size = int(self._lib.retro_get_memory_size(MEMORY_SAVE_RAM))
        ptr = self._lib.retro_get_memory_data(MEMORY_SAVE_RAM)
        if not size or not ptr or not blob:
            return
        ct.memmove(ptr, blob, min(size, len(blob)))


# ---------------------------------------------------------------------------
# Single-instance management
# ---------------------------------------------------------------------------

_active: Optional[LibretroCore] = None


def load(core_path: str | Path, system_dir: str | Path,
         save_dir: str | Path, options: Optional[dict] = None) -> LibretroCore:
    """Load a core, replacing whatever was loaded before.

    Cores keep process-global state and ctypes reuses the same DLL handle for a
    given path, so two live instances would fight over it. Unloading first
    keeps that from happening.
    """
    global _active
    if _active is not None:
        _active.unload()
        _active = None
    _active = LibretroCore(core_path, system_dir, save_dir, options=options)
    return _active


def active() -> Optional[LibretroCore]:
    return _active


def unload() -> None:
    global _active
    if _active is not None:
        _active.unload()
        _active = None
