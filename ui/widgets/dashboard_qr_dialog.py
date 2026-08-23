from __future__ import annotations

import io

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QDialog, QLabel, QLineEdit, QVBoxLayout

__all__ = ["DashboardQrDialog"]


class DashboardQrDialog(QDialog):
    """Shows the LAN dashboard pairing QR + raw URL (selectable) so the user
    can scan it with their phone. Same qrcode.make() -> QPixmap pattern as
    actions/auth_dialog.py's Google OAuth QR."""

    def __init__(self, url: str, parent=None, public: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Emparejar dashboard")
        self.setFixedSize(320, 400)
        self.setStyleSheet("QDialog { background: #0A0C16; }")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        title = QLabel("Escanea con tu móvil")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet("color: #B6C4FF; font-size: 13px; font-weight: 700; background: transparent;")
        lay.addWidget(title)

        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        try:
            import qrcode
            img = qrcode.make(url)
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
        except Exception as e:
            qr_label.setText(f"(no se pudo generar el QR: {e})")
            qr_label.setStyleSheet("color: #FF5E82; background: transparent; font-size: 11px;")
        lay.addWidget(qr_label)

        hint = QLabel(
            "IP pública — funciona desde cualquier red, siempre que hayas "
            "redirigido el puerto en tu router hacia este PC."
            if public else
            "Debe estar en la misma red WiFi."
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet("color: rgba(220,225,255,0.55); font-size: 11px; background: transparent;")
        lay.addWidget(hint)

        url_field = QLineEdit(url)
        url_field.setReadOnly(True)
        url_field.setCursor(Qt.CursorShape.IBeamCursor)
        url_field.setStyleSheet(
            "QLineEdit { background: #141829; color: #DCE1FF; border: 1px solid #3A4160;"
            " border-radius: 8px; padding: 6px 10px; font-size: 11px; }"
        )
        lay.addWidget(url_field)
