// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only

import { z } from 'zod';
import { aciSchema } from '../../types/ServiceId.std.ts';

const deviceSchema = z
  .object({
    aci: aciSchema,
    device: z.number().int().min(1).max(127),
    registration: z.number().int().min(1).max(16383),
    identity: z.string().regex(/^05[0-9a-f]{64}$/),
  })
  .strict();

export const pairedDeviceSchema = deviceSchema;
export type PairedDevice = z.infer<typeof deviceSchema>;
export const MAX_PACKET_BYTES = 32_768;
const hash = z.string().regex(/^[0-9a-f]{64}$/);
const timestamp = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
export const mailboxRows = z
  .array(
    z
      .object({
        id: hash,
        expires: timestamp,
        // Outbound sessions retain the exact packet and intent for repeat delivery.
        packet: z.string().max(44_000).optional(),
        intent: hash.optional(),
        // Inbound rows refer to native Signal state. No decrypted text or profile key is stored here.
        bodyDigest: hash.optional(),
        profileDigest: hash.optional(),
        receipt: z
          .object({
            state: z.enum(['Delivered', 'Read', 'Viewed']),
            timestamps: z.array(timestamp).min(1).max(32),
          })
          .strict()
          .optional(),
      })
      .strict(),
  )
  .max(256);
export type MailboxRow = z.infer<typeof mailboxRows>[number];

const packetSchema = z
  .object({
    version: z.literal(1),
    grant: z.string().regex(/^[a-f0-9]{64}$/),
    from: deviceSchema,
    to: deviceSchema,
    timestamp: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
    // Only Signal Protocol existing-session (Whisper) messages use this format.
    ciphertext: z
      .string()
      .min(4)
      .max(24576)
      .regex(/^[A-Za-z0-9+/]+={0,2}$/),
  })
  .strict();
export type MailboxPacket = z.infer<typeof packetSchema>;

export function encodePacket(input: MailboxPacket): Uint8Array<ArrayBuffer> {
  const packet = packetSchema.parse(input);
  const bytes = Buffer.from(packet.ciphertext, 'base64');
  if (bytes.toString('base64') !== packet.ciphertext) {
    throw new Error('Invalid mailbox ciphertext encoding');
  }
  return Buffer.from(JSON.stringify(packet), 'utf8');
}

export function decodePacket(bytes: Uint8Array<ArrayBuffer>): MailboxPacket {
  if (!bytes.length || bytes.length > MAX_PACKET_BYTES) {
    throw new Error('Invalid mailbox packet size');
  }
  const packet = packetSchema.parse(
    JSON.parse(Buffer.from(bytes).toString('utf8')),
  );
  if (!Buffer.from(encodePacket(packet)).equals(bytes)) {
    throw new Error('Noncanonical mailbox packet');
  }
  return packet;
}

export function sameDevice(a: PairedDevice, b: PairedDevice): boolean {
  return (
    a.aci === b.aci &&
    a.device === b.device &&
    a.registration === b.registration &&
    a.identity === b.identity
  );
}
