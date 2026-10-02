// SPDX-License-Identifier: AGPL-3.0-only
// Synthetic reports and an explicit TEST-ONLY executable, not Signal crypto or real core/peer proof.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { access, chmod, mkdir, mkdtemp, open, readFile, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { checkedFragmentReport, fragmentCommand, validateFragmentConfig } from '../overlay/ts/services/backups/volparossa/fragments.node.ts';
import { checkedRecovery, readRecovery, storageCommand } from '../overlay/ts/services/backups/volparossa/storage.node.ts';

const BUILD = resolve(import.meta.dirname, '../build');
const KEYS = ['11', '22', '33'].map(value => value.repeat(32));
const CONFIG = { providers: KEYS.map(key => ({ key })) };
function descriptor(version = 2) {
  return { version, ...(version === 2 ? { storage: { kind: 'fragments', providerKeys: KEYS } } : {}),
    keysBase64: Buffer.alloc(64, 0xaa).toString('base64'), digestBase64: Buffer.alloc(32, 0xbb).toString('base64'),
    plaintextBytes: 8, ciphertextBytes: 12, ciphertextSha256: 'cc'.repeat(32) };
}
function report(operation = 'deposit') {
  const unattempted = operation === 'create';
  return {
    operation: `private_storage_fragments_${operation}`, logical_ciphertext_bytes: 12,
    fragment_count: 3, copies_per_fragment: 2, distinct_provider_identities: 3,
    reserved_payload_bytes: 0, committed_payload_bytes: unattempted ? 0 : 24, uncertain_payload_bytes: 0,
    physical_payload_charge_upper_bound: unattempted ? 0 : 24, metadata_overhead_measured: false,
    expired_copies_remain_charged: true, fragments_with_confirmed_unexpired_copy: unattempted ? 0 : 3,
    fully_redundant_from_retained_receipts: !unattempted, current_remote_availability_proven: false,
    independent_failure_domains_proven: false, network_contribution_credit: false, automatic_repair: false,
    automatic_handoff: false, read_consumes_archive: false, erasure_coding: false, owner_signature_verified: true,
    providers: KEYS.map(provider_key => ({ provider_key, physical_payload_charge_upper_bound: unattempted ? 0 : 8 })),
    fragments: KEYS.map((_, index) => ({ index, offset: index * 4, ciphertext_bytes: 4,
      confirmed_unexpired_copies: unattempted ? 0 : 2,
      copies: [0, 1].map(position => ({ provider_key: KEYS[(index + position) % 3], charge: unattempted ? 'unattempted' : 'committed' })) })),
    ...(['create', 'status'].includes(operation) ? {} : { operation_complete: true }),
    ...(operation === 'restore' ? { restored: true, whole_archive_sha256_verified: true } : {}),
  };
}
function repaired(operation = 'restore') {
  const result = report(operation);
  result.fragments[0].copies[0].charge = 'uncertain';
  result.fragments[0].copies.push({ provider_key: '44'.repeat(32), charge: 'committed' });
  result.providers.push({ provider_key: '44'.repeat(32), physical_payload_charge_upper_bound: 4 });
  return Object.assign(result, { report_version: 2, distinct_provider_identities: 4,
    uncertain_payload_bytes: 4, physical_payload_charge_upper_bound: 28,
    placement_authorizations: 1, retained_copy_records: 7, pending_retirements: 1,
    desired_copies_per_fragment: 2, replacement_overhead_included: true });
}

async function fixture(body) {
  await mkdir(BUILD, { recursive: true, mode: 0o700 });
  const work = await mkdtemp(join(BUILD, 'frag-'));
  const directory = await open(work, 'r');
  const server = createServer();
  // Relative fd path avoids AF_UNIX's length bound in a deep worktree, without leaving the workspace.
  server.listen(`/proc/self/fd/${directory.fd}/s`);
  await once(server, 'listening');
  const config = { executable: join(work, 'cli'), controlSocket: join(work, 's'),
    identity: join(work, 'owner'), passphraseFile: join(work, 'passphrase'),
    providers: KEYS.map((key, index) => ({ key, grant: join(work, `grant-${index}`) })),
    lifetimeSeconds: 3600, fragmentBytes: 262144 };
  try {
    for (const path of [config.identity, config.passphraseFile, ...config.providers.map(p => p.grant)]) {
      await writeFile(path, 'TEST-ONLY-SECRET', { mode: 0o600 });
    }
    const cli = async source => {
      await writeFile(config.executable, `#!${process.execPath}\n${source}\n`, { mode: 0o700 });
      await chmod(config.executable, 0o700);
    };
    await body({ work, config, cli });
  } finally {
    await new Promise(resolve => server.close(resolve));
    await directory.close();
    await rm(work, { recursive: true, force: true });
  }
}

