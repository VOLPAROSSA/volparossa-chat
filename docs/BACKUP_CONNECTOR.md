# Encrypted backup connector

The first **native Signal encrypted-export/core-storage/import round trip passes** in a
disposable KVM guest, using the older whole-archive replica connector. New deposits now use
the core's fragment-placement API; that revised native three-provider round trip is **not yet
proven**. This remains a development integration, not a complete backup product.
The narrow connector checks and four preparatory compile steps also pass; a complete
TypeScript typecheck is not claimed. The process-boundary unit test uses an explicitly
test-only CLI substitute, while the native trial and production connector invoke the real
core executable. Production code has no substitute backend.

## What this slice actually adds

The overlay's `ts/services/backups/volparossa/connector.preload.ts` calls upstream
`backupsService.exportLocalBackup()` and waits for completion. It captures the three native
snapshot files and every referenced encrypted attachment. Missing metadata/media, symlinks,
duplicate references and unsafe names fail the candidate rather than silently making a
partial backup appear complete. Ordinary send/receive, identity/session keys, native account
linking and calling remain untouched. The original local-backup feature gate remains in force.

`archive.node.ts` streams a bounded **private container**, not a cryptographic format or public
wire protocol. It limits each container to 100 GiB of payload, 16,384 entries, a 4 MiB header
and 256 KiB copy buffers. These are implementation bounds, not network-wide capacity limits.
Upstream `AttachmentCrypto.encryptAttachmentV2` encrypts/authenticates the **whole** container,
including names, metadata and the attachment-reference list. The core sees only that opaque
ciphertext. No public cache, plaintext deduplication or training publication is involved.

`fragments.node.ts` invokes fixed `volparossa storage fragments` operations without a shell.
New deposits require **three to eight** explicitly selected provider identities and their
owner-bound grants. The core splits the encrypted archive into distinct fragments and retains
**two copies of each fragment**; it owns that uniform policy, placement, signatures and byte
accounting. There is no application-selectable copy tier or second ledger. `fragmentBytes`
optionally bounds a fragment (default 16 MiB, maximum 1 GiB); the actual core plan may split
smaller to spread the archive across the provider pool. The core limits an encrypted archive
to 64 GiB and 256 fragments, so larger archives require a sufficiently large fragment bound.
Distinct identities alone do not prove independent failure domains.

Core receipts, provider TLS and route validation remain in the real CLI. The connector checks
complete contiguous ranges, two-copy placement, original ordered identities, per-provider
charges and non-consuming restore. It accepts the core's report v2 for owner-signed repair
history: replacements may add providers, while unconfirmed old copies remain charged. It does
not initiate repair or grant renewal. A partial transfer leaves the original ciphertext and
journals for explicit resume; it does not regenerate a backup, roll back copies, silently
fall back to full replicas or delete someone else's retained data.
Each CLI invocation has a finite 30-minute outer bound; cancellation waits for its exact
child to exit. The core's per-exchange limits are unchanged.

After restore, the connector checks ciphertext length/hash and awaits Signal's complete
digest/MAC verification **before** parsing or extracting. It then checks the native media
references and returns a new snapshot for the ordinary installer/link/import lifecycle.
Restoration does not consume remote copies or overwrite an existing output tree.

## Private state and authority

The operator supplies a fresh, empty owner-only `0700` work directory. `recovery.json` is a
`0600` application-private outer-envelope descriptor; it contains the wrapper key and must
never reach the agent, provider, logs or public artifacts. **Signal's original backup/account
recovery material is still required as well**; the wrapper key does not replace it. Explicit
portable recovery/key management and a user interface remain later work. Do not delete the
descriptor merely because a transfer failed or a window closed.

