<script>
  import { activePanel, PANELS } from '../stores/nav.js'
  import NotAvailable from './NotAvailable.svelte'
  import Home from '../panels/Home.svelte'
  import WhatsAppPanel from '../panels/WhatsAppPanel.svelte'
  import MusicPanel from '../panels/MusicPanel.svelte'
  import Gmail from '../panels/Gmail.svelte'
  import Drive from '../panels/Drive.svelte'
  import Calendar from '../panels/Calendar.svelte'
  import CardTrader from '../panels/CardTrader.svelte'
  import Settings from '../panels/Settings.svelte'
  import Movies from '../panels/Movies.svelte'
  import TVShows from '../panels/TVShows.svelte'
  import Anime from '../panels/Anime.svelte'
  import YouTube from '../panels/YouTube.svelte'
  import Emulators from '../panels/Emulators.svelte'

  const COMPONENTS = {
    home: Home,
    whatsapp: WhatsAppPanel,
    music: MusicPanel,
    gmail: Gmail,
    drive: Drive,
    calendar: Calendar,
    cardtrader: CardTrader,
    settings: Settings,
    movies: Movies,
    tv: TVShows,
    anime: Anime,
    youtube: YouTube,
    games: Emulators,
  }

  let currentLabel = $derived(PANELS.find((p) => p.id === $activePanel)?.label ?? $activePanel)

  // Keep-alive: the Qt shell uses a QStackedWidget, which only ever hides a
  // panel widget on switch, never destroys it — the browsed playlist, the
  // open WhatsApp chat, scroll position etc. all survive. Destroying and
  // recreating the component on every switch (the previous behavior here)
  // silently threw all of that away, so every visit to Música/WhatsApp
  // landed back at the top-level list. Once a panel has been visited its
  // component instance is kept mounted (just display:none'd) instead.
  // Each panel still gets an `active` prop so it can pause its own
  // background work (RAF loop, polling) while hidden rather than running
  // forever for every panel ever visited.
  let visited = $state([])
  $effect(() => {
    if ($activePanel && COMPONENTS[$activePanel] && !visited.includes($activePanel)) {
      visited = [...visited, $activePanel]
    }
  })
</script>

<div class="host">
  {#each visited as id (id)}
    {@const Component = COMPONENTS[id]}
    <div class="slot" class:slot-hidden={$activePanel !== id}>
      <Component active={$activePanel === id} />
    </div>
  {/each}
  {#if !COMPONENTS[$activePanel]}
    <NotAvailable label={currentLabel} />
  {/if}
</div>

<style>
  .host {
    flex: 1;
    min-width: 0;
    /* min-height:0 is what lets a panel's inner scroll area actually
       scroll — without it a long list stretches this box past the window
       and gets clipped instead. */
    min-height: 0;
    height: 100%;
    overflow: hidden;
    position: relative;
  }

  .slot {
    height: 100%;
    width: 100%;
    min-height: 0;
  }

  .slot-hidden {
    display: none;
  }
</style>
