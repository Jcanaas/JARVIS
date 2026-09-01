import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, TouchableOpacity, Modal, Platform } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import { useKeepAwake } from "expo-keep-awake";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { C, radius, spacing } from "../theme";
import { api, PadLayout } from "../api";
import {
  configureForegroundDisplay, ensureNotificationPermission, ensureChannel,
  presentNotification, dismissNotification, onNotificationTapped,
} from "../notifications";
import AnalogStick, {
  AXIS_MAX, STICK_SIZE, stickTravel,
  type Offset, type Rect, type StickSide,
} from "./AnalogStick";

const DIGITAL_LAYOUT: PadLayout = {
  sticks: 0, triggers: false, stick_buttons: false, shoulders: false, face: "nintendo",
};

const CHANNEL_ID = "jarvis-gamepad";

/**
 * Turns the phone into a controller for the game running on the PC.
 *
 * Not a tab on purpose: a gamepad is only meaningful while a game is actually
 * running, so the app watches for that and offers itself — as a dialog in the
 * app and as a sticky Android notification, so it is reachable from outside
 * the app too. The desktop's "Mando móvil" button can re-raise both.
 */
export default function GamepadOverlay() {
  const [available, setAvailable] = useState(false);
  const [prompt, setPrompt] = useState(false);
  const [open, setOpen] = useState(false);
  const [consoleId, setConsoleId] = useState("");
  const [layout, setLayout] = useState<PadLayout>(DIGITAL_LAYOUT);
  const dismissedRef = useRef(false);
  const announceRef = useRef<number | null>(null);
  const notifIdRef = useRef<string | null>(null);

  const clearNotification = useCallback(async () => {
    const id = notifIdRef.current;
    notifIdRef.current = null;
    await dismissNotification(id);
  }, []);

  const showNotification = useCallback(async () => {
    if (notifIdRef.current) return; // already up; don't stack duplicates
    if (!(await ensureNotificationPermission())) return;
    if (Platform.OS === "android") await ensureChannel(CHANNEL_ID, "Mando de juegos");
    notifIdRef.current = await presentNotification({
      title: "JARVIS · hay un juego en marcha",
      body: "Toca para usar el móvil como mando.",
      // Survives a swipe: it is a standing offer for as long as the game is
      // running, not a one-off alert.
      sticky: true,
      autoDismiss: false,
      data: { kind: "gamepad" },
      ...(Platform.OS === "android" ? { channelId: CHANNEL_ID } : {}),
    });
  }, []);

  // Tapping the notification brings the app up; open the pad straight away.
  useEffect(() => {
    configureForegroundDisplay();
    return onNotificationTapped((data) => {
      if (data?.kind === "gamepad") {
        setPrompt(false);
        setOpen(true);
      }
    });
  }, []);

  const poll = useCallback(async () => {
    try {
      const { active, console: cid, announce, layout: padLayout } = await api.getGamepadStatus();
      setAvailable(active);
      setConsoleId(cid || "");
      setLayout(padLayout ? { ...DIGITAL_LAYOUT, ...padLayout } : DIGITAL_LAYOUT);

      // The desktop asked for the prompt again: honour it even if the user
      // dismissed the last one.
      if (typeof announce === "number") {
        if (announceRef.current !== null && announce !== announceRef.current) {
          dismissedRef.current = false;
          if (active) setPrompt(true);
        }
        announceRef.current = announce;
      }

      if (active) {
        if (!dismissedRef.current) setPrompt(true);
        showNotification();
      } else {
        // The game ended: drop the pad and the notification rather than
        // leaving a dead controller and a stale notification behind.
        setPrompt(false);
        setOpen(false);
        dismissedRef.current = false;
        clearNotification();
      }
    } catch {
      setAvailable(false);
    }
  }, [showNotification, clearNotification]);

  useEffect(() => {
    poll();
    const t = setInterval(poll, 5000);
    return () => clearInterval(t);
  }, [poll]);

  useEffect(() => () => { clearNotification(); }, [clearNotification]);

  if (open) {
    return <GamepadPad consoleId={consoleId} layout={layout} onClose={() => setOpen(false)} />;
  }

  return (
    <Modal
      visible={prompt && available}
      transparent
      animationType="fade"
      onRequestClose={() => { dismissedRef.current = true; setPrompt(false); }}
    >
      <View style={styles.backdrop}>
        <View style={styles.dialog}>
          <View style={styles.iconCircle}>
            <Ionicons name="game-controller" size={34} color={C.pri} />
          </View>
          <Text style={styles.dialogTitle}>Hay un juego en marcha</Text>
          <Text style={styles.dialogBody}>
            Puedes usar el móvil como mando{consoleId ? ` para ${consoleId.toUpperCase()}` : ""}.
            Se abrirá en horizontal y la pantalla no se apagará mientras juegues.
          </Text>
          <TouchableOpacity
            style={styles.primaryBtn}
            onPress={() => { setPrompt(false); setOpen(true); }}
            activeOpacity={0.85}
          >
            <Ionicons name="game-controller" size={17} color="#08101F" />
            <Text style={styles.primaryText}>Usar como mando</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={styles.secondaryBtn}
            onPress={() => { dismissedRef.current = true; setPrompt(false); }}
          >
            <Text style={styles.secondaryText}>Ahora no</Text>
          </TouchableOpacity>
          <Text style={styles.footnote}>
            Puedes volver a abrirlo desde la notificación, o con «Mando móvil» en el PC.
          </Text>
        </View>
      </View>
    </Modal>
  );
}

