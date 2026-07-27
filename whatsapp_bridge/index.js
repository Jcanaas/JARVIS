const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const express = require('express');
const bodyParser = require('body-parser');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {
  loadResolveSources,
  dedupeLinkedIdentityCandidates,
} = require('./contact_resolver');

const app = express();
app.use(bodyParser.json());

// Writable data dir: provided by the Python launcher (DATA_DIR/whatsapp_bridge)
// so the session/token survive when the app is installed read-only. Falls back
// to the script folder when run standalone in development.
const DATA_DIR = process.env.JARVIS_WA_DATA || __dirname;
try { fs.mkdirSync(DATA_DIR, { recursive: true }); } catch (e) {}

// --- Security: generate and load bridge token ---
const TOKEN_FILE = path.join(DATA_DIR, 'bridge_token');

function getOrCreateToken() {
  try {
    const token = fs.readFileSync(TOKEN_FILE, 'utf-8').trim();
    if (token && token.length > 20) return token;
  } catch (e) {
    // File doesn't exist or is invalid
  }
  const newToken = crypto.randomBytes(32).toString('hex');
  try {
    fs.writeFileSync(TOKEN_FILE, newToken, 'utf-8');
  } catch (e) {
    console.error('Failed to save bridge token:', e);
  }
  return newToken;
}

const BRIDGE_TOKEN = getOrCreateToken();

// Middleware: validate token on all write endpoints
function requireToken(req, res, next) {
  const token = req.get('X-Bridge-Token') || '';
  if (token !== BRIDGE_TOKEN) {
    return res.status(401).json({ ok: false, error: 'Unauthorized' });
  }
  next();
}

const MAX_BUFFERED_MESSAGES = 1000;
let messages = [];
let latestQR = null;
let isReady = false;
let reconnecting = false;
// Coarse lifecycle state surfaced to the Python UI so it can show a proper
// loading screen instead of a bare "waiting for QR" that never resolves:
//   starting   → Chromium launching / WhatsApp Web loading, no QR yet
//   qr         → a QR code is available to scan (latestQR set)
//   loading    → phone linked, WhatsApp Web syncing (loading_screen events)
//   ready      → fully connected
//   auth_failure / disconnected → transient error, auto-reconnecting
let clientState = 'starting';
let stateDetail = '';
let initWatchdog = null;

function setState(state, detail) {
  clientState = state;
  stateDetail = detail || '';
}
const messageAcks = new Map();
// Our own per-chat unread counter. whatsapp-web.js `chat.unreadCount` is
// unreliable for messages that arrive during the session (often stays 0 until a
// full resync), so we track unread ourselves: increment on each incoming
// message and reset when the chat is read (mark_read) or answered (fromMe).
const unreadByChat = new Map();  // chatId -> unread count

// Caches to avoid hammering WhatsApp servers with repeated lookups.
const nameCache = new Map();        // id -> display name (or null)
const profilePicCache = new Map();  // id -> { url, ts }
const PROFILE_PIC_TTL_MS = 10 * 60 * 1000;

// --- Persistence: keep the message buffer/acks across bridge restarts ---
const STATE_FILE = path.join(DATA_DIR, 'bridge_state.json');
let _saveTimer = null;
let _saveDirty = false;

function loadState() {
  try {
    const raw = fs.readFileSync(STATE_FILE, 'utf8');
    const data = JSON.parse(raw);
    if (Array.isArray(data.messages)) {
      messages = data.messages.slice(-MAX_BUFFERED_MESSAGES);
    }
    if (data.acks && typeof data.acks === 'object') {
      for (const [id, ack] of Object.entries(data.acks)) {
        if (ack !== null && ack !== undefined) messageAcks.set(id, ack);
      }
    }
    if (data.unread && typeof data.unread === 'object') {
      for (const [id, n] of Object.entries(data.unread)) {
        const count = parseInt(n, 10);
        if (count > 0) unreadByChat.set(id, count);
      }
    }
    console.log(`Restored ${messages.length} messages and ${messageAcks.size} acks from disk`);
  } catch (e) {
    // No state file yet, or it is corrupt — start clean.
  }
}

function _writeState() {
  _saveTimer = null;
  if (!_saveDirty) return;
  _saveDirty = false;
  const payload = JSON.stringify({
    messages: messages.slice(-MAX_BUFFERED_MESSAGES),
    acks: Object.fromEntries(messageAcks),
    unread: Object.fromEntries(unreadByChat),
  });
  const tmp = `${STATE_FILE}.tmp`;
  try {
    fs.writeFileSync(tmp, payload);
    fs.renameSync(tmp, STATE_FILE);  // atomic replace
  } catch (e) {
    console.error('failed to persist bridge state:', e && e.message ? e.message : e);
  }
}

// Debounced save: at most one disk write every 5s, regardless of traffic.
function persistState() {
  _saveDirty = true;
  if (_saveTimer) return;
  _saveTimer = setTimeout(_writeState, 5000);
}

function flushState() {
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveDirty = true;
  _writeState();
}

// Installed builds don't ship puppeteer's Chromium (it lives in the dev
// machine's ~/.cache/puppeteer), so point puppeteer at a system browser.
// Chrome first, then Edge (present on every Windows 10/11). Returning
// undefined keeps puppeteer's own cache lookup for development.
function findSystemChrome() {
  const candidates = [
    process.env.JARVIS_CHROME,
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    process.env.LOCALAPPDATA
      && path.join(process.env.LOCALAPPDATA, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].filter(Boolean);
  for (const c of candidates) {
    try { if (fs.existsSync(c)) return c; } catch (e) {}
  }
  return undefined;
}

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: path.join(DATA_DIR, '.wwebjs_auth') }),
  takeoverOnConflict: true,   // keep this session instead of getting kicked out
  takeoverTimeoutMs: 10000,
  // Pin the WhatsApp Web build. Two reasons: (1) it stops WhatsApp Web's
  // periodic self-update reload, which detaches the puppeteer frame the
  // injected Store lives in; (2) the stored session is bound to the web build
  // it was linked on — changing the version forces a QR re-scan, so keeping a
  // stable pin lets the session resume across restarts. Must stay recent enough
  // that WhatsApp still accepts it for login (an old build forces re-link).
  //
  // NOTE: pinning does NOT fix the getChats/fetchMessages/downloadMedia "r"
  // error seen on this (LID-migrated) account — that is a whatsapp-web.js vs
  // current-WhatsApp-Web incompatibility with no upstream fix yet. The chat
  // list and open-conversation views fall back to the buffered /messages, so
  // they work; full history and on-demand media stay degraded until upstream
  // catches up.
  webVersion: '2.3000.1043180520-alpha',
  webVersionCache: {
    type: 'remote',
    remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/{version}.html',
    strict: false,
  },
  puppeteer: {
    executablePath: findSystemChrome(),
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  },
});

function clientReady() {
  return !!(isReady && client.info && client.info.wid && pageAlive());
}

// The puppeteer page can die under us (WhatsApp Web reloads or the renderer
// crashes) while wwebjs keeps 'ready' state: every pupPage.evaluate then
// throws "Attempted to use detached Frame", events stop arriving, and the
// bridge silently serves the stale buffer. Detect that state cheaply.
function pageAlive() {
  const page = client.pupPage;
  if (!page || page.isClosed()) return false;
  const frame = page.mainFrame();
  if (!frame) return false;
  if (typeof frame.isDetached === 'function') return !frame.isDetached();
  if (typeof frame.detached === 'boolean') return !frame.detached;
  return true;
}

