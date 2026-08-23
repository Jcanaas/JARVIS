"""Tests for the Google Calendar helpers the calendar UIs depend on.

`ui/panels/calendar.py` called `list_events_range` and `update_event`, and
passed `attendees=` to `create_event` — none of which existed. Every one of
those calls sits inside a broad `except Exception`, so the desktop month view
silently came up empty and editing an event silently failed instead of
raising. These tests pin the contract that panel relies on.
"""

import unittest
from unittest.mock import MagicMock, patch

from actions import google_calendar as gcal


def _fake_service(items=None, single=None):
    """Stubs the chain svc.events().list(...).execute()."""
    events = MagicMock()
    if items is not None:
        events.list.return_value.execute.return_value = {"items": items}
    if single is not None:
        events.insert.return_value.execute.return_value = single
        events.patch.return_value.execute.return_value = single
    svc = MagicMock()
    svc.events.return_value = events
    return svc, events


class NormalizeEventTests(unittest.TestCase):
    def test_timed_event_exposes_start_end_and_is_not_all_day(self):
        out = gcal._normalize_event({
            "id": "e1",
            "summary": "Reunión",
            "start": {"dateTime": "2026-08-16T09:00:00+02:00"},
            "end": {"dateTime": "2026-08-16T10:00:00+02:00"},
            "location": "Sala 2",
        })
        self.assertEqual(out["id"], "e1")
        self.assertEqual(out["start"], "2026-08-16T09:00:00+02:00")
        self.assertEqual(out["end"], "2026-08-16T10:00:00+02:00")
        self.assertFalse(out["all_day"])
        self.assertEqual(out["location"], "Sala 2")

    def test_all_day_event_is_flagged(self):
        """Google sends `date` instead of `dateTime`; without the flag the UI
        can't tell an all-day event from one starting at midnight."""
        out = gcal._normalize_event({
            "id": "e2",
            "summary": "Vacaciones",
            "start": {"date": "2026-08-20"},
            "end": {"date": "2026-08-21"},
        })
        self.assertTrue(out["all_day"])
        self.assertEqual(out["start"], "2026-08-20")

    def test_missing_fields_get_safe_defaults(self):
        out = gcal._normalize_event({})
        self.assertEqual(out["summary"], "(sin título)")
        self.assertEqual(out["attendees"], [])
        self.assertIsNone(out["start"])


class ListEventsRangeTests(unittest.TestCase):
    def test_queries_the_requested_window(self):
        svc, events = _fake_service(items=[])
        with patch.object(gcal, "_get_service", return_value=svc):
            gcal.list_events_range("2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z")

        kwargs = events.list.call_args.kwargs
        self.assertEqual(kwargs["timeMin"], "2026-08-01T00:00:00Z")
        self.assertEqual(kwargs["timeMax"], "2026-09-01T00:00:00Z")
        # Recurring events must be expanded, or a weekly meeting shows up once.
        self.assertTrue(kwargs["singleEvents"])

    def test_returns_normalized_events(self):
        svc, _ = _fake_service(items=[
            {"id": "a", "summary": "Uno", "start": {"dateTime": "2026-08-16T09:00:00+02:00"}},
        ])
        with patch.object(gcal, "_get_service", return_value=svc):
            out = gcal.list_events_range("x", "y")

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["summary"], "Uno")
        self.assertIn("all_day", out[0])


class CreateEventAttendeeTests(unittest.TestCase):
    def test_accepts_plain_email_strings(self):
        """The desktop dialog hands over a list of e-mail strings, but the API
        wants [{'email': ...}]."""
        svc, events = _fake_service(single={"id": "n1", "summary": "X", "htmlLink": "l"})
        with patch.object(gcal, "_get_service", return_value=svc):
            gcal.create_event("X", "2026-08-16T09:00:00", attendees=["a@b.com", "c@d.com"])

        body = events.insert.call_args.kwargs["body"]
        self.assertEqual(body["attendees"], [{"email": "a@b.com"}, {"email": "c@d.com"}])

    def test_omits_attendees_key_when_none_given(self):
        svc, events = _fake_service(single={"id": "n1", "summary": "X", "htmlLink": "l"})
        with patch.object(gcal, "_get_service", return_value=svc):
            gcal.create_event("X", "2026-08-16T09:00:00")

        self.assertNotIn("attendees", events.insert.call_args.kwargs["body"])


class UpdateEventTests(unittest.TestCase):
    def test_only_patches_the_fields_provided(self):
        svc, events = _fake_service(single={"id": "e1", "summary": "Nuevo", "start": {}, "end": {}})
        with patch.object(gcal, "_get_service", return_value=svc):
            gcal.update_event("e1", summary="Nuevo")

        body = events.patch.call_args.kwargs["body"]
        self.assertEqual(body, {"summary": "Nuevo"})
        self.assertEqual(events.patch.call_args.kwargs["eventId"], "e1")

    def test_empty_attendee_list_clears_guests_but_none_leaves_them(self):
        svc, events = _fake_service(single={"id": "e1", "summary": "S", "start": {}, "end": {}})
        with patch.object(gcal, "_get_service", return_value=svc):
            gcal.update_event("e1", attendees=[])
            cleared = events.patch.call_args.kwargs["body"]
            gcal.update_event("e1", summary="S")
            untouched = events.patch.call_args.kwargs["body"]

        self.assertEqual(cleared["attendees"], [])
        self.assertNotIn("attendees", untouched)

    def test_returns_a_normalized_event(self):
        svc, _ = _fake_service(single={
            "id": "e1", "summary": "S",
            "start": {"dateTime": "2026-08-16T09:00:00+02:00"},
            "end": {"dateTime": "2026-08-16T10:00:00+02:00"},
        })
        with patch.object(gcal, "_get_service", return_value=svc):
            out = gcal.update_event("e1", summary="S")

        self.assertFalse(out["all_day"])
        self.assertEqual(out["summary"], "S")


class DesktopPanelContractTests(unittest.TestCase):
    def test_every_function_the_calendar_panel_calls_exists(self):
        """ui/panels/calendar.py resolves these lazily inside try/except, so a
        missing one degrades to an empty screen rather than an error."""
        for name in ("list_events_range", "search_events", "create_event", "update_event", "delete_event"):
            self.assertTrue(hasattr(gcal, name), f"google_calendar.{name} is missing")

    def test_create_and_update_accept_the_keywords_the_panel_passes(self):
        import inspect

        create = set(inspect.signature(gcal.create_event).parameters)
        update = set(inspect.signature(gcal.update_event).parameters)
        for kw in ("summary", "start", "end", "description", "location", "attendees"):
            self.assertIn(kw, create, f"create_event lacks '{kw}'")
            self.assertIn(kw, update, f"update_event lacks '{kw}'")


if __name__ == "__main__":
    unittest.main()