/** Face-button captions. libretro's retropad is laid out like a SNES pad, so
 * a PlayStation core still receives b/a/y/x — only the glyphs differ. */
const FACE_LABELS: Record<string, { up: string; left: string; right: string; down: string }> = {
  nintendo:    { up: "X", left: "Y", right: "A", down: "B" },
  playstation: { up: "△", left: "□", right: "○", down: "✕" },
};

/** Throttle for analog updates while dragging; centring is always immediate. */
const AXIS_SEND_MS = 50;

function GamepadPad({
  consoleId, layout, onClose,
}: { consoleId: string; layout: PadLayout; onClose: () => void }) {
  useKeepAwake(); // a controller that lets the screen sleep is useless

  // Visual state only. What is actually sent lives in the refs below, so a
  // re-render can never lag or duplicate a press.
  const [down, setDown] = useState<Record<string, boolean>>({});
  const [offsets, setOffsets] = useState<Record<StickSide, Offset>>({
    left: { x: 0, y: 0 }, right: { x: 0, y: 0 },
  });

  const rootRef = useRef<View | null>(null);
  const rootOrigin = useRef<Offset>({ x: 0, y: 0 });
  const rects = useRef<Record<string, Rect>>({});
  /** touch identifier → control id, kept for the life of that finger so a
   *  stick keeps following it even when dragged outside its own circle. */
  const owners = useRef<Map<number, string>>(new Map());
  const sentButtons = useRef<Set<string>>(new Set());
  const sentAxes = useRef<Record<StickSide, Offset>>({
    left: { x: 0, y: 0 }, right: { x: 0, y: 0 },
  });
  const lastAxisSend = useRef<Record<StickSide, number>>({ left: 0, right: 0 });

  useEffect(() => {
    ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.LANDSCAPE).catch(() => {});
    return () => {
      ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP).catch(() => {});
      // Never leave a button held or a stick off-centre if the pad closes
      // mid-press: the core would keep receiving that input forever.
      api.sendGamepadInput({ clear: true }).catch(() => {});
    };
  }, []);

  const send = useCallback((name: string, pressed: boolean) => {
    api.sendGamepadInput({ buttons: [{ name, pressed }] }).catch(() => {});
  }, []);

  const sendAxes = useCallback((index: number, x: number, y: number) => {
    api.sendGamepadInput({
      axes: [
        { index, axis: 0, value: x },  // RETRO_DEVICE_ID_ANALOG_X
        { index, axis: 1, value: y },  // RETRO_DEVICE_ID_ANALOG_Y
      ],
    }).catch(() => {});
  }, []);

  const onMeasure = useCallback((id: string, rect: Rect) => {
    rects.current[id] = rect;
  }, []);

  const measureRoot = useCallback(() => {
    rootRef.current?.measureInWindow((x, y) => { rootOrigin.current = { x, y }; });
  }, []);

  /** Control under a touch. Touch coords are relative to the root view, the
   *  measured rects are relative to the window, hence the origin subtraction. */
  const hitTest = useCallback((px: number, py: number): string | null => {
    const { x: ox, y: oy } = rootOrigin.current;
    for (const [id, r] of Object.entries(rects.current)) {
      const x = r.x - ox;
      const y = r.y - oy;
      if (px >= x && px <= x + r.w && py >= y && py <= y + r.h) return id;
    }
    return null;
  }, []);

  const handleTouches = useCallback((evt: any, ended: boolean = false) => {
    const touches: any[] = evt?.nativeEvent?.touches || [];
    const active = new Map<number, Offset>();
    for (const t of touches) active.set(t.identifier, { x: t.pageX, y: t.pageY });
    // Android keeps the lifted pointer in `touches` on an up event while iOS
    // drops it. Take it out ourselves so a released button can't stay stuck.
    if (ended) {
      for (const t of (evt?.nativeEvent?.changedTouches || [])) active.delete(t.identifier);
    }

    // Fingers that lifted release whatever they were holding.
    for (const id of Array.from(owners.current.keys())) {
      if (!active.has(id)) owners.current.delete(id);
    }
    // New fingers claim whatever control they landed on.
    for (const [tid, pos] of active) {
      if (owners.current.has(tid)) continue;
      const control = hitTest(pos.x, pos.y);
      if (control) owners.current.set(tid, control);
    }

    const pressed = new Set<string>();
    const nextOffsets: Record<StickSide, Offset> = {
      left: { x: 0, y: 0 }, right: { x: 0, y: 0 },
    };
    const travel = stickTravel(STICK_SIZE);
    const { x: ox, y: oy } = rootOrigin.current;

    for (const [tid, control] of owners.current) {
      const pos = active.get(tid);
      if (!pos) continue;
      if (control.startsWith("btn:")) {
        pressed.add(control.slice(4));
      } else if (control.startsWith("stick:")) {
        const side = control.slice(6) as StickSide;
        const r = rects.current[control];
        if (!r) continue;
        let dx = pos.x - ((r.x - ox) + r.w / 2);
        let dy = pos.y - ((r.y - oy) + r.h / 2);
        const dist = Math.hypot(dx, dy);
        if (dist > travel) { dx *= travel / dist; dy *= travel / dist; }
        nextOffsets[side] = { x: dx, y: dy };
      }
    }

    // --- buttons: only the changes go out ---
    let buttonsChanged = false;
    for (const name of pressed) {
      if (!sentButtons.current.has(name)) { send(name, true); buttonsChanged = true; }
    }
    for (const name of sentButtons.current) {
      if (!pressed.has(name)) { send(name, false); buttonsChanged = true; }
    }
    if (buttonsChanged) {
      sentButtons.current = pressed;
      const map: Record<string, boolean> = {};
      for (const name of pressed) map[name] = true;
      setDown(map);
    }

    // --- sticks: throttled while moving, immediate on release ---
    const now = Date.now();
    const sides: StickSide[] = layout.sticks > 1 ? ["left", "right"] : ["left"];
    for (const side of sides) {
      const next = nextOffsets[side];
      const prev = sentAxes.current[side];
      if (next.x === prev.x && next.y === prev.y) continue;
      const centring = next.x === 0 && next.y === 0;
      if (!centring && now - lastAxisSend.current[side] < AXIS_SEND_MS) continue;
      lastAxisSend.current[side] = now;
      sentAxes.current[side] = next;
      const clamp = (v: number) => Math.max(-1, Math.min(1, v / travel));
      sendAxes(
        side === "left" ? 0 : 1,
        Math.round(clamp(next.x) * AXIS_MAX),
        Math.round(clamp(next.y) * AXIS_MAX),
      );
    }
    setOffsets(nextOffsets);
  }, [hitTest, layout.sticks, send, sendAxes]);

  const face = FACE_LABELS[layout.face] || FACE_LABELS.nintendo;

  return (
    <Modal visible transparent={false} animationType="fade" onRequestClose={onClose} supportedOrientations={["landscape"]}>
      <View
        ref={rootRef}
        onLayout={measureRoot}
        style={styles.pad}
        // One responder for the whole pad: this is what makes holding a
        // direction while tapping a button possible at all.
        onStartShouldSetResponder={() => true}
        onMoveShouldSetResponder={() => true}
        // Refuse to hand the responder over while fingers are on controls,
        // or a stray tap would freeze them. With nothing held we let go, so
        // the close button still works normally.
        onResponderTerminationRequest={() => owners.current.size === 0}
        onResponderGrant={handleTouches}
        onResponderMove={handleTouches}
        // Start/End fire for *every* pointer while we hold the responder,
        // including ones whose target is a different view. Without them a
        // crisp second-finger tap on a button — down and up between two move
        // events — would never be seen at all.
        onResponderStart={handleTouches}
        onResponderEnd={(e) => handleTouches(e, true)}
        onResponderRelease={(e) => handleTouches(e, true)}
        onResponderTerminate={(e) => handleTouches(e, true)}
      >
        <TouchableOpacity style={styles.closeBtn} onPress={onClose} hitSlop={12}>
          <Ionicons name="close" size={20} color={C.textMed} />
        </TouchableOpacity>
        {!!consoleId && <Text style={styles.padConsole}>{consoleId.toUpperCase()}</Text>}

        {(layout.shoulders || layout.triggers) && (
          <View style={styles.shoulderRow}>
            <View style={styles.shoulderGroup}>
              {layout.triggers && <PadButton id="btn:l2" label="L2" down={!!down.l2} onMeasure={onMeasure} style={styles.shoulder} />}
              {layout.shoulders && <PadButton id="btn:l" label="L1" down={!!down.l} onMeasure={onMeasure} style={styles.shoulder} />}
            </View>
            <View style={styles.shoulderGroup}>
              {layout.shoulders && <PadButton id="btn:r" label="R1" down={!!down.r} onMeasure={onMeasure} style={styles.shoulder} />}
              {layout.triggers && <PadButton id="btn:r2" label="R2" down={!!down.r2} onMeasure={onMeasure} style={styles.shoulder} />}
            </View>
          </View>
        )}

        <View style={styles.mainRow}>
          <View style={styles.dpad}>
            <PadButton id="btn:up" label="▲" down={!!down.up} onMeasure={onMeasure} style={[styles.dBtn, styles.dUp]} />
            <PadButton id="btn:left" label="◀" down={!!down.left} onMeasure={onMeasure} style={[styles.dBtn, styles.dLeft]} />
            <PadButton id="btn:right" label="▶" down={!!down.right} onMeasure={onMeasure} style={[styles.dBtn, styles.dRight]} />
            <PadButton id="btn:down" label="▼" down={!!down.down} onMeasure={onMeasure} style={[styles.dBtn, styles.dDown]} />
          </View>

          <View style={styles.centerCol}>
            <PadButton id="btn:select" label="SELECT" down={!!down.select} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} />
            <PadButton id="btn:start" label="START" down={!!down.start} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} />
          </View>

          <View style={styles.face}>
            <PadButton id="btn:x" label={face.up} down={!!down.x} onMeasure={onMeasure} style={[styles.fBtn, styles.fUp]} />
            <PadButton id="btn:y" label={face.left} down={!!down.y} onMeasure={onMeasure} style={[styles.fBtn, styles.fLeft]} />
            <PadButton id="btn:a" label={face.right} down={!!down.a} onMeasure={onMeasure} style={[styles.fBtn, styles.fRight]} />
            <PadButton id="btn:b" label={face.down} down={!!down.b} onMeasure={onMeasure} style={[styles.fBtn, styles.fDown]} />
          </View>
        </View>

        {layout.sticks > 0 && (
          <View style={styles.stickRow}>
            <AnalogStick
              id="stick:left"
              label={layout.sticks > 1 ? "IZQ" : "STICK"}
              offset={offsets.left}
              onMeasure={onMeasure}
            />
            {layout.sticks > 1 && (
              <AnalogStick
                id="stick:right"
                label="DCH"
                offset={offsets.right}
                onMeasure={onMeasure}
              />
            )}
            {layout.stick_buttons && (
              <View style={styles.stickButtons}>
                <PadButton id="btn:l3" label="L3" down={!!down.l3} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} />
                <PadButton id="btn:r3" label="R3" down={!!down.r3} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} />
              </View>
            )}
          </View>
        )}
      </View>
    </Modal>
  );
}

