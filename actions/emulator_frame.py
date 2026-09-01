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


# Azahar's citra_libretro renders the 3DS top+bottom screens into one
# combined framebuffer and reads touch as RETRO_DEVICE_POINTER over that
# *whole* frame (src/citra_libretro/input/mouse_tracker.cpp), clipping
# internally against its own bottom-screen layout rect. With the default
# layout option, verified empirically against a real running core: the
# bottom (touch) screen occupies x in [10%, 90%] and y in [50%, 100%] of the
# combined frame. Proportions, not fixed pixels, so this keeps working if
# resolution_factor changes the frame size.
_BOTTOM_SCREEN_X0, _BOTTOM_SCREEN_X1 = 0.10, 0.90
_BOTTOM_SCREEN_Y0, _BOTTOM_SCREEN_Y1 = 0.50, 1.00

# RETRO_DEVICE_ID_POINTER_X/Y range over the full frame.
_POINTER_RANGE = 0x7FFF


def crop_bottom_screen_3ds(bgr: np.ndarray) -> np.ndarray:
    """Crops the 3DS touch screen out of a combined top+bottom BGR frame."""
    height, width = bgr.shape[:2]
    x0 = int(width * _BOTTOM_SCREEN_X0)
    x1 = int(width * _BOTTOM_SCREEN_X1)
    y0 = int(height * _BOTTOM_SCREEN_Y0)
    y1 = int(height * _BOTTOM_SCREEN_Y1)
    return bgr[y0:y1, x0:x1]


def crop_top_screen_3ds(bgr: np.ndarray) -> np.ndarray:
    """Crops the 3DS top (main) screen out of a combined top+bottom BGR
    frame — full width, everything above the same split point
    crop_bottom_screen_3ds() crops the touch screen below."""
    height = bgr.shape[0]
    y1 = int(height * _BOTTOM_SCREEN_Y0)
    return bgr[:y1]


def touch_to_pointer(fx: float, fy: float) -> tuple[int, int]:
    """Converts a touch fraction (0..1, 0..1) *within the bottom-screen crop*
    into the RETRO_DEVICE_POINTER coordinates (-0x7fff..0x7fff) Azahar
    expects over the full combined frame — the exact inverse of the crop
    above, followed by mouse_tracker.cpp's own normalization."""
    frame_fx = _BOTTOM_SCREEN_X0 + max(0.0, min(1.0, fx)) * (_BOTTOM_SCREEN_X1 - _BOTTOM_SCREEN_X0)
    frame_fy = _BOTTOM_SCREEN_Y0 + max(0.0, min(1.0, fy)) * (_BOTTOM_SCREEN_Y1 - _BOTTOM_SCREEN_Y0)
    px = round(frame_fx * 2 * _POINTER_RANGE - _POINTER_RANGE)
    py = round(frame_fy * 2 * _POINTER_RANGE - _POINTER_RANGE)
    return px, py