// client.destroy() does not reliably kill the browser when the page is in
// that detached state — the orphaned Chrome keeps the profile's Singleton
// lock and every re-initialize() fails with "Reintentando iniciar el
// navegador…" forever. Kill the whole tree by PID before relaunching.
let browserPid = null;
function killBrowserTree() {
  let pid = null;
  try {
    const proc = client.pupBrowser && client.pupBrowser.process();
    pid = proc ? proc.pid : null;
  } catch (e) {
    // browser handle already torn down
  }
  pid = pid || browserPid;
  browserPid = null;
  if (!pid) return;
  try {
    if (process.platform === 'win32') {
      require('child_process').execSync(`taskkill /PID ${pid} /T /F`, { stdio: 'ignore' });
    } else {
      process.kill(pid, 'SIGKILL');
    }
    console.warn(`Killed stale browser tree (pid ${pid})`);
  } catch (e) {
    // already gone
  }
}

// Chromium leaves Singleton* lock files in its user-data dir. If the previous
// bridge (or its Chromium) was killed abruptly, those locks can make the next
// launch hang or fail to spawn the browser — the classic "it works after a
// restart" symptom. Since we only reach here after successfully binding the
// HTTP port (so no other bridge instance is live), it is safe to remove them.
function clearStaleChromiumLocks() {
  const profileDir = path.join(DATA_DIR, '.wwebjs_auth');
  const locks = ['SingletonLock', 'SingletonCookie', 'SingletonSocket'];
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch (e) {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (locks.includes(entry.name)) {
        try {
          fs.unlinkSync(full);
          console.log(`Removed stale Chromium lock: ${full}`);
        } catch (e) {
          // best effort — a live lock means another Chromium still owns it
        }
      }
    }
  };
  walk(profileDir);
}

// If Jarvis (or this bridge) was killed hard last time — task-killed, crashed,
// Windows shutdown — the Chrome it launched can survive as an orphan still
// holding this profile's Singleton locks. Job Object cleanup only fires if
// that job handle actually closes; a hard kill of the *parent* Python
// process can skip that. Sweep any Chrome bound to our profile dir before
// we touch the locks, so a bad previous shutdown can't poison this launch
// with a stale/half-written IndexedDB (the "random names, missing messages"
// symptom without a single detached-frame error in this run's own log).
function killOrphanChromeForProfile() {
  if (process.platform !== 'win32') return;
  const profileDir = path.join(DATA_DIR, '.wwebjs_auth');
  try {
    const escaped = profileDir.replace(/'/g, "''");
    const cmd = `Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | ` +
      `Where-Object { $_.CommandLine -like '*${escaped}*' } | ` +
      `ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }`;
    require('child_process').execSync(
      `powershell -NoProfile -NonInteractive -Command "${cmd.replace(/"/g, '\\"')}"`,
      { stdio: 'ignore', timeout: 10000 },
    );
  } catch (e) {
    // best effort — no matching processes, or powershell unavailable
  }
}

// Guard against a silently hung initialize() *or* a hung post-auth sync: if
// the client doesn't reach 'ready' within the timeout — whether it's still
// waiting for a QR/browser launch, or stuck on "sincronizando" after the
// phone confirmed the session — force a full reconnect so it self-heals
// instead of leaving the UI stuck forever. Re-armed on 'authenticated' and
// on every 'loading_screen' tick (see below) since a real sync can take a
// while and each progress event proves it's still alive.
function armInitWatchdog(seconds = 45) {
  clearInitWatchdog();
  initWatchdog = setTimeout(() => {
    initWatchdog = null;
    if (!isReady) {
      console.warn(`init watchdog: not ready after ${seconds}s (state=${clientState}) — forcing reconnect`);
      reconnect('init-timeout');
    }
  }, seconds * 1000);
}

function clearInitWatchdog() {
  if (initWatchdog) {
    clearTimeout(initWatchdog);
    initWatchdog = null;
  }
}

// --- Crash guards: never let an unhandled async error kill the bridge ---
process.on('unhandledRejection', (reason) => {
  console.error('unhandledRejection:', reason && reason.message ? reason.message : reason);
});
process.on('uncaughtException', (err) => {
  console.error('uncaughtException:', err && err.message ? err.message : err);
});

async function reconnect(reason) {
  if (reconnecting) return;
  reconnecting = true;
  isReady = false;
  latestQR = null;
  setState('starting', 'Reiniciando conexión…');
  nameCache.clear();
  profilePicCache.clear();
  console.warn(`Reconnecting WhatsApp client (${reason || 'manual'})...`);
  try {
    await client.destroy();
  } catch (e) {
    // ignore: client may already be dead
  }
  // destroy() can leave the browser alive (detached-frame state); make sure
  // nothing keeps the profile locked before we try to launch again.
  killBrowserTree();
  const attempt = () => {
    try {
      clearStaleChromiumLocks();
    } catch (e) {
      // best effort
    }
    armInitWatchdog();
    client.initialize().catch((err) => {
      console.error('initialize failed, retrying in 10s:', err && err.message ? err.message : err);
      setState('starting', 'Reintentando iniciar el navegador…');
      setTimeout(attempt, 10000);
    });
  };
  setTimeout(() => { reconnecting = false; attempt(); }, 3000);
}

// Page-health watchdog: the detached-frame state emits no 'disconnected'
// event, so nothing above would ever notice it. Ping the page every minute
// while we believe we are ready; if it stops answering, force a reconnect
// instead of serving the stale buffer until someone restarts the app.
let pagePingBusy = false;
setInterval(async () => {
  if (!isReady || reconnecting || pagePingBusy) return;
  pagePingBusy = true;
  try {
    if (!pageAlive()) throw new Error('page closed or frame detached');
    await Promise.race([
      client.pupPage.evaluate(() => true),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('page ping timeout')), 15000)),
    ]);
  } catch (e) {
    console.warn(`page watchdog: ${e && e.message ? e.message : e} — forcing reconnect`);
    reconnect('page-dead');
  } finally {
    pagePingBusy = false;
  }
}, 60000);

