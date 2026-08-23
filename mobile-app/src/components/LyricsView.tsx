import React, { useEffect, useMemo, useRef, useState } from "react";
import { View, Text, StyleSheet, ScrollView, ActivityIndicator } from "react-native";
import { C, spacing } from "../theme";
import { api, LyricLine } from "../api";

const LINE_HEIGHT = 46;

/**
 * Synced lyrics that follow playback.
 *
 * Scrolling is driven by the extrapolated position rather than by a poll of
 * its own: the desktop reports the position every few seconds, and lyrics
 * would visibly lurch if they only moved that often.
 */
export default function LyricsView({
  title, artists, position, playing,
}: {
  title: string;
  artists: string;
  /** Seconds, as last reported. */
  position: number;
  playing: boolean;
}) {
  const [lines, setLines] = useState<LyricLine[] | null>(null);
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<ScrollView>(null);
  // Re-anchored when the reported position changes, AND when playing toggles.
  // Without the `playing` half of this, resuming after a pause reused the
  // OLD anchor timestamp — the elapsed term then included the entire paused
  // stretch, so the highlighted line jumped forward the instant play
  // resumed instead of tracking the song from where it actually restarted.
  const anchor = useRef({ position, at: Date.now() });
  const [now, setNow] = useState(position);
  useEffect(() => {
    anchor.current = { position, at: Date.now() };
    setNow(position); // don't wait up to 300ms for the interval to catch up
  }, [position, playing]);

  useEffect(() => {
    let cancelled = false;
    if (!title) { setLines([]); return; }
    setLoading(true);
    setLines(null);
    api.getLyrics(title, artists)
      .then((l) => { if (!cancelled) setLines(l); })
      .catch(() => { if (!cancelled) setLines([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [title, artists]);

  useEffect(() => {
    const t = setInterval(() => {
      const { position: p, at } = anchor.current;
      setNow(playing ? p + (Date.now() - at) / 1000 : p);
    }, 300);
    return () => clearInterval(t);
  }, [playing]);

  const activeIndex = useMemo(() => {
    if (!lines?.length) return -1;
    let index = -1;
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].time <= now) index = i;
      else break;
    }
    return index;
  }, [lines, now]);

  useEffect(() => {
    if (activeIndex < 0) return;
    // Keep the current line about a third down the panel.
    scrollRef.current?.scrollTo({ y: Math.max(0, activeIndex * LINE_HEIGHT - 120), animated: true });
  }, [activeIndex]);

  if (loading) {
    return (
      <View style={styles.centerFill}>
        <ActivityIndicator color={C.pri} />
      </View>
    );
  }
  if (!lines?.length) {
    return (
      <View style={styles.centerFill}>
        <Text style={styles.empty}>No hay letra sincronizada para esta canción.</Text>
      </View>
    );
  }

  return (
    <ScrollView
      ref={scrollRef}
      style={styles.scroller}
      contentContainerStyle={styles.wrap}
      showsVerticalScrollIndicator={false}
    >
      {lines.map((l, i) => (
        <Text
          key={i}
          style={[
            styles.line,
            i === activeIndex && styles.lineActive,
            i < activeIndex && styles.linePast,
          ]}
        >
          {l.line || "♪"}
        </Text>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroller: { flex: 1, width: "100%" },
  wrap: { paddingVertical: spacing.lg, paddingHorizontal: spacing.lg, alignItems: "center" },
  centerFill: { flex: 1, width: "100%", alignItems: "center", justifyContent: "center" },
  line: { color: C.textMed, fontSize: 16, lineHeight: LINE_HEIGHT, fontWeight: "600", textAlign: "center" },
  lineActive: { color: C.pri, fontSize: 19, fontWeight: "800" },
  linePast: { opacity: 0.45 },
  empty: { color: C.textMed, fontSize: 13, textAlign: "center", padding: 28, lineHeight: 19 },
});
