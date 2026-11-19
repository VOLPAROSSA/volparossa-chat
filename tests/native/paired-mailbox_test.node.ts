// SPDX-License-Identifier: AGPL-3.0-only
import assert from 'node:assert/strict';
import { writeFile, readFile, mkdir, mkdtemp, readdir } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { createInterface } from 'node:readline';
import { promisify } from 'node:util';
import Database from '@signalapp/sqlcipher';
import { createHash } from 'node:crypto';
import { execFile, spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { Bootstrap } from '../bootstrap.node.ts';
import {
  acceptConversation,
  clickOnConversationWithAci,
  waitForEnabledComposer,
  typeIntoInput,
} from '../helpers.node.ts';
import { EnvelopeType, Proto, ServiceIdKind } from '@signalapp/mock-server';
import { contactByEncryptedUsernameRoute } from '../../util/signalRoutes.std.ts';
import type { App } from '../playwright.node.ts';
import type { Device } from '@signalapp/mock-server';
const phase = (name: string) => writeFile(process.env.CHAT_NATIVE_PHASE!, name);
async function register(bootstrap: Bootstrap, phone: string, name: string) {
  await bootstrap.init({ isStandalone: true });
  const app = await bootstrap.prepareForStandaloneRegistration();
  const page = await app.getWindow();
  page.setDefaultTimeout(14_000);
  const button = (name: string) => page.getByRole('button', { name });
  await typeIntoInput(page.getByPlaceholder('Phone number'), phone, '');
  await button('Continue').click();
  await button('Yes').click();
  await button('Verify in Browser').click();
  const challenge = await app.waitForChallenge();
  assert.equal(challenge.reason, 'standalone registration');
  await app.solveChallenge({ seq: challenge.seq, data: { captcha: 'unused' } });
  for (let n = 1; n <= 6; n++) {
    await typeIntoInput(page.getByLabel(`Character ${n} of 6`), '1', '');
  }
  await button('Continue').click();
  await typeIntoInput(page.getByPlaceholder('First name (required)'), name, '');
  await button('Continue').click();
  for (const prompt of ['Create your PIN', 'Enter your PIN']) {
    await typeIntoInput(page.getByPlaceholder(prompt), '876543', '');
    await button('Continue').click();
  }
  const stored = await app.waitForSVRStore();
  assert.equal(stored?.pin, '876543');
  await page.getByText('Welcome to Signal').waitFor();
  return app;
}
async function openPeer(
  app: App,
  boot: Bootstrap,
  remote: Device,
  username: string
) {
  await boot.server.setUsername(remote.aci, username);
  const { entropy, serverId } = await boot.server.setUsernameLink(
    remote.aci,
    username
  );
  const link = contactByEncryptedUsernameRoute
    .toWebUrl({
      encryptedUsername: Buffer.concat([entropy, serverId]).toString(
        'base64url'
      ),
    })
    .toString();
  const page = await app.getWindow();
  await page.evaluate(url => {
    if (!window.SignalCI) throw new Error('Native test API is unavailable');
    return window.SignalCI.openSignalRoute(url);
  }, link);
  await page.getByTestId('conversation-hero').getByText(username).waitFor();
}

async function send(app: App, body: string) {
  const input = await waitForEnabledComposer(await app.getWindow());
  await typeIntoInput(input, body, '');
  await input.press('Enter');
}
async function until<T>(observe: () => Promise<T | undefined>, seconds = 38) {
  const end = performance.now() + seconds * 1000;
  do {
    const result = await observe();
    if (result !== undefined) return result;
    await delay(150);
  } while (performance.now() < end);
  throw Error('Expected native handoff or SQL row was not observed');
}
async function inspectNative<T>(
  bootstrap: Bootstrap,
  read: (db: Database) => T
): Promise<T> {
  // Only the two disposable standalone test profiles are available inside this namespace.
  const profile = dirname(bootstrap.logsDir);
  const config = JSON.parse(
    await readFile(join(profile, 'config.json'), 'utf8')
  );
  assert.equal(typeof config.key, 'string');
  assert(/^[0-9a-f]{64}$/.test(config.key));
  const db = new Database(join(profile, 'sql/db.sqlite'));
  try {
    db.pragma(`key = "x'${config.key}'"`);
    db.pragma('query_only = ON');
    return read(db);
  } finally {
    db.close();
  }
}
function previousProofs(): Array<string> {
  return Array.from({ length: 256 }, (_, index) =>
    createHash('sha256')
      .update(`previous synthetic import ${index}`)
      .digest('hex')
  );
}

// Only seed the previous release's application-owned item in a closed,
// disposable SQLCipher profile. Messages, peer sessions and status rows are
// created exclusively by the real Desktops.
async function seedPreviousMailbox(bootstrap: Bootstrap, mailbox: string) {
  const profile = dirname(bootstrap.logsDir);
  const { key } = JSON.parse(
    await readFile(join(profile, 'config.json'), 'utf8')
  );
  assert(typeof key === 'string' && /^[0-9a-f]{64}$/.test(key));
  const { expires } = JSON.parse(
    await readFile(join(mailbox, 'pair.json'), 'utf8')
  );
  const db = new Database(join(profile, 'sql/db.sqlite'));
  try {
    db.pragma(`key = "x'${key}'"`);
    const id = 'volparossaMailboxV1';
    const value = previousProofs().map(proof => ({
      id: proof,
      bodyDigest: proof,
      expires,
    }));
    db.prepare('INSERT INTO items (id, json) VALUES ($id, $json)').run({
      id,
      json: JSON.stringify({ id, value }),
    });
  } finally {
    db.close();
  }
}

async function ledgerCount(boot: Bootstrap): Promise<number> {
  return inspectNative(boot, db => {
    const total = db
      .prepare('SELECT count(*) AS n FROM volparossa_mailbox')
      .get()?.n;
    assert(typeof total === 'number');
    assert.equal(
      db
        .prepare(
          "SELECT count(*) AS n FROM items WHERE id='volparossaMailboxV1'"
        )
        .get()?.n,
      0
    );
    const migrated = db
      .prepare(
        "SELECT id FROM volparossa_mailbox WHERE json_extract(json, '$.bodyDigest') = id ORDER BY id"
      )
      .all()
      .map(row => row.id);
    assert.deepEqual(migrated, previousProofs().sort());
    return total;
  });
}

async function ratchet(bootstrap: Bootstrap, self: Device, remote: Device) {
  return inspectNative(bootstrap, db => {
    const row = db
      .prepare(
        'SELECT record FROM sessions WHERE ourServiceId=$local AND serviceId=$remote AND deviceId=$device'
      )
      .get({ local: self.aci, remote: remote.aci, device: remote.deviceId });
    assert(
      row &&
        (row.record instanceof Uint8Array || typeof row.record === 'string')
    );
    return createHash('sha256').update(row.record).digest('hex');
  });
}
async function device(d: Device) {
  return {
    aci: d.aci,
    device: d.deviceId,
    registration: d.getCheckedRegistrationId(ServiceIdKind.ACI),
    identity: Buffer.from(
      (await d.getIdentityKey(ServiceIdKind.ACI)).serialize()
    ).toString('hex'),
  };
}
async function configure(
  bootstrap: Bootstrap,
  name: string,
  self: Device,
  peer: Device,
  providerState = '/tmp/r/providers',
  replacing = false
) {
  const dir = join(dirname(bootstrap.logsDir), 'volparossa-mailbox');
  const input = join(providerState, name);
  await mkdir(dir, { mode: 0o700, recursive: replacing });
  for (const sub of ['outbox', 'custody', 'inbox', 'imported'])
    await mkdir(join(dir, sub), { mode: 0o700, recursive: replacing });
  const grants = JSON.parse(await readFile(join(input, 'grants.json'), 'utf8'));
  const pair = {
    version: 1,
    local: await device(self),
    remote: await device(peer),
    ...grants,
  };
  await writeFile(
    join(dir, 'core.json'),
    await readFile(join(input, 'core.json')),
    { mode: 0o600, flag: replacing ? 'w' : 'wx' }
  );
  await writeFile(join(dir, 'pair.json'), JSON.stringify(pair), {
    mode: 0o600,
    flag: replacing ? 'w' : 'wx',
  });
  return dir;
}
async function command(dir: string, operation: string) {
  const { stdout } = await promisify(execFile)(
    '/usr/bin/python3',
    ['-B', '/trial/signal_mailbox.py', operation, '--mailbox', dir],
    { timeout: 180000 }
  );
  const data = JSON.parse(stdout);
  assert.equal(data.operation, operation);
  assert(Number.isSafeInteger(data.packets));
  return data.packets as number;
}
async function readPacket(dir: string, stamp: number) {
  return until(async () => {
    const entries = (await readdir(join(dir, 'outbox'))).filter(k =>
      /^[0-9a-f]{64}$/.test(k)
    );
    for (const key of entries) {
      const bytes = await readFile(join(dir, 'outbox', key));
      const packet = JSON.parse(bytes.toString('utf8'));
      if (packet.timestamp === stamp) return { key, bytes, packet };
    }
    return undefined;
  });
}
async function observedRow(
  app: App,
  stamp: number,
  body: string,
  type: 'incoming' | 'outgoing',
  peer?: Device,
  expected?: string
) {
  return until(async () => {
    const rows = await app.getMessagesBySentAt(stamp);
    if (!rows.length) return undefined;
    assert.equal(rows.length, 1);
    const [row] = rows;
    assert(row);
    assert.equal(row.type, type);
    assert.equal(row.body, body);
    if (type === 'incoming') {
      assert.equal(row.sourceServiceId, peer!.aci);
      assert.equal(row.sourceDevice, peer!.deviceId);
    }
    if (
      expected &&
      row.sendStateByConversationId?.[row.conversationId]?.status !== expected
    )
      return undefined;
    assert(row.id);
    return row.id;
  });
}
async function outgoingTimestamp(boot: Bootstrap, body: string) {
  return until(async () =>
    inspectNative(boot, sql => {
      const rows = sql
        .prepare(
          "SELECT sent_at FROM messages WHERE type='outgoing' AND body = $body"
        )
        .all({ body });
      if (!rows.length) return undefined;
      assert.equal(rows.length, 1);
      const value = rows[0]?.sent_at;
      assert(
        typeof value === 'number' && Number.isSafeInteger(value) && value > 0
      );
      return value;
    })
  );
}

async function startOwnerWorker(dir: string) {
  const child = spawn(
    '/usr/bin/python3',
    [
      '-B',
      '/trial/signal_mailbox.py',
      'run',
      '--mailbox',
      dir,
      '--poll-seconds',
      '1',
    ],
    { stdio: ['ignore', 'pipe', 'ignore'] }
  );
  const exited = new Promise<{
    code: number | null;
    signal: NodeJS.Signals | null;
  }>((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code, signal) => resolve({ code, signal }));
  });
  const lines = createInterface({ input: child.stdout });
  try {
    await Promise.race([
      new Promise<void>((resolve, reject) =>
        lines.once('line', line => {
          try {
            assert.deepEqual(JSON.parse(line), {
              operation: 'run',
              state: 'running',
            });
            resolve();
          } catch (error) {
            reject(error);
          }
        })
      ),
      exited.then(() => {
        throw Error('Worker exited before readiness');
      }),
    ]);
  } catch (error) {
    child.kill();
    throw error;
  } finally {
    lines.close();
    child.stdout.resume();
  }
  return async (signal: NodeJS.Signals = 'SIGTERM') => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    child.kill(signal);
    const result = await exited;
    if (signal === 'SIGKILL') assert.equal(result.signal, signal);
    else assert.equal(result.code, 0);
  };
}

