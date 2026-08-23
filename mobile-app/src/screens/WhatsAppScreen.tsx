import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  View, Text, StyleSheet, FlatList, TextInput, TouchableOpacity, KeyboardAvoidingView, Platform, Image,
  Modal, Dimensions, ActivityIndicator, Alert,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import {
  useAudioPlayer, useAudioRecorder, RecordingPresets, requestRecordingPermissionsAsync, setAudioModeAsync,
} from "expo-audio";
import * as DocumentPicker from "expo-document-picker";
import * as ImagePicker from "expo-image-picker";
import { C, spacing, radius } from "../theme";
import { api, ApiError, WaChat, WaMessage, mediaProxyUrl, avatarProxyUrl } from "../api";

function initials(name: string): string {
  return (name || "?").trim().slice(0, 2).toUpperCase();
}

// The bridge falls back to a literal "[type]" body (e.g. "[e2e_notification]",
// "[pinned-message]") for WhatsApp protocol/system message types it has no
// friendly text for — these are known common ones; anything else still gets
// humanized instead of shown as a raw bracket tag.
const SYSTEM_TYPE_LABELS: Record<string, { label: string; icon: string }> = {
  gp2: { label: "Actualización del grupo", icon: "people" },
  group_notification: { label: "Actualización del grupo", icon: "people" },
  call_log: { label: "Llamada", icon: "call" },
  e2e_notification: { label: "Aviso de cifrado", icon: "lock-closed" },
  notification: { label: "Notificación", icon: "information-circle" },
  notification_template: { label: "Notificación", icon: "information-circle" },
  broadcast_notification: { label: "Difusión", icon: "megaphone" },
  ciphertext: { label: "Mensaje pendiente de descifrar", icon: "hourglass" },
  order: { label: "Pedido", icon: "cart" },
  payment: { label: "Pago", icon: "cash" },
  product: { label: "Producto", icon: "pricetag" },
  groups_v4_invite: { label: "Invitación a grupo", icon: "person-add" },
  list: { label: "Lista", icon: "list" },
  list_response: { label: "Respuesta de lista", icon: "list" },
  buttons_response: { label: "Respuesta de botón", icon: "checkmark-circle" },
  template_button_reply: { label: "Respuesta de botón", icon: "checkmark-circle" },
  interactive: { label: "Mensaje interactivo", icon: "apps" },
  native_flow: { label: "Mensaje interactivo", icon: "apps" },
  scheduled_event_creation: { label: "Evento programado", icon: "calendar" },
  revoked: { label: "Mensaje eliminado", icon: "trash" },
  protocol: { label: "Mensaje de protocolo", icon: "information-circle" },
  reaction: { label: "Reacción", icon: "heart" },
  unknown: { label: "Mensaje no compatible", icon: "help-circle" },
  pin_message: { label: "Mensaje fijado", icon: "pin" },
  "pinned-message": { label: "Mensaje fijado", icon: "pin" },
  pinned_message: { label: "Mensaje fijado", icon: "pin" },
};

