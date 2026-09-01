<script>
  import { onMount, onDestroy } from 'svelte'
  import Header from './lib/Header.svelte'
  import NavRail from './lib/NavRail.svelte'
  import PanelHost from './lib/PanelHost.svelte'
  import RightPanel from './lib/RightPanel.svelte'
  import Footer from './lib/Footer.svelte'
  import PlaybackBar from './lib/PlaybackBar.svelte'
  import Toast from './lib/Toast.svelte'
  import WhatsAppToast from './lib/WhatsAppToast.svelte'
  import Splash from './lib/Splash.svelte'
  import SetupOverlay from './lib/SetupOverlay.svelte'
  import { startEventStream, playback, whatsappUnread, voiceState } from './stores/sse.js'
  import { activePanel, panelByKey } from './stores/nav.js'
  import { getState, getSetupStatus, toggleMic, toggleFullscreen, fireInterrupt } from './api.js'

  let ready = $state(false)
  let needsSetup = $state(false)
  let setupChecked = $state(false)

  onMount(() => {
    getSetupStatus()
      .then((res) => { needsSetup = !res.ready })
      .catch(() => { needsSetup = false })
      .finally(() => { setupChecked = true })

    startEventStream()

    // A minimum splash time avoids a jarring flash when the backend is
    // already warm; the timeout is a fallback so a slow/broken /api/state
    // never leaves the splash on screen forever.
    const minDelay = new Promise((resolve) => setTimeout(resolve, 900))
    const stateFetch = getState()
      .then((state) => {
        if (state.playback) playback.set(state.playback)
        if (typeof state.whatsapp_unread === 'number') whatsappUnread.set(state.whatsapp_unread)
        if (state.state) voiceState.set(state.state)
      })
      .catch((e) => console.warn('No se pudo leer /api/state inicial', e))

    Promise.race([
      Promise.all([minDelay, stateFetch]),
      new Promise((resolve) => setTimeout(resolve, 5000)),
    ]).then(() => {
      ready = true
    })
  })

  // Same letter-shortcut UX as ui/__init__.py's _handle_mode_shortcut_event:
  // single letter, no modifier, ignored while typing in an editable field.
  function isEditableTarget(el) {
    if (!el) return false
    const tag = el.tagName
    return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable
  }

  // Same global shortcuts as ui/__init__.py:286-296: F4 mute, F11
  // fullscreen, Esc interrupts JARVIS mid-speech. These fire regardless of
  // focus (F4/F11/Esc aren't typing keys), matching the Qt QShortcuts.
  function onKeydown(e) {
    if (e.key === 'F4') { e.preventDefault(); toggleMic().catch(() => {}); return }
    if (e.key === 'F11') { e.preventDefault(); toggleFullscreen().catch(() => {}); return }
    if (e.key === 'Escape') { fireInterrupt().catch(() => {}); return }

    if (e.ctrlKey || e.altKey || e.metaKey) return
    if (isEditableTarget(e.target)) return
    const panel = panelByKey(e.key)
    if (panel && panel.available) {
      activePanel.set(panel.id)
    }
  }

  onMount(() => window.addEventListener('keydown', onKeydown))
  onDestroy(() => window.removeEventListener('keydown', onKeydown))
</script>

{#if setupChecked && needsSetup}
  <SetupOverlay onDone={() => (needsSetup = false)} />
{:else if !ready}
  <Splash />
{/if}

<div class="shell">
  <Header />
  <div class="body">
    <NavRail />
    <PanelHost />
    <RightPanel />
  </div>
  <PlaybackBar />
  <Footer />
</div>
<Toast />
<WhatsAppToast />

<style>
  .shell {
    display: flex;
    flex-direction: column;
    height: 100%;
    width: 100%;
  }

  .body {
    flex: 1;
    min-height: 0;
    display: flex;
  }
</style>
