// SPDX-License-Identifier: AGPL-3.0-only
// Real file/process-boundary tests with synthetic containers and a TEST-ONLY CLI peer.
// These do not execute Signal crypto, Electron, or a real VOLPAROSSA route.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createServer } from 'node:net';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, writeFile, rm, symlink, chmod, access, open } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { bundleSnapshot, unpackAuthenticatedBundle } from '../overlay/ts/services/backups/volparossa/archive.node.ts';
import { checkedReport, replicaCommand, validateConfig } from '../overlay/ts/services/backups/volparossa/replicas.node.ts';

const BUILD = resolve(import.meta.dirname, '../build');
const SNAPSHOT = 'signal-backup-2026-09-29-12-00-00';
const MEDIA = 'ab'.repeat(32);
async function fixture(body) {
  await mkdir(BUILD, { recursive: true, mode: 0o700 });
  const root = await mkdtemp(join(BUILD, 'backup-unit-'));
  try {
    const native = join(root, 'native');
    const work = join(root, 'work');
    await mkdir(native, { mode: 0o700 });
    await mkdir(work, { mode: 0o700 });
    const snapshot = join(native, SNAPSHOT);
    await mkdir(snapshot, { mode: 0o700 });
    for (const name of ['main', 'metadata', 'files']) {
      await writeFile(join(snapshot, name), `synthetic-${name}`, { mode: 0o600 });
    }
    await mkdir(join(native, 'files', 'ab'), { recursive: true, mode: 0o700 });
    const attachment = join(native, 'files', 'ab', MEDIA);
    await writeFile(attachment, Buffer.alloc(700_003, 0xa5), { mode: 0o600 });
    await body({ root, native, work, snapshot, attachment, bundle: join(work, 'snapshot.bundle') });
  } finally { await rm(root, { recursive: true, force: true }); }
}

test('bounded complete snapshot container survives source removal with exact attachment bytes', async () => {
  await fixture(async ({ native, work, snapshot, bundle }) => {
    const size = await bundleSnapshot(snapshot, [MEDIA], bundle);
    assert.equal((await readFile(bundle)).length, size);
    await rm(native, { recursive: true });
    const restored = await unpackAuthenticatedBundle(bundle, join(work, 'restored'));
    assert.equal(await readFile(join(restored, 'main'), 'utf8'), 'synthetic-main');
    assert.deepEqual(await readFile(join(work, 'restored/files/ab', MEDIA)), Buffer.alloc(700_003, 0xa5));
    await assert.rejects(unpackAuthenticatedBundle(bundle, join(work, 'restored')));
    assert.equal(await readFile(join(restored, 'metadata'), 'utf8'), 'synthetic-metadata');
  });
});

test('missing required metadata, missing media, unsafe names and symlinks cannot form a complete snapshot', async () => {
  for (const mutate of [
    async f => { await rm(join(f.snapshot, 'metadata')); },
    async f => { await rm(f.attachment); },
    async f => { await rm(f.attachment); await symlink(join(f.snapshot, 'main'), f.attachment); },
  ]) {
    await fixture(async f => {
      await mutate(f);
      await assert.rejects(bundleSnapshot(f.snapshot, [MEDIA], f.bundle));
      await assert.rejects(access(f.bundle));
    });
  }
  await fixture(async f => {
    await assert.rejects(bundleSnapshot(f.snapshot, [MEDIA, MEDIA], f.bundle));
    await assert.rejects(bundleSnapshot(f.snapshot, ['../../private'], f.bundle));
  });
});

test('truncated, trailing, traversal and duplicate-key containers never publish a restore', async () => {
  await fixture(async f => {
    await bundleSnapshot(f.snapshot, [MEDIA], f.bundle);
    const original = await readFile(f.bundle);
    const headerLength = original.readUInt32BE(4);
    const header = JSON.parse(original.subarray(8, 8 + headerLength));
    const cases = [original.subarray(0, original.length - 1), Buffer.concat([original, Buffer.from('extra')])];
    for (const mutate of [h => { h.entries[0].name = '../escape'; }, h => { h.extra = 'not canonical'; }]) {
      const changed = structuredClone(header);
      mutate(changed);
      const data = Buffer.from(JSON.stringify(changed));
      const prefix = Buffer.from(original.subarray(0, 8));
      prefix.writeUInt32BE(data.length, 4);
      cases.push(Buffer.concat([prefix, data, original.subarray(8 + headerLength)]));
    }
    const duplicate = Buffer.from(original.subarray(8, 8 + headerLength).toString().replace('"version":1', '"version":1,"version":1'));
    const prefix = Buffer.from(original.subarray(0, 8));
    prefix.writeUInt32BE(duplicate.length, 4);
    cases.push(Buffer.concat([prefix, duplicate, original.subarray(8 + headerLength)]));
    for (let i = 0; i < cases.length; i += 1) {
      const bad = join(f.work, `bad-${i}`);
      const output = join(f.work, `out-${i}`);
      await writeFile(bad, cases[i], { mode: 0o600 });
      await assert.rejects(unpackAuthenticatedBundle(bad, output));
      await assert.rejects(access(output));
    }
  });
});

