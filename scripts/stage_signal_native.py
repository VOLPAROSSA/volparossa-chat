#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicitly stage verified native archives; never extract, install or execute them."""

import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

from stage_signal_source import COMMIT, ROOT

LOCK_SHA256 = "bc06486a375791ed118b10f10c33428b0fefa37ce22e6c2cf56c2bf37dd11040"
MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_MEMBERS = 20_000
PINS = {
    "electron": {
        "package": "electron", "version": "44.1.0", "license": "MIT",
        "npm_url": "https://registry.npmjs.org/electron/-/electron-44.1.0.tgz",
        "npm_bytes": 196901,
        "integrity": "sha512-kmLg8axOg22DC3fXx5NCwBYW8fx0rK2zzJ3Tf67GjNCkxfoxEcd+yo0QfDjlBtHJs3xeA8fTg64b6iVzFTVErg==",
        "target": "electron-44.1.0-linux-x64", "format": "zip",
        "filename": "electron-v44.1.0-linux-x64.zip", "bytes": 122591871,
        "url": "https://github.com/electron/electron/releases/download/v44.1.0/electron-v44.1.0-linux-x64.zip",
        "sha256": "9cea932df41cb6e68122ecd32f814db5a7544592f3091983b959f4f7e0d6fd88",
    },
    "ringrtc": {
        "package": "@signalapp/ringrtc", "version": "2.71.0", "license": "AGPL-3.0-only",
        "npm_url": "https://registry.npmjs.org/@signalapp/ringrtc/-/ringrtc-2.71.0.tgz",
        "npm_bytes": 79667,
        "integrity": "sha512-F75ix86XyYoA14x3yPoPwxr6TUq4pdWKhHLcPfZxRR/0xxrvo8PXHvDw5d95YOoi8BJfE0by6DxgrwG9DyvE0Q==",
        "target": "ringrtc-2.71.0", "format": "tar.gz",
        "filename": "ringrtc-desktop-build-v2.71.0.tar.gz", "bytes": None,
        "url": "https://build-artifacts.signal.org/libraries/ringrtc-desktop-build-v2.71.0.tar.gz",
        "sha256": "0b5b0560d00e090070ecac783e7869bd49412786ce8a3aac01b87ae88ea8d66f",
    },
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path, algorithm="sha256"):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).digest()


def verify_lock(lock, pin):
    require(len(lock) <= 4 * 1024 * 1024 and hashlib.sha256(lock).hexdigest() == LOCK_SHA256,
            "Signal lockfile differs from reviewed source")
    key = re.escape(pin["package"] + "@" + pin["version"])
    matches = re.findall(r"^  '?" + key + r"'?:\n    resolution: \{integrity: ([^,]+), tarball: ([^}]+)\}",
                         lock.decode("utf-8"), re.M)
    require(matches == [(pin["integrity"], pin["npm_url"])], "native package lockfile binding differs")


class HttpsRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        require(urllib.parse.urlsplit(newurl).scheme == "https", "non-HTTPS artifact redirect")
        return super().redirect_request(request, response, code, message, headers, newurl)


def download(url, output, maximum, expected_bytes):
    require(0 < maximum <= MAX_DOWNLOAD and urllib.parse.urlsplit(url).scheme == "https",
            "invalid artifact download bound")
    opener = urllib.request.build_opener(HttpsRedirects())
    request = urllib.request.Request(url, headers={"User-Agent": "volparossa-native-stager/1"})
    started, count = time.monotonic(), 0
    with opener.open(request, timeout=30) as response, output.open("xb") as target:
        length = response.headers.get("Content-Length")
        require(length is None or 0 <= int(length) <= maximum, "native archive exceeds download bound; review required")
        while chunk := response.read(min(65536, maximum - count + 1)):
            count += len(chunk)
            require(count <= maximum and time.monotonic() - started <= 300,
                    "native archive exceeds download/time bound; review required")
            target.write(chunk)
    require(count > 0 and (expected_bytes is None or count == expected_bytes), "native artifact size differs")
    output.chmod(0o600)


