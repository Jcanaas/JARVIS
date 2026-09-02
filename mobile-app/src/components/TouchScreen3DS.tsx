import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet } from "react-native";
import { Image } from "expo-image";
import { C, radius } from "../theme";
import { api, gamepadScreenWsUrl } from "../api";

/** Matches actions/emulator_frame.py's crop_bottom_screen_3ds() — the crop
 * is 320x240 out of a 400x480 combined frame, always 4:3. Setting the same
 * ratio here means contentFit="contain" never letterboxes, so a touch's
 * fraction across the rendered image is the same fraction across the real
 * cropped screen with no extra math. */
const SCREEN_ASPECT = 4 / 3;

/**
 * Mirrors the 3DS bottom (touch) screen on the phone and forwards taps/
 * drags on it as real touch input to the emulator core.
 *
 * Reads actions/lan_dashboard.py's persistent WebSocket stream
 * (_gamepad_screen_ws_serve) instead of polling /api/gamepad/screen over
 * HTTP. The polling version was tried first and worked, but real device
 * timing data showed the backend itself responds in ~2-3ms even during
 * real gameplay — a fresh HTTP request per frame (headers, a new response
 * object, a new Werkzeug request thread) was still the actual bottleneck,
 * something no amount of shrinking the JPEG payload could fix. A single
 * long-lived connection pays that per-request overhead once instead of
 * once per frame.
 *
 * Frames arrive as base64 *text* messages, not binary — handed straight to
 * expo-image as a `data:image/jpeg;base64,...` URI. Deliberately not
 * ArrayBuffer/Blob: this exact codebase already hit a real on-device
 * runtime error going that route for this same component (see the
 * now-removed "fetching the bytes into a data: URI ourselves" attempt in
 * this file's git history) — a text message needs none of that.
 *
 * Uses expo-image, not React Native's core Image, for the same reason as
 * before: core Image on Android resets its native view on any `source`
 * change (documented behaviour), which caused a visible flicker on every
 * single frame; expo-image keeps the previous frame on screen until the
 * next one is actually ready.
 */
export default function TouchScreen3DS({
  active = true,
  wsPort,
}: {
  active?: boolean;
  /** From api.getGamepadStatus()'s ws_port — null until the first status
   * poll lands, or if the WebSocket server somehow never started. */
  wsPort: number | null;
}) {
  const [uri, setUri] = useState<string | null>(null);
  const rectRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const rootRef = useRef<View | null>(null);
  const touching = useRef(false);
  // TEMPORARY — kept from the polling version's diagnostics while this
  // streaming approach gets its first real-device verification. Shows
  // connection state and, once frames are arriving, the inter-frame
  // interval (how long since the previous frame — the streaming
  // equivalent of the old per-request round trip). Safe to remove once
  // confirmed working.
  const [status, setStatus] = useState("connecting…");
  const lastFrameAt = useRef(0);

  useEffect(() => {
    if (wsPort == null) {
      setStatus("no ws_port yet");
      return;
    }
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let ws: WebSocket | null = null;

    const connect = () => {
      if (cancelled) return;
      setStatus("connecting…");
      ws = new WebSocket(gamepadScreenWsUrl(wsPort));
      ws.onopen = () => setStatus("connected");
      ws.onmessage = (e) => {
        const now = Date.now();
        const dt = lastFrameAt.current ? now - lastFrameAt.current : 0;
        lastFrameAt.current = now;
        setUri(`data:image/jpeg;base64,${e.data}`);
        setStatus(dt ? `ok ${dt}ms` : "ok");
      };
      ws.onerror = () => setStatus("ws error");
      // Fires on both a clean close and a failed connection attempt —
      // either way, the mirror should keep trying rather than going dark
      // for the rest of the session over one dropped connection (Wi-Fi
      // hiccup, the desktop briefly unreachable, ...).
      ws.onclose = () => {
        if (cancelled) return;
        setStatus("disconnected, retrying…");
        retryTimer = setTimeout(connect, 1000);
      };
    };
    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      ws?.close();
    };
  }, [wsPort]);

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
      {!!uri && (
        <Image
          source={{ uri }}
          style={styles.image}
          contentFit="contain"
          cachePolicy="none"
          transition={0}
          onError={(e: any) => setStatus(`decode error: ${e?.error || "unknown"}`)}
        />
      )}
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
