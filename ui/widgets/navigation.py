from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QEasingCurve, pyqtSignal

from .anim import HiFpsAnimation
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget, QGraphicsDropShadowEffect,
)

from ..theme import C, qcol
from ..icons import _line_icon, _icon_button
from .effects import HoverGlow, _SnapshotVeil, _ANIM_DUR


class _ModeShortcutTooltip(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("ModeShortcutTooltip")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("""
            QFrame#ModeShortcutTooltip {
                background: rgba(8, 11, 23, 0.96);
                border: 1px solid rgba(182, 196, 255, 0.26);
                border-radius: 8px;
            }
            QLabel#ModeTipLabel {
                color: #F2F3FA;
                background: transparent;
                border: none;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#ModeTipHint {
                color: #8FA8FF;
                background: transparent;
                border: none;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#ModeTipKey {
                color: #07101E;
                background: #B6C4FF;
                border: none;
                border-radius: 5px;
                padding: 2px 7px;
                font-size: 10px;
                font-weight: 900;
            }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 8, 7)
        lay.setSpacing(8)
        self._label = QLabel()
        self._label.setObjectName("ModeTipLabel")
        self._hint = QLabel("Atajo")
        self._hint.setObjectName("ModeTipHint")
        self._key = QLabel()
        self._key.setObjectName("ModeTipKey")
        self._key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._label)
        lay.addSpacing(2)
        lay.addWidget(self._hint)
        lay.addWidget(self._key)

        glow = QGraphicsDropShadowEffect(self)
        glow.setOffset(0, 10)
        glow.setBlurRadius(22)
        glow.setColor(qcol("#000000", 130))
        self.setGraphicsEffect(glow)

    def show_for(self, anchor: QWidget, label: str, shortcut: str):
        self._label.setText(label)
        self._key.setText(shortcut.upper())
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(anchor.width() + 10, (anchor.height() - self.height()) // 2))
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            if pos.x() + self.width() > geo.right() - 6:
                pos.setX(anchor.mapToGlobal(QPoint(-self.width() - 10, 0)).x())
            pos.setY(max(geo.top() + 6, min(pos.y(), geo.bottom() - self.height() - 6)))
        self.move(pos)
        self.show()
        self.raise_()


class TooltipVerticalNavbar(QWidget):
    mode_selected = pyqtSignal(str)

    def __init__(self, items: list[dict], parent=None):
        super().__init__(parent)
        self.setObjectName("TooltipVerticalNavbar")
        self._buttons: dict[str, QPushButton] = {}
        self._badges: dict[str, QLabel] = {}
        self._tooltip = _ModeShortcutTooltip(self)
        self.setStyleSheet("""
            QWidget#TooltipVerticalNavbar {
                background: rgba(255, 255, 255, 0.035);
                border: 1px solid rgba(182, 196, 255, 0.11);
                border-radius: 12px;
            }
            QPushButton#TooltipVerticalNavButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 11px;
                padding: 0;
            }
            QPushButton#TooltipVerticalNavButton:hover {
                background: rgba(255,255,255,0.06);
                border-color: rgba(182,196,255,0.18);
            }
            QPushButton#TooltipVerticalNavButton:checked {
                background: rgba(94,130,255,0.16);
                border-color: rgba(182,196,255,0.42);
            }
            QPushButton#TooltipVerticalNavButton:focus {
                border: 2px solid rgba(182,196,255,0.58);
            }
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(7)

        for item in items:
            mode = item["mode"]
            label = item["label"]
            shortcut = str((item.get("labelHasKeyword") or [""])[0]).upper()
            icon_name = item["icon"]
            button = QPushButton()
            button.setObjectName("TooltipVerticalNavButton")
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setFixedSize(50, 50)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(self._nav_icon(icon_name))
            button.setIconSize(QSize(22, 22))
            button.setAccessibleName(f"Abrir {label}")
            button.setProperty("navLabel", label)
            button.setProperty("navShortcut", shortcut)
            button.installEventFilter(self)
            button.clicked.connect(lambda _checked=False, m=mode: self.mode_selected.emit(m))
            HoverGlow(button, color=C.PRI_DIM, radius=28)
            lay.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
            self._buttons[mode] = button

            if item.get("hasBadge"):
                badge = QLabel("", button)
                badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                badge.setToolTip("Chats con mensajes sin leer")
                badge.setFixedSize(22, 22)
                badge.move(27, 3)
                badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                badge.setStyleSheet("""
                    QLabel {
                        color: #03111B;
                        background: #7C9AFF;
                        border: 1px solid rgba(255, 255, 255, 0.45);
                        border-radius: 10px;
                        font-size: 9px;
                        font-weight: 800;
                    }
                """)
                badge.hide()
                self._badges[mode] = badge

    def buttons(self) -> dict[str, QPushButton]:
        return self._buttons

    def badge(self, mode: str) -> QLabel | None:
        return self._badges.get(mode)

    def _nav_icon(self, icon_name: str) -> QIcon:
        return _line_icon(icon_name, C.TEXT_DIM, 20)

    def eventFilter(self, obj, event):
        if isinstance(obj, QPushButton):
            if event.type() == QEvent.Type.Enter:
                obj.setFixedSize(52, 52)
                obj.setIconSize(QSize(23, 23))
                label = str(obj.property("navLabel") or "")
                shortcut = str(obj.property("navShortcut") or "")
                if label and shortcut:
                    self._tooltip.show_for(obj, label, shortcut)
            elif event.type() in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
                obj.setFixedSize(50, 50)
                obj.setIconSize(QSize(22, 22))
                self._tooltip.hide()
        return super().eventFilter(obj, event)