function report(config, operation, bytes = 524326) {
  return { operation: `private_storage_replicas_${operation}`, logical_ciphertext_bytes: bytes,
    operation_complete: true, read_consumes_archive: false,
    copies: config.providers.map(p => ({ provider_key: p.key, charge: 'committed' })) };
}

async function processFixture(body) {
  await fixture(async f => {
    const socket = join(f.work, 'control.sock');
    const server = createServer();
    const directory = await open(f.work, 'r');
    // Keep deep worktree fixtures within the workspace despite AF_UNIX's short address limit.
    server.listen(`/proc/self/fd/${directory.fd}/control.sock`);
    await once(server, 'listening');
    const config = { executable: join(f.work, 'test-cli'), controlSocket: socket,
      identity: join(f.work, 'owner'), passphraseFile: join(f.work, 'passphrase'),
      providers: [{ key: '11'.repeat(32), grant: join(f.work, 'grant-a') },
        { key: '22'.repeat(32), grant: join(f.work, 'grant-b') }], lifetimeSeconds: 3600 };
    for (const path of [config.identity, config.passphraseFile, ...config.providers.map(p => p.grant)]) {
      await writeFile(path, 'TEST-ONLY-SECRET', { mode: 0o600 });
    }
    const writeCli = async source => {
      await writeFile(config.executable, `#!${process.execPath}\n${source}\n`, { mode: 0o700 });
      await chmod(config.executable, 0o700);
    };
    try { await body(f, config, writeCli); }
    finally { await new Promise(resolve => server.close(resolve)); await directory.close(); }
  });
}

test('CLI boundary executes bounded argv, validates pinned identities and refuses incomplete reports', async () => {
  await processFixture(async (f, config, writeCli) => {
    const valid = report(config, 'deposit');
    await writeCli(`if (process.argv.some(a => a.includes('TEST-ONLY-SECRET'))) process.exit(3); console.log(${JSON.stringify(JSON.stringify(valid))});`);
    assert.deepEqual(await replicaCommand(config, 'deposit', join(f.work, 'replicas'), 524326), valid);
    for (const mutate of [r => { r.copies.reverse(); }, r => { r.logical_ciphertext_bytes++; },
      r => { r.operation_complete = false; }, r => { r.read_consumes_archive = true; }]) {
      const value = structuredClone(valid); mutate(value);
      assert.throws(() => checkedReport(value, 'deposit', config, 524326));
    }
    await assert.rejects(validateConfig({ ...config, providers: [config.providers[0], config.providers[0]] }));
    await writeCli("process.stderr.write('TEST-ONLY-SECRET'); process.exit(1);");
    await assert.rejects(replicaCommand(config, 'deposit', join(f.work, 'replicas'), 524326),
      error => error.message === 'VOLPAROSSA_BACKUP_TRANSFER_INCOMPLETE');
  });
});

test('cancel waits for the owned CLI child to exit and keeps pre-existing state', async () => {
  await processFixture(async (f, config, writeCli) => {
    await writeFile(join(f.work, 'retained'), 'original-journal');
    await writeCli("require('node:fs').writeFileSync('test.pid', String(process.pid)); setInterval(() => {}, 1000);");
    const controller = new AbortController();
    const command = replicaCommand(config, 'deposit', join(f.work, 'replicas'), 524326, [], controller.signal);
    // Attach immediately so a setup failure cannot become an unhandled rejection.
    const rejected = assert.rejects(command, /TRANSFER_INCOMPLETE/);
    let pid;
    for (let i = 0; i < 100; i += 1) {
      try { pid = Number(await readFile(join(f.work, 'test.pid'), 'utf8')); break; }
      catch { await new Promise(resolve => setTimeout(resolve, 10)); }
    }
    assert.ok(pid > 0);
    controller.abort();
    await rejected;
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
    assert.equal(await readFile(join(f.work, 'retained'), 'utf8'), 'original-journal');
  });
});
