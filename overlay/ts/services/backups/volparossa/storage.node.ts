// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only
import { constants } from 'node:fs';
import { open } from 'node:fs/promises';
import { join } from 'node:path';
import { privateDirectory } from './archive.node.ts';
import { replicaCommand } from './replicas.node.ts';
import type { ReplicaConfig, ReplicaReport } from './replicas.node.ts';
import { fragmentCommand } from './fragments.node.ts';
import type { FragmentOperation, FragmentReport } from './fragments.node.ts';

type Envelope = {
  keysBase64: string;
  digestBase64: string;
  plaintextBytes: number;
  ciphertextBytes: number;
  ciphertextSha256: string;
};
export type Recovery = Envelope & (
  { version: 1; storage?: never } |
  { version: 2; storage: { kind: 'fragments'; providerKeys: string[] } }
);
export type StorageReport = ReplicaReport | FragmentReport;

function ensure(condition: unknown): asserts condition {
  if (!condition) { throw new Error('VOLPAROSSA_BACKUP_RECOVERY_INVALID'); }
}

/** Only an explicit retained version selects the storage engine; never probe or fall back. */
export function checkedRecovery(value: unknown): Recovery {
  const descriptor = value as Recovery;
  ensure(descriptor && (descriptor.version === 1 || descriptor.version === 2));
  if (descriptor.version === 1) {
    ensure(!Object.hasOwn(descriptor, 'storage'));
  } else {
    const storage = descriptor.storage;
    ensure(storage && storage.kind === 'fragments' && Array.isArray(storage.providerKeys)
      && storage.providerKeys.length >= 3 && storage.providerKeys.length <= 8
      && storage.providerKeys.every(key => typeof key === 'string' && /^[a-f0-9]{64}$/.test(key))
      && new Set(storage.providerKeys).size === storage.providerKeys.length);
  }
  ensure(typeof descriptor.keysBase64 === 'string' && Buffer.from(descriptor.keysBase64, 'base64').length === 64
    && typeof descriptor.digestBase64 === 'string' && Buffer.from(descriptor.digestBase64, 'base64').length === 32
    && Number.isSafeInteger(descriptor.plaintextBytes) && descriptor.plaintextBytes > 0
    && Number.isSafeInteger(descriptor.ciphertextBytes) && descriptor.ciphertextBytes > 0
    && typeof descriptor.ciphertextSha256 === 'string' && /^[a-f0-9]{64}$/.test(descriptor.ciphertextSha256));
  return descriptor;
}

export async function readRecovery(work: string): Promise<Recovery> {
  await privateDirectory(work);
  const file = await open(join(work, 'recovery.json'), constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    ensure(info.isFile() && info.nlink === 1 && info.uid === process.getuid?.()
      && (info.mode & 0o777) === 0o600 && info.size <= 4096);
    return checkedRecovery(JSON.parse(await file.readFile('utf8')));
  } finally { await file.close(); }
}

/** Resume/restore retains the original archive identity, owner, journal and storage kind. */
export async function storageCommand(
  config: ReplicaConfig, descriptor: Recovery, operation: FragmentOperation, work: string,
  extra: string[] = [], signal?: AbortSignal
): Promise<StorageReport> {
  checkedRecovery(descriptor);
  if (descriptor.version === 1) {
    // Existing replicas can be resumed/restored; no new legacy deposits may be initialized here.
    ensure(operation !== 'create');
    return replicaCommand(config, operation, join(work, 'replicas'), descriptor.ciphertextBytes, extra, signal);
  }
  const providerKeys = descriptor.storage.providerKeys;
  ensure(config.providers.length === providerKeys.length
    && config.providers.every((provider, index) => provider.key === providerKeys[index]));
  return fragmentCommand(config, operation, join(work, 'fragments'), descriptor.ciphertextBytes, extra, signal);
}
