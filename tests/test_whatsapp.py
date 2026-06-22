import unittest
from unittest.mock import Mock, patch

from actions import whatsapp


class WhatsAppIntentTests(unittest.TestCase):
    def test_extracts_message_from_dile_command(self):
        contact, body = whatsapp.normalize_send_request(
            "Rafa",
            "Dile a Rafa que llego en diez minutos",
        )
        self.assertEqual(contact, "Rafa")
        self.assertEqual(body, "llego en diez minutos")

    def test_extracts_contact_when_model_puts_full_command_in_body(self):
        contact, body = whatsapp.normalize_send_request(
            "",
            "Mándale un mensaje a Mamá: compra pan",
        )
        self.assertEqual(contact, "Mamá")
        self.assertEqual(body, "compra pan")

    def test_preserves_normal_message(self):
        contact, body = whatsapp.normalize_send_request(
            "Rafa",
            "Dile a Juan que mañana no puedo",
        )
        self.assertEqual(contact, "Rafa")
        self.assertEqual(body, "Dile a Juan que mañana no puedo")


class WhatsAppBridgeClientTests(unittest.TestCase):
    @patch("actions.whatsapp.requests.get")
    def test_unready_chat_list_raises_in_strict_mode(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "ready": False, "chats": []}
        get.return_value = response

        with self.assertRaises(whatsapp.WhatsAppUnavailable):
            whatsapp.list_recent_chats(raise_on_unready=True)

    @patch("actions.whatsapp.requests.post")
    def test_send_rejects_bridge_level_failure(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": False, "error": "send failed"}
        post.return_value = response

        with self.assertRaises(whatsapp.WhatsAppError):
            whatsapp.send_whatsapp("123@c.us", "hola")

    @patch("actions.whatsapp.requests.post")
    def test_reads_message_acknowledgements(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "acks": {"msg-1": 3, "msg-2": 2}}
        post.return_value = response

        self.assertEqual(
            whatsapp.get_message_acks(["msg-1", "msg-2"]),
            {"msg-1": 3, "msg-2": 2},
        )

    @patch("actions.whatsapp.requests.get")
    def test_conversation_reports_unready_bridge_in_strict_mode(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "ready": False, "messages": []}
        get.return_value = response

        with self.assertRaises(whatsapp.WhatsAppUnavailable):
            whatsapp.get_conversation("123@c.us", strict=True)

    @patch("actions.whatsapp.requests.get")
    def test_contact_resolution_preserves_bridge_error_detail(self, get):
        response = Mock()
        response.ok = False
        response.status_code = 500
        response.json.return_value = {"ok": False, "error": "contact store failed"}
        get.return_value = response

        with self.assertRaisesRegex(whatsapp.WhatsAppUnavailable, "contact store failed"):
            whatsapp.resolve_contact("Mama", strict=True)


if __name__ == "__main__":
    unittest.main()