function normalizeName(value) {
  return (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('es')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function ignoredId(id) {
  const value = (id || '').toString().toLowerCase();
  return value.includes('@newsletter') || value === 'status@broadcast' || value.includes('@broadcast');
}

// WhatsApp system/protocol notices (encryption notice, business template
// updates, ...) — not real messages. They must never count toward unread,
// never appear as a chat preview, and never show up in a conversation.
// Note: 'gp2' (group changes) and group notifications ARE shown.
const NOISE_TYPES = new Set(['e2e_notification', 'notification_template']);
function isNoiseMessage(m) {
  return !!(m && NOISE_TYPES.has(m.type));
}

function contactDisplayName(contact, fallback = '') {
  return (
    (contact && (contact.name || contact.pushname || contact.shortName)) ||
    fallback ||
    (contact && contact.number) ||
    ''
  ).toString().trim();
}

function matchScore(query, candidate) {
  const q = normalizeName(query);
  const value = normalizeName(candidate);
  if (!q || !value) return 0;
  if (value === q) return 100;
  if (value.startsWith(`${q} `) || value.endsWith(` ${q}`)) return 85;
  if (value.split(' ').includes(q)) return 80;
  if (value.includes(q)) return 65;
  return 0;
}

function messageBody(m) {
  return m.body || (
    m.type === 'image'  ? '[imagen]' :
    m.type === 'video'  ? '[video]' :
    m.type === 'audio' || m.type === 'ptt' ? '[nota de voz]' :
    m.type === 'document' ? '[documento]' :
    m.type === 'sticker' ? '[sticker]' :
    m.type === 'location' ? '[ubicación]' :
    m.type === 'gp2' ? '[actualización del grupo]' :
    `[${m.type}]`
  );
}

async function safeProfilePicUrl(id) {
  try {
    if (!id) return null;
    const cached = profilePicCache.get(id);
    if (cached && (Date.now() - cached.ts) < PROFILE_PIC_TTL_MS) return cached.url;
    const url = await client.getProfilePicUrl(id);
    profilePicCache.set(id, { url: url || null, ts: Date.now() });
    return url || null;
  } catch (e) {
    return null;
  }
}

async function safeContactName(id) {
  try {
    if (!id) return null;
    if (nameCache.has(id)) return nameCache.get(id);
    const contact = await client.getContactById(id);
    const name = contact.name || contact.pushname || contact.shortName || contact.number || null;
    nameCache.set(id, name);
    return name;
  } catch (e) {
    return null;
  }
}

async function getQuotedInfo(m) {
  if (!m.hasQuotedMsg) return null;
  try {
    const q = await m.getQuotedMessage();
    if (!q) return null;
    const senderId = q.author || q.from;
    return {
      id: serializeMsgId(q.id),
      body: messageBody(q).slice(0, 200),
      fromMe: !!q.fromMe,
      senderName: q.fromMe ? null : await safeContactName(senderId),
      type: q.type || 'chat',
    };
  } catch (e) {
    return null;
  }
}

async function serializeMessage(m, chatId = null) {
  const messageId = serializeMsgId(m.id);
  const bodyText = messageBody(m);
  const mentionedIds = Array.isArray(m.mentionedIds) ? m.mentionedIds : [];
  const mentions = {};
  for (const id of mentionedIds) {
    const name = await safeContactName(id);
    if (name) mentions[id] = name;
  }
  const authorName = m.author ? await safeContactName(m.author) : null;
  const quoted = await getQuotedInfo(m);
  return {
    id: messageId,
    from: m.from,
    to: m.to || chatId,
    chatId: chatId || m.from || m.to || null,
    author: m.author || null,
    authorName: authorName,
    senderName: (m._data && m._data.notifyName) || authorName || null,
    body: bodyText,
    type: m.type || 'chat',
    fromMe: !!m.fromMe,
    direction: m.fromMe ? 'out' : 'in',
    hasMedia: !!m.hasMedia,
    mediaUrl: m.hasMedia && messageId ? `/media?id=${encodeURIComponent(messageId)}` : null,
    mentionedIds: mentionedIds,
    mentions: mentions,
    quoted: quoted,
    ack: messageId && messageAcks.has(messageId) ? messageAcks.get(messageId) : (m.ack ?? null),
    // whatsapp-web.js message timestamps are SECONDS; everything else in the
    // buffer/pending pipeline is milliseconds (Date.now()). Serving seconds
    // here made live-fetched history sort against buffered/optimistic entries
    // as if from 1970 — messages landed in the wrong spot. Normalize to ms.
    timestamp: toMs(m.timestamp) || Date.now()
  };
}

// Normalize a WhatsApp timestamp to milliseconds. wwebjs hands out epoch
// seconds (10 digits); our buffer entries use Date.now() ms (13 digits).
function toMs(ts) {
  const n = Number(ts) || 0;
  return n > 0 && n < 1e12 ? n * 1000 : n;
}

// Chronological (real WhatsApp send-time) timestamp for a message, in ms —
// for chat-list ordering/preview and in-conversation display. Buffer entries
// carry a separate `waTs` alongside their arrival-order `timestamp` (see the
// 'message'/'message_create' handlers for why the two must not be conflated);
// live WWebJS message models only have `.timestamp` (raw WA seconds), which
// toMs() normalizes the same way.
function waTime(m) {
  if (!m) return 0;
  return toMs(m.waTs || m.timestamp || 0);
}

// Serialize a MessageID. On LID chats, fetchMessages() can return ids whose
// _serialized getter is undefined; without an id the UI can't dedupe the
// message and /media?id=... gets the literal string "undefined". Rebuild the
// canonical `${fromMe}_${remote}_${id}[_participant]` form from the parts.
function serializeMsgId(mid) {
  if (!mid) return null;
  if (mid._serialized) return mid._serialized;
  const remote = (mid.remote && (mid.remote._serialized || mid.remote)) || '';
  if (!mid.id || !remote) return null;
  const participant = mid.participant && (mid.participant._serialized || mid.participant);
  return `${mid.fromMe ? 'true' : 'false'}_${remote}_${mid.id}${participant ? '_' + participant : ''}`;
}

client.on('loading_screen', (percent, message) => {
  // Fires while WhatsApp Web syncs after the phone is linked. Each tick is
  // proof of life, so reset (not clear) the watchdog: if the ticks stop
  // coming — or never start — for too long, we still want to self-heal.
  armInitWatchdog(90);
  const pct = parseInt(percent, 10);
  setState('loading', Number.isFinite(pct) ? `Cargando WhatsApp… ${pct}%` : (message || 'Cargando WhatsApp…'));
});

client.on('qr', (qr) => {
  console.log('QR_RECEIVED');
  latestQR = qr;
  isReady = false;
  setState('qr', 'Escanea el código con tu teléfono');
  // A QR means the browser is up and waiting for the user — not hung.
  clearInitWatchdog();
  qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
  console.log('WhatsApp client ready');
  isReady = true;
  latestQR = null;
  setState('ready', '');
  clearInitWatchdog();
  // Remember the browser PID while the handle is healthy so killBrowserTree()
  // still works after the page detaches and pupBrowser stops answering.
  try {
    const proc = client.pupBrowser && client.pupBrowser.process();
    browserPid = proc ? proc.pid : null;
  } catch (e) {
    browserPid = null;
  }
});

client.on('authenticated', () => {
  console.log('Authenticated');
  latestQR = null;
  setState('loading', 'Sesión verificada, sincronizando…');
  // Session confirmed but not synced yet: 'loading_screen' may or may not
  // fire depending on the WhatsApp Web version, so this is the only
  // guaranteed backstop against a sync that silently hangs — reconnect if
  // 'ready' doesn't arrive within 90s.
  armInitWatchdog(90);
});
client.on('auth_failure', (msg) => {
  console.error('Auth failure', msg);
  isReady = false;
  setState('auth_failure', 'Fallo de autenticación, reintentando…');
  reconnect('auth_failure');
});
client.on('disconnected', (reason) => {
  console.warn('WhatsApp client disconnected', reason);
  isReady = false;
  setState('disconnected', 'Conexión perdida, reconectando…');
  reconnect(`disconnected: ${reason}`);
});

client.on('message', async (msg) => {
  try {
    // On LID chats msg.id._serialized is undefined; using it raw made mediaUrl
    // "/media?id=undefined", and getMessageById('undefined') throws "Invalid
    // serialized message id specified" (500) on transcribe/download. Rebuild
    // the canonical id like serializeMessage() does.
    const messageId = serializeMsgId(msg.id);
    const entry = {
      id: messageId,
      from: msg.from,
      to: msg.to || null,
      chatId: msg.fromMe ? msg.to : msg.from,
      author: msg.author || null,
      senderName: (msg._data && msg._data.notifyName) || null,
      body: messageBody(msg),
      type: msg.type || 'chat',
      fromMe: !!msg.fromMe,
      direction: msg.fromMe ? 'out' : 'in',
      hasMedia: !!msg.hasMedia,
      mediaUrl: msg.hasMedia && messageId ? `/media?id=${encodeURIComponent(messageId)}` : null,
      mentionedIds: Array.isArray(msg.mentionedIds) ? msg.mentionedIds : [],
      quoted: await getQuotedInfo(msg),
      // `timestamp` is arrival order (Date.now()) — the Python manager's
      // /messages?since=<cursor> polling and unread/dedup logic depend on it
      // being monotonic with when WE processed the event, not the message's
      // own send time. A catch-up-synced historical message (e.g. after a
      // reconnect) can have a real send time far in the past; stamping it
      // with that instead would put it BELOW the poll cursor and the message
      // would silently never be delivered/notified.
      // `waTs` is WhatsApp's own send time (normalized to ms) — used for chat
      // list ordering/preview and in-conversation display, where showing
      // "just now" for a message actually sent hours ago is exactly the bug
      // being fixed here.
      timestamp: Date.now(),
      waTs: toMs(msg.timestamp) || Date.now(),
    };
    console.log(`[MSG IN] from=${entry.from} author=${entry.author || '-'} name=${entry.senderName || '-'} type=${entry.type} body=${entry.body.slice(0,60)}`);;
    // System notices (encryption notice, business template updates) aren't
    // real messages: never buffer, never count as unread.
    if (isNoiseMessage(entry)) return;
    messages.push(entry);
    if (messages.length > MAX_BUFFERED_MESSAGES) messages.shift();
    // Count this as unread for its chat (the `message` event is incoming only).
    if (!msg.fromMe && entry.chatId) {
      unreadByChat.set(entry.chatId, (unreadByChat.get(entry.chatId) || 0) + 1);
    }
    persistState();
  } catch (e) {
    console.error('message processing failed', e);
  }
});

// Fires for every message create, including ones the user sends from the phone
// or other devices. Sending in a chat means the user is there → mark it read.
client.on('message_create', async (msg) => {
  try {
    if (msg.fromMe && msg.to) {
      if (unreadByChat.get(msg.to)) {
        unreadByChat.set(msg.to, 0);
        persistState();
      }

      // Messages sent from the phone (or another linked device) only fire
      // this event, never 'message' (that one is incoming-only). Without
      // this, the buffer Python polls never learns about them and they only
      // show up after a full conversation reload. Our own sends via
      // /send and /send_media already push their own entry, so dedupe by id
      // to avoid double entries for those.
      // Rebuild via serializeMsgId (LID chats drop id._serialized) so mediaUrl
      // never becomes "/media?id=undefined". See the 'message' handler above.
      const id = serializeMsgId(msg.id);
      if (id && !isNoiseMessage(msg) && !messages.some(m => m.id === id)) {
        const entry = {
          id,
          from: msg.from,
          to: msg.to,
          chatId: msg.to,
          author: msg.author || null,
          senderName: (msg._data && msg._data.notifyName) || null,
          body: messageBody(msg),
          type: msg.type || 'chat',
          fromMe: true,
          direction: 'out',
          hasMedia: !!msg.hasMedia,
          mediaUrl: msg.hasMedia && id ? `/media?id=${encodeURIComponent(id)}` : null,
          mentionedIds: Array.isArray(msg.mentionedIds) ? msg.mentionedIds : [],
          quoted: await getQuotedInfo(msg),
          // See the identical comment in the 'message' handler above: keep
          // `timestamp` as arrival order for the polling cursor, `waTs` as
          // WhatsApp's real send time for display/ordering.
          timestamp: Date.now(),
          waTs: toMs(msg.timestamp) || Date.now(),
        };
        messages.push(entry);
        if (messages.length > MAX_BUFFERED_MESSAGES) messages.shift();
        persistState();
      }
    }
  } catch (e) {
    console.error('message_create processing failed', e);
  }
});

// Update the original buffer entry's body in place (for full conversation
// reloads via /chat_messages) AND push a lightweight 'edit' event entry so
// the Python polling loop (which filters /messages by timestamp) picks up
// the change for chats already open, without moving the message out of its
// original position in the timeline.
function recordEdit(id, chatId, newBody) {
  const entry = messages.find(m => m.id === id);
  if (entry) {
    entry.body = newBody;
    entry.edited = true;
  }
  messages.push({
    id,
    type: 'edit',
    chatId: chatId || (entry && entry.chatId) || null,
    body: newBody,
    edited: true,
    fromMe: entry ? !!entry.fromMe : true,
    timestamp: Date.now(),
  });
  if (messages.length > MAX_BUFFERED_MESSAGES) messages.shift();
  persistState();
}

// Fires when any message (ours or a contact's) gets edited, including edits
// made from another device (phone) that our own /edit endpoint never saw.
client.on('message_edit', (msg, newBody) => {
  try {
    const id = msg.id ? msg.id._serialized : null;
    if (!id) return;
    recordEdit(id, msg.fromMe ? msg.to : msg.from, newBody);
  } catch (e) {
    console.error('message_edit processing failed', e);
  }
});

app.get('/qr', (req, res) => {
  res.json({
    ok: true,
    ready: isReady,
    qr: latestQR,
    state: clientState,
    detail: stateDetail,
  });
});

// Force a fresh connection attempt (used by the UI "Reintentar" button when a
// link/loading gets stuck). Token-protected like the other write endpoints.
app.post('/reconnect', requireToken, (req, res) => {
  reconnect('manual');
  res.json({ ok: true, state: clientState });
});

app.get('/status', (req, res) => {
  res.json({ ok: true, ready: clientReady() });
});

client.on('message_ack', (msg, ack) => {
  const id = msg && msg.id ? msg.id._serialized : null;
  if (!id) return;
  messageAcks.set(id, ack);
  for (const entry of messages) {
    if (entry.id === id) entry.ack = ack;
  }
  if (messageAcks.size > 5000) {
    messageAcks.delete(messageAcks.keys().next().value);
  }
  persistState();
});

app.get('/messages', (req, res) => {
  const since = parseInt(req.query.since || '0', 10);
  const out = messages.filter(m => m.timestamp > since);
  res.json({ ok: true, messages: out });
});

app.get('/chats', async (req, res) => {
  try {
    if (!clientReady()) {
      return res.json({ ok: true, ready: false, chats: [] });
    }
    const limit = Math.max(1, Math.min(5000, parseInt(req.query.limit || '200', 10) || 200));
    const includePictures = req.query.pictures !== '0';
    const chats = await client.getChats();
    const visibleChats = chats.filter(c => {
      const id = c.id && c.id._serialized ? c.id._serialized : c.id;
      return id && !ignoredId(id);
    });
    const out = await Promise.all(visibleChats.slice(0, limit).map(async (c) => {
      const id = c.id && c.id._serialized ? c.id._serialized : c.id;
      // On this (LID) account, chat.lastReceivedKey — what WWebJS derives
      // c.lastMessage from — routinely points at a STALE message (measured:
      // 14 of 25 chats wrong, typically an old call_log while real recent
      // chat content exists). A stale lastMessage doesn't just show a wrong
      // preview, it sets `timestamp` below, which the chat list sorts by — so
      // actually-recent chats get buried under old ones and look "missing"/
      // out of order. chat.fetchMessages() (Store query, not the cached
      // lastReceivedKey pointer) was verified accurate in every case tested,
      // so use it as the source of truth instead of c.lastMessage.
      let last = null;
      try {
        const recent = await c.fetchMessages({ limit: 8 });
        last = recent
          .filter(m => !isNoiseMessage(m) && m.type !== 'edit' && m.type !== 'revoked')
          .slice(-1)[0] || null;
      } catch (e) {
        // fetchMessages can fail transiently; fall back below.
      }
      if (!last) {
        last = c.lastMessage || null;
        if (last && isNoiseMessage(last)) last = null;
      }
      // Safety net: our own live buffer might have something newer still
      // (e.g. arrived between the fetchMessages snapshot and now). Match by
      // chatId ONLY — chatId is always set correctly at ingestion (msg.to for
      // outgoing, msg.from for incoming). Matching on raw `from`/`to` too was
      // wrong: on an outgoing entry `from` is always OUR OWN account id, so
      // for the "Note to Self" chat (id === our own id) that OR clause matched
      // every message we've ever sent to ANYONE, not just the self-chat.
      // Only entries with a real waTs qualify: entries persisted before this
      // field existed (restored from bridge_state.json on restart) fall back
      // to arrival-order timestamp inside waTime(), which can be arbitrarily
      // later than the message's real send time and would otherwise win this
      // comparison for the wrong reason. Better to trust the just-fetched
      // Store data (`last`) than a legacy entry with no real send time.
      const bufferLatest = messages
        .filter(m => m && m.type !== 'edit' && !isNoiseMessage(m) && m.chatId === id && m.waTs)
        .sort((a, b) => waTime(b) - waTime(a))[0] || null;
      if (bufferLatest && (!last || waTime(bufferLatest) > waTime(last))) {
        last = bufferLatest;
      }
      // Delivery/read state for the last message, so the chat list can show
      // checkmarks on our own last message (like WhatsApp does). last.id can
      // be missing _serialized on this build (see serializeMsgId); fall back
      // to last.ack from the model itself when we can't resolve the id.
      const lastId = last ? serializeMsgId(last.id) : null;
      const ack = last
        ? (lastId && messageAcks.has(lastId) ? messageAcks.get(lastId) : (last.ack ?? null))
        : null;
      return {
        chatId: id,
        name: c.name || c.formattedTitle || (c.contact && (c.contact.name || c.contact.pushname)) || id,
        isGroup: !!c.isGroup,
        // whatsapp-web.js unreadCount is authoritative when it reflects a read
        // (e.g. on the phone); our own counter fills in live messages it misses.
        unread: Math.max(c.unreadCount || 0, unreadByChat.get(id) || 0),
        // Real WhatsApp send time (ms), not arrival order — see waTime().
        timestamp: last ? waTime(last) : toMs(c.timestamp || 0),
        preview: last ? messageBody(last) : '',
        fromMe: last ? !!last.fromMe : false,
        ack: ack,
        pictureUrl: includePictures ? await safeProfilePicUrl(id) : null,
      };
    }));
    out.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    res.json({ ok: true, ready: true, chats: out, total: visibleChats.length });
  } catch (e) {
    console.warn('chats unavailable, retrying later:', e && e.message ? e.message : e.toString());
    if (e && e.stack) console.warn(e.stack.split('\n').slice(0, 6).join('\n'));
    res.status(503).json({ ok: false, ready: false, chats: [], error: e && e.message ? e.message : e.toString() });
  }
});

// Diagnostic: run the injected getChats pipeline step by step INSIDE the page
// and report which call throws (the pupPage boundary minifies errors to "r",
// hiding the real stack). Read-only; safe to call on a live session.
app.get('/debug_getchats', async (req, res) => {
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, error: 'not ready' });
    const report = await client.pupPage.evaluate(async () => {
      const out = { steps: [] };
      const step = (name, fn) => {
        try { const v = fn(); out.steps.push([name, 'ok', typeof v === 'number' ? v : undefined]); return v; }
        catch (e) { out.steps.push([name, 'THROW', (e && (e.stack || e.message || String(e))).slice(0, 500)]); return undefined; }
      };
      const col = step('require WAWebCollections', () => window.require('WAWebCollections'));
      const chats = step('Chat.getModelsArray', () => col.Chat.getModelsArray());
      step('chats.length', () => chats.length);
      if (chats && chats.length) {
        // try the full model conversion on the first few chats to find the poison one
        for (let i = 0; i < Math.min(chats.length, 8); i++) {
          const c = chats[i];
          const id = c && c.id && c.id._serialized;
          try {
            await window.WWebJS.getChatModel(c);
            out.steps.push(['getChatModel ' + id, 'ok']);
          } catch (e) {
            out.steps.push(['getChatModel ' + id, 'THROW', (e && (e.stack || e.message || String(e))).slice(0, 700)]);
            // drill into the known sub-steps
            try { c.serialize(); out.steps.push(['  serialize', 'ok']); }
            catch (e2) { out.steps.push(['  serialize', 'THROW', String(e2 && (e2.message || e2)).slice(0, 200)]); }
            if (c.groupMetadata) {
              try { window.require('WAWebLidMigrationUtils').toPn; out.steps.push(['  toPn ref', 'ok']); }
              catch (e3) { out.steps.push(['  toPn ref', 'THROW', String(e3).slice(0, 200)]); }
            }
            break;
          }
        }
      }
      return out;
    });
    res.json({ ok: true, report });
  } catch (e) {
    res.status(500).json({ ok: false, error: e && e.message, stack: e && e.stack ? e.stack.split('\n').slice(0, 8) : null });
  }
});

