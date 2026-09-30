#!/usr/bin/env python3
"""Offline synthetic archive/launcher checks; no download, install or native Signal proof."""

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage_node as node


def fixture(variant=None):
    files = {
        "bin/node": b'#!/bin/sh\nprintf "%s\\n" "$0" "$@"\ncommand -v node\n',
        "LICENSE": b"synthetic Node license\n",
        f"{node.NPM}/package.json": b'{"name":"npm","version":"11.6.0"}',
        f"{node.NPM}/LICENSE": b"synthetic npm license\n",
        f"{node.NPM}/bin/npm-cli.js": b"synthetic npm CLI\n",
        f"{node.NPM}/bin/npx-cli.js": b"synthetic npx CLI\n",
        f"{node.NPM}/node_modules/test/index.js": b"synthetic bundled package\n",
    }
    links = {
        "bin/npm": f"../{node.NPM}/bin/npm-cli.js",
        "bin/npx": f"../{node.NPM}/bin/npx-cli.js",
        f"{node.NPM}/node_modules/.bin/test": "../test/index.js",
    }
    if variant == "entrypoint":
        links["bin/npm"] = "/outside"
    elif variant == "escape":
        links[f"{node.NPM}/node_modules/.bin/test"] = "../../../../../../outside"
    elif variant == "cycle":
        links[f"{node.NPM}/node_modules/.bin/test"] = "test"
    elif variant == "ancestor":
        files[f"{node.NPM}/node_modules/.bin/test/child"] = b"unreachable"
    elif variant == "missing":
        del files[f"{node.NPM}/bin/npm-cli.js"]
    elif variant == "npm-identity":
        files[f"{node.NPM}/package.json"] = b'{"name":"not-npm","version":"11.6.0"}'
    elif variant == "traversal":
        files["../outside"] = b"outside"
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:xz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(f"{node.NAME}/{name}")
            member.mode = 0o755 if name == "bin/node" else 0o644
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
            if variant == "duplicate" and name == "LICENSE":
                archive.addfile(member, io.BytesIO(content))
        for name, target in links.items():
            member = tarfile.TarInfo(f"{node.NAME}/{name}")
            member.type = tarfile.SYMTYPE
            member.linkname = target
            archive.addfile(member)
    return result.getvalue()


def checked(data, with_npm=True):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as archive:
        return node.checked_members(archive, with_npm)


class NodeStaging(unittest.TestCase):
    def test_selects_original_node_and_complete_bounded_npm_subtree(self):
        files, links, version = checked(fixture())
        self.assertEqual(version, "11.6.0")
        self.assertEqual(len(files), 7)
        self.assertEqual(len(links), 1)
        self.assertEqual(files[f"{node.NPM}/LICENSE"][0], b"synthetic npm license\n")
        self.assertNotIn("bin/npm", files)
        minimal, links, version = checked(fixture(), False)
        self.assertEqual(set(minimal), {"bin/node", "LICENSE"})
        self.assertEqual(links, {})
        self.assertIsNone(version)

    def test_rejects_unsafe_incomplete_or_ambiguous_archive(self):
        for variant in ("entrypoint", "escape", "cycle", "ancestor", "missing",
                        "npm-identity", "traversal", "duplicate"):
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                checked(fixture(variant))
        for field in ("MAX_MEMBERS", "MAX_ARCHIVE_BYTES", "MAX_SELECTED_BYTES"):
            with self.subTest(field=field), mock.patch.object(node, field, 1), self.assertRaises(ValueError):
                checked(fixture())

    def test_separate_npm_target_preserves_old_runtime_and_launches_only_its_node(self):
        data = fixture()
        sha = hashlib.sha256(data).hexdigest()
        sums = f"{sha}  {node.NAME}.tar.xz\n".encode()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            build = Path(temporary)
            existing = build / node.NAME
            existing.mkdir()
            (existing / "unknown-user-file").write_bytes(b"untouched")
            (build / "node-runtime-report.json").write_bytes(b"old report")
            with mock.patch.object(node, "SHA256", sha), \
                    mock.patch.object(node, "fetch", side_effect=[sums, data]) as fetch:
                report = node.stage(build, with_npm=True)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual((existing / "unknown-user-file").read_bytes(), b"untouched")
            self.assertEqual((build / "node-runtime-report.json").read_bytes(), b"old report")
            runtime = build / (node.NAME + "-with-npm")
            self.assertEqual(report["npm_version"], "11.6.0")
            self.assertFalse(report["npm_dependency_installation_performed"])
            self.assertFalse(report["global_install_or_path_change"])
            self.assertTrue(report["launchers_use_pinned_node"])
            self.assertEqual((runtime / f"{node.NPM}/node_modules/.bin/test").read_bytes(),
                             b"synthetic bundled package\n")
            other = build / "other"
            other.mkdir()
            (other / "node").write_text("#!/bin/sh\nexit 99\n")
            (other / "node").chmod(0o755)
            for command in ("npm", "npx"):
                result = subprocess.run([str(runtime / "bin" / command), "--version"],
                    env={**os.environ, "PATH": f"{other}:/usr/bin:/bin"},
                    capture_output=True, text=True, timeout=5, check=True)
                self.assertEqual(result.stdout.splitlines(), [
                    str(runtime / "bin/node"),
                    str(runtime / "bin" / f"../{node.NPM}/bin/{command}-cli.js"),
                    "--version", str(runtime / "bin/node")])
            self.assertEqual(json.loads((build / "node-npm-runtime-report.json").read_bytes()), report)
            with mock.patch.object(node, "fetch") as fetch, self.assertRaises(ValueError):
                node.stage(build, with_npm=True)
            fetch.assert_not_called()

    def test_authentication_and_existing_path_refusals_precede_publication(self):
        data = fixture()
        sha = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            build = Path(temporary)
            with mock.patch.object(node, "SHA256", sha), \
                    mock.patch.object(node, "fetch", return_value=b"wrong checksum"), self.assertRaises(ValueError):
                node.stage(build, True)
            with mock.patch.object(node, "SHA256", sha), \
                    mock.patch.object(node, "fetch", side_effect=[
                        f"{sha}  {node.NAME}.tar.xz\n".encode(), b"wrong archive"]), self.assertRaises(ValueError):
                node.stage(build, True)
            self.assertEqual(list(build.iterdir()), [])
            target = build / (node.NAME + "-with-npm")
            target.symlink_to(build / "missing")
            with mock.patch.object(node, "fetch") as fetch, self.assertRaises(ValueError):
                node.stage(build, True)
            fetch.assert_not_called()
            self.assertTrue(target.is_symlink())


if __name__ == "__main__":
    (ROOT / "build").mkdir(mode=0o700, exist_ok=True)
    unittest.main()
