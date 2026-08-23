import React, { useCallback, useMemo, useState } from "react";
import {
  View, Text, StyleSheet, TouchableOpacity, ScrollView, FlatList, Modal,
  TextInput, ActivityIndicator, KeyboardAvoidingView, Platform, Alert,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { C, spacing, radius } from "../theme";
import { api, ApiError, CalendarEvent } from "../api";

const WEEKDAYS = ["L", "M", "X", "J", "V", "S", "D"];
const MONTHS = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio",
  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
];

/** Local YYYY-MM-DD. Deliberately not toISOString(), which converts to UTC and
 * shifts the day across midnight for anyone east or west of Greenwich. */
function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The 6x7 block a month grid shows, Monday-first, including the neighbouring
 * days that pad the first and last weeks. */
function monthGrid(year: number, month: number): Date[] {
  const first = new Date(year, month, 1);
  const offset = (first.getDay() + 6) % 7; // JS weeks start on Sunday
  const start = new Date(year, month, 1 - offset);
  return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
}

/** Which local day an event belongs to. All-day events carry a bare date and
 * must NOT go through Date parsing, which would treat them as UTC midnight. */
function eventDayKey(ev: CalendarEvent): string {
  const raw = ev.start || "";
  if (ev.all_day || /^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw.slice(0, 10);
  const d = new Date(raw);
  return isNaN(d.getTime()) ? "" : dayKey(d);
}

function eventTimeLabel(ev: CalendarEvent): string {
  if (ev.all_day) return "Todo el día";
  const s = new Date(ev.start);
  if (isNaN(s.getTime())) return "";
  const hhmm = (d: Date) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (ev.end) {
    const e = new Date(ev.end);
    if (!isNaN(e.getTime())) return `${hhmm(s)} – ${hhmm(e)}`;
  }
  return hhmm(s);
}

function isValidTime(t: string): boolean {
  const m = t.match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return false;
  return Number(m[1]) <= 23 && Number(m[2]) <= 59;
}

export default function CalendarScreen() {
  const today = useMemo(() => new Date(), []);
  const [cursor, setCursor] = useState({ year: today.getFullYear(), month: today.getMonth() });
  const [selected, setSelected] = useState<string>(dayKey(today));
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [needsAuth, setNeedsAuth] = useState(false);
  const [error, setError] = useState("");

  const [composerOpen, setComposerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ summary: "", start: "09:00", end: "10:00", location: "" });

  const insets = useSafeAreaInsets();
  const grid = useMemo(() => monthGrid(cursor.year, cursor.month), [cursor]);

  const load = useCallback(async () => {
    const cells = monthGrid(cursor.year, cursor.month);
    const from = cells[0];
    const to = cells[cells.length - 1];
    // Whole grid, not just the month, so the padding days show their events too.
    const timeMin = new Date(from.getFullYear(), from.getMonth(), from.getDate(), 0, 0, 0).toISOString();
    const timeMax = new Date(to.getFullYear(), to.getMonth(), to.getDate() + 1, 0, 0, 0).toISOString();
    setLoading(true);
    setError("");
    try {
      setEvents(await api.getCalendarEvents(timeMin, timeMax));
      setNeedsAuth(false);
    } catch (e) {
      setEvents([]);
      if (e instanceof ApiError && e.status === 503) {
        setNeedsAuth(true);
      } else {
        setError(e instanceof Error ? e.message : "No se pudo cargar el calendario.");
      }
    }
    setLoading(false);
  }, [cursor]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const byDay = useMemo(() => {
    const map: Record<string, CalendarEvent[]> = {};
    for (const ev of events) {
      const k = eventDayKey(ev);
      if (!k) continue;
      (map[k] ||= []).push(ev);
    }
    return map;
  }, [events]);

  const dayEvents = byDay[selected] || [];
  const selectedDate = useMemo(() => {
    const [y, m, d] = selected.split("-").map(Number);
    return new Date(y, m - 1, d);
  }, [selected]);

  function shiftMonth(delta: number) {
    setCursor((c) => {
      const d = new Date(c.year, c.month + delta, 1);
      return { year: d.getFullYear(), month: d.getMonth() };
    });
  }
  function goToday() {
    const now = new Date();
    setCursor({ year: now.getFullYear(), month: now.getMonth() });
    setSelected(dayKey(now));
  }

  async function saveEvent() {
    const summary = form.summary.trim();
    if (!summary) return;
    if (!isValidTime(form.start) || !isValidTime(form.end)) {
      Alert.alert("Hora no válida", "Usa el formato HH:MM (por ejemplo 09:30).");
      return;
    }
    setSaving(true);
    try {
      // Sent without a timezone on purpose: the desktop resolves it in its own
      // local time, which is the timezone the event is actually meant to be in.
      await api.createCalendarEvent({
        summary,
        start: `${selected}T${form.start}:00`,
        end: `${selected}T${form.end}:00`,
        location: form.location.trim(),
      });
      setComposerOpen(false);
      setForm({ summary: "", start: "09:00", end: "10:00", location: "" });
      await load();
    } catch (e) {
      Alert.alert("No se pudo crear", e instanceof Error ? e.message : "Error desconocido");
    }
    setSaving(false);
  }

  function confirmDelete(ev: CalendarEvent) {
    Alert.alert(
      "Eliminar evento",
      `¿Seguro que quieres eliminar "${ev.summary}"? Esta acción no se puede deshacer.`,
      [
        { text: "Cancelar", style: "cancel" },
        {
          text: "Eliminar",
          style: "destructive",
          onPress: async () => {
            try {
              await api.deleteCalendarEvent(ev.id);
              await load();
            } catch (e) {
              Alert.alert("No se pudo eliminar", e instanceof Error ? e.message : "Error desconocido");
            }
          },
        },
      ],
    );
  }

  const todayKey = dayKey(new Date());

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.navBtn} onPress={() => shiftMonth(-1)} hitSlop={10}>
          <Ionicons name="chevron-back" size={18} color={C.pri} />
        </TouchableOpacity>
        <View style={{ flex: 1, alignItems: "center" }}>
          <Text style={styles.monthLabel}>{MONTHS[cursor.month]} {cursor.year}</Text>
        </View>
        <TouchableOpacity style={styles.navBtn} onPress={() => shiftMonth(1)} hitSlop={10}>
          <Ionicons name="chevron-forward" size={18} color={C.pri} />
        </TouchableOpacity>
        <TouchableOpacity style={styles.todayBtn} onPress={goToday}>
          <Text style={styles.todayText}>Hoy</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.weekRow}>
        {WEEKDAYS.map((w, i) => (
          <Text key={i} style={styles.weekday}>{w}</Text>
        ))}
      </View>

      <View style={styles.grid}>
        {grid.map((d, i) => {
          const k = dayKey(d);
          const inMonth = d.getMonth() === cursor.month;
          const count = (byDay[k] || []).length;
          const isSelected = k === selected;
          return (
            <TouchableOpacity
              key={i}
              style={[styles.cell, isSelected && styles.cellSelected]}
              onPress={() => setSelected(k)}
              activeOpacity={0.7}
            >
              <Text
                style={[
                  styles.cellText,
                  !inMonth && styles.cellTextDim,
                  k === todayKey && styles.cellToday,
                  isSelected && styles.cellTextSelected,
                ]}
              >
                {d.getDate()}
              </Text>
              <View style={styles.dotRow}>
                {count > 0 && <View style={styles.dot} />}
                {count > 1 && <View style={styles.dot} />}
                {count > 2 && <View style={styles.dot} />}
              </View>
            </TouchableOpacity>
          );
        })}
      </View>

      <View style={styles.dayHeader}>
        <Text style={styles.dayTitle}>
          {selectedDate.getDate()} de {MONTHS[selectedDate.getMonth()]}
        </Text>
        {loading && <ActivityIndicator color={C.pri} size="small" />}
      </View>

      {needsAuth ? (
        <Text style={styles.hint}>
          Google Calendar no está conectado todavía.{"\n"}
          Inicia sesión desde el modo Calendario del escritorio.
        </Text>
      ) : error ? (
        <Text style={styles.hint}>{error}</Text>
      ) : (
        <FlatList
          style={{ flex: 1 }}
          contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingBottom: 90 }}
          data={dayEvents}
          keyExtractor={(e) => e.id}
          ListEmptyComponent={<Text style={styles.hint}>No hay eventos este día.</Text>}
          renderItem={({ item }) => (
            <View style={styles.eventRow}>
              <View style={styles.eventBar} />
              <View style={{ flex: 1 }}>
                <Text style={styles.eventTitle} numberOfLines={2}>{item.summary}</Text>
                <Text style={styles.eventMeta}>
                  {eventTimeLabel(item)}
                  {item.location ? `  ·  ${item.location}` : ""}
                </Text>
                {!!item.attendees?.length && (
                  <Text style={styles.eventMeta}>{item.attendees.length} invitado(s)</Text>
                )}
              </View>
              <TouchableOpacity onPress={() => confirmDelete(item)} hitSlop={10} style={styles.delBtn}>
                <Ionicons name="trash-outline" size={17} color={C.red} />
              </TouchableOpacity>
            </View>
          )}
        />
      )}

      {!needsAuth && (
        <TouchableOpacity
          style={[styles.fab, { bottom: 16 + insets.bottom }]}
          onPress={() => setComposerOpen(true)}
          activeOpacity={0.85}
        >
          <Ionicons name="add" size={26} color="#08101F" />
        </TouchableOpacity>
      )}

      <Modal visible={composerOpen} transparent animationType="slide" onRequestClose={() => setComposerOpen(false)}>
        <KeyboardAvoidingView
          style={styles.modalBackdrop}
          behavior={Platform.OS === "ios" ? "padding" : "height"}
        >
          <View style={styles.sheet}>
            <View style={styles.sheetHeader}>
              <Text style={styles.sheetTitle}>
                Nuevo evento · {selectedDate.getDate()} {MONTHS[selectedDate.getMonth()].slice(0, 3)}
              </Text>
              <TouchableOpacity onPress={() => setComposerOpen(false)} hitSlop={10}>
                <Ionicons name="close" size={20} color={C.textMed} />
              </TouchableOpacity>
            </View>

            <ScrollView keyboardShouldPersistTaps="handled">
              <TextInput
                style={styles.input}
                value={form.summary}
                onChangeText={(v) => setForm((f) => ({ ...f, summary: v }))}
                placeholder="Título"
                placeholderTextColor={C.textMed}
                cursorColor={C.pri}
                selectionColor={C.pri}
                keyboardAppearance="dark"
              />
              <View style={{ flexDirection: "row", gap: spacing.sm }}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.fieldLabel}>Inicio</Text>
                  <TextInput
                    style={styles.input}
                    value={form.start}
                    onChangeText={(v) => setForm((f) => ({ ...f, start: v }))}
                    placeholder="09:00"
                    placeholderTextColor={C.textMed}
                    cursorColor={C.pri}
                    selectionColor={C.pri}
                    keyboardAppearance="dark"
                    keyboardType="numbers-and-punctuation"
                  />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.fieldLabel}>Fin</Text>
                  <TextInput
                    style={styles.input}
                    value={form.end}
                    onChangeText={(v) => setForm((f) => ({ ...f, end: v }))}
                    placeholder="10:00"
                    placeholderTextColor={C.textMed}
                    cursorColor={C.pri}
                    selectionColor={C.pri}
                    keyboardAppearance="dark"
                    keyboardType="numbers-and-punctuation"
                  />
                </View>
              </View>
              <TextInput
                style={styles.input}
                value={form.location}
                onChangeText={(v) => setForm((f) => ({ ...f, location: v }))}
                placeholder="Ubicación (opcional)"
                placeholderTextColor={C.textMed}
                cursorColor={C.pri}
                selectionColor={C.pri}
                keyboardAppearance="dark"
              />

              <TouchableOpacity
                style={[styles.saveBtn, (saving || !form.summary.trim()) && { opacity: 0.5 }]}
                onPress={saveEvent}
                disabled={saving || !form.summary.trim()}
              >
                <Text style={styles.saveText}>{saving ? "Creando…" : "Crear evento"}</Text>
              </TouchableOpacity>
            </ScrollView>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  header: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: spacing.lg, paddingTop: spacing.md, paddingBottom: spacing.sm },
  navBtn: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(255,255,255,0.05)" },
  monthLabel: { color: C.text, fontSize: 15.5, fontWeight: "800", textTransform: "capitalize" },
  todayBtn: { paddingHorizontal: 11, paddingVertical: 6, borderRadius: 14, backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.35)" },
  todayText: { color: C.pri, fontSize: 11.5, fontWeight: "700" },
  weekRow: { flexDirection: "row", paddingHorizontal: spacing.lg },
  weekday: { flex: 1, textAlign: "center", color: C.textMed, fontSize: 10.5, fontWeight: "700" },
  grid: { flexDirection: "row", flexWrap: "wrap", paddingHorizontal: spacing.lg, paddingTop: 4 },
  cell: { width: `${100 / 7}%`, aspectRatio: 1, alignItems: "center", justifyContent: "center", borderRadius: 10 },
  cellSelected: { backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.4)" },
  cellText: { color: C.text, fontSize: 13 },
  cellTextDim: { color: C.textMed, opacity: 0.45 },
  cellToday: { color: C.pri, fontWeight: "800" },
  cellTextSelected: { fontWeight: "800" },
  dotRow: { flexDirection: "row", gap: 2, height: 5, marginTop: 2 },
  dot: { width: 4, height: 4, borderRadius: 2, backgroundColor: C.acc2 },
  dayHeader: { flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: spacing.lg, marginTop: spacing.md, marginBottom: spacing.sm },
  dayTitle: { color: C.acc2, fontSize: 11, fontWeight: "800", textTransform: "uppercase", letterSpacing: 0.5 },
  hint: { color: C.textMed, fontSize: 12.5, textAlign: "center", padding: 24, lineHeight: 19 },
  eventRow: { flexDirection: "row", alignItems: "center", gap: 10, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: 12, padding: 11, marginBottom: 7 },
  eventBar: { width: 3, alignSelf: "stretch", borderRadius: 2, backgroundColor: C.priDim },
  eventTitle: { color: C.text, fontSize: 13.5, fontWeight: "700" },
  eventMeta: { color: C.textMed, fontSize: 11.5, marginTop: 2 },
  delBtn: { width: 30, height: 30, alignItems: "center", justifyContent: "center" },
  fab: { position: "absolute", right: 18, width: 54, height: 54, borderRadius: 27, backgroundColor: C.pri, alignItems: "center", justifyContent: "center", elevation: 6 },
  modalBackdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)", justifyContent: "flex-end" },
  sheet: { backgroundColor: C.panel, borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg, padding: spacing.lg, maxHeight: "85%" },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: spacing.md },
  sheetTitle: { color: C.text, fontSize: 14.5, fontWeight: "800" },
  fieldLabel: { color: C.textMed, fontSize: 10.5, fontWeight: "700", marginBottom: 4, textTransform: "uppercase" },
  input: { backgroundColor: C.panel2, color: C.text, borderWidth: 1, borderColor: C.border, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 11, fontSize: 14.5, marginBottom: spacing.sm },
  saveBtn: { backgroundColor: C.pri, borderRadius: 14, paddingVertical: 13, alignItems: "center", marginTop: spacing.sm },
  saveText: { color: "#08101F", fontWeight: "800", fontSize: 14 },
});
