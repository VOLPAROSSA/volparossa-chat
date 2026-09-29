#!/usr/bin/env python3
"""Offline pin/extraction controls; not an Electron build or Signal restore proof."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import stage_signal_source as source
import apply_signal_overlay as overlay


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


if __name__ == "__main__":
    (ROOT / "build").mkdir(mode=0o700, exist_ok=True)
    unittest.main()
