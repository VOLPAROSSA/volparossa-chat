#!/usr/bin/env python3
"""Stage pinned upstream source only; never install packages or run upstream scripts."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "ef3872cb0249ec939d8aff857568a0e87a6b5075"
TREE = "5ad0cc5dda0420c6bbfc1bfe2317fde00c891064"
ARCHIVE = f"https://codeload.github.com/signalapp/Signal-Desktop/tar.gz/{COMMIT}"
ARCHIVE_SHA256 = "224c0cf821287cdc33b05b242f98559d823a6045865cb2f15b656dbcf5b8d038"
TREE_URL = f"https://api.github.com/repos/signalapp/Signal-Desktop/git/trees/{TREE}?recursive=1"
PREFIX = f"Signal-Desktop-{COMMIT}/"


def fetch(url, maximum):
    request = urllib.request.Request(url, headers={"User-Agent": "volparossa-source-stager/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("upstream response exceeds bound")
    return data


def git_hash(kind, data):
    return hashlib.sha1(f"{kind} {len(data)}\0".encode() + data).hexdigest()


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
        path = PurePosixPath(item["path"])
        if path.is_absolute() or ".." in path.parts or str(path) != item["path"]:
            raise ValueError("unsafe upstream path")
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
            actual.add(name)
    if actual != set(files):
        raise ValueError("source checkout incomplete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--download", action="store_true",
                        help="explicit source-only download; no dependency installation")
    modes.add_argument("--verify-existing", action="store_true", help="verify staged source, fetch tree metadata only")
    args = parser.parse_args()
    lock = json.loads((ROOT / "upstream-lock.json").read_text())
    if lock["signal_desktop"]["commit"] != COMMIT:
        raise ValueError("source pin changed; review stager")
    build = ROOT / "build"
    if build.is_symlink():
        raise ValueError("build directory must not be a symlink")
    build.mkdir(mode=0o700, exist_ok=True)
    target = build / f"signal-desktop-{COMMIT}"
    tree_bytes = fetch(TREE_URL, 4 * 1024 * 1024)
    files = checked_tree(json.loads(tree_bytes))
    if args.verify_existing:
        if target.is_symlink() or not target.is_dir():
            raise ValueError("invalid staged source path")
        verify_checkout(target, files)
        (build / "signal-source-tree.json").write_bytes(tree_bytes)
        print(json.dumps({"source_verified": True, "files": len(files), "git_tree": TREE}))
        return
    if target.exists() or target.is_symlink():
        raise ValueError("source target already exists; it will not be overwritten")
    temporary = Path(tempfile.mkdtemp(prefix="signal-source-", dir=build))
    try:
        archive = temporary / "source.tar.gz"
        archive.write_bytes(fetch(ARCHIVE, 128 * 1024 * 1024))
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise ValueError("source archive hash differs from reviewed artifact")
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
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    main()
