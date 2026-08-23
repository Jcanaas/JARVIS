import time
import unittest
from unittest.mock import Mock, patch

from actions.whatsapp_manager import (
    WhatsAppManager,
    _classify_backlog,
    _apply_pending_update,
    _event_seen_key,
    _is_actionable_pending,
    _pending_entry_from_message,
    _reconcile_pending_entries,
)


class WhatsAppPendingContractTests(unittest.TestCase):
    def test_startup_guard_keeps_genuinely_new_messages_actionable(self):
        self.assertFalse(_classify_backlog(1, in_startup_window=True, is_flood=False))
        self.assertTrue(_classify_backlog(20, in_startup_window=True, is_flood=False))
        self.assertTrue(_classify_backlog(1, in_startup_window=True, is_flood=True))
        self.assertTrue(_classify_backlog(120, in_startup_window=False, is_flood=False))

    def test_only_fresh_incoming_messages_are_actionable_pending(self):
        fresh = {"id": "fresh", "from": "123@c.us", "fromMe": False, "is_backlog": False}
        backlog = {"id": "old", "from": "123@c.us", "fromMe": False, "is_backlog": True}
        outgoing = {"id": "mine", "from": "123@c.us", "fromMe": True, "is_backlog": False}
        edit = {"id": "edit", "from": "123@c.us", "fromMe": False, "event": "edit"}

        self.assertTrue(_is_actionable_pending(fresh))
        self.assertFalse(_is_actionable_pending(backlog))
        self.assertFalse(_is_actionable_pending(outgoing))
        self.assertFalse(_is_actionable_pending(edit))

    def test_list_pending_hides_startup_backlog_and_outgoing_messages(self):
        manager = WhatsAppManager(start_thread=False)
        manager._pending = {
            "fresh": {"id": "fresh", "from": "123@c.us", "fromMe": False, "is_backlog": False},
            "old": {"id": "old", "from": "123@c.us", "fromMe": False, "is_backlog": True},
            "mine": {"id": "mine", "from": "123@c.us", "fromMe": True, "is_backlog": False},
        }

        self.assertEqual([entry["id"] for entry in manager.list_pending()], ["fresh"])

    def test_unread_reconciliation_keeps_only_newest_unread_per_chat(self):
        pending = {
            "a": {"id": "a", "chatId": "one@c.us", "from": "one@c.us", "timestamp": 100},
            "b": {"id": "b", "chatId": "one@c.us", "from": "one@c.us", "timestamp": 200},
            "c": {"id": "c", "chatId": "two@c.us", "from": "two@c.us", "timestamp": 300},
        }

        reconciled = _reconcile_pending_entries(pending, {"one@c.us": 1, "two@c.us": 0})

        self.assertEqual(set(reconciled), {"b"})

    @patch("actions.whatsapp_manager._save_pending")
    def test_manager_removes_pending_after_cross_device_read(self, save):
        manager = WhatsAppManager(
            start_thread=False,
            unread_fetcher=lambda: {"one@c.us": 0},
        )
        manager._pending = {
            "a": {
                "id": "a", "chatId": "one@c.us", "from": "one@c.us",
                "fromMe": False, "is_backlog": False, "timestamp": 100,
            },
        }

        self.assertTrue(manager._sync_pending_unread(force=True))
        self.assertEqual(manager.list_pending(), [])
        save.assert_called_once_with({})

    def test_edit_dedup_key_includes_event_timestamp(self):
        first = _event_seen_key({"type": "edit", "timestamp": 100}, "msg-1")
        repeated = _event_seen_key({"type": "edit", "timestamp": 100}, "msg-1")
        later = _event_seen_key({"type": "edit", "timestamp": 101}, "msg-1")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, later)

    def test_revoke_event_removes_message_from_pending(self):
        pending = {"msg-1": {"id": "msg-1", "body": "hola"}}

        self.assertTrue(_apply_pending_update(pending, {"id": "msg-1", "event": "revoke"}))
        self.assertEqual(pending, {})

    def test_reaction_event_updates_pending_message_without_making_new_pending(self):
        pending = {"msg-1": {"id": "msg-1", "body": "hola", "reactions": []}}

        changed = _apply_pending_update(pending, {
            "id": "msg-1", "event": "reaction",
            "reaction": {"senderId": "ana", "reaction": "👍"},
        })

        self.assertTrue(changed)
        self.assertEqual(pending["msg-1"]["reactions"][0]["reaction"], "👍")
        self.assertEqual(len(pending), 1)

    def test_pending_entry_preserves_canonical_message_fields(self):
        entry = _pending_entry_from_message({
            "id": "msg-1",
            "from": "123@c.us",
            "chatId": "123@c.us",
            "authorName": "Ana",
            "body": "hola",
            "type": "chat",
            "timestamp": 1_700_000_001_000,
            "waTs": 1_700_000_000_000,
            "quoted": {"id": "msg-0", "body": "antes", "type": "chat"},
            "edited": True,
            "translation": "hello",
        }, is_backlog=False)

        # ``from`` remains the legacy pending-chat alias until all callers use
        # chatId; sourceFrom keeps the actual wire sender without data loss.
        self.assertEqual(entry["from"], "123@c.us")
        self.assertEqual(entry["sourceFrom"], "123@c.us")
        self.assertEqual(entry["chatId"], "123@c.us")
        self.assertEqual(entry["sentAtMs"], 1_700_000_000_000)
        self.assertEqual(entry["quoted"]["id"], "msg-0")
        self.assertTrue(entry["edited"])
        self.assertEqual(entry["translation"], "hello")


