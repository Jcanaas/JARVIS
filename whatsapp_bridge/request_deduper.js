'use strict';

function createRequestDeduper(maxSize = 500) {
  const requests = new Map();

  async function run(key, operation) {
    const requestKey = String(key || '').trim();
    if (!requestKey) return operation();
    if (requests.has(requestKey)) return requests.get(requestKey);

    const pending = Promise.resolve().then(operation);
    requests.set(requestKey, pending);
    while (requests.size > maxSize) requests.delete(requests.keys().next().value);
    try {
      return await pending;
    } catch (error) {
      // A failed operation may be retried with the same key.
      if (requests.get(requestKey) === pending) requests.delete(requestKey);
      throw error;
    }
  }

  return { run, size: () => requests.size };
}

function recordedSendResponse(messages, clientRequestId) {
  const requestId = String(clientRequestId || '').trim();
  if (!requestId || !Array.isArray(messages)) return null;
  const entry = messages.find(item => (
    item && (item.event || 'message') === 'message' && item.clientRequestId === requestId
  ));
  if (!entry) return null;
  return {
    ok: true,
    id: entry.id,
    to: entry.chatId || entry.to,
    body: entry.body || '',
    type: entry.type || 'chat',
    ack: entry.ack ?? 0,
    deduplicated: true,
  };
}

module.exports = { createRequestDeduper, recordedSendResponse };