async function providerInbox(dir: string) {
  const cfg = JSON.parse(await readFile(join(dir, 'core.json'), 'utf8'));
  const root = await mkdtemp('/tmp/r/worker-observation-');
  const { stdout } = await promisify(execFile)(
    cfg.executable,
    [
      '--control-socket',
      cfg.socket,
      'content',
      'mailbox',
      'fetch',
      '--invitation',
      cfg.receiveInvitation,
      '--identity',
      cfg.identity,
      '--passphrase-file',
      cfg.passphrase,
      '--output-dir',
      join(root, 'messages'),
    ],
    { timeout: 180000 }
  );
  const result = JSON.parse(stdout);
  assert.equal(result.listed_providers, 2);
  assert.equal(result.acknowledged_providers_per_message, 0);
  assert.equal(result.consumer_import_attested, false);
  const packets: Buffer[] = [];
  for (const item of result.pending) packets.push(await readFile(item.payload));
  assert.equal(packets.length, result.messages_fetched);
  return packets;
}

async function ownerTransfer(
  a: Bootstrap,
  b: Bootstrap,
  alice: App,
  da: Device,
  left: string,
  right: string
) {
  const workers: Array<(signal?: NodeJS.Signals) => Promise<void>> = [];
  const start = async (dir: string) => {
    const stop = await startOwnerWorker(dir);
    workers.push(stop);
    return stop;
  };
  try {
    await phase('worker-outgoing');
    const body = 'Offline delivery by an enabled owner worker.';
    await send(alice, body);
    const stamp = await outgoingTimestamp(a, body);
    const original = await readPacket(left, stamp);
    assert.equal((await providerInbox(right)).length, 0);
    const stopFirst = await start(left);
    const senderId = await observedRow(
      alice,
      stamp,
      body,
      'outgoing',
      undefined,
      'Sent'
    );
    await alice.close();
    await stopFirst();

    await phase('worker-offline-pickup');
    const stopReceiver = await start(right);
    await until(async () =>
      (await readdir(join(right, 'inbox'))).includes(original.key)
        ? true
        : undefined
    );
    assert.deepEqual(
      await readFile(join(right, 'inbox', original.key)),
      original.bytes
    );
    await stopReceiver();
    const stillAtProviders = async () =>
      (await providerInbox(right)).some(packet =>
        packet.equals(original.bytes)
      );
    assert(await stillAtProviders());
    const recipient = await b.startApp();
    const incomingId = await observedRow(
      recipient,
      stamp,
      body,
      'incoming',
      da
    );
    await until(async () =>
      (await readdir(join(right, 'imported'))).includes(original.key)
        ? true
        : undefined
    );
    assert(await stillAtProviders());

    await phase('worker-native-confirmation');
    await start(right);
    await until(async () =>
      !(await readdir(join(right, 'inbox'))).includes(original.key)
        ? true
        : undefined
    );
    assert.equal(await stillAtProviders(), false);
    await until(
      async () => ((await providerInbox(left)).length > 0 ? true : undefined),
      70
    );
    const stopInterrupted = await start(left);
    await until(async () =>
      (await readdir(join(left, 'inbox'))).length > 0 ? true : undefined
    );
    await stopInterrupted('SIGKILL');
    await start(left);
    const sender = await a.startApp();
    await phase('worker-sender-restart');
    assert.equal(
      await observedRow(
        sender,
        stamp,
        body,
        'outgoing',
        undefined,
        'Delivered'
      ),
      senderId
    );
    assert.equal(
      await observedRow(recipient, stamp, body, 'incoming', da),
      incomingId
    );
    await until(async () => {
      for (const dir of [left, right]) {
        for (const name of [
          'outbox',
          'custody',
          'inbox',
          'imported',
          'retired',
          'transport/received',
        ]) {
          if ((await readdir(join(dir, name))).length) return undefined;
        }
      }
      return (await providerInbox(left)).length === 0 &&
        (await providerInbox(right)).length === 0
        ? true
        : undefined;
    }, 75);
    await sender.close();
    await recipient.close();
  } finally {
    for (const stop of workers.reverse()) await stop();
  }
}

