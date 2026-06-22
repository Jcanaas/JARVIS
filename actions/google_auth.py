"""Shared Google OAuth helper — Calendar, Gmail and Drive.

All three services share one token file (config/google_token.json) so the
user only needs to authenticate once.  If the existing token was created with
fewer scopes (e.g. calendar only), re-auth is triggered automatically.
"""
from __future__ import annotations

import threading
from pathlib import Path

from actions.paths import config_path

CREDENTIALS_FILE  = config_path("google_credentials.json")
TOKEN_FILE        = config_path("google_token.json")

# Serialises the interactive OAuth flow so concurrent callers don't each spin up
# their own loopback server / browser tab.
_AUTH_LOCK = threading.Lock()

# Combined scopes for Calendar + Gmail + Drive + YouTube.
# A single OAuth flow grants all of them, so the user authenticates only once.
ALL_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def has_credentials() -> bool:
    """True if the OAuth client secret (google_credentials.json) is present."""
    return CREDENTIALS_FILE.exists()


def is_signed_in() -> bool:
    """True if a usable Google token exists, WITHOUT launching the browser flow.

    A token counts as usable if it is currently valid or can be silently
    refreshed.  Used by onboarding to decide whether to show the setup pop-up.
    """
    if not TOKEN_FILE.exists():
        return False
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), ALL_SCOPES)
    except Exception:
        return False

    if not creds:
        return False
    # Token must cover all current scopes, otherwise a re-auth is required.
    if creds.scopes and not set(ALL_SCOPES).issubset(set(creds.scopes)):
        return False
    if creds.valid:
        return True
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            return True
        except Exception:
            return False
    return False


def get_google_service(api_name: str, api_version: str):
    """Return an authorised Google API service client.

    Handles token loading, silent refresh, and full OAuth browser flow with
    UI dialogs when needed.
    """
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), ALL_SCOPES)
        except Exception:
            creds = None

    needs_auth = False

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
                needs_auth = True
        else:
            needs_auth = True

    # Force re-auth if existing token lacks new scopes (e.g. only had calendar)
    if creds and creds.valid and creds.scopes:
        if not set(ALL_SCOPES).issubset(set(creds.scopes)):
            needs_auth = True
            creds = None

    if needs_auth:
        if not CREDENTIALS_FILE.exists():
            try:
                from actions.auth_dialog import show_gcal_setup_dialog
                show_gcal_setup_dialog()
            except Exception:
                pass
            raise FileNotFoundError(
                f"Falta {CREDENTIALS_FILE}. "
                "Descarga las credenciales OAuth desde Google Cloud Console y "
                "guárdalas como config/google_credentials.json."
            )

        # Serialize the browser flow: if several features need Google at once we
        # must run a single consent, not one local server per caller.
        with _AUTH_LOCK:
            # Another thread may have just completed auth while we waited.
            if TOKEN_FILE.exists():
                try:
                    fresh = Credentials.from_authorized_user_file(str(TOKEN_FILE), ALL_SCOPES)
                    if fresh and fresh.valid and fresh.scopes and \
                            set(ALL_SCOPES).issubset(set(fresh.scopes)):
                        return build(api_name, api_version, credentials=fresh)
                except Exception:
                    pass

            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), ALL_SCOPES)
            try:
                from actions.auth_dialog import show_gcal_auth_pending_dialog
                show_gcal_auth_pending_dialog()
            except Exception:
                pass
            # Bind explicitly to 127.0.0.1 (not "localhost"): on Windows 11
            # "localhost" can resolve to IPv6 ::1 while the loopback server
            # listens on IPv4, which makes the OAuth redirect fail with
            # ERR_CONNECTION_REFUSED. Using 127.0.0.1 for both the bind address
            # and the redirect URI avoids that mismatch.
            creds = flow.run_local_server(
                host="127.0.0.1",
                port=0,
                open_browser=True,
                success_message=(
                    "Autenticacion completada. Ya puedes cerrar esta pestana "
                    "y volver a Jarvis."
                ),
            )
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return build(api_name, api_version, credentials=creds)
