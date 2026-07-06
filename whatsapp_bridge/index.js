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

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: path.join(DATA_DIR, '.wwebjs_auth') }),
  takeoverOnConflict: true,   // keep this session instead of getting kicked out
  takeoverTimeoutMs: 10000,
  webVersionCache: {
    type: 'remote',
    remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/data.json',
  },
  puppeteer: {
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  },
});

function clientReady() {
  return !!(isReady && client.info && client.info.wid);
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

async function serializeMessage(m, chatId = null) {
  const messageId = m.id ? m.id._serialized : null;
  const bodyText = messageBody(m);
  const mentionedIds = Array.isArray(m.mentionedIds) ? m.mentionedIds : [];
  const mentions = {};
  for (const id of mentionedIds) {
    const name = await safeContactName(id);
    if (name) mentions[id] = name;
  }
  const authorName = m.author ? await safeContactName(m.author) : null;
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
    mediaUrl: m.hasMedia && m.id ? `/media?id=${encodeURIComponent(m.id._serialized)}` : null,
    mentionedIds: mentionedIds,
    mentions: mentions,
    ack: messageId && messageAcks.has(messageId) ? messageAcks.get(messageId) : (m.ack ?? null),
    timestamp: m.timestamp || Date.now()
  };
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

client.on('message', (msg) => {
  try {
    const entry = {
      id: msg.id ? msg.id._serialized : null,
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
      mediaUrl: msg.hasMedia && msg.id ? `/media?id=${encodeURIComponent(msg.id._serialized)}` : null,
      mentionedIds: Array.isArray(msg.mentionedIds) ? msg.mentionedIds : [],
      timestamp: Date.now(),
    };
    console.log(`[MSG IN] from=${entry.from} author=${entry.author || '-'} name=${entry.senderName || '-'} type=${entry.type} body=${entry.body.slice(0,60)}`);;
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
client.on('message_create', (msg) => {
  try {
    if (msg.fromMe && msg.to) {
      if (unreadByChat.get(msg.to)) {
        unreadByChat.set(msg.to, 0);
        persistState();
      }
    }
  } catch (e) {
    // best effort
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
      let last = c.lastMessage || null;
      return {
        chatId: id,
        name: c.name || c.formattedTitle || (c.contact && (c.contact.name || c.contact.pushname)) || id,
        isGroup: !!c.isGroup,
        // whatsapp-web.js unreadCount is authoritative when it reflects a read
        // (e.g. on the phone); our own counter fills in live messages it misses.
        unread: Math.max(c.unreadCount || 0, unreadByChat.get(id) || 0),
        timestamp: (last && last.timestamp) || c.timestamp || 0,
        preview: last ? messageBody(last) : '',
        fromMe: last ? !!last.fromMe : false,
        pictureUrl: includePictures ? await safeProfilePicUrl(id) : null,
      };
    }));
    out.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    res.json({ ok: true, ready: true, chats: out, total: visibleChats.length });
  } catch (e) {
    console.warn('chats unavailable, retrying later:', e && e.message ? e.message : e.toString());
    res.status(503).json({ ok: false, ready: false, chats: [], error: e && e.message ? e.message : e.toString() });
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
  const cached = mediaCache.get(id);
  if (cached && (Date.now() - cached.ts) < MEDIA_CACHE_TTL_MS) {
    cached.ts = Date.now();
    return cached;
  }
  const msg = await client.getMessageById(id);
  if (!msg) return { error: 'message not found', status: 404 };
  if (!msg.hasMedia) return { error: 'message has no media', status: 404 };
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
    const m = await client.sendMessage(to, body);
    try {
      const fromId = client.info && client.info.wid ? client.info.wid._serialized : 'me';
      const entry = {
        id: m.id ? m.id._serialized : null,
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
        ack: m.ack ?? 0
      };
      if (entry.id) messageAcks.set(entry.id, entry.ack);
      messages.push(entry);
      if (messages.length > MAX_BUFFERED_MESSAGES) messages.shift();
      persistState();
    } catch (e) {
      console.error('failed to record outgoing message', e);
    }
    res.json({ ok: true, id: m.id._serialized, to, body, ack: m.ack ?? 0 });
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

// fetch recent messages for a specific chat id (chatId or phone@c.us)
app.get('/chat_messages', async (req, res) => {
  const chatId = req.query.chatId || req.query.to;
  if (!chatId) return res.status(400).json({ ok: false, error: 'missing chatId' });
  try {
    if (!clientReady()) return res.json({ ok: true, ready: false, messages: [] });
    const chat = await client.getChatById(chatId);
    if (!chat) return res.json({ ok: true, messages: [] });
    const limit = Math.max(1, Math.min(5000, parseInt(req.query.limit || '1000', 10) || 1000));
    const msgs = await chat.fetchMessages({ limit });
    const out = await Promise.all(msgs.map(m => serializeMessage(m, chatId)));
    res.json({ ok: true, messages: out });
  } catch (e) {
    console.error('chat_messages error', e);
    res.status(500).json({ ok: false, error: e.toString() });
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
  // Never let cleanup block shutdown for more than a moment.
  const bail = setTimeout(() => process.exit(0), 3000);
  Promise.resolve()
    .then(() => client.destroy())
    .catch(() => {})
    .finally(() => { clearTimeout(bail); process.exit(0); });
}
process.on('SIGINT', gracefulExit);
process.on('SIGTERM', gracefulExit);

const PORT = process.env.PORT || 3000;
loadState();
app.listen(PORT, '127.0.0.1', () => {
  console.log(`WhatsApp bridge listening on 127.0.0.1:${PORT}`);
  // We own the port → no other bridge is live → safe to clear stale locks that
  // a previous abrupt shutdown may have left behind.
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