New descriptors use `version: 2` and `storage: { kind: "fragments", providerKeys: [...] }`;
the immutable original provider order is retained alongside the ciphertext digest and hash.
The associated core reconstruction state is `work/fragments`. Retain that complete signed
manifest/journal tree as well as the descriptor and owner identity: the wrapper key alone is
not enough to locate and authenticate a backup. Existing `version: 1` descriptors without a
storage field still resume and restore through `replicas.node.ts` and `work/replicas`, including
the old two-provider setup. Their bytes are not rewritten or automatically migrated. Unknown
versions, ambiguous storage discriminators and changed original provider bindings fail closed.
The historical function names `exportToReplicas`/`restoreFromReplicas` remain for native CI
compatibility; a **new** export now always chooses fragments. Supplying two providers therefore
fails before native export rather than creating another legacy archive.

The development connector uses the existing **administrative** core attachment. It is not
a least-authority multi-application SDK. The app owns its separately encrypted storage-owner
identity; the CLI reads an explicit `0600` passphrase file. Only private paths—not secret
values—appear in argv; recovery keys are not passed to the CLI at all. No second core is
started, no roles enabled, and no host routing/DNS/firewall changes are made by these scripts.

## Reproduce the narrow checks

Run from this repository before dependency installation/building; staging refuses existing
targets rather than overwriting them. After compilation, generated files are expected and
the build's original-source hash binding replaces the clean-tree overlay check:

```sh
python3 scripts/stage_signal_source.py --download
python3 scripts/stage_node.py
python3 -B tests/test_source_staging.py
build/node-v24.19.0-linux-x64/bin/node --test tests/backup_connector.test.mjs tests/fragment_backup.test.mjs tests/backup_withdrawal.test.mjs
python3 scripts/apply_signal_overlay.py
python3 scripts/apply_signal_overlay.py --check
```

The original pinned source stays in `build/signal-desktop-ef3872cb0249ec939d8aff857568a0e87a6b5075`;
the separate patched tree is `build/signal-backup-candidate`. Both, Node and generated reports
are ignored by Git. `--verify-existing` on the source stager refreshes only upstream tree
metadata and rechecks every staged file. The scripts preserve upstream license files/notices;
Signal-targeted overlay code is AGPL-3.0-only. The Node staging keeps its upstream LICENSE too.

Measured downloads: **48,865,481 bytes** of Signal source and **31,633,904 bytes** of Node.
The checked source is 96,820,148 bytes; extracted Node plus its license is 126,147,070 bytes.
The initial source-only staging occupied approximately 334 MiB, **before** dependencies
and native inputs were added. See [initial artifact provenance](../provenance/source-staging.json)
and [current build evidence](../provenance/build-staging.json).
Git content hashes and official HTTPS checksums were verified; no independently verified
upstream release-signature claim is made.

Fourteen focused checks pass for the container, legacy recovery, fragment command/report
contracts, retained v2 descriptor/restart, incomplete-response rejection, child cancellation
and the optional test rendezvous below. These use synthetic data and an explicitly test-only
CLI executable; they are **not** real provider, Signal-crypto or native-fragment proof. The
existing native encryption/import implementation and payload assertions are unchanged. No
full Signal build, real personal backup or guest trial was run for this connector revision.

## Native round-trip proof — 2026-09-30

The overlay augments `ts/CI.preload.ts` and adds an opt-in upstream test named
`exports and imports a VOLPAROSSA replicated encrypted backup` in
`ts/test-mock/backups/backups_test.node.ts`. The existing Signal helper creates messages and
an attachment, erases the original profile/CDN, relinks and compares imported messages and
attachment plaintext hashes. The new callback exports/deposits through the actual connector,
removes its local ciphertext, then restores through the configured core. The native testserver
still handles Signal test registration/linking; this does not prove server-free messaging.

