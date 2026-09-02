import React, { useCallback, useEffect, useRef, useState } from "react";
import { View, Text, StyleSheet, TouchableOpacity, Platform, BackHandler } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import { useKeepAwake } from "expo-keep-awake";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { C, radius, spacing } from "../theme";
import { api, PadLayout } from "../api";
import { loadPadLayout, savePadLayout, resetPadLayout, type PadOffsets } from "../storage";
import {
  configureForegroundDisplay, ensureNotificationPermission, ensureChannel,
  presentNotification, dismissNotification, onNotificationTapped,
} from "../notifications";
import AnalogStick, {
  AXIS_MAX, STICK_SIZE, stickTravel,
  type Offset, type Rect, type StickSide,
} from "./AnalogStick";
import TouchScreen3DS from "./TouchScreen3DS";

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
  const [hasTouchScreen, setHasTouchScreen] = useState(false);
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
      const { active, console: cid, announce, layout: padLayout, has_touch_screen } = await api.getGamepadStatus();
      setAvailable(active);
      setConsoleId(cid || "");
      setLayout(padLayout ? { ...DIGITAL_LAYOUT, ...padLayout } : DIGITAL_LAYOUT);
      setHasTouchScreen(!!has_touch_screen);

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

  const promptVisible = prompt && available;

  // Android's hardware back button used to be handled by <Modal>'s own
  // onRequestClose — replicated manually now that this isn't a Modal (see
  // the styles.overlayFull comment for why).
  useEffect(() => {
    if (!promptVisible) return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      dismissedRef.current = true;
      setPrompt(false);
      return true;
    });
    return () => sub.remove();
  }, [promptVisible]);

  if (open) {
    return (
      <GamepadPad
        consoleId={consoleId}
        layout={layout}
        hasTouchScreen={hasTouchScreen}
        onClose={() => setOpen(false)}
      />
    );
  }

  if (!promptVisible) return null;

  return (
    <View style={styles.overlayFull}>
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
    </View>
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
  consoleId, layout, hasTouchScreen, onClose,
}: { consoleId: string; layout: PadLayout; hasTouchScreen: boolean; onClose: () => void }) {
  useKeepAwake(); // a controller that lets the screen sleep is useless

  // Visual state only. What is actually sent lives in the refs below, so a
  // re-render can never lag or duplicate a press.
  const [down, setDown] = useState<Record<string, boolean>>({});
  const [offsets, setOffsets] = useState<Record<StickSide, Offset>>({
    left: { x: 0, y: 0 }, right: { x: 0, y: 0 },
  });

  // TEMPORARY diagnostic overlay — three fix attempts for "buttons/stick
  // don't respond" have each turned out wrong or incomplete without a real
  // device to test on. This turns the next report into hard evidence
  // instead of a fourth blind guess: grants = is handleTouches even being
  // invoked at all; rects = are PadButton/AnalogStick's onMeasure calls
  // actually landing (0 here would mean measureInWindow itself is broken,
  // not hitTest); lastHit = what hitTest resolved the most recent touch to,
  // if anything. `everHit` is sticky (never resets to "none" once something
  // has matched at least once) with a running total, specifically to tell
  // apart "genuinely never matches" from "nothing happens to be held at
  // this exact instant" — the previous version showed a live-but-not-sticky
  // lastHit, which read as "none" the moment nothing was actively pressed
  // and was ambiguous about whether hitTest had ever actually worked at
  // all. Safe to remove once the real cause is confirmed.
  const [debugInfo, setDebugInfo] = useState({
    grants: 0, rects: 0, currentlyHeld: "none", everHit: "none", totalHits: 0,
  });

  // Repositionable layout (Fase D3): each control *group* — not each single
  // button, dragging 15 individual buttons on a phone screen would be
  // fiddly — can be dragged to a device-local offset from its default spot.
  const [editMode, setEditMode] = useState(false);
  const [padOffsets, setPadOffsets] = useState<PadOffsets>({});
  // A resized-up group can grow enough to overlap a neighbour, and React
  // Native hands a touch on that overlap to whichever sibling paints last —
  // fixed render order otherwise, so the neighbour underneath becomes
  // permanently unreachable (can't drag or resize it) the moment something
  // grows on top of it. Bumping a group to the front of the stack the
  // instant it's touched means whatever's currently blocking access to
  // another group is always the one you can reach *first* — move or shrink
  // it and the one underneath is reachable again, instead of staying stuck.
  const [zOrder, setZOrder] = useState<Record<string, number>>({});
  const zCounter = useRef(0);

  useEffect(() => {
    loadPadLayout().then(setPadOffsets);
  }, []);

  const bringToFront = useCallback((id: string) => {
    zCounter.current += 1;
    setZOrder((prev) => ({ ...prev, [id]: zCounter.current }));
  }, []);

  const dragGroup = useCallback((id: string, offset: Offset) => {
    setPadOffsets((prev) => {
      const next = { ...prev, [id]: { ...prev[id], x: offset.x, y: offset.y } };
      savePadLayout(next).catch(() => {});
      return next;
    });
  }, []);

  const resizeGroup = useCallback((id: string, scale: number) => {
    setPadOffsets((prev) => {
      const next = { ...prev, [id]: { x: prev[id]?.x || 0, y: prev[id]?.y || 0, scale } };
      savePadLayout(next).catch(() => {});
      return next;
    });
  }, []);

  const resetGroups = useCallback(() => {
    setPadOffsets({});
    setZOrder({});
    zCounter.current = 0;
    resetPadLayout().catch(() => {});
  }, []);

  const rects = useRef<Record<string, Rect>>({});
  /** touch identifier → control id, kept for the life of that finger so a
   *  stick keeps following it even when dragged outside its own circle. */
  const owners = useRef<Map<number, string>>(new Map());
  const sentButtons = useRef<Set<string>>(new Set());
  const sentAxes = useRef<Record<StickSide, Offset>>({
    left: { x: 0, y: 0 }, right: { x: 0, y: 0 },
  });
  const lastAxisSend = useRef<Record<StickSide, number>>({ left: 0, right: 0 });

  // Forces every PadButton/AnalogStick to re-run measureInWindow()
  // periodically — see PadButton's remeasureTick doc for why onLayout alone
  // isn't enough here: the 3DS touch-screen mirror sitting to the pad's
  // left (a sibling with its own async-loaded content) can shift every
  // control's *absolute* window position after their first layout pass
  // without ever changing their layout frame *relative to their own
  // parent*, which is all onLayout actually watches — leaving rects.current
  // stuck with stale coordinates from before that shift, silently, forever.
  const [remeasureTick, setRemeasureTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setRemeasureTick((v) => v + 1), 400);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.LANDSCAPE).catch(() => {});
    return () => {
      ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP).catch(() => {});
      // Never leave a button held or a stick off-centre if the pad closes
      // mid-press: the core would keep receiving that input forever.
      api.sendGamepadInput({ clear: true }).catch(() => {});
    };
  }, []);

  // Android's hardware back button used to be handled by <Modal>'s own
  // onRequestClose — replicated manually now that this isn't a Modal (see
  // styles.overlayFull's comment for why).
  useEffect(() => {
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      onClose();
      return true;
    });
    return () => sub.remove();
  }, [onClose]);

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

  /** Control under a touch. Both the touch coords (pageX/pageY, from
   *  handleTouches) and the measured rects (measureInWindow, from
   *  PadButton/AnalogStick's onMeasure) are already window-absolute — no
   *  origin adjustment needed, they're the same coordinate space. (An
   *  earlier version subtracted rootOrigin from the rect only, comparing it
   *  against an unadjusted touch position — silently correct only because
   *  the pad used to sit flush against the window's left edge, so that
   *  origin was ~{0,0}; once the 3DS touch-screen mirror pushed the pad's
   *  own x-origin rightward, every hit test started missing and no button
   *  or stick ever registered a touch again.) */
  const hitTest = useCallback((px: number, py: number): string | null => {
    for (const [id, r] of Object.entries(rects.current)) {
      if (px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h) return id;
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
    let debugLastHit: string | null = null;
    for (const [tid, pos] of active) {
      if (owners.current.has(tid)) continue;
      const control = hitTest(pos.x, pos.y);
      if (control) { owners.current.set(tid, control); debugLastHit = control; }
    }
    setDebugInfo((prev) => ({
      grants: prev.grants + 1,
      rects: Object.keys(rects.current).length,
      currentlyHeld: owners.current.size ? Array.from(owners.current.values()).join(",") : "none",
      everHit: debugLastHit ?? prev.everHit,
      totalHits: debugLastHit ? prev.totalHits + 1 : prev.totalHits,
    }));

    const pressed = new Set<string>();
    const nextOffsets: Record<StickSide, Offset> = {
      left: { x: 0, y: 0 }, right: { x: 0, y: 0 },
    };
    const travel = stickTravel(STICK_SIZE);

    for (const [tid, control] of owners.current) {
      const pos = active.get(tid);
      if (!pos) continue;
      if (control.startsWith("btn:")) {
        pressed.add(control.slice(4));
      } else if (control.startsWith("stick:")) {
        const side = control.slice(6) as StickSide;
        const r = rects.current[control];
        if (!r) continue;
        // Same window-absolute space as hitTest above — see its comment.
        let dx = pos.x - (r.x + r.w / 2);
        let dy = pos.y - (r.y + r.h / 2);
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
  const groupOffset = (id: string): Offset => {
    const g = padOffsets[id];
    return g ? { x: g.x, y: g.y } : { x: 0, y: 0 };
  };
  const groupScale = (id: string): number => padOffsets[id]?.scale ?? 1;
  const groupZIndex = (id: string): number => zOrder[id] || 0;

  return (
    <View style={styles.overlayFull}>
      <View style={styles.screen}>
        <View
          style={[styles.pad, hasTouchScreen && styles.padWithTouchScreen]}
          // One responder for the whole pad: this is what makes holding a
          // direction while tapping a button possible at all. Edit mode
          // disables this — dragging a group must not also register as
          // gameplay input — each Draggable becomes its own responder instead.
          onStartShouldSetResponder={() => !editMode}
          onMoveShouldSetResponder={() => !editMode}
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
          {/* TEMPORARY — see debugInfo's declaration above. */}
          <Text style={styles.debugLine} pointerEvents="none">
            g:{debugInfo.grants} r:{debugInfo.rects} held:{debugInfo.currentlyHeld}{"\n"}
            everHit:{debugInfo.everHit} totalHits:{debugInfo.totalHits}
          </Text>
          <TouchableOpacity
            style={[styles.editBtn, editMode && styles.editBtnActive]}
            onPress={() => setEditMode((v) => !v)}
            hitSlop={12}
          >
            <Ionicons name={editMode ? "checkmark" : "move"} size={16} color={editMode ? "#08101F" : C.textMed} />
          </TouchableOpacity>
          {editMode && (
            <TouchableOpacity style={styles.resetBtn} onPress={resetGroups} hitSlop={12}>
              <Text style={styles.resetText}>Restaurar</Text>
            </TouchableOpacity>
          )}
          {editMode && (
            <Text style={styles.editHint}>Arrastra los grupos para moverlos, usa +/- para cambiar el tamaño</Text>
          )}

          {(layout.shoulders || layout.triggers) && (
            <View style={styles.shoulderRow}>
              <Draggable id="group:shoulder-l" editMode={editMode} offset={groupOffset("group:shoulder-l")} scale={groupScale("group:shoulder-l")} zIndex={groupZIndex("group:shoulder-l")} onDrag={dragGroup} onResize={resizeGroup} onActivate={bringToFront} style={styles.shoulderGroup}>
                {layout.triggers && <PadButton id="btn:l2" label="L2" down={!!down.l2} onMeasure={onMeasure} style={styles.shoulder} disabled={editMode} remeasureTick={remeasureTick} />}
                {layout.shoulders && <PadButton id="btn:l" label="L1" down={!!down.l} onMeasure={onMeasure} style={styles.shoulder} disabled={editMode} remeasureTick={remeasureTick} />}
              </Draggable>
              <Draggable id="group:shoulder-r" editMode={editMode} offset={groupOffset("group:shoulder-r")} scale={groupScale("group:shoulder-r")} zIndex={groupZIndex("group:shoulder-r")} onDrag={dragGroup} onResize={resizeGroup} onActivate={bringToFront} style={styles.shoulderGroup}>
                {layout.shoulders && <PadButton id="btn:r" label="R1" down={!!down.r} onMeasure={onMeasure} style={styles.shoulder} disabled={editMode} remeasureTick={remeasureTick} />}
                {layout.triggers && <PadButton id="btn:r2" label="R2" down={!!down.r2} onMeasure={onMeasure} style={styles.shoulder} disabled={editMode} remeasureTick={remeasureTick} />}
              </Draggable>
            </View>
          )}

          <View style={styles.mainRow}>
            <Draggable id="group:dpad" editMode={editMode} offset={groupOffset("group:dpad")} scale={groupScale("group:dpad")} zIndex={groupZIndex("group:dpad")} onDrag={dragGroup} onResize={resizeGroup} onActivate={bringToFront} style={styles.dpad}>
              <PadButton id="btn:up" label="▲" down={!!down.up} onMeasure={onMeasure} style={[styles.dBtn, styles.dUp]} disabled={editMode} remeasureTick={remeasureTick} />
              <PadButton id="btn:left" label="◀" down={!!down.left} onMeasure={onMeasure} style={[styles.dBtn, styles.dLeft]} disabled={editMode} remeasureTick={remeasureTick} />
              <PadButton id="btn:right" label="▶" down={!!down.right} onMeasure={onMeasure} style={[styles.dBtn, styles.dRight]} disabled={editMode} remeasureTick={remeasureTick} />
              <PadButton id="btn:down" label="▼" down={!!down.down} onMeasure={onMeasure} style={[styles.dBtn, styles.dDown]} disabled={editMode} remeasureTick={remeasureTick} />
            </Draggable>

            <Draggable id="group:center" editMode={editMode} offset={groupOffset("group:center")} scale={groupScale("group:center")} zIndex={groupZIndex("group:center")} onDrag={dragGroup} onResize={resizeGroup} onActivate={bringToFront} style={styles.centerCol}>
              <PadButton id="btn:select" label="SELECT" down={!!down.select} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} disabled={editMode} remeasureTick={remeasureTick} />
              <PadButton id="btn:start" label="START" down={!!down.start} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} disabled={editMode} remeasureTick={remeasureTick} />
            </Draggable>

            <Draggable id="group:face" editMode={editMode} offset={groupOffset("group:face")} scale={groupScale("group:face")} zIndex={groupZIndex("group:face")} onDrag={dragGroup} onResize={resizeGroup} onActivate={bringToFront} style={styles.face}>
              <PadButton id="btn:x" label={face.up} down={!!down.x} onMeasure={onMeasure} style={[styles.fBtn, styles.fUp]} disabled={editMode} remeasureTick={remeasureTick} />
              <PadButton id="btn:y" label={face.left} down={!!down.y} onMeasure={onMeasure} style={[styles.fBtn, styles.fLeft]} disabled={editMode} remeasureTick={remeasureTick} />
              <PadButton id="btn:a" label={face.right} down={!!down.a} onMeasure={onMeasure} style={[styles.fBtn, styles.fRight]} disabled={editMode} remeasureTick={remeasureTick} />
              <PadButton id="btn:b" label={face.down} down={!!down.b} onMeasure={onMeasure} style={[styles.fBtn, styles.fDown]} disabled={editMode} remeasureTick={remeasureTick} />
            </Draggable>
          </View>

          {layout.sticks > 0 && (
            <Draggable id="group:sticks" editMode={editMode} offset={groupOffset("group:sticks")} scale={groupScale("group:sticks")} zIndex={groupZIndex("group:sticks")} onDrag={dragGroup} onResize={resizeGroup} onActivate={bringToFront} style={styles.stickRow}>
              <AnalogStick
                id="stick:left"
                label={layout.sticks > 1 ? "IZQ" : "STICK"}
                offset={offsets.left}
                onMeasure={onMeasure}
                remeasureTick={remeasureTick}
              />
              {layout.sticks > 1 && (
                <AnalogStick
                  id="stick:right"
                  label="DCH"
                  offset={offsets.right}
                  onMeasure={onMeasure}
                  remeasureTick={remeasureTick}
                />
              )}
              {layout.stick_buttons && (
                <View style={styles.stickButtons}>
                  <PadButton id="btn:l3" label="L3" down={!!down.l3} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} disabled={editMode} remeasureTick={remeasureTick} />
                  <PadButton id="btn:r3" label="R3" down={!!down.r3} onMeasure={onMeasure} style={styles.small} textStyle={styles.smallText} disabled={editMode} remeasureTick={remeasureTick} />
                </View>
              )}
            </Draggable>
          )}
        </View>

        {/* Rendered *after* pad, not before — styles.screen is row-reverse,
            so this still ends up visually on the left where it always was,
            but now it's the later sibling and paints (and hit-tests) on
            top of pad naturally, with no zIndex/elevation trick needed.
            That trick (elevation especially) is the prime remaining
            suspect for the pad's whole left side — d-pad, stick — never
            receiving a touch in *either* mode, gameplay or edit: elevation
            was the one thing applied unconditionally in both, and nothing
            scoped to gameplay-only (handleTouches/hitTest fixes) or
            edit-only ever had a chance to touch it. */}
        {hasTouchScreen && (
          <View style={styles.touchCol}>
            <Draggable
              id="group:touchscreen"
              editMode={editMode}
              offset={groupOffset("group:touchscreen")}
              scale={groupScale("group:touchscreen")}
              zIndex={groupZIndex("group:touchscreen")}
              onDrag={dragGroup}
              onResize={resizeGroup}
              onActivate={bringToFront}
            >
              <TouchScreen3DS active={!editMode} />
            </Draggable>
          </View>
        )}
      </View>
    </View>
  );
}

/** Scale range a group can be resized to — wide enough to matter on a phone
 * screen, narrow enough that a group can't grow off-screen or shrink to
 * nothing by mistake. */
const MIN_SCALE = 0.6;
const MAX_SCALE = 1.9;
const SCALE_STEP = 0.1;

/**
 * Wraps one control group so it can be dragged to a device-local offset —
 * and, via the +/- buttons at its corner, scaled — in edit mode. Outside
 * edit mode it's a plain positioned+scaled View with no responder of its
 * own, so it never competes with GamepadPad's single-responder gameplay
 * handling (see PadButton's docstring for why that matters).
 */
function Draggable({
  id, editMode, offset, scale, zIndex, onDrag, onResize, onActivate, children, style,
}: {
  id: string;
  editMode: boolean;
  offset: Offset;
  scale: number;
  zIndex: number;
  onDrag: (id: string, offset: Offset) => void;
  onResize: (id: string, scale: number) => void;
  onActivate: (id: string) => void;
  children: React.ReactNode;
  style?: any;
}) {
  const start = useRef<Offset>({ x: 0, y: 0 });
  const dragging = useRef(false);

  const onGrant = useCallback((e: any) => {
    onActivate(id);
    dragging.current = true;
    start.current = { x: e.nativeEvent.pageX - offset.x, y: e.nativeEvent.pageY - offset.y };
  }, [id, offset, onActivate]);

  const onMove = useCallback((e: any) => {
    if (!dragging.current) return;
    onDrag(id, {
      x: e.nativeEvent.pageX - start.current.x,
      y: e.nativeEvent.pageY - start.current.y,
    });
  }, [id, onDrag]);

  const onRelease = useCallback(() => { dragging.current = false; }, []);

  const shrink = useCallback(() => {
    onActivate(id);
    onResize(id, Math.max(MIN_SCALE, Math.round((scale - SCALE_STEP) * 100) / 100));
  }, [id, onActivate, onResize, scale]);

  const grow = useCallback(() => {
    onActivate(id);
    onResize(id, Math.min(MAX_SCALE, Math.round((scale + SCALE_STEP) * 100) / 100));
  }, [id, onActivate, onResize, scale]);

  const customized = offset.x !== 0 || offset.y !== 0 || scale !== 1;
  // Android has known issues where even an *identity* transform (0,0,
  // scale 1) on an ancestor View can throw off measureInWindow() results
  // and touch-bubbling for its children — exactly the symptoms reported
  // here (buttons and the stick not registering touches at all, not even
  // updating local state, gameplay-only). Skipping the transform style
  // entirely for the untouched (never dragged/resized) case removes that
  // risk for the default layout everyone starts with; a group only gets
  // wrapped in a real transform once the user has actually customized it.
  const positioned = customized
    ? [style, { transform: [{ translateX: offset.x }, { translateY: offset.y }, { scale }] }, editMode && { zIndex }]
    : [style, editMode && { zIndex }];

  if (!editMode) {
    return <View style={positioned}>{children}</View>;
  }

  return (
    <View
      style={[positioned, styles.draggable]}
      onStartShouldSetResponder={() => true}
      onMoveShouldSetResponder={() => true}
      onResponderGrant={onGrant}
      onResponderMove={onMove}
      onResponderRelease={onRelease}
      onResponderTerminate={onRelease}
    >
      {children}
      {/* Tap +/- rather than a drag-to-resize handle: a corner drag gesture
          nested inside this same View's own drag responder turned out
          unreliable in practice on-device across several attempts (precise
          hit-testing, negotiating which responder wins). Plain
          TouchableOpacity taps use the exact same mechanism already proven
          reliable for every other button in this file (close/edit/reset),
          not a bespoke gesture. */}
      <View style={styles.resizeControls} pointerEvents="box-none">
        <TouchableOpacity style={styles.resizeBtn} onPress={shrink} hitSlop={10}>
          <Ionicons name="remove" size={14} color="#08101F" />
        </TouchableOpacity>
        <TouchableOpacity style={styles.resizeBtn} onPress={grow} hitSlop={10}>
          <Ionicons name="add" size={14} color="#08101F" />
        </TouchableOpacity>
      </View>
    </View>
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
  id, label, down, style, textStyle, onMeasure, disabled, remeasureTick,
}: {
  id: string;
  label: string;
  down: boolean;
  style?: any;
  textStyle?: any;
  onMeasure: (id: string, rect: Rect) => void;
  /** True while the pad is in edit mode — the root responder is off then
   *  (see GamepadPad), so this button won't receive input either way; this
   *  just dims it to make that visually obvious. */
  disabled?: boolean;
  /** Bumped periodically by the parent to force a fresh measureInWindow()
   * even when onLayout hasn't refired — see AnalogStick's copy of this
   * same prop for the full explanation (a sibling appearing/resizing shifts
   * a control's *absolute* window position without changing its layout
   * frame *relative to its own parent*, the only thing onLayout watches). */
  remeasureTick?: number;
}) {
  const ref = useRef<View | null>(null);
  const measure = useCallback(() => {
    ref.current?.measureInWindow((x, y, w, h) => onMeasure(id, { x, y, w, h }));
  }, [id, onMeasure]);

  useEffect(() => { measure(); }, [remeasureTick, measure]);

  return (
    <View ref={ref} onLayout={measure} style={[styles.btn, style, down && styles.btnDown, disabled && styles.btnDisabled]}>
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

  // A plain absolutely-positioned View, not React Native's <Modal> — on
  // Android, Modal renders in its own separate native Window (a Dialog),
  // and system-bar/edge-to-edge behaviour can only be controlled for the
  // main Activity's window, never a Modal's — a confirmed, still-open
  // upstream React Native limitation (statusBarTranslucent/
  // navigationBarTranslucent, tried first, don't actually fix it — see
  // github.com/expo/expo/issues/39749, where the Expo team's own
  // conclusion is "use something other than Modal"). Rendered as a normal
  // sibling inside the same window as everything else in App.tsx instead
  // — that's why it needs its own hardware-back-button handling
  // (BackHandler) that <Modal>'s onRequestClose used to give for free.
  // No background of its own — the prompt's own .backdrop is translucent
  // (dims the screen behind it) while the pad's own .screen is opaque;
  // this wrapper just needs to not get in the way of either. Plain
  // top/left/right/bottom rather than StyleSheet.absoluteFill: the latter
  // is itself an already-registered style reference, and nesting it as a
  // value inside another StyleSheet.create() call is untested territory.
  overlayFull: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0 },
  // row-reverse rather than row: touchCol is rendered *after* pad in JSX
  // now (see the comment where it's rendered) purely so it paints on top —
  // row-reverse keeps it visually on the left, where it always was, without
  // needing pad to render first and something else to still show up left
  // of it.
  screen: { flex: 1, flexDirection: "row-reverse", backgroundColor: C.bg },
  touchCol: { alignItems: "center", justifyContent: "center", paddingLeft: 20, paddingVertical: 12 },
  pad: { flex: 1, backgroundColor: C.bg, justifyContent: "center", paddingHorizontal: 24, paddingVertical: 12 },
  // Narrower when sharing the screen with the 3DS touch mirror, so the pad
  // doesn't crowd it off a phone-sized landscape width.
  padWithTouchScreen: { paddingHorizontal: 12 },
  editBtn: {
    position: "absolute", top: 14, right: 64, width: 36, height: 36, borderRadius: 18,
    backgroundColor: "rgba(255,255,255,0.06)", alignItems: "center", justifyContent: "center", zIndex: 2,
  },
  editBtnActive: { backgroundColor: C.pri },
  resetBtn: { position: "absolute", top: 14, right: 108, height: 36, paddingHorizontal: 12, borderRadius: 18, backgroundColor: "rgba(255,255,255,0.06)", alignItems: "center", justifyContent: "center", zIndex: 2 },
  resetText: { color: C.textMed, fontSize: 11, fontWeight: "700" },
  editHint: { position: "absolute", top: 58, alignSelf: "center", color: C.textMed, fontSize: 11, opacity: 0.8 },
  draggable: { borderWidth: 1, borderColor: C.pri, borderStyle: "dashed", borderRadius: 12 },
  resizeControls: {
    position: "absolute", bottom: -16, right: -16, flexDirection: "row", gap: 6, zIndex: 3,
  },
  resizeBtn: {
    width: 28, height: 28, borderRadius: 14,
    backgroundColor: C.pri, borderWidth: 2, borderColor: C.bg,
    alignItems: "center", justifyContent: "center",
  },
  btnDisabled: { opacity: 0.55 },
  shoulderGroup: { flexDirection: "row", gap: 8 },
  stickRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginTop: 14, paddingHorizontal: 10 },
  // L3/R3 as real buttons: a touchscreen has no way to click a stick in, and
  // firing L3 just for touching the stick (as this used to) would hold it
  // down for the whole time you were steering.
  stickButtons: { gap: 10, alignItems: "center" },
  closeBtn: { position: "absolute", top: 14, right: 18, width: 36, height: 36, borderRadius: 18, backgroundColor: "rgba(255,255,255,0.06)", alignItems: "center", justifyContent: "center", zIndex: 2 },
  padConsole: { position: "absolute", top: 22, left: 22, color: C.textMed, fontSize: 11, fontWeight: "800", letterSpacing: 1 },
  // TEMPORARY — see debugInfo's declaration above.
  debugLine: { position: "absolute", bottom: 6, left: 6, color: "#ff6666", fontSize: 10, fontFamily: "monospace" },
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
