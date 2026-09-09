<script>
  import { playback } from '../stores/sse.js'
  import { getSongDetails, getAudioAnalysis } from '../api.js'

  // Right-side "now playing" info panel — ports ui/panels/music.py's
  // details_panel (MusicModePanelV2, music.py:280-318): cover, title,
  // artist/album/year/duration, and an "INFORMACIÓN DEL ARTISTA" block with
  // photo + bio (_render_details_html, music.py:2655-2687). Clicking the
  // artist or album name opens that page (old UI's music:artist / music:album
  // links, music.py:2689-2695) via the callbacks below instead of an <a href>.

  let { onOpenArtist, onOpenAlbum } = $props()

  let details = $state(null)
  let loading = $state(false)
  let loadedKey = ''

  let key = $derived(`${$playback.videoId || ''}|${$playback.title || ''}|${$playback.artists || ''}`)

  $effect(() => {
    const p = $playback
    const k = key
    if (!p.title || k === loadedKey) return
    loadedKey = k
    loading = true
    details = null
    getSongDetails(p.videoId || '', p.title || '', p.artists || '')
      .then((d) => {
        if (key === k) details = d
      })
      .catch(() => {
        if (key === k) details = null
      })
      .finally(() => {
        if (key === k) loading = false
      })
  })

  // BPM/key are retrieved separately from catalog providers. The backend
  // cache is keyed on videoId, while title and artist names let providers
  // find the actual song.
  let analysis = $state(null)
  let analysisLoading = $state(false)
  let analyzedVideoId = ''

  $effect(() => {
    const p = $playback
    const vid = p.videoId || ''
    if (!vid || vid === analyzedVideoId) return
    analyzedVideoId = vid
    analysisLoading = true
    analysis = null
    getAudioAnalysis(vid, p.title || '', p.artists || '', p.duration || 0)
      .then((a) => {
        if ($playback.videoId === vid) analysis = a
      })
      .catch(() => {
        if ($playback.videoId === vid) analysis = null
      })
      .finally(() => {
        if ($playback.videoId === vid) analysisLoading = false
      })
  })

  let bpmLabel = $derived(analysis?.bpm ? `${analysis.bpm} BPM` : '')
  let keyLabel = $derived(
    analysis?.key && analysis?.camelot ? `${analysis.key} (${analysis.camelot})`
      : analysis?.key || analysis?.camelot || ''
  )
  // GetSongBPM's terms require crediting them wherever their data shows up
  // (that's the trade for the free API) — see actions/audio_analysis.py.
  let showGetSongBpmCredit = $derived(analysis?.key_source === 'getsongbpm')

  let cover = $derived(details?.thumbnail || $playback.thumbnail || '')
  let artistName = $derived(details?.artistName || $playback.artists || '')
  let artistDesc = $derived(details?.artistDescription || '')
  let artistPhoto = $derived(details?.artistThumbnail || '')
  // Once loading finishes, the artist search may legitimately turn up no
  // bio at all (common for "Topic" / auto-generated channels) — say so
  // instead of leaving a block with just a heading and no content, which
  // reads as broken rather than "no data".
  let artistNote = $derived(
    loading ? 'Cargando información del artista…'
      : (!artistDesc && !artistPhoto ? 'Sin biografía disponible en YouTube Music.' : '')
  )
</script>

