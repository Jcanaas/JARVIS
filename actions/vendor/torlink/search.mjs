// Vendored torrent-search sources, adapted from torlink (baairon/torlink, MIT).
// https://github.com/baairon/torlink — search-only subset (no TUI, no download
// engine): YTS, The Pirate Bay and 1337x, each returning a normalized
// TorrentResult. Invoked by actions/torrent_search.py as a subprocess:
//   node search.mjs "<query>" --kind movie|tv --limit N
// and prints a JSON array to stdout.

const USER_AGENT = "Mark-XXXIX (+https://github.com/baairon/torlink)";

const TRACKERS = [
  "udp://tracker.opentrackr.org:1337/announce",
  "udp://open.demonii.com:1337/announce",
  "udp://tracker.openbittorrent.com:6969/announce",
  "udp://tracker.torrent.eu.org:451/announce",
  "udp://exodus.desync.com:6969/announce",
  "udp://open.stealth.si:80/announce",
  "udp://tracker.dler.org:6969/announce",
];

function buildMagnet(infoHash, name) {
  const dn = encodeURIComponent(name);
  const tr = TRACKERS.map((t) => `&tr=${encodeURIComponent(t)}`).join("");
  return `magnet:?xt=urn:btih:${infoHash}&dn=${dn}${tr}`;
}

