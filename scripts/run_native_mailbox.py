#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Run two pinned Signal Desktops against the Core CLI and two signed mailbox stores."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'ts/test-mock/messaging/volparossa_paired_mailbox_test.node.ts'
TITLE = 'native paired Signal mailbox recovers one offline message exactly once after native durable import'
CORE_SOURCE = '67ae4b6948f77cd33fd2af719878fc2d9b14ec96'
OBSERVATIONS = {
    'version': 1, 'standalone_desktops': 2, 'mailbox_providers': 2,
    'stock_messaging_before_pairing': True, 'receiver_offline_during_deposit': True,
    'sender_offline_during_pickup': True, 'repeat_fetch_before_import': True,
    'confirm_before_import': 0, 'native_row_survived_restart': True,
    'repeat_import_preserved_ratchet': True, 'delivered_after_sender_restart': True,
    'paired_stock_submissions': 0, 'both_provider_inboxes_empty': True,
}


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def check(value, message):
    if not value:
        raise ValueError(message)


def executable(path):
    path = path.resolve(strict=True)
    st = path.stat()
    check(stat.S_ISREG(st.st_mode) and st.st_uid in {0, os.getuid()} and st.st_mode & 0o111
          and not st.st_mode & 0o022, 'expected owned executable')
    return path


def wait_ready(proc, predicate, deadline):
    while time.monotonic() < deadline:
        check(proc.poll() is None, 'fixture stopped before readiness')
        if predicate():
            return
        time.sleep(0.05)
    raise TimeoutError('fixture readiness deadline')


