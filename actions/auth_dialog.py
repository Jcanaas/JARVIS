"""Auth dialogs for WhatsApp QR code and Google Calendar OAuth setup.

Thread-safe: worker threads put GUI calls on a queue; a QTimer drains the
queue on the Qt main thread every 100 ms.  Call install_main_thread_poller()
once from the main thread (done automatically by JarvisUI.__init__).
"""
from __future__ import annotations

import io
import queue as _queue
import threading
import webbrowser
from typing import Optional


# ---------------------------------------------------------------------------
# Cross-thread call queue — producer: worker threads / consumer: main thread
# ---------------------------------------------------------------------------

_pending: _queue.Queue = _queue.Queue()


def _drain() -> None:
    """Drain all pending GUI calls.  Must be called on the Qt main thread."""
    while True:
        try:
            func, done = _pending.get_nowait()
        except _queue.Empty:
            break
        try:
            func()
        finally:
            done.set()


def install_main_thread_poller() -> None:
    """Install a QTimer that drains _pending every 100 ms on the main thread.
    Call this once from the Qt main thread (e.g. JarvisUI.__init__).
    """
    from PyQt6.QtCore import QTimer
    _timer = QTimer()
    _timer.setInterval(100)
    _timer.timeout.connect(_drain)
    _timer.start()
    # Keep reference alive on this module so it is never GC'd
    global _poller_timer
    _poller_timer = _timer


_poller_timer = None  # set by install_main_thread_poller()


def _schedule_and_wait(func, timeout: float = 300.0) -> None:
    """Queue *func* onto the Qt main thread and block until it returns."""
    done = threading.Event()
    _pending.put((func, done))
    done.wait(timeout=timeout)


# ---------------------------------------------------------------------------
# Shared stylesheet helpers
# ---------------------------------------------------------------------------

_BASE_STYLE = """
    QDialog { background: #0B1220; color: #E0E8F0; }
    QLabel  { color: #E0E8F0; }
"""

_BTN_PRIMARY = (
    "QPushButton { background:#1E4A8A; color:white; padding:8px 22px; "
    "border-radius:4px; font-weight:bold; font-size:12px; } "
    "QPushButton:hover { background:#2A5FAD; }"
)
_BTN_SECONDARY = (
    "QPushButton { background:#1E2A3A; color:#E0E8F0; padding:8px 18px; "
    "border-radius:4px; font-size:12px; } "
    "QPushButton:hover { background:#2A3A4A; }"
)


# ---------------------------------------------------------------------------
# WhatsApp QR dialog
# ---------------------------------------------------------------------------

