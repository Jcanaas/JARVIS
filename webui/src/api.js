// Thin wrapper over actions/webui_server.py's REST + SSE API. Same origin
// in both dev (via vite.config.js's /api proxy) and production (webui_server
// serves this app's built dist/ itself), so no base URL / CORS handling
// needed here.

export async function getJSON(path) {
  const res = await fetch(path)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const err = new Error(body.error || `${res.status} ${res.statusText}`)
    if (body.needs_api_key) err.needsApiKey = true
    throw err
  }
  return res.json()
}

export async function postJSON(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.error || `${res.status} ${res.statusText}`)
  }
  return data
}

export function getState() {
  return getJSON('/api/state')
}

export const getSetupStatus = () => getJSON('/api/setup/status')
export const completeSetup = (geminiApiKey, osSystem) =>
  postJSON('/api/setup/complete', { gemini_api_key: geminiApiKey, os_system: osSystem })

export const getLog = (since = 0) => getJSON(`/api/log?since=${since}`)
export const sendCommand = (text) => postJSON('/api/command', { text })
export const toggleMic = () => postJSON('/api/mic/toggle', {})
export const toggleFullscreen = () => postJSON('/api/window/fullscreen', {})
export const setAlwaysOnTop = (enabled) => postJSON('/api/window/always_on_top', { enabled })
export const fireInterrupt = () => postJSON('/api/interrupt', {})

// WhatsApp
export const listChats = () => getJSON('/api/whatsapp/chats')
export const getMessages = (chatId) => getJSON(`/api/whatsapp/messages?chat_id=${encodeURIComponent(chatId)}`)
export const sendMessage = (chatId, text) => postJSON('/api/whatsapp/send', { chat_id: chatId, text })
export const getWhatsappStatus = () => getJSON('/api/whatsapp/status')
export const whatsappReconnect = () => postJSON('/api/whatsapp/reconnect', {})
export const getWhatsappAvatar = (chatId) => getJSON(`/api/whatsapp/avatar?chat_id=${encodeURIComponent(chatId)}`)
export const whatsappAvatarImageUrl = (chatId) => `/api/whatsapp/avatar_image?chat_id=${encodeURIComponent(chatId)}`
export const whatsappMediaUrl = (mediaUrl) => `/api/whatsapp/media?media_url=${encodeURIComponent(mediaUrl)}`
export const transcribeWhatsappAudio = (message) => postJSON('/api/whatsapp/transcribe', { message })
export const editWhatsappMessage = (messageId, newBody) => postJSON('/api/whatsapp/edit', { message_id: messageId, new_body: newBody })
export const markChatRead = (chatId) => postJSON('/api/whatsapp/mark_read', { chat_id: chatId })
export const getMessageAcks = (messageIds) => postJSON('/api/whatsapp/acks', { message_ids: messageIds })
export const suggestWhatsappReply = (chatId, incomingBody = '') =>
  postJSON('/api/whatsapp/suggest', { chat_id: chatId, incoming_body: incomingBody })