// Diagnostic twin of /debug_getchats for the media pipeline: run each step of
// Message.downloadMedia's page-side logic and report the throwing call.
app.get('/debug_media', async (req, res) => {
  const id = (req.query.id || '').toString();
  if (!id) return res.status(400).json({ ok: false, error: 'missing id' });
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, error: 'not ready' });
    const report = await client.pupPage.evaluate(async (msgId) => {
      const out = { steps: [] };
      const trap = async (name, fn) => {
        try { const v = await fn(); out.steps.push([name, 'ok', typeof v === 'string' ? v.slice(0, 80) : (v && typeof v === 'object' ? Object.keys(v).slice(0, 10).join('|') : String(v))]); return v; }
        catch (e) { out.steps.push([name, 'THROW', (e && (e.stack || e.message || String(e))).slice(0, 600)]); return undefined; }
      };
      let msg = await trap('Msg.get', () => window.require('WAWebCollections').Msg.get(msgId));
      if (!msg) msg = await trap('Msg.getMessagesById', async () => (await window.require('WAWebCollections').Msg.getMessagesById([msgId]))?.messages?.[0]);
      if (!msg) return out;
      await trap('mediaData present', () => !!msg.mediaData);
      await trap('mediaStage', () => msg.mediaData && msg.mediaData.mediaStage);
      if (msg.mediaData && msg.mediaData.mediaStage !== 'RESOLVED') {
        await trap('msg.downloadMedia()', () => msg.downloadMedia({ downloadEvenIfExpensive: true, rmrReason: 1 }));
        await trap('mediaStage after', () => msg.mediaData.mediaStage);
      }
      await trap('WAWebDownloadManager ref', () => !!window.require('WAWebDownloadManager'));
      // Field location check: wwebjs reads msg.directPath etc directly, but on
      // newer builds these may live on msg.mediaData only.
      await trap('fields on msg', () => ['directPath','encFilehash','filehash','mediaKey','mediaKeyTimestamp','mimetype','size'].map(k=>k+'='+(msg[k]!==undefined?'y':'-')).join(' '));
      await trap('fields on mediaData', () => msg.mediaData ? ['directPath','encFilehash','filehash','mediaKey','mediaKeyTimestamp','mimetype','size'].map(k=>k+'='+(msg.mediaData[k]!==undefined?'y':'-')).join(' ') : 'no mediaData');
      const md = msg.mediaData || {};
      const pick = (k) => msg[k] !== undefined ? msg[k] : md[k];
      const mockQpl = { addAnnotations: function(){return this;}, addPoint: function(){return this;} };
      const dec = await trap('downloadAndMaybeDecrypt', () => window.require('WAWebDownloadManager').downloadManager.downloadAndMaybeDecrypt({
        directPath: pick('directPath'), encFilehash: pick('encFilehash'), filehash: pick('filehash'),
        mediaKey: pick('mediaKey'), mediaKeyTimestamp: pick('mediaKeyTimestamp'),
        type: msg.type, signal: new AbortController().signal, downloadQpl: mockQpl,
      }));
      if (dec) await trap('arrayBufferToBase64Async', async () => (await window.WWebJS.arrayBufferToBase64Async(dec)).length + ' b64 chars');
      return out;
    }, id);
    res.json({ ok: true, report });
  } catch (e) {
    res.status(500).json({ ok: false, error: e && e.message, stack: e && e.stack ? e.stack.split('\n').slice(0, 6) : null });
  }
});

