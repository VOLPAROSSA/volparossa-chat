#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Compile the pinned Signal candidate offline; never launch Electron or install packages."""

import argparse
import json
import os
import sys
import uuid

import stage_signal_dependencies as deps

SOURCE_DATE_EPOCH = "1790198897"
ASSETS = ("build:protobuf", "build:emoji-data", "build:rolldown:prod", "build:compact-locales",
          "build:styles:prod", "get-expire-time", "build:policy-files")
STEPS = (
    ("types", ("--dir", "packages/types", "run", "build"), 300,
     ("packages/types/dist/index.std.cjs", "packages/types/dist/index.std.mjs")),
    ("windows-ucv", ("--dir", "packages/windows-ucv", "run", "build"), 300,
     ("packages/windows-ucv/dist/index.js",)),
    ("mock-server", ("--dir", "packages/mock-server", "run", "build"), 600,
     ("packages/mock-server/src/index.js", "packages/mock-server/protos/compiled.js")),
    ("app-assets", ("exec", "run-s", "--print-label", *ASSETS), 1200,
     ("ts/protobuf/compiled.std.js", "build/emoji-data.json", "bundles/main.js",
      "bundles/preload/main.js", "bundles/workers/sql.js", "build/compact-locales/keys.json",
      "build/compact-locales/en/values.json", "stylesheets/manifest.css",
      "stylesheets/manifest_bridge.css", "stylesheets/quill.css", "stylesheets/tailwind.css",
      "config/local-production.json", "build/org.signalapp.enable-backups.policy",
      "build/org.signalapp.plaintext-export.policy", "build/org.signalapp.view-aep.policy")),
)


def source_matches(candidate, marker, allow_generated):
    actual = deps.source_binding(candidate)
    original = marker["source_files"]
    if not allow_generated:
        return actual == original
    return all(actual.get(name) == value for name, value in original.items())


def prepare(build, resume=False):
    candidate = build / "signal-backup-candidate"
    dependency_state = build / "signal-dependencies"
    runtime = build / (deps.NAME + "-with-npm")
    manager = build / f"pnpm-{deps.PNPM_VERSION}"
    state = build / "signal-candidate-build"
    for path in (build, candidate, dependency_state, runtime, runtime / "bin",
                 manager, manager / "bin", manager / "dist", candidate / "node_modules"):
        deps.owned(path, directory=True)
    marker = deps.read_json(dependency_state / "owner.json")
    if (marker.get("schema") != 1 or marker.get("uid") != os.getuid()
            or marker.get("candidate") != str(candidate) or marker.get("signal_source") != deps.COMMIT
            or marker.get("lock_sha256") != deps.LOCK_SHA256):
        raise ValueError("dependency owner/source binding differs")
    for key, path in (("node_sha256", runtime / "bin/node"),
                      ("npm_launcher_sha256", runtime / "bin/npm"),
                      ("pnpm_entry_sha256", manager / "bin/pnpm.mjs"),
                      ("pnpm_bundle_sha256", manager / "dist/pnpm.mjs")):
        if deps.digest(path) != marker.get(key):
            raise ValueError("staged runtime differs from dependency installation")
    receipts = sorted(dependency_state.glob("attempt-*.json"), key=lambda path: path.stat().st_mtime_ns)
    if not receipts:
        raise ValueError("successful locked dependency installation required")
    receipt = deps.read_json(receipts[-1])
    if (receipt.get("dependency_installation_succeeded") is not True
            or receipt.get("source_and_lock_unchanged") is not True
            or receipt.get("signal_source") != deps.COMMIT
            or receipt.get("lock_sha256") != deps.LOCK_SHA256):
        raise ValueError("latest dependency installation did not preserve the exact source and lock")
    binding = dict(schema=1, uid=os.getuid(), candidate=str(candidate), source=deps.COMMIT,
                   dependency_owner_sha256=deps.digest(dependency_state / "owner.json"),
                   dependency_receipt_sha256=deps.digest(receipts[-1]),
                   source_date_epoch=SOURCE_DATE_EPOCH)
    if not source_matches(candidate, marker, allow_generated=resume):
        raise ValueError("candidate source changed; no build or overwrite performed")
    if resume:
        deps.owned(state, directory=True)
        if deps.read_json(state / "owner.json") != binding:
            raise ValueError("build ownership or dependency receipt differs")
        for name in deps.STATE_DIRS:
            deps.owned(state / name, directory=True)
        for name in ("config/empty.npmrc", "config/empty-global.npmrc", "bin/pnpm"):
            deps.owned(state / name)
        if ((state / "config/empty.npmrc").read_bytes()
                or (state / "config/empty-global.npmrc").read_bytes()
                or (state / "bin/pnpm").read_bytes() != deps.launcher(state, runtime, manager)):
            raise ValueError("build launcher/configuration differs; refusing overwrite")
    else:
        if state.exists() or state.is_symlink():
            raise ValueError("build state exists; explicit --resume required")
        state.mkdir(mode=0o700)
        for name in deps.STATE_DIRS:
            (state / name).mkdir(mode=0o700)
        deps.write_new(state / "config/empty.npmrc", b"")
        deps.write_new(state / "config/empty-global.npmrc", b"")
        deps.write_new(state / "bin/pnpm", deps.launcher(state, runtime, manager), 0o700)
        deps.write_new(state / "owner.json", (json.dumps(binding, sort_keys=True) + "\n").encode())
    return candidate, runtime, state, marker


