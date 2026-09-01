import { writable } from 'svelte/store'

// On a HiDPI display the window's CSS width is roughly half its pixel
// width, so the side panel can eat most of the usable area before the user
// ever sees a panel. Start collapsed when there isn't room for both; the
// header toggle still opens it.
const NARROW = 900

export const rightPanelCollapsed = writable(
  typeof window !== 'undefined' && window.innerWidth < NARROW
)
