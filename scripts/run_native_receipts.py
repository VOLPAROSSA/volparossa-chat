#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Run the actual built Desktop with an isolated local Signal mock service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = 'ts/test-mock/messaging/volparossa_receipt_reopen_test.node.ts'
TITLE = 'native Signal receipt recovery retains recipient-bound encrypted receipts across a desktop restart'
EXPECTED = dict(version=1, original_messages=4, receipt_senders=2, encrypted_receipts=2,
                delivered_after_reopen=3, unaddressed_control_stayed_sent=True,
                original_row_ids_retained=True, profile_reopened=True, teardown_completed=True,
                transport='loopback-signal-mock-service')


def native():
    if sorted(name for _, name in socket.if_nameindex()) != ['lo']:
        raise ValueError('native receipt trial requires an isolated loopback interface')
    # Positive control exercises both ends before starting any mock or app.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(), timeout=2), listener.accept()[0]:
            pass
    os.execvp('xvfb-run', ['xvfb-run', '-a', '-s', '-screen 0 1280x1024x24 -nolisten tcp',
               'node', 'node_modules/mocha/bin/mocha.js', '--forbid-pending', '--fail-zero',
               '--require', 'ts/test-mock/setup-ci.node.ts', '--grep', '^' + TITLE + '$', '--reporter', 'spec', FIXTURE])


def sandbox(private, candidate, node, fixture, target, driver, mounts=()):
    """Mount only verified runtimes and one fixture inside a fresh loopback-only Desktop."""
    for name in ('home', 'config', 'cache', 'data', 'runtime', 'tmp'):
        (private / name).mkdir(mode=0o700)
    # Populate a fresh mount namespace with runtime inputs only. Linking, X11 and
    # every mock-service socket live beneath this private namespace.
    args = ['bwrap', '--die-with-parent', '--unshare-user', '--unshare-pid', '--unshare-net',
            '--unshare-ipc', '--cap-drop', 'ALL', '--clearenv']
    for path in ('/usr', '/lib', '/lib64', '/bin', '/etc'):
        args.extend(['--ro-bind', path, path])
    args += ['--dev', '/dev', '--proc', '/proc', '--remount-ro', '/proc',
             '--tmpfs', '/tmp', '--bind', str(private), '/tmp/r', '--dir', '/run',
             '--ro-bind', str(node.parent), str(node.parent),
             '--ro-bind', str(candidate), str(candidate),
             '--tmpfs', str((candidate / target).parent),
             '--ro-bind', str(fixture), str(candidate / target),
             '--remount-ro', str((candidate / target).parent),
             '--ro-bind', str(driver), '/trial/run.py', '--chdir', str(candidate)]
    for source, destination in mounts:
        args += ['--ro-bind', str(source), destination]
    for k, v in dict(PATH=f'{node}:{candidate}/node_modules/.bin:/usr/bin:/bin',
                     HOME='/tmp/r/home', XDG_CONFIG_HOME='/tmp/r/config', XDG_CACHE_HOME='/tmp/r/cache',
                     XDG_DATA_HOME='/tmp/r/data', XDG_RUNTIME_DIR='/tmp/r/runtime', TMPDIR='/tmp/r/tmp',
                     CI='1', NODE_ENV='production', LANG='C.UTF-8', LC_ALL='C.UTF-8',
                     PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD='1', CHAT_NATIVE_RESULT='/tmp/r/result.json',
                     CHAT_NATIVE_PHASE='/tmp/r/phase').items():
        args.extend(['--setenv', k, v])
    return args + ['/usr/bin/python3', '-B', '/trial/run.py', '--inside']


def trial():
    build = ROOT / 'build'
    candidate = build / 'signal-backup-candidate'
    node = build / 'node-v24.19.0-linux-x64-with-npm/bin'
    fixture = ROOT / 'tests/native/receipt-reopen_test.node.ts'
    result = build / 'native-receipts-result.json'
    if result.exists() or result.is_symlink():
        raise ValueError('the output path already exists')
    source = json.loads((build / 'signal-source-report.json').read_bytes())
    before = hashlib.sha256((candidate / 'ts/textsecure/MessageReceiver.preload.ts').read_bytes()).hexdigest()
    expected_source = json.loads((ROOT / 'upstream-lock.json').read_bytes())['signal_desktop']['commit']
    overlay = json.loads((build / 'signal-overlay-report.json').read_bytes())
    if (source['commit'] != expected_source or source['all_git_blob_and_tree_hashes_verified'] is not True
            or overlay['upstream_commit'] != expected_source
            or before != overlay['modified_files']['ts/textsecure/MessageReceiver.preload.ts']):
        raise ValueError('candidate receiver does not match its verified source and overlay')
    with tempfile.TemporaryDirectory(prefix='receipt-test-', dir=build) as work:
        private = Path(work)
        args = sandbox(private, candidate, node, fixture, FIXTURE, Path(__file__).resolve())
        with (private / 'log').open('xb') as log:
            code = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, timeout=300, check=False).returncode
        if code:
            phase = (private / 'phase').read_text() if (private / 'phase').is_file() else 'bootstrap'
            phases = {'bootstrap', 'link', 'send', 'initial-state', 'peer-control', 'delivery', 'restart', 'cleanup'}
            detail = (private / 'log').read_bytes()[-8192:]
            markers = [b'bwrap:', b'SyntaxError', b'TimeoutError', b'Cannot find module',
                       b'No tests found', b'error while loading shared libraries', b'App failed to start']
            category = next((m.decode() for m in markers if m in detail), 'native assertion or startup error')
            raise ValueError(f'native receipt test failed during {phase if phase in phases else "unknown"} ({category})')
        report = json.loads((private / 'result.json').read_bytes())
        if report != EXPECTED:
            raise ValueError('native receipt report did not match the required observations')
    report.update(chat_revision=subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                  text=True).strip(), signal_revision=source['commit'],
                  receiver_sha256=before, test_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
                  native_network_interfaces=['lo'], private_profile_removed=True)
    with result.open('x') as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write('\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    try:
        native() if parser.parse_args().inside else trial()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
