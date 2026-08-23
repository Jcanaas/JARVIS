/**
 * Guarded access to expo-notifications.
 *
 * The library throws on Android inside Expo Go the moment anything touches its
 * push-token paths ("Android Push notifications ... was removed from Expo Go
 * with the release of SDK 53"), and a throw while a module is being evaluated
 * takes the whole bundle down with a "runtime not ready" error before the app
 * ever renders.
 *
 * We only use LOCAL notifications, which do still work in Expo Go — so the
 * module is required lazily and every call is wrapped, letting the app run
 * either way instead of refusing to start.
 */

import { isRunningInExpoGo } from "expo";

let cached: any | null | undefined;

function load(): any | null {
  if (cached !== undefined) return cached;
  // Not even a require: importing the module inside Expo Go on Android is
  // enough to trigger its push-token code paths, which throw on sight. The
  // library exposes this very check for the purpose. A development build or
  // the APK returns false here and gets the real module.
  if (isRunningInExpoGo()) {
    cached = null;
    return cached;
  }
  try {
    cached = require("expo-notifications");
  } catch {
    cached = null;
  }
  return cached;
}

/** Show notifications even while the app is in the foreground — which is
 * exactly when the user is looking at the phone. Safe to call repeatedly. */
export function configureForegroundDisplay() {
  const N = load();
  if (!N?.setNotificationHandler) return;
  try {
    N.setNotificationHandler({
      handleNotification: async () => ({
        shouldShowBanner: true,
        shouldShowList: true,
        shouldPlaySound: false,
        shouldSetBadge: false,
      }),
    });
  } catch {
    // notifications unavailable in this runtime
  }
}

/** Returns true when notifications may be posted. */
export async function ensureNotificationPermission(): Promise<boolean> {
  const N = load();
  if (!N?.getPermissionsAsync) return false;
  try {
    const current = await N.getPermissionsAsync();
    if (current?.granted) return true;
    const asked = await N.requestPermissionsAsync();
    return !!asked?.granted;
  } catch {
    return false;
  }
}

export async function ensureChannel(id: string, name: string) {
  const N = load();
  if (!N?.setNotificationChannelAsync) return;
  try {
    await N.setNotificationChannelAsync(id, {
      name,
      importance: N.AndroidImportance?.DEFAULT ?? 3,
      sound: null,
    });
  } catch {
    // channels are Android-only; ignore elsewhere
  }
}

export async function presentNotification(content: any): Promise<string | null> {
  const N = load();
  if (!N?.scheduleNotificationAsync) return null;
  try {
    return await N.scheduleNotificationAsync({ content, trigger: null });
  } catch {
    return null;
  }
}

export async function dismissNotification(id: string | null) {
  const N = load();
  if (!id || !N?.dismissNotificationAsync) return;
  try {
    await N.dismissNotificationAsync(id);
  } catch {
    // already gone
  }
}

/** Subscribe to taps on a notification. Returns an unsubscribe function. */
export function onNotificationTapped(handler: (data: any) => void): () => void {
  const N = load();
  if (!N?.addNotificationResponseReceivedListener) return () => {};
  try {
    const sub = N.addNotificationResponseReceivedListener((response: any) => {
      handler(response?.notification?.request?.content?.data);
    });
    return () => {
      try { sub.remove(); } catch { /* already gone */ }
    };
  } catch {
    return () => {};
  }
}
