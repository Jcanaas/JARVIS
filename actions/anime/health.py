"""Real swarm health via BEP15 UDP scrape.

The seeder counts our sources report are not trustworthy. Measured against
live trackers on 2026-09-05:

    fuente dice   real
        541    ->    0     [HorribleSubs] Shingeki no Kyojin S2 - 26 [720p]
       1209    ->  132     [SubsPlease] Sousou no Frieren - 01 (1080p)
        298    ->   35     [SubsPlease] One Piece - 1044 (1080p)
        152    ->    6     [MTBB] Shingeki no Kyojin - 01 (BD 1080p)

Inflated by ~10x across the board, and in the worst case pure fiction: a
release advertised with 541 seeders had *none*. Ranking by those numbers put
a completely dead torrent at the top of the list, which is what "nada me
encuentra seeds" actually was — not a tracker problem (our tracker list was
separately verified to find the same peers as the anime-specific ones).

BEP15 scrape asks a tracker for seeder/leecher counts without joining the
swarm, and one packet can carry many infohashes, so checking a whole result
page costs one request per tracker rather than one per torrent.
"""
from __future__ import annotations

import random
import socket
import struct
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from actions.anime import cache
from actions.trackers import TRACKERS

_PROTOCOL_ID = 0x41727101980
_TIMEOUT = 4.0
# BEP15 caps a scrape at 74 infohashes per packet.
_MAX_PER_PACKET = 70
# Swarm size drifts slowly and this runs on every result page, so a short
# cache keeps repeat views instant without going stale enough to matter.
_TTL = 600


def _scrape_one(tracker: str, hashes: list[bytes]) -> dict[bytes, int]:
    """{infohash: seeders} from one tracker, or {} on any failure."""
    parsed = urllib.parse.urlparse(tracker)
    if parsed.scheme != "udp" or not parsed.hostname:
        return {}
    try:
        ip = socket.gethostbyname(parsed.hostname)
    except OSError:
        return {}

    addr = (ip, parsed.port or 80)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(_TIMEOUT)
    try:
        tid = random.randint(0, 2**31 - 1)
        sock.sendto(struct.pack(">QII", _PROTOCOL_ID, 0, tid), addr)
        data, _ = sock.recvfrom(2048)
        if len(data) < 16:
            return {}
        action, _rtid, connection_id = struct.unpack(">IIQ", data[:16])
        if action != 0:
            return {}

        out: dict[bytes, int] = {}
        for chunk_start in range(0, len(hashes), _MAX_PER_PACKET):
            chunk = hashes[chunk_start:chunk_start + _MAX_PER_PACKET]
            req = struct.pack(">QII", connection_id, 2,
                              random.randint(0, 2**31 - 1)) + b"".join(chunk)
            sock.sendto(req, addr)
            resp, _ = sock.recvfrom(8192)
            if len(resp) < 8 or struct.unpack(">I", resp[:4])[0] != 2:
                continue
            body = resp[8:]
            for i, info_hash in enumerate(chunk):
                off = i * 12
                if off + 12 > len(body):
                    break
                seeders, _completed, _leechers = struct.unpack(
                    ">III", body[off:off + 12])
                out[info_hash] = seeders
        return out
    except (OSError, struct.error):
        return {}
    finally:
        sock.close()


def scrape_seeders(info_hashes: list[bytes]) -> dict[bytes, int]:
    """Live seeder count per infohash, from whichever trackers answer.

    Takes the highest count any tracker reports: each tracker only sees the
    peers that announced to it, so the maximum is the closest single estimate
    of the real swarm. A hash no tracker knows comes back 0 — genuinely
    unseeded as far as we can tell.

    Returns {} if every tracker fails (blocked UDP, offline), which callers
    must treat as "unknown", not as "all dead".
    """
    unique = list({h for h in info_hashes if h and len(h) == 20})
    if not unique:
        return {}

    with ThreadPoolExecutor(max_workers=min(13, len(TRACKERS))) as pool:
        per_tracker = list(pool.map(lambda t: _scrape_one(t, unique), TRACKERS))

    merged: dict[bytes, int] = {}
    for result in per_tracker:
        for info_hash, seeders in result.items():
            if seeders > merged.get(info_hash, -1):
                merged[info_hash] = seeders
    return merged


def scrape_seeders_cached(info_hashes: list[bytes]) -> dict[bytes, int]:
    """scrape_seeders() with a short per-infohash disk cache."""
    unique = list({h for h in info_hashes if h and len(h) == 20})
    if not unique:
        return {}

    out: dict[bytes, int] = {}
    missing: list[bytes] = []
    for info_hash in unique:
        hit = cache.get("swarm", info_hash.hex(), ttl=_TTL)
        if hit is None:
            missing.append(info_hash)
        else:
            out[info_hash] = int(hit)

    if missing:
        fresh = scrape_seeders(missing)
        for info_hash, seeders in fresh.items():
            # 0 is a real, useful answer ("dead swarm"), and cache.get only
            # returns None for a genuine miss, so storing it is safe.
            cache.set("swarm", info_hash.hex(), seeders)
            out[info_hash] = seeders
    return out
