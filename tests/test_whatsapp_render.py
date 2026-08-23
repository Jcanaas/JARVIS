"""Bubble rendering for the WhatsApp message types with structured payloads.

The window is built offscreen and never shown: ``_add_message_bubble`` only
needs the widget tree, so no bridge, manager or network access is involved.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from actions.whatsapp_contract import normalize_message


VCARD = "BEGIN:VCARD\r\nFN:Ana Pérez\r\nTEL;type=CELL:+34 600 111 222\r\nEND:VCARD"


def _bubble_texts(bubble) -> list[str]:
    """Every non-empty label/button caption inside one rendered bubble."""
    texts = []
    for child in bubble.findChildren(object):
        getter = getattr(child, "text", None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            texts.append(value)
    return texts


class WhatsAppRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from actions.whatsapp_ui import WhatsAppWindow

        cls.window = WhatsAppWindow(manager=None, embedded=True)
        cls.window.current_chat_id = "chat@c.us"

    def _render(self, raw: dict, who: str = "Ana", from_me: bool = False) -> list[str]:
        window = self.window
        before = len(window._message_bubbles)
        window._add_message_bubble(dict(normalize_message(raw)), who, from_me)
        self.assertGreater(len(window._message_bubbles), before)
        return _bubble_texts(window._message_bubbles[-1])

    def test_location_renders_a_card_instead_of_the_placeholder_body(self):
        texts = self._render({
            "id": "loc-1", "type": "location", "body": "[ubicación]",
            "location": {
                "latitude": 41.4036, "longitude": 2.1744,
                "name": "Sagrada Família", "address": "Barcelona",
            },
        })

        self.assertIn("📍 Sagrada Família", texts)
        self.assertIn("Barcelona", texts)
        self.assertIn("41.40360, 2.17440", texts)
        self.assertIn("Abrir en el mapa", texts)
        self.assertNotIn("[ubicación]", texts)

    def test_contact_card_shows_name_and_phones_not_the_raw_vcard(self):
        texts = self._render({
            "id": "vc-1", "type": "vcard", "body": "[contacto] Ana Pérez",
            "contacts": [VCARD],
        })

        self.assertIn("👤 Ana Pérez", texts)
        self.assertIn("+34 600 111 222", texts)
        self.assertFalse(any("BEGIN:VCARD" in text for text in texts))

    def test_poll_shows_question_and_options_without_inventing_vote_counts(self):
        texts = self._render({
            "id": "poll-1", "type": "poll_creation", "body": "[encuesta] ¿Cuándo quedamos?",
            "poll": {
                "name": "¿Cuándo quedamos?", "options": ["Viernes", "Sábado"],
                "allowMultipleAnswers": True,
            },
        })

        self.assertIn("📊 ¿Cuándo quedamos?", texts)
        self.assertIn("○  Viernes", texts)
        self.assertIn("○  Sábado", texts)
        self.assertTrue(any("Varias respuestas permitidas" in text for text in texts))
        self.assertFalse(any("%" in text for text in texts))

    def test_document_bubble_shows_file_name_size_and_type(self):
        texts = self._render({
            "id": "doc-1", "type": "document", "hasMedia": True,
            "mediaUrl": "/media?id=doc-1", "body": "[documento]",
            "fileName": "informe.pdf", "fileSize": 254800,
            "mimetype": "application/pdf",
        })

        self.assertIn("📎 informe.pdf", texts)
        self.assertTrue(any("249 KB" in text and "application/pdf" in text for text in texts))

    def test_unsupported_type_is_named_instead_of_leaking_the_wire_type(self):
        texts = self._render({"id": "call-1", "type": "call_log", "body": ""})

        self.assertIn("[Llamada]", texts)

    def test_future_type_keeps_its_raw_name_visible(self):
        texts = self._render({"id": "new-1", "type": "some_future_type", "body": ""})

        self.assertIn("[some_future_type]", texts)

    def test_revoked_message_is_rendered_without_its_original_payload(self):
        texts = self._render({
            "id": "rev-1", "type": "revoked", "revoked": True,
            "body": "[mensaje eliminado]",
        })

        self.assertIn("[mensaje eliminado]", texts)
        self.assertFalse(any("📎" in text or "📍" in text for text in texts))

    def test_quote_without_an_id_still_renders_its_snippet(self):
        texts = self._render({
            "id": "q-1", "type": "chat", "body": "vale",
            "quoted": {"id": None, "body": "¿vienes?", "fromMe": False, "senderName": "Ana"},
        })

        self.assertIn("¿vienes?", texts)
        self.assertIn("vale", texts)

    def test_media_caption_is_shown_next_to_the_attachment(self):
        texts = self._render({
            "id": "img-1", "type": "image", "hasMedia": True,
            "mediaUrl": "/media?id=img-1", "body": "[imagen]", "caption": "en la playa",
        })

        self.assertIn("en la playa", texts)
        self.assertNotIn("[imagen]", texts)


class UnreadDividerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from actions.whatsapp_ui import WhatsAppWindow

        cls.window = WhatsAppWindow(manager=None, embedded=True)
        cls.window.current_chat_id = "chat@c.us"

    def _conversation(self) -> list[dict]:
        base = 1_700_000_000_000
        raw = [
            {"id": "in-1", "body": "uno", "fromMe": False},
            {"id": "out-1", "body": "mi respuesta", "fromMe": True},
            {"id": "in-2", "body": "dos", "fromMe": False},
            {"id": "in-3", "body": "tres", "fromMe": False},
        ]
        return [
            dict(normalize_message({
                **item, "chatId": "chat@c.us", "type": "chat",
                "timestamp": base + index * 1000, "waTs": base + index * 1000,
            }))
            for index, item in enumerate(raw)
        ]

    def _layout_texts(self) -> list[str]:
        window = self.window
        texts = []
        for index in range(window.chat_layout.count()):
            item = window.chat_layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                texts.extend(_bubble_texts(widget))
        return texts

    def test_divider_is_placed_before_the_first_unread_incoming_message(self):
        self.window._begin_message_render(self._conversation(), "Ana", unread=2)

        texts = self._layout_texts()
        self.assertIn("2 mensajes no leídos", texts)
        self.assertLess(texts.index("2 mensajes no leídos"), texts.index("dos"))
        self.assertGreater(texts.index("2 mensajes no leídos"), texts.index("mi respuesta"))
        self.assertIsNotNone(self.window._unread_separator)

    def test_single_unread_message_uses_the_singular_label(self):
        self.window._begin_message_render(self._conversation(), "Ana", unread=1)

        texts = self._layout_texts()
        self.assertIn("Mensaje no leído", texts)
        self.assertLess(texts.index("Mensaje no leído"), texts.index("tres"))

    def test_no_divider_when_the_chat_has_nothing_unread(self):
        self.window._begin_message_render(self._conversation(), "Ana", unread=0)

        self.assertFalse(any("no leído" in text for text in self._layout_texts()))
        self.assertIsNone(self.window._unread_separator)

    def test_divider_is_skipped_when_the_loaded_page_is_shorter_than_the_count(self):
        # Only three incoming messages are loaded: anchoring a "10 unread"
        # divider at the top would claim messages are unread that are not.
        self.window._begin_message_render(self._conversation(), "Ana", unread=10)

        self.assertFalse(any("no leído" in text for text in self._layout_texts()))
        self.assertIsNone(self.window._unread_separator)


class ReactionPatchInPlaceTests(unittest.TestCase):
    """A reaction event must patch the existing bubble, not rebuild the chat.

    Rebuilding the whole conversation for a single 👍 tears down and redraws
    every bubble on screen (images, audio players, documents) — for a chat
    with hundreds of loaded messages this is a visible stutter. The manager
    routes reaction updates through ``_apply_message_update_event``, which
    should call ``_patch_reaction_in_place`` and leave every other bubble
    untouched.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from actions.whatsapp_ui import WhatsAppWindow

        cls.window = WhatsAppWindow(manager=None, embedded=True)

    def _seed(self, chat_id: str, message_id: str) -> dict:
        window = self.window
        window.current_chat_id = chat_id
        msg = dict(normalize_message({
            "id": message_id, "chatId": chat_id, "type": "chat", "body": "hola",
        }))
        window._conversation_cache[chat_id] = ([msg], "Ana")
        window._add_message_bubble(msg, "Ana", False)
        return msg

    def test_adding_a_reaction_reuses_the_same_bubble_and_other_bubbles(self):
        window = self.window
        chat_id, message_id = "reaction-chat-1", "r-1"
        self._seed(chat_id, message_id)
        other_bubble = window._message_bubbles_by_id[message_id]
        bubble_count_before = len(window._message_bubbles)

        window._apply_message_update_event(chat_id, message_id, {
            "id": message_id, "event": "reaction",
            "reaction": {"senderId": "ana@c.us", "reaction": "👍"},
        })

        self.assertIs(window._message_bubbles_by_id[message_id], other_bubble)
        self.assertEqual(len(window._message_bubbles), bubble_count_before)
        label = window._message_reaction_labels_by_id[message_id]
        self.assertIn("👍", label.text())

    def test_removing_the_last_reaction_deletes_the_label_not_the_bubble(self):
        window = self.window
        chat_id, message_id = "reaction-chat-2", "r-2"
        self._seed(chat_id, message_id)
        bubble = window._message_bubbles_by_id[message_id]
        window._apply_message_update_event(chat_id, message_id, {
            "id": message_id, "event": "reaction",
            "reaction": {"senderId": "ana@c.us", "reaction": "👍"},
        })
        self.assertIn(message_id, window._message_reaction_labels_by_id)

        window._apply_message_update_event(chat_id, message_id, {
            "id": message_id, "event": "reaction",
            "reaction": {"senderId": "ana@c.us", "reaction": "", "removed": True},
        })

        self.assertNotIn(message_id, window._message_reaction_labels_by_id)
        self.assertIs(window._message_bubbles_by_id[message_id], bubble)

    def test_second_sender_reacting_updates_the_same_label_text(self):
        window = self.window
        chat_id, message_id = "reaction-chat-3", "r-3"
        self._seed(chat_id, message_id)
        window._apply_message_update_event(chat_id, message_id, {
            "id": message_id, "event": "reaction",
            "reaction": {"senderId": "ana@c.us", "reaction": "👍"},
        })
        label = window._message_reaction_labels_by_id[message_id]

        window._apply_message_update_event(chat_id, message_id, {
            "id": message_id, "event": "reaction",
            "reaction": {"senderId": "luis@c.us", "reaction": "👍"},
        })

        self.assertIs(window._message_reaction_labels_by_id[message_id], label)
        self.assertIn("👍 2", label.text())

    def test_revoke_still_falls_back_to_a_full_rerender(self):
        window = self.window
        chat_id, message_id = "reaction-chat-4", "r-4"
        self._seed(chat_id, message_id)

        window._apply_message_update_event(chat_id, message_id, {
            "id": message_id, "event": "revoke", "type": "revoked",
        })

        cached, _name = window._conversation_cache[chat_id]
        self.assertEqual(cached[0]["body"], "[mensaje eliminado]")
        self.assertIn("[mensaje eliminado]", _bubble_texts(window._message_bubbles_by_id[message_id]))


class MessageSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from actions.whatsapp_ui import WhatsAppWindow

        cls.window = WhatsAppWindow(manager=None, embedded=True)

    def _seed(self, chat_id: str) -> list[dict]:
        window = self.window
        window.current_chat_id = chat_id
        raw = [
            {"id": "s-1", "body": "hola que tal"},
            {"id": "s-2", "body": "nos vemos mañana"},
            {"id": "s-3", "body": "hola de nuevo"},
        ]
        msgs = [
            dict(normalize_message({**item, "chatId": chat_id, "type": "chat"}))
            for item in raw
        ]
        window._conversation_cache[chat_id] = (msgs, "Ana")
        for msg in msgs:
            window._add_message_bubble(msg, "Ana", False)
        return msgs

    def test_search_only_matches_rendered_messages_in_order(self):
        window = self.window
        self._seed("search-chat-1")
        window.msg_search.setText("hola")

        window._run_message_search()

        self.assertEqual(window._search_matches, ["s-1", "s-3"])
        self.assertEqual(window._search_index, 0)
        self.assertEqual(window.msg_search_count.text(), "1/2")

    def test_next_and_prev_wrap_around_the_match_list(self):
        window = self.window
        self._seed("search-chat-2")
        window.msg_search.setText("hola")
        window._run_message_search()

        window._message_search_next()
        self.assertEqual(window._search_index, 1)
        window._message_search_next()
        self.assertEqual(window._search_index, 0)  # wraps forward
        window._message_search_prev()
        self.assertEqual(window._search_index, 1)  # wraps backward

    def test_no_query_yields_no_matches_and_empty_count(self):
        window = self.window
        self._seed("search-chat-3")
        window.msg_search.setText("")

        window._run_message_search()

        self.assertEqual(window._search_matches, [])
        self.assertEqual(window.msg_search_count.text(), "")

    def test_closing_search_clears_matches_and_hides_the_bar(self):
        window = self.window
        self._seed("search-chat-4")
        window.msg_search_bar.setVisible(True)
        window.msg_search.setText("hola")
        window._run_message_search()

        window._close_message_search()

        self.assertFalse(window.msg_search_bar.isVisible())
        self.assertEqual(window._search_matches, [])
        self.assertEqual(window.msg_search.text(), "")

    def test_switching_chat_closes_an_open_search(self):
        window = self.window
        self._seed("search-chat-5")
        window._chat_index["search-chat-5"] = {"name": "Ana"}
        window._chat_index["search-chat-6"] = {"name": "Luis"}
        window._conversation_cache["search-chat-6"] = ([], "Luis")
        window.msg_search_bar.setVisible(True)
        window.msg_search.setText("hola")
        # No avatar for either chat: without this, opening a chat kicks off a
        # real HTTP call on a daemon thread to fetch its profile picture.
        original_avatar_fetch = window._ensure_avatar_url_async
        window._ensure_avatar_url_async = lambda *_a, **_k: None
        self.addCleanup(setattr, window, "_ensure_avatar_url_async", original_avatar_fetch)

        window._open_chat("search-chat-6")

        self.assertFalse(window.msg_search_bar.isVisible())
        self.assertEqual(window.msg_search.text(), "")


if __name__ == "__main__":
    unittest.main()