/**
 * A pad button — a plain View, deliberately not a Pressable.
 *
 * Pressable competes for React Native's single touch responder, and winning it
 * terminated the stick's drag, so you could not steer and press at the same
 * time. Buttons now report their rect and let GamepadPad — the one responder —
 * decide which of them the live touches are sitting on.
 */
function PadButton({
  id, label, down, style, textStyle, onMeasure,
}: {
  id: string;
  label: string;
  down: boolean;
  style?: any;
  textStyle?: any;
  onMeasure: (id: string, rect: Rect) => void;
}) {
  const ref = useRef<View | null>(null);
  const measure = useCallback(() => {
    ref.current?.measureInWindow((x, y, w, h) => onMeasure(id, { x, y, w, h }));
  }, [id, onMeasure]);

  return (
    <View ref={ref} onLayout={measure} style={[styles.btn, style, down && styles.btnDown]}>
      <Text style={[styles.btnText, textStyle]}>{label}</Text>
    </View>
  );
}

const D = 62;

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.65)", alignItems: "center", justifyContent: "center", padding: spacing.lg },
  dialog: {
    width: "100%", maxWidth: 360, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA,
    borderRadius: radius.lg, padding: 22, alignItems: "center",
  },
  iconCircle: {
    width: 68, height: 68, borderRadius: 34, backgroundColor: C.priGho,
    borderWidth: 1, borderColor: "rgba(182,196,255,0.4)",
    alignItems: "center", justifyContent: "center", marginBottom: 14,
  },
  dialogTitle: { color: C.text, fontSize: 17, fontWeight: "800", textAlign: "center" },
  dialogBody: { color: C.textMed, fontSize: 13, lineHeight: 19, textAlign: "center", marginTop: 8, marginBottom: 18 },
  primaryBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    backgroundColor: C.pri, borderRadius: 14, paddingVertical: 13, width: "100%",
  },
  primaryText: { color: "#08101F", fontWeight: "800", fontSize: 14.5 },
  secondaryBtn: { paddingVertical: 12, width: "100%", alignItems: "center" },
  secondaryText: { color: C.textMed, fontSize: 13.5, fontWeight: "600" },
  footnote: { color: C.textMed, fontSize: 10.5, textAlign: "center", opacity: 0.8, lineHeight: 15 },

  pad: { flex: 1, backgroundColor: C.bg, justifyContent: "center", paddingHorizontal: 24, paddingVertical: 12 },
  shoulderGroup: { flexDirection: "row", gap: 8 },
  stickRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 14, paddingHorizontal: 10 },
  // L3/R3 as real buttons: a touchscreen has no way to click a stick in, and
  // firing L3 just for touching the stick (as this used to) would hold it
  // down for the whole time you were steering.
  stickButtons: { gap: 10, alignItems: "center" },
  closeBtn: { position: "absolute", top: 14, right: 18, width: 36, height: 36, borderRadius: 18, backgroundColor: "rgba(255,255,255,0.06)", alignItems: "center", justifyContent: "center", zIndex: 2 },
  padConsole: { position: "absolute", top: 22, left: 22, color: C.textMed, fontSize: 11, fontWeight: "800", letterSpacing: 1 },
  shoulderRow: { flexDirection: "row", justifyContent: "space-between", marginBottom: 18 },
  shoulder: { width: 96, height: 42, borderRadius: 12 },
  mainRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  dpad: { width: D * 3, height: D * 3 },
  dBtn: { position: "absolute", width: D, height: D, borderRadius: 12 },
  dUp: { top: 0, left: D },
  dLeft: { top: D, left: 0 },
  dRight: { top: D, left: D * 2 },
  dDown: { top: D * 2, left: D },
  centerCol: { gap: 12, alignItems: "center" },
  small: { width: 92, height: 34, borderRadius: 17 },
  smallText: { fontSize: 11, fontWeight: "800" },
  face: { width: D * 3, height: D * 3 },
  fBtn: { position: "absolute", width: D, height: D, borderRadius: D / 2 },
  fUp: { top: 0, left: D },
  fLeft: { top: D, left: 0 },
  fRight: { top: D, left: D * 2 },
  fDown: { top: D * 2, left: D },
  btn: {
    backgroundColor: C.panel2, borderWidth: 1, borderColor: C.border,
    alignItems: "center", justifyContent: "center",
  },
  btnDown: { backgroundColor: C.priGho, borderColor: C.pri },
  btnText: { color: C.textDim, fontSize: 17, fontWeight: "800" },
});
