import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, Switch, Modal, TextInput,
  ScrollView, FlatList, ActivityIndicator, Alert, KeyboardAvoidingView, Platform,
} from "react-native";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { C, spacing, radius } from "../theme";
import { api, WaAutomations, WaChat, WaRule, WaRuleContact } from "../api";

const DAY_LABELS = ["L", "M", "X", "J", "V", "S", "D"];

function emptyRule(): WaRule {
  return {
    name: "",
    enabled: true,
    contacts: [],
    always: false,
    days: [0, 1, 2, 3, 4],
    start: "09:00",
    end: "18:00",
    prompt: "",
  };
}

function isValidTime(t: string): boolean {
  const m = t.match(/^(\d{1,2}):(\d{2})$/);
  return !!m && Number(m[1]) <= 23 && Number(m[2]) <= 59;
}

function scheduleSummary(rule: WaRule): string {
  if (rule.always) return "Siempre activa";
  const days = rule.days.length === 7 ? "Todos los días" : rule.days.map((d) => DAY_LABELS[d]).join(" ");
  return `${days || "Sin días"} · ${rule.start}–${rule.end}`;
}

export default function WhatsAppAutomations() {
  const [data, setData] = useState<WaAutomations | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<WaRule | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api.getWhatsappAutomations());
    } catch (e) {
      setError(e instanceof Error ? e.message : "No se pudieron cargar las automatizaciones.");
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  async function toggleSetting(key: "whatsapp_auto_translate" | "whatsapp_auto_transcribe", value: boolean) {
    // Optimistic: the switch must not lag a round-trip behind the finger.
    setData((d) => (d ? { ...d, settings: { ...d.settings, [key]: value } } : d));
    try {
      await api.setWhatsappAutomationSetting(key, value);
    } catch {
      load(); // reconcile against whatever the desktop actually stored
    }
  }

  async function toggleRule(rule: WaRule, enabled: boolean) {
    try {
      await api.saveWhatsappRule({ ...rule, enabled });
      await load();
    } catch (e) {
      Alert.alert("No se pudo guardar", e instanceof Error ? e.message : "Error desconocido");
    }
  }

  async function moveRule(rule: WaRule, delta: number) {
    if (!rule.id) return;
    try {
      await api.moveWhatsappRule(rule.id, delta);
      await load();
    } catch { /* transient */ }
  }

  function confirmDelete(rule: WaRule) {
    if (!rule.id) return;
    Alert.alert(
      "Eliminar regla",
      `¿Eliminar "${rule.name}"? Esta acción no se puede deshacer.`,
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Eliminar",
          style: "destructive",
          onPress: async () => {
            try {
              await api.deleteWhatsappRule(rule.id!);
              await load();
            } catch (e) {
              Alert.alert("No se pudo eliminar", e instanceof Error ? e.message : "Error desconocido");
            }
          },
        },
      ],
    );
  }

  return (
    <View style={styles.card}>
      <View style={styles.cardHeader}>
        <Text style={styles.cardTitle}>AUTOMATIZACIONES DE WHATSAPP</Text>
        {loading && <ActivityIndicator color={C.pri} size="small" />}
      </View>

      {error ? <Text style={styles.hint}>{error}</Text> : null}

      <View style={styles.row}>
        <View style={{ flex: 1 }}>
          <Text style={styles.rowTitle}>Traducir automáticamente</Text>
          <Text style={styles.rowSub}>Traduce los mensajes entrantes en otro idioma.</Text>
        </View>
        <Switch
          value={!!data?.settings.whatsapp_auto_translate}
          onValueChange={(v) => toggleSetting("whatsapp_auto_translate", v)}
          trackColor={{ false: C.panel2, true: C.priDim }}
          thumbColor={C.text}
          disabled={!data}
        />
      </View>

      <View style={styles.row}>
        <View style={{ flex: 1 }}>
          <Text style={styles.rowTitle}>Transcribir notas de voz</Text>
          <Text style={styles.rowSub}>Convierte los audios recibidos en texto.</Text>
        </View>
        <Switch
          value={!!data?.settings.whatsapp_auto_transcribe}
          onValueChange={(v) => toggleSetting("whatsapp_auto_transcribe", v)}
          trackColor={{ false: C.panel2, true: C.priDim }}
          thumbColor={C.text}
          disabled={!data}
        />
      </View>

      <View style={styles.divider} />
      <Text style={styles.sectionLabel}>
        Respuestas automáticas{data?.rules.length ? ` · ${data.rules.length}` : ""}
      </Text>
      <Text style={styles.rowSub}>
        JARVIS responde solo a los contactos elegidos, dentro del horario indicado.
        Si varias reglas coinciden, gana la primera de la lista.
      </Text>

      {data?.rules.length === 0 && <Text style={styles.hint}>No hay reglas todavía.</Text>}

      {data?.rules.map((rule, i) => (
        <View key={rule.id || i} style={styles.ruleRow}>
          <View style={styles.orderCol}>
            <TouchableOpacity onPress={() => moveRule(rule, -1)} disabled={i === 0} hitSlop={6}>
              <Ionicons name="chevron-up" size={15} color={i === 0 ? C.borderA : C.textMed} />
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => moveRule(rule, 1)}
              disabled={i === (data?.rules.length ?? 0) - 1}
              hitSlop={6}
            >
              <Ionicons
                name="chevron-down"
                size={15}
                color={i === (data?.rules.length ?? 0) - 1 ? C.borderA : C.textMed}
              />
            </TouchableOpacity>
          </View>
          <TouchableOpacity style={{ flex: 1 }} onPress={() => setEditing(rule)} activeOpacity={0.7}>
            <Text style={styles.ruleName} numberOfLines={1}>{rule.name || "Regla sin nombre"}</Text>
            <Text style={styles.rowSub} numberOfLines={1}>{scheduleSummary(rule)}</Text>
            <Text style={styles.rowSub} numberOfLines={1}>
              {rule.contacts.length
                ? rule.contacts.map((c) => c.name).join(", ")
                : "Sin contactos — no se aplicará"}
            </Text>
          </TouchableOpacity>
          <Switch
            value={rule.enabled}
            onValueChange={(v) => toggleRule(rule, v)}
            trackColor={{ false: C.panel2, true: C.priDim }}
            thumbColor={C.text}
          />
        </View>
      ))}

      <TouchableOpacity style={styles.addBtn} onPress={() => setEditing(emptyRule())}>
        <Ionicons name="add" size={17} color={C.pri} />
        <Text style={styles.addText}>Nueva regla</Text>
      </TouchableOpacity>

      <RuleEditor
        rule={editing}
        onClose={() => setEditing(null)}
        onSaved={async () => { setEditing(null); await load(); }}
        onDelete={(r) => { setEditing(null); confirmDelete(r); }}
      />
    </View>
  );
}