def show_whatsapp_qr_dialog() -> bool:
    """Show a dialog with the WhatsApp QR code fetched from the local bridge.

    Polls ``http://127.0.0.1:3000/qr`` every 2 seconds and updates the image.
    Auto-closes when the bridge reports ``ready: true``.

    Returns True if the phone was successfully connected, False if cancelled.
    """
    connected = threading.Event()

    def _get_bridge_token() -> dict:
        """Load bridge token from file."""
        try:
            from actions.paths import WHATSAPP_DIR
            token_file = WHATSAPP_DIR / "bridge_token"
            if token_file.exists():
                token = token_file.read_text(encoding="utf-8").strip()
                return {"X-Bridge-Token": token} if token else {}
        except Exception:
            pass
        return {}

    def _create():
        import requests as req
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtWidgets import (
            QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )

        dlg = QDialog()
        dlg.setWindowTitle("WhatsApp — Scan QR Code")
        dlg.setModal(True)
        dlg.resize(360, 440)
        dlg.setStyleSheet(_BASE_STYLE)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(6)

        title = QLabel("Scan with WhatsApp")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:16px; font-weight:bold; margin:8px 0 2px 0;")
        layout.addWidget(title)

        subtitle = QLabel("Open WhatsApp → Settings → Linked Devices → Link a Device")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size:11px; color:#8899AA; margin-bottom:8px;")
        layout.addWidget(subtitle)

        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_label.setMinimumSize(280, 280)
        qr_label.setStyleSheet("background:#FFFFFF; border-radius:6px;")
        layout.addWidget(qr_label)

        _loading = QLabel("Connecting to bridge…")
        _loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _loading.setStyleSheet("color:#8899AA; font-size:11px; margin-top:4px;")
        layout.addWidget(_loading)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(_BTN_SECONDARY)
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        # --- polling timer ---
        _timer = QTimer(dlg)

        def _update_qr():
            try:
                import qrcode
                r = req.get("http://127.0.0.1:3000/qr", headers=_get_bridge_token(), timeout=3)
                data = r.json()

                if data.get("ready"):
                    _loading.setText("Connected!")
                    _loading.setStyleSheet("color:#4ADE80; font-size:12px; font-weight:bold;")
                    connected.set()
                    _timer.stop()
                    QTimer.singleShot(900, dlg.accept)
                    return

                qr_raw = data.get("qr")
                if qr_raw:
                    img = qrcode.make(qr_raw)
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    buf.seek(0)
                    pixmap = QPixmap()
                    pixmap.loadFromData(buf.read())
                    pixmap = pixmap.scaled(
                        264, 264,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    qr_label.setPixmap(pixmap)
                    _loading.setText("Scan the code with your phone")
                    _loading.setStyleSheet("color:#AABBCC; font-size:11px;")
                else:
                    _loading.setText("Waiting for QR… (bridge starting up?)")
            except Exception as exc:
                _loading.setText(f"Bridge unreachable — {exc}")
                _loading.setStyleSheet("color:#F87171; font-size:11px;")

        _timer.timeout.connect(_update_qr)
        _timer.start(2000)
        _update_qr()   # immediate first fetch

        dlg.exec()
        _timer.stop()

    _schedule_and_wait(_create)
    return connected.is_set()


# ---------------------------------------------------------------------------
# Google Calendar — missing credentials dialog
# ---------------------------------------------------------------------------

def show_google_setup_dialog() -> None:
    """Onboarding dialog: connect a Google account for Calendar, Gmail, Drive
    and YouTube with a single sign-in (BYO OAuth credentials).

    Adapts to the current state:
      * No ``google_credentials.json`` → guide the user to create their own
        OAuth client and import the downloaded JSON.
      * Credentials present but not signed in → offer "Sign in now".
    """

    def _create():
        import shutil
        from pathlib import Path
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import (
            QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )
        from actions.google_auth import (
            CREDENTIALS_FILE, has_credentials, is_signed_in,
        )

        dlg = QDialog()
        dlg.setWindowTitle("Conectar cuenta de Google")
        dlg.setModal(True)
        dlg.resize(560, 470)
        dlg.setStyleSheet(_BASE_STYLE)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        title = QLabel("Conecta tu cuenta de Google")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:16px; font-weight:bold; margin:10px 0 2px 0;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Un solo inicio de sesión habilita Calendar, Gmail, Drive y YouTube."
        )
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size:11px; color:#8899AA; margin-bottom:6px;")
        layout.addWidget(subtitle)

        msg = QLabel(
            "Necesitas tus propias credenciales OAuth (gratis):<br><br>"
            "&nbsp;&nbsp;1. Abre <b>Google Cloud Console</b> y crea un proyecto.<br>"
            "&nbsp;&nbsp;2. En <b>APIs y servicios → Biblioteca</b>, activa:<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;• Google Calendar API<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;• Gmail API<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;• Google Drive API<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;• YouTube Data API v3<br>"
            "&nbsp;&nbsp;3. Configura la <b>pantalla de consentimiento</b> (tipo Externo) "
            "y añádete como <b>usuario de prueba</b>.<br>"
            "&nbsp;&nbsp;4. En <b>Credenciales</b>, crea un <b>ID de cliente OAuth</b> "
            "de tipo <b>App de escritorio</b>.<br>"
            "&nbsp;&nbsp;5. Descarga el JSON e <b>impórtalo</b> aquí abajo.<br><br>"
            "<i>Nota: con la app en modo «Testing» funciona para tu propia cuenta "
            "sin verificación de Google.</i>"
        )
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size:12px; color:#AABBCC; padding:4px 14px;")
        layout.addWidget(msg)

        status = QLabel("")
        status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status.setWordWrap(True)
        status.setStyleSheet("font-size:11px; color:#8899AA; margin-top:2px;")
        layout.addWidget(status)

        layout.addStretch()

        # --- buttons ---
        btn_row = QHBoxLayout()
        open_btn = QPushButton("Abrir Cloud Console")
        open_btn.setStyleSheet(_BTN_SECONDARY)
        open_btn.clicked.connect(
            lambda: webbrowser.open("https://console.cloud.google.com/apis/credentials")
        )
        import_btn = QPushButton("Importar credenciales…")
        import_btn.setStyleSheet(_BTN_SECONDARY)
        signin_btn = QPushButton("Iniciar sesión ahora")
        signin_btn.setStyleSheet(_BTN_PRIMARY)
        close_btn = QPushButton("Cerrar")
        close_btn.setStyleSheet(_BTN_SECONDARY)
        close_btn.clicked.connect(dlg.accept)

        btn_row.addWidget(open_btn)
        btn_row.addWidget(import_btn)
        btn_row.addStretch()
        btn_row.addWidget(signin_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        def _refresh_state():
            if not has_credentials():
                status.setText("Aún no se han importado credenciales.")
                status.setStyleSheet("font-size:11px; color:#F87171;")
                signin_btn.setEnabled(False)
            elif is_signed_in():
                status.setText("Cuenta conectada ✓")
                status.setStyleSheet("font-size:12px; color:#4ADE80; font-weight:bold;")
                signin_btn.setEnabled(False)
            else:
                status.setText("Credenciales listas. Pulsa «Iniciar sesión ahora».")
                status.setStyleSheet("font-size:11px; color:#4ADE80;")
                signin_btn.setEnabled(True)

        def _do_import():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Selecciona google_credentials.json", "", "JSON (*.json)"
            )
            if not path:
                return
            try:
                CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, str(CREDENTIALS_FILE))
                status.setText("Credenciales importadas correctamente.")
                status.setStyleSheet("font-size:11px; color:#4ADE80;")
            except Exception as exc:
                status.setText(f"No se pudieron importar: {exc}")
                status.setStyleSheet("font-size:11px; color:#F87171;")
            _refresh_state()

        def _do_signin():
            signin_btn.setEnabled(False)
            status.setText("Abriendo el navegador para autorizar…")
            status.setStyleSheet("font-size:11px; color:#AABBCC;")

            def _worker():
                ok = False
                err = ""
                try:
                    from actions.google_auth import get_google_service
                    # A single flow grants every scope in ALL_SCOPES.
                    get_google_service("calendar", "v3")
                    ok = True
                except Exception as exc:
                    err = str(exc)

                def _done():
                    if ok:
                        status.setText("Cuenta conectada ✓")
                        status.setStyleSheet("font-size:12px; color:#4ADE80; font-weight:bold;")
                    else:
                        status.setText(f"No se pudo iniciar sesión: {err}")
                        status.setStyleSheet("font-size:11px; color:#F87171;")
                    _refresh_state()
                _pending.put((_done, threading.Event()))

            threading.Thread(target=_worker, daemon=True).start()

        import_btn.clicked.connect(_do_import)
        signin_btn.clicked.connect(_do_signin)

        # When credentials already ship with the app, hide the API-setup steps
        # and present a clean "Sign in with Google" screen.
        if has_credentials():
            subtitle.setText(
                "Inicia sesión con Google para habilitar Calendar, Gmail, "
                "Drive y YouTube."
            )
            msg.setVisible(False)
            open_btn.setVisible(False)
            import_btn.setVisible(False)
            signin_btn.setText("Iniciar sesión con Google")
            dlg.resize(440, 220)

        _refresh_state()
        dlg.raise_()
        dlg.activateWindow()
        dlg.exec()

    _schedule_and_wait(_create)


