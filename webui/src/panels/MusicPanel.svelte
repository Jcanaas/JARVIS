<script>
  import { onMount, onDestroy } from 'svelte'
  import {
    searchMusic, getRecommendations, listPlaylists, listPlaylistTracks, musicAction,
  } from '../api.js'
  import { playback } from '../stores/sse.js'

  // Mirrors ui/panels/music.py's MusicModePanelV2 flow: a persistent search
  // bar, two browse tabs (Recomendaciones/Playlists) while not searching,
  // three filter tabs (Canciones/Playlists/Artistas) once you search, a hero
  // header describing what's on screen, and a list where clicking a song
  // queues the whole list from that point (_play_song, music.py:1977) and
  // clicking a playlist drills into its tracks.

  // Long playlists are rendered in pages, the way the Qt panel streams its
  // first 100 rows and fills in the rest — mounting 1000+ rows (each with a
  // remote cover) at once is what made scrolling stutter.
  const PAGE = 60
  const FIRST_PAGE = 100

  let playlistToken = 0

  let query = $state('')
  let section = $state('playlists')
  let searching = $state(false)
  let loading = $state(false)
  let error = $state('')

  let searchResults = $state({ songs: [], playlists: [], artists: [] })
  let searchFilter = $state('songs')

  let items = $state([])
  let visibleCount = $state(PAGE)
  let kind = $state('playlists')
  let header = $state({
    type: 'Playlists', title: 'Playlists',
    meta: 'Tu biblioteca de YouTube Music', thumbnail: '',
  })
  let currentPlaylist = $state(null)
  let listEl = $state()
  let sentinelEl = $state()
  let loadingMore = $state(false)
  let observer

  let visibleItems = $derived(items.slice(0, visibleCount))

  function setItems(next, { keepScroll = false } = {}) {
    items = next || []
    if (!keepScroll) {
      visibleCount = PAGE
      if (listEl) listEl.scrollTop = 0
    }
  }

  function showMore() {
    if (visibleCount >= items.length) return
    visibleCount = Math.min(items.length, visibleCount + PAGE)
  }

  // A sentinel + IntersectionObserver rather than a scroll handler: it fires
  // regardless of how the scroll was produced (wheel, keyboard, touchpad
  // momentum, scrollbar drag), which a scroll-position threshold does not
  // reliably do inside a contained, nested flex scroller.
  function attachObserver(node) {
    if (observer) observer.disconnect()
    if (!node || !listEl) return
    observer = new IntersectionObserver(
      (entries) => { if (entries.some((e) => e.isIntersecting)) showMore() },
      { root: listEl, rootMargin: '400px 0px' },
    )
    observer.observe(node)
  }

  $effect(() => { attachObserver(sentinelEl) })

  onDestroy(() => {
    if (observer) observer.disconnect()
  })

  async function load(fn, onDone) {
    loading = true
    error = ''
    try {
      onDone(await fn())
    } catch (e) {
      error = e.message
      setItems([])
    } finally {
      loading = false
    }
  }

  async function loadPlaylists() {
    searching = false
    section = 'playlists'
    currentPlaylist = null
    kind = 'playlists'
    header = { type: 'Playlists', title: 'Playlists', meta: 'Tu biblioteca de YouTube Music', thumbnail: '' }
    await load(listPlaylists, setItems)
  }

  async function loadRecommendations() {
    searching = false
    section = 'recommendations'
    currentPlaylist = null
    kind = 'songs'
    header = { type: 'Recomendaciones', title: 'Recomendaciones', meta: 'Hechas para ti según tu actividad', thumbnail: '' }
    await load(getRecommendations, setItems)
  }

  async function doSearch() {
    const q = query.trim()
    if (!q) return loadPlaylists()
    searching = true
    currentPlaylist = null
    header = { type: 'Búsqueda', title: `Buscar: ${q}`, meta: 'Filtra por canciones, playlists o artistas', thumbnail: '' }
    await load(() => searchMusic(q), (res) => {
      searchResults = res
      applySearchFilter('songs')
    })
  }

  function applySearchFilter(filter) {
    searchFilter = filter
    kind = filter
    setItems(searchResults[filter] || [])
  }

  function onSearchKeydown(e) {
    if (e.key === 'Enter') doSearch()
  }

  async function openPlaylist(playlist) {
    const pid = playlist.playlistId || playlist.browseId
    if (!pid) return
    currentPlaylist = playlist
    kind = 'playlist_tracks'
    header = {
      type: 'Lista',
      title: playlist.title || '(sin título)',
      meta: [playlist.author, playlist.itemCount ? `${playlist.itemCount} canciones` : '']
        .filter(Boolean).join(' · '),
      thumbnail: playlist.thumbnail || '',
    }
    // First page lands fast (~1s); the full playlist can take many seconds
    // for a large library, so it is fetched afterwards and swapped in
    // without disturbing what the user is already looking at. Same shape as
    // ui/panels/music.py's _PLAYLIST_FIRST_PAGE streaming.
    const token = ++playlistToken
    await load(() => listPlaylistTracks(pid, FIRST_PAGE), setItems)
    if (token !== playlistToken) return
    loadingMore = true
    try {
      const all = await listPlaylistTracks(pid, 0)
      if (token === playlistToken && all.length > items.length) {
        setItems(all, { keepScroll: true })
      }
    } catch {
      // Keep the first page; it is still usable.
    } finally {
      if (token === playlistToken) loadingMore = false
    }
  }

  function backToList() {
    if (searching) applySearchFilter('playlists')
    else if (section === 'recommendations') loadRecommendations()
    else loadPlaylists()
  }

  // Payload shape follows music.py's _audio_tracks_from_items — only
  // entries that actually have a videoId — plus the cover (see below).
  function audioTracks(list) {
    return list
      .filter((t) => t.videoId)
      .map((t) => ({
        videoId: t.videoId,
        title: t.title || '',
        artists: t.artists || '',
        // _build_playlist_from_tracks keeps this, and current() is what
        // feeds the playback bar's artwork -- omit it and the bar has no
        // cover to show.
        thumbnail: t.thumbnail || '',
      }))
  }

  async function send(action, params) {
    try {
      await musicAction(action, params)
    } catch (e) {
      error = e.message
    }
  }

  // _play_song (music.py:1977): inside a track listing, clicking a row
  // queues the entire list and starts at that track — it does not play the
  // song in isolation.
  function playSongAt(index) {
    const tracks = audioTracks(items)
    if (tracks.length) {
      const startIndex = items.slice(0, index).filter((t) => t.videoId).length
      return send('play_tracks', {
        tracks,
        start_index: Math.min(startIndex, tracks.length - 1),
        shuffle: false,
      })
    }
    const track = items[index] || {}
    if (track.videoId) {
      return send('play_track', {
        videoId: track.videoId, title: track.title || '', artists: track.artists || '',
      })
    }
    const q = `${track.title || ''} ${track.artists || ''}`.trim()
    if (q) return send('play', { query: q, type: 'song' })
  }

  // _play_current_playlist_shuffled (music.py:2013): shuffles client-side
  // and queues the result, rather than asking the backend to shuffle.
  function shufflePlaylist() {
    const tracks = audioTracks(items)
    if (!tracks.length) return
    for (let i = tracks.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1))
      ;[tracks[i], tracks[j]] = [tracks[j], tracks[i]]
    }
    send('play_tracks', { tracks, start_index: 0, shuffle: false })
  }

  onMount(loadPlaylists)
