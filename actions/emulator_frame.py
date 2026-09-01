"""Frame-format conversion for the headless MJPEG stream in
actions/webui_server.py.

Reads the exact wire format actions/libretro.py's current_frame() produces
and returns a packed BGR uint8 array — OpenCV's native channel order, so
cv2.imencode needs no further conversion. Pure numpy, no Qt: kept separate
from ui/widgets/retro.py's _build_image, which builds a QImage for on-screen
painting from the same source format for a different (Qt) consumer.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from actions.libretro import PIXEL_0RGB1555, PIXEL_RGB565, PIXEL_XRGB8888


def frame_to_bgr(data, width: int, height: int, pitch: int,
                  pixel_format: int) -> Optional[np.ndarray]:
    """Returns a contiguous (height, width, 3) uint8 BGR array, or None if
    there's no frame yet (data/width/height falsy)."""
    if not data or not width or not height:
        return None

    buf = np.frombuffer(bytes(data), dtype=np.uint8)

    if pixel_format == PIXEL_XRGB8888:
        # In-memory byte order is B,G,R,X (see actions/libretro.py's
        # _GL_BGRA / QImage.Format_RGB32 note) — already BGR, just drop the
        # 4th byte instead of swapping channels.
        rows = buf[: height * pitch].reshape(height, pitch // 4, 4)[:, :width, :]
        return np.ascontiguousarray(rows[:, :, :3])

    if pixel_format == PIXEL_0RGB1555:
        px = buf[: height * pitch].view(np.uint16).reshape(height, pitch // 2)[:, :width]
        r = ((px >> 10) & 0x1F) << 3
        g = ((px >> 5) & 0x1F) << 3
        b = (px & 0x1F) << 3
        return np.stack([b, g, r], axis=-1).astype(np.uint8)

    # pixel_format == PIXEL_RGB565 — mGBA's default, and the fallback for
    # any other value, matching ui/widgets/retro.py's own default branch.
    px = buf[: height * pitch].view(np.uint16).reshape(height, pitch // 2)[:, :width]
    r = ((px >> 11) & 0x1F) << 3
    g = ((px >> 5) & 0x3F) << 2
    b = (px & 0x1F) << 3
    return np.stack([b, g, r], axis=-1).astype(np.uint8)
