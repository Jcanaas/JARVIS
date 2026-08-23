import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, Animated, Easing, PanResponder, LayoutChangeEvent } from "react-native";
import Slider from "@react-native-community/slider";
import { C } from "../theme";

function fmtTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export type PlaybackSnapshot = {
  position: number;
  duration: number;
  playing: boolean;
  /** Wall-clock ms when `position` was sampled (Date.now()). */
  at: number;
  /** Changes whenever the track changes, so the bar restarts cleanly. */
  trackKey: string;
};

/**
 * Track progress bar.
 *
 * Driven by a single native-driver Animated.timing that runs for the whole
 * remaining track duration, instead of re-rendering React every ~100ms off a
 * JS interval. The interval approach re-rendered the entire Música screen at
 * 10fps, which is exactly what made the bar look choppy; here the fill is
 * animated on the UI thread at the display's own refresh rate and React only
 * re-renders when the track or play state actually changes.
 */
export function ProgressBar({
  snapshot,
  onSeek,
}: {
  snapshot: PlaybackSnapshot;
  onSeek: (seconds: number) => void;
}) {
  const progress = useRef(new Animated.Value(0)).current;
  const animRef = useRef<Animated.CompositeAnimation | null>(null);
  const widthRef = useRef(0);
  const [barWidth, setBarWidth] = useState(0);
  // Only the small time labels need periodic React updates (once a second),
  // and they're cheap — the bar itself never re-renders for them.
  const [labelSeconds, setLabelSeconds] = useState(0);
  const draggingRef = useRef(false);
  const [dragging, setDragging] = useState(false);
  const dragSecondsRef = useRef(0);

  const { position, duration, playing, at, trackKey } = snapshot;

  /** Seconds elapsed right now, extrapolated from the last server sample. */
  const liveSeconds = useCallback(() => {
    if (draggingRef.current) return dragSecondsRef.current;
    if (duration <= 0) return 0;
    const extra = playing ? (Date.now() - at) / 1000 : 0;
    return Math.min(duration, Math.max(0, position + extra));
  }, [position, duration, playing, at]);

  // (Re)start the native animation whenever the server hands us a new sample,
  // the track changes, or playback is paused/resumed.
  useEffect(() => {
    animRef.current?.stop();
    if (duration <= 0) {
      progress.setValue(0);
      return;
    }
    const startSeconds = liveSeconds();
    progress.setValue(startSeconds / duration);
    if (playing && !draggingRef.current) {
      const remainingMs = Math.max(0, (duration - startSeconds) * 1000);
      const anim = Animated.timing(progress, {
        toValue: 1,
        duration: remainingMs,
        easing: Easing.linear,
        useNativeDriver: true,
      });
      animRef.current = anim;
      anim.start();
    }
    return () => {
      animRef.current?.stop();
    };
  }, [progress, duration, playing, position, at, trackKey, liveSeconds]);

  useEffect(() => {
    setLabelSeconds(liveSeconds());
    const t = setInterval(() => setLabelSeconds(liveSeconds()), 500);
    return () => clearInterval(t);
  }, [liveSeconds]);

  const seekToX = useCallback(
    (x: number) => {
      const w = widthRef.current;
      if (w <= 0 || duration <= 0) return 0;
      const ratio = Math.min(1, Math.max(0, x / w));
      progress.setValue(ratio);
      const seconds = ratio * duration;
      dragSecondsRef.current = seconds;
      setLabelSeconds(seconds);
      return seconds;
    },
    [duration, progress]
  );

  const panResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => duration > 0,
        onMoveShouldSetPanResponder: () => duration > 0,
        onPanResponderGrant: (e) => {
          animRef.current?.stop();
          draggingRef.current = true;
          setDragging(true);
          seekToX(e.nativeEvent.locationX);
        },
        onPanResponderMove: (e) => {
          seekToX(e.nativeEvent.locationX);
        },
        onPanResponderRelease: (e) => {
          const seconds = seekToX(e.nativeEvent.locationX);
          draggingRef.current = false;
          setDragging(false);
          onSeek(Math.round(seconds));
        },
        onPanResponderTerminate: () => {
          draggingRef.current = false;
          setDragging(false);
        },
      }),
    [duration, seekToX, onSeek]
  );

  const onLayout = (e: LayoutChangeEvent) => {
    widthRef.current = e.nativeEvent.layout.width;
    setBarWidth(e.nativeEvent.layout.width);
  };

  const fillStyle = {
    transform: [{ scaleX: progress }],
  };
  const thumbTranslate = progress.interpolate({
    inputRange: [0, 1],
    outputRange: [0, Math.max(0, barWidth)],
    extrapolate: "clamp" as const,
  });

  return (
    <View style={styles.progressWrap}>
      <View style={styles.touchArea} onLayout={onLayout} {...panResponder.panHandlers}>
        <View style={styles.track}>
          <Animated.View style={[styles.fill, fillStyle]} />
        </View>
        <Animated.View
          pointerEvents="none"
          style={[
            styles.thumb,
            { transform: [{ translateX: thumbTranslate }, { scale: dragging ? 1.3 : 1 }] },
          ]}
        />
      </View>
      <View style={styles.times}>
        <Text style={styles.timeText}>{fmtTime(labelSeconds)}</Text>
        <Text style={styles.timeText}>{fmtTime(duration)}</Text>
      </View>
    </View>
  );
}

