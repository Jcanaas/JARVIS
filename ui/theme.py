from __future__ import annotations

import platform
from pathlib import Path

from PyQt6.QtGui import QColor

from actions.paths import RESOURCE_DIR, CONFIG_DIR, MEMORY_DIR, config_path


def _base_dir() -> Path:
	"""Read-only resource root (use CONFIG_DIR/MEMORY_DIR for writable data)."""
	return RESOURCE_DIR


BASE_DIR   = RESOURCE_DIR
API_FILE   = config_path("api_keys.json")

_DEFAULT_W, _DEFAULT_H = 1180, 760
_MIN_W,     _MIN_H     = 900, 620
_LEFT_W  = 88
_RIGHT_W = 320

_OS = platform.system()


class C:
	BG        = "#0A0C16"
	PANEL     = "#141829"
	PANEL2    = "#1E2338"
	BORDER    = "#3A4160"
	BORDER_B  = "#6B76A5"
	BORDER_A  = "#252C48"
	PRI       = "#B6C4FF"
	PRI_DIM   = "#5E82FF"
	PRI_GHO   = "#131C46"
	ACC       = "#A7AFFF"
	ACC2      = "#8FA8FF"
	GREEN     = "#4ADE80"
	GREEN_D   = "#22C55E"
	RED       = "#FF5E82"
	MUTED_C   = "#9AA3C0"
	TEXT      = "#F2F3FA"
	TEXT_DIM  = "#C9CDE4"
	TEXT_MED  = "#9AA3C0"
	WHITE     = "#FFFFFF"
	DARK      = "#060812"
	BAR_BG    = "#0D1020"
	GLASS     = "rgba(255, 255, 255, 0.055)"
	GLASS_2   = "rgba(255, 255, 255, 0.095)"
	GLASS_D   = "rgba(8, 10, 22, 0.78)"
	SHADOW    = "rgba(0, 0, 0, 0.35)"


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
			background: rgba(182, 196, 255, 0.45);
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


__all__ = [
	'_base_dir', 'BASE_DIR', 'API_FILE', '_DEFAULT_W', '_DEFAULT_H', '_MIN_W', '_MIN_H',
	'_LEFT_W', '_RIGHT_W', '_OS', 'C', 'FONT_UI', 'FONT_UI_FALLBACK', 'FONT_MONO',
	'qcol', '_scrollbar_qss',
]
