#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Explicitly provision locked Signal dependencies, without package lifecycle scripts."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import shlex
import signal
import stat
import subprocess
import sys
import time
import uuid

from stage_node import NAME, SHA256 as NODE_SHA256, VERSION as NODE_VERSION
from stage_pnpm import INTEGRITY, LOCK_SHA256, VERSION as PNPM_VERSION
from stage_signal_source import COMMIT, ROOT

DISK_BOUND = 8 * 1024**3
FILE_BOUND = 512 * 1024**2
LOG_BOUND = 16 * 1024**2
TIME_BOUND = 30 * 60
STATE_DIRS = ("bin", "store", "cache", "config", "state", "data", "tmp", "node-cache", "npm-cache")


def owned(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if info.st_uid != os.getuid() or not expected(info.st_mode):
        raise ValueError(f"expected owned {'directory' if directory else 'regular file'}: {path}")


def digest(path):
    owned(path)
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_new(path, content, mode=0o600):
    with path.open("xb") as output:
        output.write(content)
    path.chmod(mode)


def read_json(path):
    owned(path)
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError("staging report exceeds bound")
    return json.loads(path.read_bytes())


def source_binding(candidate):
    """Generated node_modules may resume; all source/overlay bytes stay immutable."""
    files = {}
    for parent, directories, names in os.walk(candidate, followlinks=False):
        if "node_modules" in directories:
            owned(Path(parent) / "node_modules", directory=True)
        directories[:] = sorted(name for name in directories if name != "node_modules")
        for name in directories:
            owned(Path(parent) / name, directory=True)
        for name in sorted(names):
            path = Path(parent) / name
            files[str(path.relative_to(candidate))] = digest(path)
    if files.get("pnpm-lock.yaml") != LOCK_SHA256:
        raise ValueError("candidate lock differs from reviewed exact lock")
    return files


def environment(state, runtime, legacy=False, runtime_override=False):
    # No home override, CI integrity bypass, inherited credentials/proxy or Node injection.
    env = {name: os.environ[name] for name in ("HOME", "USER", "LOGNAME") if name in os.environ}
    env.update({
        "PATH": f"{state / 'bin'}:{runtime / 'bin'}:/usr/bin:/bin",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "XDG_CONFIG_HOME": str(state / "config"), "XDG_CACHE_HOME": str(state / "cache"),
        "XDG_STATE_HOME": str(state / "state"), "XDG_DATA_HOME": str(state / "data"),
        "TMPDIR": str(state / "tmp"), "NODE_COMPILE_CACHE": str(state / "node-cache"),
        "NODE_OPTIONS": "--max-old-space-size=2048", "PNPM_MAX_WORKERS": "2",
        "NPM_CONFIG_USERCONFIG": str(state / "config/empty.npmrc"),
        "NPM_CONFIG_GLOBALCONFIG": str(state / "config/empty-global.npmrc"),
        "NPM_CONFIG_CACHE": str(state / "npm-cache"), "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "pnpm_config_pm_on_fail": "error", "pnpm_config_runtime_on_fail": "error",
        "pnpm_config_ignore_scripts": "true", "pnpm_config_verify_store_integrity": "true",
        "pnpm_config_update_notifier": "false", "ELECTRON_SKIP_BINARY_DOWNLOAD": "1",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD": "1",
    })
    if not legacy:
        # pnpm's typed environment parser preserves these as numbers; config.* CLI does not.
        env.update(pnpm_config_network_concurrency="4", pnpm_config_child_concurrency="2",
                   pnpm_config_fetch_retries="1", pnpm_config_fetch_timeout="60000")
    if not legacy and not runtime_override:
        del env["pnpm_config_runtime_on_fail"]
    return env


def manager_options(state, legacy=False, runtime_override=False):
    # Names verified against the exact pnpm 11.24.0 distribution, not older pnpm flags.
    options = [
        "--pm-on-fail=error", "--runtime-on-fail=error", "--update-notifier=false",
        f"--store-dir={state / 'store'}", f"--cache-dir={state / 'cache'}",
        f"--state-dir={state / 'state'}", f"--userconfig={state / 'config/empty.npmrc'}",
        "--enable-global-virtual-store=false", "--verify-store-integrity=true",
        "--network-concurrency=4", "--child-concurrency=2", "--fetch-retries=1",
        "--fetch-timeout=60000",
    ]
    # Real pnpm settings, but install and the nested config command accept different flags.
    if not legacy:
        numeric = {"network-concurrency", "child-concurrency", "fetch-retries", "fetch-timeout"}
        options = ["--config." + option[2:] for option in options
                   if option[2:].split("=", 1)[0] not in numeric]
        if not runtime_override:
            # runtimeOnFail mutates devEngines.runtime and pnpm persists it even when frozen.
            # Node is already exact-version checked; skip all runtime fetch/bin-linking instead.
            options = ["--config.runtime=false" if option == "--config.runtime-on-fail=error"
                       else option for option in options]
    return options


def launcher(state, runtime, manager, legacy=False, runtime_override=False):
    # Signal's trusted configuration hook invokes pnpm with only PATH inherited.
    # This launcher also confines that nested config read to the same staging area.
    lines = ["#!/bin/sh", "set -eu"]
    for name, value in environment(state, runtime, legacy, runtime_override).items():
        if name not in ("HOME", "USER", "LOGNAME"):
            lines.append(f"export {name}={shlex.quote(value)}")
    command = [str(runtime / "bin/node"), str(manager / "bin/pnpm.mjs"),
               *manager_options(state, legacy, runtime_override)]
    lines.append("exec " + shlex.join(command) + ' "$@"')
    return ("\n".join(lines) + "\n").encode()


def prepare(build, resume=False):
    candidate = build / "signal-backup-candidate"
    runtime = build / (NAME + "-with-npm")
    manager = build / f"pnpm-{PNPM_VERSION}"
    state = build / "signal-dependencies"
    for path in (build, candidate, runtime, runtime / "bin", manager, manager / "bin", manager / "dist"):
        owned(path, directory=True)
    node_report = read_json(build / "node-npm-runtime-report.json")
    pnpm_report = read_json(build / "pnpm-runtime-report.json")
    if (node_report.get("sha256") != NODE_SHA256 or node_report.get("version") != NODE_VERSION
            or node_report.get("launchers_use_pinned_node") is not True
            or node_report.get("npm_version") != "11.17.0"
            or pnpm_report.get("integrity") != INTEGRITY
            or pnpm_report.get("version") != PNPM_VERSION
            or pnpm_report.get("signal_source") != COMMIT
            or pnpm_report.get("lock_sha256") != LOCK_SHA256):
        raise ValueError("stage the reviewed Node/npm and pnpm before dependencies")
    binding = dict(schema=1, uid=os.getuid(), signal_source=COMMIT, lock_sha256=LOCK_SHA256,
                   candidate=str(candidate), npm_version=node_report["npm_version"],
                   node_sha256=digest(runtime / "bin/node"),
                   npm_launcher_sha256=digest(runtime / "bin/npm"),
                   pnpm_entry_sha256=digest(manager / "bin/pnpm.mjs"),
                   pnpm_bundle_sha256=digest(manager / "dist/pnpm.mjs"))
    if resume:
        owned(state, directory=True)
        marker = read_json(state / "owner.json")
        if any(marker.get(key) != value for key, value in binding.items()):
            raise ValueError("resume owner/source/runtime binding differs")
        for name in STATE_DIRS:
            owned(state / name, directory=True)
        for name in ("config/empty.npmrc", "config/empty-global.npmrc", "bin/pnpm"):
            owned(state / name)
        if ((state / "config/empty.npmrc").read_bytes()
                or (state / "config/empty-global.npmrc").read_bytes()
                or source_binding(candidate) != marker.get("source_files")):
            raise ValueError("resume source/configuration changed; refusing overwrite")
        installed_launcher = (state / "bin/pnpm").read_bytes()
        expected_launcher = launcher(state, runtime, manager)
        if installed_launcher != expected_launcher:
            if installed_launcher not in (launcher(state, runtime, manager, legacy=True),
                                          launcher(state, runtime, manager, runtime_override=True)):
                raise ValueError("resume launcher changed; refusing overwrite")
            # Upgrade only the exact previously generated launcher, preserving all state/logs.
            replacement = state / "bin" / ("pnpm-parser-fix-" + uuid.uuid4().hex)
            write_new(replacement, expected_launcher, 0o700)
            os.replace(replacement, state / "bin/pnpm")
    else:
        if state.exists() or state.is_symlink():
            raise ValueError("dependency state exists; use --resume only for this stager's unchanged candidate")
        # This checks the entire pristine source plus exact generated connector overlay.
        subprocess.run([sys.executable, str(ROOT / "scripts/apply_signal_overlay.py"), "--check"],
                       check=True, timeout=120, stdin=subprocess.DEVNULL)
        binding["source_files"] = source_binding(candidate)
        state.mkdir(mode=0o700)
        for name in STATE_DIRS:
            (state / name).mkdir(mode=0o700)
        write_new(state / "config/empty.npmrc", b"")
        write_new(state / "config/empty-global.npmrc", b"")
        write_new(state / "bin/pnpm", launcher(state, runtime, manager), 0o700)
        write_new(state / "owner.json", (json.dumps(binding, sort_keys=True) + "\n").encode())
    return candidate, runtime, state, read_json(state / "owner.json")


def measured_bytes(paths):
    """Count allocated blocks once, including symlinks but never traversing them."""
    seen, allocated, largest = set(), 0, 0
    for root in paths:
        for parent, directories, files in os.walk(root, followlinks=False):
            for path in [Path(parent), *(Path(parent) / name for name in directories + files)]:
                try:
                    info = path.lstat()
                except FileNotFoundError:  # Atomic cache publication/removal may race measurement.
                    continue
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    continue
                seen.add(identity)
                allocated += info.st_blocks * 512
                if stat.S_ISREG(info.st_mode):
                    largest = max(largest, info.st_size)
    return allocated, largest


def child_limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_BOUND, FILE_BOUND))
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    os.nice(10)


