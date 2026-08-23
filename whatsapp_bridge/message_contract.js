'use strict';

/**
 * Canonical wire contract for messages stored by the local bridge.
 * Keep this type list aligned with actions/whatsapp_contract.py.
 *
 * @typedef {'in'|'out'} MessageDirection
 * @typedef {'message'|'edit'|'revoke'|'reaction'} MessageEvent
 * @typedef {'chat'|'audio'|'ptt'|'image'|'album'|'video'|'document'|'sticker'|
 * 'location'|'vcard'|'multi_vcard'|'order'|'revoked'|'product'|'unknown'|
 * 'groups_v4_invite'|'list'|'list_response'|'buttons_response'|'payment'|
 * 'broadcast_notification'|'call_log'|'ciphertext'|'debug'|'e2e_notification'|
 * 'gp2'|'group_notification'|'hsm'|'interactive'|'native_flow'|'notification'|
 * 'notification_template'|'oversized'|'protocol'|'reaction'|
 * 'template_button_reply'|'poll_creation'|'scheduled_event_creation'|'edit'} MessageType
 */

const KNOWN_MESSAGE_TYPES = new Set([
  'chat', 'audio', 'ptt', 'image', 'album', 'video', 'document', 'sticker',
  'location', 'vcard', 'multi_vcard', 'order', 'revoked', 'product', 'unknown',
  'groups_v4_invite', 'list', 'list_response', 'buttons_response', 'payment',
  'broadcast_notification', 'call_log', 'ciphertext', 'debug',
  'e2e_notification', 'gp2', 'group_notification', 'hsm', 'interactive',
  'native_flow', 'notification', 'notification_template', 'oversized',
  'protocol', 'reaction', 'template_button_reply', 'poll_creation',
  'scheduled_event_creation', 'edit',
]);

function toMilliseconds(value) {
  const number = Number(value) || 0;
  return number > 0 && number < 1e12 ? number * 1000 : number;
}

function asText(value) {
  if (value === null || value === undefined) return null;
  const result = String(value).trim();
  return result || null;
}

function asBoolean(value) {
  if (typeof value === 'string') {
    return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase());
  }
  return !!value;
}

// whatsapp-web.js hands out `duration` as a string of seconds and `_data.size`
// as a number; an Number.isInteger() guard silently dropped the former.
function asInteger(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  const number = Number(value);
  return Number.isFinite(number) ? Math.trunc(number) : null;
}