app.get('/debug_lastmsg_compare', async (req, res) => {
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, error: 'not ready' });
    const limit = parseInt(req.query.limit || '25', 10);
    const chats = await client.getChats();
    const visible = chats.filter(c => {
      const id = c.id && c.id._serialized ? c.id._serialized : c.id;
      return id && !ignoredId(id);
    }).slice(0, limit);
    const rows = [];
    for (const c of visible) {
      const id = c.id && c.id._serialized ? c.id._serialized : c.id;
      const claimedLast = c.lastMessage;
      let realLast = null;
      try {
        const msgs = await c.fetchMessages({ limit: 5 });
        realLast = msgs.filter(m => !isNoiseMessage(m) && m.type !== 'edit' && m.type !== 'revoked').slice(-1)[0] || null;
      } catch (e) {
        rows.push({ id, name: c.name, fetchError: e && e.message });
        continue;
      }
      rows.push({
        id, name: c.name,
        claimed: claimedLast ? { type: claimedLast.type, ts: toMs(claimedLast.timestamp), body: (claimedLast.body || '').slice(0, 25) } : null,
        real: realLast ? { type: realLast.type, ts: toMs(realLast.timestamp), body: (realLast.body || '').slice(0, 25) } : null,
      });
    }
    res.json({ ok: true, rows });
  } catch (e) {
    res.status(500).json({ ok: false, error: e && e.message });
  }
});