function RuleEditor({
  rule, onClose, onSaved, onDelete,
}: {
  rule: WaRule | null;
  onClose: () => void;
  onSaved: () => void;
  onDelete: (rule: WaRule) => void;
}) {
  const [draft, setDraft] = useState<WaRule>(emptyRule());
  const [saving, setSaving] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => { if (rule) setDraft({ ...rule }); }, [rule]);

  function toggleDay(d: number) {
    setDraft((r) => ({
      ...r,
      days: r.days.includes(d) ? r.days.filter((x) => x !== d) : [...r.days, d].sort(),
    }));
  }

  async function save() {
    if (!draft.name.trim()) {
      Alert.alert("Falta el nombre", "Ponle un nombre a la regla.");
      return;
    }
    if (!draft.prompt.trim()) {
      Alert.alert("Falta la instrucción", "Escribe qué debe responder JARVIS.");
      return;
    }
    if (!draft.always && (!isValidTime(draft.start) || !isValidTime(draft.end))) {
      Alert.alert("Hora no válida", "Usa el formato HH:MM (por ejemplo 09:30).");
      return;
    }
    setSaving(true);
    try {
      await api.saveWhatsappRule({ ...draft, name: draft.name.trim(), prompt: draft.prompt.trim() });
      onSaved();
    } catch (e) {
      Alert.alert("No se pudo guardar", e instanceof Error ? e.message : "Error desconocido");
    }
    setSaving(false);
  }

  return (
    <Modal visible={!!rule} transparent animationType="slide" onRequestClose={onClose}>
      <KeyboardAvoidingView
        style={styles.backdrop}
        behavior={Platform.OS === "ios" ? "padding" : "height"}
      >
        <View style={styles.sheet}>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>{draft.id ? "Editar regla" : "Nueva regla"}</Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={20} color={C.textMed} />
            </TouchableOpacity>
          </View>

          <ScrollView keyboardShouldPersistTaps="handled">
            <Text style={styles.fieldLabel}>Nombre</Text>
            <TextInput
              style={styles.input}
              value={draft.name}
              onChangeText={(v) => setDraft((r) => ({ ...r, name: v }))}
              placeholder="Fuera de oficina"
              placeholderTextColor={C.textMed}
              cursorColor={C.pri}
              selectionColor={C.pri}
              keyboardAppearance="dark"
            />

            <Text style={styles.fieldLabel}>Contactos</Text>
            <TouchableOpacity style={styles.pickerBtn} onPress={() => setPickerOpen(true)}>
              <Ionicons name="people" size={16} color={C.pri} />
              <Text style={styles.pickerText} numberOfLines={1}>
                {draft.contacts.length
                  ? draft.contacts.map((c) => c.name).join(", ")
                  : "Elegir contactos…"}
              </Text>
            </TouchableOpacity>

            <View style={styles.inlineRow}>
              <Text style={styles.rowTitle}>Siempre activa</Text>
              <Switch
                value={draft.always}
                onValueChange={(v) => setDraft((r) => ({ ...r, always: v }))}
                trackColor={{ false: C.panel2, true: C.priDim }}
                thumbColor={C.text}
              />
            </View>

            {!draft.always && (
              <>
                <Text style={styles.fieldLabel}>Días</Text>
                <View style={styles.dayRow}>
                  {DAY_LABELS.map((label, d) => {
                    const on = draft.days.includes(d);
                    return (
                      <TouchableOpacity
                        key={d}
                        style={[styles.dayChip, on && styles.dayChipOn]}
                        onPress={() => toggleDay(d)}
                      >
                        <Text style={[styles.dayChipText, on && styles.dayChipTextOn]}>{label}</Text>
                      </TouchableOpacity>
                    );
                  })}
                </View>

                <View style={{ flexDirection: "row", gap: spacing.sm }}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.fieldLabel}>Desde</Text>
                    <TextInput
                      style={styles.input}
                      value={draft.start}
                      onChangeText={(v) => setDraft((r) => ({ ...r, start: v }))}
                      placeholder="09:00"
                      placeholderTextColor={C.textMed}
                      cursorColor={C.pri}
                      selectionColor={C.pri}
                      keyboardAppearance="dark"
                      keyboardType="numbers-and-punctuation"
                    />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.fieldLabel}>Hasta</Text>
                    <TextInput
                      style={styles.input}
                      value={draft.end}
                      onChangeText={(v) => setDraft((r) => ({ ...r, end: v }))}
                      placeholder="18:00"
                      placeholderTextColor={C.textMed}
                      cursorColor={C.pri}
                      selectionColor={C.pri}
                      keyboardAppearance="dark"
                      keyboardType="numbers-and-punctuation"
                    />
                  </View>
                </View>
                <Text style={styles.rowSub}>
                  Si la hora final es menor que la inicial, el tramo cruza la medianoche.
                </Text>
              </>
            )}

            <Text style={styles.fieldLabel}>Qué debe responder</Text>
            <TextInput
              style={[styles.input, { height: 96, textAlignVertical: "top" }]}
              value={draft.prompt}
              onChangeText={(v) => setDraft((r) => ({ ...r, prompt: v }))}
              placeholder="Responde que estoy reunido y que contestaré por la tarde."
              placeholderTextColor={C.textMed}
              cursorColor={C.pri}
              selectionColor={C.pri}
              keyboardAppearance="dark"
              multiline
            />

            <TouchableOpacity
              style={[styles.saveBtn, saving && { opacity: 0.5 }]}
              onPress={save}
              disabled={saving}
            >
              <Text style={styles.saveText}>{saving ? "Guardando…" : "Guardar regla"}</Text>
            </TouchableOpacity>

            {!!draft.id && (
              <TouchableOpacity style={styles.deleteBtn} onPress={() => onDelete(draft)}>
                <Text style={styles.deleteText}>Eliminar regla</Text>
              </TouchableOpacity>
            )}
          </ScrollView>
        </View>
      </KeyboardAvoidingView>

      <ContactPicker
        visible={pickerOpen}
        selected={draft.contacts}
        onClose={() => setPickerOpen(false)}
        onChange={(contacts) => setDraft((r) => ({ ...r, contacts }))}
      />
    </Modal>
  );
}