export async function sendWhatsappMedia(chatId, file, caption = '') {
  const form = new FormData()
  form.set('chat_id', chatId)
  form.set('caption', caption)
  form.set('file', file)
  const res = await fetch('/api/whatsapp/send_media', { method: 'POST', body: form })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`)
  return data
}

// Music
export const searchMusic = (q) => getJSON(`/api/music/search?q=${encodeURIComponent(q)}`)
export const getRecommendations = () => getJSON('/api/music/recommendations')
export const listPlaylists = () => getJSON('/api/music/playlists')
export const listPlaylistTracks = (playlistId, limit = 100) =>
  getJSON(`/api/music/playlist_tracks?playlist_id=${encodeURIComponent(playlistId)}&limit=${limit}`)
export const getLyrics = (title, artists = '') =>
  getJSON(`/api/music/lyrics?title=${encodeURIComponent(title)}&artists=${encodeURIComponent(artists)}`)
export const musicQueue = () => getJSON('/api/music/queue')
export const musicAction = (action, params) => postJSON(`/api/music/${action}`, params)

// Movies / TV
export const searchMovies = (q, kind = 'multi') =>
  getJSON(`/api/movies/search?q=${encodeURIComponent(q)}&kind=${kind}`)
export const getMoviesTrending = (kind = 'movie') => getJSON(`/api/movies/trending?kind=${kind}`)
export const getMovieDetails = (tmdbId, kind = 'movie') => getJSON(`/api/movies/details/${tmdbId}?kind=${kind}`)
export const getMovieTorrents = (tmdbId, kind = 'movie', season = 0, episode = 0) =>
  getJSON(`/api/movies/torrents?tmdb_id=${tmdbId}&kind=${kind}&season=${season}&episode=${episode}`)
export const playMovieTorrent = (magnet, fileIdx = -1) =>
  postJSON('/api/movies/play', { magnet, file_idx: fileIdx })
export const stopMoviePlayback = () => postJSON('/api/movies/stop', {})
export const getMoviesStatus = () => getJSON('/api/movies/status')

// Anime — same vlc_player playback pipeline as Movies/TV (playMovieTorrent/
// stopMoviePlayback above), different metadata + torrent lookup.
export const searchAnime = (q) => getJSON(`/api/anime/search?q=${encodeURIComponent(q)}`)
export const getAnimeTrending = (airing = false) => getJSON(`/api/anime/trending?airing=${airing ? 1 : 0}`)
export const getAnimeTorrents = (tmdbId, season = 0, episode = 0) =>
  getJSON(`/api/anime/torrents?tmdb_id=${tmdbId}&season=${season}&episode=${episode}`)

// YouTube — search/recommendations only; playback is the official iframe
// embed (see actions/webui_server.py's comment for why: modern YouTube
// serves no single pre-muxed URL a <video> tag could play directly).
export const searchYoutube = (q) => getJSON(`/api/youtube/search?q=${encodeURIComponent(q)}`)
export const getYoutubeRecommendations = () => getJSON('/api/youtube/recommendations')

// Emuladores — actions/emulator_session.py, one session per process.
// /api/emulator/stream (MJPEG) is consumed directly as an <img src>, no
// wrapper needed here.
export const getEmulatorLibrary = () => getJSON('/api/emulator/library')
export const getEmulatorStatus = () => getJSON('/api/emulator/status')
export const startEmulator = (entryId) => postJSON('/api/emulator/start', { entry_id: entryId })
export const stopEmulator = () => postJSON('/api/emulator/stop', {})
export const pauseEmulator = (paused) => postJSON('/api/emulator/pause', { paused })
export const resetEmulator = () => postJSON('/api/emulator/reset', {})
export const sendEmulatorInput = (body) => postJSON('/api/emulator/input', body)
export const saveEmulatorState = () => postJSON('/api/emulator/state/save', {})
export const loadEmulatorState = () => postJSON('/api/emulator/state/load', {})
export const getEmulatorPad = (consoleId) => getJSON(`/api/emulator/pad?console_id=${encodeURIComponent(consoleId)}`)
export async function importEmulatorBios(file) {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/emulator/bios/import', { method: 'POST', body: form })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`)
  return data
}
export const getEmulatorControls = (consoleId) =>
  getJSON(`/api/emulator/controls?console_id=${encodeURIComponent(consoleId)}`)
export const bindEmulatorControl = (consoleId, button, code) =>
  postJSON('/api/emulator/controls/bind', { console_id: consoleId, button, code })
export const resetEmulatorControls = (consoleId) =>
  postJSON('/api/emulator/controls/reset', { console_id: consoleId })

// Calendar
export const getCalendarEvents = (timeMin, timeMax) =>
  getJSON(`/api/calendar/events?time_min=${encodeURIComponent(timeMin)}&time_max=${encodeURIComponent(timeMax)}`)
export const searchCalendar = (q) => getJSON(`/api/calendar/search?q=${encodeURIComponent(q)}`)
export const createEvent = (payload) => postJSON('/api/calendar/create', payload)
export const updateEvent = (payload) => postJSON('/api/calendar/update', payload)
export const deleteEvent = (eventId) => postJSON('/api/calendar/delete', { event_id: eventId })
export const listContacts = () => getJSON('/api/contacts')
export const upsertContact = (name, email) => postJSON('/api/contacts', { name, email })

// Gmail
export const getEmails = ({ page = 1, pageSize = 50, label = 'ALL', unreadOnly = false, query = '' } = {}) =>
  getJSON(`/api/gmail/emails?page=${page}&page_size=${pageSize}&label=${encodeURIComponent(label)}&unread_only=${unreadOnly ? 1 : 0}&query=${encodeURIComponent(query)}`)
export const readEmail = (id) => getJSON(`/api/gmail/email/${encodeURIComponent(id)}`)
export const getEmailImages = (id) => getJSON(`/api/gmail/email/${encodeURIComponent(id)}/images`)
export const archiveEmail = (id) => postJSON(`/api/gmail/email/${encodeURIComponent(id)}/archive`, {})
export const trashEmail = (id) => postJSON(`/api/gmail/email/${encodeURIComponent(id)}/trash`, {})
export const aiDraftEmail = (instruction, to = '', subject = '') =>
  postJSON('/api/gmail/ai_draft', { instruction, to, subject })
export async function sendEmail({ to, subject, body, attachments = [] }) {
  const form = new FormData()
  form.set('to', to)
  form.set('subject', subject)
  form.set('body', body)
  for (const f of attachments) form.append('attachments', f)
  const res = await fetch('/api/gmail/send', { method: 'POST', body: form })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`)
  return data
}

// Drive
export const getDriveFiles = (folderId = null, count = 200) =>
  getJSON(`/api/drive/files?count=${count}${folderId ? `&folder_id=${encodeURIComponent(folderId)}` : ''}`)
export const searchDrive = (q, folderId = null) =>
  getJSON(`/api/drive/search?q=${encodeURIComponent(q)}${folderId ? `&folder_id=${encodeURIComponent(folderId)}` : ''}`)
export const getDrivePreview = (fileId) => getJSON(`/api/drive/preview/${encodeURIComponent(fileId)}`)
export const drivePreviewRawUrl = (fileId) => `/api/drive/preview_raw/${encodeURIComponent(fileId)}`
export const driveDownloadUrl = (fileId) => `/api/drive/download/${encodeURIComponent(fileId)}`
export async function uploadDriveFile(file, folderId = null) {
  const form = new FormData()
  form.set('file', file)
  if (folderId) form.set('folder_id', folderId)
  const res = await fetch('/api/drive/upload', { method: 'POST', body: form })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`)
  return data
}
export const createDriveFolder = (name, parentId = null) =>
  postJSON('/api/drive/create_folder', { name, parent_id: parentId })
