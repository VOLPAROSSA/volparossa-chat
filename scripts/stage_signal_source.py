#!/usr/bin/env python3
"""Stage pinned upstream source only; never install packages or run upstream scripts."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import shutil
import ssl
import stat
import sys
import tarfile
import tempfile
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "ef3872cb0249ec939d8aff857568a0e87a6b5075"
TREE = "5ad0cc5dda0420c6bbfc1bfe2317fde00c891064"
ARCHIVE = f"https://codeload.github.com/signalapp/Signal-Desktop/tar.gz/{COMMIT}"
ARCHIVE_SHA256 = "224c0cf821287cdc33b05b242f98559d823a6045865cb2f15b656dbcf5b8d038"
PREFIX = f"Signal-Desktop-{COMMIT}/"
METADATA_BOUND = 4 * 1024 * 1024


def fetch(url, maximum):
    request = urllib.request.Request(url, headers={"User-Agent": "volparossa-source-stager/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("upstream response exceeds bound")
    return data


def git_hash(kind, data):
    return hashlib.sha1(f"{kind} {len(data)}\0".encode() + data).hexdigest()


def checked_path(name):
    path = PurePosixPath(name)
    if not name or str(path) == "." or path.is_absolute() or ".." in path.parts or str(path) != name or "\0" in name:
        raise ValueError("unsafe upstream path")
    return path


def archive_tree(archive):
    """Reconstruct Git objects from the exact archive; trust only the pinned root tree."""
    entries, directories, seen, expanded = {}, {""}, set(), 0
    with tarfile.open(archive, "r:gz") as source:
        for index, member in enumerate(source):
            if index >= 10_001 or member.name in seen:
                raise ValueError("source archive entries exceeded or duplicated")
            seen.add(member.name)
            if member.isdir() and member.name.rstrip("/") == PREFIX[:-1]:
                continue
            if not member.name.startswith(PREFIX):
                raise ValueError("archive prefix differs")
            name = member.name[len(PREFIX):]
            if member.isdir():
                name = name.rstrip("/")
            path = checked_path(name)
            if name in entries or member.mode & ~0o777:
                raise ValueError("duplicate or privileged archive entry")
            if member.isdir():
                entries[name] = dict(path=name, type="tree", mode="040000")
                directories.add(name)
            elif member.isfile():
                if not 0 <= member.size <= 64 * 1024 * 1024:
                    raise ValueError("upstream file size")
                expanded += member.size
                if expanded > 256 * 1024 * 1024:
                    raise ValueError("expanded source exceeds bound")
                stream = source.extractfile(member)
                if stream is None:
                    raise ValueError("missing archive entry")
                data = stream.read(member.size + 1)
                if len(data) != member.size:
                    raise ValueError("truncated archive entry")
                entries[name] = dict(path=name, type="blob", size=len(data),
                    mode="100755" if member.mode & 0o111 else "100644", sha=git_hash("blob", data))
            else:
                raise ValueError("unsupported archive entry")
            directories.update(str(parent) for parent in path.parents if str(parent) != ".")
    for name in directories - {""}:
        if name in entries and entries[name]["type"] != "tree":
            raise ValueError("archive file masks directory")
        entries.setdefault(name, dict(path=name, type="tree", mode="040000"))
    if not 1 <= len(entries) <= 10_000:
        raise ValueError("source tree size")
    children = {name: [] for name in directories}
    for item in entries.values():
        parent = str(PurePosixPath(item["path"]).parent)
        children["" if parent == "." else parent].append(item)
    root_hash = None
    for directory in sorted(directories, key=lambda value: (value.count("/"), len(value)), reverse=True):
        if not children[directory]:
            raise ValueError("untracked empty archive directory")
        def name(item):
            return PurePosixPath(item["path"]).name.encode("utf-8")
        ordered = sorted(children[directory], key=lambda item: name(item) + (b"/" if item["type"] == "tree" else b""))
        raw = b"".join(item["mode"].lstrip("0").encode() + b" " + name(item)
                       + b"\0" + bytes.fromhex(item["sha"]) for item in ordered)
        hashed = git_hash("tree", raw)
        if directory:
            entries[directory]["sha"] = hashed
        else:
            root_hash = hashed
    document = dict(sha=root_hash, truncated=False, tree=sorted(entries.values(), key=lambda item: item["path"]))
    checked_tree(document)
    return document


def checked_tree(document):
    if document.get("sha") != TREE or document.get("truncated") is not False:
        raise ValueError("wrong or incomplete pinned source tree")
    entries = document["tree"]
    if not 1 <= len(entries) <= 10_000:
        raise ValueError("source tree size")
    directories = {"": []}
    files = {}
    expected_trees = {"": TREE}
    seen = set()
    for item in entries:
        path = checked_path(item["path"])
        if str(path) in seen:
            raise ValueError("duplicate upstream entry")
        seen.add(str(path))
        mode = item["mode"]
        if mode == "040000" and item["type"] == "tree":
            directories.setdefault(str(path), [])
            expected_trees[str(path)] = item["sha"]
        elif mode in ("100644", "100755") and item["type"] == "blob":
            if not 0 <= item["size"] <= 64 * 1024 * 1024:
                raise ValueError("upstream file size")
            files[str(path)] = item
        else:
            raise ValueError("unsupported upstream object; explicit review required")
        parent = str(path.parent) if path.parent != PurePosixPath(".") else ""
        directories.setdefault(parent, []).append(item)
    if sum(item["size"] for item in files.values()) > 256 * 1024 * 1024:
        raise ValueError("expanded source exceeds bound")
    for directory, children in directories.items():
        def name(item):
            return PurePosixPath(item["path"]).name.encode()
        children.sort(key=lambda item: name(item) + (b"/" if item["type"] == "tree" else b""))
        raw = b"".join(item["mode"].lstrip("0").encode() + b" " + name(item)
                       + b"\0" + bytes.fromhex(item["sha"]) for item in children)
        if git_hash("tree", raw) != expected_trees.get(directory):
            raise ValueError("source tree hash mismatch")
    return files


def extract_checked(archive, destination, files):
    seen = set()
    with tarfile.open(archive, "r:gz") as source:
        for member in source:
            if member.isdir():
                continue
            if not member.isfile() or not member.name.startswith(PREFIX):
                raise ValueError("unsupported archive entry")
            name = member.name[len(PREFIX):]
            expected = files.get(name)
            if expected is None or name in seen or member.size != expected["size"]:
                raise ValueError("archive differs from exact upstream tree")
            if member.mode & ~0o777 or ("100755" if member.mode & 0o111 else "100644") != expected["mode"]:
                raise ValueError("archive executable mode differs from pinned tree")
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("missing archive entry")
            data = stream.read(member.size + 1)
            if git_hash("blob", data) != expected["sha"]:
                raise ValueError("upstream file hash mismatch")
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                output.write(data)
            path.chmod(0o755 if expected["mode"] == "100755" else 0o644)
            seen.add(name)
    if seen != set(files):
        raise ValueError("archive is missing upstream files")


def verify_checkout(source, files):
    actual = set()
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("source checkout symlink refused")
        if path.is_file():
            name = str(path.relative_to(source))
            item = files.get(name)
            if item is None or path.stat().st_size != item["size"] or git_hash("blob", path.read_bytes()) != item["sha"]:
                raise ValueError("source checkout differs from pinned tree")
            if stat.S_IMODE(path.stat().st_mode) != (0o755 if item["mode"] == "100755" else 0o644):
                raise ValueError("source checkout mode differs from pinned tree")
            actual.add(name)
    if actual != set(files):
        raise ValueError("source checkout incomplete")


def failure_diagnostic(error, phase):
    """Closed error classification: no URL, response body, path or exception text."""
    status = None
    if isinstance(error, urllib.error.HTTPError):
        category, error_type = "http", "http_error"
        status = error.code if type(error.code) is int and 100 <= error.code <= 599 else None
    else:
        reason = error.reason if isinstance(error, urllib.error.URLError) else error
        if isinstance(reason, ssl.SSLError):
            category, error_type = "tls", "tls_error"
        elif isinstance(reason, (TimeoutError, socket.timeout)):
            category, error_type = "timeout", "timeout_error"
        elif isinstance(reason, socket.gaierror):
            category, error_type = "network", "dns_error"
        elif isinstance(error, urllib.error.URLError):
            category, error_type = "network", "url_error"
        elif isinstance(error, (ValueError, KeyError, TypeError, tarfile.TarError, EOFError)):
            category, error_type = "validation", "invalid_source"
        elif isinstance(error, OSError):
            category, error_type = "io", "os_error"
        else:
            category, error_type = "unknown", "unknown_error"
    return dict(version=1, kind="signal-source-staging-failure", phase=phase,
                category=category, error_type=error_type, http_status=status)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--download", action="store_true",
                        help="explicit source-only download; no dependency installation")
    modes.add_argument("--verify-existing", action="store_true", help="verify staged source and pinned tree metadata offline")
    args = parser.parse_args(argv)
    phase = "configuration"
    phases = []
    try:
        return stage(args, lambda value: phases.append(value))
    except Exception as error:
        phase = phases[-1] if phases else phase
        diagnostic = failure_diagnostic(error, phase)
        build = ROOT / "build"
        if build.is_dir() and not build.is_symlink():
            try:
                descriptor = os.open(build / "signal-source-error.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "w") as output:
                    output.write(json.dumps(diagnostic, sort_keys=True) + "\n")
            except OSError:
                pass  # Never replace earlier evidence or mask the original failure.
        print(json.dumps(diagnostic, sort_keys=True), file=sys.stderr)
        return 1


def stage(args, set_phase):
    lock = json.loads((ROOT / "upstream-lock.json").read_text())
    if lock["signal_desktop"]["commit"] != COMMIT:
        raise ValueError("source pin changed; review stager")
    build = ROOT / "build"
    if build.is_symlink():
        raise ValueError("build directory must not be a symlink")
    build.mkdir(mode=0o700, exist_ok=True)
    target = build / f"signal-desktop-{COMMIT}"
    if args.verify_existing:
        set_phase("existing_verification")
        if target.is_symlink() or not target.is_dir():
            raise ValueError("invalid staged source path")
        metadata = build / "signal-source-tree.json"
        if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size > METADATA_BOUND:
            raise ValueError("invalid staged source metadata")
        files = checked_tree(json.loads(metadata.read_bytes()))
        verify_checkout(target, files)
        print(json.dumps({"source_verified": True, "files": len(files), "git_tree": TREE}))
        return 0
    if target.exists() or target.is_symlink():
        raise ValueError("source target already exists; it will not be overwritten")
    temporary = Path(tempfile.mkdtemp(prefix="signal-source-", dir=build))
    try:
        archive = temporary / "source.tar.gz"
        set_phase("archive_fetch")
        archive.write_bytes(fetch(ARCHIVE, 128 * 1024 * 1024))
        set_phase("archive_verification")
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise ValueError("source archive hash differs from reviewed artifact")
        document = archive_tree(archive)
        files = checked_tree(document)
        tree_bytes = (json.dumps(document, sort_keys=True) + "\n").encode()
        if len(tree_bytes) > METADATA_BOUND:
            raise ValueError("source metadata exceeds bound")
        set_phase("source_extraction")
        source = temporary / "source"
        source.mkdir()
        extract_checked(archive, source, files)
        if hashlib.sha256((source / "LICENSE").read_bytes()).hexdigest() != lock["signal_desktop"]["license_sha256"]:
            raise ValueError("upstream license hash mismatch")
        report = {
            "schema_version": 1, "commit": COMMIT, "git_tree": TREE,
            "archive_url": ARCHIVE,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "archive_bytes": archive.stat().st_size,
            "source_bytes": sum(item["size"] for item in files.values()),
            "verified_files": len(files), "all_git_blob_and_tree_hashes_verified": True,
            "dependency_installation_performed": False,
            "source_build_completed": False,
        }
        source.rename(target)
        (build / "signal-source-tree.json").write_bytes(tree_bytes)
        # Report is beside source, so the source directory remains the exact upstream tree.
        report_path = build / "signal-source-report.json"
        with report_path.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
        print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    sys.exit(main())
