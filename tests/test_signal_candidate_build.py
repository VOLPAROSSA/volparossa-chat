#!/usr/bin/env python3
"""Offline compile-wrapper tests: no build, native app or network is executed."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import build_signal_candidate as build
import stage_signal_dependencies as deps
from test_signal_dependency_staging import fixture, put


class SignalCandidateBuild(unittest.TestCase):
    def test_only_reviewed_compile_steps_no_generic_generate_or_native_execution(self):
        self.assertEqual([name for name, *_ in build.STEPS], ["types", "windows-ucv", "mock-server", "app-assets"])
        self.assertEqual(build.STEPS[-1][1], ("exec", "run-s", "--print-label", *build.ASSETS))
        for _, arguments, timeout, outputs in build.STEPS:
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 1200)
            self.assertTrue(outputs)
            for forbidden in ("install", "rebuild", "postinstall", "generate", "build:preload-cache", "start"):
                self.assertNotIn(forbidden, arguments)

    def test_sandbox_has_only_candidate_and_owned_state_writable_without_home_override(self):
        candidate = Path("/owned/build/candidate")
        state = Path("/owned/build/state")
        command = build.sandbox_command(candidate, state, ("run", "build"))
        self.assertIn("--unshare-net", command)
        self.assertIn("--unshare-pid", command)
        self.assertIn("--die-with-parent", command)
        i = command.index("--ro-bind")
        self.assertEqual(command[i:i + 3], ["--ro-bind", "/", "/"])
        mounts = [command[i + 1:i + 3] for i, value in enumerate(command) if value == "--bind"]
        self.assertEqual(mounts, [[str(candidate)] * 2, [str(state)] * 2])
        with mock.patch.dict(os.environ, HOME="/unchanged"):
            env = build.build_environment(state, Path("/owned/build/node"))
        self.assertEqual(env["HOME"], "/unchanged")
        self.assertEqual(env["SOURCE_DATE_EPOCH"], "1790198897")
        self.assertEqual(env["SKIP_VERIFY_DEPS_BEFORE_RUN"], "1")
        self.assertEqual(env["pnpm_config_offline"], "true")
        self.assertEqual(env["NPM_CONFIG_IGNORE_SCRIPTS"], "true")
        self.assertEqual(env["pnpm_config_runtime"], "false")
        self.assertEqual(env["npm_execpath"], str(state / "bin/pnpm"))
        self.assertNotIn("CI", env)
        asset_command = build.sandbox_command(candidate, state, build.STEPS[-1][1])
        index = asset_command.index("run-s")
        self.assertEqual(asset_command[index:index + 4],
                         ["run-s", "--npm-path", str(state / "bin/pnpm"), "--print-label"])

    def test_requires_successful_exact_dependency_receipt_and_preserves_source_on_resume(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as directory:
            root = Path(directory)
            candidate, _, _, lock_hash = fixture(root)
            with mock.patch.object(deps, "LOCK_SHA256", lock_hash), mock.patch.object(deps.subprocess, "run"):
                _, _, dependency_state, _ = deps.prepare(root)
                (candidate / "node_modules").mkdir()
                with self.assertRaisesRegex(ValueError, "successful locked dependency"):
                    build.prepare(root)
                receipt = dict(dependency_installation_succeeded=False, source_and_lock_unchanged=True,
                               signal_source=deps.COMMIT, lock_sha256=lock_hash)
                put(dependency_state / "attempt-1.json", json.dumps(receipt).encode())
                with self.assertRaisesRegex(ValueError, "latest dependency"):
                    build.prepare(root)
                receipt["dependency_installation_succeeded"] = True
                put(dependency_state / "attempt-1.json", json.dumps(receipt).encode())
                _, _, state, marker = build.prepare(root)
                put(candidate / "bundles/fixture.js", b"generated fixture")
                self.assertEqual(build.prepare(root, resume=True)[3], marker)
                with self.assertRaises(ValueError):
                    build.prepare(root)
                put(candidate / "package.json", b"unrelated user edit")
                with self.assertRaisesRegex(ValueError, "source changed"):
                    build.prepare(root, resume=True)
                self.assertEqual((candidate / "package.json").read_bytes(), b"unrelated user edit")
                self.assertEqual((candidate / "bundles/fixture.js").read_bytes(), b"generated fixture")
                self.assertTrue((state / "owner.json").is_file())


if __name__ == "__main__":
    unittest.main()
