"""Controller bindings for the emulator — keyboard and gamepad, persisted.

One console button can be reached from several places at once (the D-pad and
the left stick, Z and the Xbox A button), so every binding is a *list* of
inputs rather than a single one. That is also what makes rebinding safe: adding
a pad binding never silently removes the keyboard one.

Keyboard bindings are stored as Qt key codes and pad bindings as the signal ids
from actions/gamepad.py. Neither side is interpreted here; this module only
owns the mapping and its persistence, so the UI can rebind by capturing
whatever the user pressed without knowing what kind of device produced it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from actions import app_settings

_SETTINGS_KEY = "emulator_input"
_SETTINGS_VERSION = 2

# A libretro RetroPad is shared by every core, but the machines connected to
# it are not.  Keep the underlying ids stable and present the controls that
# actually exist on each console (including the names printed on its pad).
_TWO_BUTTON: list[tuple[str, str]] = [
    ("up", "Arriba"),
    ("down", "Abajo"),
    ("left", "Izquierda"),
    ("right", "Derecha"),
    ("a", "A"),
    ("b", "B"),
    ("start", "Start"),
    ("select", "Select"),
]
_GBA: list[tuple[str, str]] = [
    *_TWO_BUTTON[:6],
    ("l", "L"),
    ("r", "R"),
    ("start", "Start"),
    ("select", "Select"),
]
_SIX_BUTTON: list[tuple[str, str]] = [
    *_TWO_BUTTON[:4],
    ("a", "A"), ("b", "B"), ("x", "X"), ("y", "Y"),
    ("l", "L"), ("r", "R"),
    ("start", "Start"), ("select", "Select"),
]
_PS2: list[tuple[str, str]] = [
    *_TWO_BUTTON[:4],
    ("b", "Cruz"), ("a", "Círculo"),
    ("y", "Cuadrado"), ("x", "Triángulo"),
    ("l", "L1"), ("r", "R1"), ("l2", "L2"), ("r2", "R2"),
    ("l3", "L3"), ("r3", "R3"),
    ("start", "Start"), ("select", "Select"),
]
_MEGA_DRIVE: list[tuple[str, str]] = [
    *_TWO_BUTTON[:4],
    ("y", "A"), ("b", "B"), ("a", "C"),
    ("x", "X"), ("l", "Y"), ("r", "Z"),
    ("start", "Start"), ("select", "Mode"),
]
_N64: list[tuple[str, str]] = [
    *_TWO_BUTTON[:4],
    ("b", "A"), ("y", "B"),
    ("l", "L"), ("r", "R"), ("l2", "Z"),
    ("start", "Start"),
]
_PCE: list[tuple[str, str]] = [
    *_TWO_BUTTON[:4],
    ("a", "I"), ("b", "II"),
    ("start", "Run"), ("select", "Select"),
]
_SEGA_8BIT: list[tuple[str, str]] = [
    *_TWO_BUTTON[:4],
    ("b", "1"), ("a", "2"), ("start", "Start"),
]

_BUTTON_ORDERS: dict[str, list[tuple[str, str]]] = {
    "gba": _GBA,
    "gb": _TWO_BUTTON,
    "gbc": _TWO_BUTTON,
    "nes": _TWO_BUTTON,
    "snes": _SIX_BUTTON,
    "md": _MEGA_DRIVE,
    "sms": _SEGA_8BIT,
    "gg": _SEGA_8BIT,
    "pce": _PCE,
    "ngpc": _TWO_BUTTON,
    "n64": _N64,
    "ps2": _PS2,
}

# Compatibility for callers outside the emulator UI. New code must ask for a
# console explicitly through button_order().
BUTTON_ORDER = _GBA
BUTTON_IDS = [button for button, _ in BUTTON_ORDER]


def button_order(console_id: str = "gba") -> list[tuple[str, str]]:
    return list(_BUTTON_ORDERS.get(console_id, _GBA))

# Qt.Key values, kept as plain ints so this module never imports Qt — it is
# used from actions/ code and tests that run headless.
_KEY_UP, _KEY_DOWN, _KEY_LEFT, _KEY_RIGHT = 0x01000013, 0x01000015, 0x01000012, 0x01000014
_KEY_RETURN, _KEY_ENTER = 0x01000004, 0x01000005
_KEY_BACKSPACE, _KEY_SHIFT = 0x01000003, 0x01000020
_KEY_A, _KEY_Q, _KEY_S, _KEY_W, _KEY_X, _KEY_Z = (
    0x41, 0x51, 0x53, 0x57, 0x58, 0x5A
)
_KEY_SPACE = 0x20

_BASE_KEYBOARD: dict[str, list[int]] = {
    "up": [_KEY_UP],
    "down": [_KEY_DOWN],
    "left": [_KEY_LEFT],
    "right": [_KEY_RIGHT],
    "a": [_KEY_X, _KEY_SPACE],
    "b": [_KEY_Z],
    "x": [_KEY_S],
    "y": [_KEY_A],
    "l": [_KEY_Q],
    "r": [_KEY_W],
    "l2": [0x31],
    "r2": [0x32],
    "l3": [0x33],
    "r3": [0x34],
    "start": [_KEY_RETURN, _KEY_ENTER],
    "select": [_KEY_BACKSPACE, _KEY_SHIFT],
}

# A and B are crossed on purpose: the GBA's A is the *right* button and B the
# left one, which on an Xbox pad is B and A respectively. Binding them by name
# instead of by position is the classic way to get them backwards.
_BASE_PAD: dict[str, list[str]] = {
    "up": ["xinput:dpup", "xinput:lsup", "hid:hatup"],
    "down": ["xinput:dpdown", "xinput:lsdown", "hid:hatdown"],
    "left": ["xinput:dpleft", "xinput:lsleft", "hid:hatleft"],
    "right": ["xinput:dpright", "xinput:lsright", "hid:hatright"],
    "a": ["xinput:b"],
    "b": ["xinput:a"],
    "x": ["xinput:y"],
    "y": ["xinput:x"],
    "l": ["xinput:lb"],
    "r": ["xinput:rb"],
    "l2": ["xinput:lt"],
    "r2": ["xinput:rt"],
    "l3": ["xinput:lstick"],
    "r3": ["xinput:rstick"],
    "start": ["xinput:start"],
    "select": ["xinput:back"],
}


def _profile_defaults(console_id: str) -> tuple[dict[str, list[int]],
                                                 dict[str, list[str]]]:
    ids = [button for button, _ in button_order(console_id)]
    keyboard = {button: list(_BASE_KEYBOARD.get(button, [])) for button in ids}
    pad = {button: list(_BASE_PAD.get(button, [])) for button in ids}
    if console_id == "gba":
        # Preserve the original, comfortable shoulder layout for GBA.
        keyboard["l"], keyboard["r"] = [_KEY_A], [_KEY_S]
        pad["l"] = ["xinput:lb", "xinput:lt"]
        pad["r"] = ["xinput:rb", "xinput:rt"]
    if console_id in ("n64", "ps2"):
        # Those machines have a real analogue stick. Using it as a second
        # D-pad as on handhelds would press both controls at the same time.
        for direction in ("up", "down", "left", "right"):
            pad[direction] = [signal for signal in pad[direction]
                              if not signal.startswith("xinput:ls")]
    return keyboard, pad


@dataclass
class Bindings:
    keyboard: dict[str, list[int]] = field(default_factory=dict)
    pad: dict[str, list[str]] = field(default_factory=dict)

    # -- lookups used by the running emulator -------------------------

    def keymap(self) -> dict[int, str]:
        """Qt key code -> button, the direction the key handler needs."""
        out: dict[int, str] = {}
        for button, keys in self.keyboard.items():
            for key in keys:
                out[int(key)] = button
        return out

    def padmap(self) -> dict[str, str]:
        """Signal id -> button."""
        out: dict[str, str] = {}
        for button, signals in self.pad.items():
            for signal in signals:
                out[str(signal)] = button
        return out

    # -- editing ------------------------------------------------------

    def bind_key(self, button: str, key: int, replace: bool = True) -> None:
        self._bind(self.keyboard, button, int(key), replace)

    def bind_signal(self, button: str, signal: str, replace: bool = True) -> None:
        self._bind(self.pad, button, str(signal), replace)

    @staticmethod
    def _bind(table: dict, button: str, value, replace: bool) -> None:
        # An input bound to two buttons at once would make one of them
        # unreachable, so it is taken off whatever held it before.
        for other, values in table.items():
            if value in values and other != button:
                values.remove(value)
        table[button] = [value] if replace else (
            table.get(button, []) + [value]
            if value not in table.get(button, []) else table[button]
        )

    def clear(self, button: str, kind: str) -> None:
        table = self.keyboard if kind == "keyboard" else self.pad
        table[button] = []

    def to_dict(self) -> dict:
        return {
            "keyboard": {b: [int(k) for k in keys]
                         for b, keys in self.keyboard.items()},
            "pad": {b: [str(s) for s in signals]
                    for b, signals in self.pad.items()},
        }


def defaults(console_id: str = "gba") -> Bindings:
    keyboard, pad = _profile_defaults(console_id)
    return Bindings(
        keyboard=keyboard,
        pad=pad,
    )


def load(console_id: str = "gba") -> Bindings:
    """Stored bindings, falling back to the defaults per button.

    Merging per button (not per file) matters when BUTTON_ORDER grows: a
    config written before a button existed still gets a working default for
    it instead of leaving it unbound.
    """
    bindings = defaults(console_id)
    try:
        stored = app_settings.get(_SETTINGS_KEY) or {}
    except Exception:
        stored = {}
    if not isinstance(stored, dict):
        return bindings

    # Version 1 stored one global (GBA-shaped) mapping. Treat it as GBA only;
    # loading any other console must start from that console's own defaults.
    if "profiles" in stored:
        profiles = stored.get("profiles")
        stored = profiles.get(console_id, {}) if isinstance(profiles, dict) else {}
    elif console_id != "gba":
        stored = {}

    ids = [button for button, _ in button_order(console_id)]
    keyboard = stored.get("keyboard")
    if isinstance(keyboard, dict):
        for button in ids:
            value = keyboard.get(button)
            if isinstance(value, list):
                bindings.keyboard[button] = [int(k) for k in value
                                             if isinstance(k, (int, float))]
    pad = stored.get("pad")
    if isinstance(pad, dict):
        for button in ids:
            value = pad.get(button)
            if isinstance(value, list):
                bindings.pad[button] = [str(s) for s in value if s]
    return bindings


def save(bindings: Bindings, console_id: str = "gba") -> None:
    try:
        stored = app_settings.get(_SETTINGS_KEY) or {}
    except Exception:
        stored = {}
    profiles = {}
    if isinstance(stored, dict):
        current = stored.get("profiles")
        if isinstance(current, dict):
            profiles = dict(current)
        elif "keyboard" in stored or "pad" in stored:
            profiles["gba"] = stored
    profiles[console_id] = bindings.to_dict()
    app_settings.set(_SETTINGS_KEY, {
        "version": _SETTINGS_VERSION,
        "profiles": profiles,
    })


def reset(console_id: str = "gba") -> Bindings:
    bindings = defaults(console_id)
    save(bindings, console_id)
    return bindings


def key_label(key: int) -> str:
    """Readable name for a Qt key code, without importing Qt."""
    named = {
        _KEY_UP: "↑", _KEY_DOWN: "↓", _KEY_LEFT: "←", _KEY_RIGHT: "→",
        _KEY_RETURN: "Enter", _KEY_ENTER: "Intro",
        _KEY_BACKSPACE: "Retroceso", _KEY_SHIFT: "Shift",
        _KEY_SPACE: "Espacio", 0x01000001: "Tab", 0x01000000: "Esc",
    }
    if key in named:
        return named[key]
    if 0x20 < key < 0x7F:
        return chr(key).upper()
    return f"0x{key:X}"
