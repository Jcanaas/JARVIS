import React, { useCallback, useState } from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, TextInput,
  ActivityIndicator, Alert, KeyboardAvoidingView, Platform,
} from "react-native";
import * as Clipboard from "expo-clipboard";
import { useFocusEffect } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { C, spacing, radius } from "../theme";
import { api } from "../api";

type IconName = React.ComponentProps<typeof Ionicons>["name"];

export default function RemoteScreen() {
  const insets = useSafeAreaInsets();
  const [clipText, setClipText] = useState("");
  const [pcClip, setPcClip] = useState("");
  const [busy, setBusy] = useState("");
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [appName, setAppName] = useState("");

  const loadStatus = useCallback(async () => {
    try { setStatus(await api.getRemoteStatus()); } catch { /* transient */ }
  }, []);

  useFocusEffect(useCallback(() => { loadStatus(); }, [loadStatus]));

  async function run(action: string, params?: Record<string, unknown>) {
    setBusy(action);
    try {
      await api.remoteAction(action, params);
    } catch (e) {
      Alert.alert("No se pudo", e instanceof Error ? e.message : "Error desconocido");
    }
    setBusy("");
  }

  /** Destructive and hard to undo from the sofa — always confirm. */
  function confirmRun(action: string, title: string, message: string) {
    Alert.alert(title, message, [
      { text: "Cancelar", style: "cancel" },
      { text: "Continuar", style: "destructive", onPress: () => run(action) },
    ]);
  }

  async function pullFromPc() {
    setBusy("pull");
    try {
      const { text } = await api.getClipboard();
      setPcClip(text);
      await Clipboard.setStringAsync(text);
    } catch (e) {
      Alert.alert("No se pudo leer", e instanceof Error ? e.message : "Error desconocido");
    }
    setBusy("");
  }

  async function pushToPc() {
    const text = clipText.trim();
    if (!text) return;
    setBusy("push");
    try {
      await api.setClipboard(text);
      setPcClip(text);
    } catch (e) {
      Alert.alert("No se pudo enviar", e instanceof Error ? e.message : "Error desconocido");
    }
    setBusy("");
  }

  async function pasteFromPhone() {
    const text = await Clipboard.getStringAsync().catch(() => "");
    if (text) setClipText(text);
  }

  const disk = status?.home_disk;

  return (
    <KeyboardAvoidingView
      style={styles.container}
      behavior={Platform.OS === "ios" ? "padding" : "height"}
    >
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingTop: insets.top + spacing.lg, gap: spacing.md }}>
        <View style={styles.card}>
          <Text style={styles.cardTitle}>PORTAPAPELES</Text>
          <Text style={styles.sub}>Pasa texto y enlaces entre el móvil y el PC.</Text>

          <TextInput
            style={styles.input}
            value={clipText}
            onChangeText={setClipText}
            placeholder="Texto para enviar al PC…"
            placeholderTextColor={C.textMed}
            cursorColor={C.pri}
            selectionColor={C.pri}
            keyboardAppearance="dark"
            multiline
          />
          <View style={styles.btnRow}>
            <TouchableOpacity style={styles.ghostBtn} onPress={pasteFromPhone}>
              <Ionicons name="clipboard-outline" size={15} color={C.pri} />
              <Text style={styles.ghostText}>Pegar del móvil</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.primaryBtn, (!clipText.trim() || busy === "push") && { opacity: 0.5 }]}
              onPress={pushToPc}
              disabled={!clipText.trim() || busy === "push"}
            >
              <Ionicons name="arrow-up" size={15} color="#08101F" />
              <Text style={styles.primaryText}>Enviar al PC</Text>
            </TouchableOpacity>
          </View>

          <TouchableOpacity
            style={[styles.ghostBtn, { marginTop: 8 }]}
            onPress={pullFromPc}
            disabled={busy === "pull"}
          >
            {busy === "pull" ? (
              <ActivityIndicator color={C.pri} size="small" />
            ) : (
              <Ionicons name="arrow-down" size={15} color={C.pri} />
            )}
            <Text style={styles.ghostText}>Traer del PC y copiar aquí</Text>
          </TouchableOpacity>

          {!!pcClip && (
            <View style={styles.clipPreview}>
              <Text style={styles.clipLabel}>EN EL PC</Text>
              <Text style={styles.clipText} numberOfLines={6}>{pcClip}</Text>
            </View>
          )}
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>VOLUMEN</Text>
          <View style={styles.btnRow}>
            <RemoteBtn icon="volume-low" label="Bajar" onPress={() => run("volume_down")} busy={busy === "volume_down"} />
            <RemoteBtn icon="volume-mute" label="Silencio" onPress={() => run("volume_mute")} busy={busy === "volume_mute"} />
            <RemoteBtn icon="volume-high" label="Subir" onPress={() => run("volume_up")} busy={busy === "volume_up"} />
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>PANTALLA Y SISTEMA</Text>
          <View style={styles.btnRow}>
            <RemoteBtn icon="lock-closed" label="Bloquear" onPress={() => run("lock_screen")} busy={busy === "lock_screen"} />
            <RemoteBtn icon="moon" label="Apagar pantalla" onPress={() => run("sleep_display")} busy={busy === "sleep_display"} />
            <RemoteBtn icon="albums" label="Escritorio" onPress={() => run("show_desktop")} busy={busy === "show_desktop"} />
          </View>
        </View>

        <View style={styles.card}>
          <Text style={styles.cardTitle}>ABRIR APLICACIÓN</Text>
          <View style={styles.btnRow}>
            <TextInput
              style={[styles.input, { flex: 1, marginBottom: 0 }]}
              value={appName}
              onChangeText={setAppName}
              placeholder="spotify, chrome, steam…"
              placeholderTextColor={C.textMed}
              cursorColor={C.pri}
              selectionColor={C.pri}
              keyboardAppearance="dark"
              autoCapitalize="none"
            />
            <TouchableOpacity
              style={[styles.primaryBtn, !appName.trim() && { opacity: 0.5 }]}
              onPress={() => { run("app_launch", { name: appName.trim() }); setAppName(""); }}
              disabled={!appName.trim()}
            >
              <Ionicons name="open-outline" size={15} color="#08101F" />
              <Text style={styles.primaryText}>Abrir</Text>
            </TouchableOpacity>
          </View>
        </View>

        {!!status && (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>ESTADO DEL PC</Text>
            <View style={styles.kv}>
              <Text style={styles.k}>Sistema</Text>
              <Text style={styles.v}>{status.os}</Text>
            </View>
            {!!disk && (
              <View style={styles.kv}>
                <Text style={styles.k}>Disco</Text>
                <Text style={styles.v}>{disk.free} libres de {disk.total}</Text>
              </View>
            )}
          </View>
        )}

        <View style={styles.card}>
          <Text style={styles.cardTitle}>APAGADO</Text>
          <Text style={styles.sub}>
            Cuidado: si apagas el PC, la app pierde la conexión y JARVIS deja de responder.
          </Text>
          <TouchableOpacity
            style={styles.dangerBtn}
            onPress={() => confirmRun("restart_computer", "Reiniciar el PC", "Se cerrará todo lo que tengas abierto. ¿Seguro?")}
          >
            <Text style={styles.dangerText}>Reiniciar</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.dangerBtn, { marginTop: 8 }]}
            onPress={() => confirmRun("shutdown_computer", "Apagar el PC", "Se apagará el ordenador y perderás la conexión con JARVIS. ¿Seguro?")}
          >
            <Text style={styles.dangerText}>Apagar</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function RemoteBtn({
  icon, label, onPress, busy,
}: { icon: IconName; label: string; onPress: () => void; busy: boolean }) {
  return (
    <TouchableOpacity style={styles.tile} onPress={onPress} disabled={busy} activeOpacity={0.7}>
      {busy ? <ActivityIndicator color={C.pri} size="small" /> : <Ionicons name={icon} size={20} color={C.pri} />}
      <Text style={styles.tileText} numberOfLines={1}>{label}</Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: radius.lg, padding: 16 },
  cardTitle: { color: C.acc2, fontSize: 11, fontWeight: "800", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 8 },
  sub: { color: C.textMed, fontSize: 11.5, lineHeight: 17, marginBottom: 10 },
  input: { backgroundColor: C.panel2, color: C.text, borderWidth: 1, borderColor: C.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 11, fontSize: 14, marginBottom: spacing.sm, minHeight: 44 },
  btnRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  ghostBtn: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.35)", borderRadius: 12, paddingVertical: 11 },
  ghostText: { color: C.pri, fontWeight: "700", fontSize: 12.5 },
  primaryBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, backgroundColor: C.pri, borderRadius: 12, paddingVertical: 11, paddingHorizontal: 16 },
  primaryText: { color: "#08101F", fontWeight: "800", fontSize: 12.5 },
  clipPreview: { marginTop: 12, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.borderA, borderRadius: 12, padding: 11 },
  clipLabel: { color: C.textMed, fontSize: 9.5, fontWeight: "800", letterSpacing: 0.5, marginBottom: 4 },
  clipText: { color: C.text, fontSize: 13, lineHeight: 18 },
  tile: { flex: 1, alignItems: "center", justifyContent: "center", gap: 6, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.borderA, borderRadius: 12, paddingVertical: 14 },
  tileText: { color: C.textDim, fontSize: 11, fontWeight: "600" },
  kv: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 7, borderTopWidth: 1, borderTopColor: C.borderA, gap: 10 },
  k: { color: C.textMed, fontSize: 12.5 },
  v: { color: C.text, fontSize: 12.5, fontWeight: "600", flexShrink: 1, textAlign: "right" },
  dangerBtn: { backgroundColor: "rgba(255,94,130,0.10)", borderWidth: 1, borderColor: "rgba(255,94,130,0.3)", borderRadius: 12, paddingVertical: 11, alignItems: "center" },
  dangerText: { color: C.red, fontWeight: "700", fontSize: 13 },
});
