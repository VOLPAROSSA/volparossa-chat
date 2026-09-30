// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only
// Private container only: encryption/authentication belongs to Signal AttachmentCrypto.

import { constants } from 'node:fs';
import { lstat, mkdir, open, realpath, rm } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, resolve, sep } from 'node:path';

const MAGIC = Buffer.from('VSB1');
const MAX_HEADER = 4 * 1024 * 1024;
const MAX_FILES = 16_384;
const MAX_BYTES = 100 * 1024 * 1024 * 1024;
const CHUNK = 256 * 1024;
const SNAPSHOT = /^signal-backup-\d{4}(?:-\d{2}){5}$/;
const MEDIA = /^[a-f0-9]{64}(?:_thumbnail)?$/;
type Entry = { name: string; size: number };
type Manifest = { version: 1; snapshot: string; entries: Entry[] };

function ensure(condition: unknown): asserts condition {
  if (!condition) {
    throw new Error('VOLPAROSSA_BACKUP_INVALID_ARCHIVE');
  }
}

export async function privateDirectory(path: string): Promise<void> {
  ensure(isAbsolute(path) && resolve(path) === path);
  const info = await lstat(path);
  ensure(info.isDirectory() && !info.isSymbolicLink() && info.uid === process.getuid?.());
  ensure((info.mode & 0o777) === 0o700 && (await realpath(path)) === path);
}

async function regular(path: string): Promise<FileHandle> {
  ensure(isAbsolute(path) && (await realpath(path)) === path);
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await file.stat();
    ensure(info.isFile() && info.nlink === 1 && info.uid === process.getuid?.());
    ensure(Number.isSafeInteger(info.size) && info.size >= 0 && info.size <= MAX_BYTES + MAX_HEADER + 8);
    return file;
  } catch (error) {
    await file.close();
    throw error;
  }
}

async function writeAll(file: FileHandle, data: Uint8Array): Promise<void> {
  let offset = 0;
  while (offset < data.length) {
    const { bytesWritten } = await file.write(data, offset, data.length - offset);
    ensure(bytesWritten > 0);
    offset += bytesWritten;
  }
}

async function readExact(file: FileHandle, count: number): Promise<Buffer> {
  const result = Buffer.alloc(count);
  let offset = 0;
  while (offset < count) {
    const { bytesRead } = await file.read(result, offset, count - offset, null);
    ensure(bytesRead > 0);
    offset += bytesRead;
  }
  return result;
}

async function copyExact(source: FileHandle, destination: FileHandle, size: number): Promise<void> {
  let remaining = size;
  while (remaining > 0) {
    const part = await readExact(source, Math.min(CHUNK, remaining));
    await writeAll(destination, part);
    remaining -= part.length;
  }
}

function validate(manifest: Manifest): void {
  ensure(manifest?.version === 1 && SNAPSHOT.test(manifest.snapshot));
  ensure(Array.isArray(manifest.entries) && manifest.entries.length >= 3 && manifest.entries.length <= MAX_FILES);
  const required = new Set(['main', 'metadata', 'files'].map(name => `${manifest.snapshot}/${name}`));
  let previous = '';
  let total = 0;
  for (const entry of manifest.entries) {
    ensure(entry && typeof entry.name === 'string' && entry.name > previous);
    ensure(Number.isSafeInteger(entry.size) && entry.size >= 0);
    previous = entry.name;
    if (required.has(entry.name)) {
      ensure(entry.size > 0 || entry.name.endsWith('/files'));
      required.delete(entry.name);
    } else {
      const parts = entry.name.split('/');
      ensure(parts.length === 3 && parts[0] === 'files' && MEDIA.test(parts[2]));
      ensure(parts[1] === parts[2].slice(0, 2) && entry.size > 0);
    }
    total += entry.size;
    ensure(total <= MAX_BYTES);
  }
  ensure(required.size === 0);
}