async function volumeMessages(
  a: Bootstrap,
  b: Bootstrap,
  da: Device,
  db: Device
): Promise<{ messages: number; grants: number }> {
  let messages = 0;
  // Each signed invitation covers at most 64 distinct manifests. A user text
  // can prompt both a delivery and a read receipt in the reverse direction.
  for (let round = 0; round < 10; round++) {
    await phase(`volume-grant-${round + 1}`);
    const providerState = join('/tmp/r', `providers-volume-${round}`);
    await mkdir(providerState, { mode: 0o700 });
    const provider = spawn(
      '/trial/core/mailbox-provider-pair',
      [providerState],
      { stdio: ['pipe', 'ignore', 'ignore'] }
    );
    const exited = new Promise<number | null>((resolve, reject) => {
      provider.once('error', reject);
      provider.once('exit', resolve);
    });
    let roundComplete = false;
    try {
      await until(async () => {
        assert.equal(provider.exitCode, null);
        return (await readdir(providerState)).includes('ready')
          ? true
          : undefined;
      }, 60);
      const left = await configure(a, 'a', da, db, providerState, round > 0);
      const right = await configure(b, 'b', db, da, providerState, round > 0);
      const sender = await a.startApp();
      const recipient = await b.startApp();
      const senderPage = await sender.getWindow();
      await senderPage.locator('#LeftPane').waitFor();
      await (await recipient.getWindow()).locator('#LeftPane').waitFor();
      await clickOnConversationWithAci(senderPage, db.aci);
      const prefix = `Native volume ${round + 1}/`;
      const expected = Array.from(
        { length: 26 },
        (_, i) => prefix + String(i + 1).padStart(2, '0')
      );
      const sending = (async () => {
        for (const body of expected) await send(sender, body);
      })();
      let submissionError: unknown;
      void sending.catch(error => {
        submissionError = error;
      });
      const readBodies = (boot: Bootstrap, kind: string) =>
        inspectNative(boot, sql => {
          const rows = sql
            .prepare(
              'SELECT body, conversationId, sourceServiceId, sourceDevice, json FROM messages WHERE type = $kind AND body LIKE $pattern ORDER BY body'
            )
            .all({ kind, pattern: prefix + '%' });
          return rows.map(row => {
            assert(typeof row.json === 'string');
            const message = JSON.parse(row.json);
            assert(
              typeof row.body === 'string' &&
                typeof row.conversationId === 'string'
            );
            const status =
              message.sendStateByConversationId?.[row.conversationId]?.status;
            return {
              body: row.body,
              status,
              source: row.sourceServiceId,
              device: row.sourceDevice,
            };
          });
        });
      let packets = 0;
      await until(async () => {
        if (submissionError) throw submissionError;
        const sent = await Promise.all([
          command(left, 'deposit'),
          command(right, 'deposit'),
        ]);
        packets += sent[0];
        await Promise.all([command(right, 'fetch'), command(left, 'fetch')]);
        await Promise.all([
          command(right, 'confirm'),
          command(left, 'confirm'),
        ]);
        const [outgoing, incoming] = await Promise.all([
          readBodies(a, 'outgoing'),
          readBodies(b, 'incoming'),
        ]);
        if (
          incoming.length < expected.length ||
          outgoing.length < expected.length
        )
          return undefined;
        assert.deepEqual(
          incoming.map(m => m.body),
          expected
        );
        assert.deepEqual(
          outgoing.map(m => m.body),
          expected
        );
        assert(
          incoming.every(m => m.source === da.aci && m.device === da.deviceId)
        );
        if (
          !outgoing.every(m =>
            ['Delivered', 'Read', 'Viewed'].includes(m.status)
          )
        )
          return undefined;
        for (const dir of [left, right]) {
          for (const name of [
            'inbox',
            'outbox',
            'custody',
            'retired',
            'transport/received',
          ]) {
            if ((await readdir(join(dir, name))).length > 0) return undefined;
          }
        }
        return true;
      }, 720);
      await sending;
      assert(packets >= expected.length);
      assert.equal(await command(left, 'fetch'), 0);
      assert.equal(await command(right, 'fetch'), 0);
      await sender.close();
      await recipient.close();
      messages += expected.length;
      roundComplete = true;
    } finally {
      provider.stdin.end();
      const exitCode = await exited;
      // Preserve the original trial error if a provider also failed to exit cleanly.
      if (roundComplete) assert.equal(exitCode, 0);
    }
  }
  return { messages, grants: 10 };
}

