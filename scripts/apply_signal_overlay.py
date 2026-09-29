#!/usr/bin/env python3
"""Create a separate checked candidate; leave the pristine pinned source untouched."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from stage_signal_source import COMMIT, ROOT, checked_tree, verify_checkout

SOURCE_HASHES = {
    # Filled from the blob-verified upstream checkout, not from an editable candidate.
    "ts/CI.preload.ts": "c0cceb89192d42513577f400ecd70fa3a2f3a06114bee2a8a07e3fde60677427",
    "ts/test-mock/backups/backups_test.node.ts": "efdc142d335f9f930ce3e382a455d46ed0c22f86da09bdb575bc7e8994f761cd",
}


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("pinned patch context differs")
    return text.replace(before, after, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check an existing candidate without editing it")
    args = parser.parse_args()
    build = ROOT / "build"
    source = build / f"signal-desktop-{COMMIT}"
    target = build / "signal-backup-candidate"
    if build.is_symlink() or source.is_symlink() or target.is_symlink() or (target.exists() and not args.check):
        raise ValueError("source/candidate path unsafe or candidate already exists")
    files = checked_tree(json.loads((build / "signal-source-tree.json").read_text()))
    verify_checkout(source, files)
    patched = {}
    for name, digest in SOURCE_HASHES.items():
        path = source / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("pinned patch source hash differs")
        patched[name] = path.read_text()
    ci = patched["ts/CI.preload.ts"]
    ci = replace_once(ci, "import { backupsService } from './services/backups/index.preload.ts';",
        "import { backupsService } from './services/backups/index.preload.ts';\n"
        "import { exportToReplicas, restoreFromReplicas, readConnectorConfig } "
        "from './services/backups/volparossa/connector.preload.ts';")
    ci = replace_once(ci, "  exportLocalBackup: (backupsBaseDir: string) => Promise<string>;",
        "  exportLocalBackup: (backupsBaseDir: string) => Promise<string>;\n"
        "  volparossaBackupExport: (work: string, config: string) => Promise<void>;\n"
        "  volparossaBackupRestore: (work: string, config: string, destination: string) => Promise<string>;")
    ci = replace_once(ci, "    exportLocalBackup,\n", "    exportLocalBackup,\n"
        "    async volparossaBackupExport(work, config) {\n"
        "      await exportToReplicas(work, await readConnectorConfig(config));\n"
        "    },\n"
        "    async volparossaBackupRestore(work, config, destination) {\n"
        "      return restoreFromReplicas(work, await readConnectorConfig(config), destination);\n"
        "    },\n")
    patched["ts/CI.preload.ts"] = ci
    name = "ts/test-mock/backups/backups_test.node.ts"
    before = "  it('exports and imports regular backup', async function () {"
    patched[name] = replace_once(patched[name], before,
        (ROOT / "patches/signal-backup-test.txt").read_text().rstrip() + "\n\n" + before)
    overlays = {str(path.relative_to(ROOT / "overlay")): path
                for path in (ROOT / "overlay").rglob("*.ts")}
    if args.check:
        expected_names = set(files) | set(overlays)
        actual_names = set()
        for path in target.rglob("*"):
            if path.is_symlink():
                raise ValueError("candidate symlink refused")
            if not path.is_file():
                continue
            name = str(path.relative_to(target))
            actual_names.add(name)
            if name in patched:
                expected = patched[name].encode()
            elif name in overlays:
                expected = overlays[name].read_bytes()
            elif name in files:
                expected = (source / name).read_bytes()
            else:
                raise ValueError("unexpected candidate file")
            if path.read_bytes() != expected:
                raise ValueError(f"candidate differs from exact generated overlay: {name}")
        if actual_names != expected_names:
            raise ValueError("candidate incomplete")
        print(json.dumps({"exact_source_overlay_check": True, "candidate_files": len(actual_names),
                          "signal_runtime_test_executed": False}))
        return
    shutil.copytree(source, target, symlinks=True)
    for name, text in patched.items():
        (target / name).write_text(text)
    for path in sorted((ROOT / "overlay").rglob("*.ts")):
        relative = path.relative_to(ROOT / "overlay")
        destination = target / relative
        if destination.exists() or destination.is_symlink() or path.is_symlink():
            raise ValueError("overlay would overwrite upstream file")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    names = [*patched, *(str(path.relative_to(ROOT / "overlay")) for path in (ROOT / "overlay").rglob("*.ts"))]
    report = {"upstream_commit": COMMIT, "candidate": str(target.relative_to(ROOT)),
              "modified_files": {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in names},
              "dependency_installation_performed": False, "signal_runtime_test_executed": False}
    (build / "signal-overlay-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
