"""Shared BitTorrent tracker announce list for magnet building.

Torrentio/Peerflix return only an infoHash; the magnet we build must carry a
robust tracker set or WebTorrent has to rely on DHT alone, which is slow and
often fails for lower-swarm releases ("half the torrents don't load").

bittorrent-tracker announces to every entry here in parallel (fire-and-forget
per tracker, see node_modules/bittorrent-tracker/client.js `_announce`), so
list ORDER doesn't affect latency — only which trackers are actually alive
does. This list was pruned by live-testing a raw BEP15 UDP "connect" handshake
against every candidate (2026-07-18): entries that didn't resolve in DNS or
never answered within 5s were dropped (tracker.openbittorrent.com,
tracker.tiny-vps.com, oh.fuuuuuck.com, tracker.moeking.me all NXDOMAIN;
ipv4.tracker.harry.lu resolved to 127.0.0.1, clearly broken; both HTTPS
entries — tamersunion.org, tracker.gbitt.info — were NXDOMAIN too, so no HTTPS
fallback is currently known-good). Re-run that probe periodically; public
tracker uptime rots over time.
"""
from __future__ import annotations

from urllib.parse import quote

TRACKERS: list[str] = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://open.tracker.cl:1337/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://explodie.org:6969/announce",
    "udp://tracker-udp.gbitt.info:80/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://opentracker.io:6969/announce",
    "udp://p4p.arenabg.com:1337/announce",
    "udp://tracker.dump.cl:6969/announce",
    "udp://tracker.bittor.pw:1337/announce",
]


def magnet_tracker_suffix() -> str:
    """Return the ``&tr=…`` query fragment to append to a magnet URI."""
    return "".join(f"&tr={quote(t)}" for t in TRACKERS)
