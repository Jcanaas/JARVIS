"""Gamepad input for the emulator, with no third-party dependency.

Qt6 dropped QtGamepad and the project has no SDL/pygame, so both Windows input
stacks are driven directly through ctypes — the same approach actions/libretro.py
already takes for the core:

- **XInput** covers Xbox pads and the many third-party controllers that
  present themselves as one. Cheap to poll, fixed button layout.
- **HID** covers everything else — DualShock/DualSense, Switch Pro, arcade
  sticks, old joysticks. Rather than hard-coding each pad's report layout,
  Windows' own HID parser (``HidP_GetUsages`` / ``HidP_GetUsageValue``) is
  asked what the device reports, so a pad this code has never seen still works.

Everything a pad can do is flattened into a **signal id** — a short stable
string like ``xinput:a``, ``hid:btn3`` or ``hid:axis30-``. The frontend never
interprets these: the user binds one by pressing it (actions/input_config.py),
exactly how RetroArch does it. That is deliberate — it is the only way to
support pads that cannot be tested here, and it also means a pad with an odd
layout is a non-event instead of a bug report.

Polling runs on its own daemon thread because a HID ``ReadFile`` blocks until
the device sends a report; the UI thread only ever reads the last known state.
"""
from __future__ import annotations

import ctypes as ct
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Optional

IS_WINDOWS = hasattr(ct, "WinDLL")

# --- XInput ---------------------------------------------------------------

_XINPUT_BUTTONS = [
    (0x0001, "dpup"), (0x0002, "dpdown"), (0x0004, "dpleft"), (0x0008, "dpright"),
    (0x0010, "start"), (0x0020, "back"), (0x0040, "lstick"), (0x0080, "rstick"),
    (0x0100, "lb"), (0x0200, "rb"), (0x1000, "a"), (0x2000, "b"),
    (0x4000, "x"), (0x8000, "y"),
]
_STICK_DEADZONE = 16000
_TRIGGER_THRESHOLD = 100


def _normalise_stick(value: int) -> int:
    """Remove XInput drift and expand the remaining range to libretro S16."""
    value = int(value)
    magnitude = abs(value)
    if magnitude <= _STICK_DEADZONE:
        return 0
    scaled = round((magnitude - _STICK_DEADZONE) * 32767
                   / (32767 - _STICK_DEADZONE))
    return max(-32767, min(32767, scaled if value > 0 else -scaled))

# Human labels for the signal ids, used by the bindings UI.
SIGNAL_LABELS = {
    "xinput:a": "A", "xinput:b": "B", "xinput:x": "X", "xinput:y": "Y",
    "xinput:lb": "LB", "xinput:rb": "RB", "xinput:lt": "LT", "xinput:rt": "RT",
    "xinput:start": "Start", "xinput:back": "Back",
    "xinput:lstick": "L3", "xinput:rstick": "R3",
    "xinput:dpup": "Cruceta ↑", "xinput:dpdown": "Cruceta ↓",
    "xinput:dpleft": "Cruceta ←", "xinput:dpright": "Cruceta →",
    "xinput:lsup": "Stick I ↑", "xinput:lsdown": "Stick I ↓",
    "xinput:lsleft": "Stick I ←", "xinput:lsright": "Stick I →",
}

_HAT_DIRS = ("up", "upright", "right", "downright",
             "down", "downleft", "left", "upleft")
# "Hat", not "Cruceta": a pad can expose both an XInput D-pad and a HID hat,
# and two bindings reading identically in the UI look like a duplicate.
_HAT_LABELS = {"up": "Hat ↑", "down": "Hat ↓",
               "left": "Hat ←", "right": "Hat →"}


