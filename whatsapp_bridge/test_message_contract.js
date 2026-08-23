const test = require('node:test');
const assert = require('node:assert/strict');

const {
  applyReaction,
  applyRevoke,
  messagesSince,
  normalizeMessageRecord,
  parseVcard,
  upsertMessage,
} = require('./message_contract');

const VCARD = [
  'BEGIN:VCARD',
  'VERSION:3.0',
  'FN:Ana Pérez',
  'TEL;type=CELL;waid=34600111222:+34 600 111 222',
  'TEL;type=WORK:+34 930 000 000',
  'END:VCARD',
].join('\r\n');

test('normalizes one complete message record without losing metadata', () => {
  const message = normalizeMessageRecord({
    id: 'msg-1',
    clientRequestId: 'local-1',
    from: '123@c.us',
    chatId: '123@c.us',
    body: 'hola',
    type: 'chat',
    timestamp: 1700000001000,
    waTs: 1700000000000,
    authorName: 'Ana',
    mentions: { '456@c.us': 'Luis' },
    quoted: { id: 'msg-0', body: 'antes', type: 'chat' },
    edited: true,
  });

  assert.equal(message.observedAtMs, 1700000001000);
  assert.equal(message.clientRequestId, 'local-1');
  assert.equal(message.sentAtMs, 1700000000000);
  assert.equal(message.quoted.id, 'msg-0');
  assert.equal(message.authorName, 'Ana');
  assert.equal(message.edited, true);
});

test('maps future message types to unknown while retaining the raw type', () => {
  const message = normalizeMessageRecord({ id: 'msg-2', type: 'future_type' });
  assert.equal(message.type, 'unknown');
  assert.equal(message.rawType, 'future_type');
});

test('upserts duplicate message ids instead of appending a second entry', () => {
  const messages = [];
  upsertMessage(messages, { id: 'msg-3', body: 'first', timestamp: 1700000000001 }, 1000);
  upsertMessage(messages, { id: 'msg-3', body: 'confirmed', ack: 2, timestamp: 1700000000002 }, 1000);

  assert.equal(messages.length, 1);
  assert.equal(messages[0].body, 'confirmed');
  assert.equal(messages[0].ack, 2);
  assert.equal(messages[0].timestamp, 1700000000002);
});

test('keeps edit events separate from the original message record', () => {
  const messages = [];
  upsertMessage(messages, { id: 'msg-4', body: 'first', timestamp: 1700000000001 }, 1000);
  upsertMessage(messages, {
    id: 'msg-4', event: 'edit', type: 'edit', body: 'changed', timestamp: 1700000000002,
  }, 1000);

  assert.equal(messages.length, 2);
  assert.equal(messages[1].event, 'edit');
});

test('polling includes the cursor millisecond so late same-ms events are not lost', () => {
  const messages = [
    { id: 'already-seen', timestamp: 1700000000000 },
    { id: 'arrived-late', timestamp: 1700000000000 },
    { id: 'next', timestamp: 1700000000001 },
  ];

  assert.deepEqual(
    messagesSince(messages, 1700000000000).map(message => message.id),
    ['already-seen', 'arrived-late', 'next'],
  );
});

test('revoke patches the original record and emits a polling event', () => {
  const messages = [];
  upsertMessage(messages, {
    id: 'msg-5', chatId: 'chat@c.us', body: 'secret', hasMedia: true,
    timestamp: 1700000000000,
  });

  applyRevoke(messages, {
    id: 'msg-5', chatId: 'chat@c.us', timestamp: 1700000000100,
  });

  assert.equal(messages[0].revoked, true);
  assert.equal(messages[0].body, '[mensaje eliminado]');
  assert.equal(messages[0].hasMedia, false);
  assert.equal(messages[1].event, 'revoke');
});

test('location payloads keep coordinates given as strings', () => {
  const message = normalizeMessageRecord({
    id: 'loc-1',
    type: 'location',
    location: { latitude: '41.4036', longitude: '2.1744', description: 'Sagrada Família' },
  });

  assert.equal(message.location.latitude, 41.4036);
  assert.equal(message.location.longitude, 2.1744);
  assert.equal(message.location.name, 'Sagrada Família');
  assert.equal(normalizeMessageRecord({ id: 'loc-2', location: {} }).location, null);
});

test('raw vCards become a display name and a phone list', () => {
  assert.deepEqual(parseVcard(VCARD).phones, ['+34 600 111 222', '+34 930 000 000']);

  const message = normalizeMessageRecord({ id: 'vc-1', type: 'vcard', vCards: [VCARD] });

  assert.equal(message.contacts.length, 1);
  assert.equal(message.contacts[0].displayName, 'Ana Pérez');
});

test('poll options accept both plain strings and wwebjs option objects', () => {
  const message = normalizeMessageRecord({
    id: 'poll-1',
    type: 'poll_creation',
    poll: {
      name: '¿Cuándo quedamos?',
      options: [{ name: 'Viernes', localId: 0 }, 'Sábado'],
      allowMultipleAnswers: true,
    },
  });

  assert.deepEqual(message.poll.options, ['Viernes', 'Sábado']);
  assert.equal(message.poll.allowMultipleAnswers, true);
  assert.equal(normalizeMessageRecord({ id: 'poll-2', poll: { options: [] } }).poll, null);
});

test('attachment size and duration survive as numbers when sent as strings', () => {
  const message = normalizeMessageRecord({
    id: 'doc-1', type: 'document', fileName: 'informe.pdf',
    fileSize: '254800', duration: '12',
  });

  assert.equal(message.fileSize, 254800);
  assert.equal(message.duration, 12);
  assert.equal(normalizeMessageRecord({ id: 'doc-2', duration: 'n/a' }).duration, null);
});

test('revoke strips every structured payload from the original record', () => {
  const messages = [];
  upsertMessage(messages, {
    id: 'msg-7', chatId: 'chat@c.us', type: 'location', timestamp: 1700000000000,
    location: { latitude: 41.4, longitude: 2.1 },
    contacts: [{ displayName: 'Ana', phones: [], vcard: '' }],
    poll: { name: '¿Vienes?', options: ['Sí'] },
    fileName: 'informe.pdf', fileSize: 10, mimetype: 'application/pdf', duration: 5,
    reactions: [{ senderId: 'ana@c.us', reaction: '👍' }],
  });

  applyRevoke(messages, { id: 'msg-7', chatId: 'chat@c.us', timestamp: 1700000000100 });

  assert.equal(messages[0].location, null);
  assert.deepEqual(messages[0].contacts, []);
  assert.equal(messages[0].poll, null);
  assert.equal(messages[0].fileName, null);
  assert.equal(messages[0].fileSize, null);
  assert.equal(messages[0].duration, null);
  assert.deepEqual(messages[0].reactions, []);
});

test('reaction updates are keyed by sender and empty reaction removes them', () => {
  const messages = [];
  upsertMessage(messages, {
    id: 'msg-6', chatId: 'chat@c.us', body: 'hola', timestamp: 1700000000000,
  });

  applyReaction(messages, {
    messageId: 'msg-6', chatId: 'chat@c.us', senderId: 'ana@c.us',
    reaction: '👍', timestamp: 1700000000100,
  });
  assert.deepEqual(messages[0].reactions, [{ senderId: 'ana@c.us', reaction: '👍' }]);
  assert.equal(messages[1].event, 'reaction');

  applyReaction(messages, {
    messageId: 'msg-6', chatId: 'chat@c.us', senderId: 'ana@c.us',
    reaction: '', timestamp: 1700000000200,
  });
  assert.deepEqual(messages[0].reactions, []);
});