app.get('/debug_synccount', async (req, res) => {
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, error: 'not ready' });
    const info = await client.pupPage.evaluate(() => {
      const col = window.require('WAWebCollections');
      const chats = col.Chat.getModelsArray();
      const archived = chats.filter((c) => c.archive).length;
      // Contact collection: everyone WhatsApp knows about locally, regardless
      // of whether a chat/conversation exists.
      let contactCount = null;
      try { contactCount = col.Contact.getModelsArray().length; } catch (e) {}
      return {
        chatCount: chats.length,
        archivedChats: archived,
        contactCount,
        appState: window.Store && window.Store.AppState ? undefined : undefined,
      };
    });
    res.json({ ok: true, info });
  } catch (e) {
    res.status(500).json({ ok: false, error: e && e.message });
  }
});

app.get('/debug_lastmsg', async (req, res) => {
  const chatId = (req.query.chatId || '').toString();
  if (!chatId) return res.status(400).json({ ok: false, error: 'missing chatId' });
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, error: 'not ready' });
    const report = await client.pupPage.evaluate(async (cid) => {
      const out = { steps: [] };
      const trap = (name, fn) => {
        try { const v = fn(); out.steps.push([name, 'ok', typeof v === 'object' && v ? JSON.stringify(v).slice(0, 300) : String(v)]); return v; }
        catch (e) { out.steps.push([name, 'THROW', (e && (e.stack || e.message || String(e))).slice(0, 400)]); return undefined; }
      };
      const chat = trap('Chat.get', () => window.require('WAWebCollections').Chat.get(cid));
      if (!chat) return out;
      trap('chat.lastReceivedKey', () => chat.lastReceivedKey);
      trap('chat.lastReceivedKey._serialized', () => chat.lastReceivedKey && chat.lastReceivedKey._serialized);
      trap('chat.msgs.length', () => chat.msgs && chat.msgs.length);
      const key = chat.lastReceivedKey && chat.lastReceivedKey._serialized;
      if (key) {
        const m1 = trap('Msg.get(key)', () => window.require('WAWebCollections').Msg.get(key));
        if (!m1) {
          const m2 = await (async () => {
            try {
              const r = await window.require('WAWebCollections').Msg.getMessagesById([key]);
              out.steps.push(['Msg.getMessagesById', 'ok', JSON.stringify(!!(r && r.messages && r.messages[0]))]);
              return r && r.messages && r.messages[0];
            } catch (e) { out.steps.push(['Msg.getMessagesById', 'THROW', String(e && (e.message || e)).slice(0, 300)]); return null; }
          })();
          if (m2) trap('getMessageModel(m2)', () => window.WWebJS.getMessageModel(m2));
        } else {
          trap('getMessageModel(m1)', () => window.WWebJS.getMessageModel(m1));
        }
      }
      return out;
    }, chatId);
    res.json({ ok: true, report });
  } catch (e) {
    res.status(500).json({ ok: false, error: e && e.message, stack: e && e.stack ? e.stack.split('\n').slice(0, 6) : null });
  }
});

