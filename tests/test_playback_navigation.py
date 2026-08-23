import unittest
from unittest.mock import patch

from actions import ytmusic_headless


class PlaybackNavigationTests(unittest.TestCase):
    def setUp(self):
        self.old_playlist = list(ytmusic_headless._playlist)
        self.old_index = ytmusic_headless._playlist_idx
        self.old_fading = ytmusic_headless._crossfade_fading_out
        self.old_switch = ytmusic_headless._autoplay_last_switch
        self.addCleanup(
            setattr, ytmusic_headless, "_autoplay_last_switch", self.old_switch
        )
        ytmusic_headless._playlist[:] = [
            {"videoId": "one", "title": "One", "artists": "Artist"},
            {"videoId": "two", "title": "Two", "artists": "Artist"},
        ]
        ytmusic_headless._playlist_idx = 0
        ytmusic_headless._crossfade_fading_out = True

    def tearDown(self):
        ytmusic_headless._playlist[:] = self.old_playlist
        ytmusic_headless._playlist_idx = self.old_index
        ytmusic_headless._crossfade_fading_out = self.old_fading

    def test_next_rolls_back_index_when_transition_fails(self):
        with patch.object(
            ytmusic_headless,
            "_crossfade_transition",
            return_value="No se pudo cargar la canción en mpv.",
        ):
            result = ytmusic_headless.next(manual=True)

        self.assertIn("No se pudo", result)
        self.assertEqual(ytmusic_headless._playlist_idx, 0)
        self.assertTrue(ytmusic_headless._crossfade_fading_out)

    def test_previous_rolls_back_index_when_transition_raises(self):
        with patch.object(
            ytmusic_headless,
            "_crossfade_transition",
            side_effect=RuntimeError("transition failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "transition failed"):
                ytmusic_headless.previous(manual=True)

        self.assertEqual(ytmusic_headless._playlist_idx, 0)
        self.assertTrue(ytmusic_headless._crossfade_fading_out)

    def test_jump_to_moves_to_requested_index(self):
        ytmusic_headless._playlist[:] = [
            {"videoId": "one", "title": "One", "artists": "Artist"},
            {"videoId": "two", "title": "Two", "artists": "Artist"},
            {"videoId": "three", "title": "Three", "artists": "Artist"},
        ]
        with patch.object(ytmusic_headless, "_crossfade_transition", return_value="Reproduciendo Three"):
            result = ytmusic_headless.jump_to(2)

        self.assertIn("Reproduciendo", result)
        self.assertEqual(ytmusic_headless._playlist_idx, 2)

    def test_jump_to_same_index_is_a_noop(self):
        ytmusic_headless._playlist_idx = 0
        result = ytmusic_headless.jump_to(0)
        self.assertIn("Ya está sonando", result)
        self.assertEqual(ytmusic_headless._playlist_idx, 0)

    def test_jump_to_clamps_out_of_range_index(self):
        ytmusic_headless._playlist[:] = [
            {"videoId": "one", "title": "One", "artists": "Artist"},
            {"videoId": "two", "title": "Two", "artists": "Artist"},
        ]
        with patch.object(ytmusic_headless, "_crossfade_transition", return_value="Reproduciendo Two"):
            ytmusic_headless.jump_to(99)
        self.assertEqual(ytmusic_headless._playlist_idx, 1)

    def test_jump_to_rolls_back_when_transition_fails(self):
        ytmusic_headless._playlist[:] = [
            {"videoId": "one", "title": "One", "artists": "Artist"},
            {"videoId": "two", "title": "Two", "artists": "Artist"},
        ]
        ytmusic_headless._playlist_idx = 0
        with patch.object(ytmusic_headless, "_crossfade_transition", return_value="No se pudo cargar la canción en mpv."):
            result = ytmusic_headless.jump_to(1)
        self.assertIn("No se pudo", result)
        self.assertEqual(ytmusic_headless._playlist_idx, 0)

    def test_queue_snapshot_anchors_on_the_audible_track_not_the_index(self):
        """_playlist_idx is advanced when a transition *starts*; a crossfade
        that never completes leaves it pointing at a track that isn't playing.
        The up-next list must follow the audio, or it shows an unrelated slice
        of the playlist."""
        ytmusic_headless._playlist_idx = 1
        with patch.dict(ytmusic_headless._last_meta, {"videoId": "one"}, clear=False):
            snapshot = ytmusic_headless.queue_snapshot(limit=5)

        self.assertEqual([t["videoId"] for t in snapshot], ["one", "two"])
        self.assertEqual([t["index"] for t in snapshot], [0, 1])

    def test_jump_to_uses_the_audible_track_as_the_origin(self):
        """With a stale index, a delta measured from _playlist_idx lands on the
        wrong track entirely."""
        ytmusic_headless._playlist_idx = 1  # stale: "one" is what's audible
        with patch.dict(ytmusic_headless._last_meta, {"videoId": "one"}, clear=False):
            with patch.object(
                ytmusic_headless, "_crossfade_transition", return_value="Reproduciendo 'Two'.",
            ) as transition:
                ytmusic_headless.jump_to(1)

        transition.assert_called_once()
        self.assertEqual(transition.call_args.args[0]["videoId"], "two")
        self.assertEqual(ytmusic_headless._playlist_idx, 1)

    def test_jump_to_the_audible_track_is_the_noop_not_the_stale_index(self):
        """The desync made tapping the track you can hear return 'already
        playing' for a *different* track, so the tap did nothing."""
        ytmusic_headless._playlist_idx = 1
        with patch.dict(ytmusic_headless._last_meta, {"videoId": "one"}, clear=False):
            with patch.object(ytmusic_headless, "_crossfade_transition") as transition:
                noop = ytmusic_headless.jump_to(0)
            self.assertIn("Ya está sonando", noop)
            transition.assert_not_called()

    def test_next_advances_from_the_audible_track_when_index_is_stale(self):
        ytmusic_headless._playlist[:] = [
            {"videoId": "one", "title": "One", "artists": "A"},
            {"videoId": "two", "title": "Two", "artists": "A"},
            {"videoId": "three", "title": "Three", "artists": "A"},
        ]
        ytmusic_headless._playlist_idx = 2  # stale
        with patch.dict(ytmusic_headless._last_meta, {"videoId": "one"}, clear=False):
            with patch.object(
                ytmusic_headless, "_crossfade_transition", return_value="Reproduciendo 'Two'.",
            ) as transition:
                ytmusic_headless.next(manual=True)

        self.assertEqual(transition.call_args.args[0]["videoId"], "two")

    def test_manual_skip_arms_the_autoplay_cooldown(self):
        """The worker debounces its own advances with this stamp. Without the
        user's skip counting too, a skip taken inside the outgoing track's
        near-end window was followed straight away by an automatic advance —
        one press, two songs."""
        ytmusic_headless._autoplay_last_switch = 0.0
        with patch.object(
            ytmusic_headless, "_crossfade_transition", return_value="Reproduciendo 'Two'.",
        ):
            ytmusic_headless.next(manual=True)

        self.assertGreater(ytmusic_headless._autoplay_last_switch, 0.0)

    def test_failed_skip_does_not_arm_the_cooldown(self):
        ytmusic_headless._autoplay_last_switch = 0.0
        with patch.object(
            ytmusic_headless, "_crossfade_transition",
            return_value="No se pudo cargar la canción en mpv.",
        ):
            ytmusic_headless.next(manual=True)

        self.assertEqual(ytmusic_headless._autoplay_last_switch, 0.0)

    def test_autoplay_holds_off_right_after_any_track_change(self):
        ytmusic_headless._autoplay_last_switch = 1000.0
        # Deep into the near-end window of a 200 s track, one second later.
        self.assertEqual(
            ytmusic_headless._autoplay_decision(199.0, 200.0, False, False, 1001.0),
            "none",
        )
        self.assertEqual(
            ytmusic_headless._autoplay_decision(199.0, 200.0, False, False, 1010.0),
            "advance",
        )

    def test_autoplay_silences_a_stream_that_hit_eof_at_zero(self):
        ytmusic_headless._autoplay_last_switch = 0.0
        self.assertEqual(
            ytmusic_headless._autoplay_decision(0.0, 200.0, True, False, 1000.0),
            "silence",
        )

    def test_autoplay_never_advances_twice_for_the_same_ending(self):
        ytmusic_headless._autoplay_last_switch = 0.0
        self.assertEqual(
            ytmusic_headless._autoplay_decision(199.0, 200.0, True, True, 1000.0),
            "none",
        )

    def test_loading_new_playlist_rolls_back_selection_when_play_fails(self):
        old_playlist = list(ytmusic_headless._playlist)
        replacement = [
            {"videoId": "new", "title": "New", "artists": "Artist"},
        ]
        with patch.object(
            ytmusic_headless,
            "_play_video",
            return_value="No se pudo cargar la canción en mpv.",
        ):
            result = ytmusic_headless._load_and_play_playlist(replacement, 0)

        self.assertIn("No se pudo", result)
        self.assertEqual(ytmusic_headless._playlist, old_playlist)
        self.assertEqual(ytmusic_headless._playlist_idx, 0)


if __name__ == "__main__":
    unittest.main()
