"""Tests for the AniList-primary / Kitsu-fallback catalog orchestration.

The point of the fallback is that the Kitsu addon and Torrentio share a host
(strem.fun), so one outage used to take out browsing and playback together.
AniList fails independently of both.
"""
import unittest
from unittest.mock import patch

from actions.movie_search import Movie


def _movie(title="Frieren", kitsu_id="46474"):
    return Movie(tmdb_id=0, title=title, media_type="tv", kitsu_id=kitsu_id)


class WithFallbackTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.catalog as catalog
        self.catalog = catalog

    def test_returns_primary_when_it_works(self):
        out = self.catalog._with_fallback(lambda: ["primary"],
                                          lambda: ["fallback"], "x")
        self.assertEqual(out, ["primary"])

    def test_uses_fallback_when_primary_raises(self):
        def boom():
            raise RuntimeError("anilist down")

        out = self.catalog._with_fallback(boom, lambda: ["fallback"], "x")
        self.assertEqual(out, ["fallback"])

    def test_uses_fallback_when_primary_returns_empty(self):
        """An empty result is as useless as an error, so it should also fall
        through rather than showing the user a blank grid."""
        out = self.catalog._with_fallback(lambda: [], lambda: ["fallback"], "x")
        self.assertEqual(out, ["fallback"])

    def test_raises_primary_error_when_both_fail(self):
        """The primary error describes the source we actually wanted, so it
        is the more useful one to surface."""
        class PrimaryError(RuntimeError):
            pass

        def primary():
            raise PrimaryError("anilist down")

        def fallback():
            raise RuntimeError("kitsu down too")

        with self.assertRaises(PrimaryError):
            self.catalog._with_fallback(primary, fallback, "x")

    def test_raises_when_both_return_empty(self):
        with self.assertRaises(self.catalog.KitsuError):
            self.catalog._with_fallback(lambda: [], lambda: [], "trending")

    def test_fallback_error_does_not_mask_a_working_primary(self):
        out = self.catalog._with_fallback(
            lambda: ["primary"],
            lambda: (_ for _ in ()).throw(RuntimeError("boom")), "x")
        self.assertEqual(out, ["primary"])


class CatalogRoutingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.catalog as catalog
        self.catalog = catalog

    def test_trending_prefers_anilist(self):
        with patch("actions.anime.anilist.get_trending_anime",
                   return_value=[_movie("From AniList")]) as mocked_anilist, \
             patch("actions.anime.catalog._kitsu_get_trending_anime") as mocked_kitsu:
            out = self.catalog.get_trending_anime(limit=5)
        self.assertEqual(out[0].title, "From AniList")
        mocked_anilist.assert_called_once_with(limit=5)
        mocked_kitsu.assert_not_called()

    def test_trending_falls_back_to_kitsu(self):
        from actions.anime.anilist import AniListError
        with patch("actions.anime.anilist.get_trending_anime",
                   side_effect=AniListError("down")), \
             patch("actions.anime.catalog._kitsu_get_trending_anime",
                   return_value=[_movie("From Kitsu")]):
            out = self.catalog.get_trending_anime(limit=5)
        self.assertEqual(out[0].title, "From Kitsu")

    def test_search_prefers_anilist(self):
        with patch("actions.anime.anilist.search_anime",
                   return_value=[_movie("From AniList")]), \
             patch("actions.anime.catalog._kitsu_search_anime") as mocked_kitsu:
            out = self.catalog.search_anime("frieren", limit=3)
        self.assertEqual(out[0].title, "From AniList")
        mocked_kitsu.assert_not_called()

    def test_search_falls_back_to_kitsu(self):
        from actions.anime.anilist import AniListError
        with patch("actions.anime.anilist.search_anime",
                   side_effect=AniListError("down")), \
             patch("actions.anime.catalog._kitsu_search_anime",
                   return_value=[_movie("From Kitsu")]):
            out = self.catalog.search_anime("frieren", limit=3)
        self.assertEqual(out[0].title, "From Kitsu")

    def test_genre_falls_back_to_kitsu(self):
        from actions.anime.anilist import AniListError
        with patch("actions.anime.anilist.get_anime_by_genre",
                   side_effect=AniListError("down")), \
             patch("actions.anime.catalog._kitsu_get_anime_by_genre",
                   return_value=[_movie("From Kitsu")]):
            out = self.catalog.get_anime_by_genre("Acción", limit=3)
        self.assertEqual(out[0].title, "From Kitsu")

    def test_airing_falls_back_to_kitsu(self):
        from actions.anime.anilist import AniListError
        with patch("actions.anime.anilist.get_airing_anime",
                   side_effect=AniListError("down")), \
             patch("actions.anime.catalog._kitsu_get_airing_anime",
                   return_value=[_movie("From Kitsu")]):
            out = self.catalog.get_airing_anime(limit=3)
        self.assertEqual(out[0].title, "From Kitsu")


