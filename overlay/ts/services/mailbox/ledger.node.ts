// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only

import { createHash } from 'node:crypto';
import type { ReadableDB, WritableDB } from '../../sql/Interface.std.ts';
import { strictAssert } from '../../util/assert.std.ts';
import { mailboxRowSchema } from './packet.node.ts';
import type { MailboxRow } from './packet.node.ts';

// The extension has its own table inside the existing SQLCipher profile. It
// needs no change to Signal's upstream schema version or backup format.
export function openMailboxLedger(db: WritableDB): void {
  db.transaction(() => {
    db.exec(`
      CREATE TABLE IF NOT EXISTS volparossa_mailbox (
        id TEXT PRIMARY KEY, expires INTEGER NOT NULL, packetKey TEXT, json TEXT NOT NULL
      ) WITHOUT ROWID;
      CREATE INDEX IF NOT EXISTS volparossa_mailbox_expiry ON volparossa_mailbox(expires);
      CREATE UNIQUE INDEX IF NOT EXISTS volparossa_mailbox_packet
        ON volparossa_mailbox(packetKey) WHERE packetKey IS NOT NULL;
    `);
    const previous = db
      .prepare("SELECT json FROM items WHERE id = 'volparossaMailboxV1'", {
        pluck: true,
      })
      .get();
    const now = Date.now();
    if (previous !== undefined) {
      strictAssert(
        typeof previous === 'string',
        'Invalid previous mailbox item'
      );
      const rows: unknown = JSON.parse(previous).value;
      strictAssert(Array.isArray(rows), 'Invalid previous mailbox records');
      for (const entry of rows) {
        const row = mailboxRowSchema.parse(entry);
        if (row.expires > now) insertMailboxRow(db, row, now);
      }
      db.exec("DELETE FROM items WHERE id = 'volparossaMailboxV1'");
    }
    db.prepare('DELETE FROM volparossa_mailbox WHERE expires <= $now').run({
      now,
    });
  })();
}

export function insertMailboxRow(
  db: WritableDB,
  value: MailboxRow,
  now: number
): void {
  const row = mailboxRowSchema.parse(value);
  strictAssert(
    row.expires > now && !row.settled,
    'Expired or previously settled mailbox row'
  );
  if (row.packet) {
    const key = createHash('sha256')
      .update(Buffer.from(row.packet, 'base64'))
      .digest('hex');
    strictAssert(
      !row.packetKey || row.packetKey === key,
      'Mailbox packet differs from its digest'
    );
    row.packetKey = key;
  }
  db.prepare(
    'INSERT INTO volparossa_mailbox (id, expires, packetKey, json) VALUES ($id, $expires, $key, $value)'
  ).run({
    id: row.id,
    expires: row.expires,
    key: row.packetKey ?? null,
    value: JSON.stringify(row),
  });
}

export function getMailboxRow(
  db: ReadableDB,
  id: string
): MailboxRow | undefined {
  const raw = db
    .prepare(
      'SELECT json FROM volparossa_mailbox WHERE id = $id AND expires > $now',
      { pluck: true }
    )
    .get({ id, now: Date.now() });
  return typeof raw === 'string'
    ? mailboxRowSchema.parse(JSON.parse(raw))
    : undefined;
}

export function getMailboxRowForCustody(
  db: ReadableDB,
  packetKey: string
): MailboxRow | undefined {
  const raw = db
    .prepare(
      'SELECT json FROM volparossa_mailbox WHERE packetKey = $packetKey AND expires > $now',
      { pluck: true }
    )
    .get({ packetKey, now: Date.now() });
  return typeof raw === 'string'
    ? mailboxRowSchema.parse(JSON.parse(raw))
    : undefined;
}

export function settleMailboxRow(db: WritableDB, id: string): void {
  // The native consumer has verified the ordinary durable Signal result.
  // Retain only immutable identifiers and retry intent for the whole grant.
  const result = db
    .prepare(
      `
    UPDATE volparossa_mailbox
    SET json = json_set(json_remove(json, '$.packet', '$.bodyDigest', '$.profileDigest', '$.receipt'), '$.settled', json('true'))
    WHERE id = $id AND expires > $now
  `
    )
    .run({ id, now: Date.now() });
  strictAssert(result.changes === 1, 'Missing or expired native mailbox proof');
}