</script>

<div class="panel">
  <div class="search-row">
    <input
      type="text"
      placeholder="Buscar playlists, canciones o artistas"
      bind:value={query}
      onkeydown={onSearchKeydown}
    />
    <button onclick={doSearch}>Buscar</button>
  </div>

  {#if !searching}
    <div class="tabs">
      <button class:active={section === 'recommendations'} onclick={loadRecommendations}>Recomendaciones</button>
      <button class:active={section === 'playlists'} onclick={loadPlaylists}>Playlists</button>
    </div>
  {:else if kind !== 'playlist_tracks'}
    <div class="tabs">
      <button class:active={searchFilter === 'songs'} onclick={() => applySearchFilter('songs')}>Canciones</button>
      <button class:active={searchFilter === 'playlists'} onclick={() => applySearchFilter('playlists')}>Playlists</button>
      <button class:active={searchFilter === 'artists'} onclick={() => applySearchFilter('artists')}>Artistas</button>
    </div>
  {/if}

  <div class="hero">
    {#if kind === 'playlist_tracks'}
      <button class="back" onclick={backToList} title="Volver">←</button>
    {/if}
    {#if header.thumbnail}
      <img src={header.thumbnail} alt="" class="hero-cover" width="64" height="64" decoding="async" />
    {:else}
      <div class="hero-cover thumb-fallback"></div>
    {/if}
    <div class="hero-text">
      <div class="hero-type">{header.type}</div>
      <div class="hero-title">{header.title}</div>
      <div class="hero-meta">{header.meta}</div>
    </div>
    {#if kind === 'playlist_tracks' && items.length}
      <button class="shuffle" onclick={shufflePlaylist}>Aleatorio</button>
    {/if}
  </div>

  {#if error}
    <div class="error">{error}</div>
  {/if}

  <div class="list" bind:this={listEl}>
    {#if loading}
      <div class="empty">Cargando…</div>
    {:else if !items.length}
      <div class="empty">
        {#if kind === 'playlists'}No hay playlists en tu biblioteca.
        {:else if kind === 'artists'}No se encontraron artistas.
        {:else if kind === 'playlist_tracks'}Esta playlist no contiene canciones disponibles.
        {:else}No se encontraron resultados.{/if}
      </div>
    {:else if kind === 'playlists'}
      {#each visibleItems as pl, i (i)}
        <button class="row" onclick={() => openPlaylist(pl)}>
          {#if pl.thumbnail}
            <img src={pl.thumbnail} alt="" class="row-thumb" width="40" height="40" loading="lazy" decoding="async" />
          {:else}<div class="row-thumb thumb-fallback"></div>{/if}
          <div class="row-main">
            <div class="row-title">{pl.title}</div>
            <div class="row-sub">{pl.author || ''}</div>
          </div>
          <div class="row-extra count">{pl.itemCount ?? ''}</div>
        </button>
      {/each}
    {:else if kind === 'artists'}
      {#each visibleItems as a, i (i)}
        <button class="row">
          {#if a.thumbnail}
            <img src={a.thumbnail} alt="" class="row-thumb round" width="40" height="40" loading="lazy" decoding="async" />
          {:else}<div class="row-thumb round thumb-fallback"></div>{/if}
          <div class="row-main">
            <div class="row-title">{a.name || a.title}</div>
            <div class="row-sub">{a.subscribers || a.description || ''}</div>
          </div>
        </button>
      {/each}
    {:else}
      {#each visibleItems as track, i (i)}
        {@const isCurrent = !!track.videoId && track.videoId === $playback.videoId}
        <button class="row" class:current={isCurrent} onclick={() => playSongAt(i)}>
          <div class="row-index" class:current={isCurrent}>
            {isCurrent ? ($playback.playing ? '▶' : 'Ⅱ') : i + 1}
          </div>
          {#if track.thumbnail}
            <img src={track.thumbnail} alt="" class="row-thumb" width="40" height="40" loading="lazy" decoding="async" />
          {:else}<div class="row-thumb thumb-fallback"></div>{/if}
          <div class="row-main">
            <div class="row-title">{track.title}</div>
            <div class="row-sub">{track.artists || ''}</div>
          </div>
          <div class="row-extra">{track.album || ''}</div>
          <div class="row-extra duration">{track.duration || ''}</div>
        </button>
      {/each}
    {/if}

    {#if !loading && visibleCount < items.length}
      <div class="sentinel" bind:this={sentinelEl}></div>
      <button class="more" onclick={showMore}>
        Mostrar más ({items.length - visibleCount} restantes)
      </button>
    {:else if !loading && loadingMore}
      <div class="more-hint">Cargando el resto de la lista…</div>
    {/if}
  </div>
</div>

<style>
  .panel {
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    padding: 16px;
    gap: 10px;
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
    padding: 8px 12px;
    font-size: 14px;
  }

  .search-row input:focus {
    outline: none;
    border-color: var(--pri-dim);
  }

  .search-row button {
    background: var(--pri-gho);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 8px 14px;
    cursor: pointer;
  }

  .tabs {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }

  .tabs button {
    background: transparent;
    border: 1px solid var(--border-a);
    border-radius: 999px;
    color: var(--text-med);
    padding: 5px 14px;
    font-size: 12px;
    cursor: pointer;
  }

  .tabs button.active {
    background: var(--pri-gho);
    border-color: var(--pri-dim);
    color: var(--pri);
  }

  .hero {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-shrink: 0;
    background: var(--panel);
    border: 1px solid var(--border-a);
    border-radius: var(--radius);
    padding: 14px;
  }

  .back {
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text);
    width: 30px;
    height: 30px;
    flex-shrink: 0;
    cursor: pointer;
  }

  .hero-cover {
    width: 64px;
    height: 64px;
    border-radius: 10px;
    object-fit: cover;
    flex-shrink: 0;
    background: var(--panel-2);
  }

  .hero-text {
    flex: 1;
    min-width: 0;
  }

  .hero-type {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-med);
  }

  .hero-title {
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .hero-meta {
    font-size: 12px;
    color: var(--text-med);
  }

  .shuffle {
    background: var(--pri-gho);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--pri);
    padding: 7px 14px;
    font-size: 12px;
    cursor: pointer;
    flex-shrink: 0;
  }

  .error {
    color: var(--red);
    font-size: 13px;
    flex-shrink: 0;
  }

  .list {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    display: flex;
    flex-direction: column;
    gap: 2px;
    /* Keeps row repaints off the rest of the shell while scrolling a long
       playlist. */
    contain: layout paint;
  }

  .empty {
    color: var(--text-med);
    font-size: 13px;
    padding: 24px;
    text-align: center;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
    background: transparent;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    padding: 7px 10px;
    cursor: pointer;
    text-align: left;
    color: var(--text);
    /* Infinite scroll keeps appending rows and never unmounts them (a
       1463-track playlist ends up with ~1500 real DOM rows). Off-screen
       rows skip layout/paint/style entirely instead of costing a full
       render each — contain-intrinsic-size is the row's own height
       (padding 7*2 + ~40px thumb + borders) so scrollbar geometry stays
       correct while a row is skipped. */
    content-visibility: auto;
    contain-intrinsic-size: auto 56px;
  }

  .row:hover {
    background: var(--glass);
    border-color: var(--border-a);
  }

  .row.current {
    background: var(--pri-gho);
    border-color: var(--pri-dim);
  }
  .row.current .row-title { color: var(--pri); }

  .row-index {
    width: 24px;
    flex-shrink: 0;
    text-align: right;
    font-size: 12px;
    color: var(--text-med);
    font-family: var(--font-mono);
  }
  .row-index.current { color: var(--pri); font-size: 13px; }

  .row-thumb {
    width: 40px;
    height: 40px;
    border-radius: 6px;
    object-fit: cover;
    background: var(--panel-2);
    flex-shrink: 0;
  }

  .row-thumb.round {
    border-radius: 50%;
  }

  .row-main {
    flex: 1;
    min-width: 0;
  }

  .row-title {
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .row-sub {
    font-size: 12px;
    color: var(--text-med);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .row-extra {
    flex-shrink: 0;
    width: 150px;
    font-size: 12px;
    color: var(--text-med);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .row-extra.count {
    width: 60px;
    text-align: right;
  }

  .row-extra.duration {
    width: 54px;
    text-align: right;
    font-family: var(--font-mono);
  }

  /* Two 150px fixed columns are unusable once the panel is only a couple
     hundred CSS px wide (HiDPI windows hit this immediately). Drop the
     album column first, then the hero cover, before squeezing the title. */
  @media (max-width: 1000px) {
    .row-extra:not(.duration):not(.count) {
      display: none;
    }
  }

  @media (max-width: 760px) {
    .panel {
      padding: 10px;
    }
    .hero {
      padding: 10px;
      gap: 10px;
    }
    .hero-cover {
      width: 44px;
      height: 44px;
    }
    .hero-title {
      font-size: 14px;
    }
    .row-extra.duration {
      display: none;
    }
  }

  .more {
    flex-shrink: 0;
    margin: 10px auto;
    background: var(--panel-2);
    border: 1px solid var(--border-a);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    padding: 8px 18px;
    font-size: 12px;
    cursor: pointer;
  }

  .more:hover {
    border-color: var(--pri-dim);
    color: var(--text);
  }

  .sentinel {
    flex-shrink: 0;
    height: 1px;
  }

  .more-hint {
    flex-shrink: 0;
    text-align: center;
    padding: 10px;
    font-size: 11px;
    color: var(--text-med);
  }
</style>