def join(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_desktops():
    unix = Path('/tmp/.X11-unix')
    unix.mkdir(mode=0o1777, exist_ok=True)
    with subprocess.Popen(['/trial/Xvfb', ':156', '-screen', '0', '1280x1024x24', '-nolisten', 'tcp']) as display:
        try:
            wait_ready(display, (unix / 'X156').is_socket, time.monotonic() + 10)
            command = ['node', 'node_modules/mocha/bin/mocha.js', '--forbid-pending', '--fail-zero',
                       '--require', 'ts/test-mock/setup-ci.node.ts', '--grep', '^' + TITLE + '$',
                       '--reporter', 'spec', TARGET]
            subprocess.run(command, env=os.environ | {'DISPLAY': ':156'}, timeout=275, check=True)
        finally:
            join(display)


def run_inside(prepared):
    check(sorted(name for _, name in socket.if_nameindex()) == ['lo'], 'isolated loopback required')
    state = Path('/tmp/r/providers')
    if prepared:
        cli = '/trial/core/volparossa'
        agent = '/trial/core/agent.sock'
        check(Path(agent).is_socket(), 'missing prepared Core agent')
        for side in ('a', 'b'):
            config = json.loads((state / side / 'core.json').read_bytes())
            check(config['executable'] == cli and config['socket'] == agent, 'prepared Core binding differs')
        status = subprocess.check_output([cli, '--control-socket', agent, 'content', 'status'], timeout=15)
        check(bool(json.loads(status)['control_relay_peer_id']), 'prepared agent has no active control relay')
        run_desktops()
        return
    state.mkdir(mode=0o700)
    with subprocess.Popen(['/trial/core/mailbox-provider-pair', str(state)], stdin=subprocess.PIPE) as provider:
        try:
            wait_ready(provider, (state / 'ready').is_file, time.monotonic() + 100)
            run_desktops()
            check(provider.poll() is None, 'provider stopped during native mailbox trial')
        finally:
            provider.stdin.close()
            try:
                provider.wait(timeout=10)
            finally:
                join(provider)
    check(provider.returncode == 0, 'provider cleanup failed')


def build_core(src, target):
    src = src.resolve(strict=True)
    git = lambda *args: subprocess.check_output(['git', '-C', str(src), *args], text=True).strip()
    check(git('rev-parse', 'HEAD') == CORE_SOURCE and not git('status', '--porcelain'), 'expected clean pinned Core checkout')
    rust = subprocess.check_output(['rustc', '--version'], text=True).strip()
    check(rust.startswith('rustc 1.89.0 '), 'expected Core Rust 1.89.0 toolchain')
    subprocess.run(['cargo', 'build', '--locked', '--offline', '--target-dir', str(target),
                    '-p', 'volparossa', '--example', 'mailbox-provider-pair', '--bin', 'volparossa'],
                   cwd=src, check=True, timeout=1800)
    check(git('rev-parse', 'HEAD') == CORE_SOURCE and not git('status', '--porcelain'), 'Core source changed')
    return executable(target / 'debug/volparossa'), executable(target / 'debug/examples/mailbox-provider-pair'), rust


def trial(args):
    from run_native_receipts import sandbox
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    check(not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT), 'expected clean Chat source')
    build = ROOT / 'build'
    candidate = build / 'signal-backup-candidate'
    result = build / 'native-mailbox-result.json'
    check(not os.path.lexists(result), 'output already exists')
    fixture = ROOT / 'tests/native/paired-mailbox_test.node.ts'
    bridge = ROOT / 'scripts/signal_mailbox.py'
    target = (args.cargo_target or build / 'core-target').resolve()
    cli, provider, rust = build_core(args.core_source, target)
    xvfb = executable(args.xvfb)
    signal_revision = json.loads((ROOT / 'upstream-lock.json').read_bytes())['signal_desktop']['commit']
    source = json.loads((build / 'signal-source-report.json').read_bytes())
    overlay = json.loads((build / 'signal-overlay-report.json').read_bytes())
    check(source['all_git_blob_and_tree_hashes_verified'] is True and source['commit'] == overlay['upstream_commit']
          == signal_revision, 'Signal source revision mismatch')
    for name, expected in overlay['modified_files'].items():
        check(digest(candidate / name) == expected, 'staged overlay differs')
    runtime_paths = ['bundles/preload/main.js', 'bundles/workers/sql.js', 'node_modules/electron/dist/electron',
                     'node_modules/@signalapp/libsignal-client/prebuilds/linux-x64/@signalapp+libsignal-client.node',
                     'node_modules/@signalapp/sqlcipher/prebuilds/linux-x64/@signalapp+sqlcipher.node']
    hashes = {path: digest(candidate / path) for path in runtime_paths}
    cli_hash, provider_hash = digest(cli), digest(provider)
    with tempfile.TemporaryDirectory(prefix='paired-desktop-', dir=build) as work:
        private = Path(work) / 'runtime'
        private.mkdir(mode=0o700)
        # A concurrent Cargo build can replace a target executable. Freeze the two
        # checked inputs before running any synthetic account or signed store.
        core_tools = Path(work) / 'tools'
        core_tools.mkdir(mode=0o700)
        for src, name, expected in ((cli, 'volparossa', cli_hash), (provider, 'mailbox-provider-pair', provider_hash)):
            dst = core_tools / name
            shutil.copyfile(src, dst)
            dst.chmod(0o500)
            check(digest(dst) == expected, 'Core executable changed before it could be mounted')
        mounts = [(core_tools, '/trial/core'), (bridge, '/trial/signal_mailbox.py'), (xvfb, '/trial/Xvfb')]
        node = build / 'node-v24.19.0-linux-x64-with-npm/bin'
        command = sandbox(private, candidate, node, fixture, TARGET, Path(__file__).resolve(), mounts)
        with (private / 'log').open('xb') as log:
            status = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=400)
        if status.returncode:
            phase = (private / 'phase').read_text() if (private / 'phase').is_file() else 'bootstrap'
            check(phase in {'bootstrap', 'register-a', 'register-b', 'open-before', 'warm-send', 'warm-receive',
                  'warm-reply', 'stock-acknowledgements', 'pair', 'offline-compose', 'outgoing-row', 'private-outbox', 'outbox-binding', 'provider-custody', 'offline-fetch',
                  'recipient-reopen', 'confirm-native-import', 'peer-receipt', 'sender-reopen', 'complete'},
                  'unexpected fixture phase')
            raise ValueError('native mailbox trial failed during ' + phase)
        observed = json.loads((private / 'result.json').read_bytes())
        check(json.dumps(observed, sort_keys=True) == json.dumps(OBSERVATIONS, sort_keys=True), 'incomplete observations')
    check(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip() == revision
          and not subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT), 'Chat source changed during the trial')
    report = dict(observed, chat_revision=revision, signal_revision=signal_revision, core_fixture_revision=CORE_SOURCE,
                  rustc=rust, core_cli_sha256=cli_hash, core_provider_fixture_sha256=provider_hash,
                  fixture_sha256=digest(fixture), bridge_sha256=digest(bridge), runtime_sha256=hashes,
                  original_signal_network='isolated Signal test service', mailbox_transport='local signed Core streams',
                  private_profiles_removed=True, provider_stores_removed=True, native_network_interfaces=['lo'])
    with result.open('x') as out:
        json.dump(report, out, sort_keys=True, indent=2)
        out.write('\n')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--prepared-providers', action='store_true', help='use the existing Core KVM guest agent inside the native sandbox')
    parser.add_argument('--core-source', type=Path, help='clean checkout of the pinned Core revision with locked dependencies available')
    parser.add_argument('--cargo-target', type=Path, help='reuse compatible Cargo intermediates for the offline build')
    parser.add_argument('--xvfb', type=Path, default=shutil.which('Xvfb'))
    try:
        args = parser.parse_args()
        if args.inside:
            run_inside(args.prepared_providers)
        else:
            check(not args.prepared_providers, 'prepared providers require the existing isolated guest sandbox')
            check(args.core_source and args.xvfb, 'provide --core-source and an installed or explicit --xvfb')
            trial(args)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
