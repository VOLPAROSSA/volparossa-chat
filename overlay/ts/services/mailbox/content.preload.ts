// Copyright 2026 Project VOLPAROSSA contributors
// SPDX-License-Identifier: AGPL-3.0-only

import { SignalService as Proto } from '../../protobuf/index.std.ts';
import type { MailboxRow } from './packet.node.ts';
import { check, digest } from './files.node.ts';

export function supportedContent(
  value: Proto.Content.Params,
  timestamp: number,
): Pick<MailboxRow, 'bodyDigest' | 'profileDigest' | 'receipt'> {
  check(
    !value.senderKeyDistributionMessage?.length,
    'Group key distribution is outside the paired mailbox',
  );
  // A contact may include its native account-signature proof under Signal's
  // existing phone-number privacy decision. Native receive verifies that proof.
  const signature = value.pniSignatureMessage;
  check(
    !signature ||
      (signature.pni?.length === 16 && signature.signature?.length === 64),
    'Invalid accompanying Signal identity proof',
  );
  const message = value.content?.dataMessage;
  if (message?.flags === Proto.DataMessage.Flags.PROFILE_KEY_UPDATE) {
    check(
      !message.body &&
        !message.groupV2 &&
        !message.attachments?.length &&
        !message.storyContext &&
        message.profileKey?.length === 32 &&
        Number(message.timestamp) === timestamp,
      'Invalid native profile-key update',
    );
    return { profileDigest: digest(message.profileKey) };
  }
  if (message) {
    check(
      typeof message.body === 'string' &&
        message.body.length > 0 &&
        Buffer.byteLength(message.body) <= 8192 &&
        Number(message.timestamp) === timestamp &&
        !message.groupV2 &&
        !message.attachments?.length &&
        !message.flags &&
        !message.sticker &&
        !message.reaction &&
        !message.delete &&
        !message.quote &&
        !message.preview?.length &&
        !message.contact?.length &&
        !message.storyContext &&
        !message.isViewOnce,
      'Unsupported paired message content',
    );
    return { bodyDigest: digest(message.body) };
  }
  const receipt = value.content?.receiptMessage;
  check(
    receipt && receipt.timestamp?.length && receipt.timestamp.length <= 32,
    'Unsupported paired content',
  );
  const timestamps = [...new Set(receipt.timestamp.map(Number))];
  check(
    timestamps.every((n) => Number.isSafeInteger(n) && n > 0),
    'Invalid receipt timestamps',
  );
  const kind = receipt.type ?? Proto.ReceiptMessage.Type.DELIVERY;
  const state =
    kind === Proto.ReceiptMessage.Type.DELIVERY
      ? 'Delivered'
      : kind === Proto.ReceiptMessage.Type.READ
        ? 'Read'
        : kind === Proto.ReceiptMessage.Type.VIEWED
          ? 'Viewed'
          : undefined;
  check(state, 'Unsupported Signal receipt');
  return { receipt: { state, timestamps } };
}