describe('native paired Signal mailbox', function () {
  this.timeout(process.env.CHAT_NATIVE_VOLUME === '1' ? 4_000_000 : 240000);
  it('recovers one offline message exactly once after native durable import', async () => {
    const a = new Bootstrap({ linkedDevices: 0, contactCount: 0 });
    const b = new Bootstrap({
      server: a.server,
      linkedDevices: 0,
      contactCount: 0,
    });
    const devices: Device[] = [];
    let receiverRecords = 0,
      senderRecords = 0;
    const create = a.server.registerDevice.bind(a.server);
    a.server.registerDevice = async opts => {
      const dev = await create(opts);
      if (!opts.primary) devices.push(dev);
      return dev;
    };
    const ordinary = a.server.handleMessage.bind(a.server);
    let closed = false,
      stock = 0,
      forbidden = 0;
    a.server.handleMessage = async (
      source,
      kind,
      type,
      target,
      encrypted,
      timestamp
    ) => {
      if (!devices.includes(target))
        return ordinary(source, kind, type, target, encrypted, timestamp);
      if (closed) {
        forbidden++;
        throw new Error('Paired stock transport disabled');
      }
      stock++;
      const wireType = new Map([
        [EnvelopeType.CipherText, Proto.Envelope.Type.DOUBLE_RATCHET],
        [EnvelopeType.PreKey, Proto.Envelope.Type.PREKEY_MESSAGE],
        [EnvelopeType.SealedSender, Proto.Envelope.Type.UNIDENTIFIED_SENDER],
        [EnvelopeType.Plaintext, Proto.Envelope.Type.PLAINTEXT_CONTENT],
      ]).get(type);
      assert(wireType);
      const content = Proto.Envelope.encode({
        type: wireType,
        sourceServiceIdBinary: source?.aciBinary ?? null,
        sourceDeviceId: source?.deviceId ?? null,
        destinationServiceIdBinary:
          target.getServiceIdBinaryByKind(kind) ?? null,
        serverTimestamp: timestamp,
        clientTimestamp: timestamp,
        content: encrypted,
        urgent: null,
        serverGuid: null,
        ephemeral: null,
        story: null,
        reportSpamToken: null,
        serverGuidBinary: null,
        updatedPniBinary: null,
        sourceServiceId: null,
        destinationServiceId: null,
        updatedPni: null,
      });
      await a.server.send(target, Buffer.from(content));
    };
    try {
      await phase('register-a');
      const alice = await register(a, '+14155551111', 'Owner One');
      await phase('register-b');
      const bob = await register(b, '+14155551112', 'Owner Two');
      assert.equal(devices.length, 2);
      const [da, db] = devices;
      assert(da && db);
      assert.equal(da.deviceId, 1);
      assert.equal(db.deviceId, 1);
      await phase('open-before');
      await openPeer(alice, a, db, 'OwnerTwo.42');
      await openPeer(bob, b, da, 'OwnerOne.42');
      await phase('warm-send');
      await send(alice, 'Ordinary baseline one');
      const window = await bob.getWindow();
      await phase('warm-receive');
      await window
        .locator('.ConversationView')
        .getByText('Ordinary baseline one', { exact: true })
        .waitFor();
      await phase('warm-reply');
      const accept = window.getByRole('button', {
        name: 'Accept',
        exact: true,
      });
      if (await accept.isVisible()) {
        await acceptConversation(window);
      }
      await send(bob, 'Ordinary baseline two');
      await (await alice.getWindow())
        .locator('.ConversationView')
        .getByText('Ordinary baseline two', { exact: true })
        .waitFor();
      await phase('stock-acknowledgements');
      for (const profile of [a, b]) {
        await until(async () => {
          const jobs = await inspectNative(profile, db =>
            db
              .prepare(
                "SELECT count(*) AS n FROM jobs WHERE queueType='conversation'"
              )
              .get()
          );
          return jobs?.n === 0 ? true : undefined;
        });
      }
      // Both profiles were visible during setup. Let the application's receipt batcher
      // finish before shutting down the receiver and changing the available transport.
      await delay(1500);
      await phase('pair');
      assert(stock >= 2);
      if (process.env.CHAT_NATIVE_VOLUME === '1') {
        await alice.close();
        await bob.close();
        closed = true;
        const { messages, grants } = await volumeMessages(a, b, da, db);
        assert.equal(messages, 260);
        assert.equal(forbidden, 0);
        await writeFile(
          process.env.CHAT_NATIVE_RESULT!,
          JSON.stringify({
            version: 1,
            standalone_desktops: 2,
            migrated_fixture_rows: 0,
            distinct_native_texts: messages,
            recipient_sqlcipher_rows: messages,
            sender_rows_delivered: messages,
            enrolled_grant_pairs: grants,
            providers_per_grant: 2,
            paired_stock_submissions: forbidden,
            settled_handoffs_removed: true,
            provider_inboxes_empty: true,
          }),
          { flag: 'wx', mode: 0o600 }
        );
        return;
      }
      const left = await configure(a, 'a', da, db);
      const right = await configure(b, 'b', db, da);
      await bob.close();
      if (process.env.CHAT_NATIVE_WORKER === '1') {
        closed = true;
        await ownerTransfer(a, b, alice, da, left, right);
        assert.equal(forbidden, 0);
        await writeFile(
          process.env.CHAT_NATIVE_RESULT!,
          JSON.stringify({
            version: 1,
            standalone_desktops: 2,
            mailbox_providers: 2,
            receiver_offline_during_deposit: true,
            sender_offline_during_pickup: true,
            worker_stopped_before_native_import: true,
            custody_retained_while_worker_stopped: true,
            worker_killed_after_pickup: true,
            original_row_delivered_after_restart: true,
            original_incoming_row_retained: true,
            paired_stock_submissions: forbidden,
            both_provider_inboxes_empty: true,
            settled_handoffs_removed: true,
          }),
          { flag: 'wx', mode: 0o600 }
        );
        return;
      }
      await seedPreviousMailbox(b, right);
      closed = true;
      await phase('offline-send');
      const body = 'Private offline synthetic message.';
      await send(alice, body);
      const stamp = await outgoingTimestamp(a, body);
      const outgoing = await readPacket(left, stamp);
      assert.equal(outgoing.packet.from.aci, da.aci);
      assert.equal(outgoing.packet.to.aci, db.aci);
      assert(!outgoing.bytes.includes(Buffer.from(body)));
      const ts = outgoing.packet.timestamp;
      await phase('provider-custody');
      let deposited = await command(left, 'deposit');
      assert(deposited >= 1);
      const outgoingId = await observedRow(
        alice,
        ts,
        body,
        'outgoing',
        undefined,
        'Sent'
      );
      // The conversation may enqueue a previously authorized profile update.
      // Wait until all native jobs have confirmed custody and completed cleanup.
      await until(async () => {
        deposited += await command(left, 'deposit');
        const busy = await inspectNative(
          a,
          sql =>
            sql
              .prepare(
                "SELECT count(*) AS n FROM jobs WHERE queueType='conversation'"
              )
              .get()?.n
        );
        return busy === 0 &&
          !(await readdir(join(left, 'outbox'))).length &&
          !(await readdir(join(left, 'custody'))).length
          ? true
          : undefined;
      }, 65);
      assert.equal(await command(left, 'deposit'), 0);
      await alice.close();
      await seedPreviousMailbox(a, left);
      await phase('offline-fetch');
      assert.equal(await command(right, 'fetch'), deposited);
      assert.equal(await command(right, 'confirm'), 0);
      assert.equal(await command(right, 'fetch'), deposited);
      assert.deepEqual(
        await readFile(join(right, 'inbox', outgoing.key)),
        outgoing.bytes
      );
      await phase('recipient-reopen');
      let recipient = await b.startApp();
      let page = await recipient.getWindow();
      await page.locator('#LeftPane').waitFor();
      const incomingId = await observedRow(recipient, ts, body, 'incoming', da);
      await phase('confirm-native-import');
      await until(async () =>
        (await readdir(join(right, 'imported'))).includes(outgoing.key)
          ? true
          : undefined
      );
      const confirmations = await command(right, 'confirm');
      assert(confirmations >= 1);
      await until(async () => {
        await command(right, 'confirm');
        return (await command(right, 'fetch')) === 0 ? true : undefined;
      });
      assert.equal(await command(right, 'confirm'), 0);
      assert.equal(await command(right, 'fetch'), 0);
      await phase('peer-receipt');
      // A contact can also send its already authorized profile update. Deposit every
      // real packet and wait for the native receipt sender to finish this exact message.
      let receiptSent = false;
      const deliveryEvent = (async () => {
        for (let i = 0; i < 8; i++) {
          const event = await recipient.waitForReceipts();
          if (
            event.type === 'deliveryReceipt' &&
            event.timestamps.includes(ts)
          ) {
            receiptSent = true;
            return;
          }
        }
        throw Error('Native sender did not report the exact delivery receipt');
      })();
      void deliveryEvent.catch(() => undefined);
      await until(async () => {
        await command(right, 'deposit');
        return receiptSent ? true : undefined;
      }, 65);
      await deliveryEvent;
      await until(async () => {
        await command(right, 'deposit');
        return !(await readdir(join(right, 'outbox'))).length &&
          !(await readdir(join(right, 'custody'))).length
          ? true
          : undefined;
      }, 65);
      receiverRecords = await ledgerCount(b);
      assert(receiverRecords > 256);
      await recipient.close();
      const initialSession = await ratchet(b, db, da);
      await phase('sender-reopen');
      const returning = await command(left, 'fetch');
      assert(returning >= 1);
      assert.equal(await command(left, 'confirm'), 0);
      const sender = await a.startApp();
      await (await sender.getWindow()).locator('#LeftPane').waitFor();
      assert.equal(
        await observedRow(sender, ts, body, 'outgoing', undefined, 'Delivered'),
        outgoingId
      );
      await until(async () =>
        (await readdir(join(left, 'imported'))).length === returning
          ? true
          : undefined
      );
      assert.equal(await command(left, 'confirm'), returning);
      assert.equal(await command(left, 'fetch'), 0);
      senderRecords = await ledgerCount(a);
      assert(senderRecords > 256);
      // Both provider transfers and receiver handoffs were fully retired.
      // Re-present the exact original ciphertext as a late duplicate.
      for (const dir of [
        'inbox',
        'imported',
        'transport/received',
        'transport/confirmed',
      ]) {
        assert.deepEqual(await readdir(join(right, dir)), []);
      }
      await writeFile(join(right, 'inbox', outgoing.key), outgoing.bytes, {
        flag: 'wx',
        mode: 0o600,
      });
      recipient = await b.startApp();
      page = await recipient.getWindow();
      await until(async () =>
        (await readdir(join(right, 'imported'))).includes(outgoing.key)
          ? true
          : undefined
      );
      assert.equal(await ratchet(b, db, da), initialSession);
      await clickOnConversationWithAci(page, da.aci);
      await page
        .locator('.ConversationView')
        .getByText(body, { exact: true })
        .waitFor();
      assert.equal(
        await observedRow(recipient, ts, body, 'incoming', da),
        incomingId
      );
      await recipient.close();
      assert.equal(forbidden, 0);
      await sender.close();
      await phase('complete');
    } finally {
      await b.teardown();
      await a.teardown();
    }
    await writeFile(
      process.env.CHAT_NATIVE_RESULT!,
      JSON.stringify({
        version: 1,
        standalone_desktops: 2,
        previous_records_per_profile: 256,
        both_profiles_added_native_records_past_256:
          senderRecords > 256 && receiverRecords > 256,
        settled_handoffs_removed: true,
        mailbox_providers: 2,
        stock_messaging_before_pairing: true,
        receiver_offline_during_deposit: true,
        sender_offline_during_pickup: true,
        repeat_fetch_before_import: true,
        confirm_before_import: 0,
        native_row_survived_restart: true,
        repeat_import_preserved_ratchet: true,
        delivered_after_sender_restart: true,
        paired_stock_submissions: forbidden,
        both_provider_inboxes_empty: true,
      }),
      { flag: 'wx', mode: 0o600 }
    );
  });
});