class _PaginationPageButton(QPushButton):
    """Botón numerado de paginación con halo de hover y resalte del activo."""

    def __init__(self, page: int, parent=None):
        super().__init__(str(page), parent)
        self.page = page
        self.setObjectName("PagePageButton")
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"Ir a la página {page}")
        HoverGlow(self, color=C.PRI_DIM, radius=22)
        self.set_active(False)

    def set_active(self, active: bool):
        self.setChecked(active)
        if active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {C.PRI};
                    color: #0a0e26;
                    border: 1px solid {C.PRI_DIM};
                    border-radius: 8px;
                    font-size: 12px;
                    font-weight: 800;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.045);
                    color: {C.TEXT_DIM};
                    border: 1px solid rgba(255,255,255,0.09);
                    border-radius: 8px;
                    font-size: 12px;
                    font-weight: 700;
                }}
                QPushButton:hover {{
                    color: {C.TEXT};
                    background: rgba(255,255,255,0.08);
                    border-color: rgba(182,196,255,0.35);
                }}
                QPushButton:pressed {{ background: rgba(255,255,255,0.03); }}
            """)


class PaginationBar(QWidget):
    """Barra de paginación numerada (anterior / páginas / siguiente) con el
    mismo lenguaje visual que el resto de la app: chip activo en azul
    primario con halo de hover en cada botón en vez de los físicos
    "lift"/shimmer de la referencia web, que no tienen equivalente nativo
    en widgets de Qt."""

    page_changed = pyqtSignal(int)

    def __init__(self, total_pages: int = 1, current_page: int = 1,
                 max_visible: int = 5, parent=None):
        super().__init__(parent)
        self._total = max(1, int(total_pages))
        self._current = max(1, min(int(current_page), self._total))
        self._max_visible = max(3, int(max_visible))

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)

        self.prev_btn = _icon_button("chevron_left", "Página anterior", size=30, icon_size=15)
        self.prev_btn.clicked.connect(lambda: self.go_to(self._current - 1))
        self._layout.addWidget(self.prev_btn)

        self._pages_host = QHBoxLayout()
        self._pages_host.setSpacing(4)
        self._layout.addLayout(self._pages_host)

        self.next_btn = _icon_button("chevron_right", "Página siguiente", size=30, icon_size=15)
        self.next_btn.clicked.connect(lambda: self.go_to(self._current + 1))
        self._layout.addWidget(self.next_btn)

        self._rebuild()

    def current_page(self) -> int:
        return self._current

    def set_pages(self, total_pages: int, current_page: int | None = None):
        self._total = max(1, int(total_pages))
        if current_page is not None:
            self._current = max(1, min(int(current_page), self._total))
        else:
            self._current = max(1, min(self._current, self._total))
        self._rebuild()

    def go_to(self, page: int):
        page = max(1, min(int(page), self._total))
        if page == self._current:
            return
        self._current = page
        self._rebuild()
        self.page_changed.emit(page)

    def _window(self) -> list[int]:
        radius = max(1, (self._max_visible - 2) // 2)
        pages = {1, self._total}
        for p in range(self._current - radius, self._current + radius + 1):
            if 1 <= p <= self._total:
                pages.add(p)
        return sorted(pages)

    def _rebuild(self):
        while self._pages_host.count():
            item = self._pages_host.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        prev_page = None
        for page in self._window():
            if prev_page is not None and page - prev_page > 1:
                gap = QLabel("…")
                gap.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; font-size: 12px;")
                gap.setFixedWidth(16)
                gap.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._pages_host.addWidget(gap)
            btn = _PaginationPageButton(page)
            btn.set_active(page == self._current)
            btn.clicked.connect(lambda _c=False, p=page: self.go_to(p))
            self._pages_host.addWidget(btn)
            prev_page = page

        self.prev_btn.setEnabled(self._current > 1)
        self.next_btn.setEnabled(self._current < self._total)


class AnimatedStack(QStackedWidget):
    """QStackedWidget con transición fluida: la página nueva se desliza en
    vivo mientras una instantánea de la anterior se desvanece encima."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._veil: _SnapshotVeil | None = None

    def setCurrentIndex(self, index: int):
        w = self.widget(index)
        if w is not None:
            self.setCurrentWidget(w)
        else:
            super().setCurrentIndex(index)

    def _finish_transition(self):
        if self._veil is not None:
            self._veil.deleteLater()
            self._veil = None
        cur = self.currentWidget()
        if cur is not None and cur.pos() != QPoint(0, 0):
            cur.move(0, 0)

    def setCurrentWidget(self, w: QWidget):
        old = self.currentWidget()
        if (w is old or old is None or not self.isVisible()
                or self.width() <= 0 or self.height() <= 0):
            super().setCurrentWidget(w)
            return
        # Si hay una transición en curso, se remata al instante (evita
        # solapamientos y temblores al cambiar de espacio rápidamente).
        self._finish_transition()
        try:
            old_pix = old.grab()
        except Exception:
            super().setCurrentWidget(w)
            return
        super().setCurrentWidget(w)

        veil = _SnapshotVeil(self, old_pix)
        veil.setGeometry(self.rect())
        veil.show()
        veil.raise_()
        self._veil = veil

        fade = HiFpsAnimation(veil, setter=veil.set_opacity)
        fade.setDuration(200)
        fade.setStartValue(1.0)
        fade.setEndValue(0.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.finished.connect(self._finish_transition)

        # La página entrante (viva) sube suavemente a su sitio.
        w.move(0, 14)
        slide = HiFpsAnimation(veil, setter=w.move)
        slide.setDuration(_ANIM_DUR)
        slide.setStartValue(QPoint(0, 14))
        slide.setEndValue(QPoint(0, 0))
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        fade.start(delete_when_stopped=True)
        slide.start(delete_when_stopped=True)


__all__ = [
    'TooltipVerticalNavbar',
    '_ModeShortcutTooltip',
    'PaginationBar',
    '_PaginationPageButton',
    'AnimatedStack',
]
