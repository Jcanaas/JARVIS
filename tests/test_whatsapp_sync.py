"""Polling cycle of WhatsAppManager: dedup, cursor, updates and rehydration.

These drive ``_loop`` directly with a fake ``fetch_messages`` so the ordering
rules that only exist inside that loop (session dedup, cursor advance, update
events routed away from new-message listeners, reconnect floods) are covered
without a bridge.
"""
import time
import unittest
from unittest.mock import patch

from actions.whatsapp_manager import WhatsAppManager


def run_cycles(manager: WhatsAppManager, batches: list[list[dict]]) -> list[int]:
    """Run ``_loop`` once per batch and return the cursor used for each fetch."""
    pending_batches = list(batches)
    cursors: list[int] = []

    def fake_fetch(since):
        cursors.append(since)
        if not pending_batches:
            manager._stop.set()
            return []
        return pending_batches.pop(0)

    with patch("actions.whatsapp_manager.fetch_messages", side_effect=fake_fetch), \
            patch("actions.whatsapp_manager.is_ignored_message", return_value=False), \
            patch("actions.whatsapp_manager._save_pending"):
        manager._loop()
    return cursors


def build_manager(pending: dict | None = None, **kwargs) -> WhatsAppManager:
    """Build a manager whose pending queue starts from `pending`, never disk.

    ``WhatsAppManager.__init__`` calls ``_load_pending()``, which reads the
    REAL store at ``%LOCALAPPDATA%/Jarvis/whatsapp_pending.json``. With the
    desktop app running, a message awaiting a reply lands there mid-run and
    every ``list_pending() == []`` assertion below fails — intermittently, and
    only on the developer's own machine. Seeding it explicitly keeps these
    tests about the polling loop instead of about the user's inbox.
    """
    defaults = dict(
        start_thread=False,
        poll_interval=0.0,
        # None short-circuits the unread reconciliation, which would otherwise
        # hit the bridge over HTTP from inside the loop.
        unread_fetcher=lambda: None,
        startup_grace_secs=0.0,
        auto_translate=False,
        auto_transcribe=False,
    )
    defaults.update(kwargs)
    with patch("actions.whatsapp_manager._load_pending", return_value=dict(pending or {})):
        return WhatsAppManager(**defaults)


def message(**overrides) -> dict:
    now = int(time.time() * 1000)
    base = {
        "id": "msg-1",
        "from": "chat@c.us",
        "chatId": "chat@c.us",
        "body": "hola",
        "type": "chat",
        "fromMe": False,
        "timestamp": now,
        "waTs": now,
    }
    base.update(overrides)
    return base


class PollingCycleTests(unittest.TestCase):
    def test_repeated_ids_are_delivered_once_across_polls(self):
        manager = build_manager()
        seen = []
        manager.add_message_listener(seen.append)

        run_cycles(manager, [
            [message(id="a"), message(id="a")],
            [message(id="a"), message(id="b")],
        ])

        self.assertEqual([entry["id"] for entry in seen], ["a", "b"])

    def test_cursor_advances_only_to_data_actually_received(self):
        manager = build_manager()
        manager._last_ts = 1_000

        cursors = run_cycles(manager, [[
            message(id="a", timestamp=1_100),
            message(id="b", timestamp=1_300),
            message(id="c", timestamp=1_200),
        ]])

        # Never "now": a message arriving between the HTTP response and the
        # assignment would fall below the cursor and be lost forever.
        self.assertEqual(cursors[0], 1_000)
        self.assertEqual(cursors[1], 1_300)
        self.assertEqual(manager._last_ts, 1_300)

    def test_out_of_order_late_message_is_delivered_but_flagged_backlog(self):
        manager = build_manager(backlog_age_secs=90.0)
        seen = []
        manager.add_message_listener(seen.append)
        old = int(time.time() * 1000) - 10 * 60 * 1000

        run_cycles(manager, [[message(id="late", timestamp=old, waTs=old)]])

        self.assertEqual([entry["id"] for entry in seen], ["late"])
        # Visible in the chat UI, but never announced and never pending.
        self.assertTrue(seen[0]["is_backlog"])
        self.assertEqual(manager.list_pending(), [])

    def test_reconnect_flood_is_marked_backlog_and_leaves_no_pending(self):
        manager = build_manager(flood_threshold=2)
        seen = []
        manager.add_message_listener(seen.append)

        run_cycles(manager, [[message(id=f"m{index}") for index in range(4)]])

        self.assertEqual(len(seen), 4)
        self.assertTrue(all(entry["is_backlog"] for entry in seen))
        self.assertEqual(manager.list_pending(), [])

    def test_messages_sent_from_the_phone_reach_the_ui_without_becoming_pending(self):
        manager = build_manager()
        seen = []
        manager.add_message_listener(seen.append)

        run_cycles(manager, [[message(id="mine", fromMe=True, to="chat@c.us")]])

        self.assertEqual([entry["id"] for entry in seen], ["mine"])
        self.assertEqual(manager.list_pending(), [])


