import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, TouchableOpacity, Modal, Pressable, Platform } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import { useKeepAwake } from "expo-keep-awake";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { C, radius, spacing } from "../theme";
import { api, PadLayout } from "../api";
import {
  configureForegroundDisplay, ensureNotificationPermission, ensureChannel,
  presentNotification, dismissNotification, onNotificationTapped,
} from "../notifications";
import AnalogStick from "./AnalogStick";

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

function GamepadPad({
  consoleId, layout, onClose,
}: { consoleId: string; layout: PadLayout; onClose: () => void }) {
  useKeepAwake(); // a controller that lets the screen sleep is useless
  const [l3, setL3] = useState(false);
  const [r3, setR3] = useState(false);

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

  const face = FACE_LABELS[layout.face] || FACE_LABELS.nintendo;
  const stickPress = (name: "l3" | "r3", setter: (v: boolean) => void) =>
    layout.stick_buttons
      ? (pressed: boolean) => { setter(pressed); send(name, pressed); }
      : undefined;

  return (
    <Modal visible transparent={false} animationType="fade" onRequestClose={onClose} supportedOrientations={["landscape"]}>
      <View style={styles.pad}>
        <TouchableOpacity style={styles.closeBtn} onPress={onClose} hitSlop={12}>
          <Ionicons name="close" size={20} color={C.textMed} />
        </TouchableOpacity>
        {!!consoleId && <Text style={styles.padConsole}>{consoleId.toUpperCase()}</Text>}

        {(layout.shoulders || layout.triggers) && (
          <View style={styles.shoulderRow}>
            <View style={styles.shoulderGroup}>
              {layout.triggers && <PadButton label="L2" name="l2" onSend={send} style={styles.shoulder} />}
              {layout.shoulders && <PadButton label="L1" name="l" onSend={send} style={styles.shoulder} />}
            </View>
            <View style={styles.shoulderGroup}>
              {layout.shoulders && <PadButton label="R1" name="r" onSend={send} style={styles.shoulder} />}
              {layout.triggers && <PadButton label="R2" name="r2" onSend={send} style={styles.shoulder} />}
            </View>
          </View>
        )}

        <View style={styles.mainRow}>
          <View style={styles.dpad}>
            <PadButton label="▲" name="up" onSend={send} style={[styles.dBtn, styles.dUp]} />
            <PadButton label="◀" name="left" onSend={send} style={[styles.dBtn, styles.dLeft]} />
            <PadButton label="▶" name="right" onSend={send} style={[styles.dBtn, styles.dRight]} />
            <PadButton label="▼" name="down" onSend={send} style={[styles.dBtn, styles.dDown]} />
          </View>

          <View style={styles.centerCol}>
            <PadButton label="SELECT" name="select" onSend={send} style={styles.small} textStyle={styles.smallText} />
            <PadButton label="START" name="start" onSend={send} style={styles.small} textStyle={styles.smallText} />
          </View>

          <View style={styles.face}>
            <PadButton label={face.up} name="x" onSend={send} style={[styles.fBtn, styles.fUp]} />
            <PadButton label={face.left} name="y" onSend={send} style={[styles.fBtn, styles.fLeft]} />
            <PadButton label={face.right} name="a" onSend={send} style={[styles.fBtn, styles.fRight]} />
            <PadButton label={face.down} name="b" onSend={send} style={[styles.fBtn, styles.fDown]} />
          </View>
        </View>

        {layout.sticks > 0 && (
          <View style={styles.stickRow}>
            <AnalogStick
              side="left"
              label={layout.sticks > 1 ? (layout.stick_buttons ? "L3" : "IZQ") : "STICK"}
              onAxes={sendAxes}
              onPress={stickPress("l3", setL3)}
              pressed={l3}
            />
            {layout.sticks > 1 && (
              <AnalogStick
                side="right"
                label={layout.stick_buttons ? "R3" : "DCH"}
                onAxes={sendAxes}
                onPress={stickPress("r3", setR3)}
                pressed={r3}
              />
            )}
          </View>
        )}
      </View>
    </Modal>
  );
}

function PadButton({
  label, name, onSend, style, textStyle,
}: {
  label: string;
  name: string;
  onSend: (name: string, pressed: boolean) => void;
  style?: any;
  textStyle?: any;
}) {
  const [down, setDown] = useState(false);
  const heldRef = useRef(false);

  // onPressIn/Out rather than onPress: a gamepad needs press AND release, and
  // it must react on touch-down, not on lift.
  return (
    <Pressable
      style={[styles.btn, style, down && styles.btnDown]}
      onPressIn={() => { heldRef.current = true; setDown(true); onSend(name, true); }}
      onPressOut={() => {
        if (!heldRef.current) return;
        heldRef.current = false;
        setDown(false);
        onSend(name, false);
      }}
    >
      <Text style={[styles.btnText, textStyle]}>{label}</Text>
    </Pressable>
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
  stickRow: { flexDirection: "row", justifyContent: "space-between", marginTop: 14, paddingHorizontal: 10 },
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
