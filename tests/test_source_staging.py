#!/usr/bin/env python3
"""Offline pin/extraction controls; not an Electron build or Signal restore proof."""
import io
import contextlib
import hashlib
import gzip
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import socket
import ssl
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage_signal_source as source
import apply_signal_overlay as overlay


def sample_archive(path, changes=None):
    changes = changes or {}
    files = {"LICENSE": (b"Fixture license\n", 0o644), "a.txt": (b"text\n", 0o644),
             "a/run": (b"#!/bin/sh\nexit 0\n", 0o755)}
    files.update(changes)
    with tarfile.open(path, "w:gz") as archive:
        for name in ("", "a"):
            member = tarfile.TarInfo(source.PREFIX + name)
            member.type, member.mode = tarfile.DIRTYPE, 0o755
            archive.addfile(member)
        for name, (data, mode) in files.items():
            member = tarfile.TarInfo(source.PREFIX + name)
            member.size, member.mode = len(data), mode
            archive.addfile(member, io.BytesIO(data))
    return files


def sample_tree(files):
    # Independent small fixture layout: the file a.txt sorts before directory a/.
    blob = lambda name: bytes.fromhex(source.git_hash("blob", files[name][0]))
    child = source.git_hash("tree", b"100755 run\0" + blob("a/run"))
    return source.git_hash("tree", b"100644 LICENSE\0" + blob("LICENSE")
        + b"100644 a.txt\0" + blob("a.txt") + b"40000 a\0" + bytes.fromhex(child))


