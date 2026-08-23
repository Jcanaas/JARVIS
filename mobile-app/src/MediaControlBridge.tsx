import { useEffect, useRef } from "react";
import { requireOptionalNativeModule } from "expo";
import { api } from "./api";
import { ensureNotificationPermission } from "./notifications";
import { C } from "./theme";
import { getOutputMode } from "./outputMode";
import {
  NowPlaying, getNowPlaying, setNowPlaying, subscribeNowPlaying, runTransport,
} from "./playback";

/**
 * Mirrors playback into the phone's system media controls (notification and
 * lock screen) and routes their buttons back to whatever is actually playing.
 *
 * Reads from the shared playback store rather than from /api/status directly,
 * so the notification follows the music when the output moves between the PC
 * and the phone. It still polls the desktop itself while the PC is the
 * output, because the Música screen may not be mounted.
 *
 * expo-media-control is a native module, so it does not exist in Expo Go. All
 * access goes through `loadMediaControl()`: in Expo Go the bridge simply does
 * nothing instead of crashing the app.
 */

type PlaybackStateEnum = {
  NONE: number; STOPPED: number; PLAYING: number; PAUSED: number; BUFFERING: number; ERROR: number;
};

type MediaControlModule = {
  enableMediaControls: (options?: any) => Promise<void>;
  disableMediaControls: () => Promise<void>;
  updateMetadata: (metadata: any) => Promise<void>;
  updatePlaybackState: (state: number, position?: number, playbackRate?: number) => Promise<void>;
  PlaybackState: PlaybackStateEnum;
  Command: Record<string, string>;
  /** The raw native module — see the comment on the event subscription. */
  native: any;
};

function loadMediaControl(): MediaControlModule | null {
  try {
    // expo-media-control calls requireNativeModule() at module scope, so in
    // Expo Go merely requiring the package throws. Ask for the native module
    // optionally FIRST — that returns null instead of throwing.
    const native = requireOptionalNativeModule("ExpoMediaControl");
    if (!native) return null;

    const mod = require("expo-media-control");
    if (!mod?.enableMediaControls) return null;
    return {
      enableMediaControls: mod.enableMediaControls,
      disableMediaControls: mod.disableMediaControls,
      updateMetadata: mod.updateMetadata,
      updatePlaybackState: mod.updatePlaybackState,
      PlaybackState: mod.PlaybackState,
      Command: mod.Command,
      native,
    };
  } catch {
    return null;
  }
}

function nativeState(mc: MediaControlModule, state: NowPlaying): number {
  const S = mc.PlaybackState;
  if (!state.trackKey) return S.NONE;
  return state.playing ? S.PLAYING : S.PAUSED;
}

export default function MediaControlBridge() {
  const enabledRef = useRef(false);
  const stampRef = useRef("");

  useEffect(() => {
    const mc = loadMediaControl();
    if (!mc) return; // Expo Go, or module unavailable — feature is simply off.

    let cancelled = false;
    let subscription: { remove: () => void } | null = null;
    let unsubscribe: (() => void) | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;

    const push = async (state: NowPlaying) => {
      try {
        if (!state.trackKey) return;
        // Metadata only when something in it changed: re-sending it every few
        // seconds makes Android re-download the artwork. The cover colour
        // arrives a poll or two later (the desktop samples it off-thread), so
        // it counts as a change too.
        const stamp = `${state.trackKey}|${state.coverColor || ""}|${state.source}`;
        if (stamp !== stampRef.current) {
          stampRef.current = stamp;
          await mc.updateMetadata({
            title: state.title,
            artist: state.artists,
            duration: state.duration,
            ...(state.thumbnail ? { artwork: { uri: state.thumbnail } } : {}),
            // Android paints the notification background from this.
            ...(state.coverColor ? { color: state.coverColor, colorized: true } : {}),
          });
        }
        await mc.updatePlaybackState(
          nativeState(mc, state),
          state.position,
          state.playing ? 1.0 : 0.0,
        );
      } catch {
        // transient — the next update retries
      }
    };

    (async () => {
      // Android 13+ refuses to post ANY notification without this, and the
      // media-control library neither asks for it nor reports the failure —
      // it catches the SecurityException and carries on, so the media
      // notification just never appears.
      await ensureNotificationPermission();
      if (cancelled) return;

      try {
        await mc.enableMediaControls({
          capabilities: [
            mc.Command.PLAY,
            mc.Command.PAUSE,
            mc.Command.NEXT_TRACK,
            mc.Command.PREVIOUS_TRACK,
            mc.Command.SEEK,
          ],
          // Android shows at most 3 buttons in the collapsed notification.
          compactCapabilities: [
            mc.Command.PREVIOUS_TRACK,
            mc.Command.PLAY,
            mc.Command.NEXT_TRACK,
          ],
          notification: { color: C.priDim, showWhenClosed: true },
        });
      } catch {
        return; // couldn't enable — leave the bridge inert
      }
      if (cancelled) return;
      enabledRef.current = true;

      // Subscribe to the NATIVE event, not the library's addListener(): that
      // one pushes the callback into an array nothing ever reads, so the
      // notification buttons silently do nothing. The native module does emit
      // "mediaControlEvent" correctly.
      try {
        subscription = mc.native.addListener("mediaControlEvent", (event: any) => {
          switch (event?.command) {
            case mc.Command.PLAY: runTransport("play"); break;
            case mc.Command.PAUSE: runTransport("pause"); break;
            case mc.Command.NEXT_TRACK: runTransport("next"); break;
            case mc.Command.PREVIOUS_TRACK: runTransport("previous"); break;
            case mc.Command.SEEK: {
              const d = event?.data;
              const seconds = typeof d === "number" ? d : (d?.position ?? d?.seconds);
              if (typeof seconds === "number" && isFinite(seconds)) {
                runTransport("seek", Math.max(0, Math.round(seconds)));
              }
              break;
            }
            default: break;
          }
        });
      } catch {
        // no events — the notification still shows, just without controls
      }

      unsubscribe = subscribeNowPlaying(push);
      push(getNowPlaying());

      // While the PC is the output, keep polling it here: the Música screen
      // owns that state but may not be mounted yet.
      const pollDesktop = async () => {
        if (getOutputMode() !== "pc") return;
        try {
          const s = await api.getStatus();
          const m = s.music;
          setNowPlaying({
            source: "pc",
            trackKey: m.videoId || m.title || "",
            title: m.title,
            artists: m.artists,
            thumbnail: m.thumbnail,
            coverColor: m.coverColor,
            duration: m.duration,
            position: m.position,
            playing: m.playing,
          });
        } catch {
          // transient (desktop asleep, Wi-Fi blip) — next tick retries
        }
      };
      pollDesktop();
      timer = setInterval(pollDesktop, 3000);
    })();

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      try { subscription?.remove(); } catch { /* already gone */ }
      unsubscribe?.();
      if (enabledRef.current) {
        enabledRef.current = false;
        mc.disableMediaControls().catch(() => {});
      }
    };
  }, []);

  return null;
}
