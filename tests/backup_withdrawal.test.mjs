// SPDX-License-Identifier: AGPL-3.0-only
// Real private-file rendezvous, synthetic acknowledgement; does not stop a real provider.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { waitForBackupWithdrawal } from '../overlay/ts/test-mock/backups/volparossa-withdrawal.node.ts';

const BUILD = resolve(import.meta.dirname, '../build');
const KEY = '11'.repeat(32);
async function fixture(body) {
  await mkdir(BUILD, { recursive: true, mode: 0o700 });
  const work = await mkdtemp(join(BUILD, 'withdraw-'));
  const config = join(work, 'config.json');
  await writeFile(config, JSON.stringify({ providers: [{ key: KEY }] }), { mode: 0o600 });
  try { await body({ work, config }); }
  finally { await rm(work, { recursive: true, force: true }); }
}
async function ready(work) {
  for (let n = 0; n < 100; n++) {
    try {
      const value = JSON.parse(await readFile(join(work, 'withdrawal-ready.json'), 'utf8'));
      assert.deepEqual(value, { version: 1, ready: true, provider_key: KEY });
      return;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      await new Promise(resolve => setTimeout(resolve, 5));
    }
  }
  assert.fail('ready marker absent');
}
async function acknowledge(work, value) {
  await writeFile(join(work, 'ack.tmp'), JSON.stringify(value), { mode: 0o600 });
  await rename(join(work, 'ack.tmp'), join(work, 'withdrawal-confirmed.json'));
}

test('withdrawal rendezvous requires exact new acknowledgement for configured provider A', async () => {
  await fixture(async ({ work, config }) => {
    const task = waitForBackupWithdrawal(work, config, 1000);
    await ready(work);
    await acknowledge(work, { version: 1, provider_stopped: true, provider_key: KEY });
    await task;
  });
  for (const value of [{ version: 1, provider_stopped: true, provider_key: '22'.repeat(32) },
    { version: 1, provider_stopped: false, provider_key: KEY },
    { version: 1, provider_stopped: true, provider_key: KEY, extra: true }]) {
    await fixture(async ({ work, config }) => {
      const rejected = assert.rejects(waitForBackupWithdrawal(work, config, 1000), /WITHDRAWAL_INVALID/);
      await ready(work); await acknowledge(work, value); await rejected;
    });
  }
});

test('stale acknowledgement, ready or part and timeout fail closed without entering restore', async () => {
  for (const name of ['withdrawal-confirmed.json', 'withdrawal-ready.json', 'withdrawal-ready.json.part']) {
    await fixture(async ({ work, config }) => {
      await writeFile(join(work, name), 'previous-rendezvous', { mode: 0o600 });
      await assert.rejects(waitForBackupWithdrawal(work, config), /WITHDRAWAL_STALE/);
      assert.equal(await readFile(join(work, name), 'utf8'), 'previous-rendezvous');
    });
  }
  await fixture(async ({ work, config }) => {
    await assert.rejects(waitForBackupWithdrawal(work, config, 10), /WITHDRAWAL_TIMEOUT/);
  });
});
