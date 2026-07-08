from __future__ import annotations

import json
import html as html_lib
import base64
import calendar as _calendar_mod
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
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import requests

from PyQt6.QtCore import (
    QAbstractAnimation, QBuffer, QByteArray, QDate, QEasingCurve, QEvent, QIODevice, QItemSelectionModel, QMimeData,
    QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTime, QTimer, QUrl,
    QVariantAnimation, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QCursor, QDesktopServices, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QFontMetrics, QIcon, QImageReader, QKeySequence, QLinearGradient, QPainter,
    QPainterPath, QPen, QPalette, QPixmap, QRadialGradient, QShortcut,
)
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QGraphicsPixmapItem, QGraphicsScene,
    QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget, QProgressBar, QSlider, QStackedWidget,
    QAbstractItemView, QButtonGroup, QComboBox, QHeaderView, QListWidget, QListWidgetItem,
    QInputDialog, QListView, QMenu, QMessageBox, QSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QDialog, QDialogButtonBox, QTimeEdit, QSpacerItem,
    QDateEdit,
)

try:
    # `import vlc` can raise beyond ImportError: python-vlc loads libvlc.dll at
    # import time, so a missing/mismatched VLC app raises OSError/FileNotFoundError.
    # Catch broadly so Jarvis still starts and just disables playback.
    import vlc
    HAS_VLC = True
except Exception:
    HAS_VLC = False
from actions.whatsapp_ui import WhatsAppWindow

from actions.paths import RESOURCE_DIR, CONFIG_DIR, MEMORY_DIR, config_path
from actions import app_settings

from .theme import *
from .icons import *
from .widgets import *
from .panels import *
from .whatsapp import WhatsAppToast, WhatsAppModePicker, WhatsAppRuleDialog