test('real fragment report contract binds exact ranges, two copies, provider order and accounting', () => {
  for (const operation of ['create', 'status', 'deposit', 'restore', 'progress']) {
    const value = report(operation);
    assert.equal(checkedFragmentReport(value, operation, CONFIG, 12), value);
  }
  const mutations = [r => r.fragments[1].offset++, r => r.fragments[2].ciphertext_bytes++,
    r => r.copies_per_fragment++, r => r.providers.reverse(), r => r.fragments[0].copies.reverse(),
    r => r.fragments[0].copies.pop(), r => r.physical_payload_charge_upper_bound--,
    r => r.providers[0].physical_payload_charge_upper_bound--, r => r.uncertain_payload_bytes++,
    r => { r.read_consumes_archive = true; }, r => { r.owner_signature_verified = false; },
    r => { r.operation_complete = false; }, r => { r.current_remote_availability_proven = true; },
    r => { r.report_version = 1; }, r => { r.placement_authorizations = 0; }];
  for (const mutate of mutations) {
    const value = report(); mutate(value);
    assert.throws(() => checkedFragmentReport(value, 'deposit', CONFIG, 12));
  }
  const restore = report('restore'); restore.whole_archive_sha256_verified = false;
  assert.throws(() => checkedFragmentReport(restore, 'restore', CONFIG, 12));
});

test('signed-placement report v2 keeps uncertain source charges and accepts replacement provider records', () => {
  const value = repaired();
  assert.equal(checkedFragmentReport(value, 'restore', CONFIG, 12), value);
  for (const mutate of [r => r.uncertain_payload_bytes--, r => r.retained_copy_records--,
    r => { r.pending_retirements = 2; }, r => { r.desired_copies_per_fragment = 3; },
    r => { r.replacement_overhead_included = false; }, r => { r.fragments[0].copies[2].provider_key = KEYS[1]; }]) {
    const bad = structuredClone(value); mutate(bad);
    assert.throws(() => checkedFragmentReport(bad, 'restore', CONFIG, 12));
  }
  assert.throws(() => checkedFragmentReport({ ...value, operation: 'private_storage_fragments_create' }, 'create', CONFIG, 12));
});

test('private recovery discriminator is explicit, persisted and rejects downgrade/unknown/provider mutation', async () => {
  for (const version of [1, 2]) assert.equal(checkedRecovery(descriptor(version)).version, version);
  for (const mutate of [d => { d.version = 3; }, d => { delete d.storage; },
    d => { d.storage.kind = 'replicas'; }, d => { d.storage.providerKeys = [KEYS[0], KEYS[0], KEYS[1]]; },
    d => { d.version = 1; }, d => { d.ciphertextSha256 = ''; }]) {
    const bad = descriptor(); mutate(bad); assert.throws(() => checkedRecovery(bad));
  }
  await fixture(async ({ work }) => {
    const path = join(work, 'recovery.json');
    await writeFile(path, JSON.stringify(descriptor()), { mode: 0o600 });
    assert.deepEqual(await readRecovery(work), descriptor());
    await chmod(path, 0o644);
    await assert.rejects(readRecovery(work), /RECOVERY_INVALID/);
  });
});

test('new archive create invokes actual fragment CLI shape with explicit grants and no app copy policy', async () => {
  await fixture(async ({ work, config, cli }) => {
    await cli(`require('node:fs').writeFileSync('argv.json', JSON.stringify(process.argv.slice(2))); console.log(${JSON.stringify(JSON.stringify(report('create')))});`);
    const extra = ['--input', join(work, 'archive.signal'), '--sha256', descriptor().ciphertextSha256, '--already-encrypted'];
    await storageCommand(config, descriptor(), 'create', work, extra);
    const args = JSON.parse(await readFile(join(work, 'argv.json'), 'utf8'));
    assert.deepEqual(args.slice(0, 7), ['--control-socket', config.controlSocket, 'storage', 'fragments', 'create', '--state', join(work, 'fragments')]);
    assert.deepEqual(args.slice(7), ['--identity', config.identity, '--passphrase-file', config.passphraseFile,
      ...extra, ...config.providers.flatMap(p => ['--provider-key', p.key, '--grant', p.grant]),
      '--fragment-bytes', '262144', '--lifetime-seconds', '3600']);
    assert.ok(!args.includes('--copies') && !args.some(a => a.includes('TEST-ONLY-SECRET')));
    await assert.rejects(validateFragmentConfig({ ...config, providers: config.providers.slice(0, 2) }));
    await assert.rejects(validateFragmentConfig({ ...config, copies: 2 }));
    await assert.rejects(fragmentCommand({ ...config, fragmentBytes: 1 }, 'create', join(work, 'other'), 257));
  });
});

