<script>
  import { onMount } from 'svelte'
  import {
    getCalendarEvents, searchCalendar, createEvent, updateEvent, deleteEvent,
    listContacts, upsertContact,
  } from '../api.js'
  import Icon from '../lib/Icon.svelte'

  // Mirrors ui/panels/calendar.py's CalendarModePanel: a single month-grid
  // view (no week/day/agenda toggle — the Qt panel doesn't have one
  // either), a selected-day event list, and one dialog shared for
  // create/edit. All events live on the "primary" calendar, same as Qt.

  const WEEKDAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']

  let cursor = $state(startOfMonth(new Date()))
  let selectedDate = $state(new Date())
  let events = $state([])
  let loading = $state(false)
  let error = $state('')
  let needsAuth = $state(false)

  let searchQuery = $state('')
  let searchResults = $state(null)
  let searching = $state(false)

  let dialogOpen = $state(false)
  let editingId = $state('')
  let form = $state(emptyForm())
  let contacts = $state([])
  let newAttendeeName = $state('')
  let newAttendeeEmail = $state('')
  let pickedContact = $state('')
  let dialogError = $state('')
  let saving = $state(false)

  function startOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1)
  }

  function emptyForm() {
    const now = roundToHour(new Date())
    const end = new Date(now.getTime() + 60 * 60 * 1000)
    return {
      summary: '', date: toDateInput(now), startTime: toTimeInput(now),
      endTime: toTimeInput(end), location: '', description: '', attendees: [],
    }
  }

  function roundToHour(d) {
    const r = new Date(d)
    r.setMinutes(0, 0, 0)
    r.setHours(r.getHours() + 1)
    return r
  }

  function toDateInput(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  }
  function toTimeInput(d) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }

  // Calendar grid: Monday-first, always full weeks covering the month.
  let gridDays = $derived.by(() => {
    const first = cursor
    const startWeekday = (first.getDay() + 6) % 7 // 0=Mon
    const gridStart = new Date(first)
    gridStart.setDate(first.getDate() - startWeekday)
    const days = []
    for (let i = 0; i < 42; i++) {
      const d = new Date(gridStart)
      d.setDate(gridStart.getDate() + i)
      days.push(d)
    }
    // Trim trailing all-next-month rows beyond 5 weeks when unnecessary,
    // matching the Qt panel's up-to-6-row grid.
    return days
  })

  let eventsByDay = $derived.by(() => {
    const map = new Map()
    for (const ev of events) {
      const key = (ev.start || '').slice(0, 10)
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(ev)
    }
    return map
  })

  let selectedKey = $derived(toDateInput(selectedDate))
  let selectedEvents = $derived(
    searchResults ?? (eventsByDay.get(selectedKey) || []).sort((a, b) => (a.start > b.start ? 1 : -1))
  )

  function isSameMonth(d) {
    return d.getMonth() === cursor.getMonth() && d.getFullYear() === cursor.getFullYear()
  }
  function isToday(d) {
    const t = new Date()
    return d.toDateString() === t.toDateString()
  }
  function isSelected(d) {
    return d.toDateString() === selectedDate.toDateString()
  }

  async function loadMonth() {
    loading = true
    error = ''
    needsAuth = false
    const rangeStart = new Date(cursor.getFullYear(), cursor.getMonth() - 1, 1)
    const rangeEnd = new Date(cursor.getFullYear(), cursor.getMonth() + 2, 1)
    try {
      events = await getCalendarEvents(rangeStart.toISOString(), rangeEnd.toISOString())
    } catch (e) {
      error = e.message
      needsAuth = e.message === 'needs_auth' || /needs_auth|credentials|token/i.test(e.message)
      events = []
    } finally {
      loading = false
    }
  }

  function shiftMonth(delta) {
    cursor = new Date(cursor.getFullYear(), cursor.getMonth() + delta, 1)
    loadMonth()
  }

  function goToday() {
    cursor = startOfMonth(new Date())
    selectedDate = new Date()
    searchResults = null
    searchQuery = ''
    loadMonth()
  }

  function selectDate(d) {
    selectedDate = d
    searchResults = null
    searchQuery = ''
    if (!isSameMonth(d)) {
      cursor = startOfMonth(d)
      loadMonth()
    }
  }

  async function doSearch() {
    const q = searchQuery.trim()
    if (!q) {
      searchResults = null
      return
    }
    searching = true
    try {
      searchResults = await searchCalendar(q)
    } catch (e) {
      error = e.message
    } finally {
      searching = false
    }
  }

  function clearSearch() {
    searchQuery = ''
    searchResults = null
  }

  function fmtTime(iso) {
    if (!iso || iso.length <= 10) return 'Todo el día'
    const d = new Date(iso)
    return d.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
  }
  function fmtRange(ev) {
    if (ev.all_day) return 'Todo el día'
    return `${fmtTime(ev.start)} – ${fmtTime(ev.end)}`
  }

  function openCreateDialog() {
    editingId = ''
    form = emptyForm()
    form.date = toDateInput(selectedDate)
    dialogError = ''
    pickedContact = ''
    newAttendeeName = ''
    newAttendeeEmail = ''
    dialogOpen = true
    loadContacts()
  }

  function openEditDialog(ev) {
    editingId = ev.id
    const start = new Date(ev.start)
    const end = new Date(ev.end)
    form = {
      summary: ev.summary || '',
      date: toDateInput(start),
      startTime: toTimeInput(start),
      endTime: toTimeInput(end),
      location: ev.location || '',
      description: ev.description || '',
      attendees: (ev.attendees || []).map((a) => (typeof a === 'string' ? { email: a } : a)),
    }
    dialogError = ''
    pickedContact = ''
    newAttendeeName = ''
    newAttendeeEmail = ''
    dialogOpen = true
    loadContacts()
  }

  async function loadContacts() {
    try {
      contacts = await listContacts()
    } catch {
      contacts = []
    }
  }

  let availableContacts = $derived(
    contacts.filter((c) => !form.attendees.some((a) => a.email === c.email))
  )

  function addPickedContact() {
    const c = contacts.find((x) => x.email === pickedContact)
    if (!c) return
    form.attendees = [...form.attendees, c]
    pickedContact = ''
  }

  async function addNewAttendee() {
    const name = newAttendeeName.trim()
    const email = newAttendeeEmail.trim()
    if (!email || !email.includes('@')) {
      dialogError = 'Email de invitado no válido.'
      return
    }
    try {
      await upsertContact(name, email)
    } catch {
      // Non-fatal — still add them to this event even if the contact
      // book write failed.
    }
    form.attendees = [...form.attendees, { name, email }]
    newAttendeeName = ''
    newAttendeeEmail = ''
    dialogError = ''
  }

  function removeAttendee(email) {
    form.attendees = form.attendees.filter((a) => a.email !== email)
  }

  async function saveEvent() {
    if (!form.summary.trim()) {
      dialogError = 'El evento necesita un título.'
      return
    }
    let start = `${form.date}T${form.startTime}:00`
    let end = `${form.date}T${form.endTime}:00`
    if (end <= start) {
      const endDate = new Date(`${form.date}T${form.startTime}:00`)
      endDate.setHours(endDate.getHours() + 1)
      end = `${form.date}T${toTimeInput(endDate)}:00`
    }
    saving = true
    dialogError = ''
    const payload = {
      summary: form.summary.trim(),
      start, end,
      location: form.location,
      description: form.description,
      attendees: form.attendees,
    }
    try {
      if (editingId) {
        await updateEvent({ event_id: editingId, ...payload })
      } else {
        await createEvent(payload)
      }
      dialogOpen = false
      await loadMonth()
    } catch (e) {
      dialogError = e.message
    } finally {
      saving = false
    }
  }

  async function removeEvent(id) {
    try {
      await deleteEvent(id)
      if (dialogOpen && editingId === id) dialogOpen = false
      await loadMonth()
    } catch (e) {
      error = e.message
    }
  }

  const MONTH_FMT = new Intl.DateTimeFormat('es-ES', { month: 'long', year: 'numeric' })
  const DAY_FMT = new Intl.DateTimeFormat('es-ES', { weekday: 'long', day: 'numeric', month: 'long' })

  onMount(loadMonth)