function bracketSystemInfo(body: string): { label: string; icon: string } | null {
  const m = (body || "").trim().match(/^\[([^\]]+)\]$/);
  if (!m) return null;
  const key = m[1].toLowerCase().trim();
  if (SYSTEM_TYPE_LABELS[key]) return SYSTEM_TYPE_LABELS[key];
  const words = key.replace(/[_-]+/g, " ").trim();
  const label = words.charAt(0).toUpperCase() + words.slice(1);
  return { label, icon: "information-circle" };
}
function fmtTime(ts?: number): string {
  if (!ts) return "";
  const d = new Date(ts > 2e12 ? ts : ts * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  return `${d.getDate()}/${d.getMonth() + 1}`;
}

function AudioBubbleContent({ uri, fromMe }: { uri: string; fromMe: boolean }) {
  const player = useAudioPlayer(uri);
  return (
    <TouchableOpacity
      style={styles.audioRow}
      onPress={() => (player.playing ? player.pause() : player.play())}
    >
      <Ionicons name={player.playing ? "pause-circle" : "play-circle"} size={30} color={fromMe ? "#08101F" : C.pri} />
      <Text style={[styles.audioLabel, fromMe && { color: "#08101F" }]}>Nota de voz</Text>
    </TouchableOpacity>
  );
}

// Derived from the screen, not hardcoded: the bubble itself is capped at 86%
// of the width, and a fixed 240px image fought that cap on narrow phones,
// which is what made some bubbles come out oddly proportioned.
const BUBBLE_IMG_MAX_W = Math.min(250, Math.round(Dimensions.get("window").width * 0.6));
const BUBBLE_IMG_MAX_H = 320;
const BUBBLE_IMG_MIN_H = 120;

/** Chat-bubble photo: sized to the image's real aspect ratio (capped) instead
 * of a fixed 220×220 crop, so portraits/landscapes stop being cropped or
 * squished. Tapping opens it full-screen via onOpen. */
function MediaImage({ uri, onOpen }: { uri: string; onOpen: (uri: string) => void }) {
  const [ratio, setRatio] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    Image.getSize(
      uri,
      (w, h) => { if (!cancelled && w > 0 && h > 0) setRatio(w / h); },
      () => {}
    );
    return () => { cancelled = true; };
  }, [uri]);

  // Before the real ratio is known, hold a 4:3 box: reserving a squat strip
  // and then growing made the bubble visibly jump as each image resolved.
  const effectiveRatio = ratio ?? 4 / 3;
  const h = Math.min(BUBBLE_IMG_MAX_H, Math.max(BUBBLE_IMG_MIN_H, BUBBLE_IMG_MAX_W / effectiveRatio));
  const w = Math.min(BUBBLE_IMG_MAX_W, h * effectiveRatio);

  return (
    <TouchableOpacity activeOpacity={0.85} onPress={() => onOpen(uri)}>
      <Image source={{ uri }} style={{ width: w, height: h, borderRadius: 12, backgroundColor: C.panel2 }} resizeMode="cover" />
    </TouchableOpacity>
  );
}

