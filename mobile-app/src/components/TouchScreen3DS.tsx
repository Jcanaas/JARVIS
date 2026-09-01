import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Image, StyleSheet } from "react-native";
import { C, radius } from "../theme";
import { api, gamepadScreenUrl } from "../api";

/** Matches actions/emulator_frame.py's crop_bottom_screen_3ds() — the crop
 * is 320x240 out of a 400x480 combined frame, always 4:3. Setting the same
 * ratio here means resizeMode="contain" never letterboxes, so a touch's
 * fraction across the rendered <Image> is the same fraction across the
 * real cropped screen with no extra math. */
const SCREEN_ASPECT = 4 / 3;
const POLL_MS = 100; // ~10fps target — plenty for a screen only tapped, not watched

const BASE64_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** Hand-rolled base64 — deliberately not Blob/FileReader.readAsDataURL
 * (tried, and threw a runtime error on-device asking to add a package:
 * RN's Blob support isn't uniformly there without one). fetch()'s
 * arrayBuffer() has none of that baggage, and encoding it ourselves needs
 * nothing beyond the JS that already ships. */
function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const chunks: string[] = [];
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    chunks.push(
      BASE64_CHARS[(n >> 18) & 63], BASE64_CHARS[(n >> 12) & 63],
      BASE64_CHARS[(n >> 6) & 63], BASE64_CHARS[n & 63],
    );
  }
  const remaining = bytes.length - i;
  if (remaining === 1) {
    const n = bytes[i] << 16;
    chunks.push(BASE64_CHARS[(n >> 18) & 63], BASE64_CHARS[(n >> 12) & 63], "==");
  } else if (remaining === 2) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8);
    chunks.push(BASE64_CHARS[(n >> 18) & 63], BASE64_CHARS[(n >> 12) & 63], BASE64_CHARS[(n >> 6) & 63], "=");
  }
  return chunks.join("");
}

/**
 * Mirrors the 3DS bottom (touch) screen on the phone and forwards taps/
 * drags on it as real touch input to the emulator core.
 *
 * Polls actions/lan_dashboard.py's /api/gamepad/screen for a fresh JPEG
 * snapshot instead of opening one long MJPEG connection — React Native's
 * Image component doesn't reliably decode multipart/x-mixed-replace the
 * way a browser <img> does, and polling a still image is a well-worn,
 * reliable substitute at the ~10fps this needs.
 *
 * Fetches the bytes itself and swaps in a data: URI, rather than handing
 * <Image> a network URL each tick — three things were tried here: a plain
 * setInterval overwriting a hidden layer's source.uri (flickered);
 * Image.prefetch() before swapping a single <Image>'s uri (still
 * flickered — prefetch's cache isn't guaranteed to be the same cache
 * <Image> reads from); fetch().blob() + FileReader.readAsDataURL (threw a
 * runtime error on-device — RN's Blob support needs an extra package this
 * project doesn't have). fetch().arrayBuffer() needs none of that: it's
 * the same Fetch API primitive already used everywhere else in this app,
 * and encoding it to base64 ourselves (below) has no external dependency
 * at all. By the time setUri runs the full JPEG is already sitting in JS
 * memory as a string, so there's nothing left to load underneath whatever
 * is on screen. The poll loop is a self-paced setTimeout chain (not
 * setInterval), so a slow LAN response can't pile up requests faster than
 * they resolve.
 */
export default function TouchScreen3DS({ active = true }: { active?: boolean }) {
  const [uri, setUri] = useState<string | null>(null);
  const rectRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const rootRef = useRef<View | null>(null);
  const touching = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const res = await fetch(gamepadScreenUrl(Date.now()));
        if (res.ok) {
          const buffer = await res.arrayBuffer();
          const dataUri = `data:image/jpeg;base64,${arrayBufferToBase64(buffer)}`;
          if (!cancelled) setUri(dataUri);
        }
      } catch {
        // Network hiccup or no game running right now — keep showing the
        // last good frame and just try again next tick.
      }
      if (!cancelled) timer = setTimeout(poll, POLL_MS);
    };
    timer = setTimeout(poll, 0);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  const measure = useCallback(() => {
    rootRef.current?.measureInWindow((x, y, w, h) => { rectRef.current = { x, y, w, h }; });
  }, []);

  const sendTouch = useCallback((pageX: number, pageY: number, pressed: boolean) => {
    const rect = rectRef.current;
    if (!rect || rect.w <= 0 || rect.h <= 0) return;
    const x = Math.max(0, Math.min(1, (pageX - rect.x) / rect.w));
    const y = Math.max(0, Math.min(1, (pageY - rect.y) / rect.h));
    api.sendGamepadTouch({ x, y, pressed }).catch(() => {});
  }, []);

  const onGrant = useCallback((e: any) => {
    touching.current = true;
    sendTouch(e.nativeEvent.pageX, e.nativeEvent.pageY, true);
  }, [sendTouch]);

  const onMove = useCallback((e: any) => {
    if (!touching.current) return;
    sendTouch(e.nativeEvent.pageX, e.nativeEvent.pageY, true);
  }, [sendTouch]);

  const onRelease = useCallback((e: any) => {
    if (!touching.current) return;
    touching.current = false;
    sendTouch(e.nativeEvent.pageX, e.nativeEvent.pageY, false);
  }, [sendTouch]);

  useEffect(() => () => {
    // Never leave a stray touch held on the core if this unmounts mid-press.
    if (touching.current) api.sendGamepadTouch({ x: 0, y: 0, pressed: false }).catch(() => {});
  }, []);

  return (
    <View
      ref={rootRef}
      onLayout={measure}
      style={[styles.frame, !active && styles.frameInactive]}
      // While a parent Draggable is repositioning this whole screen (edit
      // mode), this view must not claim the responder — RN grants it to the
      // deepest view that returns true here, which would otherwise always
      // beat the Draggable wrapper and make it undraggable.
      onStartShouldSetResponder={() => active}
      onMoveShouldSetResponder={() => active}
      onResponderGrant={onGrant}
      onResponderMove={onMove}
      onResponderRelease={onRelease}
      onResponderTerminate={onRelease}
    >
      {!!uri && <Image source={{ uri }} style={styles.image} resizeMode="contain" />}
    </View>
  );
}

const styles = StyleSheet.create({
  // A fixed base width rather than "100%": this component gets wrapped in
  // GamepadOverlay.tsx's Draggable with no explicit size of its own, so a
  // percentage width here would need that wrapper to already have a
  // resolved width — a percentage inside an auto-sized wrapper is exactly
  // the kind of circular sizing Yoga can get wrong, and it's also what the
  // Draggable resize handle anchors its corner position against. A fixed
  // number sidesteps both.
  frame: {
    width: 260, aspectRatio: SCREEN_ASPECT,
    backgroundColor: "#000", borderRadius: radius.md,
    borderWidth: 1, borderColor: C.border, overflow: "hidden",
  },
  image: { width: "100%", height: "100%" },
  frameInactive: { opacity: 0.55 },
});
