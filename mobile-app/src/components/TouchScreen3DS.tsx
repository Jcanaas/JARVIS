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
 * Prefetch-then-swap, not double-buffered opacity layers (an earlier
 * version of this tried that and still flickered black in practice):
 * Image.prefetch() downloads and decodes the next frame into RN's image
 * cache *before* it ever becomes a visible <Image>'s source, so the actual
 * swap is a cache hit with nothing to decode — no layer is ever showing a
 * source mid-load. The poll loop also only re-arms itself after the
 * current fetch settles (a self-paced setTimeout, not setInterval), so a
 * slow LAN request can't pile up requests faster than they resolve — the
 * likely reason two layers alone didn't fix it: firing a new source into
 * the "hidden" layer every fixed 100ms regardless of whether its previous
 * load had finished just cancels and restarts that layer's fetch forever
 * on a network slower than 100ms round-trip, so it never has a frame to
 * swap to.
 */
export default function TouchScreen3DS({ active = true }: { active?: boolean }) {
  const [uri, setUri] = useState(() => gamepadScreenUrl(Date.now()));
  const rectRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const rootRef = useRef<View | null>(null);
  const touching = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      const next = gamepadScreenUrl(Date.now());
      try {
        await Image.prefetch(next);
      } catch {
        // Network hiccup or no game running right now — keep showing the
        // last good frame and just try again next tick.
      }
      if (cancelled) return;
      setUri(next);
      timer = setTimeout(poll, POLL_MS);
    };
    timer = setTimeout(poll, POLL_MS);

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
      <Image source={{ uri }} style={styles.image} resizeMode="contain" />
    </View>
  );
}

const styles = StyleSheet.create({
  frame: {
    width: "100%", maxWidth: 320, aspectRatio: SCREEN_ASPECT,
    backgroundColor: "#000", borderRadius: radius.md,
    borderWidth: 1, borderColor: C.border, overflow: "hidden",
  },
  image: { width: "100%", height: "100%" },
  frameInactive: { opacity: 0.55 },
});
