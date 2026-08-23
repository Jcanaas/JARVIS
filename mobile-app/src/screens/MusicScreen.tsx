import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View, Text, StyleSheet, FlatList, TextInput, TouchableOpacity, ScrollView, Image, ActivityIndicator,
  Modal, Dimensions,
} from "react-native";
import { useFocusEffect } from "@react-navigation/native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Ionicons } from "@react-native-vector-icons/ionicons";
import { useAudioPlayer, useAudioPlayerStatus, setAudioModeAsync } from "expo-audio";
import { C, spacing, radius } from "../theme";
import { api, Playlist, Track, Status, Artist, ArtistDetail, trackStreamUrl } from "../api";
import { ProgressBar, VolumeSlider, PlaybackSnapshot } from "../components/PlayerBars";
import { OutputMode, getOutputMode, setOutputMode, subscribeOutputMode, loadOutputMode } from "../outputMode";
import { setNowPlaying, setTransportHandler } from "../playback";
import LyricsView from "../components/LyricsView";

type Tab = "library" | "search";
type SearchMode = "songs" | "artists";
type SheetTab = "queue" | "lyrics";

function Cover({ uri, size, radius: r }: { uri?: string; size: number; radius: number }) {
  const [failed, setFailed] = useState(false);
  if (!uri || failed) {
    return (
      <View style={[covStyles.fallback, { width: size, height: size, borderRadius: r }]}>
        <Ionicons name="musical-notes" size={size * 0.38} color={C.textMed} />
      </View>
    );
  }
  return (
    <Image
      source={{ uri }}
      style={{ width: size, height: size, borderRadius: r, backgroundColor: C.panel2 }}
      onError={() => setFailed(true)}
    />
  );
}

const covStyles = StyleSheet.create({
  fallback: {
    backgroundColor: C.panel2, borderWidth: 1, borderColor: C.borderA,
    alignItems: "center", justifyContent: "center",
  },
});

