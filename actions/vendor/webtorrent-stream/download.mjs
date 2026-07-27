// Minimal WebTorrent-to-disk downloader for Jarvis.
//
// Given a magnet link, this adds the torrent and saves it to a destination
// folder, emitting progress as JSON lines to stdout. Used for both game
// repacks (all files) and "save instead of stream" for movies/anime
// (--file-index picks a single file, mirroring stream.mjs).
//
// Usage:
//   node download.mjs "<magnet>" --dest "<dir>" [--file-index N]
//
// Emits one JSON line per progress tick while downloading:
//   {"progress":0.42,"downloadSpeed":1234567,"downloaded":123,"total":456,"done":false}
// and a final line when complete:
//   {"done":true,"path":"<dest>/<name>"}
// or on error, a single line to stderr and exit(1).

import WebTorrent from "webtorrent";

const READY_TIMEOUT_MS = 45000; // metadata must arrive within this window
const PROGRESS_INTERVAL_MS = 1000;

function parseArgs(argv) {
  let magnet = "";
  let dest = "";
  let fileIndex = -1; // -1 = download every file (default for repacks)
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--dest") dest = argv[++i] || "";
    else if (argv[i] === "--file-index") {
      const v = parseInt(argv[++i], 10);
      fileIndex = Number.isInteger(v) ? v : -1;
    } else if (!magnet) magnet = argv[i];
  }
  return { magnet, dest, fileIndex };
}

function fail(message) {
  process.stderr.write(String(message) + "\n");
  process.exit(1);
}

const { magnet, dest, fileIndex } = parseArgs(process.argv.slice(2));
if (!magnet) fail("No magnet link provided");
if (!dest) fail("No destination directory provided (--dest)");

const client = new WebTorrent();
client.on("error", (err) => fail(`WebTorrent error: ${err.message || err}`));

const readyTimer = setTimeout(() => {
  fail("Torrent metadata timed out (no peers?)");
}, READY_TIMEOUT_MS);

client.add(magnet, { path: dest }, (torrent) => {
  clearTimeout(readyTimer);

  if (fileIndex >= 0 && fileIndex < torrent.files.length) {
    // Single-file mode (movies/anime "save instead of stream"): deselect
    // everything else so we don't pull down unrelated files in the torrent.
    torrent.files.forEach((f) => f.deselect());
    torrent.files[fileIndex].select();
  }
  // Otherwise (repacks): leave WebTorrent's default of selecting every file.

  const tick = setInterval(() => {
    process.stdout.write(JSON.stringify({
      progress: torrent.progress,
      downloadSpeed: torrent.downloadSpeed,
      downloaded: torrent.downloaded,
      total: torrent.length,
      done: false,
    }) + "\n");
  }, PROGRESS_INTERVAL_MS);

  torrent.on("done", () => {
    clearInterval(tick);
    process.stdout.write(JSON.stringify({
      done: true,
      path: `${dest}/${torrent.name}`,
    }) + "\n");
    client.destroy(() => process.exit(0));
  });
});

// Clean shutdown on signals (user-cancelled download) so partial state doesn't
// keep the OS port/handles open.
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => {
    try {
      client.destroy(() => process.exit(0));
    } catch {
      process.exit(0);
    }
  });
}