# Backwards-compatible alias (older call sites used the Calendar-specific name).
show_gcal_setup_dialog = show_google_setup_dialog


# ---------------------------------------------------------------------------
# Google Calendar — OAuth flow dialog
# ---------------------------------------------------------------------------

def show_gcal_auth_pending_dialog() -> None:
    """Show a brief info dialog while the browser OAuth flow is in progress.

    Uses show() (non-blocking) so the Qt main thread stays responsive while
    the worker thread runs flow.run_local_server().  Auto-closes after 45 s.
    """

    def _create():
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtWidgets import (
            QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )

        dlg = QDialog()
        dlg.setWindowTitle("Google Calendar — Authorization")
        dlg.setModal(False)
        dlg.resize(420, 180)
        dlg.setStyleSheet(_BASE_STYLE)

        layout = QVBoxLayout(dlg)

        title = QLabel("Authorizing Google Calendar…")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:15px; font-weight:bold; margin:14px 0 6px 0;")
        layout.addWidget(title)

        msg = QLabel(
            "A browser window will open for you to grant access.\n"
            "Complete the authorization and return here."
        )
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size:12px; color:#AABBCC;")
        layout.addWidget(msg)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(_BTN_SECONDARY)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        # Auto-close after 45 s (enough time for browser OAuth)
        QTimer.singleShot(45000, dlg.close)

        # Keep reference so Python doesn't GC the dialog
        app = QApplication.instance()
        if not hasattr(app, '_gcal_auth_dlg'):
            app._gcal_auth_dlg = None
        app._gcal_auth_dlg = dlg

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    _schedule_and_wait(_create, timeout=5.0)


