// Persistent WebTorrent streaming daemon for Jarvis movie playback.
//
// Replaces the old model (spawn stream.mjs fresh per play, kill it, spawn a
// new one for the next). This process is launched ONCE and stays alive across
// plays: one shared WebTorrent client (and its DHT node + HTTP server) serves
// every torrent in the session, one at a time. Only the DHT routing table
// actually carries over between plays (each tracker announce still does its
// own fresh BEP15 handshake — bittorrent-tracker doesn't cache connection IDs
// across Torrent instances, verified by reading its source), so the win here
// is smaller than "instant replays" — mainly a warm DHT routing table plus
// skipping Node's ~200-500ms startup cost per play. Still a real, free
// improvement with no downside.
//
// Protocol: newline-delimited JSON on stdin/stdout.
//
// Commands (stdin):
//   {"cmd":"play","id":"<reqid>","magnet":"...","fileIndex":-1}
//   {"cmd":"stop","id":"<reqid>"}
//   {"cmd":"shutdown","id":"<reqid>"}
//
// Events (stdout), tagged with the triggering command's "id":
//   {"id":"...","type":"progress","peers":N,"elapsed":S}
//   {"id":"...","type":"ready","url":"...","name":"..."}
//   {"id":"...","type":"error","message":"..."}
//   {"id":"...","type":"stopped"}
//   {"id":"...","type":"shutdown"}   (written right before the process exits)
//
// Only one torrent is ever active: a "play" while another is active destroys
// the old one first (client.remove), on the same client/DHT so routing state
// survives the swap.

import readline from "node:readline";
import WebTorrent from "webtorrent";

const VIDEO_EXT = /\.(mkv|mp4|avi|mov|webm|m4v|flv|wmv|mpg|mpeg|ts)$/i;
const READY_TIMEOUT_MS = 120000;
const PROGRESS_INTERVAL_MS = 4000;

// Mirrors actions/trackers.py TRACKERS exactly — see that file's docstring
// for how this list was pruned (live BEP15 probe, dead entries removed).
const ANNOUNCE = [
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
];

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

const client = new WebTorrent();
client.on("error", (err) => {
  // Client-level errors aren't tied to a request; log for diagnostics but
  // don't crash the daemon — a torrent-level error surfaces via its own
  // "play" request's error event instead.
  process.stderr.write(`[daemon] client error: ${err.message || err}\n`);
});

// One persistent HTTP server for the whole daemon lifetime; it looks up the
// requested infoHash against whatever's currently in the client, so swapping
// the active torrent naturally invalidates old URLs (matches prior behavior:
// only one thing plays at a time). createServer() only builds the server —
// it does NOT start listening on its own, unlike the old per-play script
// which called .listen() itself; forgetting this here means .address()
// returns null and the daemon crashes the instant the first torrent's
// metadata arrives.
const httpServer = client.createServer();
httpServer.server.on("error", (err) => {
  process.stderr.write(`[daemon] http server error: ${err.message || err}\n`);
});
httpServer.listen(0, "127.0.0.1");

let current = null;      // { torrent, magnet } | null
let activeReq = null;    // { id, readyTimer, progressTimer, startedAt } | null

function clearActiveReq() {
  if (activeReq) {
    clearTimeout(activeReq.readyTimer);
    clearInterval(activeReq.progressTimer);
    activeReq = null;
  }
}

function destroyCurrent(cb) {
  if (!current) return cb();
  const t = current.torrent;
  current = null;
  try {
    client.remove(t.infoHash, { destroyStore: false }, () => cb());
  } catch {
    cb();
  }
}

function handlePlay(id, magnet, fileIndex) {
  clearActiveReq();
  destroyCurrent(() => {
    const startedAt = Date.now();
    activeReq = { id, readyTimer: null, progressTimer: null, startedAt };

    activeReq.readyTimer = setTimeout(() => {
      if (!activeReq || activeReq.id !== id) return;
      clearActiveReq();
      destroyCurrent(() => {});
      send({ id, type: "error", message: "Torrent metadata timed out (no seeders o red lenta)." });
    }, READY_TIMEOUT_MS);

    activeReq.progressTimer = setInterval(() => {
      if (!activeReq || activeReq.id !== id) return;
      const t = current ? current.torrent : client.torrents.find((x) => x.infoHash);
      const peers = t ? t.numPeers : 0;
      send({ id, type: "progress", peers, elapsed: Math.round((Date.now() - startedAt) / 1000) });
    }, PROGRESS_INTERVAL_MS);

    let torrent;
    try {
      torrent = client.add(magnet, { announce: ANNOUNCE }, (t) => {
        if (!activeReq || activeReq.id !== id) return; // superseded by a newer play/stop
        clearActiveReq();

        const videos = t.files.filter((f) => VIDEO_EXT.test(f.name));
        const pool = videos.length ? videos : t.files;
        if (!pool.length) {
          send({ id, type: "error", message: "Torrent has no playable files" });
          destroyCurrent(() => {});
          return;
        }

        let file;
        if (fileIndex >= 0 && fileIndex < t.files.length &&
            VIDEO_EXT.test(t.files[fileIndex].name)) {
          file = t.files[fileIndex];
        } else {
          file = pool.reduce((a, b) => (b.length > a.length ? b : a));
        }
        t.files.forEach((f) => f.deselect());
        file.select();

        const normalized = file.path.replace(/\\/g, "/");
        const encodedPath = encodeURI(normalized);
        const port = httpServer.address().port;
        const url = `http://127.0.0.1:${port}/webtorrent/${t.infoHash}/${encodedPath}`;
        send({ id, type: "ready", url, name: file.name });
      });
    } catch (err) {
      clearActiveReq();
      send({ id, type: "error", message: `WebTorrent error: ${err.message || err}` });
      return;
    }
    current = { torrent, magnet };
  });
}

function handleStop(id) {
  clearActiveReq();
  destroyCurrent(() => send({ id, type: "stopped" }));
}

function handleShutdown(id) {
  clearActiveReq();
  destroyCurrent(() => {
    send({ id, type: "shutdown" });
    try {
      httpServer.close();
    } catch {}
    try {
      client.destroy(() => process.exit(0));
    } catch {
      process.exit(0);
    }
    // Belt-and-braces: force exit if client.destroy hangs.
    setTimeout(() => process.exit(0), 3000).unref();
  });
}

const rl = readline.createInterface({ input: process.stdin, terminal: false });
rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch (err) {
    send({ type: "fatal", message: `Invalid command JSON: ${err.message}` });
    return;
  }
  const { cmd, id } = msg;
  if (cmd === "play") {
    handlePlay(id, msg.magnet, Number.isInteger(msg.fileIndex) ? msg.fileIndex : -1);
  } else if (cmd === "stop") {
    handleStop(id);
  } else if (cmd === "shutdown") {
    handleShutdown(id);
  } else {
    send({ id, type: "error", message: `Unknown command: ${cmd}` });
  }
});

// If stdin closes (parent process died), shut down cleanly instead of
// lingering as an orphaned process holding a torrent + DHT node open.
rl.on("close", () => handleShutdown(null));

for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => handleShutdown(null));
}

process.stderr.write("[daemon] ready\n");