{#if $playback.title}
  <aside class="now-playing">
    {#if cover}
      <img src={cover} alt="" class="cover" decoding="async" />
    {:else}
      <div class="cover thumb-fallback"></div>
    {/if}

    <h2 class="title">{$playback.title}</h2>
    <button
      class="link artists"
      onclick={() => onOpenArtist(details?.artistBrowseId || '', artistName)}
      disabled={!artistName}
    >{$playback.artists || ''}</button>

    <table class="meta">
      <tbody>
        <tr>
          <td class="label">Álbum</td>
          <td>
            {#if details?.album}
              <button class="link" onclick={() => onOpenAlbum(details.albumId || '', details.album)}>{details.album}</button>
            {:else}<span class="dim">—</span>{/if}
          </td>
        </tr>
        <tr>
          <td class="label">Año</td>
          <td>{details?.year || '—'}</td>
        </tr>
        <tr>
          <td class="label">Artista</td>
          <td>
            {#if artistName}
              <button class="link" onclick={() => onOpenArtist(details?.artistBrowseId || '', artistName)}>{artistName}</button>
            {:else}<span class="dim">—</span>{/if}
          </td>
        </tr>
        <tr>
          <td class="label">Duración</td>
          <td>{details?.duration || '—'}</td>
        </tr>
        <tr>
          <td class="label">Ritmo</td>
          <td>
            {#if analysisLoading}<span class="dim">Analizando…</span>
            {:else if bpmLabel}{bpmLabel}
            {:else}<span class="dim">—</span>{/if}
          </td>
        </tr>
        <tr>
          <td class="label">Tono</td>
          <td>
            {#if analysisLoading}<span class="dim">Analizando…</span>
            {:else if keyLabel}{keyLabel}
            {:else}<span class="dim">—</span>{/if}
          </td>
        </tr>
      </tbody>
    </table>
    {#if bpmLabel || keyLabel}
      <p class="approx-note">
        Datos de catálogo, no siempre disponibles ni exactos para cada canción.
        {#if showGetSongBpmCredit}
          Tono por <a href="https://getsongbpm.com" target="_blank" rel="noopener">GetSongBPM.com</a>.
        {/if}
      </p>
    {/if}

    {#if artistName || loading}
      <div class="artist-block">
        <div class="artist-heading">INFORMACIÓN DEL ARTISTA</div>
        {#if artistPhoto}
          <img src={artistPhoto} alt="" class="artist-photo" decoding="async" />
        {/if}
        {#if artistName}
          <button
            class="link artist-name"
            onclick={() => onOpenArtist(details?.artistBrowseId || '', artistName)}
          >{artistName}</button>
        {/if}
        {#if artistDesc}
          <p class="artist-desc">{artistDesc}</p>
        {:else if artistNote}
          <p class="artist-desc dim">{artistNote}</p>
        {/if}
      </div>
    {/if}
  </aside>
{/if}

<style>
  .now-playing {
    width: 300px;
    flex-shrink: 0;
    overflow-y: auto;
    padding: 18px 16px;
    border-left: 1px solid var(--border-a);
    background: var(--panel);
  }

  .cover {
    display: block;
    width: 100%;
    aspect-ratio: 1 / 1;
    border-radius: 10px;
    object-fit: cover;
    object-position: center;
    background: var(--panel-2);
    margin-bottom: 14px;
  }

  .title {
    margin: 0 0 3px 0;
    font-size: 19px;
    line-height: 1.15;
    color: var(--text);
  }

  .link {
    background: none;
    border: none;
    padding: 0;
    margin: 0;
    font: inherit;
    color: var(--text-med);
    text-align: left;
    cursor: pointer;
  }
  .link:hover:not(:disabled) {
    color: var(--pri);
    text-decoration: underline;
  }
  .link:disabled {
    cursor: default;
  }

  .artists {
    font-size: 13px;
    font-weight: 700;
    margin-bottom: 14px;
    display: block;
  }

  .meta {
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 4px;
  }

  .meta td {
    padding: 6px 0;
    vertical-align: middle;
    font-size: 13px;
  }

  .meta td.label {
    color: var(--text-dim);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    width: 78px;
    white-space: nowrap;
  }

  .meta .link {
    font-weight: 700;
    color: var(--text);
  }

  .dim {
    color: var(--text-dim);
  }

  .approx-note {
    color: var(--text-dim);
    font-size: 11px;
    font-style: italic;
    margin: 0 0 4px 0;
  }
  .approx-note a {
    color: var(--text-dim);
  }
  .approx-note a:hover {
    color: var(--pri);
  }

  .artist-block {
    margin-top: 24px;
  }

  .artist-heading {
    color: var(--text-dim);
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.06em;
    margin-bottom: 12px;
  }

  .artist-photo {
    display: block;
    width: 100%;
    height: auto;
    border-radius: 10px;
    object-fit: cover;
    margin-bottom: 12px;
    background: var(--panel-2);
  }

  .artist-name {
    font-size: 15px;
    font-weight: 800;
    color: var(--text);
    display: block;
    margin-bottom: 8px;
  }

  .artist-desc {
    color: var(--text-med);
    font-size: 13px;
    line-height: 1.5;
    margin: 0;
    white-space: pre-line;
  }

  @media (max-width: 1100px) {
    .now-playing {
      display: none;
    }
  }
</style>
