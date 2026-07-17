"""Smoke test: every mode panel, dialog and helper widget must construct.

Catches NameError/ImportError leftovers from the ui.py -> ui/ refactor that
only fire when a class is actually instantiated (mode click, dialog open...).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def main_window(app):
    from ui import JarvisUI
    ui = JarvisUI("")
    return ui._win


MODES = ["normal", "gmail", "drive", "music", "youtube", "movies", "tv", "calendar", "settings"]


@pytest.mark.parametrize("mode", MODES)
def test_mode_opens(main_window, mode):
    getattr(main_window, f"_show_{mode}_mode")()


def test_anime_mode(main_window):
    main_window._show_anime_mode()


def test_gmail_compose_dialog(app):
    from ui.panels.gmail import GmailComposeDialog
    dlg = GmailComposeDialog()
    dlg.deleteLater()


def test_calendar_event_dialog(app):
    from ui.panels.calendar import CalendarEventDialog
    dlg = CalendarEventDialog()
    dlg.deleteLater()


def test_whatsapp_rule_dialog(app):
    from ui.whatsapp import WhatsAppRuleDialog
    dlg = WhatsAppRuleDialog()
    dlg.deleteLater()


def test_music_float_window(app):
    from ui.panels.music import _MusicFloatWindow
    win = _MusicFloatWindow({})
    win.update_state("Song", "Artist", 10.0, 100.0, True)
    win.close()


def test_torrent_select_dialog(app):
    from types import SimpleNamespace
    from ui.panels.movies import _TorrentSelectDialog
    torrent = SimpleNamespace(title="t", seeders=1, size="1 GB", quality="1080p", source="x")
    dlg = _TorrentSelectDialog([torrent])
    dlg.deleteLater()


def test_movie_card(app):
    from types import SimpleNamespace
    from ui.panels.movies import _MovieCard
    movie = SimpleNamespace(id=1, title="Test", poster_url="", year=2024,
                            release_year=2024, rating=7.5, vote_average=7.5,
                            media_type="movie", overview="")
    card = _MovieCard(movie)
    card.deleteLater()


def test_overlays(app):
    from ui.widgets.overlays import _DetachWindow, _FloatOverlay
    win = _DetachWindow(lambda: None)
    win.close()
    ov = _FloatOverlay({}, draggable=True, resizable=True)
    ov.close()


def test_whatsapp_toast(app):
    from ui.whatsapp import WhatsAppToast
    toast = WhatsAppToast("Test", "hola", chat_id="x", on_open=lambda cid: None)
    toast.close()


def test_calendar_load_month(main_window):
    main_window._show_calendar_mode()
    panel = main_window._calendar_panel
    panel._load_month()


def test_public_api():
    import ui
    assert ui.JarvisUI
    assert ui.DriveModePanel