def signal_label(signal: str) -> str:
    """Readable name for a signal id, for the bindings screen."""
    if not signal:
        return "—"
    if signal in SIGNAL_LABELS:
        return SIGNAL_LABELS[signal]
    if signal.startswith("hid:btn"):
        return f"Botón {signal[7:]}"
    if signal.startswith("hid:hat"):
        return _HAT_LABELS.get(signal[7:], f"Cruceta {signal[7:]}")
    if signal.startswith("hid:axis"):
        body = signal[8:]
        sign = "+" if body.endswith("+") else "-"
        usage = body[:-1]
        names = {"30": "X", "31": "Y", "32": "Z", "33": "Rx", "34": "Ry", "35": "Rz"}
        return f"Eje {names.get(usage, usage)}{sign}"
    return signal


class _XINPUT_GAMEPAD(ct.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ct.c_ubyte), ("bRightTrigger", ct.c_ubyte),
        ("sThumbLX", ct.c_short), ("sThumbLY", ct.c_short),
        ("sThumbRX", ct.c_short), ("sThumbRY", ct.c_short),
    ]


class _XINPUT_STATE(ct.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _XINPUT_GAMEPAD)]


def _load_xinput():
    if not IS_WINDOWS:
        return None
    for name in ("XInput1_4.dll", "xinput1_3.dll", "XInput9_1_0.dll"):
        try:
            lib = ct.WinDLL(name)
        except OSError:
            continue
        lib.XInputGetState.argtypes = [wintypes.DWORD, ct.POINTER(_XINPUT_STATE)]
        lib.XInputGetState.restype = wintypes.DWORD
        return lib
    return None


# --- HID ------------------------------------------------------------------

_HIDP_STATUS_SUCCESS = 0x00110000
_HIDP_INPUT = 0
_USAGE_PAGE_GENERIC = 0x01
_USAGE_PAGE_BUTTON = 0x09
_USAGE_HAT = 0x39
_AXIS_USAGES = (0x30, 0x31, 0x32, 0x33, 0x34, 0x35)  # X Y Z Rx Ry Rz
_GAMEPAD_USAGES = (0x04, 0x05)  # Joystick, Game Pad


class _GUID(ct.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                ("d3", wintypes.WORD), ("d4", ct.c_ubyte * 8)]


class _SP_DEVICE_INTERFACE_DATA(ct.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", _GUID),
                ("Flags", wintypes.DWORD), ("Reserved", ct.POINTER(wintypes.ULONG))]


class _HIDD_ATTRIBUTES(ct.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class _HIDP_CAPS(ct.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT), ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class _RANGE(ct.Structure):
    _fields_ = [("UsageMin", wintypes.USHORT), ("UsageMax", wintypes.USHORT),
                ("StringMin", wintypes.USHORT), ("StringMax", wintypes.USHORT),
                ("DesignatorMin", wintypes.USHORT), ("DesignatorMax", wintypes.USHORT),
                ("DataIndexMin", wintypes.USHORT), ("DataIndexMax", wintypes.USHORT)]


class _NOTRANGE(ct.Structure):
    _fields_ = [("Usage", wintypes.USHORT), ("Reserved1", wintypes.USHORT),
                ("StringIndex", wintypes.USHORT), ("Reserved2", wintypes.USHORT),
                ("DesignatorIndex", wintypes.USHORT), ("Reserved3", wintypes.USHORT),
                ("DataIndex", wintypes.USHORT), ("Reserved4", wintypes.USHORT)]


class _UNION(ct.Union):
    _fields_ = [("Range", _RANGE), ("NotRange", _NOTRANGE)]


class _HIDP_VALUE_CAPS(ct.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT), ("ReportID", ct.c_ubyte),
        ("IsAlias", ct.c_ubyte), ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT), ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT), ("IsRange", ct.c_ubyte),
        ("IsStringRange", ct.c_ubyte), ("IsDesignatorRange", ct.c_ubyte),
        ("IsAbsolute", ct.c_ubyte), ("HasNull", ct.c_ubyte),
        ("Reserved", ct.c_ubyte), ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT), ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG), ("Units", wintypes.ULONG),
        ("LogicalMin", ct.c_long), ("LogicalMax", ct.c_long),
        ("PhysicalMin", ct.c_long), ("PhysicalMax", ct.c_long),
        ("u", _UNION),
    ]


