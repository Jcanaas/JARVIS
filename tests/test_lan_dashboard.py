import os
import tempfile
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import MagicMock, patch


def _done_future(value):
    """A controller future that has already produced its result."""
    future: Future = Future()
    future.set_result(value)
    return future


class LanDashboardTests(unittest.TestCase):
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

        from actions import lan_dashboard
        lan_dashboard._log_buffer.clear()
        lan_dashboard._next_id = 0
        lan_dashboard._ui_ref = None
        lan_dashboard._jarvis_ref = None

    def test_get_lan_ip_returns_nonempty_string(self):
        from actions import lan_dashboard
        ip = lan_dashboard.get_lan_ip()
        self.assertIsInstance(ip, str)
        self.assertTrue(ip)

    def test_get_public_ip_returns_first_provider_answer(self):
        from actions import lan_dashboard
        resp = MagicMock()
        resp.text = "203.0.113.7\n"
        resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=resp) as mock_get:
            ip = lan_dashboard.get_public_ip()
        self.assertEqual(ip, "203.0.113.7")
        mock_get.assert_called_once()

    def test_get_public_ip_falls_through_providers_on_failure(self):
        from actions import lan_dashboard
        good = MagicMock()
        good.text = "198.51.100.9"
        good.raise_for_status = MagicMock()
        with patch("requests.get", side_effect=[Exception("timeout"), good]):
            ip = lan_dashboard.get_public_ip()
        self.assertEqual(ip, "198.51.100.9")

    def test_get_public_ip_returns_none_when_all_providers_fail(self):
        from actions import lan_dashboard
        with patch("requests.get", side_effect=Exception("offline")):
            ip = lan_dashboard.get_public_ip()
        self.assertIsNone(ip)

    def test_token_persists_across_calls(self):
        from actions import lan_dashboard
        t1 = lan_dashboard._get_or_create_token()
        t2 = lan_dashboard._get_or_create_token()
        self.assertEqual(t1, t2)
        self.assertTrue(len(t1) > 10)

    def test_dashboard_rejects_missing_or_wrong_token(self):
        from actions import lan_dashboard
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.get("/")
        self.assertEqual(r.status_code, 401)

        r = client.get("/api/log")
        self.assertEqual(r.status_code, 403)

        r = client.get("/api/log?token=wrong")
        self.assertEqual(r.status_code, 403)

    def test_dashboard_accepts_valid_token(self):
        from actions import lan_dashboard
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.get(f"/?token={token}")
        self.assertEqual(r.status_code, 200)

        r = client.get(f"/api/status?token={token}")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["running"])
        self.assertIn("music", body)
        self.assertIn("mode", body)
        self.assertIn("muted", body)

    def test_event_bus_log_appears_in_buffer(self):
        from actions import lan_dashboard, event_bus

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        lan_dashboard._on_log_event({"source": "test", "message": "hello"})

        r = client.get(f"/api/log?since=0&token={token}")
        self.assertEqual(r.status_code, 200)
        items = r.get_json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["message"], "hello")
        self.assertEqual(items[0]["source"], "test")

        # since= excludes already-seen items
        r2 = client.get(f"/api/log?since={items[0]['id']}&token={token}")
        self.assertEqual(r2.get_json(), [])

    def test_status_reflects_ui_ref(self):
        from actions import lan_dashboard

        win = types.SimpleNamespace(
            _play_title="Song", _play_artists="Artist",
            _play_playing=True, _play_state="playing",
            _active_mode="Music", _muted=False,
        )
        fake_ui = types.SimpleNamespace(_win=win)
        lan_dashboard.set_ui(fake_ui)
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.get(f"/api/status?token={token}")
        body = r.get_json()
        self.assertEqual(body["music"]["title"], "Song")
        self.assertTrue(body["music"]["playing"])
        self.assertEqual(body["mode"], "Music")
        self.assertFalse(body["muted"])

    def test_status_prefers_the_live_player_over_the_window_mirror(self):
        """The window mirror is refreshed by a 1 s poller whose result lands on
        the Qt GUI thread, so right after the phone presses next it still names
        the previous track — and the phone's own follow-up poll showed the old
        song while the desktop already showed the new one."""
        from actions import lan_dashboard

        win = types.SimpleNamespace(
            _play_title="Stale", _play_artists="Old", _play_playing=False,
            _play_state="paused", _play_video_id="old", _play_position=99.0,
            _play_duration=120.0, _music_volume_level=30,
        )
        lan_dashboard.set_ui(types.SimpleNamespace(_win=win))
        self.addCleanup(lan_dashboard.set_ui, None)

        live = {
            "title": "Fresh", "artists": "New", "videoId": "new",
            "playing": True, "state": "playing", "position": 3.0,
            "duration": 200.0, "volume": 70, "thumbnail": "",
        }
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        from actions import ytmusic_headless
        with patch.dict(ytmusic_headless._last_meta, {"videoId": "new"}, clear=False):
            with patch("actions.ytmusic_headless.current", return_value=live):
                music = client.get(f"/api/status?token={token}").get_json()["music"]

        self.assertEqual(music["title"], "Fresh")
        self.assertEqual(music["videoId"], "new")
        self.assertTrue(music["playing"])
        self.assertEqual(music["state"], "playing")
        self.assertEqual(music["position"], 3.0)
        self.assertEqual(music["volume"], 70)

    def test_status_falls_back_to_the_window_when_no_headless_track(self):
        from actions import lan_dashboard

        win = types.SimpleNamespace(
            _play_title="Song", _play_artists="Artist", _play_playing=True,
            _play_state="playing",
        )
        lan_dashboard.set_ui(types.SimpleNamespace(_win=win))
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        from actions import ytmusic_headless
        with patch.dict(
            ytmusic_headless._last_meta, {"title": "", "videoId": ""}, clear=False
        ):
            music = client.get(f"/api/status?token={token}").get_json()["music"]

        self.assertEqual(music["title"], "Song")
        self.assertTrue(music["playing"])

    def test_command_rejects_when_not_ready(self):
        from actions import lan_dashboard

        lan_dashboard.set_jarvis(None)
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/command?token={token}", json={"text": "hola"})
        self.assertEqual(r.status_code, 503)

    def test_command_dispatches_to_jarvis_session(self):
        from actions import lan_dashboard

        fake_jarvis = MagicMock()
        fake_jarvis._loop = MagicMock()
        fake_jarvis.session = MagicMock()
        lan_dashboard.set_jarvis(fake_jarvis)
        self.addCleanup(lan_dashboard.set_jarvis, None)

        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            token = lan_dashboard._get_or_create_token()
            app = lan_dashboard._make_app(token)
            client = app.test_client()

            r = client.post(f"/api/command?token={token}", json={"text": "hola jarvis"})
            self.assertEqual(r.status_code, 202)
            mock_run.assert_called_once()
            self.assertEqual(mock_run.call_args[0][1], fake_jarvis._loop)

        items = lan_dashboard._log_buffer
        self.assertTrue(any("hola jarvis" in e["message"] for e in items))

    def test_command_rejects_empty_text(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/command?token={token}", json={"text": "  "})
        self.assertEqual(r.status_code, 400)

    def test_music_action_whitelist(self):
        from actions import lan_dashboard

        fake_ctrl = MagicMock()
        fake_ctrl.submit.return_value = _done_future("ok")
        fake_ui = types.SimpleNamespace(playback_controller=fake_ctrl)
        lan_dashboard.set_ui(fake_ui)
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/music/pause?token={token}")
        self.assertEqual(r.status_code, 200)
        fake_ctrl.submit.assert_called_once_with("pause", {})

        r = client.post(f"/api/music/not_a_real_action?token={token}")
        self.assertEqual(r.status_code, 400)

    def test_slow_action_is_accepted_instead_of_failing(self):
        """A cold track takes 2-3 s to resolve. The old five-second timeout
        turned anything slower into an HTTP error the phone swallowed silently,
        so the tap looked ignored and people tapped again — queueing a second
        play behind the first."""
        from actions import lan_dashboard
        from concurrent.futures import Future

        fake_ctrl = MagicMock()
        fake_ctrl.submit.return_value = Future()      # never completes
        lan_dashboard.set_ui(types.SimpleNamespace(playback_controller=fake_ctrl))
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        client = lan_dashboard._make_app(token).test_client()

        with patch.object(lan_dashboard, "_MUSIC_ACTION_WAIT_SECONDS", 0.05):
            r = client.post(f"/api/music/next?token={token}")

        self.assertEqual(r.status_code, 202)
        self.assertTrue(r.get_json()["ok"])
        self.assertTrue(r.get_json()["pending"])
        fake_ctrl.submit.assert_called_once_with("next", {})

    def test_prefetch_warms_streams_without_using_the_playback_worker(self):
        """Warm-ups must never queue behind (or in front of) transport commands
        on the single serialized worker."""
        from actions import lan_dashboard

        fake_ctrl = MagicMock()
        lan_dashboard.set_ui(types.SimpleNamespace(playback_controller=fake_ctrl))
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        client = lan_dashboard._make_app(token).test_client()

        with patch(
            "actions.ytmusic_headless.prefetch_tracks", return_value={"scheduled": 1}
        ) as prefetch:
            r = client.post(
                f"/api/music/prefetch?token={token}",
                json={"video_ids": ["a", "b", "c", "d", "e"]},
            )

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["scheduled"], lan_dashboard._MAX_PHONE_PREFETCH)
        self.assertEqual(prefetch.call_count, lan_dashboard._MAX_PHONE_PREFETCH)
        fake_ctrl.submit.assert_not_called()
        fake_ctrl.execute.assert_not_called()

    def test_prefetch_accepts_track_objects_and_ignores_empty_ids(self):
        from actions import lan_dashboard

        lan_dashboard.set_ui(types.SimpleNamespace(playback_controller=MagicMock()))
        self.addCleanup(lan_dashboard.set_ui, None)
        token = lan_dashboard._get_or_create_token()
        client = lan_dashboard._make_app(token).test_client()

        with patch(
            "actions.ytmusic_headless.prefetch_tracks", return_value={"scheduled": 1}
        ) as prefetch:
            r = client.post(
                f"/api/music/prefetch?token={token}",
                json={"tracks": [{"videoId": "abc"}, {"videoId": ""}, {"title": "x"}]},
            )

        self.assertEqual(r.get_json()["scheduled"], 1)
        prefetch.assert_called_once_with([{"videoId": "abc"}], 0, 1)

    def test_prefetch_requires_a_token(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        client = lan_dashboard._make_app(token).test_client()
        r = client.post("/api/music/prefetch?token=wrong", json={"video_ids": ["a"]})
        self.assertEqual(r.status_code, 403)

    def test_music_action_not_ready(self):
        from actions import lan_dashboard

        lan_dashboard.set_ui(None)
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/music/play?token={token}")
        self.assertEqual(r.status_code, 503)

    def test_music_playlists(self):
        from actions import lan_dashboard, ytmusic

        stub = [{"playlistId": "LM", "title": "Canciones que te gustan", "author": "", "itemCount": 10}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(ytmusic, "list_playlists", return_value=stub):
            r = client.get(f"/api/music/playlists?token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), stub)

    def test_music_playlists_fails_soft(self):
        from actions import lan_dashboard, ytmusic

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(ytmusic, "list_playlists", side_effect=RuntimeError("boom")):
            r = client.get(f"/api/music/playlists?token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), [])

    def test_music_playlist_tracks(self):
        from actions import lan_dashboard, ytmusic

        stub = [{"videoId": "abc", "title": "Song", "artists": "Artist", "duration": "3:21"}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(ytmusic, "list_playlist_tracks", return_value=stub) as mock_tracks:
            r = client.get(f"/api/music/playlist_tracks?playlist_id=LM&token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), stub)
            mock_tracks.assert_called_once_with("LM", limit=200)

        r = client.get(f"/api/music/playlist_tracks?token={token}")
        self.assertEqual(r.status_code, 400)

    def test_music_search(self):
        from actions import lan_dashboard, ytmusic

        stub = [{"videoId": "abc", "title": "Song", "artists": "Artist"}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(ytmusic, "search_songs", return_value=stub) as mock_search:
            r = client.get(f"/api/music/search?q=hello&token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), stub)
            mock_search.assert_called_once_with("hello", limit=20)

        r = client.get(f"/api/music/search?token={token}")
        self.assertEqual(r.status_code, 400)

    def test_music_queue(self):
        from actions import lan_dashboard, ytmusic_headless

        stub = [{"title": "Next song", "artists": "Someone", "videoId": "xyz"}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(ytmusic_headless, "queue_snapshot", return_value=stub):
            r = client.get(f"/api/music/queue?token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), stub)

    def test_music_play_playlist_action_whitelisted(self):
        from actions import lan_dashboard

        fake_ctrl = MagicMock()
        fake_ctrl.submit.return_value = _done_future("ok")
        fake_ui = types.SimpleNamespace(playback_controller=fake_ctrl)
        lan_dashboard.set_ui(fake_ui)
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/music/play_playlist?token={token}", json={"playlist_id": "LM"})
        self.assertEqual(r.status_code, 200)
        fake_ctrl.submit.assert_called_once_with("play_playlist", {"playlist_id": "LM"})

    def test_music_resume_action_whitelisted(self):
        """The phone's lock-screen/notification controls send discrete PLAY and
        PAUSE commands, not a toggle. "play" can't serve as resume — it means
        "search for this query and play it" — so resume needs its own action."""
        from actions import lan_dashboard

        fake_ctrl = MagicMock()
        fake_ctrl.submit.return_value = _done_future("ok")
        fake_ui = types.SimpleNamespace(playback_controller=fake_ctrl)
        lan_dashboard.set_ui(fake_ui)
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/music/resume?token={token}", json={})
        self.assertEqual(r.status_code, 200)
        fake_ctrl.submit.assert_called_once_with("resume", {})

    def test_music_prefetch_tracks_is_not_whitelisted(self):
        """Prefetch warm-ups must not enter the serialized playback queue: each
        takes about a second and would delay the user's real transport
        commands behind them."""
        from actions import lan_dashboard

        fake_ctrl = MagicMock()
        fake_ui = types.SimpleNamespace(playback_controller=fake_ctrl)
        lan_dashboard.set_ui(fake_ui)
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/music/prefetch_tracks?token={token}", json={})
        self.assertEqual(r.status_code, 400)
        fake_ctrl.submit.assert_not_called()

    def test_gamepad_status_reports_no_game_when_nothing_is_running(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        # ui.widgets.retro needs Qt; the endpoint must degrade to "no game"
        # rather than 500 when it can't be imported or nothing is attached.
        r = client.get(f"/api/gamepad/status?token={token}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("active", r.get_json())

    def test_gamepad_input_forwards_buttons_to_the_running_core(self):
        import types as _types
        from actions import lan_dashboard

        core = MagicMock()
        screen = _types.SimpleNamespace(core=core, _console_id="snes")

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        from ui.widgets import retro
        with patch.object(retro, "active_screen", return_value=screen):
            r = client.post(
                f"/api/gamepad/input?token={token}",
                json={"buttons": [{"name": "a", "pressed": True}, {"name": "up", "pressed": False}]},
            )

        self.assertEqual(r.status_code, 200)
        core.set_button.assert_any_call("a", True)
        core.set_button.assert_any_call("up", False)

    def test_gamepad_input_drops_unknown_button_names(self):
        """A typo must not reach the core; libretro would map it to nothing
        useful and the failure would be silent."""
        import types as _types
        from actions import lan_dashboard

        core = MagicMock()
        screen = _types.SimpleNamespace(core=core, _console_id="snes")

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        from ui.widgets import retro
        with patch.object(retro, "active_screen", return_value=screen):
            r = client.post(
                f"/api/gamepad/input?token={token}",
                json={"buttons": [{"name": "turbo", "pressed": True}]},
            )

        self.assertEqual(r.status_code, 200)
        core.set_button.assert_not_called()

    def test_gamepad_input_without_a_game_is_a_conflict(self):
        import types as _types
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        from ui.widgets import retro
        with patch.object(retro, "active_screen", return_value=None):
            r = client.post(f"/api/gamepad/input?token={token}", json={"buttons": []})

        self.assertEqual(r.status_code, 409)

    def test_gamepad_input_forwards_analog_axes(self):
        """PS2/N64 pads are useless without sticks, and those travel as axes
        rather than buttons."""
        import types as _types
        from actions import lan_dashboard
        from ui.widgets import retro

        core = MagicMock()
        screen = _types.SimpleNamespace(core=core, _console_id="ps2")

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(retro, "active_screen", return_value=screen):
            r = client.post(
                f"/api/gamepad/input?token={token}",
                json={"axes": [
                    {"index": 0, "axis": 0, "value": -32767},
                    {"index": 1, "axis": 1, "value": 12000},
                ]},
            )

        self.assertEqual(r.status_code, 200)
        core.set_axis.assert_any_call(0, 0, -32767)
        core.set_axis.assert_any_call(1, 1, 12000)

    def test_gamepad_input_clear_releases_everything(self):
        """Closing the pad mid-press must not leave the core holding a button
        or a stick pushed off-centre."""
        import types as _types
        from actions import lan_dashboard
        from ui.widgets import retro

        core = MagicMock()
        screen = _types.SimpleNamespace(core=core, _console_id="ps2")

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(retro, "active_screen", return_value=screen):
            r = client.post(f"/api/gamepad/input?token={token}", json={"clear": True})

        self.assertEqual(r.status_code, 200)
        core.clear_input.assert_called_once()

    def test_gamepad_status_describes_the_console_pad(self):
        import types as _types
        from actions import lan_dashboard
        from ui.widgets import retro

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        screen = _types.SimpleNamespace(core=MagicMock(), _console_id="ps2")
        with patch.object(retro, "active_screen", return_value=screen):
            ps2 = client.get(f"/api/gamepad/status?token={token}").get_json()

        screen = _types.SimpleNamespace(core=MagicMock(), _console_id="gb")
        with patch.object(retro, "active_screen", return_value=screen):
            gb = client.get(f"/api/gamepad/status?token={token}").get_json()

        self.assertEqual(ps2["layout"]["sticks"], 2)
        self.assertTrue(ps2["layout"]["stick_buttons"])
        self.assertEqual(ps2["layout"]["face"], "playstation")
        # A Game Boy has no sticks, no triggers and no shoulders.
        self.assertEqual(gb["layout"]["sticks"], 0)
        self.assertFalse(gb["layout"]["triggers"])
        self.assertFalse(gb["layout"]["shoulders"])

    def test_gamepad_announce_bumps_the_counter_the_phone_watches(self):
        """The phone re-opens its prompt when this number changes, which is how
        the desktop's 'Mando móvil' button calls it back after a dismissal."""
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        before = client.get(f"/api/gamepad/status?token={token}").get_json()["announce"]
        r = client.post(f"/api/gamepad/announce?token={token}")
        after = client.get(f"/api/gamepad/status?token={token}").get_json()["announce"]

        self.assertEqual(r.status_code, 200)
        self.assertEqual(after, before + 1)
        self.assertEqual(r.get_json()["announce"], after)

    def test_gamepad_status_carries_the_announce_counter_even_with_no_game(self):
        """The phone needs a baseline to compare against, or the first
        re-announce after launch would look like 'no change'."""
        from actions import lan_dashboard
        from ui.widgets import retro

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(retro, "active_screen", return_value=None):
            body = client.get(f"/api/gamepad/status?token={token}").get_json()

        self.assertFalse(body["active"])
        self.assertIn("announce", body)

    def test_gamepad_endpoints_reject_a_bad_token(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        self.assertEqual(client.get("/api/gamepad/status?token=wrong").status_code, 403)
        self.assertEqual(client.post("/api/gamepad/input?token=wrong", json={}).status_code, 403)
        self.assertEqual(client.post("/api/gamepad/announce?token=wrong").status_code, 403)

    def test_voice_transcribes_and_returns_the_text(self):
        import io
        from actions import lan_dashboard, file_processor

        lan_dashboard.set_jarvis(None)  # no live session: transcribe only
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(file_processor, "_process_audio", return_value="pon música") as proc:
            r = client.post(
                f"/api/voice?token={token}",
                data={"audio": (io.BytesIO(b"fake-audio"), "voice.m4a")},
                content_type="multipart/form-data",
            )

        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["text"], "pon música")
        self.assertFalse(body["ran"])  # nothing to run it on
        self.assertEqual(proc.call_args.args[1], "transcribe")

    def test_voice_runs_the_transcript_as_a_command_when_jarvis_is_live(self):
        import io
        import types as _types
        from actions import lan_dashboard, file_processor

        sent = []

        class FakeLoop:
            pass

        fake = _types.SimpleNamespace(
            _loop=FakeLoop(), session=object(),
            _send_text_command=lambda text: sent.append(text),
        )
        lan_dashboard.set_jarvis(fake)
        self.addCleanup(lan_dashboard.set_jarvis, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(file_processor, "_process_audio", return_value="siguiente canción"), \
             patch("asyncio.run_coroutine_threadsafe") as run:
            r = client.post(
                f"/api/voice?token={token}",
                data={"audio": (io.BytesIO(b"fake"), "voice.m4a")},
                content_type="multipart/form-data",
            )

        self.assertTrue(r.get_json()["ran"])
        run.assert_called_once()

    def test_voice_requires_audio_and_a_token(self):
        import io
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        self.assertEqual(client.post(f"/api/voice?token={token}").status_code, 400)
        r = client.post(
            "/api/voice?token=wrong",
            data={"audio": (io.BytesIO(b"x"), "v.m4a")},
            content_type="multipart/form-data",
        )
        self.assertEqual(r.status_code, 403)

    def test_remote_clipboard_round_trip(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch("pyperclip.copy") as copy, patch("pyperclip.paste", return_value="del pc"):
            w = client.post(f"/api/remote/clipboard?token={token}", json={"text": "del movil"})
            r = client.get(f"/api/remote/clipboard?token={token}")

        self.assertEqual(w.status_code, 200)
        copy.assert_called_once_with("del movil")
        self.assertEqual(r.get_json()["text"], "del pc")

    def test_remote_system_action_is_dispatched(self):
        from actions import lan_dashboard, computer_settings

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(computer_settings, "lock_screen") as lock:
            r = client.post(f"/api/remote/system?token={token}", json={"action": "lock_screen"})
        self.assertEqual(r.status_code, 200)
        lock.assert_called_once()

        with patch.object(computer_settings, "volume_set") as vol:
            r = client.post(f"/api/remote/system?token={token}", json={"action": "volume_set", "level": 30})
        self.assertEqual(r.status_code, 200)
        vol.assert_called_once_with(30)

    def test_remote_system_rejects_actions_outside_the_whitelist(self):
        """computer_settings also exposes type_text(); an open getattr dispatch
        would hand the network a keyboard on the desktop."""
        from actions import lan_dashboard, computer_settings

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(computer_settings, "type_text") as typer:
            r = client.post(
                f"/api/remote/system?token={token}",
                json={"action": "type_text", "text": "rm -rf"},
            )
        self.assertEqual(r.status_code, 400)
        typer.assert_not_called()

    def test_remote_app_launch_requires_a_name(self):
        from actions import lan_dashboard, system_tools

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(system_tools, "app_launch") as launch:
            r = client.post(f"/api/remote/system?token={token}", json={"action": "app_launch"})
        self.assertEqual(r.status_code, 400)
        launch.assert_not_called()

        with patch.object(system_tools, "app_launch", return_value="ok") as launch:
            r = client.post(
                f"/api/remote/system?token={token}",
                json={"action": "app_launch", "name": "chrome"},
            )
        self.assertEqual(r.status_code, 200)
        launch.assert_called_once_with("chrome")

    def test_remote_endpoints_reject_a_bad_token(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        self.assertEqual(client.get("/api/remote/clipboard?token=wrong").status_code, 403)
        self.assertEqual(client.post("/api/remote/clipboard?token=wrong", json={}).status_code, 403)
        self.assertEqual(client.post("/api/remote/system?token=wrong", json={}).status_code, 403)
        self.assertEqual(client.get("/api/remote/status?token=wrong").status_code, 403)

    def test_cover_colour_is_computed_off_the_request_thread(self):
        """Reading a cover takes over a second. /api/status is polled every few
        seconds, so the first call must return empty and schedule the work
        rather than stall the phone's whole UI."""
        import io as _io
        import time as _time
        from PIL import Image
        from actions import lan_dashboard

        lan_dashboard._cover_color_cache.clear()
        buf = _io.BytesIO()
        Image.new("RGB", (32, 32), (200, 40, 60)).save(buf, format="PNG")
        response = MagicMock(content=buf.getvalue())

        url = "https://yt3.googleusercontent.com/test=w800-h800-l90-rj"
        with patch("requests.get", return_value=response):
            first = lan_dashboard.cover_accent_color(url)
            deadline = _time.time() + 5
            while _time.time() < deadline and not lan_dashboard._cover_color_cache.get(url):
                _time.sleep(0.05)
            second = lan_dashboard.cover_accent_color(url)

        self.assertEqual(first, "")
        self.assertTrue(second.startswith("#"), second)
        # A red square must come back red, not averaged into grey.
        r = int(second[1:3], 16)
        self.assertGreater(r, 150)

    def test_cover_colour_skips_black_borders(self):
        """The biggest bucket in real artwork is usually its dark border; an
        unfiltered pick would tint every notification near-black."""
        import io as _io
        import time as _time
        from PIL import Image
        from actions import lan_dashboard

        lan_dashboard._cover_color_cache.clear()
        art = Image.new("RGB", (64, 64), (0, 0, 0))          # mostly black
        art.paste(Image.new("RGB", (20, 20), (60, 200, 90)), (22, 22))  # green core
        buf = _io.BytesIO()
        art.save(buf, format="PNG")

        url = "https://yt3.googleusercontent.com/dark=w800-h800-l90-rj"
        with patch("requests.get", return_value=MagicMock(content=buf.getvalue())):
            lan_dashboard.cover_accent_color(url)
            deadline = _time.time() + 5
            while _time.time() < deadline and not lan_dashboard._cover_color_cache.get(url):
                _time.sleep(0.05)
            color = lan_dashboard.cover_accent_color(url)

        g = int(color[3:5], 16)
        self.assertGreater(g, 100, f"expected the green accent, got {color}")

    def test_now_playing_cover_is_requested_at_a_large_size(self):
        """YouTube Music stores list artwork at 120², which is mush behind a
        260pt now-playing cover. The size lives in the URL."""
        from actions import lan_dashboard

        win = types.SimpleNamespace(
            _play_title="Song", _play_artists="Artist", _play_playing=True,
            _play_state="playing", _play_video_id="abc",
            _play_thumbnail="https://yt3.googleusercontent.com/xyz=w120-h120-s-l90-rj",
        )
        lan_dashboard.set_ui(types.SimpleNamespace(_win=win))
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        thumb = client.get(f"/api/status?token={token}").get_json()["music"]["thumbnail"]
        self.assertIn("w800-h800", thumb)
        self.assertNotIn("w120", thumb)

    def test_list_covers_use_a_row_sized_request(self):
        """Rows are ~44pt: pulling the 800² copy for each would waste the
        phone's data for no visible gain."""
        from actions import lan_dashboard, ytmusic

        stub = [{"videoId": "a", "title": "T", "artists": "A",
                 "thumbnail": "https://yt3.googleusercontent.com/xyz=w120-h120-s-l90-rj"}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(ytmusic, "search_songs", return_value=stub):
            body = client.get(f"/api/music/search?q=x&token={token}").get_json()

        self.assertIn("w240-h240", body[0]["thumbnail"])

    def test_non_google_covers_are_left_alone(self):
        """The liked-songs cover is a static gstatic PNG and the video-frame
        fallbacks are i.ytimg URLs; neither takes a size suffix."""
        from actions import lan_dashboard, ytmusic

        stub = [
            {"playlistId": "LM", "title": "Me gusta",
             "thumbnail": "https://www.gstatic.com/youtube/media/ytm/images/pbg/liked-songs-delhi-1200.png"},
            {"playlistId": "P2", "title": "Otra",
             "thumbnail": "https://i.ytimg.com/vi/abc/hqdefault.jpg"},
        ]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(ytmusic, "list_playlists", return_value=stub):
            body = client.get(f"/api/music/playlists?token={token}").get_json()

        self.assertEqual(body[0]["thumbnail"], stub[0]["thumbnail"])
        self.assertEqual(body[1]["thumbnail"], stub[1]["thumbnail"])

    def test_music_lyrics_are_returned_as_timed_lines(self):
        from actions import lan_dashboard, lyrics

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(lyrics, "get_synced_lyrics", return_value=((0.0, "uno"), (2.5, "dos"))):
            r = client.get(f"/api/music/lyrics?title=T&artists=A&token={token}")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), [{"time": 0.0, "line": "uno"}, {"time": 2.5, "line": "dos"}])

    def test_music_lyrics_missing_is_an_empty_list_not_an_error(self):
        """Plenty of tracks simply have no synced lyrics; the phone shows a
        hint for that, so it must not arrive as a failure."""
        from actions import lan_dashboard, lyrics

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(lyrics, "get_synced_lyrics", side_effect=RuntimeError("provider down")):
            r = client.get(f"/api/music/lyrics?title=T&artists=A&token={token}")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), [])

    def test_music_set_like_is_whitelisted(self):
        from actions import lan_dashboard

        fake_ctrl = MagicMock()
        fake_ctrl.submit.return_value = _done_future("ok")
        lan_dashboard.set_ui(types.SimpleNamespace(playback_controller=fake_ctrl))
        self.addCleanup(lan_dashboard.set_ui, None)

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/music/set_like?token={token}", json={"video_id": "abc", "liked": True})
        self.assertEqual(r.status_code, 200)
        fake_ctrl.submit.assert_called_once_with("set_like", {"video_id": "abc", "liked": True})

    def test_music_stream_prefers_a_downloaded_file(self):
        """A track already in the offline library must be served from disk —
        no yt-dlp resolution, no upstream fetch."""
        import tempfile
        from pathlib import Path
        from actions import lan_dashboard, offline_library, ytmusic_headless

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.m4a"
            audio.write_bytes(b"fake-audio-bytes")

            token = lan_dashboard._get_or_create_token()
            app = lan_dashboard._make_app(token)
            client = app.test_client()

            with patch.object(offline_library, "local_file_for", return_value=str(audio)), \
                 patch.object(ytmusic_headless, "_resolve_stream_for_video") as resolve:
                r = client.get(f"/api/music/stream?video_id=abc&token={token}")
                status, body = r.status_code, r.data
                # send_file keeps the handle open; on Windows the temp dir
                # cannot be removed until the response is closed.
                r.close()

            self.assertEqual(status, 200)
            self.assertEqual(body, b"fake-audio-bytes")
            resolve.assert_not_called()

    def test_music_stream_serves_byte_ranges_for_local_files(self):
        """Without Range support Android can't seek, and may refuse to start."""
        import tempfile
        from pathlib import Path
        from actions import lan_dashboard, offline_library

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "song.m4a"
            audio.write_bytes(b"0123456789")

            token = lan_dashboard._get_or_create_token()
            app = lan_dashboard._make_app(token)
            client = app.test_client()

            with patch.object(offline_library, "local_file_for", return_value=str(audio)):
                r = client.get(
                    f"/api/music/stream?video_id=abc&token={token}",
                    headers={"Range": "bytes=2-5"},
                )
                status, body = r.status_code, r.data
                r.close()  # see note above: Windows won't unlink an open file

            self.assertEqual(status, 206)
            self.assertEqual(body, b"2345")

    def test_music_stream_proxies_the_resolved_stream_when_not_downloaded(self):
        """The resolved URLs are bound to the session that fetched them, so the
        desktop proxies the bytes instead of redirecting the phone to them."""
        from actions import lan_dashboard, offline_library, ytmusic_headless

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        upstream = MagicMock()
        upstream.status_code = 206
        upstream.headers = {"Content-Type": "audio/webm", "Content-Range": "bytes 0-9/10"}
        upstream.iter_content.return_value = iter([b"streamed"])

        with patch.object(offline_library, "local_file_for", return_value=None), \
             patch.object(ytmusic_headless, "_resolve_stream_for_video", return_value=("http://cdn/x", 200)), \
             patch("requests.get", return_value=upstream) as get:
            r = client.get(
                f"/api/music/stream?video_id=abc&token={token}",
                headers={"Range": "bytes=0-9"},
            )

        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.data, b"streamed")
        # The client's Range must reach the CDN, or seeking downloads the
        # whole track every time.
        self.assertEqual(get.call_args.kwargs["headers"]["Range"], "bytes=0-9")
        self.assertEqual(r.headers["Content-Range"], "bytes 0-9/10")

    def test_music_stream_requires_a_video_id_and_a_token(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        self.assertEqual(client.get(f"/api/music/stream?token={token}").status_code, 400)
        self.assertEqual(client.get("/api/music/stream?video_id=a&token=wrong").status_code, 403)

    def test_whatsapp_acks_are_returned_per_message(self):
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "get_message_acks", return_value={"m1": 3}) as fn:
            r = client.post(f"/api/whatsapp/acks?token={token}", json={"ids": ["m1", " "]})

        self.assertEqual(r.get_json(), {"m1": 3})
        fn.assert_called_once_with(["m1"])

    def test_whatsapp_transcribe_audio(self):
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "transcribe_message_audio", return_value="hola qué tal") as fn:
            r = client.post(
                f"/api/whatsapp/transcribe?token={token}",
                json={"message": {"id": "m1", "type": "ptt"}},
            )

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["text"], "hola qué tal")
        fn.assert_called_once()

    def test_whatsapp_suggest_reply(self):
        from actions import lan_dashboard, whatsapp_ai

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_ai, "generate_whatsapp_reply", return_value="Voy en 10") as fn:
            r = client.post(
                f"/api/whatsapp/suggest?token={token}",
                json={"chat_id": "1@c.us", "incoming": "¿llegas?"},
            )

        self.assertEqual(r.get_json()["text"], "Voy en 10")
        fn.assert_called_once_with("1@c.us", "¿llegas?")

    def test_whatsapp_send_media_writes_a_temp_file_and_cleans_up(self):
        """The bridge only accepts a local path, so the upload has to land on
        disk first — and must not stay there."""
        import io as _io
        from pathlib import Path
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        seen = {}

        def fake_send(to, file_path, caption="", **kwargs):
            seen["path"] = file_path
            seen["existed"] = Path(file_path).is_file()
            return {"ok": True}

        with patch.object(whatsapp, "send_whatsapp_media", side_effect=fake_send):
            r = client.post(
                f"/api/whatsapp/send_media?token={token}",
                data={"chat_id": "1@c.us", "file": (_io.BytesIO(b"bytes"), "foto.jpg")},
                content_type="multipart/form-data",
            )

        self.assertEqual(r.status_code, 200)
        self.assertTrue(seen["existed"], "the file must exist while the bridge reads it")
        self.assertFalse(Path(seen["path"]).exists(), "the temp file must be removed afterwards")

    def test_whatsapp_send_media_requires_chat_and_file(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/whatsapp/send_media?token={token}", data={"chat_id": "1@c.us"})
        self.assertEqual(r.status_code, 400)

    def test_whatsapp_translate_passes_the_text_through(self):
        from actions import lan_dashboard, whatsapp_ai

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_ai, "translate_if_foreign", return_value="hola") as tr:
            r = client.post(f"/api/whatsapp/translate?token={token}", json={"text": "hello"})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["text"], "hola")
        tr.assert_called_once_with("hello")

    def test_whatsapp_translate_keeps_the_empty_already_translated_answer(self):
        """An empty string is the helper's way of saying 'already Spanish' —
        it must reach the phone as-is, not become an error."""
        from actions import lan_dashboard, whatsapp_ai

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_ai, "translate_if_foreign", return_value=""):
            r = client.post(f"/api/whatsapp/translate?token={token}", json={"text": "hola"})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["text"], "")

    def test_whatsapp_mark_read(self):
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "mark_chat_read", return_value=True) as mark:
            r = client.post(f"/api/whatsapp/mark_read?token={token}", json={"chat_id": "1@c.us"})

        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        mark.assert_called_once_with("1@c.us")

    def test_whatsapp_translate_and_mark_read_reject_a_bad_token(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        self.assertEqual(client.post("/api/whatsapp/translate?token=wrong", json={}).status_code, 403)
        self.assertEqual(client.post("/api/whatsapp/mark_read?token=wrong", json={}).status_code, 403)

    def test_whatsapp_automations_returns_settings_and_rules(self):
        from actions import lan_dashboard, app_settings, whatsapp_rules

        rules = [{"id": "r1", "name": "Fuera de oficina", "enabled": True}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_rules, "load_rules", return_value=rules), \
             patch.object(app_settings, "get", side_effect=lambda k, d=None: k.endswith("translate")):
            r = client.get(f"/api/whatsapp/automations?token={token}")

        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body["rules"], rules)
        self.assertTrue(body["settings"]["whatsapp_auto_translate"])
        self.assertFalse(body["settings"]["whatsapp_auto_transcribe"])

    def test_whatsapp_automation_setting_write(self):
        from actions import lan_dashboard, app_settings

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(app_settings, "set") as setter:
            r = client.post(
                f"/api/whatsapp/automations/setting?token={token}",
                json={"key": "whatsapp_auto_transcribe", "value": True},
            )
        self.assertEqual(r.status_code, 200)
        setter.assert_called_once_with("whatsapp_auto_transcribe", True)

    def test_whatsapp_automation_setting_rejects_unlisted_keys(self):
        """This endpoint must not become a way to write any app setting from
        the network — only the two WhatsApp toggles are allowed."""
        from actions import lan_dashboard, app_settings

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(app_settings, "set") as setter:
            r = client.post(
                f"/api/whatsapp/automations/setting?token={token}",
                json={"key": "lan_dashboard_token", "value": True},
            )
        self.assertEqual(r.status_code, 400)
        setter.assert_not_called()

    def test_whatsapp_rule_save_creates_when_id_is_absent(self):
        from actions import lan_dashboard, whatsapp_rules

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_rules, "add_rule", return_value={"id": "new"}) as add, \
             patch.object(whatsapp_rules, "update_rule") as upd:
            r = client.post(f"/api/whatsapp/rules/save?token={token}", json={"rule": {"name": "X"}})

        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["created"])
        add.assert_called_once()
        upd.assert_not_called()

    def test_whatsapp_rule_save_updates_when_id_is_present(self):
        from actions import lan_dashboard, whatsapp_rules

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_rules, "update_rule", return_value=True) as upd, \
             patch.object(whatsapp_rules, "add_rule") as add:
            r = client.post(
                f"/api/whatsapp/rules/save?token={token}",
                json={"rule": {"id": "r1", "name": "X"}},
            )

        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["created"])
        upd.assert_called_once()
        add.assert_not_called()

    def test_whatsapp_rule_save_falls_back_to_create_for_a_stale_id(self):
        """The phone may still hold a rule deleted on the desktop; recreating it
        beats silently dropping the edit."""
        from actions import lan_dashboard, whatsapp_rules

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_rules, "update_rule", return_value=False), \
             patch.object(whatsapp_rules, "add_rule", return_value={"id": "new"}) as add:
            r = client.post(
                f"/api/whatsapp/rules/save?token={token}",
                json={"rule": {"id": "gone", "name": "X"}},
            )

        self.assertTrue(r.get_json()["created"])
        add.assert_called_once()

    def test_whatsapp_rule_delete_and_move(self):
        from actions import lan_dashboard, whatsapp_rules

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp_rules, "delete_rule", return_value=True) as dele:
            r = client.post(f"/api/whatsapp/rules/delete?token={token}", json={"rule_id": "r1"})
        self.assertEqual(r.status_code, 200)
        dele.assert_called_once_with("r1")

        with patch.object(whatsapp_rules, "move_rule", return_value=True) as mv:
            r = client.post(f"/api/whatsapp/rules/move?token={token}", json={"rule_id": "r1", "delta": -1})
        self.assertEqual(r.status_code, 200)
        mv.assert_called_once_with("r1", -1)

    def test_whatsapp_automation_endpoints_reject_a_bad_token(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        self.assertEqual(client.get("/api/whatsapp/automations?token=wrong").status_code, 403)
        self.assertEqual(
            client.post("/api/whatsapp/automations/setting?token=wrong", json={}).status_code, 403
        )
        self.assertEqual(client.post("/api/whatsapp/rules/save?token=wrong", json={}).status_code, 403)

    def test_calendar_events_range(self):
        from actions import lan_dashboard, google_calendar

        stub = [{"id": "e1", "summary": "Reunión", "start": "2026-08-16T09:00:00+02:00", "all_day": False}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(google_calendar, "list_events_range", return_value=stub) as fn:
            r = client.get(
                f"/api/calendar/events?time_min=2026-08-01T00:00:00Z"
                f"&time_max=2026-09-01T00:00:00Z&token={token}"
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), stub)
        fn.assert_called_once_with("2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z")

    def test_calendar_events_requires_a_range(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.get(f"/api/calendar/events?token={token}")
        self.assertEqual(r.status_code, 400)

    def test_calendar_auth_failure_is_reported_as_needing_sign_in(self):
        """A missing Google token is the expected 'not set up yet' state, so the
        app can show a sign-in hint instead of a generic failure."""
        from actions import lan_dashboard, google_calendar

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(
            google_calendar, "list_events_range", side_effect=FileNotFoundError("no credentials"),
        ):
            r = client.get(f"/api/calendar/events?time_min=a&time_max=b&token={token}")

        self.assertEqual(r.status_code, 503)
        self.assertTrue(r.get_json()["needs_auth"])

    def test_calendar_create_event(self):
        from actions import lan_dashboard, google_calendar

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(google_calendar, "create_event", return_value={"id": "n1"}) as fn:
            r = client.post(
                f"/api/calendar/create?token={token}",
                json={"summary": "Cita", "start": "2026-08-16T09:00:00", "end": "2026-08-16T10:00:00"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(fn.call_args.kwargs["summary"], "Cita")
        self.assertEqual(fn.call_args.kwargs["end"], "2026-08-16T10:00:00")

    def test_calendar_create_requires_summary_and_start(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        r = client.post(f"/api/calendar/create?token={token}", json={"summary": "Solo título"})
        self.assertEqual(r.status_code, 400)

    def test_calendar_delete_event(self):
        from actions import lan_dashboard, google_calendar

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(google_calendar, "delete_event", return_value="deleted") as fn:
            r = client.post(f"/api/calendar/delete?token={token}", json={"event_id": "e1"})
        self.assertEqual(r.status_code, 200)
        fn.assert_called_once_with("e1")

    def test_calendar_endpoints_reject_a_bad_token(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        self.assertEqual(client.get("/api/calendar/events?token=wrong").status_code, 403)
        self.assertEqual(client.post("/api/calendar/delete?token=wrong", json={}).status_code, 403)

    def test_whatsapp_chats(self):
        from actions import lan_dashboard, whatsapp

        stub = [{"chatId": "1@c.us", "name": "Alice", "preview": "hola", "unread": 2}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "list_recent_chats", return_value=stub):
            r = client.get(f"/api/whatsapp/chats?token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), stub)

    def test_whatsapp_chats_not_ready(self):
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "list_recent_chats", side_effect=whatsapp.WhatsAppUnavailable("no")):
            r = client.get(f"/api/whatsapp/chats?token={token}")
            self.assertEqual(r.status_code, 503)

    def test_whatsapp_messages(self):
        from actions import lan_dashboard, whatsapp

        stub = [{"id": "m1", "body": "hola", "fromMe": False, "authorName": "Alice"}]
        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "get_conversation", return_value=stub) as mock_conv:
            r = client.get(f"/api/whatsapp/messages?chat_id=1@c.us&token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.get_json(), stub)
            mock_conv.assert_called_once_with("1@c.us", limit=50, timeout=25, strict=True)

        r = client.get(f"/api/whatsapp/messages?token={token}")
        self.assertEqual(r.status_code, 400)

    def test_whatsapp_send(self):
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "send_whatsapp", return_value={"ok": True}) as mock_send:
            r = client.post(
                f"/api/whatsapp/send?token={token}",
                json={"chat_id": "1@c.us", "text": "hola"},
            )
            self.assertEqual(r.status_code, 200)
            mock_send.assert_called_once_with(to="1@c.us", body="hola")

        r = client.post(f"/api/whatsapp/send?token={token}", json={"chat_id": "1@c.us"})
        self.assertEqual(r.status_code, 400)

    def test_whatsapp_media_proxy(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        fake_resp = MagicMock()
        fake_resp.headers = {"Content-Type": "image/webp"}
        fake_resp.content = b"\xff\xd8\xff\xe0fakesticker"
        fake_resp.raise_for_status.return_value = None

        with patch("requests.get", return_value=fake_resp) as mock_get:
            r = client.get(f"/api/whatsapp/media?url=/media?id=abc&mimetype=image/jpeg&token={token}")
            self.assertEqual(r.status_code, 200)
            # real Content-Type from the bridge wins over the client-guessed one
            self.assertEqual(r.mimetype, "image/webp")
            self.assertEqual(r.get_data(), b"\xff\xd8\xff\xe0fakesticker")
            mock_get.assert_called_once()

        r = client.get(f"/api/whatsapp/media?token={token}")
        self.assertEqual(r.status_code, 400)

    def test_whatsapp_media_proxy_download_failed(self):
        from actions import lan_dashboard

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch("requests.get", side_effect=RuntimeError("boom")):
            r = client.get(f"/api/whatsapp/media?url=/media?id=x&token={token}")
            self.assertEqual(r.status_code, 502)

    def test_whatsapp_avatar_proxy(self):
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        fake_resp = MagicMock()
        fake_resp.headers = {"Content-Type": "image/jpeg"}
        fake_resp.content = b"avatarbytes"
        fake_resp.raise_for_status.return_value = None

        with patch.object(whatsapp, "get_profile_picture_url", return_value="https://pps.example/pic.jpg"), \
             patch("requests.get", return_value=fake_resp):
            r = client.get(f"/api/whatsapp/avatar?chat_id=1@c.us&token={token}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.mimetype, "image/jpeg")
            self.assertEqual(r.get_data(), b"avatarbytes")

    def test_whatsapp_avatar_no_picture(self):
        from actions import lan_dashboard, whatsapp

        token = lan_dashboard._get_or_create_token()
        app = lan_dashboard._make_app(token)
        client = app.test_client()

        with patch.object(whatsapp, "get_profile_picture_url", return_value=None):
            r = client.get(f"/api/whatsapp/avatar?chat_id=1@c.us&token={token}")
            self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
