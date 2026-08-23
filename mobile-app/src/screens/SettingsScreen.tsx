import React, { useEffect, useState } from "react";
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { C, spacing, radius } from "../theme";
import { clearConnection, loadConnection } from "../storage";
import { pingServer } from "../api";
import WhatsAppAutomations from "../components/WhatsAppAutomations";

export default function SettingsScreen({ onForget }: { onForget: () => void }) {
  const [baseUrl, setBaseUrl] = useState("—");
  const [connected, setConnected] = useState<boolean | null>(null);

  useEffect(() => {
    (async () => {
      const conn = await loadConnection();
      if (conn) {
        setBaseUrl(conn.baseUrl);
        setConnected(await pingServer(conn));
      }
    })();
  }, []);

  async function forget() {
    await clearConnection();
    onForget();
  }

  const insets = useSafeAreaInsets();

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={{ padding: spacing.lg, paddingTop: insets.top + spacing.lg, gap: spacing.md }}
    >
      <View style={styles.card}>
        <Text style={styles.cardTitle}>CONEXIÓN</Text>
        <View style={styles.kv}>
          <Text style={styles.k}>Servidor</Text>
          <Text style={styles.v}>{baseUrl}</Text>
        </View>
        <View style={styles.kv}>
          <Text style={styles.k}>Estado</Text>
          <Text style={[styles.v, connected === false && { color: C.red }]}>
            {connected === null ? "…" : connected ? "Conectado" : "Sin respuesta"}
          </Text>
        </View>
        <TouchableOpacity style={styles.dangerBtn} onPress={forget}>
          <Text style={styles.dangerBtnText}>Olvidar conexión y re-emparejar</Text>
        </TouchableOpacity>
      </View>

      <WhatsAppAutomations />

      <View style={styles.card}>
        <Text style={styles.cardTitle}>ACERCA DE</Text>
        <Text style={styles.p}>
          App móvil de JARVIS. Se conecta al panel remoto solo-LAN de tu PC — funciona
          únicamente dentro de tu red WiFi, mientras el token siga siendo válido.
        </Text>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: radius.lg, padding: 16 },
  cardTitle: { color: C.acc2, fontSize: 11, fontWeight: "800", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 10 },
  kv: { flexDirection: "row", justifyContent: "space-between", paddingVertical: 7, borderTopWidth: 1, borderTopColor: C.borderA },
  k: { color: C.textMed, fontSize: 13 },
  v: { color: C.text, fontSize: 13, fontWeight: "600" },
  p: { color: C.textMed, fontSize: 12.5, lineHeight: 19 },
  dangerBtn: { marginTop: 12, backgroundColor: "rgba(255,94,130,0.10)", borderWidth: 1, borderColor: "rgba(255,94,130,0.3)", borderRadius: 12, paddingVertical: 11, alignItems: "center" },
  dangerBtnText: { color: C.red, fontWeight: "700", fontSize: 13.5 },
});
