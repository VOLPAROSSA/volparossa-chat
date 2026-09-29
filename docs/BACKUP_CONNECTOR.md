# Encrypted backup connector candidate

This is executable integration source, **not yet a working Signal backup product**. The
five Node file/process tests and three offline source-staging tests pass. The exact-source
overlay applies. Electron/native Signal encryption and import, the complete TypeScript
typecheck, and the combined real-core storage test have **not** run. The process-boundary
unit test uses an explicitly test-only CLI substitute; production code always invokes
the operator-selected real core executable and has no substitute backend.

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

`replicas.node.ts` invokes fixed `volparossa storage replicas` operations without a shell.
Two to eight independently selected provider identities and their owner-bound grants are
required. Core receipts, provider TLS and route validation remain in the core's real CLI;
the connector also checks operation, size, ordered identities, completion and non-consuming
restore. A partial transfer leaves its original ciphertext and journals for explicit resume;
it does not regenerate a backup, roll back copies or delete someone else's retained data.
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

The development connector uses the existing **administrative** core attachment. It is not
a least-authority multi-application SDK. The app owns its separately encrypted storage-owner
identity; the CLI reads an explicit `0600` passphrase file. Only private paths—not secret
values—appear in argv; recovery keys are not passed to the CLI at all. No second core is
started, no roles enabled, and no host routing/DNS/firewall changes are made by these scripts.

## Reproduce the narrow checks

Run from this repository; staging refuses existing targets rather than overwriting them:

```sh
python3 scripts/stage_signal_source.py --download
python3 scripts/stage_node.py
python3 -B tests/test_source_staging.py
build/node-v24.19.0-linux-x64/bin/node --test tests/backup_connector.test.mjs
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
The current pristine source, patched source and runtime together occupy approximately
334 MiB on disk. See [exact artifact provenance](../provenance/source-staging.json).
Git content hashes and official HTTPS checksums were verified; no independently verified
upstream release-signature claim is made.

## Next: the real combined proof

The overlay augments `ts/CI.preload.ts` and adds an opt-in upstream test named
`exports and imports a VOLPAROSSA replicated encrypted backup` in
`ts/test-mock/backups/backups_test.node.ts`. The existing Signal helper creates messages and
an attachment, erases the original profile/CDN, relinks and compares imported messages and
attachment plaintext hashes. The new callback exports/deposits through the actual connector,
removes its local ciphertext, then restores through the configured core. The native testserver
still handles Signal test registration/linking; a pass would not prove server-free messaging.

The test requires `VOLPAROSSA_BACKUP_CONFIG` (an owner-only JSON file path) and
`VOLPAROSSA_BACKUP_WORK` (a fresh owner-only directory path). The JSON fields are:
`executable`, `controlSocket`, `identity`, `passphraseFile`, `providers` (ordered `{key, grant}`
pairs) and `lifetimeSeconds`. No secret values belong in these environment variables.
Provider capacity must cover the complete encrypted fixture, not just one chunk. Retained
copies and recovery files are deliberately left for explicit fixture/operator cleanup.

Run that proof only in a disposable integration environment with actual policy-authorized
providers and an established protected core route. Required build inputs are pinned upstream
Node 24.19.0, pnpm 11.24.0, Electron 44.1.0 and its native libsignal/SQLCipher/RingRTC graph.
The lockfile contains over 2,300 artifact-resolution entries; the total download/build size
has not yet been measured. No large dependency installation, package lifecycle scripts or
Electron download is authorized or performed by the staging tools. Those inputs need a
separate bounded provisioning plan before `pnpm generate` and the targeted native test.
There is no adaptive reciprocal-contribution, automatic provider repair or independent
failure-domain proof in this connector milestone.
