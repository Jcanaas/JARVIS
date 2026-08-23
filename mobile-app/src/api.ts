import { Connection } from "./storage";

export type MusicStatus = {
  title: string;
  artists: string;
  playing: boolean;
  state: string;
  position: number;
  duration: number;
  volume: number;
  videoId?: string;
  thumbnail?: string;
  liked?: boolean;
  /** `#rrggbb` sampled from the cover; tints the media notification. Empty
   * until the desktop has finished reading that cover. */
  coverColor?: string;
};

export type Status = {
  running: boolean;
  now: number;
  music: MusicStatus;
  mode: string;
  muted: boolean;
};

export type LogEntry = {
  id: number;
  type: "log" | "toast" | "error";
  source: string;
  message: string;
};

export type Playlist = {
  playlistId: string;
  title: string;
  author: string;
  itemCount: number | string;
  thumbnail?: string;
};

export type Track = {
  videoId: string;
  title: string;
  artists: string;
  duration?: string;
  thumbnail?: string;
  /** Only present on /api/music/queue items — absolute index into the
   * backend's playlist, required by the jump_to action (NOT this item's
   * position within the queue list, which is relative to the current track). */
  index?: number;
};

export type Artist = {
  browseId: string;
  name: string;
  thumbnail?: string;
  subscribers?: string;
  url?: string;
};

export type ArtistDetail = Artist & {
  description?: string;
  monthlyListeners?: string;
  views?: string;
  top_songs: Track[];
  recommendations: Track[];
  albums: { browseId: string; title: string; artists: string; year?: string; thumbnail?: string }[];
  singles: { browseId: string; title: string; artists: string; year?: string; thumbnail?: string }[];
  videos: { videoId: string; title: string; artists: string; thumbnail?: string }[];
  related: Artist[];
};

export type WaChat = {
  chatId: string;
  name: string;
  preview: string;
  timestamp: number;
  fromMe: boolean;
  unread: number;
  isGroup: boolean;
};

export type WaMessage = {
  id: string;
  body: string;
  fromMe: boolean;
  authorName?: string;
  senderName?: string;
  hasMedia?: boolean;
  mediaUrl?: string;
  mimetype?: string;
  fileName?: string;
  /** WhatsApp's own message kind: "image" | "sticker" | "audio" | "ptt" |
   * "video" | "document" | "chat" | ... — more reliable than mimetype,
   * which is often null until the media is actually downloaded. */
  type?: string;
  timestamp?: number;
  sentAtMs?: number;
};

export type CalendarEvent = {
  id: string;
  summary: string;
  /** RFC3339 instant, or a bare YYYY-MM-DD when `all_day` is true. */
  start: string;
  end?: string;
  all_day?: boolean;
  location?: string;
  description?: string;
  attendees?: { email?: string; displayName?: string }[];
  link?: string;
};

/** One synced lyric line; `time` is the second it starts at. */
export type LyricLine = { time: number; line: string };

export type WaRuleContact = { chat_id: string; name: string };

/** An auto-reply rule. Order in the list IS priority — first match wins. */
export type WaRule = {
  id?: string;
  name: string;
  enabled: boolean;
  contacts: WaRuleContact[];
  /** When true, days/start/end are ignored and the rule is always active. */
  always: boolean;
  /** 0 = Monday … 6 = Sunday. */
  days: number[];
  start: string;
  end: string;
  timezone?: string;
  prompt: string;
};

export type WaAutomations = {
  settings: { whatsapp_auto_translate: boolean; whatsapp_auto_transcribe: boolean };
  rules: WaRule[];
};

/** What the running console's controller actually has, so the touch pad can
 * draw sticks/triggers only where they exist. */