function asCoordinate(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function normalizeLocation(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const latitude = asCoordinate(value.latitude);
  const longitude = asCoordinate(value.longitude);
  const name = asText(value.name ?? value.description);
  const address = asText(value.address);
  const url = asText(value.url);
  if (latitude === null && longitude === null && !name && !address && !url) return null;
  return { latitude, longitude, name, address, url };
}

const VCARD_NAME_RE = /^FN[^:]*:(.+)$/im;
const VCARD_PHONE_RE = /^TEL[^:]*:(.+)$/gim;

/** Best-effort display name + phones out of one raw vCard payload. */
function parseVcard(raw) {
  const text = String(raw || '');
  const nameMatch = text.match(VCARD_NAME_RE);
  const phones = [];
  VCARD_PHONE_RE.lastIndex = 0;
  let match = VCARD_PHONE_RE.exec(text);
  while (match) {
    const phone = String(match[1] || '').trim();
    if (phone) phones.push(phone);
    match = VCARD_PHONE_RE.exec(text);
  }
  return { displayName: nameMatch ? asText(nameMatch[1]) : null, phones, vcard: text };
}

function normalizeContacts(value) {
  const items = typeof value === 'string' ? [value] : value;
  if (!Array.isArray(items)) return [];
  const result = [];
  for (const item of items) {
    let card = null;
    if (typeof item === 'string') {
      card = parseVcard(item);
    } else if (item && typeof item === 'object' && !Array.isArray(item)) {
      const phones = Array.isArray(item.phones)
        ? item.phones.map(phone => String(phone).trim()).filter(Boolean)
        : [];
      card = {
        displayName: asText(item.displayName ?? item.name),
        phones,
        vcard: String(item.vcard || ''),
      };
      if (!card.displayName && !card.phones.length && card.vcard) card = parseVcard(card.vcard);
    }
    if (card && (card.displayName || card.phones.length || card.vcard)) result.push(card);
  }
  return result;
}

function normalizePoll(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const options = Array.isArray(value.options)
    ? value.options
      .map(option => (option && typeof option === 'object'
        ? asText(option.name ?? option.localId)
        : asText(option)))
      .filter(Boolean)
    : [];
  const name = asText(value.name) || '';
  if (!name && !options.length) return null;
  return { name, options, allowMultipleAnswers: asBoolean(value.allowMultipleAnswers) };
}

function normalizeQuoted(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const rawType = String(value.type || 'chat').trim().toLowerCase() || 'chat';
  return {
    id: asText(value.id),
    body: String(value.body || ''),
    fromMe: asBoolean(value.fromMe),
    senderName: asText(value.senderName),
    type: KNOWN_MESSAGE_TYPES.has(rawType) ? rawType : 'unknown',
    rawType,
  };
}

function normalizeReaction(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const senderId = asText(value.senderId);
  if (!senderId) return null;
  const reaction = String(value.reaction || '');
  return { senderId, reaction, removed: asBoolean(value.removed) || !reaction };
}

function normalizeReactions(value) {
  if (!Array.isArray(value)) return [];
  return value.map(normalizeReaction).filter(item => item && item.reaction);
}

/** @returns {Record<string, *>} */
function normalizeMessageRecord(record = {}) {
  const raw = { ...(record || {}) };
  const fromMe = asBoolean(raw.fromMe) || String(raw.direction || '').toLowerCase() === 'out';
  const direction = fromMe ? 'out' : 'in';
  const from = asText(raw.from);
  const to = asText(raw.to);
  const chatId = asText(raw.chatId) || (fromMe ? to : from);
  const rawType = String(raw.type || 'chat').trim().toLowerCase() || 'chat';
  const type = KNOWN_MESSAGE_TYPES.has(rawType) ? rawType : 'unknown';
  const observedAtMs = toMilliseconds(raw.observedAtMs || raw.timestamp);
  const sentAtMs = toMilliseconds(raw.sentAtMs || raw.waTs) || observedAtMs;
  const requestedEvent = String(raw.event || (type === 'edit' ? 'edit' : 'message')).toLowerCase();
  const event = ['message', 'edit', 'revoke', 'reaction'].includes(requestedEvent)
    ? requestedEvent
    : 'message';
  const mentionedIds = Array.isArray(raw.mentionedIds)
    ? raw.mentionedIds.map(String)
    : [];
  const mentions = raw.mentions && typeof raw.mentions === 'object' && !Array.isArray(raw.mentions)
    ? { ...raw.mentions }
    : {};
  const ackNumber = raw.ack === null || raw.ack === undefined ? null : Number(raw.ack);
  const ack = [-1, 0, 1, 2, 3, 4].includes(ackNumber) ? ackNumber : null;

  return {
    ...raw,
    id: asText(raw.id),
    clientRequestId: asText(raw.clientRequestId),
    from,
    to,
    chatId,
    author: asText(raw.author),
    authorName: asText(raw.authorName),
    senderName: asText(raw.senderName),
    body: String(raw.body || ''),
    caption: String(raw.caption || ''),
    type,
    rawType,
    event,
    fromMe,
    direction,
    timestamp: observedAtMs,
    waTs: sentAtMs,
    observedAtMs,
    sentAtMs,
    hasMedia: asBoolean(raw.hasMedia) || !!raw.mediaUrl,
    mediaUrl: asText(raw.mediaUrl),
    fileName: asText(raw.fileName || raw.filename),
    mimetype: asText(raw.mimetype),
    fileSize: asInteger(raw.fileSize),
    duration: asInteger(raw.duration),
    mentionedIds,
    mentions,
    quoted: normalizeQuoted(raw.quoted),
    location: normalizeLocation(raw.location),
    contacts: normalizeContacts(raw.contacts || raw.vCards),
    poll: normalizePoll(raw.poll),
    ack,
    edited: asBoolean(raw.edited),
    revoked: asBoolean(raw.revoked) || type === 'revoked',
    reactions: normalizeReactions(raw.reactions),
    reaction: normalizeReaction(raw.reaction),
    translation: String(raw.translation || ''),
    transcription: String(raw.transcription || ''),
  };
}

function mergeRecords(existing, incoming) {
  const merged = { ...existing };
  for (const [key, value] of Object.entries(incoming)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value) && value.length === 0 && Array.isArray(merged[key]) && merged[key].length) continue;
    if (
      value && typeof value === 'object' && !Array.isArray(value)
      && Object.keys(value).length === 0
      && merged[key] && typeof merged[key] === 'object' && Object.keys(merged[key]).length
    ) continue;
    if (value === '' && merged[key] && !['body', 'caption', 'translation', 'transcription'].includes(key)) continue;
    merged[key] = value;
  }
  return merged;
}

