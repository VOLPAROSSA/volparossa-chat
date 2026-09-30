#!/usr/bin/env python3
"""Offline extraction/integrity checks, not Signal-native execution evidence."""
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage_pnpm as pnpm


def archive(name="package/bin/pnpm.mjs", kind=tarfile.REGTYPE, repeat=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as packed:
        for _ in range(2 if repeat else 1):
            member = tarfile.TarInfo(name)
            member.type = kind
            member.mode = 0o755
            member.linkname = "/outside"
            member.size = 3 if kind == tarfile.REGTYPE else 0
            packed.addfile(member, io.BytesIO(b"abc") if member.size else None)
    return output.getvalue()


class PnpmStaging(unittest.TestCase):
    def test_integrity_rejects_an_unsigned_substitute(self):
        with self.assertRaisesRegex(ValueError, "integrity"):
            pnpm.verify_integrity(archive())

    def test_bounded_regular_extraction_preserves_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.assertEqual(pnpm.extract_checked(archive(), target), (3, 1))
            output = target / "bin/pnpm.mjs"
            self.assertEqual(output.read_bytes(), b"abc")
            self.assertEqual(output.stat().st_mode & 0o777, 0o755)

    def test_escape_links_and_duplicates_are_rejected(self):
        for data in (archive("package/../escape"), archive("/package/absolute"),
                     archive(kind=tarfile.SYMTYPE), archive(kind=tarfile.LNKTYPE),
                     archive(kind=tarfile.FIFOTYPE), archive(repeat=True)):
            with self.subTest(), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    pnpm.extract_checked(data, Path(directory))

    def test_overwrite_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            pnpm.extract_checked(archive(), target)
            with self.assertRaises(FileExistsError):
                pnpm.extract_checked(archive(), target)


if __name__ == "__main__":
    unittest.main()