export default function MusicScreen() {
  const [tab, setTab] = useState<Tab>("library");
  const [status, setStatus] = useState<Status | null>(null);
  const [queue, setQueue] = useState<Track[]>([]);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [openPlaylist, setOpenPlaylist] = useState<Playlist | null>(null);
  const [playlistTracks, setPlaylistTracks] = useState<Track[]>([]);
  const [loadingTracks, setLoadingTracks] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Track[]>([]);
  const [searchMode, setSearchMode] = useState<SearchMode>("songs");
  const [artistResults, setArtistResults] = useState<Artist[]>([]);
  const [openArtist, setOpenArtist] = useState<ArtistDetail | null>(null);
  const [loadingArtist, setLoadingArtist] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [playerOpen, setPlayerOpen] = useState(false);
  const [sheetTab, setSheetTab] = useState<SheetTab>("queue");
  // Applies to whatever you start next; the backend shuffles when building
  // the queue, so it cannot be toggled retroactively on the current one.
  const [shuffle, setShuffle] = useState(false);
  const [likeBusy, setLikeBusy] = useState(false);
  // Mirrors status.music.liked, but can run ahead of it: the desktop only
  // re-reports the like state on its next poll.
  const [likedLocal, setLikedLocal] = useState(false);
  // Fed to <ProgressBar>, which extrapolates + animates from it natively —
  // no JS interval re-rendering this screen just to advance a progress bar.
  const [snapshot, setSnapshot] = useState<PlaybackSnapshot>({
    position: 0, duration: 0, playing: false, at: Date.now(), trackKey: "",
  });
  const searchDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastTrackRef = useRef<string>("");

  // ── output: PC speakers vs this phone ──────────────────────────────────
  const [mode, setMode] = useState<OutputMode>(getOutputMode());
  const [phoneIndex, setPhoneIndex] = useState(0);
  const phonePlayer = useAudioPlayer();
  const phoneStatus = useAudioPlayerStatus(phonePlayer);
  const modeRef = useRef(mode);
  modeRef.current = mode;

  useEffect(() => {
    loadOutputMode().then(setMode);
    return subscribeOutputMode(setMode);
  }, []);

  useEffect(() => {
    // Keep playing with the screen off / the ringer silenced — otherwise
    // "playing on the phone" stops the moment the user pockets it.
    setAudioModeAsync({ playsInSilentMode: true, shouldPlayInBackground: true }).catch(() => {});
  }, []);

  /** The track the phone is (or should be) playing in phone mode. */
  const phoneTrack: Track | undefined = queue[phoneIndex];

  /** Ask the desktop to resolve these streams before they are tapped.
   *
   * On the desktop, selecting a row warms it and the double-click that follows
   * is instant. A tap on the phone has no such step, so without this every tap
   * waits out the resolution first. Fire-and-forget: it runs off the desktop's
   * playback worker and a failure only costs us the head start. */
  const warmTracks = useCallback((tracks: Array<{ videoId?: string }>) => {
    const ids = tracks.filter((t) => t?.videoId).slice(0, 3) as Track[];
    if (ids.length) api.prefetchTracks(ids).catch(() => {});
  }, []);

  // The track the user just tapped, shown while the desktop resolves it. The
  // status poll still describes the previous song for a couple of seconds, and
  // a header that does not move makes a tap look ignored — so people tap again,
  // which queues a second play behind the first.
  const [pendingTrack, setPendingTrack] = useState<Track | null>(null);
  const pendingRef = useRef<Track | null>(null);
  const pendingSinceRef = useRef(0);

  const beginPending = useCallback((track: Track | undefined) => {
    if (!track?.title) return;
    pendingSinceRef.current = Date.now();
    pendingRef.current = track;
    setPendingTrack(track);
    // The old track's progress would otherwise keep running under the new title.
    setSnapshot({
      position: 0, duration: 0, playing: false,
      at: Date.now(), trackKey: track.videoId || "",
    });
  }, []);

  const pollStatus = useCallback(async () => {
    try {
      const s = await api.getStatus();
      setStatus(s);
      const trackKey = s.music.videoId || s.music.title || "";
      // Hand the header back to the real state once the desktop is playing what
      // was tapped — or if it never got there, so a failed play cannot leave a
      // song frozen on screen forever.
      const wasPending = pendingRef.current;
      let pending = wasPending;
      if (
        wasPending &&
        ((wasPending.videoId && wasPending.videoId === s.music.videoId) ||
          Date.now() - pendingSinceRef.current > 15000)
      ) {
        pending = null;
      }
      if (pending !== wasPending) {
        pendingRef.current = pending;
        setPendingTrack(pending);
      }
      // In phone mode the desktop is paused; its position would drag the
      // progress bar back to wherever the PC stopped. While a tap is pending,
      // the desktop is still describing the previous song — same problem.
      if (modeRef.current === "pc" && !pending) {
        setSnapshot({
          position: s.music.position,
          duration: s.music.duration,
          playing: s.music.playing,
          at: Date.now(),
          trackKey,
        });
      }
      // The queue is anchored at the currently playing track, so it goes stale
      // as soon as playback moves on — including when a track simply ends and
      // auto-advances, which no button press would have told us about.
      if (trackKey && trackKey !== lastTrackRef.current) {
        lastTrackRef.current = trackKey;
        loadQueue();
      }
    } catch {
      // transient
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      pollStatus();
      loadQueue();
      const t = setInterval(pollStatus, 2500);
      return () => clearInterval(t);
    }, [pollStatus])
  );

  // Load whatever track the phone cursor points at. In phone mode the phone
  // keeps its OWN cursor over the queue instead of driving the desktop's
  // transport: asking the backend to advance would start the PC's speakers
  // too, which is exactly what the user switched away from.
  useEffect(() => {
    if (mode !== "phone" || !phoneTrack?.videoId) return;
    try {
      phonePlayer.replace({ uri: trackStreamUrl(phoneTrack.videoId) });
      phonePlayer.play();
    } catch {
      // not paired / bad URL — the UI still shows the track as selected
    }
  }, [mode, phoneTrack?.videoId, phonePlayer]);

  // Seconds to resume from once the newly loaded source is ready. Seeking
  // straight after replace() is lost: the player has no duration yet, and the
  // effect above re-loads the source anyway, which rewinds it to zero.
  const pendingSeekRef = useRef<number | null>(null);

  useEffect(() => {
    if (mode !== "phone" || !phoneStatus.isLoaded) return;
    const target = pendingSeekRef.current;
    if (target == null) return;
    pendingSeekRef.current = null;
    phonePlayer.seekTo(target).catch(() => {});
  }, [mode, phoneStatus.isLoaded, phonePlayer]);

  // Auto-advance when a track ends. Guarded per track: didJustFinish can stay
  // true across several status updates, and an unguarded effect would then
  // skip a song for every extra update it saw.
  const finishedTrackRef = useRef<string>("");
  useEffect(() => {
    if (mode !== "phone" || !phoneStatus.didJustFinish) return;
    const key = phoneTrack?.videoId || "";
    if (!key || finishedTrackRef.current === key) return;
    finishedTrackRef.current = key;
    setPhoneIndex((i) => (i + 1 < queue.length ? i + 1 : i));
  }, [mode, phoneStatus.didJustFinish, phoneTrack?.videoId, queue.length]);

  // Re-anchor the progress bar on track / play-state changes only. Feeding it
  // every status tick would restart its animation several times a second.
  useEffect(() => {
    if (mode !== "phone") return;
    setSnapshot({
      position: phoneStatus.currentTime || 0,
      duration: phoneStatus.duration || 0,
      playing: phoneStatus.playing,
      at: Date.now(),
      trackKey: phoneTrack?.videoId || "",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, phoneTrack?.videoId, phoneStatus.playing, phoneStatus.duration]);

  async function switchOutput(next: OutputMode) {
    if (next === mode || switching) return;
    setSwitching(true);
    try {
      if (next === "phone") {
        // Pause FIRST, then read the position: the desktop records where it
        // actually stopped, which beats extrapolating from a poll up to
        // 2.5s old.
        await api.musicAction("pause").catch(() => {});
        let at = 0;
        try {
          at = (await api.getStatus()).music.position || 0;
        } catch {
          at = status?.music.position ?? 0;
        }
        pendingSeekRef.current = at > 1 ? at : null;
        setPhoneIndex(0); // the queue is anchored at the track that was playing
        setOutputMode("phone"); // the load effect streams it and the seek effect resumes it
      } else {
        const at = phoneStatus.currentTime || 0;
        const samePosition = Math.round(at);
        phonePlayer.pause();
        setOutputMode("pc");
        // The queue is anchored on the desktop's own track, so index 0 means
        // the phone never moved off it — jumping there would be refused as
        // "already playing" and would leave the PC paused.
        if (phoneIndex === 0) {
          if (samePosition > 1) {
            await api.musicAction("seek", { seconds: samePosition }).catch(() => {});
          }
          await api.musicAction("resume").catch(() => {});
        } else {
          const target = queue[phoneIndex]?.index;
          if (target !== undefined) {
            // jump_to already starts playback on the PC; seek then lands it
            // on the second the phone was at.
            await api.musicAction("jump_to", { index: target }).catch(() => {});
          }
          if (samePosition > 1) {
            await api.musicAction("seek", { seconds: samePosition }).catch(() => {});
          }
        }
        await pollStatus();
        await loadQueue();
      }
    } finally {
      setSwitching(false);
    }
  }

  async function runControl(action: "previous" | "next" | "toggle") {
    if (switching) return; // ignore taps piling up while a transition is in flight

    if (mode === "phone") {
      if (action === "toggle") {
        phoneStatus.playing ? phonePlayer.pause() : phonePlayer.play();
      } else if (action === "next") {
        setPhoneIndex((i) => (i + 1 < queue.length ? i + 1 : i));
      } else {
        setPhoneIndex((i) => Math.max(0, i - 1));
      }
      return;
    }

    if (action !== "toggle") setSwitching(true);
    try {
      await api.musicAction(action);
    } catch {
      // shows up via next status poll if it truly failed
    }
    await pollStatus();
    if (action !== "toggle") {
      await loadQueue();
      setSwitching(false);
    }
  }

  async function jumpToQueueIndex(absoluteIndex: number | undefined, queuePos?: number) {
    if (mode === "phone") {
      if (queuePos !== undefined) setPhoneIndex(queuePos);
      return;
    }
    if (switching || absoluteIndex === undefined) return;
    if (queuePos !== undefined) beginPending(queue[queuePos]);
    setSwitching(true);
    try {
      await api.musicAction("jump_to", { index: absoluteIndex });
    } catch {
      // shows up via next status poll if it truly failed
    }
    await pollStatus();
    await loadQueue();
    setSwitching(false);
  }

  async function loadQueue() {
    // Warming goes through /api/music/prefetch, never through musicAction: the
    // transport actions share one serialized worker on the desktop, and a burst
    // of warm-ups queued there would sit in front of the user's next tap.
    try {
      const q = await api.getQueue();
      setQueue(q);
      warmTracks(q.slice(1));   // [0] is what's already playing
    } catch { /* transient */ }
  }
  async function loadPlaylists() {
    try { setPlaylists(await api.getPlaylists()); } catch { /* transient */ }
  }

  async function openPlaylistDetail(pl: Playlist) {
    setOpenPlaylist(pl);
    setPlaylistTracks([]);
    setLoadingTracks(true);
    try {
      const tracks = await api.getPlaylistTracks(pl.playlistId);
      setPlaylistTracks(tracks);
      warmTracks(tracks);
    } catch {
      setPlaylistTracks([]);
    }
    setLoadingTracks(false);
  }
  function closePlaylistDetail() {
    setOpenPlaylist(null);
    setPlaylistTracks([]);
  }
  /** Load a selection into the desktop's queue, which is the single source of
   * truth for what plays next. In phone mode the PC is silenced right after,
   * so the queue is built there but heard here. */
  async function startSelection(
    action: string,
    params: Record<string, unknown>,
    tapped?: Track,
  ) {
    beginPending(tapped);
    try {
      await api.musicAction(action, params);
      if (mode === "phone") {
        await api.musicAction("pause").catch(() => {});
        setPhoneIndex(0);
      }
      await pollStatus();
      await loadQueue();
    } catch {
      // transient — the next status poll reconciles
    }
  }

  function playWholePlaylist() {
    if (!openPlaylist) return;
    startSelection(
      "play_playlist",
      { playlist_id: openPlaylist.playlistId, shuffle },
      shuffle ? undefined : playlistTracks[0],   // shuffled: we can't know which one
    );
  }
  function playTrackInPlaylist(index: number) {
    if (!playlistTracks.length) return;
    startSelection(
      "play_tracks",
      { tracks: playlistTracks, start_index: index, shuffle },
      shuffle ? undefined : playlistTracks[index],
    );
  }
  function playSearchResult(index: number) {
    if (!results.length) return;
    startSelection(
      "play_tracks",
      { tracks: results, start_index: index, shuffle },
      shuffle ? undefined : results[index],
    );
  }

  function onSearchChange(q: string) {
    setQuery(q);
    if (searchDebounce.current) clearTimeout(searchDebounce.current);
    if (!q.trim()) { setResults([]); setArtistResults([]); return; }
    searchDebounce.current = setTimeout(async () => {
      const term = q.trim();
      if (searchMode === "artists") {
        try { setArtistResults(await api.searchArtists(term)); } catch { setArtistResults([]); }
      } else {
        try {
          const songs = await api.searchSongs(term);
          setResults(songs);
          warmTracks(songs);
        } catch { setResults([]); }
      }
    }, 400);
  }

  function switchSearchMode(m: SearchMode) {
    setSearchMode(m);
    if (!query.trim()) return;
    // Re-run the pending query against the newly selected mode instead of
    // leaving stale results from the other mode on screen.
    if (searchDebounce.current) clearTimeout(searchDebounce.current);
    const term = query.trim();
    if (m === "artists") {
      api.searchArtists(term).then(setArtistResults).catch(() => setArtistResults([]));
    } else {
      api.searchSongs(term)
        .then((songs) => { setResults(songs); warmTracks(songs); })
        .catch(() => setResults([]));
    }
  }

  async function openArtistDetail(browseId: string) {
    setLoadingArtist(true);
    setOpenArtist(null);
    try {
      const artist = await api.getArtist(browseId);
      setOpenArtist(artist);
      warmTracks(artist.top_songs || []);
    } catch {
      setOpenArtist(null);
    }
    setLoadingArtist(false);
  }

  function playArtistTopSongs() {
    if (!openArtist?.top_songs?.length) return;
    startSelection(
      "play_tracks",
      { tracks: openArtist.top_songs, start_index: 0, shuffle },
      shuffle ? undefined : openArtist.top_songs[0],
    );
  }
  function playArtistTrack(index: number) {
    if (!openArtist?.top_songs?.length) return;
    startSelection(
      "play_tracks",
      { tracks: openArtist.top_songs, start_index: index, shuffle },
      shuffle ? undefined : openArtist.top_songs[index],
    );
  }

  function switchTab(t: Tab) {
    setTab(t);
    if (t === "library") loadPlaylists();
  }

  async function toggleLike() {
    const videoId = onPhone ? phoneTrack?.videoId : status?.music.videoId;
    if (!videoId || likeBusy) return;
    const next = !liked;
    setLikeBusy(true);
    setLikedLocal(next); // optimistic: the desktop poll confirms it a beat later
    try {
      await api.musicAction("set_like", { video_id: videoId, liked: next });
    } catch {
      setLikedLocal(!next);
    }
    setLikeBusy(false);
  }

  const insets = useSafeAreaInsets();
  const onPhone = mode === "phone";
  // One shape for the header regardless of which device is actually playing.
  const nowPlaying = onPhone
    ? {
        title: phoneTrack?.title || "",
        artists: phoneTrack?.artists || "",
        thumbnail: phoneTrack?.thumbnail,
        playing: phoneStatus.playing,
        volume: Math.round((phonePlayer.volume ?? 1) * 100),
      }
    : pendingTrack
    ? {
        // Tapped, not yet resolved on the desktop. Showing the previous song
        // here for two seconds is what makes a tap read as ignored.
        title: pendingTrack.title || "",
        artists: pendingTrack.artists || "",
        thumbnail: pendingTrack.thumbnail,
        playing: false,
        volume: status?.music.volume ?? 100,
      }
    : {
        title: status?.music.title || "",
        artists: status?.music.artists || "",
        thumbnail: status?.music.thumbnail,
        playing: !!status?.music.playing,
        volume: status?.music.volume ?? 100,
      };
  const loadingTrack = !onPhone && !!pendingTrack;
  const hasTrack = !!nowPlaying.title;
  const liked = likedLocal;

  // Follow the desktop's own like state, but only when it reports a different
  // track — otherwise it would undo an optimistic toggle a beat after the tap.
  const likeTrackRef = useRef("");
  useEffect(() => {
    const key = status?.music.videoId || "";
    if (key && key !== likeTrackRef.current) {
      likeTrackRef.current = key;
      setLikedLocal(!!status?.music.liked);
    }
  }, [status?.music.videoId, status?.music.liked]);

  // Publish whatever is playing — on either device — so the system media
  // notification follows the music instead of being stuck on the PC's view.
  useEffect(() => {
    if (!onPhone) return; // the bridge polls the desktop itself
    setNowPlaying({
      source: "phone",
      trackKey: phoneTrack?.videoId || "",
      title: phoneTrack?.title || "",
      artists: phoneTrack?.artists || "",
      thumbnail: phoneTrack?.thumbnail,
      duration: phoneStatus.duration || 0,
      position: phoneStatus.currentTime || 0,
      playing: phoneStatus.playing,
    });
  }, [onPhone, phoneTrack?.videoId, phoneStatus.playing, phoneStatus.duration, phoneStatus.currentTime]);

  // The notification's buttons land here, and only this screen knows whether
  // they mean "tell the PC" or "drive the local player".
  useEffect(() => {
    setTransportHandler((command, value) => {
      if (command === "seek") {
        if (typeof value !== "number") return;
        if (onPhone) phonePlayer.seekTo(value).catch(() => {});
        else api.musicAction("seek", { seconds: value }).catch(() => {});
        return;
      }
      if (command === "play" || command === "pause") {
        if (onPhone) {
          command === "play" ? phonePlayer.play() : phonePlayer.pause();
        } else {
          api.musicAction(command === "play" ? "resume" : "pause").catch(() => {});
        }
        return;
      }
      runControl(command);
    });
    return () => setTransportHandler(null);
  });

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <View style={styles.topBar}>
        <Text style={styles.topTitle}>Música</Text>
        <View style={styles.outputRow}>
          {(["pc", "phone"] as OutputMode[]).map((m) => (
            <TouchableOpacity
              key={m}
              style={[styles.outputBtn, mode === m && styles.outputBtnOn]}
              onPress={() => switchOutput(m)}
              disabled={switching}
            >
              <Ionicons
                name={m === "pc" ? "desktop-outline" : "phone-portrait-outline"}
                size={13}
                color={mode === m ? C.pri : C.textMed}
              />
              <Text style={[styles.outputText, mode === m && styles.outputTextOn]}>
                {m === "pc" ? "PC" : "Móvil"}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
      </View>

      <View style={styles.subnav}>
        {(["library", "search"] as Tab[]).map((t) => (
          <TouchableOpacity key={t} style={[styles.subtab, tab === t && styles.subtabActive]} onPress={() => switchTab(t)}>
            <Ionicons
              name={t === "library" ? "library" : "search"}
              size={14}
              color={tab === t ? C.pri : C.textMed}
            />
            <Text style={[styles.subtabText, tab === t && styles.subtabTextActive]}>
              {t === "library" ? "Biblioteca" : "Buscar"}
            </Text>
          </TouchableOpacity>
        ))}
      </View>

      {tab === "library" && !openPlaylist && (
        <FlatList
          contentContainerStyle={{ padding: spacing.lg, paddingBottom: 120, gap: spacing.md }}
          data={playlists}
          numColumns={2}
          keyExtractor={(p) => p.playlistId}
          columnWrapperStyle={{ gap: spacing.md, justifyContent: "flex-start" }}
          ListEmptyComponent={<Text style={styles.emptyHint}>No se encontraron playlists.</Text>}
          renderItem={({ item }) => (
            <TouchableOpacity style={styles.plCard} onPress={() => openPlaylistDetail(item)} activeOpacity={0.75}>
              <Cover uri={item.thumbnail} size={148} radius={10} />
              <Text style={styles.plTitle} numberOfLines={2}>{item.title}</Text>
              <Text style={styles.plSub}>{item.itemCount || 0} canciones</Text>
            </TouchableOpacity>
          )}
        />
      )}

      {tab === "library" && openPlaylist && (
        <View style={{ flex: 1 }}>
          <View style={styles.plDetailHero}>
            <TouchableOpacity style={styles.backBtn} onPress={closePlaylistDetail} hitSlop={10}>
              <Ionicons name="chevron-back" size={18} color={C.pri} />
            </TouchableOpacity>
            <Cover uri={openPlaylist.thumbnail} size={120} radius={12} />
            <Text style={styles.plDetailTitle} numberOfLines={2}>{openPlaylist.title}</Text>
            <Text style={styles.plDetailSub}>{playlistTracks.length || openPlaylist.itemCount || 0} canciones</Text>
            <View style={styles.heroActions}>
              <TouchableOpacity
                style={[styles.shuffleChip, shuffle && styles.shuffleChipOn]}
                onPress={() => setShuffle((v) => !v)}
              >
                <Ionicons name="shuffle" size={16} color={shuffle ? C.pri : C.textMed} />
              </TouchableOpacity>
              <TouchableOpacity style={styles.playAllBtn} onPress={playWholePlaylist} activeOpacity={0.8}>
                <Ionicons name={shuffle ? "shuffle" : "play"} size={16} color="#08101F" />
                <Text style={styles.playAllText}>{shuffle ? "Aleatorio" : "Reproducir"}</Text>
              </TouchableOpacity>
            </View>
          </View>
          {loadingTracks ? (
            <Text style={styles.emptyHint}>Cargando canciones…</Text>
          ) : (
            <FlatList
              contentContainerStyle={{ padding: spacing.lg, paddingTop: spacing.sm, paddingBottom: 120 }}
              data={playlistTracks}
              keyExtractor={(t, i) => t.videoId || String(i)}
              ListEmptyComponent={<Text style={styles.emptyHint}>Esta playlist está vacía.</Text>}
              renderItem={({ item, index }) => (
                <TouchableOpacity style={styles.srRow} onPress={() => playTrackInPlaylist(index)} activeOpacity={0.6}>
                  <Cover uri={item.thumbnail} size={44} radius={8} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.srTitle} numberOfLines={1}>{item.title}</Text>
                    <Text style={styles.srArtist} numberOfLines={1}>{item.artists}</Text>
                  </View>
                  {!!item.duration && <Text style={styles.trackDuration}>{item.duration}</Text>}
                </TouchableOpacity>
              )}
            />
          )}
        </View>
      )}

      {tab === "search" && (
        <View style={{ flex: 1 }}>
          <View style={styles.searchBox}>
            <TextInput
              style={styles.searchInput}
              value={query}
              onChangeText={onSearchChange}
              placeholder="Buscar canciones o artistas…"
              placeholderTextColor={C.textMed}
              cursorColor={C.pri}
              selectionColor={C.pri}
              keyboardAppearance="dark"
            />
            <View style={styles.searchModeRow}>
              {(["songs", "artists"] as SearchMode[]).map((m) => (
                <TouchableOpacity
                  key={m}
                  style={[styles.searchModeChip, searchMode === m && styles.searchModeChipOn]}
                  onPress={() => switchSearchMode(m)}
                >
                  <Text style={[styles.searchModeText, searchMode === m && styles.searchModeTextOn]}>
                    {m === "songs" ? "Canciones" : "Artistas"}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
          {searchMode === "artists" ? (
            <FlatList
              contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingBottom: 120 }}
              data={artistResults}
              keyExtractor={(a, i) => a.browseId || String(i)}
              ListEmptyComponent={<Text style={styles.emptyHint}>{query ? "Sin resultados." : "Escribe algo para buscar."}</Text>}
              renderItem={({ item }) => (
                <TouchableOpacity
                  style={styles.srRow}
                  onPress={() => item.browseId && openArtistDetail(item.browseId)}
                  activeOpacity={0.6}
                >
                  <Cover uri={item.thumbnail} size={44} radius={22} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.srTitle} numberOfLines={1}>{item.name}</Text>
                    {!!item.subscribers && <Text style={styles.srArtist} numberOfLines={1}>{item.subscribers} suscriptores</Text>}
                  </View>
                  <Ionicons name="chevron-forward" size={18} color={C.textMed} />
                </TouchableOpacity>
              )}
            />
          ) : (
            <FlatList
              contentContainerStyle={{ paddingHorizontal: spacing.lg, paddingBottom: 120 }}
              data={results}
              keyExtractor={(t, i) => t.videoId || String(i)}
              ListEmptyComponent={<Text style={styles.emptyHint}>{query ? "Sin resultados." : "Escribe algo para buscar."}</Text>}
              renderItem={({ item, index }) => (
                <TouchableOpacity style={styles.srRow} onPress={() => playSearchResult(index)} activeOpacity={0.6}>
                  <Cover uri={item.thumbnail} size={44} radius={8} />
                  <View style={{ flex: 1 }}>
                    <Text style={styles.srTitle} numberOfLines={1}>{item.title}</Text>
                    <Text style={styles.srArtist} numberOfLines={1}>{item.artists}</Text>
                  </View>
                  <Ionicons name="play-circle" size={30} color={C.pri} />
                </TouchableOpacity>
              )}
            />
          )}
        </View>
      )}

      {/* Mini player: always reachable while browsing, opens the full sheet. */}
      {hasTrack && (
        <TouchableOpacity style={styles.miniBar} activeOpacity={0.85} onPress={() => setPlayerOpen(true)}>
          <Cover uri={nowPlaying.thumbnail} size={40} radius={7} />
          <View style={{ flex: 1 }}>
            <Text style={styles.miniTitle} numberOfLines={1}>{nowPlaying.title}</Text>
            <Text style={styles.miniArtist} numberOfLines={1}>{nowPlaying.artists || "—"}</Text>
          </View>
          <TouchableOpacity style={styles.miniPlay} hitSlop={10} onPress={() => runControl("toggle")}>
            <Ionicons name={nowPlaying.playing ? "pause" : "play"} size={18} color="#08101F" />
          </TouchableOpacity>
        </TouchableOpacity>
      )}

      <Modal visible={playerOpen} animationType="slide" onRequestClose={() => setPlayerOpen(false)}>
        <View style={[styles.sheet, { paddingTop: insets.top }]}>
          <View style={styles.sheetHeader}>
            <TouchableOpacity style={styles.collapseBtn} onPress={() => setPlayerOpen(false)} hitSlop={8}>
              <Ionicons name="chevron-down" size={20} color={C.textMed} />
            </TouchableOpacity>
            <Text style={styles.sheetSource}>{onPhone ? "SONANDO EN EL MÓVIL" : "SONANDO EN EL PC"}</Text>
            <View style={{ width: 34 }} />
          </View>

          {/* One continuous page — cover through the queue/lyrics content —
              so the whole sheet is swipeable. LyricsView's own inner
              ScrollView (fixed height, not flex:1) nests fine inside this
              one: RN hands the drag to whichever one is actually scrollable
              at that point, so both the page AND the lyrics list scroll. */}
          <ScrollView
            style={{ flex: 1, width: "100%" }}
            contentContainerStyle={styles.sheetTop}
            showsVerticalScrollIndicator={false}
          >
            <View>
              <Cover uri={nowPlaying.thumbnail} size={220} radius={16} />
              {(switching || loadingTrack || (onPhone && !phoneStatus.isLoaded)) && (
                <View style={styles.coverSpinner}>
                  <ActivityIndicator color={C.pri} size="large" />
                </View>
              )}
            </View>

            <View style={styles.titleRow}>
              <View style={{ flex: 1 }}>
                <Text style={styles.mtitle} numberOfLines={2}>{nowPlaying.title}</Text>
                <Text style={styles.martist} numberOfLines={1}>{nowPlaying.artists || "—"}</Text>
              </View>
            </View>

            <ProgressBar
              snapshot={snapshot}
              onSeek={(seconds) => {
                if (onPhone) phonePlayer.seekTo(seconds).catch(() => {});
                else api.musicAction("seek", { seconds }).catch(() => {});
                setSnapshot((s) => ({ ...s, position: seconds, at: Date.now() }));
              }}
            />

            <View style={styles.controls}>
              <TouchableOpacity
                style={styles.ctrlBtn}
                onPress={() => setShuffle((v) => !v)}
                hitSlop={10}
              >
                <Ionicons name="shuffle" size={20} color={shuffle ? C.pri : C.textMed} />
              </TouchableOpacity>
              <TouchableOpacity style={styles.ctrlBtn} onPress={() => runControl("previous")} disabled={switching} hitSlop={10}>
                <Ionicons name="play-skip-back" size={24} color={switching ? C.textMed : C.pri} />
              </TouchableOpacity>
              <TouchableOpacity style={[styles.ctrlBtn, styles.ctrlBtnPlay]} onPress={() => runControl("toggle")} hitSlop={10}>
                <Ionicons name={nowPlaying.playing ? "pause" : "play"} size={32} color="#08101F" />
              </TouchableOpacity>
              <TouchableOpacity style={styles.ctrlBtn} onPress={() => runControl("next")} disabled={switching} hitSlop={10}>
                <Ionicons name="play-skip-forward" size={24} color={switching ? C.textMed : C.pri} />
              </TouchableOpacity>
              <TouchableOpacity style={styles.ctrlBtn} onPress={toggleLike} disabled={likeBusy} hitSlop={10}>
                <Ionicons
                  name={liked ? "heart" : "heart-outline"}
                  size={20}
                  color={liked ? C.pri : C.textMed}
                />
              </TouchableOpacity>
            </View>

            <View style={styles.volumeRow}>
              <Ionicons name="volume-medium" size={18} color={C.textMed} />
              <VolumeSlider
                volume={nowPlaying.volume}
                onChange={(level) => {
                  if (onPhone) phonePlayer.volume = Math.max(0, Math.min(1, level / 100));
                  else api.musicAction("volume", { level }).catch(() => {});
                }}
              />
            </View>

            <View style={styles.sheetTabs}>
              {(["queue", "lyrics"] as SheetTab[]).map((t) => (
                <TouchableOpacity
                  key={t}
                  style={[styles.sheetTab, sheetTab === t && styles.sheetTabOn]}
                  onPress={() => setSheetTab(t)}
                >
                  <Text style={[styles.sheetTabText, sheetTab === t && styles.sheetTabTextOn]}>
                    {t === "queue" ? "A continuación" : "Letra"}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>

            {sheetTab === "lyrics" ? (
              <View style={styles.lyricsBox}>
                <LyricsView
                  title={nowPlaying.title}
                  artists={nowPlaying.artists}
                  position={snapshot.position}
                  playing={nowPlaying.playing}
                />
              </View>
            ) : queue.length === 0 ? (
              <Text style={styles.emptyHint}>La cola está vacía.</Text>
            ) : (
              queue.map((t, i) => {
                const current = onPhone ? i === phoneIndex : i === 0;
                return (
                  <TouchableOpacity
                    key={i}
                    style={[styles.queueRow, current && styles.queueRowCurrent, switching && { opacity: 0.5 }]}
                    disabled={current || switching}
                    activeOpacity={0.6}
                    onPress={() => jumpToQueueIndex(t.index, i)}
                  >
                    <Cover uri={t.thumbnail} size={42} radius={8} />
                    <View style={{ flex: 1 }}>
                      <Text style={[styles.queueTitle, current && { color: C.pri }]} numberOfLines={1}>{t.title}</Text>
                      <Text style={styles.queueArtist} numberOfLines={1}>{t.artists}</Text>
                    </View>
                    {current ? (
                      <Ionicons name="volume-high" size={16} color={C.pri} />
                    ) : (
                      <Text style={styles.queueIndex}>{i + 1}</Text>
                    )}
                  </TouchableOpacity>
                );
              })
            )}
          </ScrollView>
        </View>
      </Modal>

      <Modal
        visible={loadingArtist || !!openArtist}
        animationType="slide"
        onRequestClose={() => setOpenArtist(null)}
      >
        <View style={[styles.sheet, { paddingTop: insets.top }]}>
          <View style={styles.sheetHeader}>
            <TouchableOpacity onPress={() => setOpenArtist(null)} hitSlop={12}>
              <Ionicons name="chevron-down" size={24} color={C.textMed} />
            </TouchableOpacity>
            <Text style={styles.sheetSource}>ARTISTA</Text>
            <View style={{ width: 24 }} />
          </View>

          {loadingArtist || !openArtist ? (
            <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
              <ActivityIndicator color={C.pri} size="large" />
            </View>
          ) : (
            <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 60, alignItems: "center" }} showsVerticalScrollIndicator={false}>
              <Cover uri={openArtist.thumbnail} size={140} radius={70} />
              <Text style={[styles.mtitle, { marginTop: 14, textAlign: "center" }]} numberOfLines={2}>{openArtist.name}</Text>
              {!!(openArtist.subscribers || openArtist.monthlyListeners) && (
                <Text style={styles.martist}>
                  {openArtist.monthlyListeners ? `${openArtist.monthlyListeners} oyentes mensuales` : `${openArtist.subscribers} suscriptores`}
                </Text>
              )}

              {!!openArtist.top_songs?.length && (
                <TouchableOpacity style={[styles.playAllBtn, { marginTop: 18 }]} onPress={playArtistTopSongs} activeOpacity={0.8}>
                  <Ionicons name="play" size={16} color="#08101F" />
                  <Text style={styles.playAllText}>Reproducir top canciones</Text>
                </TouchableOpacity>
              )}

              {!!openArtist.description && (
                <Text style={styles.artistBio}>{openArtist.description}</Text>
              )}

              {!!openArtist.top_songs?.length && (
                <View style={{ width: "100%", maxWidth: 360, marginTop: 24 }}>
                  <Text style={styles.sectionLabel}>CANCIONES POPULARES</Text>
                  {openArtist.top_songs.slice(0, 10).map((t, i) => (
                    <TouchableOpacity key={t.videoId || i} style={styles.srRow} onPress={() => playArtistTrack(i)} activeOpacity={0.6}>
                      <Cover uri={t.thumbnail} size={40} radius={7} />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.srTitle} numberOfLines={1}>{t.title}</Text>
                      </View>
                      <Ionicons name="play-circle" size={24} color={C.pri} />
                    </TouchableOpacity>
                  ))}
                </View>
              )}

              {!!openArtist.albums?.length && (
                <View style={{ width: "100%", maxWidth: 360, marginTop: 20 }}>
                  <Text style={styles.sectionLabel}>ÁLBUMES</Text>
                  <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: spacing.md }}>
                    {openArtist.albums.map((al) => (
                      <View key={al.browseId} style={{ width: 120 }}>
                        <Cover uri={al.thumbnail} size={120} radius={10} />
                        <Text style={styles.plTitle} numberOfLines={2}>{al.title}</Text>
                        {!!al.year && <Text style={styles.plSub}>{al.year}</Text>}
                      </View>
                    ))}
                  </ScrollView>
                </View>
              )}

              {!!openArtist.related?.length && (
                <View style={{ width: "100%", maxWidth: 360, marginTop: 20 }}>
                  <Text style={styles.sectionLabel}>ARTISTAS RELACIONADOS</Text>
                  {openArtist.related.map((a) => (
                    <TouchableOpacity
                      key={a.browseId}
                      style={styles.srRow}
                      onPress={() => a.browseId && openArtistDetail(a.browseId)}
                      activeOpacity={0.6}
                    >
                      <Cover uri={a.thumbnail} size={40} radius={20} />
                      <View style={{ flex: 1 }}>
                        <Text style={styles.srTitle} numberOfLines={1}>{a.name}</Text>
                      </View>
                      <Ionicons name="chevron-forward" size={16} color={C.textMed} />
                    </TouchableOpacity>
                  ))}
                </View>
              )}
            </ScrollView>
          )}
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: C.bg },
  topBar: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingTop: spacing.sm },
  topTitle: { color: C.text, fontSize: 20, fontWeight: "800" },
  outputRow: { flexDirection: "row", gap: 4, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: 16, padding: 3 },
  outputBtn: { flexDirection: "row", alignItems: "center", gap: 4, paddingVertical: 5, paddingHorizontal: 10, borderRadius: 13 },
  outputBtnOn: { backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.4)" },
  outputText: { color: C.textMed, fontSize: 11, fontWeight: "700" },
  outputTextOn: { color: C.pri },
  subnav: { flexDirection: "row", gap: spacing.xs, padding: spacing.md },
  subtab: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: radius.sm, paddingVertical: 9 },
  subtabActive: { backgroundColor: C.priGho, borderColor: "rgba(182,196,255,0.4)" },
  subtabText: { color: C.textMed, fontSize: 12.5, fontWeight: "700" },
  subtabTextActive: { color: C.pri },
  emptyHint: { color: C.textMed, fontSize: 13, textAlign: "center", padding: 20, lineHeight: 20 },

  miniBar: {
    position: "absolute", left: 8, right: 8, bottom: 8,
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: C.panel2, borderWidth: 1, borderColor: C.borderA,
    borderRadius: radius.md, padding: 6, paddingRight: 8,
    shadowColor: "#000", shadowOpacity: 0.35, shadowRadius: 10, shadowOffset: { width: 0, height: 4 }, elevation: 8,
  },
  miniTitle: { color: C.text, fontSize: 12.5, fontWeight: "700" },
  miniArtist: { color: C.textMed, fontSize: 10.5, marginTop: 1 },
  miniPlay: { width: 32, height: 32, borderRadius: 16, backgroundColor: C.pri, alignItems: "center", justifyContent: "center" },

  sheet: { flex: 1, backgroundColor: C.bg },
  sheetHeader: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: spacing.lg, paddingTop: spacing.sm, paddingBottom: spacing.sm },
  collapseBtn: {
    width: 34, height: 34, borderRadius: 17, alignItems: "center", justifyContent: "center",
    backgroundColor: "rgba(255,255,255,0.06)", borderWidth: 1, borderColor: "rgba(255,255,255,0.09)",
  },
  sheetSource: { color: C.textMed, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  sheetTop: { alignItems: "center", paddingHorizontal: spacing.lg, paddingBottom: 40 },
  coverSpinner: { position: "absolute", top: 0, left: 0, right: 0, bottom: 0, alignItems: "center", justifyContent: "center", backgroundColor: "rgba(10,12,22,0.45)", borderRadius: 16 },
  titleRow: { flexDirection: "row", alignItems: "center", gap: 12, width: "100%", maxWidth: 340, marginTop: 16 },
  mtitle: { color: C.text, fontSize: 20, fontWeight: "800" },
  martist: { color: C.textMed, fontSize: 14, marginTop: 3 },
  controls: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 18 },
  ctrlBtn: { width: 50, height: 50, borderRadius: 25, alignItems: "center", justifyContent: "center" },
  ctrlBtnPlay: { width: 70, height: 70, borderRadius: 35, backgroundColor: C.pri },
  volumeRow: { flexDirection: "row", alignItems: "center", gap: 10, width: "100%", maxWidth: 340, marginTop: 20 },

  sheetTabs: { flexDirection: "row", gap: 8, marginTop: 20, marginBottom: 12, alignSelf: "flex-start" },
  sheetTab: { paddingVertical: 7, paddingHorizontal: 14, borderRadius: 16, backgroundColor: C.panel },
  sheetTabOn: { backgroundColor: C.priGho, borderWidth: 1, borderColor: "rgba(182,196,255,0.4)" },
  sheetTabText: { color: C.textMed, fontSize: 12, fontWeight: "700" },
  sheetTabTextOn: { color: C.pri },

  // Bounded, not flex:1 — this sits inside the sheet's own ScrollView (so the
  // whole player page, cover through lyrics, is one swipeable surface), and
  // flex:1 collapses to 0 height inside a ScrollView's content. Sized off the
  // real screen height rather than a flat number, so it's actually a big
  // panel instead of a token-sized stub.
  lyricsBox: { width: "100%", maxWidth: 360, height: Dimensions.get("window").height },
  queueRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: 12, padding: 8, paddingRight: 12, width: "100%", maxWidth: 360, marginBottom: 6 },
  queueRowCurrent: { borderColor: "rgba(182,196,255,0.4)", backgroundColor: C.priGho },
  queueIndex: { color: C.textMed, width: 18, textAlign: "center", fontSize: 11 },
  queueTitle: { color: C.text, fontSize: 13.5, fontWeight: "600" },
  queueArtist: { color: C.textMed, fontSize: 11.5 },

  plCard: { width: "48%", backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: 14, padding: 12, alignItems: "center" },
  plTitle: { color: C.text, fontSize: 13, fontWeight: "700", marginTop: 10, textAlign: "center" },
  plSub: { color: C.textMed, fontSize: 11, marginTop: 4 },
  plDetailHero: { alignItems: "center", padding: spacing.lg, paddingBottom: spacing.md },
  backBtn: { position: "absolute", top: spacing.lg, left: spacing.lg, width: 34, height: 34, borderRadius: 17, backgroundColor: "rgba(255,255,255,0.06)", borderWidth: 1, borderColor: "rgba(255,255,255,0.09)", alignItems: "center", justifyContent: "center", zIndex: 1 },
  plDetailTitle: { color: C.text, fontSize: 18, fontWeight: "800", marginTop: 14, textAlign: "center" },
  plDetailSub: { color: C.textMed, fontSize: 12, marginTop: 3 },
  heroActions: { flexDirection: "row", alignItems: "center", gap: 10, marginTop: 14 },
  shuffleChip: { width: 40, height: 40, borderRadius: 20, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, alignItems: "center", justifyContent: "center" },
  shuffleChipOn: { backgroundColor: C.priGho, borderColor: "rgba(182,196,255,0.45)" },
  playAllBtn: { flexDirection: "row", alignItems: "center", gap: 6, backgroundColor: C.pri, borderRadius: 20, paddingVertical: 10, paddingHorizontal: 22 },
  playAllText: { color: "#08101F", fontWeight: "800", fontSize: 13 },
  trackDuration: { color: C.textMed, fontSize: 11, marginRight: 2 },

  searchBox: { padding: spacing.lg, paddingTop: 0, paddingBottom: spacing.sm },
  searchInput: { backgroundColor: C.panel, color: C.text, borderWidth: 1, borderColor: C.border, borderRadius: 14, paddingHorizontal: 15, paddingVertical: 11, fontSize: 14.5 },
  searchModeRow: { flexDirection: "row", gap: spacing.xs, marginTop: spacing.sm },
  searchModeChip: { paddingVertical: 6, paddingHorizontal: 14, borderRadius: 14, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA },
  searchModeChipOn: { backgroundColor: C.priGho, borderColor: "rgba(182,196,255,0.4)" },
  searchModeText: { color: C.textMed, fontSize: 12, fontWeight: "700" },
  searchModeTextOn: { color: C.pri },
  sectionLabel: { color: C.textMed, fontSize: 11, fontWeight: "800", letterSpacing: 0.6, marginBottom: 8 },
  artistBio: { color: C.textDim, fontSize: 12.5, lineHeight: 18, marginTop: 16, width: "100%", maxWidth: 360 },
  srRow: { flexDirection: "row", alignItems: "center", gap: 12, backgroundColor: C.panel, borderWidth: 1, borderColor: C.borderA, borderRadius: 12, padding: 8, paddingRight: 12, marginBottom: 7 },
  srTitle: { color: C.text, fontSize: 13.5, fontWeight: "700" },
  srArtist: { color: C.textMed, fontSize: 11.5 },
});
