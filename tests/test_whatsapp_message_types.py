"""Normalization of the message types the bridge only used to pass through.

Location, contact cards and polls arrived as free-form payloads (or as a raw
vCard blob inside ``body``), so the UI could not render them without guessing.
These tests pin the canonical shape and the coercions around it.
"""
import unittest

from actions.whatsapp_contract import (
    apply_message_update,
    normalize_message,
    parse_vcard,
)


VCARD = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "N:Pérez;Ana;;;\r\n"
    "FN:Ana Pérez\r\n"
    "TEL;type=CELL;waid=34600111222:+34 600 111 222\r\n"
    "TEL;type=WORK:+34 930 000 000\r\n"
    "END:VCARD"
)


class LocationMessageTests(unittest.TestCase):
    def test_location_is_structured_and_accepts_string_coordinates(self):
        message = normalize_message({
            "id": "m1", "type": "location", "body": "[ubicación]",
            "location": {
                "latitude": "41.4036", "longitude": "2.1744",
                "description": "Sagrada Família", "address": "Barcelona",
            },
        })

        self.assertEqual(message["type"], "location")
        self.assertAlmostEqual(message["location"]["latitude"], 41.4036)
        self.assertAlmostEqual(message["location"]["longitude"], 2.1744)
        # `description` is the legacy whatsapp-web.js field name for `name`.
        self.assertEqual(message["location"]["name"], "Sagrada Família")
        self.assertEqual(message["location"]["address"], "Barcelona")

    def test_empty_location_payload_normalizes_to_none(self):
        message = normalize_message({"id": "m2", "type": "location", "location": {}})

        self.assertIsNone(message["location"])

    def test_location_without_coordinates_survives_when_it_has_a_name(self):
        message = normalize_message({
            "id": "m3", "type": "location", "location": {"name": "Casa"},
        })

        self.assertEqual(message["location"]["name"], "Casa")
        self.assertIsNone(message["location"]["latitude"])


class ContactCardTests(unittest.TestCase):
    def test_raw_vcard_is_parsed_into_a_name_and_phone_list(self):
        card = parse_vcard(VCARD)

        self.assertEqual(card["displayName"], "Ana Pérez")
        self.assertEqual(card["phones"], ["+34 600 111 222", "+34 930 000 000"])
        self.assertIn("BEGIN:VCARD", card["vcard"])

    def test_legacy_vcards_field_is_accepted_and_normalized(self):
        message = normalize_message({"id": "m4", "type": "vcard", "vCards": [VCARD]})

        self.assertEqual(len(message["contacts"]), 1)
        self.assertEqual(message["contacts"][0]["displayName"], "Ana Pérez")

    def test_already_structured_contacts_are_kept_without_reparsing(self):
        message = normalize_message({
            "id": "m5", "type": "multi_vcard",
            "contacts": [{"displayName": "Luis", "phones": ["+34 600 000 000"], "vcard": ""}],
        })

        self.assertEqual(message["contacts"], [
            {"displayName": "Luis", "phones": ["+34 600 000 000"], "vcard": ""},
        ])

    def test_structured_contact_without_metadata_falls_back_to_its_vcard(self):
        message = normalize_message({
            "id": "m6", "type": "vcard", "contacts": [{"vcard": VCARD}],
        })

        self.assertEqual(message["contacts"][0]["displayName"], "Ana Pérez")


class PollMessageTests(unittest.TestCase):
    def test_poll_options_accept_both_strings_and_option_objects(self):
        from_objects = normalize_message({
            "id": "m7", "type": "poll_creation",
            "poll": {
                "name": "¿Cuándo quedamos?",
                "options": [{"name": "Viernes", "localId": 0}, {"name": "Sábado", "localId": 1}],
                "allowMultipleAnswers": True,
            },
        })
        from_strings = normalize_message({
            "id": "m8", "type": "poll_creation",
            "poll": {"name": "¿Cuándo quedamos?", "options": ["Viernes", "Sábado"]},
        })

        self.assertEqual(from_objects["poll"]["options"], ["Viernes", "Sábado"])
        self.assertTrue(from_objects["poll"]["allowMultipleAnswers"])
        self.assertEqual(from_strings["poll"]["options"], ["Viernes", "Sábado"])
        self.assertFalse(from_strings["poll"]["allowMultipleAnswers"])

    def test_empty_poll_payload_normalizes_to_none(self):
        self.assertIsNone(normalize_message({"id": "m9", "poll": {"options": []}})["poll"])


class AttachmentMetadataTests(unittest.TestCase):
    def test_numeric_strings_from_the_bridge_become_integers(self):
        message = normalize_message({
            "id": "m10", "type": "document", "hasMedia": True,
            "fileName": "informe.pdf", "fileSize": "254800",
            "mimetype": "application/pdf", "duration": "12",
        })

        self.assertEqual(message["fileName"], "informe.pdf")
        self.assertEqual(message["fileSize"], 254800)
        self.assertEqual(message["mimetype"], "application/pdf")
        # whatsapp-web.js exposes duration as a string of seconds; the old
        # isinstance(int) guard dropped it silently.
        self.assertEqual(message["duration"], 12)

    def test_unparseable_metadata_degrades_to_none_instead_of_raising(self):
        message = normalize_message({"id": "m11", "fileSize": "n/a", "duration": None})

        self.assertIsNone(message["fileSize"])
        self.assertIsNone(message["duration"])


class RevokeClearsPayloadTests(unittest.TestCase):
    def test_revoke_drops_every_structured_payload_of_the_original(self):
        original = normalize_message({
            "id": "m12", "type": "location", "body": "[ubicación]",
            "location": {"latitude": 41.4, "longitude": 2.1},
            "contacts": [{"displayName": "Ana", "phones": [], "vcard": ""}],
            "poll": {"name": "¿Vienes?", "options": ["Sí"]},
            "fileName": "informe.pdf", "fileSize": 10, "mimetype": "application/pdf",
            "duration": 5, "reactions": [{"senderId": "ana", "reaction": "👍"}],
            "transcription": "hola", "translation": "hello",
        })

        revoked = apply_message_update(original, {"id": "m12", "event": "revoke"})

        self.assertEqual(revoked["body"], "[mensaje eliminado]")
        self.assertTrue(revoked["revoked"])
        self.assertIsNone(revoked["location"])
        self.assertEqual(revoked["contacts"], [])
        self.assertIsNone(revoked["poll"])
        self.assertIsNone(revoked["fileName"])
        self.assertIsNone(revoked["fileSize"])
        self.assertIsNone(revoked["mimetype"])
        self.assertIsNone(revoked["duration"])
        self.assertEqual(revoked["reactions"], [])
        self.assertEqual(revoked["transcription"], "")
        self.assertEqual(revoked["translation"], "")


if __name__ == "__main__":
    unittest.main()