class SourcePins(unittest.TestCase):
    def test_source_and_node_pins_match_explicit_scope(self):
        lock = json.loads((ROOT / "upstream-lock.json").read_text())
        self.assertEqual(lock["signal_desktop"]["commit"], source.COMMIT)
        self.assertEqual(len(source.ARCHIVE_SHA256), 64)
        self.assertEqual(len(source.TREE), 40)
        self.assertTrue(all(len(digest) == 64 for digest in overlay.SOURCE_HASHES.values()))
        with self.assertRaises(ValueError):
            source.checked_tree({"sha": source.TREE, "truncated": True, "tree": []})

    def test_exact_blob_extraction_rejects_alteration_links_and_extra_paths(self):
        # Generated local archive fixture only: no upstream downloads in this test.
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            data = b"source bytes"
            files = {"a.txt": {"sha": source.git_hash("blob", data), "size": len(data), "mode": "100644"}}
            for index, variant in enumerate(("valid", "changed", "symlink", "extra")):
                archive = root / f"{index}.tar.gz"
                with tarfile.open(archive, "w:gz") as target:
                    member = tarfile.TarInfo(source.PREFIX + ("../escape" if variant == "extra" else "a.txt"))
                    if variant == "symlink":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/outside"
                        target.addfile(member)
                    else:
                        content = b"changedbytes" if variant == "changed" else data
                        member.size = len(content)
                        target.addfile(member, io.BytesIO(content))
                output = root / f"out-{index}"
                output.mkdir()
                if variant == "valid":
                    source.extract_checked(archive, output, files)
                    self.assertEqual((output / "a.txt").read_bytes(), data)
                else:
                    with self.assertRaises(ValueError):
                        source.extract_checked(archive, output, files)

    def test_patch_refuses_ambiguous_or_missing_context(self):
        self.assertEqual(overlay.replace_once("aXb", "X", "Y"), "aYb")
        for text in ("ab", "aXXb"):
            with self.assertRaises(ValueError):
                overlay.replace_once(text, "X", "Y")

    def test_archive_reconstructs_exact_git_modes_paths_and_nested_trees(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            path = Path(temporary) / "source.tar.gz"
            files = sample_archive(path)
            with mock.patch.object(source, "TREE", sample_tree(files)):
                document = source.archive_tree(path)
                verified = source.checked_tree(document)
                self.assertEqual(set(verified), set(files))
                self.assertEqual(verified["a/run"]["mode"], "100755")
                self.assertEqual(verified["a.txt"]["mode"], "100644")
                for mutation in ({"a.txt": (b"tampered\n", 0o644)},
                                 {"a/run": (files["a/run"][0], 0o644)},
                                 {"extra": (b"extra", 0o644)}):
                    sample_archive(path, mutation)
                    with self.assertRaises(ValueError):
                        source.archive_tree(path)
                for mutate in (lambda d: d["tree"][0].update(path="../private"),
                               lambda d: d["tree"][0].update(sha="0" * 40),
                               lambda d: d["tree"].pop()):
                    changed = json.loads(json.dumps(document))
                    mutate(changed)
                    with self.assertRaises(ValueError):
                        source.checked_tree(changed)

    def test_archive_rejects_aliases_links_types_and_resource_bounds(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            path = Path(temporary) / "source.tar.gz"
            for variant in ("outside", "traversal", "duplicate", "symlink", "hardlink", "device",
                            "empty-directory", "file-directory-collision", "large", "privileged-mode"):
                with self.subTest(variant=variant):
                    if variant == "large":
                        member = tarfile.TarInfo(source.PREFIX + "large")
                        member.size = 64 * 1024**2 + 1
                        with gzip.open(path, "wb") as raw:
                            raw.write(member.tobuf() + b"\0" * 1024)
                        with self.assertRaisesRegex(ValueError, "upstream file size"):
                            source.archive_tree(path)
                        continue
                    with tarfile.open(path, "w:gz") as archive:
                        name = "outside" if variant == "outside" else source.PREFIX + (
                            "../outside" if variant == "traversal" else "a")
                        member = tarfile.TarInfo(name)
                        if variant in ("symlink", "hardlink"):
                            member.type = tarfile.SYMTYPE if variant == "symlink" else tarfile.LNKTYPE
                            member.linkname = "/PRIVATE"
                        elif variant == "device":
                            member.type = tarfile.CHRTYPE
                        elif variant == "empty-directory":
                            member.type = tarfile.DIRTYPE
                        elif variant == "privileged-mode":
                            member.mode = 0o4755
                        archive.addfile(member)
                        if variant == "duplicate":
                            archive.addfile(member)
                        elif variant == "file-directory-collision":
                            archive.addfile(tarfile.TarInfo(source.PREFIX + "a/file"))
                    with self.assertRaises(ValueError):
                        source.archive_tree(path)

    def test_offline_metadata_check_never_fetches_and_rejects_mode_changes(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            build = root / "build"
            build.mkdir()
            archive = root / "fixture.tar.gz"
            files = sample_archive(archive)
            checkout = build / f"signal-desktop-{source.COMMIT}"
            checkout.mkdir()
            (root / "upstream-lock.json").write_text(json.dumps({"signal_desktop": {"commit": source.COMMIT}}))
            with mock.patch.object(source, "ROOT", root), mock.patch.object(source, "TREE", sample_tree(files)), \
                    mock.patch.object(source, "fetch", side_effect=AssertionError("offline lookup fetched")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                document = source.archive_tree(archive)
                (build / "signal-source-tree.json").write_text(json.dumps(document))
                source.extract_checked(archive, checkout, source.checked_tree(document))
                self.assertEqual(source.main(["--verify-existing"]), 0)
                (checkout / "a/run").chmod(0o644)
                self.assertEqual(source.main(["--verify-existing"]), 1)
                error = json.loads((build / "signal-source-error.json").read_bytes())
                self.assertEqual(error["phase"], "existing_verification")
                self.assertEqual(error["category"], "validation")

    def test_download_fetches_only_exact_archive_and_checks_archive_and_tree_pins(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            archive = root / "fixture.tar.gz"
            files = sample_archive(archive)
            data = archive.read_bytes()
            (root / "upstream-lock.json").write_text(json.dumps({"signal_desktop": {
                "commit": source.COMMIT, "license_sha256": hashlib.sha256(files["LICENSE"][0]).hexdigest()}}))
            with mock.patch.object(source, "ROOT", root), mock.patch.object(source, "TREE", sample_tree(files)), \
                    mock.patch.object(source, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest()), \
                    mock.patch.object(source, "fetch", return_value=data) as fetched, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(source.main(["--download"]), 0)
                fetched.assert_called_once_with(source.ARCHIVE, 128 * 1024**2)
                report = json.loads((root / "build/signal-source-report.json").read_bytes())
                self.assertTrue(report["all_git_blob_and_tree_hashes_verified"])
                self.assertEqual(report["verified_files"], 3)

    def test_wrong_archive_or_root_tree_pin_never_reaches_source_extraction(self):
        for variant in ("archive_hash", "root_tree"):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
                root = Path(temporary)
                archive = root / "fixture.tar.gz"
                files = sample_archive(archive)
                data = archive.read_bytes()
                (root / "upstream-lock.json").write_text(json.dumps({"signal_desktop": {"commit": source.COMMIT}}))
                with mock.patch.object(source, "ROOT", root), \
                        mock.patch.object(source, "TREE", "0" * 40 if variant == "root_tree" else sample_tree(files)), \
                        mock.patch.object(source, "ARCHIVE_SHA256", hashlib.sha256(data).hexdigest()), \
                        mock.patch.object(source, "fetch", return_value=data + (b"changed" if variant == "archive_hash" else b"")), \
                        mock.patch.object(source, "extract_checked") as extract, \
                        contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(source.main(["--download"]), 1)
                    extract.assert_not_called()
                    error = json.loads((root / "build/signal-source-error.json").read_bytes())
                    self.assertEqual(error["phase"], "archive_verification")
                    self.assertEqual(error["category"], "validation")
                    self.assertFalse((root / "build" / f"signal-desktop-{source.COMMIT}").exists())

    @unittest.skipUnless(os.environ.get("SIGNAL_CHECKOUT_FIXTURE"), "optional existing pinned checkout; never downloaded")
    def test_existing_real_checkout_reconstructs_the_same_pinned_git_tree(self):
        checkout = Path(os.environ["SIGNAL_CHECKOUT_FIXTURE"])
        self.assertFalse(checkout.is_symlink())
        document = json.loads((checkout.parent / "signal-source-tree.json").read_bytes())
        files = source.checked_tree(document)
        source.verify_checkout(checkout, files)
        self.assertEqual(len(files), 4570)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            archive = Path(temporary) / "checkout-fixture.tar.gz"
            # A local test archive, not the original codeload archive or its hash proof.
            with tarfile.open(archive, "w:gz", compresslevel=1) as output:
                output.add(checkout, arcname=source.PREFIX[:-1], recursive=False)
                for path in sorted(checkout.rglob("*")):
                    output.add(path, arcname=source.PREFIX + path.relative_to(checkout).as_posix(), recursive=False)
            rebuilt = source.archive_tree(archive)
            self.assertEqual(rebuilt["sha"], source.TREE)
            canonical = lambda item: {key: item[key] for key in ("path", "type", "mode", "size", "sha")}
            self.assertEqual({name: canonical(item) for name, item in source.checked_tree(rebuilt).items()},
                             {name: canonical(item) for name, item in files.items()})

    def test_failure_diagnostics_are_closed_and_preserve_http_status(self):
        errors = (
            (urllib.error.HTTPError("PRIVATE_URL", 429, "PRIVATE_BODY", {}, None), "http", "http_error", 429),
            (urllib.error.URLError(ssl.SSLError("PRIVATE_TLS")), "tls", "tls_error", None),
            (urllib.error.URLError(TimeoutError("PRIVATE_TIMEOUT")), "timeout", "timeout_error", None),
            (urllib.error.URLError(socket.gaierror("PRIVATE_DNS")), "network", "dns_error", None),
            (urllib.error.URLError("PRIVATE_URL"), "network", "url_error", None),
            (ValueError("PRIVATE_FILE"), "validation", "invalid_source", None),
            (PermissionError("PRIVATE_PATH"), "io", "os_error", None),
            (RuntimeError("PRIVATE_UNEXPECTED"), "unknown", "unknown_error", None),
        )
        for error, category, kind, status in errors:
            value = source.failure_diagnostic(error, "archive_fetch")
            self.assertEqual(value, dict(version=1, kind="signal-source-staging-failure",
                phase="archive_fetch", category=category, error_type=kind, http_status=status))
            self.assertNotIn("PRIVATE", json.dumps(value))
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            root = Path(temporary)
            (root / "upstream-lock.json").write_text(json.dumps({"signal_desktop": {"commit": source.COMMIT}}))
            with mock.patch.object(source, "ROOT", root), mock.patch.object(source, "fetch", side_effect=errors[0][0]), \
                    contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(source.main(["--download"]), 1)
                path = root / "build/signal-source-error.json"
                original = path.read_bytes()
                self.assertEqual(json.loads(original)["http_status"], 429)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("PRIVATE", stderr.getvalue())
                self.assertEqual(source.main(["--download"]), 1)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(set(p.name for p in (root / "build").iterdir()), {"signal-source-error.json"})


if __name__ == "__main__":
    (ROOT / "build").mkdir(mode=0o700, exist_ok=True)
    unittest.main()
