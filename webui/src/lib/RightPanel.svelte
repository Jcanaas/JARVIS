<script>
  import { onMount, onDestroy } from 'svelte'
  import { events, voiceState, micAvailable } from '../stores/sse.js'
  import { rightPanelCollapsed } from '../stores/ui.js'
  import { getLog, sendCommand, toggleMic, toggleFullscreen } from '../api.js'
  import Icon from './Icon.svelte'
  import DownloadsList from './DownloadsList.svelte'
  import FileDropZone from './FileDropZone.svelte'

  let entries = $state([])
  let draft = $state('')
  let sending = $state(false)
  let listEl = $state()
  let filesOpen = $state(true)

  let muted = $derived($voiceState === 'MUTED')

  function scrollToBottom() {
    requestAnimationFrame(() => {
      if (listEl) listEl.scrollTop = listEl.scrollHeight
    })
  }

  async function loadHistory() {
    try {
      entries = await getLog(0)
      scrollToBottom()
    } catch (e) {
      console.warn('No se pudo cargar el historial de actividad', e)
    }
  }

  const unsubscribe = events.subscribe(({ type, data }) => {
    if (type === 'log' || type === 'toast') {
      entries = [...entries.slice(-499), { id: Date.now() + Math.random(), type, message: data.text }]
      scrollToBottom()
    }
  })

  onMount(loadHistory)
  onDestroy(unsubscribe)

  async function send() {
    const text = draft.trim()
    if (!text) return
    sending = true
    draft = ''
    try {
      await sendCommand(text)
    } catch (e) {
      entries = [...entries, { id: Date.now(), type: 'error', message: e.message }]
      scrollToBottom()
    } finally {
      sending = false
    }
  }

  function onDraftKeydown(e) {
    if (e.key === 'Enter') {
      e.preventDefault()
      send()
    }
  }

  async function onToggleMic() {
    if (!$micAvailable) return
    try { await toggleMic() } catch (e) { console.warn(e) }
  }

  async function onToggleFullscreen() {
    try { await toggleFullscreen() } catch (e) { console.warn(e) }
  }
</script>

{#if !$rightPanelCollapsed}
<aside class="right-panel">
  <section class="block log-block">
    <h3>Sesión / Actividad</h3>
    <div class="log-list" bind:this={listEl}>
      {#each entries as entry (entry.id)}
        <div class="log-entry" class:error={entry.type === 'error'} class:toast={entry.type === 'toast'}>
          {entry.message}
        </div>
      {/each}
      {#if !entries.length}
        <div class="log-empty">Sin actividad todavía.</div>
      {/if}
    </div>
  </section>

  <section class="block files-block">
    <button class="files-toggle" onclick={() => (filesOpen = !filesOpen)}>
      <h3 class="no-margin">Archivos</h3>
      <span class="chevron" class:open={filesOpen}>›</span>
    </button>
    {#if filesOpen}
      <DownloadsList />
      <FileDropZone />
    {/if}
  </section>

  <section class="block command-block">
    <h3>J.A.R.V.I.S / Enviar una orden</h3>
    <div class="command-row">
      <input
        type="text"
        placeholder="Escribe una orden…"
        bind:value={draft}
        onkeydown={onDraftKeydown}
        disabled={sending}
      />
      <button onclick={send} disabled={sending || !draft.trim()} title="Enviar">
        <Icon name="send" size={15} />
      </button>
    </div>

    <button
      class="wide-btn"
      class:muted
      onclick={onToggleMic}
      disabled={!$micAvailable}
      title={$micAvailable ? 'Silenciar/activar micrófono (F4)' : 'Sin micrófono disponible'}
    >
      {muted ? 'Micrófono silenciado' : 'Micrófono activo'}
    </button>

    <button class="wide-btn" onclick={onToggleFullscreen} title="Pantalla completa (F11)">
      Pantalla completa
    </button>
  </section>
</aside>
{/if}

<style>
  .right-panel {
    width: var(--right-w, 300px);
    flex: 0 0 var(--right-w, 300px);
    background: var(--panel);
    border-left: 1px solid var(--border-a);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .block {
    display: flex;
    flex-direction: column;
    padding: 14px 14px 10px;
  }

  .log-block {
    flex: 1;
    min-height: 0;
    border-bottom: 1px solid var(--border-a);
  }

  h3 {
    margin: 0 0 10px;
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-med);
  }

  .files-block {
    border-bottom: 1px solid var(--border-a);
  }

  .files-toggle {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    background: none;
    border: none;
    cursor: pointer;
    padding: 0;
    margin-bottom: 8px;
  }

  .no-margin { margin: 0; }

  .chevron {
    color: var(--text-med);
    transition: transform 0.15s;
  }
  .chevron.open { transform: rotate(90deg); }

  .log-list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 6px;
    contain: layout paint;
  }

  .log-entry {
    font-size: 12px;
    color: var(--text-dim);
    line-height: 1.4;
    word-break: break-word;
  }

  .log-entry.toast {
    color: var(--pri);
  }

  .log-entry.error {
    color: var(--red);
  }

  .log-empty {
    color: var(--text-med);
    font-size: 12px;
  }

  .command-row {
    display: flex;
    gap: 6px;
    margin-bottom: 8px;
  }

  .command-row input {
    flex: 1;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    padding: 7px 10px;
    font-size: 12px;
  }

  .command-row input:focus {
    outline: none;
    border-color: var(--pri-dim);
  }

  .command-row button {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 34px;
    flex-shrink: 0;
    background: var(--pri-gho);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    cursor: pointer;
  }

  .wide-btn {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 8px 10px;
    font-size: 12px;
    text-align: left;
    cursor: pointer;
    margin-top: 6px;
  }

  .wide-btn:hover:not(:disabled) {
    border-color: var(--pri-dim);
    color: var(--text);
  }

  .wide-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .wide-btn.muted {
    color: var(--red);
    border-color: rgba(255, 94, 130, 0.3);
  }
</style>