function ImageViewerModal({ uri, onClose }: { uri: string | null; onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  useEffect(() => { if (uri) setLoading(true); }, [uri]);
  const { width, height } = Dimensions.get("window");
  return (
    <Modal visible={!!uri} transparent animationType="fade" onRequestClose={onClose}>
      <TouchableOpacity style={styles.viewerBackdrop} activeOpacity={1} onPress={onClose}>
        {!!uri && (
          <Image
            source={{ uri }}
            style={{ width, height: height * 0.85 }}
            resizeMode="contain"
            onLoadEnd={() => setLoading(false)}
          />
        )}
        {loading && <ActivityIndicator style={StyleSheet.absoluteFill} color={C.pri} size="large" />}
        <TouchableOpacity style={styles.viewerCloseBtn} onPress={onClose} hitSlop={12}>
          <Ionicons name="close" size={22} color={C.text} />
        </TouchableOpacity>
      </TouchableOpacity>
    </Modal>
  );
}

function MessageContent({ item, onOpenImage }: { item: WaMessage; onOpenImage: (uri: string) => void }) {
  if (item.hasMedia && item.mediaUrl) {
    const uri = mediaProxyUrl(item.mediaUrl, item.mimetype);
    // item.type (WhatsApp's own classification: "image"/"sticker"/"audio"/
    // "ptt"/"video"/"document") is the reliable discriminator — mimetype is
    // frequently null until the media has actually been downloaded once, so
    // it's only used here as a fallback for message types this app doesn't
    // special-case yet.
    const isSticker = item.type === "sticker";
    const isImage = !isSticker && (item.type === "image" || !!item.mimetype?.startsWith("image/"));
    const isAudio = item.type === "audio" || item.type === "ptt" || !!item.mimetype?.startsWith("audio/");

    if (isSticker) {
      // Stickers are small transparent WEBP art, not photos — cropping them
      // into a 220² square (like a photo) stretches and clips the art.
      return <Image source={{ uri }} style={styles.stickerImage} resizeMode="contain" />;
    }
    if (isImage) {
      return (
        <>
          <MediaImage uri={uri} onOpen={onOpenImage} />
          {!!item.body && <Text style={[styles.bubbleText, item.fromMe && { color: "#08101F" }]}>{item.body}</Text>}
        </>
      );
    }
    if (isAudio) {
      return <AudioBubbleContent uri={uri} fromMe={item.fromMe} />;
    }
    return (
      <View style={styles.fileRow}>
        <Ionicons name="document-attach" size={18} color={item.fromMe ? "#08101F" : C.pri} />
        <Text style={[styles.bubbleText, item.fromMe && { color: "#08101F" }]}>{item.fileName || "Archivo adjunto"}</Text>
      </View>
    );
  }
  const sys = bracketSystemInfo(item.body || "");
  if (sys) {
    return (
      <View style={styles.systemRow}>
        <Ionicons name={sys.icon as any} size={13} color={C.textMed} />
        <Text style={styles.systemText}>{sys.label}</Text>
      </View>
    );
  }
  return <Text style={[styles.bubbleText, item.fromMe && { color: "#08101F" }]}>{item.body || ""}</Text>;
}

/** WhatsApp's own tick ladder: one tick sent, two delivered, two blue read.
 * Wrapped in a fixed-size, overflow-hidden box — the icon font's own
 * line-height is much taller than the glyph itself, and styling the
 * Ionicons Text directly left a gap under the bubble text. */
function AckTicks({ ack, pending }: { ack?: number; pending?: boolean }) {
  if (pending) {
    return (
      <View style={styles.ticksBox}>
        <Ionicons name="time-outline" size={13} color="rgba(8,16,31,0.55)" />
      </View>
    );
  }
  if (ack === undefined) return null;
  const read = ack >= 3;
  return (
    <View style={styles.ticksBox}>
      <Ionicons
        name={ack >= 2 ? "checkmark-done" : "checkmark"}
        size={15}
        color={read ? "#4FC3F7" : "rgba(8,16,31,0.55)"}
      />
    </View>
  );
}

function ChatAvatar({ chatId, name, size = 44 }: { chatId: string; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const box = { width: size, height: size, borderRadius: size / 2 };
  if (failed) {
    return (
      <View style={[styles.avatar, box]}>
        <Text style={[styles.avatarText, { fontSize: size * 0.36 }]}>{initials(name)}</Text>
      </View>
    );
  }
  return (
    <Image
      source={{ uri: avatarProxyUrl(chatId) }}
      style={[styles.avatar, box]}
      onError={() => setFailed(true)}
    />
  );
}

export default function WhatsAppScreen() {
  const [chats, setChats] = useState<WaChat[]>([]);
  const [notReady, setNotReady] = useState(false);
  const [activeChat, setActiveChat] = useState<{ id: string; name: string; isGroup: boolean } | null>(null);
  const [messages, setMessages] = useState<WaMessage[]>([]);
  const [input, setInput] = useState("");
  const [viewerUri, setViewerUri] = useState<string | null>(null);
  // messageId -> translation ("" once we know it needs none, so a second tap
  // doesn't pay for the same call again).
  const [translations, setTranslations] = useState<Record<string, string>>({});
  const [translating, setTranslating] = useState<string | null>(null);
  // messageId -> transcript of a voice note.
  const [transcripts, setTranscripts] = useState<Record<string, string>>({});
  const [transcribing, setTranscribing] = useState<string | null>(null);
  // Delivery/read state of our own messages: 1 sent, 2 delivered, 3 read.
  const [acks, setAcks] = useState<Record<string, number>>({});
  const [suggesting, setSuggesting] = useState(false);
  const [sendingFile, setSendingFile] = useState(false);
  const [attachMenuOpen, setAttachMenuOpen] = useState(false);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [recording, setRecording] = useState(false);
  const activeChatRef = useRef(activeChat);
  activeChatRef.current = activeChat;
  const insets = useSafeAreaInsets();
  // FlatList `inverted` renders index 0 at the visual bottom — reverse so the
  // newest message (last in `messages`, chronological order) lands there.
  // This keeps the thread pinned to the latest message with zero manual
  // scrollToEnd calls, and (unlike scrollToEnd) reacts instantly to the
  // keyboard opening instead of lagging a frame behind it.
  const invertedMessages = useMemo(() => [...messages].reverse(), [messages]);
  // Optimistic sends not yet confirmed by the server's own message list —
  // kept appended to whatever loadMessages() fetches so a sent bubble shows
  // instantly instead of waiting for the next poll/round-trip.
  const pendingSendsRef = useRef<{ id: string; text: string }[]>([]);

  const loadChats = useCallback(async () => {
    if (activeChatRef.current) return;
    try {
      setChats(await api.getWhatsappChats());
      setNotReady(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) setNotReady(true);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      loadChats();
      const t = setInterval(loadChats, 4000);
      return () => clearInterval(t);
    }, [loadChats])
  );

  const loadMessages = useCallback(async (chatId: string) => {
    try {
      const msgs = await api.getWhatsappMessages(chatId);
      // Drop any pending optimistic send whose text now shows up server-side
      // (real message won) — keep the rest appended so they don't flicker
      // away while the bridge is still catching up.
      pendingSendsRef.current = pendingSendsRef.current.filter(
        (p) => !msgs.some((m) => m.fromMe && m.body === p.text)
      );
      const stillPending: WaMessage[] = pendingSendsRef.current.map((p) => ({
        id: p.id, body: p.text, fromMe: true,
      }));
      setMessages([...msgs, ...stillPending]);
    } catch {
      // transient
    }
  }, []);

  // Read receipts for our own messages. The bridge only reports them on
  // demand, so they are polled alongside the thread.
  useEffect(() => {
    if (!activeChat) return;
    const pull = async () => {
      const ids = messages.filter((m) => m.fromMe && !m.id.startsWith("pending-")).map((m) => m.id);
      if (!ids.length) return;
      try { setAcks(await api.getWhatsappAcks(ids.slice(-40))); } catch { /* transient */ }
    };
    pull();
    const t = setInterval(pull, 5000);
    return () => clearInterval(t);
  }, [activeChat, messages.length]);

  useEffect(() => {
    if (!activeChat) return;
    loadMessages(activeChat.id);
    const t = setInterval(() => loadMessages(activeChat.id), 3000);
    return () => clearInterval(t);
  }, [activeChat, loadMessages]);

  function openChat(chatId: string, name: string, isGroup: boolean) {
    setMessages([]);
    setTranslations({});
    setActiveChat({ id: chatId, name, isGroup });
    // Same as opening it on the desktop: reading it here should clear the
    // unread badge everywhere, including on WhatsApp itself.
    api.markWhatsappRead(chatId).catch(() => {});
    setChats((prev) => prev.map((c) => (c.chatId === chatId ? { ...c, unread: 0 } : c)));
  }

  async function transcribeMessage(item: WaMessage) {
    if (transcripts[item.id] !== undefined || transcribing === item.id) return;
    setTranscribing(item.id);
    try {
      const { text } = await api.transcribeWhatsappAudio(item);
      setTranscripts((prev) => ({ ...prev, [item.id]: text }));
    } catch (e) {
      Alert.alert("No se pudo transcribir", e instanceof Error ? e.message : "Error desconocido");
    }
    setTranscribing(null);
  }

  async function suggestReply() {
    if (!activeChat || suggesting) return;
    setSuggesting(true);
    try {
      // Seed it with the last message we received, which is what a reply
      // should actually answer.
      const lastIncoming = [...messages].reverse().find((m) => !m.fromMe);
      const { text } = await api.suggestWhatsappReply(activeChat.id, lastIncoming?.body || "");
      if (text) setInput(text);
    } catch (e) {
      Alert.alert("No se pudo sugerir", e instanceof Error ? e.message : "Error desconocido");
    }
    setSuggesting(false);
  }

  async function attachFile(fromGallery: boolean) {
    if (!activeChat || sendingFile) return;
    try {
      let uri = "", name = "", mime = "application/octet-stream";
      if (fromGallery) {
        const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (!perm.granted) return;
        const picked = await ImagePicker.launchImageLibraryAsync({ quality: 0.9 });
        if (picked.canceled || !picked.assets?.length) return;
        const asset = picked.assets[0];
        uri = asset.uri;
        name = asset.fileName || `imagen-${Date.now()}.jpg`;
        mime = asset.mimeType || "image/jpeg";
      } else {
        const picked = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true });
        if (picked.canceled || !picked.assets?.length) return;
        const asset = picked.assets[0];
        uri = asset.uri;
        name = asset.name || `archivo-${Date.now()}`;
        mime = asset.mimeType || "application/octet-stream";
      }
      setSendingFile(true);
      await api.sendWhatsappMedia(activeChat.id, uri, name, mime);
      await loadMessages(activeChat.id);
    } catch (e) {
      Alert.alert("No se pudo enviar", e instanceof Error ? e.message : "Error desconocido");
    }
    setSendingFile(false);
  }

  async function toggleRecording() {
    if (!activeChat) return;
    if (recording) {
      setRecording(false);
      try {
        await recorder.stop();
        const uri = recorder.uri;
        if (uri) {
          setSendingFile(true);
          await api.sendWhatsappMedia(activeChat.id, uri, `audio-${Date.now()}.m4a`, "audio/m4a");
          await loadMessages(activeChat.id);
        }
      } catch (e) {
        Alert.alert("No se pudo enviar el audio", e instanceof Error ? e.message : "Error desconocido");
      }
      setSendingFile(false);
      await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true }).catch(() => {});
      return;
    }
    const perm = await requestRecordingPermissionsAsync();
    if (!perm.granted) {
      Alert.alert("Sin permiso de micrófono");
      return;
    }
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true }).catch(() => {});
    await recorder.prepareToRecordAsync();
    recorder.record();
    setRecording(true);
  }

  async function translateMessage(item: WaMessage) {
    if (translations[item.id] !== undefined || translating === item.id) return;
    const body = (item.body || "").trim();
    if (!body) return;
    setTranslating(item.id);
    try {
      const { text } = await api.translateWhatsapp(body);
      // The helper returns "" when the text is already in Spanish; store that
      // so the row shows "no hace falta" instead of asking again.
      setTranslations((prev) => ({ ...prev, [item.id]: text }));
    } catch {
      setTranslating(null);
      return;
    }
    setTranslating(null);
  }
  function back() {
    setActiveChat(null);
    loadChats();
  }
  async function send() {
    const text = input.trim();
    if (!text || !activeChat) return;
    setInput("");
    const tempId = `pending-${Date.now()}`;
    pendingSendsRef.current.push({ id: tempId, text });
    setMessages((prev) => [...prev, { id: tempId, body: text, fromMe: true }]);
    try {
      await api.sendWhatsapp(activeChat.id, text);
      setTimeout(() => loadMessages(activeChat.id), 600);
    } catch {
      // transient — the bubble stays; next successful poll will reconcile it
    }
  }

  if (activeChat) {
    return (
      <KeyboardAvoidingView
        style={[styles.container, { paddingTop: insets.top }]}
        behavior={Platform.OS === "ios" ? "padding" : "height"}
      >
        <View style={styles.threadHeader}>
          <TouchableOpacity style={styles.backBtn} onPress={back}>
            <Ionicons name="chevron-back" size={18} color={C.pri} />
          </TouchableOpacity>
          <ChatAvatar chatId={activeChat.id} name={activeChat.name} size={34} />
          <Text style={styles.threadName} numberOfLines={1}>{activeChat.name}</Text>
        </View>
        <FlatList
          style={{ flex: 1 }}
          inverted
          contentContainerStyle={{ padding: spacing.lg, gap: spacing.sm }}
          data={invertedMessages}
          keyExtractor={(m) => m.id}
          renderItem={({ item }) => {
            const isSticker = item.hasMedia && item.type === "sticker";
            const isSystem = !item.hasMedia && !!bracketSystemInfo(item.body || "");
            if (isSystem) {
              return (
                <View style={styles.systemBubble}>
                  <MessageContent item={item} onOpenImage={setViewerUri} />
                </View>
              );
            }
            const translation = translations[item.id];
            const canTranslate = !item.fromMe && !!(item.body || "").trim() && !item.hasMedia;
            const isVoice = !!item.hasMedia && (item.type === "ptt" || item.type === "audio");
            return (
              <TouchableOpacity
                activeOpacity={canTranslate ? 0.75 : 1}
                // Long-press rather than tap: a tap on a bubble shouldn't spend
                // an AI call, and images already use tap to open the viewer.
                onLongPress={canTranslate ? () => translateMessage(item) : undefined}
                delayLongPress={350}
                style={[
                  styles.bubble,
                  item.fromMe ? styles.bubbleYou : styles.bubbleThem,
                  isSticker && styles.bubbleSticker,
                ]}
              >
                {!item.fromMe && activeChat.isGroup && !!(item.authorName || item.senderName) && (
                  <Text style={styles.bubbleMeta}>{item.authorName || item.senderName}</Text>
                )}
                <MessageContent item={item} onOpenImage={setViewerUri} />
                {isVoice && (
                  <TouchableOpacity
                    style={styles.inlineAction}
                    onPress={() => transcribeMessage(item)}
                    disabled={transcribing === item.id}
                  >
                    <Ionicons name="text" size={13} color={item.fromMe ? "#08101F" : C.pri} />
                    <Text style={[styles.inlineActionText, item.fromMe && { color: "#08101F" }]}>
                      {transcribing === item.id ? "Transcribiendo…" : "Transcribir"}
                    </Text>
                  </TouchableOpacity>
                )}
                {transcripts[item.id] !== undefined && (
                  <View style={styles.translationBox}>
                    <Text style={styles.translationLabel}>TRANSCRIPCIÓN</Text>
                    <Text style={styles.translationText}>
                      {transcripts[item.id] || "No se entendió nada en el audio."}
                    </Text>
                  </View>
                )}
                {item.fromMe && <AckTicks ack={acks[item.id]} pending={item.id.startsWith("pending-")} />}
                {translating === item.id && (
                  <Text style={styles.translationHint}>Traduciendo…</Text>
                )}
                {translation !== undefined && (
                  <View style={styles.translationBox}>
                    <Text style={styles.translationLabel}>TRADUCCIÓN</Text>
                    <Text style={styles.translationText}>
                      {translation || "Ya está en español."}
                    </Text>
                  </View>
                )}
              </TouchableOpacity>
            );
          }}
        />
        <View style={styles.composer}>
          <TouchableOpacity
            style={styles.plusBtn}
            onPress={() => setAttachMenuOpen(!attachMenuOpen)}
            disabled={sendingFile}
          >
            <Ionicons name="add" size={20} color={C.pri} />
          </TouchableOpacity>
          <View style={styles.inputWrap}>
            <TextInput
              style={styles.input}
              value={input}
              onChangeText={setInput}
              placeholder="Escribe una respuesta…"
              placeholderTextColor={C.textMed}
              cursorColor={C.pri}
              selectionColor={C.pri}
              keyboardAppearance="dark"
              multiline
            />
          </View>
          <TouchableOpacity
            style={styles.suggestBtn}
            onPress={suggestReply}
            disabled={suggesting}
          >
            {suggesting
              ? <ActivityIndicator size="small" color={C.pri} />
              : <Ionicons name="sparkles" size={17} color={C.pri} />}
          </TouchableOpacity>
          <TouchableOpacity style={styles.sendBtn} onPress={send}>
            <Ionicons name="send" size={17} color="#08101F" />
          </TouchableOpacity>
        </View>
        {attachMenuOpen && (
          <View style={styles.attachMenu}>
            <TouchableOpacity style={styles.menuItem} onPress={() => { attachFile(true); setAttachMenuOpen(false); }} disabled={sendingFile}>
              <Ionicons name="image" size={18} color={C.pri} />
              <Text style={styles.menuItemText}>Foto</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.menuItem} onPress={() => { attachFile(false); setAttachMenuOpen(false); }} disabled={sendingFile}>
              <Ionicons name="document" size={18} color={C.pri} />
              <Text style={styles.menuItemText}>Archivo</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.menuItem, recording && styles.menuItemActive]}
              onPress={() => { toggleRecording(); if (recording) setAttachMenuOpen(false); }}
              disabled={sendingFile}
            >
              <Ionicons name={recording ? "stop" : "mic"} size={18} color={recording ? "#08101F" : C.pri} />
              <Text style={[styles.menuItemText, recording && { color: "#08101F" }]}>
                {recording ? "Detener" : "Audio"}
              </Text>
            </TouchableOpacity>
          </View>
        )}
        <ImageViewerModal uri={viewerUri} onClose={() => setViewerUri(null)} />
      </KeyboardAvoidingView>
    );
  }

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <FlatList
        contentContainerStyle={{ padding: spacing.lg }}
        data={chats}
        keyExtractor={(c) => c.chatId}
        ListEmptyComponent={
          <Text style={styles.emptyHint}>
            {notReady
              ? "WhatsApp no está emparejado todavía. Empareja el bridge desde el escritorio."
              : "No hay chats recientes."}
          </Text>
        }
        renderItem={({ item }) => (
          <TouchableOpacity style={styles.chatRow} onPress={() => openChat(item.chatId, item.name || item.chatId, item.isGroup)}>
            <ChatAvatar chatId={item.chatId} name={item.name} />
            <View style={{ flex: 1 }}>
              <View style={styles.chatTop}>
                <Text style={styles.chatName} numberOfLines={1}>{item.name || item.chatId}</Text>
                <Text style={styles.chatTime}>{fmtTime(item.timestamp)}</Text>
              </View>
              <Text style={styles.chatPreview} numberOfLines={1}>
                {(item.fromMe ? "Tú: " : "") + (item.preview || "")}
              </Text>
            </View>
            {!!item.unread && (
              <View style={styles.badge}><Text style={styles.badgeText}>{item.unread}</Text></View>
            )}
          </TouchableOpacity>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  emptyHint: { color: C.textMed, fontSize: 12.5, textAlign: "center", padding: 24, lineHeight: 19 },
  chatRow: { flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 11 },
  avatar: { width: 44, height: 44, borderRadius: 22, backgroundColor: C.panel2, alignItems: "center", justifyContent: "center" },
  avatarText: { color: C.pri, fontWeight: "800", fontSize: 16 },
  chatTop: { flexDirection: "row", justifyContent: "space-between", alignItems: "baseline", gap: 8 },
  chatName: { color: C.text, fontSize: 14, fontWeight: "700", flexShrink: 1 },
  chatTime: { color: C.textMed, fontSize: 10.5 },
  chatPreview: { color: C.textMed, fontSize: 12.5, marginTop: 2 },
  badge: { backgroundColor: C.green, borderRadius: 10, paddingHorizontal: 7, paddingVertical: 1, marginLeft: 6 },
  badgeText: { color: "#08101F", fontSize: 10.5, fontWeight: "800" },
  threadHeader: { flexDirection: "row", alignItems: "center", gap: 10, padding: spacing.md, borderBottomWidth: 1, borderBottomColor: C.borderA, backgroundColor: C.panel },
  backBtn: { width: 34, height: 34, borderRadius: 17, backgroundColor: "rgba(255,255,255,0.035)", borderWidth: 1, borderColor: "rgba(255,255,255,0.09)", alignItems: "center", justifyContent: "center" },
  threadName: { color: C.text, fontSize: 14.5, fontWeight: "700", flex: 1 },
  bubble: { maxWidth: "86%", minWidth: 72, padding: 12, borderRadius: 17 },
  bubbleYou: { alignSelf: "flex-end", backgroundColor: C.priDim, borderBottomRightRadius: 4 },
  bubbleThem: { alignSelf: "flex-start", backgroundColor: C.panel2, borderWidth: 1, borderColor: C.borderA, borderBottomLeftRadius: 4 },
  bubbleSticker: { backgroundColor: "transparent", borderWidth: 0, padding: 0 },
  bubbleMeta: { color: C.pri, fontSize: 10, fontWeight: "700", marginBottom: 3, textTransform: "uppercase" },
  bubbleText: { color: C.text, fontSize: 14.5, lineHeight: 20 },
  viewerBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.92)", alignItems: "center", justifyContent: "center" },
  viewerCloseBtn: {
    position: "absolute", top: 50, right: 20, width: 40, height: 40, borderRadius: 20,
    backgroundColor: "rgba(255,255,255,0.12)", alignItems: "center", justifyContent: "center",
  },
  stickerImage: { width: 120, height: 120 },
  audioRow: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 2 },
  audioLabel: { color: C.text, fontSize: 13.5, fontWeight: "600" },
  fileRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  ticksBox: {
    alignSelf: "flex-end", marginTop: 3, height: 15, width: 18,
    alignItems: "center", justifyContent: "center", overflow: "hidden",
  },
  inlineAction: { flexDirection: "row", alignItems: "center", gap: 5, marginTop: 6 },
  inlineActionText: { color: C.pri, fontSize: 11.5, fontWeight: "700" },
  composerActions: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.md, paddingTop: 6 },
  actionChip: { flexDirection: "row", alignItems: "center", gap: 5, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: 14, paddingVertical: 6, paddingHorizontal: 11 },
  actionChipText: { color: C.pri, fontSize: 11.5, fontWeight: "700" },
  translationHint: { color: C.textMed, fontSize: 11, fontStyle: "italic", marginTop: 6 },
  translationBox: { marginTop: 8, paddingTop: 7, borderTopWidth: 1, borderTopColor: "rgba(255,255,255,0.13)" },
  translationLabel: { color: C.acc2, fontSize: 9, fontWeight: "800", letterSpacing: 0.6, marginBottom: 2 },
  translationText: { color: C.textDim, fontSize: 13.5, lineHeight: 18.5 },
  systemBubble: { alignSelf: "center", maxWidth: "90%" },
  systemRow: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "rgba(255,255,255,0.05)", borderWidth: 1, borderColor: C.borderA, borderRadius: 12, paddingVertical: 6, paddingHorizontal: 12 },
  systemText: { color: C.textMed, fontSize: 11.5, fontStyle: "italic" },
  composer: { flexDirection: "row", alignItems: "center", gap: spacing.sm, padding: spacing.md, borderTopWidth: 1, borderTopColor: C.borderA },
  inputWrap: {
    flex: 1, minHeight: 46, maxHeight: 100, backgroundColor: C.panel, borderWidth: 1, borderColor: C.border,
    borderRadius: 22, flexDirection: "row", alignItems: "center", paddingHorizontal: 12,
  },
  input: {
    flex: 1, color: C.text, paddingVertical: Platform.OS === "android" ? 6 : 12, fontSize: 15,
    maxHeight: 90, textAlignVertical: "center",
  },
  plusBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.35)", alignItems: "center", justifyContent: "center" },
  suggestBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.35)", alignItems: "center", justifyContent: "center" },
  sendBtn: { width: 46, height: 46, borderRadius: 23, backgroundColor: C.priDim, alignItems: "center", justifyContent: "center" },
  attachMenu: { flexDirection: "row", gap: 8, paddingHorizontal: spacing.md, paddingBottom: spacing.md, backgroundColor: C.panel, borderTopWidth: 1, borderTopColor: C.borderA },
  menuItem: { flex: 1, flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: "rgba(182,196,255,0.1)", borderRadius: 12, paddingVertical: 10, paddingHorizontal: 12 },
  menuItemActive: { backgroundColor: C.red },
  menuItemText: { color: C.pri, fontSize: 12, fontWeight: "600" },
});
