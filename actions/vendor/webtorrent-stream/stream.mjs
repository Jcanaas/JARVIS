// Minimal WebTorrent HTTP streaming server for Jarvis.
//
// Given a magnet link, this adds the torrent, picks the largest video file,
// and serves it over HTTP so a player (VLC) can stream it while it downloads.
//
// Usage:
//   node stream.mjs "<magnet>" [--port N]
//
// On success it prints a single JSON line to stdout and keeps running:
//   {"url":"http://127.0.0.1:8888/0/Movie.mkv","name":"Movie.mkv","port":8888}
//
// The process must stay alive for the duration of playback; the Python side
// (actions/vlc_player.py) launches it, reads that line, and kills it when done.

import WebTorrent from "webtorrent";

const VIDEO_EXT = /\.(mkv|mp4|avi|mov|webm|m4v|flv|wmv|mpg|mpeg|ts)$/i;
const DEFAULT_PORT = 0; // 0 = ephemeral: OS picks a free port, avoiding clashes
const READY_TIMEOUT_MS = 45000; // metadata must arrive within this window

function parseArgs(argv) {
  let magnet = "";
  let port = DEFAULT_PORT;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--port") port = parseInt(argv[++i], 10) || DEFAULT_PORT;
    else if (!magnet) magnet = argv[i];
  }
  return { magnet, port };
}

function fail(message) {
  process.stderr.write(String(message) + "\n");
  process.exit(1);
}

const { magnet, port } = parseArgs(process.argv.slice(2));
if (!magnet) fail("No magnet link provided");

const client = new WebTorrent();

client.on("error", (err) => fail(`WebTorrent error: ${err.message || err}`));

const readyTimer = setTimeout(() => {
  fail("Torrent metadata timed out (no peers?)");
}, READY_TIMEOUT_MS);

client.add(magnet, (torrent) => {
  clearTimeout(readyTimer);

  // Pick the largest video file; fall back to the largest file overall.
  const videos = torrent.files.filter((f) => VIDEO_EXT.test(f.name));
  const pool = videos.length ? videos : torrent.files;
  if (!pool.length) fail("Torrent has no playable files");

  const file = pool.reduce((a, b) => (b.length > a.length ? b : a));

  // Deselect everything, then prioritize only the chosen file so we don't waste
  // bandwidth downloading extras (samples, other episodes, etc.).
  torrent.files.forEach((f) => f.deselect());
  file.select();

  // WebTorrent v2 serves files at <pathname>/<infoHash>/<file.path> from a
  // server created on the client (not the torrent).
  const server = client.createServer();
  const httpServer = server.server;

  httpServer.on("error", (err) => fail(`Stream server error: ${err.message || err}`));

  httpServer.listen(port, "127.0.0.1", () => {
    // With an ephemeral port the OS assigns the real one; read it back.
    const actualPort = httpServer.address().port;
    // The server matches file.path with backslashes normalized to '/', decoded
    // via decodeURI — so build the URL with forward slashes and encodeURI (its
    // inverse) so a Windows file.path like "Dir\movie.mp4" resolves correctly.
    const normalized = file.path.replace(/\\/g, "/");
    const encodedPath = encodeURI(normalized);
    const url = `http://127.0.0.1:${actualPort}/webtorrent/${torrent.infoHash}/${encodedPath}`;
    process.stdout.write(
      JSON.stringify({ url, name: file.name, port: actualPort }) + "\n",
    );
  });
});

// Clean shutdown on signals so the OS port is released promptly.
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => {
    try {
      client.destroy(() => process.exit(0));
    } catch {
      process.exit(0);
    }
  });
}
