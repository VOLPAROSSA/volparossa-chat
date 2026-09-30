#!/usr/bin/env python3
"""Offline dependency provisioner checks; no package downloads or native execution."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage_signal_dependencies as stage


def put(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(data)


def fixture(build):
    lock = b"offline synthetic exact lock\n"
    lock_hash = hashlib.sha256(lock).hexdigest()
    candidate = build / "signal-backup-candidate"
    runtime = build / (stage.NAME + "-with-npm")
    manager = build / f"pnpm-{stage.PNPM_VERSION}"
    for path, content in (
            (candidate / "pnpm-lock.yaml", lock),
            (candidate / "package.json", b'{"private":true}'),
            (candidate / "packages/mock-server/package.json", b'{"private":true}'),
            (runtime / "bin/node", b"synthetic pinned node"),
            (runtime / "bin/npm", b"synthetic pinned npm"),
            (manager / "bin/pnpm.mjs", b"synthetic pinned pnpm entrypoint"),
            (manager / "dist/pnpm.mjs", b"synthetic pinned pnpm bundle")):
        put(path, content)
    put(build / "node-npm-runtime-report.json", json.dumps(dict(
        sha256=stage.NODE_SHA256, version=stage.NODE_VERSION,
        launchers_use_pinned_node=True, npm_version="11.17.0")).encode())
    put(build / "pnpm-runtime-report.json", json.dumps(dict(
        integrity=stage.INTEGRITY, version=stage.PNPM_VERSION,
        signal_source=stage.COMMIT, lock_sha256=lock_hash)).encode())
    return candidate, runtime, manager, lock_hash


class SignalDependencyStaging(unittest.TestCase):
    def temporary(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def test_environment_and_nested_wrapper_stay_scoped_without_home_override(self):
        build = self.temporary()
        state, runtime, manager = build / "state", build / "runtime", build / "manager"
        inherited = dict(HOME="/unchanged-owner-home", USER="owner", CI="true",
                         NODE_OPTIONS="--require /untrusted", HTTPS_PROXY="http://proxy",
                         NPM_TOKEN="secret", PNPM_HOME="/outside")
        with mock.patch.dict(os.environ, inherited, clear=True):
            env = stage.environment(state, runtime)
            wrapper = stage.launcher(state, runtime, manager).decode()
        self.assertEqual(env["HOME"], inherited["HOME"])
        for key in ("CI", "HTTPS_PROXY", "NPM_TOKEN", "PNPM_HOME"):
            self.assertNotIn(key, env)
        self.assertNotIn("untrusted", env["NODE_OPTIONS"])
        self.assertNotIn("export HOME", wrapper)
        self.assertIn("--config.pm-on-fail=error", wrapper)
        self.assertIn("--config.runtime=false", wrapper)
        self.assertNotIn("runtime_on_fail", wrapper)
        self.assertIn("--config.verify-store-integrity=true", wrapper)
        self.assertIn("--config.enable-global-virtual-store=false", wrapper)
        self.assertIn("export pnpm_config_ignore_scripts=true", wrapper)
        self.assertIn("export XDG_CONFIG_HOME=", wrapper)
        self.assertIn(str(runtime / "bin/node"), wrapper)
        self.assertEqual(env["PNPM_MAX_WORKERS"], "2")
        self.assertTrue(env["NPM_CONFIG_USERCONFIG"].startswith(str(state)))

    def test_fresh_prepare_checks_overlay_and_resume_preserves_downloads(self):
        build = self.temporary()
        candidate, runtime, _, lock_hash = fixture(build)
        with mock.patch.object(stage, "LOCK_SHA256", lock_hash), \
                mock.patch.object(stage.subprocess, "run") as checker:
            _, _, state, marker = stage.prepare(build)
            checker.assert_called_once()
            self.assertIn("--check", checker.call_args.args[0])
            put(state / "store/download", b"retained partial archive")
            put(candidate / "node_modules/installed/index.js", b"synthetic downloaded package")
            put(candidate / "packages/mock-server/node_modules/foo", b"synthetic workspace package")
            self.assertEqual(stage.prepare(build, resume=True)[3], marker)
            self.assertEqual(checker.call_count, 1)
            self.assertEqual((state / "store/download").read_bytes(), b"retained partial archive")
            self.assertEqual(marker["lock_sha256"], lock_hash)
            self.assertEqual(marker["node_sha256"], stage.digest(runtime / "bin/node"))
            self.assertEqual((state / "bin/pnpm").stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ValueError):
                stage.prepare(build)
            put(candidate / "package.json", b"changed source")
            with self.assertRaisesRegex(ValueError, "source/configuration changed"):
                stage.prepare(build, resume=True)

    def test_resume_migrates_only_exact_previous_launcher(self):
        build = self.temporary()
        _, runtime, manager, lock_hash = fixture(build)
        with mock.patch.object(stage, "LOCK_SHA256", lock_hash), \
                mock.patch.object(stage.subprocess, "run"):
            _, _, state, marker = stage.prepare(build)
            previous = stage.launcher(state, runtime, manager, legacy=True)
            put(state / "bin/pnpm", previous)
            put(state / "store/download", b"preserved")
            put(state / "attempt-old.json", b"historical failure")
            self.assertEqual(stage.prepare(build, resume=True)[3], marker)
            self.assertEqual((state / "bin/pnpm").read_bytes(), stage.launcher(state, runtime, manager))
            self.assertEqual((state / "store/download").read_bytes(), b"preserved")
            self.assertEqual((state / "attempt-old.json").read_bytes(), b"historical failure")
            put(state / "bin/pnpm", stage.launcher(state, runtime, manager, runtime_override=True))
            self.assertEqual(stage.prepare(build, resume=True)[3], marker)
            self.assertEqual((state / "bin/pnpm").read_bytes(), stage.launcher(state, runtime, manager))
            put(state / "bin/pnpm", previous + b"# unknown edit\n")
            with self.assertRaisesRegex(ValueError, "launcher changed"):
                stage.prepare(build, resume=True)
            self.assertEqual((state / "bin/pnpm").read_bytes(), previous + b"# unknown edit\n")

    def test_unknown_state_and_changed_runtime_or_config_are_not_overwritten(self):
        build = self.temporary()
        _, runtime, _, lock_hash = fixture(build)
        with mock.patch.object(stage, "LOCK_SHA256", lock_hash), \
                mock.patch.object(stage.subprocess, "run") as checker:
            _, _, state, _ = stage.prepare(build)
            put(state / "config/empty.npmrc", b"unknown credential configuration")
            with self.assertRaises(ValueError):
                stage.prepare(build, resume=True)
            self.assertEqual((state / "config/empty.npmrc").read_bytes(), b"unknown credential configuration")
            put(runtime / "bin/node", b"changed runtime")
            with self.assertRaisesRegex(ValueError, "binding differs"):
                stage.prepare(build, resume=True)
            self.assertEqual(checker.call_count, 1)
        other = self.temporary()
        _, _, _, lock_hash = fixture(other)
        put(other / "signal-dependencies/unknown", b"user-owned data")
        with mock.patch.object(stage, "LOCK_SHA256", lock_hash), \
                mock.patch.object(stage.subprocess, "run") as checker, self.assertRaises(ValueError):
            stage.prepare(other)
        checker.assert_not_called()
        self.assertEqual((other / "signal-dependencies/unknown").read_bytes(), b"user-owned data")

    def test_resume_rejects_redirected_dependency_directory_and_changed_lock(self):
        build = self.temporary()
        candidate, _, _, lock_hash = fixture(build)
        with mock.patch.object(stage, "LOCK_SHA256", lock_hash), \
                mock.patch.object(stage.subprocess, "run"):
            stage.prepare(build)
            (candidate / "node_modules").symlink_to(build, target_is_directory=True)
            with self.assertRaises(ValueError):
                stage.prepare(build, resume=True)
            (candidate / "node_modules").unlink()
            put(candidate / "pnpm-lock.yaml", b"changed lock")
            with self.assertRaisesRegex(ValueError, "exact lock"):
                stage.prepare(build, resume=True)

    def test_disk_measurement_counts_hardlinks_once_and_never_follows_symlinks(self):
        build = self.temporary()
        put(build / "source", b"x" * 8192)
        before, largest = stage.measured_bytes([build])
        directory_blocks = build.stat().st_blocks
        os.link(build / "source", build / "same-inode")
        (build / "loop").symlink_to(build, target_is_directory=True)
        after, largest_after = stage.measured_bytes([build, build])
        extra_blocks = (build / "loop").lstat().st_blocks + build.stat().st_blocks - directory_blocks
        self.assertEqual(after, before + extra_blocks * 512)
        self.assertEqual(largest, 8192)
        self.assertEqual(largest_after, 8192)

    def run_local(self, program, timeout=5):
        build = self.temporary()
        log = build / "execution.log"
        result = stage.run_bounded([sys.executable, "-c", program], build,
                                   {"PATH": "/usr/bin:/bin"}, log, [build], timeout)
        return build, log, result

    def test_local_success_is_joined_with_reportable_output(self):
        _, log, result = self.run_local("print('offline fixture only')")
        self.assertEqual(result["result"], "INSTALLED")
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["process_group_joined"])
        self.assertEqual(log.read_text().strip(), "offline fixture only")

    def test_deadline_kills_and_joins_owned_descendant_group(self):
        program = ("import os, subprocess, sys, time; "
                   "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                   "print(os.getpgrp(), flush=True); time.sleep(30)")
        _, log, result = self.run_local(program, timeout=0.5)
        self.assertEqual(result["result"], "TIME_BOUND")
        self.assertTrue(result["process_group_joined"])
        self.assertLess(result["elapsed_seconds"], 9)
        group = int(log.read_text().strip())
        with self.assertRaises(ProcessLookupError):
            os.killpg(group, 0)

    def test_file_limit_stops_write_and_preserves_partial_download(self):
        with mock.patch.object(stage, "FILE_BOUND", 16384):
            build, _, result = self.run_local("open('partial-download', 'wb').write(b'x' * 32768)")
        self.assertEqual(result["result"], "INSTALL_COMMAND_FAILED")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertTrue(result["process_group_joined"])
        self.assertEqual((build / "partial-download").stat().st_size, 16384)

    def test_disk_limit_prevents_any_process_launch(self):
        build = self.temporary()
        with mock.patch.object(stage, "DISK_BOUND", 0), \
                mock.patch.object(stage.subprocess, "Popen") as launch:
            result = stage.run_bounded(["never"], build, {}, build / "log", [build])
        launch.assert_not_called()
        self.assertEqual(result["result"], "DISK_OR_FILE_BOUND")


if __name__ == "__main__":
    (ROOT / "build").mkdir(mode=0o700, exist_ok=True)
    unittest.main()
