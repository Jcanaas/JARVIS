<script>
  // Shared implementation for Movies, TV and Anime (ui/panels/movies.py's
  // MoviesModePanel handles all three the same way).
  // Flow: browse/search → pick a title → resolve torrent sources via
  // Torrentio → pick one → vlc_player streams it over HTTP, which a plain
  // <video> tag plays directly once ready (metadata fetch from the swarm
  // takes anywhere from a few seconds to ~65s on slow trackers, hence the
  // live "buscando fuente…" progress instead of a bare spinner).
  //
  // Movies/TV are keyed by TMDB id; anime is keyed by Kitsu id with ABSOLUTE
  // episode numbering, because that is how Torrentio indexes it. Sending a
  // season-relative number (as this panel used to, via TMDB→IMDb) silently
  // returns the wrong episode for anything past season 1.
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

  let episodes = $state([])
  let episodesLoading = $state(false)
  let episodeStates = $state({})   // {episodeNumber: 'seen' | 'partial'}
  const EPISODE_PAGE_SIZE = 100
  let visibleEpisodeCount = $state(EPISODE_PAGE_SIZE)

  let animeGenres = $state([])
  let activeGenre = $state('')

  let continueItems = $state([])
  let continueLoading = $state(false)

  let autoplayEnabled = $state(true)
  let autoAdvanceSecs = $state(0)   // >0 while the cancelable countdown runs
  let autoAdvanceTimer = null

  let videoEl = $state(null)
  let progressTimer = null
  const PROGRESS_SAVE_INTERVAL_MS = 10000

  // Custom CSS-based fullscreen for the browser <video> path — NOT the
  // native Fullscreen API. pywebview's embedded webview (WebKitGTK on
  // Linux, WebView2/EdgeChromium on Windows) never wires the page's
  // requestFullscreen() call through to actually expanding within its own
  // window: verified live, video.requestFullscreen() throws
  // "Permissions check failed" even though document.fullscreenEnabled and
  // the Feature-Policy both report it as allowed — the embedding host
  // itself doesn't implement it. So instead of the browser API, a CSS
  // class makes the player container cover the viewport itself. This
  // sidesteps the whole embedded-webview fullscreen problem rather than
  // fighting it. The VLC engine is unaffected: its fullscreen (already
  // wired) runs on libVLC's own top-level window, entirely outside
  // pywebview.
  let pseudoFullscreen = $state(false)

  // Custom overlay control bar for the browser <video> — replaces the
  // native `controls` entirely (previously left both showing at once: the
  // browser's own bar *and* our buttons below it, with two different-
  // looking fullscreen buttons where only ours actually worked). One
  // control surface, positioned over the video like a real player.
  let videoPosition = $state(0)
  let videoDuration = $state(0)
  let videoPaused = $state(true)
  let videoVolume = $state(1)
  let videoMuted = $state(false)

  // Subtitle tracks for the <video>. The custom control bar replaced the
  // browser's native controls, which is where the subtitle menu used to
  // live — so without this there is no way to turn subtitles on at all.
  // Sourced from /api/anime/subtitles (external, converted to WebVTT); the
  // list is per-language rather than per-file because the addon returns one
  // best match per language.
  const SUBTITLE_LANGS = [
    { code: 'es', label: 'Español' },
    { code: 'en', label: 'Inglés' },
  ]
  let subtitleLang = $state('es')     // '' = off
  let subtitleMenuOpen = $state(false)
  let subtitleAvailable = $state({})  // {lang: true|false}, probed lazily

  // Opening/ending/recap skip (AniSkip).
  let skipWindows = $state([])
  let activeSkip = $state(null)
  let skipWindowsRequested = false
  let dismissedSkipKey = null

  let streamState = $state('idle') // idle | connecting | playing | error
  let streamProgress = $state({ peers: 0, elapsed: 0 })
  let streamUrl = $state('')
  let streamError = $state('')
  let streamRequestId = $state('')

  function fetchTrending() {
    return kind === 'anime' ? api.getAnimeTrending(false) : api.getMoviesTrending(kind)
  }
  function fetchSearch(q) {
    return kind === 'anime' ? api.searchAnime(q) : api.searchMovies(q, kind)
  }
  function fetchTorrents() {
    return kind === 'anime'
      ? api.getAnimeTorrents(selected.kitsu_id, episode, selected.total_episodes)
      : api.getMovieTorrents(selected.tmdb_id, kind, season, episode)
  }

  // Anime results all carry tmdb_id 0, so the grid keys on the Kitsu id —
  // but that id can legitimately be empty: AniList surfaces brand-new shows
  // before api.ani.zip has a mapping for them, and two such titles in one
  // page both keyed "kitsu:" crashed the whole grid with Svelte's
  // each_key_duplicate. Falling back to the title keeps every key unique
  // without hiding those titles.
  const itemKey = (m) =>
    kind === 'anime' ? (m.kitsu_id ? `kitsu:${m.kitsu_id}` : `title:${m.title}`) : m.tmdb_id

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

  async function loadAnimeGenres() {
    if (kind !== 'anime' || animeGenres.length) return
    try {
      animeGenres = await api.getAnimeGenres()
    } catch {
      // Genre browsing is additive: the main catalog remains usable if the
      // optional category endpoint is temporarily unavailable.
      animeGenres = []
    }
  }

  async function selectAnimeGenre(genre) {
    if (genre === activeGenre) return
    activeGenre = genre
    query = ''
    if (!genre) return loadTrending()
    loading = true
    error = ''
    needsApiKey = false
    try {
      items = await api.getAnimeByGenre(genre)
    } catch (e) {
      error = e.message
      items = []
    } finally {
      loading = false
    }
  }

  async function doSearch() {
    const q = query.trim()
    if (!q) {
      activeGenre = ''
      return loadTrending()
    }
    activeGenre = ''
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
    episodes = []
    episodeStates = {}
    visibleEpisodeCount = EPISODE_PAGE_SIZE
    if (kind === 'anime' && movie.kitsu_id) {
      loadEpisodes(movie)
      loadProgressFor(movie)
    }
  }

  async function loadEpisodes(movie) {
    episodesLoading = true
    try {
      const eps = await api.getAnimeEpisodes(movie.kitsu_id)
      // Guard against a slow response for a title the user already left.
      if (selected === movie) {
        episodes = eps
        visibleEpisodeCount = Math.max(EPISODE_PAGE_SIZE, episode)
      }
    } catch {
      if (selected === movie) episodes = []
    } finally {
      episodesLoading = false
    }
  }

  // Pre-selects the last-watched episode and paints the seen/partial dots in
  // the episode list, so reopening a series you're mid-way through doesn't
  // default back to episode 1.
  async function loadProgressFor(movie) {
    try {
      const res = await api.getAnimeProgress(movie.kitsu_id)
      if (selected !== movie) return
      episodeStates = Object.fromEntries(
        Object.entries(res.episodes || {}).map(([k, v]) => [Number(k), v])
      )
      if (res.latest) {
        episode = res.latest.episode
        visibleEpisodeCount = Math.max(visibleEpisodeCount, episode)
      }
    } catch { /* progress is an enhancement, not required to browse */ }
  }

  function pickEpisode(n) {
    episode = n
    visibleEpisodeCount = Math.max(visibleEpisodeCount, n)
    pendingResumeSeconds = 0   // manual pick is never a resume
    findSources()
  }

  function backToBrowse() {
    view = 'browse'
    selected = null
    cancelAutoAdvance()
  }

  async function findSources() {
    if (!selected) return
    torrentsLoading = true
    torrentsError = ''
    torrents = []
    try {
      torrents = await fetchTorrents()
    } catch (e) {
      torrentsError = e.message
    } finally {
      torrentsLoading = false
    }
  }

  const unsubscribe = events.subscribe(({ type, data }) => {
    if (!data || data.request_id !== streamRequestId) return
    if (type === 'stream_progress') {
      streamProgress = data
    } else if (type === 'stream_ready') {
      streamState = 'playing'
      streamUrl = data.url
      startProgressTracking()
    } else if (type === 'stream_error') {
      streamState = 'error'
      streamError = data.error || 'No se pudo iniciar la reproducción.'
    }
  })
  onDestroy(() => {
    unsubscribe()
    stopProgressTracking()
    cancelAutoAdvance()
  })

  async function playTorrent(stream) {
    view = 'player'
    streamState = 'connecting'
    streamProgress = { peers: 0, elapsed: 0 }
    streamUrl = ''
    streamError = ''
    // A fresh episode is starting — any prefetch belonged to whatever was
    // playing before, so it can't be reused as "the next episode" now.
    prefetchedNextEp = null
    prefetchedTorrents = null
    resetSkipState()
    videoPosition = 0
    videoDuration = 0
    videoPaused = true
    streamRequestId = typeof crypto?.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    try {
      await api.playMovieTorrent(stream.magnet, stream.file_idx,
                                 kind === 'anime' ? episode : 0, streamRequestId)
    } catch (e) {
      streamState = 'error'
      streamError = e.message
    }
  }


  // One-shot resume/auto-next: picks the best (highest-seeders — the list is
  // already sorted that way by the backend) torrent itself instead of
  // stopping at the torrent list, so "Continuar viendo" and auto-next are
  // actually one click / zero clicks. A real health-aware picker is phase 4;
  // this is the minimal version that makes phase 1's "encadena solo" true.
  async function playEpisodeAuto(movie, epNumber, resumeSeconds = 0, precomputedTorrents = null) {
    selected = movie
    episode = epNumber
    visibleEpisodeCount = Math.max(visibleEpisodeCount, epNumber)
    pendingResumeSeconds = resumeSeconds
    view = 'detail'
    torrentsError = ''
    torrents = []
    try {
      // An empty prefetch (the swarm genuinely had nothing) shouldn't be
      // treated as "already tried" — retry with a normal fetch instead of
      // surfacing a stale failure.
      let found = precomputedTorrents && precomputedTorrents.length ? precomputedTorrents : null
      if (!found) {
        torrentsLoading = true
        found = await fetchTorrents()
      }
      torrents = found
      if (found.length) {
        await playTorrent(found[0])
      } else {
        torrentsError = 'No se encontraron fuentes para este episodio.'
      }
    } catch (e) {
      torrentsError = e.message
    } finally {
      torrentsLoading = false
    }
  }

  async function resumeContinueWatching(item) {
    const kitsuId = item.media_key.replace('kitsu:', '')
    try {
      const meta = await api.getAnimeMeta(kitsuId)
      // Set `selected` before kicking off loadEpisodes so its "did the user
      // navigate away" guard (selected === movie) is already correct instead
      // of relying on it becoming true a moment later inside playEpisodeAuto.
      selected = meta
      episodes = []
      episodeStates = {}
      visibleEpisodeCount = Math.max(EPISODE_PAGE_SIZE, item.episode)
      loadEpisodes(meta)
      await playEpisodeAuto(meta, item.episode, item.position_s)
    } catch (e) {
      error = e.message
    }
  }

  async function removeFromContinueWatching(item, evt) {
    evt.stopPropagation()
    continueItems = continueItems.filter((x) => x !== item)
    try { await api.removeAnimeProgress(item.media_key.replace('kitsu:', '')) } catch { /* best-effort */ }
  }

  async function loadContinueWatching() {
    if (kind !== 'anime') return
    continueLoading = true
    try {
      continueItems = await api.getAnimeContinueWatching()
    } catch {
      continueItems = []
    } finally {
      continueLoading = false
    }
  }

  // -- Playback progress: position tracking, auto-complete, auto-next -----

  let pendingResumeSeconds = 0
  let prefetchedNextEp = null
  let prefetchedTorrents = null

  function onVideoLoadedMetadata() {
    if (!videoEl) return
    if (pendingResumeSeconds > 0) {
      videoEl.currentTime = pendingResumeSeconds
    }
    pendingResumeSeconds = 0
    videoDuration = videoEl.duration
    videoVolume = videoEl.volume
    videoMuted = videoEl.muted
    maybeLoadSkipWindows(videoEl.duration)
  }

  function nextEpisodeAfter(n) {
    const nums = episodes.map((e) => e.number).filter((x) => x > n).sort((a, b) => a - b)
    return nums.length ? nums[0] : null
  }

  // Shared by both engines: the <video> path passes its own element's
  // currentTime/duration; the VLC path passes the last polled status.
  function saveProgressTick(positionS, durationS) {
    if (kind !== 'anime' || !selected) return
    if (positionS === undefined) {
      if (!videoEl || !videoEl.duration) return
      positionS = videoEl.currentTime
      durationS = videoEl.duration
    }
    api.saveAnimeProgress(
      selected.kitsu_id, episode, positionS, durationS,
      selected.title, selected.poster_url
    ).then((entry) => {
      episodeStates = { ...episodeStates, [episode]: entry.completed ? 'seen' : 'partial' }
    }).catch(() => { /* best-effort — local playback keeps going regardless */ })
  }

  function startProgressTracking() {
    stopProgressTracking()
    if (kind !== 'anime') return
    progressTimer = setInterval(() => saveProgressTick(), PROGRESS_SAVE_INTERVAL_MS)
  }

  function stopProgressTracking() {
    if (progressTimer) { clearInterval(progressTimer); progressTimer = null }
  }

  // Starts resolving the next episode's sources once the current one is 80%
  // through, so auto-next doesn't sit through another 45-65s swarm lookup
  // right when the current episode ends. Shared by both engines.
  function checkPrefetch(positionS, durationS) {
    if (kind !== 'anime' || !autoplayEnabled || !durationS) return
    const frac = positionS / durationS
    if (frac < 0.8 || prefetchedNextEp !== null) return
    const next = nextEpisodeAfter(episode)
    if (!next) return
    prefetchedNextEp = next
    api.getAnimeTorrents(selected.kitsu_id, next, selected.total_episodes)
      .then((found) => { prefetchedTorrents = found })
      .catch(() => { prefetchedTorrents = [] })
  }

  function onVideoTimeUpdate() {
    if (!videoEl) return
    videoPosition = videoEl.currentTime
    if (!videoEl.duration) return
    checkSkipWindow(videoEl.currentTime)
    checkPrefetch(videoEl.currentTime, videoEl.duration)
  }

  function onVideoEnded() {
    handleEpisodeEnded(videoEl?.currentTime, videoEl?.duration)
  }

  // Shared episode-end handling: save the final position, mark it seen
  // (redundant with saveProgressTick's own >=85% detection, but a rounding
  // gap between "ended" and an exact 85% tick shouldn't cost the badge), and
  // kick off auto-next. Called from the <video> "ended" event or from the
  // VLC status poller noticing playback stopped at the end.
  function handleEpisodeEnded(positionS, durationS) {
    saveProgressTick(positionS, durationS)
    api.completeAnimeEpisode(selected.kitsu_id, episode, selected.title, selected.poster_url)
      .then(() => { episodeStates = { ...episodeStates, [episode]: 'seen' } })
      .catch(() => {})
    if (kind === 'anime' && autoplayEnabled) {
      const next = nextEpisodeAfter(episode)
      if (next) startAutoAdvance(next)
    }
  }

  function startAutoAdvance(nextEp) {
    autoAdvanceSecs = 8
    autoAdvanceTimer = setInterval(() => {
      autoAdvanceSecs -= 1
      if (autoAdvanceSecs <= 0) {
        cancelAutoAdvance()
        const torrentsReady = prefetchedNextEp === nextEp ? prefetchedTorrents : null
        prefetchedNextEp = null
        prefetchedTorrents = null
        playEpisodeAuto(selected, nextEp, 0, torrentsReady)
      }
    }, 1000)
  }

  function cancelAutoAdvance() {
    if (autoAdvanceTimer) { clearInterval(autoAdvanceTimer); autoAdvanceTimer = null }
    autoAdvanceSecs = 0
  }

  // -- Opening/ending/recap skip (AniSkip) ---------------------------------

  function resetSkipState() {
    skipWindows = []
    activeSkip = null
    skipWindowsRequested = false
    dismissedSkipKey = null
  }

  async function maybeLoadSkipWindows(durationS) {
    if (skipWindowsRequested || kind !== 'anime' || !selected?.kitsu_id || !durationS) return
    skipWindowsRequested = true
    try {
      skipWindows = await api.getAnimeSkipWindows(selected.kitsu_id, episode, durationS)
    } catch {
      skipWindows = []
    }
  }

  function checkSkipWindow(positionS) {
    if (!skipWindows.length) { activeSkip = null; return }
    const hit = skipWindows.find((w) => positionS >= w.start_s && positionS < w.end_s)
    if (!hit) { activeSkip = null; return }
    const key = `${hit.kind}-${hit.start_s}`
    activeSkip = key === dismissedSkipKey ? null : hit
  }

  const SKIP_LABELS = { op: 'Saltar opening', ed: 'Saltar final', recap: 'Saltar resumen' }

  function doSkip() {
    if (!activeSkip) return
    const target = activeSkip.end_s
    if (videoEl) videoEl.currentTime = target
    dismissedSkipKey = `${activeSkip.kind}-${activeSkip.start_s}`
    activeSkip = null
  }

  function dismissSkip() {
    if (activeSkip) dismissedSkipKey = `${activeSkip.kind}-${activeSkip.start_s}`
    activeSkip = null
  }

  // -- Browser <video> custom control bar ----------------------------------

  function togglePlayPauseVideo() {
    if (!videoEl) return
    if (videoEl.paused) videoEl.play(); else videoEl.pause()
  }

  function onSeekBarInput(e) {
    if (!videoEl) return
    const seconds = Number(e.target.value)
    videoEl.currentTime = seconds
    videoPosition = seconds
  }

  function onVolumeBarInput(e) {
    if (!videoEl) return
    videoEl.volume = Number(e.target.value)
    videoEl.muted = false
  }

  function toggleMuteVideo() {
    if (videoEl) videoEl.muted = !videoEl.muted
  }

  function fmtClock(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0))
    const s = seconds % 60
    const m = Math.floor(seconds / 60) % 60
    const h = Math.floor(seconds / 3600)
    const pad = (n) => String(n).padStart(2, '0')
    return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
  }

  // -- Subtitles -----------------------------------------------------------

  function applySubtitleSelection() {
    if (!videoEl) return
    // <track> elements are declarative, but their *mode* is not: setting
    // `default` only picks the initial one, so switching languages has to
    // walk video.textTracks directly.
    for (const t of videoEl.textTracks) {
      t.mode = (subtitleLang && t.language === subtitleLang) ? 'showing' : 'disabled'
    }
  }

  function chooseSubtitle(lang) {
    subtitleLang = lang
    subtitleMenuOpen = false
    // The <track> for a newly chosen language may not have been added yet;
    // let Svelte render it, then set the modes.
    setTimeout(applySubtitleSelection, 0)
  }

  /** A <track> whose src 404s fires `error`; that is our availability signal.
   *  Using the element's own load result avoids a second round trip per
   *  language — a HEAD probe would make the server fetch and convert the
   *  whole subtitle just to be discarded. */
  function onTrackError(lang) {
    subtitleAvailable = { ...subtitleAvailable, [lang]: false }
    if (subtitleLang !== lang) return
    // Fall through to the next language that hasn't already failed, so
    // subtitles aren't silently off just because Spanish is missing here.
    const next = SUBTITLE_LANGS.find(
      (l) => l.code !== lang && subtitleAvailable[l.code] !== false)
    subtitleLang = next ? next.code : ''
    setTimeout(applySubtitleSelection, 0)
  }

  function onTrackLoad(lang) {
    subtitleAvailable = { ...subtitleAvailable, [lang]: true }
    applySubtitleSelection()
  }

  function togglePseudoFullscreen() {
    pseudoFullscreen = !pseudoFullscreen
  }

  function onPlayerKeydown(e) {
    if (e.key === 'Escape' && pseudoFullscreen) pseudoFullscreen = false
  }

  async function stopPlayback() {
    saveProgressTick()
    stopProgressTracking()
    cancelAutoAdvance()
    resetSkipState()
    pseudoFullscreen = false
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

  $effect(() => {
    if (active && kind === 'anime') loadAnimeGenres()
  })

  $effect(() => {
    // Re-reads `view` so this refires every time the user comes back to the
    // browse screen (e.g. after watching an episode) rather than once on
    // mount — otherwise "Continuar viendo" stays stuck on whatever it was
    // (usually empty) the first time this panel opened. Verified live: the
    // rail didn't update after a real watch session until this tracked view.
    if (active && kind === 'anime' && view === 'browse') loadContinueWatching()
  })

  $effect(() => {
    if (kind !== 'anime') return
    api.getAnimeAutoplay().then((res) => { autoplayEnabled = res.autoplay }).catch(() => {})
  })
</script>

<svelte:window onkeydown={onPlayerKeydown} />

<div class="panel">
  {#if view !== 'player'}
    {#if kind === 'anime' && view === 'browse'}
      <section class="anime-intro" aria-label="Descubrir anime">
        <div class="anime-intro-copy">
          <span class="eyebrow">CATÁLOGO ANIME</span>
          <h1>Encuentra tu próxima obsesión.</h1>
          <p>Series en emisión, clásicos y novedades organizados para explorar sin perderte.</p>
        </div>
        <div class="search-row anime-search">
          <input
            type="text"
            placeholder="Buscar por título…"
            bind:value={query}
            onkeydown={onSearchKeydown}
          />
          <button onclick={doSearch}>Buscar</button>
        </div>
      </section>
      <div class="genre-bar" aria-label="Categorías de anime">
        <button class:active={!activeGenre} onclick={() => selectAnimeGenre('')}>Todo</button>
        {#each animeGenres as genre (genre)}
          <button class:active={activeGenre === genre} onclick={() => selectAnimeGenre(genre)}>{genre}</button>
        {/each}
      </div>
    {:else}
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
  {/if}

  {#if view === 'browse'}
    {#if kind === 'anime' && continueItems.length}
      <div class="continue-rail">
        <h3 class="section-title">Continuar viendo</h3>
        <div class="continue-scroll">
          {#each continueItems as item (item.media_key)}
            <div
              class="continue-card"
              role="button"
              tabindex="0"
              onclick={() => resumeContinueWatching(item)}
              onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && resumeContinueWatching(item)}
            >
              <button class="continue-remove" title="Quitar" onclick={(e) => removeFromContinueWatching(item, e)}>
                <Icon name="close" size={12} />
              </button>
              {#if item.poster_url}
                <img src={item.poster_url} alt="" class="poster" loading="lazy" />
              {:else}
                <div class="poster thumb-fallback"></div>
              {/if}
              {#if item.fraction > 0}
                <div class="continue-progress"><div style={`width:${Math.min(100, item.fraction * 100)}%`}></div></div>
              {/if}
              <div class="card-title">{item.title}</div>
              <div class="card-meta">Episodio {item.episode}</div>
            </div>
          {/each}
        </div>
      </div>
    {/if}
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
      {#if kind === 'anime'}
        <div class="catalog-heading">
          <div>
            <span class="eyebrow">{activeGenre ? 'CATEGORÍA' : 'SELECCIÓN DE HOY'}</span>
            <h2>{activeGenre || 'Tendencias ahora'}</h2>
          </div>
          <span class="result-count">{items.length} títulos</span>
        </div>
      {/if}
      <div class="grid">
        {#each items as m (itemKey(m))}
          <button class="card" onclick={() => openDetail(m)}>
            {#if m.poster_url}
              <img src={m.poster_url} alt="" class="poster" loading="lazy" />
            {:else}
              <div class="poster thumb-fallback"></div>
            {/if}
            <div class="card-title">{m.title}</div>
            <div class="card-meta">
              {#if m.release_year}<span>{m.release_year}</span>{/if}
              {#if m.rating}<span class="rating">★ {m.rating.toFixed(1)}</span>{/if}
            </div>
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

      {#if kind === 'tv'}
        <div class="episode-row">
          <label>Temporada <input type="number" min="1" bind:value={season} /></label>
          <label>Episodio <input type="number" min="1" bind:value={episode} /></label>
        </div>
      {/if}

      <!-- Search-result status for the *selected* episode/title. Deliberately
           placed above the episode list: for anime that list is often 100s-
           1000s of rows (One Piece: 1180) with no virtualization, so a
           result block appended after it would sit tens of thousands of
           pixels below the fold — verified live, that's exactly what
           happened before this was moved here. -->
      {#if kind === 'anime' && torrentsLoading}
        <div class="empty">Buscando fuentes…</div>
      {:else if torrentsError}
        <div class="empty error">{torrentsError}</div>
      {:else if torrents.length}
        <h3 class="section-title">Fuentes encontradas</h3>
        <div class="torrent-list">
          {#each torrents as t, i (i)}
            <button class="torrent-row" class:dead={t.seeders_verified && t.seeders === 0}
                    onclick={() => playTorrent(t)}>
              <div class="torrent-main">
                <div class="torrent-title">{t.title}</div>
                <div class="torrent-meta">
                  {t.provider}
                  {#if t.spanish}<span class="badge">ES</span>{/if}
                  {#if !t.browser_playable}<span class="badge warn">códec no compatible</span>{/if}
                  {#if t.seeders_verified && t.seeders === 0}
                    <span class="badge warn">sin seeds</span>
                  {/if}
                </div>
              </div>
              <div class="torrent-extra">{t.size}</div>
              <!-- Counts are a live tracker scrape, not what the source
                   claimed: sources overstate ~10x and sometimes invent them
                   outright (541 advertised vs 0 real, measured). -->
              <div class="torrent-extra seeders"
                   title={t.seeders_verified ? 'Seeders reales (comprobados)' : 'Según la fuente, sin comprobar'}>
                👤 {t.seeders}{t.seeders_verified ? '' : '?'}
              </div>
            </button>
          {/each}
        </div>
      {/if}

      {#if kind === 'anime'}
        <h3 class="section-title">Episodios</h3>
        {#if episodesLoading}
          <div class="empty">Cargando episodios…</div>
        {:else if !episodes.length}
          <div class="empty">
            No se encontraron episodios.
            <button class="primary" onclick={findSources} disabled={torrentsLoading}>
              {torrentsLoading ? 'Buscando fuentes…' : 'Buscar fuentes igualmente'}
            </button>
          </div>
        {:else}
          <div class="episode-list">
            {#each episodes.slice(0, visibleEpisodeCount) as ep (ep.number)}
              <button
                class="episode-item"
                class:active={ep.number === episode}
                onclick={() => pickEpisode(ep.number)}
              >
                <span class="episode-dot" class:seen={episodeStates[ep.number] === 'seen'}
                      class:partial={episodeStates[ep.number] === 'partial'}></span>
                {#if ep.thumbnail}
                  <img src={ep.thumbnail} alt="" class="episode-thumb" loading="lazy" />
                {:else}
                  <div class="episode-thumb thumb-fallback">{ep.number}</div>
                {/if}
                <div class="episode-text">
                  <div class="episode-title">{ep.number}. {ep.title}</div>
                  <div class="episode-meta">
                    {ep.aired || ''}{ep.runtime ? ` · ${ep.runtime} min` : ''}
                  </div>
                  {#if ep.overview}<div class="episode-overview">{ep.overview}</div>{/if}
                </div>
              </button>
            {/each}
          </div>
          {#if visibleEpisodeCount < episodes.length}
            <button class="primary" onclick={() => (visibleEpisodeCount += EPISODE_PAGE_SIZE)}>
              Mostrar {Math.min(EPISODE_PAGE_SIZE, episodes.length - visibleEpisodeCount)} episodios más
            </button>
          {/if}
        {/if}
      {/if}
    </div>
  {:else if view === 'player'}
    <div class="player" class:pseudo-fullscreen={pseudoFullscreen}>
      {#if streamState === 'connecting'}
        <div class="empty">
          Buscando fuente… {streamProgress.peers} peers, {streamProgress.elapsed}s
        </div>
      {:else if streamState === 'error'}
        <div class="empty error">{streamError}</div>
      {:else if streamState === 'playing' && streamUrl}
        <div class="video-shell">
          <!-- svelte-ignore a11y_media_has_caption -->
          <video
            bind:this={videoEl}
            src={streamUrl}
            autoplay
            class="video"
            onloadedmetadata={onVideoLoadedMetadata}
            ontimeupdate={onVideoTimeUpdate}
            onended={onVideoEnded}
            onplay={() => (videoPaused = false)}
            onpause={() => (videoPaused = true)}
            onvolumechange={() => { if (videoEl) { videoVolume = videoEl.volume; videoMuted = videoEl.muted } }}
          >
            {#if kind === 'anime' && selected?.kitsu_id}
              {#each SUBTITLE_LANGS as lang (lang.code)}
                <track
                  kind="subtitles"
                  srclang={lang.code}
                  label={lang.label}
                  default={lang.code === subtitleLang}
                  src={api.animeSubtitlesUrl(selected.kitsu_id, episode, lang.code)}
                  onload={() => onTrackLoad(lang.code)}
                  onerror={() => onTrackError(lang.code)}
                />
              {/each}
            {/if}
          </video>

          {#if activeSkip}
            <div class="skip-banner video-banner-top">
              <span>{SKIP_LABELS[activeSkip.kind] || 'Saltar'}</span>
              <button class="primary-btn" onclick={doSkip}>Saltar</button>
              <button class="ghost-btn" onclick={dismissSkip}>Ignorar</button>
            </div>
          {:else if autoAdvanceSecs > 0}
            <div class="autoadvance-banner video-banner-top">
              Siguiente episodio en {autoAdvanceSecs}s…
              <button class="ghost-btn" onclick={cancelAutoAdvance}>Cancelar</button>
            </div>
          {/if}

          <!-- One control surface for the whole player, overlaid on the video
               itself — previously this coexisted with the browser's own
               native controls (its own, non-functional, fullscreen button
               included; see the pseudoFullscreen comment above), which read
               as two separate, half-working toolbars. -->
          <div class="video-controls-overlay">
            <div class="video-seek-row">
              <span class="video-time">{fmtClock(videoPosition)}</span>
              <input
                type="range" min="0" max={videoDuration || 0}
                value={videoPosition} oninput={onSeekBarInput}
              />
              <span class="video-time">{fmtClock(videoDuration)}</span>
            </div>
            <div class="video-controls-row">
              <button class="icon-btn" onclick={togglePlayPauseVideo}
                      title={videoPaused ? 'Reproducir' : 'Pausar'}
                      aria-label={videoPaused ? 'Reproducir' : 'Pausar'}>
                <Icon name={videoPaused ? 'play' : 'pause'} size={18} />
              </button>
              <button class="icon-btn" onclick={toggleMuteVideo}
                      title={videoMuted ? 'Activar sonido' : 'Silenciar'}
                      aria-label={videoMuted ? 'Activar sonido' : 'Silenciar'}>
                <Icon name={videoMuted ? 'volume-muted' : 'volume'} size={18} />
              </button>
              <input
                type="range" class="video-volume-slider" min="0" max="1" step="0.05"
                value={videoVolume} oninput={onVolumeBarInput}
                title="Volumen" aria-label="Volumen"
              />
              <div class="video-controls-spacer"></div>

              {#if kind === 'anime' && selected?.kitsu_id}
                <div class="subtitle-menu-wrap">
                  <button class="icon-btn" class:active={!!subtitleLang}
                          onclick={() => (subtitleMenuOpen = !subtitleMenuOpen)}
                          title="Subtítulos" aria-label="Subtítulos">
                    <Icon name="subtitles" size={18} />
                  </button>
                  {#if subtitleMenuOpen}
                    <div class="subtitle-menu">
                      <button class:selected={subtitleLang === ''}
                              onclick={() => chooseSubtitle('')}>Desactivados</button>
                      {#each SUBTITLE_LANGS as lang (lang.code)}
                        <button
                          class:selected={subtitleLang === lang.code}
                          disabled={subtitleAvailable[lang.code] === false}
                          onclick={() => chooseSubtitle(lang.code)}>
                          {lang.label}{subtitleAvailable[lang.code] === false ? ' — no disponible' : ''}
                        </button>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/if}

              <button class="icon-btn" onclick={togglePseudoFullscreen}
                      title={pseudoFullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'}
                      aria-label={pseudoFullscreen ? 'Salir de pantalla completa' : 'Pantalla completa'}>
                <Icon name={pseudoFullscreen ? 'fullscreen-exit' : 'fullscreen'} size={18} />
              </button>
              <button class="icon-btn" onclick={stopPlayback}
                      title="Detener y volver" aria-label="Detener y volver">
                <Icon name="close" size={18} />
              </button>
            </div>
          </div>
        </div>
      {/if}
      {#if !(streamState === 'playing' && streamUrl)}
        <!-- While playing, the overlay control bar owns "stop"; the
             connecting and error states still need a way out. -->
        <button class="ghost-btn stop-btn" onclick={stopPlayback}>Detener y volver</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .panel { height: 100%; min-height: 0; display: flex; flex-direction: column; padding: 18px 20px; gap: 14px; }

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

  .anime-intro {
    display: flex; align-items: end; justify-content: space-between; gap: 24px;
    padding: 22px 24px; border: 1px solid var(--border-a); border-radius: 16px;
    background:
      radial-gradient(circle at 82% 0%, rgba(123, 103, 255, 0.22), transparent 38%),
      linear-gradient(120deg, rgba(27, 30, 62, 0.95), var(--panel));
  }
  .anime-intro-copy { max-width: 520px; }
  .eyebrow { display: block; color: var(--pri); font-size: 10px; font-weight: 800; letter-spacing: .13em; }
  .anime-intro h1 { margin: 6px 0 7px; font-size: clamp(22px, 2.3vw, 30px); line-height: 1.08; letter-spacing: -.035em; }
  .anime-intro p { margin: 0; color: var(--text-med); font-size: 13px; line-height: 1.5; }
  .anime-search { width: min(360px, 100%); }
  .genre-bar { display: flex; gap: 8px; overflow-x: auto; padding: 0 1px 4px; scrollbar-width: thin; }
  .genre-bar button {
    flex: none; border: 1px solid var(--border-a); border-radius: 999px; background: var(--panel);
    color: var(--text-med); padding: 7px 12px; font-size: 12px; cursor: pointer; transition: .18s ease;
  }
  .genre-bar button:hover { color: var(--text); border-color: var(--pri-dim); }
  .genre-bar button.active { color: var(--pri); border-color: var(--pri-dim); background: var(--pri-gho); }
  .catalog-heading { display: flex; align-items: end; justify-content: space-between; margin-top: 2px; }
  .catalog-heading h2 { margin: 3px 0 0; font-size: 18px; letter-spacing: -.02em; }
  .result-count { color: var(--text-dim); font-size: 11px; }

  .empty { color: var(--text-med); font-size: 13px; padding: 24px; text-align: center; }
  .empty.error { color: var(--red); }
  .hint { margin-top: 6px; font-size: 12px; color: var(--text-med); }

  .grid {
    flex: 1; min-height: 0; overflow-y: auto;
    display: grid; grid-template-columns: repeat(auto-fill, minmax(148px, 1fr)); gap: 18px 14px;
  }
  .card {
    background: transparent; border: none; cursor: pointer; text-align: left; color: var(--text);
    display: flex; flex-direction: column; gap: 6px; min-width: 0;
  }
  .card .poster { box-shadow: 0 8px 18px rgba(0,0,0,.2); transition: transform .18s ease, box-shadow .18s ease; }
  .card:hover .poster { transform: translateY(-4px); box-shadow: 0 16px 28px rgba(0,0,0,.38); }
  .poster { width: 100%; aspect-ratio: 2/3; object-fit: cover; border-radius: 10px; background: var(--panel-2); }
  .card-title { font-size: 13px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .card-meta { display: flex; align-items: center; gap: 7px; font-size: 11px; color: var(--text-med); }
  .rating { color: #f4c860; font-weight: 700; }

  .continue-rail { flex-shrink: 0; }
  .continue-scroll {
    display: flex; gap: 12px; overflow-x: auto; padding-bottom: 4px;
  }
  .continue-card {
    position: relative; flex: none; width: 130px;
    background: transparent; cursor: pointer; text-align: left; color: var(--text);
    display: flex; flex-direction: column; gap: 4px;
  }
  .continue-remove {
    position: absolute; top: 4px; right: 4px; z-index: 1;
    width: 20px; height: 20px; border-radius: 50%; border: none;
    background: rgba(0, 0, 0, 0.6); color: var(--text);
    display: flex; align-items: center; justify-content: center; cursor: pointer;
  }
  .continue-remove:hover { background: rgba(0, 0, 0, 0.85); }
  .continue-progress {
    height: 3px; margin-top: -8px; background: rgba(255, 255, 255, 0.15); border-radius: 2px;
  }
  .continue-progress > div { height: 100%; background: var(--pri); border-radius: 2px; }

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
    /* Flexbox gotcha: overflow:hidden makes a flex item's *automatic* min
       size 0 (spec default), so inside .detail's flex column this collapsed
       to 0 height the instant its siblings' total content (an episode list —
       always long for anime, often 1000+ rows) exceeded the viewport. The
       other siblings resist shrinking (content-based min), so all the
       squeeze landed here. flex-shrink:0 opts it out of the squeeze
       entirely — verified live against a real 1180-episode series (One
       Piece) before landing this. */
    flex-shrink: 0;
  }
  .detail-hero-overlay {
    display: flex; gap: 14px; padding: 16px;
    background: linear-gradient(90deg, rgba(10,12,26,0.92), rgba(10,12,26,0.75));
  }
  .detail-poster { width: 90px; border-radius: var(--radius-sm); flex-shrink: 0; }
  .detail-info h2 { margin: 0 0 4px; font-size: 17px; }
  .detail-meta { font-size: 12px; color: var(--text-med); margin-bottom: 8px; }
  .detail-overview { font-size: 12px; color: var(--text-dim); line-height: 1.5; margin: 0; }

  .section-title { margin: 14px 0 6px; font-size: 13px; color: var(--text); }
  .episode-list { display: flex; flex-direction: column; gap: 6px; }
  .episode-item {
    display: flex; gap: 12px; align-items: flex-start; text-align: left;
    padding: 6px; border: none; background: transparent; cursor: pointer;
    border-radius: var(--radius); color: inherit; font: inherit;
  }
  .episode-item:hover { background: rgba(255, 255, 255, 0.07); }
  .episode-item.active { background: rgba(255, 255, 255, 0.12); }
  .episode-dot {
    flex: none; width: 8px; height: 8px; border-radius: 50%; margin-top: 6px;
    background: transparent; border: 1px solid var(--border-a);
  }
  .episode-dot.partial { background: var(--pri-dim); border-color: var(--pri-dim); }
  .episode-dot.seen { background: var(--pri); border-color: var(--pri); }
  .episode-thumb {
    width: 140px; height: 79px; flex: none; object-fit: cover;
    border-radius: 8px; background: var(--panel-2);
  }
  .episode-thumb.thumb-fallback {
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; font-weight: 700; color: var(--text-med);
  }
  .episode-text { min-width: 0; }
  .episode-title { font-size: 13px; font-weight: 600; }
  .episode-meta { font-size: 11px; color: var(--text-med); margin-top: 2px; }
  .episode-overview {
    font-size: 11px; color: var(--text-med); margin-top: 4px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }
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

  /* Same flexbox collapse as .detail-hero above: overflow-y:auto zeroes
     this item's automatic min-height as a child of .detail's flex
     column, and a 1180-episode series is more than enough overflow to
     trigger the squeeze every time. flex-shrink:0 opts out. */
  .torrent-list { display: flex; flex-direction: column; gap: 4px; overflow-y: auto; flex-shrink: 0; }
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
  .badge.warn { background: rgba(255, 140, 140, 0.14); color: #ff9a9a; }
  .torrent-row.dead { opacity: 0.55; }
  .torrent-extra { flex-shrink: 0; font-size: 11px; color: var(--text-med); width: 60px; text-align: right; }

  .player { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; align-items: center; justify-content: center; }
  .player.pseudo-fullscreen {
    position: fixed; inset: 0; z-index: 1000; gap: 6px; padding: 10px;
    background: #000; border-radius: 0;
  }
  .video-shell {
    position: relative; width: 100%; max-height: 100%;
    display: flex; align-items: center; justify-content: center;
  }
  .player.pseudo-fullscreen .video-shell { width: 100%; height: 100%; max-height: 100%; }
  .video { width: 100%; max-height: 100%; border-radius: var(--radius); background: #000; display: block; }
  .player.pseudo-fullscreen .video { height: 100%; max-height: 100%; border-radius: 0; object-fit: contain; }

  .video-banner-top {
    position: absolute; top: 12px; left: 50%; transform: translateX(-50%); z-index: 2;
  }
  .video-controls-overlay {
    position: absolute; left: 0; right: 0; bottom: 0; z-index: 2;
    display: flex; flex-direction: column; gap: 6px; padding: 10px 14px 12px;
    background: linear-gradient(to top, rgba(0, 0, 0, 0.85), rgba(0, 0, 0, 0));
    border-radius: 0 0 var(--radius) var(--radius);
  }
  .player.pseudo-fullscreen .video-controls-overlay { border-radius: 0; }
  .video-seek-row { display: flex; align-items: center; gap: 8px; }
  .video-seek-row input[type="range"] { flex: 1; }
  .video-time { font-size: 11px; color: #fff; width: 42px; text-align: center; flex-shrink: 0; }
  .video-controls-row { display: flex; align-items: center; gap: 8px; }
  .icon-btn {
    display: flex; align-items: center; justify-content: center;
    width: 34px; height: 34px; flex-shrink: 0;
    background: transparent; border: none; border-radius: 50%;
    color: #fff; cursor: pointer; padding: 0;
  }
  .icon-btn:hover { background: rgba(255, 255, 255, 0.18); }
  .icon-btn.active { color: var(--pri); }

  .subtitle-menu-wrap { position: relative; }
  .subtitle-menu {
    position: absolute; bottom: calc(100% + 8px); right: 0; z-index: 3;
    min-width: 180px; display: flex; flex-direction: column;
    background: var(--panel); border: 1px solid var(--border-a);
    border-radius: var(--radius-sm); overflow: hidden;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  }
  .subtitle-menu button {
    background: transparent; border: none; color: var(--text);
    text-align: left; padding: 9px 14px; font-size: 12px; cursor: pointer;
  }
  .subtitle-menu button:hover:not(:disabled) { background: rgba(255, 255, 255, 0.09); }
  .subtitle-menu button.selected { color: var(--pri); }
  .subtitle-menu button:disabled { color: var(--text-dim); cursor: not-allowed; }
  .video-volume-slider { width: 80px; }
  .video-controls-spacer { flex: 1; }

  .ghost-btn {
    background: transparent; border: 1px solid var(--border-a); border-radius: var(--radius-sm);
    color: var(--text-dim); padding: 7px 14px; font-size: 12px; cursor: pointer;
  }
  .stop-btn { flex-shrink: 0; }
  .autoadvance-banner {
    display: flex; align-items: center; gap: 12px; justify-content: center;
    font-size: 13px; color: var(--text-med); padding: 6px;
  }
  .skip-banner {
    display: flex; align-items: center; gap: 10px; justify-content: center;
    font-size: 13px; color: var(--text); padding: 8px 14px;
    background: var(--panel); border: 1px solid var(--border-a); border-radius: var(--radius-sm);
  }
  .primary-btn {
    background: var(--pri-gho); border: 1px solid var(--pri-dim); border-radius: var(--radius-sm);
    color: var(--pri); padding: 6px 14px; font-size: 12px; cursor: pointer;
  }

  .thumb-fallback { background: var(--panel-2); }

  @media (max-width: 760px) {
    .panel { padding: 14px; }
    .anime-intro { align-items: stretch; flex-direction: column; padding: 18px; gap: 16px; }
    .anime-search { width: 100%; }
    .grid { grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 14px 10px; }
  }
</style>