def inspect_archive(path, kind):
    """Inspect bounded regular files/directories only; retain original archive bytes.

    Links and special entries require explicit future review even though this
    stager never extracts them. No setuid bit is applied to chrome-sandbox.
    """
    seen, files, parents, expanded, licenses = set(), set(), set(), 0, []

    def check(name, size, directory, regular, stream):
        nonlocal expanded
        normalized = name[:-1] if directory and name.endswith("/") else name
        member = PurePosixPath(normalized)
        require(normalized and len(normalized.encode()) <= 4096 and not member.is_absolute()
                and str(member) == normalized and ".." not in member.parts and "\\" not in normalized
                and ":" not in normalized and all(ord(char) >= 32 for char in normalized), "unsafe native archive path")
        require(normalized not in seen and len(seen) < MAX_MEMBERS, "duplicate or excessive native archive members")
        require(directory or regular, "native archive links/special entries require review")
        require(not any(str(parent) in files for parent in member.parents), "archive file used as a parent")
        require(directory or normalized not in parents, "archive parent changed to file")
        require(0 <= size <= MAX_EXPANDED and (not directory or size == 0), "invalid native archive member size")
        expanded += size
        require(expanded <= MAX_EXPANDED, "native archive expanded size exceeds bound; review required")
        seen.add(normalized)
        parents.update(str(parent) for parent in member.parents)
        if directory:
            return
        files.add(normalized)
        count = 0
        while chunk := stream.read(65536):
            count += len(chunk)
            require(count <= size, "native archive member exceeds declaration")
        require(count == size, "truncated native archive member")
        if "license" in member.name.lower() or "acknowledgment" in member.name.lower() or member.name.lower() == "copying":
            licenses.append(normalized)

    if kind == "zip":
        with zipfile.ZipFile(path) as archive:
            require(len(archive.infolist()) <= MAX_MEMBERS, "excessive native ZIP members")
            for member in archive.infolist():
                mode = member.external_attr >> 16
                directory = member.is_dir()
                regular = stat.S_IFMT(mode) in (0, stat.S_IFREG)
                require(not directory or stat.S_IFMT(mode) in (0, stat.S_IFDIR), "non-directory native ZIP entry")
                require(not member.flag_bits & 1, "encrypted native ZIP unsupported")
                with archive.open(member) as stream:
                    check(member.filename, member.file_size, directory, regular, stream)
    else:
        require(kind == "tar.gz", "unsupported native archive format")
        with tarfile.open(path, "r|gz") as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        check(member.name, member.size, False, True, stream)
                else:
                    check(member.name, member.size, member.isdir(), False, None)
    require(files, "empty native archive")
    return dict(members=len(seen), expanded_bytes=expanded, retained_license_members=licenses)


def verify_package(package_path, pin):
    require(package_path.stat().st_size == pin["npm_bytes"]
            and digest(package_path, "sha512") == base64.b64decode(pin["integrity"][7:], validate=True),
            "native npm package differs from lockfile integrity")
    details = inspect_archive(package_path, "tar.gz")
    with tarfile.open(package_path, "r:gz") as archive:
        def document(name):
            member = archive.getmember("package/" + name)
            require(member.isfile() and member.size <= 16384, "native package metadata exceeds bound")
            return json.load(archive.extractfile(member))
        package = document("package.json")
        require(all(package.get(key) == pin[key] for key in ("version", "license"))
                and package.get("name") == pin["package"], "native npm identity differs")
        if pin["package"] == "electron":
            require(document("checksums.json").get(pin["filename"]) == pin["sha256"],
                    "Electron checksum differs from SRI-covered manifest")
        else:
            config = package["config"]
            require(config["prebuildChecksum"] == pin["sha256"]
                    and config["prebuildUrl"].replace("${npm_package_version}", pin["version"]) == pin["url"],
                    "RingRTC artifact differs from SRI-covered configuration")
    return details


def stage(build, artifact):
    pin = PINS[artifact]
    require(build.is_dir() and not build.is_symlink() and build.resolve() == build, "invalid workspace build directory")
    source = build / f"signal-desktop-{COMMIT}"
    lock = source / "pnpm-lock.yaml"
    require(source.is_dir() and not source.is_symlink() and lock.is_file() and not lock.is_symlink()
            and lock.stat().st_size <= 4 * 1024 * 1024, "stage pinned Signal source first")
    verify_lock(lock.read_bytes(), pin)
    parent = build / "native-inputs"
    require(not parent.is_symlink(), "native input parent must not be a symlink")
    parent.mkdir(mode=0o700, exist_ok=True)
    target = parent / pin["target"]
    require(not target.exists() and not target.is_symlink(), "native target exists; refusing overwrite")
    with tempfile.TemporaryDirectory(prefix=artifact + "-", dir=parent) as temporary:
        staged = Path(temporary) / "inputs"
        staged.mkdir(mode=0o700)
        package_path = staged / (pin["package"].split("/")[-1] + "-" + pin["version"] + ".tgz")
        download(pin["npm_url"], package_path, pin["npm_bytes"], pin["npm_bytes"])
        package_details = verify_package(package_path, pin)
        native_path = staged / pin["filename"]
        download(pin["url"], native_path, MAX_DOWNLOAD, pin["bytes"])
        require(digest(native_path).hex() == pin["sha256"], "native artifact checksum differs")
        native_details = inspect_archive(native_path, pin["format"])
        report = dict(schema_version=1, signal_source=COMMIT, lock_sha256=LOCK_SHA256,
            artifact=artifact, version=pin["version"], license=pin["license"],
            package=dict(file=package_path.name, url=pin["npm_url"], integrity=pin["integrity"],
                sha256=digest(package_path).hex(), bytes=package_path.stat().st_size, **package_details),
            native=dict(file=native_path.name, url=pin["url"], sha256=pin["sha256"],
                bytes=native_path.stat().st_size, **native_details),
            checksum_bound_to_verified_npm_package=True, original_archives_and_notices_retained=True,
            extraction_performed=False, lifecycle_scripts_executed=False, native_code_executed=False,
            host_install_or_permissions_changed=False, source_build_proven=False)
        with (staged / "provenance.json").open("x") as output:
            output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
        require(not target.exists() and not target.is_symlink(), "native target appeared; refusing overwrite")
        staged.rename(target)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True)
    parser.add_argument("--artifact", choices=tuple(PINS), required=True)
    args = parser.parse_args()
    print(json.dumps(stage(ROOT / "build", args.artifact), sort_keys=True))


if __name__ == "__main__":
    main()