def _load_hid():
    if not IS_WINDOWS:
        return None, None, None
    try:
        setupapi = ct.WinDLL("setupapi")
        hid = ct.WinDLL("hid")
        k32 = ct.WinDLL("kernel32")
    except OSError:
        return None, None, None

    handle = wintypes.HANDLE
    setupapi.SetupDiGetClassDevsW.argtypes = [
        ct.POINTER(_GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiGetClassDevsW.restype = handle
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        handle, ct.c_void_p, ct.POINTER(_GUID), wintypes.DWORD,
        ct.POINTER(_SP_DEVICE_INTERFACE_DATA)]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        handle, ct.POINTER(_SP_DEVICE_INTERFACE_DATA), ct.c_void_p,
        wintypes.DWORD, ct.POINTER(wintypes.DWORD), ct.c_void_p]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [handle]

    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                ct.c_void_p, wintypes.DWORD, wintypes.DWORD, handle]
    k32.CreateFileW.restype = handle
    k32.ReadFile.argtypes = [handle, ct.c_void_p, wintypes.DWORD,
                             ct.POINTER(wintypes.DWORD), ct.c_void_p]
    k32.ReadFile.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [handle]
    k32.CancelIoEx.argtypes = [handle, ct.c_void_p]

    hid.HidD_GetHidGuid.argtypes = [ct.POINTER(_GUID)]
    hid.HidD_GetAttributes.argtypes = [handle, ct.POINTER(_HIDD_ATTRIBUTES)]
    hid.HidD_GetPreparsedData.argtypes = [handle, ct.POINTER(ct.c_void_p)]
    hid.HidD_FreePreparsedData.argtypes = [ct.c_void_p]
    hid.HidD_GetProductString.argtypes = [handle, ct.c_void_p, wintypes.ULONG]
    hid.HidP_GetCaps.argtypes = [ct.c_void_p, ct.POINTER(_HIDP_CAPS)]
    hid.HidP_MaxUsageListLength.argtypes = [ct.c_int, wintypes.USHORT, ct.c_void_p]
    hid.HidP_MaxUsageListLength.restype = wintypes.ULONG
    hid.HidP_GetUsages.argtypes = [
        ct.c_int, wintypes.USHORT, wintypes.USHORT, ct.POINTER(wintypes.USHORT),
        ct.POINTER(wintypes.ULONG), ct.c_void_p, ct.c_char_p, wintypes.ULONG]
    hid.HidP_GetUsages.restype = ct.c_long
    hid.HidP_GetUsageValue.argtypes = [
        ct.c_int, wintypes.USHORT, wintypes.USHORT, wintypes.USHORT,
        ct.POINTER(wintypes.ULONG), ct.c_void_p, ct.c_char_p, wintypes.ULONG]
    hid.HidP_GetUsageValue.restype = ct.c_long
    hid.HidP_GetValueCaps.argtypes = [
        ct.c_int, ct.POINTER(_HIDP_VALUE_CAPS), ct.POINTER(wintypes.USHORT),
        ct.c_void_p]
    hid.HidP_GetValueCaps.restype = ct.c_long
    return setupapi, hid, k32


@dataclass
class PadInfo:
    name: str
    kind: str            # "xinput" | "hid"
    identifier: str      # slot number, or VID:PID
    axes: list = field(default_factory=list)


