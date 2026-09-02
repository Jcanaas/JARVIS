import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet } from "react-native";
import { Image } from "expo-image";
import { C, radius } from "../theme";
import { api, gamepadScreenUrl } from "../api";

/** Matches actions/emulator_frame.py's crop_bottom_screen_3ds() — the crop
 * is 320x240 out of a 400x480 combined frame, always 4:3. Setting the same
 * ratio here means contentFit="contain" never letterboxes, so a touch's
 * fraction across the rendered image is the same fraction across the real
 * cropped screen with no extra math. */
const SCREEN_ASPECT = 4 / 3;
// The 3DS's own bottom screen updates at up to 30fps like the top one, and
// a real 3DS session measured server round-trips at ~5ms (checked directly
// against actions/lan_dashboard.py's /api/gamepad/screen, 40 requests back
// to back) — plenty of headroom to match that instead of visibly lagging
// behind the top screen shown elsewhere (the desktop web panel, or a
// second phone glance at the game).
const POLL_MS = 33; // ~30fps

/**
 * Mirrors the 3DS bottom (touch) screen on the phone and forwards taps/
 * drags on it as real touch input to the emulator core.
 *
 * Polls actions/lan_dashboard.py's /api/gamepad/screen for a fresh JPEG
 * snapshot instead of opening one long MJPEG connection — nothing here
 * reliably decodes multipart/x-mixed-replace the way a browser <img> does,
 * and polling a still image is a well-worn, reliable substitute at the
 * ~30fps this needs. Verified server-side separately: 40 consecutive
 * requests all returned valid, consistent frames in ~5ms each — the
 * flicker seen on-device was never a backend problem, and there's plenty
 * of headroom in that response time for a much higher poll rate than the
 * original conservative 10fps.
 *
 * Uses expo-image, not React Native's core Image. Three things were tried
 * on core Image first, all still flickered on-device: a plain setInterval
 * overwriting a hidden layer's source.uri; Image.prefetch() before
 * swapping a single Image's uri; fetching the bytes into a data: URI
 * ourselves (which additionally needed dropping Blob/FileReader — RN's
 * Blob support isn't uniformly there — in favour of a hand-rolled base64
 * encoder over arrayBuffer()). None of it mattered: React Native's core
 * Image on Android is documented to reset its native view on any
 * `source` change regardless of whether the new bytes are already
 * in memory or cached — the flicker was never about the network or the
 * swap mechanism, it was that component. expo-image is built specifically
 * to not do that (it keeps the previous frame on screen until the next one
 * is actually ready), so this goes back to the simplest version — poll a
 * plain network URL — now that the component underneath is the right one.
 */
export default function TouchScreen3DS({ active = true }: { active?: boolean }) {
  const [uri, setUri] = useState(() => gamepadScreenUrl(Date.now()));
  const rectRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const rootRef = useRef<View | null>(null);
  const touching = useRef(false);
  // TEMPORARY — the user reports an empty black box where the mirror should
  // be (space reserved, image never shows). expo-image fails silently on a
  // bad response, so there was no way to tell a 404/403/network error apart
  // from "still loading" from on-device. Safe to remove once the real cause
  // is confirmed. See GamepadOverlay.tsx's own (also temporary) debug
  // overlay for the same pattern used earlier to diagnose the touch-offset
  // issue.
  const [status, setStatus] = useState("loading…");
  const loadedOnce = useRef(false);

  // Self-paced, not a fixed setInterval: a plain interval fires a *new*
  // request every POLL_MS regardless of whether the previous one has
  // finished. That's fine at the ~5ms round-trips measured against an idle
  // core, but during real gameplay the core's own render thread holds the
  // GIL/frame lock far more, and a slower response time than the 33ms
  // interval means requests pile up faster than they drain — a growing
  // backlog that (per user report) never resolves, image or error, because
  // nothing is actually stuck, there's just an ever-growing queue in front
  // of every request. Only ever having one in flight — the next one fires
  // POLL_MS after the *previous one finishes*, from onLoad/onError below —
  // can't pile up, and the mirror rate quietly adapts to however fast the
  // backend can actually go instead of demanding a fixed rate.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // TEMPORARY — paired with the round-trip time shown in the debug overlay
  // below and actions/lan_dashboard.py's own server-side timing breakdown
  // (printed to its console, throttled to ~1/sec): comparing the two tells
  // us how much of the per-request time is genuinely network vs. backend
  // work, instead of guessing at payload size again.
  const requestStartedAt = useRef(0);
  const scheduleNext = useCallback((delay: number) => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      requestStartedAt.current = Date.now();
      setUri(gamepadScreenUrl(Date.now()));
    }, delay);
  }, []);

  useEffect(() => {
    scheduleNext(0);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [scheduleNext]);

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
      <Image
        source={{ uri }}
        style={styles.image}
        contentFit="contain"
        cachePolicy="none"
        transition={0}
        onLoad={() => {
          loadedOnce.current = true;
          setStatus(`ok ${Date.now() - requestStartedAt.current}ms`);
          scheduleNext(POLL_MS);
        }}
        onError={(e: any) => {
          setStatus(`error ${Date.now() - requestStartedAt.current}ms: ${e?.error || "unknown"}`);
          scheduleNext(POLL_MS);
        }}
      />
      {/* TEMPORARY — see the status state's own comment above. */}
      <Text style={styles.debugText}>{status}</Text>
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
  debugText: {
    position: "absolute", top: 4, left: 4, right: 4,
    color: "#ff5c5c", fontSize: 9, fontFamily: "monospace",
  },
});
