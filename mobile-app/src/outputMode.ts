import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * Where the music actually comes out: the desktop's speakers, or this phone.
 *
 * Kept in a tiny module-level store rather than React context because more
 * than one tree reads it (the Música screen and the system-media bridge) and
 * they don't share a common provider above them.
 */
export type OutputMode = "pc" | "phone";

const KEY = "jarvis.outputMode";

let current: OutputMode = "pc";
const listeners = new Set<(mode: OutputMode) => void>();

export function getOutputMode(): OutputMode {
  return current;
}

export function setOutputMode(mode: OutputMode) {
  if (mode === current) return;
  current = mode;
  AsyncStorage.setItem(KEY, mode).catch(() => {});
  listeners.forEach((fn) => fn(mode));
}

export function subscribeOutputMode(fn: (mode: OutputMode) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Restores the last choice on boot. Safe to call more than once. */
export async function loadOutputMode(): Promise<OutputMode> {
  try {
    const saved = await AsyncStorage.getItem(KEY);
    if (saved === "phone" || saved === "pc") {
      current = saved;
      listeners.forEach((fn) => fn(current));
    }
  } catch {
    // keep the default
  }
  return current;
}
