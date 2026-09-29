// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only
// Explicit development connector to the existing administrative CLI, not an app capability API.

import { spawn } from 'node:child_process';
import { lstat, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { privateDirectory } from './archive.node.ts';

export type ReplicaConfig = {
  executable: string;
  controlSocket: string;
  identity: string;
  passphraseFile: string;
  providers: { key: string; grant: string }[];
  lifetimeSeconds: number;
};

export type ReplicaReport = {
  operation: string;
  logical_ciphertext_bytes: number;
  operation_complete?: boolean;
  read_consumes_archive: false;
  copies: { provider_key: string; charge: string }[];
  restored?: boolean;
  restored_from_provider_key?: string;
  [name: string]: unknown;
};

function ensure(condition: unknown): asserts condition {
  if (!condition) {
    throw new Error('VOLPAROSSA_BACKUP_INVALID_CONNECTOR');
  }
}

export async function validateConfig(config: ReplicaConfig): Promise<void> {
  ensure(config && Array.isArray(config.providers) && config.providers.length >= 2 && config.providers.length <= 8);
  ensure(new Set(config.providers.map(provider => provider.key)).size === config.providers.length);
  ensure(config.providers.every(provider => /^[a-f0-9]{64}$/.test(provider.key)));
  ensure(Number.isInteger(config.lifetimeSeconds) && config.lifetimeSeconds >= 1 && config.lifetimeSeconds <= 2_678_400);
  const paths = [config.executable, config.controlSocket, config.identity, config.passphraseFile,
    ...config.providers.map(provider => provider.grant)];
  for (const path of paths) {
    ensure(typeof path === 'string' && isAbsolute(path) && resolve(path) === path);
    ensure((await realpath(path)) === path);
  }
  const executable = await lstat(config.executable);
  ensure(executable.isFile() && (executable.mode & 0o111) !== 0 && (executable.mode & 0o022) === 0);
  ensure((await lstat(config.controlSocket)).isSocket());
  for (const path of [config.identity, config.passphraseFile, ...config.providers.map(provider => provider.grant)]) {
    const info = await lstat(path);
    ensure(info.isFile() && info.nlink === 1 && info.uid === process.getuid?.() && (info.mode & 0o777) === 0o600);
  }
}

export function checkedReport(value: unknown, operation: string, config: ReplicaConfig, bytes: number): ReplicaReport {
  const report = value as ReplicaReport;
  ensure(report && report.operation === `private_storage_replicas_${operation}`);
  ensure(report.logical_ciphertext_bytes === bytes && report.read_consumes_archive === false);
  ensure(Array.isArray(report.copies) && report.copies.length === config.providers.length);
  ensure(report.copies.every((copy, index) => copy.provider_key === config.providers[index].key));
  if (operation !== 'create' && operation !== 'status') {
    ensure(report.operation_complete === true);
  }
  if (operation === 'restore') {
    ensure(report.restored === true && config.providers.some(provider => provider.key === report.restored_from_provider_key));
  }
  return report;
}

/** No secret values in argv/env. The existing CLI reads its owner-only 0600 passphrase file. */
export async function replicaCommand(
  config: ReplicaConfig, operation: 'create' | 'deposit' | 'restore' | 'progress' | 'status',
  state: string, bytes: number, extra: string[] = [], signal?: AbortSignal
): Promise<ReplicaReport> {
  await validateConfig(config);
  await privateDirectory(dirname(state));
  ensure(isAbsolute(state) && resolve(state) === state && Number.isSafeInteger(bytes) && bytes > 0);
  signal?.throwIfAborted();
  const args = ['--control-socket', config.controlSocket, 'storage', 'replicas', operation, '--state', state];
  if (operation !== 'status') {
    args.push('--identity', config.identity, '--passphrase-file', config.passphraseFile);
  }
  args.push(...extra);
  if (operation === 'create') {
    for (const provider of config.providers) {
      args.push('--provider-key', provider.key, '--grant', provider.grant);
    }
    args.push('--lifetime-seconds', String(config.lifetimeSeconds));
  }
  return new Promise((resolveResult, reject) => {
    const child = spawn(config.executable, args, {
      shell: false, cwd: dirname(state), env: { LANG: 'C.UTF-8' },
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    let output = Buffer.alloc(0);
    let stopped = false;
    let killTimer: ReturnType<typeof setTimeout> | undefined;
    const stop = () => {
      if (stopped) { return; }
      stopped = true;
      child.kill('SIGTERM');
      killTimer = setTimeout(() => child.kill('SIGKILL'), 5_000);
    };
    // Finite development exchange; a timeout preserves the original CLI journals for reconciliation.
    const deadline = setTimeout(stop, 30 * 60 * 1_000);
    signal?.addEventListener('abort', stop, { once: true });
    if (signal?.aborted) { stop(); }
    child.stdout.on('data', (chunk: Buffer) => {
      if (output.length + chunk.length > 64 * 1024) { stop(); return; }
      output = Buffer.concat([output, chunk]);
    });
    child.on('error', stop);
    child.on('close', code => {
      clearTimeout(deadline);
      clearTimeout(killTimer);
      signal?.removeEventListener('abort', stop);
      try {
        ensure(!stopped && code === 0);
        resolveResult(checkedReport(JSON.parse(output.toString('utf8')), operation, config, bytes));
      } catch {
        reject(new Error('VOLPAROSSA_BACKUP_TRANSFER_INCOMPLETE'));
      }
    });
  });
}
