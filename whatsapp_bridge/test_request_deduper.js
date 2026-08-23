const test = require('node:test');
const assert = require('node:assert/strict');

const { createRequestDeduper, recordedSendResponse } = require('./request_deduper');

test('concurrent requests with the same key execute the send once', async () => {
  const deduper = createRequestDeduper();
  let calls = 0;
  const operation = async () => {
    calls += 1;
    await new Promise(resolve => setTimeout(resolve, 10));
    return { id: 'wa-1' };
  };

  const [first, second] = await Promise.all([
    deduper.run('local-1', operation),
    deduper.run('local-1', operation),
  ]);

  assert.equal(calls, 1);
  assert.deepEqual(first, second);
});

test('completed requests remain idempotent', async () => {
  const deduper = createRequestDeduper();
  let calls = 0;

  await deduper.run('local-2', async () => ++calls);
  const result = await deduper.run('local-2', async () => ++calls);

  assert.equal(calls, 1);
  assert.equal(result, 1);
});

test('failed requests can be retried with the same key', async () => {
  const deduper = createRequestDeduper();
  let calls = 0;

  await assert.rejects(deduper.run('local-3', async () => {
    calls += 1;
    throw new Error('offline');
  }));
  const result = await deduper.run('local-3', async () => {
    calls += 1;
    return 'ok';
  });

  assert.equal(result, 'ok');
  assert.equal(calls, 2);
});

test('persisted message records prevent a duplicate after bridge restart', () => {
  const result = recordedSendResponse([{
    id: 'wa-4', event: 'message', clientRequestId: 'local-4',
    chatId: 'chat@c.us', body: 'hola', type: 'chat', ack: 2,
  }], 'local-4');

  assert.equal(result.id, 'wa-4');
  assert.equal(result.deduplicated, true);
  assert.equal(result.ack, 2);
});
