#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Offline synthetic archives only; no native downloads, installation or execution."""

import base64
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage_signal_native as native


def tar_bytes(entries, kind=tarfile.REGTYPE):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        for name, content in entries:
            member = tarfile.TarInfo(name)
            member.type, member.mode, member.linkname = kind, 0o755, "/outside"
            member.size = len(content) if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(content) if member.size else None)
    return data.getvalue()


def zip_bytes(entries, mode=stat.S_IFREG | 0o755):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name, content in entries:
            member = zipfile.ZipInfo(name)
            member.create_system, member.external_attr = 3, mode << 16
            archive.writestr(member, content)
    return data.getvalue()


class NativeStaging(unittest.TestCase):
    def inspect(self, data, kind):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive"
            path.write_bytes(data)
            return native.inspect_archive(path, kind)

    def test_regular_archives_preserve_notices_without_extraction(self):
        entries = [("electron", b"not executable test data"), ("LICENSE", b"fixture license")]
        for kind, data in (("zip", zip_bytes(entries)), ("tar.gz", tar_bytes(entries))):
            result = self.inspect(data, kind)
            self.assertEqual(result, dict(members=2, expanded_bytes=sum(len(row[1]) for row in entries),
                retained_license_members=["LICENSE"]))

    def test_paths_links_and_duplicate_members_are_rejected(self):
        for name in ("../escape", "/absolute", "a/../escape", "a\\escape", "C:escape", "a//b"):
            for kind, data in (("zip", zip_bytes([(name, b"x")])), ("tar.gz", tar_bytes([(name, b"x")]))):
                with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                    self.inspect(data, kind)
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            with self.assertRaises(ValueError):
                self.inspect(tar_bytes([("link", b"")], kind), "tar.gz")
        with self.assertRaises(ValueError):
            self.inspect(zip_bytes([("link", b"/outside")], stat.S_IFLNK | 0o777), "zip")
        for entries in ([("a", b"x"), ("a", b"x")], [("a/b", b"x"), ("a", b"x")],
                        [("a", b"x"), ("a/b", b"x")]):
            with self.assertRaises(ValueError):
                self.inspect(tar_bytes(entries), "tar.gz")

    def test_count_and_expanded_size_bounds_apply_before_payload_read(self):
        data = tar_bytes([("a", b"12"), ("b", b"34")])
        with patch.object(native, "MAX_EXPANDED", 1), self.assertRaises(ValueError):
            self.inspect(data, "tar.gz")
        with patch.object(native, "MAX_MEMBERS", 1), self.assertRaises(ValueError):
            self.inspect(data, "tar.gz")

    def test_lock_and_package_integrity_reject_substitutes(self):
        with self.assertRaises(ValueError):
            native.verify_lock(b"changed lock", native.PINS["electron"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "substitute.tgz"
            path.write_bytes(tar_bytes([("package/package.json", b"{}")]))
            with self.assertRaises(ValueError):
                native.verify_package(path, native.PINS["electron"])

    def test_download_rejects_unadvertised_oversize_and_size_mismatch(self):
        class Response(io.BytesIO):
            headers = {}
        for data, bound, expected in ((b"oversized", 4, None), (b"short", 8, 6)):
            with tempfile.TemporaryDirectory() as directory, patch.object(native.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = Response(data)
                with self.assertRaises(ValueError):
                    native.download("https://fixture.invalid/data", Path(directory) / "archive", bound, expected)
        with self.assertRaises(ValueError):
            native.HttpsRedirects().redirect_request(None, None, 302, "", {}, "http://fixture.invalid/data")

    def test_complete_staging_keeps_only_verified_archives_and_refuses_overwrite(self):
        pin = dict(native.PINS["electron"])
        binary = zip_bytes([("electron", b"inert fixture"), ("LICENSE", b"fixture license")])
        pin.update(bytes=len(binary), sha256=hashlib.sha256(binary).hexdigest())
        package = tar_bytes([
            ("package/package.json", json.dumps(dict(name=pin["package"], version=pin["version"], license=pin["license"])).encode()),
            ("package/checksums.json", json.dumps({pin["filename"]: pin["sha256"]}).encode()),
            ("package/LICENSE", b"fixture MIT notice"),
        ])
        pin.update(npm_bytes=len(package), integrity="sha512-" + base64.b64encode(hashlib.sha512(package).digest()).decode())
        lock = (f"  electron@44.1.0:\n    resolution: {{integrity: {pin['integrity']}, tarball: {pin['npm_url']}}}\n").encode()
        def download(url, path, maximum, expected):
            data = package if url == pin["npm_url"] else binary
            self.assertLessEqual(len(data), maximum)
            self.assertEqual(len(data), expected)
            path.write_bytes(data)
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            source = build / f"signal-desktop-{native.COMMIT}"
            source.mkdir()
            (source / "pnpm-lock.yaml").write_bytes(lock)
            with patch.object(native, "PINS", {"electron": pin}), \
                 patch.object(native, "LOCK_SHA256", hashlib.sha256(lock).hexdigest()), \
                 patch.object(native, "download", side_effect=download) as fetch:
                report = native.stage(build, "electron")
                self.assertEqual(fetch.call_count, 2)
                self.assertTrue(report["checksum_bound_to_verified_npm_package"])
                self.assertFalse(report["extraction_performed"])
                self.assertFalse(report["native_code_executed"])
                target = build / "native-inputs" / pin["target"]
                self.assertEqual({item.name for item in target.iterdir()},
                    {"electron-44.1.0.tgz", pin["filename"], "provenance.json"})
                self.assertEqual((target / pin["filename"]).read_bytes(), binary)
                with self.assertRaises(ValueError):
                    native.stage(build, "electron")
                self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
