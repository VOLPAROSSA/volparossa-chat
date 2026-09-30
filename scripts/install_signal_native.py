#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Materialize reviewed native archives offline; never run a lifecycle script or binary.

The only destination is the existing Signal candidate's node_modules. This
reproduces Electron's dist/path.txt and RingRTC's build/prebuild-cache layouts,
without invoking their download-capable installers. Existing outputs are never
replaced. Original npm files and third-party notices remain unchanged.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import zipfile

import stage_signal_native as native


def regular(path, maximum):
    native.require(path.is_file() and not path.is_symlink() and path.resolve() == path
                   and 0 < path.stat().st_size <= maximum, "missing, linked or oversized input file")


def verified_inputs(build, artifact):
    pin = native.PINS[artifact]
    native.require(build.is_dir() and not build.is_symlink() and build.resolve() == build,
                   "invalid workspace build directory")
    candidate = build / "signal-backup-candidate"
    native.require(candidate.is_dir() and candidate.resolve() == candidate,
                   "stage the Signal candidate first")
    lock = candidate / "pnpm-lock.yaml"
    regular(lock, 4 * 1024 * 1024)
    native.verify_lock(lock.read_bytes(), pin)
    folder = build / "native-inputs" / pin["target"]
    package_archive = folder / (pin["package"].split("/")[-1] + "-" + pin["version"] + ".tgz")
    archive = folder / pin["filename"]
    provenance = folder / "provenance.json"
    regular(package_archive, native.MAX_DOWNLOAD)
    regular(archive, native.MAX_DOWNLOAD)
    regular(provenance, 16384)
    package_details = native.verify_package(package_archive, pin)
    native.require(native.digest(archive).hex() == pin["sha256"]
                   and (pin["bytes"] is None or archive.stat().st_size == pin["bytes"]),
                   "native archive differs from reviewed pin")
    native_details = native.inspect_archive(archive, pin["format"])
    expected = dict(schema_version=1, signal_source=native.COMMIT, lock_sha256=native.LOCK_SHA256,
        artifact=artifact, version=pin["version"], license=pin["license"],
        package=dict(file=package_archive.name, url=pin["npm_url"], integrity=pin["integrity"],
            sha256=native.digest(package_archive).hex(), bytes=package_archive.stat().st_size,
            **package_details),
        native=dict(file=archive.name, url=pin["url"], sha256=pin["sha256"],
            bytes=archive.stat().st_size, **native_details),
        checksum_bound_to_verified_npm_package=True, original_archives_and_notices_retained=True,
        extraction_performed=False, lifecycle_scripts_executed=False, native_code_executed=False,
        host_install_or_permissions_changed=False, source_build_proven=False)
    native.require(json.loads(provenance.read_bytes()) == expected,
                   "staged native provenance differs from verified inputs")
    modules = candidate / "node_modules"
    native.require(modules.is_dir() and modules.resolve() == modules,
                   "install exact npm dependencies without scripts first")
    # pnpm's public package links are expected; their resolved targets must
    # remain within this candidate, never a global/shared package directory.
    package = (modules / pin["package"]).resolve(strict=True)
    native.require(package.is_relative_to(modules) and package != modules and package.is_dir(),
                   "native package escapes candidate node_modules")
    verify_installed_package(package, package_archive)
    return pin, package, archive, native_details


def verify_installed_package(package, package_archive):
    """Compare every original npm file, including its installer and notices."""
    with tarfile.open(package_archive, "r:gz") as source:
        for member in source:
            native.require(member.name.startswith("package/"), "unexpected npm package prefix")
            relative = PurePosixPath(member.name).relative_to("package")
            target = package / str(relative)
            native.require(target.resolve() == target and not target.is_symlink(),
                           "linked original native npm file")
            if member.isdir():
                native.require(target.is_dir(), "missing original npm directory")
                continue
            native.require(member.isfile() and target.is_file() and target.stat().st_size == member.size,
                           "installed native npm file differs")
            with source.extractfile(member) as stream:
                expected = hashlib.file_digest(stream, "sha256").digest()
            native.require(native.digest(target) == expected, "installed native npm file differs")


