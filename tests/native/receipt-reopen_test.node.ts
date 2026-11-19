// SPDX-License-Identifier: AGPL-3.0-only
// Executed inside the pinned Signal Desktop test-mock/messaging directory.
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { ReceiptType, StorageState } from '@signalapp/mock-server';
import type { PrimaryDevice } from '@signalapp/mock-server';
import { Bootstrap } from '../bootstrap.node.ts';
import type { App } from '../bootstrap.node.ts';
import {
  clickOnConversation,
  typeIntoInput,
  waitForEnabledComposer,
  waitForNonProfileKeyUpdateMessage,
} from '../helpers.node.ts';

type Original = { timestamp: number; body: string; conversationId: string };

async function send(app: App, peer: PrimaryDevice, body: string): Promise<Original> {
  const page = await app.getWindow();
  await clickOnConversation(page, peer);
  const composer = await waitForEnabledComposer(page);
  await typeIntoInput(composer, body, '');
  await composer.press('Enter');
  const received = await waitForNonProfileKeyUpdateMessage(peer);
  assert.equal(received.body, body);
  const timestamp = Number(received.dataMessage.timestamp);
  assert(Number.isSafeInteger(timestamp) && timestamp > 0);
  const conversationId = await page.evaluate(
    serviceId => window.SignalCI?.getConversationId(serviceId), peer.device.aci
  );
  assert(conversationId);
  return { timestamp, body, conversationId };
}

async function readState(app: App, original: Original, expected: string): Promise<string> {
  const end = performance.now() + 30_000;
  do {
    const rows = await app.getMessagesBySentAt(original.timestamp);
    if (rows.length > 0) {
      assert.equal(rows.length, 1);
      const [row] = rows;
      assert.equal(row.type, 'outgoing');
      assert.equal(row.body, original.body);
      assert.equal(row.conversationId, original.conversationId);
      if (row.sendStateByConversationId?.[original.conversationId]?.status === expected) {
        assert(row.id);
        return row.id;
      }
    }
    await delay(100);
  } while (performance.now() < end);
  throw Error('expected recipient status was not observed in the database');
}

describe('native Signal receipt recovery', function () {
  this.timeout(180_000);

  it('retains recipient-bound encrypted receipts across a desktop restart', async () => {
    const bootstrap = new Bootstrap({ contactCount: 2, linkedDevices: 0 });
    const phase = (name: string) => writeFile(process.env.CHAT_NATIVE_PHASE!, name);
    await phase('bootstrap');
    await bootstrap.init();
    try {
      const [alice, bob] = bootstrap.contacts;
      let state = StorageState.getEmpty();
      for (const peer of [alice, bob]) {
        state = state.addContact(peer, {
          identityKey: peer.publicKey.serialize(), profileKey: peer.profileKey.serialize(),
          whitelisted: true, e164: peer.device.number, givenName: peer.profileName,
        }).pin(peer);
      }
      await bootstrap.phone.setStorageState(state);
      await phase('link');
      let app = await bootstrap.link();
      await phase('send');
      const first = await send(app, alice, 'Receipt fixture one');
      const second = await send(app, alice, 'Receipt fixture two');
      const control = await send(app, alice, 'Receipt fixture control');
      const sentinel = await send(app, bob, 'Receipt fixture sentinel');
      const messages = [first, second, control, sentinel];
      assert.equal(new Set(messages.map(m => m.timestamp)).size, 4);
      assert.notEqual(first.conversationId, sentinel.conversationId);
      await phase('initial-state');
      const ids = await Promise.all(messages.map(m => readState(app, m, 'Sent')));

      // Bob signs/encrypts a receipt for both conversations. Only his own message may change.
      await phase('peer-control');
      await bob.sendReceipt(bootstrap.desktop, {
        timestamp: bootstrap.getTimestamp(), type: ReceiptType.Delivery,
        messageTimestamps: [control.timestamp, sentinel.timestamp],
      });
      assert.equal(await readState(app, sentinel, 'Delivered'), ids[3]);
      assert.equal(await readState(app, control, 'Sent'), ids[2]);

      await phase('delivery');
      await alice.sendReceipt(bootstrap.desktop, {
        timestamp: bootstrap.getTimestamp(), type: ReceiptType.Delivery,
        messageTimestamps: [first.timestamp, first.timestamp, second.timestamp],
      });
      const expected = ['Delivered', 'Delivered', 'Sent', 'Delivered'];
      const states = () => Promise.all(messages.map((m, index) => readState(app, m, expected[index])));
      assert.deepEqual(await states(), ids);
      await phase('restart');
      await app.close();
      app = await bootstrap.startApp();
      await (await app.getWindow()).locator('#LeftPane').waitFor();
      assert.deepEqual(await states(), ids);
      await phase('cleanup');
      await app.close();
    } finally {
      await bootstrap.teardown();
    }
    // Only aggregate observations leave the synthetic profile.
    await writeFile(process.env.CHAT_NATIVE_RESULT!, JSON.stringify({
      version: 1, original_messages: 4, receipt_senders: 2, encrypted_receipts: 2,
      delivered_after_reopen: 3, unaddressed_control_stayed_sent: true,
      original_row_ids_retained: true, profile_reopened: true, teardown_completed: true,
      transport: 'loopback-signal-mock-service',
    }) + '\n', { flag: 'wx', mode: 0o600 });
  });
});