class _HidDevice:
    """One open HID pad, read on its own thread."""

    def __init__(self, hid, k32, path: str, name: str, vid: int, pid: int):
        self._hid, self._k32 = hid, k32
        self.path, self.name = path, name
        self.vid, self.pid = vid, pid
        self.handle = None
        self._pp = ct.c_void_p()
        self.report_len = 0
        self.axes: dict[int, tuple[int, int]] = {}   # usage -> (logical min, max)
        self.has_hat = False
        self._max_buttons = 0
        self.signals: set[str] = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def open(self) -> bool:
        k32, hid = self._k32, self._hid
        invalid = ct.c_void_p(-1).value
        # GENERIC_READ | FILE_SHARE_READ|WRITE — shared so this never blocks a
        # game or Steam that already has the pad open.
        handle = k32.CreateFileW(self.path, 0x80000000, 3, None, 3, 0, None)
        if not handle or handle == invalid:
            return False
        self.handle = handle
        if not hid.HidD_GetPreparsedData(handle, ct.byref(self._pp)):
            self.close()
            return False

        caps = _HIDP_CAPS()
        hid.HidP_GetCaps(self._pp, ct.byref(caps))
        self.report_len = int(caps.InputReportByteLength)
        if not self.report_len:
            self.close()
            return False
        self._max_buttons = int(
            hid.HidP_MaxUsageListLength(_HIDP_INPUT, _USAGE_PAGE_BUTTON, self._pp))

        count = wintypes.USHORT(caps.NumberInputValueCaps)
        if count.value:
            array = (_HIDP_VALUE_CAPS * count.value)()
            if hid.HidP_GetValueCaps(_HIDP_INPUT, array, ct.byref(count),
                                     self._pp) == _HIDP_STATUS_SUCCESS:
                for entry in array[:count.value]:
                    if entry.UsagePage != _USAGE_PAGE_GENERIC:
                        continue
                    usage = (entry.u.Range.UsageMin if entry.IsRange
                             else entry.u.NotRange.Usage)
                    if usage == _USAGE_HAT:
                        self.has_hat = True
                    elif usage in _AXIS_USAGES:
                        self.axes[usage] = (int(entry.LogicalMin),
                                            int(entry.LogicalMax))
        return True

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        k32, hid = self._k32, self._hid
        buf = ct.create_string_buffer(self.report_len)
        read = wintypes.DWORD()
        usage_list = (wintypes.USHORT * max(1, self._max_buttons))()
        while not self._stop.is_set():
            if not k32.ReadFile(self.handle, buf, self.report_len,
                                ct.byref(read), None):
                # Unplugged, or the handle was cancelled on shutdown.
                break
            if not read.value:
                continue
            self.signals = self._decode(buf, read.value, usage_list)
        self.signals = set()

    def _decode(self, buf, length: int, usage_list) -> set[str]:
        hid = self._hid
        out: set[str] = set()

        if self._max_buttons:
            count = wintypes.ULONG(self._max_buttons)
            if hid.HidP_GetUsages(_HIDP_INPUT, _USAGE_PAGE_BUTTON, 0, usage_list,
                                  ct.byref(count), self._pp, buf.raw,
                                  length) == _HIDP_STATUS_SUCCESS:
                for i in range(count.value):
                    out.add(f"hid:btn{usage_list[i]}")

        value = wintypes.ULONG()
        if self.has_hat:
            if hid.HidP_GetUsageValue(_HIDP_INPUT, _USAGE_PAGE_GENERIC, 0,
                                      _USAGE_HAT, ct.byref(value), self._pp,
                                      buf.raw, length) == _HIDP_STATUS_SUCCESS:
                # 0..7 clockwise from "up"; anything else is the neutral
                # position (8, or the logical-max null value).
                if value.value < 8:
                    direction = _HAT_DIRS[value.value]
                    out.add(f"hid:hat{direction}")
                    # Diagonals also fire their two component directions so a
                    # binding to "up" still works when pressed up-right.
                    for part in ("up", "down", "left", "right"):
                        if part in direction and part != direction:
                            out.add(f"hid:hat{part}")

        for usage, (low, high) in self.axes.items():
            if hid.HidP_GetUsageValue(_HIDP_INPUT, _USAGE_PAGE_GENERIC, 0, usage,
                                      ct.byref(value), self._pp, buf.raw,
                                      length) != _HIDP_STATUS_SUCCESS:
                continue
            span = high - low
            if span <= 0:
                continue
            # -1..1 around the centre, with a wide dead zone: analogue sticks
            # rest off-centre often enough that a tight one self-triggers.
            normalised = ((value.value - low) / span) * 2.0 - 1.0
            if normalised > 0.5:
                out.add(f"hid:axis{usage:02x}+")
            elif normalised < -0.5:
                out.add(f"hid:axis{usage:02x}-")
        return out

    def close(self):
        self._stop.set()
        if self.handle:
            try:
                self._k32.CancelIoEx(self.handle, None)
            except Exception:
                pass
        if self._pp:
            try:
                self._hid.HidD_FreePreparsedData(self._pp)
            except Exception:
                pass
            self._pp = ct.c_void_p()
        if self.handle:
            try:
                self._k32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None