class WhatsAppCloudPrivacyTests(unittest.TestCase):
    @patch("actions.app_settings.get", return_value=False)
    def test_automatic_cloud_processing_is_opt_in(self, _get):
        manager = WhatsAppManager(start_thread=False)

        self.assertFalse(manager._should_auto_translate({"type": "chat", "body": "hello"}))
        self.assertFalse(manager._should_auto_transcribe({"type": "ptt", "hasMedia": True}))

    def test_explicit_opt_in_only_processes_supported_content(self):
        manager = WhatsAppManager(
            start_thread=False,
            auto_translate=True,
            auto_transcribe=True,
        )

        self.assertTrue(manager._should_auto_translate({"type": "chat", "body": "hello"}))
        self.assertFalse(manager._should_auto_translate({"type": "image", "body": "[imagen]"}))
        self.assertTrue(manager._should_auto_transcribe({"type": "audio", "hasMedia": True}))
        self.assertFalse(manager._should_auto_transcribe({"type": "video", "hasMedia": True}))


class WhatsAppAutoReplyTests(unittest.TestCase):
    @patch("actions.whatsapp_manager.resolve_contact", return_value="123@c.us")
    def test_startup_backlog_never_triggers_auto_reply(self, _resolve):
        sent = Mock()
        generated = Mock(return_value="No debe enviarse")
        manager = WhatsAppManager(
            start_thread=False,
            reply_generator=generated,
            message_sender=sent,
        )
        manager.start_auto_reply("Rafa", 10)

        manager._schedule_auto_reply({
            "id": "old", "from": "123@c.us", "body": "mensaje antiguo",
            "fromMe": False, "is_backlog": True,
        })

        generated.assert_not_called()
        sent.assert_not_called()

    @patch("actions.whatsapp_manager.resolve_contact", return_value="123@c.us")
    def test_starts_and_stops_temporary_session(self, _resolve):
        manager = WhatsAppManager(start_thread=False)

        session = manager.start_auto_reply("Rafa", 10)

        self.assertEqual(session["chat_id"], "123@c.us")
        self.assertEqual(len(manager.list_auto_replies()), 1)
        self.assertEqual(manager.stop_auto_reply("123@c.us"), 1)
        self.assertEqual(manager.list_auto_replies(), [])

    @patch("actions.whatsapp_manager.resolve_contact", return_value="123@c.us")
    def test_incoming_message_is_generated_and_sent(self, _resolve):
        sent = Mock(return_value={"ok": True})
        generated = Mock(return_value="Respuesta propuesta")
        manager = WhatsAppManager(
            start_thread=False,
            reply_generator=generated,
            message_sender=sent,
        )
        manager.start_auto_reply("Rafa", 10)

        manager._schedule_auto_reply(
            {"id": "message-1", "from": "123@c.us", "body": "¿Vienes?"}
        )
        deadline = time.time() + 2
        while manager._auto_reply_busy and time.time() < deadline:
            time.sleep(0.01)

        generated.assert_called_once_with("123@c.us", "¿Vienes?")
        sent.assert_called_once_with(to="123@c.us", body="Respuesta propuesta")

    @patch("actions.whatsapp_manager.resolve_contact", return_value="group@g.us")
    def test_rejects_group_auto_reply(self, _resolve):
        manager = WhatsAppManager(start_thread=False)

        with self.assertRaises(ValueError):
            manager.start_auto_reply("Grupo", 10)

    @patch("actions.whatsapp_manager.resolve_contact", return_value="123@c.us")
    def test_queues_consecutive_messages(self, _resolve):
        sent = Mock(return_value={"ok": True})
        generated = Mock(side_effect=["Primera", "Segunda"])
        manager = WhatsAppManager(
            start_thread=False,
            reply_generator=generated,
            message_sender=sent,
        )
        manager.start_auto_reply("Rafa", 10)

        manager._schedule_auto_reply({"from": "123@c.us", "body": "Uno"})
        manager._schedule_auto_reply({"from": "123@c.us", "body": "Dos"})
        deadline = time.time() + 2
        while manager._auto_reply_busy and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(generated.call_count, 2)
        self.assertEqual(sent.call_count, 2)

    @patch("actions.whatsapp_manager.resolve_contact", return_value="123@c.us")
    def test_expired_session_does_not_reply(self, _resolve):
        sent = Mock()
        manager = WhatsAppManager(
            start_thread=False,
            reply_generator=Mock(return_value="No debe enviarse"),
            message_sender=sent,
        )
        manager.start_auto_reply("Rafa", 10)
        manager._auto_reply_sessions["123@c.us"]["expires_at"] = time.time() - 1

        manager._schedule_auto_reply({"from": "123@c.us", "body": "Hola"})

        sent.assert_not_called()
        self.assertEqual(manager.list_auto_replies(), [])


if __name__ == "__main__":
    unittest.main()