export type PadLayout = {
  console?: string;
  /** 0 (digital only), 1 (N64) or 2 (PS2-style). */
  sticks: number;
  triggers: boolean;
  stick_buttons: boolean;
  shoulders: boolean;
  face: "nintendo" | "playstation" | string;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let conn: Connection | null = null;

export function configureApi(c: Connection) {
  conn = c;
}

/** Builds an authenticated URL for /api/whatsapp/media, suitable for
 * <Image source={{uri}}> or an audio player — proxies the bridge's media
 * bytes through our own token-gated endpoint (actions/lan_dashboard.py). */
export function mediaProxyUrl(mediaUrl: string, mimetype?: string): string {
  const c = requireConn();
  const params = new URLSearchParams({ url: mediaUrl, token: c.token });
  if (mimetype) params.set("mimetype", mimetype);
  return `${c.baseUrl}/api/whatsapp/media?${params.toString()}`;
}

/** Authenticated URL the phone's own audio player can stream a track from,
 * used when the output is switched from the PC to this phone. */
export function trackStreamUrl(videoId: string): string {
  const c = requireConn();
  return `${c.baseUrl}/api/music/stream?video_id=${encodeURIComponent(videoId)}&token=${encodeURIComponent(c.token)}`;
}

/** Authenticated URL for a chat/contact's profile picture, proxied through
 * actions/lan_dashboard.py's /api/whatsapp/avatar (may 404 — no picture set,
 * or the bridge couldn't fetch one; callers should fall back to initials). */
export function avatarProxyUrl(chatId: string): string {
  const c = requireConn();
  return `${c.baseUrl}/api/whatsapp/avatar?chat_id=${encodeURIComponent(chatId)}&token=${encodeURIComponent(c.token)}`;
}

function requireConn(): Connection {
  if (!conn) throw new Error("API not configured — pair with the desktop app first");
  return conn;
}

/** Message for status 0 (fetch never reached the server — DNS/refused/
 * timeout, surfaced by Android as a raw "java.net.ConnectException" etc.).
 * Almost always means the PC's LAN IP changed since pairing (it went to
 * sleep and woke up on a new DHCP lease, moved network, ...). */
const UNREACHABLE_MSG =
  "No se pudo conectar con el PC. Comprueba que esté encendido, en la " +
  "misma red, y que la IP no haya cambiado (Ajustes → olvidar conexión y " +
  "volver a escanear el QR).";

/** Wraps a fetch() call so any network-level failure (host unreachable,
 * DNS, timeout — as opposed to a valid HTTP error response) surfaces as a
 * clear ApiError instead of the raw native exception text. */
async function safeFetch(url: string, opts?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, opts);
  } catch {
    throw new ApiError(0, UNREACHABLE_MSG);
  }
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const c = requireConn();
  const sep = path.includes("?") ? "&" : "?";
  const url = `${c.baseUrl}${path}${sep}token=${encodeURIComponent(c.token)}`;
  const res = await safeFetch(url, opts);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const msg = (data && (data as any).error) || `HTTP ${res.status}`;
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

export type PingResult =
  | { ok: true }
  /** The desktop answered, but refused us — almost always a stale token. */
  | { ok: false; reason: "rejected"; status: number }
  /** Nothing answered: wrong network, PC asleep, or JARVIS not running. */
  | { ok: false; reason: "unreachable" };

export async function pingServer(c: Connection): Promise<boolean> {
  return (await probeServer(c)).ok;
}

/** Same check as pingServer, but says WHY it failed.
 *
 * Worth the extra type: a 403 from a rotated token and an unreachable host
 * look identical to the caller otherwise, and blaming the Wi-Fi for a token
 * problem sends you chasing the wrong thing.
 */
export async function probeServer(c: Connection): Promise<PingResult> {
  try {
    const url = `${c.baseUrl}/api/status?token=${encodeURIComponent(c.token)}`;
    const res = await fetch(url);
    if (res.ok) return { ok: true };
    return { ok: false, reason: "rejected", status: res.status };
  } catch {
    return { ok: false, reason: "unreachable" };
  }
}

