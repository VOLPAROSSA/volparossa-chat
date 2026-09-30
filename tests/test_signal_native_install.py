#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic native archives only; no network, installers or native execution."""

import base64
import hashlib
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import install_signal_native as install
import stage_signal_native as native
from test_signal_native_staging import tar_bytes, zip_bytes


class NativeInstall(unittest.TestCase):
    def fixture(self, build, artifact):
        pin = dict(native.PINS[artifact])
        if artifact == "electron":
            entries = [("electron", b"inert fixture"), ("version", pin["version"].encode()),
                       ("LICENSE", b"MIT fixture"), ("chrome-sandbox", b"inert sandbox")]
            binary = zip_bytes(entries)
        else:
            entries = [(f"build/{platform}/libringrtc-{arch}.node", b"inert fixture")
                       for platform in ("linux", "darwin", "win32") for arch in ("x64", "arm64")]
            binary = tar_bytes(entries)
        pin.update(bytes=len(binary), sha256=hashlib.sha256(binary).hexdigest())
        metadata = dict(name=pin["package"], version=pin["version"], license=pin["license"])
        if artifact == "ringrtc":
            metadata["config"] = dict(prebuildChecksum=pin["sha256"], prebuildUrl=pin["url"])
        npm_files = [("package.json", json.dumps(metadata).encode()), ("LICENSE", b"npm license fixture")]
        if artifact == "electron":
            npm_files.append(("checksums.json", json.dumps({pin["filename"]: pin["sha256"]}).encode()))
        else:
            npm_files.append(("scripts/fetch-prebuild.js", b"// inert installer fixture"))
        package_bytes = tar_bytes([("package/" + name, data) for name, data in npm_files])
        pin.update(npm_bytes=len(package_bytes),
                   integrity="sha512-" + base64.b64encode(hashlib.sha512(package_bytes).digest()).decode())
        lock = (f"  '{pin['package']}@{pin['version']}':\n    resolution: "
                f"{{integrity: {pin['integrity']}, tarball: {pin['npm_url']}}}\n").encode()
        source = build / f"signal-desktop-{native.COMMIT}"
        source.mkdir()
        (source / "pnpm-lock.yaml").write_bytes(lock)
        candidate = build / "signal-backup-candidate"
        candidate.mkdir()
        (candidate / "pnpm-lock.yaml").write_bytes(lock)
        modules = candidate / "node_modules"
        real = modules / ".pnpm" / "fixture" / "node_modules" / pin["package"]
        real.mkdir(parents=True)
        for name, data in npm_files:
            target = real / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        linked = modules / pin["package"]
        linked.parent.mkdir(parents=True, exist_ok=True)
        linked.symlink_to(real, target_is_directory=True)
        pins = patch.object(native, "PINS", {artifact: pin})
        lock_pin = patch.object(native, "LOCK_SHA256", hashlib.sha256(lock).hexdigest())
        self.addCleanup(pins.stop)
        self.addCleanup(lock_pin.stop)
        pins.start()
        lock_pin.start()
        def fake_download(url, path, *_):
            path.write_bytes(package_bytes if url == pin["npm_url"] else binary)
        with patch.object(native, "download", side_effect=fake_download):
            native.stage(build, artifact)
        return pin, real, linked

    def test_exact_electron_layout_preserves_sources_and_no_setuid(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            pin, package, _ = self.fixture(build, "electron")
            before = {p.name: p.read_bytes() for p in package.iterdir()}
            with patch.object(native, "download", side_effect=AssertionError("network forbidden")):
                result = install.materialize(build, "electron")
            self.assertEqual((package / "path.txt").read_bytes(), b"electron")
            self.assertEqual((package / "dist" / "version").read_text(), pin["version"])
            self.assertEqual(stat.S_IMODE((package / "dist" / "chrome-sandbox").stat().st_mode), 0o755)
            for name, content in before.items():
                self.assertEqual((package / name).read_bytes(), content)
            self.assertFalse(result["native_code_executed"])
            self.assertFalse(result["network_access"])
            with self.assertRaisesRegex(ValueError, "refusing overwrite"):
                install.materialize(build, "electron")

    def test_exact_ringrtc_layout_and_offline_installer_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            pin, package, _ = self.fixture(build, "ringrtc")
            result = install.materialize(build, "ringrtc")
            self.assertEqual((package / "build/linux/libringrtc-x64.node").read_bytes(), b"inert fixture")
            self.assertEqual(len(list((package / "build").rglob("*.node"))), 6)
            self.assertEqual(native.digest(package / "scripts/prebuild.tar.gz").hex(), pin["sha256"])
            self.assertTrue(result["original_npm_files_verified"])
            self.assertEqual((package / "LICENSE").read_bytes(), b"npm license fixture")

    def test_changed_package_archive_metadata_and_lock_are_rejected(self):
        for target in ("package", "native", "metadata", "lock"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                build = Path(directory)
                pin, package, _ = self.fixture(build, "electron")
                folder = build / "native-inputs" / pin["target"]
                path = {"package": package / "LICENSE", "native": folder / pin["filename"],
                        "metadata": folder / "provenance.json",
                        "lock": build / "signal-backup-candidate/pnpm-lock.yaml"}[target]
                path.write_bytes(b"changed")
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    install.materialize(build, "electron")
                self.assertFalse((package / "dist").exists())

    def test_package_symlink_escape_and_existing_output_are_rejected(self):
        for target in ("escape", "linked_original", "existing_dist", "dangling_path"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                build = Path(directory)
                _, package, linked = self.fixture(build, "electron")
                if target == "escape":
                    linked.unlink()
                    linked.symlink_to(build, target_is_directory=True)
                elif target == "linked_original":
                    original = package / "LICENSE"
                    copy = build / "license"
                    copy.write_bytes(original.read_bytes())
                    original.unlink()
                    original.symlink_to(copy)
                elif target == "existing_dist":
                    (package / "dist").mkdir()
                else:
                    (package / "path.txt").symlink_to(build / "missing")
                with self.assertRaises(ValueError):
                    install.materialize(build, "electron")

    def test_extraction_rejects_traversal_links_privilege_bits_and_bounds(self):
        samples = [("zip", zip_bytes([("../escape", b"x")])),
                   ("zip", zip_bytes([("link", b"/outside")], stat.S_IFLNK | 0o777)),
                   ("zip", zip_bytes([("chrome-sandbox", b"x")], stat.S_IFREG | 0o4755)),
                   ("tar.gz", tar_bytes([("link", b"")], tarfile.LNKTYPE))]
        for kind, data in samples:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "archive"
                archive.write_bytes(data)
                with self.assertRaises(ValueError):
                    install.extract_verified(archive, kind, root / "output")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive"
            archive.write_bytes(zip_bytes([("big", b"12")]))
            with patch.object(native, "MAX_EXPANDED", 1), self.assertRaises(ValueError):
                install.extract_verified(archive, "zip", root / "output")


if __name__ == "__main__":
    unittest.main()