class AniListParsingTests(unittest.TestCase):
    def setUp(self):
        import actions.anime.anilist as anilist
        self.anilist = anilist

    def _item(self, **over):
        item = {
            "id": 154587,
            "idMal": 52991,
            "title": {"romaji": "Sousou no Frieren",
                      "english": "Frieren: Beyond Journey's End"},
            "description": "A <i>mage</i> called Frieren.<br>Second line.",
            "episodes": 28,
            "seasonYear": 2023,
            "averageScore": 92,
            "coverImage": {"extraLarge": "http://x/xl.jpg", "large": "http://x/l.jpg"},
            "bannerImage": "http://x/banner.jpg",
        }
        item.update(over)
        return item

    def test_prefers_english_title(self):
        m = self.anilist._parse_media(self._item())
        self.assertEqual(m.title, "Frieren: Beyond Journey's End")

    def test_falls_back_to_romaji_title(self):
        m = self.anilist._parse_media(
            self._item(title={"romaji": "Sousou no Frieren", "english": None}))
        self.assertEqual(m.title, "Sousou no Frieren")

    def test_converts_score_to_ten_point_scale(self):
        """AniList scores 0-100; the rest of the app renders IMDb-style 0-10."""
        m = self.anilist._parse_media(self._item())
        self.assertAlmostEqual(m.rating, 9.2)

    def test_missing_score_is_zero_not_a_crash(self):
        m = self.anilist._parse_media(self._item(averageScore=None))
        self.assertEqual(m.rating, 0.0)

    def test_strips_html_from_description(self):
        m = self.anilist._parse_media(self._item())
        self.assertNotIn("<", m.overview)
        self.assertIn("mage", m.overview)

    def test_item_without_id_is_dropped(self):
        self.assertIsNone(self.anilist._parse_media(self._item(id=None)))

    def test_item_without_any_title_is_dropped(self):
        self.assertIsNone(
            self.anilist._parse_media(self._item(title={"romaji": None, "english": None})))

    def test_attach_kitsu_ids_leaves_unmappable_titles_empty(self):
        """Very new shows have no ani.zip mapping yet; they must still show
        up rather than vanishing from the grid."""
        movie = _movie("Brand New 2026 Show", kitsu_id="")
        with patch("actions.anime.mappings.mappings",
                   side_effect=RuntimeError("no mapping")):
            out = self.anilist._attach_kitsu_ids([(movie, 999999)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kitsu_id, "")

    def test_attach_kitsu_ids_fills_in_the_mapping(self):
        movie = _movie("Frieren", kitsu_id="")

        class FakeMapping:
            kitsu_id = "46474"

        with patch("actions.anime.mappings.mappings", return_value=FakeMapping()):
            out = self.anilist._attach_kitsu_ids([(movie, 154587)])
        self.assertEqual(out[0].kitsu_id, "46474")


if __name__ == "__main__":
    unittest.main()
