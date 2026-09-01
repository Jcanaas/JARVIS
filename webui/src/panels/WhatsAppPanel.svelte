<script>
  import { onDestroy } from 'svelte'
  import * as api from '../api.js'
  import { events } from '../stores/sse.js'
  import Icon from '../lib/Icon.svelte'

  // Mirrors actions/whatsapp_ui.py's WhatsAppWindow as closely as a browser
  // sandbox allows: chat list with search/filters, day-separated thread
  // with media/audio/ack ticks, a reply/edit-aware composer with
  // attach-file, voice notes (browser MediaRecorder — the bridge accepts
  // any audio file, not just WhatsApp's native ptt codec) and an AI
  // "Sugerir" draft, and the QR-linking screen when the bridge has no
  // session yet. Not ported: native right-click context menu (hover
  // buttons instead), reactions, polls, @mention highlighting, message
  // search — flagged as remaining gaps, not silently dropped.

  // PanelHost keeps this component mounted (display:none) instead of
  // destroying it on switch, so the open chat/scroll position survive —
  // `active` only gates the status poll below so it doesn't keep hitting
  // the bridge every 6s for a panel nobody is looking at.
  let { active = true } = $props()

  let bridgeStatus = $state(null)
  let statusPoll

  // Not every chat has a photo set — track which chat_ids 404'd on the
  // avatar proxy so those permanently fall back to the initial letter
  // instead of retrying every render.
  let avatarFailed = $state(new Set())
  function markAvatarFailed(chatId) {
    avatarFailed = new Set(avatarFailed).add(chatId)
  }

  let chats = $state([])
  let loadingChats = $state(true)
  let chatsError = $state('')
  let chatFilter = $state('all') // all | unread | groups
  let chatSearch = $state('')

  let activeChatId = $state('')
  let messages = $state([])
  let loadingMessages = $state(false)
  let draft = $state('')
  let sending = $state(false)
  let suggesting = $state(false)

  let replyTo = $state(null)
  let editingMessage = $state(null)

  let recording = $state(false)
  let mediaRecorder = null
  let recordedChunks = []

  let fileInput = $state()
  let acksTimer

  function friendlyError(message) {
    if (message === 'not_ready') return 'WhatsApp todavía no está listo (el puente no está conectado).'
    return message
  }

  let wasReady = false

  async function loadStatus() {
    try {
      bridgeStatus = await api.getWhatsappStatus()
    } catch {
      bridgeStatus = { ready: false, state: 'starting' }
    }
    const isReady = !!(bridgeStatus?.ready || bridgeStatus?.state === 'ready')
    // Reload the chat list only on the not-ready → ready transition (first
    // load, or bridge reconnect) — not on every 6s status tick, which would
    // otherwise re-fetch and re-render the whole list every poll even when
    // nothing changed (the refresh button and `whatsapp_notification`/
    // `open_chat` SSE events already cover in-session updates).
    if (isReady && !wasReady) {
      loadChats()
    }
    wasReady = isReady
  }

  async function retryLink() {
    try {
      await api.whatsappReconnect()
    } catch { /* status poll will reflect the outcome */ }
    loadStatus()
  }

  let filteredChats = $derived(
    chats
      .filter((c) => {
        if (chatFilter === 'unread') return (c.unread || 0) > 0
        if (chatFilter === 'groups') return !!c.isGroup
        return true
      })
      .filter((c) => !chatSearch.trim() || (c.name || '').toLowerCase().includes(chatSearch.trim().toLowerCase()))
  )

  async function loadChats() {
    loadingChats = true
    chatsError = ''
    try {
      chats = await api.listChats()
    } catch (e) {
      chatsError = friendlyError(e.message)
    } finally {
      loadingChats = false
    }
  }

  async function openChat(chatId) {
    activeChatId = chatId
    loadingMessages = true
    replyTo = null
    editingMessage = null
    try {
      messages = await api.getMessages(chatId)
      pollAcks()
      api.markChatRead(chatId).catch(() => {})
      chats = chats.map((c) => (c.chatId === chatId ? { ...c, unread: 0 } : c))
    } catch (e) {
      chatsError = friendlyError(e.message)
      messages = []
    } finally {
      loadingMessages = false
    }
  }

  function pollAcks() {
    clearTimeout(acksTimer)
    const ids = messages.filter((m) => m.fromMe && m.id).map((m) => m.id).slice(-20)
    if (!ids.length) return
    api.getMessageAcks(ids).then((acks) => {
      messages = messages.map((m) => (acks[m.id] != null ? { ...m, ack: acks[m.id] } : m))
    }).catch(() => {})
    acksTimer = setTimeout(pollAcks, 8000)
  }

  async function send() {
    const text = draft.trim()
    if (!text || !activeChatId) return
    sending = true
    try {
      if (editingMessage) {
        await api.editWhatsappMessage(editingMessage.id, text)
        editingMessage = null
      } else {
        await api.sendMessage(activeChatId, text)
      }
      draft = ''
      replyTo = null
      await openChat(activeChatId)
    } catch (e) {
      chatsError = e.message
    } finally {
      sending = false
    }
  }

  function onDraftKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  function startReply(msg) {
    editingMessage = null
    replyTo = msg
  }

  function startEdit(msg) {
    replyTo = null
    editingMessage = msg
    draft = msg.body || ''
  }

  function cancelComposerContext() {
    replyTo = null
    editingMessage = null
    draft = ''
  }

  function copyText(text) {
    navigator.clipboard?.writeText(text || '').catch(() => {})
  }

  async function retrySend(msg) {
    try {
      await api.sendMessage(activeChatId, msg.body || '')
      await openChat(activeChatId)
    } catch (e) {
      chatsError = e.message
    }
  }

  function onFilePicked(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (file) sendFile(file)
  }

  async function sendFile(file) {
    if (!activeChatId) return
    try {
      await api.sendWhatsappMedia(activeChatId, file)
      await openChat(activeChatId)
    } catch (e) {
      chatsError = e.message
    }
  }

  function onDrop(e) {
    e.preventDefault()
    const file = e.dataTransfer?.files?.[0]
    if (file) sendFile(file)
  }

  async function toggleRecording() {
    if (recording) {
      mediaRecorder?.stop()
      recording = false
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      recordedChunks = []
      mediaRecorder = new MediaRecorder(stream)
      mediaRecorder.ondataavailable = (e) => e.data.size && recordedChunks.push(e.data)
      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(recordedChunks, { type: 'audio/webm' })
        const file = new File([blob], `nota-de-voz-${Date.now()}.webm`, { type: 'audio/webm' })
        await sendFile(file)
      }
      mediaRecorder.start()
      recording = true
    } catch (e) {
      chatsError = 'No se pudo acceder al micrófono: ' + e.message
    }
  }

  async function suggestReply() {
    if (!activeChatId) return
    suggesting = true
    try {
      const res = await api.suggestWhatsappReply(activeChatId)
      if (res.draft) draft = res.draft
    } catch (e) {
      chatsError = e.message
    } finally {
      suggesting = false
    }
  }

  async function transcribe(msg) {
    // Match by id rather than object identity: openChat()/the
    // whatsapp_notification handler replace `messages` wholesale whenever a
    // new message lands in this chat, which would otherwise silently drop
    // the result if that happens while a transcription (a real Gemini
    // round-trip, a couple seconds) is still in flight.
    const id = msg.id
    messages = messages.map((m) => (m.id === id ? { ...m, _transcribing: true } : m))
    try {
      const res = await api.transcribeWhatsappAudio(msg)
      messages = messages.map((m) => (m.id === id ? { ...m, _transcript: res.text, _transcribing: false } : m))
    } catch (e) {
      messages = messages.map((m) => (m.id === id ? { ...m, _transcript: `Error: ${e.message}`, _transcribing: false } : m))
    }
  }

  function activeChatName() {
    return chats.find((c) => c.chatId === activeChatId)?.name || activeChatId
  }

  function dayLabel(ms) {
    if (!ms) return ''
    const d = new Date(ms)
    const today = new Date()
    const yesterday = new Date(today)
    yesterday.setDate(today.getDate() - 1)
    if (d.toDateString() === today.toDateString()) return 'Hoy'
    if (d.toDateString() === yesterday.toDateString()) return 'Ayer'
    return d.toLocaleDateString('es-ES', { day: 'numeric', month: 'long', year: d.getFullYear() !== today.getFullYear() ? 'numeric' : undefined })
  }

  function msgTime(ms) {
    if (!ms) return ''
    return new Date(ms).toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' })
  }

  let messageGroups = $derived.by(() => {
    const groups = []
    let lastDay = ''
    for (const m of messages) {
      const ts = m.sentAtMs || m.waTs || m.timestamp
      const day = ts ? new Date(ts).toDateString() : ''
      if (day && day !== lastDay) {
        groups.push({ type: 'day', label: dayLabel(ts) })
        lastDay = day
      }
      groups.push({ type: 'msg', msg: m, time: msgTime(ts) })
    }
    return groups
  })

  function ackIcon(ack) {
    if (ack === -1) return '⚠'
    if (ack >= 3) return '✓✓'
    if (ack === 2) return '✓✓'
    if (ack === 1) return '✓'
    return ''
  }

  const unsubscribe = events.subscribe(({ type, data }) => {
    if (type === 'open_chat' && data.contact) {
      openChat(data.contact)
    } else if (type === 'whatsapp_notification' && data.chat_id === activeChatId) {
      openChat(activeChatId)
    } else if (type === 'whatsapp_notification') {
      loadChats()
    }
  })

  $effect(() => {
    if (!active) return
    loadStatus()
    statusPoll = setInterval(loadStatus, 6000)
    return () => clearInterval(statusPoll)
  })

  onDestroy(() => {
    unsubscribe()
    clearInterval(statusPoll)
    clearTimeout(acksTimer)
  })
