from __future__ import annotations

import json
import html as html_lib
import base64
import hashlib
import math
import mimetypes
import os
import platform
import random
import subprocess
import sys
import tempfile
import threading
import time
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import requests

from PyQt6.QtCore import (
    QBuffer, QByteArray, QEasingCurve, QIODevice, QItemSelectionModel, QMimeData,
    QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QCursor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QIcon, QImageReader, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar, QSlider, QStackedWidget,
    QAbstractItemView, QComboBox, QHeaderView, QListWidget, QListWidgetItem,
    QInputDialog, QListView, QMenu, QMessageBox, QSpinBox,
    QTableWidget, QTableWidgetItem,
)
from actions.whatsapp_ui import WhatsAppWindow

from actions.paths import RESOURCE_DIR, CONFIG_DIR, MEMORY_DIR, config_path

def _base_dir() -> Path:
    """Read-only resource root (use CONFIG_DIR/MEMORY_DIR for writable data)."""
    return RESOURCE_DIR

BASE_DIR   = RESOURCE_DIR
API_FILE   = config_path("api_keys.json")

_DEFAULT_W, _DEFAULT_H = 1180, 760
_MIN_W,     _MIN_H     = 900, 620
_LEFT_W  = 196
_RIGHT_W = 320

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#080B12"
    PANEL     = "#141927"
    PANEL2    = "#1F2637"
    BORDER    = "#334155"
    BORDER_B  = "#5B6B84"
    BORDER_A  = "#273144"
    PRI       = "#7DD3FC"
    PRI_DIM   = "#38BDF8"
    PRI_GHO   = "#10243A"
    ACC       = "#A7F3D0"
    ACC2      = "#93C5FD"
    GREEN     = "#A7F3D0"
    GREEN_D   = "#34D399"
    RED       = "#FB7185"
    MUTED_C   = "#94A3B8"
    TEXT      = "#F8FAFC"
    TEXT_DIM  = "#CBD5E1"
    TEXT_MED  = "#94A3B8"
    WHITE     = "#FFFFFF"
    DARK      = "#070A11"
    BAR_BG    = "#0C111C"
    GLASS     = "rgba(255, 255, 255, 0.050)"
    GLASS_2   = "rgba(255, 255, 255, 0.085)"
    GLASS_D   = "rgba(7, 10, 17, 0.76)"
    SHADOW    = "rgba(0, 0, 0, 0.28)"


FONT_UI = "Segoe UI Variable"
FONT_UI_FALLBACK = "Segoe UI"
FONT_MONO = "Cascadia Mono"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


def _scrollbar_qss() -> str:
    return """
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
            margin: 6px 2px 6px 2px;
            border: none;
        }
        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.24);
            border: none;
            border-radius: 3px;
            min-height: 34px;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(125, 211, 252, 0.45);
        }
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            background: transparent;
            border: none;
            height: 0px;
            width: 0px;
        }
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {
            background: transparent;
            border: none;
            height: 0px;
        }
        QScrollBar::up-arrow:vertical,
        QScrollBar::down-arrow:vertical {
            background: transparent;
            border: none;
            width: 0px;
            height: 0px;
        }
        QScrollBar:horizontal {
            background: transparent;
            height: 8px;
            margin: 2px 6px 2px 6px;
            border: none;
        }
        QScrollBar::handle:horizontal {
            background: rgba(255, 255, 255, 0.24);
            border: none;
            border-radius: 3px;
            min-width: 34px;
        }
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {
            background: transparent;
            border: none;
            height: 0px;
            width: 0px;
        }
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {
            background: transparent;
            border: none;
            width: 0px;
        }
        QScrollBar::left-arrow:horizontal,
        QScrollBar::right-arrow:horizontal,
        QAbstractScrollArea::corner {
            background: transparent;
            border: none;
            width: 0px;
            height: 0px;
        }
    """


