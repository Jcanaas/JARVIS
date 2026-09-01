import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, StyleSheet } from "react-native";
import { Image } from "expo-image";
import { C, radius } from "../theme";
import { api, gamepadScreenUrl } from "../api";

/** Matches actions/emulator_frame.py's crop_bottom_screen_3ds() — the crop
 * is 320x240 out of a 400x480 combined frame, always 4:3. Setting the same
 * ratio here means contentFit="contain" never letterboxes, so a touch's
 * fraction across the rendered image is the same fraction across the real
 * cropped screen with no extra math. */
const SCREEN_ASPECT = 4 / 3;
const POLL_MS = 100; // ~10fps target — plenty for a screen only tapped, not watched

/**
 * Mirrors the 3DS bottom (touch) screen on the phone and forwards taps/
 * drags on it as real touch input to the emulator core.
 *
 * Polls actions/lan_dashboard.py's /api/gamepad/screen for a fresh JPEG
 * snapshot instead of opening one long MJPEG connection — nothing here
 * reliably decodes multipart/x-mixed-replace the way a browser <img> does,
 * and polling a still image is a well-worn, reliable substitute at the
 * ~10fps this needs. Verified server-side separately: 40 consecutive
 * requests all returned valid, consistent frames in ~5ms each — the
 * flicker seen on-device was never a backend problem.
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

  useEffect(() => {
    const t = setInterval(() => setUri(gamepadScreenUrl(Date.now())), POLL_MS);
    return () => clearInterval(t);
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
      <Image
        source={{ uri }}
        style={styles.image}
        contentFit="contain"
        cachePolicy="none"
        transition={0}
      />
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
