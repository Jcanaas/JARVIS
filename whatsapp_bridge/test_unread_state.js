const test = require('node:test');
const assert = require('node:assert/strict');

const { incrementUnread, reconcileUnread, setUnread } = require('./unread_state');

test('replaces stale persisted unread state with the first live chat snapshot', () => {
  const counts = new Map([['chat@c.us', 7]]);
  const touched = new Set();

  assert.equal(reconcileUnread(counts, touched, 'chat@c.us', 0), 0);
  assert.equal(counts.get('chat@c.us'), 0);
});

test('keeps current-session event state when chat.unreadCount is stale', () => {
  const counts = new Map();
  const touched = new Set();
  setUnread(counts, touched, 'chat@c.us', 2);

  assert.equal(reconcileUnread(counts, touched, 'chat@c.us', 0), 2);
});

test('first current-session message does not inherit a stale persisted count', () => {
  const counts = new Map([['chat@c.us', 7]]);
  const touched = new Set();

  assert.equal(incrementUnread(counts, touched, 'chat@c.us'), 1);
  assert.equal(reconcileUnread(counts, touched, 'chat@c.us', 0), 1);
});

test('a cross-device unread event can clear the current-session count', () => {
  const counts = new Map();
  const touched = new Set();
  setUnread(counts, touched, 'chat@c.us', 3);
  setUnread(counts, touched, 'chat@c.us', 0);

  assert.equal(reconcileUnread(counts, touched, 'chat@c.us', 3), 0);
});