</script>

<div class="panel">
  {#if !bridgeStatus?.ready && bridgeStatus?.state !== 'ready'}
    <div class="link-screen">
      {#if bridgeStatus?.state === 'qr' && bridgeStatus?.qr}
        <img class="qr-img" src={bridgeStatus.qr} alt="Código QR de WhatsApp" />
        <p>Escanea este código con WhatsApp en tu teléfono para vincular.</p>
      {:else}
        <div class="spinner"></div>
        <p>{bridgeStatus?.state === 'auth_failure' ? 'Fallo de autenticación.' : 'Conectando con WhatsApp…'}</p>
      {/if}
      <button class="retry-btn" onclick={retryLink}>Reintentar</button>
    </div>
  {:else}
    <aside class="chat-list">
      <div class="chat-list-head">
        <input class="chat-search" type="text" placeholder="Buscar chats…" bind:value={chatSearch} />
        <button class="icon-btn" onclick={loadChats} title="Actualizar"><Icon name="whatsapp" size={14} /></button>
      </div>
      <div class="filter-chips">
        <button class:active={chatFilter === 'all'} onclick={() => (chatFilter = 'all')}>Todo</button>
        <button class:active={chatFilter === 'unread'} onclick={() => (chatFilter = 'unread')}>Sin leer</button>
        <button class:active={chatFilter === 'groups'} onclick={() => (chatFilter = 'groups')}>Grupos</button>
      </div>

      {#if loadingChats}
        <div class="empty">Cargando chats…</div>
      {:else if chatsError && !chats.length}
        <div class="empty error">{chatsError}</div>
      {:else if !filteredChats.length}
        <div class="empty">Sin conversaciones.</div>
      {/if}
      {#each filteredChats as chat, ci (ci)}
        <button
          class="chat-row"
          class:active={chat.chatId === activeChatId}
          onclick={() => openChat(chat.chatId)}
        >
          <div class="avatar">
            {#if !avatarFailed.has(chat.chatId)}
              <img src={api.whatsappAvatarImageUrl(chat.chatId)} alt="" loading="lazy" onerror={() => markAvatarFailed(chat.chatId)} />
            {:else}
              {(chat.name || '?').slice(0, 1).toUpperCase()}
            {/if}
          </div>
          <div class="meta">
            <div class="name">{chat.name}</div>
            <div class="preview">{chat.preview}</div>
          </div>
          {#if chat.unread > 0}
            <span class="unread">{chat.unread}</span>
          {/if}
        </button>
      {/each}
    </aside>

    <section class="thread" ondrop={onDrop} ondragover={(e) => e.preventDefault()}>
      {#if !activeChatId}
        <div class="empty center">Selecciona una conversación</div>
      {:else}
        <header class="thread-header">
          <div class="avatar small">
            {#if !avatarFailed.has(activeChatId)}
              <img src={api.whatsappAvatarImageUrl(activeChatId)} alt="" onerror={() => markAvatarFailed(activeChatId)} />
            {:else}
              {(activeChatName() || '?').slice(0, 1).toUpperCase()}
            {/if}
          </div>
          <span>{activeChatName()}</span>
        </header>
        <div class="messages">
          {#if loadingMessages}
            <div class="empty">Cargando mensajes…</div>
          {:else if !messages.length}
            <div class="empty">Sin mensajes todavía.</div>
          {/if}
          {#each messageGroups as item, i (i)}
            {#if item.type === 'day'}
              <div class="day-sep"><span>{item.label}</span></div>
            {:else}
              {@const msg = item.msg}
              <div class="bubble-row" class:mine={msg.fromMe}>
                <div class="bubble" class:mine={msg.fromMe} class:revoked={msg.type === 'revoked'}>
                  {#if msg.type === 'revoked'}
                    <em class="revoked-text">Mensaje eliminado</em>
                  {:else if ['image', 'video', 'document'].includes(msg.type) && msg.mediaUrl}
                    <div class="media-card">
                      {#if msg.type === 'image'}
                        <img class="media-img" src={api.whatsappMediaUrl(msg.mediaUrl)} alt="" loading="lazy" />
                      {:else if msg.type === 'video'}
                        <video class="media-video" controls src={api.whatsappMediaUrl(msg.mediaUrl)}></video>
                      {:else}
                        <a class="media-doc" href={api.whatsappMediaUrl(msg.mediaUrl)} target="_blank" rel="noopener">
                          📎 {msg.filename || 'Documento'}
                        </a>
                      {/if}
                      {#if msg.caption || msg.body}<div class="caption">{msg.caption || msg.body}</div>{/if}
                    </div>
                  {:else if ['audio', 'ptt'].includes(msg.type) && msg.mediaUrl}
                    <div class="audio-card">
                      <audio controls src={api.whatsappMediaUrl(msg.mediaUrl)}></audio>
                      {#if (msg.transcription || '').trim()}
                        <div class="transcript">📝 {msg.transcription}</div>
                      {:else}
                        <button class="mini-btn" onclick={() => transcribe(msg)} disabled={msg._transcribing}>
                          {msg._transcribing ? 'Transcribiendo…' : 'Transcribir'}
                        </button>
                        {#if msg._transcript}<div class="transcript">📝 {msg._transcript}</div>{/if}
                      {/if}
                    </div>
                  {:else if msg.type === 'location' && (msg.latitude || msg.location)}
                    <a class="media-doc" href={`https://maps.google.com/?q=${msg.latitude},${msg.longitude}`} target="_blank" rel="noopener">
                      📍 Ver ubicación en el mapa
                    </a>
                  {:else}
                    {msg.body}
                  {/if}
                  {#if (msg.translation || '').trim()}
                    <div class="transcript">🌐 {msg.translation}</div>
                  {/if}
                  <div class="bubble-meta">
                    <span class="time">{item.time}</span>
                    {#if msg.fromMe}<span class="ack" class:error={msg.ack === -1}>{ackIcon(msg.ack)}</span>{/if}
                  </div>
                </div>
                <div class="bubble-actions">
                  <button title="Responder" onclick={() => startReply(msg)}><Icon name="reply" size={14} /></button>
                  {#if msg.fromMe && msg.type !== 'revoked'}
                    <button title="Editar" onclick={() => startEdit(msg)}><Icon name="edit" size={14} /></button>
                    {#if msg.ack === -1}<button title="Reintentar envío" onclick={() => retrySend(msg)}><Icon name="refresh" size={14} /></button>{/if}
                  {/if}
                  <button title="Copiar texto" onclick={() => copyText(msg.body)}><Icon name="copy" size={14} /></button>
                </div>
              </div>
            {/if}
          {/each}
        </div>

        {#if replyTo || editingMessage}
          <div class="composer-banner">
            <span>{editingMessage ? 'Editando mensaje' : `Respondiendo: ${(replyTo?.body || '').slice(0, 60)}`}</span>
            <button onclick={cancelComposerContext}>✕</button>
          </div>
        {/if}

        <div class="composer">
          <button class="attach-btn" onclick={() => fileInput.click()} title="Adjuntar archivo">
            <Icon name="attach" size={16} />
          </button>
          <input type="file" bind:this={fileInput} onchange={onFilePicked} hidden />
          <textarea
            placeholder="Escribe un mensaje…"
            bind:value={draft}
            onkeydown={onDraftKeydown}
            rows="1"
          ></textarea>
          <button class="mic-btn" class:recording onclick={toggleRecording} title="Nota de voz">
            <Icon name={recording ? 'stop' : 'mic'} size={16} />
          </button>
          <button class="suggest-btn" onclick={suggestReply} disabled={suggesting} title="Sugerir respuesta con IA">
            {suggesting ? 'Pensando…' : 'Sugerir'}
          </button>
          <button class="send-btn" onclick={send} disabled={sending || !draft.trim()} title="Enviar">
            <Icon name="send" size={16} />
          </button>
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .panel { height: 100%; min-height: 0; display: flex; }

  .link-screen {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 14px;
    color: var(--text-med);
  }
  .qr-img { width: 220px; height: 220px; background: #fff; border-radius: 8px; padding: 10px; }
  .spinner {
    width: 40px; height: 40px;
    border: 3px solid var(--border-a);
    border-top-color: var(--pri);
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .retry-btn {
    background: var(--pri-gho);
    border: 1px solid var(--pri-dim);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 7px 16px;
    font-size: 12px;
    cursor: pointer;
  }

  .chat-list {
    width: 280px;
    flex-shrink: 0;
    min-height: 0;
    border-right: 1px solid var(--border-a);
    display: flex;
    flex-direction: column;
    contain: layout paint;
  }

  .chat-list-head { display: flex; gap: 6px; padding: 10px; flex-shrink: 0; }
  .chat-search {
    flex: 1; min-width: 0;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 6px 10px;
    font-size: 12px;
  }
  .icon-btn {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    width: 30px;
    cursor: pointer;
  }

  .filter-chips { display: flex; gap: 6px; padding: 0 10px 8px; flex-shrink: 0; }
  .filter-chips button {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: 999px;
    color: var(--text-med);
    padding: 3px 10px;
    font-size: 11px;
    cursor: pointer;
  }
  .filter-chips button.active { background: var(--pri-gho); border-color: var(--pri-dim); color: var(--pri); }

  .empty { color: var(--text-med); font-size: 13px; padding: 16px; }
  .empty.center { height: 100%; display: flex; align-items: center; justify-content: center; }
  .empty.error { color: var(--red); }

  .chat-row {
    display: flex;
    align-items: center;
    gap: 10px;
    background: transparent;
    border: none;
    border-bottom: 1px solid var(--border-a);
    padding: 10px 12px;
    cursor: pointer;
    text-align: left;
    color: var(--text);
    flex-shrink: 0;
  }
  .chat-row:hover, .chat-row.active { background: var(--glass); }

  .avatar {
    width: 36px; height: 36px;
    border-radius: 50%;
    background: var(--pri-gho);
    color: var(--pri);
    display: flex; align-items: center; justify-content: center;
    font-weight: 600;
    flex-shrink: 0;
    overflow: hidden;
  }
  .avatar img { width: 100%; height: 100%; object-fit: cover; }
  .avatar.small { width: 28px; height: 28px; font-size: 12px; }

  .meta { flex: 1; min-width: 0; }
  .name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .preview { font-size: 12px; color: var(--text-med); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

  .unread {
    min-width: 18px; height: 18px; padding: 0 5px;
    border-radius: 9px;
    background: var(--green);
    color: var(--dark);
    font-size: 11px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
  }

  .thread { flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; }

  .thread-header {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border-a);
    font-weight: 600;
    flex-shrink: 0;
  }

  .messages {
    flex: 1; min-height: 0;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    contain: layout paint;
  }

  .day-sep { display: flex; justify-content: center; margin: 10px 0; }
  .day-sep span {
    font-size: 10px;
    color: var(--text-med);
    background: var(--panel-2);
    border-radius: 999px;
    padding: 3px 10px;
  }

  .bubble-row { display: flex; align-items: flex-end; gap: 6px; align-self: flex-start; max-width: 65%; }
  .bubble-row.mine { align-self: flex-end; flex-direction: row-reverse; }

  .bubble {
    background: var(--panel-2);
    border-radius: var(--radius-sm);
    padding: 8px 12px;
    font-size: 13px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .bubble.mine { background: var(--pri-gho); color: var(--pri); }
  .bubble.revoked { font-style: italic; color: var(--text-med); }
  .revoked-text { font-size: 12px; }

  .bubble-meta { display: flex; justify-content: flex-end; gap: 4px; margin-top: 3px; }
  .time { font-size: 10px; color: var(--text-med); }
  .ack { font-size: 10px; color: var(--acc); }
  .ack.error { color: var(--red); }

  .bubble-actions {
    display: flex;
    gap: 2px;
    opacity: 0;
    transition: opacity 0.1s;
  }
  .bubble-row:hover .bubble-actions { opacity: 1; }
  .bubble-actions button {
    display: flex;
    align-items: center;
    background: none;
    border: none;
    color: var(--text-med);
    cursor: pointer;
    padding: 3px 4px;
  }
  .bubble-actions button:hover { color: var(--pri); }

  .media-card { display: flex; flex-direction: column; gap: 4px; }
  .media-img { max-width: 220px; border-radius: 6px; }
  .media-video { max-width: 240px; border-radius: 6px; }
  .media-doc { color: var(--pri); text-decoration: none; font-size: 12px; }
  .caption { font-size: 12px; }

  .audio-card { display: flex; flex-direction: column; gap: 4px; }
  .audio-card audio { width: 220px; height: 32px; }
  .mini-btn {
    align-self: flex-start;
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    font-size: 10px;
    padding: 2px 8px;
    cursor: pointer;
  }
  .transcript { font-size: 11px; color: var(--text-dim); font-style: italic; }

  .composer-banner {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 16px;
    background: var(--panel-2);
    font-size: 12px;
    color: var(--text-med);
    border-top: 1px solid var(--border-a);
  }
  .composer-banner button { background: none; border: none; color: var(--text-med); cursor: pointer; }

  .composer {
    display: flex;
    align-items: flex-end;
    gap: 6px;
    padding: 10px 16px;
    border-top: 1px solid var(--border-a);
    flex-shrink: 0;
  }

  .composer textarea {
    flex: 1;
    resize: none;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 8px 12px;
    font-size: 13px;
    max-height: 120px;
    font-family: inherit;
  }
  .composer textarea:focus { outline: none; border-color: var(--pri-dim); }

  .composer button {
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--pri-gho);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    width: 36px;
    height: 36px;
    flex-shrink: 0;
    cursor: pointer;
  }
  .composer button:hover:not(:disabled) { border-color: var(--pri-dim); }
  .composer button:disabled { opacity: 0.5; cursor: not-allowed; }
  .composer .mic-btn.recording { color: var(--red); border-color: rgba(255,94,130,0.4); }
  .composer .suggest-btn { width: auto; padding: 0 10px; font-size: 12px; white-space: nowrap; }
</style>