def _compute_initial_window_geometry(
    default_size: tuple[int, int],
    screen_geometry: QRect,
    min_size: tuple[int, int],
    margin: int = 24,
) -> QRect:
    """Compute a window rect that fits the available screen area and stays centered."""
    try:
        if not isinstance(screen_geometry, QRect):
            screen_geometry = QRect(*screen_geometry)
    except Exception:
        screen_geometry = QRect(0, 0, *default_size)

    if screen_geometry.isNull():
        screen_geometry = QRect(0, 0, *default_size)

    margin = max(0, int(margin))
    usable_w = max(1, screen_geometry.width() - (2 * margin))
    usable_h = max(1, screen_geometry.height() - (2 * margin))

    default_w, default_h = default_size
    min_w, min_h = min_size

    target_w = min(default_w, usable_w)
    if target_w < min_w:
        target_w = min(min_w, usable_w)
    target_h = min(default_h, usable_h)
    if target_h < min_h:
        target_h = min(min_h, usable_h)

    target_w = max(1, min(target_w, max(1, usable_w)))
    target_h = max(1, min(target_h, max(1, usable_h)))

    x = screen_geometry.x() + max(0, (screen_geometry.width() - target_w) // 2)
    y = screen_geometry.y() + max(0, (screen_geometry.height() - target_h) // 2)
    return QRect(x, y, target_w, target_h)


class MainWindow(QMainWindow):
    _log_sig   = pyqtSignal(str)
    _state_sig = pyqtSignal(str)
    _playback_sig = pyqtSignal(dict)
    _playback_like_sig = pyqtSignal(str, bool, str)
    _download_sig = pyqtSignal(dict)
    _whatsapp_chat_sig = pyqtSignal(str)
    _mic_avail_sig = pyqtSignal(bool)
    _toast_sig = pyqtSignal(str, int)
    _wa_notify_sig = pyqtSignal(dict)
    _wa_unread_sig = pyqtSignal(int)
    _wa_avatar_sig = pyqtSignal(object, object)

    def __init__(self, face_path: str):
        super().__init__()
        self.setWindowTitle("J.A.R.V.I.S — MARK XXXIX")
        self.setWindowIcon(_build_app_icon())
        self.setMinimumSize(_MIN_W, _MIN_H)

        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry() if screen else QRect(0, 0, _DEFAULT_W, _DEFAULT_H)
        geometry = _compute_initial_window_geometry(
            (_DEFAULT_W, _DEFAULT_H),
            screen_geometry,
            (_MIN_W, _MIN_H),
            margin=24,
        )
        self.setGeometry(geometry)

        self.on_text_command  = None
        self.on_download_cancel = None
        self._muted           = False
        self._mic_available   = True
        self._toast_label: QLabel | None = None
        self._wa_toasts: list[WhatsAppToast] = []
        self._wa_avatar_cache: dict[str, bytes] = {}
        self._wa_unread_count = 0
        self._wa_unread_fetching = False
        self._right_collapsed = True
        self._current_file: str | None = None
        self._mode_combo: QComboBox | None = None
        self._mode_buttons: dict[str, QPushButton] = {}
        self._mode_icon_names: dict[str, str] = {}
        self._mode_shortcuts: dict[str, str] = {}
        self._whatsapp_unread_badge: QLabel | None = None
        self._active_mode = "Normal"
        self._whatsapp_panel: WhatsAppWindow | None = None
        self._whatsapp_picker: WhatsAppModePicker | None = None
        self._gmail_panel: GmailModePanel | None = None
        self._drive_panel: DriveModePanel | None = None
        self._music_panel: QWidget | None = None
        self._youtube_panel: QWidget | None = None
        self._movies_panel: QWidget | None = None
        self._anime_panel: QWidget | None = None
        self._calendar_panel: QWidget | None = None
        self._settings_panel: QWidget | None = None

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
        self._center_stack = AnimatedStack()
        self._center_stack.setObjectName("Workspace")
        self._center_stack.setStyleSheet("""
            QStackedWidget#Workspace {
                background: rgba(6, 12, 22, 0.72);
                border: 1px solid rgba(182, 196, 255, 0.10);
                border-radius: 12px;
            }
        """)
        self._center_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._center_stack.addWidget(self.hud)
        body.addWidget(self._center_stack, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)
        self._apply_right_panel_visibility()

        root.addLayout(body, stretch=1)
        # Playback bar sits above footer
        root.addWidget(self._build_playback_bar())
        root.addWidget(self._build_footer())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        self._whatsapp_badge_timer = QTimer(self)
        self._whatsapp_badge_timer.timeout.connect(self._update_whatsapp_unread_badge)
        self._whatsapp_badge_timer.start(4000)

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._playback_sig.connect(self._apply_playback)
        self._playback_like_sig.connect(self._apply_playback_like)
        self._download_sig.connect(self._apply_download_state)
        self._whatsapp_chat_sig.connect(self._open_whatsapp)
        self._mic_avail_sig.connect(self._apply_mic_available)
        self._toast_sig.connect(self._show_toast)
        self._wa_notify_sig.connect(self._show_wa_notification)
        self._wa_unread_sig.connect(self._apply_wa_unread_badge)
        self._wa_avatar_sig.connect(self._apply_wa_toast_avatar)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        self._wa_win: WhatsAppWindow | None = None

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        
        # Playback state and external callback
        self._play_title = ""
        self._play_artists = ""
        self._play_duration = 0
        self._play_position = 0
        self._play_playing = False
        self._play_video_id = ""
        self._play_liked = False
        self._like_pending = False   # True while a set_like request is in flight
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
        self._music_float: _MusicFloatWindow | None = None

        self._apply_startup_settings()

    def closeEvent(self, event):
        """Ensure the Qt loop actually quits when the main window is closed,
        even if floating/overlay windows are still open."""
        # Reattach any detached YouTube video first so the shared mpv surface
        # isn't destroyed out from under the panel (that path can crash).
        try:
            yt = self._youtube_panel
            if yt is not None and hasattr(yt, "_detached_mode") and yt._detached_mode is not None:
                yt._reattach_video()
        except Exception:
            pass
        for attr in ("_music_float",):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass
        try:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
                app.quit()
        except Exception:
            pass
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and self._handle_mode_shortcut_event(obj, event):
            return True
        return super().eventFilter(obj, event)

    def _handle_mode_shortcut_event(self, obj, event) -> bool:
        if not self.isActiveWindow() or self._shortcut_focus_is_editing():
            return False
        if event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        if isinstance(obj, QWidget) and obj.window() is not self:
            return False
        key = str(event.text() or "").upper()
        if len(key) != 1:
            return False
        mode = self._mode_shortcuts.get(key)
        if not mode:
            return False
        self._on_mode_change(mode)
        return True

    def _shortcut_focus_is_editing(self) -> bool:
        widget = QApplication.focusWidget()
        while widget is not None:
            if isinstance(widget, (QLineEdit, QTextEdit, QComboBox, QSpinBox, QTimeEdit, QDateEdit)):
                return True
            class_name = widget.metaObject().className()
            if any(token in class_name for token in ("LineEdit", "TextEdit", "PlainTextEdit", "SpinBox", "DateEdit", "TimeEdit")):
                return True
            widget = widget.parentWidget()
        return False

    def _apply_startup_settings(self):
        """Apply persisted appearance/startup preferences when the window builds."""
        try:
            if bool(app_settings.get("ui_always_on_top", False)):
                self.set_always_on_top(True)
        except Exception:
            pass
        try:
            space = str(app_settings.get("startup_space", "last"))
            if space == "last":
                space = str(app_settings.get("last_space", "Normal"))
            if space and space != "Normal":
                # Defer until the event loop is running and managers are wired.
                QTimer.singleShot(350, lambda s=space: self._on_mode_change(s))
        except Exception:
            pass

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _toggle_right_panel(self):
        """Pliega/despliega el panel derecho (sesión, archivos y comando)."""
        self._right_collapsed = not self._right_collapsed
        self._apply_right_panel_visibility()
        if self._right_collapsed:
            self._right_toggle_btn.setIcon(_line_icon("panel_open", C.TEXT_DIM, 18))
            self._right_toggle_btn.setToolTip("Mostrar panel lateral")
            self._right_toggle_btn.setAccessibleName("Mostrar panel lateral")
        else:
            self._right_toggle_btn.setIcon(_line_icon("panel_close", C.TEXT_DIM, 18))
            self._right_toggle_btn.setToolTip("Ocultar panel lateral")
            self._right_toggle_btn.setAccessibleName("Ocultar panel lateral")

    def _apply_right_panel_visibility(self):
        """El panel derecho (actividad, archivos, comando) solo se oculta si el
        usuario lo ha plegado con el botón, en cualquier modo."""
        self._right_panel.setVisible(not self._right_collapsed)

    def _toggle_music_float(self):
        if self._music_float is not None:
            self._music_float.close()
            self._music_float = None
            self._pb_float_btn.setIcon(_line_icon("pip", C.TEXT_DIM, 18))
            return

        self._music_float = _MusicFloatWindow({
            "prev":   lambda: self._emit_playback_cmd("previous"),
            "toggle": lambda: self._emit_playback_cmd("toggle_play"),
            "next":   lambda: self._emit_playback_cmd("next"),
            "seek":   lambda pos: self._emit_playback_cmd("seek", {"position": pos}),
            "close":  self._toggle_music_float,
        })
        self._music_float.update_state(
            self._play_title, self._play_artists,
            self._play_position, self._play_duration,
            self._play_playing,
            self._get_now_playing_thumb(),
        )
        try:
            geo = QApplication.primaryScreen().availableGeometry()
            pos = QPoint(geo.right() - 404, geo.bottom() - 140)
        except Exception:
            pos = QPoint(80, 80)
        self._music_float.move(pos)
        self._music_float.show()
        self._pb_float_btn.setIcon(_line_icon("pip", C.PRI, 18))

    def _get_now_playing_thumb(self) -> QPixmap | None:
        panel = self._music_panel
        if panel is None:
            return None
        try:
            data = getattr(panel, '_now_playing_data', {}) or {}
            url = str(data.get("thumbnail") or data.get("cover") or "").strip()
            if not url:
                return None
            cache = getattr(panel, '_thumb_cache', {})
            raw = cache.get(url)
            if not raw:
                return None
            pix = QPixmap()
            pix.loadFromData(bytes(raw))
            return pix if not pix.isNull() else None
        except Exception:
            return None

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
            self._center_stack.setVisible(True)
            self._apply_right_panel_visibility()
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

    def set_always_on_top(self, enabled: bool):
        """Toggle the always-on-top window flag, preserving visibility."""
        try:
            was_visible = self.isVisible()
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(enabled))
            if was_visible:
                self.show()
        except Exception:
            pass

    def _set_mode_combo(self, mode: str):
        self._active_mode = mode
        # Remember the last space so "Espacio inicial → Último usado" works.
        if mode in ("Normal", "WhatsApp", "Gmail", "Drive", "Music", "YouTube", "Movies", "Anime", "Calendar"):
            try:
                app_settings.set("last_space", mode)
            except Exception:
                pass
        if self._mode_combo is not None:
            self._mode_combo.blockSignals(True)
            idx = self._mode_combo.findText(mode)
            if idx >= 0:
                self._mode_combo.setCurrentIndex(idx)
            self._mode_combo.blockSignals(False)
        for key, button in self._mode_buttons.items():
            active = key == mode
            button.setChecked(active)
            icon_name = self._mode_icon_names.get(key)
            if icon_name:
                button.setIcon(_line_icon(icon_name, C.PRI if active else C.TEXT_DIM, 20))
        mode_copy = {
            "Normal": ("Inicio", "Núcleo de voz"),
            "WhatsApp": ("WhatsApp", "Conversaciones"),
            "Gmail": ("Correo", "Bandeja de entrada"),
            "Drive": ("Drive", "Archivos en la nube"),
            "Music": ("Música", "Biblioteca y reproducción"),
            "YouTube": ("YouTube", "Vídeos y reproducción"),
            "Movies": ("Películas", "Streaming via torrents"),
            "Anime": ("Anime", "Manga y series japonesas"),
            "Calendar": ("Calendario", "Google Calendar"),
            "Ajustes": ("Ajustes", "Configuración de la app"),
        }
        title, context = mode_copy.get(mode, (mode, "Espacio de trabajo"))
        if hasattr(self, "_header_mode_label"):
            self._header_mode_label.setText(title)
        if hasattr(self, "_header_context_label"):
            self._header_context_label.setText(context)

    def _show_normal_mode(self):
        self._set_mode_combo("Normal")
        self._center_stack.setCurrentWidget(self.hud)
        self._center_stack.setVisible(True)
        self._apply_right_panel_visibility()

    def _show_whatsapp_picker(self):
        self._open_whatsapp("")

    def _show_gmail_mode(self):
        self._set_mode_combo("Gmail")
        self._apply_right_panel_visibility()
        if self._gmail_panel is None:
            self._gmail_panel = GmailModePanel(parent=self)
            self._center_stack.addWidget(self._gmail_panel)
        self._center_stack.setCurrentWidget(self._gmail_panel)
        self._center_stack.setVisible(True)

    def _show_drive_mode(self):
        self._set_mode_combo("Drive")
        self._apply_right_panel_visibility()
        if self._drive_panel is None:
            self._drive_panel = DriveModePanel(progress_hook=self._download_sig.emit, parent=self)
            self._center_stack.addWidget(self._drive_panel)
        self._center_stack.setCurrentWidget(self._drive_panel)
        self._center_stack.setVisible(True)

    def _show_music_mode(self):
        self._set_mode_combo("Music")
        self._apply_right_panel_visibility()
        if self._music_panel is None:
            self._music_panel = MusicModePanelV2(parent=self)
            self._center_stack.addWidget(self._music_panel)
        self._center_stack.setCurrentWidget(self._music_panel)
        self._center_stack.setVisible(True)

    def _show_youtube_mode(self):
        self._set_mode_combo("YouTube")
        self._apply_right_panel_visibility()
        if self._youtube_panel is None:
            self._youtube_panel = YouTubeModePanel(progress_hook=self._download_sig.emit, parent=self)
            self._center_stack.addWidget(self._youtube_panel)
        self._center_stack.setCurrentWidget(self._youtube_panel)
        self._center_stack.setVisible(True)

    def _show_movies_mode(self):
        self._set_mode_combo("Movies")
        self._apply_right_panel_visibility()
        if self._movies_panel is None:
            self._movies_panel = MoviesModePanel(parent=self)
            self._center_stack.addWidget(self._movies_panel)
        self._center_stack.setCurrentWidget(self._movies_panel)
        self._center_stack.setVisible(True)

    def _show_anime_mode(self):
        self._set_mode_combo("Anime")
        self._apply_right_panel_visibility()
        if self._anime_panel is None:
            self._anime_panel = AnimeModePanel(parent=self)
            self._center_stack.addWidget(self._anime_panel)
        self._center_stack.setCurrentWidget(self._anime_panel)
        self._center_stack.setVisible(True)

    def _show_calendar_mode(self):
        self._set_mode_combo("Calendar")
        self._apply_right_panel_visibility()
        if self._calendar_panel is None:
            self._calendar_panel = CalendarModePanel(parent=self)
            self._center_stack.addWidget(self._calendar_panel)
        self._center_stack.setCurrentWidget(self._calendar_panel)
        self._center_stack.setVisible(True)

    def _show_settings_mode(self):
        self._set_mode_combo("Ajustes")
        self._apply_right_panel_visibility()
        if self._settings_panel is None:
            self._settings_panel = SettingsModePanel(parent=self)
            self._center_stack.addWidget(self._settings_panel)
        self._center_stack.setCurrentWidget(self._settings_panel)
        self._center_stack.setVisible(True)

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
        elif mode == "Movies":
            self._show_movies_mode()
        elif mode == "Anime":
            self._show_anime_mode()
        elif mode == "Calendar":
            self._show_calendar_mode()
        elif mode == "Ajustes":
            self._show_settings_mode()
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

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setObjectName("AppHeader")
        w.setFixedHeight(66)
        w.setStyleSheet("""
            QWidget#AppHeader {
                background: rgba(5, 9, 16, 0.97);
                border-bottom: 1px solid rgba(182, 196, 255, 0.14);
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
        if self._right_collapsed:
            self._right_toggle_btn = _icon_button("panel_open", "Mostrar panel lateral", size=34, icon_size=17)
        else:
            self._right_toggle_btn = _icon_button("panel_close", "Ocultar panel lateral", size=34, icon_size=17)
        self._right_toggle_btn.clicked.connect(self._toggle_right_panel)
        lay.addWidget(self._right_toggle_btn)
        return w

    def _style_header_state(self, state: str):
        state = str(state or "LISTENING").upper()
        palette = {
            "SPEAKING": ("HABLANDO", "#B6C4FF", "rgba(94, 130, 255, 0.11)"),
            "THINKING": ("PENSANDO", "#C4B5FD", "rgba(167, 139, 250, 0.11)"),
            "PROCESSING": ("PROCESANDO", "#FDE68A", "rgba(250, 204, 21, 0.09)"),
            "MUTED": ("SILENCIADO", "#FDA4AF", "rgba(244, 63, 94, 0.09)"),
            "LISTENING": ("ESCUCHANDO", "#4ADE80", "rgba(52, 211, 153, 0.09)"),
        }
        label, color, background = palette.get(state, (state, "#B6C4FF", "rgba(94, 130, 255, 0.09)"))
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
                background: rgba(10, 12, 26, 0.86);
                border: 1px solid rgba(182, 196, 255, 0.10);
                border-radius: 12px;
            }
        """)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 16, 12, 14)
        lay.setSpacing(6)

        nav_items = [
            {"mode": "Normal", "icon": "home", "label": "Inicio", "labelHasKeyword": ["I"], "hasBadge": False},
            {"mode": "WhatsApp", "icon": "chat", "label": "WhatsApp", "labelHasKeyword": ["W"], "hasBadge": True},
            {"mode": "Gmail", "icon": "mail", "label": "Correo", "labelHasKeyword": ["C"], "hasBadge": False},
            {"mode": "Drive", "icon": "drive", "label": "Drive", "labelHasKeyword": ["D"], "hasBadge": False},
            {"mode": "Music", "icon": "music", "label": "Música", "labelHasKeyword": ["M"], "hasBadge": False},
            {"mode": "YouTube", "icon": "youtube", "label": "YouTube", "labelHasKeyword": ["Y"], "hasBadge": False},
            {"mode": "Movies", "icon": "film", "label": "Películas", "labelHasKeyword": ["P"], "hasBadge": False},
            {"mode": "Anime", "icon": "tv", "label": "Anime", "labelHasKeyword": ["N"], "hasBadge": False},
            {"mode": "Calendar", "icon": "calendar", "label": "Calendario", "labelHasKeyword": ["A"], "hasBadge": False},
            {"mode": "Ajustes", "icon": "settings", "label": "Ajustes", "labelHasKeyword": ["J"], "hasBadge": False},
        ]
        self._mode_icon_names = {item["mode"]: item["icon"] for item in nav_items}
        self._mode_shortcuts = {
            str((item.get("labelHasKeyword") or [""])[0]).upper(): item["mode"]
            for item in nav_items
            if item.get("labelHasKeyword")
        }
        mode_nav = TooltipVerticalNavbar(nav_items, parent=w)
        mode_nav.mode_selected.connect(self._on_mode_change)
        self._mode_buttons = mode_nav.buttons()
        self._whatsapp_unread_badge = mode_nav.badge("WhatsApp")
        lay.addStretch(1)
        lay.addWidget(mode_nav, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)
        self._mode_buttons["Normal"].setChecked(True)

        return w

    def _update_whatsapp_unread_badge(self):
        """Refresh the WhatsApp badge with the number of chats pending.

        Summing every chat's ``unreadCount`` produced a large opaque number
        (old muted groups inflate it and it barely moves when you read one
        chat). Counting *chats* with something unread matches what the user
        sees in the chat list and drops visibly as each chat is opened. The
        bridge call runs off the UI thread so it never blocks the interface.
        """
        if self._whatsapp_unread_badge is None:
            return
        if getattr(self, "whatsapp_manager", None) is None:
            self._apply_wa_unread_badge(0)
            return
        if self._wa_unread_fetching:
            return
        self._wa_unread_fetching = True

        def worker():
            count = -1  # sentinel: keep the previous value on failure
            try:
                from actions.whatsapp import list_recent_chats
                chats = list_recent_chats(300, timeout=8, include_pictures=False)
                count = sum(1 for c in chats if int(c.get("unread") or 0) > 0)
            except Exception:
                count = -1
            finally:
                self._wa_unread_fetching = False
            if count >= 0:
                self._wa_unread_sig.emit(int(count))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_wa_unread_badge(self, count: int):
        badge = self._whatsapp_unread_badge
        if badge is None:
            return
        self._wa_unread_count = int(count)
        if count <= 0:
            badge.hide()
            return
        badge.setText("99+" if count > 99 else str(count))
        badge.setFixedWidth(26 if count > 99 else 20)
        badge.move(23 if count > 99 else 29, 4)
        badge.show()
        badge.raise_()

    def _build_right_panel(self) -> QWidget:
        w = QWidget()
        w.setObjectName("CommandPanel")
        w.setFixedWidth(_RIGHT_W)
        w.setStyleSheet("""
            QWidget#CommandPanel {
                background: rgba(7, 14, 24, 0.90);
                border: 1px solid rgba(182, 196, 255, 0.10);
                border-radius: 12px;
            }
            QFrame#SideSurface {
                background: transparent;
                border: none;
            }
            QFrame#SectionDivider {
                background: rgba(182, 196, 255, 0.10);
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
        HoverGlow(self._mute_btn, color=C.GREEN, radius=32)
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
                color: {C.TEXT}; background: rgba(255, 255, 255, 0.11); border: 1px solid rgba(182, 196, 255, 0.36);
            }}
        """)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        HoverGlow(fs_btn, radius=32)
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
            QLineEdit:focus {{ background: rgba(255, 255, 255, 0.060); border: 1px solid rgba(182, 196, 255, 0.35); }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = _icon_button("send", "Enviar comando", size=42, icon_size=19, accent=True)
        send.clicked.connect(self._send)
        self._send_btn = send
        row.addWidget(send)
        return row

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(30)
        w.setStyleSheet(
            "background: rgba(5, 9, 17, 0.94); "
            "border-top: 1px solid rgba(182, 196, 255, 0.08);"
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
            "border-top: 1px solid rgba(182, 196, 255, 0.12);"
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
            "QSlider::sub-page:horizontal { background:#B6C4FF; border-radius:3px; }"
            "QSlider::handle:horizontal { background:#DCE1FF; width:12px; height:12px; margin:-4px 0; border-radius:6px; }"
            "QSlider::handle:horizontal:hover { background:#b6c4ff; }"
        )
        info.addWidget(self._track_lbl)
        info.addWidget(self._slider)
        lay.addLayout(info, stretch=1)

        # Time label
        self._time_lbl = QLabel("--:-- / --:--")
        self._time_lbl.setFont(QFont(FONT_UI, 9))
        self._time_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addWidget(self._time_lbl)

        # Floating player button
        self._pb_float_btn = _icon_button("pip", "Reproductor flotante", size=34, icon_size=17)
        self._pb_float_btn.setToolTip("Abrir/cerrar reproductor flotante de música")
        lay.addWidget(self._pb_float_btn)

        # Connections
        self._pb_prev.clicked.connect(lambda: self._emit_playback_cmd('previous'))
        self._pb_play.clicked.connect(lambda: self._emit_playback_cmd('toggle_play'))
        self._pb_next.clicked.connect(lambda: self._emit_playback_cmd('next'))
        self._pb_like.clicked.connect(self._toggle_current_like)
        self._pb_float_btn.clicked.connect(self._toggle_music_float)
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
        self._like_pending = True
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
        self._like_pending = False
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
            self._like_pending = False   # track changed → no pending op for it
            self._pb_like.set_liked(False)
            self._pb_like.setEnabled(liked is not None)
            self._pb_like.setToolTip(
                "Comprobando Me gusta..." if liked is None else "Marcar como Me gusta"
            )
        # Don't let the 1s poller override the button while the user's like
        # request is still in flight (avoids the toggle flickering back).
        if liked is not None and not self._like_pending:
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
        if self._music_float is not None:
            try:
                self._music_float.update_state(
                    title, artists, position, duration, playing,
                    self._get_now_playing_thumb(),
                )
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
        if not self._mic_available:
            return  # button is locked when there's no microphone
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _apply_mic_available(self, available: bool):
        """Lock the mic button (forced mute) when no input device is present;
        re-enable and resume listening as soon as one appears."""
        available = bool(available)
        if available == self._mic_available:
            # still keep button enabled state in sync
            self._mute_btn.setEnabled(available)
            return
        self._mic_available = available
        if not available:
            self._muted = True
            self.hud.muted = True
            self._mute_btn.setEnabled(False)
            self._apply_state("MUTED")
            self._style_mute_btn()
            self._log.append_log("SYS: No microphone detected.")
        else:
            self._mute_btn.setEnabled(True)
            self._muted = False
            self.hud.muted = False
            self._apply_state("LISTENING")
            self._style_mute_btn()
            self._log.append_log("SYS: Microphone connected.")

    def _show_toast(self, text: str, ms: int = 2500):
        """Brief notification pinned to the top-left of the window."""
        try:
            if self._toast_label is not None:
                self._toast_label.deleteLater()
        except Exception:
            pass
        lbl = QLabel(text, self)
        lbl.setStyleSheet(
            "QLabel {"
            " background: rgba(10,12,26,0.94);"
            " color: #E8EBFF;"
            " border: 1px solid rgba(182,196,255,0.35);"
            " border-radius: 10px;"
            " padding: 9px 14px; font-size: 12px; font-weight: 600;"
            " }"
        )
        lbl.adjustSize()
        lbl.move(18, 18)
        lbl.show()
        lbl.raise_()
        self._toast_label = lbl
        QTimer.singleShot(max(500, int(ms)), lbl.deleteLater)

    # ── WhatsApp floating notifications ──────────────────────────────────────
    def _show_wa_notification(self, entry: dict):
        """Show a floating desktop toast for an incoming WhatsApp message.

        Runs on the Qt main thread (invoked via ``_wa_notify_sig``). New toasts
        stack upward from the bottom-right corner; the oldest is dropped when
        more than four are visible at once.
        """
        try:
            if not app_settings.get("whatsapp_notifications", True):
                return
            entry = entry or {}
            title   = str(entry.get("title") or "WhatsApp")
            body    = str(entry.get("body") or "")
            chat_id = str(entry.get("chat_id") or "")
            try:
                dur_s = int(app_settings.get("whatsapp_notification_duration_s", 7))
            except Exception:
                dur_s = 7
            # 0 (or less) means "stay until dismissed".
            duration_ms = dur_s * 1000 if dur_s > 0 else 0
            if self._wa_toasts and len(self._wa_toasts) >= 4:
                self._wa_toasts[0]._dismiss()
            toast = WhatsAppToast(
                title, body, chat_id,
                on_open=self._on_wa_toast_open,
                on_closed=self._on_wa_toast_closed,
                duration_ms=duration_ms,
            )
            self._wa_toasts.append(toast)
            toast.show_animated()
            self._reflow_wa_toasts()
            if chat_id:
                self._fetch_wa_toast_avatar(toast, chat_id)
        except Exception:
            pass

    def _fetch_wa_toast_avatar(self, toast, chat_id: str):
        """Load the contact's profile photo for a toast (cached, off the UI thread)."""
        raw = self._wa_avatar_cache.get(chat_id)
        if raw:
            toast.set_avatar_bytes(raw)
            return

        def worker():
            data = b""
            try:
                from actions.whatsapp import get_profile_picture_url
                url = get_profile_picture_url(chat_id)
                if url:
                    resp = requests.get(url, timeout=5)
                    resp.raise_for_status()
                    data = resp.content
            except Exception:
                data = b""
            if data:
                self._wa_avatar_cache[chat_id] = data
                self._wa_avatar_sig.emit(toast, data)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_wa_toast_avatar(self, toast, raw):
        try:
            if toast in self._wa_toasts:
                toast.set_avatar_bytes(raw)
        except Exception:
            pass

    def _on_wa_toast_open(self, chat_id: str):
        try:
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _on_wa_toast_closed(self, toast):
        try:
            if toast in self._wa_toasts:
                self._wa_toasts.remove(toast)
        except Exception:
            pass
        self._reflow_wa_toasts()

    def _reflow_wa_toasts(self):
        """Re-stack the visible toasts from the bottom-right corner upward."""
        try:
            screen = QApplication.primaryScreen().availableGeometry()
        except Exception:
            return
        margin, gap = 18, 10
        y = screen.bottom() - margin
        for toast in reversed(self._wa_toasts):
            try:
                w, h = toast.width(), toast.height()
                y -= h
                toast.move(screen.right() - w - margin, int(y))
                y -= gap
            except Exception:
                continue

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
        if not self._mic_available:
            self._mute_btn.setText("Sin micrófono")
            self._mute_btn.setIcon(_line_icon("mic_off", C.TEXT_DIM, 18))
            self._mute_btn.setToolTip("No se detecta ningún micrófono")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.04); color: {C.TEXT_DIM};
                    border: 1px solid rgba(255,255,255,0.10); border-radius: 10px;
                }}
            """)
            return
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
            self._mute_btn.setIcon(_line_icon("mic", C.GREEN, 18))
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(74, 222, 128, 0.12); color: {C.GREEN};
                    border: 1px solid rgba(74, 222, 128, 0.28); border-radius: 10px;
                }}
                QPushButton:hover {{ background: rgba(74, 222, 128, 0.18); color: {C.TEXT}; }}
                QPushButton:focus {{ border: 2px solid rgba(74, 222, 128, 0.58); }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.add_history(txt)
        self._input.clear()
        # Feedback visual: destello en el botón + onda expansiva en el orbe.
        try:
            pulse_glow(self._send_btn)
            self.hud.burst()
        except Exception:
            pass
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
        # Wire the actions/ event bus to this UI so decoupled actions (agent
        # path, background threads) can still reach the log/toast widgets.
        try:
            from actions.event_bus import subscribe, ActionEvent
            subscribe(ActionEvent.LOG, lambda data: self.write_log(data.get("message", "")))
            subscribe(ActionEvent.TOAST, lambda data: self.show_toast(data.get("text", ""), data.get("duration", 2500)))
            subscribe(ActionEvent.ERROR, lambda data: self.write_log(f"[{data.get('source', 'Error')}] {data.get('message', '')}"))
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

    def set_mic_available(self, available: bool):
        try:
            self._win._mic_avail_sig.emit(bool(available))
        except Exception:
            pass

    def show_toast(self, text: str, ms: int = 2500):
        try:
            self._win._toast_sig.emit(str(text), int(ms))
        except Exception:
            pass

    def show_whatsapp_notification(self, entry: dict):
        """Pop a floating desktop notification for an incoming WhatsApp message.

        Thread-safe: marshals to the Qt main thread via a signal. ``entry`` may
        carry ``title`` (sender/contact), ``body`` (message text) and
        ``chat_id`` (clicked → opens that chat).
        """
        try:
            self._win._wa_notify_sig.emit(dict(entry or {}))
        except Exception:
            pass

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