/**
 * Volume slider that applies while you drag, not only on release.
 *
 * The value is local state during interaction: a controlled `value` fed
 * straight from the 2.5s status poll fights the drag (the thumb snaps back to
 * the last polled level mid-gesture), which is what made dragging feel like it
 * did nothing until release.
 */
export function VolumeSlider({
  volume,
  onChange,
}: {
  volume: number;
  onChange: (level: number) => void;
}) {
  const [local, setLocal] = useState(volume);
  const draggingRef = useRef(false);
  const lastSentRef = useRef({ at: 0, level: volume });

  // Accept polled updates only while the user isn't touching the slider.
  useEffect(() => {
    if (!draggingRef.current) setLocal(volume);
  }, [volume]);

  return (
    <Slider
      style={{ flex: 1 }}
      minimumValue={0}
      maximumValue={100}
      step={1}
      value={local}
      minimumTrackTintColor={C.pri}
      maximumTrackTintColor={C.panel2}
      thumbTintColor={C.pri}
      onSlidingStart={() => { draggingRef.current = true; }}
      onValueChange={(v) => {
        const level = Math.round(v);
        setLocal(level);
        // Throttle: mpv applies volume instantly, but one request per
        // animation frame would still swamp the serialized command queue.
        const now = Date.now();
        if (level !== lastSentRef.current.level && now - lastSentRef.current.at >= 120) {
          lastSentRef.current = { at: now, level };
          onChange(level);
        }
      }}
      onSlidingComplete={(v) => {
        const level = Math.round(v);
        draggingRef.current = false;
        setLocal(level);
        lastSentRef.current = { at: Date.now(), level };
        onChange(level);
      }}
    />
  );
}

const TRACK_H = 4;
const THUMB = 13;

const styles = StyleSheet.create({
  progressWrap: { width: "100%", maxWidth: 340, marginTop: 24 },
  touchArea: { height: 28, justifyContent: "center" },
  track: { height: TRACK_H, borderRadius: TRACK_H / 2, backgroundColor: C.panel2, overflow: "hidden" },
  fill: {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: "100%",
    backgroundColor: C.pri,
    transformOrigin: "left center",
  },
  thumb: {
    position: "absolute",
    left: -THUMB / 2,
    width: THUMB,
    height: THUMB,
    borderRadius: THUMB / 2,
    backgroundColor: C.pri,
  },
  thumbActive: { transform: [{ scale: 1.25 }] },
  times: { flexDirection: "row", justifyContent: "space-between", marginTop: 2 },
  timeText: { color: C.textMed, fontSize: 11 },
});