export const shareDriveFile = (fileId, { email = '', role = 'reader', anyone = false, notify = false } = {}) =>
  postJSON('/api/drive/share', { file_id: fileId, email, role, anyone, notify })
export const renameDriveFile = (fileId, newName) =>
  postJSON('/api/drive/rename', { file_id: fileId, new_name: newName })
export const deleteDriveFile = (fileId, permanent = false) =>
  postJSON('/api/drive/delete', { file_id: fileId, permanent })

// CardTrader
export const cardtraderStatus = () => getJSON('/api/cardtrader/status')
export const cardtraderSync = () => postJSON('/api/cardtrader/sync', {})
export const cardtraderSearch = (payload) => postJSON('/api/cardtrader/search', payload)
export const cardtraderQuoteDeck = (payload) => postJSON('/api/cardtrader/quote_deck', payload)
export const cardtraderCartAdd = (items) => postJSON('/api/cardtrader/cart_add', { items })
export const cardtraderCart = () => getJSON('/api/cardtrader/cart')
export const cardtraderCartRemove = (productId, quantity) =>
  postJSON('/api/cardtrader/cart_remove', { product_id: productId, quantity })

// Settings
export const getSettings = () => getJSON('/api/settings')
export const setSetting = (key, value) => postJSON('/api/settings', { key, value })
export const toggleWhatsappConnection = (enabled) => postJSON('/api/settings/whatsapp/toggle', { enabled })
export const getWhatsappRules = () => getJSON('/api/settings/whatsapp_rules')
export const addWhatsappRule = (rule) => postJSON('/api/settings/whatsapp_rules', rule)
export const updateWhatsappRule = (id, rule) => postJSON(`/api/settings/whatsapp_rules/${encodeURIComponent(id)}`, rule)
export const deleteWhatsappRule = (id) =>
  fetch(`/api/settings/whatsapp_rules/${encodeURIComponent(id)}`, { method: 'DELETE' }).then((r) => r.json())
export const moveWhatsappRule = (id, delta) =>
  postJSON(`/api/settings/whatsapp_rules/${encodeURIComponent(id)}/move`, { delta })
export const getApiKeys = () => getJSON('/api/settings/api_keys')
export const setApiKeys = (keys) => postJSON('/api/settings/api_keys', keys)
export const getProfile = () => getJSON('/api/settings/profile')
export const setProfile = (profile) => postJSON('/api/settings/profile', profile)
export const getAnimeSfw = () => getJSON('/api/settings/anime_sfw')
export const setAnimeSfw = (sfw) => postJSON('/api/settings/anime_sfw', { sfw })
export const getAudioDevices = () => getJSON('/api/settings/audio_devices')
export const setPerformancePlan = (plan) => postJSON('/api/settings/performance_plan', { plan })
export const getLanDashboardQr = () => getJSON('/api/settings/lan_dashboard/qr')
export const resetJarvis = () => postJSON('/api/settings/reset', {})
export const getAbout = () => getJSON('/api/settings/about')

// Downloads (torrents: games/movies/anime)
export const listDownloads = () => getJSON('/api/downloads')
export const pauseDownload = (id) => postJSON(`/api/downloads/${encodeURIComponent(id)}/pause`, {})
export const resumeDownload = (id) => postJSON(`/api/downloads/${encodeURIComponent(id)}/resume`, {})
export const removeDownload = (id, deleteFiles = false) =>
  postJSON(`/api/downloads/${encodeURIComponent(id)}/remove`, { delete_files: deleteFiles })
export const openDownloadFolder = (id) => postJSON(`/api/downloads/${encodeURIComponent(id)}/open_folder`, {})

// File drop zone
export async function uploadFile(file) {
  const form = new FormData()
  form.set('file', file)
  const res = await fetch('/api/files/upload', { method: 'POST', body: form })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`)
  return data
}

let _eventSource = null

// Single shared EventSource for the whole app; returns it (creating it on
// first call) so every panel/store subscribes to the same connection
// instead of opening one per component.
export function connectEvents(onMessage) {
  if (_eventSource) return _eventSource
  _eventSource = new EventSource('/api/events')
  _eventSource.onmessage = (evt) => {
    try {
      const payload = JSON.parse(evt.data)
      onMessage(payload.type, payload.data)
    } catch {
      // ignore malformed frames
    }
  }
  return _eventSource
}