/** Capture only a completed native snapshot and its exact encrypted media references. */
export async function bundleSnapshot(snapshot: string, mediaNames: ReadonlyArray<string>, output: string): Promise<number> {
  ensure(isAbsolute(snapshot) && SNAPSHOT.test(basename(snapshot)));
  const base = dirname(snapshot);
  await privateDirectory(base);
  await privateDirectory(dirname(output));
  ensure((await realpath(snapshot)) === snapshot);
  ensure(mediaNames.length <= MAX_FILES - 3 && new Set(mediaNames).size === mediaNames.length);
  ensure(mediaNames.every(name => MEDIA.test(name)));
  const names = ['main', 'metadata', 'files'].map(name => `${basename(snapshot)}/${name}`);
  names.push(...mediaNames.map(name => `files/${name.slice(0, 2)}/${name}`));
  names.sort();
  const entries: Entry[] = [];
  const fingerprints = new Map<string, { ino: number; dev: number; mtimeMs: number; ctimeMs: number }>();
  for (const name of names) {
    const file = await regular(join(base, name));
    try {
      const info = await file.stat();
      entries.push({ name, size: info.size });
      fingerprints.set(name, info);
    } finally {
      await file.close();
    }
  }
  const manifest: Manifest = { version: 1, snapshot: basename(snapshot), entries };
  validate(manifest);
  const header = Buffer.from(JSON.stringify(manifest));
  ensure(header.length <= MAX_HEADER);
  const prefix = Buffer.alloc(8);
  MAGIC.copy(prefix);
  prefix.writeUInt32BE(header.length, 4);
  const destination = await open(output, 'wx', 0o600);
  try {
    await writeAll(destination, prefix);
    await writeAll(destination, header);
    for (const entry of entries) {
      const file = await regular(join(base, entry.name));
      try {
        const before = await file.stat();
        const original = fingerprints.get(entry.name);
        ensure(original && before.ino === original.ino && before.dev === original.dev);
        ensure(before.size === entry.size && before.mtimeMs === original.mtimeMs && before.ctimeMs === original.ctimeMs);
        await copyExact(file, destination, entry.size);
        const after = await file.stat();
        ensure(after.size === before.size && after.mtimeMs === before.mtimeMs && after.ctimeMs === before.ctimeMs);
      } finally {
        await file.close();
      }
    }
    await destination.sync();
    return prefix.length + header.length + entries.reduce((sum, entry) => sum + entry.size, 0);
  } catch (error) {
    await rm(output, { force: true });
    throw error;
  } finally {
    await destination.close();
  }
}

/** Call ONLY after Signal has authenticated the complete decrypted container. */
export async function unpackAuthenticatedBundle(input: string, destination: string): Promise<string> {
  await privateDirectory(dirname(destination));
  ensure(isAbsolute(destination) && resolve(destination) === destination);
  const source = await regular(input);
  let created = false;
  try {
    const prefix = await readExact(source, 8);
    ensure(prefix.subarray(0, 4).equals(MAGIC));
    const length = prefix.readUInt32BE(4);
    ensure(length > 0 && length <= MAX_HEADER);
    const header = await readExact(source, length);
    const parsed = JSON.parse(header.toString('utf8')) as Manifest;
    const manifest: Manifest = {
      version: parsed.version, snapshot: parsed.snapshot,
      entries: parsed.entries.map(entry => ({ name: entry.name, size: entry.size })),
    };
    ensure(header.equals(Buffer.from(JSON.stringify(manifest))));
    validate(manifest);
    const expected = 8 + length + manifest.entries.reduce((sum, entry) => sum + entry.size, 0);
    ensure((await source.stat()).size === expected);
    await mkdir(destination, { mode: 0o700 });
    created = true;
    for (const entry of manifest.entries) {
      const output = join(destination, entry.name);
      ensure(output.startsWith(destination + sep));
      await mkdir(dirname(output), { recursive: true, mode: 0o700 });
      const file = await open(output, 'wx', 0o600);
      try {
        await copyExact(source, file, entry.size);
        await file.sync();
      } finally {
        await file.close();
      }
    }
    return join(destination, manifest.snapshot);
  } catch (error) {
    if (created) {
      await rm(destination, { recursive: true, force: true });
    }
    throw error;
  } finally {
    await source.close();
  }
}
