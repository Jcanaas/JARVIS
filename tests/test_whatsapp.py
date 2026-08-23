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
    @patch("actions.whatsapp.requests.post")
    def test_send_passes_client_request_id_for_idempotency(self, post):
        from actions.whatsapp import send_whatsapp

        post.return_value.ok = True
        post.return_value.content = b"{}"
        post.return_value.json.return_value = {"ok": True, "id": "wa-1"}
        post.return_value.raise_for_status.return_value = None

        send_whatsapp("123@c.us", "hola", client_request_id="local-1")

        self.assertEqual(post.call_args.kwargs["json"]["clientRequestId"], "local-1")

    @patch("actions.whatsapp.requests.post")
    def test_send_preserves_bridge_http_error_detail(self, post):
        from actions.whatsapp import WhatsAppError, send_whatsapp

        post.return_value.ok = False
        post.return_value.content = b"error"
        post.return_value.json.return_value = {
            "ok": False, "error": "El número no está registrado en WhatsApp.",
        }

        with self.assertRaisesRegex(WhatsAppError, "no está registrado"):
            send_whatsapp("123@c.us", "hola")

    @patch("actions.whatsapp.requests.post")
    @patch("os.path.isfile", return_value=True)
    def test_media_send_passes_same_idempotency_key(self, _isfile, post):
        from actions.whatsapp import send_whatsapp_media

        post.return_value.ok = True
        post.return_value.content = b"{}"
        post.return_value.json.return_value = {"ok": True, "id": "wa-2"}
        post.return_value.raise_for_status.return_value = None

        send_whatsapp_media(
            "123@c.us", "image.png", client_request_id="local-media-1",
        )

        self.assertEqual(
            post.call_args.kwargs["json"]["clientRequestId"], "local-media-1",
        )

    @patch("actions.whatsapp.requests.get")
    def test_reads_normalized_unread_counts(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "ready": True,
            "unread": {"one@c.us": 2, "two@c.us": "0", "bad@c.us": "x"},
        }
        get.return_value = response

        self.assertEqual(
            whatsapp.get_unread_counts(),
            {"one@c.us": 2, "two@c.us": 0, "bad@c.us": 0},
        )

    @patch("actions.whatsapp.requests.get")
    def test_fetch_messages_returns_canonical_messages(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "messages": [{
                "id": "msg-1",
                "from": "123@c.us",
                "body": "hola",
                "type": "chat",
                "timestamp": 1_700_000_000,
                "waTs": 1_699_999_999,
                "quoted": {"id": "msg-0", "body": "antes", "type": "chat"},
                "edited": True,
            }]
        }
        get.return_value = response

        messages = whatsapp.fetch_messages()

        self.assertEqual(messages[0]["chatId"], "123@c.us")
        self.assertEqual(messages[0]["timestamp"], 1_700_000_000_000)
        self.assertEqual(messages[0]["waTs"], 1_699_999_999_000)
        self.assertEqual(messages[0]["quoted"]["id"], "msg-0")
        self.assertTrue(messages[0]["edited"])

    @patch("actions.whatsapp.requests.get")
    def test_conversation_uses_same_contract_as_live_messages(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "ready": True,
            "messages": [{
                "id": "history-1",
                "from": "123@c.us",
                "body": "histórico",
                "type": "chat",
                "timestamp": 1_700_000_000,
                "edited": True,
                "authorName": "Ana",
            }],
        }
        get.return_value = response

        messages = whatsapp.get_conversation("123@c.us")

        self.assertEqual(messages[0]["chatId"], "123@c.us")
        self.assertEqual(messages[0]["sentAtMs"], 1_700_000_000_000)
        self.assertEqual(messages[0]["authorName"], "Ana")
        self.assertTrue(messages[0]["edited"])

    @patch("actions.whatsapp.requests.get")
    def test_conversation_sorts_by_whatsapp_send_time_not_poll_arrival(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "ready": True,
            "messages": [
                {
                    "id": "newer",
                    "from": "123@c.us",
                    "body": "segundo",
                    "timestamp": 1_700_000_000_100,
                    "waTs": 1_600_000_000_200,
                },
                {
                    "id": "older",
                    "from": "123@c.us",
                    "body": "primero",
                    "timestamp": 1_700_000_000_200,
                    "waTs": 1_600_000_000_100,
                },
            ],
        }
        get.return_value = response

        messages = whatsapp.get_conversation("123@c.us")

        self.assertEqual([message["id"] for message in messages], ["older", "newer"])

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
