// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only

import {
  CiphertextMessageType,
  ProtocolAddress,
  SignalMessage,
} from '@signalapp/libsignal-client';
import type { CiphertextMessage } from '@signalapp/libsignal-client';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { readdir } from 'node:fs/promises';
import {
  SendStatus,
  isDelivered,
  isRead,
  isViewed,
} from '../../messages/MessageSendState.std.ts';
import { DataReader } from '../../sql/Client.preload.ts';
import { Address } from '../../types/Address.std.ts';
import { QualifiedAddress } from '../../types/QualifiedAddress.std.ts';
import type { ServiceIdString } from '../../types/ServiceId.std.ts';
import { Sessions, IdentityKeys } from '../../LibSignalStores.node.ts';
import { signalProtocolStore } from '../../SignalProtocolStore.preload.ts';
import { itemStorage } from '../../textsecure/Storage.preload.ts';
import { Zone } from '../../util/Zone.std.ts';
import {
  decodePacket,
  encodePacket,
  sameDevice,
  MAX_PACKET_BYTES,
  mailboxRows,
} from './packet.node.ts';
import type { MailboxPacket, MailboxRow } from './packet.node.ts';
import {
  absent,
  check,
  digest,
  directory,
  pairAt,
  publish,
  readPrivate,
} from './files.node.ts';
import type { MailboxPair } from './files.node.ts';

export function mailboxRoot(): string {
  return join(window.SignalContext.getPath('userData'), 'volparossa-mailbox');
}
export async function rowFor(id: string): Promise<MailboxRow | undefined> {
  const entries = await DataReader.getItemById('volparossaMailboxV1');
  return (
    entries &&
    mailboxRows
      .parse(entries.value)
      .find((row) => row.id === id && row.expires > Date.now())
  );
}
async function checkIdentity(pair: MailboxPair): Promise<void> {
  check(
    pair.expires > Date.now() && pair.expires <= Date.now() + 31 * 86400_000,
  );
  const ourAci = itemStorage.user.getCheckedAci();
  const ours = signalProtocolStore.getIdentityKeyPair(ourAci);
  const theirs = await signalProtocolStore.loadIdentityKey(pair.remote.aci);
  check(
    ourAci === pair.local.aci &&
      itemStorage.user.getCheckedDeviceId() === pair.local.device &&
      (await signalProtocolStore.getLocalRegistrationId(ourAci)) ===
        pair.local.registration &&
      ours &&
      Buffer.from(ours.publicKey.serialize()).toString('hex') ===
        pair.local.identity &&
      theirs &&
      Buffer.from(theirs).toString('hex') === pair.remote.identity,
  );
}
export async function strictPair(
  serviceId: ServiceIdString,
): Promise<MailboxPair | undefined> {
  const pair = await pairAt(mailboxRoot());
  if (!pair || pair.remote.aci !== serviceId) {
    return undefined;
  }
  await checkIdentity(pair);
  return pair;
}

type Encrypt = (stores: {
  identityKeyStore: IdentityKeys;
  sessionStore: Sessions;
  destinationAddress: ProtocolAddress;
  localAddress: ProtocolAddress;
}) => Promise<CiphertextMessage>;

export async function sendPacket(
  pair: MailboxPair,
  timestamp: number,
  plaintext: Uint8Array<ArrayBuffer>,
  receipt: MailboxRow['receipt'],
  encrypt: Encrypt,
): Promise<number> {
  const intent = digest(plaintext);
  const id = digest(
    JSON.stringify([
      'outbound',
      pair.sendGrant,
      pair.local,
      pair.remote,
      receipt ?? timestamp,
    ]),
  );
  const qualified = new QualifiedAddress(
    pair.local.aci,
    new Address(pair.remote.aci, pair.remote.device),
  );
  const zone = new Zone('VolparossaMailboxSend', { pendingSessions: true });
  const packet = await signalProtocolStore.enqueueSessionJob(
    qualified,
    async () => {
      const stores = {
        signalProtocolStore,
        ourServiceId: pair.local.aci,
        zone,
      };
      const sessionStore = new Sessions(stores);
      const destinationAddress = ProtocolAddress.new(
        pair.remote.aci,
        pair.remote.device,
      );
      const session = await sessionStore.getSession(destinationAddress);
      check(
        session?.hasCurrentState() &&
          session.remoteRegistrationId() === pair.remote.registration,
      );
      const retained = await rowFor(id);
      if (retained) {
        check(retained.intent === intent && retained.packet);
        return Buffer.from(retained.packet, 'base64');
      }
      const ciphertext = await encrypt({
        sessionStore,
        identityKeyStore: new IdentityKeys(stores),
        destinationAddress,
        localAddress: ProtocolAddress.new(pair.local.aci, pair.local.device),
      });
      check(ciphertext.type() === CiphertextMessageType.Whisper);
      const bytes = encodePacket({
        version: 1,
        grant: pair.sendGrant,
        from: pair.local,
        to: pair.remote,
        timestamp,
        ciphertext: Buffer.from(ciphertext.serialize()).toString('base64'),
      });
      zone.mailboxRows.push({
        id,
        expires: pair.expires,
        intent,
        packet: Buffer.from(bytes).toString('base64'),
      });
      return bytes;
    },
    zone,
  );
  // This handoff follows the SQLCipher transaction that saved the advanced session and exact packet together.
  const root = mailboxRoot();
  const key = digest(packet);
  await publish(join(root, 'outbox', key), packet);
  // Provider custody and delivery are distinct. The normal send job retries this same packet until the Core CLI has deposited it.
  for (let n = 0; n < 30; n += 1) {
    try {
      const custody = await readPrivate(join(root, 'custody', key), 128);
      check(custody.toString('ascii') === `${pair.sendGrant}\n`);
      return pair.remote.device;
    } catch (e) {
      if (!absent(e)) {
        throw e;
      }
    }
    await delay(1000);
  }
  throw new Error('Awaiting authenticated mailbox deposit');
}

