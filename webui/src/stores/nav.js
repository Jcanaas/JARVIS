import { writable } from 'svelte/store'

// Same order and letter shortcuts as the left icon rail in ui/__init__.py
// (nav_items, ~946-991) — the point of this list is to mirror the PyQt6
// structure 1:1, not to redesign it.
export const PANELS = [
  { id: 'home', label: 'Inicio', subtitle: 'Núcleo de voz', icon: 'home', key: 'I', available: true },
  { id: 'whatsapp', label: 'WhatsApp', subtitle: 'Mensajes', icon: 'whatsapp', key: 'W', available: true },
  { id: 'gmail', label: 'Gmail', subtitle: 'Correo', icon: 'gmail', key: 'C', available: true },
  { id: 'drive', label: 'Drive', subtitle: 'Archivos', icon: 'drive', key: 'D', available: true },
  { id: 'music', label: 'Música', subtitle: 'Reproductor', icon: 'music', key: 'M', available: true },
  { id: 'youtube', label: 'YouTube', subtitle: 'Vídeos', icon: 'youtube', key: 'Y', available: true },
  { id: 'movies', label: 'Películas', subtitle: 'Streaming', icon: 'movies', key: 'P', available: true },
  { id: 'anime', label: 'Anime', subtitle: 'Streaming', icon: 'anime', key: 'N', available: true },
  { id: 'tv', label: 'TV', subtitle: 'En directo', icon: 'tv', key: 'T', available: true },
  { id: 'games', label: 'Emuladores', subtitle: 'Juegos', icon: 'games', key: 'U', available: false },
  { id: 'calendar', label: 'Calendario', subtitle: 'Agenda', icon: 'calendar', key: 'A', available: true },
  { id: 'cardtrader', label: 'CardTrader', subtitle: 'Mercado de cartas', icon: 'cardtrader', key: 'G', available: true },
  { id: 'settings', label: 'Ajustes', subtitle: 'Configuración', icon: 'settings', key: 'J', available: true },
]

export const activePanel = writable('home')

export function panelByKey(key) {
  return PANELS.find((p) => p.key === key.toUpperCase())
}