test('restart reuses v2 descriptor and journal; failed/incomplete fragment commands never fall back to replicas', async () => {
  await fixture(async ({ work, config, cli }) => {
    await writeFile(join(work, 'recovery.json'), JSON.stringify(descriptor()), { mode: 0o600 });
    await mkdir(join(work, 'fragments'), { mode: 0o700 });
    await writeFile(join(work, 'fragments', 'retained'), 'immutable-original-journal', { mode: 0o600 });
    await cli(`require('node:fs').appendFileSync('calls.jsonl', JSON.stringify(process.argv.slice(2))+String.fromCharCode(10)); console.log(${JSON.stringify(JSON.stringify(report()))});`);
    for (let n = 0; n < 2; n++) await storageCommand(config, await readRecovery(work), 'deposit', work, ['--input', join(work, 'archive.signal'), '--already-encrypted']);
    const calls = (await readFile(join(work, 'calls.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.deepEqual(calls[0], calls[1]);
    for (const code of [0, 1]) {
      await cli(`require('node:fs').appendFileSync('calls.jsonl', JSON.stringify(process.argv.slice(2))+String.fromCharCode(10)); console.log(${JSON.stringify(JSON.stringify({ ...report(), operation_complete: false }))}); process.exit(${code});`);
      await assert.rejects(storageCommand(config, await readRecovery(work), 'deposit', work), /TRANSFER_INCOMPLETE/);
    }
    const all = (await readFile(join(work, 'calls.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(all.length, 4);
    assert.ok(all.every(args => args[3] === 'fragments'));
    assert.equal(await readFile(join(work, 'fragments', 'retained'), 'utf8'), 'immutable-original-journal');
    await assert.rejects(access(join(work, 'replicas')));
    await assert.rejects(storageCommand({ ...config, providers: [...config.providers].reverse() }, descriptor(), 'restore', work), /RECOVERY_INVALID/);
  });
});

test('recorded v1 archive with two providers uses unchanged replica restore and cannot create new replicas', async () => {
  await fixture(async ({ work, config, cli }) => {
    const legacyConfig = { ...config, providers: config.providers.slice(0, 2) };
    const value = { operation: 'private_storage_replicas_restore', logical_ciphertext_bytes: 12,
      operation_complete: true, read_consumes_archive: false, restored: true,
      restored_from_provider_key: KEYS[0], copies: legacyConfig.providers.map(p => ({ provider_key: p.key, charge: 'committed' })) };
    await writeFile(join(work, 'recovery.json'), JSON.stringify(descriptor(1)), { mode: 0o600 });
    await cli(`require('node:fs').writeFileSync('argv.json', JSON.stringify(process.argv.slice(2))); console.log(${JSON.stringify(JSON.stringify(value))});`);
    const original = await readFile(join(work, 'recovery.json'));
    await storageCommand(legacyConfig, await readRecovery(work), 'restore', work, ['--output', join(work, 'output')]);
    const args = JSON.parse(await readFile(join(work, 'argv.json'), 'utf8'));
    assert.equal(args[3], 'replicas');
    assert.equal(args[6], join(work, 'replicas'));
    assert.deepEqual(await readFile(join(work, 'recovery.json')), original);
    await assert.rejects(storageCommand(legacyConfig, descriptor(1), 'create', work), /RECOVERY_INVALID/);
  });
});

test('fragment cancellation joins its exact child and preserves the original recovery file', async () => {
  await fixture(async ({ work, config, cli }) => {
    const original = JSON.stringify(descriptor());
    await writeFile(join(work, 'recovery.json'), original, { mode: 0o600 });
    await cli("require('node:fs').writeFileSync('pid', String(process.pid)); setInterval(() => {}, 1000);");
    const controller = new AbortController();
    const rejected = assert.rejects(storageCommand(config, descriptor(), 'restore', work, [], controller.signal), /TRANSFER_INCOMPLETE/);
    let pid;
    for (let attempt = 0; attempt < 100; attempt++) {
      try { pid = Number(await readFile(join(work, 'pid'), 'utf8')); break; }
      catch { await new Promise(resolve => setTimeout(resolve, 10)); }
    }
    assert.ok(pid > 0); controller.abort(); await rejected;
    assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
    assert.equal(await readFile(join(work, 'recovery.json'), 'utf8'), original);
  });
});
