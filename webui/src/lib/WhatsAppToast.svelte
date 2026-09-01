<script>
  // Port of ui/__init__.py's _show_wa_notification: a floating desktop toast
  // for incoming WhatsApp messages, independent of the in-app chat refresh
  // that rides the same SSE event (see webui_host.py's show_whatsapp_notification
  // comment). Respects "Notificaciones → Ventanas flotantes" and its
  // duration setting via the toast_enabled/duration_ms fields on the event
  // payload — Settings.svelte writes those settings but has no other way to
  // reach this component live.
  import { events } from '../stores/sse.js'
  import { activePanel } from '../stores/nav.js'
  import Icon from './Icon.svelte'

  const MAX_TOASTS = 4
  let toastList = $state([])
  let nextId = 0

  events.subscribe(({ type, data }) => {
    if (type !== 'whatsapp_notification' || !data || data.toast_enabled === false) return
    const id = ++nextId
    const durationMs = data.duration_ms ?? 7000
    toastList = [...toastList, {
      id,
      title: data.title || 'WhatsApp',
      body: data.body || '',
      chatId: data.chat_id || '',
      durationMs,
    }]
    if (toastList.length > MAX_TOASTS) toastList = toastList.slice(toastList.length - MAX_TOASTS)
    if (durationMs > 0) {
      setTimeout(() => dismiss(id), durationMs)
    }
  })

  function dismiss(id) {
    toastList = toastList.filter((t) => t.id !== id)
  }

  function open(t) {
    dismiss(t.id)
    activePanel.set('whatsapp')
    if (t.chatId) events.set({ type: 'open_chat', data: { contact: t.chatId } })
  }
</script>

<div class="wa-toast-stack">
  {#each toastList as t (t.id)}
    <div class="wa-toast" onclick={() => open(t)} role="button" tabindex="0" onkeydown={(e) => e.key === 'Enter' && open(t)}>
      <div class="wa-toast-icon"><Icon name="whatsapp" size={16} /></div>
      <div class="wa-toast-body">
        <div class="wa-toast-title">{t.title}</div>
        <div class="wa-toast-text">{t.body}</div>
      </div>
      <button class="wa-toast-close" onclick={(e) => { e.stopPropagation(); dismiss(t.id) }} title="Cerrar">
        <Icon name="close" size={12} />
      </button>
    </div>
  {/each}
</div>

<style>
  .wa-toast-stack {
    position: fixed;
    bottom: 24px;
    right: 24px;
    display: flex;
    flex-direction: column-reverse;
    gap: 8px;
    z-index: 1100;
    width: min(320px, calc(100vw - 48px));
  }

  .wa-toast {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    background: var(--glass-d);
    backdrop-filter: blur(8px);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 10px 12px;
    box-shadow: 0 4px 16px var(--shadow);
    cursor: pointer;
  }
  .wa-toast:hover { border-color: var(--pri-dim); }

  .wa-toast-icon {
    flex-shrink: 0;
    width: 30px; height: 30px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: var(--pri-gho);
    color: var(--pri);
  }

  .wa-toast-body { flex: 1; min-width: 0; }
  .wa-toast-title { font-size: 12px; font-weight: 600; color: var(--text); }
  .wa-toast-text {
    font-size: 12px;
    color: var(--text-med);
    overflow: hidden;
    text-overflow: ellipsis;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
  }

  .wa-toast-close {
    flex-shrink: 0;
    background: none;
    border: none;
    color: var(--text-med);
    cursor: pointer;
    padding: 2px;
    display: flex;
  }
  .wa-toast-close:hover { color: var(--text); }
</style>
