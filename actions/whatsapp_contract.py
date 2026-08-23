"""Canonical message contract for the local WhatsApp bridge.

The bridge historically emitted slightly different dictionaries for live,
historical, text-send and media-send paths.  This module is the single Python
boundary that turns any of those wire payloads into one forward-compatible
shape while keeping the legacy camelCase keys used by the current UI.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Literal, Mapping, MutableMapping, TypeVar, TypedDict, cast


_T = TypeVar("_T")


MessageDirection = Literal["in", "out"]
MessageEvent = Literal["message", "edit", "revoke", "reaction"]
MessageAck = Literal[-1, 0, 1, 2, 3, 4]
MessageType = Literal[
    "chat", "audio", "ptt", "image", "album", "video", "document",
    "sticker", "location", "vcard", "multi_vcard", "order", "revoked",
    "product", "unknown", "groups_v4_invite", "list", "list_response",
    "buttons_response", "payment", "broadcast_notification", "call_log",
    "ciphertext", "debug", "e2e_notification", "gp2",
    "group_notification", "hsm", "interactive", "native_flow",
    "notification", "notification_template", "oversized", "protocol",
    "reaction", "template_button_reply", "poll_creation",
    "scheduled_event_creation", "edit",
]

KNOWN_MESSAGE_TYPES: frozenset[str] = frozenset(MessageType.__args__)


class QuotedMessage(TypedDict, total=False):
    id: str | None
    body: str
    fromMe: bool
    senderName: str | None
    type: MessageType
    rawType: str


class ReactionInfo(TypedDict, total=False):
    senderId: str
    reaction: str
    removed: bool


class LocationInfo(TypedDict, total=False):
    latitude: float | None
    longitude: float | None
    name: str | None
    address: str | None
    url: str | None


class ContactCard(TypedDict, total=False):
    displayName: str | None
    phones: list[str]
    vcard: str


class PollInfo(TypedDict, total=False):
    name: str
    options: list[str]
    allowMultipleAnswers: bool


WhatsAppMessage = TypedDict(
    "WhatsAppMessage",
    {
        "id": str | None,
        "clientRequestId": str | None,
        "from": str | None,
        "to": str | None,
        "chatId": str | None,
        "author": str | None,
        "authorName": str | None,
        "senderName": str | None,
        "body": str,
        "caption": str,
        "type": MessageType,
        "rawType": str,
        "event": MessageEvent,
        "fromMe": bool,
        "direction": MessageDirection,
        "timestamp": int,
        "waTs": int,
        "observedAtMs": int,
        "sentAtMs": int,
        "hasMedia": bool,
        "mediaUrl": str | None,
        "fileName": str | None,
        "mimetype": str | None,
        "fileSize": int | None,
        "duration": int | None,
        "mentionedIds": list[str],
        "mentions": dict[str, str],
        "quoted": QuotedMessage | None,
        "location": LocationInfo | None,
        "contacts": list[ContactCard],
        "poll": PollInfo | None,
        "ack": MessageAck | None,
        "edited": bool,
        "revoked": bool,
        "reactions": list[ReactionInfo],
        "reaction": ReactionInfo | None,
        "translation": str,
        "transcription": str,
    },
    total=False,
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _milliseconds(value: Any) -> int:
    try:
        number = int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0
    return number * 1000 if 0 < number < 1_000_000_000_000 else number


def _message_type(value: Any) -> tuple[MessageType, str]:
    raw_type = str(value or "chat").strip().lower() or "chat"
    normalized = raw_type if raw_type in KNOWN_MESSAGE_TYPES else "unknown"
    return cast(MessageType, normalized), raw_type


def _integer(value: Any) -> int | None:
    """Coerce the numeric strings whatsapp-web.js uses for size/duration.

    ``m.duration`` arrives as a string of seconds and ``_data.size`` as a
    number; an ``isinstance(value, int)`` check silently dropped the first.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _coordinate(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _location(value: Any) -> LocationInfo | None:
    if not isinstance(value, Mapping):
        return None
    latitude = _coordinate(value.get("latitude"))
    longitude = _coordinate(value.get("longitude"))
    name = _text(value.get("name") or value.get("description"))
    address = _text(value.get("address"))
    url = _text(value.get("url"))
    if latitude is None and longitude is None and not (name or address or url):
        return None
    return {
        "latitude": latitude,
        "longitude": longitude,
        "name": name,
        "address": address,
        "url": url,
    }


_VCARD_NAME_RE = re.compile(r"^FN[^:]*:(.+)$", re.IGNORECASE | re.MULTILINE)
_VCARD_PHONE_RE = re.compile(r"^TEL[^:]*:(.+)$", re.IGNORECASE | re.MULTILINE)


def parse_vcard(raw: str) -> ContactCard:
    """Best-effort display name + phone list out of one raw vCard payload.

    The bridge already parses the vCards it serializes; this keeps a Python
    fallback for records captured before that (persisted buffers) and for the
    raw strings whatsapp-web.js exposes as ``msg.vCards``.
    """
    text = str(raw or "")
    name_match = _VCARD_NAME_RE.search(text)
    phones = [
        phone.strip() for phone in _VCARD_PHONE_RE.findall(text) if phone.strip()
    ]
    return {
        "displayName": _text(name_match.group(1)) if name_match else None,
        "phones": phones,
        "vcard": text,
    }


def _contacts(value: Any) -> list[ContactCard]:
    if isinstance(value, (str, bytes)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result: list[ContactCard] = []
    for item in value:
        if isinstance(item, Mapping):
            phones = item.get("phones")
            card: ContactCard = {
                "displayName": _text(item.get("displayName") or item.get("name")),
                "phones": [str(phone).strip() for phone in phones if str(phone).strip()]
                if isinstance(phones, (list, tuple))
                else [],
                "vcard": str(item.get("vcard") or ""),
            }
            if not card["displayName"] and not card["phones"] and card["vcard"]:
                card = parse_vcard(card["vcard"])
        elif isinstance(item, (str, bytes)):
            card = parse_vcard(item.decode("utf-8", "replace") if isinstance(item, bytes) else item)
        else:
            continue
        if card.get("displayName") or card.get("phones") or card.get("vcard"):
            result.append(card)
    return result


def _poll(value: Any) -> PollInfo | None:
    if not isinstance(value, Mapping):
        return None
    raw_options = value.get("options")
    options: list[str] = []
    if isinstance(raw_options, (list, tuple)):
        for option in raw_options:
            label = (
                _text(option.get("name") or option.get("localId"))
                if isinstance(option, Mapping)
                else _text(option)
            )
            if label:
                options.append(label)
    name = _text(value.get("name")) or ""
    if not name and not options:
        return None
    return {
        "name": name,
        "options": options,
        "allowMultipleAnswers": _bool(value.get("allowMultipleAnswers")),
    }


def _quoted_message(value: Any) -> QuotedMessage | None:
    if not isinstance(value, Mapping):
        return None
    kind, raw_kind = _message_type(value.get("type"))
    return {
        "id": _text(value.get("id")),
        "body": str(value.get("body") or ""),
        "fromMe": _bool(value.get("fromMe")),
        "senderName": _text(value.get("senderName")),
        "type": kind,
        "rawType": raw_kind,
    }


def _reaction(value: Any) -> ReactionInfo | None:
    if not isinstance(value, Mapping):
        return None
    sender_id = _text(value.get("senderId"))
    reaction = str(value.get("reaction") or "")
    if not sender_id:
        return None
    return {
        "senderId": sender_id,
        "reaction": reaction,
        "removed": _bool(value.get("removed")) or not reaction,
    }


def _reactions(value: Any) -> list[ReactionInfo]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[ReactionInfo] = []
    for item in value:
        normalized = _reaction(item)
        if normalized and normalized.get("reaction"):
            result.append(normalized)
    return result


def normalize_message(message: Mapping[str, Any] | None) -> WhatsAppMessage:
    """Return one stable message shape from any bridge payload variant.

    Unknown keys are intentionally retained.  This lets UI-only state such as
    ``_pending`` survive a normalization pass and keeps newer bridge fields
    forward-compatible with an older Python client.
    """
    raw: dict[str, Any] = dict(message or {})
    from_me = _bool(raw.get("fromMe")) or str(raw.get("direction") or "").lower() == "out"
    direction: MessageDirection = "out" if from_me else "in"
    source = _text(raw.get("from"))
    destination = _text(raw.get("to"))
    chat_id = _text(raw.get("chatId")) or (destination if from_me else source)
    kind, raw_kind = _message_type(raw.get("type"))

    observed_at = _milliseconds(raw.get("observedAtMs") or raw.get("timestamp"))
    sent_at = _milliseconds(raw.get("sentAtMs") or raw.get("waTs")) or observed_at
    event_value = str(raw.get("event") or ("edit" if kind == "edit" else "message")).lower()
    event: MessageEvent = cast(
        MessageEvent,
        event_value if event_value in {"message", "edit", "revoke", "reaction"} else "message",
    )

    mentioned = raw.get("mentionedIds")
    mentioned_ids = [str(item) for item in mentioned] if isinstance(mentioned, (list, tuple, set)) else []
    mention_names = raw.get("mentions")
    mentions = (
        {str(key): str(value) for key, value in mention_names.items() if value is not None}
        if isinstance(mention_names, Mapping)
        else {}
    )
    try:
        ack_number = int(raw["ack"]) if raw.get("ack") is not None else None
    except (TypeError, ValueError):
        ack_number = None
    ack = cast(MessageAck | None, ack_number if ack_number in {-1, 0, 1, 2, 3, 4} else None)

    raw.update({
        "id": _text(raw.get("id")),
        "clientRequestId": _text(raw.get("clientRequestId")),
        "from": source,
        "to": destination,
        "chatId": chat_id,
        "author": _text(raw.get("author")),
        "authorName": _text(raw.get("authorName")),
        "senderName": _text(raw.get("senderName")),
        "body": str(raw.get("body") or ""),
        "caption": str(raw.get("caption") or ""),
        "type": kind,
        "rawType": raw_kind,
        "event": event,
        "fromMe": from_me,
        "direction": direction,
        "timestamp": observed_at,
        "waTs": sent_at,
        "observedAtMs": observed_at,
        "sentAtMs": sent_at,
        "hasMedia": _bool(raw.get("hasMedia")) or bool(raw.get("mediaUrl")),
        "mediaUrl": _text(raw.get("mediaUrl")),
        "fileName": _text(raw.get("fileName") or raw.get("filename")),
        "mimetype": _text(raw.get("mimetype")),
        "fileSize": _integer(raw.get("fileSize")),
        "duration": _integer(raw.get("duration")),
        "mentionedIds": mentioned_ids,
        "mentions": mentions,
        "quoted": _quoted_message(raw.get("quoted")),
        "location": _location(raw.get("location")),
        "contacts": _contacts(raw.get("contacts") or raw.get("vCards")),
        "poll": _poll(raw.get("poll")),
        "ack": ack,
        "edited": _bool(raw.get("edited")),
        "revoked": _bool(raw.get("revoked")) or kind == "revoked",
        "reactions": _reactions(raw.get("reactions")),
        "reaction": _reaction(raw.get("reaction")),
        "translation": str(raw.get("translation") or ""),
        "transcription": str(raw.get("transcription") or ""),
    })
    return cast(WhatsAppMessage, raw)


def apply_message_update(
    message: Mapping[str, Any], update: Mapping[str, Any],
) -> WhatsAppMessage:
    """Apply an edit, revoke or reaction event to a canonical message copy."""
    current = dict(normalize_message(message))
    event = str(update.get("event") or update.get("type") or "").lower()
    if event == "edit":
        current["body"] = str(update.get("body") or "")
        current["edited"] = True
    elif event == "revoke" or str(update.get("type") or "").lower() == "revoked":
        current.update({
            "body": "[mensaje eliminado]",
            "caption": "",
            "type": "revoked",
            "rawType": "revoked",
            "revoked": True,
            "hasMedia": False,
            "mediaUrl": None,
            # A deleted message keeps nothing but its slot in the timeline:
            # dropping the payload here is what stops the UI from rendering a
            # location card / contact card / poll for content WhatsApp already
            # removed on every other client.
            "fileName": None,
            "fileSize": None,
            "mimetype": None,
            "duration": None,
            "location": None,
            "contacts": [],
            "poll": None,
            "reactions": [],
            "transcription": "",
            "translation": "",
        })
    elif event == "reaction":
        reaction = _reaction(update.get("reaction"))
        if reaction:
            sender_id = reaction["senderId"]
            reactions = [
                item for item in _reactions(current.get("reactions"))
                if item.get("senderId") != sender_id
            ]
            if reaction.get("reaction") and not reaction.get("removed"):
                reactions.append(reaction)
            current["reactions"] = reactions
    return normalize_message(current)


def normalize_messages(messages: Any) -> list[WhatsAppMessage]:
    if not isinstance(messages, list):
        return []
    return [normalize_message(message) for message in messages if isinstance(message, Mapping)]


def remap_message_key(
    mapping: MutableMapping[str, _T], old_id: str, new_id: str,
) -> _T | None:
    """Move a UI/cache reference after an optimistic id becomes a server id."""
    value = mapping.pop(str(old_id or ""), None)
    if value is not None:
        mapping[str(new_id or old_id)] = value
    return value


def new_local_message_id() -> str:
    """Return a collision-resistant id for one optimistic outgoing message."""
    return f"local-{uuid.uuid4().hex}"


def apply_optimistic_send_result(
    message: Mapping[str, Any],
    response: Mapping[str, Any] | None,
    error: str = "",
) -> WhatsAppMessage:
    """Apply confirmed, uncertain or failed HTTP send state to a local echo."""
    updated = dict(message)
    result = dict(response or {})
    if error:
        updated.pop("_pending", None)
        updated.pop("_confirming", None)
        updated["_failed"] = True
        updated["_sendError"] = str(error)
    elif result.get("pendingConfirmation"):
        updated.pop("_pending", None)
        updated.pop("_failed", None)
        updated.pop("_sendError", None)
        updated["_confirming"] = True
    else:
        updated.pop("_pending", None)
        updated.pop("_confirming", None)
        updated.pop("_failed", None)
        updated.pop("_sendError", None)
        updated.pop("_retry", None)
        if result.get("id"):
            updated["id"] = str(result["id"])
        if result.get("ack") is not None:
            updated["ack"] = result["ack"]
    return normalize_message(updated)


def is_optimistic_echo(
    local_message: Mapping[str, Any],
    bridge_message: Mapping[str, Any],
    match_window_ms: int = 15_000,
) -> bool:
    """Whether a bridge outgoing message confirms one optimistic local echo."""
    local_id = str(local_message.get("id") or "")
    if not local_id.startswith("local-") or not _bool(local_message.get("fromMe")):
        return False
    request_id = str(bridge_message.get("clientRequestId") or "").strip()
    if request_id:
        return request_id == local_id
    local_body = str(local_message.get("body") or "")
    bridge_body = str(bridge_message.get("body") or "")
    body_matches = local_body == bridge_body or (
        not local_body
        and _bool(bridge_message.get("hasMedia") or local_message.get("hasMedia"))
    )
    if not body_matches:
        return False
    if _bool(local_message.get("_confirming")):
        return True
    try:
        delta = abs(
            int(local_message.get("timestamp") or 0)
            - int(bridge_message.get("timestamp") or 0)
        )
    except (TypeError, ValueError):
        return False
    return delta < max(0, int(match_window_ms))