[Core run 36742201942](https://github.com/VOLPAROSSA/volparossa/actions/runs/36742201942)
at core `90dbea789b57efcbc6cab941e54dfb6a5240511e` and this repository's
`c897667d76bea8140f0bc5f373404e43cbd54552` passes that exact native test: one test, one pass,
zero failures or pending tests. The 198,352-byte encrypted archive is retained on two real
providers, charging 396,704 payload bytes. Signal's native import verifies messages,
attachment hashes and screenshots after the original ciphertext is removed. Both remote
copies remain until explicit owner deletion; both stores then report zero leases and bytes.
Twelve real Exit MPTCP/TLS exchanges and two selected WireGuard relay paths carry the work.
Drained privacy captures, joined native processes, removal of private state and unchanged
guest-host networking all pass. The original 23-file artifact ZIP has SHA-256
`8eb0cfa37d26f9864e28ca13c570c7c251736d9e196c97e0c1e000ecfc9df4f2`.

The test app is capless and limited to loopback IP access plus the protected core socket.
The pinned Playwright Electron launcher disables Chromium sandboxing; its private mounts
and disposable guest are not an Electron-sandbox claim. The two provider namespaces are not
independent hardware. This proves real snapshot encryption and import, but not decentralized
Signal delivery/calling, automatic repair/contribution accounting, production recovery UX
or the full alpha. Earlier failed startup trials remain recorded in the core status.

The test requires `VOLPAROSSA_BACKUP_CONFIG` (an owner-only JSON file path) and
`VOLPAROSSA_BACKUP_WORK` (a fresh owner-only directory path). The JSON fields are:
`executable`, `controlSocket`, `identity`, `passphraseFile`, `providers` (ordered `{key, grant}`
pairs) and `lifetimeSeconds`. No secret values belong in these environment variables.
New fragment exports additionally accept optional `fragmentBytes` and require at least three
providers; the historical passing proof below used two whole-archive replicas. Provider
capacity/lease limits must cover every fragment copy assigned by the core, not merely one
transfer chunk. Retained
copies and recovery files are deliberately left for explicit fixture/operator cleanup.

For the **new disposable fragment test only**, `VOLPAROSSA_BACKUP_WITHDRAWAL=1` enables a
120-second rendezvous after the local encrypted archive is removed and before native restore.
The test writes owner-only `withdrawal-ready.json` with
`{ "version": 1, "ready": true, "provider_key": "<first configured provider>" }`.
The external topology driver must actually stop that provider, verify its stopped status,
then atomically publish owner-only `withdrawal-confirmed.json` containing exactly
`{ "version": 1, "provider_stopped": true, "provider_key": "<same provider>" }`.
Wrong, stale or missing acknowledgements fail the test; the marker itself is not evidence of
provider loss. The core fixture must independently verify that fact and subsequent recovery.
Without the explicit environment flag the original native test sequence is unchanged. This
helper lives exclusively in `ts/test-mock` and cannot authorize a production storage action.

Run that proof only in a disposable integration environment with actual policy-authorized
providers and an established protected core route. It explicitly generates the preload cache
and starts/imports through Electron; successful preparatory compilation alone is not proof.

## Verified build staging — 2026-09-30

The build uses Node 24.19.0, npm 11.17.0, pnpm 11.24.0, Electron 44.1.0 and the pinned
native libsignal/SQLCipher/RingRTC graph. The lockfile contains over 2,300 resolution entries;
the platform-selected installation added **2,023 packages**. Source/tool staging, dependency
installation, native extraction and compilation are explicit separate operations.
The first prerequisite can now be staged explicitly with
`python3 scripts/stage_node.py --with-npm`. It verifies the same pinned Node archive and
creates **`build/node-v24.19.0-linux-x64-with-npm`**, leaving the existing node-only runtime
and its report untouched. The complete bundled npm package and licenses are retained;
local `bin/npm` and `bin/npx` launchers invoke that runtime's exact Node and adjust PATH
only for their own child processes. Run either entrypoint with `--version` to inspect
the staged package without installing dependencies. Staging bounds the archive to 64 MiB
compressed, 20,000 entries and 512 MiB expanded, selects at most 256 MiB, and rejects
unsafe paths, unsupported links or existing output targets. Four offline synthetic
archive/launcher checks pass, including a conflicting Node on PATH. Actual staging on
2026-09-30 retained 1,923 files / 138,226,804 bytes; the exact Node reports 24.19.0 and
both bundled npm/npx entrypoints report **11.17.0**. The local
`build/node-npm-runtime-report.json` records those bytes and the pinned archive hash.

`python3 scripts/stage_pnpm.py --download` separately stages **pnpm 11.24.0**, using the
integrity value from the exact Signal lockfile. Actual staging retained 455 members /
20,095,957 bytes from a 5,071,766-byte archive, including the MIT license. The exact
Node/pnpm combination reports 11.24.0. Four offline archive/integrity checks pass.
Its local receipt is `build/pnpm-runtime-report.json`. These two steps install no
project dependencies, run no package lifecycle scripts and change no global configuration.

`python3 scripts/stage_signal_dependencies.py --download` installs the exact candidate graph
with `--frozen-lockfile --ignore-scripts`, integrity checking and no automatic manager/runtime
download. The final cached retry passed with **all 4,573 original candidate files and the
lock unchanged**. Its receipt is
`build/signal-dependencies/attempt-2489b2b9294244648a0f25d3f94fe969.json`.
The candidate plus dependency state measured 3,615,031,296 allocated bytes at that point.
Stores, caches, configuration and temporary files stay under `build`; HOME is not overridden.
The provisioner uses two CPUs, four concurrent network requests, a 30-minute deadline,
a 512 MiB per-file limit and an 8 GiB measured stop threshold—not a filesystem quota.
An explicit `--resume` preserves matching owned downloads rather than deleting them.

Native input staging is also explicit:

```sh
python3 scripts/stage_signal_native.py --download --artifact electron
python3 scripts/stage_signal_native.py --download --artifact ringrtc
```

Both completed on 2026-09-30. The Electron archive is 122,591,871 bytes (72 members;
295,221,748 expanded bytes); RingRTC is 34,753,889 bytes (10 members; 79,387,624 expanded
bytes). Each archive's checksum is bound to metadata inside its lockfile-SRI-verified npm
package. Original npm/native archives, Electron licenses and RingRTC acknowledgments are
retained under `build/native-inputs`, with individual provenance reports. Six offline
integrity/path/bound checks pass. Archive staging itself does **not** extract or execute native
code. The subsequent offline materialization also completed:

```sh
python3 scripts/install_signal_native.py --artifact electron
python3 scripts/install_signal_native.py --artifact ringrtc
python3 scripts/build_signal_candidate.py --build
```

Materialization verifies original installed npm files against their exact archives, then
creates only Electron's `dist`/`path.txt` and RingRTC's `build`/cached-archive paths inside
the candidate. Existing outputs are refused. Ordinary permissions and notices are retained;
`chrome-sandbox` is `0755`, not setuid. This does not reproduce the native dependencies'
source builds or execute Electron/RingRTC.

The compile wrapper passed **types → windows-ucv → mock-server → app assets**, with
**20 required output hashes**, unchanged original source/lock and joined child process groups.
Each step runs in Bubblewrap with no network, a read-only host root, and only the candidate
and owned build-state directory writable. It deliberately sets `SOURCE_DATE_EPOCH=1790198897`
and uses the staged pnpm for nested script runners. It does not invoke root postinstall,
generic rebuild/install, release packaging, preload-cache generation or the Signal app/test.
The receipt is `build/signal-candidate-build/attempt-1f732d5abbfd48ea861f8862b5d2b7d9.json`;
the sanitized [build evidence](../provenance/build-staging.json) records its hash and outputs.
Use `--build --resume` only for this unchanged owned candidate after an interrupted build.

Earlier failed receipts remain failed: unsupported pnpm flags, pnpm's unwanted addition of
`devEngines.runtime.onFail`, and the asset runner selecting npm. Only that exactly identified
manifest addition was explicitly removed; the corrected installer disables runtime fetching
without changing the manifest, and the corrected runner explicitly selects pnpm. The final
successful receipts—not those earlier failures—support the statements above.

There is no adaptive reciprocal-contribution, automatic provider repair or independent
failure-domain proof in this connector milestone.
