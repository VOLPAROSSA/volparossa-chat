// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only
// Disposable native-test coordination ONLY. Never imported by the production backup connector.
import { constants } from 'node:fs';
import { lstat, open, rename } from 'node:fs/promises';
import { join } from 'node:path';
import { privateDirectory } from '../../services/backups/volparossa/archive.node.ts';

function ensure(condition: unknown): asserts condition {
  if (!condition) { throw new Error('VOLPAROSSA_BACKUP_WITHDRAWAL_INVALID'); }
}
async function readPrivate(path: string, maximum: number): Promise<unknown> {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = await file.stat();
    ensure(info.isFile() && info.nlink === 1 && info.uid === process.getuid?.()
      && (info.mode & 0o777) === 0o600 && info.size > 0 && info.size <= maximum);
    return JSON.parse(await file.readFile('utf8'));
  } finally { await file.close(); }
}

export async function waitForBackupWithdrawal(
  work: string, config: string, timeoutMs = 120_000
): Promise<void> {
  ensure(Number.isSafeInteger(timeoutMs) && timeoutMs > 0 && timeoutMs <= 120_000);
  await privateDirectory(work);
  const value = await readPrivate(config, 16 * 1024) as { providers: { key: string }[] };
  const provider = value?.providers?.[0]?.key;
  ensure(typeof provider === 'string' && /^[a-f0-9]{64}$/.test(provider));
  const confirmation = join(work, 'withdrawal-confirmed.json');
  const readyPath = join(work, 'withdrawal-ready.json');
  const part = join(work, 'withdrawal-ready.json.part');
  // A previous/partial rendezvous can never authorize the next native restore.
  for (const path of [confirmation, readyPath, part]) {
    try { await lstat(path); throw new Error('VOLPAROSSA_BACKUP_WITHDRAWAL_STALE'); }
    catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) { throw error; }
    }
  }
  const ready = await open(part, 'wx', 0o600);
  try {
    await ready.writeFile(JSON.stringify({ version: 1, ready: true, provider_key: provider }));
    await ready.sync();
  } finally { await ready.close(); }
  // The strict external poll must never observe an empty or partially written JSON file.
  await rename(part, readyPath);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const ack = await readPrivate(confirmation, 256) as Record<string, unknown>;
      ensure(ack && Object.keys(ack).length === 3 && ack.version === 1
        && ack.provider_stopped === true && ack.provider_key === provider);
      return;
    } catch (error) {
      if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) { throw error; }
    }
    await new Promise(resolve => setTimeout(resolve, Math.min(100, Math.max(1, deadline - Date.now()))));
  }
  throw new Error('VOLPAROSSA_BACKUP_WITHDRAWAL_TIMEOUT');
}
