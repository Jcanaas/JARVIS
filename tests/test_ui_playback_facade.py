"""Guards the JarvisUI -> MainWindow playback facade signature.

`JarvisUI.update_playback` is a thread-safe pass-through to
`MainWindow.update_playback` (it marshals onto the GUI thread via a signal).
Because every call site is wrapped in `except Exception: pass`, adding a
parameter to only one of the two silently kills ALL playback updates in the
desktop UI instead of raising — the progress bar and transport controls just
stop appearing, with nothing in the logs. That regression has happened; this
test makes the two signatures stay in lockstep.

Parsed with `ast` rather than imported so the check doesn't need a Qt runtime.
"""

import ast
import unittest
from pathlib import Path

UI_INIT = Path(__file__).resolve().parent.parent / "ui" / "__init__.py"


def _update_playback_signatures():
    # utf-8-sig: ui/__init__.py carries a BOM, which plain utf-8 leaves in the
    # first token and makes ast.parse() choke.
    tree = ast.parse(UI_INIT.read_text(encoding="utf-8-sig"))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "update_playback":
                    found[node.name] = [a.arg for a in child.args.args]
    return found


class PlaybackFacadeSignatureTests(unittest.TestCase):
    def test_both_update_playback_definitions_exist(self):
        sigs = _update_playback_signatures()
        self.assertIn("MainWindow", sigs)
        self.assertIn("JarvisUI", sigs)

    def test_facade_accepts_everything_the_window_accepts(self):
        sigs = _update_playback_signatures()
        self.assertEqual(
            sigs["JarvisUI"],
            sigs["MainWindow"],
            "JarvisUI.update_playback must mirror MainWindow.update_playback; "
            "a mismatch silently breaks desktop playback updates.",
        )

    def test_facade_forwards_every_parameter_into_the_signal_payload(self):
        """A parameter the facade accepts but never forwards is just as broken
        as one it rejects — the window would keep its stale value forever."""
        tree = ast.parse(UI_INIT.read_text(encoding="utf-8-sig"))
        payload_keys = None
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ClassDef) and node.name == "JarvisUI"):
                continue
            for child in node.body:
                if not (isinstance(child, ast.FunctionDef) and child.name == "update_playback"):
                    continue
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Dict):
                        payload_keys = {
                            k.value for k in sub.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)
                        }
        self.assertIsNotNone(payload_keys, "no signal payload dict found in JarvisUI.update_playback")

        params = set(_update_playback_signatures()["JarvisUI"]) - {"self", "video_id"}
        missing = params - payload_keys
        self.assertFalse(
            missing,
            f"JarvisUI.update_playback accepts {sorted(missing)} but never puts them in the "
            "signal payload, so MainWindow never receives them.",
        )


if __name__ == "__main__":
    unittest.main()