export const api = {
  getStatus: () => request<Status>("/api/status"),
  getLog: (since: number) => request<LogEntry[]>(`/api/log?since=${since}`),
  sendCommand: (text: string) => postJson<{ ok: boolean }>("/api/command", { text }),

  musicAction: (action: string, params?: Record<string, unknown>) =>
    postJson<{ ok: boolean; result?: string }>(`/api/music/${action}`, params),
  getPlaylists: () => request<Playlist[]>("/api/music/playlists"),
  getPlaylistTracks: (playlistId: string) =>
    request<Track[]>(`/api/music/playlist_tracks?playlist_id=${encodeURIComponent(playlistId)}`),
  searchSongs: (q: string) => request<Track[]>(`/api/music/search?q=${encodeURIComponent(q)}`),
  searchArtists: (q: string) => request<Artist[]>(`/api/music/search_artists?q=${encodeURIComponent(q)}`),
  getArtist: (browseId: string) =>
    request<ArtistDetail>(`/api/music/artist?browse_id=${encodeURIComponent(browseId)}`),
  /** Time-stamped lines; empty when no provider had synced lyrics. */
  getLyrics: (title: string, artists: string) =>
    request<LyricLine[]>(
      `/api/music/lyrics?title=${encodeURIComponent(title)}&artists=${encodeURIComponent(artists)}`,
    ),
  getQueue: () => request<Track[]>("/api/music/queue"),
  /** Warm the desktop's stream cache for tracks we are showing.
   *
   * A tap on the phone is the play — there is no "select it first" step to
   * warm anything, so without this every tap waits out the full yt-dlp
   * resolution (measured 2-3 s) before a sound comes out. Fire-and-forget:
   * the desktop runs it off its playback worker, so it can never delay a
   * transport command, and a failure just means the tap is slow again. */
  prefetchTracks: (tracks: Array<Pick<Track, "videoId">>) =>
    postJson<{ scheduled: number }>("/api/music/prefetch", {
      video_ids: tracks.map((t) => t.videoId).filter(Boolean),
    }),

  getCalendarEvents: (timeMin: string, timeMax: string) =>
    request<CalendarEvent[]>(
      `/api/calendar/events?time_min=${encodeURIComponent(timeMin)}&time_max=${encodeURIComponent(timeMax)}`,
    ),
  createCalendarEvent: (body: {
    summary: string; start: string; end?: string; description?: string; location?: string;
  }) => postJson<{ ok: boolean }>("/api/calendar/create", body),
  updateCalendarEvent: (body: {
    event_id: string; summary?: string; start?: string; end?: string; description?: string; location?: string;
  }) => postJson<{ ok: boolean }>("/api/calendar/update", body),
  deleteCalendarEvent: (eventId: string) =>
    postJson<{ ok: boolean }>("/api/calendar/delete", { event_id: eventId }),

  /** Uploads a recording; the desktop transcribes it and (by default) runs it
   * as a spoken command. Returns what it understood. */
  sendVoice: async (uri: string, run = true) => {
    const c = requireConn();
    const form = new FormData();
    // React Native's FormData takes this {uri,name,type} shape rather than a Blob.
    form.append("audio", { uri, name: "voice.m4a", type: "audio/m4a" } as any);
    form.append("run", run ? "1" : "0");
    const res = await safeFetch(`${c.baseUrl}/api/voice?token=${encodeURIComponent(c.token)}`, {
      method: "POST",
      body: form,
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      throw new ApiError(res.status, (data && (data as any).error) || `HTTP ${res.status}`);
    }
    return data as { ok: boolean; text: string; ran: boolean };
  },

  getGamepadStatus: () =>
    request<{
      active: boolean;
      console: string;
      buttons: string[];
      announce?: number;
      layout?: PadLayout;
    }>("/api/gamepad/status"),
  /** Asks the phone-side prompt to reappear (mirrors the desktop's button). */
  announceGamepad: () => postJson<{ ok: boolean; announce: number }>("/api/gamepad/announce", {}),
  sendGamepadInput: (body: {
    buttons?: { name: string; pressed: boolean }[];
    axes?: { index: number; axis: number; value: number }[];
    clear?: boolean;
  }) => postJson<{ ok: boolean }>("/api/gamepad/input", body),

  getClipboard: () => request<{ text: string }>("/api/remote/clipboard"),
  setClipboard: (text: string) => postJson<{ ok: boolean }>("/api/remote/clipboard", { text }),
  remoteAction: (action: string, params?: Record<string, unknown>) =>
    postJson<{ ok: boolean; result?: string }>("/api/remote/system", { action, ...(params || {}) }),
  getRemoteStatus: () => request<Record<string, any>>("/api/remote/status"),

  getWhatsappAutomations: () => request<WaAutomations>("/api/whatsapp/automations"),
  setWhatsappAutomationSetting: (key: string, value: boolean) =>
    postJson<{ ok: boolean }>("/api/whatsapp/automations/setting", { key, value }),
  saveWhatsappRule: (rule: WaRule) =>
    postJson<{ ok: boolean; created: boolean }>("/api/whatsapp/rules/save", { rule }),
  deleteWhatsappRule: (ruleId: string) =>
    postJson<{ ok: boolean }>("/api/whatsapp/rules/delete", { rule_id: ruleId }),
  moveWhatsappRule: (ruleId: string, delta: number) =>
    postJson<{ ok: boolean }>("/api/whatsapp/rules/move", { rule_id: ruleId, delta }),

  getWhatsappChats: () => request<WaChat[]>("/api/whatsapp/chats"),
  getWhatsappMessages: (chatId: string) =>
    request<WaMessage[]>(`/api/whatsapp/messages?chat_id=${encodeURIComponent(chatId)}`),
  /** `{messageId: ack}` — 1 sent, 2 delivered, 3 read, 4 played. */
  getWhatsappAcks: (ids: string[]) =>
    postJson<Record<string, number>>("/api/whatsapp/acks", { ids }),
  transcribeWhatsappAudio: (message: WaMessage) =>
    postJson<{ ok: boolean; text: string }>("/api/whatsapp/transcribe", { message }),
  suggestWhatsappReply: (chatId: string, incoming = "") =>
    postJson<{ ok: boolean; text: string }>("/api/whatsapp/suggest", { chat_id: chatId, incoming }),
  sendWhatsappMedia: async (chatId: string, uri: string, name: string, mimeType: string, caption = "") => {
    const c = requireConn();
    const form = new FormData();
    form.append("file", { uri, name, type: mimeType } as any);
    form.append("chat_id", chatId);
    form.append("caption", caption);
    const res = await safeFetch(`${c.baseUrl}/api/whatsapp/send_media?token=${encodeURIComponent(c.token)}`, {
      method: "POST",
      body: form,
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new ApiError(res.status, (data as any)?.error || `HTTP ${res.status}`);
    return data as { ok: boolean };
  },

  /** Empty `text` means the message was already in Spanish. */
  translateWhatsapp: (text: string) =>
    postJson<{ ok: boolean; text: string }>("/api/whatsapp/translate", { text }),
  markWhatsappRead: (chatId: string) =>
    postJson<{ ok: boolean }>("/api/whatsapp/mark_read", { chat_id: chatId }),
  sendWhatsapp: (chatId: string, text: string) =>
    postJson<{ ok: boolean }>("/api/whatsapp/send", { chat_id: chatId, text }),
};
