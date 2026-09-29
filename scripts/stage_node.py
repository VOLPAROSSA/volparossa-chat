#!/usr/bin/env python3
"""Explicit, hash-pinned Node runtime for this workspace's tests; no npm install."""

import hashlib
import json
from pathlib import Path
import tarfile
import tempfile

from stage_signal_source import ROOT, fetch

VERSION = "24.19.0"
NAME = f"node-v{VERSION}-linux-x64"
SHA256 = "14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647"
BASE = f"https://nodejs.org/dist/v{VERSION}/"


def main():
    build = ROOT / "build"
    if build.is_symlink():
        raise ValueError("build symlink refused")
    build.mkdir(mode=0o700, exist_ok=True)
    target = build / NAME
    if target.exists() or target.is_symlink():
        raise ValueError("runtime target exists; refusing overwrite")
    sums = fetch(BASE + "SHASUMS256.txt", 64 * 1024)
    expected = f"{SHA256}  {NAME}.tar.xz".encode()
    if expected not in sums.splitlines():
        raise ValueError("official HTTPS checksum disagrees with reviewed pin")
    data = fetch(BASE + NAME + ".tar.xz", 64 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("Node artifact hash mismatch")
    with tempfile.TemporaryFile() as temporary:
        temporary.write(data)
        temporary.seek(0)
        with tarfile.open(fileobj=temporary, mode="r:xz") as archive:
            checked = {}
            for name in ("bin/node", "LICENSE"):
                member = archive.getmember(f"{NAME}/{name}")
                if not member.isfile() or not 0 < member.size < 160 * 1024 * 1024:
                    raise ValueError("Node archive member invalid")
                checked[name] = archive.extractfile(member).read(member.size + 1)
            target.mkdir(mode=0o700)
            for name, content in checked.items():
                output = target / name
                output.parent.mkdir(exist_ok=True)
                with output.open("xb") as stream:
                    stream.write(content)
                output.chmod(0o755 if name == "bin/node" else 0o644)
    report = {"version": VERSION, "url": BASE + NAME + ".tar.xz", "sha256": SHA256,
              "archive_bytes": len(data), "extracted_bytes": sum(map(len, checked.values())),
              "authentication": "official nodejs.org HTTPS SHASUMS256 plus reviewed exact hash",
              "release_pgp_signature_independently_verified": False,
              "global_install_or_path_change": False}
    (build / "node-runtime-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