</script>

<div class="panel">
  <header class="cal-header">
    <div class="month-nav">
      <button class="nav-btn" onclick={() => shiftMonth(-1)} title="Mes anterior">‹</button>
      <span class="month-label">{MONTH_FMT.format(cursor)}</span>
      <button class="nav-btn" onclick={() => shiftMonth(1)} title="Mes siguiente">›</button>
      <button class="today-chip" onclick={goToday}>Hoy</button>
    </div>
    <div class="spacer"></div>
    {#if loading}<span class="status">Cargando…</span>{/if}
    <button class="icon-btn" onclick={loadMonth} title="Actualizar"><Icon name="calendar" size={15} /></button>
    <button class="add-btn" onclick={openCreateDialog}>+ Evento</button>
  </header>

  <div class="search-row">
    <input
      type="text" placeholder="Buscar eventos…" bind:value={searchQuery}
      onkeydown={(e) => e.key === 'Enter' && doSearch()}
    />
    {#if searchResults}
      <button class="clear-btn" onclick={clearSearch}>✕</button>
    {:else}
      <button class="search-btn" onclick={doSearch}>Buscar</button>
    {/if}
  </div>

  {#if needsAuth}
    <div class="auth-hint">Necesitas iniciar sesión con Google para usar el calendario.</div>
  {:else if error}
    <div class="error">{error}</div>
  {/if}

  <div class="body">
    <div class="grid-wrap">
      <div class="weekdays">
        {#each WEEKDAYS as wd}<div class="wd">{wd}</div>{/each}
      </div>
      <div class="grid">
        {#each gridDays as d (d.toISOString())}
          <button
            class="cell"
            class:dim={!isSameMonth(d)}
            class:today={isToday(d)}
            class:selected={isSelected(d)}
            onclick={() => selectDate(d)}
          >
            <span class="day-num">{d.getDate()}</span>
            <div class="dots">
              {#each (eventsByDay.get(toDateInput(d)) || []).slice(0, 3) as _}
                <span class="dot"></span>
              {/each}
              {#if (eventsByDay.get(toDateInput(d)) || []).length > 3}
                <span class="more">+{(eventsByDay.get(toDateInput(d)) || []).length - 3}</span>
              {/if}
            </div>
          </button>
        {/each}
      </div>
    </div>

    <div class="day-panel">
      <div class="day-panel-header">
        <span class="day-label">{searchResults ? `Resultados: "${searchQuery}"` : DAY_FMT.format(selectedDate)}</span>
        <button class="new-event-btn" onclick={openCreateDialog}>Nuevo evento</button>
      </div>
      <div class="events-list">
        {#if searching}
          <div class="empty">Buscando…</div>
        {:else if !selectedEvents.length}
          <div class="empty">{searchResults ? 'Sin resultados.' : 'Sin eventos este día.'}</div>
        {/if}
        {#each selectedEvents as ev (ev.id)}
          <div
            class="event-row"
            role="button"
            tabindex="0"
            onclick={() => openEditDialog(ev)}
            onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && openEditDialog(ev)}
          >
            <div class="event-main">
              <div class="event-title">{ev.summary || '(sin título)'}</div>
              <div class="event-time">{fmtRange(ev)}{ev.location ? ` · ${ev.location}` : ''}</div>
            </div>
            {#if ev.attendees?.length}
              <span class="attendee-count">{ev.attendees.length}</span>
            {/if}
            <button
              class="delete-icon"
              onclick={(e) => { e.stopPropagation(); removeEvent(ev.id) }}
              title="Eliminar evento"
            >✕</button>
          </div>
        {/each}
      </div>
    </div>
  </div>
</div>

{#if dialogOpen}
  <div class="modal-backdrop" onclick={() => (dialogOpen = false)}>
    <div class="modal" onclick={(e) => e.stopPropagation()}>
      <h3>{editingId ? 'Editar evento' : 'Crear evento'}</h3>

      <label>Título<input type="text" bind:value={form.summary} placeholder="Título del evento" /></label>

      <div class="row3">
        <label>Fecha<input type="date" bind:value={form.date} /></label>
        <label>Inicio<input type="time" bind:value={form.startTime} /></label>
        <label>Fin<input type="time" bind:value={form.endTime} /></label>
      </div>

      <label>Ubicación<input type="text" bind:value={form.location} placeholder="Opcional" /></label>
      <label>Descripción<textarea bind:value={form.description} rows="2" placeholder="Opcional"></textarea></label>

      <div class="attendees-block">
        <div class="attendees-label">Invitados</div>
        {#if form.attendees.length}
          <div class="attendee-chips">
            {#each form.attendees as a (a.email)}
              <span class="chip">{a.name || a.email} <button onclick={() => removeAttendee(a.email)}>×</button></span>
            {/each}
          </div>
        {/if}
        <div class="attendee-add-row">
          <select bind:value={pickedContact}>
            <option value="">Elegir contacto…</option>
            {#each availableContacts as c}<option value={c.email}>{c.name || c.email}</option>{/each}
          </select>
          <button onclick={addPickedContact} disabled={!pickedContact}>Añadir</button>
        </div>
        <div class="attendee-add-row">
          <input type="text" placeholder="Nombre" bind:value={newAttendeeName} />
          <input type="email" placeholder="email@ejemplo.com" bind:value={newAttendeeEmail} />
          <button onclick={addNewAttendee}>+</button>
        </div>
      </div>

      {#if dialogError}<div class="error">{dialogError}</div>{/if}

      <div class="modal-actions">
        {#if editingId}
          <button class="danger" onclick={() => removeEvent(editingId)}>Eliminar evento</button>
        {/if}
        <div class="spacer"></div>
        <button class="ghost" onclick={() => (dialogOpen = false)}>Cancelar</button>
        <button class="primary" onclick={saveEvent} disabled={saving}>
          {saving ? 'Guardando…' : editingId ? 'Guardar cambios' : 'Crear evento'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel {
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: 16px;
    gap: 10px;
  }

  .cal-header {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
  }

  .month-nav {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .month-label {
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    text-transform: capitalize;
    min-width: 140px;
  }

  .nav-btn, .icon-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    cursor: pointer;
    font-size: 16px;
  }

  .today-chip {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: 999px;
    color: var(--text-med);
    padding: 4px 12px;
    font-size: 12px;
    cursor: pointer;
  }

  .spacer { flex: 1; }

  .status { font-size: 12px; color: var(--text-med); }

  .add-btn {
    background: var(--pri-gho);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 6px 14px;
    font-size: 12px;
    cursor: pointer;
  }

  .search-row {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }

  .search-row input {
    flex: 1;
    min-width: 0;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 7px 12px;
    font-size: 13px;
  }

  .search-btn, .clear-btn {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 0 14px;
    cursor: pointer;
  }

  .auth-hint {
    color: var(--acc2);
    font-size: 13px;
    background: var(--panel-2);
    border-radius: var(--radius-sm);
    padding: 8px 12px;
  }

  .error { color: var(--red); font-size: 13px; }

  .body {
    flex: 1;
    min-height: 0;
    display: flex;
    gap: 14px;
  }

  .grid-wrap {
    flex: 3;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  .weekdays {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    text-align: center;
    font-size: 11px;
    color: var(--text-med);
    padding-bottom: 6px;
  }

  .grid {
    flex: 1;
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    grid-template-rows: repeat(6, 1fr);
    gap: 4px;
  }

  .cell {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    padding: 6px 4px;
    cursor: pointer;
    color: var(--text-dim);
  }

  .cell.dim { opacity: 0.4; }
  .cell.today { border-color: var(--pri-dim); }
  .cell.selected { background: var(--pri-gho); border-color: var(--pri-dim); }

  .day-num { font-size: 12px; }

  .dots {
    display: flex;
    gap: 2px;
    align-items: center;
    min-height: 6px;
  }

  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--pri);
  }

  .more { font-size: 9px; color: var(--text-med); }

  .day-panel {
    flex: 2;
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 12px;
  }

  .day-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 8px;
    flex-shrink: 0;
  }

  .day-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    text-transform: capitalize;
  }

  .new-event-btn {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 5px 10px;
    font-size: 11px;
    cursor: pointer;
    flex-shrink: 0;
  }

  .events-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .empty { color: var(--text-med); font-size: 12px; padding: 12px 0; }

  .event-row {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--panel-2);
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    padding: 8px 10px;
    cursor: pointer;
    text-align: left;
  }

  .event-row:hover { border-color: var(--border-a); }

  .event-main { flex: 1; min-width: 0; }

  .event-title {
    font-size: 12px;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .event-time { font-size: 11px; color: var(--text-med); }

  .attendee-count {
    font-size: 10px;
    color: var(--text-med);
    background: var(--panel);
    border-radius: 999px;
    padding: 1px 6px;
  }

  .delete-icon {
    background: transparent;
    border: none;
    color: var(--text-med);
    cursor: pointer;
    font-size: 12px;
  }

  .delete-icon:hover { color: var(--red); }

  /* Modal */
  .modal-backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }

  .modal {
    width: min(480px, 90vw);
    max-height: 85vh;
    overflow-y: auto;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .modal h3 { margin: 0 0 4px; color: var(--text); }

  .modal label {
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: var(--text-med);
  }

  .modal input, .modal textarea, .modal select {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 7px 10px;
    font-size: 13px;
    font-family: inherit;
  }

  .row3 {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 8px;
  }

  .attendees-block {
    border-top: 1px solid var(--border-a);
    padding-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .attendees-label { font-size: 11px; color: var(--text-med); }

  .attendee-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .chip {
    background: var(--pri-gho);
    color: var(--pri);
    border-radius: 999px;
    padding: 3px 8px;
    font-size: 11px;
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .chip button {
    background: none;
    border: none;
    color: inherit;
    cursor: pointer;
    font-size: 13px;
  }

  .attendee-add-row {
    display: flex;
    gap: 6px;
  }

  .attendee-add-row select, .attendee-add-row input { flex: 1; min-width: 0; }

  .attendee-add-row button {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 0 12px;
    cursor: pointer;
  }

  .modal-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
  }

  .modal-actions button {
    border-radius: var(--radius-sm);
    padding: 7px 14px;
    font-size: 12px;
    cursor: pointer;
    border: 1px solid var(--border-a);
  }

  .modal-actions .ghost { background: transparent; color: var(--text-dim); }
  .modal-actions .primary { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }
  .modal-actions .danger { background: transparent; border-color: rgba(255,94,130,0.3); color: var(--red); }
</style>
