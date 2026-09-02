import React, { useCallback, useEffect, useRef } from "react";
import { View, Text, StyleSheet } from "react-native";
import { C } from "../theme";

/** libretro analog range. Values outside it are clamped by the core anyway. */
export const AXIS_MAX = 32767;
export const STICK_SIZE = 118;

/** How far the knob may travel from the centre, for a base of `size` px. */
export function stickTravel(size: number = STICK_SIZE): number {
  return size / 2 - (size * 0.42) / 2;
}

export type StickSide = "left" | "right";
export type Offset = { x: number; y: number };
export type Rect = { x: number; y: number; w: number; h: number };

/**
 * Thumbstick — presentation only.
 *
 * It deliberately owns no gesture handling. React Native grants the touch
 * responder to a single view at a time, so a button taking the responder
 * mid-drag terminated the stick's PanResponder and froze it until the finger
 * was lifted — you could not hold a direction and press a button, which is
 * most of playing anything. The whole pad is one responder instead (see
 * GamepadOverlay), which routes every active touch to the control under it;
 * this component just reports its rect and draws where the knob sits.
 */
export default function AnalogStick({
  id,
  size = STICK_SIZE,
  label,
  offset,
  pressed,
  onMeasure,
  remeasureTick,
}: {
  /** Control id the pad uses to address this stick, e.g. "stick:left". */
  id: string;
  size?: number;
  label?: string;
  offset: Offset;
  /** L3/R3 highlight — the pad decides, this only renders it. */
  pressed?: boolean;
  onMeasure: (id: string, rect: Rect) => void;
  /** Bumped periodically by the parent to force a fresh measureInWindow()
   * even when onLayout hasn't refired — see its own comment in
   * GamepadOverlay.tsx for why that matters (a sibling appearing/resizing
   * shifts this control's *absolute* window position without changing its
   * layout frame *relative to its own parent*, which is the only thing
   * onLayout watches; a stale absolute measurement from before that shift
   * would otherwise never get corrected). */
  remeasureTick?: number;
}) {
  const radius = size / 2;
  const knob = size * 0.42;
  const baseRef = useRef<View | null>(null);

  const measure = useCallback(() => {
    baseRef.current?.measureInWindow((x, y, w, h) => onMeasure(id, { x, y, w, h }));
  }, [id, onMeasure]);

  useEffect(() => { measure(); }, [remeasureTick, measure]);

  return (
    <View style={{ alignItems: "center", gap: 4 }}>
      <View
        ref={baseRef}
        onLayout={measure}
        style={[
          styles.base,
          { width: size, height: size, borderRadius: radius },
          pressed && styles.basePressed,
        ]}
      >
        <View
          pointerEvents="none"
          style={[
            styles.knob,
            {
              width: knob, height: knob, borderRadius: knob / 2,
              transform: [{ translateX: offset.x }, { translateY: offset.y }],
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