# ---------------------------------------------------------------------------
# YouTube Music — OAuth flow dialog
# ---------------------------------------------------------------------------

def show_ytmusic_auth_pending_dialog() -> None:
    """Show a brief info dialog while YouTube Music login runs in a browser."""

    def _create():
        from PyQt6.QtCore import Qt, QTimer
        from PyQt6.QtWidgets import (
            QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
        )

        dlg = QDialog()
        dlg.setWindowTitle("YouTube Music - Google Sign-in")
        dlg.setModal(False)
        dlg.resize(440, 190)
        dlg.setStyleSheet(_BASE_STYLE)

        layout = QVBoxLayout(dlg)

        title = QLabel("Sign in to YouTube Music")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:15px; font-weight:bold; margin:14px 0 6px 0;")
        layout.addWidget(title)

        msg = QLabel(
            "A separate browser window will open at music.youtube.com.\n"
            "Sign in with Google there. Jarvis will detect the session automatically."
        )
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setWordWrap(True)
        msg.setStyleSheet("font-size:12px; color:#AABBCC;")
        layout.addWidget(msg)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("OK")
        ok_btn.setStyleSheet(_BTN_SECONDARY)
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        QTimer.singleShot(300000, dlg.close)

        app = QApplication.instance()
        if not hasattr(app, '_ytmusic_auth_dlg'):
            app._ytmusic_auth_dlg = None
        app._ytmusic_auth_dlg = dlg

        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    _schedule_and_wait(_create, timeout=5.0)


def close_ytmusic_auth_pending_dialog() -> None:
    """Close the YouTube Music login notice if it is currently visible."""

    def _close():
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        dlg = getattr(app, "_ytmusic_auth_dlg", None) if app else None
        if dlg is not None:
            dlg.close()
            app._ytmusic_auth_dlg = None

    _schedule_and_wait(_close, timeout=5.0)
