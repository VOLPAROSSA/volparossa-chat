#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Offline tests of persistent application handoffs around the Core CLI boundary."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import signal_mailbox as mailbox


class MailboxHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.bridge = mailbox.Bridge.__new__(mailbox.Bridge)
        self.bridge.root = self.root
        self.bridge.state = mailbox.directory(self.root / 'transport', True)
        self.bridge.pair = {'sendGrant': '11' * 32, 'receiveGrant': '22' * 32, 'expires': 4_000_000_000_000}
        self.bridge.config = {'receiveInvitation': 'in', 'sendInvitation': 'out', 'remoteOwner': '33' * 32}
        for group in ('outbox', 'custody', 'inbox', 'imported', 'retired'):
            mailbox.directory(self.root / group, True)

    def packet(self, direction):
        return json.dumps({'grant': self.bridge.pair[direction], 'synthetic': True}).encode()

    def test_manifest_free_cache_is_preserved_before_retrying_original_packet(self):
        payload = self.packet('sendGrant')
        packet = self.root / 'outbox' / mailbox.sha(payload)
        mailbox.publish(packet, payload)
        state = mailbox.directory(self.bridge.state / packet.name, True)
        cache = mailbox.directory(state / 'cache', True)
        mailbox.publish(cache / '.volparossa-owner-v1', b'first preparation')
        self.bridge.cli = Mock(return_value={'confirmed_providers': 2, 'message_id': '44' * 32})
        self.assertEqual(self.bridge.deposit(), 1)
        args = self.bridge.cli.call_args.args
        self.assertEqual(args[0], 'send')
        self.assertEqual(args[args.index('--input') + 1], packet)
        self.assertNotIn('--resume', args)
        self.assertFalse(cache.exists())
        self.assertEqual((state / 'first-unpublished-cache' / '.volparossa-owner-v1').read_bytes(), b'first preparation')
        self.assertEqual(mailbox.read(packet, 256), payload)
        self.assertEqual(self.bridge.deposit(), 0)
        self.bridge.cli.assert_called_once()

    def test_existing_manifest_always_uses_the_original_resume_path(self):
        payload = self.packet('sendGrant')
        packet = self.root / 'outbox' / mailbox.sha(payload)
        mailbox.publish(packet, payload)
        state = mailbox.directory(self.bridge.state / packet.name, True)
        cache = mailbox.directory(state / 'cache', True)
        mailbox.publish(state / 'manifest', b'original incomplete or valid manifest')
        self.bridge.cli = Mock(side_effect=ValueError('Core refused the original manifest'))
        with self.assertRaisesRegex(ValueError, 'Core refused'):
            self.bridge.deposit()
        args = self.bridge.cli.call_args.args
        self.assertIn('--resume', args)
        self.assertNotIn('--input', args)
        self.assertTrue(cache.is_dir())
        self.assertFalse((state / 'first-unpublished-cache').exists())
        self.assertFalse((self.root / 'custody' / packet.name).exists())

    def test_repeated_preparation_preserves_unrecognized_entries(self):
        original = mailbox.directory(self.root / 'first', True)
        cache = mailbox.directory(self.root / 'cache', True)
        owner = cache / '.volparossa-owner-v1'
        mailbox.publish(owner, b'second preparation')
        other = cache / 'consumer-owned'
        mailbox.publish(other, b'retain')
        with self.assertRaisesRegex(ValueError, 'unexpected entry'):
            mailbox.preserve_unpublished_cache(cache, original)
        self.assertEqual(other.read_bytes(), b'retain')
        self.assertTrue(owner.exists())
        other.unlink()
        mailbox.preserve_unpublished_cache(cache, original)
        self.assertFalse(cache.exists())
        self.assertTrue(original.is_dir())

    def test_native_settlement_recovers_after_crash_and_stale_republication(self):
        payload = self.packet('sendGrant')
        key = mailbox.sha(payload)
        marker = (self.bridge.pair['sendGrant'] + '\n').encode()
        mailbox.publish(self.root / 'outbox' / key, payload)
        self.bridge.cli = Mock(return_value={'confirmed_providers': 2, 'message_id': '77' * 32})
        self.assertEqual(self.bridge.deposit(), 1)
        self.assertTrue((self.root / 'outbox' / key).exists())
        mailbox.publish(self.root / 'retired' / key, marker)
        with patch.object(mailbox, 'remove_private_tree', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.bridge.deposit()
        self.assertTrue((self.root / 'retired' / key).exists())
        self.assertEqual(self.bridge.deposit(), 0)
        for name in ('outbox', 'retired', 'custody', 'transport'):
            self.assertFalse((self.root / name / key).exists())
        # A sender suspended before publication can wake after every file was retired.
        mailbox.publish(self.root / 'outbox' / key, payload)
        self.assertEqual(self.bridge.deposit(), 0)
        self.assertFalse((self.root / 'outbox' / key).exists())
        self.bridge.cli.assert_called_once()

    def test_more_than_256_distinct_settled_sends_do_not_stall_subsequent_work(self):
        marker = (self.bridge.pair['sendGrant'] + '\n').encode()
        digests = set()
        self.bridge.cli = Mock(return_value={'confirmed_providers': 2, 'message_id': '77' * 32})
        for i in range(270):
            payload = json.dumps({'grant': self.bridge.pair['sendGrant'], 'sequence': i}).encode()
            key = mailbox.sha(payload)
            digests.add(key)
            mailbox.publish(self.root / 'outbox' / key, payload)
        self.assertEqual(self.bridge.deposit(), 270)
        for key in digests:
            mailbox.publish(self.root / 'retired' / key, marker)
        self.assertEqual(self.bridge.deposit(), 0)
        self.assertEqual(self.bridge.cli.call_count, 270)
        self.assertFalse(list((self.root / 'outbox').iterdir()))
        self.assertFalse(list((self.root / 'custody').iterdir()))
        with mailbox.closing(self.bridge.send_ledger()) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM settled').fetchone(), (270,))

    def test_empty_poll_future_arrival_and_native_import_confirmation(self):
        payload = self.packet('receiveGrant')
        message_id = '77' * 32
        waiting = False
        poll_number = 0
        acknowledged = False
        def cli(operation, *args):
            nonlocal poll_number, acknowledged
            if operation == 'fetch':
                poll_number += 1
                incoming = mailbox.directory(args[args.index('--output-dir') + 1], True)
                count = int(waiting and not acknowledged)
                if count:
                    item = mailbox.directory(incoming / message_id, True)
                    mailbox.publish(item / 'payload', payload)
                    mailbox.publish(item / 'import-token', bytes([poll_number]) * 32)
                    mailbox.publish(item / 'pending.pb', f'pending {poll_number}'.encode())
                return {'messages_fetched': count, 'acknowledged_providers_per_message': 0,
                        'consumer_import_attested': False, 'provider_custody_consumed': False}
            self.assertEqual(operation, 'confirm-import')
            kept = args[args.index('--pending') + 1]
            self.assertEqual(kept.read_bytes(), b'pending 2')
            self.assertEqual((kept.parent / 'import-token').read_bytes(), bytes([2]) * 32)
            self.assertEqual(args[args.index('--imported-sha256') + 1], mailbox.sha(payload))
            self.assertFalse(acknowledged)
            acknowledged = True
            result = {'operation': 'mailbox_confirm_import', 'message_id': message_id,
                      'consumer_import_attested': True, 'acknowledged_providers': 2}
            mailbox.publish(kept.parent / 'confirmed.json', json.dumps(result).encode())
            return result
        self.bridge.cli = cli
        self.assertEqual(self.bridge.fetch(), 0)
        waiting = True
        self.assertEqual(self.bridge.fetch(), 1)
        self.assertEqual(self.bridge.fetch(), 1)
        self.assertEqual(self.bridge.confirm(), 0)
        self.assertFalse(acknowledged)
        key = mailbox.sha(payload)
        self.assertEqual(mailbox.read(self.root / 'inbox' / key, 256), payload)
        mailbox.publish(self.root / 'imported' / key, (self.bridge.pair['receiveGrant'] + '\n').encode())
        with patch.object(mailbox, 'remove_private_tree', side_effect=OSError('interrupted after acknowledgement')):
            with self.assertRaises(OSError):
                self.bridge.confirm()
        self.assertTrue(acknowledged)
        self.assertFalse(list((self.bridge.state / 'received').iterdir()))
        self.assertTrue(list((self.bridge.state / 'confirmed').iterdir()))
        self.assertEqual(self.bridge.confirm(), 0)
        self.assertEqual(self.bridge.fetch(), 0)
        for name in ('inbox', 'imported', 'transport/confirmed'):
            self.assertFalse(list((self.root / name).iterdir()))


if __name__ == '__main__':
    unittest.main()