def build_environment(state, runtime):
    env = deps.environment(state, runtime)
    env.update(SOURCE_DATE_EPOCH=SOURCE_DATE_EPOCH, SKIP_VERIFY_DEPS_BEFORE_RUN="1",
               pnpm_config_offline="true", pnpm_config_enable_pre_post_scripts="false",
               pnpm_config_runtime="false", NPM_CONFIG_IGNORE_SCRIPTS="true",
               npm_execpath=str(state / "bin/pnpm"))
    return env


def sandbox_command(candidate, state, args):
    if args[:2] == ("exec", "run-s"):
        # npm-run-all2 8.0.4 accepts this option; pnpm exec alone does not set npm_execpath.
        # Pass the fixed launcher explicitly and retain npm_execpath for nested run-s/run-p.
        args = (*args[:2], "--npm-path", str(state / "bin/pnpm"), *args[2:])
    # No writable host home, network namespace, native app or broad build/rebuild command.
    return ["/usr/bin/bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid",
            "--unshare-net", "--unshare-ipc", "--cap-drop", "ALL", "--ro-bind", "/", "/",
            "--proc", "/proc", "--remount-ro", "/proc", "--dev", "/dev",
            "--bind", str(candidate), str(candidate), "--bind", str(state), str(state),
            "--chdir", str(candidate), "--", str(state / "bin/pnpm"), *args]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", required=True,
                        help="explicitly run only the listed offline preparatory compile steps")
    parser.add_argument("--resume", action="store_true", help="rebuild in unchanged owned staging")
    args = parser.parse_args()
    os.umask(0o077)
    candidate, runtime, state, marker = prepare(deps.ROOT / "build", args.resume)
    env = build_environment(state, runtime)
    attempt = uuid.uuid4().hex
    report_path = state / f"attempt-{attempt}.json"
    report = dict(source=deps.COMMIT, lock_sha256=deps.LOCK_SHA256, candidate=str(candidate),
        source_date_epoch=SOURCE_DATE_EPOCH, report=str(report_path), steps=[],
        network_access=False, host_root_read_only=True, writable_mounts=[str(candidate), str(state)],
        automatic_package_lifecycle_scripts=False, dependency_installation=False,
        native_app_started=False, preload_cache_generated=False, signal_backup_runtime_proven=False,
        cpu_affinity_count=2, kernel_per_file_bound_bytes=deps.FILE_BOUND,
        measured_disk_stop_threshold_bytes=deps.DISK_BOUND, disk_threshold_is_filesystem_quota=False)
    print(json.dumps({"offline_build_plan": report, "steps": [name for name, *_ in STEPS]}, sort_keys=True),
          flush=True)
    succeeded = True
    for name, arguments, timeout, outputs in STEPS:
        command = sandbox_command(candidate, state, arguments)
        log = state / f"attempt-{attempt}-{name}.log"
        result = deps.run_bounded(command, candidate, env, log, [candidate, state], timeout=timeout)
        record = dict(name=name, command=command, log=str(log), timeout_seconds=timeout, **result)
        record["result"] = "COMPILED" if result["result"] == "INSTALLED" else result["result"]
        try:
            unchanged = source_matches(candidate, marker, allow_generated=True)
        except (OSError, ValueError):
            unchanged = False
        try:
            output_hashes = {path: deps.digest(candidate / path) for path in outputs}
        except (OSError, ValueError):
            output_hashes = {}
            if record["result"] == "COMPILED":
                record["result"] = "REQUIRED_OUTPUT_MISSING_OR_UNSAFE"
        record.update(original_source_and_lock_unchanged=unchanged, output_sha256=output_hashes)
        record["succeeded"] = record["result"] == "COMPILED" and unchanged and len(output_hashes) == len(outputs)
        report["steps"].append(record)
        print(json.dumps({"offline_build_step": record}, sort_keys=True), flush=True)
        if not record["succeeded"]:
            succeeded = False
            break
    report["preparatory_compilation_succeeded"] = succeeded
    deps.write_new(report_path, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps(report, sort_keys=True))
    return 0 if succeeded else 1


if __name__ == "__main__":
    sys.exit(main())