def join_group(process):
    """Only signal/reap the new process group created for this invocation."""
    for sig, seconds in ((signal.SIGTERM, 3), (signal.SIGKILL, 5)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            process.poll()
            if process.returncode is not None:
                try:
                    while os.waitpid(-process.pid, os.WNOHANG)[0]:
                        pass
                except ChildProcessError:
                    pass
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    return True
            time.sleep(0.05)
    return False


def run_bounded(command, cwd, env, log, measured_paths, timeout=TIME_BOUND):
    # Adopt/reap descendants of this staging process; no host-wide service setting.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError("cannot enable local child subreaper")
    started, code, reason, joined = time.monotonic(), None, None, True
    allocated, largest = measured_bytes(measured_paths)
    if allocated > DISK_BOUND or largest > FILE_BOUND:
        return dict(exit_code=None, result="DISK_OR_FILE_BOUND", process_group_joined=True,
                    elapsed_seconds=0, measured_allocated_bytes=allocated, largest_file_bytes=largest)
    process = None
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    old_term = signal.signal(signal.SIGTERM, interrupted)
    with log.open("xb") as output:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, start_new_session=True, preexec_fn=child_limits)
            next_measurement = 0
            while True:
                code = process.poll()
                now = time.monotonic()
                if now >= next_measurement:
                    allocated, largest = measured_bytes(measured_paths)
                    next_measurement = now + 3
                if allocated > DISK_BOUND or largest > FILE_BOUND or output.tell() > LOG_BOUND:
                    reason = "DISK_OR_FILE_BOUND"
                    break
                if now - started >= timeout:
                    reason = "TIME_BOUND"
                    break
                if code is not None:
                    reason = "INSTALLED" if code == 0 else "INSTALL_COMMAND_FAILED"
                    break
                time.sleep(0.25)
        except KeyboardInterrupt:
            reason = "INTERRUPTED"
        except (OSError, subprocess.SubprocessError):
            reason = "LOCAL_PROCESS_OR_IO_FAILURE"
        finally:
            if process is not None:
                joined = join_group(process)
                code = process.returncode
            signal.signal(signal.SIGTERM, old_term)
    allocated, largest = measured_bytes(measured_paths)
    if allocated > DISK_BOUND or largest > FILE_BOUND:
        reason = "DISK_OR_FILE_BOUND"
    if not joined:
        reason = "CHILD_CLEANUP_UNCONFIRMED"
    return dict(exit_code=code, result=reason, process_group_joined=joined,
                elapsed_seconds=round(time.monotonic() - started, 3),
                measured_allocated_bytes=allocated, largest_file_bytes=largest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", required=True,
                        help="explicitly install the locked graph; never run lifecycle/native scripts")
    parser.add_argument("--resume", action="store_true", help="reuse only matching owned staging and downloads")
    args = parser.parse_args()
    os.umask(0o077)
    candidate, runtime, state, marker = prepare(ROOT / "build", args.resume)
    env = environment(state, runtime)
    versions = {}
    for name, executable, expected in (
            ("node", runtime / "bin/node", "v" + NODE_VERSION),
            ("npm", runtime / "bin/npm", marker["npm_version"]),
            ("pnpm", state / "bin/pnpm", PNPM_VERSION)):
        actual = subprocess.run([str(executable), "--version"], cwd=state, env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=True).stdout.strip()
        if actual != expected:
            raise ValueError(f"staged {name} version differs")
        versions[name] = actual
    attempt = uuid.uuid4().hex
    log, report_path = state / f"attempt-{attempt}.log", state / f"attempt-{attempt}.json"
    command = [str(state / "bin/pnpm"), "install", "--frozen-lockfile", "--ignore-scripts",
               "--side-effects-cache=false", "--reporter=append-only"]
    report = dict(signal_source=COMMIT, lock_sha256=LOCK_SHA256, runtime_versions=versions,
        candidate=str(candidate), command=command, log=str(log), report=str(report_path),
        package_lifecycle_scripts_enabled=False, pinned_source_configuration_hook_enabled=True,
        native_build_proven=False, signal_backup_runtime_proven=False,
        manager_or_runtime_auto_download_enabled=False, home_overridden=False,
        timeout_seconds=TIME_BOUND, measured_disk_stop_threshold_bytes=DISK_BOUND,
        disk_threshold_is_filesystem_quota=False, kernel_per_file_bound_bytes=FILE_BOUND,
        cpu_affinity_count=2, network_concurrency=4, downloads_preserved_on_failure=True)
    print(json.dumps({"dependency_install_plan": report}, sort_keys=True), flush=True)
    result = run_bounded(command, candidate, env, log, [candidate, state])
    try:
        unchanged = source_binding(candidate) == marker["source_files"]
    except (OSError, ValueError):
        unchanged = False
    report.update(result, source_and_lock_unchanged=unchanged)
    report["dependency_installation_succeeded"] = result["result"] == "INSTALLED" and unchanged
    if not unchanged:
        report["result"] = "SOURCE_CHANGED"
    write_new(report_path, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps(report, sort_keys=True))
    return 0 if report["dependency_installation_succeeded"] else 1


if __name__ == "__main__":
    sys.exit(main())
