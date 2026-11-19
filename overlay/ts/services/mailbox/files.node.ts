// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only

import { createHash, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, mkdir, open, realpath, rename, unlink } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { z } from 'zod';
import { pairedDeviceSchema } from './packet.node.ts';

const sha = z.string().regex(/^[a-f0-9]{64}$/);
const schema = z
  .object({
    version: z.literal(1),
    local: pairedDeviceSchema,
    remote: pairedDeviceSchema,
    sendGrant: sha,
    receiveGrant: sha,
    expires: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
  })
  .strict();
export type MailboxPair = z.infer<typeof schema>;
export function digest(bytes: Uint8Array | string): string {
  return createHash('sha256').update(bytes).digest('hex');
}
export function absent(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}
export function check(
  value: unknown,
  reason = 'Mailbox authority or private handoff invalid',
): asserts value {
  if (!value) {
    throw new Error(reason);
  }
}
export async function directory(path: string): Promise<void> {
  const st = await lstat(path);
  check(
    st.isDirectory() &&
      st.uid === process.getuid?.() &&
      (st.mode & 0o777) === 0o700 &&
      (await realpath(path)) === path,
  );
}
export async function readPrivate(
  path: string,
  max: number,
): Promise<Buffer<ArrayBuffer>> {
  await directory(dirname(path));
  const fd = await open(
    path,
    constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
  );
  try {
    const st = await fd.stat();
    check(
      st.isFile() &&
        st.uid === process.getuid?.() &&
        st.nlink === 1 &&
        (st.mode & 0o777) === 0o600 &&
        st.size > 0 &&
        st.size <= max,
    );
    const bytes = Buffer.alloc(st.size + 1);
    const { bytesRead } = await fd.read(bytes, 0, bytes.length, 0);
    check(bytesRead === st.size);
    return bytes.subarray(0, st.size);
  } finally {
    await fd.close();
  }
}
export async function pairAt(root: string): Promise<MailboxPair | undefined> {
  try {
    await lstat(root);
  } catch (e) {
    if (absent(e)) {
      return undefined;
    }
    throw e;
  }
  const pair = schema.parse(
    JSON.parse(
      (await readPrivate(join(root, 'pair.json'), 4096)).toString('utf8'),
    ),
  );
  check(
    pair.local.aci !== pair.remote.aci && pair.sendGrant !== pair.receiveGrant,
  );
  return pair;
}
export async function publish(
  path: string,
  bytes: Uint8Array<ArrayBuffer>,
): Promise<void> {
  const parent = dirname(path);
  await mkdir(parent, { recursive: true, mode: 0o700 });
  await directory(parent);
  try {
    check(
      (await readPrivate(path, bytes.length)).equals(bytes),
      'Existing handoff differs',
    );
    return;
  } catch (err) {
    if (!absent(err)) {
      throw err;
    }
  }
  const tmp = join(parent, `.${randomUUID()}`);
  const file = await open(tmp, 'wx', 0o600);
  try {
    try {
      await file.writeFile(bytes);
      await file.sync();
    } finally {
      await file.close();
    }
    // Outbound names hash the bytes. Import markers name a packet that binds
    // its grant, so concurrent native publishers of either path agree byte for byte.
    await rename(tmp, path);
  } finally {
    try {
      await unlink(tmp);
    } catch (err) {
      if (!absent(err)) {
        throw err;
      }
    }
  }
  const dir = await open(parent, 'r');
  try {
    await dir.sync();
  } finally {
    await dir.close();
  }
}
