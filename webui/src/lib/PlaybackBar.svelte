<script>
  import { playback } from '../stores/sse.js'
  import { musicAction, getLyrics } from '../api.js'
  import Icon from './Icon.svelte'

  // Same control set and order as ui/__init__.py's _build_playback_bar
  // (:1199): prev / play-pause / next / like / karaoke, then track title +
  // seek, the time readout, a volume icon + slider.
  // Not ported: the "reproductor flotante" (pip) button — it detaches a
  // separate always-on-top Qt window, which has no meaning inside a single
  // web view.

  let error = $state('')
  let seeking = $state(false)
  let seekValue = $state(0)

  let karaokeOpen = $state(false)
  let lyrics = $state([])
  let lyricsKey = $state('')
  let lyricsLoading = $state(false)

  // While dragging, show the handle where the user put it instead of
  // snapping back to the last position the poller reported.
  let position = $derived(seeking ? seekValue : ($playback.position || 0))

  let activeLyric = $derived(
    lyrics.length
      ? lyrics.reduce((best, l) => (l.time <= position ? l : best), lyrics[0])
      : null
  )

  async function send(action, params) {
    try {
      await musicAction(action, params)
    } catch (e) {
      error = e.message
    }
  }

  const togglePlay = () => send('toggle', {})
  const next = () => send('next', {})
  const previous = () => send('previous', {})

  function onVolumeInput(e) {
    send('volume', { level: Number(e.target.value) })
  }

  function onSeekInput(e) {
    seeking = true
    seekValue = Number(e.target.value)
  }

  function onSeekCommit(e) {
    const pos = Number(e.target.value)
    seeking = false
    send('seek', { position: pos })
  }

  function toggleLike() {
    const p = $playback
    if (!p.videoId) return
    send('set_like', { video_id: p.videoId, liked: !p.liked })
  }

  async function toggleKaraoke() {
    karaokeOpen = !karaokeOpen
    if (!karaokeOpen) return
    const p = $playback
    const key = `${p.title}|${p.artists}`
    if (!p.title || key === lyricsKey) return
    lyricsLoading = true
    try {
      lyrics = await getLyrics(p.title, p.artists || '')
      lyricsKey = key
    } catch {
      lyrics = []
    } finally {
      lyricsLoading = false
    }
  }

  function fmtTime(seconds) {
    if (!seconds || Number.isNaN(seconds)) return '0:00'
    const s = Math.floor(seconds)
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
  }
</script>

{#if $playback.title}
  {#if karaokeOpen}
    <div class="karaoke">
      {#if lyricsLoading}
        <span class="karaoke-dim">Buscando letra…</span>
      {:else if !lyrics.length}
        <span class="karaoke-dim">No se encontró letra sincronizada para esta canción.</span>
      {:else}
        <span class="karaoke-line">{activeLyric?.line ?? ''}</span>
      {/if}
    </div>
  {/if}

  <div class="bar">
    {#if $playback.thumbnail}
      <img src={$playback.thumbnail} alt="" class="thumb" width="48" height="48" decoding="async" />
    {:else}
      <div class="thumb thumb-fallback"></div>
    {/if}

    <div class="controls">
      <button class="ctrl" onclick={previous} title="Anterior"><Icon name="previous" size={15} /></button>
      <button class="ctrl play" onclick={togglePlay} title="Reproducir o pausar">
        <Icon name={$playback.playing ? 'pause' : 'play'} size={15} />
      </button>
      <button class="ctrl" onclick={next} title="Siguiente"><Icon name="next" size={15} /></button>
      <button
        class="ctrl like"
        class:on={$playback.liked}
        onclick={toggleLike}
        title="Marcar como Me gusta"
      >
        <Icon name={$playback.liked ? 'heart-filled' : 'heart'} size={15} />
      </button>
      <button
        class="ctrl karaoke-btn"
        class:on={karaokeOpen}
        onclick={toggleKaraoke}
        title="Mostrar letra sincronizada"
      >
        <Icon name="karaoke" size={15} />
      </button>
    </div>

    <div class="meta">
      <div class="title-row">
        <span class="title">{$playback.title}</span>
        <span class="artists">{$playback.artists || ''}</span>
      </div>
      <input
        class="seek"
        type="range"
        min="0"
        max={$playback.duration || 0}
        value={position}
        oninput={onSeekInput}
        onchange={onSeekCommit}
        title="Posición de reproducción"
      />
    </div>

    <div class="time">{fmtTime(position)} / {fmtTime($playback.duration)}</div>

    <div class="vol-icon" title="Volumen"><Icon name="volume" size={16} /></div>
    <input
      class="volume"
      type="range"
      min="0"
      max="100"
      value={$playback.volume ?? 100}
      oninput={onVolumeInput}
      title="Volumen de la música"
    />
  </div>
{/if}

<style>
  .bar {
    height: 72px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 18px;
    background: rgba(7, 13, 23, 0.96);
    border-top: 1px solid rgba(182, 196, 255, 0.12);
  }

  .thumb {
    width: 48px;
    height: 48px;
    border-radius: 8px;
    object-fit: cover;
    flex-shrink: 0;
    background: var(--panel-2);
  }

  .controls {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .ctrl {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    height: 30px;
    flex-shrink: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    cursor: pointer;
  }

  .ctrl:hover {
    border-color: var(--border-a);
    color: var(--text);
  }

  .ctrl.play {
    color: var(--acc);
  }

  .ctrl.like.on {
    color: var(--red);
  }

  .ctrl.karaoke-btn.on {
    color: var(--pri);
    border-color: var(--pri-dim);
  }

  .meta {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .title-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-width: 0;
  }

  .title {
    color: var(--text);
    font-weight: 700;
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .artists {
    color: var(--text-med);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .time {
    font-size: 11px;
    color: var(--text-dim);
    font-family: var(--font-mono);
    flex-shrink: 0;
    white-space: nowrap;
  }

  .vol-icon {
    color: var(--text-dim);
    display: flex;
    flex-shrink: 0;
  }

  .volume {
    width: 90px;
    flex-shrink: 0;
  }

  .seek {
    width: 100%;
  }

  input[type='range'] {
    accent-color: var(--pri);
    height: 14px;
    cursor: pointer;
  }

  .karaoke {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 10px 18px;
    background: rgba(7, 13, 23, 0.96);
    border-top: 1px solid rgba(182, 196, 255, 0.12);
    min-height: 44px;
  }

  .karaoke-line {
    color: var(--pri);
    font-size: 15px;
    font-weight: 600;
    text-align: center;
  }

  .karaoke-dim {
    color: var(--text-med);
    font-size: 12px;
  }

  /* The window is often only ~600 CSS px wide on a HiDPI display; drop the
     least essential pieces before the title starts getting squeezed. */
  @media (max-width: 900px) {
    .bar {
      gap: 8px;
      padding: 8px 12px;
    }
    .vol-icon {
      display: none;
    }
    .volume {
      width: 64px;
    }
    .title-row {
      flex-direction: column;
      gap: 0;
      align-items: flex-start;
    }
  }

  /* The cover is deliberately kept to the end — it is the piece the bar is
     most often asked about — so the volume slider goes first. */
  @media (max-width: 700px) {
    .volume {
      display: none;
    }
  }
</style>