def _build_app_icon() -> QIcon:
    pm = QPixmap(256, 256)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Static rendering of the central JARVIS orb used by the main HUD.
    p.setPen(Qt.PenStyle.NoPen)
    tile = QRadialGradient(QPointF(116, 108), 180)
    tile.setColorAt(0.0, QColor("#14283A"))
    tile.setColorAt(0.55, QColor("#080E18"))
    tile.setColorAt(1.0, QColor("#05080D"))
    p.setBrush(QBrush(tile))
    p.drawEllipse(QRectF(8, 8, 240, 240))

    for radius, alpha, width in (
        (108, 72, 4.0),
        (88, 105, 3.5),
        (65, 155, 3.0),
    ):
        p.setPen(QPen(QColor(125, 211, 252, alpha), width))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(128 - radius, 128 - radius, radius * 2, radius * 2))

    p.setPen(QPen(QColor(125, 211, 252, 185), 5))
    p.drawArc(QRectF(20, 20, 216, 216), 15 * 16, 82 * 16)
    p.drawArc(QRectF(20, 20, 216, 216), 195 * 16, 82 * 16)

    glow = QRadialGradient(QPointF(128, 128), 82)
    glow.setColorAt(0.0, QColor(240, 255, 255, 245))
    glow.setColorAt(0.16, QColor(167, 243, 208, 230))
    glow.setColorAt(0.43, QColor(125, 211, 252, 190))
    glow.setColorAt(0.74, QColor(56, 189, 248, 75))
    glow.setColorAt(1.0, QColor(8, 11, 18, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(glow))
    p.drawEllipse(QRectF(47, 47, 162, 162))

    core = QRadialGradient(QPointF(128, 128), 38)
    core.setColorAt(0.0, QColor(255, 255, 255, 255))
    core.setColorAt(0.35, QColor(167, 243, 208, 235))
    core.setColorAt(1.0, QColor(56, 189, 248, 0))
    p.setBrush(QBrush(core))
    p.drawEllipse(QRectF(90, 90, 76, 76))

    p.setPen(QPen(QColor(200, 255, 255, 205), 4.0))
    wave = QPainterPath()
    wave.moveTo(47, 128)
    wave.cubicTo(76, 128, 85, 112, 106, 120)
    wave.cubicTo(123, 126, 133, 143, 151, 135)
    wave.cubicTo(170, 126, 179, 128, 209, 128)
    p.drawPath(wave)
    p.end()
    return QIcon(pm)


def _line_icon(name: str, color: str = C.TEXT_DIM, size: int = 20) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / 24.0
    p.scale(scale, scale)
    pen = QPen(qcol(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    def line(x1, y1, x2, y2):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    if name == "send":
        path = QPainterPath()
        path.moveTo(4, 5); path.lineTo(21, 12); path.lineTo(4, 19)
        path.lineTo(7, 12); path.closeSubpath()
        p.drawPath(path); line(7, 12, 21, 12)
    elif name == "mic":
        p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        p.drawArc(QRectF(6, 8, 12, 10), 180 * 16, 180 * 16)
        line(12, 18, 12, 21); line(9, 21, 15, 21)
    elif name == "mic_off":
        p.drawRoundedRect(QRectF(9, 3, 6, 11), 3, 3)
        p.drawArc(QRectF(6, 8, 12, 10), 180 * 16, 180 * 16)
        line(12, 18, 12, 21); line(9, 21, 15, 21); line(4, 4, 20, 20)
    elif name == "fullscreen":
        line(4, 9, 4, 4); line(4, 4, 9, 4)
        line(15, 4, 20, 4); line(20, 4, 20, 9)
        line(20, 15, 20, 20); line(20, 20, 15, 20)
        line(9, 20, 4, 20); line(4, 20, 4, 15)
    elif name == "panel_close":
        p.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
        line(16, 4, 16, 20); line(12, 9, 9, 12); line(9, 12, 12, 15)
    elif name == "panel_open":
        p.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
        line(16, 4, 16, 20); line(10, 9, 13, 12); line(13, 12, 10, 15)
    elif name == "chevron_down":
        line(6, 9, 12, 15); line(12, 15, 18, 9)
    elif name == "chevron_up":
        line(6, 15, 12, 9); line(12, 9, 18, 15)
    elif name == "chevron_left":
        line(15, 5, 8, 12); line(8, 12, 15, 19)
    elif name == "chevron_right":
        line(9, 5, 16, 12); line(16, 12, 9, 19)
    elif name == "search":
        p.drawEllipse(QRectF(4, 4, 11, 11)); line(14, 14, 20, 20)
    elif name == "more":
        p.setBrush(QBrush(qcol(color)))
        for x in (6, 12, 18):
            p.drawEllipse(QRectF(x - 1, 11, 2, 2))
    elif name == "upload":
        line(12, 16, 12, 4); line(7, 9, 12, 4); line(17, 9, 12, 4)
        p.drawRoundedRect(QRectF(4, 15, 16, 6), 2, 2)
    elif name == "download":
        line(12, 4, 12, 16); line(7, 11, 12, 16); line(17, 11, 12, 16)
        p.drawRoundedRect(QRectF(4, 15, 16, 6), 2, 2)
    elif name == "share":
        p.drawEllipse(QRectF(4, 10, 4, 4))
        p.drawEllipse(QRectF(16, 4, 4, 4))
        p.drawEllipse(QRectF(16, 16, 4, 4))
        line(8, 11, 16, 7); line(8, 13, 16, 17)
    elif name == "edit":
        path = QPainterPath()
        path.moveTo(5, 19); path.lineTo(8, 14); path.lineTo(16, 6)
        path.lineTo(19, 9); path.lineTo(11, 17); path.closeSubpath()
        p.drawPath(path); line(14, 8, 17, 11); line(5, 19, 10, 18)
    elif name == "trash":
        p.drawRoundedRect(QRectF(7, 7, 10, 13), 1, 1)
        line(5, 7, 19, 7); line(9, 4, 15, 4); line(10, 10, 10, 17); line(14, 10, 14, 17)
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(3, 7); path.lineTo(9, 7); path.lineTo(11, 10)
        path.lineTo(21, 10); path.lineTo(19, 20); path.lineTo(3, 20)
        path.closeSubpath(); p.drawPath(path)
    elif name == "external":
        p.drawRoundedRect(QRectF(4, 7, 13, 13), 2, 2)
        line(11, 4, 20, 4); line(20, 4, 20, 13)
        line(20, 4, 10, 14)
    elif name == "play":
        path = QPainterPath()
        path.moveTo(8, 5); path.lineTo(19, 12); path.lineTo(8, 19)
        path.closeSubpath(); p.drawPath(path)
    elif name == "pause":
        line(9, 6, 9, 18); line(15, 6, 15, 18)
    elif name == "close":
        line(6, 6, 18, 18); line(18, 6, 6, 18)
    elif name == "refresh":
        p.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 285 * 16)
        line(17, 4, 20, 4); line(20, 4, 20, 7)
    elif name == "home":
        path = QPainterPath()
        path.moveTo(4, 11); path.lineTo(12, 4); path.lineTo(20, 11)
        path.moveTo(6, 10); path.lineTo(6, 20); path.lineTo(18, 20); path.lineTo(18, 10)
        p.drawPath(path); p.drawRect(QRectF(10, 14, 4, 6))
    elif name == "chat":
        p.drawRoundedRect(QRectF(3, 4, 18, 14), 4, 4)
        path = QPainterPath()
        path.moveTo(8, 18); path.lineTo(7, 21); path.lineTo(12, 18)
        p.drawPath(path)
    elif name == "mail":
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 2, 2)
        line(4, 7, 12, 13); line(12, 13, 20, 7)
    elif name == "drive":
        path = QPainterPath()
        path.moveTo(9, 3); path.lineTo(15, 3); path.lineTo(21, 14)
        path.lineTo(18, 20); path.lineTo(6, 20); path.lineTo(3, 14)
        path.closeSubpath(); p.drawPath(path)
        line(9, 3, 3, 14); line(15, 3, 21, 14); line(3, 14, 21, 14)
    elif name == "music":
        line(10, 6, 19, 4); line(10, 6, 10, 17); line(19, 4, 19, 15)
        p.drawEllipse(QRectF(6, 16, 4, 3)); p.drawEllipse(QRectF(15, 14, 4, 3))
    elif name == "youtube":
        p.drawRoundedRect(QRectF(3, 6, 18, 12), 4, 4)
        tri = QPainterPath()
        tri.moveTo(10, 9); tri.lineTo(15, 12); tri.lineTo(10, 15)
        tri.closeSubpath(); p.drawPath(tri)
    elif name == "volume":
        spk = QPainterPath()
        spk.moveTo(4, 9); spk.lineTo(8, 9); spk.lineTo(12, 5)
        spk.lineTo(12, 19); spk.lineTo(8, 15); spk.lineTo(4, 15)
        spk.closeSubpath(); p.drawPath(spk)
        p.drawArc(QRectF(13, 8, 5, 8), -70 * 16, 140 * 16)
        p.drawArc(QRectF(13, 5, 9, 14), -60 * 16, 120 * 16)
    elif name == "pip":
        p.drawRoundedRect(QRectF(3, 5, 18, 14), 2.5, 2.5)
        p.drawRoundedRect(QRectF(12, 12, 7, 5), 1.2, 1.2)
    elif name == "forward":
        p.setBrush(qcol(color))
        for off in (0, 8):
            tri = QPainterPath()
            tri.moveTo(4 + off, 6); tri.lineTo(11 + off, 12); tri.lineTo(4 + off, 18)
            tri.closeSubpath(); p.drawPath(tri)
    elif name == "backward":
        p.setBrush(qcol(color))
        for off in (0, 8):
            tri = QPainterPath()
            tri.moveTo(20 - off, 6); tri.lineTo(13 - off, 12); tri.lineTo(20 - off, 18)
            tri.closeSubpath(); p.drawPath(tri)
    elif name == "fullscreen_exit":
        line(8, 4, 8, 8); line(8, 8, 4, 8)
        line(16, 4, 16, 8); line(16, 8, 20, 8)
        line(8, 20, 8, 16); line(8, 16, 4, 16)
        line(16, 20, 16, 16); line(16, 16, 20, 16)
    elif name == "volume_off":
        spk = QPainterPath()
        spk.moveTo(4, 9); spk.lineTo(8, 9); spk.lineTo(12, 5)
        spk.lineTo(12, 19); spk.lineTo(8, 15); spk.lineTo(4, 15)
        spk.closeSubpath(); p.drawPath(spk)
        line(15, 9, 21, 15); line(21, 9, 15, 15)
    elif name == "playlist":
        for y in (6, 12, 18):
            p.drawEllipse(QRectF(4, y - 1, 2, 2))
            line(9, y, 20, y)
    elif name == "heart":
        path = QPainterPath()
        path.moveTo(12, 20)
        path.cubicTo(10, 18, 4, 14, 4, 9)
        path.cubicTo(4, 5, 9, 3, 12, 7)
        path.cubicTo(15, 3, 20, 5, 20, 9)
        path.cubicTo(20, 14, 14, 18, 12, 20)
        p.drawPath(path)
    elif name == "shuffle":
        line(4, 7, 7, 7); line(7, 7, 17, 17); line(17, 17, 20, 17)
        line(16, 14, 20, 17); line(20, 17, 16, 20)
        line(4, 17, 7, 17); line(7, 17, 11, 13)
        line(13, 11, 17, 7); line(17, 7, 20, 7)
        line(16, 4, 20, 7); line(20, 7, 16, 10)
    elif name in {"file", "image", "video", "audio", "code", "archive", "chart"}:
        path = QPainterPath()
        path.moveTo(6, 3); path.lineTo(15, 3); path.lineTo(19, 7)
        path.lineTo(19, 21); path.lineTo(6, 21); path.closeSubpath()
        p.drawPath(path); line(15, 3, 15, 7); line(15, 7, 19, 7)
        if name == "image":
            p.drawEllipse(QRectF(9, 9, 2.5, 2.5))
            line(8, 18, 12, 14); line(12, 14, 14, 16); line(14, 16, 17, 13)
        elif name == "video":
            p.drawRoundedRect(QRectF(8, 10, 6, 6), 1, 1)
            path = QPainterPath()
            path.moveTo(14, 12); path.lineTo(17, 10); path.lineTo(17, 16)
            path.lineTo(14, 14); path.closeSubpath(); p.drawPath(path)
        elif name == "audio":
            line(14, 9, 14, 16); line(14, 9, 18, 8); line(18, 8, 18, 14)
            p.drawEllipse(QRectF(11, 15, 3, 2)); p.drawEllipse(QRectF(15, 13, 3, 2))
        elif name == "code":
            line(11, 11, 8, 14); line(8, 14, 11, 17)
            line(15, 11, 18, 14); line(18, 14, 15, 17)
        elif name == "archive":
            p.drawRect(QRectF(8, 10, 9, 8)); line(8, 13, 17, 13); line(11, 16, 14, 16)
        elif name == "chart":
            line(9, 17, 9, 14); line(13, 17, 13, 11); line(17, 17, 17, 8)
        else:
            line(9, 11, 16, 11); line(9, 15, 16, 15)
    p.end()
    return QIcon(pm)


def _icon_button(
    name: str,
    tooltip: str,
    size: int = 38,
    icon_size: int = 19,
    accent: bool = False,
) -> QPushButton:
    button = QPushButton()
    button.setFixedSize(size, size)
    button.setIcon(_line_icon(name, C.PRI if accent else C.TEXT_DIM, icon_size))
    button.setIconSize(QSize(icon_size, icon_size))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(f"""
        QPushButton {{
            background: {"rgba(56, 189, 248, 0.16)" if accent else "rgba(255, 255, 255, 0.045)"};
            border: 1px solid {"rgba(125, 211, 252, 0.30)" if accent else "rgba(255, 255, 255, 0.09)"};
            border-radius: {min(10, size // 3)}px;
            padding: 0;
        }}
        QPushButton:hover {{
            background: {"rgba(56, 189, 248, 0.24)" if accent else "rgba(255, 255, 255, 0.09)"};
            border-color: rgba(125, 211, 252, 0.38);
        }}
        QPushButton:pressed {{ background: rgba(56, 189, 248, 0.12); }}
        QPushButton:focus {{ border: 2px solid rgba(125, 211, 252, 0.62); }}
        QPushButton:disabled {{ background: rgba(255, 255, 255, 0.02); border-color: rgba(255, 255, 255, 0.04); }}
    """)
    return button


def _set_windows_app_id() -> None:
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Mark-XXXIX.JARVIS")
    except Exception:
        pass


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(1.5)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        gpu = self._get_gpu()

        tmp = self._get_temp()

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # NVIDIA
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return sum(vals) / len(vals)
        except Exception:
            pass

        # AMD (Linux)
        if _OS == "Linux":
            try:
                r = subprocess.run(
                    ["rocm-smi", "--showuse", "--csv"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    for line in r.stdout.strip().split("\n"):
                        parts = line.split(",")
                        if len(parts) >= 2:
                            try:
                                return float(parts[1].strip().replace("%", ""))
                            except ValueError:
                                pass
            except Exception:
                pass

            # Intel GPU (Linux)
            try:
                r = subprocess.run(
                    ["intel_gpu_top", "-J", "-s", "500"],
                    capture_output=True, text=True, timeout=1
                )
                if r.returncode == 0 and "Render/3D" in r.stdout:
                    import re
                    m = re.search(r'"busy":\s*([\d.]+)', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        # macOS — powermetrics (GPU Engine)
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["sudo", "-n", "powermetrics", "-n", "1", "-i", "500",
                     "--samplers", "gpu_power"],
                    capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0 and "GPU" in r.stdout:
                    import re
                    m = re.search(r'GPU\s+Active:\s+([\d.]+)%', r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        return -1.0

    def _get_temp(self) -> float:
        try:
            temps = psutil.sensors_temperatures()
            candidates = ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                          "cpu-thermal", "zenpower", "it8688"]
            for name in candidates:
                if name in temps:
                    entries = temps[name]
                    if entries:
                        return entries[0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass
        if _OS == "Darwin":
            try:
                r = subprocess.run(
                    ["osx-cpu-temp"], capture_output=True, text=True, timeout=2
                )
                if r.returncode == 0:
                    import re
                    m = re.search(r"([\d.]+)", r.stdout)
                    if m:
                        return float(m.group(1))
            except Exception:
                pass

        if _OS == "Windows":
            try:
                r = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-WmiObject MSAcpi_ThermalZoneTemperature -Namespace root/wmi).CurrentTemperature"],
                    capture_output=True, text=True, timeout=3
                )
                if r.returncode == 0 and r.stdout.strip():
                    raw = float(r.stdout.strip().split("\n")[0])
                    return (raw / 10.0) - 273.15
            except Exception:
                pass

        return -1.0

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._scan       = 0.0
        self._scan2      = 180.0
        self._rings      = [0.0, 120.0, 240.0]
        self._pulses: list[float] = [0.0, 50.0, 100.0]
        self._blink      = True
        self._blink_tick = 0
        self._particles: list[list[float]] = []
        self._audio_level = 0.0          # 0.0-1.0, driven externally
        self._bass   = 0.0               # low-freq band level 0-1
        self._mid    = 0.0               # mid-freq band level 0-1
        self._treble = 0.0               # high-freq band level 0-1
        self._last_audio_t = 0.0         # timestamp del último audio detectado
        self.music_playing = False       # True while a track is playing
        _N = 64
        self._bar_heights = [0.0] * _N
        self._audio_data_lock = threading.Lock()
        self._pending_fft: list[float] | None = None
        self._pending_bands: tuple[float, float, float] | None = None
        self._bar_phases  = [random.uniform(0, 2 * math.pi) for _ in range(_N)]
        self._rot_angle   = 0.0

        self._tmr = QTimer(self)
        self._tmr.setTimerType(Qt.TimerType.PreciseTimer)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(33)

    def _load_face(self, path: str):
        try:
            from PIL import Image, ImageDraw
            import io
            img = Image.open(path).convert("RGBA")
            sz  = min(img.size)
            img = img.resize((sz, sz), Image.LANCZOS)
            mk  = Image.new("L", (sz, sz), 0)
            ImageDraw.Draw(mk).ellipse((2, 2, sz - 2, sz - 2), fill=255)
            img.putalpha(mk)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap(); px.loadFromData(buf.getvalue())
            self._face_px = px
        except Exception:
            self._face_px = None

    def _step(self):
        self._tick += 1
        now = time.time()
        with self._audio_data_lock:
            pending_fft = self._pending_fft
            pending_bands = self._pending_bands
            self._pending_fft = None
            self._pending_bands = None

        if pending_bands is not None:
            self._bass, self._mid, self._treble = pending_bands
            self._audio_level = max(pending_bands)
        if pending_fft is not None:
            for i, value in enumerate(pending_fft[:len(self._bar_heights)]):
                if value > self._bar_heights[i]:
                    self._bar_heights[i] = value

        _al = max(self._bass, self._mid, self._treble, self._audio_level)
        _active = self.speaking or self.music_playing

        if _al > 0.02:
            # ataque directo: sin interpolación, reacción inmediata
            self._scale = 1.0 + _al * 0.32
            self._halo  = 58.0 + _al * 180.0
            self._last_t = now
            self._last_audio_t = now
        elif now - self._last_audio_t < 0.18:
            # 180ms de hold + decaimiento suave tras el audio
            self._scale += (1.0  - self._scale) * 0.22
            self._halo  += (55.0 - self._halo)  * 0.22
        elif _active:
            # hablando/música sin nivel detectable: pulso suave
            self._scale += (1.015 - self._scale) * 0.14
            self._halo  += (70.0  - self._halo)  * 0.14
        else:
            # reposo total: respiración mínima y estable
            breath = math.sin(self._tick * 0.012) * 0.003
            self._scale = 1.001 + breath
            self._halo  = 52.0  + breath * 1200

        speeds = [1.3, -0.9, 2.0] if self.speaking else ([0.9, -0.65, 1.4] if self.music_playing else [0.55, -0.35, 0.9])
        for i, spd in enumerate(speeds):
            self._rings[i] = (self._rings[i] + spd) % 360

        self._scan  = (self._scan  + (3.0 if self.speaking else (2.0 if self.music_playing else 1.3))) % 360
        self._scan2 = (self._scan2 + (-2.0 if self.speaking else (-1.4 if self.music_playing else -0.75))) % 360
        rot_spd = 1.8 if (self.speaking or self.music_playing) else (0.9 if _al > 0.02 else 0.25)
        self._rot_angle = (self._rot_angle + rot_spd) % 360
        # decay de barras FFT cada tick (en-place para no perder escrituras del hilo de audio)
        _decay = 0.84
        for _i in range(len(self._bar_heights)):
            self._bar_heights[_i] *= _decay

        fw  = min(self.width(), self.height())
        lim = fw * 0.74
        spd = 4.2 if self.speaking else (3.2 if self.music_playing else 2.0)
        self._pulses = [r + spd for r in self._pulses if r + spd < lim]
        # los golpes de bajo emiten pulsos extra
        _emit = 0.12 if self._bass > 0.35 else (0.07 if self.speaking else (0.05 if self.music_playing else 0.025))
        if len(self._pulses) < 5 and random.random() < _emit:
            self._pulses.append(0.0)

        if (self.speaking or self.music_playing) and random.random() < (0.28 if self.speaking else 0.1):
            cx, cy = self.width() / 2, self.height() / 2
            ang = random.uniform(0, 2 * math.pi)
            r_s = fw * 0.28
            self._particles.append([
                cx + math.cos(ang) * r_s, cy + math.sin(ang) * r_s,
                math.cos(ang) * random.uniform(0.9, 2.4),
                math.sin(ang) * random.uniform(0.9, 2.4) - 0.4, 1.0,
            ])
        self._particles = [
            [p[0]+p[2], p[1]+p[3], p[2]*0.97, p[3]*0.97, p[4]-0.028]
            for p in self._particles if p[4] > 0
        ]

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0



        self.update()

    def set_audio_level(self, level: float):
        """Set real-time audio amplitude (0.0–1.0) for the orb visualizer."""
        self._audio_level = max(0.0, min(1.0, float(level)))

    def set_audio_bands(self, bass: float, mid: float, treble: float):
        """Set per-band levels (0-1). Drives frequency-aware waveform shape."""
        values = (
            max(0.0, min(1.0, float(bass))),
            max(0.0, min(1.0, float(mid))),
            max(0.0, min(1.0, float(treble))),
        )
        with self._audio_data_lock:
            self._pending_bands = values

    def set_fft_bins(self, bins):
        """bins: lista de 64 floats 0-1 con amplitud por banda de frecuencia."""
        latest = [
            max(0.0, min(1.0, float(v)))
            for v in list(bins)[:len(self._bar_heights)]
        ]
        with self._audio_data_lock:
            self._pending_fft = latest

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        bg = QLinearGradient(QPointF(0, 0), QPointF(W, H))
        bg.setColorAt(0.0, qcol("#111827"))
        bg.setColorAt(0.45, qcol("#080B12"))
        bg.setColorAt(1.0, qcol("#0F172A"))
        p.fillRect(self.rect(), QBrush(bg))

        glow = QRadialGradient(QPointF(W * 0.42, H * 0.34), fw * 0.78)
        glow.setColorAt(0.0, qcol(C.PRI, 42))
        glow.setColorAt(0.42, qcol(C.PRI_GHO, 34))
        glow.setColorAt(1.0, qcol(C.BG, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QRectF(W * 0.02, H * -0.10, W * 0.92, H * 0.92))

        glow2 = QRadialGradient(QPointF(W * 0.70, H * 0.68), fw * 0.55)
        glow2.setColorAt(0.0, qcol(C.ACC, 24))
        glow2.setColorAt(1.0, qcol(C.BG, 0))
        p.setBrush(QBrush(glow2))
        p.drawEllipse(QRectF(W * 0.40, H * 0.32, W * 0.72, H * 0.72))

        r_face = fw * 0.262

        # halo glow
        for i in range(10):
            r   = r_face * (1.8 - i * 0.08)
            frc = 1.0 - i / 10
            a   = max(0, min(255, int(self._halo * 0.085 * frc)))
            col = qcol(C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))

        # pulse rings
        for pr in self._pulses:
            a   = max(0, int(230 * (1.0 - pr / (fw * 0.74))))
            col = qcol(C.PRI, a)
            p.setPen(QPen(col, 1.5)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QRectF(cx - pr, cy - pr, pr * 2, pr * 2))

        # spinning arc rings
        for idx, (r_frac, w_r, arc_l, gap) in enumerate(
            [(0.48, 3, 115, 78), (0.40, 2, 78, 55), (0.32, 1, 56, 40)]
        ):
            ring_r = fw * r_frac
            base   = self._rings[idx]
            a_val  = max(0, min(255, int(self._halo * (1.0 - idx * 0.18))))
            col    = qcol(C.PRI, a_val)
            p.setPen(QPen(col, w_r)); p.setBrush(Qt.BrushStyle.NoBrush)
            angle = base
            rect  = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)
            while angle < base + 360:
                p.drawArc(rect, int(angle * 16), int(arc_l * 16))
                angle += arc_l + gap

        # scanners
        sr = fw * 0.50
        sa = min(255, int(self._halo * 1.5))
        ex = 75 if self.speaking else 44
        p.setPen(QPen(qcol(C.PRI, sa), 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        srect = QRectF(cx - sr, cy - sr, sr * 2, sr * 2)
        p.drawArc(srect, int(self._scan * 16), int(ex * 16))
        p.setPen(QPen(qcol(C.ACC, sa // 2), 1.5))
        p.drawArc(srect, int(self._scan2 * 16), int(ex * 16))

        # tick marks
        t_out, t_in = fw * 0.497, fw * 0.474
        p.setPen(QPen(qcol(C.PRI, 140), 1))
        for deg in range(0, 360, 10):
            rad = math.radians(deg)
            inn = t_in if deg % 30 == 0 else t_in + 6
            p.drawLine(
                QPointF(cx + t_out * math.cos(rad), cy - t_out * math.sin(rad)),
                QPointF(cx + inn  * math.cos(rad), cy - inn  * math.sin(rad)),
            )

        # crosshair
        ch_r, gap_h = fw * 0.51, fw * 0.16
        p.setPen(QPen(qcol(C.PRI, int(self._halo * 0.5)), 1))
        p.drawLine(QPointF(cx - ch_r, cy), QPointF(cx - gap_h, cy))
        p.drawLine(QPointF(cx + gap_h, cy), QPointF(cx + ch_r, cy))
        p.drawLine(QPointF(cx, cy - ch_r), QPointF(cx, cy - gap_h))
        p.drawLine(QPointF(cx, cy + gap_h), QPointF(cx, cy + ch_r))

        # corner brackets
        bl = 24
        bc = qcol(C.PRI, 210)
        hl, hr = cx - fw // 2, cx + fw // 2
        ht, hb = cy - fw // 2, cy + fw // 2
        p.setPen(QPen(bc, 2))
        for bx, by, dx, dy in [(hl,ht,1,1),(hr,ht,-1,1),(hl,hb,1,-1),(hr,hb,-1,-1)]:
            p.drawLine(QPointF(bx, by), QPointF(bx + dx * bl, by))
            p.drawLine(QPointF(bx, by), QPointF(bx, by + dy * bl))

        # --- central reactive orb ---
        r_orb_base = fw * 0.262
        orb_r = r_orb_base * self._scale   # crece con el audio
        al = self._audio_level

        # fill gradient
        grad = QRadialGradient(QPointF(cx, cy), orb_r)
        if self.speaking:
            lv = min(1.0, al * 1.4 + 0.30)
            grad.setColorAt(0.0, qcol("#FFFFFF", min(255, int(90 + 165 * lv))))
            grad.setColorAt(0.22, qcol(C.ACC, min(255, int(140 + 115 * lv))))
            grad.setColorAt(0.62, qcol(C.PRI, min(200, int(75 + 125 * lv))))
            grad.setColorAt(0.90, qcol(C.PRI_GHO, 35))
            grad.setColorAt(1.0, qcol(C.BG, 0))
        else:
            grad.setColorAt(0.0, qcol(C.ACC, min(200, int(85 + 115 * al))))
            grad.setColorAt(0.40, qcol(C.PRI, min(180, int(80 + 100 * al))))
            grad.setColorAt(0.78, qcol(C.PRI_GHO, 40))
            grad.setColorAt(1.0, qcol(C.BG, 0))
        p.setBrush(QBrush(grad)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - orb_r, cy - orb_r, orb_r * 2, orb_r * 2))

        # outer glow ring
        ring_a = min(255, int(self._halo * 2.0))
        if self.speaking:
            ring_col = qcol(C.ACC, ring_a)
        else:
            ring_col = qcol(C.PRI, ring_a)
        p.setPen(QPen(ring_col, 2.0)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - orb_r, cy - orb_r, orb_r * 2, orb_r * 2))

        # --- equalizer vertical (estilo reproductor de música) ---
        if True:
            n_disp     = 32
            n_src      = len(self._bar_heights)
            step       = max(1, n_src // n_disp)
            bar_area_w = r_orb_base * 2.6
            bar_slot_w = bar_area_w / n_disp
            bar_w      = bar_slot_w * 0.62
            bar_max_h  = fw * 0.17
            baseline_y = cy + r_orb_base * 1.18
            for i in range(n_disp):
                idx   = min(i * step, n_src - 1)
                group = self._bar_heights[idx : idx + step]
                h     = max(group) if group else 0.0
                idle_h   = 0.022 + 0.010 * math.sin(self._tick * 0.016 + i * 0.25)
                display_h = max(h, idle_h)
                bar_h = display_h * bar_max_h
                bx    = cx - bar_area_w / 2 + i * bar_slot_w + (bar_slot_w - bar_w) / 2
                # gradiente: base oscura → punta cian brillante
                grad = QLinearGradient(QPointF(bx, baseline_y),
                                       QPointF(bx, baseline_y - bar_h))
                grad.setColorAt(0.0, qcol(C.PRI, 55))
                grad.setColorAt(0.55, qcol(C.PRI, min(210, int(90 + 120 * display_h))))
                if h > 0.55:
                    grad.setColorAt(1.0, qcol("#FFFFFF", min(255, int(170 + 85 * h))))
                else:
                    grad.setColorAt(1.0, qcol(C.ACC, min(255, int(110 + 145 * display_h))))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(grad))
                p.drawRoundedRect(QRectF(bx, baseline_y - bar_h, bar_w, bar_h), 2.0, 2.0)
                # punto de peak
                if h > 0.12:
                    peak_col = qcol(C.ACC, min(255, int(190 + 65 * h)))
                    p.setBrush(QBrush(peak_col))
                    p.drawEllipse(QRectF(bx + bar_w * 0.1, baseline_y - bar_h - 4,
                                         bar_w * 0.8, 3.5))

        # --- waveform frecuencia-adaptativa ---
        t = self._tick
        wave_amp = orb_r * max(0.05, min(0.70, al * 0.78 + 0.05))
        _tb = self._bass; _tm = self._mid; _tt = self._treble
        _tot = max(0.001, _tb + _tm + _tt)
        b_r = _tb / _tot;  m_r = _tm / _tot;  tr_r = _tt / _tot
        # ciclos visibles: bajo=pocos lentos, agudos=muchos rápidos
        n_cyc = max(1.2, min(8.0, b_r * 1.5 + m_r * 4.0 + tr_r * 8.0))
        t_spd = max(0.018, min(0.22, b_r * 0.025 + m_r * 0.070 + tr_r * 0.18))
        # armónicos de agudos
        harm  = min(0.55, tr_r * 0.80)
        clip_path = QPainterPath()
        clip_path.addEllipse(QRectF(cx - orb_r * 0.90, cy - orb_r * 0.90,
                                     orb_r * 1.80, orb_r * 1.80))
        p.save()
        p.setClipPath(clip_path)
        n_pts = 100
        for wave_idx in range(2):
            ph   = wave_idx * math.pi * 0.55
            dim  = 1.0 - wave_idx * 0.38
            if al > 0.03 or self.speaking:
                # color según banda dominante
                if b_r > 0.50:
                    w_col = qcol(C.ACC, min(255, int((150 + 105 * al) * dim)))
                elif tr_r > 0.50:
                    w_col = qcol("#C8FFFF", min(255, int((135 + 120 * al) * dim)))
                else:
                    w_col = qcol(C.PRI, min(255, int((140 + 115 * al) * dim)))
            else:
                w_col = qcol(C.PRI, int(128 * dim))
            pen_w = 1.9 - wave_idx * 0.7
            wave_path = QPainterPath()
            for i in range(n_pts + 1):
                frac = i / n_pts
                x    = cx - orb_r + 2 * orb_r * frac
                base = math.sin(t * t_spd + frac * n_cyc * math.tau + ph)
                hrm  = harm * math.sin(t * t_spd * 2.6 + frac * n_cyc * 2.4 * math.tau + ph)
                y    = cy + wave_amp * (base + hrm)
                if i == 0:
                    wave_path.moveTo(QPointF(x, y))
                else:
                    wave_path.lineTo(QPointF(x, y))
            p.setPen(QPen(w_col, pen_w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(wave_path)
        p.restore()

        # inner bright core pulse
        core_r = orb_r * max(0.18, min(0.42, 0.22 + 0.20 * al))
        cg = QRadialGradient(QPointF(cx, cy), core_r)
        if self.speaking:
            cg.setColorAt(0, qcol("#FFFFFF", min(255, int(195 + 60 * al))))
            cg.setColorAt(0.45, qcol(C.ACC, min(200, int(115 + 85 * al))))
            cg.setColorAt(1, qcol(C.PRI, 0))
        else:
            cg.setColorAt(0, qcol(C.ACC, min(200, int(125 + 75 * al))))
            cg.setColorAt(1, qcol(C.PRI, 0))
        p.setBrush(QBrush(cg)); p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(cx - core_r, cy - core_r, core_r * 2, core_r * 2))

        # particles
        for pt in self._particles:
            a = max(0, min(255, int(pt[4] * 255)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(qcol(C.ACC if self.speaking else C.PRI, a)))
            p.drawEllipse(QPointF(pt[0], pt[1]), 2.5, 2.5)

        # status text
        sy = cy + fw * 0.43
        if self.muted:
            txt, col = "⊘  SILENCIADO", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  HABLANDO",   qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  PENSANDO",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESANDO", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  ESCUCHANDO",  qcol(C.ACC)
        else:
            sym = "●" if self._blink else "○"
            label = {"INITIALISING": "INICIANDO"}.get(self.state, self.state)
            txt, col = f"{sym}  {label}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont(FONT_UI, 11, QFont.Weight.DemiBold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        self._value = max(0.0, min(100.0, pct))
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        card = QLinearGradient(QPointF(0, 0), QPointF(W, H))
        card.setColorAt(0, qcol("#FFFFFF", 24))
        card.setColorAt(1, qcol("#FFFFFF", 10))
        p.setBrush(QBrush(card))
        p.setPen(QPen(qcol(C.BORDER_B, 70), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 10, 10)

        bar_h   = 4
        bar_y   = H - bar_h - 5
        bar_w   = W - 12
        bar_x   = 6
        fill_w  = int(bar_w * self._value / 100)

        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 2, 2)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        if fill_w > 0:
            p.setBrush(QBrush(bar_col))
            p.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 2, 2)

        p.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

class _MediaBtn(QPushButton):
    """Botón de control multimedia dibujado con QPainter — sin emoji."""
    PREV = "prev"; PLAY = "play"; PAUSE = "pause"; NEXT = "next"

    def __init__(self, shape: str, parent=None):
        super().__init__(parent)
        self._shape = shape
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hovered = False
        self.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.035);
                border: 1px solid rgba(255,255,255,0.075);
                border-radius: 10px;
            }
            QPushButton:hover {
                background: rgba(125,211,252,0.10);
                border-color: rgba(125,211,252,0.28);
            }
            QPushButton:focus {
                border: 2px solid rgba(125,211,252,0.56);
            }
        """)

    def set_shape(self, shape: str):
        if self._shape != shape:
            self._shape = shape
            self.update()

    def enterEvent(self, e):  self._hovered = True;  self.update()
    def leaveEvent(self, e):  self._hovered = False; self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2.0, H / 2.0
        col = qcol(C.ACC if self._hovered else C.TEXT_DIM)
        p.setBrush(QBrush(col)); p.setPen(Qt.PenStyle.NoPen)
        if self._shape == self.PREV:
            # |◄  barra izquierda + triángulo izquierda
            p.fillRect(QRectF(cx - 12, cy - 7, 3, 14), col)
            path = QPainterPath()
            path.moveTo(cx - 1,  cy)
            path.lineTo(cx + 9,  cy - 7)
            path.lineTo(cx + 9,  cy + 7)
            path.closeSubpath(); p.drawPath(path)
        elif self._shape == self.NEXT:
            # ►|  triángulo derecha + barra derecha
            path = QPainterPath()
            path.moveTo(cx + 1,  cy)
            path.lineTo(cx - 9,  cy - 7)
            path.lineTo(cx - 9,  cy + 7)
            path.closeSubpath(); p.drawPath(path)
            p.fillRect(QRectF(cx + 9, cy - 7, 3, 14), col)
        elif self._shape == self.PLAY:
            path = QPainterPath()
            path.moveTo(cx + 9,  cy)
            path.lineTo(cx - 5,  cy - 9)
            path.lineTo(cx - 5,  cy + 9)
            path.closeSubpath(); p.drawPath(path)
        elif self._shape == self.PAUSE:
            p.fillRect(QRectF(cx - 7, cy - 8, 4, 16), col)
            p.fillRect(QRectF(cx + 3, cy - 8, 4, 16), col)


class _LikeBtn(QPushButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._liked = False
        self.setFixedSize(40, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.035);
                border: 1px solid rgba(255,255,255,0.075);
                border-radius: 10px;
            }
            QPushButton:hover {
                background: rgba(125,211,252,0.10);
                border-color: rgba(125,211,252,0.28);
            }
            QPushButton:disabled {
                background: rgba(255,255,255,0.02);
                border-color: rgba(255,255,255,0.04);
            }
        """)

    def set_liked(self, liked: bool):
        liked = bool(liked)
        if liked != self._liked:
            self._liked = liked
            self.update()

    def is_liked(self) -> bool:
        return self._liked

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.moveTo(20, 31)
        path.cubicTo(17, 28, 9, 23, 9, 16)
        path.cubicTo(9, 10, 16, 8, 20, 13)
        path.cubicTo(24, 8, 31, 10, 31, 16)
        path.cubicTo(31, 23, 23, 28, 20, 31)
        color = qcol(C.PRI if self._liked else (C.ACC if self.underMouse() else C.TEXT_DIM))
        p.setPen(QPen(color, 1.8))
        p.setBrush(QBrush(color) if self._liked else Qt.BrushStyle.NoBrush)
        p.drawPath(path)


class GlassComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self.setStyleSheet("QComboBox { background: transparent; border: none; padding: 0; }")
        self.view().setStyleSheet(f"""
            QListView {{
                background: rgba(15, 23, 42, 245);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 14px;
                padding: 6px;
                outline: none;
                selection-background-color: rgba(125, 211, 252, 0.24);
                selection-color: {C.TEXT};
            }}
            QListView::item {{
                min-height: 30px;
                padding: 6px 10px;
                border-radius: 10px;
            }}
            QListView::item:hover {{
                background: rgba(255, 255, 255, 0.10);
            }}
        """ + _scrollbar_qss())

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        hover = self.underMouse() or self.hasFocus()

        bg = QLinearGradient(QPointF(r.left(), r.top()), QPointF(r.left(), r.bottom()))
        bg.setColorAt(0.0, qcol("#FFFFFF", 28 if hover else 18))
        bg.setColorAt(1.0, qcol("#FFFFFF", 12 if hover else 7))
        p.setBrush(QBrush(bg))
        p.setPen(QPen(qcol(C.PRI if hover else "#FFFFFF", 72 if hover else 28), 1.0))
        p.drawRoundedRect(r, 14, 14)

        inset = r.adjusted(1.0, 1.0, -1.0, -1.0)
        p.setPen(QPen(qcol("#FFFFFF", 22), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(inset, 13, 13)

        dot_col = {
            "Normal": C.ACC2,
            "WhatsApp": "#25D366",
            "Gmail": "#F87171",
            "Drive": "#FBBF24",
            "YouTube": "#F87171",
        }.get(self.currentText(), C.PRI)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(dot_col, 230)))
        p.drawEllipse(QRectF(r.left() + 12, r.center().y() - 3.5, 7, 7))

        p.setPen(QPen(qcol(C.TEXT), 1))
        p.setFont(self.font())
        p.drawText(r.adjusted(27, 0, -34, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.currentText())

        cx = r.right() - 19
        cy = r.center().y() + 1
        pen = QPen(qcol(C.TEXT_DIM if not hover else C.PRI), 1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(cx - 5, cy - 3), QPointF(cx, cy + 3))
        p.drawLine(QPointF(cx + 5, cy - 3), QPointF(cx, cy + 3))


class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont(FONT_UI, 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                border-radius: 0px;
                padding: 2px 4px;
                selection-background-color: {C.PRI_GHO};
            }}
        """ + _scrollbar_qss())
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        if   tl.startswith("you:"):    self._tag = "you"
        elif tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):   self._tag = "file"
        elif "err" in tl:              self._tag = "err"
        else:                          self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.ACC),
                "sys":  qcol(C.ACC2),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

class DownloadWidget(QWidget):
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hide_tmr = QTimer(self)
        self._hide_tmr.setSingleShot(True)
        self._hide_tmr.timeout.connect(self.hide)
        self.setStyleSheet(f"""
            QWidget {{
                background: rgba(255, 255, 255, 0.030);
                border: 1px solid rgba(255, 255, 255, 0.070);
                border-radius: 16px;
            }}
            QLabel {{
                color: {C.TEXT};
                background: transparent;
            }}
            QProgressBar {{
                background: rgba(255, 255, 255, 0.040);
                border: none;
                border-radius: 6px;
                text-align: center;
                color: {C.TEXT};
                height: 14px;
            }}
            QProgressBar::chunk {{
                background: {C.PRI};
                border-radius: 6px;
            }}
            QPushButton {{
                background: rgba(255, 255, 255, 0.030);
                color: {C.TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.080);
                border-radius: 10px;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                color: {C.ACC};
                border-color: {C.ACC};
            }}
            QPushButton:disabled {{
                color: {C.MUTED_C};
                border-color: {C.BORDER};
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)

        self._title = QLabel("DESCARGA")
        self._title.setFont(QFont(FONT_UI, 7, QFont.Weight.DemiBold))
        self._title.setStyleSheet(f"color: {C.TEXT_MED};")
        top.addWidget(self._title)
        top.addStretch()
        self._status = QLabel("Inactivo")
        self._status.setFont(QFont(FONT_UI, 7))
        self._status.setStyleSheet(f"color: {C.TEXT_DIM};")
        top.addWidget(self._status)
        lay.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        lay.addWidget(self._bar)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)

        self._detail = QLabel("Sin descargas activas")
        self._detail.setFont(QFont(FONT_UI, 7))
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(f"color: {C.TEXT_DIM};")
        bottom.addWidget(self._detail, stretch=1)

        self._cancel = QPushButton("Cancelar")
        self._cancel.setEnabled(False)
        self._cancel.clicked.connect(self.cancel_requested.emit)
        bottom.addWidget(self._cancel)
        lay.addLayout(bottom)
        self.hide()

    def set_state(self, state: dict):
        active = bool(state.get("active", False))
        percent = float(state.get("percent", 0) or 0)
        label = str(state.get("label", "Idle") or "Idle")
        detail = str(state.get("detail", "") or "")
        percent_txt = state.get("percent_text")
        title = str(state.get("title") or state.get("task") or "PROGRESO")

        if not active and percent <= 0 and not detail and label.lower() in ("idle", "none", ""):
            self._hide_tmr.stop()
            self.hide()
            return

        self._hide_tmr.stop()
        self.setVisible(True)
        self._title.setText(title.upper()[:32])
        self._status.setText(label)
        self._detail.setText(detail or "Sin tareas activas")
        self._bar.setValue(max(0, min(100, int(round(percent)))))
        if percent_txt:
            self._bar.setFormat(str(percent_txt))
        else:
            self._bar.setFormat(f"{int(round(percent))}%")
        self._cancel.setEnabled(active and bool(state.get("can_cancel", True)))
        if not active:
            self._hide_tmr.start(15000)


class WhatsAppModePicker(QWidget):
    open_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QWidget {{ background: transparent; color: {C.TEXT}; font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}"; }}
            QLabel {{ background: transparent; color: {C.TEXT_DIM}; }}
            QLineEdit {{
                background: rgba(255, 255, 255, 0.035);
                color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.080);
                border-radius: 14px;
                padding: 10px 12px;
            }}
            QPushButton {{
                background: rgba(125, 211, 252, 0.16);
                color: {C.TEXT};
                border: 1px solid rgba(125, 211, 252, 0.28);
                border-radius: 14px;
                padding: 10px 14px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: rgba(125, 211, 252, 0.24); border-color: {C.PRI}; }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 34, 34, 34)
        root.setSpacing(10)
        root.addStretch()

        title = QLabel("MODO WHATSAPP")
        title.setFont(QFont(FONT_UI, 18, QFont.Weight.DemiBold))
        title.setStyleSheet(f"color: {C.ACC};")
        root.addWidget(title)

        hint = QLabel("Escribe el contacto, número o nombre del chat que quieres abrir.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        self.contact_input = QLineEdit()
        self.contact_input.setPlaceholderText("Ej: Mama, +34..., Juan")
        self.contact_input.returnPressed.connect(self._emit_open)
        row.addWidget(self.contact_input, stretch=1)
        btn = QPushButton("Abrir chat")
        btn.clicked.connect(self._emit_open)
        row.addWidget(btn)
        root.addLayout(row)
        root.addStretch()

    def _emit_open(self):
        contact = self.contact_input.text().strip()
        if contact:
            self.open_requested.emit(contact)


class GmailModePanel(QWidget):
    _result_sig = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._current_email: dict | None = None
        self._compact_reader = False
        self._page = 1
        self._pages = 1
        self._total_emails = 0
        self._page_size = 50
        self._list_label = "ALL"
        self._list_unread = False
        self._list_query = ""
        self._rendered_email: dict | None = None
        self._enriched_email_html: dict[str, str] = {}
        self.setStyleSheet(self._panel_style())
        self._result_sig.connect(self._handle_result)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        self.reader_page = QFrame()
        self.reader_page.setObjectName("GmailReader")
        reader_lay = QVBoxLayout(self.reader_page)
        reader_lay.setContentsMargins(24, 18, 24, 20)
        reader_lay.setSpacing(0)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self.back_btn = QPushButton()
        self.back_btn.setObjectName("GmailIconButton")
        self.back_btn.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 18))
        self.back_btn.setIconSize(QSize(18, 18))
        self.back_btn.setToolTip("Volver a la bandeja")
        self.back_btn.setFixedSize(34, 30)
        self.back_btn.clicked.connect(self._show_inbox)
        toolbar.addWidget(self.back_btn)
        self.reader_folder = QLabel("BANDEJA")
        self.reader_folder.setObjectName("GmailEyebrow")
        toolbar.addWidget(self.reader_folder)
        toolbar.addStretch()
        self.reader_date = QLabel("")
        self.reader_date.setObjectName("GmailDate")
        toolbar.addWidget(self.reader_date)
        reader_lay.addLayout(toolbar)

        self.reader_subject = QLabel("Selecciona un correo")
        self.reader_subject.setObjectName("GmailSubject")
        self.reader_subject.setWordWrap(True)
        reader_lay.addWidget(self.reader_subject)

        sender_row = QHBoxLayout()
        sender_row.setContentsMargins(0, 10, 0, 13)
        sender_row.setSpacing(10)
        self.sender_avatar = QLabel("@")
        self.sender_avatar.setObjectName("GmailAvatar")
        self.sender_avatar.setFixedSize(38, 38)
        self.sender_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sender_row.addWidget(self.sender_avatar)
        sender_text = QVBoxLayout()
        sender_text.setSpacing(1)
        self.reader_sender = QLabel("Ningún mensaje abierto")
        self.reader_sender.setObjectName("GmailSender")
        self.reader_sender.setWordWrap(True)
        self.reader_recipient = QLabel("")
        self.reader_recipient.setObjectName("GmailRecipient")
        self.reader_recipient.setWordWrap(True)
        sender_text.addWidget(self.reader_sender)
        sender_text.addWidget(self.reader_recipient)
        sender_row.addLayout(sender_text, stretch=1)
        reader_lay.addLayout(sender_row)

        divider = QFrame()
        divider.setObjectName("GmailDivider")
        divider.setFixedHeight(1)
        reader_lay.addWidget(divider)

        self.preview = QTextBrowser()
        self.preview.setObjectName("GmailPreview")
        self.preview.setReadOnly(True)
        self.preview.setOpenExternalLinks(True)
        self.preview.setPlaceholderText("Selecciona un correo de la bandeja.")
        reader_lay.addWidget(self.preview, stretch=1)

        self.inbox_page = QFrame()
        self.inbox_page.setObjectName("GmailInbox")
        self.inbox_page.setMinimumWidth(300)
        self.inbox_page.setMaximumWidth(380)
        inbox_lay = QVBoxLayout(self.inbox_page)
        inbox_lay.setContentsMargins(12, 14, 12, 12)
        inbox_lay.setSpacing(9)

        heading_row = QHBoxLayout()
        inbox_heading = QLabel("Bandeja")
        inbox_heading.setObjectName("GmailInboxTitle")
        heading_row.addWidget(inbox_heading)
        heading_row.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("GmailCount")
        heading_row.addWidget(self.status)
        inbox_lay.addLayout(heading_row)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("GmailSearch")
        self.search_input.setPlaceholderText("Buscar correo")
        self.search_input.returnPressed.connect(self.search_emails)
        inbox_lay.addWidget(self.search_input)

        filters = QHBoxLayout()
        filters.setSpacing(5)
        self.inbox_btn = QPushButton("Entrada")
        self.inbox_btn.setObjectName("GmailFilter")
        self.inbox_btn.clicked.connect(self.load_inbox)
        self.unread_btn = QPushButton("No leídos")
        self.unread_btn.setObjectName("GmailFilter")
        self.unread_btn.clicked.connect(self.load_unread)
        self.recent_btn = QPushButton("Todo")
        self.recent_btn.setObjectName("GmailFilter")
        self.recent_btn.clicked.connect(self.load_recent)
        filters.addWidget(self.inbox_btn)
        filters.addWidget(self.unread_btn)
        filters.addWidget(self.recent_btn)
        filters.addStretch()
        inbox_lay.addLayout(filters)

        self.email_list = QListWidget()
        self.email_list.setObjectName("GmailList")
        self.email_list.itemSelectionChanged.connect(self._on_email_selected)
        self.email_list.setSpacing(0)
        self.email_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inbox_lay.addWidget(self.email_list, stretch=1)

        pagination = QHBoxLayout()
        pagination.setSpacing(6)
        self.prev_page_btn = QPushButton()
        self.prev_page_btn.setObjectName("GmailPageButton")
        self.prev_page_btn.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 17))
        self.prev_page_btn.setIconSize(QSize(17, 17))
        self.prev_page_btn.setToolTip("Página anterior")
        self.prev_page_btn.setFixedSize(30, 28)
        self.prev_page_btn.clicked.connect(lambda: self._load_page(self._page - 1))
        self.page_label = QLabel("1 / 1")
        self.page_label.setObjectName("GmailPageLabel")
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.next_page_btn = QPushButton()
        self.next_page_btn.setObjectName("GmailPageButton")
        self.next_page_btn.setIcon(_line_icon("chevron_right", C.TEXT_DIM, 17))
        self.next_page_btn.setIconSize(QSize(17, 17))
        self.next_page_btn.setToolTip("Página siguiente")
        self.next_page_btn.setFixedSize(30, 28)
        self.next_page_btn.clicked.connect(lambda: self._load_page(self._page + 1))
        pagination.addWidget(self.prev_page_btn)
        pagination.addWidget(self.page_label, stretch=1)
        pagination.addWidget(self.next_page_btn)
        inbox_lay.addLayout(pagination)

        root.addWidget(self.reader_page, stretch=7)
        root.addWidget(self.inbox_page, stretch=3)
        self.back_btn.setVisible(False)
        QTimer.singleShot(200, self.load_recent)

    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                letter-spacing: 0;
            }}
            QFrame#GmailReader {{
                background: rgba(5, 11, 20, 0.88);
                border: 1px solid rgba(125, 211, 252, 0.11);
                border-radius: 10px;
            }}
            QFrame#GmailInbox {{
                background: rgba(8, 17, 29, 0.92);
                border: 1px solid rgba(125, 211, 252, 0.12);
                border-radius: 10px;
            }}
            QLabel#GmailInboxTitle {{
                color: #f8fafc;
                font-size: 17px;
                font-weight: 900;
            }}
            QLabel#GmailCount, QLabel#GmailDate, QLabel#GmailRecipient {{
                color: rgba(186, 215, 238, 0.58);
                font-size: 11px;
            }}
            QLabel#GmailEyebrow {{
                color: #7dd3fc;
                font-size: 11px;
                font-weight: 900;
            }}
            QLabel#GmailSubject {{
                color: #f8fafc;
                font-size: 23px;
                font-weight: 900;
                padding: 16px 0 4px 0;
            }}
            QLabel#GmailSender {{
                color: #e7f3ff;
                font-size: 13px;
                font-weight: 800;
            }}
            QLabel#GmailAvatar {{
                color: #dff5ff;
                background: rgba(56, 189, 248, 0.17);
                border: 1px solid rgba(125, 211, 252, 0.26);
                border-radius: 19px;
                font-size: 14px;
                font-weight: 900;
            }}
            QFrame#GmailDivider {{
                background: rgba(125, 211, 252, 0.12);
                border: none;
            }}
            QLineEdit#GmailSearch {{
                min-height: 34px;
                background: rgba(3, 9, 17, 0.72);
                color: #eaf6ff;
                border: 1px solid rgba(125, 211, 252, 0.14);
                border-radius: 6px;
                padding: 0 11px;
                selection-background-color: #38bdf8;
            }}
            QLineEdit#GmailSearch:focus {{
                border-color: rgba(125, 211, 252, 0.55);
            }}
            QPushButton#GmailFilter {{
                min-height: 27px;
                background: rgba(255, 255, 255, 0.04);
                color: rgba(220, 237, 250, 0.70);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 5px;
                padding: 0 9px;
                font-size: 11px;
                font-weight: 700;
            }}
            QPushButton#GmailFilter:hover {{
                color: #f8fafc;
                background: rgba(56, 189, 248, 0.12);
                border-color: rgba(125, 211, 252, 0.24);
            }}
            QPushButton#GmailIconButton {{
                background: rgba(255, 255, 255, 0.045);
                color: #dbeafe;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 5px;
                font-size: 20px;
                padding: 0;
            }}
            QPushButton#GmailIconButton:hover {{
                background: rgba(56, 189, 248, 0.14);
                border-color: rgba(125, 211, 252, 0.32);
            }}
            QPushButton#GmailPageButton {{
                background: rgba(255, 255, 255, 0.045);
                color: #dbeafe;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 5px;
                font-size: 18px;
                padding: 0;
            }}
            QPushButton#GmailPageButton:hover {{
                background: rgba(56, 189, 248, 0.14);
                border-color: rgba(125, 211, 252, 0.32);
            }}
            QPushButton#GmailPageButton:disabled {{
                color: rgba(186, 215, 238, 0.20);
                background: rgba(255, 255, 255, 0.018);
                border-color: rgba(255, 255, 255, 0.035);
            }}
            QLabel#GmailPageLabel {{
                color: rgba(220, 237, 250, 0.65);
                font-size: 11px;
                font-weight: 700;
            }}
            QListWidget#GmailList {{
                background: transparent;
                border: none;
                outline: none;
                padding: 0;
            }}
            QListWidget#GmailList::item {{
                border: none;
                border-bottom: 1px solid rgba(125, 211, 252, 0.09);
                padding: 0;
                margin: 0;
            }}
            QListWidget#GmailList::item:hover {{
                background: rgba(125, 211, 252, 0.055);
            }}
            QListWidget#GmailList::item:selected {{
                background: rgba(56, 189, 248, 0.14);
                border-left: 2px solid #38bdf8;
            }}
            QTextBrowser#GmailPreview {{
                background: transparent;
                color: #dbe7f1;
                border: none;
                padding: 18px 3px 8px 3px;
                selection-background-color: rgba(56, 189, 248, 0.45);
            }}
        """ + _scrollbar_qss()

    def _run(self, op: str, fn):
        if op == "list":
            self.status.setText("Cargando…")
            self.prev_page_btn.setEnabled(False)
            self.next_page_btn.setEnabled(False)

        def worker():
            try:
                result = fn()
                if op == "read_images" and isinstance(result, dict):
                    prepared = dict(result)
                    current = self._rendered_email or {}
                    html_body = str(current.get("html") or "").strip()
                    if html_body:
                        prepared["_prepared_html"] = self._inject_email_images(
                            html_body,
                            prepared.get("inline_images") or [],
                        )
                    result = prepared
            except Exception as exc:
                result = exc
            try:
                self._result_sig.emit(op, result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _sender_parts(self, value: str) -> tuple[str, str]:
        from email.utils import parseaddr

        name, address = parseaddr(str(value or ""))
        name = name.strip().strip('"') or address.split("@", 1)[0] or "Remitente"
        return name, address

    def _short_date(self, value: str, include_time: bool = False) -> str:
        from datetime import datetime
        from email.utils import parsedate_to_datetime

        try:
            dt = parsedate_to_datetime(str(value or ""))
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            now = datetime.now(dt.tzinfo)
            if dt.date() == now.date():
                return dt.strftime("%H:%M")
            if dt.year == now.year:
                return dt.strftime("%d %b" + (", %H:%M" if include_time else ""))
            return dt.strftime("%d %b %Y")
        except Exception:
            text = str(value or "").strip()
            return text[:22]

    def _email_row_widget(self, email: dict) -> QWidget:
        row = QWidget()
        row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(10, 8, 9, 8)
        layout.setSpacing(2)

        sender_name, _address = self._sender_parts(email.get("from", ""))
        top = QHBoxLayout()
        top.setSpacing(6)
        sender = QLabel(sender_name)
        sender.setStyleSheet(
            "color:#f3f8fc; background:transparent; font-size:12px; font-weight:800;"
            if email.get("unread")
            else "color:#d0dde7; background:transparent; font-size:12px; font-weight:650;"
        )
        date = QLabel(self._short_date(email.get("date", "")))
        date.setStyleSheet("color:rgba(186,215,238,0.50); background:transparent; font-size:10px;")
        date.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(sender, stretch=1)
        top.addWidget(date)
        layout.addLayout(top)

        subject_text = str(email.get("subject") or "(sin asunto)").strip()
        if len(subject_text) > 54:
            subject_text = subject_text[:53].rstrip() + "…"
        subject = QLabel(("●  " if email.get("unread") else "") + subject_text)
        subject.setStyleSheet(
            "color:#8edbff; background:transparent; font-size:11px; font-weight:750;"
            if email.get("unread")
            else "color:#aebdca; background:transparent; font-size:11px;"
        )
        layout.addWidget(subject)

        snippet_text = re.sub(r"\s+", " ", str(email.get("snippet") or "")).strip()
        if len(snippet_text) > 76:
            snippet_text = snippet_text[:75].rstrip() + "…"
        snippet = QLabel(snippet_text)
        snippet.setStyleSheet("color:rgba(190,207,220,0.48); background:transparent; font-size:10px;")
        layout.addWidget(snippet)
        return row

    def _set_reader_header(self, email: dict):
        sender_name, sender_address = self._sender_parts(email.get("from", ""))
        self.reader_subject.setText(email.get("subject") or "(sin asunto)")
        self.reader_sender.setText(sender_name)
        self.reader_recipient.setText(sender_address)
        self.reader_date.setText(self._short_date(email.get("date", ""), include_time=True))
        initials = "".join(part[:1] for part in sender_name.split()[:2]).upper() or "@"
        self.sender_avatar.setText(initials)

    def _apply_responsive_layout(self):
        narrow = self.width() < 760
        if not narrow:
            self.reader_page.setVisible(True)
            self.inbox_page.setVisible(True)
            self.back_btn.setVisible(False)
            self.inbox_page.setMaximumWidth(360)
            self.inbox_page.setMinimumWidth(285)
            return

        self.inbox_page.setMaximumWidth(16777215)
        self.inbox_page.setMinimumWidth(0)
        if self._compact_reader and self._current_email:
            self.reader_page.setVisible(True)
            self.inbox_page.setVisible(False)
            self.back_btn.setVisible(True)
        else:
            self.reader_page.setVisible(False)
            self.inbox_page.setVisible(True)
            self.back_btn.setVisible(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def load_inbox(self):
        self._show_inbox()
        self._list_label = "INBOX"
        self._list_unread = False
        self._list_query = ""
        self._load_page(1)

    def load_recent(self):
        self._show_inbox()
        self._list_label = "ALL"
        self._list_unread = False
        self._list_query = ""
        self._load_page(1)

    def load_unread(self):
        self._show_inbox()
        self._list_label = "ALL"
        self._list_unread = True
        self._list_query = ""
        self._load_page(1)

    def search_emails(self):
        query = self.search_input.text().strip()
        if not query:
            self.load_recent()
            return
        self._show_inbox()
        self._list_label = "ALL"
        self._list_unread = False
        self._list_query = query
        self._load_page(1)

    def _load_page(self, page: int):
        if page < 1 or page > self._pages:
            return
        self._show_inbox()
        label = self._list_label
        unread = self._list_unread
        query = self._list_query
        page_size = self._page_size
        self._run(
            "list",
            lambda: __import__(
                "actions.gmail",
                fromlist=["get_email_page"],
            ).get_email_page(
                page=page,
                page_size=page_size,
                label=label,
                unread_only=unread,
                query=query,
            ),
        )

    def _on_email_selected(self):
        item = self.email_list.currentItem()
        if not item:
            return
        email = item.data(Qt.ItemDataRole.UserRole) or {}
        email_id = email.get("id")
        if email_id:
            self._current_email = email
            self._rendered_email = None
            self._set_reader_header(email)
            snippet = html_lib.escape(str(email.get("snippet") or "").strip())
            loading_text = snippet or "Cargando mensaje…"
            self.preview.setHtml(
                f"<p style='color:rgba(186,215,238,.58); margin:24px 0; line-height:1.6;'>{loading_text}</p>"
            )
            self._compact_reader = True
            self._apply_responsive_layout()
            self._run("read", lambda: __import__("actions.gmail", fromlist=["read_email"]).read_email(email_id))

    def _is_complex_email(self, html_body: str) -> bool:
        html_body = str(html_body or "")
        return (
            len(html_body) > 30000
            and (
                "@media" in html_body.lower()
                or len(re.findall(r"<table\b", html_body, flags=re.I)) >= 12
                or "mso-" in html_body.lower()
            )
        )

    def _show_inbox(self):
        self._compact_reader = False
        self._apply_responsive_layout()

    def _handle_result(self, op: str, result):
        if isinstance(result, Exception):
            if op == "list":
                self.status.setText("Error")
                self.prev_page_btn.setEnabled(self._page > 1)
                self.next_page_btn.setEnabled(self._page < self._pages)
            elif op == "read":
                self.preview.setPlainText(str(result))
            return
        if op == "list":
            page_data = result if isinstance(result, dict) else {"emails": result or []}
            self._items = list(page_data.get("emails") or [])
            self._page = int(page_data.get("page") or 1)
            self._pages = max(1, int(page_data.get("pages") or 1))
            self._total_emails = max(0, int(page_data.get("total") or len(self._items)))
            self.email_list.clear()
            for email in self._items:
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, email)
                item.setSizeHint(QSize(0, 76))
                self.email_list.addItem(item)
                self.email_list.setItemWidget(item, self._email_row_widget(email))
            self.status.setText(f"{self._total_emails} correos")
            self.page_label.setText(f"{self._page} / {self._pages}")
            self.prev_page_btn.setEnabled(self._page > 1)
            self.next_page_btn.setEnabled(self._page < self._pages)
            if not self._items:
                self.status.setText("No hay resultados.")
            return
        if op == "read":
            if not self._current_email or result.get("id") != self._current_email.get("id"):
                return
            self._rendered_email = dict(result)
            self._set_reader_header(result)
            to_value = str(result.get("to") or "").strip()
            if to_value:
                self.reader_recipient.setText(f"{self.reader_recipient.text()}  ·  para {to_value}")
            cached_html = self._enriched_email_html.get(str(result.get("id") or ""))
            if cached_html:
                enriched = dict(result)
                enriched["_prepared_html"] = cached_html
                self._render_email_body(enriched)
            else:
                self._render_email_body(result)
            if self._is_complex_email(result.get("html", "")):
                email_id = result.get("id")
                width = max(680, self.preview.viewport().width())
                self._run(
                    "render_mail",
                    lambda: __import__(
                        "actions.gmail",
                        fromlist=["render_email_preview"],
                    ).render_email_preview(email_id, result.get("html", ""), width),
                )
                return
            if result.get("html") and not cached_html:
                email_id = result.get("id")
                html_body = str(result.get("html") or "")
                if re.search(r'<img\b[^>]*\bsrc=["\']cid:', html_body, flags=re.I):
                    self._run(
                        "read_images",
                        lambda: __import__(
                            "actions.gmail",
                            fromlist=["read_email_images"],
                        ).read_email_images(email_id),
                    )
                else:
                    self._run(
                        "read_images",
                        lambda: {"id": email_id, "inline_images": []},
                    )
            return
        if op == "read_images":
            if not self._current_email or result.get("id") != self._current_email.get("id"):
                return
            enriched = dict(self._rendered_email or {})
            prepared_html = result.get("_prepared_html")
            if prepared_html:
                self._enriched_email_html[str(result.get("id") or "")] = prepared_html
                enriched["_prepared_html"] = prepared_html
                self._render_email_body(enriched)
            return
        if op == "render_mail":
            if not self._current_email or result.get("id") != self._current_email.get("id"):
                return
            image_path = str(result.get("image_path") or "")
            if not image_path or not Path(image_path).exists():
                return
            image_url = QUrl.fromLocalFile(image_path).toString()
            display_width = max(320, self.preview.viewport().width() - 18)
            self.preview.setHtml(
                "<html><body style='margin:0;background:#050a12;text-align:center;'>"
                f"<img src='{image_url}' width='{display_width}' style='display:block;margin:0 auto;'>"
                "</body></html>"
            )

    def _render_email_body(self, result: dict):
        html_body = (result.get("_prepared_html") or result.get("html") or "").strip()
        plain_body = (result.get("body") or "").strip()
        if html_body:
            self.preview.setHtml(
                f"""
                <html>
                  <head>
                    <style>
                      body {{
                        font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                        color: {C.TEXT};
                        background: transparent;
                        font-size: 13px;
                        line-height: 1.62;
                        margin: 0;
                        padding: 10px 8px 24px 8px;
                        overflow-wrap: anywhere;
                      }}
                      .mail-content {{ max-width: 780px; margin: 0 auto; }}
                      p {{ margin: 0 0 14px 0; }}
                      a {{ color: #7dd3fc; text-decoration: none; }}
                      a:hover {{ text-decoration: underline; }}
                      img, video {{ max-width: 100%; height: auto; }}
                      table {{ border-collapse: collapse; max-width: 100%; margin: 8px 0; }}
                      td, th {{ border-color: rgba(255,255,255,0.12); padding: 4px; }}
                      blockquote {{
                        margin: 14px 0;
                        padding-left: 14px;
                        border-left: 2px solid rgba(125, 211, 252, 0.35);
                        color: rgba(248, 250, 252, 0.82);
                      }}
                    </style>
                  </head>
                  <body><div class="mail-content">{html_body}</div></body>
                </html>
                """
            )
        else:
            self.preview.setHtml(
                f"""
                <html>
                  <body style="font-family:'{FONT_UI}', '{FONT_UI_FALLBACK}'; color:{C.TEXT}; font-size:13px; white-space:pre-wrap; line-height:1.62; margin:0; padding:10px 8px 24px 8px;">
                    <div style="max-width:780px; margin:0 auto;">{html_lib.escape(plain_body).replace("\n", "<br>")}</div>
                  </body>
                </html>
                """
            )

    def _inject_email_images(self, html_body: str, inline_images: list[dict]) -> str:
        transparent_pixel = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        cid_map = {}
        for img in inline_images:
            cid = str(img.get("cid") or "").strip().lower()
            data_url = str(img.get("data_url") or "").strip()
            if cid and data_url:
                cid_map[cid] = data_url

        def _displayable_data_url(raw: bytes, mime: str) -> str:
            byte_array = QByteArray(raw)
            buffer = QBuffer(byte_array)
            if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
                return ""
            reader = QImageReader(buffer)
            size = reader.size()
            if size.isValid() and size.width() <= 2 and size.height() <= 2:
                return ""
            if mime in {"image/gif", "image/webp"}:
                image = reader.read()
                if image.isNull():
                    return ""
                png_bytes = QByteArray()
                png_buffer = QBuffer(png_bytes)
                png_buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                if image.save(png_buffer, "PNG"):
                    raw = bytes(png_bytes)
                    mime = "image/png"
            return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

        def _normalize_data_url(src: str) -> str:
            match = re.match(
                r"data:(image/[^;,]+)(?:;[^,]*)?;base64,(.+)",
                src,
                flags=re.I | re.S,
            )
            if not match:
                return src
            try:
                raw = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=False)
            except Exception:
                return src
            return _displayable_data_url(raw, match.group(1).lower())

        def _fetch_remote_image(src: str) -> str:
            src = html_lib.unescape(str(src or "").strip())
            if src.startswith("//"):
                src = "https:" + src
            if not src.startswith(("http://", "https://")):
                return src
            cache = getattr(self, "_email_img_cache", None)
            if cache is None:
                cache = {}
                self._email_img_cache = cache
            if src in cache:
                return cache[src]

            cache_dir = MEMORY_DIR / "gmail_images"
            cache_key = hashlib.sha256(src.encode("utf-8", errors="ignore")).hexdigest()
            cached_files = list(cache_dir.glob(f"{cache_key}.*")) if cache_dir.exists() else []
            if cached_files:
                try:
                    cached_file = cached_files[0]
                    raw = cached_file.read_bytes()
                    suffix = cached_file.suffix.lower().lstrip(".") or "png"
                    mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
                    data_url = _displayable_data_url(raw, mime)
                    if not data_url:
                        cache[src] = ""
                        return ""
                    cache[src] = data_url
                    return data_url
                except OSError:
                    pass
            try:
                resp = requests.get(
                    src,
                    timeout=12,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0 Safari/537.36"
                        ),
                        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                    },
                )
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "image/png").split(";")[0].strip()
                if not ctype.startswith("image/") or not resp.content:
                    cache[src] = src
                    return src
                data_url = _displayable_data_url(resp.content, ctype)
                if not data_url:
                    cache[src] = ""
                    return ""
                cache[src] = data_url
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    extension = {
                        "image/jpeg": "jpg",
                        "image/png": "png",
                        "image/gif": "gif",
                        "image/webp": "webp",
                        "image/svg+xml": "svg",
                    }.get(ctype, "img")
                    (cache_dir / f"{cache_key}.{extension}").write_bytes(resp.content)
                except OSError:
                    pass
                return data_url
            except Exception:
                cache[src] = src
                return src

        def _repl(match: re.Match) -> str:
            prefix, src, suffix = match.group(1), match.group(2), match.group(3)
            key = src.strip().lower()
            if key.startswith("cid:"):
                cid = key[4:].strip("<>")
                data_url = cid_map.get(cid)
                if data_url:
                    return f"{prefix}{_normalize_data_url(data_url) or transparent_pixel}{suffix}"
                return match.group(0)
            if key.startswith("data:image/"):
                return f"{prefix}{_normalize_data_url(src) or transparent_pixel}{suffix}"
            if key.startswith(("http://", "https://", "//")):
                return f"{prefix}{_fetch_remote_image(src) or transparent_pixel}{suffix}"
            return match.group(0)

        prepared = re.sub(
            r'(<img\b[^>]*\bsrc=["\'])([^"\']+)(["\'])',
            _repl,
            html_body,
            flags=re.I,
        )

        def _css_repl(match: re.Match) -> str:
            prefix, quote, src, suffix = (
                match.group(1),
                match.group(2) or "",
                match.group(3),
                match.group(4),
            )
            key = html_lib.unescape(src.strip()).lower()
            if key.startswith(("http://", "https://", "//")):
                return f"{prefix}{quote}{_fetch_remote_image(src)}{quote}{suffix}"
            return match.group(0)

        return re.sub(
            r'((?:background|background-image)\s*:[^;]*?\burl\(\s*)(["\']?)([^)"\']+)(\s*\))',
            _css_repl,
            prepared,
            flags=re.I,
        )


class DriveModePanel(QWidget):
    _result_sig = pyqtSignal(str, object)
    _preview_sig = pyqtSignal(int, object)

    def __init__(self, progress_hook=None, parent=None):
        super().__init__(parent)
        self.progress_hook = progress_hook
        self._items: list[dict] = []
        self._preview_request = 0
        self._preview_cache: dict[str, dict] = {}
        self._audio_temp_files: dict[str, str] = {}
        self._folder_stack: list[tuple[str, str]] = []
        self._current_folder_id = "root"
        self._current_folder_name = "Mi unidad"
        self.setStyleSheet(self._panel_style())
        self._result_sig.connect(self._handle_result)
        self._preview_sig.connect(self._apply_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        heading = QHBoxLayout()
        heading.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Drive")
        title.setObjectName("DriveTitle")
        subtitle = QLabel("Archivos y documentos de tu cuenta")
        subtitle.setObjectName("DriveSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch()
        self.status = QLabel("Listo")
        self.status.setObjectName("DriveStatus")
        heading.addWidget(self.status)
        root.addLayout(heading)

        toolbar = QFrame()
        toolbar.setObjectName("DriveToolbar")
        search_row = QHBoxLayout(toolbar)
        search_row.setContentsMargins(10, 8, 10, 8)
        search_row.setSpacing(8)
        self.folder_back = QPushButton()
        self.folder_back.setObjectName("DriveToolButton")
        self.folder_back.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 17))
        self.folder_back.setToolTip("Volver a la carpeta anterior")
        self.folder_back.setEnabled(False)
        self.folder_back.clicked.connect(self.go_back_folder)
        search_row.addWidget(self.folder_back)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("DriveSearch")
        self.search_input.setPlaceholderText("Buscar archivos en Drive...")
        self.search_input.returnPressed.connect(self.search_files)
        search_row.addWidget(self.search_input, stretch=1)
        recent = QPushButton("Mi unidad")
        recent.setObjectName("DriveToolButton")
        recent.setIcon(_line_icon("refresh", C.TEXT_DIM, 17))
        recent.clicked.connect(self.load_recent)
        search = QPushButton("Buscar")
        search.setObjectName("DriveToolButton")
        search.setIcon(_line_icon("search", C.TEXT_DIM, 17))
        search.clicked.connect(self.search_files)
        upload = QPushButton("Subir")
        upload.setObjectName("DrivePrimaryButton")
        upload.setIcon(_line_icon("upload", C.PRI, 17))
        upload.clicked.connect(self.upload_selected_file)
        search_row.addWidget(recent)
        search_row.addWidget(search)
        search_row.addWidget(upload)
        root.addWidget(toolbar)

        self.folder_path = QLabel("Mi unidad")
        self.folder_path.setObjectName("DrivePath")
        root.addWidget(self.folder_path)

        body = QHBoxLayout()
        body.setSpacing(10)
        list_panel = QFrame()
        list_panel.setObjectName("DriveListPanel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        list_header = QLabel("ARCHIVOS")
        list_header.setObjectName("DriveSectionLabel")
        list_layout.addWidget(list_header)
        self.file_list = QListWidget()
        self.file_list.setObjectName("DriveFileList")
        self.file_list.itemSelectionChanged.connect(self._show_selected_details)
        self.file_list.itemDoubleClicked.connect(self._activate_drive_item)
        list_layout.addWidget(self.file_list, stretch=1)
        body.addWidget(list_panel, stretch=7)

        details_panel = QFrame()
        details_panel.setObjectName("DriveDetailsPanel")
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(16, 14, 16, 14)
        details_layout.setSpacing(8)
        details_header = QLabel("DETALLES")
        details_header.setObjectName("DriveSectionLabel")
        details_layout.addWidget(details_header)
        self.preview_stack = QStackedWidget()
        self.preview_stack.setObjectName("DrivePreviewStack")
        self.preview_image = QLabel("Selecciona un archivo")
        self.preview_image.setObjectName("DrivePreviewImage")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setMinimumHeight(250)
        self.preview_image.setWordWrap(True)
        self.preview_text = QTextBrowser()
        self.preview_text.setObjectName("DrivePreviewText")
        self.preview_text.setReadOnly(True)
        self.preview_audio = QWidget()
        audio_layout = QVBoxLayout(self.preview_audio)
        audio_layout.setContentsMargins(28, 28, 28, 28)
        audio_layout.setSpacing(14)
        audio_layout.addStretch()
        self.audio_title = QLabel("Audio")
        self.audio_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.audio_title.setStyleSheet(
            f"color:{C.TEXT};font-size:16px;font-weight:750;background:transparent;"
        )
        audio_layout.addWidget(self.audio_title)
        audio_controls = QHBoxLayout()
        audio_controls.setSpacing(10)
        self.audio_play = QPushButton()
        self.audio_play.setFixedSize(42, 42)
        self.audio_play.setIcon(_line_icon("play", C.PRI, 19))
        self.audio_play.setIconSize(QSize(19, 19))
        self.audio_play.setToolTip("Reproducir o pausar")
        self.audio_play.clicked.connect(self._toggle_drive_audio)
        self.audio_seek = QSlider(Qt.Orientation.Horizontal)
        self.audio_seek.setRange(0, 0)
        self.audio_seek.sliderMoved.connect(self._seek_drive_audio)
        self.audio_time = QLabel("0:00 / 0:00")
        self.audio_time.setStyleSheet(f"color:{C.TEXT_MED};background:transparent;")
        audio_controls.addWidget(self.audio_play)
        audio_controls.addWidget(self.audio_seek, stretch=1)
        audio_controls.addWidget(self.audio_time)
        audio_layout.addLayout(audio_controls)
        audio_layout.addStretch()

        self._drive_audio_output = QAudioOutput(self)
        self._drive_audio_output.setVolume(0.75)
        self._drive_audio_player = QMediaPlayer(self)
        self._drive_audio_player.setAudioOutput(self._drive_audio_output)
        self._drive_audio_player.positionChanged.connect(self._update_drive_audio_position)
        self._drive_audio_player.durationChanged.connect(self._update_drive_audio_duration)
        self._drive_audio_player.playbackStateChanged.connect(self._update_drive_audio_state)
        self._drive_audio_player.errorOccurred.connect(self._drive_audio_error)
        self.preview_stack.addWidget(self.preview_image)
        self.preview_stack.addWidget(self.preview_text)
        self.preview_stack.addWidget(self.preview_audio)
        details_layout.addWidget(self.preview_stack, stretch=4)

        self.details = QTextBrowser()
        self.details.setObjectName("DriveDetails")
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Selecciona un archivo para ver detalles.")
        self.details.setOpenExternalLinks(True)
        self.details.setMaximumHeight(150)
        details_layout.addWidget(self.details, stretch=1)
        body.addWidget(details_panel, stretch=5)
        root.addLayout(body, stretch=1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for text, icon, handler, destructive in [
            ("Descargar", "download", self.download_selected_file, False),
            ("Compartir", "share", self.share_selected_file, False),
            ("Renombrar", "edit", self.rename_selected_file, False),
            ("Borrar", "trash", self.delete_selected_file, True),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("DriveDangerButton" if destructive else "DriveActionButton")
            btn.setIcon(_line_icon(icon, "#fda4af" if destructive else C.TEXT_DIM, 16))
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(handler)
            actions.addWidget(btn)
        actions.addStretch()
        root.addLayout(actions)
        QTimer.singleShot(200, self.load_recent)

    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                letter-spacing: 0;
            }}
            QLabel#DriveTitle {{
                color: #f8fafc;
                font-size: 22px;
                font-weight: 900;
            }}
            QLabel#DriveSubtitle {{
                color: rgba(186, 215, 238, 0.58);
                font-size: 11px;
            }}
            QLabel#DriveStatus {{
                background: rgba(52, 211, 153, 0.10);
                color: #a7f3d0;
                border: 1px solid rgba(52, 211, 153, 0.22);
                border-radius: 6px;
                padding: 5px 9px;
                font-size: 10px;
                font-weight: 800;
            }}
            QFrame#DriveToolbar, QFrame#DriveListPanel, QFrame#DriveDetailsPanel {{
                background: rgba(7, 15, 26, 0.90);
                border: 1px solid rgba(125, 211, 252, 0.11);
                border-radius: 10px;
            }}
            QLineEdit#DriveSearch {{
                min-height: 34px;
                background: rgba(2, 8, 15, 0.72);
                color: {C.TEXT};
                border: 1px solid rgba(125, 211, 252, 0.12);
                border-radius: 7px;
                padding: 0 11px;
                selection-background-color: {C.PRI};
            }}
            QLineEdit#DriveSearch:focus {{
                border-color: rgba(125, 211, 252, 0.52);
            }}
            QLabel#DriveSectionLabel {{
                color: rgba(125, 211, 252, 0.72);
                padding: 13px 15px 9px 15px;
                font-size: 10px;
                font-weight: 900;
            }}
            QLabel#DrivePath {{
                color: rgba(186, 215, 238, 0.72);
                padding: 0 4px;
                font-size: 11px;
                font-weight: 700;
            }}
            QListWidget#DriveFileList {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                outline: none;
                padding: 0 8px 8px 8px;
            }}
            QListWidget#DriveFileList::item {{
                min-height: 42px;
                border: none;
                border-bottom: 1px solid rgba(125, 211, 252, 0.08);
                padding: 7px 9px;
            }}
            QListWidget#DriveFileList::item:hover {{
                background: rgba(125, 211, 252, 0.06);
            }}
            QListWidget#DriveFileList::item:selected {{
                background: rgba(56, 189, 248, 0.13);
                color: #f8fafc;
                border-left: 2px solid #38bdf8;
            }}
            QStackedWidget#DrivePreviewStack {{
                background: rgba(2, 8, 15, 0.46);
                border: 1px solid rgba(125, 211, 252, 0.08);
                border-radius: 8px;
            }}
            QLabel#DrivePreviewImage {{
                color: rgba(186, 215, 238, 0.58);
                background: transparent;
                border: none;
                padding: 14px;
                font-size: 12px;
            }}
            QTextBrowser#DrivePreviewText {{
                background: transparent;
                color: #dbe7f1;
                border: none;
                padding: 12px;
                font-family: "Cascadia Mono", "Consolas";
                font-size: 11px;
            }}
            QTextBrowser#DriveDetails {{
                background: transparent;
                color: #dbe7f1;
                border: none;
                padding: 4px 0 0 0;
                selection-background-color: rgba(56, 189, 248, 0.40);
            }}
            QPushButton#DriveToolButton, QPushButton#DriveActionButton {{
                min-height: 32px;
                background: rgba(255, 255, 255, 0.035);
                color: {C.TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.075);
                border-radius: 7px;
                padding: 0 11px;
                font-weight: 700;
            }}
            QPushButton#DrivePrimaryButton {{
                min-height: 32px;
                background: rgba(56, 189, 248, 0.16);
                color: #dff5ff;
                border: 1px solid rgba(125, 211, 252, 0.28);
                border-radius: 7px;
                padding: 0 12px;
                font-weight: 800;
            }}
            QPushButton#DriveDangerButton {{
                min-height: 32px;
                background: rgba(244, 63, 94, 0.07);
                color: #fda4af;
                border: 1px solid rgba(244, 63, 94, 0.16);
                border-radius: 7px;
                padding: 0 11px;
                font-weight: 700;
            }}
            QPushButton#DriveToolButton:hover, QPushButton#DriveActionButton:hover,
            QPushButton#DrivePrimaryButton:hover {{
                background: rgba(56, 189, 248, 0.18);
                color: {C.TEXT};
                border-color: rgba(125, 211, 252, 0.34);
            }}
            QPushButton#DriveDangerButton:hover {{
                background: rgba(244, 63, 94, 0.14);
                border-color: rgba(251, 113, 133, 0.32);
            }}
        """ + _scrollbar_qss()

    def _run(self, op: str, fn):
        self.status.setText("Trabajando...")

        def worker():
            try:
                result = fn()
            except Exception as exc:
                result = exc
            self._result_sig.emit(op, result)

        threading.Thread(target=worker, daemon=True).start()

    def _selected_file(self) -> dict:
        item = self.file_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else {}

    def load_recent(self):
        self._folder_stack.clear()
        self._current_folder_id = "root"
        self._current_folder_name = "Mi unidad"
        self.search_input.clear()
        self._update_folder_navigation()
        self._load_current_folder()

    def _load_current_folder(self):
        folder_id = self._current_folder_id
        self._run(
            "list",
            lambda: __import__("actions.gdrive", fromlist=["list_files"]).list_files(
                count=200,
                folder_id=folder_id,
            ),
        )

    def _activate_drive_item(self, item):
        file = item.data(Qt.ItemDataRole.UserRole) if item else {}
        if file.get("mimeType") == "application/vnd.google-apps.folder":
            self._folder_stack.append((self._current_folder_id, self._current_folder_name))
            self._current_folder_id = str(file.get("id") or "")
            self._current_folder_name = str(file.get("name") or "Carpeta")
            self.search_input.clear()
            self._update_folder_navigation()
            self._load_current_folder()
            return
        self._show_selected_details()

    def go_back_folder(self):
        if not self._folder_stack:
            return
        self._current_folder_id, self._current_folder_name = self._folder_stack.pop()
        self.search_input.clear()
        self._update_folder_navigation()
        self._load_current_folder()

    def _update_folder_navigation(self):
        names = [name for _folder_id, name in self._folder_stack]
        names.append(self._current_folder_name)
        self.folder_path.setText("  /  ".join(names))
        self.folder_back.setEnabled(bool(self._folder_stack))

    def search_files(self):
        query = self.search_input.text().strip()
        if not query:
            self._load_current_folder()
            return
        folder_id = self._current_folder_id
        self._run(
            "list",
            lambda: __import__("actions.gdrive", fromlist=["search_files"]).search_files(
                query,
                count=200,
                folder_id=folder_id,
            ),
        )

    def upload_selected_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Selecciona un archivo para subir")
        if not path:
            return
        self._run(
            "uploaded",
            lambda: __import__("actions.gdrive", fromlist=["upload_file"]).upload_file(
                path,
                progress_hook=self.progress_hook,
            ),
        )

    def download_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        self._run(
            "downloaded",
            lambda: __import__("actions.gdrive", fromlist=["download_file"]).download_file(
                file_id=file.get("id", ""),
                progress_hook=self.progress_hook,
            ),
        )

    def share_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        email, ok = QInputDialog.getText(self, "Compartir archivo", "Email o vacio para enlace publico:")
        if not ok:
            return
        self._run(
            "shared",
            lambda: __import__("actions.gdrive", fromlist=["share_file"]).share_file(
                file_id=file.get("id", ""),
                email=email.strip(),
                anyone=not bool(email.strip()),
                role="reader",
            ),
        )

    def rename_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        new_name, ok = QInputDialog.getText(self, "Renombrar archivo", "Nuevo nombre:", text=file.get("name", ""))
        if not ok or not new_name.strip():
            return
        self._run(
            "renamed",
            lambda: __import__("actions.gdrive", fromlist=["rename_file"]).rename_file(
                file_id=file.get("id", ""),
                new_name=new_name.strip(),
            ),
        )

    def delete_selected_file(self):
        file = self._selected_file()
        if not file:
            return
        answer = QMessageBox.question(
            self,
            "Borrar archivo",
            f"Enviar a la papelera: {file.get('name', '')}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run(
            "deleted",
            lambda: __import__("actions.gdrive", fromlist=["delete_file"]).delete_file(file_id=file.get("id", "")),
        )

    def _show_selected_details(self):
        file = self._selected_file()
        if not file:
            return
        if self._drive_audio_player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self._drive_audio_player.stop()
        self._render_drive_metadata(file)
        file_id = str(file.get("id") or "")
        if not file_id:
            return
        self._preview_request += 1
        request_id = self._preview_request
        audio_path = self._audio_temp_files.get(file_id)
        if audio_path and Path(audio_path).exists():
            self._apply_preview(
                request_id,
                {
                    "kind": "audio",
                    "data": b"",
                    "mimeType": file.get("mimeType") or "",
                    "info": file,
                },
            )
            return
        cached = self._preview_cache.get(file_id)
        if cached is not None:
            self._apply_preview(request_id, cached)
            return
        self.preview_stack.setCurrentWidget(self.preview_image)
        self.preview_image.setPixmap(QPixmap())
        self.preview_image.setText("Cargando previsualización...")
        self.status.setText("Cargando vista previa...")

        def worker():
            try:
                result = __import__(
                    "actions.gdrive",
                    fromlist=["get_file_preview"],
                ).get_file_preview(file_id)
            except Exception as exc:
                result = exc
            self._preview_sig.emit(request_id, result)

        threading.Thread(target=worker, daemon=True).start()

    def _render_drive_metadata(self, file: dict):
        name = html_lib.escape(str(file.get("name") or "(sin nombre)"))
        mime = html_lib.escape(str(file.get("mimeType") or "Tipo desconocido"))
        modified = html_lib.escape(str(file.get("modifiedTime") or "")[:19].replace("T", " "))
        description = html_lib.escape(str(file.get("description") or ""))
        try:
            size = int(file.get("size") or 0)
            units = ["B", "KB", "MB", "GB"]
            value = float(size)
            unit = units[0]
            for candidate in units:
                unit = candidate
                if value < 1024 or candidate == units[-1]:
                    break
                value /= 1024
            size_text = f"{value:.1f} {unit}" if size else ""
        except (TypeError, ValueError):
            size_text = ""
        rows = [
            f"<div style='font-size:15px;font-weight:700;color:#f8fafc'>{name}</div>",
            f"<div style='margin-top:5px;color:#91a9bd'>{mime}</div>",
        ]
        facts = " · ".join(part for part in (size_text, modified) if part)
        if facts:
            rows.append(f"<div style='margin-top:5px;color:#7890a5'>{html_lib.escape(facts)}</div>")
        if description:
            rows.append(f"<div style='margin-top:8px;color:#cbd5e1'>{description}</div>")
        self.details.setHtml("".join(rows))

    def _apply_preview(self, request_id: int, result):
        if request_id != self._preview_request:
            return
        if isinstance(result, Exception):
            self.preview_stack.setCurrentWidget(self.preview_image)
            self.preview_image.setPixmap(QPixmap())
            self.preview_image.setText(f"No se pudo cargar la vista previa.\n{result}")
            self.status.setText("Vista previa no disponible")
            return
        if not isinstance(result, dict):
            return
        file_id = str((result.get("info") or {}).get("id") or "")
        kind = str(result.get("kind") or "unsupported")
        raw = bytes(result.get("data") or b"")
        if file_id and kind != "audio":
            self._preview_cache[file_id] = result
        self.status.setText("Listo")

        if kind == "text":
            text = raw.decode("utf-8", errors="replace")
            if len(text) > 120_000:
                text = text[:120_000] + "\n\n[Vista previa recortada]"
            self.preview_text.setPlainText(text)
            self.preview_stack.setCurrentWidget(self.preview_text)
            return

        if kind == "audio":
            info = result.get("info") or {}
            file_id = str(info.get("id") or "")
            name = str(info.get("name") or result.get("name") or "Audio")
            path = self._audio_temp_files.get(file_id)
            if not path or not Path(path).exists():
                suffix = Path(name).suffix
                if not suffix:
                    suffix = mimetypes.guess_extension(str(result.get("mimeType") or "")) or ".audio"
                handle = tempfile.NamedTemporaryFile(
                    prefix="jarvis-drive-",
                    suffix=suffix,
                    delete=False,
                )
                try:
                    handle.write(raw)
                    path = handle.name
                finally:
                    handle.close()
                if file_id:
                    self._audio_temp_files[file_id] = path
            self.audio_title.setText(name)
            self.audio_seek.setValue(0)
            self.audio_time.setText("0:00 / 0:00")
            self._drive_audio_player.setSource(QUrl.fromLocalFile(path))
            self.preview_stack.setCurrentWidget(self.preview_audio)
            return

        pixmap = QPixmap()
        if kind == "image":
            pixmap.loadFromData(raw)
        elif kind == "pdf":
            buffer = QBuffer()
            buffer.setData(QByteArray(raw))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            document = QPdfDocument(self)
            document.load(buffer)
            if document.pageCount() > 0:
                pixmap = QPixmap.fromImage(document.render(0, QSize(900, 1180)))
            document.deleteLater()

        self.preview_stack.setCurrentWidget(self.preview_image)
        if not pixmap.isNull():
            target = self.preview_image.size()
            if target.width() < 100 or target.height() < 100:
                target = QSize(620, 360)
            self.preview_image.setText("")
            self.preview_image.setPixmap(
                pixmap.scaled(
                    target,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            return

        labels = {
            "folder": "Carpeta de Google Drive",
            "too_large": "Archivo demasiado grande para previsualizar",
            "unsupported": "Este formato no admite previsualizacion",
        }
        self.preview_image.setPixmap(QPixmap())
        self.preview_image.setText(labels.get(kind, "Vista previa no disponible"))

    def _toggle_drive_audio(self):
        if self._drive_audio_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._drive_audio_player.pause()
        else:
            self._drive_audio_player.play()

    def _seek_drive_audio(self, position: int):
        self._drive_audio_player.setPosition(int(position))

    def _update_drive_audio_duration(self, duration: int):
        self.audio_seek.setRange(0, max(0, int(duration)))
        self._update_drive_audio_position(self._drive_audio_player.position())

    def _update_drive_audio_position(self, position: int):
        if not self.audio_seek.isSliderDown():
            self.audio_seek.setValue(max(0, int(position)))
        duration = max(0, int(self._drive_audio_player.duration()))
        self.audio_time.setText(
            f"{self._format_drive_media_time(position)} / {self._format_drive_media_time(duration)}"
        )

    def _update_drive_audio_state(self, state):
        icon = "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play"
        self.audio_play.setIcon(_line_icon(icon, C.PRI, 19))

    def _drive_audio_error(self, _error, message: str):
        if message:
            self.status.setText("No se pudo reproducir el audio")
            self.audio_title.setToolTip(message)

    @staticmethod
    def _format_drive_media_time(milliseconds: int) -> str:
        seconds = max(0, int(milliseconds) // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def closeEvent(self, event):
        self._drive_audio_player.stop()
        for path in self._audio_temp_files.values():
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        self._audio_temp_files.clear()
        super().closeEvent(event)

    def _handle_result(self, op: str, result):
        if isinstance(result, Exception):
            self.status.setText("Error")
            self.details.setPlainText(str(result))
            return
        if op == "list":
            self._items = list(result or [])
            self.file_list.clear()
            for file in self._items:
                item = QListWidgetItem(f"{file.get('name') or '(sin nombre)'}\n{file.get('modifiedTime', '')[:10]}")
                mime = str(file.get("mimeType") or "").lower()
                icon_name = "folder" if "folder" in mime else "image" if "image" in mime else "video" if "video" in mime else "audio" if "audio" in mime else "archive" if any(value in mime for value in ("zip", "rar", "compressed")) else "file"
                item.setIcon(_line_icon(icon_name, C.TEXT_MED, 18))
                item.setData(Qt.ItemDataRole.UserRole, file)
                self.file_list.addItem(item)
            self.status.setText(f"{len(self._items)} archivo(s)")
            if not self._items:
                self.details.setPlainText("No hay resultados.")
                self.preview_image.setText("No hay archivos para mostrar")
            else:
                self.file_list.setCurrentRow(0)
            return
        if isinstance(result, dict):
            self.status.setText(result.get("name") or op)
            self.details.setPlainText(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            self.status.setText(op)
            self.details.setPlainText(str(result))
        if op in {"uploaded", "renamed", "deleted"}:
            QTimer.singleShot(500, self.load_recent)


class MusicModePanelV2(QWidget):
    _thumb_sig = pyqtSignal(object, object)
    _result_sig = pyqtSignal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._table_kind = "playlists"
        self._current_playlist: dict | None = None
        self._search_results: dict[str, list[dict]] = {"songs": [], "playlists": [], "artists": []}
        self._thumb_cache: dict[str, bytes] = {}
        self._thumb_loading: set[str] = set()
        self._thumb_rows: dict[str, set[int]] = {}
        self._thumb_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="music-art")
        self._thumb_executor_closed = False
        self._header_data: dict = {}
        self._now_playing_data: dict = {}
        self._now_playing_key: tuple[str, str] = ("", "")
        self._detail_request = 0
        self._details_loading_key: tuple[str, str, str] = ("", "", "")
        self._detail_render_fingerprint: tuple = ()
        self._detail_render_track_key: tuple[str, str] = ("", "")
        self._details_visible_once = False
        self._artist_page_data: dict = {}
        self._artist_image_targets: dict[str, list[tuple[object, int]]] = {}
        self._artist_page_open = False
        self._table_revision = 0
        self._playing_mark_revision = -1
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_thumb_executor)

        self.setStyleSheet(self._panel_style())
        self._thumb_sig.connect(self._apply_thumb)
        self._result_sig.connect(self._handle_result)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self.query_input = QLineEdit()
        self.query_input.setObjectName("MusicSearch")
        self.query_input.setPlaceholderText("Buscar playlists, canciones o artistas")
        self.query_input.returnPressed.connect(self.search)
        search_row.addWidget(self.query_input, stretch=1)
        self.search_btn = QPushButton("Buscar")
        self.search_btn.setObjectName("MusicSearchButton")
        self.search_btn.setIcon(_line_icon("search", C.TEXT_DIM, 17))
        self.search_btn.setIconSize(QSize(17, 17))
        self.search_btn.clicked.connect(self.search)
        search_row.addWidget(self.search_btn)
        root.addLayout(search_row)

        self.filter_row = QWidget()
        filter_layout = QHBoxLayout(self.filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(8)
        self._filter_buttons: dict[str, QPushButton] = {}
        for key, label in (("songs", "Canciones"), ("playlists", "Playlists"), ("artists", "Artistas")):
            btn = QPushButton(label)
            btn.setObjectName("MusicFilterButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked=False, k=key: self._show_search_filter(k))
            self._filter_buttons[key] = btn
            filter_layout.addWidget(btn)
        filter_layout.addStretch()
        self.filter_row.setVisible(False)
        root.addWidget(self.filter_row)

        body = QHBoxLayout()
        body.setSpacing(10)
        root.addLayout(body, stretch=1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        body.addWidget(left, stretch=7)

        self.music_content_stack = QStackedWidget()
        left_layout.addWidget(self.music_content_stack)
        self.browse_page = QWidget()
        browse_layout = QVBoxLayout(self.browse_page)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        browse_layout.setSpacing(0)
        self.music_content_stack.addWidget(self.browse_page)

        self.header = QFrame()
        self.header.setObjectName("MusicHeader")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(16)
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(128, 128)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setObjectName("MusicCover")
        header_layout.addWidget(self.cover_label)

        header_text = QVBoxLayout()
        header_text.setSpacing(8)
        header_text.addStretch()
        self.type_label = QLabel("Playlists")
        self.type_label.setObjectName("MusicType")
        self.title_label = QLabel("Playlists")
        self.title_label.setObjectName("MusicTitle")
        self.title_label.setWordWrap(True)
        self.meta_label = QLabel("Tu biblioteca de YouTube Music")
        self.meta_label.setObjectName("MusicMeta")
        self.meta_label.setWordWrap(True)
        header_text.addWidget(self.type_label)
        header_text.addWidget(self.title_label)
        header_text.addWidget(self.meta_label)
        header_actions = QHBoxLayout()
        header_actions.setSpacing(8)
        self.shuffle_btn = QPushButton("Aleatorio")
        self.shuffle_btn.setObjectName("MusicHeaderAction")
        self.shuffle_btn.setIcon(_line_icon("shuffle", "#DFF5FF", 17))
        self.shuffle_btn.setIconSize(QSize(17, 17))
        self.shuffle_btn.setToolTip("Reproducir esta playlist en orden aleatorio")
        self.shuffle_btn.clicked.connect(self._play_current_playlist_shuffled)
        self.shuffle_btn.setVisible(False)
        header_actions.addWidget(self.shuffle_btn)
        header_actions.addStretch()

        header_text.addLayout(header_actions)
        header_text.addStretch()
        header_layout.addLayout(header_text, stretch=1)

        # "⋯" settings button — top-right corner of the header banner
        corner_col = QVBoxLayout()
        corner_col.setContentsMargins(0, 0, 0, 0)
        corner_col.setSpacing(0)
        self._hdr_menu_btn = QPushButton("⋯")
        self._hdr_menu_btn.setObjectName("MusicHeaderMenuBtn")
        self._hdr_menu_btn.setToolTip("Opciones")
        self._hdr_menu_btn.setFixedSize(32, 32)
        self._hdr_menu_btn.clicked.connect(self._show_header_menu)
        corner_col.addWidget(self._hdr_menu_btn, alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        corner_col.addStretch()
        header_layout.addLayout(corner_col)

        # Crossfade state (no longer a visible widget — controlled via ⋯ menu)
        self._cf_enabled: bool = False
        self._cf_secs: int = 3

        browse_layout.addWidget(self.header)

        self.table = QTableWidget()
        self.table.setObjectName("MusicTable")
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setIconSize(QSize(44, 44))
        self.table.cellClicked.connect(self._select_row)
        self.table.cellDoubleClicked.connect(self._activate_row)
        self.table.cellActivated.connect(self._activate_row)
        self.table.verticalScrollBar().valueChanged.connect(self._prefetch_visible_thumbnails)
        browse_layout.addWidget(self.table, stretch=1)

        self.status = QLabel("Listo")
        self.status.setObjectName("MusicStatus")
        self.status.setVisible(False)
        browse_layout.addWidget(self.status)

        self.artist_page = self._build_artist_page()
        self.music_content_stack.addWidget(self.artist_page)

        self.details_panel = QFrame()
        self.details_panel.setObjectName("NowPlayingPanel")
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(14, 14, 14, 14)
        details_layout.setSpacing(10)
        self.details_heading = QLabel("REPRODUCIENDO")
        self.details_heading.setObjectName("NowPlayingHeading")
        details_layout.addWidget(self.details_heading)
        self.detail_cover = QLabel()
        self.detail_cover.setObjectName("NowPlayingCover")
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setMinimumHeight(260)
        self.detail_cover.setMaximumHeight(360)
        self.detail_cover.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.details_scroll = QScrollArea()
        self.details_scroll.setObjectName("NowPlayingScroll")
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_content = QWidget()
        self.details_content.setObjectName("NowPlayingContent")
        details_content_layout = QVBoxLayout(self.details_content)
        details_content_layout.setContentsMargins(0, 0, 0, 0)
        details_content_layout.setSpacing(12)
        details_content_layout.addWidget(self.detail_cover)
        self.details = QLabel()
        self.details.setOpenExternalLinks(False)
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.details.setObjectName("NowPlayingDetails")
        self.details.linkActivated.connect(self._on_details_link)
        details_content_layout.addWidget(self.details)
        details_content_layout.addStretch()
        self.details_scroll.setWidget(self.details_content)
        details_layout.addWidget(self.details_scroll, stretch=1)
        self.details_panel.setMinimumWidth(310)
        self.details_panel.setVisible(False)
        body.addWidget(self.details_panel, stretch=3)

        self._set_header("Playlists", "Tu biblioteca de YouTube Music", "Playlists", {})
        QTimer.singleShot(500, lambda: self._send_playback("warmup", {}))
        QTimer.singleShot(200, self.load_playlists)

    def _shutdown_thumb_executor(self):
        if self._thumb_executor_closed:
            return
        self._thumb_executor_closed = True
        self._thumb_executor.shutdown(wait=False, cancel_futures=True)

    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
                letter-spacing: 0;
            }}
            QLineEdit#MusicSearch {{
                min-height: 42px;
                background: rgba(8, 14, 25, 0.88);
                color: {C.TEXT};
                border: 1px solid rgba(125, 211, 252, 0.12);
                border-radius: 10px;
                padding: 0 14px;
                font-size: 13px;
                selection-background-color: {C.PRI};
                selection-color: #06121a;
            }}
            QLineEdit#MusicSearch:focus {{
                background: rgba(14, 15, 18, 0.94);
                border-color: rgba(125, 211, 252, 0.58);
            }}
            QPushButton#MusicSearchButton {{
                min-height: 40px;
                background: rgba(56, 189, 248, 0.16);
                color: #DFF5FF;
                border: 1px solid rgba(125, 211, 252, 0.28);
                border-radius: 10px;
                padding: 0 16px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton#MusicSearchButton:hover {{
                background: rgba(56, 189, 248, 0.24);
                border-color: rgba(125, 211, 252, 0.48);
            }}
            QPushButton#MusicFilterButton {{
                min-height: 32px;
                background: rgba(8, 14, 25, 0.74);
                color: rgba(255, 255, 255, 0.72);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 0 15px;
                font-size: 12px;
                font-weight: 800;
            }}
            QPushButton#MusicFilterButton:hover {{
                background: rgba(255, 255, 255, 0.10);
                color: {C.TEXT};
            }}
            QPushButton#MusicFilterButton:checked {{
                background: rgba(56, 189, 248, 0.16);
                color: #DFF5FF;
                border-color: rgba(125, 211, 252, 0.32);
            }}
            QFrame#MusicHeader {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(15, 63, 94, 0.96),
                    stop:0.48 rgba(11, 36, 59, 0.94),
                    stop:1 rgba(7, 17, 31, 0.96));
                border: 1px solid rgba(125, 211, 252, 0.14);
                border-top-left-radius: 12px;
                border-top-right-radius: 12px;
            }}
            QLabel#MusicCover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #145274,
                    stop:1 #7DD3FC);
                color: white;
                border-radius: 10px;
                font-size: 50px;
                font-weight: 900;
            }}
            QLabel#MusicType {{
                color: rgba(255, 255, 255, 0.88);
                font-size: 12px;
                font-weight: 900;
                background: transparent;
            }}
            QLabel#MusicTitle {{
                color: white;
                font-size: 34px;
                font-weight: 900;
                background: transparent;
            }}
            QLabel#MusicMeta {{
                color: rgba(255, 255, 255, 0.76);
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }}
            QPushButton#MusicHeaderAction {{
                min-height: 32px;
                background: rgba(56, 189, 248, 0.16);
                color: #DFF5FF;
                border: 1px solid rgba(125, 211, 252, 0.28);
                border-radius: 8px;
                padding: 0 13px;
                font-size: 11px;
                font-weight: 800;
            }}
            QPushButton#MusicHeaderAction:hover {{
                background: rgba(56, 189, 248, 0.24);
                border-color: rgba(125, 211, 252, 0.46);
            }}
            QPushButton#MusicHeaderMenuBtn {{
                background: transparent;
                color: rgba(180, 210, 240, 0.55);
                border: none;
                border-radius: 8px;
                font-size: 18px;
                font-weight: 900;
                padding: 0;
            }}
            QPushButton#MusicHeaderMenuBtn:hover {{
                background: rgba(56, 189, 248, 0.14);
                color: #DFF5FF;
            }}
            QPushButton#MusicHeaderMenuBtn:pressed {{
                background: rgba(56, 189, 248, 0.22);
            }}
            QTableWidget#MusicTable {{
                background: rgba(5, 11, 20, 0.90);
                color: #f2f2f2;
                border: 1px solid rgba(255, 255, 255, 0.075);
                border-top: none;
                border-bottom-left-radius: 12px;
                border-bottom-right-radius: 12px;
                outline: none;
                padding: 8px 10px 10px 10px;
            }}
            QTableWidget#MusicTable::item {{
                border: none;
                padding: 7px 8px;
                color: #e8e8e8;
            }}
            QTableWidget#MusicTable::item:selected {{
                background: rgba(255, 255, 255, 0.13);
                color: white;
                border: none;
            }}
            QHeaderView::section {{
                background: rgba(7, 14, 24, 0.96);
                color: rgba(255, 255, 255, 0.62);
                border: none;
                border-bottom: 1px solid rgba(255, 255, 255, 0.10);
                padding: 10px 8px;
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#MusicStatus {{
                color: rgba(255, 255, 255, 0.58);
                background: transparent;
                padding: 8px 4px 0 4px;
                font-size: 12px;
            }}
            QFrame#NowPlayingPanel {{
                background: rgba(8, 14, 25, 0.90);
                border: 1px solid rgba(125, 211, 252, 0.12);
                border-radius: 12px;
            }}
            QLabel#NowPlayingHeading {{
                color: #8aa0b4;
                background: transparent;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: 1.4px;
                padding-bottom: 2px;
            }}
            QLabel#NowPlayingCover {{
                background: rgba(255, 255, 255, 0.040);
                border: 1px solid rgba(255, 255, 255, 0.075);
                border-radius: 12px;
                color: rgba(255, 255, 255, 0.46);
                font-size: 42px;
                font-weight: 900;
            }}
            QScrollArea#NowPlayingScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#NowPlayingContent {{
                background: transparent;
            }}
            QLabel#NowPlayingDetails {{
                background: transparent;
                color: #f8fafc;
                border: none;
                padding: 0;
                font-size: 13px;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 10px;
                margin: 8px 2px 8px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: rgba(255,255,255,0.22);
                min-height: 42px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: rgba(255,255,255,0.34);
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
                border: none;
                height: 0;
            }}
            QScrollBar:horizontal {{
                background: transparent;
                height: 10px;
                margin: 2px 8px 2px 8px;
            }}
            QScrollBar::handle:horizontal {{
                background: rgba(255,255,255,0.22);
                min-width: 42px;
                border-radius: 5px;
            }}
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {{
                background: transparent;
                border: none;
                width: 0;
            }}
            QScrollArea#ArtistPageScroll {{
                background: rgba(5, 11, 20, 0.90);
                border: 1px solid rgba(125, 211, 252, 0.10);
                border-radius: 12px;
            }}
            QWidget#ArtistPageContent {{
                background: transparent;
            }}
            QFrame#ArtistHero {{
                background: rgba(15, 22, 34, 0.94);
                border: 1px solid rgba(125, 211, 252, 0.16);
                border-radius: 12px;
            }}
            QLabel#ArtistHeroImage {{
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }}
            QLabel#ArtistPageName {{
                color: white;
                font-size: 38px;
                font-weight: 900;
            }}
            QLabel#ArtistPageStats {{
                color: #7dd3fc;
                font-size: 13px;
                font-weight: 800;
            }}
            QLabel#ArtistPageDescription {{
                color: rgba(255, 255, 255, 0.72);
                font-size: 13px;
                line-height: 1.35;
            }}
            QLabel#ArtistSectionTitle {{
                color: white;
                font-size: 20px;
                font-weight: 900;
                padding-top: 8px;
            }}
            QPushButton#ArtistBackButton {{
                background: rgba(255, 255, 255, 0.07);
                color: #dbeafe;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                padding: 7px 14px;
                font-weight: 800;
            }}
            QPushButton#ArtistBackButton:hover {{
                background: rgba(125, 211, 252, 0.15);
                border-color: rgba(125, 211, 252, 0.45);
            }}
            QListWidget#ArtistCardList {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QListWidget#ArtistCardList::item {{
                color: #f8fafc;
                border-radius: 8px;
                padding: 6px;
            }}
            QListWidget#ArtistCardList::item:hover {{
                background: rgba(255, 255, 255, 0.08);
            }}
            QTableWidget#ArtistTrackTable {{
                background: rgba(8, 10, 15, 0.70);
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 10px;
                outline: none;
            }}
            QTableWidget#ArtistTrackTable::item {{
                border: none;
                padding: 6px 8px;
            }}
            QTableWidget#ArtistTrackTable::item:selected {{
                background: rgba(125, 211, 252, 0.14);
            }}
        """

    def _build_artist_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("ArtistPageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        content.setObjectName("ArtistPageContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 18, 20, 24)
        layout.setSpacing(14)

        nav = QHBoxLayout()
        self.artist_back_btn = QPushButton("Volver")
        self.artist_back_btn.setObjectName("ArtistBackButton")
        self.artist_back_btn.setIcon(_line_icon("chevron_left", C.TEXT_DIM, 17))
        self.artist_back_btn.setIconSize(QSize(17, 17))
        self.artist_back_btn.clicked.connect(self._show_browse_content)
        nav.addWidget(self.artist_back_btn)
        nav.addStretch()
        layout.addLayout(nav)

        hero = QFrame()
        hero.setObjectName("ArtistHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(18, 18, 18, 18)
        hero_layout.setSpacing(20)
        self.artist_hero_image = QLabel()
        self.artist_hero_image.setObjectName("ArtistHeroImage")
        self.artist_hero_image.setFixedSize(210, 210)
        self.artist_hero_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.artist_hero_image.setText("♪")
        hero_layout.addWidget(self.artist_hero_image)

        hero_text = QVBoxLayout()
        hero_text.setSpacing(8)
        self.artist_page_name = QLabel("Artista")
        self.artist_page_name.setObjectName("ArtistPageName")
        self.artist_page_name.setWordWrap(True)
        self.artist_page_stats = QLabel("")
        self.artist_page_stats.setObjectName("ArtistPageStats")
        self.artist_page_stats.setWordWrap(True)
        self.artist_page_description = QLabel("")
        self.artist_page_description.setObjectName("ArtistPageDescription")
        self.artist_page_description.setWordWrap(True)
        self.artist_page_description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hero_text.addStretch()
        hero_text.addWidget(self.artist_page_name)
        hero_text.addWidget(self.artist_page_stats)
        hero_text.addWidget(self.artist_page_description)
        hero_text.addStretch()
        hero_layout.addLayout(hero_text, stretch=1)
        layout.addWidget(hero)

        self.artist_popular_table = self._make_artist_track_table()
        self.artist_popular_table.cellDoubleClicked.connect(
            lambda row, _col: self._play_artist_page_track("top_songs", row)
        )
        layout.addWidget(self._artist_section_label("Mas escuchadas"))
        layout.addWidget(self.artist_popular_table)

        self.artist_recommended_table = self._make_artist_track_table()
        self.artist_recommended_table.cellDoubleClicked.connect(
            lambda row, _col: self._play_artist_page_track("recommendations", row)
        )
        self.artist_recommended_title = self._artist_section_label("Canciones recomendadas")
        layout.addWidget(self.artist_recommended_title)
        layout.addWidget(self.artist_recommended_table)

        self.artist_albums_list = self._make_artist_card_list()
        self.artist_albums_list.itemClicked.connect(self._open_artist_album_item)
        self.artist_albums_title = self._artist_section_label("Albumes")
        layout.addWidget(self.artist_albums_title)
        layout.addWidget(self.artist_albums_list)

        self.artist_singles_list = self._make_artist_card_list()
        self.artist_singles_list.itemClicked.connect(self._open_artist_album_item)
        self.artist_singles_title = self._artist_section_label("Singles y EPs")
        layout.addWidget(self.artist_singles_title)
        layout.addWidget(self.artist_singles_list)

        self.artist_videos_list = self._make_artist_card_list()
        self.artist_videos_list.itemClicked.connect(self._play_artist_video_item)
        self.artist_videos_title = self._artist_section_label("Videos")
        layout.addWidget(self.artist_videos_title)
        layout.addWidget(self.artist_videos_list)

        self.artist_related_list = self._make_artist_card_list()
        self.artist_related_list.itemClicked.connect(self._open_related_artist_item)
        self.artist_related_title = self._artist_section_label("Artistas relacionados")
        layout.addWidget(self.artist_related_title)
        layout.addWidget(self.artist_related_list)

        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _artist_section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ArtistSectionTitle")
        return label

    def _make_artist_track_table(self) -> QTableWidget:
        table = QTableWidget()
        table.setObjectName("ArtistTrackTable")
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["#", "Titulo", "Album", "Duracion"])
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setIconSize(QSize(42, 42))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 42)
        table.setColumnWidth(2, 220)
        table.setColumnWidth(3, 82)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return table

    def _make_artist_card_list(self) -> QListWidget:
        widget = QListWidget()
        widget.setObjectName("ArtistCardList")
        widget.setViewMode(QListView.ViewMode.IconMode)
        widget.setFlow(QListView.Flow.LeftToRight)
        widget.setWrapping(False)
        widget.setResizeMode(QListView.ResizeMode.Adjust)
        widget.setMovement(QListView.Movement.Static)
        widget.setIconSize(QSize(142, 142))
        widget.setGridSize(QSize(166, 205))
        widget.setSpacing(4)
        widget.setFixedHeight(218)
        widget.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return widget

    def _show_browse_content(self):
        self._artist_page_open = False
        self.music_content_stack.setCurrentWidget(self.browse_page)

    def _show_artist_loading(self, name: str, data: dict):
        self._artist_page_open = True
        self._artist_page_data = dict(data or {})
        self.artist_page_name.setText(name or "Artista")
        self.artist_page_stats.setText("Cargando página del artista...")
        self.artist_page_description.setText("")
        self.artist_hero_image.setPixmap(QPixmap())
        self.artist_hero_image.setText("♪")
        for table in (self.artist_popular_table, self.artist_recommended_table):
            table.setRowCount(0)
            table.setFixedHeight(56)
        for widget in (
            self.artist_albums_list, self.artist_singles_list,
            self.artist_videos_list, self.artist_related_list,
        ):
            widget.clear()
        self.music_content_stack.setCurrentWidget(self.artist_page)

    def _populate_artist_track_table(self, table: QTableWidget, tracks: list[dict]):
        table.setRowCount(0)
        for row, raw in enumerate(tracks):
            data = dict(raw)
            table.insertRow(row)
            table.setRowHeight(row, 54)
            number = QTableWidgetItem(str(row + 1))
            number.setData(Qt.ItemDataRole.UserRole, data)
            number.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 0, number)
            title = self._safe_text(data.get("title"))
            artists = self._safe_text(data.get("artists"))
            title_item = QTableWidgetItem(f"{title}\n{artists}" if artists else title)
            title_item.setData(Qt.ItemDataRole.UserRole, data)
            table.setItem(row, 1, title_item)
            album_item = QTableWidgetItem(self._safe_text(data.get("album")))
            album_item.setData(Qt.ItemDataRole.UserRole, data)
            table.setItem(row, 2, album_item)
            duration_item = QTableWidgetItem(self._safe_text(data.get("duration")))
            duration_item.setData(Qt.ItemDataRole.UserRole, data)
            duration_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItem(row, 3, duration_item)
            url = self._safe_text(data.get("thumbnail"))
            if url:
                self._queue_artist_image(url, title_item, 42)
        table.setFixedHeight(38 + max(1, len(tracks)) * 54)

    def _populate_artist_cards(self, widget: QListWidget, items: list[dict], subtitle_key: str):
        widget.clear()
        for raw in items:
            data = dict(raw)
            title = self._safe_text(data.get("title") or data.get("name"))
            subtitle = self._safe_text(data.get(subtitle_key))
            item = QListWidgetItem(f"{title}\n{subtitle}" if subtitle else title)
            item.setData(Qt.ItemDataRole.UserRole, data)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            item.setToolTip(title)
            widget.addItem(item)
            url = self._safe_text(data.get("thumbnail"))
            if url:
                self._queue_artist_image(url, item, 142)

    def _queue_artist_image(self, url: str, target, size: int):
        url = str(url or "").strip()
        if not url:
            return
        self._artist_image_targets.setdefault(url, []).append((target, int(size)))
        cached = self._thumb_cache.get(url)
        if cached:
            self._apply_thumb(url, cached)
        else:
            self._ensure_thumb_async({"thumbnail": url})

    def _apply_artist_target_image(self, target, raw: bytes, size: int):
        pix = QPixmap()
        if not pix.loadFromData(raw) or pix.isNull():
            return
        scaled = pix.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if isinstance(target, QLabel):
            target.setText("")
            target.setPixmap(scaled)
        elif isinstance(target, QListWidgetItem):
            target.setIcon(QIcon(scaled))
        elif isinstance(target, QTableWidgetItem):
            target.setIcon(QIcon(scaled))

    def _render_artist_page(self, data: dict):
        self._artist_page_open = True
        self._artist_page_data = dict(data or {})
        self._artist_image_targets.clear()
        name = self._safe_text(data.get("name")) or "Artista"
        stats = [
            self._safe_text(data.get("monthlyListeners")),
            self._safe_text(data.get("subscribers")),
            self._safe_text(data.get("views")),
        ]
        stats = [value for value in stats if value]
        self.artist_page_name.setText(name)
        self.artist_page_stats.setText("  ·  ".join(stats))
        self.artist_page_description.setText(self._safe_text(data.get("description")))
        self.artist_hero_image.setPixmap(QPixmap())
        self.artist_hero_image.setText("♪")
        hero_url = self._safe_text(data.get("thumbnail"))
        if hero_url:
            self._queue_artist_image(hero_url, self.artist_hero_image, 210)

        popular = list(data.get("top_songs") or [])
        recommended = list(data.get("recommendations") or [])
        albums = list(data.get("albums") or [])
        singles = list(data.get("singles") or [])
        videos = list(data.get("videos") or [])
        related = list(data.get("related") or [])
        self._populate_artist_track_table(self.artist_popular_table, popular)
        self._populate_artist_track_table(self.artist_recommended_table, recommended)
        self.artist_recommended_title.setVisible(bool(recommended))
        self.artist_recommended_table.setVisible(bool(recommended))
        self._populate_artist_cards(self.artist_albums_list, albums, "year")
        self._populate_artist_cards(self.artist_singles_list, singles, "year")
        self._populate_artist_cards(self.artist_videos_list, videos, "views")
        self._populate_artist_cards(self.artist_related_list, related, "subscribers")
        for title, widget, values in (
            (self.artist_albums_title, self.artist_albums_list, albums),
            (self.artist_singles_title, self.artist_singles_list, singles),
            (self.artist_videos_title, self.artist_videos_list, videos),
            (self.artist_related_title, self.artist_related_list, related),
        ):
            title.setVisible(bool(values))
            widget.setVisible(bool(values))
        self.music_content_stack.setCurrentWidget(self.artist_page)
        self.artist_page.verticalScrollBar().setValue(0)

    def _play_artist_page_track(self, key: str, row: int):
        tracks = list(self._artist_page_data.get(key) or [])
        if not (0 <= row < len(tracks)):
            return
        playable = self._audio_tracks_from_data(tracks)
        if playable:
            self._send_playback("play_tracks", {
                "tracks": playable,
                "start_index": row,
                "shuffle": False,
            })

    def _audio_tracks_from_data(self, tracks: list[dict]) -> list[dict]:
        return [
            {
                "videoId": item.get("videoId", ""),
                "title": item.get("title", ""),
                "artists": item.get("artists", ""),
            }
            for item in tracks
            if item.get("videoId")
        ]

    def _open_artist_album_item(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self._open_album_page(data)

    def _play_artist_video_item(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and data.get("videoId"):
            self._send_playback("play_track", data)

    def _open_related_artist_item(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            self._open_artist_page(data)

    def _run(self, op: str, fn):
        self.status.setVisible(True)
        self.status.setText("Cargando...")

        def worker():
            try:
                result = fn()
            except Exception as exc:
                result = exc
            try:
                self._result_sig.emit(op, result)
            except RuntimeError:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _safe_text(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            return str(value.get("name") or value.get("title") or value.get("text") or value.get("id") or "")
        if isinstance(value, list):
            parts = [self._safe_text(item) for item in value]
            return ", ".join(part for part in parts if part)
        return str(value)

    def _esc(self, value) -> str:
        return html_lib.escape(self._safe_text(value))

    def _playlist_title(self, data: dict) -> str:
        title = self._safe_text(data.get("title") or data.get("name") or "")
        if (data.get("playlistId") or data.get("browseId")) == "LM" or title.lower() in {"liked music", "liked songs"}:
            return "Canciones que te gustan"
        return title or "Playlist"

    def _playlist_meta(self, data: dict, track_count: int | None = None) -> str:
        author = self._safe_text(data.get("author") or "YouTube Music")
        count = track_count if track_count is not None else data.get("itemCount") or data.get("trackCount") or ""
        if count:
            return f"{author} - {count} canciones"
        return author

    def _format_date(self, value) -> str:
        text = self._safe_text(value).strip()
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
        return text

    def _set_header(self, title: str, subtitle: str = "", kind: str = "", data: dict | None = None):
        self._header_data = dict(data or {})
        self.type_label.setText(kind or "Music")
        self.title_label.setText(title)
        self.meta_label.setText(subtitle)
        self._set_cover(self._header_data)
        self._ensure_thumb_async(self._header_data)

    def _set_cover(self, data: dict):
        pix = self._thumb_pixmap(data, 128)
        if pix is not None:
            self.cover_label.setText("")
            self.cover_label.setPixmap(pix)
        else:
            pid = data.get("playlistId") or data.get("browseId") or ""
            liked = pid == "LM" or "liked" in self._safe_text(data.get("title")).lower()
            icon_name = "heart" if liked else "playlist" if pid or self._table_kind == "playlists" else "music"
            self.cover_label.setPixmap(QPixmap())
            self.cover_label.setText("")
            self.cover_label.setPixmap(_line_icon(icon_name, "#F8FAFC", 58).pixmap(58, 58))

    def _thumb_pixmap(self, data: dict, size: int = 44) -> QPixmap | None:
        raw = data.get("thumb_b64") or ""
        if not raw:
            return None
        try:
            pix = QPixmap()
            pix.loadFromData(base64.b64decode(raw))
            if not pix.isNull():
                scaled = pix.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = max(0, (scaled.width() - size) // 2)
                y = max(0, (scaled.height() - size) // 2)
                cropped = scaled.copy(x, y, size, size)
                result = QPixmap(size, size)
                result.fill(Qt.GlobalColor.transparent)
                painter = QPainter(result)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                clip = QPainterPath()
                clip.addRoundedRect(QRectF(0, 0, size, size), 4, 4)
                painter.setClipPath(clip)
                painter.drawPixmap(0, 0, cropped)
                painter.end()
                return result
        except Exception:
            pass
        return None

    def _playlist_cover_icon(self, liked: bool, size: int = 44) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        background = QLinearGradient(QPointF(0, 0), QPointF(size, size))
        if liked:
            background.setColorAt(0.0, QColor("#245A86"))
            background.setColorAt(1.0, QColor("#6FC7EA"))
        else:
            background.setColorAt(0.0, QColor("#172A3D"))
            background.setColorAt(1.0, QColor("#244B69"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(QRectF(0, 0, size, size), 5, 5)

        icon_size = max(20, int(size * 0.52))
        icon = _line_icon(
            "heart" if liked else "playlist",
            "#F8FAFC",
            icon_size,
        ).pixmap(icon_size, icon_size)
        offset = (size - icon_size) // 2
        painter.drawPixmap(offset, offset, icon)
        painter.end()
        return QIcon(pix)

    def _mime_from_raw(self, raw: bytes) -> str:
        if raw.startswith(b"\xff\xd8"):
            return "image/jpeg"
        if raw.startswith(b"\x89PNG"):
            return "image/png"
        if raw.startswith(b"GIF"):
            return "image/gif"
        if raw.startswith(b"RIFF") and b"WEBP" in raw[:16]:
            return "image/webp"
        return "image/jpeg"

    def _data_uri(self, raw: bytes) -> str:
        if not raw:
            return ""
        return f"data:{self._mime_from_raw(raw)};base64,{base64.b64encode(raw).decode('ascii')}"

    def _image_html(self, src: str, width: int, margin: str = "0 0 14px 0") -> str:
        if not src:
            return ""
        return (
            f'<img src="{html_lib.escape(src, quote=True)}" width="{width}" '
            f'style="display:block; margin:{margin};">'
        )

    def _details_image_width(self) -> int:
        try:
            width = self.details_scroll.viewport().width()
        except Exception:
            width = 320
        return max(260, min(560, int(width or 320) - 6))

    def _image_src(self, data: dict, b64_key: str, src_key: str, url_key: str, src_url_key: str = "") -> str:
        url = str(data.get(url_key) or "")
        src_url = str(data.get(src_url_key) or "") if src_url_key else ""
        if data.get(src_key) and (not src_url or not url or src_url == url):
            return str(data.get(src_key))
        if data.get(b64_key):
            return f"data:image/jpeg;base64,{data.get(b64_key)}"
        if url:
            return url
        return ""

    def _raw_from_image_data(self, data: dict, b64_key: str, src_key: str, url_key: str, src_url_key: str = "") -> bytes:
        url = str(data.get(url_key) or "")
        src_url = str(data.get(src_url_key) or "") if src_url_key else ""
        src = str(data.get(src_key) or "")
        if src.startswith("data:") and (not src_url or not url or src_url == url):
            try:
                return base64.b64decode(src.split(",", 1)[1])
            except Exception:
                return b""
        if data.get(b64_key) and (not src_url or not url or src_url == url):
            try:
                return base64.b64decode(str(data.get(b64_key)))
            except Exception:
                return b""
        if url and url in self._thumb_cache:
            return self._thumb_cache[url]
        return b""

    def _rounded_pixmap(self, src: QPixmap, radius: int = 10) -> QPixmap:
        if src.isNull():
            return src
        out = QPixmap(src.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, src.width(), src.height()), radius, radius)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, src)
        painter.end()
        return out

    def _set_detail_cover(self, data: dict):
        raw = self._raw_from_image_data(data, "thumb_b64", "thumb_src", "thumbnail", "thumb_src_url")
        if not raw:
            self.detail_cover.setPixmap(QPixmap())
            self.detail_cover.setText("♪")
            self._ensure_thumb_async(data)
            return
        pix = QPixmap()
        pix.loadFromData(raw)
        if pix.isNull():
            self.detail_cover.setPixmap(QPixmap())
            self.detail_cover.setText("♪")
            return
        max_w = max(260, min(520, self.detail_cover.width() or 320))
        max_h = max(260, min(360, self.detail_cover.maximumHeight() or 340))
        scaled = pix.scaled(
            max_w,
            max_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.detail_cover.setText("")
        self.detail_cover.setPixmap(self._rounded_pixmap(scaled, 10))

    def _fetch_thumb_b64(self, url: str) -> str:
        url = str(url or "").strip()
        if not url:
            return ""
        cached = self._thumb_cache.get(url)
        if cached is not None:
            return base64.b64encode(cached).decode("ascii")
        try:
            resp = requests.get(url, timeout=8)
            resp.raise_for_status()
            self._thumb_cache[url] = resp.content
            return base64.b64encode(resp.content).decode("ascii")
        except Exception:
            return ""

    def _ensure_thumb_async(self, data: dict):
        if not data:
            return
        url = str(data.get("thumbnail") or data.get("cover") or data.get("artistThumbnail") or "").strip()
        src_url = str(data.get("thumb_src_url") or "")
        if data.get("thumb_b64") and (not url or not src_url or src_url == url):
            return
        if not url:
            return
        cached = self._thumb_cache.get(url)
        if cached is not None:
            self._apply_thumb(url, cached)
            return
        if url in self._thumb_loading:
            return
        if self._thumb_executor_closed:
            return
        self._thumb_loading.add(url)

        def worker():
            raw = b""
            try:
                resp = requests.get(url, timeout=6)
                resp.raise_for_status()
                raw = resp.content
            except Exception:
                raw = b""
            try:
                self._thumb_sig.emit(url, raw)
            except RuntimeError:
                pass

        try:
            self._thumb_executor.submit(worker)
        except RuntimeError:
            self._thumb_loading.discard(url)

    def _apply_thumb(self, url, raw):
        url = str(url or "")
        self._thumb_loading.discard(url)
        if not url or not raw:
            return
        self._thumb_cache[url] = bytes(raw)
        encoded = base64.b64encode(bytes(raw)).decode("ascii")

        header_url = str(self._header_data.get("thumbnail") or self._header_data.get("cover") or "")
        if header_url == url:
            self._header_data["thumb_b64"] = encoded
            self._set_cover(self._header_data)

        for row in list(self._thumb_rows.get(url, set())):
            data = self._row_data(row)
            if str(data.get("thumbnail") or data.get("cover") or data.get("artistThumbnail") or "") != url:
                continue
            data["thumb_b64"] = encoded
            data["thumb_src"] = self._data_uri(bytes(raw))
            data["thumb_src_url"] = url
            self._set_row_data(row, data)
            self._set_row_icon(row, data)

        now_url = str(self._now_playing_data.get("thumbnail") or self._now_playing_data.get("cover") or "")
        artist_url = str(self._now_playing_data.get("artistThumbnail") or "")
        if now_url == url:
            self._now_playing_data["thumb_b64"] = encoded
            self._now_playing_data["thumb_src"] = self._data_uri(bytes(raw))
            self._now_playing_data["thumb_src_url"] = url
            self._render_now_playing()
        elif artist_url == url:
            self._now_playing_data["artist_thumb_b64"] = encoded
            self._now_playing_data["artist_thumb_src"] = self._data_uri(bytes(raw))
            self._now_playing_data["artist_thumb_src_url"] = url
            self._render_now_playing()

        targets = self._artist_image_targets.pop(url, [])
        for target, size in targets:
            try:
                self._apply_artist_target_image(target, bytes(raw), size)
            except RuntimeError:
                pass

    def _prefetch_thumbnails(self, count: int | None = None):
        total = self.table.rowCount() if count is None else min(self.table.rowCount(), max(0, int(count)))
        for row in range(total):
            self._ensure_thumb_async(self._row_data(row))

    def _prefetch_visible_thumbnails(self, *_):
        viewport_h = self.table.viewport().height()
        for row in range(self.table.rowCount()):
            y = self.table.rowViewportPosition(row)
            h = self.table.rowHeight(row)
            if y + h < -240:
                continue
            if y > viewport_h + 420:
                continue
            self._ensure_thumb_async(self._row_data(row))

    def _configure_table(self, headers: list[str], widths: dict[int, int] | None = None, stretch: int = 1):
        self._table_revision += 1
        self._thumb_rows.clear()
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        header = self.table.horizontalHeader()
        for col in range(len(headers)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
        if 0 <= stretch < len(headers):
            header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
        for col, width in (widths or {}).items():
            self.table.setColumnWidth(col, width)

    def _item(self, text: str, data: dict, align=None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, data)
        if align is not None:
            item.setTextAlignment(align)
        return item

    def _set_row_data(self, row: int, data: dict):
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if item:
                item.setData(Qt.ItemDataRole.UserRole, data)

    def _row_data(self, row: int) -> dict:
        for col in range(self.table.columnCount()):
            item = self.table.item(row, col)
            if not item:
                continue
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict):
                return data
        return {}

    def _set_row_icon(self, row: int, data: dict):
        icon_col = 1 if self._table_kind in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks"
        } else 0
        item = self.table.item(row, icon_col)
        if not item:
            return
        if self._table_kind == "playlists":
            pid = data.get("playlistId") or data.get("browseId") or ""
            liked = pid == "LM" or "liked" in self._safe_text(data.get("title")).lower()
            if liked:
                item.setIcon(self._playlist_cover_icon(True))
                return
        pix = self._thumb_pixmap(data, 44)
        if pix is not None:
            item.setIcon(QIcon(pix))
            return
        url = str(data.get("thumbnail") or data.get("cover") or data.get("artistThumbnail") or "").strip()
        if url:
            self._thumb_rows.setdefault(url, set()).add(row)
        if self._table_kind == "playlists":
            item.setIcon(self._playlist_cover_icon(False))

    def _add_song_row(self, row: int, data: dict, index: int):
        self.table.insertRow(row)
        self.table.setRowHeight(row, 58)
        number = "▶" if data.get("_playing") else str(index + 1)
        self.table.setItem(row, 0, self._item(number, data, Qt.AlignmentFlag.AlignCenter))
        title = self._safe_text(data.get("title") or "(sin titulo)")
        artists = self._safe_text(data.get("artists"))
        title_text = f"{title}\n{artists}" if artists else title
        self.table.setItem(row, 1, self._item(title_text, data))
        self.table.setItem(row, 2, self._item(self._safe_text(data.get("album")), data))
        self.table.setItem(row, 3, self._item(self._safe_text(data.get("duration")), data, Qt.AlignmentFlag.AlignCenter))
        self._set_row_icon(row, data)

    def _show_songs(self, items: list[dict], table_kind: str = "songs", playlist: dict | None = None):
        self._show_browse_content()
        self._table_kind = table_kind
        self._items = []
        self._configure_table(
            ["#", "Titulo", "Album", "Duracion"],
            widths={0: 44, 2: 260, 3: 86},
            stretch=1,
        )
        for idx, raw in enumerate(items or []):
            data = dict(raw)
            data["_kind"] = "song"
            data["_index"] = idx
            self._items.append(data)
            self._add_song_row(idx, data, idx)
        if playlist:
            playlist = dict(playlist)
            playlist["itemCount"] = playlist.get("itemCount") or len(self._items)
            self._current_playlist = playlist
            self._set_header(self._playlist_title(playlist), self._playlist_meta(playlist, len(self._items)), "Lista", playlist)
        is_playlist = bool(playlist) and table_kind == "playlist_tracks" and bool(self._items)
        self.shuffle_btn.setVisible(is_playlist)
        self.status.setVisible(False)
        self._prefetch_thumbnails()
        self._prefetch_audio_streams(0, 4)
        self._restore_playing_selection()

    def _show_playlists(self, items: list[dict]):
        self._show_browse_content()
        self._table_kind = "playlists"
        self._current_playlist = None
        self._items = []
        self._configure_table(["Playlist", "Autor", "Canciones"], widths={1: 220, 2: 110}, stretch=0)
        for row, raw in enumerate(items or []):
            data = dict(raw)
            data["_kind"] = "playlist"
            self._items.append(data)
            self.table.insertRow(row)
            self.table.setRowHeight(row, 62)
            self.table.setItem(row, 0, self._item(self._playlist_title(data), data))
            self.table.setItem(row, 1, self._item(self._safe_text(data.get("author")), data))
            self.table.setItem(row, 2, self._item(self._safe_text(data.get("itemCount")), data, Qt.AlignmentFlag.AlignCenter))
            self._set_row_icon(row, data)
        self._set_header("Playlists", "Tu biblioteca de YouTube Music", "Playlists", {})
        self.shuffle_btn.setVisible(False)
        self.status.setVisible(False)
        self._prefetch_thumbnails()

    def _show_artists(self, items: list[dict]):
        self._show_browse_content()
        self._table_kind = "artists"
        self._current_playlist = None
        self._items = []
        self._configure_table(["Artista", "Info"], widths={1: 220}, stretch=0)
        for row, raw in enumerate(items or []):
            data = dict(raw)
            data["_kind"] = "artist"
            self._items.append(data)
            self.table.insertRow(row)
            self.table.setRowHeight(row, 62)
            self.table.setItem(row, 0, self._item(self._safe_text(data.get("name") or data.get("title")), data))
            self.table.setItem(row, 1, self._item(self._safe_text(data.get("subscribers") or data.get("description")), data))
            self._set_row_icon(row, data)
        self._set_header("Artistas", "Resultados de la busqueda", "Busqueda", {})
        self.shuffle_btn.setVisible(False)
        self.status.setVisible(False)
        self._prefetch_thumbnails()

    # ------------------------------------------------------------------
    # Header "⋯" settings menu
    # ------------------------------------------------------------------

    _MENU_STYLE = """
        QMenu {
            background: #0d1117;
            color: #e6f0f8;
            border: 1px solid rgba(125, 211, 252, 0.20);
            border-radius: 10px;
            padding: 5px 4px;
        }
        QMenu::item {
            padding: 7px 20px 7px 14px;
            border-radius: 6px;
            font-size: 12px;
        }
        QMenu::item:selected { background: rgba(56, 189, 248, 0.16); }
        QMenu::item:checked  { color: #7DD3FC; }
        QMenu::separator {
            height: 1px;
            background: rgba(125, 211, 252, 0.12);
            margin: 4px 10px;
        }
    """

    def _show_header_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._MENU_STYLE)

        in_playlist = self._table_kind == "playlist_tracks"
        in_playlists_list = self._table_kind == "playlists"

        if in_playlists_list:
            act_import = menu.addAction("Importar playlist...")
            act_import.triggered.connect(self._import_playlist_dialog)

        if in_playlist:
            act_export_cur = menu.addAction("Exportar esta lista...")
            act_export_cur.triggered.connect(self._do_export_current)
            act_export_liked = menu.addAction("Exportar Me Gusta...")
            act_export_liked.triggered.connect(self._do_export_liked)
            menu.addSeparator()

            cf_label = f"{'✓' if self._cf_enabled else '   '}  Crossfade  ({self._cf_secs} s)"
            act_cf = menu.addAction(cf_label)
            act_cf.setCheckable(True)
            act_cf.setChecked(self._cf_enabled)
            act_cf.triggered.connect(self._toggle_crossfade)

            act_cf_dur = menu.addAction("  Cambiar duración del crossfade...")
            act_cf_dur.triggered.connect(self._change_crossfade_duration)

        if not in_playlists_list and not in_playlist:
            menu.addAction("Sin opciones disponibles").setEnabled(False)

        btn = self._hdr_menu_btn
        menu.exec(btn.mapToGlobal(btn.rect().bottomRight()))

    def _toggle_crossfade(self, checked: bool):
        self._cf_enabled = checked
        self._send_playback("set_crossfade", {"seconds": self._cf_secs, "enabled": checked})

    def _change_crossfade_duration(self):
        val, ok = QInputDialog.getInt(
            self, "Duración del crossfade",
            "Segundos de fundido entre canciones (1-15):",
            self._cf_secs, 1, 15, 1,
        )
        if ok:
            self._cf_secs = val
            if self._cf_enabled:
                self._send_playback("set_crossfade", {"seconds": val, "enabled": True})

    # ------------------------------------------------------------------
    # Export / Import
    # ------------------------------------------------------------------

    def _do_export_liked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar Me Gusta",
            str(Path.home() / "Downloads" / "jarvis_me_gusta.json"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import export_liked_to_file
                result = export_liked_to_file(path)
                QTimer.singleShot(0, lambda: QMessageBox.information(
                    self, "Exportación completada",
                    f"Se exportaron {result['count']} canciones a:\n{result['path']}"
                ))
            except Exception as e:
                QTimer.singleShot(0, lambda: QMessageBox.warning(
                    self, "Error al exportar", str(e)
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _do_export_current(self):
        pl = self._current_playlist
        if not pl:
            QMessageBox.information(self, "Sin playlist", "Abre una playlist primero.")
            return
        pid = pl.get("playlistId") or pl.get("browseId") or ""
        name = (pl.get("title") or pl.get("name") or pid or "playlist").replace("/", "_")
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar lista actual",
            str(Path.home() / "Downloads" / f"jarvis_{name}.json"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import export_playlist_to_file
                result = export_playlist_to_file(pid, path)
                QTimer.singleShot(0, lambda: QMessageBox.information(
                    self, "Exportación completada",
                    f"Se exportaron {result['count']} canciones de '{result['name']}' a:\n{result['path']}"
                ))
            except Exception as e:
                QTimer.singleShot(0, lambda: QMessageBox.warning(
                    self, "Error al exportar", str(e)
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _import_playlist_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importar playlist Jarvis",
            str(Path.home() / "Downloads"),
            "Playlist Jarvis (*.json)",
        )
        if not path:
            return

        def _work():
            try:
                from actions.ytmusic import import_playlist_from_file
                tracks = import_playlist_from_file(path)
                if not tracks:
                    QTimer.singleShot(0, lambda: QMessageBox.warning(
                        self, "Playlist vacía", "No se encontraron pistas con videoId."
                    ))
                    return
                import json as _json
                data = _json.loads(Path(path).read_text(encoding="utf-8"))
                playlist_name = data.get("name", Path(path).stem)
                self._send_playback("play_tracks", {"tracks": tracks, "start_index": 0, "shuffle": False})
                QTimer.singleShot(0, lambda: (
                    self._set_header(
                        playlist_name,
                        f"Importada • {len(tracks)} canciones",
                        "Importada",
                        {},
                    ),
                    self._show_imported_tracks(tracks),
                ))
            except Exception as e:
                QTimer.singleShot(0, lambda: QMessageBox.warning(
                    self, "Error al importar", str(e)
                ))

        threading.Thread(target=_work, daemon=True).start()

    def _show_imported_tracks(self, tracks: list):
        """Display imported tracks in the table (runs on UI thread)."""
        self._table_kind = "playlist_tracks"
        self._items = [
            {
                "videoId": t.get("videoId", ""),
                "title": t.get("title", ""),
                "artists": t.get("artists", ""),
                "_kind": "song",
                "_index": i,
            }
            for i, t in enumerate(tracks)
        ]
        self._show_songs(self._items, table_kind="playlist_tracks")
        self.shuffle_btn.setVisible(True)

    def load_playlists(self, force: bool = False):
        if self._artist_page_open and not force:
            return
        self._show_browse_content()
        self.filter_row.setVisible(False)
        self._run("library_playlists", lambda: __import__("actions.ytmusic", fromlist=["list_playlists"]).list_playlists(limit=None))

    def search(self):
        self._show_browse_content()
        query = self.query_input.text().strip()
        if not query:
            self.load_playlists(force=True)
            return

        def _load():
            ytmod = __import__(
                "actions.ytmusic",
                fromlist=["search_songs", "search_playlists", "search_artists"],
            )
            return {
                "songs": ytmod.search_songs(query, limit=40),
                "playlists": ytmod.search_playlists(query, limit=40),
                "artists": ytmod.search_artists(query, limit=30),
            }

        self._set_header(f"Buscar: {query}", "Filtra por canciones, playlists o artistas", "Busqueda", {})
        self._run("search_all", _load)

    def _show_search_filter(self, key: str):
        if key not in self._filter_buttons:
            return
        for name, btn in self._filter_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(name == key)
            btn.blockSignals(False)
        if key == "playlists":
            self._show_playlists(self._search_results.get("playlists", []))
            self._set_header("Playlists", "Resultados de la busqueda", "Busqueda", {})
        elif key == "artists":
            self._show_artists(self._search_results.get("artists", []))
        else:
            self._current_playlist = None
            self._show_songs(self._search_results.get("songs", []), table_kind="search_songs")
            self._set_header("Canciones", "Resultados de la busqueda", "Busqueda", {})

    def _select_row(self, row: int, _col: int = 0):
        if row >= 0:
            if self._table_kind == "artists":
                self.table.selectRow(row)
                data = self._row_data(row)
                if data:
                    self._open_artist_page(data)
                return
            if self._table_kind == "playlists":
                self.table.selectRow(row)
            else:
                QTimer.singleShot(0, self._restore_playing_selection)
            self._prefetch_audio_streams(row, 4)

    def _restore_playing_selection(self):
        if self._table_kind not in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks"
        }:
            return
        selected_row = -1
        for row in range(self.table.rowCount()):
            if self._row_data(row).get("_playing"):
                selected_row = row
                break
        selection_model = self.table.selectionModel()
        if selection_model is None:
            return
        if selected_row >= 0:
            index = self.table.model().index(selected_row, 1)
            selection_model.select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect
                | QItemSelectionModel.SelectionFlag.Rows,
            )
        else:
            selection_model.clearSelection()

    def _audio_tracks_from_items(self, start: int = 0, count: int | None = None) -> list[dict]:
        try:
            start_i = max(0, int(start or 0))
        except Exception:
            start_i = 0
        items = self._items[start_i:] if count is None else self._items[start_i:start_i + max(1, int(count))]
        return [
            {
                "videoId": item.get("videoId", ""),
                "title": item.get("title", ""),
                "artists": item.get("artists", ""),
            }
            for item in items
            if item.get("videoId")
        ]

    def _prefetch_audio_streams(self, start: int = 0, count: int = 4):
        if self._table_kind not in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks"
        }:
            return
        tracks = self._audio_tracks_from_items(start, count)
        if tracks:
            self._send_playback("prefetch_tracks", {"tracks": tracks, "start_index": 0, "count": count})

    def _activate_row(self, row: int, _col: int = 0):
        data = self._row_data(row)
        if not data:
            return
        kind = data.get("_kind", "song")
        if kind == "playlist":
            self.open_playlist(data)
            return
        if kind == "artist":
            self._open_artist_page(data)
            return
        if kind == "song":
            self._play_song(data)

    def open_playlist(self, data: dict):
        pid = data.get("playlistId") or data.get("browseId") or ""
        if not pid:
            return
        self._current_playlist = dict(data)
        self._table_kind = "playlist_tracks"
        self._set_header(self._playlist_title(data), self._playlist_meta(data), "Lista", data)

        def _load():
            return __import__("actions.ytmusic", fromlist=["list_playlist_tracks"]).list_playlist_tracks(
                query_or_id=pid,
                limit=None,
                shuffle=False,
            )

        self._run("playlist_tracks", _load)

    def _play_song(self, data: dict):
        self._mark_playing_row(
            self._safe_text(data.get("title")),
            self._safe_text(data.get("artists")),
        )
        if self._table_kind in {"playlist_tracks", "album_tracks", "artist_tracks"}:
            tracks = self._audio_tracks_from_items(0, None)
            if tracks:
                self._send_playback("play_tracks", {
                    "tracks": tracks,
                    "start_index": int(data.get("_index", 0) or 0),
                    "shuffle": False,
                })
                return
        if self._current_playlist and self._table_kind == "playlist_tracks":
            playlist_id = self._current_playlist.get("playlistId") or self._current_playlist.get("browseId") or ""
            if playlist_id:
                self._send_playback("play_playlist", {
                    "playlist_id": playlist_id,
                    "limit": 1000,
                    "start_index": int(data.get("_index", 0) or 0),
                    "shuffle": False,
                })
                return
        if data.get("videoId"):
            self._send_playback("play_track", {
                "videoId": data.get("videoId", ""),
                "title": data.get("title", ""),
                "artists": data.get("artists", ""),
            })
            return
        query = f"{data.get('title', '')} {data.get('artists', '')}".strip()
        if query:
            self._send_playback("play", {"query": query, "type": "song"})

    def _play_current_playlist_shuffled(self):
        if self._table_kind != "playlist_tracks":
            return
        tracks = self._audio_tracks_from_items(0, None)
        if not tracks:
            return
        random.shuffle(tracks)
        first = tracks[0]
        self._mark_playing_row(
            self._safe_text(first.get("title")),
            self._safe_text(first.get("artists")),
        )
        self._send_playback("play_tracks", {
            "tracks": tracks,
            "start_index": 0,
            "shuffle": False,
        })

    def _send_playback(self, action: str, params: dict | None = None):
        win = self.window()
        cb = getattr(win, "on_playback_command", None)
        if cb:
            threading.Thread(target=cb, args=(action, params or {}), daemon=True).start()

    _DETAILS_MIN_WIDTH = 760
    _HEADER_NARROW_WIDTH = 560

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_details_visibility()
        self._apply_header_responsive()

    def _details_should_show(self) -> bool:
        data = self._now_playing_data if isinstance(self._now_playing_data, dict) else {}
        return bool(data.get("title")) and self.width() >= self._DETAILS_MIN_WIDTH

    def _update_details_visibility(self):
        try:
            self.details_panel.setVisible(self._details_should_show())
        except RuntimeError:
            pass

    def _apply_header_responsive(self):
        narrow = self.width() < self._HEADER_NARROW_WIDTH
        if narrow == getattr(self, "_header_narrow_state", None):
            return
        self._header_narrow_state = narrow
        self.title_label.setStyleSheet(
            "color: white; background: transparent; font-weight: 900;"
            f" font-size: {26 if narrow else 34}px;"
        )

    def update_now_playing(self, title: str, artists: str, playing: bool = True):
        title = self._safe_text(title).strip()
        artists = self._safe_text(artists).strip()
        if not title:
            self._now_playing_data = {}
            self._now_playing_key = ("", "")
            self._detail_render_fingerprint = ()
            self._detail_render_track_key = ("", "")
            self._details_loading_key = ("", "", "")
            self.details_panel.setVisible(False)
            self._details_visible_once = False
            return

        key = (title.lower(), artists.lower())
        track_changed = key != self._now_playing_key
        matched = self._find_matching_track(title, artists)
        data = dict(matched or {"title": title, "artists": artists, "_kind": "song"})
        data["_playing"] = bool(playing)
        if key == self._now_playing_key:
            kept = {k: v for k, v in self._now_playing_data.items() if v not in ("", None, [])}
            self._now_playing_data = {**data, **kept}
        else:
            self._now_playing_data = data
        self._now_playing_key = key
        self._update_details_visibility()
        self._details_visible_once = True
        if track_changed or self._playing_mark_revision != self._table_revision:
            self._mark_playing_row(title, artists)
        self._ensure_thumb_async(self._now_playing_data)
        self._render_now_playing()
        detail_key = self._details_key(self._now_playing_data)
        if not self._now_playing_data.get("_details_loaded") and self._details_loading_key != detail_key:
            self._load_now_playing_details(self._now_playing_data)

    def _find_matching_track(self, title: str, artists: str) -> dict:
        title_n = title.strip().lower()
        artists_n = artists.strip().lower()
        for item in self._items:
            if self._safe_text(item.get("title")).strip().lower() != title_n:
                continue
            if artists_n and artists_n not in self._safe_text(item.get("artists")).strip().lower():
                continue
            return item
        return {}

    def _details_key(self, data: dict) -> tuple[str, str, str]:
        return (
            self._safe_text(data.get("videoId")).strip(),
            self._safe_text(data.get("title")).strip().lower(),
            self._safe_text(data.get("artists")).strip().lower(),
        )

    def _mark_playing_row(self, title: str, artists: str):
        title_n = title.strip().lower()
        artists_n = artists.strip().lower()
        if self._table_kind not in {
            "songs", "playlist_tracks", "search_songs", "album_tracks", "artist_tracks"
        }:
            return
        self._playing_mark_revision = self._table_revision
        selected_row = -1
        for row in range(self.table.rowCount()):
            data = self._row_data(row)
            is_playing = self._safe_text(data.get("title")).strip().lower() == title_n
            if is_playing and artists_n:
                is_playing = artists_n in self._safe_text(data.get("artists")).strip().lower()
            data["_playing"] = is_playing
            self._set_row_data(row, data)
            number_item = self.table.item(row, 0)
            if number_item:
                number_item.setText("▶" if is_playing else str(int(data.get("_index", row) or row) + 1))
            if is_playing and selected_row < 0:
                selected_row = row
        self.table.clearSelection()
        if selected_row >= 0:
            self.table.selectRow(selected_row)
            self.table.setCurrentCell(selected_row, 1)

    def _load_now_playing_details(self, data: dict):
        self._detail_request += 1
        token = self._detail_request
        request_key = self._details_key(data)
        self._details_loading_key = request_key

        def worker():
            try:
                ytmod = __import__("actions.ytmusic", fromlist=["get_song_details"])
                result = ytmod.get_song_details(
                    video_id=data.get("videoId", ""),
                    title=data.get("title", ""),
                    artists=data.get("artists", ""),
                    album_id=data.get("albumId", ""),
                    artist_id=data.get("artistId", ""),
                )
                self._result_sig.emit("now_playing_details", {"token": token, "key": request_key, "details": result})
                images = {}
                if result.get("thumbnail"):
                    raw_url = result.get("thumbnail", "")
                    images["thumbnail"] = raw_url
                    images["thumb_b64"] = self._fetch_thumb_b64(raw_url)
                    cached = self._thumb_cache.get(str(raw_url or "").strip())
                    if cached:
                        images["thumb_src"] = self._data_uri(cached)
                        images["thumb_src_url"] = raw_url
                if result.get("artistThumbnail"):
                    raw_url = result.get("artistThumbnail", "")
                    images["artistThumbnail"] = raw_url
                    images["artist_thumb_b64"] = self._fetch_thumb_b64(raw_url)
                    cached = self._thumb_cache.get(str(raw_url or "").strip())
                    if cached:
                        images["artist_thumb_src"] = self._data_uri(cached)
                        images["artist_thumb_src_url"] = raw_url
                if images:
                    self._result_sig.emit("now_playing_images", {"token": token, "key": request_key, "details": images})
            except Exception as exc:
                self._result_sig.emit("now_playing_details", {"token": token, "key": request_key, "error": str(exc)})

        threading.Thread(target=worker, daemon=True).start()

    def _render_now_playing(self):
        data = self._now_playing_data or {}
        if not data:
            return
        fp = self._detail_fingerprint(data)
        if fp == self._detail_render_fingerprint:
            return
        scroll = self.details_scroll.verticalScrollBar()
        same_track = self._detail_render_track_key == self._now_playing_key
        previous_scroll = scroll.value() if same_track else 0
        self._set_detail_cover(data)
        self.details.setText(self._render_details_html(data))
        self._detail_render_fingerprint = fp
        self._detail_render_track_key = self._now_playing_key
        if same_track:
            QTimer.singleShot(0, lambda: scroll.setValue(min(previous_scroll, scroll.maximum())))
        else:
            QTimer.singleShot(0, lambda: scroll.setValue(0))

    def _detail_fingerprint(self, data: dict) -> tuple:
        keys = (
            "videoId",
            "title",
            "artists",
            "album",
            "year",
            "duration",
            "thumbnail",
            "thumb_src_url",
            "artistName",
            "artistDescription",
            "artistThumbnail",
            "artist_thumb_src_url",
            "_details_loaded",
        )
        return tuple(self._safe_text(data.get(k)) for k in keys) + (str(self._details_image_width()),)

    def _meta_row(self, label: str, value, href: str = "") -> str:
        shown = self._safe_text(value) or "-"
        rendered = self._esc(shown)
        if href and shown != "-":
            rendered = (
                f"<a href='{html_lib.escape(href, quote=True)}' "
                "style='color:#7dd3fc; text-decoration:none; font-weight:800;'>"
                f"{rendered}</a>"
            )
        return (
            "<tr>"
            f"<td style='color:#7f8ea3; font-size:11px; font-weight:700; padding:7px 16px 7px 0; white-space:nowrap; vertical-align:middle;'>{self._esc(str(label).upper())}</td>"
            f"<td style='color:#f1f5f9; font-size:13px; font-weight:700; padding:7px 0; vertical-align:middle;'>{rendered}</td>"
            "</tr>"
        )

    def _render_details_html(self, data: dict) -> str:
        artist_src = self._image_src(data, "artist_thumb_b64", "artist_thumb_src", "artistThumbnail", "artist_thumb_src_url")
        image_width = self._details_image_width()
        artist_img = self._image_html(artist_src, image_width, "0 0 14px 0")
        artist_name = data.get("artistName") or data.get("artists") or ""
        artist_desc = data.get("artistDescription") or "Cargando información del artista..."
        artist_block = ""
        if artist_name or artist_desc or artist_img:
            artist_block = (
                "<div style='margin-top:26px;'>"
                "<div style='color:#7f8ea3; font-size:11px; font-weight:800; letter-spacing:1px; margin:0 0 12px 0;'>INFORMACIÓN DEL ARTISTA</div>"
                f"{artist_img}"
                "<div style='font-size:17px; font-weight:900; margin:0 0 8px 0;'>"
                f"<a href='music:artist' style='color:#f8fafc; text-decoration:none;'>{self._esc(artist_name)}</a>"
                "</div>"
                f"<p style='color:#b9c0c9; font-size:14px; line-height:1.5; margin:0;'>{self._esc(artist_desc)}</p>"
                "</div>"
            )
        return (
            "<div style='color:#f8fafc;'>"
            f"<h2 style='margin:0 0 3px 0; font-size:22px; line-height:1.15;'>{self._esc(data.get('title'))}</h2>"
            "<div style='font-size:13px; font-weight:700; margin-bottom:16px;'>"
            f"<a href='music:artist' style='color:#9aa7b4; text-decoration:none;'>{self._esc(data.get('artists'))}</a>"
            "</div>"
            "<table cellspacing='0' cellpadding='0' style='margin:2px 0 6px 0;'>"
            f"{self._meta_row('Álbum', data.get('album'), 'music:album')}"
            f"{self._meta_row('Año', data.get('year'))}"
            f"{self._meta_row('Artista', artist_name, 'music:artist')}"
            f"{self._meta_row('Duración', data.get('duration'))}"
            "</table>"
            f"{artist_block}"
            "</div>"
        )

    def _on_details_link(self, link: str):
        target = str(link or "").strip().lower()
        data = dict(self._now_playing_data or {})
        if target == "music:album":
            self._open_album_page(data)
        elif target == "music:artist":
            self._open_artist_page(data)

    def _open_album_page(self, data: dict):
        album_id = self._safe_text(data.get("albumId")).strip()
        album_name = self._safe_text(data.get("album") or data.get("title")).strip()
        if not album_id and not album_name:
            return
        self._show_browse_content()
        self.filter_row.setVisible(False)
        self._set_header(album_name or "Album", "Cargando album...", "Album", data)

        def _load():
            ytmod = __import__("actions.ytmusic", fromlist=["get_album_details"])
            return ytmod.get_album_details(query=album_name, browse_id=album_id)

        self._run("album_page", _load)

    def _open_artist_page(self, data: dict):
        artist_id = self._safe_text(data.get("artistBrowseId") or data.get("artistId")).strip()
        artist_name = self._safe_text(data.get("artistName") or data.get("artists")).strip()
        if not artist_id and not artist_name:
            return
        self.filter_row.setVisible(False)
        self._show_artist_loading(artist_name or "Artista", data)

        def _load():
            ytmod = __import__("actions.ytmusic", fromlist=["get_artist_details"])
            return ytmod.get_artist_details(query=artist_name, browse_id=artist_id)

        self._run("artist_page", _load)

    def _merge_now_playing_details(self, payload: dict, mark_loaded: bool = True):
        if not isinstance(payload, dict):
            return
        payload_key = payload.get("key")
        current_key = self._details_key(self._now_playing_data)
        if payload_key and payload_key != current_key:
            return
        if not payload_key and payload.get("token") != self._detail_request:
            return
        self._details_loading_key = ("", "", "")
        if payload.get("error") and mark_loaded:
            return
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            return
        self._now_playing_data.update({k: v for k, v in details.items() if v not in ("", None, [])})
        if mark_loaded:
            self._now_playing_data["_details_loaded"] = True
        self._render_now_playing()

    def _handle_result(self, op: str, result):
        if op == "now_playing_details":
            self._merge_now_playing_details(result)
            return
        if op == "now_playing_images":
            self._merge_now_playing_details(result, mark_loaded=False)
            return
        if self._artist_page_open and op in {
            "library_playlists", "playlist_tracks", "search_all"
        }:
            return
        if isinstance(result, Exception):
            self.status.setVisible(True)
            self.status.setText("Error")
            self.table.setRowCount(0)
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderLabels(["Error"])
            self.table.insertRow(0)
            self.table.setItem(0, 0, QTableWidgetItem(str(result)))
            return
        if op == "album_page":
            data = dict(result or {}) if isinstance(result, dict) else {}
            tracks = list(data.get("tracks") or [])
            self._current_playlist = None
            self._show_songs(tracks, table_kind="album_tracks")
            title = self._safe_text(data.get("title")) or "Album"
            artists = self._safe_text(data.get("artists"))
            year = self._safe_text(data.get("year"))
            subtitle = " - ".join(part for part in (artists, year, f"{len(tracks)} canciones") if part)
            self._set_header(title, subtitle, "Album", data)
            return
        if op == "artist_page":
            data = dict(result or {}) if isinstance(result, dict) else {}
            self._current_playlist = None
            self._render_artist_page(data)
            return
        if op == "library_playlists":
            self.filter_row.setVisible(False)
            self._show_playlists(list(result or []))
            return
        if op == "playlist_tracks":
            self.filter_row.setVisible(False)
            self._show_songs(list(result or []), table_kind="playlist_tracks", playlist=self._current_playlist or {})
            return
        if op == "search_all":
            if not isinstance(result, dict):
                result = {}
            self._search_results = {
                "songs": list(result.get("songs") or []),
                "playlists": list(result.get("playlists") or []),
                "artists": list(result.get("artists") or []),
            }
            self.filter_row.setVisible(True)
            if self._search_results["songs"]:
                self._show_search_filter("songs")
            elif self._search_results["playlists"]:
                self._show_search_filter("playlists")
            else:
                self._show_search_filter("artists")


_FILE_ICONS = {
    "image":   ("image", C.ACC), "video":   ("video", C.ACC2),
    "audio":   ("audio", C.ACC), "pdf":     ("file", C.RED),
    "word":    ("file", C.ACC2), "excel":   ("chart", C.ACC),
    "code":    ("code", C.ACC2), "archive": ("archive", C.ACC2),
    "pptx":    ("chart", C.RED), "text":     ("file", C.TEXT_DIM),
    "data":    ("chart", C.ACC), "unknown":  ("file", C.TEXT_DIM),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg.setColorAt(0, qcol("#FFFFFF", 34 if z._hovering or z._drag_over else 22))
        bg.setColorAt(1, qcol("#FFFFFF", 12))
        p.setBrush(QBrush(bg)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, 16, 16)

        if z._current_file:   border_col = qcol(C.ACC, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.BORDER, 160)

        pen = QPen(border_col, 1.3, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 16, 16)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here or click to browse")
        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        icon_pm = _line_icon("upload", C.PRI, 24).pixmap(24, 24)
        p.drawPixmap(int(cx - 12), int(cy - 24), icon_pm)
        p.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        icon_pm = _line_icon(icon, icon_col, 30).pixmap(30, 30)
        p.drawPixmap(
            int(block_x + (block_w - 30) / 2),
            int((H - 30) / 2),
            icon_pm,
        )

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont(FONT_UI, 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont(FONT_UI, 6))
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        close_pm = _line_icon("close", C.RED, 18).pixmap(18, 18)
        p.drawPixmap(W - 29, int((H - 18) / 2), close_pm)

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _SeekSlider(QSlider):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setTracking(True)

    def _value_from_pos(self, pos):
        span = max(1, self.width() - 1) if self.orientation() == Qt.Orientation.Horizontal else max(1, self.height() - 1)
        coord = max(0, min(pos.x() if self.orientation() == Qt.Orientation.Horizontal else pos.y(), span))
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        inverted = self.invertedAppearance() ^ rtl
        if inverted:
            coord = span - coord
        value = self.minimum() + (self.maximum() - self.minimum()) * (coord / span)
        return int(round(value))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_from_pos(e.position().toPoint()))
            self.sliderPressed.emit()
            self.sliderMoved.emit(self.value())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.isSliderDown() and (e.buttons() & Qt.MouseButton.LeftButton):
            self.setValue(self._value_from_pos(e.position().toPoint()))
            self.sliderMoved.emit(self.value())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setValue(self._value_from_pos(e.position().toPoint()))
            self.setSliderDown(False)
            self.sliderReleased.emit()
            e.accept()
            return
        super().mouseReleaseEvent(e)


class _CommandInput(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._draft: str = ""

    def add_history(self, text: str):
        text = str(text or "").strip()
        if not text:
            return
        if self._history and self._history[-1] == text:
            self._history_idx = None
            self._draft = ""
            return
        self._history.append(text)
        self._history_idx = None
        self._draft = ""

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and self._history:
            if self._history_idx is None:
                if key == Qt.Key.Key_Up:
                    self._draft = self.text()
                    self._history_idx = len(self._history) - 1
                else:
                    return
            else:
                if key == Qt.Key.Key_Up and self._history_idx > 0:
                    self._history_idx -= 1
                elif key == Qt.Key.Key_Down:
                    if self._history_idx < len(self._history) - 1:
                        self._history_idx += 1
                    else:
                        self._history_idx = None
                        self.setText(self._draft)
                        self.setCursorPosition(len(self.text()))
                        return
            if self._history_idx is not None:
                value = self._history[self._history_idx]
                self.setText(value)
                self.setCursorPosition(len(value))
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._history_idx = None
            self._draft = ""

        super().keyPressEvent(event)


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(15, 23, 42, 235);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 22px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont(FONT_UI, font_size,
                            QFont.Weight.DemiBold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("Initialisation Required", 15, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont(FONT_UI, 10))
        self._key_input.setFixedHeight(38)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.035); color: {C.TEXT};
                border: 1px solid rgba(255, 255, 255, 0.080); border-radius: 14px; padding: 6px 12px;
            }}
            QLineEdit:focus {{ border: 1px solid rgba(125, 211, 252, 0.42); }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows", "Windows"), ("mac", "macOS"), ("linux", "Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
            btn.setFixedHeight(36)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("Initialise Systems")
        init_btn.setFont(QFont(FONT_UI, 10, QFont.Weight.DemiBold))
        init_btn.setFixedHeight(40)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(125, 211, 252, 0.16); color: {C.PRI};
                border: 1px solid rgba(125, 211, 252, 0.32); border-radius: 15px;
            }}
            QPushButton:hover {{
                background: rgba(125, 211, 252, 0.25); border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#1a271a"),"mac":(C.ACC,"#1a271a"),"linux":(C.ACC,"#1a271a")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 14px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: rgba(255, 255, 255, 0.06); color: {C.TEXT_DIM};
                        border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 14px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid rgba(125, 211, 252, 0.34); }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class _CornerGrip(QWidget):
    """Bottom-right resize handle for the frameless floating overlay."""

    def __init__(self, overlay):
        super().__init__(overlay)
        self._ov = overlay
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(qcol("#FFFFFF", 190), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for off in (0, 6, 12):
            p.drawLine(QPointF(20 - off, 8), QPointF(8, 20 - off))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._ov._begin_resize(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._ov._do_resize(event.globalPosition().toPoint())
            event.accept()

    def mouseReleaseEvent(self, event):
        self._ov._end_resize()
        event.accept()


class _FloatOverlay(QWidget):
    """Transparent always-on-top window placed over the floating video.

    Shows playback controls + title/artist only while the cursor is over it
    (polled, to avoid child enter/leave flicker), and drags the video with it.
    """

    def __init__(self, callbacks: dict, draggable: bool = True, resizable: bool = False):
        super().__init__()
        self._cb = callbacks
        self._drag = None
        self._origin = None
        self._draggable = draggable
        self._resizable = resizable
        self._min_w = 320
        self._resize_anchor = None
        self._resize_w0 = 0
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        self._top = QWidget(self)
        self._top.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 rgba(0,0,0,0.62), stop:1 rgba(0,0,0,0));"
        )
        top_l = QHBoxLayout(self._top)
        top_l.setContentsMargins(12, 8, 8, 8)
        top_l.setSpacing(8)
        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self._title = QLabel("")
        self._title.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: 800; background: transparent;")
        self._artist = QLabel("")
        self._artist.setStyleSheet("color: rgba(255,255,255,0.82); font-size: 10px; background: transparent;")
        text_col.addWidget(self._title)
        text_col.addWidget(self._artist)
        top_l.addLayout(text_col, 1)
        self._restore = _icon_button("close", "Cerrar", size=30, icon_size=16)
        self._restore.clicked.connect(lambda: self._cb.get("restore", lambda: None)())
        top_l.addWidget(self._restore, alignment=Qt.AlignmentFlag.AlignTop)

        self._bottom = QWidget(self)
        self._bottom.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 rgba(0,0,0,0), stop:1 rgba(0,0,0,0.62));"
        )
        bot_l = QHBoxLayout(self._bottom)
        bot_l.setContentsMargins(8, 8, 8, 10)
        bot_l.setSpacing(16)
        bot_l.addStretch()
        self._prev = _MediaBtn(_MediaBtn.PREV)
        self._prev.setToolTip("Vídeo anterior")
        self._prev.clicked.connect(lambda: self._cb.get("prev", lambda: None)())
        self._rwd = _icon_button("backward", "Retroceder 10 s", size=40, icon_size=18)
        self._rwd.clicked.connect(lambda: self._cb.get("rewind", lambda: None)())
        self._play = _MediaBtn(_MediaBtn.PLAY)
        self._play.clicked.connect(lambda: self._cb.get("toggle", lambda: None)())
        self._fwd = _icon_button("forward", "Adelantar 10 s", size=40, icon_size=18)
        self._fwd.clicked.connect(lambda: self._cb.get("forward", lambda: None)())
        self._next = _MediaBtn(_MediaBtn.NEXT)
        self._next.setToolTip("Vídeo siguiente")
        self._next.clicked.connect(lambda: self._cb.get("next", lambda: None)())
        bot_l.addWidget(self._prev)
        bot_l.addWidget(self._rwd)
        bot_l.addWidget(self._play)
        bot_l.addWidget(self._fwd)
        bot_l.addWidget(self._next)
        bot_l.addStretch()

        self._grip = _CornerGrip(self) if self._resizable else None

        self._set_controls_visible(False)
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(220)
        self._hover_timer.timeout.connect(self._check_hover)
        self._hover_timer.start()

    def resizeEvent(self, event):
        w, h = self.width(), self.height()
        self._top.setGeometry(0, 0, w, 58)
        self._bottom.setGeometry(0, h - 64, w, 64)
        if self._grip is not None:
            self._grip.move(w - self._grip.width() - 3, h - self._grip.height() - 3)
            self._grip.raise_()

    def _begin_resize(self, gpos):
        self._resize_anchor = gpos
        self._resize_w0 = self.width()

    def _do_resize(self, gpos):
        if self._resize_anchor is None:
            return
        dx = gpos.x() - self._resize_anchor.x()
        new_w = self._resize_w0 + dx
        try:
            max_w = QApplication.primaryScreen().availableGeometry().width() - 40
        except Exception:
            max_w = 1600
        new_w = max(self._min_w, min(int(new_w), max_w))
        new_h = int(round(new_w * 9 / 16))
        self.resize(new_w, new_h)
        resizer = self._cb.get("resized")
        if resizer:
            resizer(new_w, new_h)

    def _end_resize(self):
        self._resize_anchor = None

    def _set_controls_visible(self, visible: bool):
        self._top.setVisible(visible)
        self._bottom.setVisible(visible)
        if self._grip is not None:
            self._grip.setVisible(visible)
        if visible:
            self._top.raise_()
            self._bottom.raise_()
            if self._grip is not None:
                self._grip.raise_()

    def _check_hover(self):
        inside = self.frameGeometry().contains(QCursor.pos())
        if inside != self._top.isVisible():
            self._set_controls_visible(inside)

    def set_meta(self, title: str, artist: str):
        title = str(title or "")
        short = title if len(title) <= 42 else title[:41].rstrip() + "…"
        self._title.setText(short)
        self._title.setToolTip(title)
        self._artist.setText(str(artist or ""))

    def set_playing(self, playing: bool):
        self._play.set_shape(_MediaBtn.PAUSE if playing else _MediaBtn.PLAY)

    def mousePressEvent(self, event):
        if self._draggable and event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint()
            self._origin = self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            new_pos = self._origin + (event.globalPosition().toPoint() - self._drag)
            self.move(new_pos)
            mover = self._cb.get("moved")
            if mover:
                mover(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None

    def closeEvent(self, event):
        try:
            self._cb.get("restore", lambda: None)()
        except Exception:
            pass
        super().closeEvent(event)


class _DetachWindow(QWidget):
    """Top-level window hosting the detached video. If the user closes it from the
    OS (Alt+F4, taskbar), it first hands the video back to the panel so the shared
    mpv surface is never destroyed (which would crash the app)."""

    def __init__(self, on_close):
        super().__init__()
        self._on_close = on_close

    def closeEvent(self, event):
        try:
            if self._on_close is not None:
                self._on_close()
        except Exception:
            pass
        super().closeEvent(event)


class FlowLayout(QLayout):
    """Reflowing layout: items wrap to the next row based on available width."""

    def __init__(self, parent=None, margin=0, hspacing=16, vspacing=18):
        super().__init__(parent)
        self._items: list = []
        self._hspace = hspacing
        self._vspace = vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0
        right = rect.right() - margins.right()
        for item in self._items:
            w = item.sizeHint().width()
            h = item.sizeHint().height()
            next_x = x + w + self._hspace
            if next_x - self._hspace > right and line_height > 0:
                x = rect.x() + margins.left()
                y = y + line_height + self._vspace
                next_x = x + w + self._hspace
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x
            line_height = max(line_height, h)
        return y + line_height - rect.y() + margins.bottom()


class _AspectVideo(QWidget):
    """Keeps a child surface at a fixed aspect ratio, centered (no double black bars)."""

    def __init__(self, surface: QWidget, ratio: float = 16 / 9, parent=None):
        super().__init__(parent)
        self._surface = surface
        self._ratio = ratio
        surface.setParent(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        outer_w, outer_h = self.width(), self.height()
        w = outer_w
        h = int(round(w / self._ratio))
        if h > outer_h:
            h = outer_h
            w = int(round(h * self._ratio))
        self._surface.setGeometry((outer_w - w) // 2, (outer_h - h) // 2, w, h)


class _VideoCard(QWidget):
    activated = pyqtSignal(str)

    def __init__(self, video: dict, parent=None):
        super().__init__(parent)
        self._vid = str(video.get("id") or "")
        self.setObjectName("YtCard")
        self.setFixedWidth(248)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(7)

        self.thumb = QLabel()
        self.thumb.setFixedSize(248, 140)
        self.thumb.setObjectName("YtCardThumb")
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.thumb)

        title = QLabel(video.get("title", ""))
        title.setWordWrap(True)
        title.setFixedHeight(38)
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 12px; font-weight: 700;")
        title.setToolTip(video.get("title", ""))
        lay.addWidget(title)

        meta = QLabel(self._meta(video))
        meta.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 10px;")
        lay.addWidget(meta)

    @staticmethod
    def _meta(video: dict) -> str:
        parts = []
        if video.get("channel"):
            parts.append(str(video["channel"]))
        try:
            total = int(video.get("duration") or 0)
        except Exception:
            total = 0
        if total > 0:
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            parts.append(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
        return "  ·  ".join(parts)

    def thumb_label(self) -> QLabel:
        return self.thumb

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self._vid)


class _PanelControls(QWidget):
    """Translucent control bar overlaid on the in-panel video (YouTube style).

    A frameless tool window that tracks the video area's screen rect and shows a
    bottom control bar on hover. Controls are exposed as attributes so the panel
    keeps its existing wiring.
    """

    def __init__(self, panel, video_box):
        super().__init__(panel)
        self._panel = panel
        self._box = video_box
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)

        self._bar = QWidget(self)
        self._bar.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 rgba(0,0,0,0), stop:1 rgba(0,0,0,0.74));"
        )
        bar_l = QHBoxLayout(self._bar)
        bar_l.setContentsMargins(14, 24, 14, 12)
        bar_l.setSpacing(10)

        self.play_btn = _MediaBtn(_MediaBtn.PLAY)
        self.time_lbl = QLabel("0:00 / 0:00")
        self.time_lbl.setStyleSheet("color: #FFFFFF; font-size: 11px; background: transparent;")
        self.seek = _SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setCursor(Qt.CursorShape.PointingHandCursor)
        self.seek.setStyleSheet(
            "QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.25); border-radius:2px; }"
            "QSlider::sub-page:horizontal { background:#38BDF8; border-radius:3px; }"
            "QSlider::handle:horizontal { background:#DFF5FF; width:13px; height:13px; margin:-5px 0; border-radius:6px; }"
        )
        self.vol_icon = QLabel()
        self.vol_icon.setPixmap(_line_icon("volume", "#FFFFFF", 17).pixmap(17, 17))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(90)
        self.volume.setFixedWidth(88)
        self.volume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.volume.setStyleSheet(
            "QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.25); border-radius:2px; }"
            "QSlider::sub-page:horizontal { background:#7DD3FC; border-radius:3px; }"
            "QSlider::handle:horizontal { background:#DFF5FF; width:12px; height:12px; margin:-4px 0; border-radius:6px; }"
        )
        self.like_btn = _LikeBtn()
        self.download_btn = _icon_button("download", "Descargar vídeo", size=36, icon_size=18)
        self.float_btn = _icon_button("pip", "Vídeo flotante", size=36, icon_size=18)
        self.fullscreen_btn = _icon_button("fullscreen", "Pantalla completa", size=36, icon_size=18)

        bar_l.addWidget(self.play_btn)
        bar_l.addWidget(self.time_lbl)
        bar_l.addWidget(self.seek, stretch=1)
        bar_l.addWidget(self.vol_icon)
        bar_l.addWidget(self.volume)
        bar_l.addSpacing(8)
        bar_l.addWidget(self.like_btn)
        bar_l.addWidget(self.download_btn)
        bar_l.addWidget(self.float_btn)
        bar_l.addWidget(self.fullscreen_btn)

        self._bar.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._sync)
        self._timer.start()

    def resizeEvent(self, event):
        self._bar.setGeometry(0, self.height() - 62, self.width(), 62)

    def _sync(self):
        panel = self._panel
        try:
            show = (
                panel.stack.currentIndex() == 1
                and panel._detached_mode is None
                and panel.isVisible()
                and self._box.isVisible()
                and not panel.window().isMinimized()
            )
        except Exception:
            show = False
        if not show:
            if self.isVisible():
                self.hide()
            return
        top_left = self._box.mapToGlobal(QPoint(0, 0))
        rect = QRect(top_left.x(), top_left.y(), self._box.width(), self._box.height())
        if self.geometry() != rect:
            self.setGeometry(rect)
        if not self.isVisible():
            self.show()
            self.raise_()
        inside = rect.contains(QCursor.pos())
        if inside != self._bar.isVisible():
            self._bar.setVisible(inside)
            if inside:
                self._bar.raise_()


class YouTubeModePanel(QWidget):
    """YouTube-like mode: search on top, responsive grid of recommended videos,
    and an embedded mpv player page."""

    _results_sig = pyqtSignal(object, str, str)
    _thumb_sig   = pyqtSignal(str, object)
    _like_sig    = pyqtSignal(str, bool, str)
    _play_sig    = pyqtSignal(str, bool)
    _pos_sig     = pyqtSignal(float, float, bool)
    _comments_sig = pyqtSignal(str, object, str)
    _details_sig = pyqtSignal(str, object, str)

    def __init__(self, progress_hook=None, parent=None):
        super().__init__(parent)
        self._progress_hook = progress_hook
        self._by_id: dict[str, dict] = {}
        self._thumb_cache: dict[str, bytes] = {}
        self._thumb_loading: set[str] = set()
        self._thumb_targets: dict[str, list[QLabel]] = {}
        self._thumb_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="yt-art")
        self._thumb_executor_closed = False
        self._player = None
        self._current: dict = {}
        self._liked = False
        self._duration = 0.0
        self._user_dragging = False
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._loaded_feed = False
        self._feed = "home"
        self._detached_mode: str | None = None
        self._fs_window: QWidget | None = None
        self._float_window: QWidget | None = None
        self._float_overlay = None
        self._float_drag_pos = None
        self._ordered_ids: list[str] = []
        self._desc_full = ""
        self._desc_expanded = False

        self._results_sig.connect(self._apply_results)
        self._thumb_sig.connect(self._apply_thumb)
        self._like_sig.connect(self._apply_like)
        self._play_sig.connect(self._apply_play_started)
        self._pos_sig.connect(self._apply_position)
        self._comments_sig.connect(self._apply_comments)
        self._details_sig.connect(self._apply_details)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

        self.setStyleSheet(self._panel_style())
        self._build_ui()
        QTimer.singleShot(150, self._load_initial_feed)

    # ----------------------------------------------------------------- styles
    def _panel_style(self) -> str:
        return f"""
            QWidget {{
                background: transparent;
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
            }}
            QLineEdit#YtSearch {{
                min-height: 44px;
                background: rgba(8, 14, 25, 0.88);
                color: {C.TEXT};
                border: 1px solid rgba(125, 211, 252, 0.12);
                border-radius: 22px;
                padding: 0 18px;
                font-size: 13px;
                selection-background-color: {C.PRI};
                selection-color: #06121a;
            }}
            QLineEdit#YtSearch:focus {{
                background: rgba(14, 15, 18, 0.94);
                border-color: rgba(125, 211, 252, 0.55);
            }}
            QPushButton#YtSearchButton {{
                min-height: 44px;
                background: rgba(248, 113, 113, 0.18);
                color: #FFE4E4;
                border: 1px solid rgba(248, 113, 113, 0.34);
                border-radius: 22px;
                padding: 0 20px;
                font-size: 12px;
                font-weight: 900;
            }}
            QPushButton#YtSearchButton:hover {{
                background: rgba(248, 113, 113, 0.28);
                border-color: rgba(248, 113, 113, 0.55);
            }}
            QWidget#YtCardThumb, QLabel#YtCardThumb {{
                background: rgba(255, 255, 255, 0.05);
                border-radius: 12px;
            }}
            QScrollArea#YtScroll {{ background: transparent; border: none; }}
            QLabel#YtHeader {{ color: {C.TEXT}; font-size: 15px; font-weight: 800; }}
            QLabel#YtStatus {{ color: {C.TEXT_MED}; font-size: 11px; }}
            QLabel#YtTitle {{ color: {C.TEXT}; font-size: 19px; font-weight: 800; }}
            QLabel#YtChannel {{ color: {C.TEXT_MED}; font-size: 12px; }}
            QPushButton#YtBack {{
                background: rgba(255, 255, 255, 0.05);
                color: {C.TEXT_DIM};
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 9px;
                padding: 7px 14px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#YtBack:hover {{ background: rgba(125, 211, 252, 0.12); color: {C.TEXT}; }}
        """ + _scrollbar_qss()

    # --------------------------------------------------------------------- ui
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.nav_back_btn = _icon_button("chevron_left", "Volver", size=44, icon_size=20)
        self.nav_back_btn.clicked.connect(self._go_home)
        self.nav_back_btn.setVisible(False)
        top.addWidget(self.nav_back_btn)
        self.home_btn = _icon_button("home", "Inicio (recomendados)", size=44, icon_size=19)
        self.home_btn.clicked.connect(self._go_home)
        top.addWidget(self.home_btn)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("YtSearch")
        self.search_input.setPlaceholderText("Buscar en YouTube")
        self.search_input.returnPressed.connect(self._do_search)
        top.addWidget(self.search_input, stretch=1)
        self.search_btn = _icon_button("search", "Buscar", size=44, icon_size=19, accent=True)
        self.search_btn.clicked.connect(self._do_search)
        top.addWidget(self.search_btn)
        root.addLayout(top)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, stretch=1)

        # ---- grid page ----
        grid_page = QWidget()
        gp = QVBoxLayout(grid_page)
        gp.setContentsMargins(0, 0, 0, 0)
        gp.setSpacing(10)
        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        self.tab_foryou = self._make_tab("Para ti", "home")
        self.tab_trending = self._make_tab("Tendencias", "trending")
        head_row.addWidget(self.tab_foryou)
        head_row.addWidget(self.tab_trending)
        head_row.addStretch()
        self.status = QLabel("")
        self.status.setObjectName("YtStatus")
        head_row.addWidget(self.status)
        gp.addLayout(head_row)
        # Hidden label kept for compatibility with result headers
        self.header_lbl = QLabel("")
        self.header_lbl.setObjectName("YtHeader")
        self.header_lbl.setVisible(False)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setObjectName("YtScroll")
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_scroll.viewport().setStyleSheet("background: transparent;")
        self.grid_host = QWidget()
        self.grid_host.setStyleSheet("background: transparent;")
        self.flow = FlowLayout(self.grid_host, margin=2)
        self.grid_scroll.setWidget(self.grid_host)
        gp.addWidget(self.grid_scroll, stretch=1)
        self.stack.addWidget(grid_page)

        # ---- player page ----
        player_page = QWidget()
        pp = QVBoxLayout(player_page)
        pp.setContentsMargins(0, 0, 0, 0)
        pp.setSpacing(8)

        # --- video at the top (16:9, centered; height via _size_video) ---
        self.video_surface = QWidget()
        self.video_surface.setObjectName("YtSurface")
        self.video_surface.setStyleSheet("QWidget#YtSurface { background: #000000; border-radius: 12px; }")
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.video_surface.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        surf_lay = QVBoxLayout(self.video_surface)
        surf_lay.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QLabel("Selecciona un vídeo")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color: rgba(203,213,225,0.45); font-size: 13px; background: transparent;")
        surf_lay.addWidget(self.placeholder)
        self.video_box = _AspectVideo(self.video_surface)
        self.video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.video_box.setMinimumHeight(200)

        video_holder = QVBoxLayout()
        video_holder.setContentsMargins(0, 0, 0, 0)
        video_holder.setSpacing(0)
        video_holder.addWidget(self.video_box, 0, Qt.AlignmentFlag.AlignHCenter)
        self._left_col = video_holder
        pp.addLayout(video_holder)

        # --- YouTube-style controls overlaid on the video (shown on hover) ---
        self._pc = _PanelControls(self, self.video_box)
        self.play_btn = self._pc.play_btn
        self.seek = self._pc.seek
        self.time_lbl = self._pc.time_lbl
        self.volume = self._pc.volume
        self.like_btn = self._pc.like_btn
        self.download_btn = self._pc.download_btn
        self.float_btn = self._pc.float_btn
        self.fullscreen_btn = self._pc.fullscreen_btn
        self.like_btn.setToolTip("Me gusta (en tu cuenta de YouTube)")
        for _b in (self.play_btn, self.seek, self.volume, self.like_btn,
                   self.download_btn, self.float_btn, self.fullscreen_btn):
            _b.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_play)
        self.seek.sliderPressed.connect(lambda: setattr(self, "_user_dragging", True))
        self.seek.sliderReleased.connect(self._on_seek_released)
        self.volume.valueChanged.connect(self._on_volume)
        self.like_btn.clicked.connect(self._toggle_like)
        self.download_btn.clicked.connect(self._download_current)
        self.float_btn.clicked.connect(self._toggle_floating_video)
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen_video)

        # --- scrollable content: title, description, comments ---
        self.watch_scroll = QScrollArea()
        self.watch_scroll.setObjectName("YtScroll")
        self.watch_scroll.setWidgetResizable(True)
        self.watch_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.watch_scroll.viewport().setStyleSheet("background: transparent;")
        watch = QWidget()
        watch.setStyleSheet("background: transparent;")
        wl = QVBoxLayout(watch)
        wl.setContentsMargins(2, 6, 8, 4)
        wl.setSpacing(12)

        self.title_lbl = QLabel("—")
        self.title_lbl.setObjectName("YtTitle")
        self.title_lbl.setWordWrap(True)
        wl.addWidget(self.title_lbl)
        self.channel_lbl = QLabel("")
        self.channel_lbl.setObjectName("YtChannel")
        wl.addWidget(self.channel_lbl)

        # description card
        self.desc_card = QFrame()
        self.desc_card.setObjectName("YtDesc")
        self.desc_card.setStyleSheet(
            "QFrame#YtDesc { background: rgba(255,255,255,0.04);"
            " border: 1px solid rgba(255,255,255,0.07); border-radius: 12px; }"
            "QLabel { background: transparent; }"
        )
        dcl = QVBoxLayout(self.desc_card)
        dcl.setContentsMargins(14, 12, 14, 12)
        dcl.setSpacing(6)
        self.desc_meta = QLabel("")
        self.desc_meta.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: 700;")
        dcl.addWidget(self.desc_meta)
        self.desc_text = QLabel("")
        self.desc_text.setWordWrap(True)
        self.desc_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc_text.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
        dcl.addWidget(self.desc_text)
        self.desc_toggle = QPushButton("Mostrar más")
        self.desc_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.desc_toggle.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {C.PRI}; border: none;"
            " padding: 2px 0; font-size: 11px; font-weight: 800; text-align: left; }"
        )
        self.desc_toggle.clicked.connect(self._toggle_description)
        self.desc_toggle.setVisible(False)
        dcl.addWidget(self.desc_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        wl.addWidget(self.desc_card)

        # comments
        self.comments_header = QLabel("Comentarios")
        self.comments_header.setStyleSheet(f"color: {C.TEXT}; font-size: 14px; font-weight: 800;")
        wl.addWidget(self.comments_header)
        self.comments_host = QWidget()
        self.comments_host.setStyleSheet("background: transparent;")
        self.comments_layout = QVBoxLayout(self.comments_host)
        self.comments_layout.setContentsMargins(0, 0, 0, 0)
        self.comments_layout.setSpacing(14)
        self.comments_layout.addStretch()
        wl.addWidget(self.comments_host)
        wl.addStretch()

        self.watch_scroll.setWidget(watch)
        pp.addWidget(self.watch_scroll, stretch=1)

        self.stack.addWidget(player_page)

    # ------------------------------------------------------------- feeds/grid
    def _make_tab(self, label: str, key: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, k=key: self._select_feed(k))
        btn.setStyleSheet(self._tab_style())
        return btn

    @staticmethod
    def _tab_style() -> str:
        return f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.04);
                color: {C.TEXT_MED};
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 16px;
                padding: 7px 16px;
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: rgba(125, 211, 252, 0.12); color: {C.TEXT}; }}
            QPushButton:checked {{
                background: rgba(56, 189, 248, 0.18);
                color: #EAF8FF;
                border-color: rgba(125, 211, 252, 0.40);
            }}
        """

    def _load_initial_feed(self):
        try:
            from actions.youtube_player import is_authenticated
            authed = is_authenticated()
        except Exception:
            authed = False
        self._select_feed("home" if authed else "trending")

    def _select_feed(self, key: str):
        self._feed = key
        self.tab_foryou.setChecked(key == "home")
        self.tab_trending.setChecked(key == "trending")
        self.stack.setCurrentIndex(0)
        self.load_feed()

    def load_feed(self):
        self._loaded_feed = True
        feed = self._feed
        self.status.setText("Cargando…")

        def worker():
            results = None
            error = ""
            try:
                if feed == "home":
                    from actions.youtube_player import fetch_subscriptions_feed
                    results = fetch_subscriptions_feed(limit=24)
                    if not results:
                        from actions.youtube_player import fetch_recommended
                        results = fetch_recommended(limit=24)
                else:
                    from actions.youtube_player import fetch_recommended
                    results = fetch_recommended(limit=24)
            except Exception as exc:
                error = str(exc)
            self._results_sig.emit(results, error, feed)

        threading.Thread(target=worker, daemon=True).start()

    def _do_search(self):
        query = self.search_input.text().strip()
        if not query:
            return
        self.tab_foryou.setChecked(False)
        self.tab_trending.setChecked(False)
        self.stack.setCurrentIndex(0)
        self.status.setText("Buscando…")

        def worker():
            results = None
            error = ""
            try:
                from actions.youtube_player import search_videos
                results = search_videos(query, limit=24)
            except Exception as exc:
                error = str(exc)
            self._results_sig.emit(results, error, "search")

        threading.Thread(target=worker, daemon=True).start()

    def _go_home(self):
        self.nav_back_btn.setVisible(False)
        self.stack.setCurrentIndex(0)
        if not self._loaded_feed:
            self._load_initial_feed()

    def _clear_flow(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()

    def _apply_results(self, results, error: str, header: str):
        self.header_lbl.setText(header)
        if results is None:
            self.status.setText(f"Error: {error}" if error else "No se pudo cargar.")
            return
        self._clear_flow()
        self._thumb_targets.clear()
        self._by_id = {v["id"]: v for v in results}
        self._ordered_ids = [v["id"] for v in results]
        if not results:
            self.status.setText("Sin resultados.")
            return
        for video in results:
            card = _VideoCard(video)
            card.activated.connect(self._play_video_by_id)
            self._thumb_targets.setdefault(video["id"], []).append(card.thumb_label())
            self.flow.addWidget(card)
            self._request_thumb(video["id"])
        self.status.setText(f"{len(results)} vídeos")

    # -------------------------------------------------------------- thumbnails
    def _request_thumb(self, vid: str):
        if not vid:
            return
        if vid in self._thumb_cache:
            self._apply_thumb(vid, self._thumb_cache[vid])
            return
        if vid in self._thumb_loading or self._thumb_executor_closed:
            return
        self._thumb_loading.add(vid)

        def worker():
            raw = b""
            try:
                import requests
                resp = requests.get(f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg", timeout=8)
                if resp.ok:
                    raw = resp.content
            except Exception:
                raw = b""
            self._thumb_sig.emit(vid, raw)

        try:
            self._thumb_executor.submit(worker)
        except RuntimeError:
            pass

    def _apply_thumb(self, vid: str, raw):
        self._thumb_loading.discard(vid)
        if not raw:
            return
        self._thumb_cache[vid] = bytes(raw)
        base = QPixmap()
        if not base.loadFromData(bytes(raw)):
            return
        for label in list(self._thumb_targets.get(vid, [])):
            try:
                w = label.width() or 248
                h = label.height() or 140
                scaled = base.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                     Qt.TransformationMode.SmoothTransformation)
                if scaled.width() > w or scaled.height() > h:
                    x = max(0, (scaled.width() - w) // 2)
                    y = max(0, (scaled.height() - h) // 2)
                    scaled = scaled.copy(x, y, w, h)
                label.setPixmap(scaled)
            except RuntimeError:
                try:
                    self._thumb_targets[vid].remove(label)
                except (KeyError, ValueError):
                    pass

    # ---------------------------------------------------------------- playback
    def _play_video_by_id(self, vid: str):
        video = self._by_id.get(vid) or {"id": vid, "title": vid, "channel": ""}
        self._play_video(video)

    def _play_video(self, video: dict):
        self._current = dict(video)
        self._liked = False
        self.like_btn.set_liked(False)
        for _b in (self.like_btn, self.download_btn, self.float_btn,
                   self.fullscreen_btn, self.play_btn, self.seek, self.volume):
            _b.setEnabled(True)
        self.title_lbl.setText(video.get("title", ""))
        self.channel_lbl.setText(video.get("channel", ""))
        self.placeholder.hide()
        if self._float_overlay is not None:
            self._float_overlay.set_meta(video.get("title", ""), video.get("channel", ""))
        if self._detached_mode is None:
            self.nav_back_btn.setVisible(True)
            self.stack.setCurrentIndex(1)
            QTimer.singleShot(0, self._size_video)

        if self._player is None:
            wid = int(self.video_surface.winId())
            from actions.youtube_player import EmbeddedVideoPlayer
            self._player = EmbeddedVideoPlayer(wid)

        vid = video["id"]
        url = f"https://www.youtube.com/watch?v={vid}"
        player = self._player
        volume = self.volume.value()

        def worker():
            ok = False
            try:
                ok = player.play(url)
                if ok:
                    player.set_volume(volume)
            except Exception:
                ok = False
            self._play_sig.emit(vid, ok)

        threading.Thread(target=worker, daemon=True).start()
        self._start_poller()
        self._load_comments(vid)
        self._load_details(vid)

    def _size_video(self):
        if self._detached_mode is not None:
            return
        box = getattr(self, "video_box", None)
        if box is None:
            return
        avail_w = self.width() - 40
        # controls are overlaid on the video now, so it can be larger; keep room
        # for the title + description/comments below.
        max_h = int(self.height() * 0.74)
        if avail_w <= 0 or max_h <= 0:
            return
        w = avail_w
        h = int(w * 9 / 16)
        if h > max_h:
            h = max_h
            w = int(h * 16 / 9)
        box.setFixedSize(max(240, w), max(135, h))

    def _load_details(self, vid: str):
        self._set_description("", "")
        self.desc_text.setText("Cargando descripción…")

        def worker():
            details = None
            error = ""
            try:
                from actions.youtube_player import fetch_video_details
                details = fetch_video_details(vid)
            except Exception as exc:
                error = str(exc)
            self._details_sig.emit(vid, details, error)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_details(self, vid: str, details, error: str):
        if vid != self._current.get("id"):
            return
        if not details:
            self._set_description("", "Descripción no disponible")
            return
        if details.get("title"):
            self.title_lbl.setText(details["title"])
            self._current["title"] = details["title"]
        if details.get("channel"):
            self.channel_lbl.setText(details["channel"])
        self._set_description(details.get("description", ""), self._details_meta(details))

    @staticmethod
    def _details_meta(details: dict) -> str:
        parts = []
        views = details.get("views")
        if views:
            try:
                parts.append(f"{int(views):,} vistas")
            except (TypeError, ValueError):
                pass
        published = str(details.get("publishedAt", ""))
        if published:
            parts.append(published[:10])
        return "  ·  ".join(parts)

    def _set_description(self, text: str, meta: str):
        self._desc_full = str(text or "")
        self._desc_expanded = False
        self.desc_meta.setText(meta or "")
        self.desc_meta.setVisible(bool(meta))
        self._apply_desc_collapsed()

    def _apply_desc_collapsed(self):
        full = self._desc_full
        if not full:
            self.desc_text.setText("Sin descripción.")
            self.desc_toggle.setVisible(False)
            return
        limit = 280
        if len(full) <= limit:
            self.desc_text.setText(full)
            self.desc_toggle.setVisible(False)
            return
        if self._desc_expanded:
            self.desc_text.setText(full)
            self.desc_toggle.setText("Mostrar menos")
        else:
            self.desc_text.setText(full[:limit].rstrip() + "…")
            self.desc_toggle.setText("Mostrar más")
        self.desc_toggle.setVisible(True)

    def _toggle_description(self):
        self._desc_expanded = not self._desc_expanded
        self._apply_desc_collapsed()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._size_video()

    def _load_comments(self, vid: str):
        self._clear_comments()
        self.comments_header.setText("Comentarios")
        loading = QLabel("Cargando comentarios…")
        loading.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
        self.comments_layout.insertWidget(0, loading)

        def worker():
            comments = None
            error = ""
            try:
                from actions.youtube_player import fetch_comments
                comments = fetch_comments(vid, limit=30)
            except Exception as exc:
                error = str(exc)
            self._comments_sig.emit(vid, comments, error)

        threading.Thread(target=worker, daemon=True).start()

    def _clear_comments(self):
        while self.comments_layout.count():
            item = self.comments_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self.comments_layout.addStretch()

    def _apply_comments(self, vid: str, comments, error: str):
        if vid != self._current.get("id"):
            return
        self._clear_comments()
        if comments is None:
            note = QLabel("Comentarios no disponibles para este vídeo.")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
            self.comments_layout.insertWidget(0, note)
            if error:
                self._log(f"ERR: comentarios YouTube — {error[:140]}")
            return
        self.comments_header.setText(f"Comentarios · {len(comments)}")
        if not comments:
            note = QLabel("Sin comentarios.")
            note.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 11px;")
            self.comments_layout.insertWidget(0, note)
            return
        for index, comment in enumerate(comments):
            self.comments_layout.insertWidget(index, self._make_comment_widget(comment))

    def _make_comment_widget(self, comment: dict) -> QWidget:
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        author = QLabel(str(comment.get("author", "")))
        author.setStyleSheet(f"color: {C.TEXT_DIM}; font-size: 11px; font-weight: 700;")
        lay.addWidget(author)
        text = QLabel(str(comment.get("text", "")))
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.setStyleSheet(f"color: {C.TEXT}; font-size: 12px;")
        lay.addWidget(text)
        likes = int(comment.get("likes") or 0)
        if likes > 0:
            meta = QLabel(f"♥ {likes:,}")
            meta.setStyleSheet(f"color: {C.TEXT_MED}; font-size: 10px;")
            lay.addWidget(meta)
        return box

    def _apply_play_started(self, vid: str, ok: bool):
        if vid != self._current.get("id"):
            return
        if ok:
            self.play_btn.set_shape(_MediaBtn.PAUSE)
            if self._float_overlay is not None:
                self._float_overlay.set_playing(True)
        else:
            self.placeholder.setText("No se pudo reproducir (¿mpv disponible?)")
            self.placeholder.show()

    def _toggle_play(self):
        if self._player is not None:
            self._player.toggle()

    def pause_playback(self):
        if self._player is not None:
            try:
                self._player.pause()
            except Exception:
                pass

    def _on_volume(self, value: int):
        if self._player is not None:
            self._player.set_volume(value)

    def _on_seek_released(self):
        self._user_dragging = False
        if self._player is None or self._duration <= 0:
            return
        frac = self.seek.value() / 1000.0
        self._player.seek_abs(frac * self._duration)

    def _start_poller(self):
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()

        def loop():
            while not self._poll_stop.is_set():
                player = self._player
                if player is not None and player.is_running():
                    pos = player.position()
                    dur = player.duration()
                    paused = player.paused()
                    if pos is not None:
                        playing = (not bool(paused)) if paused is not None else True
                        self._pos_sig.emit(float(pos), float(dur or 0.0), playing)
                time.sleep(0.6)

        self._poll_thread = threading.Thread(target=loop, daemon=True)
        self._poll_thread.start()

    def _apply_position(self, pos: float, dur: float, playing: bool):
        self._duration = dur
        self.play_btn.set_shape(_MediaBtn.PAUSE if playing else _MediaBtn.PLAY)
        if self._float_overlay is not None:
            self._float_overlay.set_playing(playing)
        if not self._user_dragging and dur > 0:
            self.seek.setValue(int(max(0.0, min(1.0, pos / dur)) * 1000))
        self.time_lbl.setText(f"{self._fmt_clock(pos)} / {self._fmt_clock(dur)}")

    @staticmethod
    def _fmt_clock(seconds: float) -> str:
        try:
            total = int(seconds or 0)
        except Exception:
            return "0:00"
        if total <= 0:
            return "0:00"
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # -------------------------------------------------------------------- like
    def _toggle_like(self):
        vid = self._current.get("id")
        if not vid:
            return
        desired = not self._liked
        self.like_btn.setEnabled(False)

        def worker():
            error = ""
            try:
                from actions.youtube_player import rate_video
                rate_video(vid, "like" if desired else "none")
            except Exception as exc:
                error = str(exc)
            self._like_sig.emit(vid, desired, error)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_like(self, vid: str, liked: bool, error: str):
        self.like_btn.setEnabled(True)
        if vid != self._current.get("id"):
            return
        if error:
            self._log(f"ERR: YouTube like — {error[:140]}")
            return
        self._liked = liked
        self.like_btn.set_liked(liked)

    # ----------------------------------------------------------------- actions
    def _download_current(self):
        vid = self._current.get("id")
        if not vid:
            return
        url = f"https://www.youtube.com/watch?v={vid}"

        def worker():
            try:
                from actions.youtube_video import download_video
                download_video(url, quality="best", progress_hook=self._progress_hook)
            except Exception as exc:
                self._log(f"ERR: descarga YouTube — {str(exc)[:140]}")

        threading.Thread(target=worker, daemon=True).start()

    def _open_external(self):
        vid = self._current.get("id")
        if vid:
            QDesktopServices.openUrl(QUrl(f"https://www.youtube.com/watch?v={vid}"))

    # ------------------------------------------------------- fullscreen / float
    def is_floating(self) -> bool:
        return self._detached_mode == "floating"

    def _toggle_fullscreen_video(self):
        if self._detached_mode == "fullscreen":
            self._reattach_video()
        else:
            self._detach_video("fullscreen")

    def _toggle_floating_video(self):
        if self._detached_mode == "floating":
            self._reattach_video()
        else:
            self._detach_video("floating")

    def _detach_video(self, mode: str):
        if self.video_box is None:
            return
        if self._detached_mode is not None:
            self._reattach_video()

        win = _DetachWindow(self._reattach_video)
        win.setWindowTitle("JARVIS — YouTube")
        win.setStyleSheet("background: #000000;")
        wlay = QVBoxLayout(win)
        wlay.setContentsMargins(0, 0, 0, 0)
        wlay.setSpacing(0)

        self._left_col.removeWidget(self.video_box)
        # In a detached window the video should FILL it, so drop the panel's fixed 16:9 size.
        self.video_box.setMinimumSize(0, 0)
        self.video_box.setMaximumSize(16777215, 16777215)
        self.video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        wlay.addWidget(self.video_box, stretch=1)

        hint = QLabel(
            "Reproduciendo en pantalla completa" if mode == "fullscreen"
            else "Reproduciendo en ventana flotante"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(
            "color: rgba(203,213,225,0.5); font-size: 13px;"
            "background: rgba(8,14,25,0.4); border: 1px solid rgba(125,211,252,0.10);"
            "border-radius: 12px;"
        )
        self._left_col.insertWidget(0, hint, stretch=1)
        self._detached_hint = hint
        self._detached_mode = mode

        overlay = _FloatOverlay({
            "toggle": self._toggle_play,
            "prev": self._prev_video,
            "next": self._next_video,
            "rewind": self._rewind_video,
            "forward": self._forward_video,
            "restore": self._reattach_video,
            "moved": lambda p: self._float_window.move(p) if self._float_window else None,
            "resized": lambda w, h: self._float_window.resize(w, h) if self._float_window else None,
        }, draggable=(mode == "floating"), resizable=(mode == "floating"))
        self._float_overlay = overlay
        overlay.set_meta(self._current.get("title", ""), self._current.get("channel", ""))

        if mode == "fullscreen":
            self._fs_window = win
            self.fullscreen_btn.setIcon(_line_icon("fullscreen_exit", C.PRI, 18))
            win.showFullScreen()
            screen = win.screen() or QApplication.primaryScreen()
            overlay.setGeometry(screen.geometry())
            for target in (win, overlay):
                shortcut = QShortcut(QKeySequence("Escape"), target)
                shortcut.activated.connect(self._reattach_video)
        else:
            self._float_window = win
            self.float_btn.setIcon(_line_icon("pip", C.PRI, 18))
            win.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            win.setMinimumSize(320, 180)
            win.resize(480, 270)
            try:
                geo = QApplication.primaryScreen().availableGeometry()
                pos = QPoint(geo.right() - 500, geo.bottom() - 310)
            except Exception:
                pos = QPoint(80, 80)
            win.move(pos)
            win.show()
            overlay.setMinimumSize(320, 180)
            overlay.resize(480, 270)
            overlay.move(pos)

        overlay.show()
        overlay.raise_()

    def _reattach_video(self):
        if self._detached_mode is None:
            return
        self._detached_mode = None
        win = self._fs_window or self._float_window
        overlay = self._float_overlay
        self._fs_window = None
        self._float_window = None
        self._float_overlay = None
        hint = getattr(self, "_detached_hint", None)
        if hint is not None:
            self._left_col.removeWidget(hint)
            hint.deleteLater()
            self._detached_hint = None
        # Reparent the video back to the panel BEFORE the window dies (avoids
        # destroying the shared mpv surface, which would crash).
        if win is not None and win.layout() is not None:
            win.layout().removeWidget(self.video_box)
        self._left_col.insertWidget(0, self.video_box, 0, Qt.AlignmentFlag.AlignHCenter)
        if overlay is not None:
            overlay.close()
            overlay.deleteLater()
        if win is not None:
            win.close()
            win.deleteLater()
        self.fullscreen_btn.setIcon(_line_icon("fullscreen", C.TEXT_DIM, 18))
        self.float_btn.setIcon(_line_icon("pip", C.TEXT_DIM, 18))
        QTimer.singleShot(0, self._size_video)

    def _prev_video(self):
        if not self._ordered_ids:
            return
        current = self._current.get("id")
        try:
            idx = self._ordered_ids.index(current)
        except ValueError:
            idx = 0
        prev = self._ordered_ids[(idx - 1) % len(self._ordered_ids)]
        video = self._by_id.get(prev) or {"id": prev, "title": prev, "channel": ""}
        self._play_video(video)

    def _next_video(self):
        if not self._ordered_ids:
            return
        current = self._current.get("id")
        try:
            idx = self._ordered_ids.index(current)
        except ValueError:
            idx = -1
        nxt = self._ordered_ids[(idx + 1) % len(self._ordered_ids)]
        video = self._by_id.get(nxt) or {"id": nxt, "title": nxt, "channel": ""}
        self._play_video(video)

    def _forward_video(self):
        if self._player is not None:
            self._player.seek_rel(10)

    def _rewind_video(self):
        if self._player is not None:
            self._player.seek_rel(-10)

    # ----------------------------------------------------------------- helpers
    def _log(self, text: str):
        win = self.window()
        if hasattr(win, "_log_sig"):
            try:
                win._log_sig.emit(text)
            except Exception:
                pass

    def _shutdown(self):
        self._poll_stop.set()
        pc = getattr(self, "_pc", None)
        if pc is not None:
            try:
                pc._timer.stop()
                pc.close()
            except Exception:
                pass
        for win in (self._fs_window, self._float_window, self._float_overlay):
            if win is not None:
                try:
                    win.close()
                except Exception:
                    pass
        if not self._thumb_executor_closed:
            self._thumb_executor_closed = True
            self._thumb_executor.shutdown(wait=False, cancel_futures=True)
        if self._player is not None:
            self._player.shutdown()
            self._player = None


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _playback_sig = pyqtSignal(dict)
    _playback_like_sig = pyqtSignal(str, bool, str)
    _download_sig = pyqtSignal(dict)
    _whatsapp_chat_sig = pyqtSignal(str)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S — MARK XXXIX")
        self.setWindowIcon(_build_app_icon())
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command  = None
        self.on_download_cancel = None
        self._muted           = False
        self._compact         = False
        self._current_file: str | None = None
        self._mode_combo: QComboBox | None = None
        self._mode_buttons: dict[str, QPushButton] = {}
        self._whatsapp_unread_badge: QLabel | None = None
        self._active_mode = "Normal"
        self._whatsapp_panel: WhatsAppWindow | None = None
        self._whatsapp_picker: WhatsAppModePicker | None = None
        self._gmail_panel: GmailModePanel | None = None
        self._drive_panel: DriveModePanel | None = None
        self._music_panel: QWidget | None = None
        self._youtube_panel: QWidget | None = None

        central = QWidget()
        central.setObjectName("AppRoot")
        central.setStyleSheet(f"""
            QWidget#AppRoot {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #060912, stop:0.52 #0A0F1B, stop:1 #0B1422);
                color: {C.TEXT};
                font-family: "{FONT_UI}", "{FONT_UI_FALLBACK}";
            }}
        """)
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 10)
        body.setSpacing(12)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        self.hud = HudCanvas(face_path)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._center_stack = QStackedWidget()
        self._center_stack.setObjectName("Workspace")
        self._center_stack.setStyleSheet("""
            QStackedWidget#Workspace {
                background: rgba(6, 12, 22, 0.72);
                border: 1px solid rgba(125, 211, 252, 0.10);
                border-radius: 12px;
            }
        """)
        self._center_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._center_stack.addWidget(self.hud)
        body.addWidget(self._center_stack, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        # Playback bar sits above footer
        root.addWidget(self._build_playback_bar())
        root.addWidget(self._build_footer())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Timer de actualización de métricas
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._whatsapp_badge_timer = QTimer(self)
        self._whatsapp_badge_timer.timeout.connect(self._update_whatsapp_unread_badge)
        self._whatsapp_badge_timer.start(1500)

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._playback_sig.connect(self._apply_playback)
        self._playback_like_sig.connect(self._apply_playback_like)
        self._download_sig.connect(self._apply_download_state)
        self._whatsapp_chat_sig.connect(self._open_whatsapp)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        self._wa_win: WhatsAppWindow | None = None

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        
        # Playback state and external callback
        self._play_title = ""
        self._play_artists = ""
        self._play_duration = 0
        self._play_position = 0
        self._play_playing = False
        self._play_video_id = ""
        self._play_liked = False
        self._play_position_anchor = 0.0
        self._play_position_anchor_ts = 0.0
        self._user_dragging = False   # True while user is dragging the seek slider
        self._music_volume_level = 55
        self._music_volume_restore = 55
        self._music_duck_target = 42
        self._music_duck_floor = 34
        self._music_duck_step = 2
        self._music_duck_active = False
        self._music_duck_should_restore = False
        self._music_duck_timer = QTimer(self)
        self._music_duck_timer.setInterval(60)
        self._music_duck_timer.timeout.connect(self._step_music_duck)
        self._playback_anim_timer = QTimer(self)
        self._playback_anim_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._playback_anim_timer.setInterval(16)
        self._playback_anim_timer.timeout.connect(self._tick_playback_progress)
        self._playback_anim_timer.start()
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.timeout.connect(self._on_seek)
        self.on_playback_command = None  # callback: fn(action, params)

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_compact(self):
        self._compact = not self._compact
        self._left_panel.setVisible(not self._compact)
        self._center_stack.setVisible(not self._compact)
        if self._compact:
            self._right_panel.setVisible(True)
            self._compact_btn.setIcon(_line_icon("panel_open", C.TEXT_DIM, 18))
            self._compact_btn.setToolTip("Modo completo")
            self.setMinimumWidth(_RIGHT_W + 32)
            self.resize(_RIGHT_W + 32, self.height())
        else:
            self._right_panel.setVisible(self._active_mode != "WhatsApp")
            self._compact_btn.setIcon(_line_icon("panel_close", C.TEXT_DIM, 18))
            self._compact_btn.setToolTip("Modo compacto")
            self.setMinimumWidth(_MIN_W)
            self.resize(_DEFAULT_W, self.height())

    def _toggle_file_zone(self):
        visible = self._drop_zone.isVisible()
        self._drop_zone.setVisible(not visible)
        self._file_hint.setVisible(not visible)
        icon_name = "chevron_up" if not visible else "chevron_down"
        self._file_toggle_btn.setIcon(_line_icon(icon_name, C.PRI, 16))

    def _open_whatsapp(self, contact: str = ""):
        try:
            if isinstance(contact, bool):
                contact = ""
            contact = str(contact or "").strip()
            self._set_mode_combo("WhatsApp")
            mgr = getattr(self, 'whatsapp_manager', None)
            if self._whatsapp_panel is not None:
                self._center_stack.removeWidget(self._whatsapp_panel)
                self._whatsapp_panel.deleteLater()
            self._whatsapp_panel = WhatsAppWindow(manager=mgr, contact=contact, embedded=True, parent=self)
            self._whatsapp_panel.close_requested.connect(self._close_whatsapp_mode)
            self._center_stack.addWidget(self._whatsapp_panel)
            self._center_stack.setCurrentWidget(self._whatsapp_panel)
            self._center_stack.setVisible(not self._compact)
            self._right_panel.setVisible(self._compact)
        except Exception as e:
            QMessageBox.warning(self, 'Error', f'No se pudo abrir WhatsApp UI: {e}')

    def _close_whatsapp_mode(self):
        try:
            self._show_normal_mode()
            if self._whatsapp_panel is not None:
                panel = self._whatsapp_panel
                self._whatsapp_panel = None
                self._center_stack.removeWidget(panel)
                panel.deleteLater()
        except Exception:
            pass

    def _set_mode_combo(self, mode: str):
        self._active_mode = mode
        if self._mode_combo is not None:
            self._mode_combo.blockSignals(True)
            idx = self._mode_combo.findText(mode)
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)
            self._mode_combo.blockSignals(False)
        for key, button in self._mode_buttons.items():
            button.setChecked(key == mode)
        mode_copy = {
            "Normal": ("Inicio", "Núcleo de voz"),
            "WhatsApp": ("WhatsApp", "Conversaciones"),
            "Gmail": ("Correo", "Bandeja de entrada"),
            "Drive": ("Drive", "Archivos en la nube"),
            "Music": ("Música", "Biblioteca y reproducción"),
            "YouTube": ("YouTube", "Vídeos y reproducción"),
        }
        title, context = mode_copy.get(mode, (mode, "Espacio de trabajo"))
        if hasattr(self, "_header_mode_label"):
            self._header_mode_label.setText(title)
        if hasattr(self, "_header_context_label"):
            self._header_context_label.setText(context)

    def _show_normal_mode(self):
        self._set_mode_combo("Normal")
        self._center_stack.setCurrentWidget(self.hud)
        self._center_stack.setVisible(not self._compact)
        self._right_panel.setVisible(True)

    def _show_whatsapp_picker(self):
        self._open_whatsapp("")

    def _show_gmail_mode(self):
        self._set_mode_combo("Gmail")
        self._right_panel.setVisible(True)
        if self._gmail_panel is None:
            self._gmail_panel = GmailModePanel(parent=self)
            self._center_stack.addWidget(self._gmail_panel)
        self._center_stack.setCurrentWidget(self._gmail_panel)
        self._center_stack.setVisible(not self._compact)

    def _show_drive_mode(self):
        self._set_mode_combo("Drive")
        self._right_panel.setVisible(True)
        if self._drive_panel is None:
            self._drive_panel = DriveModePanel(progress_hook=self._download_sig.emit, parent=self)
            self._center_stack.addWidget(self._drive_panel)
        self._center_stack.setCurrentWidget(self._drive_panel)
        self._center_stack.setVisible(not self._compact)

    def _show_music_mode(self):
        self._set_mode_combo("Music")
        self._right_panel.setVisible(True)
        if self._music_panel is None:
            self._music_panel = MusicModePanelV2(parent=self)
            self._center_stack.addWidget(self._music_panel)
        self._center_stack.setCurrentWidget(self._music_panel)
        self._center_stack.setVisible(not self._compact)

    def _show_youtube_mode(self):
        self._set_mode_combo("YouTube")
        self._right_panel.setVisible(True)
        if self._youtube_panel is None:
            self._youtube_panel = YouTubeModePanel(progress_hook=self._download_sig.emit, parent=self)
            self._center_stack.addWidget(self._youtube_panel)
        self._center_stack.setCurrentWidget(self._youtube_panel)
        self._center_stack.setVisible(not self._compact)

    def _on_mode_change(self, mode: str):
        if (mode != "YouTube" and self._youtube_panel is not None
                and not self._youtube_panel.is_floating()):
            self._youtube_panel.pause_playback()
        if mode == "WhatsApp":
            if self._whatsapp_panel is not None:
                self._center_stack.setCurrentWidget(self._whatsapp_panel)
            else:
                self._open_whatsapp("")
        elif mode == "Gmail":
            self._show_gmail_mode()
        elif mode == "Drive":
            self._show_drive_mode()
        elif mode == "Music":
            self._show_music_mode()
        elif mode == "YouTube":
            self._show_youtube_mode()
        else:
            self._show_normal_mode()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            cw = self.centralWidget()
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setObjectName("AppHeader")
        w.setFixedHeight(66)
        w.setStyleSheet("""
            QWidget#AppHeader {
                background: rgba(5, 9, 16, 0.97);
                border-bottom: 1px solid rgba(125, 211, 252, 0.14);
            }
            QFrame#HeaderDivider {
                background: rgba(148, 163, 184, 0.18);
                border: none;
            }
        """)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 0, 16, 0)
        lay.setSpacing(16)

        brand_mark = QLabel()
        brand_mark.setFixedSize(40, 40)
        brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_mark.setPixmap(_build_app_icon().pixmap(38, 38))
        brand_mark.setStyleSheet("""
            background: transparent;
            border: none;
        """)
        lay.addWidget(brand_mark)

        brand = QVBoxLayout()
        brand.setSpacing(1)
        title = QLabel("J.A.R.V.I.S")
        title.setFont(QFont(FONT_UI, 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #54B9F3; background: transparent;")
        brand.addWidget(title)
        sub = QLabel("MARK XXXIX")
        sub.setFont(QFont(FONT_MONO, 8, QFont.Weight.DemiBold))
        sub.setStyleSheet("color: #74869C; background: transparent;")
        brand.addWidget(sub)
        lay.addLayout(brand)

        divider = QFrame()
        divider.setObjectName("HeaderDivider")
        divider.setFixedSize(1, 32)
        lay.addWidget(divider)

        workspace = QVBoxLayout()
        workspace.setSpacing(2)
        self._header_mode_label = QLabel("Inicio")
        self._header_mode_label.setFont(QFont(FONT_UI, 12, QFont.Weight.DemiBold))
        self._header_mode_label.setStyleSheet("color: #F8FAFC; background: transparent;")
        self._header_context_label = QLabel("Núcleo de voz")
        self._header_context_label.setFont(QFont(FONT_UI, 9))
        self._header_context_label.setStyleSheet("color: #7F91A8; background: transparent;")
        workspace.addWidget(self._header_mode_label)
        workspace.addWidget(self._header_context_label)
        lay.addLayout(workspace)
        lay.addStretch()

        self._header_state = QFrame()
        self._header_state.setObjectName("HeaderState")
        self._header_state.setFixedHeight(32)
        state_layout = QHBoxLayout(self._header_state)
        state_layout.setContentsMargins(9, 0, 10, 0)
        state_layout.setSpacing(7)
        self._header_state_dot = QLabel()
        self._header_state_dot.setFixedSize(7, 7)
        self._header_state_label = QLabel("ESCUCHANDO")
        self._header_state_label.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        state_layout.addWidget(self._header_state_dot)
        state_layout.addWidget(self._header_state_label)
        lay.addWidget(self._header_state)
        self._style_header_state("LISTENING")

        right_col = QVBoxLayout()
        right_col.setSpacing(0)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont(FONT_MONO, 12, QFont.Weight.DemiBold))
        self._clock_lbl.setStyleSheet("color: #FFFFFF; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont(FONT_UI, 7))
        self._date_lbl.setStyleSheet("color: #708096; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        self._compact_btn = _icon_button("panel_close", "Modo compacto", size=34, icon_size=17)
        self._compact_btn.clicked.connect(self._toggle_compact)
        lay.addWidget(self._compact_btn)
        return w

    def _style_header_state(self, state: str):
        state = str(state or "LISTENING").upper()
        palette = {
            "SPEAKING": ("HABLANDO", "#7DD3FC", "rgba(56, 189, 248, 0.11)"),
            "THINKING": ("PENSANDO", "#C4B5FD", "rgba(167, 139, 250, 0.11)"),
            "PROCESSING": ("PROCESANDO", "#FDE68A", "rgba(250, 204, 21, 0.09)"),
            "MUTED": ("SILENCIADO", "#FDA4AF", "rgba(244, 63, 94, 0.09)"),
            "LISTENING": ("ESCUCHANDO", "#A7F3D0", "rgba(52, 211, 153, 0.09)"),
        }
        label, color, background = palette.get(state, (state, "#7DD3FC", "rgba(56, 189, 248, 0.09)"))
        if not hasattr(self, "_header_state"):
            return
        self._header_state_label.setText(label)
        self._header_state_label.setStyleSheet(f"color: {color}; background: transparent;")
        self._header_state_dot.setStyleSheet(f"background: {color}; border: none; border-radius: 3px;")
        self._header_state.setStyleSheet(f"""
            QFrame#HeaderState {{
                background: {background};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }}
        """)

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("NavigationPanel")
        w.setFixedWidth(_LEFT_W)
        w.setStyleSheet("""
            QWidget#NavigationPanel {
                background: rgba(8, 14, 25, 0.86);
                border: 1px solid rgba(125, 211, 252, 0.10);
                border-radius: 12px;
            }
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 16, 12, 14)
        lay.setSpacing(6)

        hdr = QLabel("ESPACIOS")
        hdr.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; padding: 0 8px 6px 8px;")
        lay.addWidget(hdr)

        nav_items = [
            ("Normal", "home", "Inicio"),
            ("WhatsApp", "chat", "WhatsApp"),
            ("Gmail", "mail", "Correo"),
            ("Drive", "drive", "Drive"),
            ("Music", "music", "Música"),
            ("YouTube", "youtube", "YouTube"),
        ]
        for mode, icon_name, label in nav_items:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setIcon(_line_icon(icon_name, C.TEXT_DIM, 19))
            button.setIconSize(QSize(19, 19))
            button.setFixedHeight(42)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAccessibleName(f"Abrir {label}")
            button.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {C.TEXT_MED};
                    border: 1px solid transparent;
                    border-radius: 9px;
                    padding: 0 12px;
                    text-align: left;
                    font-size: 10px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    color: {C.TEXT};
                    background: rgba(255,255,255,0.045);
                }}
                QPushButton:checked {{
                    color: #EAF8FF;
                    background: rgba(56,189,248,0.13);
                    border-color: rgba(125,211,252,0.22);
                }}
                QPushButton:focus {{ border: 2px solid rgba(125,211,252,0.52); }}
            """)
            button.clicked.connect(lambda _checked=False, m=mode: self._on_mode_change(m))
            self._mode_buttons[mode] = button
            lay.addWidget(button)
            if mode == "WhatsApp":
                badge = QLabel("", button)
                badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                badge.setFixedSize(20, 20)
                badge.move(142, 11)
                badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                badge.setStyleSheet("""
                    QLabel {
                        color: #03111B;
                        background: #59C7FF;
                        border: 1px solid rgba(255, 255, 255, 0.45);
                        border-radius: 10px;
                        font-size: 9px;
                        font-weight: 800;
                    }
                """)
                badge.hide()
                self._whatsapp_unread_badge = badge
        self._mode_buttons["Normal"].setChecked(True)
        lay.addSpacing(12)

        metrics_title = QLabel("SISTEMA")
        metrics_title.setFont(QFont(FONT_UI, 8, QFont.Weight.Bold))
        metrics_title.setStyleSheet(f"color:{C.TEXT_MED}; padding:0 8px 4px 8px;")
        lay.addWidget(metrics_title)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.ACC)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#e57373") # Keep a distinct color for temp, it's a "warning" metric

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = QWidget()
        info_panel.setStyleSheet(
            "background: rgba(255,255,255,0.025); "
            "border: 1px solid rgba(255,255,255,0.06); border-radius: 9px;"
        )
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(10, 8, 10, 8)
        ip_lay.setSpacing(5)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self._uptime_lbl.setStyleSheet(f"color: {C.ACC}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont(FONT_UI, 9))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont(FONT_UI, 9))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addStretch()

        return w

    def _update_whatsapp_unread_badge(self):
        badge = self._whatsapp_unread_badge
        if badge is None:
            return
        manager = getattr(self, "whatsapp_manager", None)
        try:
            count = len(manager.list_pending()) if manager is not None else 0
        except Exception:
            count = 0
        if count <= 0:
            badge.hide()
            return
        badge.setText("99+" if count > 99 else str(count))
        badge.show()
        badge.raise_()

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("CommandPanel")
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet("""
            QWidget#CommandPanel {
                background: rgba(7, 14, 24, 0.90);
                border: 1px solid rgba(125, 211, 252, 0.10);
                border-radius: 12px;
            }
            QFrame#SideSurface {
                background: transparent;
                border: none;
            }
            QFrame#SectionDivider {
                background: rgba(125, 211, 252, 0.10);
                border: none;
            }
            QLabel#SideEyebrow {
                color: #7F91A8;
                font-size: 8px;
                font-weight: 700;
            }
            QLabel#SideTitle {
                color: #F8FAFC;
                font-size: 13px;
                font-weight: 700;
            }
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        activity = QFrame()
        activity.setObjectName("SideSurface")
        activity_layout = QVBoxLayout(activity)
        activity_layout.setContentsMargins(14, 14, 14, 12)
        activity_layout.setSpacing(8)
        activity_label = QLabel("SESIÓN")
        activity_label.setObjectName("SideEyebrow")
        activity_layout.addWidget(activity_label)
        activity_title = QLabel("Actividad")
        activity_title.setObjectName("SideTitle")
        activity_layout.addWidget(activity_title)
        self._log = LogWidget()
        activity_layout.addWidget(self._log, stretch=1)

        self._download_widget = DownloadWidget()
        self._download_widget.cancel_requested.connect(self._request_download_cancel)
        activity_layout.addWidget(self._download_widget)
        lay.addWidget(activity, stretch=1)

        divider_one = QFrame()
        divider_one.setObjectName("SectionDivider")
        divider_one.setFixedHeight(1)
        lay.addWidget(divider_one)

        utilities = QFrame()
        utilities.setObjectName("SideSurface")
        utility_layout = QVBoxLayout(utilities)
        utility_layout.setContentsMargins(14, 12, 14, 12)
        utility_layout.setSpacing(8)
        _fu_hdr = QHBoxLayout(); _fu_hdr.setContentsMargins(0, 0, 0, 0); _fu_hdr.setSpacing(4)
        _fu_lbl = QLabel("Archivos")
        _fu_lbl.setObjectName("SideTitle")
        self._file_toggle_btn = _icon_button(
            "chevron_down", "Mostrar u ocultar archivos", size=30, icon_size=16
        )
        self._file_toggle_btn.clicked.connect(self._toggle_file_zone)
        _fu_hdr.addWidget(_fu_lbl); _fu_hdr.addStretch(); _fu_hdr.addWidget(self._file_toggle_btn)
        utility_layout.addLayout(_fu_hdr)

        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        self._drop_zone.setVisible(False)
        utility_layout.addWidget(self._drop_zone)

        self._file_hint = QLabel("Ningún archivo seleccionado")
        self._file_hint.setFont(QFont(FONT_UI, 8))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        self._file_hint.setVisible(False)
        utility_layout.addWidget(self._file_hint)
        lay.addWidget(utilities)

        divider_two = QFrame()
        divider_two.setObjectName("SectionDivider")
        divider_two.setFixedHeight(1)
        lay.addWidget(divider_two)

        command = QFrame()
        command.setObjectName("SideSurface")
        command_layout = QVBoxLayout(command)
        command_layout.setContentsMargins(14, 12, 14, 14)
        command_layout.setSpacing(9)
        command_label = QLabel("JARVIS")
        command_label.setObjectName("SideEyebrow")
        command_layout.addWidget(command_label)
        command_title = QLabel("Enviar una orden")
        command_title.setObjectName("SideTitle")
        command_layout.addWidget(command_title)
        command_layout.addLayout(self._build_input_row())

        self._mute_btn = QPushButton("Micrófono activo")
        self._mute_btn.setIcon(_line_icon("mic", C.ACC, 18))
        self._mute_btn.setIconSize(QSize(18, 18))
        self._mute_btn.setFixedHeight(38)
        self._mute_btn.setAccessibleName("Alternar micrófono")
        self._mute_btn.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        command_layout.addWidget(self._mute_btn)

        fs_btn = QPushButton("Pantalla completa")
        fs_btn.setIcon(_line_icon("fullscreen", C.TEXT_DIM, 18))
        fs_btn.setIconSize(QSize(18, 18))
        fs_btn.setFixedHeight(38)
        fs_btn.setFont(QFont(FONT_UI, 9, QFont.Weight.DemiBold))
        fs_btn.setToolTip("Pantalla completa (F11)")
        fs_btn.setAccessibleName("Pantalla completa")
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.06); color: {C.TEXT_MED};
                border: 1px solid rgba(255, 255, 255, 0.11); border-radius: 10px;
            }}
            QPushButton:hover {{
                color: {C.TEXT}; background: rgba(255, 255, 255, 0.11); border: 1px solid rgba(125, 211, 252, 0.36);
            }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        command_layout.addWidget(fs_btn)
        lay.addWidget(command)

        return w

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = _CommandInput()
        self._input.setPlaceholderText("Escribe una orden o pregunta…")
        self._input.setFont(QFont(FONT_UI, 9))
        self._input.setFixedHeight(42)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: rgba(255, 255, 255, 0.035); color: {C.WHITE};
                border: 1px solid rgba(255, 255, 255, 0.10); border-radius: 10px; padding: 6px 12px;
            }}
            QLineEdit:focus {{ background: rgba(255, 255, 255, 0.060); border: 1px solid rgba(125, 211, 252, 0.35); }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = _icon_button("send", "Enviar comando", size=42, icon_size=19, accent=True)
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(30)
        w.setStyleSheet(
            "background: rgba(5, 9, 17, 0.94); "
            "border-top: 1px solid rgba(125, 211, 252, 0.08);"
        )
        lay = QHBoxLayout(w); lay.setContentsMargins(20, 0, 18, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont(FONT_UI, 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("F4  micrófono    F11  pantalla completa", C.TEXT_MED))
        lay.addStretch()
        signature = _fl("JCañas", C.PRI)
        signature.setFont(QFont(FONT_UI, 8, QFont.Weight.DemiBold))
        lay.addWidget(signature)
        return w

    def _build_playback_bar(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(72)
        w.setStyleSheet(
            "background: rgba(7, 13, 23, 0.96); "
            "border-top: 1px solid rgba(125, 211, 252, 0.12);"
        )
        w.setVisible(False)          # oculta hasta que suene música
        self._playback_bar = w
        lay = QHBoxLayout(w); lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(12)

        # Botones de control — dibujados con QPainter, sin emoji
        self._pb_prev = _MediaBtn(_MediaBtn.PREV)
        self._pb_prev.setToolTip("Anterior")
        self._pb_prev.setAccessibleName("Pista anterior")
        self._pb_play = _MediaBtn(_MediaBtn.PLAY)
        self._pb_play.setToolTip("Reproducir o pausar")
        self._pb_play.setAccessibleName("Reproducir o pausar")
        self._pb_next = _MediaBtn(_MediaBtn.NEXT)
        self._pb_next.setToolTip("Siguiente")
        self._pb_next.setAccessibleName("Pista siguiente")
        self._pb_like = _LikeBtn()
        self._pb_like.setToolTip("Marcar como Me gusta")
        self._pb_like.setAccessibleName("Cambiar Me gusta de la canción")

        ctrl = QHBoxLayout(); ctrl.setSpacing(8)
        ctrl.addWidget(self._pb_prev)
        ctrl.addWidget(self._pb_play)
        ctrl.addWidget(self._pb_next)
        ctrl.addWidget(self._pb_like)
        lay.addLayout(ctrl)

        # Track info + slider
        info = QVBoxLayout(); info.setSpacing(4)
        self._track_lbl = QLabel("— Ninguna canción —")
        self._track_lbl.setFont(QFont(FONT_UI, 10, QFont.Weight.Bold))
        self._track_lbl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        self._slider = _SeekSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 100000)
        self._slider.setValue(0)
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slider.setStyleSheet(
            "QSlider::groove:horizontal { height:4px; background:rgba(255,255,255,0.12); border-radius:2px; }"
            "QSlider::sub-page:horizontal { background:#7DD3FC; border-radius:3px; }"
            "QSlider::handle:horizontal { background:#DFF5FF; width:12px; height:12px; margin:-4px 0; border-radius:6px; }"
            "QSlider::handle:horizontal:hover { background:#7dd3fc; }"
        )
        info.addWidget(self._track_lbl)
        info.addWidget(self._slider)
        lay.addLayout(info, stretch=1)

        # Time label
        self._time_lbl = QLabel("--:-- / --:--")
        self._time_lbl.setFont(QFont(FONT_UI, 9))
        self._time_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(self._time_lbl)

        # Connections
        self._pb_prev.clicked.connect(lambda: self._emit_playback_cmd('previous'))
        self._pb_play.clicked.connect(lambda: self._emit_playback_cmd('toggle_play'))
        self._pb_next.clicked.connect(lambda: self._emit_playback_cmd('next'))
        self._pb_like.clicked.connect(self._toggle_current_like)
        self._slider.sliderPressed.connect(lambda: setattr(self, '_user_dragging', True))
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._slider.sliderReleased.connect(self._on_seek)

        return w

    def _emit_playback_cmd(self, action: str, params: dict | None = None):
        if self.on_playback_command:
            try:
                threading.Thread(target=self.on_playback_command, args=(action, params or {}), daemon=True).start()
            except Exception:
                pass

    def _set_music_volume(self, level: int):
        try:
            lvl = max(0, min(100, int(level)))
        except Exception:
            lvl = 55
        self._music_volume_level = lvl
        if not self._music_duck_active and not self._music_duck_should_restore:
            self._music_volume_restore = lvl
        self._emit_playback_cmd("volume", {"level": lvl})

    def _step_music_duck(self):
        if not self._play_playing and not self._music_duck_active:
            self._music_duck_timer.stop()
            return

        target = self._music_volume_restore if self._music_duck_should_restore else self._music_duck_target
        target = max(self._music_duck_floor if self._music_duck_should_restore else 0, min(100, int(target)))
        current = int(self._music_volume_level)
        if current == target:
            self._music_duck_active = False
            if self._music_duck_should_restore:
                self._music_duck_should_restore = False
            self._music_duck_timer.stop()
            return

        direction = 1 if target > current else -1
        nxt = current + (self._music_duck_step * direction)
        if direction > 0:
            nxt = min(nxt, target)
        else:
            nxt = max(nxt, target)
        self._music_volume_level = nxt
        self._emit_playback_cmd("volume", {"level": nxt})

        if nxt == target:
            self._music_duck_active = False
            if self._music_duck_should_restore:
                self._music_duck_should_restore = False
            self._music_duck_timer.stop()

    def _start_music_duck(self):
        if self._muted or not self._play_playing:
            return
        if not self._music_duck_active:
            self._music_volume_restore = self._music_volume_level or 55
        self._music_duck_active = True
        self._music_duck_should_restore = False
        self._music_duck_target = max(self._music_duck_floor, int(self._music_volume_restore * 0.72))
        if not self._music_duck_timer.isActive():
            self._music_duck_timer.start()

    def _stop_music_duck(self):
        if self._muted:
            return
        self._music_duck_should_restore = True
        self._music_duck_active = False
        if not self._music_duck_timer.isActive():
            self._music_duck_timer.start()

    def _on_slider_moved(self, _value):
        """Fires for both click-on-track and drag; debounces seek."""
        self._user_dragging = True
        self._seek_timer.start(400)  # reset debounce timer on each move

    def _on_seek(self):
        self._seek_timer.stop()
        self._user_dragging = False
        v = self._slider.value()
        if self._play_duration and self._play_duration > 0:
            pos = (v / 100000.0) * self._play_duration
            self._play_position_anchor = float(pos)
            self._play_position_anchor_ts = time.monotonic()
            self._emit_playback_cmd('seek', {'position': pos})

    def _tick_playback_progress(self):
        if self._user_dragging or not self._play_playing or not self._play_duration or self._play_duration <= 0:
            return
        if not self._play_title:
            return

        anchor_pos = float(self._play_position_anchor or self._play_position or 0)
        anchor_ts = float(self._play_position_anchor_ts or 0.0)
        if anchor_ts <= 0.0:
            anchor_ts = time.monotonic()
            self._play_position_anchor_ts = anchor_ts

        elapsed = max(0.0, time.monotonic() - anchor_ts)
        cur = min(float(self._play_duration), anchor_pos + elapsed)
        self._play_position = cur

        pct = int((cur / self._play_duration) * 100000)
        self._slider.blockSignals(True)
        self._slider.setValue(max(0, min(100000, pct)))
        self._slider.blockSignals(False)

        def fmt(s):
            m = int(s // 60)
            ss = int(s % 60)
            return f"{m}:{ss:02d}"

        self._time_lbl.setText(f"{fmt(cur)} / {fmt(self._play_duration)}")

    # Public API to update playback UI
    def _toggle_current_like(self):
        if not self._play_video_id or not self._pb_like.isEnabled():
            return
        desired = not self._play_liked
        self._play_liked = desired
        self._pb_like.set_liked(desired)
        self._pb_like.setEnabled(False)
        self._pb_like.setToolTip("Quitando Me gusta..." if not desired else "Marcando como Me gusta...")
        self._emit_playback_cmd(
            "set_like",
            {"video_id": self._play_video_id, "liked": desired},
        )

    def _apply_playback_like(self, video_id: str, liked: bool, error: str):
        if str(video_id or "") != self._play_video_id:
            return
        self._pb_like.setEnabled(True)
        if error:
            liked = not bool(liked)
            self.statusBar().showMessage(f"No se pudo cambiar Me gusta: {error}", 5000)
        self._play_liked = bool(liked)
        self._pb_like.set_liked(self._play_liked)
        self._pb_like.setToolTip(
            "Quitar de Me gusta" if self._play_liked else "Marcar como Me gusta"
        )

    def update_playback(
        self,
        title: str,
        artists: str,
        position: float,
        duration: float,
        playing: bool,
        video_id: str = "",
        liked: bool | None = None,
    ):
        self._play_title = title
        self._play_artists = artists
        self._play_position = position
        self._play_duration = duration
        self._play_playing = playing
        if video_id and video_id != self._play_video_id:
            self._play_video_id = video_id
            self._play_liked = False
            self._pb_like.set_liked(False)
            self._pb_like.setEnabled(liked is not None)
            self._pb_like.setToolTip(
                "Comprobando Me gusta..." if liked is None else "Marcar como Me gusta"
            )
        if liked is not None:
            self._play_liked = bool(liked)
            self._pb_like.set_liked(self._play_liked)
            self._pb_like.setToolTip(
                "Quitar de Me gusta" if self._play_liked else "Marcar como Me gusta"
            )
        self._play_position_anchor = float(position or 0)
        self._play_position_anchor_ts = time.monotonic()
        txt = f"{title} — {artists}" if title else "— Ninguna canción —"
        self._track_lbl.setText(txt)
        if duration and duration > 0:
            if not self._user_dragging:
                pct = int((position / duration) * 100000)
                self._slider.setValue(max(0, min(100000, pct)))
            def fmt(s):
                m = int(s//60); ss = int(s%60); return f"{m}:{ss:02d}"
            self._time_lbl.setText(f"{fmt(position)} / {fmt(duration)}")
        else:
            if not self._user_dragging:
                self._slider.setValue(0)
            self._time_lbl.setText("--:-- / --:--")
        self._pb_play.set_shape(_MediaBtn.PAUSE if playing else _MediaBtn.PLAY)
        self._playback_bar.setVisible(bool(title))
        self.hud.music_playing = playing and bool(title)
        if self._music_panel is not None and hasattr(self._music_panel, "update_now_playing"):
            try:
                self._music_panel.update_now_playing(title, artists, playing)
            except Exception:
                pass



    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell JARVIS what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _request_download_cancel(self):
        if callable(self.on_download_cancel):
            try:
                self.on_download_cancel()
            except Exception:
                pass
        self._download_sig.emit({
            "active": True,
            "percent": self._download_widget._bar.value(),
            "label": "Cancelando...",
            "detail": self._download_widget._detail.text(),
            "can_cancel": False,
        })

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("Micrófono silenciado")
            self._mute_btn.setIcon(_line_icon("mic_off", C.RED, 18))
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(251, 113, 133, 0.14); color: {C.RED};
                    border: 1px solid rgba(251, 113, 133, 0.30); border-radius: 10px;
                }}
                QPushButton:hover {{ background: rgba(251, 113, 133, 0.20); }}
                QPushButton:focus {{ border: 2px solid rgba(251, 113, 133, 0.62); }}
            """)
        else:
            self._mute_btn.setText("Micrófono activo")
            self._mute_btn.setIcon(_line_icon("mic", C.ACC, 18))
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(167, 243, 208, 0.12); color: {C.ACC};
                    border: 1px solid rgba(167, 243, 208, 0.28); border-radius: 10px;
                }}
                QPushButton:hover {{ background: rgba(167, 243, 208, 0.18); color: {C.TEXT}; }}
                QPushButton:focus {{ border: 2px solid rgba(167, 243, 208, 0.58); }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.add_history(txt)
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        was_speaking = self.hud.speaking
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")
        self._style_header_state(state)
        if self.hud.speaking and not was_speaking:
            self._start_music_duck()
        elif was_speaking and not self.hud.speaking:
            self._stop_music_duck()

    def _apply_playback(self, info: dict):
        try:
            self.update_playback(
                info.get('title', ''),
                info.get('artists', ''),
                float(info.get('position', 0) or 0),
                float(info.get('duration', 0) or 0),
                bool(info.get('playing', False)),
                str(info.get('videoId') or info.get('video_id') or ''),
                info.get('liked'),
            )
        except Exception:
            pass

    def _apply_download_state(self, state: dict):
        try:
            self._download_widget.set_state(state)
        except Exception:
            pass

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. JARVIS online.")

class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        _set_windows_app_id()
        self._app.setApplicationName("JARVIS")
        self._app.setApplicationDisplayName("J.A.R.V.I.S")
        self._app.setWindowIcon(_build_app_icon())
        self._win = MainWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)
        # Stop headless music player when the Qt app closes
        try:
            from actions import ytmusic_headless as _hl
            self._app.aboutToQuit.connect(_hl._cleanup_on_exit)
        except Exception:
            pass
        # Install cross-thread auth dialog poller (must run on main thread)
        try:
            from actions.auth_dialog import install_main_thread_poller
            install_main_thread_poller()
        except Exception:
            pass

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_playback_command(self):
        return getattr(self._win, 'on_playback_command', None)

    @on_playback_command.setter
    def on_playback_command(self, cb):
        self._win.on_playback_command = cb

    def update_playback(
        self,
        title: str,
        artists: str,
        position: float,
        duration: float,
        playing: bool,
        video_id: str = "",
        liked: bool | None = None,
    ):
        try:
            # Emit via MainWindow signal to ensure update happens on the GUI thread
            self._win._playback_sig.emit({
                'title': title,
                'artists': artists,
                'position': position,
                'duration': duration,
                'playing': playing,
                'videoId': video_id,
                'liked': liked,
            })
        except Exception:
            pass

    def set_playback_like_state(self, video_id: str, liked: bool, error: str = ""):
        self._win._playback_like_sig.emit(str(video_id or ""), bool(liked), str(error or ""))

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def set_download_state(self, state: dict):
        try:
            self._win._download_sig.emit(state)
        except Exception:
            pass

    def set_task_state(self, state: dict):
        self.set_download_state(state)

    def open_whatsapp_chat(self, contact: str = ""):
        try:
            self._win._whatsapp_chat_sig.emit(str(contact or ""))
        except Exception:
            pass

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def start_speaking(self):
        self.set_state("SPEAKING")
        self._win.hud.set_audio_level(0.6)

    def stop_speaking(self):
        self._win.hud.set_audio_level(0.0)
        if not self.muted:
            self.set_state("LISTENING")

    def set_audio_level(self, level: float):
        """Feed real-time audio amplitude (0.0–1.0) to the orb visualizer."""
        self._win.hud.set_audio_level(level)

    def set_audio_bands(self, bass: float, mid: float, treble: float):
        """Feed per-band FFT levels (0-1) for frequency-aware visualization."""
        self._win.hud.set_audio_bands(bass, mid, treble)

    def set_fft_bins(self, bins):
        """Feed 64-bin FFT array (0-1) para las barras radiales."""
        self._win.hud.set_fft_bins(bins)

    def set_music_playing(self, playing: bool):
        """Marca si hay música reproduciéndose (anima el orbe diferente)."""
        self._win.hud.music_playing = bool(playing)

    def set_music_volume(self, level: int):
        try:
            self._win._set_music_volume(level)
        except Exception:
            pass

    def request_download_cancel(self):
        try:
            self._win._request_download_cancel()
        except Exception:
            pass