/**
 * Insert a bridge message once. Repeated message_create/send paths merge by id;
 * edit/revoke/reaction events remain separate polling records.
 */
function upsertMessage(buffer, record, maxSize = 1000) {
  const normalized = normalizeMessageRecord(record);
  const canMerge = normalized.event === 'message' && !!normalized.id;
  const existingIndex = canMerge
    ? buffer.findIndex(item => item && item.id === normalized.id && (item.event || 'message') === 'message')
    : -1;
  if (existingIndex >= 0) {
    buffer[existingIndex] = mergeRecords(buffer[existingIndex], normalized);
  } else {
    buffer.push(normalized);
  }
  while (buffer.length > maxSize) buffer.shift();
  return existingIndex >= 0 ? buffer[existingIndex] : normalized;
}

function messagesSince(buffer, since) {
  const cursor = Number(since) || 0;
  // Inclusive comparison is intentional. Consumers deduplicate by message/event
  // identity; excluding the cursor millisecond can permanently lose an event
  // appended after the previous HTTP response with the same Date.now() value.
  return buffer.filter(item => item && Number(item.timestamp || 0) >= cursor);
}

function applyRevoke(buffer, details, maxSize = 1000) {
  details = details || {};
  const id = details && details.id ? String(details.id) : null;
  const original = id
    ? buffer.find(item => item && item.id === id && (item.event || 'message') === 'message')
    : null;
  if (original) {
    original.body = '[mensaje eliminado]';
    original.caption = '';
    original.type = 'revoked';
    original.rawType = 'revoked';
    original.revoked = true;
    original.hasMedia = false;
    original.mediaUrl = null;
    // Mirror actions/whatsapp_contract.py: a deleted message keeps its slot in
    // the timeline and nothing else, so no client can rebuild the payload.
    original.fileName = null;
    original.fileSize = null;
    original.mimetype = null;
    original.duration = null;
    original.location = null;
    original.contacts = [];
    original.poll = null;
    original.reactions = [];
    original.transcription = '';
    original.translation = '';
  }
  return upsertMessage(buffer, {
    id,
    chatId: details.chatId || (original && original.chatId) || null,
    fromMe: details.fromMe ?? (original ? original.fromMe : false),
    event: 'revoke',
    type: 'revoked',
    body: '[mensaje eliminado]',
    revoked: true,
    timestamp: details.timestamp || Date.now(),
    waTs: (original && original.waTs) || details.timestamp || Date.now(),
  }, maxSize);
}

function applyReaction(buffer, details, maxSize = 1000) {
  details = details || {};
  const messageId = details && details.messageId ? String(details.messageId) : null;
  const senderId = details && details.senderId ? String(details.senderId) : '';
  const reactionText = String((details && details.reaction) || '');
  const original = messageId
    ? buffer.find(item => item && item.id === messageId && (item.event || 'message') === 'message')
    : null;
  if (original && senderId) {
    const reactions = normalizeReactions(original.reactions);
    const withoutSender = reactions.filter(item => item && item.senderId !== senderId);
    if (reactionText) withoutSender.push({ senderId, reaction: reactionText });
    original.reactions = withoutSender;
  }
  return upsertMessage(buffer, {
    id: messageId,
    chatId: details.chatId || (original && original.chatId) || null,
    event: 'reaction',
    type: 'reaction',
    body: reactionText,
    reaction: { senderId, reaction: reactionText, removed: !reactionText },
    timestamp: details.timestamp || Date.now(),
    waTs: details.waTs || details.timestamp || Date.now(),
  }, maxSize);
}

module.exports = {
  applyReaction,
  applyRevoke,
  KNOWN_MESSAGE_TYPES,
  messagesSince,
  normalizeMessageRecord,
  parseVcard,
  toMilliseconds,
  upsertMessage,
};
