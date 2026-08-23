import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, FlatList, TextInput, TouchableOpacity, KeyboardAvoidingView, Platform,
  ActivityIndicator,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import {
  useAudioRecorder, RecordingPresets, requestRecordingPermissionsAsync, setAudioModeAsync,
} from "expo-audio";
import { launchImageLibraryAsync, MediaTypeOptions } from "expo-image-picker";
import * as DocumentPicker from "expo-document-picker";
import { C, spacing, radius } from "../theme";
import { api, LogEntry, Status } from "../api";

type Bubble = { id: number; tag: "you" | "jarvis" | "sys" | "error"; label: string; text: string };

function tagOf(item: LogEntry): Bubble["tag"] {
  const msg = item.message || "";
  if (item.type === "error") return "error";
  if (/^t[uú]:/i.test(msg) || /^you:/i.test(msg)) return "you";
  if (/^jarvis:/i.test(msg)) return "jarvis";
  return "sys";
}
function stripPrefix(msg: string): string {
  return msg.replace(/^(t[uú]|you|jarvis|sys)\s*:\s*/i, "");
}

export default function ChatScreen({ onOpenMusic }: { onOpenMusic: () => void }) {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const sinceRef = useRef(0);
  const listRef = useRef<FlatList<Bubble>>(null);
  // FIFO of texts already shown optimistically (see send()) — when the
  // server's own echo of the same text comes back through polling, we skip
  // re-adding it instead of showing the bubble twice.
  const pendingSentRef = useRef<string[]>([]);

  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);

  async function toggleRecording() {
    if (transcribing) return;

    if (recording) {
      setRecording(false);
      setTranscribing(true);
      try {
        await recorder.stop();
        const uri = recorder.uri;
        if (uri) {
          const { text } = await api.sendVoice(uri);
          if (text) {
            // Shown straight away like a typed message; the desktop echoes it
            // through the log too, so mark it pending to avoid a duplicate.
            setBubbles((prev) => [...prev, { id: -Date.now(), tag: "you", label: "Tú", text }]);
            pendingSentRef.current.push(text);
          }
        }
      } catch (e) {
        setBubbles((prev) => [...prev, {
          id: -Date.now(), tag: "error", label: "error",
          text: e instanceof Error ? e.message : "No se pudo transcribir el audio.",
        }]);
      }
      setTranscribing(false);
      return;
    }

    const perm = await requestRecordingPermissionsAsync();
    if (!perm.granted) {
      setBubbles((prev) => [...prev, {
        id: -Date.now(), tag: "error", label: "error",
        text: "Sin permiso de micrófono.",
      }]);
      return;
    }
    // allowsRecording must be on for the mic to open; leaving it on afterwards
    // would duck music playback, so it is turned back off when we stop.
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true }).catch(() => {});
    await recorder.prepareToRecordAsync();
    recorder.record();
    setRecording(true);
  }

  useEffect(() => {
    if (recording) return;
    setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => {});
  }, [recording]);

  const pollLog = useCallback(async () => {
    try {
      const items = await api.getLog(sinceRef.current);
      if (items.length) {
        sinceRef.current = Math.max(sinceRef.current, ...items.map((i) => i.id));
        const mapped: Bubble[] = [];
        for (const i of items) {
          const tag = tagOf(i);
          const text = stripPrefix(i.message || "");
          if (tag === "you") {
            const idx = pendingSentRef.current.indexOf(text);
            if (idx !== -1) {
              pendingSentRef.current.splice(idx, 1);
              continue; // already shown optimistically — don't duplicate
            }
          }
          mapped.push({
            id: i.id,
            tag,
            label: tag === "you" ? "Tú" : tag === "jarvis" ? "JARVIS" : i.source || "sistema",
            text,
          });
        }
        if (mapped.length) setBubbles((prev) => [...prev, ...mapped].slice(-300));
      }
    } catch {
      // transient — next poll retries
    }
  }, []);

  const pollStatus = useCallback(async () => {
    try {
      setStatus(await api.getStatus());
    } catch {
      // transient
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      pollLog();
      pollStatus();
      const t1 = setInterval(pollLog, 2000);
      const t2 = setInterval(pollStatus, 2500);
      return () => {
        clearInterval(t1);
        clearInterval(t2);
      };
    }, [pollLog, pollStatus])
  );

  useEffect(() => {
    if (bubbles.length) requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  }, [bubbles.length]);

  async function pickPhoto() {
    try {
      const result = await launchImageLibraryAsync({
        mediaTypes: MediaTypeOptions.Images,
        quality: 0.8,
      });
      if (!result.canceled && result.assets[0]) {
        setBubbles((prev) => [...prev, {
          id: -Date.now(),
          tag: "you",
          label: "Tú",
          text: `[Foto: ${result.assets[0].fileName || "image"}]`,
        }]);
      }
    } catch (e) {
      setBubbles((prev) => [...prev, {
        id: -Date.now(),
        tag: "error",
        label: "error",
        text: "No se pudo seleccionar la foto.",
      }]);
    }
  }

  async function pickDocument() {
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: "*/*",
        multiple: false,
      });
      if (!result.canceled && result.assets[0]) {
        setBubbles((prev) => [...prev, {
          id: -Date.now(),
          tag: "you",
          label: "Tú",
          text: `[Archivo: ${result.assets[0].name || "file"}]`,
        }]);
      }
    } catch (e) {
      setBubbles((prev) => [...prev, {
        id: -Date.now(),
        tag: "error",
        label: "error",
        text: "No se pudo seleccionar el archivo.",
      }]);
    }
  }

  async function send() {
    const text = input.trim();
    if (!text) return;
    setInput("");
    // Show the bubble immediately — don't make the user wait up to 2s for
    // the next poll cycle to echo their own message back.
    setBubbles((prev) => [...prev, { id: -Date.now(), tag: "you", label: "Tú", text }]);
    pendingSentRef.current.push(text);
    setSending(true);
    try {
      await api.sendCommand(text);
    } catch {
      // shows up as an error entry via the next log poll if it truly failed server-side
    }
    setSending(false);
  }

  const music = status?.music;
  const hasTrack = !!music?.title;
  const insets = useSafeAreaInsets();

  return (
    <KeyboardAvoidingView
      style={[styles.container, { paddingTop: insets.top }]}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <TouchableOpacity style={[styles.player, !hasTrack && styles.playerEmpty]} onPress={onOpenMusic} activeOpacity={0.8}>
        <View style={{ flex: 1 }}>
          <Text style={styles.playerTitle} numberOfLines={1}>{hasTrack ? music!.title : "Nada sonando"}</Text>
          <Text style={styles.playerArtist} numberOfLines={1}>{hasTrack ? music!.artists || "—" : "—"}</Text>
        </View>
        <TouchableOpacity
          style={styles.playBtn}
          onPress={() => api.musicAction("toggle").catch(() => {})}
        >
          <Ionicons name={music?.playing ? "pause" : "play"} size={16} color={C.pri} />
        </TouchableOpacity>
      </TouchableOpacity>

      <FlatList
        ref={listRef}
        style={styles.feed}
        contentContainerStyle={{ padding: spacing.lg, gap: spacing.sm }}
        data={bubbles}
        keyExtractor={(b) => String(b.id)}
        ListEmptyComponent={<Text style={styles.emptyHint}>Sin actividad todavía.{"\n"}Envía una orden abajo.</Text>}
        renderItem={({ item }) => (
          <View
            style={[
              styles.bubble,
              item.tag === "you" && styles.bubbleYou,
              item.tag === "jarvis" && styles.bubbleJarvis,
              item.tag === "sys" && styles.bubbleSys,
              item.tag === "error" && styles.bubbleError,
            ]}
          >
            {(item.tag === "you" || item.tag === "jarvis") && (
              <Text style={[styles.bubbleMeta, item.tag === "you" && { color: "rgba(8,16,31,0.65)" }]}>{item.label}</Text>
            )}
            <Text style={[styles.bubbleText, item.tag === "you" && { color: "#08101F" }]}>{item.text}</Text>
          </View>
        )}
      />

      <View style={styles.composer}>
        <TouchableOpacity
          style={[styles.micBtn, recording && styles.micBtnActive]}
          onPress={toggleRecording}
          disabled={transcribing}
        >
          {transcribing ? (
            <ActivityIndicator color={C.pri} size="small" />
          ) : (
            <Ionicons name={recording ? "stop" : "mic"} size={19} color={recording ? "#08101F" : C.pri} />
          )}
        </TouchableOpacity>
        <View style={styles.inputWrap}>
          <TextInput
            style={styles.input}
            value={input}
            onChangeText={setInput}
            placeholder="Escribe una orden o pregunta…"
            placeholderTextColor={C.textMed}
            cursorColor={C.pri}
            selectionColor={C.pri}
            keyboardAppearance="dark"
            multiline
          />
          <View style={styles.attachmentBtns}>
            <TouchableOpacity style={styles.attachBtn} onPress={pickPhoto}>
              <Ionicons name="image" size={16} color={C.pri} />
            </TouchableOpacity>
            <TouchableOpacity style={styles.attachBtn} onPress={pickDocument}>
              <Ionicons name="document" size={16} color={C.pri} />
            </TouchableOpacity>
          </View>
        </View>
        <TouchableOpacity style={[styles.sendBtn, sending && { opacity: 0.4 }]} onPress={send} disabled={sending}>
          <Ionicons name="send" size={17} color="#08101F" />
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  player: {
    margin: spacing.lg, marginBottom: 0, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA,
    borderRadius: radius.lg, padding: spacing.md, flexDirection: "row", alignItems: "center", gap: spacing.md,
  },
  playerEmpty: { opacity: 0.7 },
  playerTitle: { color: C.text, fontSize: 13, fontWeight: "700" },
  playerArtist: { color: C.textMed, fontSize: 11, marginTop: 2 },
  playBtn: {
    width: 40, height: 40, borderRadius: 20, backgroundColor: C.priGho, borderWidth: 1,
    borderColor: "rgba(182,196,255,0.35)", alignItems: "center", justifyContent: "center",
  },
  feed: { flex: 1 },
  emptyHint: { color: C.textMed, fontSize: 13, textAlign: "center", marginTop: 60, lineHeight: 20 },
  bubble: { maxWidth: "86%", padding: 12, borderRadius: 17 },
  bubbleYou: { alignSelf: "flex-end", backgroundColor: C.priDim, borderBottomRightRadius: 4 },
  bubbleJarvis: { alignSelf: "flex-start", backgroundColor: C.panel2, borderWidth: 1, borderColor: C.borderA, borderBottomLeftRadius: 4 },
  bubbleSys: { alignSelf: "center", backgroundColor: "transparent" },
  bubbleError: { alignSelf: "center", backgroundColor: "rgba(255,94,130,0.10)", borderWidth: 1, borderColor: "rgba(255,94,130,0.3)" },
  bubbleMeta: { color: C.pri, fontSize: 10, fontWeight: "700", marginBottom: 3, textTransform: "uppercase" },
  bubbleText: { color: C.text, fontSize: 14.5, lineHeight: 20 },
  composer: {
    flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.md,
    borderTopWidth: 1, borderTopColor: C.borderA,
  },
  inputWrap: {
    flex: 1, minHeight: 46, maxHeight: 100, backgroundColor: C.panel, borderWidth: 1, borderColor: C.border,
    borderRadius: 22, flexDirection: "row", alignItems: "center", paddingHorizontal: 12,
  },
  input: {
    flex: 1, color: C.text, paddingVertical: Platform.OS === "android" ? 6 : 12, fontSize: 15,
    maxHeight: 90, textAlignVertical: "center",
  },
  attachmentBtns: {
    flexDirection: "row", gap: 4, alignItems: "center", paddingRight: 2,
  },
  attachBtn: {
    width: 32, height: 32, borderRadius: 16, backgroundColor: C.priGho,
    alignItems: "center", justifyContent: "center",
  },
  sendBtn: { width: 46, height: 46, borderRadius: 23, backgroundColor: C.priDim, alignItems: "center", justifyContent: "center" },
  micBtn: { width: 46, height: 46, borderRadius: 23, backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.35)", alignItems: "center", justifyContent: "center" },
  micBtnActive: { backgroundColor: C.red, borderColor: C.red },
});