function unescapeEntities(s) {
  return s
    .replace(/&#0?38;|&amp;/g, "&")
    .replace(/&#8211;|&#8212;/g, "-")
    .replace(/&#8217;|&#0?39;|&apos;/g, "'")
    .replace(/&#8220;|&#8221;|&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

const SIZE_UNITS = {
  B: 1, KIB: 1024, MIB: 1024 ** 2, GIB: 1024 ** 3, TIB: 1024 ** 4,
  KB: 1000, MB: 1e6, GB: 1e9, TB: 1e12,
};

function parseSize(s) {
  const m = s.match(/([\d.]+)\s*([KMGT]?I?B)/i);
  if (!m) return 0;
  return Math.round(parseFloat(m[1]) * (SIZE_UNITS[m[2].toUpperCase()] ?? 1));
}

function formatBytes(bytes) {
  if (!bytes || !Number.isFinite(bytes) || bytes <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : 2)} ${units[i]}`;
}

async function fetchWithRetries(url, opts = {}, retries = 2) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const res = await fetch(url, { headers: { "User-Agent": USER_AGENT }, ...opts });
      if (res.ok) return res;
      lastError = new Error(`HTTP ${res.status} for ${url}`);
    } catch (e) {
      lastError = e;
    }
    if (attempt < retries) await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
  }
  throw lastError;
}

// --- YTS (movies) ---------------------------------------------------------
const YTS_HOSTS = ["yts.mx", "yts.am", "yts.rs"];

async function searchYts(query) {
  const params = new URLSearchParams({ limit: "50", query_term: query });
  let lastError;
  for (const host of YTS_HOSTS) {
    try {
      const res = await fetchWithRetries(
        `https://${host}/api/v2/list_movies.json?${params.toString()}`, {}, 1,
      );
      const json = await res.json();
      const out = [];
      for (const movie of json.data?.movies ?? []) {
        const base = movie.title_long || movie.title || "Unknown";
        for (const t of movie.torrents ?? []) {
          if (!t.hash) continue;
          const infoHash = t.hash.toLowerCase();
          const tag = [t.quality, t.type].filter(Boolean).join(" ");
          const name = tag ? `${base} [${tag}]` : base;
          out.push({
            name, infoHash, source: "yts",
            sizeBytes: t.size_bytes ?? 0,
            seeders: t.seeds ?? 0,
            leechers: t.peers ?? 0,
            magnet: buildMagnet(infoHash, name),
            added: movie.date_uploaded_unix,
          });
        }
      }
      return out;
    } catch (e) {
      lastError = e;
    }
  }
  throw lastError ?? new Error("YTS unreachable");
}

// --- The Pirate Bay (movies + tv) ----------------------------------------
const TPB_API = "https://apibay.org";
const TPB_MOVIE_CATS = new Set([201, 202, 207, 209]);
const TPB_TV_CATS = new Set([205, 208]);
const ZERO_HASH = "0000000000000000000000000000000000000000";

async function searchPirateBay(query, kind) {
  const res = await fetchWithRetries(`${TPB_API}/q.php?q=${encodeURIComponent(query)}`, {}, 1);
  const items = await res.json();
  if (!Array.isArray(items)) return [];
  const cats = kind === "tv" ? TPB_TV_CATS : TPB_MOVIE_CATS;
  const out = [];
  for (const it of items) {
    const infoHash = (it.info_hash ?? "").toLowerCase();
    if (!infoHash || infoHash === ZERO_HASH || it.id === "0") continue;
    if (!cats.has(Number(it.category))) continue;
    const name = it.name || "Unknown";
    out.push({
      name, infoHash, source: "tpb",
      sizeBytes: Number(it.size) || 0,
      seeders: Number(it.seeders) || 0,
      leechers: Number(it.leechers) || 0,
      magnet: buildMagnet(infoHash, name),
      added: Number(it.added) || undefined,
    });
  }
  return out;
}

// --- 1337x (movies + tv) ---------------------------------------------------
const X1337_HOSTS = ["1337x.to", "1337x.st", "x1337x.ws", "1337xx.to"];
const STOP = new Set(["the", "a", "an", "of", "and", "or", "to"]);
const MAX_DETAILS = 8;

function parseRows(html) {
  const start = html.indexOf("table-list");
  if (start < 0) return [];
  const out = [];
  for (const tr of html.slice(start).split(/<tr[\s>]/i).slice(1)) {
    const link = tr.match(/href="(\/torrent\/[^"]+)"[^>]*>([^<]+)<\/a>/i);
    if (!link) continue;
    const size = tr.match(/class="coll-4 size[^"]*">\s*([\d.]+\s*[KMGT]i?B)/i)?.[1] ?? "";
    out.push({
      name: unescapeEntities(link[2].trim()),
      path: link[1],
      seeders: Number(tr.match(/class="coll-2 seeds[^"]*">\s*(\d+)/i)?.[1] ?? 0),
      leechers: Number(tr.match(/class="coll-3 leeches[^"]*">\s*(\d+)/i)?.[1] ?? 0),
      sizeBytes: parseSize(size),
    });
  }
  return out;
}

async function detailMagnet(base, path) {
  try {
    const res = await fetchWithRetries(`${base}${path}`, {}, 1);
    const html = await res.text();
    const raw = html.match(/magnet:\?xt=urn:btih:[^"'<>\s]+/i)?.[0];
    return raw ? unescapeEntities(raw) : null;
  } catch {
    return null;
  }
}

async function searchX1337(query, kind) {
  const cat = kind === "tv" ? "TV" : "Movies";
  const path = `/category-search/${encodeURIComponent(query).replace(/%20/g, "+")}/${cat}/1/`;

  let base = "", html = "", lastError;
  for (const host of X1337_HOSTS) {
    try {
      const candidate = `https://${host}`;
      const res = await fetchWithRetries(`${candidate}${path}`, {}, 1);
      html = await res.text();
      base = candidate;
      break;
    } catch (e) {
      lastError = e;
    }
  }
  if (!base) throw lastError ?? new Error("1337x unreachable");

  const all = parseRows(html);
  const tokens = query.toLowerCase().split(/\s+/).filter(Boolean);
  const meaningful = tokens.filter((t) => !STOP.has(t));
  const need = meaningful.length ? meaningful : tokens;
  const matched = need.length
    ? all.filter((r) => {
        const n = r.name.toLowerCase();
        return need.every((t) => n.includes(t));
      })
    : all;
  matched.sort((a, b) => b.seeders - a.seeders);
  const rows = matched.slice(0, MAX_DETAILS);

  const settled = await Promise.all(
    rows.map(async (row) => {
      const magnet = await detailMagnet(base, row.path);
      const infoHash = magnet?.match(/urn:btih:([a-zA-Z0-9]+)/i)?.[1]?.toLowerCase();
      if (!magnet || !infoHash) return null;
      return {
        name: row.name, infoHash, source: "1337x",
        sizeBytes: row.sizeBytes,
        seeders: row.seeders,
        leechers: row.leechers,
        magnet,
      };
    }),
  );
  return settled.filter((r) => r !== null);
}

// --- CLI entry point --------------------------------------------------------

function parseArgs(argv) {
  const args = { query: "", kind: "movie", limit: 10 };
  const rest = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--kind") args.kind = argv[++i];
    else if (a === "--limit") args.limit = parseInt(argv[++i], 10) || 10;
    else if (a === "--json") continue;
    else rest.push(a);
  }
  args.query = rest.join(" ").trim();
  return args;
}

async function main() {
  const { query, kind, limit } = parseArgs(process.argv.slice(3)); // skip "node search.mjs search"
  if (!query) {
    console.error("Empty query");
    process.exit(1);
  }

  const sources = kind === "tv"
    ? [searchPirateBay(query, "tv"), searchX1337(query, "tv")]
    : [searchYts(query), searchPirateBay(query, "movie"), searchX1337(query, "movie")];

  const settled = await Promise.allSettled(sources);
  const merged = [];
  for (const s of settled) {
    if (s.status === "fulfilled") merged.push(...s.value);
  }

  if (merged.length === 0) {
    console.error("No results from any source");
    process.exit(1);
  }

  merged.sort((a, b) => b.seeders - a.seeders);
  const top = merged.slice(0, limit).map((r) => ({
    name: r.name,
    magnet: r.magnet,
    seeders: r.seeders,
    leechers: r.leechers,
    size: formatBytes(r.sizeBytes),
    source: r.source,
  }));

  process.stdout.write(JSON.stringify(top));
}

main().catch((e) => {
  console.error(String(e && e.message ? e.message : e));
  process.exit(1);
});
