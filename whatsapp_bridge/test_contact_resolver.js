const assert = require('node:assert/strict');
const test = require('node:test');

const {
  loadResolveSources,
  dedupeLinkedIdentityCandidates,
} = require('./contact_resolver');

test('keeps chats when the WhatsApp contact store fails', async () => {
  const chats = [{ id: { _serialized: '123@c.us' }, name: 'Mama' }];
  const client = {
    getChats: async () => chats,
    getContacts: async () => {
      throw new Error('Invalid get call using deviceWid');
    },
  };

  const result = await loadResolveSources(client);

  assert.deepEqual(result.chats, chats);
  assert.deepEqual(result.contacts, []);
});

test('fails only when neither source is available', async () => {
  const client = {
    getChats: async () => {
      throw new Error('chats unavailable');
    },
    getContacts: async () => {
      throw new Error('contacts unavailable');
    },
  };

  await assert.rejects(() => loadResolveSources(client), /chats unavailable/);
});

test('deduplicates phone and linked-device identities for the same contact', () => {
  const result = dedupeLinkedIdentityCandidates([
    { id: '999@lid', name: 'Mamá', score: 100, isGroup: false },
    { id: '611111111@c.us', name: 'Mama', score: 100, isGroup: false },
  ]);

  assert.equal(result.length, 1);
  assert.equal(result[0].id, '611111111@c.us');
});

test('keeps genuinely different contacts with the same display name ambiguous', () => {
  const result = dedupeLinkedIdentityCandidates([
    { id: '611111111@c.us', name: 'Alex', score: 100, isGroup: false },
    { id: '622222222@c.us', name: 'Alex', score: 100, isGroup: false },
  ]);

  assert.equal(result.length, 2);
});
