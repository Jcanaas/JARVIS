import React, { useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, PanResponder, GestureResponderEvent, PanResponderGestureState } from "react-native";
import { C } from "../theme";

/** libretro analog range. Values outside it are clamped by the core anyway. */
const AXIS_MAX = 32767;
const SEND_INTERVAL_MS = 50;

export type StickSide = "left" | "right";

/**
 * Touch thumbstick that reports libretro analog axes.
 *
 * Sends at most one update every ~50ms while dragging — one request per touch
 * frame would swamp the LAN link and add latency to the very input it is
 * meant to make responsive — but the release always goes out immediately and
 * unthrottled, because a stick stuck off-centre keeps the character walking.
 */
export default function AnalogStick({
  side,
  size = 118,
  label,
  onAxes,
  onPress,
  pressed,
}: {
  side: StickSide;
  size?: number;
  label?: string;
  onAxes: (index: number, x: number, y: number) => void;
  /** L3/R3 — pressing the stick in. Omitted when the console has no such button. */
  onPress?: (pressed: boolean) => void;
  pressed?: boolean;
}) {
  const index = side === "left" ? 0 : 1;
  const radius = size / 2;
  const knob = size * 0.42;
  const travel = radius - knob / 2;

  const [pos, setPos] = useState({ x: 0, y: 0 });
  const lastSent = useRef(0);
  const movedRef = useRef(false);

  const emit = (dx: number, dy: number, force: boolean) => {
    const now = Date.now();
    if (!force && now - lastSent.current < SEND_INTERVAL_MS) return;
    lastSent.current = now;
    const nx = Math.max(-1, Math.min(1, dx / travel));
    const ny = Math.max(-1, Math.min(1, dy / travel));
    onAxes(index, Math.round(nx * AXIS_MAX), Math.round(ny * AXIS_MAX));
  };

  const responder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderGrant: () => {
          movedRef.current = false;
          onPress?.(true);
        },
        onPanResponderMove: (_e: GestureResponderEvent, g: PanResponderGestureState) => {
          movedRef.current = true;
          // Clamp to the ring so the knob can't be dragged out of the base.
          const dist = Math.hypot(g.dx, g.dy);
          const scale = dist > travel ? travel / dist : 1;
          const x = g.dx * scale;
          const y = g.dy * scale;
          setPos({ x, y });
          emit(x, y, false);
        },
        onPanResponderRelease: () => {
          setPos({ x: 0, y: 0 });
          emit(0, 0, true); // centre immediately — never leave the stick held
          onPress?.(false);
        },
        onPanResponderTerminate: () => {
          setPos({ x: 0, y: 0 });
          emit(0, 0, true);
          onPress?.(false);
        },
      }),
    [travel, index, onAxes, onPress],
  );

  return (
    <View style={{ alignItems: "center", gap: 4 }}>
      <View
        style={[styles.base, { width: size, height: size, borderRadius: radius }, pressed && styles.basePressed]}
        {...responder.panHandlers}
      >
        <View
          pointerEvents="none"
          style={[
            styles.knob,
            {
              width: knob, height: knob, borderRadius: knob / 2,
              transform: [{ translateX: pos.x }, { translateY: pos.y }],
            },
          ]}
        />
      </View>
      {!!label && <Text style={styles.label}>{label}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    backgroundColor: C.panel2, borderWidth: 1, borderColor: C.border,
    alignItems: "center", justifyContent: "center",
  },
  basePressed: { borderColor: C.pri },
  knob: { backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.55)" },
  label: { color: C.textMed, fontSize: 9.5, fontWeight: "800", letterSpacing: 0.5 },
});
