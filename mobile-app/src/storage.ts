import AsyncStorage from "@react-native-async-storage/async-storage";

const KEY = "jarvis_connection";

export type Connection = { baseUrl: string; token: string };

export async function loadConnection(): Promise<Connection | null> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.baseUrl === "string" && typeof parsed.token === "string") {
      return parsed;
    }
    return null;
  } catch {
    return null;
  }
}

export async function saveConnection(conn: Connection): Promise<void> {
  await AsyncStorage.setItem(KEY, JSON.stringify(conn));
}

export async function clearConnection(): Promise<void> {
  await AsyncStorage.removeItem(KEY);
}

/** Parses a scanned QR value like "http://192.168.0.18:8765/?token=abc123"
 * into {baseUrl, token} — same URL the desktop's "Mostrar código QR" already
 * generates (actions/lan_dashboard.py:start_dashboard). */
export function parseConnectionUrl(raw: string): Connection | null {
  try {
    const url = new URL(raw.trim());
    const token = url.searchParams.get("token");
    if (!token) return null;
    return { baseUrl: `${url.protocol}//${url.host}`, token };
  } catch {
    return null;
  }
}

const LAYOUT_KEY = "jarvis_pad_layout";

/** Pixel offset (and optional scale) from a control group's default
 * position/size, keyed by group id (e.g. "group:dpad", "group:face" — see
 * GamepadOverlay.tsx). A device-local preference, deliberately not synced
 * to the desktop: the emulator itself has no notion of where or how big
 * the phone draws its own buttons. `scale` defaults to 1 when absent. */
export type PadOffsets = Record<string, { x: number; y: number; scale?: number }>;

export async function loadPadLayout(): Promise<PadOffsets> {
  try {
    const raw = await AsyncStorage.getItem(LAYOUT_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export async function savePadLayout(offsets: PadOffsets): Promise<void> {
  await AsyncStorage.setItem(LAYOUT_KEY, JSON.stringify(offsets));
}

export async function resetPadLayout(): Promise<void> {
  await AsyncStorage.removeItem(LAYOUT_KEY);
}