def extract_verified(archive, kind, destination):
    """Extract only checked regular members into a fresh, private directory.

    Never use extractall, restore ownership, apply setuid/setgid/sticky bits,
    or create links. Limits are checked again here before writing payloads.
    """
    native.inspect_archive(archive, kind)
    destination.mkdir(mode=0o700)
    count, expanded = 0, 0

    def write(name, size, mode, directory, stream):
        nonlocal count, expanded
        relative = PurePosixPath(name.rstrip("/") if directory else name)
        native.require(str(relative) == name.rstrip("/") and relative.parts
                       and not relative.is_absolute() and ".." not in relative.parts,
                       "unsafe extraction path")
        native.require(not mode & 0o7000, "privileged native archive permissions require review")
        count += 1
        expanded += size
        native.require(count <= native.MAX_MEMBERS and 0 <= size <= native.MAX_EXPANDED
                       and expanded <= native.MAX_EXPANDED, "native extraction exceeds bounds")
        target = destination / str(relative)
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        native.require(target.parent.resolve() == target.parent and not target.is_symlink(),
                       "linked extraction target")
        if directory:
            target.mkdir(mode=0o755, exist_ok=True)
            return
        copied = 0
        with target.open("xb") as output:
            while chunk := stream.read(min(65536, size - copied + 1)):
                copied += len(chunk)
                native.require(copied <= size, "native member exceeds declared size")
                output.write(chunk)
        native.require(copied == size, "truncated native member")
        target.chmod(0o755 if mode & 0o111 else 0o644)

    if kind == "zip":
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                mode = member.external_attr >> 16
                native.require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR),
                               "native ZIP link or special member")
                with source.open(member) as stream:
                    write(member.filename, member.file_size, mode, member.is_dir(), stream)
    else:
        native.require(kind == "tar.gz", "unsupported native archive format")
        with tarfile.open(archive, "r:gz") as source:
            for member in source:
                native.require(member.isfile() or member.isdir(), "native tar link or special member")
                if member.isdir():
                    write(member.name, member.size, member.mode, True, None)
                else:
                    with source.extractfile(member) as stream:
                        write(member.name, member.size, member.mode, False, stream)
    destination.chmod(0o755)


def materialize(build, artifact):
    pin, package, archive, details = verified_inputs(build, artifact)
    if artifact == "electron":
        outputs = [package / "dist", package / "path.txt"]
    else:
        outputs = [package / "build", package / "scripts" / "prebuild.tar.gz"]
    for output in outputs:
        native.require(output.parent.is_dir() and output.parent.resolve() == output.parent
                       and not output.exists() and not output.is_symlink(),
                       "native output exists or has unsafe parent; refusing overwrite")
    with tempfile.TemporaryDirectory(prefix=".volparossa-native-", dir=package) as temporary:
        staged = Path(temporary)
        payload = staged / "payload"
        extract_verified(archive, pin["format"], payload)
        if artifact == "electron":
            native.require((payload / "electron").is_file()
                           and (payload / "version").read_text().removeprefix("v") == pin["version"]
                           and (payload / "LICENSE").is_file()
                           and not (payload / "electron.d.ts").exists(),
                           "Electron native layout differs from reviewed Linux archive")
            auxiliary = staged / "path.txt"
            auxiliary.write_text("electron")
            source = payload
        else:
            expected = {f"build/{platform}/libringrtc-{arch}.node"
                        for platform in ("linux", "darwin", "win32") for arch in ("x64", "arm64")}
            native.require({str(path.relative_to(payload)) for path in payload.rglob("*") if path.is_file()}
                           == expected, "RingRTC native layout differs from reviewed archive")
            source = payload / "build"
            auxiliary = staged / "prebuild.tar.gz"
            shutil.copyfile(archive, auxiliary)
            native.require(native.digest(auxiliary).hex() == pin["sha256"], "RingRTC cached archive differs")
        auxiliary.chmod(0o644)
        native.require(native.digest(archive).hex() == pin["sha256"], "native input changed during extraction")
        for output in outputs:
            native.require(not output.exists() and not output.is_symlink(), "native output appeared; refusing overwrite")
        # No original package file is overwritten. A failed materialization can
        # leave a partial new output, which a subsequent run refuses to replace.
        source.rename(outputs[0])
        # Exclusive hard-link creation avoids replacing even a racing path.txt
        # or cache file; TemporaryDirectory removes only its own source link.
        outputs[1].hardlink_to(auxiliary)
    return dict(schema_version=1, artifact=artifact, version=pin["version"],
        signal_source=native.COMMIT, archive_sha256=pin["sha256"], **details,
        package=str(package.relative_to(build)),
        outputs=[str(path.relative_to(build)) for path in outputs],
        original_npm_files_verified=True, third_party_notices_retained=True,
        network_access=False, lifecycle_scripts_executed=False, native_code_executed=False,
        host_install_or_permissions_changed=False, source_build_proven=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", choices=tuple(native.PINS), required=True)
    args = parser.parse_args()
    print(json.dumps(materialize(native.ROOT / "build", args.artifact), sort_keys=True))


if __name__ == "__main__":
    main()