class GamepadManager:
    """Every connected pad, collapsed into one set of active signal ids."""

    _RESCAN_SECONDS = 2.0

    def __init__(self):
        self._xinput = _load_xinput()
        self._setupapi, self._hid, self._k32 = _load_hid()
        self._devices: dict[str, _HidDevice] = {}
        self._xinput_slots: set[int] = set()
        self._last_scan = 0.0
        self._lock = threading.Lock()
        self._signals: set[str] = set()
        self._axes: dict[str, int] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ----------------------------------------------------

    def start(self):
        if self._thread is not None or not IS_WINDOWS:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
        for device in list(self._devices.values()):
            device.close()
        self._devices.clear()
        with self._lock:
            self._signals = set()
            self._axes = {}

    def _loop(self):
        while not self._stop.is_set():
            now = time.monotonic()
            if now - self._last_scan >= self._RESCAN_SECONDS:
                self._last_scan = now
                self._scan()
            active, axes = self._poll_xinput()
            for device in list(self._devices.values()):
                active |= device.signals
            with self._lock:
                self._signals = active
                self._axes = axes
            # ~250 Hz while a pad is present (finer than a 60 Hz frame, so no
            # press is missed); a lazy tick when there is nothing to read.
            self._stop.wait(0.004 if self.available() else 0.05)

    # -- XInput -------------------------------------------------------

    def _poll_xinput(self) -> tuple[set[str], dict[str, int]]:
        if self._xinput is None:
            return set(), {}
        out: set[str] = set()
        axes: dict[str, int] = {}
        # Probing an empty slot is expensive, so only known-connected slots are
        # read every pass; the periodic rescan finds new ones.
        for slot in sorted(self._xinput_slots):
            state = _XINPUT_STATE()
            if self._xinput.XInputGetState(slot, ct.byref(state)) != 0:
                continue
            pad = state.Gamepad
            for mask, name in _XINPUT_BUTTONS:
                if pad.wButtons & mask:
                    out.add(f"xinput:{name}")
            if pad.bLeftTrigger > _TRIGGER_THRESHOLD:
                out.add("xinput:lt")
            if pad.bRightTrigger > _TRIGGER_THRESHOLD:
                out.add("xinput:rt")
            if pad.sThumbLX < -_STICK_DEADZONE:
                out.add("xinput:lsleft")
            elif pad.sThumbLX > _STICK_DEADZONE:
                out.add("xinput:lsright")
            if pad.sThumbLY < -_STICK_DEADZONE:
                out.add("xinput:lsdown")
            elif pad.sThumbLY > _STICK_DEADZONE:
                out.add("xinput:lsup")
            # Libretro's Y axis is positive down; XInput's is positive up.
            # Only controller 1 is exposed by this frontend, matching port 0.
            if not axes:
                axes = {
                    "left_x": _normalise_stick(pad.sThumbLX),
                    "left_y": -_normalise_stick(pad.sThumbLY),
                    "right_x": _normalise_stick(pad.sThumbRX),
                    "right_y": -_normalise_stick(pad.sThumbRY),
                }
        return out, axes

    # -- Discovery ----------------------------------------------------

    def _scan(self):
        if self._xinput is not None:
            slots = set()
            for slot in range(4):
                state = _XINPUT_STATE()
                if self._xinput.XInputGetState(slot, ct.byref(state)) == 0:
                    slots.add(slot)
            self._xinput_slots = slots

        if self._hid is None:
            return
        try:
            found = self._enumerate_hid()
        except Exception:
            return

        for path in list(self._devices):
            if path not in found:
                self._devices.pop(path).close()
        for path, (name, vid, pid) in found.items():
            if path in self._devices:
                continue
            device = _HidDevice(self._hid, self._k32, path, name, vid, pid)
            if device.open():
                device.start()
                self._devices[path] = device
            else:
                device.close()

    def _enumerate_hid(self) -> dict[str, tuple[str, int, int]]:
        setupapi, hid, k32 = self._setupapi, self._hid, self._k32
        guid = _GUID()
        hid.HidD_GetHidGuid(ct.byref(guid))
        # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
        info = setupapi.SetupDiGetClassDevsW(ct.byref(guid), None, None, 0x12)
        invalid = ct.c_void_p(-1).value
        if not info or info == invalid:
            return {}

        found: dict[str, tuple[str, int, int]] = {}
        index = 0
        try:
            while True:
                data = _SP_DEVICE_INTERFACE_DATA()
                data.cbSize = ct.sizeof(data)
                if not setupapi.SetupDiEnumDeviceInterfaces(
                        info, None, ct.byref(guid), index, ct.byref(data)):
                    break
                index += 1
                needed = wintypes.DWORD()
                setupapi.SetupDiGetDeviceInterfaceDetailW(
                    info, ct.byref(data), None, 0, ct.byref(needed), None)
                buf = ct.create_string_buffer(max(needed.value, 8))
                # SP_DEVICE_INTERFACE_DETAIL_DATA_W.cbSize is 8 on x64 — the
                # struct is variable-length, so only this header is written.
                ct.memmove(buf, ct.byref(wintypes.DWORD(8)), 4)
                if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                        info, ct.byref(data), ct.cast(buf, ct.c_void_p),
                        needed.value, None, None):
                    continue
                path = ct.wstring_at(ct.addressof(buf) + 4)

                handle = k32.CreateFileW(path, 0, 3, None, 3, 0, None)
                if not handle or handle == invalid:
                    continue
                try:
                    caps = _HIDP_CAPS()
                    pp = ct.c_void_p()
                    if not hid.HidD_GetPreparsedData(handle, ct.byref(pp)):
                        continue
                    hid.HidP_GetCaps(pp, ct.byref(caps))
                    hid.HidD_FreePreparsedData(pp)
                    if (caps.UsagePage != _USAGE_PAGE_GENERIC
                            or caps.Usage not in _GAMEPAD_USAGES):
                        continue
                    attrs = _HIDD_ATTRIBUTES()
                    attrs.Size = ct.sizeof(attrs)
                    hid.HidD_GetAttributes(handle, ct.byref(attrs))
                    name_buf = ct.create_unicode_buffer(128)
                    hid.HidD_GetProductString(handle, name_buf, 256)
                    label = name_buf.value or f"HID {attrs.VendorID:04x}:{attrs.ProductID:04x}"
                    found[path] = (label, attrs.VendorID, attrs.ProductID)
                finally:
                    k32.CloseHandle(handle)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(info)
        return found

    # -- Public state -------------------------------------------------

    def signals(self) -> set[str]:
        """Signal ids currently active across every pad."""
        with self._lock:
            return set(self._signals)

    def axes(self) -> dict[str, int]:
        """Current XInput stick positions for libretro controller port 0."""
        with self._lock:
            return dict(self._axes)

    def pads(self) -> list[PadInfo]:
        out = [
            PadInfo(name=f"Mando XInput {slot + 1}", kind="xinput",
                    identifier=str(slot))
            for slot in sorted(self._xinput_slots)
        ]
        for device in self._devices.values():
            out.append(PadInfo(
                name=device.name, kind="hid",
                identifier=f"{device.vid:04x}:{device.pid:04x}",
                axes=sorted(device.axes),
            ))
        return out

    def available(self) -> bool:
        return bool(self._xinput_slots or self._devices)


_manager: Optional[GamepadManager] = None


def get_manager() -> GamepadManager:
    """Shared manager. Polling starts on first use and runs until shutdown."""
    global _manager
    if _manager is None:
        _manager = GamepadManager()
        _manager.start()
    return _manager


def shutdown() -> None:
    global _manager
    if _manager is not None:
        _manager.stop()
        _manager = None
