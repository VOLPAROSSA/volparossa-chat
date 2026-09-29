// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only

import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream, constants } from 'node:fs';
import { lstat, mkdir, open, readdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import {
  generateAttachmentKeys, encryptAttachmentV2, decryptAttachmentV2ToSink,
} from '../../../AttachmentCrypto.node.ts';
import { backupsService } from '../index.preload.ts';
import { readLocalBackupFilesList } from '../util/localBackup.node.ts';
import { bundleSnapshot, privateDirectory, unpackAuthenticatedBundle } from './archive.node.ts';
import { replicaCommand, validateConfig } from './replicas.node.ts';
import type { ReplicaConfig, ReplicaReport } from './replicas.node.ts';

type Recovery = {
  version: 1;
  keysBase64: string;
  digestBase64: string;
  plaintextBytes: number;
  ciphertextBytes: number;
  ciphertextSha256: string;
};

async function hash(path: string): Promise<string> {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path)) {
    digest.update(chunk);
  }
  return digest.digest('hex');
}

async function recovery(work: string): Promise<Recovery> {
  await privateDirectory(work);
  const path = join(work, 'recovery.json');
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.nlink !== 1 || info.uid !== process.getuid?.() || (info.mode & 0o777) !== 0o600 || info.size > 4096) {
      throw new Error('VOLPAROSSA_BACKUP_RECOVERY_INVALID');
    }
    const value = JSON.parse(await file.readFile('utf8')) as Recovery;
    if (value.version !== 1 || Buffer.from(value.keysBase64, 'base64').length !== 64 ||
        Buffer.from(value.digestBase64, 'base64').length !== 32 ||
        !Number.isSafeInteger(value.plaintextBytes) || value.plaintextBytes <= 0 ||
        !Number.isSafeInteger(value.ciphertextBytes) || value.ciphertextBytes <= 0 ||
        !/^[a-f0-9]{64}$/.test(value.ciphertextSha256)) {
      throw new Error('VOLPAROSSA_BACKUP_RECOVERY_INVALID');
    }
    return value;
  } finally {
    await file.close();
  }
}

/** Explicitly invoked local-encrypted export. No send/receive/default behavior is changed. */
export async function exportToReplicas(
  work: string, config: ReplicaConfig, signal = new AbortController().signal
): Promise<ReplicaReport> {
  await privateDirectory(work);
  await validateConfig(config);
  if ((await readdir(work)).length !== 0) {
    throw new Error('VOLPAROSSA_BACKUP_NEW_WORK_DIRECTORY_REQUIRED');
  }
  const native = join(work, 'native-export');
  await mkdir(native, { mode: 0o700 });
  const bundle = join(work, 'snapshot.bundle');
  const ciphertext = join(work, 'archive.signal');
  const key = generateAttachmentKeys();
  try {
    const { snapshotDir } = await backupsService.exportLocalBackup({
      backupsBaseDir: native, abortSignal: signal, onProgress: () => undefined,
    });
    if ((await lstat(join(snapshotDir, 'files'))).size > 4 * 1024 * 1024) {
      throw new Error('VOLPAROSSA_BACKUP_REFERENCE_LIST_TOO_LARGE');
    }
    const mediaNames = await readLocalBackupFilesList(snapshotDir);
    const plaintextBytes = await bundleSnapshot(snapshotDir, mediaNames, bundle);
    signal.throwIfAborted();
    const result = await encryptAttachmentV2({
      keys: key, needIncrementalMac: false,
      plaintext: { absolutePath: bundle },
      sink: createWriteStream(ciphertext, { flags: 'wx', mode: 0o600 }),
    });
    const encryptedFile = await open(ciphertext, 'r');
    try { await encryptedFile.sync(); } finally { await encryptedFile.close(); }
    const descriptor: Recovery = {
      version: 1, keysBase64: Buffer.from(key).toString('base64'),
      digestBase64: Buffer.from(result.digest).toString('base64'), plaintextBytes,
      ciphertextBytes: result.ciphertextSize, ciphertextSha256: await hash(ciphertext),
    };
    const descriptorFile = await open(join(work, 'recovery.json'), 'wx', 0o600);
    try {
      await descriptorFile.writeFile(JSON.stringify(descriptor));
      await descriptorFile.sync();
    } finally {
      await descriptorFile.close();
    }
    const directory = await open(work, 'r');
    try { await directory.sync(); } finally { await directory.close(); }
  } finally {
    key.fill(0);
    await rm(bundle, { force: true });
    await rm(native, { recursive: true, force: true });
  }
  const descriptor = await recovery(work);
  await replicaCommand(config, 'create', join(work, 'replicas'), descriptor.ciphertextBytes,
    ['--input', ciphertext, '--sha256', descriptor.ciphertextSha256, '--already-encrypted'], signal);
  return resumeReplicaDeposit(work, config, signal);
}