export type Receive = (packet: MailboxPacket, row: MailboxRow) => Promise<void>;
export async function importAvailable(receive: Receive): Promise<void> {
  const root = mailboxRoot();
  const pair = await pairAt(root);
  if (!pair) {
    return;
  }
  await checkIdentity(pair);
  const inbox = join(root, 'inbox');
  try {
    await directory(inbox);
  } catch (err) {
    if (absent(err)) {
      return;
    }
    throw err;
  }
  const entries = await readdir(inbox);
  check(entries.length <= 256);
  for (const key of entries) {
    if (key.startsWith('.')) {
      continue;
    }
    check(/^[a-f0-9]{64}$/.test(key));
    const bytes = await readPrivate(join(inbox, key), MAX_PACKET_BYTES);
    const packet = decodePacket(bytes);
    check(
      digest(bytes) === key &&
        packet.grant === pair.receiveGrant &&
        sameDevice(packet.from, pair.remote) &&
        sameDevice(packet.to, pair.local),
    );
    const session = await signalProtocolStore.loadSession(
      new QualifiedAddress(
        pair.local.aci,
        new Address(pair.remote.aci, pair.remote.device),
      ),
    );
    check(
      session?.hasCurrentState() &&
        session.remoteRegistrationId() === pair.remote.registration,
    );
    SignalMessage.deserialize(Buffer.from(packet.ciphertext, 'base64'));
    const id = digest(JSON.stringify(['inbound', pair.receiveGrant, key]));
    if (!(await rowFor(id))) {
      await receive(packet, { id, expires: pair.expires });
    }
    const committed = await rowFor(id);
    check(committed);
    if (committed.bodyDigest) {
      const messages = await DataReader.getMessagesBySentAt(packet.timestamp);
      if (
        !messages.some(
          (m) =>
            m.type === 'incoming' &&
            m.sourceServiceId === packet.from.aci &&
            m.sourceDevice === packet.from.device &&
            typeof m.body === 'string' &&
            digest(m.body) === committed.bodyDigest,
        )
      ) {
        continue;
      }
    }
    if (committed.profileDigest) {
      const conversation = window.ConversationController.get(packet.from.aci);
      if (!conversation) {
        continue;
      }
      const stored = await DataReader.getConversationById(conversation.id);
      if (
        !stored?.profileKey ||
        digest(Buffer.from(stored.profileKey, 'base64')) !==
          committed.profileDigest
      ) {
        continue;
      }
    }
    if (committed.receipt) {
      const conversation = window.ConversationController.get(packet.from.aci);
      check(conversation, 'Missing locally paired conversation');
      const done = { Delivered: isDelivered, Read: isRead, Viewed: isViewed }[
        committed.receipt.state
      ];
      const settled = await Promise.all(
        committed.receipt.timestamps.map(async (timestamp) => {
          const messages = await DataReader.getMessagesBySentAt(timestamp);
          return messages.some((m) => {
            const state =
              m.sendStateByConversationId?.[conversation.id]?.status;
            return (
              m.type === 'outgoing' &&
              m.conversationId === conversation.id &&
              state != null &&
              state !== SendStatus.Skipped &&
              done(state)
            );
          });
        }),
      );
      if (settled.some((result) => !result)) {
        continue;
      }
    }
    await publish(
      join(root, 'imported', key),
      Buffer.from(`${pair.receiveGrant}\n`),
    );
  }
}
