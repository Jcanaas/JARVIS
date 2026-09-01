import { writable } from 'svelte/store'
import { connectEvents } from '../api.js'

export const toasts = writable([])
export const playback = writable({})
export const whatsappUnread = writable(0)
export const voiceState = writable('LISTENING')
export const micAvailable = writable(true)
export const musicPlaying = writable(false)
export const downloads = writable([])
// Fired for events a specific panel needs to react to inline (new message,
// "open this chat") rather than mirror into a store — panels subscribe via
// events.subscribe and filter by .type themselves.
export const events = writable({ type: '', data: {} })

let _toastId = 0

function pushToast(text, duration) {
  const id = ++_toastId
  toasts.update((list) => [...list, { id, text, duration }])
  setTimeout(() => {
    toasts.update((list) => list.filter((t) => t.id !== id))
  }, duration || 2500)
}

let _started = false

// Call once (App.svelte does this on mount) to open the single shared
// EventSource and start fanning events into the stores above.
export function startEventStream() {
  if (_started) return
  _started = true

  connectEvents((type, data) => {
    switch (type) {
      case 'toast':
        pushToast(data.text, data.duration)
        break
      case 'error':
        pushToast(data.message || data.text || 'Error', 4000)
        break
      case 'state':
        voiceState.set(data.state)
        break
      case 'mic':
        micAvailable.set(!!data.available)
        break
      case 'playback':
        playback.set(data)
        break
      case 'music_playing':
        musicPlaying.set(!!data.playing)
        break
      case 'downloads':
        downloads.set(Array.isArray(data) ? data : [])
        break
      case 'playback_volume':
        playback.update((p) => ({ ...p, volume: data.volume }))
        break
      case 'playback_like':
        playback.update((p) =>
          p.videoId === data.videoId ? { ...p, liked: data.liked } : p
        )
        break
      case 'whatsapp_unread':
        whatsappUnread.set(data.count)
        break
      default:
        break
    }
    events.set({ type, data })
  })
}
