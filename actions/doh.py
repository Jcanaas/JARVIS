"""DNS-over-HTTPS helper to bypass ISP DNS blocks for specific domains.

Many ISPs (e.g. in Spain) block torrent/subtitle domains at the DNS level, so
they fail to resolve without a VPN. `enable_for("example.com")` makes requests
to matching hosts resolve via Cloudflare DoH (reached by IP, so it doesn't
depend on the poisoned system DNS) and connect to that IP; urllib3 keeps the
real hostname for SNI/cert validation, which defeats DNS-based blocks. IP-level
blocks would still need a VPN.

The patch is installed once, globally, but only rewrites hosts whose suffix was
registered via enable_for — all other traffic is untouched.
"""
from __future__ import annotations

from typing import Optional

import requests
import urllib3.util.connection as _conn

_DOH_ENDPOINTS = ["https://1.1.1.1/dns-query", "https://1.0.0.1/dns-query"]
_dns_cache: dict[str, str] = {}
_suffixes: set[str] = set()


def _resolve(hostname: str) -> Optional[str]:
    if hostname in _dns_cache:
        return _dns_cache[hostname]
    for endpoint in _DOH_ENDPOINTS:
        try:
            r = requests.get(
                endpoint,
                params={"name": hostname, "type": "A"},
                headers={"Accept": "application/dns-json"},
                timeout=8,
            )
            r.raise_for_status()
            for ans in r.json().get("Answer", []):
                if ans.get("type") == 1 and ans.get("data"):  # A record
                    _dns_cache[hostname] = ans["data"]
                    return ans["data"]
        except Exception:
            continue
    return None


_orig_create_connection = _conn.create_connection


def _create_connection_with_doh(address, *args, **kwargs):
    host, port = address
    if isinstance(host, str) and any(host == s or host.endswith("." + s) for s in _suffixes):
        ip = _resolve(host)
        if ip:
            address = (ip, port)
    return _orig_create_connection(address, *args, **kwargs)


def enable_for(*domain_suffixes: str) -> None:
    """Route the given domain suffixes through DoH resolution."""
    _suffixes.update(domain_suffixes)
    # Install the patch once; it's a no-op for unregistered hosts.
    if getattr(_conn.create_connection, "_jarvis_doh", False) is False:
        _create_connection_with_doh._jarvis_doh = True
        _conn.create_connection = _create_connection_with_doh