/** Retry precisely the same ciphertext and retained set; never regenerate an envelope on ambiguity. */
export async function resumeReplicaDeposit(
  work: string, config: ReplicaConfig, signal?: AbortSignal
): Promise<ReplicaReport> {
  const descriptor = await recovery(work);
  const ciphertext = join(work, 'archive.signal');
  if ((await hash(ciphertext)) !== descriptor.ciphertextSha256) {
    throw new Error('VOLPAROSSA_BACKUP_CIPHERTEXT_CHANGED');
  }
  return replicaCommand(config, 'deposit', join(work, 'replicas'), descriptor.ciphertextBytes,
    ['--input', ciphertext, '--already-encrypted'], signal);
}

/** Return a NEW authenticated native snapshot for the ordinary stage/link/import lifecycle. */
export async function restoreFromReplicas(
  work: string, config: ReplicaConfig, restoreRoot: string, signal?: AbortSignal
): Promise<string> {
  const descriptor = await recovery(work);
  await privateDirectory(restoreRoot);
  if ((await readdir(restoreRoot)).length !== 0) {
    throw new Error('VOLPAROSSA_BACKUP_NEW_RESTORE_DIRECTORY_REQUIRED');
  }
  const ciphertext = join(restoreRoot, 'download.signal');
  const bundle = join(restoreRoot, 'authenticated.bundle');
  await replicaCommand(config, 'restore', join(work, 'replicas'), descriptor.ciphertextBytes,
    ['--output', ciphertext], signal);
  try {
    if ((await lstat(ciphertext)).size !== descriptor.ciphertextBytes ||
        (await hash(ciphertext)) !== descriptor.ciphertextSha256) {
      throw new Error('VOLPAROSSA_BACKUP_CIPHERTEXT_CHANGED');
    }
    // Signal verifies digest and MAC before resolving. Never parse/extract partial plaintext.
    await decryptAttachmentV2ToSink({
      ciphertextPath: ciphertext, keysBase64: descriptor.keysBase64,
      idForLogging: 'VolparossaBackup', size: descriptor.plaintextBytes, type: 'standard',
      integrityCheck: { type: 'encrypted', digest: Buffer.from(descriptor.digestBase64, 'base64') },
      theirIncrementalMac: undefined, theirChunkSize: undefined,
    }, createWriteStream(bundle, { flags: 'wx', mode: 0o600 }));
    signal?.throwIfAborted();
    const native = join(restoreRoot, 'SignalBackups');
    const snapshot = await unpackAuthenticatedBundle(bundle, native);
    try {
      // Native list is the authority, not the outer container's own list.
      if ((await lstat(join(snapshot, 'files'))).size > 4 * 1024 * 1024) {
        throw new Error('VOLPAROSSA_BACKUP_REFERENCE_LIST_TOO_LARGE');
      }
      const media = await readLocalBackupFilesList(snapshot);
      const check = join(restoreRoot, 'reference-check.bundle');
      try { await bundleSnapshot(snapshot, media, check); } finally { await rm(check, { force: true }); }
      return snapshot;
    } catch (error) {
      await rm(native, { recursive: true, force: true });
      throw error;
    }
  } finally {
    await rm(bundle, { force: true });
    await rm(ciphertext, { force: true });
  }
}

/** Only the installer/linking flow may call importLocalBackup afterwards, as upstream does. */
export async function stageRestoredBackup(snapshot: string): Promise<void> {
  const result = await backupsService.stageLocalBackupForImport(snapshot);
  if (!result.success) {
    throw new Error('VOLPAROSSA_BACKUP_NATIVE_STRUCTURE_INVALID');
  }
}

/** CI bridge receives only a private config-file path, never recovery keys in arguments. */
export async function readConnectorConfig(path: string): Promise<ReplicaConfig> {
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    if (!info.isFile() || info.nlink !== 1 || info.uid !== process.getuid?.() || (info.mode & 0o777) !== 0o600 || info.size > 16 * 1024) {
      throw new Error('VOLPAROSSA_BACKUP_CONFIG_INVALID');
    }
    const config = JSON.parse(await file.readFile('utf8')) as ReplicaConfig;
    await validateConfig(config);
    return config;
  } finally { await file.close(); }
}
