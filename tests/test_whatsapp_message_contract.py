import unittest


class WhatsAppMessageContractTests(unittest.TestCase):
    def test_normalize_preserves_complete_live_message(self):
        from actions.whatsapp_contract import normalize_message

        raw = {
            "id": "msg-1",
            "clientRequestId": "local-1",
            "from": "111@c.us",
            "to": "me@c.us",
            "chatId": "111@c.us",
            "author": "222@lid",
            "authorName": "Ana",
            "senderName": "Ana",
            "body": "hola",
            "type": "chat",
            "fromMe": False,
            "direction": "in",
            "timestamp": 1_700_000_001_000,
            "waTs": 1_700_000_000_000,
            "hasMedia": False,
            "mediaUrl": None,
            "mentionedIds": ["333@c.us"],
            "mentions": {"333@c.us": "Luis"},
            "quoted": {
                "id": "msg-0",
                "body": "anterior",
                "fromMe": True,
                "senderName": None,
                "type": "chat",
            },
            "ack": 2,
            "edited": True,
            "translation": "hello",
            "transcription": "",
        }

        message = normalize_message(raw)

        self.assertEqual(message["chatId"], "111@c.us")
        self.assertEqual(message["clientRequestId"], "local-1")
        self.assertEqual(message["sentAtMs"], 1_700_000_000_000)
        self.assertEqual(message["observedAtMs"], 1_700_000_001_000)
        self.assertEqual(message["waTs"], message["sentAtMs"])
        self.assertEqual(message["timestamp"], message["observedAtMs"])
        self.assertEqual(message["quoted"]["id"], "msg-0")
        self.assertEqual(message["mentions"], {"333@c.us": "Luis"})
        self.assertTrue(message["edited"])
        self.assertEqual(message["translation"], "hello")

    def test_normalize_derives_direction_chat_and_millisecond_timestamps(self):
        from actions.whatsapp_contract import normalize_message

        incoming = normalize_message({
            "id": "msg-2",
            "from": "123@c.us",
            "body": "hola",
            "timestamp": 1_700_000_000,
        })
        outgoing = normalize_message({
            "id": "msg-3",
            "from": "me@c.us",
            "to": "456@c.us",
            "body": "hola",
            "timestamp": 1_700_000_000,
            "fromMe": True,
        })

        self.assertEqual(incoming["chatId"], "123@c.us")
        self.assertEqual(incoming["direction"], "in")
        self.assertEqual(incoming["timestamp"], 1_700_000_000_000)
        self.assertEqual(outgoing["chatId"], "456@c.us")
        self.assertEqual(outgoing["direction"], "out")

    def test_unknown_message_type_has_safe_fallback_without_losing_raw_type(self):
        from actions.whatsapp_contract import normalize_message

        message = normalize_message({"id": "msg-4", "type": "future_type"})

        self.assertEqual(message["type"], "unknown")
        self.assertEqual(message["rawType"], "future_type")

    def test_remap_message_key_moves_ui_references_to_server_id(self):
        from actions.whatsapp_contract import remap_message_key

        marker = object()
        mapping = {"local-1": marker}

        self.assertIs(remap_message_key(mapping, "local-1", "server-1"), marker)
        self.assertNotIn("local-1", mapping)
        self.assertIs(mapping["server-1"], marker)

    def test_apply_revoke_removes_media_and_replaces_body(self):
        from actions.whatsapp_contract import apply_message_update

        message = apply_message_update(
            {"id": "msg-5", "body": "secreto", "type": "image", "hasMedia": True, "mediaUrl": "/media?id=msg-5"},
            {"id": "msg-5", "event": "revoke", "type": "revoked"},
        )

        self.assertEqual(message["body"], "[mensaje eliminado]")
        self.assertTrue(message["revoked"])
        self.assertFalse(message["hasMedia"])
        self.assertIsNone(message["mediaUrl"])

    def test_reaction_updates_are_keyed_by_sender_and_can_be_removed(self):
        from actions.whatsapp_contract import apply_message_update

        original = {"id": "msg-6", "body": "hola", "reactions": [{"senderId": "ana", "reaction": "👍"}]}
        changed = apply_message_update(original, {
            "event": "reaction", "reaction": {"senderId": "ana", "reaction": "❤️"},
        })
        removed = apply_message_update(changed, {
            "event": "reaction", "reaction": {"senderId": "ana", "reaction": "", "removed": True},
        })

        self.assertEqual(changed["reactions"], [{"senderId": "ana", "reaction": "❤️", "removed": False}])
        self.assertEqual(removed["reactions"], [])

    def test_local_message_ids_do_not_collide(self):
        from actions.whatsapp_contract import new_local_message_id

        ids = {new_local_message_id() for _ in range(500)}

        self.assertEqual(len(ids), 500)
        self.assertTrue(all(value.startswith("local-") for value in ids))

    def test_timeout_stays_unconfirmed_until_bridge_echo_arrives(self):
        from actions.whatsapp_contract import apply_optimistic_send_result

        local = {
            "id": "local-1", "body": "hola", "fromMe": True, "_pending": True,
            "_retry": {"kind": "text"},
        }
        uncertain = apply_optimistic_send_result(local, {"pendingConfirmation": True})
        confirmed = apply_optimistic_send_result(uncertain, {"id": "wa-1", "ack": 1})

        self.assertTrue(uncertain["_confirming"])
        self.assertNotIn("_pending", uncertain)
        self.assertEqual(confirmed["id"], "wa-1")
        self.assertNotIn("_confirming", confirmed)
        self.assertNotIn("_retry", confirmed)

    def test_send_error_is_distinct_from_unconfirmed_timeout(self):
        from actions.whatsapp_contract import apply_optimistic_send_result

        failed = apply_optimistic_send_result(
            {"id": "local-2", "fromMe": True, "_pending": True}, {}, "sin conexión",
        )

        self.assertTrue(failed["_failed"])
        self.assertEqual(failed["_sendError"], "sin conexión")

    def test_bridge_echo_matches_request_id_even_after_a_long_timeout(self):
        from actions.whatsapp_contract import is_optimistic_echo

        local = {
            "id": "local-7", "fromMe": True, "body": "hola",
            "timestamp": 1000, "_confirming": True,
        }

        self.assertTrue(is_optimistic_echo(local, {
            "id": "wa-7", "fromMe": True, "body": "hola",
            "timestamp": 999_999, "clientRequestId": "local-7",
        }))
        self.assertFalse(is_optimistic_echo(local, {
            "id": "wa-8", "fromMe": True, "body": "hola",
            "timestamp": 999_999, "clientRequestId": "local-other",
        }))


if __name__ == "__main__":
    unittest.main()
