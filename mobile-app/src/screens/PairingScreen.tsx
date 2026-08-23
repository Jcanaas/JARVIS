import React, { useState } from "react";
import { View, Text, StyleSheet, ActivityIndicator, TouchableOpacity } from "react-native";
import { CameraView, useCameraPermissions, BarcodeScanningResult } from "expo-camera";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { C, spacing, radius } from "../theme";
import { parseConnectionUrl, saveConnection } from "../storage";
import { configureApi, probeServer } from "../api";

export default function PairingScreen({ onPaired }: { onPaired: () => void }) {
  const [permission, requestPermission] = useCameraPermissions();
  const [scanning, setScanning] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleScan(result: BarcodeScanningResult) {
    if (!scanning || busy) return;
    setScanning(false);
    setBusy(true);
    setError(null);

    const conn = parseConnectionUrl(result.data);
    if (!conn) {
      setError("Ese código QR no parece ser de JARVIS. Escanea el de Ajustes → Dashboard remoto.");
      setBusy(false);
      setScanning(true);
      return;
    }

    const probe = await probeServer(conn);
    if (!probe.ok) {
      setError(
        probe.reason === "rejected"
          ? `El PC respondió pero rechazó el código (HTTP ${probe.status}). ` +
            "El token del QR ha cambiado: vuelve a abrir Ajustes → Dashboard remoto en el PC y escanea el nuevo."
          : `No hay respuesta de ${conn.baseUrl}. Comprueba que JARVIS está abierto en el PC ` +
            "y que el móvil está en la misma red WiFi (no en datos móviles ni en una red de invitados).",
      );
      setBusy(false);
      setScanning(true);
      return;
    }

    configureApi(conn);
    await saveConnection(conn);
    setBusy(false);
    onPaired();
  }

  if (!permission) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={C.pri} />
      </View>
    );
  }

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.title}>Necesito la cámara</Text>
        <Text style={styles.hint}>Para escanear el código QR del panel de JARVIS.</Text>
        <TouchableOpacity style={styles.btn} onPress={requestPermission}>
          <Text style={styles.btnText}>Dar permiso</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const insets = useSafeAreaInsets();

  return (
    <View style={styles.container}>
      <CameraView
        style={StyleSheet.absoluteFill}
        facing="back"
        barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
        onBarcodeScanned={scanning ? handleScan : undefined}
      />
      <View style={[styles.overlay, { paddingTop: insets.top + spacing.lg, paddingBottom: insets.bottom + 40 }]}>
        <View style={styles.frame} />
        <Text style={styles.title}>Escanea el QR de JARVIS</Text>
        <Text style={styles.hint}>Ajustes → Inicio → Dashboard remoto → Mostrar código QR</Text>
        {busy && <ActivityIndicator color={C.pri} style={{ marginTop: spacing.md }} />}
        {error && <Text style={styles.error}>{error}</Text>}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  center: { flex: 1, backgroundColor: C.bg, alignItems: "center", justifyContent: "center", padding: spacing.xl },
  overlay: {
    flex: 1,
    alignItems: "center",
    justifyContent: "flex-end",
    paddingBottom: 80,
    paddingHorizontal: spacing.xl,
  },
  frame: {
    position: "absolute",
    top: "28%",
    width: 240,
    height: 240,
    borderRadius: radius.md,
    borderWidth: 2,
    borderColor: C.pri,
  },
  title: { color: C.text, fontSize: 17, fontWeight: "800", textAlign: "center", marginBottom: spacing.xs },
  hint: { color: C.textMed, fontSize: 13, textAlign: "center", lineHeight: 19 },
  error: { color: C.red, fontSize: 13, textAlign: "center", marginTop: spacing.md, paddingHorizontal: spacing.md },
  btn: {
    marginTop: spacing.lg,
    backgroundColor: C.priGho,
    borderWidth: 1,
    borderColor: "rgba(182,196,255,0.35)",
    borderRadius: radius.md,
    paddingVertical: 12,
    paddingHorizontal: 24,
  },
  btnText: { color: C.pri, fontWeight: "700", fontSize: 14 },
});
