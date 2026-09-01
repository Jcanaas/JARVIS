<script>
  // Shared implementation for Movies and TV (ui/panels/movies.py's
  // MoviesModePanel handles both the same way — TV just adds season/episode).
  // Flow: browse/search TMDB → pick a title → resolve torrent sources via
  // Torrentio → pick one → vlc_player streams it over HTTP, which a plain
  // <video> tag plays directly once ready (metadata fetch from the swarm
  // takes anywhere from a few seconds to ~65s on slow trackers, hence the
  // live "buscando fuente…" progress instead of a bare spinner).
  import { onDestroy } from 'svelte'
  import * as api from '../api.js'
  import { events } from '../stores/sse.js'
  import Icon from './Icon.svelte'

  let { kind = 'movie', label = 'Películas', active = true } = $props()

  let query = $state('')
  let items = $state([])
  let loading = $state(false)
  let error = $state('')
  let needsApiKey = $state(false)

  let view = $state('browse') // browse | detail | player
  let selected = $state(null)
  let season = $state(1)
  let episode = $state(1)

  let torrents = $state([])
  let torrentsLoading = $state(false)
  let torrentsError = $state('')

  let streamState = $state('idle') // idle | connecting | playing | error
  let streamProgress = $state({ peers: 0, elapsed: 0 })
  let streamUrl = $state('')
  let streamError = $state('')

  function fetchTrending() {
    return kind === 'anime' ? api.getAnimeTrending(false) : api.getMoviesTrending(kind)
  }
  function fetchSearch(q) {
    return kind === 'anime' ? api.searchAnime(q) : api.searchMovies(q, kind)
  }
  function fetchTorrents(tmdbId, s, e) {
    return kind === 'anime' ? api.getAnimeTorrents(tmdbId, s, e) : api.getMovieTorrents(tmdbId, kind, s, e)
  }

  async function loadTrending() {
    loading = true
    error = ''
    needsApiKey = false
    try {
      items = await fetchTrending()
    } catch (e) {
      error = e.message
      needsApiKey = !!e.needsApiKey
      items = []
    } finally {
      loading = false
    }
  }

  async function doSearch() {
    const q = query.trim()
    if (!q) return loadTrending()
    loading = true
    error = ''
    needsApiKey = false
    try {
      items = await fetchSearch(q)
    } catch (e) {
      error = e.message
      needsApiKey = !!e.needsApiKey
      items = []
    } finally {
      loading = false
    }
  }

  function onSearchKeydown(e) {
    if (e.key === 'Enter') doSearch()
  }

  function openDetail(movie) {
    selected = movie
    season = 1
    episode = 1
    view = 'detail'
    torrents = []
    torrentsError = ''
  }

  function backToBrowse() {
    view = 'browse'
    selected = null
  }

  async function findSources() {
    if (!selected) return
    torrentsLoading = true
    torrentsError = ''
    torrents = []
    try {
      torrents = await fetchTorrents(selected.tmdb_id, season, episode)
    } catch (e) {
      torrentsError = e.message
    } finally {
      torrentsLoading = false
    }
  }

  const unsubscribe = events.subscribe(({ type, data }) => {
    if (type === 'stream_progress') {
      streamProgress = data
    } else if (type === 'stream_ready') {
      streamState = 'playing'
      streamUrl = data.url
    } else if (type === 'stream_error') {
      streamState = 'error'
      streamError = data.error || 'No se pudo iniciar la reproducción.'
    }
  })
  onDestroy(unsubscribe)

  async function playTorrent(stream) {
    view = 'player'
    streamState = 'connecting'
    streamProgress = { peers: 0, elapsed: 0 }
    streamUrl = ''
    streamError = ''
    try {
      await api.playMovieTorrent(stream.magnet, stream.file_idx)
    } catch (e) {
      streamState = 'error'
      streamError = e.message
    }
  }

  async function stopPlayback() {
    streamUrl = ''
    streamState = 'idle'
    view = 'detail'
    try { await api.stopMoviePlayback() } catch { /* best-effort */ }
  }

  $effect(() => {
    if (active && view === 'browse' && !items.length && !loading && !error) {
      loadTrending()
    }
  })