class UpdateEventRoutingTests(unittest.TestCase):
    def test_edit_revoke_and_reaction_go_to_update_listeners_only(self):
        manager = build_manager()
        messages, updates = [], []
        manager.add_message_listener(messages.append)
        manager.add_edit_listener(updates.append)

        run_cycles(manager, [[
            message(id="a", body="hola"),
            message(id="a", event="edit", type="edit", body="hola!", timestamp=2),
            message(id="a", event="reaction", type="reaction",
                    reaction={"senderId": "ana@c.us", "reaction": "👍"}, timestamp=3),
            message(id="a", event="revoke", type="revoked", timestamp=4),
        ]])

        self.assertEqual([entry["id"] for entry in messages], ["a"])
        self.assertEqual([entry["event"] for entry in updates], ["edit", "reaction", "revoke"])
        # The revoke also clears the pending entry the original message created.
        self.assertEqual(manager.list_pending(), [])

    def test_identical_update_event_replayed_by_the_bridge_is_ignored(self):
        manager = build_manager()
        updates = []
        manager.add_edit_listener(updates.append)
        edit = message(id="a", event="edit", type="edit", body="hola!", timestamp=2)

        run_cycles(manager, [[edit], [dict(edit)]])

        self.assertEqual(len(updates), 1)

    def test_update_events_carry_the_chat_id_the_ui_indexes_by(self):
        manager = build_manager()
        updates = []
        manager.add_edit_listener(updates.append)

        run_cycles(manager, [[message(
            id="a", event="reaction", type="reaction", chatId="chat@c.us",
            reaction={"senderId": "ana@c.us", "reaction": "❤️"}, timestamp=5,
        )]])

        self.assertEqual(updates[0]["from"], "chat@c.us")
        self.assertEqual(updates[0]["chatId"], "chat@c.us")


class RehydrationTests(unittest.TestCase):
    def test_restart_keeps_recent_pending_and_drops_day_old_entries(self):
        now = int(time.time() * 1000)
        stored = {
            "recent": {
                "id": "recent", "chatId": "chat@c.us", "from": "chat@c.us",
                "fromMe": False, "is_backlog": False, "event": "message",
                "type": "chat", "timestamp": now - 60_000,
            },
            "ancient": {
                "id": "ancient", "chatId": "chat@c.us", "from": "chat@c.us",
                "fromMe": False, "is_backlog": False, "event": "message",
                "type": "chat", "timestamp": now - 48 * 3600 * 1000,
            },
        }

        manager = build_manager(pending=stored)

        self.assertEqual([entry["id"] for entry in manager.list_pending()], ["recent"])

    def test_restored_message_is_not_re_announced_after_restart(self):
        now = int(time.time() * 1000)
        stored = {
            "recent": {
                "id": "recent", "chatId": "chat@c.us", "from": "chat@c.us",
                "fromMe": False, "is_backlog": False, "event": "message",
                "type": "chat", "timestamp": now - 60_000,
            },
        }
        manager = build_manager(pending=stored)
        announced = []
        manager.on_new_message = announced.append

        # The bridge buffer replays the same message after a reconnect: the
        # pending store already has it, but the session dedup set does not, so
        # it is delivered once — and only once.
        run_cycles(manager, [
            [message(id="recent", timestamp=now - 60_000, waTs=now - 60_000)],
            [message(id="recent", timestamp=now - 60_000, waTs=now - 60_000)],
        ])

        self.assertEqual([entry["id"] for entry in announced], ["recent"])


if __name__ == "__main__":
    unittest.main()
