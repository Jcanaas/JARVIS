import time
import unittest
from unittest.mock import Mock, patch

from actions.whatsapp_manager import WhatsAppManager


class WhatsAppAutoReplyTests(unittest.TestCase):
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
