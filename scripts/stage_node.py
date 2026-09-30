#!/usr/bin/env python3
"""Explicit, hash-pinned Node/npm staging for workspace tests; no dependency install."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import tarfile
import tempfile

from stage_signal_source import ROOT, fetch

VERSION = "24.19.0"
NAME = f"node-v{VERSION}-linux-x64"
SHA256 = "14b342e71204f811bde6153be8e04b62aef63c236fef92b55f9c83154b409647"
BASE = f"https://nodejs.org/dist/v{VERSION}/"
NPM = "lib/node_modules/npm"
MAX_MEMBERS = 20_000
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_SELECTED_BYTES = 256 * 1024 * 1024


def checked_members(archive, with_npm):
    """Read only selected bytes; never delegate filesystem extraction to tar."""
    seen, files, links, entrypoints = set(), {}, {}, {}
    expanded = selected = 0
    for member in archive:
        name = member.name.rstrip("/") if member.isdir() else member.name
        path = PurePosixPath(name)
        if (not name or len(name.encode()) > 4096 or "\\" in name
                or any(ord(char) < 32 for char in name)
                or path.is_absolute() or ".." in path.parts or str(path) != name
                or path.parts[0] != NAME or name in seen):
            raise ValueError("unsafe or duplicate Node archive path")
        seen.add(name)
        expanded += member.size
        if len(seen) > MAX_MEMBERS or member.size < 0 or expanded > MAX_ARCHIVE_BYTES:
            raise ValueError("Node archive count or expanded size exceeds bound")
        relative = str(path.relative_to(NAME))
        if member.isdir():
            continue
        if with_npm and relative in ("bin/npm", "bin/npx"):
            expected = f"../{NPM}/bin/{PurePosixPath(relative).name}-cli.js"
            if not member.issym() or member.linkname != expected:
                raise ValueError("bundled npm entrypoint differs")
            entrypoints[relative] = member.linkname
            continue
        if relative not in ("bin/node", "LICENSE") and not (with_npm and relative.startswith(NPM + "/")):
            continue
        if member.issym() and relative.startswith(NPM + "/"):
            if (not member.linkname or len(member.linkname.encode()) > 4096
                    or "\\" in member.linkname or PurePosixPath(member.linkname).is_absolute()
                    or any(ord(char) < 32 for char in member.linkname)):
                raise ValueError("unsafe bundled npm symlink")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(relative), member.linkname))
            if not resolved.startswith(NPM + "/"):
                raise ValueError("bundled npm symlink escapes its package")
            links[relative] = (member.linkname, resolved)
            continue
        maximum = 160 * 1024 * 1024 if relative == "bin/node" else 16 * 1024 * 1024
        if not member.isfile() or member.mode & 0o6000 or not 0 <= member.size <= maximum:
            raise ValueError("unsupported selected Node archive member")
        selected += member.size
        if selected > MAX_SELECTED_BYTES:
            raise ValueError("selected runtime exceeds bound")
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError("missing Node archive entry")
        content = stream.read(member.size + 1)
        if len(content) != member.size:
            raise ValueError("truncated Node archive entry")
        files[relative] = (content, 0o755 if member.mode & 0o111 else 0o644)
    required = {"bin/node", "LICENSE"}
    if with_npm:
        required.update(f"{NPM}/{name}" for name in ("package.json", "LICENSE", "bin/npm-cli.js", "bin/npx-cli.js"))
        if set(entrypoints) != {"bin/npm", "bin/npx"}:
            raise ValueError("bundled npm entrypoints missing")
    if not required <= files.keys() or any(not files[name][0] for name in required):
        raise ValueError("required Node/npm files missing")
    if files["bin/node"][1] != 0o755:
        raise ValueError("Node executable mode missing")
    for name in files.keys() | links.keys():
        if any(str(parent) in links for parent in PurePosixPath(name).parents):
            raise ValueError("selected archive member has a symlink ancestor")
    for _, resolved in links.values():
        for _ in range(8):
            if resolved not in links:
                break
            resolved = links[resolved][1]
        else:
            raise ValueError("bundled npm symlink cycle or depth")
        if resolved not in files:
            raise ValueError("bundled npm symlink target missing")
    npm_version = None
    if with_npm:
        package = json.loads(files[f"{NPM}/package.json"][0])
        npm_version = package.get("version")
        if package.get("name") != "npm" or not isinstance(npm_version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", npm_version):
            raise ValueError("invalid bundled npm identity")
    return files, links, npm_version


def launcher(command):
    # Upstream CLI bytes are untouched. Unlike its /usr/bin/env node shebang,
    # this local launcher cannot accidentally select a different installed Node.
    # PATH changes apply only to npm and its script children, never the host.
    return (
        '#!/bin/sh\nset -e\n'
        'vp_npm_bin=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && pwd -P)\n'
        'PATH="$vp_npm_bin:$PATH"\nexport PATH\n'
        f'exec "$vp_npm_bin/node" "$vp_npm_bin/../{NPM}/bin/{command}-cli.js" "$@"\n'
    ).encode()


def stage(build, with_npm=False):
    if build.is_symlink():
        raise ValueError("build symlink refused")
    build.mkdir(mode=0o700, exist_ok=True)
    target = build / (NAME + ("-with-npm" if with_npm else ""))
    report_path = build / ("node-npm-runtime-report.json" if with_npm else "node-runtime-report.json")
    if target.exists() or target.is_symlink():
        raise ValueError("runtime target exists; refusing overwrite")
    if report_path.exists() or report_path.is_symlink():
        raise ValueError("runtime report exists; refusing overwrite")
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
            files, links, npm_version = checked_members(archive, with_npm)
    with tempfile.TemporaryDirectory(prefix="node-runtime-", dir=build) as staging:
        staged = Path(staging) / "runtime"
        staged.mkdir(mode=0o700)
        for name, (content, mode) in files.items():
            output = staged / name
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                stream.write(content)
            output.chmod(mode)
        for name, (link, _) in links.items():
            output = staged / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.symlink_to(link)
        if with_npm:
            for command in ("npm", "npx"):
                output = staged / "bin" / command
                with output.open("xb") as stream:
                    stream.write(launcher(command))
                output.chmod(0o755)
        if target.exists() or target.is_symlink():
            raise ValueError("runtime target appeared; refusing overwrite")
        staged.rename(target)
    report = {"version": VERSION, "url": BASE + NAME + ".tar.xz", "sha256": SHA256,
              "target": target.name, "archive_bytes": len(data),
              "extracted_bytes": sum(len(content) for content, _ in files.values()),
              "selected_files": len(files), "selected_internal_symlinks": len(links),
              "npm_version": npm_version, "npm_dependency_installation_performed": False,
              "launchers_use_pinned_node": with_npm,
              "authentication": "official nodejs.org HTTPS SHASUMS256 plus reviewed exact hash",
              "release_pgp_signature_independently_verified": False,
              "global_install_or_path_change": False}
    with report_path.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-npm", action="store_true",
                        help="stage bundled npm in a separate fresh runtime; preserve existing node-only staging")
    args = parser.parse_args()
    report = stage(ROOT / "build", args.with_npm)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
