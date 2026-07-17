#cardtrader_api.py
"""Thin HTTP client for CardTrader API v2.

https://api.cardtrader.com/api/v2 — bearer token auth. Handles throttling,
retries on 429/5xx and typed errors so callers don't deal with raw requests.
"""
from __future__ import annotations

import json
import random
import threading
import time
from typing import Any

import requests

from actions import event_bus
from actions.paths import config_path

BASE_URL = "https://api.cardtrader.com/api/v2"

# Process-wide cache for /marketplace/products, shared across CardTraderClient
# instances (a fresh client is created per tool call). Re-searching the same
# card shortly after skips the rate limit entirely instead of re-querying
# every printing.
_MARKETPLACE_CACHE_TTL = 120.0
_marketplace_cache: dict[tuple, tuple[float, list]] = {}
_marketplace_cache_lock = threading.Lock()


class CardTraderError(Exception):
    pass


class AuthError(CardTraderError):
    pass


class NotFoundError(CardTraderError):
    pass


class RateLimitedError(CardTraderError):
    pass


class ApiError(CardTraderError):
    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        super().__init__(f"CardTrader API error {status}: {payload}")


class _TokenBucket:
    """Simple thread-safe rate limiter: max `rate` calls per `per` seconds."""

    def __init__(self, rate: int, per: float):
        self.rate = rate
        self.per = per
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._timestamps = [t for t in self._timestamps if now - t < self.per]
                if len(self._timestamps) < self.rate:
                    self._timestamps.append(now)
                    return
                wait = self.per - (now - self._timestamps[0])
            time.sleep(max(wait, 0.01))


def _load_token() -> str:
    path = config_path("api_keys.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise AuthError(f"No se pudo leer {path}: {e}") from e
    token = str(data.get("cardtrader_jwt") or "").strip()
    if not token:
        raise AuthError(
            "Falta el token de CardTrader. Consiguelo en cardtrader.com -> "
            "tu perfil -> Full API App, y ponlo en la clave 'cardtrader_jwt' "
            f"de {path}."
        )
    return token


class CardTraderClient:
    def __init__(self, token: str | None = None):
        self._token = token or _load_token()
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        })
        # Global ceiling well under the documented 200 req/10s.
        self._global_bucket = _TokenBucket(rate=15, per=1.0)
        # Marketplace endpoint has its own tighter documented limit.
        self._marketplace_bucket = _TokenBucket(rate=1, per=1.0)
        # Jobs endpoint: 1 req/s (unused today, kept for completeness).
        self._jobs_bucket = _TokenBucket(rate=1, per=1.0)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        bucket: _TokenBucket | None = None,
        max_retries: int = 4,
    ) -> Any:
        bucket = bucket or self._global_bucket
        url = f"{BASE_URL}{path}"
        attempt = 0
        while True:
            attempt += 1
            self._global_bucket.acquire()
            if bucket is not self._global_bucket:
                bucket.acquire()
            try:
                resp = self._session.request(
                    method, url, params=params, json=json_body,
                    timeout=(10, 60),
                )
            except requests.RequestException as e:
                if attempt > max_retries:
                    raise ApiError(0, str(e)) from e
                time.sleep(_backoff(attempt))
                continue

            if resp.status_code == 401:
                raise AuthError("Token de CardTrader invalido o caducado. Renuevalo en cardtrader.com -> Full API App.")
            if resp.status_code == 404:
                raise NotFoundError(f"CardTrader 404: {method} {path}")
            if resp.status_code == 429:
                if attempt > max_retries:
                    raise RateLimitedError("CardTrader sigue limitando tras varios reintentos.")
                event_bus.log("CardTrader", f"429 rate limited, reintento {attempt}/{max_retries}")
                time.sleep(_backoff(attempt))
                continue
            if resp.status_code >= 500:
                if attempt > max_retries:
                    raise ApiError(resp.status_code, resp.text)
                time.sleep(_backoff(attempt))
                continue
            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                except Exception:
                    payload = resp.text
                raise ApiError(resp.status_code, payload)

            if not resp.content:
                return None
            return resp.json()

    # -- endpoints -----------------------------------------------------

    def info(self) -> dict:
        return self._request("GET", "/info")

    def games(self) -> list[dict]:
        data = self._request("GET", "/games")
        if isinstance(data, dict) and "array" in data:
            return data["array"]
        return data or []

    def expansions(self) -> list[dict]:
        return self._request("GET", "/expansions")

    def blueprints_export(self, expansion_id: int) -> list[dict]:
        try:
            return self._request("GET", "/blueprints/export", params={"expansion_id": expansion_id})
        except NotFoundError:
            return []

    def marketplace_by_blueprint(
        self, blueprint_id: int, foil: bool | None = None, language: str | None = None,
    ) -> list[dict]:
        cache_key = (blueprint_id, foil, language)
        now = time.monotonic()
        with _marketplace_cache_lock:
            cached = _marketplace_cache.get(cache_key)
        if cached and (now - cached[0]) < _MARKETPLACE_CACHE_TTL:
            return cached[1]

        params: dict[str, Any] = {"blueprint_id": blueprint_id}
        if foil is not None:
            params["foil"] = str(foil).lower()
        if language:
            params["language"] = language
        data = self._request("GET", "/marketplace/products", params=params, bucket=self._marketplace_bucket)
        result = data.get(str(blueprint_id), []) if isinstance(data, dict) else []

        with _marketplace_cache_lock:
            _marketplace_cache[cache_key] = (now, result)
        return result

    def marketplace_by_expansion(self, expansion_id: int) -> dict:
        data = self._request(
            "GET", "/marketplace/products",
            params={"expansion_id": expansion_id},
            bucket=self._marketplace_bucket,
        )
        return data or {}

    def cart(self) -> dict:
        return self._request("GET", "/cart")

    def cart_add(self, product_id: int, quantity: int, via_zero: bool = True) -> dict:
        return self._request("POST", "/cart/add", json_body={
            "product_id": product_id,
            "quantity": quantity,
            "via_cardtrader_zero": via_zero,
        })

    def cart_remove(self, product_id: int, quantity: int) -> dict:
        return self._request("POST", "/cart/remove", json_body={
            "product_id": product_id,
            "quantity": quantity,
        })


def _backoff(attempt: int) -> float:
    return min(2 ** attempt, 20) + random.uniform(0, 0.5)
