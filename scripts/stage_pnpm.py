#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicitly stage Signal's pinned pnpm; do not install dependencies or run scripts."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile

from stage_signal_source import COMMIT, ROOT, fetch

VERSION = "11.24.0"
URL = f"https://registry.npmjs.org/pnpm/-/pnpm-{VERSION}.tgz"
INTEGRITY = "sha512-vSfjRel23LC+C3oSKCF7BJqBfiGx81XJDb59xGZxiVqLwebQbCRVRQXqk+oLRfSJon7Bv7yN5qlln8oPFvoAAA=="
LOCK_SHA256 = "bc06486a375791ed118b10f10c33428b0fefa37ce22e6c2cf56c2bf37dd11040"
ARCHIVE_BOUND = 32 * 1024 * 1024
EXPANDED_BOUND = 64 * 1024 * 1024


def verify_integrity(data):
    if len(data) > ARCHIVE_BOUND or "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode() != INTEGRITY:
        raise ValueError("pnpm archive differs from Signal lockfile integrity")


def extract_checked(data, target):
    """Only bounded regular files; verified upstream archive needs no links/devices."""
    seen, total = set(), 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            name = PurePosixPath(member.name)
            if (not member.name.startswith("package/") or str(name) != member.name
                    or ".." in name.parts or name.is_absolute() or len(name.parts) < 2):
                raise ValueError("unsafe pnpm archive path")
            relative = name.relative_to("package")
            if str(relative) in seen or len(seen) >= 2048:
                raise ValueError("duplicate or excessive pnpm members")
            seen.add(str(relative))
            if member.isdir():
                continue
            if not member.isfile() or not 0 <= member.size <= 32 * 1024 * 1024:
                raise ValueError("unsupported pnpm archive entry")
            total += member.size
            if total > EXPANDED_BOUND:
                raise ValueError("pnpm expanded size exceeded")
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, output.open("xb") as destination:
                copied = 0
                while chunk := source.read(65536):
                    copied += len(chunk)
                    if copied > member.size:
                        raise ValueError("pnpm member size changed")
                    destination.write(chunk)
                if copied != member.size:
                    raise ValueError("truncated pnpm member")
            output.chmod(0o755 if member.mode & 0o111 else 0o644)
    return total, len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True,
                        help="download/hash-check pnpm only; no package lifecycle scripts")
    parser.parse_args()
    build = ROOT / "build"
    source = build / f"signal-desktop-{COMMIT}"
    lock = source / "pnpm-lock.yaml"
    target = build / f"pnpm-{VERSION}"
    report_path = build / "pnpm-runtime-report.json"
    if (build.is_symlink() or source.is_symlink() or lock.is_symlink()
            or not lock.is_file() or lock.stat().st_size > 4 * 1024 * 1024):
        raise ValueError("stage the exact Signal source before pnpm")
    if hashlib.sha256(lock.read_bytes()).hexdigest() != LOCK_SHA256:
        raise ValueError("Signal lockfile differs from reviewed source")
    if any(path.exists() or path.is_symlink() for path in (target, report_path)):
        raise ValueError("pnpm target/report exists; refusing overwrite")
    data = fetch(URL, ARCHIVE_BOUND)
    verify_integrity(data)
    with tempfile.TemporaryDirectory(prefix="pnpm-stage-", dir=build) as temporary:
        staged = Path(temporary) / "package"
        staged.mkdir(mode=0o700)
        expanded, members = extract_checked(data, staged)
        package = json.loads((staged / "package.json").read_text())
        if (package.get("name") != "pnpm" or package.get("version") != VERSION
                or package.get("license") != "MIT"
                or package.get("bin", {}).get("pnpm") != "bin/pnpm.mjs"
                or not (staged / "LICENSE").is_file()
                or not (staged / "bin/pnpm.mjs").is_file()):
            raise ValueError("unexpected pinned pnpm package")
        license_hash = hashlib.sha256((staged / "LICENSE").read_bytes()).hexdigest()
        staged.rename(target)
    report = dict(version=VERSION, url=URL, integrity=INTEGRITY,
                  sha256=hashlib.sha256(data).hexdigest(), archive_bytes=len(data),
                  extracted_bytes=expanded, members=members, license="MIT",
                  license_sha256=license_hash, signal_source=COMMIT,
                  lock_sha256=LOCK_SHA256, dependency_installation_performed=False,
                  lifecycle_scripts_executed=False, global_install_or_path_change=False)
    with report_path.open("x") as output:
        output.write(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
