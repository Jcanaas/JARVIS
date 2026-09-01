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
const POLL_MS = 100; // ~10fps — plenty for a screen only tapped, not watched

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
 * Double-buffered: swapping a single <Image>'s source.uri makes RN clear it
 * to blank while the new one decodes, which at 10 polls/sec read as a
 * constant black flicker. Two stacked Image layers fix it — the next frame
 * loads into the *hidden* layer, and only once it has fully decoded
 * (onLoad) do the layers swap, so the visible one is always a complete
 * frame, never a gap.
 */
export default function TouchScreen3DS({ active = true }: { active?: boolean }) {
  const initialUri = gamepadScreenUrl(Date.now());
  const [uris, setUris] = useState<[string, string]>([initialUri, initialUri]);
  const [visible, setVisible] = useState<0 | 1>(0);
  const nextLayer = useRef<0 | 1>(1);
  const rectRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);
  const rootRef = useRef<View | null>(null);
  const touching = useRef(false);

  useEffect(() => {
    const t = setInterval(() => {
      const layer = nextLayer.current;
      setUris((prev) => {
        const next: [string, string] = [...prev];
        next[layer] = gamepadScreenUrl(Date.now());
        return next;
      });
    }, POLL_MS);
    return () => clearInterval(t);
  }, []);

  const onLayerLoad = useCallback((layer: 0 | 1) => {
    setVisible(layer);
    nextLayer.current = layer === 0 ? 1 : 0;
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
        source={{ uri: uris[0] }}
        style={[styles.image, { opacity: visible === 0 ? 1 : 0 }]}
        resizeMode="contain"
        onLoad={() => onLayerLoad(0)}
      />
      <Image
        source={{ uri: uris[1] }}
        style={[styles.image, styles.overlay, { opacity: visible === 1 ? 1 : 0 }]}
        resizeMode="contain"
        onLoad={() => onLayerLoad(1)}
      />
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
  overlay: { position: "absolute", top: 0, left: 0 },
  frameInactive: { opacity: 0.55 },
});