</script>

<div class="panel">
  {#if view !== 'player'}
    <div class="search-row">
      <input
        type="text"
        placeholder={`Buscar ${label.toLowerCase()}…`}
        bind:value={query}
        onkeydown={onSearchKeydown}
      />
      <button onclick={doSearch}>Buscar</button>
    </div>
  {/if}

  {#if view === 'browse'}
    {#if loading}
      <div class="empty">Cargando…</div>
    {:else if error}
      <div class="empty error">
        {error}
        {#if needsApiKey}<div class="hint">Configura tmdb_api_key en Ajustes → APIs.</div>{/if}
      </div>
    {:else if !items.length}
      <div class="empty">Sin resultados.</div>
    {:else}
      <div class="grid">
        {#each items as m (m.tmdb_id)}
          <button class="card" onclick={() => openDetail(m)}>
            {#if m.poster_url}
              <img src={m.poster_url} alt="" class="poster" loading="lazy" />
            {:else}
              <div class="poster thumb-fallback"></div>
            {/if}
            <div class="card-title">{m.title}</div>
            <div class="card-meta">{m.release_year || ''}{m.rating ? ` · ★ ${m.rating.toFixed(1)}` : ''}</div>
          </button>
        {/each}
      </div>
    {/if}
  {:else if view === 'detail' && selected}
    <div class="detail">
      <button class="back" onclick={backToBrowse} title="Volver"><Icon name="up" size={16} /></button>
      <div class="detail-hero" style={selected.backdrop_url ? `background-image: url(${selected.backdrop_url})` : ''}>
        <div class="detail-hero-overlay">
          {#if selected.poster_url}<img src={selected.poster_url} alt="" class="detail-poster" />{/if}
          <div class="detail-info">
            <h2>{selected.title}</h2>
            <div class="detail-meta">{selected.release_year || ''}{selected.rating ? ` · ★ ${selected.rating.toFixed(1)}` : ''}</div>
            <p class="detail-overview">{selected.overview}</p>
          </div>
        </div>
      </div>

      {#if kind === 'tv' || kind === 'anime'}
        <div class="episode-row">
          <label>Temporada <input type="number" min="1" bind:value={season} /></label>
          <label>Episodio <input type="number" min="1" bind:value={episode} /></label>
        </div>
      {/if}

      <button class="primary" onclick={findSources} disabled={torrentsLoading}>
        {torrentsLoading ? 'Buscando fuentes…' : 'Buscar fuentes'}
      </button>

      {#if torrentsError}<div class="empty error">{torrentsError}</div>{/if}

      {#if torrents.length}
        <div class="torrent-list">
          {#each torrents as t, i (i)}
            <button class="torrent-row" onclick={() => playTorrent(t)}>
              <div class="torrent-main">
                <div class="torrent-title">{t.title}</div>
                <div class="torrent-meta">
                  {t.provider}
                  {#if t.spanish}<span class="badge">ES</span>{/if}
                </div>
              </div>
              <div class="torrent-extra">{t.size}</div>
              <div class="torrent-extra seeders">👤 {t.seeders}</div>
            </button>
          {/each}
        </div>
      {/if}
    </div>
  {:else if view === 'player'}
    <div class="player">
      {#if streamState === 'connecting'}
        <div class="empty">
          Buscando fuente… {streamProgress.peers} peers, {streamProgress.elapsed}s
        </div>
      {:else if streamState === 'error'}
        <div class="empty error">{streamError}</div>
      {:else if streamState === 'playing' && streamUrl}
        <!-- svelte-ignore a11y_media_has_caption -->
        <video src={streamUrl} controls autoplay class="video"></video>
      {/if}
      <button class="ghost-btn stop-btn" onclick={stopPlayback}>Detener y volver</button>
    </div>
  {/if}
</div>

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 16px; gap: 12px; }

  .search-row { display: flex; gap: 8px; flex-shrink: 0; }
  .search-row input {
    flex: 1; min-width: 0;
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); padding: 8px 12px; font-size: 14px;
  }
  .search-row button {
    background: var(--pri-gho); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--pri); padding: 8px 14px; cursor: pointer;
  }

  .empty { color: var(--text-med); font-size: 13px; padding: 24px; text-align: center; }
  .empty.error { color: var(--red); }
  .hint { margin-top: 6px; font-size: 12px; color: var(--text-med); }

  .grid {
    flex: 1; min-height: 0; overflow-y: auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 12px;
  }
  .card {
    background: transparent; border: none; cursor: pointer; text-align: left; color: var(--text);
    display: flex; flex-direction: column; gap: 4px;
  }
  .poster { width: 100%; aspect-ratio: 2/3; object-fit: cover; border-radius: var(--radius-sm); background: var(--panel-2); }
  .card-title { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .card-meta { font-size: 11px; color: var(--text-med); }

  .detail { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; }
  .back {
    align-self: flex-start;
    background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); width: 30px; height: 30px; cursor: pointer; transform: rotate(-90deg);
    display: flex; align-items: center; justify-content: center;
  }
  .detail-hero {
    border-radius: var(--radius); overflow: hidden; background-size: cover; background-position: center;
    background-color: var(--panel);
  }
  .detail-hero-overlay {
    display: flex; gap: 14px; padding: 16px;
    background: linear-gradient(90deg, rgba(10,12,26,0.92), rgba(10,12,26,0.75));
  }
  .detail-poster { width: 90px; border-radius: var(--radius-sm); flex-shrink: 0; }
  .detail-info h2 { margin: 0 0 4px; font-size: 17px; }
  .detail-meta { font-size: 12px; color: var(--text-med); margin-bottom: 8px; }
  .detail-overview { font-size: 12px; color: var(--text-dim); line-height: 1.5; margin: 0; }

  .episode-row { display: flex; gap: 12px; }
  .episode-row label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-med); }
  .episode-row input {
    width: 70px; background: var(--panel-2); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text); padding: 6px 8px;
  }

  .primary {
    align-self: flex-start;
    background: var(--pri-gho); border: 1px solid var(--pri-dim); border-radius: var(--radius-sm);
    color: var(--pri); padding: 8px 16px; font-size: 13px; cursor: pointer;
  }
  .primary:disabled { opacity: 0.6; cursor: not-allowed; }

  .torrent-list { display: flex; flex-direction: column; gap: 4px; overflow-y: auto; }
  .torrent-row {
    display: flex; align-items: center; gap: 10px;
    background: var(--panel); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    padding: 8px 10px; cursor: pointer; color: var(--text); text-align: left;
  }
  .torrent-row:hover { border-color: var(--pri-dim); }
  .torrent-main { flex: 1; min-width: 0; }
  .torrent-title { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .torrent-meta { font-size: 11px; color: var(--text-med); display: flex; align-items: center; gap: 6px; }
  .badge { background: var(--pri-gho); color: var(--pri); border-radius: 999px; padding: 1px 6px; font-size: 10px; }
  .torrent-extra { flex-shrink: 0; font-size: 11px; color: var(--text-med); width: 60px; text-align: right; }

  .player { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; align-items: center; justify-content: center; }
  .video { width: 100%; max-height: 100%; border-radius: var(--radius); background: #000; }
  .ghost-btn {
    background: transparent; border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text-dim); padding: 7px 14px; font-size: 12px; cursor: pointer;
  }
  .stop-btn { flex-shrink: 0; }

  .thumb-fallback { background: var(--panel-2); }
</style>
