<script>
  import { onMount } from 'svelte'
  import * as api from '../api.js'
  import Icon from '../lib/Icon.svelte'

  // Mirrors ui/panels/settings.py's 13-tab layout (settings.py:29-43),
  // same order, same controls. Gmail/Drive tabs are empty placeholders in
  // the Qt panel too ("Sin ajustes disponibles todavía.") — not a gap.

  const TABS = [
    'Música', 'Ecualizador', 'YouTube', 'WhatsApp', 'Anime',
    'Gmail', 'Drive', 'APIs', 'General', 'Apariencia', 'Rendimiento', 'Inicio', 'Acerca de',
  ]
  const EQ_BANDS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
  const EQ_PRESETS = {
    'Plano': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    'Graves': [6, 5, 4, 2, 0, 0, 0, 0, 1, 2],
    'Agudos': [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
    'Vocal': [-2, -1, 0, 2, 4, 4, 3, 1, 0, -1],
    'Rock': [4, 3, 2, 0, -1, -1, 0, 2, 3, 4],
    'Electrónica': [5, 4, 2, 0, -1, 0, 1, 2, 4, 5],
  }

  let tab = $state('Música')
  let s = $state({}) // raw app_settings snapshot
  let loaded = $state(false)
  let saveNote = $state('')

  function fmtFreq(f) {
    return f >= 1000 ? `${f / 1000}k` : String(f)
  }

  async function set(key, value) {
    s = { ...s, [key]: value }
    try {
      await api.setSetting(key, value)
    } catch (e) {
      saveNote = `Error guardando ${key}: ${e.message}`
    }
  }

  function pushMusic(action, params) {
    api.musicAction(action, params).catch(() => {})
  }

  // -- Música --
  function onCrossfadeEnabled(v) { set('crossfade_enabled', v); pushMusic('set_crossfade', { enabled: v }) }
  function onCrossfadeSecs(v) { set('crossfade_seconds', v); pushMusic('set_crossfade', { enabled: s.crossfade_enabled, seconds: v }) }
  function onCrossfadeOnSkip(v) { set('crossfade_on_skip', v); pushMusic('set_crossfade_on_skip', { enabled: v }) }
  function onAutoplay(v) { set('music_autoplay', v); pushMusic('set_autoplay', { enabled: v }) }
  function onAudioQuality(v) { set('music_audio_quality', v); pushMusic('set_audio_quality', { quality: v }) }
  function onDucking(v) { set('music_disable_ducking', v); pushMusic('set_ducking', { enabled: !v }) }

  // -- Equalizer --
  let eqGains = $derived(Array.isArray(s.eq_gains) && s.eq_gains.length === 10 ? s.eq_gains : EQ_BANDS.map(() => 0))
  function onEqEnabled(v) { set('eq_enabled', v); pushMusic('set_equalizer', { enabled: v, gains: eqGains }) }
  function onEqBand(i, v) {
    const next = [...eqGains]
    next[i] = v
    set('eq_gains', next)
    pushMusic('set_equalizer', { enabled: s.eq_enabled, gains: next })
  }
  function applyPreset(name) {
    const gains = EQ_PRESETS[name]
    if (!gains) return
    set('eq_gains', gains)
    pushMusic('set_equalizer', { enabled: s.eq_enabled, gains })
  }
  function resetEq() {
    const gains = EQ_BANDS.map(() => 0)
    set('eq_gains', gains)
    pushMusic('set_equalizer', { enabled: s.eq_enabled, gains })
  }

  // -- WhatsApp --
  let waBusy = $state(false)
  async function toggleWhatsapp(v) {
    waBusy = true
    try {
      await api.toggleWhatsappConnection(v)
      s = { ...s, whatsapp_enabled: v }
    } catch (e) {
      saveNote = e.message
    } finally {
      waBusy = false
    }
  }

  let rules = $state([])
  let rulesLoading = $state(false)
  let ruleDialogOpen = $state(false)
  let editingRule = $state(null)
  let ruleForm = $state(emptyRule())
  let waChats = $state([])

  function emptyRule() {
    return {
      id: '', name: '', enabled: true, contacts: [], always: false,
      days: [], start: '08:00', end: '20:00', timezone: 'Europe/Madrid', prompt: '',
    }
  }

  async function loadRules() {
    rulesLoading = true
    try {
      rules = await api.getWhatsappRules()
    } catch {
      rules = []
    } finally {
      rulesLoading = false
    }
  }

  function openAddRule() {
    editingRule = null
    ruleForm = emptyRule()
    ruleDialogOpen = true
    loadWaChatsForPicker()
  }

  function openEditRule(rule) {
    editingRule = rule.id
    ruleForm = { ...emptyRule(), ...rule }
    ruleDialogOpen = true
    loadWaChatsForPicker()
  }

  async function loadWaChatsForPicker() {
    try {
      waChats = await api.listChats()
    } catch {
      waChats = []
    }
  }

  const DAY_LABELS = ['L', 'M', 'X', 'J', 'V', 'S', 'D']
  function toggleDay(i) {
    ruleForm.days = ruleForm.days.includes(i)
      ? ruleForm.days.filter((d) => d !== i)
      : [...ruleForm.days, i].sort()
  }

  function toggleRuleContact(chat) {
    const exists = ruleForm.contacts.some((c) => c.chat_id === chat.chatId)
    ruleForm.contacts = exists
      ? ruleForm.contacts.filter((c) => c.chat_id !== chat.chatId)
      : [...ruleForm.contacts, { chat_id: chat.chatId, name: chat.name }]
  }

  async function saveRule() {
    if (!ruleForm.name.trim()) return
    try {
      if (editingRule) {
        await api.updateWhatsappRule(editingRule, ruleForm)
      } else {
        await api.addWhatsappRule(ruleForm)
      }
      ruleDialogOpen = false
      await loadRules()
    } catch (e) {
      saveNote = e.message
    }
  }

  async function deleteRule(id) {
    if (!confirm('¿Eliminar esta regla?')) return
    await api.deleteWhatsappRule(id)
    await loadRules()
  }

  async function moveRule(id, delta) {
    await api.moveWhatsappRule(id, delta)
    await loadRules()
  }

  async function toggleRuleEnabled(rule) {
    await api.updateWhatsappRule(rule.id, { ...rule, enabled: !rule.enabled })
    await loadRules()
  }

  // -- APIs --
  let apiKeys = $state({})
  let apiKeysLoading = $state(false)
  let apiKeysSaved = $state('')
  let showKey = $state({})
  const API_KEY_FIELDS = [
    { key: 'gemini_api_key', label: 'Gemini (Google AI)' },
    { key: 'tmdb_api_key', label: 'TMDB' },
    { key: 'mal_client_id', label: 'MyAnimeList' },
    { key: 'cardtrader_jwt', label: 'CardTrader' },
    { key: 'ytmusic_app_token', label: 'YouTube Music' },
  ]

  async function loadApiKeys() {
    apiKeysLoading = true
    try {
      apiKeys = await api.getApiKeys()
    } catch {
      apiKeys = {}
    } finally {
      apiKeysLoading = false
    }
  }

  async function saveApiKeys() {
    try {
      await api.setApiKeys(apiKeys)
      apiKeysSaved = 'Guardado — reinicia Jarvis para aplicar los cambios.'
    } catch (e) {
      apiKeysSaved = `Error: ${e.message}`
    }
  }

  // -- General --
  let profile = $state({ name: '', about: '' })
  let autofill = $state({})
  let audioDevices = $state({ inputs: [], outputs: [] })

  async function loadGeneral() {
    try { profile = await api.getProfile() } catch { /* keep defaults */ }
    try { audioDevices = await api.getAudioDevices() } catch { /* keep defaults */ }
  }

  async function saveProfile() {
    try { await api.setProfile(profile) } catch (e) { saveNote = e.message }
  }

  function setAutofill(field, value) {
    const next = { ...(s.form_profile || {}), [field]: value }
    set('form_profile', next)
  }

  async function doReset() {
    if (!confirm('Esto borrará TODOS los datos de Jarvis y reiniciará la app. ¿Continuar?')) return
    if (!confirm('De verdad, esto no se puede deshacer. ¿Seguro?')) return
    try {
      await api.resetJarvis()
    } catch (e) {
      saveNote = e.message
    }
  }

  // -- Anime --
  let animeSfw = $state(true)
  async function loadAnimeSfw() {
    try {
      const res = await api.getAnimeSfw()
      animeSfw = res.sfw
    } catch { /* keep default */ }
  }
  function onAnimeSfw(v) {
    animeSfw = v
    api.setAnimeSfw(v).catch((e) => (saveNote = e.message))
  }

  let animeAutoplay = $state(true)
  let animeAutocomplete = $state(true)
  async function loadAnimePlaybackPrefs() {
    try {
      const [ap, ac] = await Promise.all([api.getAnimeAutoplay(), api.getAnimeAutocomplete()])
      animeAutoplay = ap.autoplay
      animeAutocomplete = ac.autocomplete
    } catch { /* keep defaults */ }
  }
  function onAnimeAutoplay(v) {
    animeAutoplay = v
    api.setAnimeAutoplay(v).catch((e) => (saveNote = e.message))
  }
  function onAnimeAutocomplete(v) {
    animeAutocomplete = v
    api.setAnimeAutocomplete(v).catch((e) => (saveNote = e.message))
  }

  // -- Rendimiento --
  function onPerformancePlan(v) {
    set('performance_plan', v)
    api.setPerformancePlan(v).catch(() => {})
  }

  // -- Inicio / LAN dashboard --
  let qrOpen = $state(false)
  let qrData = $state(null)
  let qrLoading = $state(false)
  async function showQr() {
    if (!s.lan_dashboard_enabled) set('lan_dashboard_enabled', true)
    qrOpen = true
    qrLoading = true
    try {
      qrData = await api.getLanDashboardQr()
    } catch (e) {
      qrData = { error: e.message }
    } finally {
      qrLoading = false
    }
  }

  // -- Acerca de --
  let about = $state(null)

  onMount(async () => {
    loaded = false
    try {
      s = await api.getSettings()
    } catch (e) {
      saveNote = e.message
    }
    loaded = true
    loadRules()
    loadApiKeys()
    loadGeneral()
    loadAnimeSfw()
    loadAnimePlaybackPrefs()
    api.getAbout().then((r) => (about = r)).catch(() => {})
  })
</script>

<div class="panel">
  <div class="tabs">
    {#each TABS as t}
      <button class:active={tab === t} onclick={() => (tab = t)}>{t}</button>
    {/each}
  </div>

  {#if saveNote}<div class="save-note">{saveNote}</div>{/if}

  <div class="content">
    {#if !loaded}
      <div class="empty">Cargando ajustes…</div>

    {:else if tab === 'Música'}
      <section class="card">
        <h3>REPRODUCCIÓN</h3>
        <label class="row"><span>Crossfade entre canciones</span>
          <input type="checkbox" checked={s.crossfade_enabled} onchange={(e) => onCrossfadeEnabled(e.target.checked)} />
        </label>
        <label class="row" class:disabled={!s.crossfade_enabled}><span>Duración del fundido</span>
          <input type="number" min="1" max="15" value={s.crossfade_seconds ?? 3}
            disabled={!s.crossfade_enabled}
            onchange={(e) => onCrossfadeSecs(Number(e.target.value))} /> s
        </label>
        <label class="row" class:disabled={!s.crossfade_enabled}><span>Aplicar al saltar de canción</span>
          <input type="checkbox" checked={s.crossfade_on_skip} disabled={!s.crossfade_enabled}
            onchange={(e) => onCrossfadeOnSkip(e.target.checked)} />
        </label>
      </section>
      <section class="card">
        <h3>AUDIO</h3>
        <label class="row"><span>Reproducción automática</span>
          <input type="checkbox" checked={s.music_autoplay} onchange={(e) => onAutoplay(e.target.checked)} />
        </label>
        <label class="row"><span>Calidad de audio</span>
          <select value={s.music_audio_quality || 'best'} onchange={(e) => onAudioQuality(e.target.value)}>
            <option value="best">Máxima</option>
            <option value="opus">Opus</option>
            <option value="m4a">AAC-LC</option>
            <option value="low">AAC ahorro</option>
          </select>
        </label>
        <label class="row"><span>Evitar que Windows baje el volumen</span>
          <input type="checkbox" checked={s.music_disable_ducking} onchange={(e) => onDucking(e.target.checked)} />
        </label>
      </section>

    {:else if tab === 'Ecualizador'}
      <section class="card">
        <h3>ECUALIZADOR</h3>
        <label class="row"><span>Activar ecualizador</span>
          <input type="checkbox" checked={s.eq_enabled} onchange={(e) => onEqEnabled(e.target.checked)} />
        </label>
        <div class="row">
          <span>Preajuste</span>
          <select onchange={(e) => applyPreset(e.target.value)}>
            <option value="">Personalizado</option>
            {#each Object.keys(EQ_PRESETS) as p}<option value={p}>{p}</option>{/each}
          </select>
          <button class="ghost-btn" onclick={resetEq}>Reiniciar</button>
        </div>
        <div class="eq-bands">
          {#each EQ_BANDS as freq, i}
            <div class="eq-band">
              <input
                type="range" min="-12" max="12" orient="vertical"
                value={eqGains[i]}
                oninput={(e) => onEqBand(i, Number(e.target.value))}
              />
              <span class="eq-gain">{eqGains[i] > 0 ? '+' : ''}{eqGains[i]}</span>
              <span class="eq-freq">{fmtFreq(freq)}</span>
            </div>
          {/each}
        </div>
      </section>

    {:else if tab === 'YouTube'}
      <section class="card">
        <h3>REPRODUCTOR FLOTANTE (PIP)</h3>
        <label class="row"><span>Recordar tamaño y posición</span>
          <input type="checkbox" checked={s.youtube_remember_pip} onchange={(e) => set('youtube_remember_pip', e.target.checked)} />
        </label>
      </section>

    {:else if tab === 'WhatsApp'}
      <section class="card">
        <h3>CONEXIÓN</h3>
        <label class="row"><span>WhatsApp activado</span>
          <input type="checkbox" checked={s.whatsapp_enabled} disabled={waBusy} onchange={(e) => toggleWhatsapp(e.target.checked)} />
        </label>
      </section>
      <section class="card">
        <h3>NOTIFICACIONES</h3>
        <label class="row"><span>Ventanas flotantes</span>
          <input type="checkbox" checked={s.whatsapp_notifications} onchange={(e) => set('whatsapp_notifications', e.target.checked)} />
        </label>
        <label class="row"><span>Duración en pantalla</span>
          <select value={s.whatsapp_notification_duration_s ?? 5} onchange={(e) => set('whatsapp_notification_duration_s', Number(e.target.value))}>
            <option value={3}>3s</option><option value={5}>5s</option><option value={7}>7s</option>
            <option value={10}>10s</option><option value={15}>15s</option><option value={0}>Hasta cerrarla</option>
          </select>
        </label>
      </section>
      <section class="card">
        <h3>PROCESAMIENTO CON IA</h3>
        <label class="row"><span>Traducir mensajes automáticamente</span>
          <input type="checkbox" checked={s.whatsapp_auto_translate} onchange={(e) => set('whatsapp_auto_translate', e.target.checked)} />
        </label>
        <label class="row"><span>Transcribir audios automáticamente</span>
          <input type="checkbox" checked={s.whatsapp_auto_transcribe} onchange={(e) => set('whatsapp_auto_transcribe', e.target.checked)} />
        </label>
      </section>
      <section class="card">
        <div class="card-head">
          <h3>REGLAS DE RESPUESTA AUTOMÁTICA</h3>
          <button class="ghost-btn" onclick={openAddRule}>Añadir regla</button>
        </div>
        {#if rulesLoading}
          <div class="empty">Cargando…</div>
        {:else if !rules.length}
          <div class="empty">Sin reglas todavía.</div>
        {/if}
        {#each rules as rule, i (rule.id)}
          <div class="rule-card">
            <input type="checkbox" checked={rule.enabled} onchange={() => toggleRuleEnabled(rule)} />
            <div class="rule-info">
              <div class="rule-name">{rule.name}</div>
              <div class="rule-meta">
                {rule.always ? 'Siempre activa' : `${rule.days?.map((d) => DAY_LABELS[d]).join('') || ''} ${rule.start}-${rule.end}`}
                · {rule.contacts?.length || 0} contacto(s)
              </div>
            </div>
            <button class="icon-btn" disabled={i === 0} onclick={() => moveRule(rule.id, -1)} title="Subir prioridad"><Icon name="up" size={14} /></button>
            <button class="icon-btn" disabled={i === rules.length - 1} onclick={() => moveRule(rule.id, 1)} title="Bajar prioridad"><Icon name="down" size={14} /></button>
            <button class="icon-btn" onclick={() => openEditRule(rule)} title="Editar"><Icon name="edit" size={14} /></button>
            <button class="icon-btn danger" onclick={() => deleteRule(rule.id)} title="Eliminar"><Icon name="trash" size={14} /></button>
          </div>
        {/each}
      </section>

    {:else if tab === 'Anime'}
      <section class="card">
        <h3>CONTENIDO</h3>
        <label class="row"><span>Modo seguro (SFW)</span>
          <input type="checkbox" checked={animeSfw} onchange={(e) => onAnimeSfw(e.target.checked)} />
        </label>
      </section>

      <section class="card">
        <h3>REPRODUCCIÓN</h3>
        <label class="row"><span>Siguiente episodio automático</span>
          <input type="checkbox" checked={animeAutoplay}
                 onchange={(e) => onAnimeAutoplay(e.target.checked)} />
        </label>
        <label class="row"><span>Marcar visto y sincronizar con MyAnimeList al 85%</span>
          <input type="checkbox" checked={animeAutocomplete}
                 onchange={(e) => onAnimeAutocomplete(e.target.checked)} />
        </label>
      </section>

    {:else if tab === 'Gmail' || tab === 'Drive'}
      <div class="empty">Sin ajustes disponibles todavía.</div>

    {:else if tab === 'APIs'}
      <section class="card">
        <h3>CLAVES DE API</h3>
        {#if apiKeysLoading}
          <div class="empty">Cargando…</div>
        {:else}
          {#each API_KEY_FIELDS as f}
            <label class="row">
              <span><span class="dot" class:on={apiKeys[f.key]}></span> {f.label}</span>
              <div class="key-input-wrap">
                <input
                  type={showKey[f.key] ? 'text' : 'password'}
                  value={apiKeys[f.key] || ''}
                  oninput={(e) => (apiKeys[f.key] = e.target.value)}
                  placeholder="Sin configurar"
                />
                <button class="eye-btn" class:active={showKey[f.key]} onclick={() => (showKey[f.key] = !showKey[f.key])} title={showKey[f.key] ? 'Ocultar' : 'Mostrar'}><Icon name="eye" size={15} /></button>
              </div>
            </label>
          {/each}
          <div class="apis-actions">
            <button class="primary" onclick={saveApiKeys}>Guardar cambios</button>
            {#if apiKeysSaved}<span class="save-note">{apiKeysSaved}</span>{/if}
          </div>
        {/if}
      </section>

    {:else if tab === 'General'}
      <section class="card">
        <h3>TU PERFIL</h3>
        <label class="row"><span>Tu nombre</span>
          <input type="text" bind:value={profile.name} onblur={saveProfile} />
        </label>
        <label class="row wide"><span>Sobre ti</span>
          <textarea rows="3" bind:value={profile.about} onblur={saveProfile} placeholder="Pronombres, edad, gustos..."></textarea>
        </label>
      </section>
      <section class="card">
        <h3>AUTORRELLENO DE FORMULARIOS</h3>
        {#each [['name','Nombre completo'],['email','Email'],['phone','Teléfono'],['address','Dirección'],['city','Ciudad'],['postal_code','Código postal'],['country','País']] as [field, label]}
          <label class="row"><span>{label}</span>
            <input type="text" value={(s.form_profile || {})[field] || ''} onblur={(e) => setAutofill(field, e.target.value)} />
          </label>
        {/each}
      </section>
      <section class="card">
        <h3>DISPOSITIVOS DE AUDIO</h3>
        <label class="row"><span>Micrófono (entrada)</span>
          <select value={s.input_device_name || ''} onchange={(e) => set('input_device_name', e.target.value)}>
            <option value="">Automático (evita Bluetooth)</option>
            {#each audioDevices.inputs as d}<option value={d}>{d}</option>{/each}
          </select>
        </label>
        <label class="row"><span>Salida de audio</span>
          <select value={s.audio_output_device || ''} onchange={(e) => { set('audio_output_device', e.target.value); pushMusic('set_audio_output_device', { name: e.target.value }) }}>
            <option value="">Predeterminada</option>
            {#each audioDevices.outputs as d}<option value={d}>{d}</option>{/each}
          </select>
        </label>
        <label class="row"><span>Estado del micrófono al iniciar</span>
          <select value={s.mic_default_state || 'on'} onchange={(e) => set('mic_default_state', e.target.value)}>
            <option value="on">Encendido (por defecto)</option>
            <option value="off">Apagado (por defecto)</option>
            <option value="remember">Recordar último estado</option>
          </select>
        </label>
      </section>
      <section class="card danger-card">
        <h3>ZONA DE PELIGRO</h3>
        <button class="danger-btn" onclick={doReset}>Borrar todos los datos y reiniciar</button>
      </section>

    {:else if tab === 'Apariencia'}
      <section class="card">
        <h3>INTERFAZ</h3>
        <label class="row"><span>Visualizador de audio</span>
          <input type="checkbox" checked={s.ui_show_visualizer} onchange={(e) => set('ui_show_visualizer', e.target.checked)} />
        </label>
        <label class="row"><span>Ventana siempre encima</span>
          <input type="checkbox" checked={s.ui_always_on_top} onchange={(e) => { s = { ...s, ui_always_on_top: e.target.checked }; api.setAlwaysOnTop(e.target.checked).catch((err) => (saveNote = err.message)) }} />
        </label>
      </section>

    {:else if tab === 'Rendimiento'}
      <section class="card">
        <h3>PLAN DE ENERGÍA</h3>
        <label class="row"><span>Plan de energía</span>
          <select value={s.performance_plan || 'balanced'} onchange={(e) => onPerformancePlan(e.target.value)}>
            <option value="saver">Ahorro</option>
            <option value="balanced">Equilibrado (recomendado)</option>
            <option value="performance">Rendimiento</option>
          </select>
        </label>
        <p class="hint">En Linux/macOS algunas prioridades de proceso pueden estar limitadas por el sistema.</p>
      </section>

    {:else if tab === 'Inicio'}
      <section class="card">
        <h3>AL INICIAR</h3>
        <label class="row"><span>Espacio inicial</span>
          <select value={s.startup_space || 'last'} onchange={(e) => set('startup_space', e.target.value)}>
            <option value="last">Último usado</option>
            <option value="Normal">Inicio</option>
            <option value="WhatsApp">WhatsApp</option>
            <option value="Gmail">Correo</option>
            <option value="Drive">Drive</option>
            <option value="Music">Música</option>
            <option value="YouTube">YouTube</option>
            <option value="Calendar">Calendario</option>
          </select>
        </label>
        <label class="row"><span>Briefing matinal</span>
          <input type="checkbox" checked={s.startup_briefing_enabled} onchange={(e) => set('startup_briefing_enabled', e.target.checked)} />
        </label>
        <label class="row"><span>Modo proactivo</span>
          <input type="checkbox" checked={s.proactive_enabled} onchange={(e) => set('proactive_enabled', e.target.checked)} />
        </label>
        <label class="row"><span>Frecuencia</span>
          <input type="number" min="1" max="1440" value={s.proactive_interval_minutes ?? 60}
            onchange={(e) => set('proactive_interval_minutes', Number(e.target.value))} /> min
        </label>
        <label class="row wide"><span>Indicaciones</span>
          <textarea rows="3" value={s.proactive_prompt || ''} onblur={(e) => set('proactive_prompt', e.target.value)}></textarea>
        </label>
      </section>
      <section class="card">
        <h3>DASHBOARD REMOTO</h3>
        <label class="row"><span>Panel accesible desde el móvil</span>
          <input type="checkbox" checked={s.lan_dashboard_enabled} onchange={(e) => set('lan_dashboard_enabled', e.target.checked)} />
        </label>
        <label class="row"><span>Acceso remoto (IP pública)</span>
          <input type="checkbox" checked={s.lan_dashboard_public_mode} onchange={(e) => set('lan_dashboard_public_mode', e.target.checked)} />
        </label>
        <button class="ghost-btn" onclick={showQr}>Mostrar código QR</button>
      </section>

    {:else if tab === 'Acerca de'}
      <section class="card">
        {#if about}
          <div class="about-row"><span>Versión</span><span>{about.version}</span></div>
          <div class="about-row"><span>Interfaz</span><span>{about.interface}</span></div>
          <div class="about-row"><span>Ajustes guardados en</span><span class="mono">{about.settings_path}</span></div>
        {/if}
      </section>
    {/if}
  </div>
</div>

{#if ruleDialogOpen}
  <div class="modal-backdrop" onclick={() => (ruleDialogOpen = false)}>
    <div class="modal" onclick={(e) => e.stopPropagation()}>
      <h3>{editingRule ? 'Editar regla' : 'Nueva regla'}</h3>
      <label>Nombre<input type="text" bind:value={ruleForm.name} /></label>
      <label class="toggle-row"><input type="checkbox" bind:checked={ruleForm.enabled} /> Regla activa</label>

      <div class="contact-picker">
        <div class="attendees-label">Contactos</div>
        {#if ruleForm.contacts.length}
          <div class="attendee-chips">
            {#each ruleForm.contacts as c}
              <span class="chip">{c.name} <button onclick={() => (ruleForm.contacts = ruleForm.contacts.filter((x) => x.chat_id !== c.chat_id))}>×</button></span>
            {/each}
          </div>
        {/if}
        <div class="chat-pick-list">
          {#each waChats.slice(0, 30) as chat}
            <label class="chat-pick-row">
              <input type="checkbox" checked={ruleForm.contacts.some((c) => c.chat_id === chat.chatId)} onchange={() => toggleRuleContact(chat)} />
              {chat.name}
            </label>
          {/each}
        </div>
      </div>

      <label class="toggle-row"><input type="checkbox" bind:checked={ruleForm.always} /> Siempre activa</label>

      {#if !ruleForm.always}
        <div class="day-toggles">
          {#each DAY_LABELS as label, i}
            <button class="day-btn" class:active={ruleForm.days.includes(i)} onclick={() => toggleDay(i)}>{label}</button>
          {/each}
        </div>
        <div class="row3">
          <label>Inicio<input type="time" bind:value={ruleForm.start} /></label>
          <label>Fin<input type="time" bind:value={ruleForm.end} /></label>
          <label>Zona horaria<input type="text" bind:value={ruleForm.timezone} /></label>
        </div>
      {/if}

      <label>Instrucciones para la IA<textarea rows="4" bind:value={ruleForm.prompt}></textarea></label>

      <div class="modal-actions">
        <div class="spacer"></div>
        <button class="ghost" onclick={() => (ruleDialogOpen = false)}>Cancelar</button>
        <button class="primary" onclick={saveRule}>Guardar</button>
      </div>
    </div>
  </div>
{/if}

{#if qrOpen}
  <div class="modal-backdrop" onclick={() => (qrOpen = false)}>
    <div class="modal narrow" onclick={(e) => e.stopPropagation()}>
      <h3>Emparejar teléfono</h3>
      {#if qrLoading}
        <div class="empty">Generando código…</div>
      {:else if qrData?.error}
        <div class="error">{qrData.error}</div>
      {:else if qrData}
        <img class="qr-img" src={`data:image/png;base64,${qrData.qr_png_base64}`} alt="QR" />
        <input class="qr-url" type="text" readonly value={qrData.url} onclick={(e) => e.target.select()} />
      {/if}
      <div class="modal-actions">
        <div class="spacer"></div>
        <button class="ghost" onclick={() => (qrOpen = false)}>Cerrar</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 16px; gap: 10px; }

  .tabs { display: flex; flex-wrap: wrap; gap: 6px; flex-shrink: 0; }
  .tabs button {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: 999px;
    color: var(--text-med);
    padding: 5px 14px;
    font-size: 12px;
    cursor: pointer;
  }
  .tabs button.active { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }

  .save-note { font-size: 12px; color: var(--pri); flex-shrink: 0; }

  .content { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }

  .empty { color: var(--text-med); font-size: 12px; padding: 20px; text-align: center; }

  .card {
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 14px 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .card-head { display: flex; align-items: center; justify-content: space-between; }

  .card h3 { margin: 0 0 4px; font-size: 10px; letter-spacing: 0.08em; color: var(--text-med); }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 13px;
    color: var(--text-dim);
    padding: 4px 0;
  }
  .row.wide { flex-direction: column; align-items: stretch; }
  .row.disabled { opacity: 0.5; }

  .row select, .row input[type="text"], .row input[type="number"], .row textarea {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 6px 10px;
    font-size: 12px;
    font-family: inherit;
  }

  .row textarea { width: 100%; resize: vertical; }

  .ghost-btn {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 5px 12px;
    font-size: 12px;
    cursor: pointer;
  }

  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--text-med); margin-right: 4px; }
  .dot.on { background: var(--green); }

  .key-input-wrap { display: flex; gap: 4px; align-items: center; }
  .key-input-wrap input { width: 220px; }
  .eye-btn {
    display: flex; align-items: center; justify-content: center;
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(182,196,255,0.15);
    border-radius: 9px;
    width: 30px; height: 30px;
    color: var(--text-med);
    cursor: pointer;
  }
  .eye-btn.active { color: var(--pri); border-color: rgba(182,196,255,0.45); }

  .apis-actions { display: flex; align-items: center; gap: 12px; margin-top: 6px; }

  .primary {
    background: var(--pri-gho);
    border: 1px solid var(--pri-dim);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 7px 16px;
    font-size: 12px;
    cursor: pointer;
  }

  .danger-card { border-color: rgba(255,94,130,0.25); }
  .danger-btn {
    background: transparent;
    border: 1px solid rgba(255,94,130,0.4);
    border-radius: var(--radius-sm);
    color: var(--red);
    padding: 8px 14px;
    font-size: 12px;
    cursor: pointer;
    align-self: flex-start;
  }

  .eq-bands { display: flex; justify-content: space-between; gap: 6px; padding: 10px 0; }
  .eq-band { display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .eq-band input[type="range"] { writing-mode: vertical-lr; direction: rtl; height: 100px; accent-color: var(--pri); }
  .eq-gain { font-size: 10px; color: var(--text-med); font-family: var(--font-mono); }
  .eq-freq { font-size: 10px; color: var(--text-med); }

  .rule-card {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--panel-2);
    border-radius: var(--radius-sm);
    padding: 8px 10px;
  }
  .rule-info { flex: 1; min-width: 0; }
  .rule-name { font-size: 13px; color: var(--text); }
  .rule-meta { font-size: 11px; color: var(--text-med); }
  .icon-btn {
    display: flex; align-items: center; justify-content: center;
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    width: 26px; height: 26px;
    cursor: pointer;
  }
  .icon-btn:disabled { opacity: 0.3; cursor: not-allowed; }
  .icon-btn.danger:hover { color: var(--red); }

  .about-row { display: flex; justify-content: space-between; gap: 12px; padding: 4px 0; font-size: 12px; color: var(--text-med); }
  .about-row span:last-child { color: var(--text-dim); }
  .mono { font-family: var(--font-mono); font-size: 11px; }

  .hint { font-size: 11px; color: var(--text-med); margin: 4px 0 0; }

  /* Modals: same visual language as other panels */
  .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 1000; }
  .modal { width: min(480px, 90vw); max-height: 85vh; overflow-y: auto; background: var(--panel); border: 1px solid var(--border-a); border-radius: var(--radius); padding: 20px; display: flex; flex-direction: column; gap: 10px; }
  .modal.narrow { width: min(320px, 90vw); align-items: center; text-align: center; }
  .modal h3 { margin: 0 0 4px; color: var(--text); }
  .modal label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-med); }
  .modal input, .modal textarea { background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm); color: var(--text); padding: 7px 10px; font-size: 13px; font-family: inherit; }
  .toggle-row { flex-direction: row !important; align-items: center; gap: 6px !important; }
  .row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
  .day-toggles { display: flex; gap: 4px; }
  .day-btn { width: 32px; height: 32px; border-radius: var(--radius-sm); border: 1px solid var(--border-a); background: var(--panel-2); color: var(--text-med); cursor: pointer; }
  .day-btn.active { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }
  .contact-picker { border-top: 1px solid var(--border-a); padding-top: 8px; display: flex; flex-direction: column; gap: 6px; }
  .attendees-label { font-size: 11px; color: var(--text-med); }
  .attendee-chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { background: var(--pri-gho); color: var(--pri); border-radius: 999px; padding: 3px 8px; font-size: 11px; display: flex; align-items: center; gap: 4px; }
  .chip button { background: none; border: none; color: inherit; cursor: pointer; }
  .chat-pick-list { max-height: 120px; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
  .chat-pick-row { flex-direction: row !important; align-items: center; gap: 6px !important; font-size: 12px; color: var(--text-dim); }
  .modal-actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
  .spacer { flex: 1; }
  .modal-actions button { border-radius: var(--radius-sm); padding: 7px 14px; font-size: 12px; cursor: pointer; border: 1px solid var(--border-a); }
  .modal-actions .ghost { background: transparent; color: var(--text-dim); }
  .modal-actions .primary { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }
  .qr-img { width: 200px; height: 200px; border-radius: 8px; background: #fff; padding: 8px; }
  .qr-url { width: 100%; text-align: center; font-size: 11px; }
  .error { color: var(--red); font-size: 12px; }
</style>