function ContactPicker({
  visible, selected, onClose, onChange,
}: {
  visible: boolean;
  selected: WaRuleContact[];
  onClose: () => void;
  onChange: (contacts: WaRuleContact[]) => void;
}) {
  const [chats, setChats] = useState<WaChat[]>([]);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!visible) return;
    setLoading(true);
    api.getWhatsappChats()
      // Groups are excluded on purpose: the desktop auto-reply refuses to
      // answer in group chats, so offering them here would be a dead end.
      .then((cs) => setChats(cs.filter((c) => !c.isGroup)))
      .catch(() => setChats([]))
      .finally(() => setLoading(false));
  }, [visible]);

  function toggle(chat: WaChat) {
    const has = selected.some((c) => c.chat_id === chat.chatId);
    onChange(
      has
        ? selected.filter((c) => c.chat_id !== chat.chatId)
        : [...selected, { chat_id: chat.chatId, name: chat.name || chat.chatId }],
    );
  }

  const filtered = query.trim()
    ? chats.filter((c) => (c.name || c.chatId).toLowerCase().includes(query.trim().toLowerCase()))
    : chats;

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <View style={[styles.sheet, { height: "80%" }]}>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>Contactos ({selected.length})</Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="checkmark" size={22} color={C.pri} />
            </TouchableOpacity>
          </View>
          <TextInput
            style={styles.input}
            value={query}
            onChangeText={setQuery}
            placeholder="Buscar contacto…"
            placeholderTextColor={C.textMed}
            cursorColor={C.pri}
            selectionColor={C.pri}
            keyboardAppearance="dark"
          />
          {loading ? (
            <ActivityIndicator color={C.pri} style={{ marginTop: 20 }} />
          ) : (
            <FlatList
              data={filtered}
              keyExtractor={(c) => c.chatId}
              ListEmptyComponent={<Text style={styles.hint}>No hay contactos.</Text>}
              renderItem={({ item }) => {
                const on = selected.some((c) => c.chat_id === item.chatId);
                return (
                  <TouchableOpacity style={styles.contactRow} onPress={() => toggle(item)} activeOpacity={0.7}>
                    <Ionicons
                      name={on ? "checkbox" : "square-outline"}
                      size={20}
                      color={on ? C.pri : C.textMed}
                    />
                    <Text style={styles.contactName} numberOfLines={1}>{item.name || item.chatId}</Text>
                  </TouchableOpacity>
                );
              }}
            />
          )}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: radius.lg, padding: 16 },
  cardHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 10 },
  cardTitle: { color: C.acc2, fontSize: 11, fontWeight: "800", textTransform: "uppercase", letterSpacing: 0.5 },
  row: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 9, borderTopWidth: 1, borderTopColor: C.borderA },
  rowTitle: { color: C.text, fontSize: 13, fontWeight: "600" },
  rowSub: { color: C.textMed, fontSize: 11.5, marginTop: 2, lineHeight: 16 },
  divider: { height: 1, backgroundColor: C.borderA, marginVertical: 12 },
  sectionLabel: { color: C.text, fontSize: 13, fontWeight: "700", marginBottom: 2 },
  hint: { color: C.textMed, fontSize: 12, paddingVertical: 12, textAlign: "center" },
  ruleRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.borderA, borderRadius: 12, padding: 10, marginTop: 8 },
  orderCol: { alignItems: "center", justifyContent: "center", gap: 2 },
  ruleName: { color: C.text, fontSize: 13, fontWeight: "700" },
  addBtn: { flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, marginTop: 12, backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.35)", borderRadius: 12, paddingVertical: 11 },
  addText: { color: C.pri, fontWeight: "700", fontSize: 13 },
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end" },
  sheet: { backgroundColor: C.panel, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, padding: spacing.lg, maxHeight: "90%" },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md },
  sheetTitle: { color: C.text, fontSize: 15, fontWeight: "800" },
  fieldLabel: { color: C.textMed, fontSize: 10.5, fontWeight: "700", marginBottom: 4, marginTop: 6, textTransform: "uppercase" },
  input: { backgroundColor: C.panel2, color: C.text, borderWidth: 1, borderColor: C.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 11, fontSize: 14, marginBottom: spacing.sm },
  pickerBtn: { flexDirection: "row", alignItems: "center", gap: 8, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, marginBottom: spacing.sm },
  pickerText: { color: C.text, fontSize: 13.5, flex: 1 },
  inlineRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingVertical: 8 },
  dayRow: { flexDirection: "row", gap: 6, marginBottom: spacing.sm },
  dayChip: { flex: 1, aspectRatio: 1, borderRadius: 8, backgroundColor: C.panel2, borderWidth: 1, borderColor: C.border, alignItems: "center", justifyContent: "center" },
  dayChipOn: { backgroundColor: C.priGho, borderColor: "rgba(182,196,255,0.5)" },
  dayChipText: { color: C.textMed, fontSize: 12, fontWeight: "700" },
  dayChipTextOn: { color: C.pri },
  saveBtn: { backgroundColor: C.pri, borderRadius: 14, paddingVertical: 13, alignItems: "center", marginTop: spacing.md },
  saveText: { color: "#08101F", fontWeight: "800", fontSize: 14 },
  deleteBtn: { marginTop: 10, backgroundColor: "rgba(255,94,130,0.10)", borderWidth: 1, borderColor: "rgba(255,94,130,0.3)", borderRadius: 12, paddingVertical: 11, alignItems: "center" },
  deleteText: { color: C.red, fontWeight: "700", fontSize: 13 },
  contactRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 11 },
  contactName: { color: C.text, fontSize: 13.5, flex: 1 },
});
