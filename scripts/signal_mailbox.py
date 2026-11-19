#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Move paired Signal ciphertext through the ordinary authenticated Core mailbox CLI."""
import argparse
import fcntl
import hashlib
from contextlib import closing
from itertools import islice
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

HEX = re.compile(r'[a-f0-9]{64}\Z')
PACKET_LIMIT = 32768


def require(condition, message):
    if not condition:
        raise ValueError(message)


def directory(path, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    st = path.lstat()
    require(path.is_absolute() and path.resolve() == path and stat.S_ISDIR(st.st_mode)
            and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700,
            'private owned directory required')
    return path


def read(path, maximum):
    directory(path.parent)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as handle:
        st = os.fstat(handle.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1
                and stat.S_IMODE(st.st_mode) == 0o600 and 0 < st.st_size <= maximum, 'invalid private handoff')
        data = handle.read(maximum + 1)
        require(len(data) == st.st_size, 'handoff changed')
        return data


def sync_dir(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path, data):
    directory(path.parent)
    try:
        require(read(path, len(data)) == data, 'existing handoff differs')
        return
    except FileNotFoundError:
        pass
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.', delete=False) as f:
        name = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            # The bridge alone publishes custody/inbox under its exclusive lock.
            # Rename atomically so a crash never leaves a second hard link that
            # would invalidate the native consumer's single-link check.
            os.replace(name, path)
        finally:
            name.unlink(missing_ok=True)
    sync_dir(path.parent)


def preserve_unpublished_cache(cache, original):
    # Core writes its manifest atomically before the first deposit. Only this
    # manifest-free preparation is replaceable; retain the first failure.
    directory(cache)
    if not os.path.lexists(original):
        cache.rename(original)
    else:
        directory(original)
        allowed = re.compile(r'(?:[0-9a-f]{64}|\.volparossa-(?:owner|index|chunk-next|index-next|replicas|mailbox)-v1)\Z')
        entries = list(islice(cache.iterdir(), 257))
        require(len(entries) <= 256, 'incomplete preparation exceeds the handoff limit')
        for child in entries:
            info = child.lstat()
            require(allowed.fullmatch(child.name) and stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                    'unexpected entry in incomplete Core preparation')
        for child in entries:
            child.unlink()
        cache.rmdir()
    sync_dir(cache.parent)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def packets(directory_path):
    # The live queue is streamed. Settled or concurrently added packets do not
    # turn an otherwise healthy grant into a permanent lifetime limit.
    for entry in directory_path.iterdir():
        if entry.name.startswith('.'):
            continue
        require(HEX.fullmatch(entry.name), 'unexpected handoff name')
        yield entry


CACHE_FILE = re.compile(r'(?:[0-9a-f]{64}|\.volparossa-(?:owner|index|chunk-next|index-next|replicas|mailbox)-v1)\Z')
RECEIVE_FILE = re.compile(r'(?:payload|import-token|pending\.pb|confirmed\.json|ack-[a-f0-9]{64}\.json)\Z')


def check_owned_file(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600, 'unexpected file in settled handoff')


def retired_files(path, pattern):
    directory(path)
    children = list(islice(path.iterdir(), 257))
    require(len(children) <= 256, 'unexpected settled packet layout')
    for child in children:
        require(pattern.fullmatch(child.name), 'unknown file in settled handoff')
        check_owned_file(child)
    return children


def remove_private_tree(path, direction):
    # Inspect every name before touching any item. A partially retired tree
    # contains a subset of the original files and can be retried after a crash.
    if not os.path.lexists(path):
        return
    directory(path)
    if direction == 'receive':
        files = retired_files(path, RECEIVE_FILE)
        subdirs = []
    else:
        entries = list(islice(path.iterdir(), 4))
        require(len(entries) <= 3, 'unexpected outgoing retirement layout')
        files, subdirs = [], []
        for child in entries:
            if child.name == 'manifest':
                check_owned_file(child)
                files.append(child)
            else:
                require(child.name in ('cache', 'first-unpublished-cache'), 'unknown outgoing retirement path')
                files.extend(retired_files(child, CACHE_FILE))
                subdirs.append(child)
    for file in files:
        file.unlink()
    for child in subdirs:
        child.rmdir()
    path.rmdir()
    sync_dir(path.parent)


def remove_matching(path, expected, maximum):
    try:
        require(read(path, maximum) == expected, 'settled handoff differs')
    except FileNotFoundError:
        return
    path.unlink()
    sync_dir(path.parent)


class Bridge:
    def __init__(self, root):
        self.root = directory(root)
        self.pair = json.loads(read(root / 'pair.json', 4096))
        self.config = json.loads(read(root / 'core.json', 8192))
        pair, cfg = self.pair, self.config
        require(pair['version'] == 1 and int(time.time() * 1000) < pair['expires']
                and HEX.fullmatch(pair['sendGrant']) and HEX.fullmatch(pair['receiveGrant'])
                and pair['sendGrant'] != pair['receiveGrant'], 'paired invitation expired or invalid')
        require(set(cfg) == {'executable', 'socket', 'identity', 'passphrase', 'sendInvitation', 'receiveInvitation', 'remoteOwner'}
                and HEX.fullmatch(cfg['remoteOwner']), 'invalid Core configuration')
        for name in ('executable', 'socket', 'identity', 'passphrase', 'sendInvitation', 'receiveInvitation'):
            path = Path(cfg[name])
            require(path.is_absolute() and path.resolve() == path, 'absolute canonical Core paths required')
        exe, sock = Path(cfg['executable']).stat(), Path(cfg['socket']).stat()
        require(stat.S_ISREG(exe.st_mode) and exe.st_uid in {0, os.getuid()} and exe.st_mode & 0o111
                and not exe.st_mode & 0o022 and stat.S_ISSOCK(sock.st_mode), 'invalid Core executable or socket')
        for name, role in [('sendInvitation', 'sendGrant'), ('receiveInvitation', 'receiveGrant')]:
            require(sha(read(Path(cfg[name]), 16384)) == pair[role], 'invitation differs from the authenticated pairing')
        self.state = directory(root / 'transport', True)
        for name in ('outbox', 'custody', 'inbox', 'imported', 'retired'):
            directory(root / name, True)

    def cli(self, operation, *args):
        cfg = self.config
        cmd = [cfg['executable'], '--control-socket', cfg['socket'], 'content', 'mailbox', operation,
               *map(str, args), '--identity', cfg['identity'], '--passphrase-file', cfg['passphrase']]
        # Spool bounded output from the pinned CLI. Its JSON may include signed private custody receipts.
        with tempfile.TemporaryFile(dir=self.state) as out:
            p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}, timeout=180, check=False)
            require(p.returncode == 0 and 0 < out.tell() <= 4 * 1024 * 1024, 'Core operation incomplete, original state retained')
            out.seek(0)
            report = json.load(out)
        require(report['operation'] == 'mailbox_' + operation.replace('-', '_'), 'unexpected Core receipt')
        return report

    def send_ledger(self):
        # Keep only a hash and expiry after native custody. A briefly stalled
        # native sender may republish its former handoff after cleanup.
        path = self.state / 'settled-sends.sqlite'
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
            sync_dir(self.state)
        check_owned_file(path)
        db = sqlite3.connect(path)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS settled (key TEXT PRIMARY KEY, grant TEXT NOT NULL, expires INTEGER NOT NULL) WITHOUT ROWID')
        db.execute('CREATE INDEX IF NOT EXISTS settled_expiry ON settled(expires)')
        with db:
            db.execute('DELETE FROM settled WHERE expires <= ?', (int(time.time() * 1000),))
        return db

    def retire_outgoing(self, db, key, marker, already=False):
        if not already:
            require(read(self.root / 'retired' / key, 128) == marker, 'native custody grant differs')
            with db:
                db.execute('INSERT INTO settled(key, grant, expires) VALUES(?, ?, ?) ON CONFLICT(key) DO NOTHING',
                           (key, self.pair['sendGrant'], self.pair['expires']))
        found = db.execute('SELECT grant FROM settled WHERE key = ?', (key,)).fetchone()
        require(found == (self.pair['sendGrant'],), 'retired packet authority differs')
        packet = self.root / 'outbox' / key
        if os.path.lexists(packet):
            data = read(packet, PACKET_LIMIT)
            require(sha(data) == key and json.loads(data)['grant'] == self.pair['sendGrant'], 'outbound packet differs')
            remove_matching(packet, data, PACKET_LIMIT)
        remove_private_tree(self.state / key, 'send')
        remove_matching(self.root / 'custody' / key, marker, 128)
        remove_matching(self.root / 'retired' / key, marker, 128)

    def deposit(self):
        with closing(self.send_ledger()) as db:
            marker = (self.pair['sendGrant'] + '\n').encode('ascii')
            for entry in packets(directory(self.root / 'retired', True)):
                if read(entry, 128) == marker:
                    self.retire_outgoing(db, entry.name, marker)
            return self.deposit_live(db, marker)

    def deposit_live(self, db, marker):
        count = 0
        for packet in packets(self.root / 'outbox'):
            data = read(packet, PACKET_LIMIT)
            grant = json.loads(data)['grant']
            require(sha(data) == packet.name and HEX.fullmatch(grant), 'outbound packet differs')
            if grant != self.pair['sendGrant']:
                continue
            saved = self.root / 'custody' / packet.name
            if db.execute('SELECT 1 FROM settled WHERE key = ?', (packet.name,)).fetchone():
                self.retire_outgoing(db, packet.name, marker, already=True)
                continue
            if saved.exists():
                require(read(saved, 128) == marker, 'custody belongs to another grant')
                continue
            state = directory(self.state / packet.name, True)
            cache, manifest = state / 'cache', state / 'manifest'
            if not os.path.lexists(manifest) and os.path.lexists(cache):
                preserve_unpublished_cache(cache, state / 'first-unpublished-cache')
            args = ['--resume'] if os.path.lexists(manifest) else ['--input', packet]
            # Leave a retained manifest, including an invalid one, to the exact Core resume path.
            result = self.cli('send', '--invitation', self.config['sendInvitation'], '--owner-key', self.config['remoteOwner'],
                              '--cache', cache, '--manifest', manifest, *args)
            require(result['confirmed_providers'] == 2 and HEX.fullmatch(result['message_id']), 'incomplete custody')
            publish(saved, marker)
            count += 1
        return count

    def fetch(self):
        pending = directory(self.state / 'received', True)
        with tempfile.TemporaryDirectory(prefix='poll-', dir=self.state) as work:
            incoming = Path(work) / 'messages'
            result = self.cli('fetch', '--invitation', self.config['receiveInvitation'], '--output-dir', incoming)
            require(result['consumer_import_attested'] is False and result['provider_custody_consumed'] is False
                    and result['acknowledged_providers_per_message'] == 0, 'fetch unexpectedly consumed provider custody')
            count = 0
            for item in packets(directory(incoming)):
                directory(item)
                read(item / 'pending.pb', 16384)
                require(len(read(item / 'import-token', 32)) == 32, 'invalid pending import token')
                payload = read(item / 'payload', PACKET_LIMIT)
                require(json.loads(payload)['grant'] == self.pair['receiveGrant'], 'packet belongs to another invitation')
                retained = pending / item.name
                if retained.exists():
                    require(read(retained / 'payload', PACKET_LIMIT) == payload, 'retained pending payload changed')
                else:
                    item.rename(retained)
                    sync_dir(pending)
                publish(self.root / 'inbox' / sha(payload), payload)
                count += 1
            require(type(result['messages_fetched']) is int and count == result['messages_fetched'],
                    'Core fetch count differs from durable handoffs')
        # Core keeps custody on both providers until the native consumer explicitly confirms.
        return count

    def finish_imports(self, completed):
        directory(completed)
        marker = (self.pair['receiveGrant'] + '\n').encode('ascii')
        for item in completed.iterdir():
            parts = item.name.split('-')
            require(len(parts) == 2 and all(HEX.fullmatch(part) for part in parts), 'unknown confirmed import')
            _, key = parts
            handoff = self.root / 'inbox' / key
            if os.path.lexists(handoff):
                payload = read(handoff, PACKET_LIMIT)
                require(sha(payload) == key and json.loads(payload)['grant'] == self.pair['receiveGrant'], 'inbox packet differs')
                remove_matching(handoff, payload, PACKET_LIMIT)
            remove_matching(self.root / 'imported' / key, marker, 128)
            remove_private_tree(item, 'receive')

    def confirm(self):
        completed_dir = directory(self.state / 'confirmed', True)
        self.finish_imports(completed_dir)
        pending = self.state / 'received'
        if not pending.exists():
            return 0
        count = 0
        for item in packets(directory(pending)):
            directory(item)
            payload = read(item / 'payload', PACKET_LIMIT)
            if json.loads(payload)['grant'] != self.pair['receiveGrant']:
                continue
            key = sha(payload)
            accepted = self.root / 'imported' / key
            if not accepted.exists():
                continue
            require(read(accepted, 128) == (self.pair['receiveGrant'] + '\n').encode('ascii'), 'native import grant differs')
            completed = item / 'confirmed.json'
            if completed.exists():
                result = json.loads(read(completed, 32768))
            else:
                result = self.cli('confirm-import', '--pending', item / 'pending.pb', '--import-token-file', item / 'import-token',
                                  '--imported-sha256', key)
                count += 1
            require(result['operation'] == 'mailbox_confirm_import' and result['message_id'] == item.name
                    and result['consumer_import_attested'] is True and result['acknowledged_providers'] == 2,
                    'consumer confirmation incomplete')
            # This signed, native-confirmed publication is the durable cleanup
            # boundary. A crash on either side of rename preserves retry state.
            retired = completed_dir / (item.name + '-' + key)
            require(not os.path.lexists(retired), 'confirmed handoff already exists')
            item.rename(retired)
            sync_dir(completed_dir)
            sync_dir(pending)
        self.finish_imports(completed_dir)
        return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('deposit', 'fetch', 'confirm'))
    parser.add_argument('--mailbox', type=Path, required=True, help='private volparossa-mailbox directory in the linked Desktop profile')
    args = parser.parse_args()
    bridge = Bridge(args.mailbox)
    with os.fdopen(os.open(bridge.state / '.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600), 'wb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        count = getattr(bridge, args.command)()
    print(json.dumps({'operation': args.command, 'packets': count}))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, OSError, sqlite3.Error, subprocess.SubprocessError, TypeError, ValueError):
        sys.exit('Signal mailbox transfer incomplete. Retain the private handoff and retry.')
