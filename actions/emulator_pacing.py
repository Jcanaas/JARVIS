"""Frame pacing shared by the Qt widget (ui/widgets/retro.py) and the headless
web session (actions/emulator_session.py). Pure — no Qt import here, so the
headless path doesn't have to touch ui.widgets.retro to get it.

Pacing is elapsed-time driven, not tick-driven: a 16ms timer doesn't fire at
59.7275 Hz, and the drift is audible long before it's visible. Each call
returns however many frames are actually due since the last call, capped so a
stall can't turn into a burst of fast-forward.
"""
from __future__ import annotations


def advance_frame_debt(debt: float, elapsed: float, fps: float,
                        max_catchup: int = 4) -> tuple[int, float]:
    """Return (frames due, remaining debt), dropping backlog past max_catchup."""
    debt = min(max(0.0, debt + max(0.0, elapsed) * max(1.0, fps)),
               float(max_catchup))
    due = int(debt)
    return due, debt - due
