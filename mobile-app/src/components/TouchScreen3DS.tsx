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
 * Fetches the bytes itself and swaps in a data: URI, rather than handing
 * <Image> a network URL each tick (tried first: setInterval overwriting a
 * hidden layer's source.uri — flickered; tried second: Image.prefetch()
 * before swapping a single <Image>'s uri — still flickered, because
 * prefetch's cache isn't guaranteed to be the same cache <Image> reads from
 * across RN/Fresco versions, so the "prefetched" swap was still doing a
 * real network fetch behind <Image> either way). A data: URI has no network
 * layer left to race against — by the time setUri runs, the full JPEG is
 * already sitting in JS memory, so there's nothing left to load underneath
 * whatever is on screen. The poll loop is a self-paced setTimeout chain
 * (not setInterval), so a slow LAN response can't pile up requests faster
 * than they resolve.
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
          const blob = await res.blob();
          const dataUri = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onerror = () => reject(reader.error || new Error("read failed"));
            reader.onload = () => resolve(String(reader.result));
            reader.readAsDataURL(blob);
          });
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
