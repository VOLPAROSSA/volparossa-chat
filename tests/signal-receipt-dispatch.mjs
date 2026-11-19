// SPDX-License-Identifier: AGPL-3.0-only
// Runs the pinned receiver method with synthetic envelope/event boundaries.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { stripTypeScriptTypes } from 'node:module';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';
import { toNumber } from '../build/signal-backup-candidate/ts/util/toNumber.std.ts';

const candidate = new URL('../build/signal-backup-candidate/ts/textsecure/MessageReceiver.preload.ts', import.meta.url);
const source = await readFile(candidate, 'utf8');
const start = source.indexOf('  async #handleReceiptMessage(');
const end = source.indexOf('\n  #handleTypingMessage(', start);
assert.ok(start >= 0 && end > start);
assert.equal(createHash('sha256').update(source.slice(start, end)).digest('hex'),
  '7e0bd650b9b42c1fc78bbfa833ff141cdf49595fd7e4d05104f6b5c9a9556d04');

class CapturedReceipt {
  constructor(rows, envelopeId, timestamp, confirm) {
    Object.assign(this, { rows, envelopeId, timestamp, confirm });
  }
}
class DeliveryEvent extends CapturedReceipt {}
class ReadEvent extends CapturedReceipt {}
class ViewEvent extends CapturedReceipt {}
const Type = { DELIVERY: 0, READ: 1, VIEWED: 2 };
const Receiver = runInNewContext(stripTypeScriptTypes(`(class Receiver {
  events = [];
  removed = [];
  handle(envelope, receipt) { return this.#handleReceiptMessage(envelope, receipt); }
  #dispatchAndWait(_logId, event) { this.events.push(event); }
  #removeFromCache(envelope) { this.removed.push(envelope.id); }
  ${source.slice(start, end)}
})`), { Proto: { ReceiptMessage: { Type } }, DeliveryEvent, ReadEvent, ViewEvent, toNumber,
  strictAssert: assert.ok, getEnvelopeId: envelope => envelope.id, logUnexpectedUrgentValue() {} });

const envelope = { id: 'fixture-1', source: 'fixture-source', sourceServiceId: 'fixture-contact',
  sourceDevice: 1, timestamp: 900 };

test('each encrypted receipt type dispatches one row per exact timestamp in first-seen order', async () => {
  for (const [type, eventClass] of [[Type.DELIVERY, DeliveryEvent], [Type.READ, ReadEvent], [Type.VIEWED, ViewEvent]]) {
    const receiver = new Receiver();
    await receiver.handle(envelope, { type, timestamp: [20n, 10n, 20n, 10n, 30n] });
    assert.equal(receiver.events.length, 1);
    const [event] = receiver.events;
    assert.ok(event instanceof eventClass);
    assert.equal(event.envelopeId, envelope.id);
    assert.equal(event.timestamp, envelope.timestamp);
    assert.deepEqual(Array.from(event.rows, row => row.timestamp), [20, 10, 30]);
    for (const row of event.rows) {
      assert.deepEqual({ ...row }, { timestamp: row.timestamp, source: envelope.source,
        sourceServiceId: envelope.sourceServiceId, sourceDevice: 1, wasSentEncrypted: true });
    }
    assert.equal(receiver.removed.length, 0);
    event.confirm();
    assert.deepEqual(Array.from(receiver.removed), [envelope.id]);
  }
});

test('receipts from linked devices and later envelopes retain their separate events', async () => {
  const receiver = new Receiver();
  for (const [id, device] of [['one', 1], ['two', 2], ['three', 1]]) {
    await receiver.handle({ ...envelope, id, sourceDevice: device }, { type: Type.DELIVERY, timestamp: [17n, 17n] });
  }
  assert.deepEqual(Array.from(receiver.events, event => [event.envelopeId, event.rows.length,
    event.rows[0].sourceDevice, event.rows[0].timestamp]), [['one', 1, 1, 17], ['two', 1, 2, 17], ['three', 1, 1, 17]]);
});

test('exact wire values are compared before conversion and unknown types keep upstream handling', async () => {
  const receiver = new Receiver();
  const large = 2n ** 53n;
  await receiver.handle(envelope, { type: Type.READ, timestamp: [large, large + 1n, large] });
  assert.equal(receiver.events[0].rows.length, 2);
  await receiver.handle(envelope, { type: Type.READ, timestamp: [] });
  assert.equal(receiver.events[1].rows.length, 0);
  await receiver.handle(envelope, { type: 99, timestamp: [11n, 11n] });
  assert.equal(receiver.events.length, 2);
  assert.equal(receiver.removed.length, 0);
});
