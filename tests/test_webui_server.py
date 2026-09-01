import io
import json
import os
import tempfile
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock, patch


def _done_future(value):
    future: Future = Future()
    future.set_result(value)
    return future


class WebUIHostContractTests(unittest.TestCase):
    """Cheap regression guard: main.py drives `ui` purely by duck-typing,
    so a method WebUIHost forgets only surfaces as an AttributeError at
    runtime otherwise."""

    CONTRACT_METHODS = [
        "write_log", "show_toast", "show_whatsapp_notification",
        "set_state", "set_mic_available", "set_download_state",
        "set_task_state", "open_whatsapp_chat", "start_camera_stream",
        "stop_camera_stream", "start_screen_translate", "stop_screen_translate",
        "is_screen_translate_active", "stop_speaking", "start_speaking",
        "set_audio_level", "set_audio_bands", "set_fft_bins",
        "set_music_playing", "set_music_volume", "update_playback",
        "set_playback_like_state", "request_download_cancel", "play_movie",
        "wait_for_api_key", "on_quit", "set_whatsapp_unread_count",
        "get_state", "toggle_fullscreen", "fire_interrupt", "set_always_on_top",
    ]
    CONTRACT_PROPERTIES = ["muted", "current_file", "on_interrupt"]
    CONTRACT_ATTRIBUTES = [
        "playback_controller", "whatsapp_manager", "on_text_command",
        "on_playback_command", "on_download_cancel", "interrupt_callback", "root",
    ]

    def setUp(self):
        # WebUIHost now reads mic_default_state/mic_last_state at construction
        # (mirrors ui/__init__.py's _load_mic_state_from_config) — isolate
        # from this machine's real settings file so these contract checks
        # don't depend on whatever mic state was last saved for real.
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        file_patcher = patch("actions.app_settings._FILE", Path(self.temp_dir.name) / "app_settings.json")
        file_patcher.start()
        self.addCleanup(file_patcher.stop)
        backup_patcher = patch("actions.app_settings._BACKUP_FILE", None)
        backup_patcher.start()
        self.addCleanup(backup_patcher.stop)
        from actions import app_settings
        app_settings._cache = None

        from actions.webui_host import WebUIHost
        self.host = WebUIHost(port=8877)

    def test_all_contract_methods_present_and_callable(self):
        for name in self.CONTRACT_METHODS:
            self.assertTrue(hasattr(self.host, name), f"missing method: {name}")
            self.assertTrue(callable(getattr(self.host, name)), f"not callable: {name}")

    def test_contract_properties_present(self):
        for name in self.CONTRACT_PROPERTIES:
            self.assertTrue(hasattr(self.host, name), f"missing property: {name}")

    def test_contract_attributes_present(self):
        for name in self.CONTRACT_ATTRIBUTES:
            self.assertTrue(hasattr(self.host, name), f"missing attribute: {name}")

    def test_root_has_mainloop(self):
        self.assertTrue(hasattr(self.host.root, "mainloop"))
        self.assertTrue(callable(self.host.root.mainloop))

    def test_on_quit_registers_callback_run_on_close(self):
        calls = []
        self.host.on_quit(lambda: calls.append(1))
        for cb in self.host.root._quit_callbacks:
            cb()
        self.assertEqual(calls, [1])

    def test_muted_setter_getter(self):
        self.assertFalse(self.host.muted)
        self.host.muted = True
        self.assertTrue(self.host.muted)

    def test_on_interrupt_assignment_routes_to_interrupt_callback(self):
        # main.py assigns via `ui.on_interrupt = self._on_interrupt`
        # (mirrors JarvisUI's own property) — without a real property this
        # silently created a same-named shadow attribute instead of
        # updating interrupt_callback, so Esc-to-interrupt called nothing.
        calls = []
        self.host.on_interrupt = lambda: calls.append(1)
        self.assertIs(self.host.interrupt_callback, self.host.on_interrupt)
        self.host.fire_interrupt()
        self.assertEqual(calls, [1])

    def test_fire_interrupt_noop_without_callback(self):
        self.host.fire_interrupt()  # must not raise

    def test_update_playback_reflected_in_state(self):
        self.host.update_playback("Song", "Artist", 1.0, 100.0, True, video_id="abc")
        state = self.host.get_state()
        self.assertEqual(state["playback"]["title"], "Song")
        self.assertEqual(state["playback"]["videoId"], "abc")

    def test_update_playback_preserves_volume(self):
        # main.py's poller replaces the whole playback dict every tick; the
        # volume is owned by set_music_volume and must survive that.
        self.host.set_music_volume(35)
        self.host.update_playback("T", "A", 1.0, 100.0, True, video_id="v")
        self.assertEqual(self.host.get_state()["playback"]["volume"], 35)

    def test_update_playback_only_publishes_on_change(self):
        published = []
        self.host._publish = lambda kind, data: published.append(kind)
        args = ("T", "A", 1.0, 100.0, True)
        self.host.update_playback(*args)
        self.host.update_playback(*args)
        self.host.update_playback(*args)
        self.assertEqual(published.count("playback"), 1)
        self.host.update_playback("T", "A", 2.0, 100.0, True)
        self.assertEqual(published.count("playback"), 2)

    def test_set_music_playing_does_not_clobber_playback_dict(self):
        self.host.update_playback("T", "A", 1.0, 100.0, True, video_id="v")
        self.host.set_music_playing(True)
        self.assertEqual(self.host.get_state()["playback"]["title"], "T")
        self.assertTrue(self.host.get_state()["music_playing"])

    def test_set_whatsapp_unread_count_reflected_in_state(self):
        self.host.set_whatsapp_unread_count(3)
        self.assertEqual(self.host.get_state()["whatsapp_unread"], 3)

    def test_wait_for_api_key_returns_immediately_without_key(self):
        with patch.object(type(self.host), "_has_api_key", lambda self: False):
            # Must not block — this used to be a busy-wait in the legacy UI.
            self.host.wait_for_api_key()


class WebUIServerRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        real_file = Path(self.temp_dir.name) / "app_settings.json"

        env_patcher = patch.dict(os.environ, {"JARVIS_REAL_SESSION": "1"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        file_patcher = patch("actions.app_settings._FILE", real_file)
        file_patcher.start()
        self.addCleanup(file_patcher.stop)

        backup_patcher = patch("actions.app_settings._BACKUP_FILE", None)
        backup_patcher.start()
        self.addCleanup(backup_patcher.stop)

        from actions import app_settings
        app_settings._cache = None

        from actions.webui_host import WebUIHost
        self.host = WebUIHost(port=8877)

        from actions import webui_server
        self.webui_server = webui_server
        webui_server._log_buffer.clear()
        webui_server._next_log_id = 0
        webui_server._jarvis_ref = None
        self.app = webui_server._make_app(self.host)
        self.client = self.app.test_client()

    def test_api_state(self):
        resp = self.client.get("/api/state")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("playback", resp.get_json())

    def test_api_events_is_sse(self):
        resp = self.client.get("/api/events")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.content_type)

    def test_music_action_without_controller_returns_503(self):
        resp = self.client.post("/api/music/play", json={"query": "x"})
        self.assertEqual(resp.status_code, 503)

    def test_music_action_unknown_action_returns_400(self):
        resp = self.client.post("/api/music/not_a_real_action")
        self.assertEqual(resp.status_code, 400)

    def test_music_action_submits_to_controller(self):
        ctrl = MagicMock()
        ctrl.submit.return_value = _done_future("done")
        self.host.playback_controller = ctrl
        resp = self.client.post("/api/music/play", json={"query": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["result"], "done")
        ctrl.submit.assert_called_once_with("play", {"query": "x"})

    def test_music_search_returns_all_three_result_kinds(self):
        # ui/panels/music.py's search() fetches songs+playlists+artists in
        # one go and the filter tabs just pick which to show.
        with patch("actions.ytmusic.search_songs", return_value=[{"videoId": "a"}]), \
             patch("actions.ytmusic.search_playlists", return_value=[{"playlistId": "p"}]), \
             patch("actions.ytmusic.search_artists", return_value=[{"browseId": "b"}]):
            resp = self.client.get("/api/music/search?q=queen")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            sorted(resp.get_json().keys()), ["artists", "playlists", "songs"]
        )

    def test_play_tracks_action_is_accepted(self):
        # Clicking a row queues the whole listing (music.py:1977 _play_song),
        # so play_tracks must be reachable with its tracks/start_index shape.
        ctrl = MagicMock()
        ctrl.submit.return_value = _done_future("ok")
        self.host.playback_controller = ctrl
        payload = {
            "tracks": [{"videoId": "a", "title": "A", "artists": "X"}],
            "start_index": 0,
            "shuffle": False,
        }
        resp = self.client.post("/api/music/play_tracks", json=payload)
        self.assertEqual(resp.status_code, 200)
        ctrl.submit.assert_called_once_with("play_tracks", payload)

    def test_whatsapp_chats_unavailable_returns_503(self):
        with patch("actions.whatsapp.list_recent_chats") as mocked:
            from actions.whatsapp import WhatsAppUnavailable
            mocked.side_effect = WhatsAppUnavailable("bridge down")
            resp = self.client.get("/api/whatsapp/chats")
            self.assertEqual(resp.status_code, 503)

    def test_whatsapp_chats_success(self):
        with patch("actions.whatsapp.list_recent_chats") as mocked:
            mocked.return_value = [{"id": "123", "name": "Alice"}]
            resp = self.client.get("/api/whatsapp/chats")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json(), [{"id": "123", "name": "Alice"}])

    def test_whatsapp_avatar_image_missing_chat_id(self):
        resp = self.client.get("/api/whatsapp/avatar_image")
        self.assertEqual(resp.status_code, 400)

    def test_whatsapp_avatar_image_no_avatar_returns_404(self):
        with patch("actions.whatsapp.get_profile_picture_url", return_value=None):
            resp = self.client.get("/api/whatsapp/avatar_image?chat_id=123")
            self.assertEqual(resp.status_code, 404)

    def test_whatsapp_avatar_image_proxies_bytes(self):
        fake_resp = MagicMock()
        fake_resp.content = b"\xff\xd8\xff"
        fake_resp.headers = {"content-type": "image/jpeg"}
        fake_resp.raise_for_status = lambda: None
        with patch("actions.whatsapp.get_profile_picture_url", return_value="https://pps.whatsapp.net/x.jpg"), \
             patch("requests.get", return_value=fake_resp):
            resp = self.client.get("/api/whatsapp/avatar_image?chat_id=123")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.data, b"\xff\xd8\xff")
            self.assertEqual(resp.content_type, "image/jpeg")

    def test_whatsapp_send_missing_fields(self):
        resp = self.client.post("/api/whatsapp/send", json={"chat_id": ""})
        self.assertEqual(resp.status_code, 400)

    def test_whatsapp_send_success(self):
        with patch("actions.whatsapp.send_whatsapp") as mocked:
            mocked.return_value = {"id": "m1"}
            resp = self.client.post(
                "/api/whatsapp/send", json={"chat_id": "123", "text": "hola"}
            )
            self.assertEqual(resp.status_code, 200)
            mocked.assert_called_once_with(to="123", body="hola")

    def test_start_binds_loopback_only(self):
        with patch("threading.Thread") as mocked_thread:
            url = self.webui_server.start(self.host, port=18877)
            self.assertEqual(url, "http://127.0.0.1:18877/")
            # Reset module state so other tests in this run aren't affected
            # by _started staying True from this call.
            self.webui_server._started = False
        target = mocked_thread.call_args.kwargs["target"]
        # The target is a lambda closing over app.run(...) — call it with
        # app.run mocked out to inspect the host kwarg without binding a
        # real socket.
        with patch.object(self.webui_server._app, "run") as mocked_run:
            target()
        _, run_kwargs = mocked_run.call_args
        self.assertEqual(run_kwargs["host"], "127.0.0.1")

    def test_publish_delivers_to_sse_stream(self):
        gen = self.webui_server._sse_stream()
        first = next(gen)
        self.assertIn("event: ready", first)
        self.webui_server.publish("toast", {"text": "hi"})
        second = next(gen)
        self.assertIn('"type": "toast"', second)
        self.assertIn('"text": "hi"', second)
        gen.close()

    def test_publish_log_and_toast_land_in_log_buffer(self):
        self.webui_server.publish("log", {"text": "line one"})
        self.webui_server.publish("toast", {"text": "a toast"})
        resp = self.client.get("/api/log")
        messages = [e["message"] for e in resp.get_json()]
        self.assertIn("line one", messages)
        self.assertIn("a toast", messages)

    def test_log_buffer_dedups_consecutive_duplicates(self):
        self.webui_server.publish("log", {"text": "same line"})
        self.webui_server.publish("log", {"text": "same line"})
        resp = self.client.get("/api/log")
        matches = [e for e in resp.get_json() if e["message"] == "same line"]
        self.assertEqual(len(matches), 1)

    def test_log_since_filters_older_entries(self):
        self.webui_server.publish("log", {"text": "first"})
        first_id = self.client.get("/api/log").get_json()[-1]["id"]
        self.webui_server.publish("log", {"text": "second"})
        resp = self.client.get(f"/api/log?since={first_id}")
        messages = [e["message"] for e in resp.get_json()]
        self.assertEqual(messages, ["second"])

    def test_command_without_jarvis_returns_503(self):
        resp = self.client.post("/api/command", json={"text": "hola"})
        self.assertEqual(resp.status_code, 503)

    def test_command_empty_text_returns_400(self):
        resp = self.client.post("/api/command", json={"text": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_command_with_jarvis_schedules_and_logs(self):
        fake_jarvis = MagicMock()
        fake_jarvis._loop = MagicMock()
        fake_jarvis.session = MagicMock()
        self.webui_server.set_jarvis(fake_jarvis)
        self.addCleanup(self.webui_server.set_jarvis, None)
        with patch("asyncio.run_coroutine_threadsafe") as mocked:
            resp = self.client.post("/api/command", json={"text": "enciende la luz"})
        self.assertEqual(resp.status_code, 202)
        mocked.assert_called_once()
        log_resp = self.client.get("/api/log")
        self.assertTrue(
            any("enciende la luz" in e["message"] for e in log_resp.get_json())
        )

    def test_mic_toggle_flips_muted(self):
        self.assertFalse(self.host.muted)
        resp = self.client.post("/api/mic/toggle")
        self.assertEqual(resp.get_json(), {"muted": True})
        self.assertTrue(self.host.muted)
        resp = self.client.post("/api/mic/toggle")
        self.assertEqual(resp.get_json(), {"muted": False})

    def test_mic_toggle_persists_last_state_in_remember_mode(self):
        from actions import app_settings
        app_settings.set("mic_default_state", "remember")
        resp = self.client.post("/api/mic/toggle")
        self.assertEqual(resp.get_json(), {"muted": True})
        self.assertEqual(app_settings.get("mic_last_state"), "off")
        resp = self.client.post("/api/mic/toggle")
        self.assertEqual(resp.get_json(), {"muted": False})
        self.assertEqual(app_settings.get("mic_last_state"), "on")

    def test_mic_toggle_does_not_persist_when_not_remembering(self):
        from actions import app_settings
        app_settings.set("mic_default_state", "on")
        self.client.post("/api/mic/toggle")
        self.assertIsNone(app_settings.get("mic_last_state"))

    def test_boot_muted_state_loaded_from_remember_mode(self):
        from actions import app_settings
        app_settings.set("mic_default_state", "remember")
        app_settings.set("mic_last_state", "off")
        from actions.webui_host import WebUIHost
        host = WebUIHost(port=8877)
        self.assertTrue(host.muted)

    def test_mic_unavailable_forces_mute_and_reconnect_restores_prior_state(self):
        from actions import app_settings
        app_settings.set("mic_default_state", "remember")
        self.host.muted = False
        self.host.set_mic_available(False)
        self.assertTrue(self.host.muted)
        self.host.set_mic_available(True)
        self.assertFalse(self.host.muted, "reconnect should restore the state from before the forced mute, not stay muted")

    def test_window_fullscreen_noop_without_window(self):
        # host.root._window is None outside a real pywebview session — the
        # route must not blow up, just report ok.
        resp = self.client.post("/api/window/fullscreen")
        self.assertEqual(resp.status_code, 200)

    def test_window_fullscreen_calls_toggle_on_window(self):
        fake_window = MagicMock()
        self.host.root._window = fake_window
        resp = self.client.post("/api/window/fullscreen")
        self.assertEqual(resp.status_code, 200)
        fake_window.toggle_fullscreen.assert_called_once()

    def test_window_always_on_top_noop_without_window(self):
        resp = self.client.post("/api/window/always_on_top", json={"enabled": True})
        self.assertEqual(resp.status_code, 200)

    def test_window_always_on_top_sets_window_property_and_persists(self):
        fake_window = MagicMock()
        self.host.root._window = fake_window
        resp = self.client.post("/api/window/always_on_top", json={"enabled": True})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"ok": True, "enabled": True})
        self.assertTrue(fake_window.on_top)
        from actions import app_settings
        self.assertTrue(app_settings.get("ui_always_on_top"))

    def test_interrupt_route_calls_fire_interrupt(self):
        calls = []
        self.host.on_interrupt = lambda: calls.append(1)
        resp = self.client.post("/api/interrupt")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, [1])

    def test_downloads_list_empty(self):
        resp = self.client.get("/api/downloads")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [])

    def test_files_upload_without_jarvis_returns_503(self):
        resp = self.client.post(
            "/api/files/upload",
            data={"file": (io.BytesIO(b"hello"), "test.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 503)

    def test_files_upload_missing_file_returns_400(self):
        resp = self.client.post("/api/files/upload", data={}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    def test_setup_status_reflects_missing_key(self):
        with patch.object(self.host, "_has_api_key", return_value=False):
            resp = self.client.get("/api/setup/status")
        self.assertEqual(resp.get_json(), {"ready": False})

    def test_setup_complete_missing_key_returns_400(self):
        resp = self.client.post("/api/setup/complete", json={})
        self.assertEqual(resp.status_code, 400)

    def test_setup_complete_merges_existing_api_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            api_file = Path(tmp) / "api_keys.json"
            api_file.write_text(json.dumps({"tmdb_api_key": "keep-me"}), encoding="utf-8")
            with patch("actions.webui_host.API_FILE", api_file):
                resp = self.client.post(
                    "/api/setup/complete",
                    json={"gemini_api_key": "abc123", "os_system": "linux"},
                )
                self.assertEqual(resp.status_code, 200)
                saved = json.loads(api_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["gemini_api_key"], "abc123")
        self.assertEqual(saved["os_system"], "linux")
        self.assertEqual(saved["tmdb_api_key"], "keep-me")


class SynchronousThread:
    """Stand-in for threading.Thread that runs target() immediately on
    start() — the movies/play route fires a background thread for the
    (real 1-65s) torrent metadata wait, which tests should not actually do."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class MoviesRouteTests(unittest.TestCase):
    def setUp(self):
        from actions.webui_host import WebUIHost
        self.host = WebUIHost(port=8877)
        from actions import webui_server
        self.webui_server = webui_server
        self.app = webui_server._make_app(self.host)
        self.client = self.app.test_client()

    def test_search_missing_query(self):
        resp = self.client.get("/api/movies/search")
        self.assertEqual(resp.status_code, 400)

    def test_search_success(self):
        from actions.movie_search import Movie
        with patch("actions.movie_search.search") as mocked:
            mocked.return_value = [Movie(tmdb_id=1, title="Matrix", release_year=1999)]
            resp = self.client.get("/api/movies/search?q=matrix&kind=movie")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["title"], "Matrix")

    def test_search_needs_api_key(self):
        from actions.movie_search import MovieSearchError
        with patch("actions.movie_search.search", side_effect=MovieSearchError("TMDB API key not configured.")):
            resp = self.client.get("/api/movies/search?q=matrix")
        self.assertEqual(resp.status_code, 503)
        self.assertTrue(resp.get_json()["needs_api_key"])

    def test_trending_success(self):
        from actions.movie_search import Movie
        with patch("actions.movie_search.get_trending") as mocked:
            mocked.return_value = [Movie(tmdb_id=2, title="Dune", release_year=2021)]
            resp = self.client.get("/api/movies/trending?kind=movie")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["title"], "Dune")

    def test_torrents_missing_tmdb_id(self):
        resp = self.client.get("/api/movies/torrents")
        self.assertEqual(resp.status_code, 400)

    def test_torrents_no_imdb_id_returns_404(self):
        with patch("actions.movie_search.get_imdb_id", return_value=""):
            resp = self.client.get("/api/movies/torrents?tmdb_id=1&kind=movie")
        self.assertEqual(resp.status_code, 404)

    def test_torrents_success(self):
        from actions.torrentio import Stream
        with patch("actions.movie_search.get_imdb_id", return_value="tt0133093"), \
             patch("actions.torrentio.search") as mocked_search:
            mocked_search.return_value = [Stream(title="Matrix.1999.1080p", magnet="magnet:?xt=1", seeders=50)]
            resp = self.client.get("/api/movies/torrents?tmdb_id=1&kind=movie")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["seeders"], 50)
        mocked_search.assert_called_once_with("tt0133093", kind="movie", season=0, episode=0)

    def test_play_missing_magnet(self):
        resp = self.client.post("/api/movies/play", json={})
        self.assertEqual(resp.status_code, 400)

    def test_play_publishes_stream_ready_on_success(self):
        published = []
        with patch("threading.Thread", SynchronousThread), \
             patch.object(self.webui_server, "publish", side_effect=lambda t, d: published.append((t, d))), \
             patch("actions.vlc_player.start_streaming", return_value="http://127.0.0.1:9999/stream"):
            resp = self.client.post("/api/movies/play", json={"magnet": "magnet:?xt=1"})
        self.assertEqual(resp.status_code, 202)
        self.assertIn(("stream_ready", {"url": "http://127.0.0.1:9999/stream"}), published)

    def test_play_publishes_stream_error_on_failure(self):
        from actions.vlc_player import VLCPlayerError
        published = []
        with patch("threading.Thread", SynchronousThread), \
             patch.object(self.webui_server, "publish", side_effect=lambda t, d: published.append((t, d))), \
             patch("actions.vlc_player.start_streaming", side_effect=VLCPlayerError("no seeds")):
            resp = self.client.post("/api/movies/play", json={"magnet": "magnet:?xt=1"})
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(published, [("stream_error", {"error": "no seeds"})])

    def test_stop_calls_stop_streaming(self):
        with patch("actions.vlc_player.stop_streaming") as mocked:
            resp = self.client.post("/api/movies/stop")
        self.assertEqual(resp.status_code, 200)
        mocked.assert_called_once()

    def test_status_reports_ready(self):
        with patch("actions.vlc_player.get_status", return_value="webtorrent ready"):
            resp = self.client.get("/api/movies/status")
        self.assertEqual(resp.get_json(), {"ready": True, "status": "webtorrent ready"})

    def test_anime_search_missing_query(self):
        resp = self.client.get("/api/anime/search")
        self.assertEqual(resp.status_code, 400)

    def test_anime_search_success(self):
        from actions.movie_search import Movie
        with patch("actions.movie_search.search_anime") as mocked:
            mocked.return_value = [Movie(tmdb_id=5, title="Frieren", media_type="tv")]
            resp = self.client.get("/api/anime/search?q=frieren")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["title"], "Frieren")

    def test_anime_trending_uses_airing_when_requested(self):
        from actions.movie_search import Movie
        with patch("actions.movie_search.get_airing_anime") as mocked_airing, \
             patch("actions.movie_search.get_trending_anime") as mocked_trending:
            mocked_airing.return_value = [Movie(tmdb_id=6, title="One Piece", media_type="tv")]
            resp = self.client.get("/api/anime/trending?airing=1")
        self.assertEqual(resp.status_code, 200)
        mocked_airing.assert_called_once()
        mocked_trending.assert_not_called()

    def test_anime_torrents_uses_search_anime_not_generic(self):
        from actions.torrentio import Stream
        with patch("actions.movie_search.get_imdb_id", return_value="tt1234567") as mocked_imdb, \
             patch("actions.torrentio.search_anime") as mocked_search, \
             patch("actions.torrentio.search") as mocked_generic_search:
            mocked_search.return_value = [Stream(title="Frieren S01E01", magnet="magnet:?xt=1", seeders=20)]
            resp = self.client.get("/api/anime/torrents?tmdb_id=5&season=1&episode=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["seeders"], 20)
        mocked_imdb.assert_called_once_with(5, kind="tv")
        mocked_search.assert_called_once_with("tt1234567", kind="tv", season=1, episode=1)
        mocked_generic_search.assert_not_called()

    def test_anime_torrents_no_imdb_id_returns_404(self):
        with patch("actions.movie_search.get_imdb_id", return_value=""):
            resp = self.client.get("/api/anime/torrents?tmdb_id=5")
        self.assertEqual(resp.status_code, 404)

    def test_youtube_search_missing_query(self):
        resp = self.client.get("/api/youtube/search")
        self.assertEqual(resp.status_code, 400)

    def test_youtube_search_success(self):
        with patch("actions.youtube_player.search_videos") as mocked:
            mocked.return_value = [{"id": "abc12345678", "title": "Test", "channel": "C", "duration": 120}]
            resp = self.client.get("/api/youtube/search?q=test")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["id"], "abc12345678")
        mocked.assert_called_once_with("test", limit=24)

    def test_youtube_recommendations_success(self):
        with patch("actions.youtube_player.fetch_home_recommendations") as mocked:
            mocked.return_value = [{"id": "xyz98765432", "title": "Rec", "channel": "C", "duration": 60}]
            resp = self.client.get("/api/youtube/recommendations")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()[0]["id"], "xyz98765432")


class EmulatorRouteTests(unittest.TestCase):
    """ui/panels/emulators.py's EmulatorsModePanel + LibraryModePanel, minus
    the Steam/FitGirl Juegos hub (out of scope — see webui_server.py's own
    comment on the route group). Routes are mocked at the module boundary
    (actions.emulator_session / .game_library / .rom_catalog / .emulator_runtime)
    rather than driving a real core — that end-to-end path (real ROM, real
    Qt-headless session, real Flask app) was verified live while building
    this phase; these tests are about each route's own contract."""

    def setUp(self):
        from actions.webui_host import WebUIHost
        self.host = WebUIHost(port=8877)
        from actions import webui_server
        self.webui_server = webui_server
        self.app = webui_server._make_app(self.host)
        self.client = self.app.test_client()

    # -- status / lifecycle -------------------------------------------------

    def test_status_when_idle(self):
        session = MagicMock()
        session.status.return_value = {
            "running": False, "console": "", "title": "", "paused": False,
            "fps": 0.0, "hw_render": False,
        }
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.get("/api/emulator/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertFalse(body["running"])
        self.assertIn("layout", body)

    def test_start_missing_entry_id(self):
        resp = self.client.post("/api/emulator/start", json={})
        self.assertEqual(resp.status_code, 400)

    def test_start_not_found(self):
        with patch("actions.game_library.list_entries", return_value=[]):
            resp = self.client.post("/api/emulator/start", json={"entry_id": "rom:missing"})
        self.assertEqual(resp.status_code, 404)

    def test_start_success_marks_played_and_returns_status(self):
        import types as _types
        entry = _types.SimpleNamespace(
            entry_id="rom:test", kind="rom", console_id="gba",
            path="/roms/test.gba", title="Test Game",
        )
        session = MagicMock()
        session.status.return_value = {"running": True, "console": "gba"}
        with patch("actions.game_library.list_entries", return_value=[entry]), \
             patch("actions.game_library.mark_played") as mock_played, \
             patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/start", json={"entry_id": "rom:test"})
        self.assertEqual(resp.status_code, 200)
        session.start.assert_called_once_with("gba", "/roms/test.gba", "Test Game")
        mock_played.assert_called_once_with("rom:test")

    def test_start_conflict_maps_to_409(self):
        import types as _types
        from actions.emulator_session import EmulatorSessionError
        entry = _types.SimpleNamespace(
            entry_id="rom:test", kind="rom", console_id="gba",
            path="/roms/test.gba", title="Test Game",
        )
        session = MagicMock()
        session.start.side_effect = EmulatorSessionError("ya en marcha")
        with patch("actions.game_library.list_entries", return_value=[entry]), \
             patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/start", json={"entry_id": "rom:test"})
        self.assertEqual(resp.status_code, 409)
        self.assertIn("ya en marcha", resp.get_json()["error"])

    def test_stop_calls_session_stop(self):
        session = MagicMock()
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/stop")
        self.assertEqual(resp.status_code, 200)
        session.stop.assert_called_once()

    def test_pause_forwards_the_flag(self):
        session = MagicMock()
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/pause", json={"paused": True})
        self.assertEqual(resp.status_code, 200)
        session.pause.assert_called_once_with(True)

    def test_reset_calls_session_reset(self):
        session = MagicMock()
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/reset")
        self.assertEqual(resp.status_code, 200)
        session.reset.assert_called_once()

    # -- input ----------------------------------------------------------------

    def test_input_without_a_running_session_is_a_conflict(self):
        session = MagicMock()
        session.is_running.return_value = False
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/input", json={"buttons": []})
        self.assertEqual(resp.status_code, 409)

    def test_input_forwards_buttons_axes_and_clear(self):
        session = MagicMock()
        session.is_running.return_value = True
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/input", json={
                "buttons": [{"name": "a", "pressed": True}, {"name": "bogus", "pressed": True}],
                "axes": [{"index": 0, "axis": 1, "value": 200}],
                "clear": True,
            })
        self.assertEqual(resp.status_code, 200)
        session.set_button.assert_called_once_with("a", True)
        session.set_axis.assert_called_once_with(0, 1, 200)
        session.clear_input.assert_called_once()

    # -- save states ------------------------------------------------------------

    def test_state_save_success(self):
        session = MagicMock()
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/state/save")
        self.assertEqual(resp.status_code, 200)
        session.save_state.assert_called_once()

    def test_state_save_without_a_session_is_a_conflict(self):
        from actions.emulator_session import EmulatorSessionError
        session = MagicMock()
        session.save_state.side_effect = EmulatorSessionError("No hay ninguna sesión activa")
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/state/save")
        self.assertEqual(resp.status_code, 409)

    def test_state_load_without_a_saved_state_is_a_conflict(self):
        from actions.emulator_session import EmulatorSessionError
        session = MagicMock()
        session.load_state.side_effect = EmulatorSessionError("No hay ningún estado guardado")
        with patch("actions.emulator_session.get_session", return_value=session):
            resp = self.client.post("/api/emulator/state/load")
        self.assertEqual(resp.status_code, 409)

    # -- library / consoles / pad / cores ----------------------------------------

    def test_library_filters_to_rom_entries_only(self):
        import types as _types
        rom_entry = _types.SimpleNamespace(kind="rom", to_dict=lambda: {"kind": "rom", "title": "A ROM"})
        steam_entry = _types.SimpleNamespace(kind="steam", to_dict=lambda: {"kind": "steam", "title": "A Steam game"})
        with patch("actions.game_library.list_entries", return_value=[rom_entry, steam_entry]) as mocked:
            resp = self.client.get("/api/emulator/library")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual([e["kind"] for e in body], ["rom"])
        mocked.assert_called_once_with(include_pc=False)

    def test_consoles_lists_the_catalog_with_core_availability(self):
        import types as _types
        console = _types.SimpleNamespace(
            id="gba", name="Game Boy Advance", short="GBA",
            rom_exts=(".gba",), has_catalog=True,
        )
        with patch("actions.rom_catalog.CONSOLES", {"gba": console}), \
             patch("actions.emulator_runtime.has_core", return_value=True):
            resp = self.client.get("/api/emulator/consoles")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), [{
            "id": "gba", "name": "Game Boy Advance", "short": "GBA",
            "rom_exts": [".gba"], "has_catalog": True, "core_available": True,
        }])

    def test_pad_returns_the_console_specific_layout(self):
        with patch("actions.emulator_runtime.pad_layout", return_value={"sticks": 2}) as mocked:
            resp = self.client.get("/api/emulator/pad?console_id=ps2")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"sticks": 2})
        mocked.assert_called_once_with("ps2")

    def test_cores_reports_ps2_bios_presence(self):
        with patch("actions.emulator_runtime.CORES", {"gba": object(), "ps2": object()}), \
             patch("actions.emulator_runtime.core_status",
                   side_effect=lambda cid: {"available": True, "console": cid}), \
             patch("actions.bios.has_ps2_bios", return_value=True):
            resp = self.client.get("/api/emulator/cores")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["ps2"]["bios_present"])
        self.assertNotIn("bios_present", body["gba"])

    def test_cores_install_rejects_an_unknown_console(self):
        with patch("actions.emulator_runtime.CORES", {}):
            resp = self.client.post("/api/emulator/cores/install", json={"console_id": "n64"})
        self.assertEqual(resp.status_code, 404)

    def test_cores_install_publishes_progress_and_ready(self):
        published = []
        with patch("threading.Thread", SynchronousThread), \
             patch.object(self.webui_server, "publish", side_effect=lambda t, d: published.append((t, d))), \
             patch("actions.emulator_runtime.CORES", {"gba": object()}), \
             patch("actions.emulator_runtime.install_core") as mock_install:
            resp = self.client.post("/api/emulator/cores/install", json={"console_id": "gba"})
        self.assertEqual(resp.status_code, 202)
        mock_install.assert_called_once()
        self.assertEqual(mock_install.call_args.args[0], "gba")
        self.assertIn(("emulator_core_ready", {"console_id": "gba"}), published)

    def test_cores_install_publishes_error_on_failure(self):
        published = []
        with patch("threading.Thread", SynchronousThread), \
             patch.object(self.webui_server, "publish", side_effect=lambda t, d: published.append((t, d))), \
             patch("actions.emulator_runtime.CORES", {"gba": object()}), \
             patch("actions.emulator_runtime.install_core", side_effect=RuntimeError("boom")):
            resp = self.client.post("/api/emulator/cores/install", json={"console_id": "gba"})
        self.assertEqual(resp.status_code, 202)
        self.assertIn(("emulator_core_error", {"console_id": "gba", "error": "boom"}), published)

    # -- BIOS import (PS2) -------------------------------------------------
    # Web equivalent of ui/panels/emulators.py's _import_bios(), which opens
    # a native Qt file picker — browsers only have <input type=file>.

    def test_bios_import_missing_file(self):
        resp = self.client.post("/api/emulator/bios/import", data={})
        self.assertEqual(resp.status_code, 400)

    def test_bios_import_rejects_a_bad_dump(self):
        from actions.bios import BiosError
        with patch("actions.bios.import_ps2_bios", side_effect=BiosError("no parece una BIOS")):
            resp = self.client.post(
                "/api/emulator/bios/import",
                data={"file": (io.BytesIO(b"not a bios"), "fake.bin")},
                content_type="multipart/form-data",
            )
        self.assertEqual(resp.status_code, 400)

    def test_bios_import_success_copies_a_real_dump_into_a_temp_system_dir(self):
        # Real actions.bios.import_ps2_bios, not mocked — isolated to a temp
        # system_dir so this never touches this machine's actual BIOS.
        with tempfile.TemporaryDirectory() as system_dir:
            dump = b"\x00" * (4 * 1024 * 1024)  # real PS2 BIOS dumps are always exactly 4 MiB
            with patch("actions.emulator_runtime.system_dir", return_value=Path(system_dir)):
                resp = self.client.post(
                    "/api/emulator/bios/import",
                    data={"file": (io.BytesIO(dump), "SCPH-70012.bin")},
                    content_type="multipart/form-data",
                )
            self.assertEqual(resp.status_code, 200)
            body = resp.get_json()
            self.assertTrue(body["ok"])
            copied = Path(body["path"])
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.stat().st_size, 4 * 1024 * 1024)

    # -- catalog ------------------------------------------------------------

    def test_catalog_search_success(self):
        from actions.rom_catalog import Rom
        rom = Rom(title="Metroid", console_id="gba", stem="Metroid", size_bytes=100)
        with patch("actions.rom_catalog.search", return_value=[rom]), \
             patch("actions.rom_catalog.local_path", return_value=None):
            resp = self.client.get("/api/emulator/catalog/search?q=metroid&console_id=gba")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body[0]["title"], "Metroid")
        self.assertFalse(body[0]["downloaded"])

    def test_catalog_popular_success(self):
        from actions.rom_catalog import Rom
        rom = Rom(title="Pokemon", console_id="gba", stem="Pokemon", size_bytes=100)
        with patch("actions.rom_catalog.popular", return_value=[rom]), \
             patch("actions.rom_catalog.local_path", return_value=Path("/roms/Pokemon.gba")):
            resp = self.client.get("/api/emulator/catalog/popular?console_id=gba")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()[0]["downloaded"])

    def test_catalog_download_missing_params(self):
        resp = self.client.post("/api/emulator/catalog/download", json={})
        self.assertEqual(resp.status_code, 400)

    def test_catalog_download_not_found(self):
        with patch("actions.rom_catalog.get_index", return_value=[]):
            resp = self.client.post(
                "/api/emulator/catalog/download",
                json={"console_id": "gba", "stem": "missing"},
            )
        self.assertEqual(resp.status_code, 404)

    def test_catalog_download_publishes_progress_and_ready(self):
        import types as _types
        rom = _types.SimpleNamespace(stem="Metroid", console_id="gba")
        published = []

        def fake_download(rom_arg, progress=None, cancel=None):
            if progress:
                progress(0.5, 50, 100)
            return Path("/roms/Metroid.gba")

        with patch("threading.Thread", SynchronousThread), \
             patch.object(self.webui_server, "publish", side_effect=lambda t, d: published.append((t, d))), \
             patch("actions.rom_catalog.get_index", return_value=[rom]), \
             patch("actions.rom_catalog.download", side_effect=fake_download), \
             patch("actions.game_library.add_rom") as mock_add:
            resp = self.client.post(
                "/api/emulator/catalog/download",
                json={"console_id": "gba", "stem": "Metroid"},
            )
        self.assertEqual(resp.status_code, 202)
        types_seen = [t for t, _ in published]
        self.assertIn("emulator_download_progress", types_seen)
        self.assertIn("emulator_download_ready", types_seen)
        mock_add.assert_called_once_with(rom, Path("/roms/Metroid.gba"))

    def test_catalog_download_publishes_error_on_failure(self):
        import types as _types
        rom = _types.SimpleNamespace(stem="Metroid", console_id="gba")
        published = []
        with patch("threading.Thread", SynchronousThread), \
             patch.object(self.webui_server, "publish", side_effect=lambda t, d: published.append((t, d))), \
             patch("actions.rom_catalog.get_index", return_value=[rom]), \
             patch("actions.rom_catalog.download", side_effect=RuntimeError("sin espacio")):
            resp = self.client.post(
                "/api/emulator/catalog/download",
                json={"console_id": "gba", "stem": "Metroid"},
            )
        self.assertEqual(resp.status_code, 202)
        self.assertIn(("emulator_download_error", {"stem": "Metroid", "error": "sin espacio"}), published)


if __name__ == "__main__":
    unittest.main()
