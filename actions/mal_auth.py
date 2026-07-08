"""MyAnimeList OAuth2 PKCE authentication for Jarvis.

MAL uses PKCE with the "plain" code_challenge method. For a desktop app we
briefly open a local HTTP server on port 8765 to receive the callback code.

Setup (one-time, ~1 min):
  1. Go to https://myanimelist.net/apiconfig → Add Application
  2. Set App Redirect URL:  http://localhost:8765/callback
  3. Copy the client_id into  config/api_keys.json → "mal_client_id"
"""
from __future__ import annotations

import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from actions.paths import config_path

_AUTH_BASE = "https://myanimelist.net/v1/oauth2"
_API_BASE = "https://api.myanimelist.net/v2"
_REDIRECT = "http://localhost:8765/callback"
_CALLBACK_TIMEOUT = 60  # 60s timeout para el callback OAuth

# Jarvis's own MAL application id. A PKCE public client has no secret, so
# shipping the id in code is fine; api_keys.json can still override it.
_DEFAULT_CLIENT_ID = "8bc82c73fc7d32884395430949605ff4"


class MALAuthError(RuntimeError):
    """Raised when OAuth2 or token operations fail."""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def get_client_id() -> str:
    try:
        cfg = config_path("api_keys.json")
        if cfg.exists():
            with open(cfg, encoding="utf-8") as f:
                configured = json.load(f).get("mal_client_id", "")
                if configured:
                    return configured
    except Exception:
        pass
    return _DEFAULT_CLIENT_ID


def _tokens_path():
    return config_path("mal_tokens.json")


def get_tokens() -> dict:
    try:
        p = _tokens_path()
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_tokens(tokens: dict) -> None:
    p = _tokens_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(tokens, f)


# ---------------------------------------------------------------------------
# Public state
# ---------------------------------------------------------------------------

def is_logged_in() -> bool:
    tokens = get_tokens()
    return bool(tokens.get("access_token") and tokens.get("username"))


def get_username() -> str:
    return get_tokens().get("username", "")


def get_access_token() -> str:
    """Return a valid access token, refreshing if it expires within 24 h."""
    tokens = get_tokens()
    if not tokens.get("access_token"):
        raise MALAuthError("Not logged in to MAL.")
    if time.time() >= tokens.get("expires_at", 0) - 86400:
        tokens = _refresh(tokens)
    return tokens["access_token"]


def _refresh(tokens: dict) -> dict:
    client_id = get_client_id()
    if not client_id:
        raise MALAuthError("MAL client_id not configured.")
    r = requests.post(f"{_AUTH_BASE}/token", data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client_id,
    }, timeout=15)
    r.raise_for_status()
    data = r.json()
    tokens.update({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", tokens["refresh_token"]),
        "expires_at": time.time() + data.get("expires_in", 2764800),
    })
    _save_tokens(tokens)
    return tokens


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------

def login(client_id: str) -> dict:
    """Full OAuth2 PKCE login. Opens browser and waits for the redirect.

    Blocks the calling thread until the user authorizes (or timeout).
    Run this from a worker thread — never from the GUI thread.
    """
    code_verifier = secrets.token_urlsafe(96)[:128]
    state = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "state": state,
        "code_challenge": code_verifier,
        "code_challenge_method": "plain",
        "redirect_uri": _REDIRECT,
    }
    auth_url = f"{_AUTH_BASE}/authorize?" + urlencode(params)

    received: dict = {}

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            print(f"[MAL Callback] Recibido GET: {self.path}")
            qs = parse_qs(urlparse(self.path).query)
            received["code"] = qs.get("code", [""])[0]
            received["state"] = qs.get("state", [""])[0]
            received["error"] = qs.get("error", [""])[0]
            print(f"[MAL Callback] code={received['code'][:8] if received['code'] else 'NONE'}, "
                  f"error={received['error']}, state_ok={received['state'] == state}")

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if received["code"]:
                self.wfile.write(
                    b"<h2 style='font-family:sans-serif;padding:2em'>"
                    b"Autorizado. Puedes cerrar esta ventana.</h2>"
                )
            else:
                self.wfile.write(
                    b"<h2 style='font-family:sans-serif;padding:2em;color:red'>"
                    b"Error: " + received["error"].encode() + b"</h2>"
                )

        def log_message(self, *_):
            pass

    try:
        server = HTTPServer(("localhost", 8765), _Handler)
    except OSError as exc:
        raise MALAuthError(
            f"No se puede abrir puerto 8765 para el callback OAuth: {exc}. "
            "¿Hay otra aplicación usando ese puerto?"
        ) from exc
    server.timeout = _CALLBACK_TIMEOUT

    try:
        webbrowser.open(auth_url)
        server.handle_request()
    finally:
        server.server_close()

    code = received.get("code", "")
    if not code:
        raise MALAuthError("No se recibió el código de autorización (timeout o cancelación).")
    if received.get("state") != state:
        raise MALAuthError("State mismatch — posible CSRF.")

    r = requests.post(f"{_AUTH_BASE}/token", data={
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _REDIRECT,
        "code_verifier": code_verifier,
    }, timeout=15)
    r.raise_for_status()
    data = r.json()

    username = _fetch_username(data["access_token"])

    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": time.time() + data.get("expires_in", 2764800),
        "username": username,
    }
    _save_tokens(tokens)

    # Verify tokens were saved
    saved = get_tokens()
    if not saved.get("username"):
        raise MALAuthError(
            f"Tokens guardados pero no se pueden leer de vuelta. "
            f"¿Problema de permisos en {config_path('mal_tokens.json')}?"
        )

    return tokens


def _fetch_username(access_token: str) -> str:
    try:
        r = requests.get(f"{_API_BASE}/users/@me",
                         headers={"Authorization": f"Bearer {access_token}"},
                         timeout=10)
        r.raise_for_status()
        username = r.json().get("name", "")
        if not username:
            raise MALAuthError("El servidor no devolvió un nombre de usuario.")
        return username
    except requests.RequestException as exc:
        raise MALAuthError(f"No se pudo obtener el perfil: {exc}") from exc
    except Exception as exc:
        raise MALAuthError(f"Error al procesar la respuesta del perfil: {exc}") from exc


def logout() -> None:
    p = _tokens_path()
    if p.exists():
        p.unlink()
