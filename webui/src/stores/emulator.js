import { writable } from 'svelte/store'

// True while a game is actually running in the Emulators panel. App.svelte's
// single-letter panel-switch shortcuts (P for Películas, U for Emuladores…)
// check this before acting — mirrors ui/__init__.py's
// _shortcut_focus_is_editing(), which blocks those same shortcuts whenever
// the focused widget is (or is inside) RetroScreen, for the same reason: the
// emulator keyboard mapping uses plain letters (X/Z/S/A/Q/W…) that collide
// with the panel shortcuts, so without this guard pressing a game button
// would also navigate away from the panel.
export const emulatorPlaying = writable(false)
