'use strict';

function normalizeCount(value) {
  const count = Number.parseInt(value, 10);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

function setUnread(counts, touched, chatId, value) {
  if (!chatId) return 0;
  const count = normalizeCount(value);
  counts.set(chatId, count);
  touched.add(chatId);
  return count;
}

function incrementUnread(counts, touched, chatId) {
  if (!chatId) return 0;
  // Persisted counts can be stale after reads on another linked device. A new
  // bridge session starts its provisional counter at zero until a live chat
  // snapshot/event supplies the authoritative state.
  const current = touched.has(chatId) ? normalizeCount(counts.get(chatId)) : 0;
  return setUnread(counts, touched, chatId, current + 1);
}

function reconcileUnread(counts, touched, chatId, liveValue) {
  if (!chatId) return 0;
  if (touched.has(chatId)) return normalizeCount(counts.get(chatId));
  return setUnread(counts, touched, chatId, liveValue);
}

module.exports = { incrementUnread, reconcileUnread, setUnread };
