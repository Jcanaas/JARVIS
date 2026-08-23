/**
 * Single source of truth for "what is playing, and who do I tell to change it".
 *
 * The music can come out of the PC's speakers or this phone's, and the system
 * media notification has to describe whichever one is live — otherwise
 * switching outputs leaves the notification showing a track that stopped
 * minutes ago, and its buttons talking to the wrong device.
 *
 * The Música screen owns playback; this module is the seam that lets the
 * notification bridge read it without being mounted inside that screen.
 */

export type PlaybackSource = "pc" | "phone";

export type NowPlaying = {
  source: PlaybackSource;
  /** Stable identity of the track; changes drive metadata refreshes. */
  trackKey: string;
  title: string;
  artists: string;
  thumbnail?: string;
  /** `#rrggbb` sampled from the cover, used to tint the notification. */
  coverColor?: string;
  duration: number;
  position: number;
  playing: boolean;
};

export type TransportCommand = "toggle" | "play" | "pause" | "next" | "previous" | "seek";
export type TransportHandler = (command: TransportCommand, value?: number) => void;

const EMPTY: NowPlaying = {
  source: "pc", trackKey: "", title: "", artists: "",
  duration: 0, position: 0, playing: false,
};

let current: NowPlaying = EMPTY;
let transport: TransportHandler | null = null;
const listeners = new Set<(state: NowPlaying) => void>();

export function getNowPlaying(): NowPlaying {
  return current;
}

export function setNowPlaying(next: NowPlaying) {
  current = next;
  listeners.forEach((fn) => fn(next));
}

export function clearNowPlaying() {
  setNowPlaying(EMPTY);
}

export function subscribeNowPlaying(fn: (state: NowPlaying) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * Registered by the Música screen, which is the only place that knows whether
 * a command should become an HTTP call to the desktop or a local player call.
 */
export function setTransportHandler(handler: TransportHandler | null) {
  transport = handler;
}

export function runTransport(command: TransportCommand, value?: number) {
  transport?.(command, value);
}