app.get('/profile_picture', async (req, res) => {
  const chatId = (req.query.chatId || '').toString().trim();
  if (!chatId) return res.status(400).json({ ok: false, error: 'missing chatId' });
  try {
    if (!clientReady()) return res.json({ ok: true, ready: false, url: null });
    res.json({ ok: true, url: await safeProfilePicUrl(chatId) });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

// Cache decoded media so HTTP range requests (used by the audio player to seek)
// don't re-download the file from WhatsApp on every byte range.
const mediaCache = new Map();  // id -> { buffer, mimetype, filename, ts }
const MEDIA_CACHE_TTL_MS = 5 * 60 * 1000;
const MEDIA_CACHE_MAX = 40;

async function loadMedia(id) {
  // Guard against ids that can't be a real serialized MessageID (e.g. stale
  // "/media?id=undefined" entries buffered before the LID id fix). Passing
  // these to getMessageById throws "Invalid serialized message id specified"
  // (a 500); a clean 404 is the honest answer.
  if (!id || id === 'undefined' || id === 'null' || id.split('_').length < 3) {
    return { error: 'invalid message id', status: 404 };
  }
  const cached = mediaCache.get(id);
  if (cached && (Date.now() - cached.ts) < MEDIA_CACHE_TTL_MS) {
    cached.ts = Date.now();
    return cached;
  }
  const msg = await client.getMessageById(id);
  if (!msg) return { error: 'message not found', status: 404 };
  if (!msg.hasMedia) return { error: 'message has no media', status: 404 };
  // On LID messages the page-side serialization can drop id._serialized;
  // Message.downloadMedia() then sends undefined as the message id into the
  // page, where Msg.getMessagesById([undefined]) hits IndexedDB with no key
  // and throws a minified DataError ("r"). We resolved this message BY id, so
  // restore it before calling any method that round-trips through the page.
  if (msg.id && !msg.id._serialized) msg.id._serialized = id;
  const media = await msg.downloadMedia();
  if (!media) return { error: 'media unavailable', status: 404 };
  const entry = {
    buffer: Buffer.from(media.data, 'base64'),
    mimetype: media.mimetype || 'application/octet-stream',
    filename: media.filename || `whatsapp_media_${Date.now()}`,
    ts: Date.now(),
  };
  mediaCache.set(id, entry);
  if (mediaCache.size > MEDIA_CACHE_MAX) {
    mediaCache.delete(mediaCache.keys().next().value);  // evict oldest
  }
  return entry;
}

app.get('/media', async (req, res) => {
  const id = (req.query.id || '').toString();
  if (!id) return res.status(400).json({ ok: false, error: 'missing id' });
  try {
    const entry = await loadMedia(id);
    if (entry.error) return res.status(entry.status || 404).json({ ok: false, error: entry.error });
    const { buffer, mimetype, filename } = entry;
    const total = buffer.length;
    res.setHeader('Content-Type', mimetype);
    res.setHeader('Content-Disposition', `inline; filename="${filename}"`);
    // Advertise range support so QMediaPlayer can seek within the stream.
    res.setHeader('Accept-Ranges', 'bytes');

    const range = req.headers.range;
    const match = range ? /^bytes=(\d*)-(\d*)$/.exec(range.trim()) : null;
    if (match) {
      let start = match[1] === '' ? 0 : parseInt(match[1], 10);
      let end = match[2] === '' ? total - 1 : parseInt(match[2], 10);
      if (isNaN(start)) start = 0;
      if (isNaN(end) || end >= total) end = total - 1;
      if (start > end || start >= total) {
        res.setHeader('Content-Range', `bytes */${total}`);
        return res.status(416).end();
      }
      res.status(206);
      res.setHeader('Content-Range', `bytes ${start}-${end}/${total}`);
      res.setHeader('Content-Length', end - start + 1);
      return res.end(buffer.subarray(start, end + 1));
    }

    res.setHeader('Content-Length', total);
    res.send(buffer);
  } catch (e) {
    console.error('media error', e);
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

app.post('/send', requireToken, async (req, res) => {
  const to = ((req.body && req.body.to) || '').toString().trim();
  const body = ((req.body && req.body.body) || '').toString().trim();
  const quotedMessageId = ((req.body && req.body.quotedMessageId) || '').toString().trim() || null;
  if (!to || !body) return res.status(400).json({ ok: false, error: 'missing to or body' });
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, ready: false, error: 'client not ready' });
    if (!to.includes('@') || ignoredId(to)) {
      return res.status(400).json({ ok: false, error: 'invalid recipient id' });
    }
    // Verify individual recipients are actually on WhatsApp before sending,
    // so unregistered numbers fail with a clear message instead of silently.
    if (to.endsWith('@c.us')) {
      const numberId = await client.getNumberId(to);
      if (!numberId) {
        return res.status(404).json({
          ok: false,
          error: 'El número no está registrado en WhatsApp.',
        });
      }
    }
    const sendOptions = quotedMessageId ? { quotedMessageId } : {};
    const m = await client.sendMessage(to, body, sendOptions);
    // m.id._serialized puede venir undefined (bug conocido de IDs LID en
    // wwebjs) aunque el mensaje ya se haya entregado. Antes esto tiraba una
    // excepción al construir la respuesta y el catch externo devolvía
    // ok:false al instante, aunque WhatsApp ya lo había enviado.
    const sentId = (m.id && m.id._serialized) || null;
    try {
      const fromId = client.info && client.info.wid ? client.info.wid._serialized : 'me';
      const entry = {
        id: sentId,
        from: fromId,
        to: to,
        // Chat identity for consumers that group messages by chat (Python's
        // manager/UI). Without this, replies sent through this endpoint (e.g.
        // by Jarvis via voice command) were keyed by the bot's own JID instead
        // of the chat, so an already-open chat window never noticed them.
        chatId: to,
        fromMe: true,
        type: 'chat',
        body: body,
        timestamp: Date.now(),
        direction: 'out',
        // Build the quoted preview from the id we already have rather than
        // relying on m.hasQuotedMsg, which isn't reliably populated on the
        // message object returned right after sending.
        quoted: quotedMessageId ? await (async () => {
          try {
            const q = await client.getMessageById(quotedMessageId);
            if (!q) return null;
            const senderId = q.author || q.from;
            return {
              id: q.id ? q.id._serialized : quotedMessageId,
              body: messageBody(q).slice(0, 200),
              fromMe: !!q.fromMe,
              senderName: q.fromMe ? null : await safeContactName(senderId),
              type: q.type || 'chat',
            };
          } catch (e) {
            return null;
          }
        })() : null,
        ack: m.ack ?? 0
      };
      if (entry.id) messageAcks.set(entry.id, entry.ack);
      messages.push(entry);
      if (messages.length > MAX_BUFFERED_MESSAGES) messages.shift();
      persistState();
    } catch (e) {
      console.error('failed to record outgoing message', e);
    }
    res.json({ ok: true, id: sentId, to, body, ack: m.ack ?? 0 });
  } catch (e) {
    console.error('send error', e);
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

app.post('/send_media', requireToken, async (req, res) => {
  const to = ((req.body && req.body.to) || '').toString().trim();
  const filePath = ((req.body && req.body.path) || '').toString().trim();
  const caption = ((req.body && req.body.caption) || '').toString();
  if (!to || !filePath) return res.status(400).json({ ok: false, error: 'missing to or path' });
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, ready: false, error: 'client not ready' });
    if (!to.includes('@') || ignoredId(to)) {
      return res.status(400).json({ ok: false, error: 'invalid recipient id' });
    }
    if (!fs.existsSync(filePath)) {
      return res.status(404).json({ ok: false, error: `file not found: ${filePath}` });
    }
    if (to.endsWith('@c.us')) {
      const numberId = await client.getNumberId(to);
      if (!numberId) {
        return res.status(404).json({ ok: false, error: 'El número no está registrado en WhatsApp.' });
      }
    }
    const media = MessageMedia.fromFilePath(filePath);
    const options = caption ? { caption } : {};
    const m = await client.sendMessage(to, media, options);
    try {
      const fromId = client.info && client.info.wid ? client.info.wid._serialized : 'me';
      const entry = {
        id: m.id ? m.id._serialized : null,
        from: fromId,
        to: to,
        // See the /send handler above for why chatId/fromMe are needed here.
        chatId: to,
        fromMe: true,
        body: caption || `[${m.type || 'media'}]`,
        type: m.type || 'document',
        timestamp: Date.now(),
        direction: 'out',
        hasMedia: true,
        mediaUrl: m.id ? `/media?id=${encodeURIComponent(m.id._serialized)}` : null,
        ack: m.ack ?? 0,
      };
      if (entry.id) messageAcks.set(entry.id, entry.ack);
      messages.push(entry);
      if (messages.length > MAX_BUFFERED_MESSAGES) messages.shift();
      persistState();
    } catch (e) {
      console.error('failed to record outgoing media message', e);
    }
    res.json({ ok: true, id: m.id ? m.id._serialized : null, to, type: m.type || 'document', ack: m.ack ?? 0 });
  } catch (e) {
    console.error('send_media error', e);
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

app.post('/edit', requireToken, async (req, res) => {
  const id = ((req.body && req.body.id) || '').toString().trim();
  const body = ((req.body && req.body.body) || '').toString().trim();
  if (!id || !body) return res.status(400).json({ ok: false, error: 'missing id or body' });
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, ready: false, error: 'client not ready' });
    const msg = await client.getMessageById(id);
    if (!msg) return res.status(404).json({ ok: false, error: 'message not found' });
    if (!msg.fromMe) return res.status(403).json({ ok: false, error: 'only own messages can be edited' });
    // LID messages can come back without id._serialized (see loadMedia).
    if (msg.id && !msg.id._serialized) msg.id._serialized = id;
    // msg.edit() collapses every WhatsApp-side rejection (edit window expired,
    // unsupported message type, etc.) into a bare null with no reason. Check
    // the ~15-minute WhatsApp edit window ourselves first so at least that
    // common case gets a specific, actionable error instead of the generic one.
    const ageMs = Date.now() - (Number(msg.timestamp) || 0) * 1000;
    if (ageMs > 15 * 60 * 1000) {
      return res.status(409).json({
        ok: false,
        error: `El mensaje tiene más de 15 minutos (${Math.round(ageMs / 60000)} min): WhatsApp ya no permite editarlo.`,
      });
    }
    let edited;
    try {
      edited = await msg.edit(body);
    } catch (e) {
      return res.status(409).json({ ok: false, error: `No se pudo editar: ${e}` });
    }
    if (!edited) {
      return res.status(409).json({
        ok: false,
        error: 'WhatsApp rechazó la edición (tipo de mensaje no compatible, ya fue editado el máximo de veces, u otra restricción de la app).',
      });
    }
    recordEdit(id, msg.fromMe ? msg.to : msg.from, body);
    res.json({ ok: true, id, body });
  } catch (e) {
    console.error('edit error', e);
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

// fetch recent messages for a specific chat id (chatId or phone@c.us)
app.get('/chat_messages', async (req, res) => {
  const chatId = req.query.chatId || req.query.to;
  if (!chatId) return res.status(400).json({ ok: false, error: 'missing chatId' });
  const limit = Math.max(1, Math.min(5000, parseInt(req.query.limit || '1000', 10) || 1000));

  // Fallback: serve this chat's messages from the in-memory buffer. The live
  // Store path (getChatById + fetchMessages) throws a minified "r" on
  // LID-migrated accounts against the current WhatsApp Web build (no upstream
  // whatsapp-web.js fix yet). Returning the buffered messages we already have
  // lets the conversation open instead of failing with a 500. Not full history
  // and no on-demand media, but the recent thread shows.
  // Match by chatId ONLY (not raw from/to — see /chats bufferLatest comment:
  // for the "Note to Self" chat, `from` on every outgoing entry equals our own
  // account id, so an OR on from/to pulled in messages sent to any chat).
  // Sort/slice by real send time (waTime), not arrival order — a
  // catch-up-synced historical message must land in its actual chronological
  // spot in the conversation, not wherever "when we happened to receive it"
  // would put it. Remap `timestamp` in the output to that same real time,
  // since that's the field the UI renders/orders by.
  const fromBuffer = () => messages
    .filter(m => m && m.type !== 'edit' && !isNoiseMessage(m) && m.chatId === chatId)
    .sort((a, b) => waTime(a) - waTime(b))
    .slice(-limit)
    .map(m => ({ ...m, timestamp: waTime(m) }));

  try {
    if (!clientReady()) return res.json({ ok: true, ready: false, messages: fromBuffer() });
    const chat = await client.getChatById(chatId);
    if (!chat) return res.json({ ok: true, messages: fromBuffer() });
    const msgs = await chat.fetchMessages({ limit });
    const out = (await Promise.all(msgs.map(m => serializeMessage(m, chatId))))
      .filter(m => !isNoiseMessage(m));
    res.json({ ok: true, messages: out });
  } catch (e) {
    console.warn('chat_messages live fetch failed, serving buffer:', e && e.message ? e.message : e);
    res.json({ ok: true, messages: fromBuffer(), degraded: true });
  }
});

app.post('/mark_read', requireToken, async (req, res) => {
  const chatId = ((req.body && req.body.chatId) || '').toString().trim();
  if (!chatId) return res.status(400).json({ ok: false, error: 'missing chatId' });
  try {
    if (!clientReady()) return res.status(503).json({ ok: false, ready: false, error: 'client not ready' });
    const seen = await client.sendSeen(chatId);
    // Clear our own unread counter for this chat.
    if (unreadByChat.get(chatId)) {
      unreadByChat.set(chatId, 0);
      persistState();
    }
    res.json({ ok: true, chatId, seen: seen !== false });
  } catch (e) {
    console.error('mark_read error', e);
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

app.post('/message_acks', requireToken, async (req, res) => {
  const ids = Array.isArray(req.body && req.body.ids) ? req.body.ids.slice(0, 500) : [];
  if (!ids.length) return res.json({ ok: true, acks: {} });
  try {
    if (!clientReady()) {
      return res.status(503).json({ ok: false, ready: false, error: 'client not ready' });
    }
    const acks = {};
    await Promise.all(ids.map(async rawId => {
      const id = (rawId || '').toString();
      if (!id) return;
      if (messageAcks.has(id)) {
        acks[id] = messageAcks.get(id);
        return;
      }
      try {
        const message = await client.getMessageById(id);
        if (message) {
          acks[id] = message.ack ?? 0;
          messageAcks.set(id, acks[id]);
        }
      } catch (e) {
        // Very old or deleted messages may no longer be available.
      }
    }));
    res.json({ ok: true, acks });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

// reverse lookup: chat id -> display name
app.get('/name', async (req, res) => {
  const chatId = (req.query.chatId || '').toString().trim();
  if (!chatId) return res.status(400).json({ ok: false, error: 'missing chatId' });
  try {
    if (!clientReady()) return res.json({ ok: true, ready: false, name: null });
    const contact = await client.getContactById(chatId);
    return res.json({ ok: true, name: contactDisplayName(contact) || null });
  } catch (e) {
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

// Resolve against both existing chats and the complete WhatsApp contact store.
app.get('/resolve', async (req, res) => {
  const name = (req.query.name || '').toString().trim();
  if (!name) return res.status(400).json({ ok: false, error: 'missing name' });
  try {
    if (!clientReady()) return res.json({ ok: true, ready: false, id: null });
    const { chats, contacts } = await loadResolveSources(client);
    const candidates = new Map();

    for (const c of chats) {
      const id = c.id && c.id._serialized ? c.id._serialized : c.id;
      if (!id || ignoredId(id)) continue;
      const displayName = c.name || c.formattedTitle || contactDisplayName(c.contact, id);
      const score = matchScore(name, displayName);
      if (score > 0) candidates.set(id, { id, name: displayName || id, score, isGroup: !!c.isGroup });
    }
    for (const contact of contacts) {
      const id = contact.id && contact.id._serialized ? contact.id._serialized : contact.id;
      if (!id || ignoredId(id) || contact.isMe) continue;
      const displayName = contactDisplayName(contact, id);
      const score = matchScore(name, displayName);
      if (score <= 0) continue;
      const previous = candidates.get(id);
      if (!previous || score > previous.score) {
        candidates.set(id, { id, name: displayName || id, score, isGroup: false });
      }
    }

    const ranked = dedupeLinkedIdentityCandidates(
      Array.from(candidates.values())
        .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name, 'es'))
    );
    if (!ranked.length) return res.json({ ok: true, ready: true, id: null, candidates: [] });

    const bestScore = ranked[0].score;
    const best = ranked.filter(item => item.score === bestScore);
    if (best.length > 1) {
      return res.json({ ok: true, ready: true, id: null, ambiguous: true, candidates: best.slice(0, 8) });
    }
    return res.json({ ok: true, ready: true, id: best[0].id, name: best[0].name, candidates: ranked.slice(0, 8) });
  } catch (e) {
    console.error('resolve error', e);
    res.status(500).json({ ok: false, error: e.toString() });
  }
});

// Persist remaining state on shutdown so nothing in the debounce window is lost.
// Also destroy the client so Chromium releases its profile lock cleanly — a
// hard kill here is what leaves the stale locks that break the next launch.
let _exiting = false;
function gracefulExit() {
  if (_exiting) return;
  _exiting = true;
  try { flushState(); } catch (e) { /* best effort */ }
  // Never let cleanup block shutdown for more than a moment. If destroy()
  // hangs (e.g. detached frame, same failure mode as the page watchdog
  // guards against), the bail must still make sure Chrome dies — otherwise
  // it survives as an orphan holding the profile lock for the next launch.
  const bail = setTimeout(() => { killBrowserTree(); process.exit(0); }, 3000);
  Promise.resolve()
    .then(() => client.destroy())
    .catch(() => {})
    .finally(() => { clearTimeout(bail); killBrowserTree(); process.exit(0); });
}
process.on('SIGINT', gracefulExit);
process.on('SIGTERM', gracefulExit);

const PORT = process.env.PORT || 3000;
loadState();
app.listen(PORT, '127.0.0.1', () => {
  console.log(`WhatsApp bridge listening on 127.0.0.1:${PORT}`);
  // We own the port → no other bridge is live → safe to clear stale locks that
  // a previous abrupt shutdown may have left behind. Sweep any orphaned
  // Chrome from that same abrupt shutdown first, or it'll just recreate the
  // locks we're about to delete.
  try {
    killOrphanChromeForProfile();
  } catch (e) {
    // best effort
  }
  try {
    clearStaleChromiumLocks();
  } catch (e) {
    // best effort
  }
  const boot = () => {
    armInitWatchdog();
    client.initialize().catch((err) => {
      console.error('initial initialize failed, retrying in 10s:', err && err.message ? err.message : err);
      setState('starting', 'Reintentando iniciar el navegador…');
      setTimeout(boot, 10000);
    });
  };
  boot();
});
